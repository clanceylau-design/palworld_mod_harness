from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import load_json, sha256, write_json


def select_file(bundle: dict[str, Any], slot: str) -> dict[str, Any]:
    base_color = []
    for item in bundle.get("files", []):
        bindings = item.get("bindings", [])
        if any(binding.get("role") == "base_color" for binding in bindings):
            base_color.append(item)
    if not base_color:
        raise SystemExit("SourceBundle contains no base_color binding")
    needle = slot.casefold()
    matches = [
        item for item in base_color
        if needle in " ".join(str(binding.get("slotName", "")) for binding in item.get("bindings", [])).casefold()
        or needle in str(item.get("textureName", "")).casefold()
    ]
    if len(matches) == 1:
        return matches[0]
    if slot == "body" and not matches:
        return max(base_color, key=lambda item: int(item.get("width", 0)) * int(item.get("height", 0)))
    names = ", ".join(str(item.get("textureName")) for item in matches) or "none"
    raise SystemExit(f"Slot selector must resolve to exactly one base-color texture; matches: {names}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare one UV-atlas image-editing job from Palworld artifacts.")
    parser.add_argument("--source-bundle", required=True)
    parser.add_argument("--target-manifest", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--slot", default="body")
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()

    bundle_path = Path(arguments.source_bundle).resolve()
    target_path = Path(arguments.target_manifest).resolve()
    bundle = load_json(bundle_path)
    target = load_json(target_path)
    if bundle.get("artifactType") != "SourceBundle" or target.get("artifactType") != "TargetManifest":
        raise SystemExit("Inputs must be SourceBundle and TargetManifest artifacts")
    if str(bundle.get("gameBuildId")) != str(target.get("gameBuildId")):
        raise SystemExit("SourceBundle and TargetManifest build IDs differ")
    if bundle.get("target", {}).get("palId") != target.get("target", {}).get("palId"):
        raise SystemExit("SourceBundle and TargetManifest targets differ")

    selected = select_file(bundle, arguments.slot)
    source_path = Path(selected["output"]).resolve()
    job = {
        "schemaVersion": 1,
        "artifactType": "TextureGenerationJob",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "gameBuildId": str(bundle["gameBuildId"]),
        "target": bundle["target"],
        "request": {"raw": arguments.request, "slotSelector": arguments.slot},
        "backendPolicy": {"kind": "image_edit", "rawOutputDeliverable": False},
        "parentArtifacts": {
            "sourceBundle": {"path": str(bundle_path), "sha256": sha256(bundle_path)},
            "targetManifest": {"path": str(target_path), "sha256": sha256(target_path)},
        },
        "task": {
            "source": str(source_path),
            "sourceSha256": sha256(source_path),
            "outputFilename": source_path.name,
            "width": selected["width"],
            "height": selected["height"],
            "bindings": selected.get("bindings", []),
        },
        "promptSpec": {
            "assetType": "game character base-color UV texture atlas",
            "primaryRequest": arguments.request,
            "preserve": [
                "exact UV atlas layout and island boundaries",
                "feature placement and seam alignment",
                "local light-dark shading and surface detail",
                "full-canvas coverage",
            ],
            "avoid": [
                "moving, adding, removing, rotating, or cropping UV islands",
                "text, watermark, border, background scene",
                "baked highlights, cast shadows, directional lighting",
            ],
        },
        "conditioning": {"sourceStructureWeight": 0.8, "preserveSourceAlpha": True, "resample": "lanczos"},
    }
    output = Path(arguments.output).resolve()
    write_json(output, job)
    json.dump({"job": str(output), "texture": selected.get("textureName"), "source": str(source_path)}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
