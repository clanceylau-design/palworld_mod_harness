from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze a customized Blender model, create a dedicated attachment UV atlas, and bake geometry guides."
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--blend", required=True)
    parser.add_argument("--edit-report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--atlas-size", type=int, default=1024)
    return parser.parse_args()


def require_file(value: str | Path, label: str) -> Path:
    path = Path(value).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    if args.atlas_size < 256 or args.atlas_size > 4096 or args.atlas_size & (args.atlas_size - 1):
        raise ValueError("atlas-size must be a power of two between 256 and 4096")

    config_path = require_file(args.config, "toolchain config")
    blend_path = require_file(args.blend, "customized Blender file")
    edit_report_path = require_file(args.edit_report, "mesh edit report")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    blender = require_file(config["tools"]["blender"], "Blender")

    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).with_name("blender_finalize_attachment_uv.py")
    environment = os.environ.copy()
    environment["BLENDER_USER_SCRIPTS"] = str(Path(config["tools"]["blenderUserRoot"]) / "scripts")
    arguments = [
        str(blender), "--background", str(blend_path), "--python", str(script), "--",
        "--edit-report", str(edit_report_path), "--output-dir", str(output),
        "--atlas-size", str(args.atlas_size),
    ]
    process = subprocess.run(arguments, capture_output=True, text=True, env=environment, check=False)
    if process.returncode != 0:
        raise RuntimeError(f"Blender UV finalization failed\nstdout:\n{process.stdout}\nstderr:\n{process.stderr}")

    report_path = require_file(output / "model-surface-contract.json", "model surface contract")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "pass":
        raise RuntimeError("Model surface contract did not pass")
    report["launcherInputs"] = {
        "blend": {"path": str(blend_path), "sha256": sha256(blend_path)},
        "editReport": {"path": str(edit_report_path), "sha256": sha256(edit_report_path)},
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
