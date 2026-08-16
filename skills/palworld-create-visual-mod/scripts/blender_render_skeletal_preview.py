from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(
        description="Import a Palworld PSK, bind candidate textures, and render preview views."
    )
    parser.add_argument("--psk", required=True)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--target-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--pal-id", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--expected-vertices", type=int)
    parser.add_argument("--expected-bones", type=int)
    parser.add_argument("--resolution", type=int, default=768)
    return parser.parse_args(argv)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.materials, bpy.data.images, bpy.data.cameras, bpy.data.lights):
        for block in list(datablocks):
            if block.users == 0:
                datablocks.remove(block)


def load_image(path: Path, color_space: str):
    if not path.is_file():
        raise FileNotFoundError(path)
    image = bpy.data.images.load(str(path), check_existing=True)
    image.colorspace_settings.name = color_space
    return image


def find_principled_input(node, name: str):
    socket = node.inputs.get(name)
    if socket is None:
        raise RuntimeError(f"Principled BSDF input not found: {name}")
    return socket


def build_material(material, texture_dir: Path, bindings: list[dict]) -> dict[str, str]:
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (720, 0)
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.location = (420, 0)
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])

    by_role = {binding["role"]: binding for binding in bindings}
    if "base_color" not in by_role:
        raise RuntimeError(f"Material {material.name} has no base_color binding")

    resolved: dict[str, str] = {}
    base_path = texture_dir / f'{by_role["base_color"]["textureName"]}.png'
    base = nodes.new("ShaderNodeTexImage")
    base.label = "Candidate Base Color"
    base.location = (-620, 180)
    base.image = load_image(base_path, "sRGB")
    links.new(base.outputs["Color"], find_principled_input(shader, "Base Color"))
    links.new(base.outputs["Alpha"], find_principled_input(shader, "Alpha"))
    resolved["baseColor"] = str(base_path)

    if "normal" in by_role:
        normal_path = texture_dir / f'{by_role["normal"]["textureName"]}.png'
        normal = nodes.new("ShaderNodeTexImage")
        normal.label = "Candidate or passthrough Normal"
        normal.location = (-620, -120)
        normal.image = load_image(normal_path, "Non-Color")
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.location = (120, -160)
        normal_map.inputs["Strength"].default_value = 0.75
        links.new(normal.outputs["Color"], normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], find_principled_input(shader, "Normal"))
        resolved["normal"] = str(normal_path)

    if "packed_mros" in by_role:
        packed_path = texture_dir / f'{by_role["packed_mros"]["textureName"]}.png'
        packed = nodes.new("ShaderNodeTexImage")
        packed.label = "Candidate or passthrough MROS"
        packed.location = (-620, -430)
        packed.image = load_image(packed_path, "Non-Color")
        separate = nodes.new("ShaderNodeSeparateColor")
        separate.mode = "RGB"
        separate.location = (-240, -430)
        links.new(packed.outputs["Color"], separate.inputs["Color"])
        links.new(separate.outputs["Red"], find_principled_input(shader, "Metallic"))
        links.new(separate.outputs["Green"], find_principled_input(shader, "Roughness"))
        resolved["mros"] = str(packed_path)
    else:
        shader.inputs["Roughness"].default_value = 0.4

    return resolved


def build_unbound_armor_material(material) -> dict[str, str]:
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    if "orange" in material.name.lower():
        shader.inputs["Base Color"].default_value = (0.8, 0.08, 0.015, 1.0)
        shader.inputs["Metallic"].default_value = 0.78
    else:
        shader.inputs["Base Color"].default_value = (0.16, 0.22, 0.30, 1.0)
        shader.inputs["Metallic"].default_value = 0.82
    shader.inputs["Roughness"].default_value = 0.28
    return {"proceduralPreview": material.name}


def world_bounds(obj) -> tuple[Vector, Vector, Vector]:
    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    low = Vector(tuple(min(point[i] for point in points) for i in range(3)))
    high = Vector(tuple(max(point[i] for point in points) for i in range(3)))
    return low, high, (low + high) * 0.5


def point_at(obj, target: Vector) -> None:
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


def add_area_light(name: str, location: Vector, target: Vector, energy: float, size: float):
    data = bpy.data.lights.new(name=name, type="AREA")
    data.energy = energy
    data.shape = "DISK"
    data.size = size
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    point_at(obj, target)
    return obj


def main() -> None:
    args = parse_args()
    psk_path = Path(args.psk).resolve()
    candidate_dir = Path(args.candidate_dir).resolve()
    target_manifest_path = Path(args.target_manifest).resolve()
    target_manifest = json.loads(target_manifest_path.read_text(encoding="utf-8"))
    texture_dir = candidate_dir / "textures"
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    clear_scene()
    bpy.ops.preferences.addon_enable(module="io_scene_psk_psa")
    result = bpy.ops.import_scene.psk(
        filepath=str(psk_path),
        should_import_vertex_colors=True,
        vertex_color_space="SRGBA",
        should_import_vertex_normals=True,
        should_import_extra_uvs=True,
        should_import_mesh=True,
        should_import_materials=True,
        should_import_skeleton=True,
        bone_length=1.0,
        should_import_shape_keys=True,
        scale=0.01,
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"PSK import failed: {result}")

    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    if len(meshes) != 1 or len(armatures) != 1:
        raise RuntimeError(
            f"Expected one mesh and one armature, got {len(meshes)} mesh(es) and "
            f"{len(armatures)} armature(s)"
        )
    mesh_obj = meshes[0]
    armature_obj = armatures[0]
    armature_obj.hide_render = True

    bindings_by_slot: dict[str, list[dict]] = {}
    for binding in target_manifest["assets"]["textureBindings"]:
        bindings_by_slot.setdefault(binding["slotName"], []).append(binding)

    material_bindings: dict[str, dict[str, str]] = {}
    for slot in mesh_obj.material_slots:
        if slot.material is None:
            continue
        bindings = bindings_by_slot.get(slot.material.name)
        if bindings:
            material_bindings[slot.material.name] = build_material(slot.material, texture_dir, bindings)
        elif "mechanicalarmor" in slot.material.name.lower():
            material_bindings[slot.material.name] = build_unbound_armor_material(slot.material)

    low, high, center = world_bounds(mesh_obj)
    dimensions = high - low
    radius = max(dimensions) * 0.62
    camera_distance = max(radius * 3.2, 1.0)

    camera_data = bpy.data.cameras.new("PreviewCamera")
    camera_data.lens = 58
    camera = bpy.data.objects.new("PreviewCamera", camera_data)
    bpy.context.collection.objects.link(camera)
    bpy.context.scene.camera = camera

    light_scale = max(radius, 0.5)
    add_area_light(
        "Key",
        center + Vector((camera_distance * 0.7, -camera_distance * 0.8, camera_distance * 0.8)),
        center,
        950.0,
        light_scale * 2.2,
    )
    add_area_light(
        "Fill",
        center + Vector((-camera_distance * 0.8, -camera_distance * 0.2, camera_distance * 0.25)),
        center,
        500.0,
        light_scale * 2.8,
    )
    add_area_light(
        "Rim",
        center + Vector((0.0, camera_distance * 0.9, camera_distance * 0.7)),
        center,
        800.0,
        light_scale * 1.8,
    )

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = args.resolution
    scene.render.resolution_y = args.resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = False
    scene.render.image_settings.color_depth = "8"
    scene.render.resolution_percentage = 100
    scene.world.color = (0.012, 0.018, 0.035)

    view_specs = [
        ("front_three_quarter", -45.0),
        ("left_profile", 45.0),
        ("back_three_quarter", 135.0),
        ("right_profile", 225.0),
    ]
    render_paths: list[Path] = []
    target = center + Vector((0.0, 0.0, dimensions.z * 0.03))
    for name, degrees in view_specs:
        radians = math.radians(degrees)
        camera.location = center + Vector(
            (
                math.cos(radians) * camera_distance,
                math.sin(radians) * camera_distance,
                camera_distance * 0.28,
            )
        )
        point_at(camera, target)
        render_path = output_dir / f"{args.pal_id}-{name}.png"
        scene.render.filepath = str(render_path)
        bpy.ops.render.render(write_still=True)
        render_paths.append(render_path)

    blend_path = output_dir / f"{args.pal_id}-mechanical-preview.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), check_existing=False)

    armature_modifiers = [
        modifier
        for modifier in mesh_obj.modifiers
        if modifier.type == "ARMATURE" and modifier.object == armature_obj
    ]
    socket_bones = [bone.name for bone in armature_obj.data.bones if bone.name.lower().startswith("socket_")]
    structural_bone_count = len(armature_obj.data.bones) - len(socket_bones)
    checks = {
        "pskExists": psk_path.is_file(),
        "hasUv": len(mesh_obj.data.uv_layers) > 0,
        "materialSlotsBound": len(material_bindings) >= 2,
        "armatureModifierBound": len(armature_modifiers) > 0,
        "hasVertexGroups": len(mesh_obj.vertex_groups) > 0,
        "renderCount": len([path for path in render_paths if path.is_file()]) == len(view_specs),
    }
    if args.expected_vertices is not None:
        checks["expectedVertexCount"] = len(mesh_obj.data.vertices) == args.expected_vertices
    if args.expected_bones is not None:
        # CUE4Parse's registry count excludes Unreal socket pseudo-bones, while
        # the PSK interchange format carries them as bones. Compare like-for-like.
        checks["expectedBoneCountExcludingSockets"] = structural_bone_count == args.expected_bones

    report = {
        "artifactType": "PalworldSkeletalMeshPreviewReport",
        "schemaVersion": 1,
        "status": "pass" if all(checks.values()) else "fail",
        "buildId": args.build_id,
        "palId": args.pal_id,
        "source": {"psk": str(psk_path), "sha256": sha256(psk_path)},
        "candidateDirectory": str(candidate_dir),
        "targetManifest": {"path": str(target_manifest_path), "sha256": sha256(target_manifest_path)},
        "mesh": {
            "name": mesh_obj.name,
            "vertices": len(mesh_obj.data.vertices),
            "polygons": len(mesh_obj.data.polygons),
            "uvLayers": [layer.name for layer in mesh_obj.data.uv_layers],
            "materialSlots": [slot.material.name if slot.material else None for slot in mesh_obj.material_slots],
            "vertexGroups": len(mesh_obj.vertex_groups),
        },
        "skeleton": {
            "name": armature_obj.name,
            "bonesIncludingSockets": len(armature_obj.data.bones),
            "structuralBones": structural_bone_count,
            "socketBones": socket_bones,
            "armatureModifierBound": len(armature_modifiers) > 0,
        },
        "materialBindings": material_bindings,
        "shaderApproximation": {
            "baseColor": "candidate texture",
            "normal": "original game texture through Blender Normal Map node",
            "mros": "original game texture; R=metallic and G=roughness for preview",
            "note": "The in-game Unreal master material remains authoritative.",
        },
        "checks": checks,
        "renders": [
            {"path": str(path), "sha256": sha256(path)} for path in render_paths if path.is_file()
        ],
        "blendFile": {"path": str(blend_path), "sha256": sha256(blend_path)},
    }
    report_path = output_dir / "preview-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise RuntimeError(f"Preview validation failed; see {report_path}")


if __name__ == "__main__":
    main()
