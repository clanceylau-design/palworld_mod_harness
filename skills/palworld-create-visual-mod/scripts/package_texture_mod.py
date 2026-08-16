from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from common import load_json, parse_steam_acf, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inject one validated Base Color into its original cooked asset and build a Palworld V11 Pak."
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--target-manifest", required=True)
    parser.add_argument("--candidate", required=True, help="GeneratedCandidate JSON or its root directory")
    parser.add_argument("--output", required=True)
    parser.add_argument("--mod-name", required=True)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_file(path: str | Path, label: str) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} not found: {resolved}")
    return resolved


def run_checked(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(arguments, text=True, capture_output=True, check=False)
    if process.returncode != 0:
        raise RuntimeError(
            f"Command failed ({process.returncode}): {arguments[0]}\n"
            f"stdout:\n{process.stdout}\nstderr:\n{process.stderr}"
        )
    return process


def resolve_candidate(value: str) -> tuple[Path, dict]:
    supplied = Path(value).resolve()
    manifest = supplied / "generated-candidate.json" if supplied.is_dir() else supplied
    manifest = require_file(manifest, "GeneratedCandidate")
    return manifest, load_json(manifest)


def main() -> int:
    args = parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.mod_name):
        raise ValueError("--mod-name may contain only letters, digits, underscore, and hyphen")

    config_path = require_file(args.config, "toolchain config")
    target_path = require_file(args.target_manifest, "TargetManifest")
    candidate_path, candidate = resolve_candidate(args.candidate)
    config = load_json(config_path)
    target = load_json(target_path)

    validation_path = require_file(candidate_path.parent / "texture-validation-report.json", "texture validation")
    validation = load_json(validation_path)
    if validation.get("status") != "pass" or candidate.get("structureStatus") != "pass":
        raise RuntimeError("Candidate has not passed deterministic texture validation")
    if candidate.get("previewStatus") != "pass":
        raise RuntimeError("Candidate must pass skeletal-mesh preview before packaging")
    if target["target"]["palId"] != candidate["target"]["palId"]:
        raise RuntimeError("TargetManifest and GeneratedCandidate refer to different Pals")

    installed_build = parse_steam_acf(config["game"]["manifest"]).get("buildid")
    if installed_build != str(target["gameBuildId"]) or installed_build != str(candidate["gameBuildId"]):
        raise RuntimeError("Installed, target, and candidate build IDs do not match")

    tools = config["tools"]
    repak = require_file(tools["repak"], "repak")
    dds_python = require_file(tools["ue4DdsPython"], "UE4-DDS-Tools Python")
    dds_main = require_file(tools["ue4DdsMain"], "UE4-DDS-Tools main.py")
    source_pak = require_file(
        Path(config["game"]["root"]) / "Pal" / "Content" / "Paks" / "Pal-Windows.pak",
        "Pal-Windows.pak",
    )

    edited = candidate["editedTexture"]
    edited_png = require_file(edited["path"], "edited texture")
    texture_name = edited_png.stem
    matching = [
        binding
        for binding in target["assets"]["textureBindings"]
        if binding["textureName"] == texture_name and binding["role"] == "base_color"
    ]
    if len(matching) != 1:
        raise RuntimeError(f"Expected one base-color TargetManifest binding for {texture_name}")
    asset_path = matching[0]["texturePath"]
    asset_base = asset_path.rsplit(".", 1)[0]

    output_root = Path(args.output).resolve()
    extracted_root = output_root / "source-cooked"
    injected_root = output_root / "injected"
    validation_root = output_root / "roundtrip-validation"
    stage_root = output_root / "pak-stage"
    for directory in (extracted_root, injected_root, validation_root, stage_root):
        directory.mkdir(parents=True, exist_ok=True)

    suffixes = (".uasset", ".uexp", ".ubulk", ".uptnl")
    unpack_args = [str(repak), "unpack", "--output", str(extracted_root), "--force"]
    for suffix in suffixes:
        unpack_args.extend(("--include", f"{asset_base}{suffix}"))
    unpack_args.append(str(source_pak))
    unpack_process = run_checked(unpack_args)

    extracted_dir = extracted_root / Path(asset_base)
    source_uasset = require_file(extracted_dir.with_suffix(".uasset"), "extracted source uasset")
    source_companions = [path for path in (extracted_dir.with_suffix(s) for s in suffixes) if path.is_file()]
    if len(source_companions) < 2:
        raise RuntimeError("Cooked texture extraction did not produce its required companion files")

    check_process = run_checked(
        [str(dds_python), "-E", str(dds_main), str(source_uasset), "--mode", "check", "--version", "5.1"]
    )
    if "The version is 5.1." not in check_process.stdout:
        raise RuntimeError("Cooked texture did not uniquely validate as Unreal Engine 5.1")

    inject_process = run_checked(
        [
            str(dds_python),
            "-E",
            str(dds_main),
            str(source_uasset),
            str(edited_png),
            "--mode",
            "inject",
            "--version",
            "5.1",
            "--save_folder",
            str(injected_root),
            "--image_filter",
            "cubic",
        ]
    )
    injected_uasset = require_file(injected_root / f"{texture_name}.uasset", "injected uasset")

    export_process = run_checked(
        [
            str(dds_python),
            "-E",
            str(dds_main),
            str(injected_uasset),
            "--mode",
            "export",
            "--version",
            "5.1",
            "--export_as",
            "png",
            "--save_folder",
            str(validation_root),
        ]
    )
    roundtrip_png = require_file(validation_root / f"{texture_name}.png", "round-trip texture")
    source_image = Image.open(edited_png).convert("RGB")
    roundtrip_image = Image.open(roundtrip_png).convert("RGB")
    if source_image.size != roundtrip_image.size:
        raise RuntimeError(f"Round-trip dimensions changed: {source_image.size} -> {roundtrip_image.size}")
    statistics = ImageStat.Stat(ImageChops.difference(source_image, roundtrip_image))
    mean_absolute_error = [round(value, 6) for value in statistics.mean]
    if max(mean_absolute_error) > 8.0:
        raise RuntimeError(f"Round-trip BC compression error is unexpectedly high: {mean_absolute_error}")

    staged_asset_dir = stage_root / Path(asset_base).parent
    staged_asset_dir.mkdir(parents=True, exist_ok=True)
    injected_files = sorted(injected_root.glob(f"{texture_name}.*"))
    if not injected_files:
        raise RuntimeError("No injected cooked files were produced")
    for source in injected_files:
        shutil.copy2(source, staged_asset_dir / source.name)

    pak_path = output_root / f"{args.mod_name}_P.pak"
    pack_process = run_checked(
        [
            str(repak),
            "pack",
            "--version",
            "V11",
            "--compression",
            "Zlib",
            "--mount-point",
            "../../../",
            str(stage_root),
            str(pak_path),
        ]
    )
    info_process = run_checked([str(repak), "info", str(pak_path)])
    list_process = run_checked([str(repak), "list", str(pak_path)])
    entries = sorted(line.strip() for line in list_process.stdout.splitlines() if line.strip())
    expected_entries = sorted(
        f"{Path(asset_base).parent.as_posix()}/{source.name}" for source in injected_files
    )
    if entries != expected_entries:
        raise RuntimeError(f"Pak entry audit failed: expected {expected_entries}, got {entries}")

    report = {
        "schemaVersion": 1,
        "artifactType": "PalworldTextureModPackageReport",
        "status": "pass",
        "gameBuildId": installed_build,
        "palId": target["target"]["palId"],
        "modName": args.mod_name,
        "inputs": {
            "targetManifest": {"path": str(target_path), "sha256": sha256(target_path)},
            "generatedCandidate": {"path": str(candidate_path), "sha256": sha256(candidate_path)},
            "textureValidation": {"path": str(validation_path), "sha256": sha256(validation_path)},
            "sourcePak": {"path": str(source_pak), "sha256": sha256(source_pak)},
        },
        "cookedAsset": {
            "assetBase": asset_base,
            "sourceFiles": [{"path": str(path), "sha256": sha256(path)} for path in source_companions],
            "injectedFiles": [{"path": str(path), "sha256": sha256(path)} for path in injected_files],
            "engineVersionCheck": "5.1",
        },
        "roundTrip": {
            "path": str(roundtrip_png),
            "sha256": sha256(roundtrip_png),
            "dimensions": list(roundtrip_image.size),
            "meanAbsoluteErrorRgb": mean_absolute_error,
        },
        "package": {
            "path": str(pak_path),
            "sha256": sha256(pak_path),
            "size": pak_path.stat().st_size,
            "mountPoint": "../../../",
            "pakVersion": "V11",
            "compression": "Zlib",
            "entries": entries,
        },
        "toolEvidence": {
            "unpack": unpack_process.stdout.strip(),
            "inject": inject_process.stdout.strip(),
            "roundTripExport": export_process.stdout.strip(),
            "pack": pack_process.stdout.strip(),
            "pakInfo": info_process.stdout.strip(),
        },
        "runtimeStatus": "not_installed_or_launched",
    }
    report_path = output_root / "mod-package-report.json"
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
