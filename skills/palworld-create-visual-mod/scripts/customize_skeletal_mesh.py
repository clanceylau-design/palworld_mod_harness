from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from common import load_json, parse_steam_acf, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a constrained Palworld skeletal-mesh edit and preview it.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--target-manifest", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--resolution", type=int, default=768)
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


def run_checked(arguments: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(arguments, capture_output=True, text=True, env=env, check=False)
    if process.returncode != 0:
        raise RuntimeError(
            f"Command failed ({process.returncode}): {arguments[0]}\n"
            f"stdout:\n{process.stdout}\nstderr:\n{process.stderr}"
        )
    return process


def main() -> int:
    args = parse_args()
    config_path = require_file(args.config, "toolchain config")
    target_path = require_file(args.target_manifest, "TargetManifest")
    spec_path = require_file(args.spec, "mesh edit spec")
    candidate_value = Path(args.candidate).resolve()
    candidate_path = candidate_value / "generated-candidate.json" if candidate_value.is_dir() else candidate_value
    candidate_path = require_file(candidate_path, "GeneratedCandidate")
    candidate_root = candidate_path.parent

    config = load_json(config_path)
    target = load_json(target_path)
    candidate = load_json(candidate_path)
    spec = load_json(spec_path)
    build = parse_steam_acf(config["game"]["manifest"]).get("buildid")
    if any(str(value) != build for value in (target["gameBuildId"], candidate["gameBuildId"])):
        raise RuntimeError("Installed, target, and candidate build IDs do not match")
    if spec["palId"] != target["target"]["palId"] or candidate["target"]["palId"] != spec["palId"]:
        raise RuntimeError("TargetManifest, GeneratedCandidate, and edit spec refer to different Pals")

    tools = config["tools"]
    dotnet = require_file(tools["dotnet"], ".NET host")
    exporter = require_file(tools["palworldMeshExport"], "PalworldMeshExport")
    mapping = require_file(tools["mappingFile"], "Mappings.usmap")
    blender = require_file(tools["blender"], "Blender")
    pak_directory = Path(config["game"]["root"]) / "Pal" / "Content" / "Paks"

    output_root = Path(args.output).resolve()
    source_export = output_root / "source-export"
    edit_output = output_root / "edit"
    preview_output = output_root / "preview"
    for directory in (source_export, edit_output, preview_output):
        directory.mkdir(parents=True, exist_ok=True)

    mesh_path = target["assets"]["primarySkeletalMesh"]
    run_checked([str(dotnet), str(exporter), str(pak_directory), str(mapping), str(source_export), mesh_path])
    source_matches = list(source_export.rglob(f"{Path(mesh_path).stem}.psk"))
    if len(source_matches) != 1:
        raise RuntimeError(f"Expected one source PSK, found {len(source_matches)}")
    source_psk = source_matches[0]

    environment = os.environ.copy()
    environment["BLENDER_USER_SCRIPTS"] = str(Path(tools["blenderUserRoot"]) / "scripts")
    edit_script = Path(__file__).with_name("blender_customize_skeletal_mesh.py")
    run_checked(
        [
            str(blender), "--background", "--factory-startup", "--python", str(edit_script), "--",
            "--psk", str(source_psk), "--spec", str(spec_path), "--target-manifest", str(target_path),
            "--output-dir", str(edit_output),
        ],
        environment,
    )
    edit_report_path = require_file(edit_output / "mesh-edit-report.json", "mesh edit report")
    edit_report = load_json(edit_report_path)
    if edit_report.get("status") != "pass":
        raise RuntimeError("Constrained mesh edit failed validation")
    edited_psk = require_file(edit_report["outputs"]["psk"]["path"], "edited PSK")

    geometry = target["sourceGeometry"]
    render_script = Path(__file__).with_name("blender_render_skeletal_preview.py")
    run_checked(
        [
            str(blender), "--background", "--factory-startup", "--python", str(render_script), "--",
            "--psk", str(edited_psk), "--candidate-dir", str(candidate_root),
            "--target-manifest", str(target_path), "--output-dir", str(preview_output),
            "--pal-id", target["target"]["palId"], "--build-id", f"build-{build}",
            "--expected-vertices", str(edit_report["outputGeometry"]["vertices"]),
            "--expected-bones", str(geometry["boneCount"]), "--resolution", str(args.resolution),
        ],
        environment,
    )
    preview_report_path = require_file(preview_output / "preview-report.json", "customized preview report")
    preview_report = load_json(preview_report_path)
    if preview_report.get("status") != "pass":
        raise RuntimeError("Customized mesh did not pass re-import preview validation")

    report = {
        "schemaVersion": 1,
        "artifactType": "ConstrainedSkeletalMeshPipelineReport",
        "status": "pass",
        "gameBuildId": build,
        "palId": target["target"]["palId"],
        "sourcePsk": {"path": str(source_psk), "sha256": sha256(source_psk)},
        "editReport": {"path": str(edit_report_path), "sha256": sha256(edit_report_path)},
        "editedPsk": {"path": str(edited_psk), "sha256": sha256(edited_psk)},
        "previewReport": {"path": str(preview_report_path), "sha256": sha256(preview_report_path)},
        "deliveryStatus": "preview_only_unreal_reimport_required",
    }
    report_path = output_root / "mesh-pipeline-report.json"
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
