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
    parser = argparse.ArgumentParser(
        description="Export a Palworld skeletal mesh and preview a validated texture candidate on it."
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--target-manifest", required=True)
    parser.add_argument("--candidate", required=True, help="GeneratedCandidate JSON or its root directory")
    parser.add_argument("--output", required=True)
    parser.add_argument("--resolution", type=int, default=768)
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


def run_checked(arguments: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(arguments, text=True, capture_output=True, env=env, check=False)
    if process.returncode != 0:
        raise RuntimeError(
            f"Command failed ({process.returncode}): {arguments[0]}\n"
            f"stdout:\n{process.stdout}\nstderr:\n{process.stderr}"
        )
    return process


def find_psk(root: Path, mesh_path: str) -> Path:
    expected = f"{Path(mesh_path).stem}.psk".lower()
    matches = [path for path in root.rglob("*.psk") if path.name.lower() == expected]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {expected} below {root}, found {len(matches)}")
    return matches[0]


def main() -> int:
    args = parse_args()
    config_path = require_file(args.config, "toolchain config")
    target_path = require_file(args.target_manifest, "TargetManifest")
    config = load_json(config_path)
    target = load_json(target_path)

    candidate_input = Path(args.candidate).resolve()
    candidate_path = candidate_input / "generated-candidate.json" if candidate_input.is_dir() else candidate_input
    candidate_path = require_file(candidate_path, "GeneratedCandidate")
    candidate = load_json(candidate_path)
    candidate_root = candidate_path.parent
    validation_path = require_file(candidate_root / "texture-validation-report.json", "texture validation report")
    validation = load_json(validation_path)

    if target.get("artifactType") != "TargetManifest":
        raise ValueError("--target-manifest is not a TargetManifest")
    if candidate.get("artifactType") != "GeneratedCandidate":
        raise ValueError("--candidate is not a GeneratedCandidate")
    if validation.get("status") != "pass" or candidate.get("structureStatus") != "pass":
        raise RuntimeError("Candidate must pass structure and texture validation before 3D preview")
    if target["target"]["palId"] != candidate["target"]["palId"]:
        raise RuntimeError("TargetManifest and GeneratedCandidate refer to different Pals")

    manifest = parse_steam_acf(config["game"]["manifest"])
    installed_build = manifest.get("buildid")
    target_build = str(target["gameBuildId"])
    candidate_build = str(candidate["gameBuildId"])
    if installed_build != target_build or installed_build != candidate_build:
        raise RuntimeError(
            f"Build mismatch: installed={installed_build}, target={target_build}, candidate={candidate_build}"
        )

    tools = config["tools"]
    dotnet = require_file(tools["dotnet"], ".NET host")
    mapping = require_file(tools["mappingFile"], "Mappings.usmap")
    blender = require_file(tools["blender"], "Blender")
    exporter_value = tools.get("palworldMeshExport")
    if not exporter_value:
        raise RuntimeError("config.tools.palworldMeshExport is required")
    exporter = require_file(exporter_value, "PalworldMeshExport")

    game_root = Path(config["game"]["root"]).resolve()
    pak_directory = game_root / "Pal" / "Content" / "Paks"
    if not pak_directory.is_dir():
        raise FileNotFoundError(f"Pak directory not found: {pak_directory}")

    output_root = Path(args.output).resolve()
    model_export = output_root / "model-export"
    preview_output = output_root / "renders"
    model_export.mkdir(parents=True, exist_ok=True)
    preview_output.mkdir(parents=True, exist_ok=True)

    mesh_path = target["assets"]["primarySkeletalMesh"]
    export_process = run_checked(
        [str(dotnet), str(exporter), str(pak_directory), str(mapping), str(model_export), mesh_path]
    )
    psk_path = find_psk(model_export, mesh_path)

    geometry = target.get("sourceGeometry") or {}
    lods = geometry.get("lods") or []
    if not lods:
        raise RuntimeError("TargetManifest has no sourceGeometry.lods")
    expected_vertices = int(lods[0]["vertices"])
    expected_bones = int(geometry["boneCount"])

    blender_script = Path(__file__).with_name("blender_render_skeletal_preview.py").resolve()
    blender_env = os.environ.copy()
    blender_env["BLENDER_USER_SCRIPTS"] = str(Path(tools["blenderUserRoot"]) / "scripts")
    blender_process = run_checked(
        [
            str(blender),
            "--background",
            "--factory-startup",
            "--python",
            str(blender_script),
            "--",
            "--psk",
            str(psk_path),
            "--candidate-dir",
            str(candidate_root),
            "--target-manifest",
            str(target_path),
            "--output-dir",
            str(preview_output),
            "--pal-id",
            target["target"]["palId"],
            "--build-id",
            f"build-{installed_build}",
            "--expected-vertices",
            str(expected_vertices),
            "--expected-bones",
            str(expected_bones),
            "--resolution",
            str(args.resolution),
        ],
        env=blender_env,
    )

    preview_report_path = require_file(preview_output / "preview-report.json", "preview report")
    preview_report = load_json(preview_report_path)
    if preview_report.get("status") != "pass":
        raise RuntimeError(f"Preview failed validation: {preview_report_path}")

    pipeline_report = {
        "schemaVersion": 1,
        "artifactType": "TextureToSkeletalPreviewPipelineReport",
        "status": "pass",
        "gameBuildId": installed_build,
        "palId": target["target"]["palId"],
        "inputs": {
            "targetManifest": {"path": str(target_path), "sha256": sha256(target_path)},
            "generatedCandidate": {"path": str(candidate_path), "sha256": sha256(candidate_path)},
            "textureValidation": {"path": str(validation_path), "sha256": sha256(validation_path)},
            "mapping": {"path": str(mapping), "sha256": sha256(mapping)},
        },
        "meshExport": {
            "path": str(psk_path),
            "sha256": sha256(psk_path),
            "stdout": export_process.stdout.strip(),
        },
        "preview": {"path": str(preview_report_path), "sha256": sha256(preview_report_path)},
        "toolOutput": {"blenderStdoutTail": blender_process.stdout[-4000:]},
    }
    report_path = output_root / "pipeline-report.json"
    write_json(report_path, pipeline_report)
    print(json.dumps(pipeline_report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
