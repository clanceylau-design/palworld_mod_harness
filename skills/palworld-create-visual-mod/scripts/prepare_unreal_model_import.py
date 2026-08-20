from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assemble a deterministic Unreal 5.1.1 model-import bundle.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--target-manifest", required=True)
    parser.add_argument("--finalized-psk-report", required=True)
    parser.add_argument("--pbr-report", required=True)
    parser.add_argument("--body-texture", required=True)
    parser.add_argument("--eye-texture", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_file(value: str | Path, label: str) -> Path:
    path = Path(value).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def unreal_object_path(package_file: str) -> str:
    relative = package_file.replace("\\", "/")
    if relative.startswith("Pal/Content/"):
        relative = relative[len("Pal/Content/") :]
    relative = relative.removesuffix(".uasset")
    return f"/Game/{relative}.{Path(relative).name}"


def copy_artifact(source: Path, destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {"path": str(destination), "sha256": sha256(destination)}


def read_editor_version(editor: Path) -> str | None:
    version_path = editor.parents[2] / "Build" / "Build.version"
    if not version_path.is_file():
        return None
    try:
        value = json.loads(version_path.read_text(encoding="utf-8"))
        return f"{int(value['MajorVersion'])}.{int(value['MinorVersion'])}.{int(value['PatchVersion'])}"
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def main() -> int:
    args = parse_args()
    config_path = require_file(args.config, "toolchain config")
    target_path = require_file(args.target_manifest, "TargetManifest")
    psk_report_path = require_file(args.finalized_psk_report, "finalized PSK report")
    pbr_report_path = require_file(args.pbr_report, "attachment PBR report")
    body_texture = require_file(args.body_texture, "body Base Color")
    eye_texture = require_file(args.eye_texture, "eye Base Color")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    target = json.loads(target_path.read_text(encoding="utf-8"))
    psk_report = json.loads(psk_report_path.read_text(encoding="utf-8"))
    pbr_report = json.loads(pbr_report_path.read_text(encoding="utf-8"))
    if psk_report.get("status") != "pass" or pbr_report.get("status") != "pass":
        raise RuntimeError("Finalized PSK and PBR reports must pass")
    if len({target["gameBuildId"], psk_report["gameBuildId"], pbr_report["gameBuildId"]}) != 1:
        raise RuntimeError("Build IDs do not match")

    output = Path(args.output).resolve()
    inputs = output / "inputs"
    output.mkdir(parents=True, exist_ok=True)
    psk = require_file(psk_report["outputs"]["psk"]["path"], "finalized PSK")
    copied = {
        "skeletalMesh": copy_artifact(psk, inputs / "SK_ChickenPal.psk"),
        "bodyBaseColor": copy_artifact(body_texture, inputs / body_texture.name),
        "eyeBaseColor": copy_artifact(eye_texture, inputs / eye_texture.name),
        "armorBaseColor": copy_artifact(require_file(pbr_report["baseColor"]["path"], "armor Base Color"), inputs / Path(pbr_report["baseColor"]["path"]).name),
    }
    for key, artifact in pbr_report["outputs"].items():
        source = require_file(artifact["path"], key)
        copied[key] = copy_artifact(source, inputs / source.name)

    asset_root = target["target"]["assetRoot"].replace("Pal/Content/", "/Game/")
    skeletal_name = Path(target["assets"]["primarySkeletalMesh"]).stem
    editor = config["tools"].get("unrealEditor")
    editor_path = Path(editor) if editor else None
    editor_version = read_editor_version(editor_path) if editor_path and editor_path.is_file() else None
    exact_editor = editor_version == "5.1.1"
    experimental_editor = bool(
        editor_version is not None
        and not exact_editor
        and config["tools"].get("allowExperimentalUnrealEditor", False)
    )
    editor_ready = bool(editor_path and editor_path.is_file() and (exact_editor or experimental_editor))
    blockers = [] if editor_ready else ["A permitted Unreal Editor is not configured in config/toolchain.local.json"]
    status = (
        "ready_for_unreal_import"
        if exact_editor
        else "ready_for_experimental_unreal_import"
        if editor_ready
        else "blocked_editor_missing"
    )
    manifest = {
        "schemaVersion": 1,
        "artifactType": "UnrealModelImportManifest",
        "status": status,
        "gameBuildId": target["gameBuildId"],
        "palId": target["target"]["palId"],
        "requiredEngine": "5.1.1",
        "configuredEngine": editor_version,
        "palworldCookCompatibilityVerified": exact_editor,
        "target": {
            "contentRoot": asset_root,
            "skeletalMeshName": skeletal_name,
            "skeletalMeshObject": unreal_object_path(target["assets"]["primarySkeletalMesh"]),
            "reuseSkeletonObject": unreal_object_path(target["assets"]["skeleton"]),
            "reusePhysicsAssetObject": unreal_object_path(target["assets"]["physicsAsset"]),
        },
        "inputs": copied,
        "importRules": {
            "skeletalMesh": {
                "importMesh": True,
                "importAnimations": False,
                "useT0AsRefPose": False,
                "preserveSmoothingGroups": True,
                "createPhysicsAsset": False,
                "skeleton": "reuse target skeleton",
                "physicsAsset": "reuse target Physics Asset after import",
            },
            "materials": [
                {"slot": 0, "name": "MI_ChickenPal_Body", "strategy": "reuse_original_material_instance"},
                {"slot": 1, "name": "MI_ChickenPal_Eye", "strategy": "reuse_original_material_instance"},
                {"slot": 2, "name": "M_MechanicalArmor", "strategy": "create_validated_pbr_material"},
            ],
            "armorTextures": {
                "baseColor": {"source": copied["armorBaseColor"], "sRGB": True, "compression": "Default"},
                "normal": {"source": copied["normal"], "sRGB": False, "compression": "Normalmap"},
                "packedMRAO": {
                    "source": copied["packedMRAO"], "sRGB": False, "compression": "Masks",
                    "channels": pbr_report["packedContract"],
                },
                "emissiveMask": {"source": copied["emissiveMask"], "sRGB": False, "compression": "Masks"},
            },
        },
        "validationGates": [
            "engine_version_exact_5.1.1", "skeletal_mesh_import_success", "skeleton_reference_exact",
            "physics_asset_reference_exact", "material_slot_order_exact", "vertex_and_bone_counts_match",
            "animation_smoke_test", "cook_success", "pak_path_audit",
        ],
        "parents": {
            "targetManifest": {"path": str(target_path), "sha256": sha256(target_path)},
            "finalizedPskReport": {"path": str(psk_report_path), "sha256": sha256(psk_report_path)},
            "pbrReport": {"path": str(pbr_report_path), "sha256": sha256(pbr_report_path)},
        },
        "blockers": blockers,
        "deliveryStatus": "unreal_import_bundle_complete_editor_worker_required",
    }
    path = output / "unreal-model-import-manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
