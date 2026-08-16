from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description="Create a non-overlapping attachment atlas and bake geometry guides.")
    parser.add_argument("--edit-report", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--atlas-size", type=int, required=True)
    return parser.parse_args(argv)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def uv_bounds(obj) -> tuple[float, float, float, float]:
    layer = obj.data.uv_layers.active
    values = [loop.uv for loop in layer.data]
    return (
        min(value.x for value in values), min(value.y for value in values),
        max(value.x for value in values), max(value.y for value in values),
    )


def unwrap_into_cell(obj, index: int, columns: int, rows: int, padding: float = 0.035) -> dict:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.03, area_weight=0.0)
    bpy.ops.object.mode_set(mode="OBJECT")

    low_u, low_v, high_u, high_v = uv_bounds(obj)
    span_u = max(high_u - low_u, 1e-8)
    span_v = max(high_v - low_v, 1e-8)
    column = index % columns
    row = index // columns
    cell_low_u = column / columns + padding / columns
    cell_low_v = row / rows + padding / rows
    cell_high_u = (column + 1) / columns - padding / columns
    cell_high_v = (row + 1) / rows - padding / rows
    scale = min((cell_high_u - cell_low_u) / span_u, (cell_high_v - cell_low_v) / span_v)
    used_u = span_u * scale
    used_v = span_v * scale
    offset_u = cell_low_u + ((cell_high_u - cell_low_u) - used_u) * 0.5
    offset_v = cell_low_v + ((cell_high_v - cell_low_v) - used_v) * 0.5
    for loop in obj.data.uv_layers.active.data:
        loop.uv.x = offset_u + (loop.uv.x - low_u) * scale
        loop.uv.y = offset_v + (loop.uv.y - low_v) * scale
    obj.data.update()
    final = uv_bounds(obj)
    return {
        "object": obj.name,
        "cell": {"column": column, "row": row, "columns": columns, "rows": rows},
        "uvBounds": [round(value, 8) for value in final],
        "inUnitSquare": final[0] >= 0 and final[1] >= 0 and final[2] <= 1 and final[3] <= 1,
    }


def make_bake_material(name: str, image, mode: str, color=(1.0, 1.0, 1.0, 1.0)):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    image_node = nodes.new("ShaderNodeTexImage")
    image_node.image = image
    image_node.select = True
    nodes.active = image_node
    output = nodes.new("ShaderNodeOutputMaterial")
    if mode == "EMIT":
        emission = nodes.new("ShaderNodeEmission")
        emission.inputs["Color"].default_value = color
        material.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    else:
        shader = nodes.new("ShaderNodeBsdfPrincipled")
        material.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def bake_map(objects: list, output: Path, size: int, name: str, bake_type: str, colors=None) -> Path:
    image = bpy.data.images.new(name, width=size, height=size, alpha=False, float_buffer=False)
    image.generated_color = (0.0, 0.0, 0.0, 1.0)
    image.file_format = "PNG"
    image.filepath_raw = str(output / f"{name}.png")
    colors = colors or [(1.0, 1.0, 1.0, 1.0)] * len(objects)
    for index, obj in enumerate(objects):
        material = make_bake_material(
            f"Bake_{name}_{index}", image, "EMIT" if bake_type == "EMIT" else "SURFACE", colors[index]
        )
        obj.data.materials.clear()
        obj.data.materials.append(material)

    for index, obj in enumerate(objects):
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.bake(type=bake_type, margin=8, use_clear=index == 0)
    image.save()
    return Path(image.filepath_raw)


def main() -> None:
    args = parse_args()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    edit_report_path = Path(args.edit_report).resolve()
    edit_report = json.loads(edit_report_path.read_text(encoding="utf-8"))
    attachment_names = edit_report.get("attachments", [])
    attachments = [bpy.data.objects.get(name) for name in attachment_names]
    if not attachments or any(obj is None or obj.type != "MESH" for obj in attachments):
        raise RuntimeError("The edited Blend does not contain every attachment named by the edit report")

    base_meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.name not in attachment_names]
    base_uv_before = {obj.name: uv_bounds(obj) for obj in base_meshes if obj.data.uv_layers.active}
    columns = math.ceil(math.sqrt(len(attachments)))
    rows = math.ceil(len(attachments) / columns)
    atlas_entries = [unwrap_into_cell(obj, index, columns, rows) for index, obj in enumerate(attachments)]
    base_uv_after = {obj.name: uv_bounds(obj) for obj in base_meshes if obj.data.uv_layers.active}

    bpy.context.scene.render.engine = "BLENDER_EEVEE_NEXT"
    # Blender baking is exposed only through Cycles. CPU keeps this deterministic
    # on machines without a configured CUDA/OptiX device.
    bpy.context.scene.render.engine = "CYCLES"
    bpy.context.scene.cycles.device = "CPU"
    bpy.context.scene.cycles.samples = 1
    coverage = bake_map(attachments, output, args.atlas_size, "T_ChickenPal_Armor_UVCoverage", "EMIT")
    id_colors = [
        (0.85, 0.12, 0.12, 1.0),
        (0.12, 0.78, 0.20, 1.0),
        (0.12, 0.30, 0.88, 1.0),
        (0.95, 0.48, 0.04, 1.0),
    ]
    material_id = bake_map(
        attachments, output, args.atlas_size, "T_ChickenPal_Armor_MaterialID", "EMIT", id_colors
    )

    final_blend = output / "ChickenPal-mechanical-model-finalized.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(final_blend), check_existing=False)
    base_preserved = base_uv_before == base_uv_after
    checks = {
        "attachmentCountMatches": len(attachments) == len(attachment_names),
        "baseUvPreserved": base_preserved,
        "attachmentUvInUnitSquare": all(entry["inUnitSquare"] for entry in atlas_entries),
        "attachmentCellsUnique": len({(entry["cell"]["column"], entry["cell"]["row"]) for entry in atlas_entries}) == len(atlas_entries),
        "coverageMapWritten": coverage.is_file(),
        "materialIdMapWritten": material_id.is_file(),
    }
    report = {
        "schemaVersion": 1,
        "artifactType": "ModelSurfaceContract",
        "status": "pass" if all(checks.values()) else "fail",
        "gameBuildId": edit_report["gameBuildId"],
        "palId": edit_report["palId"],
        "workflow": "model_first_hybrid_surface",
        "geometryState": "frozen_for_texture_generation",
        "bodySurface": {"strategy": "preserve_original_uv_and_materials", "uvPreserved": base_preserved},
        "attachmentSurface": {
            "strategy": "dedicated_non_overlapping_atlas",
            "atlasSize": args.atlas_size,
            "materialContract": "M_MechanicalArmor",
            "entries": atlas_entries,
        },
        "bakeGuides": {
            "uvCoverage": {"path": str(coverage), "sha256": sha256(coverage)},
            "materialId": {"path": str(material_id), "sha256": sha256(material_id)},
            "normal": {"status": "pending_high_low_definition"},
            "ambientOcclusion": {"status": "pending_cycles_bake"},
            "curvature": {"status": "pending_implementation"},
        },
        "checks": checks,
        "outputs": {"blend": {"path": str(final_blend), "sha256": sha256(final_blend)}},
        "nextStage": "generate_body_and_armor_textures_from_frozen_model_contract",
        "deliveryStatus": "surface_contract_complete_texture_generation_pending",
    }
    report_path = output / "model-surface-contract.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise RuntimeError("Model surface contract validation failed")


if __name__ == "__main__":
    main()
