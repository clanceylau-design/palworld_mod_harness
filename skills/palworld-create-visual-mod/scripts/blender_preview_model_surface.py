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
    parser = argparse.ArgumentParser(description="Bind body and attachment atlases to a frozen model and render it.")
    parser.add_argument("--surface-contract", required=True)
    parser.add_argument("--armor-texture", required=True)
    parser.add_argument("--body-texture", required=True)
    parser.add_argument("--eye-texture", required=True)
    parser.add_argument("--armor-normal")
    parser.add_argument("--armor-mrao")
    parser.add_argument("--armor-emissive")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--resolution", type=int, default=768)
    return parser.parse_args(argv)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def point_at(obj, target: Vector) -> None:
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


def textured_material(name: str, texture: Path, metallic: float, roughness: float):
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    image_node = nodes.new("ShaderNodeTexImage")
    image_node.image = bpy.data.images.load(str(texture), check_existing=True)
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["Roughness"].default_value = roughness
    material.node_tree.links.new(image_node.outputs["Color"], shader.inputs["Base Color"])
    material.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def armor_pbr_material(base_color: Path, normal_path: Path | None, mrao_path: Path | None, emissive_path: Path | None):
    material = bpy.data.materials.get("M_MechanicalArmor_Final") or bpy.data.materials.new("M_MechanicalArmor_Final")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    links = material.node_tree.links
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    base_node = nodes.new("ShaderNodeTexImage")
    base_node.image = bpy.data.images.load(str(base_color), check_existing=True)
    links.new(base_node.outputs["Color"], shader.inputs["Base Color"])
    if normal_path:
        normal_node = nodes.new("ShaderNodeTexImage")
        normal_node.image = bpy.data.images.load(str(normal_path), check_existing=True)
        normal_node.image.colorspace_settings.name = "Non-Color"
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.inputs["Strength"].default_value = 1.0
        links.new(normal_node.outputs["Color"], normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], shader.inputs["Normal"])
    if mrao_path:
        packed_node = nodes.new("ShaderNodeTexImage")
        packed_node.image = bpy.data.images.load(str(mrao_path), check_existing=True)
        packed_node.image.colorspace_settings.name = "Non-Color"
        separate = nodes.new("ShaderNodeSeparateColor")
        links.new(packed_node.outputs["Color"], separate.inputs["Color"])
        links.new(separate.outputs["Red"], shader.inputs["Metallic"])
        links.new(separate.outputs["Green"], shader.inputs["Roughness"])
    else:
        shader.inputs["Metallic"].default_value = 0.72
        shader.inputs["Roughness"].default_value = 0.27
    if emissive_path and "Emission Color" in shader.inputs:
        emissive_node = nodes.new("ShaderNodeTexImage")
        emissive_node.image = bpy.data.images.load(str(emissive_path), check_existing=True)
        emissive_node.image.colorspace_settings.name = "Non-Color"
        orange = nodes.new("ShaderNodeRGB")
        orange.outputs[0].default_value = (1.0, 0.055, 0.002, 1.0)
        multiply = nodes.new("ShaderNodeMixRGB")
        multiply.blend_type = "MULTIPLY"
        multiply.inputs[0].default_value = 1.0
        links.new(orange.outputs[0], multiply.inputs[1])
        links.new(emissive_node.outputs["Color"], multiply.inputs[2])
        links.new(multiply.outputs[0], shader.inputs["Emission Color"])
        shader.inputs["Emission Strength"].default_value = 2.5
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def add_light(name: str, location: Vector, target: Vector, energy: float, size: float) -> None:
    data = bpy.data.lights.new(name, "AREA")
    data.energy = energy
    data.shape = "DISK"
    data.size = size
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    point_at(obj, target)


def main() -> None:
    args = parse_args()
    contract_path = Path(args.surface_contract).resolve()
    armor_path = Path(args.armor_texture).resolve()
    body_path = Path(args.body_texture).resolve()
    eye_path = Path(args.eye_texture).resolve()
    armor_normal = Path(args.armor_normal).resolve() if args.armor_normal else None
    armor_mrao = Path(args.armor_mrao).resolve() if args.armor_mrao else None
    armor_emissive = Path(args.armor_emissive).resolve() if args.armor_emissive else None
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    attachment_names = [entry["object"] for entry in contract["attachmentSurface"]["entries"]]
    attachments = [bpy.data.objects.get(name) for name in attachment_names]
    if any(obj is None for obj in attachments):
        raise RuntimeError("Frozen Blend is missing a contracted attachment")

    armor_material = armor_pbr_material(armor_path, armor_normal, armor_mrao, armor_emissive)
    for obj in attachments:
        obj.data.materials.clear()
        obj.data.materials.append(armor_material)

    body_material = textured_material("MI_ChickenPal_Body_Final", body_path, 0.05, 0.62)
    eye_material = textured_material("MI_ChickenPal_Eye_Final", eye_path, 0.0, 0.35)
    body_bound = eye_bound = False
    for obj in [value for value in bpy.context.scene.objects if value.type == "MESH" and value.name not in attachment_names]:
        for index, slot in enumerate(obj.material_slots):
            original = slot.material.name.lower() if slot.material else ""
            if "eye" in original:
                obj.data.materials[index] = eye_material
                eye_bound = True
            else:
                obj.data.materials[index] = body_material
                body_bound = True

    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    points = [obj.matrix_world @ Vector(corner) for obj in meshes for corner in obj.bound_box]
    low = Vector(tuple(min(point[i] for point in points) for i in range(3)))
    high = Vector(tuple(max(point[i] for point in points) for i in range(3)))
    center = (low + high) * 0.5
    dimensions = high - low
    radius = max(dimensions) * 0.62
    distance = max(radius * 3.2, 1.0)

    for obj in list(bpy.context.scene.objects):
        if obj.type in {"CAMERA", "LIGHT"}:
            bpy.data.objects.remove(obj, do_unlink=True)
    camera_data = bpy.data.cameras.new("PreviewCamera")
    camera_data.lens = 58
    camera = bpy.data.objects.new("PreviewCamera", camera_data)
    bpy.context.collection.objects.link(camera)
    bpy.context.scene.camera = camera
    add_light("Key", center + Vector((distance * 0.7, -distance * 0.8, distance * 0.8)), center, 1000, radius * 2.2)
    add_light("Fill", center + Vector((-distance * 0.8, -distance * 0.2, distance * 0.25)), center, 520, radius * 2.8)
    add_light("Rim", center + Vector((0, distance * 0.9, distance * 0.7)), center, 850, radius * 1.8)

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = args.resolution
    scene.render.resolution_y = args.resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.world.color = (0.012, 0.018, 0.035)
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 1.35
    target = center + Vector((0, 0, dimensions.z * 0.03))
    views = [("front_three_quarter", -45), ("left_profile", 45), ("back_three_quarter", 135), ("right_profile", 225)]
    renders = []
    for name, degrees in views:
        radians = math.radians(degrees)
        camera.location = center + Vector((math.cos(radians) * distance, math.sin(radians) * distance, distance * 0.28))
        point_at(camera, target)
        path = output / f"ChickenPal-model-first-{name}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        renders.append(path)
    blend_path = output / "ChickenPal-model-first-textured-preview.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), check_existing=False)
    checks = {
        "bodyTextureBound": body_bound,
        "eyeTextureBound": eye_bound,
        "allAttachmentsBound": all(obj.material_slots[0].material == armor_material for obj in attachments),
        "armorNormalBound": armor_normal is None or armor_normal.is_file(),
        "armorMraoBound": armor_mrao is None or armor_mrao.is_file(),
        "armorEmissiveBound": armor_emissive is None or armor_emissive.is_file(),
        "renderCount": sum(path.is_file() for path in renders) == 4,
    }
    report = {
        "schemaVersion": 1,
        "artifactType": "ModelFirstTexturedPreviewReport",
        "status": "pass" if all(checks.values()) else "fail",
        "gameBuildId": contract["gameBuildId"],
        "palId": contract["palId"],
        "surfaceContract": {"path": str(contract_path), "sha256": sha256(contract_path)},
        "textures": {
            "body": str(body_path), "eye": str(eye_path), "armor": str(armor_path),
            "armorNormal": str(armor_normal) if armor_normal else None,
            "armorMrao": str(armor_mrao) if armor_mrao else None,
            "armorEmissive": str(armor_emissive) if armor_emissive else None,
        },
        "checks": checks,
        "renders": [{"path": str(path), "sha256": sha256(path)} for path in renders],
        "blend": {"path": str(blend_path), "sha256": sha256(blend_path)},
        "deliveryStatus": "blender_model_matched_pbr_preview_complete_unreal_cook_pending",
    }
    report_path = output / "model-first-preview-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise RuntimeError("Model-first preview checks failed")


if __name__ == "__main__":
    main()
