from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from common import load_json, parse_steam_acf, write_json


def check(checks: list[dict[str, Any]], name: str, status: str, message: str, **details: Any) -> None:
    item: dict[str, Any] = {"name": name, "status": status, "message": message}
    if details:
        item["details"] = details
    checks.append(item)


def run_version(executable: Path, arguments: list[str]) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            [str(executable), *arguments], check=False, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    first_line = output.splitlines()[0] if output else f"exit={completed.returncode}"
    return completed.returncode == 0, first_line


def locate_unreal_editor(configured: str | None) -> Path | None:
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    for drive in "CDEFG":
        candidates.extend(
            [
                Path(f"{drive}:/Program Files/Epic Games/UE_5.1/Engine/Binaries/Win64/UnrealEditor.exe"),
                Path(f"{drive}:/Epic Games/UE_5.1/Engine/Binaries/Win64/UnrealEditor.exe"),
            ]
        )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def unreal_version(executable: Path | None) -> dict[str, int] | None:
    if executable is None:
        return None
    version_path = executable.parents[2] / "Build" / "Build.version"
    if not version_path.is_file():
        return None
    try:
        value = load_json(version_path)
        return {
            "major": int(value["MajorVersion"]),
            "minor": int(value["MinorVersion"]),
            "patch": int(value["PatchVersion"]),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the local Palworld visual-mod toolchain.")
    parser.add_argument("--config", required=True, help="Path to toolchain.local.json")
    parser.add_argument("--output", help="Optional JSON report path")
    arguments = parser.parse_args()

    config_path = Path(arguments.config).resolve()
    config = load_json(config_path)
    checks: list[dict[str, Any]] = []
    game_root = Path(config["game"]["root"])
    manifest_path = Path(config["game"]["manifest"])
    pak_path = game_root / "Pal" / "Content" / "Paks" / "Pal-Windows.pak"
    game_executable = game_root / "Pal" / "Binaries" / "Win64" / "Palworld-Win64-Shipping.exe"

    if game_root.is_dir() and game_executable.is_file() and pak_path.is_file():
        check(checks, "palworld", "pass", "Steam Palworld installation is readable.", root=str(game_root), pakBytes=pak_path.stat().st_size)
    else:
        check(checks, "palworld", "block", "Palworld root, executable, or primary Pak is missing.", root=str(game_root))

    build_id: str | None = None
    if manifest_path.is_file():
        manifest = parse_steam_acf(manifest_path)
        build_id = manifest.get("buildid")
        check(checks, "steam-manifest", "pass", "Steam manifest is readable.", buildId=build_id, manifest=str(manifest_path))
    else:
        check(checks, "steam-manifest", "block", "Steam app manifest is missing.", manifest=str(manifest_path))

    tools = config.get("tools", {})
    repak = Path(tools.get("repak") or "")
    if repak.is_file():
        ok, version = run_version(repak, ["--version"])
        if ok and pak_path.is_file():
            info_ok, info = run_version(repak, ["info", str(pak_path)])
            check(checks, "repak", "pass" if info_ok else "block", version if info_ok else info, executable=str(repak))
        else:
            check(checks, "repak", "block", version, executable=str(repak))
    else:
        check(checks, "repak", "block", "repak executable is missing.", executable=str(repak))

    blender = Path(tools.get("blender") or "")
    if blender.is_file():
        ok, version = run_version(blender, ["--version"])
        check(checks, "blender", "pass" if ok else "block", version, executable=str(blender))
    else:
        check(checks, "blender", "block", "Blender executable is missing.", executable=str(blender))

    fmodel = Path(tools.get("fmodel") or "")
    check(checks, "fmodel", "pass" if fmodel.is_file() else "block", "FModel executable is present." if fmodel.is_file() else "FModel executable is missing.", executable=str(fmodel))

    ue4ss_root = Path(tools.get("ue4ssRoot") or "")
    ue4ss_proxy = game_root / "Pal" / "Binaries" / "Win64" / "dwmapi.dll"
    ue4ss_ready = (
        ue4ss_proxy.is_file()
        and (ue4ss_root / "UE4SS.dll").is_file()
        and (ue4ss_root / "MemberVariableLayout.ini").is_file()
        and (ue4ss_root / "Mods" / "Keybinds" / "Scripts" / "main.lua").is_file()
    )
    check(
        checks,
        "ue4ss-mapping-dumper",
        "pass" if ue4ss_ready else "warn",
        "Palworld-compatible UE4SS and the USMAP keybind are installed." if ue4ss_ready else "UE4SS mapping dumper is not installed or is incomplete.",
        root=str(ue4ss_root),
        proxy=str(ue4ss_proxy),
    )

    addon_root = Path(tools.get("blenderUserRoot") or "") / "scripts" / "addons" / "io_scene_psk_psa"
    addon_ready = (addon_root / "__init__.py").is_file()
    check(checks, "psk-psa-addon", "pass" if addon_ready else "block", "PSK/PSA add-on is installed in the managed Blender user root." if addon_ready else "Run configure_blender_addon.py.", path=str(addon_root))

    unreal = locate_unreal_editor(tools.get("unrealEditor"))
    unreal_version_value = unreal_version(unreal)
    allow_experimental_unreal = bool(tools.get("allowExperimentalUnrealEditor", False))
    unreal_exact = unreal_version_value == {"major": 5, "minor": 1, "patch": 1}
    unreal_usable = unreal is not None and unreal_version_value is not None and (
        unreal_exact or allow_experimental_unreal
    )
    version_label = (
        f"{unreal_version_value['major']}.{unreal_version_value['minor']}.{unreal_version_value['patch']}"
        if unreal_version_value
        else None
    )
    check(
        checks,
        "unreal-editor",
        "pass" if unreal_usable else "block",
        (
            "Exact Unreal Editor 5.1.1 installation was found."
            if unreal_exact
            else "A non-matching Unreal Editor is explicitly enabled for experimental import/cook validation."
            if unreal_usable
            else "The configured Unreal Editor version could not be verified."
            if unreal is not None
            else "Unreal Editor 5.1.1 is not installed or configured."
        ),
        executable=str(unreal) if unreal else None,
        version=version_label,
        experimental=bool(unreal_usable and not unreal_exact),
    )
    check(
        checks,
        "palworld-cook-compatibility",
        "pass" if unreal_exact else "warn",
        (
            "Editor version exactly matches Palworld's required UE 5.1.1 cook contract."
            if unreal_exact
            else "The configured editor does not match UE 5.1.1; its cooked SkeletalMesh compatibility with Palworld is unverified."
        ),
        required="5.1.1",
        actual=version_label,
    )

    mapping_value = tools.get("mappingFile")
    generated_mapping = game_root / "Pal" / "Binaries" / "Win64" / "Mappings.usmap"
    mapping = Path(mapping_value) if mapping_value else (generated_mapping if generated_mapping.is_file() else None)
    mapping_ready = bool(mapping and mapping.is_file())
    check(checks, "mappings", "pass" if mapping_ready else "warn", "A local mappings file is configured." if mapping_ready else "No build-matched mappings.usmap is configured; package-path registry scanning still works, but deep UAsset metadata extraction is unavailable.", path=str(mapping) if mapping else None)

    dotnet = Path(tools.get("dotnet") or "")
    extractor = Path(tools.get("palworldDataExtractor") or "")
    extractor_ready = dotnet.is_file() and extractor.is_file()
    if dotnet.is_file():
        dotnet_ok, dotnet_version = run_version(dotnet, ["--version"])
    else:
        dotnet_ok, dotnet_version = False, "Configured .NET runtime is missing."
    check(
        checks,
        "palworld-data-extractor",
        "pass" if extractor_ready and dotnet_ok else "warn",
        f"Build-matched DataTable extractor is available with .NET {dotnet_version}." if extractor_ready and dotnet_ok else "The build-matched DataTable extractor is not ready.",
        dotnet=str(dotnet),
        extractor=str(extractor),
    )

    asset_metadata_extractor = Path(tools.get("palworldAssetMetadata") or "")
    asset_metadata_ready = dotnet_ok and asset_metadata_extractor.is_file()
    check(
        checks,
        "palworld-asset-metadata",
        "pass" if asset_metadata_ready else "warn",
        "Primary SkeletalMesh metadata extractor is available." if asset_metadata_ready else "The primary SkeletalMesh metadata extractor is not ready.",
        extractor=str(asset_metadata_extractor),
    )

    texture_exporter = Path(tools.get("palworldTextureExport") or "")
    texture_export_ready = dotnet_ok and texture_exporter.is_file()
    check(
        checks,
        "palworld-texture-export",
        "pass" if texture_export_ready else "warn",
        "Target-scoped Texture2D PNG exporter is available." if texture_export_ready else "The Texture2D PNG exporter is not ready.",
        exporter=str(texture_exporter),
    )

    mesh_exporter = Path(tools.get("palworldMeshExport") or "")
    mesh_export_ready = dotnet_ok and mesh_exporter.is_file()
    check(
        checks,
        "palworld-mesh-export",
        "pass" if mesh_export_ready else "warn",
        "Target SkeletalMesh PSK exporter is available." if mesh_export_ready else "The SkeletalMesh PSK exporter is not ready.",
        exporter=str(mesh_exporter),
    )

    dds_python = Path(tools.get("ue4DdsPython") or "")
    dds_main = Path(tools.get("ue4DdsMain") or "")
    dds_ready = dds_python.is_file() and dds_main.is_file()
    check(
        checks,
        "ue4-dds-tools",
        "pass" if dds_ready else "warn",
        "UE5 cooked Texture2D injection tools are available." if dds_ready else "UE4-DDS-Tools is not configured.",
        python=str(dds_python),
        main=str(dds_main),
    )

    target_compiler = Path(__file__).resolve().parent / "prepare_target_manifest.py"
    target_compiler_ready = target_compiler.is_file()
    check(
        checks,
        "target-manifest-compiler",
        "pass" if target_compiler_ready else "warn",
        "Natural-language AssetSpec and TargetManifest compiler is available." if target_compiler_ready else "The target manifest compiler is missing.",
        script=str(target_compiler),
    )

    texture_validator = Path(__file__).resolve().parent / "validate_texture_candidate.py"
    pillow_ready = importlib.util.find_spec("PIL") is not None
    texture_validator_ready = texture_validator.is_file() and pillow_ready
    check(
        checks,
        "texture-candidate-validator",
        "pass" if texture_validator_ready else "warn",
        "Pre-import texture candidate validator and Pillow are available." if texture_validator_ready else "The texture candidate validator or Pillow dependency is missing.",
        script=str(texture_validator),
        pillow=pillow_ready,
    )

    uv_edit_skill = Path(__file__).resolve().parents[2] / "palworld-edit-uv-texture"
    uv_edit_ready = (
        pillow_ready
        and (uv_edit_skill / "SKILL.md").is_file()
        and (uv_edit_skill / "scripts" / "prepare_generation_job.py").is_file()
        and (uv_edit_skill / "scripts" / "condition_candidate.py").is_file()
    )
    check(
        checks,
        "uv-texture-edit-skill",
        "pass" if uv_edit_ready else "warn",
        "Base-color generation job and UV conditioning skill are available." if uv_edit_ready else "The UV texture editing child skill is incomplete.",
        path=str(uv_edit_skill),
    )

    preview_pipeline = Path(__file__).resolve().parent / "preview_texture_candidate.py"
    mesh_customizer = Path(__file__).resolve().parent / "customize_skeletal_mesh.py"
    texture_packager = Path(__file__).resolve().parent / "package_texture_mod.py"
    preview_ready = mesh_export_ready and blender.is_file() and addon_ready and preview_pipeline.is_file()
    constrained_mesh_ready = preview_ready and mesh_customizer.is_file()
    texture_packaging_ready = repak.is_file() and dds_ready and texture_packager.is_file() and pillow_ready
    check(
        checks,
        "skeletal-preview-pipeline",
        "pass" if preview_ready else "warn",
        "One-command texture-to-original-skeleton preview pipeline is available." if preview_ready else "The skeletal preview pipeline is incomplete.",
        script=str(preview_pipeline),
    )
    check(
        checks,
        "constrained-mesh-editing",
        "pass" if constrained_mesh_ready else "warn",
        "Topology-preserving weighted-region mesh editing is available." if constrained_mesh_ready else "The constrained mesh editing pipeline is incomplete.",
        script=str(mesh_customizer),
    )
    check(
        checks,
        "texture-mod-packaging",
        "pass" if texture_packaging_ready else "warn",
        "Cooked Texture2D injection and V11 Pak packaging are available." if texture_packaging_ready else "The texture-only Mod packager is incomplete.",
        script=str(texture_packager),
    )

    disk_target = Path(tools.get("blenderUserRoot") or config_path.parent)
    disk_probe = disk_target if disk_target.exists() else disk_target.parent
    usage = shutil.disk_usage(disk_probe)
    free_gib = round(usage.free / (1024 ** 3), 2)
    check(checks, "workspace-disk", "pass" if free_gib >= 20 else "warn", f"{free_gib} GiB free on tool workspace volume.", path=str(disk_probe))

    blockers = [item["name"] for item in checks if item["status"] == "block"]
    report = {
        "schemaVersion": 1,
        "config": str(config_path),
        "gameBuildId": build_id,
        "ready": not blockers,
        "phaseReadiness": {
            "registryPathScan": not any(name in blockers for name in ("palworld", "steam-manifest", "repak")),
            "modelEditing": not any(name in blockers for name in ("blender", "psk-psa-addon")),
            "unrealBuild": unreal_usable,
            "palworldCompatibleUnrealCook": unreal_exact,
            "gameplayMetadataExtraction": mapping_ready and extractor_ready and dotnet_ok,
            "deepAssetMetadataPrerequisites": mapping_ready and fmodel.is_file(),
            "deepAssetMetadata": mapping_ready and asset_metadata_ready,
            "targetManifestCompilation": target_compiler_ready,
            "sourceBundleExport": mapping_ready and texture_export_ready,
            "textureCandidateValidation": texture_validator_ready,
            "textureGenerationConditioning": uv_edit_ready,
            "skeletalMeshPreview": preview_ready,
            "constrainedMeshEditing": constrained_mesh_ready,
            "textureModPackaging": texture_packaging_ready,
            "skeletalMeshExperimentalImport": unreal_usable and constrained_mesh_ready,
            "skeletalMeshModPackaging": False,
            "mappingDumpRuntime": ue4ss_ready,
        },
        "blockers": blockers,
        "checks": checks,
    }
    if arguments.output:
        write_json(arguments.output, report)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if not blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())
