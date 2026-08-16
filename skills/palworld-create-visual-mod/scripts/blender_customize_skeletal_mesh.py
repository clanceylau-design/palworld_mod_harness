from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
from pathlib import Path

import bpy
from mathutils import Euler
from mathutils import Vector


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description="Apply topology-preserving weighted-region edits to a PSK.")
    parser.add_argument("--psk", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--target-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args(argv)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_mesh_contract(mesh_obj, armature_obj) -> dict[str, str]:
    topology = hashlib.sha256()
    for polygon in mesh_obj.data.polygons:
        topology.update(struct.pack("<I", len(polygon.vertices)))
        for vertex_index in polygon.vertices:
            topology.update(struct.pack("<I", vertex_index))

    uv = hashlib.sha256()
    for layer in mesh_obj.data.uv_layers:
        uv.update(layer.name.encode("utf-8"))
        for loop in layer.data:
            uv.update(struct.pack("<2f", loop.uv.x, loop.uv.y))

    weights = hashlib.sha256()
    group_names = {group.index: group.name for group in mesh_obj.vertex_groups}
    for vertex in mesh_obj.data.vertices:
        for membership in sorted(vertex.groups, key=lambda value: value.group):
            weights.update(group_names[membership.group].encode("utf-8"))
            weights.update(struct.pack("<If", vertex.index, membership.weight))

    skeleton = hashlib.sha256()
    for bone in armature_obj.data.bones:
        skeleton.update(bone.name.encode("utf-8"))
        skeleton.update((bone.parent.name if bone.parent else "").encode("utf-8"))

    materials = hashlib.sha256()
    for slot in mesh_obj.material_slots:
        materials.update((slot.material.name if slot.material else "").encode("utf-8"))

    return {
        "topology": topology.hexdigest(),
        "uv": uv.hexdigest(),
        "weights": weights.hexdigest(),
        "skeleton": skeleton.hexdigest(),
        "materials": materials.hexdigest(),
    }


def local_bounds(mesh_obj) -> tuple[Vector, Vector]:
    coordinates = [vertex.co for vertex in mesh_obj.data.vertices]
    low = Vector(tuple(min(value[i] for value in coordinates) for i in range(3)))
    high = Vector(tuple(max(value[i] for value in coordinates) for i in range(3)))
    return low, high


def vertex_group_weights(mesh_obj, group_name: str) -> dict[int, float]:
    group = mesh_obj.vertex_groups.get(group_name)
    if group is None:
        raise RuntimeError(f"Vertex group does not exist: {group_name}")
    result: dict[int, float] = {}
    for vertex in mesh_obj.data.vertices:
        for membership in vertex.groups:
            if membership.group == group.index:
                result[vertex.index] = membership.weight
                break
    return result


def influence(weight: float, threshold: float, power: float) -> float:
    if weight < threshold:
        return 0.0
    normalized = (weight - threshold) / max(1.0 - threshold, 1e-8)
    return max(0.0, min(1.0, normalized)) ** power


def apply_vertex_operation(mesh_obj, operation: dict) -> dict:
    group_name = operation["vertexGroup"]
    weights = vertex_group_weights(mesh_obj, group_name)
    threshold = float(operation.get("weightThreshold", 0.15))
    power = float(operation.get("falloffPower", 1.0))
    selected = {
        index: influence(weight, threshold, power)
        for index, weight in weights.items()
        if influence(weight, threshold, power) > 0.0
    }
    if not selected:
        raise RuntimeError(f"Operation selects no vertices: {group_name}")

    weighted_total = sum(selected.values())
    center = sum(
        (mesh_obj.data.vertices[index].co * value for index, value in selected.items()),
        Vector((0.0, 0.0, 0.0)),
    ) / weighted_total
    before = {index: mesh_obj.data.vertices[index].co.copy() for index in selected}

    operation_type = operation["type"]
    if operation_type == "scale_weighted_region":
        scale = Vector(tuple(float(value) for value in operation["scale"]))
        if any(value <= 0.0 or value > 2.0 for value in scale):
            raise ValueError("Scale components must be greater than 0 and no more than 2")
        for index, value in selected.items():
            vertex = mesh_obj.data.vertices[index]
            scaled = center + Vector(tuple((vertex.co[i] - center[i]) * scale[i] for i in range(3)))
            vertex.co = vertex.co.lerp(scaled, value)
    elif operation_type == "offset_weighted_region":
        offset = Vector(tuple(float(value) for value in operation["offsetCm"]))
        for index, value in selected.items():
            mesh_obj.data.vertices[index].co += offset * value
    elif operation_type == "sharpen_weighted_tip":
        mesh_center = sum(
            (vertex.co for vertex in mesh_obj.data.vertices), Vector((0.0, 0.0, 0.0))
        ) / len(mesh_obj.data.vertices)
        direction = (center - mesh_center).normalized()
        projections = {index: (mesh_obj.data.vertices[index].co - center).dot(direction) for index in selected}
        low_projection = min(projections.values())
        high_projection = max(projections.values())
        span = max(high_projection - low_projection, 1e-8)
        tip_start = float(operation.get("tipStart", 0.45))
        sharpness = float(operation.get("sharpness", 0.75))
        extension = float(operation.get("extensionCm", 4.0))
        if not 0.0 <= tip_start < 1.0 or not 0.0 <= sharpness <= 0.95:
            raise ValueError("tipStart or sharpness is outside its safe range")
        for index, weight_influence in selected.items():
            vertex = mesh_obj.data.vertices[index]
            longitudinal = (projections[index] - low_projection) / span
            tip_influence = max(0.0, min(1.0, (longitudinal - tip_start) / (1.0 - tip_start)))
            tip_influence = tip_influence * tip_influence * (3.0 - 2.0 * tip_influence)
            amount = tip_influence * weight_influence
            relative = vertex.co - center
            along = direction * relative.dot(direction)
            perpendicular = relative - along
            vertex.co = center + along + perpendicular * (1.0 - sharpness * amount) + direction * extension * amount
    else:
        raise ValueError(f"Unsupported operation type: {operation_type}")

    displacements = [(mesh_obj.data.vertices[index].co - original).length for index, original in before.items()]
    return {
        "type": operation_type,
        "vertexGroup": group_name,
        "affectedVertices": len(selected),
        "centerCm": [round(value, 6) for value in center],
        "maxDisplacementCm": round(max(displacements), 6),
        "meanDisplacementCm": round(sum(displacements) / len(displacements), 6),
    }


def ensure_armor_material(name: str):
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.diffuse_color = (0.08, 0.11, 0.15, 1.0) if "Orange" not in name else (0.8, 0.16, 0.025, 1.0)
    return material


def add_armor_plate(mesh_obj, armature_obj, operation: dict):
    center = Vector(tuple(float(value) for value in operation["centerCm"]))
    if operation.get("snapToSurface"):
        surface_normal = Vector(tuple(float(value) for value in operation["surfaceNormal"])).normalized()
        nearest = min(mesh_obj.data.vertices, key=lambda vertex: (vertex.co - center).length).co.copy()
        center = nearest + surface_normal * float(operation.get("surfaceOffsetCm", 0.4))
    rotation = Euler(tuple(math.radians(float(value)) for value in operation.get("rotationDeg", [0, 0, 0])), "XYZ")
    bone_name = operation["bone"]
    if armature_obj.data.bones.get(bone_name) is None:
        raise RuntimeError(f"Armor attachment bone does not exist: {bone_name}")

    shape = operation.get("shape", "box")
    if shape == "hex":
        width, height, thickness = (float(value) for value in operation["sizeCm"])
        profile = [
            (-0.50, -0.22), (-0.30, -0.50), (0.30, -0.50), (0.50, -0.22),
            (0.50, 0.22), (0.30, 0.50), (-0.30, 0.50), (-0.50, 0.22),
        ]
        vertices = []
        for depth in (-thickness * 0.5, thickness * 0.5):
            vertices.extend((x * width, depth, z * height) for x, z in profile)
        faces = [tuple(range(7, -1, -1)), tuple(range(8, 16))]
        faces.extend((index, (index + 1) % 8, 8 + (index + 1) % 8, 8 + index) for index in range(8))
        mesh_data = bpy.data.meshes.new(f'{operation["name"]}_Mesh')
        mesh_data.from_pydata(vertices, [], faces)
        mesh_data.update()
        armor = bpy.data.objects.new(operation["name"], mesh_data)
        bpy.context.collection.objects.link(armor)
        armor.location = center
        armor.rotation_euler = rotation
        dimensions = Vector((width, thickness, height))
    elif shape == "box":
        dimensions = Vector(tuple(float(value) for value in operation["dimensionsCm"]))
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=center, rotation=rotation)
        armor = bpy.context.object
        armor.name = operation["name"]
        armor.dimensions = dimensions
        bpy.context.view_layer.objects.active = armor
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    else:
        raise ValueError(f"Unsupported armor plate shape: {shape}")

    bpy.context.view_layer.objects.active = armor
    armor.select_set(True)
    bevel = float(operation.get("bevelCm", min(dimensions) * 0.2))
    bevel_modifier = armor.modifiers.new("ArmorBevel", "BEVEL")
    bevel_modifier.width = bevel
    bevel_modifier.segments = 2
    bevel_modifier.limit_method = "ANGLE"
    bpy.ops.object.modifier_apply(modifier=bevel_modifier.name)
    triangulate = armor.modifiers.new("ArmorTriangulate", "TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier=triangulate.name)

    material_name = operation.get("material", "M_MechanicalArmor")
    armor.data.materials.append(ensure_armor_material(material_name))
    uv_layer = armor.data.uv_layers.new(name="UVMap")
    for loop in armor.data.loops:
        vertex = armor.data.vertices[loop.vertex_index].co
        uv_layer.data[loop.index].uv = ((vertex.x / max(dimensions.x, 1e-8)) + 0.5, (vertex.z / max(dimensions.z, 1e-8)) + 0.5)

    group = armor.vertex_groups.new(name=bone_name)
    group.add([vertex.index for vertex in armor.data.vertices], 1.0, "REPLACE")
    modifier = armor.modifiers.new("Armature", "ARMATURE")
    modifier.object = armature_obj
    armor.parent = armature_obj
    return armor, {
        "type": operation["type"],
        "name": armor.name,
        "bone": bone_name,
        "vertices": len(armor.data.vertices),
        "polygons": len(armor.data.polygons),
        "centerCm": [round(value, 6) for value in center],
        "dimensionsCm": [round(value, 6) for value in dimensions],
        "shape": shape,
        "material": material_name,
    }


def main() -> None:
    args = parse_args()
    psk_path = Path(args.psk).resolve()
    spec_path = Path(args.spec).resolve()
    target_path = Path(args.target_manifest).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    target = json.loads(target_path.read_text(encoding="utf-8"))
    if spec["palId"] != target["target"]["palId"]:
        raise RuntimeError("Edit spec and TargetManifest refer to different Pals")

    bpy.ops.preferences.addon_enable(module="io_scene_psk_psa")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
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
        scale=1.0,
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"PSK import failed: {result}")
    mesh_obj = next(obj for obj in bpy.context.scene.objects if obj.type == "MESH")
    armature_obj = next(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")

    before_contract = hash_mesh_contract(mesh_obj, armature_obj)
    before_low, before_high = local_bounds(mesh_obj)
    operation_reports = []
    attachment_objects = []
    for operation in spec["operations"]:
        if operation["type"] == "add_bone_armor_plate":
            armor, report = add_armor_plate(mesh_obj, armature_obj, operation)
            attachment_objects.append(armor)
            operation_reports.append(report)
        else:
            operation_reports.append(apply_vertex_operation(mesh_obj, operation))
    mesh_obj.data.update()
    after_contract = hash_mesh_contract(mesh_obj, armature_obj)
    after_low, after_high = local_bounds(mesh_obj)

    max_allowed = float(spec.get("constraints", {}).get("maxDisplacementCm", 15.0))
    deformation_reports = [report for report in operation_reports if "maxDisplacementCm" in report]
    maximum = max((report["maxDisplacementCm"] for report in deformation_reports), default=0.0)
    contract_checks = {
        key: before_contract[key] == after_contract[key]
        for key in ("topology", "uv", "weights", "skeleton", "materials")
    }
    checks = {
        **{f"preserve_{key}": value for key, value in contract_checks.items()},
        "hasAffectedVertices": sum(report.get("affectedVertices", 0) for report in operation_reports) > 0,
        "withinDisplacementLimit": maximum <= max_allowed,
        "attachmentsArmatureBound": all(
            any(modifier.type == "ARMATURE" and modifier.object == armature_obj for modifier in armor.modifiers)
            for armor in attachment_objects
        ),
        "attachmentBonesExist": all(
            all(group.name in armature_obj.data.bones for group in armor.vertex_groups)
            for armor in attachment_objects
        ),
    }

    bpy.ops.object.select_all(action="DESELECT")
    mesh_obj.select_set(True)
    for armor in attachment_objects:
        armor.select_set(True)
    bpy.context.view_layer.objects.active = mesh_obj
    from io_scene_psk_psa.psk.builder import PskBuildOptions, build_psk
    from io_scene_psk_psa.psk.writer import write_psk

    options = PskBuildOptions()
    options.bone_filter_mode = "ALL"
    options.use_raw_mesh_data = True
    options.materials = []
    for selected_mesh in [mesh_obj, *attachment_objects]:
        for slot in selected_mesh.material_slots:
            if slot.material not in options.materials:
                options.materials.append(slot.material)
    export_result = build_psk(bpy.context, options)
    edited_psk = output_dir / f'{target["target"]["palId"]}-customized.psk'
    write_psk(export_result.psk, str(edited_psk))
    blend_path = output_dir / f'{target["target"]["palId"]}-customized.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), check_existing=False)

    report = {
        "schemaVersion": 1,
        "artifactType": "ConstrainedSkeletalMeshEditReport",
        "status": "pass" if all(checks.values()) else "fail",
        "gameBuildId": target["gameBuildId"],
        "palId": target["target"]["palId"],
        "inputs": {
            "sourcePsk": {"path": str(psk_path), "sha256": sha256(psk_path)},
            "editSpec": {"path": str(spec_path), "sha256": sha256(spec_path)},
            "targetManifest": {"path": str(target_path), "sha256": sha256(target_path)},
        },
        "operations": operation_reports,
        "constraints": {"maxDisplacementCm": max_allowed},
        "contractBefore": before_contract,
        "contractAfter": after_contract,
        "boundsCm": {
            "before": {"low": list(before_low), "high": list(before_high)},
            "after": {"low": list(after_low), "high": list(after_high)},
        },
        "checks": checks,
        "attachments": [armor.name for armor in attachment_objects],
        "outputGeometry": {
            "vertices": len(export_result.psk.points),
            "faces": len(export_result.psk.faces),
            "materials": [material.name for material in options.materials],
            "bonesIncludingSockets": len(export_result.psk.bones),
        },
        "outputs": {
            "psk": {"path": str(edited_psk), "sha256": sha256(edited_psk)},
            "blend": {"path": str(blend_path), "sha256": sha256(blend_path)},
        },
        "exportWarnings": export_result.warnings,
        "deliveryStatus": "preview_only_unreal_reimport_required",
    }
    report_path = output_dir / "mesh-edit-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise RuntimeError(f"Mesh edit validation failed: {report_path}")


if __name__ == "__main__":
    main()
