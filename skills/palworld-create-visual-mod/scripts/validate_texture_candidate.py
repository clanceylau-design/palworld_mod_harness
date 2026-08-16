from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from common import load_json, write_json


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def has_alpha(image: Image.Image) -> bool:
    return "A" in image.getbands()


def add_check(checks: list[dict[str, Any]], name: str, passed: bool, message: str, **details: Any) -> None:
    item: dict[str, Any] = {"name": name, "status": "pass" if passed else "fail", "message": message}
    if details:
        item["details"] = details
    checks.append(item)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generated PNG textures against a SourceBundle contract.")
    parser.add_argument("--source-bundle", required=True)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--output", required=True, help="ValidationReport JSON path")
    arguments = parser.parse_args()

    bundle_path = Path(arguments.source_bundle).resolve()
    candidate_dir = Path(arguments.candidate_dir).resolve()
    output_path = Path(arguments.output).resolve()
    bundle = load_json(bundle_path)
    if bundle.get("artifactType") != "SourceBundle":
        raise SystemExit(f"Not a SourceBundle: {bundle_path}")

    checks: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for source in bundle.get("files", []):
        source_path = Path(source["output"]).resolve()
        candidate_path = candidate_dir / source_path.name
        label = source_path.name
        exists = candidate_path.is_file()
        add_check(checks, f"{label}:exists", exists, "Candidate PNG is present." if exists else "Candidate PNG is missing.", path=str(candidate_path))
        if not exists:
            continue
        try:
            with Image.open(source_path) as source_image, Image.open(candidate_path) as candidate_image:
                source_image.load()
                candidate_image.load()
                is_png = candidate_image.format == "PNG"
                dimensions_match = candidate_image.size == source_image.size == (source["width"], source["height"])
                supported_mode = candidate_image.mode in ("RGB", "RGBA")
                alpha_match = has_alpha(candidate_image) == has_alpha(source_image)
                add_check(checks, f"{label}:format", is_png, "Candidate is a PNG." if is_png else f"Expected PNG, got {candidate_image.format}.")
                add_check(checks, f"{label}:dimensions", dimensions_match, "Dimensions match the source contract." if dimensions_match else "Dimensions differ from the source contract.", expected=list(source_image.size), actual=list(candidate_image.size))
                add_check(checks, f"{label}:mode", supported_mode, "Color mode is import-safe." if supported_mode else f"Unsupported color mode {candidate_image.mode}.", actual=candidate_image.mode)
                add_check(checks, f"{label}:alpha", alpha_match, "Alpha-channel presence matches the source." if alpha_match else "Alpha-channel presence differs from the source.", expected=has_alpha(source_image), actual=has_alpha(candidate_image))
                roles = sorted({binding.get("role", "unknown") for binding in source.get("bindings", [])})
                if "normal" in roles:
                    normal_safe = candidate_image.mode in ("RGB", "RGBA") and not bool(candidate_image.info.get("srgb"))
                    add_check(checks, f"{label}:normal-map", normal_safe, "Normal map has import-safe channels and no embedded sRGB chunk." if normal_safe else "Normal map must use RGB/RGBA channels without an embedded sRGB chunk.")
                candidates.append({
                    "path": str(candidate_path),
                    "sha256": sha256(candidate_path),
                    "width": candidate_image.width,
                    "height": candidate_image.height,
                    "mode": candidate_image.mode,
                    "roles": roles,
                })
        except (OSError, ValueError) as error:
            add_check(checks, f"{label}:decode", False, f"Candidate could not be decoded: {error}")

    failures = [item for item in checks if item["status"] == "fail"]
    report = {
        "schemaVersion": 1,
        "artifactType": "ValidationReport",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "gameBuildId": str(bundle.get("gameBuildId")),
        "validationScope": "texture_candidate",
        "status": "pass" if not failures else "fail",
        "parentArtifacts": {"sourceBundle": {"path": str(bundle_path), "sha256": sha256(bundle_path)}},
        "target": bundle.get("target", {}),
        "candidateDirectory": str(candidate_dir),
        "candidates": candidates,
        "summary": {"checkCount": len(checks), "passed": len(checks) - len(failures), "failed": len(failures)},
        "checks": checks,
    }
    write_json(output_path, report)
    json.dump({"report": str(output_path), **report["summary"], "status": report["status"]}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if not failures else 6


if __name__ == "__main__":
    raise SystemExit(main())
