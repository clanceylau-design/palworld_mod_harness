from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export and round-trip validate a frozen Palworld skeletal model as PSK.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--surface-contract", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def require_file(value: str | Path, label: str) -> Path:
    path = Path(value).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def main() -> int:
    args = parse_args()
    config_path = require_file(args.config, "toolchain config")
    contract_path = require_file(args.surface_contract, "ModelSurfaceContract")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    blender = require_file(config["tools"]["blender"], "Blender")
    blend = require_file(contract["outputs"]["blend"]["path"], "frozen Blender model")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).with_name("blender_export_finalized_psk.py")
    environment = os.environ.copy()
    environment["BLENDER_USER_SCRIPTS"] = str(Path(config["tools"]["blenderUserRoot"]) / "scripts")
    command = [
        str(blender), "--background", str(blend), "--python", str(script), "--",
        "--surface-contract", str(contract_path), "--output-dir", str(output),
    ]
    process = subprocess.run(command, capture_output=True, text=True, env=environment, check=False)
    report_path = output / "finalized-psk-report.json"
    if not report_path.is_file():
        raise RuntimeError(f"Blender did not produce the PSK report\nstdout:\n{process.stdout}\nstderr:\n{process.stderr}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "pass":
        raise RuntimeError(f"Finalized PSK failed round-trip validation: {report_path}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
