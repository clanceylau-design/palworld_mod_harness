from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description="Bake raw AO, self-normal, and pointiness guides for frozen attachments.")
    parser.add_argument("--surface-contract", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args(argv)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_target_material(name: str, image, kind: str):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    image_node = nodes.new("ShaderNodeTexImage")
    image_node.image = image
    image_node.select = True
    nodes.active = image_node
    output = nodes.new("ShaderNodeOutputMaterial")
    if kind == "curvature":
        geometry = nodes.new("ShaderNodeNewGeometry")
        ramp = nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.elements[0].position = 0.38
        ramp.color_ramp.elements[1].position = 0.62
        emission = nodes.new("ShaderNodeEmission")
        material.node_tree.links.new(geometry.outputs["Pointiness"], ramp.inputs["Fac"])
        material.node_tree.links.new(ramp.outputs["Color"], emission.inputs["Color"])
        material.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    else:
        shader = nodes.new("ShaderNodeBsdfPrincipled")
        shader.inputs["Roughness"].default_value = 0.35
        material.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def bake(objects: list, output: Path, size: int, filename: str, bake_type: str, kind: str) -> Path:
    image = bpy.data.images.new(filename, width=size, height=size, alpha=False, float_buffer=False)
    image.colorspace_settings.name = "Non-Color"
    image.generated_color = (0.0, 0.0, 0.0, 1.0)
    image.file_format = "PNG"
    image.filepath_raw = str(output / filename)
    materials = []
    for index, obj in enumerate(objects):
        material = create_target_material(f"Bake_{kind}_{index}", image, kind)
        obj.data.materials.clear()
        obj.data.materials.append(material)
        materials.append(material)
    for index, obj in enumerate(objects):
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        kwargs = {"type": bake_type, "margin": 12, "use_clear": index == 0}
        if bake_type == "NORMAL":
            kwargs["normal_space"] = "TANGENT"
        bpy.ops.object.bake(**kwargs)
    image.save()
    return Path(image.filepath_raw)


def main() -> None:
    args = parse_args()
    contract_path = Path(args.surface_contract).resolve()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    names = [entry["object"] for entry in contract["attachmentSurface"]["entries"]]
    objects = [bpy.data.objects.get(name) for name in names]
    if any(obj is None or obj.type != "MESH" for obj in objects):
        raise RuntimeError("Frozen Blend is missing one or more contracted attachment meshes")

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 32
    scene.render.bake.use_clear = False
    size = int(contract["attachmentSurface"]["atlasSize"])
    ao = bake(objects, output, size, "T_ChickenPal_Armor_AO_raw.png", "AO", "ao")
    normal = bake(objects, output, size, "T_ChickenPal_Armor_N_raw.png", "NORMAL", "normal")
    curvature = bake(objects, output, size, "T_ChickenPal_Armor_Curvature_raw.png", "EMIT", "curvature")
    checks = {
        "attachmentCountMatches": len(objects) == len(names),
        "aoWritten": ao.is_file(),
        "normalWritten": normal.is_file(),
        "curvatureWritten": curvature.is_file(),
    }
    report = {
        "schemaVersion": 1,
        "artifactType": "RawAttachmentBakeReport",
        "status": "pass" if all(checks.values()) else "fail",
        "gameBuildId": contract["gameBuildId"],
        "palId": contract["palId"],
        "methods": {
            "ambientOcclusion": "cycles_ao_final_low_mesh",
            "normal": "cycles_tangent_self_bake_no_high_poly",
            "curvature": "cycles_geometry_pointiness_approximation",
        },
        "maps": {
            "ao": {"path": str(ao), "sha256": sha256(ao)},
            "normal": {"path": str(normal), "sha256": sha256(normal)},
            "curvature": {"path": str(curvature), "sha256": sha256(curvature)},
        },
        "checks": checks,
    }
    path = output / "raw-attachment-bake-report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise RuntimeError("Raw attachment bake failed")


if __name__ == "__main__":
    main()
