from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description="Export a frozen hybrid-surface Pal model to PSK and reimport it for validation.")
    parser.add_argument("--surface-contract", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args(argv)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def uv_bounds(obj) -> list[float]:
    layer = obj.data.uv_layers.active
    values = [loop.uv for loop in layer.data]
    return [min(v.x for v in values), min(v.y for v in values), max(v.x for v in values), max(v.y for v in values)]


def main() -> None:
    args = parse_args()
    contract_path = Path(args.surface_contract).resolve()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    attachment_entries = contract["attachmentSurface"]["entries"]
    attachment_names = [entry["object"] for entry in attachment_entries]
    attachments = [bpy.data.objects.get(name) for name in attachment_names]
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    if any(obj is None for obj in attachments) or len(armatures) != 1:
        raise RuntimeError("Frozen Blend does not match its attachment/armature contract")
    armature = armatures[0]

    armor_material = bpy.data.materials.get("M_MechanicalArmor") or bpy.data.materials.new("M_MechanicalArmor")
    for obj in attachments:
        obj.data.materials.clear()
        obj.data.materials.append(armor_material)
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    attachment_uv_matches = {}
    for entry, obj in zip(attachment_entries, attachments):
        actual = uv_bounds(obj)
        expected = entry["uvBounds"]
        attachment_uv_matches[obj.name] = all(abs(a - b) <= 1e-5 for a, b in zip(actual, expected))

    bpy.ops.preferences.addon_enable(module="io_scene_psk_psa")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = max(meshes, key=lambda obj: len(obj.data.vertices))
    from io_scene_psk_psa.psk.builder import PskBuildOptions, build_psk
    from io_scene_psk_psa.psk.writer import write_psk

    options = PskBuildOptions()
    options.bone_filter_mode = "ALL"
    options.use_raw_mesh_data = True
    options.materials = []
    for obj in meshes:
        for slot in obj.material_slots:
            if slot.material and slot.material not in options.materials:
                options.materials.append(slot.material)
    export_result = build_psk(bpy.context, options)
    psk_path = output / "SK_ChickenPal_Mechanical_Final.psk"
    write_psk(export_result.psk, str(psk_path))
    export_materials = [material.name for material in options.materials]
    expected_vertices = len(export_result.psk.points)
    expected_faces = len(export_result.psk.faces)
    expected_bones = len(export_result.psk.bones)

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    result = bpy.ops.import_scene.psk(
        filepath=str(psk_path), should_import_vertex_colors=True, vertex_color_space="SRGBA",
        should_import_vertex_normals=True, should_import_extra_uvs=True, should_import_mesh=True,
        should_import_materials=True, should_import_skeleton=True, bone_length=1.0,
        should_import_shape_keys=True, scale=1.0,
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"Final PSK reimport failed: {result}")
    imported_meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    imported_armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    if len(imported_meshes) != 1 or len(imported_armatures) != 1:
        raise RuntimeError("Final PSK did not reimport as one mesh and one armature")
    imported = imported_meshes[0]
    imported_materials = [slot.material.name for slot in imported.material_slots if slot.material]
    checks = {
        "attachmentUvMatchesFrozenContract": all(attachment_uv_matches.values()),
        "pskWritten": psk_path.is_file(),
        "vertexCountRoundTrip": len(imported.data.vertices) == expected_vertices,
        "faceCountRoundTrip": len(imported.data.polygons) == expected_faces,
        "boneCountRoundTrip": len(imported_armatures[0].data.bones) == expected_bones,
        "armorMaterialRoundTrip": "M_MechanicalArmor" in imported_materials,
        "materialSetRoundTrip": set(imported_materials) == set(export_materials),
        "uvPresentAfterRoundTrip": len(imported.data.uv_layers) > 0,
    }
    blend_path = output / "SK_ChickenPal_Mechanical_Final_reimport.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), check_existing=False)
    report = {
        "schemaVersion": 1,
        "artifactType": "FinalizedSkeletalMeshInterchangeReport",
        "status": "pass" if all(checks.values()) else "fail",
        "gameBuildId": contract["gameBuildId"],
        "palId": contract["palId"],
        "surfaceContract": {"path": str(contract_path), "sha256": sha256(contract_path)},
        "geometry": {"vertices": expected_vertices, "faces": expected_faces, "bonesIncludingSockets": expected_bones},
        "materials": export_materials,
        "attachmentUvChecks": attachment_uv_matches,
        "checks": checks,
        "warnings": export_result.warnings,
        "outputs": {
            "psk": {"path": str(psk_path), "sha256": sha256(psk_path)},
            "reimportBlend": {"path": str(blend_path), "sha256": sha256(blend_path)},
        },
        "deliveryStatus": "finalized_psk_roundtrip_complete_unreal_5_1_1_reimport_cook_required",
    }
    report_path = output / "finalized-psk-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise RuntimeError("Finalized PSK round-trip validation failed")


if __name__ == "__main__":
    main()
