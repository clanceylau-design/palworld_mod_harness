from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any

from PIL import Image

from common import load_json, sha256, write_json


def correlation(first: Image.Image, second: Image.Image) -> float:
    a = list(first.convert("L").resize((256, 256), Image.Resampling.BILINEAR).get_flattened_data())
    b = list(second.convert("L").resize((256, 256), Image.Resampling.BILINEAR).get_flattened_data())
    mean_a, mean_b = fmean(a), fmean(b)
    numerator = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    denominator = math.sqrt(sum((x - mean_a) ** 2 for x in a) * sum((y - mean_b) ** 2 for y in b))
    return numerator / denominator if denominator else 1.0


def condition(source: Image.Image, generated: Image.Image, structure_weight: float) -> Image.Image:
    source_rgba = source.convert("RGBA")
    generated_rgb = generated.convert("RGB").resize(source_rgba.size, Image.Resampling.LANCZOS)
    source_hsv = source_rgba.convert("RGB").convert("HSV")
    generated_hsv = generated_rgb.convert("HSV")
    source_value = source_hsv.getchannel("V")
    generated_value = generated_hsv.getchannel("V")
    value = Image.blend(generated_value, source_value, structure_weight)
    recolored = Image.merge("HSV", (generated_hsv.getchannel("H"), generated_hsv.getchannel("S"), value)).convert("RGB")
    recolored.putalpha(source_rgba.getchannel("A"))
    return recolored


def main() -> int:
    parser = argparse.ArgumentParser(description="Condition raw image-model output into a SourceBundle-compatible texture candidate.")
    parser.add_argument("--job", required=True)
    parser.add_argument("--generated", required=True)
    parser.add_argument("--output", required=True, help="GeneratedCandidate artifact directory")
    parser.add_argument("--structure-weight", type=float)
    arguments = parser.parse_args()

    job_path = Path(arguments.job).resolve()
    generated_path = Path(arguments.generated).resolve()
    job = load_json(job_path)
    if job.get("artifactType") != "TextureGenerationJob":
        raise SystemExit(f"Not a TextureGenerationJob: {job_path}")
    source_bundle_path = Path(job["parentArtifacts"]["sourceBundle"]["path"])
    source_bundle = load_json(source_bundle_path)
    source_path = Path(job["task"]["source"])
    if sha256(source_path) != job["task"]["sourceSha256"]:
        raise SystemExit("Source texture hash differs from the generation job")
    structure_weight = arguments.structure_weight
    if structure_weight is None:
        structure_weight = float(job["conditioning"]["sourceStructureWeight"])
    if not 0.0 <= structure_weight <= 1.0:
        raise SystemExit("--structure-weight must be between 0 and 1")

    artifact_root = Path(arguments.output).resolve()
    texture_root = artifact_root / "textures"
    texture_root.mkdir(parents=True, exist_ok=True)
    edited_name = job["task"]["outputFilename"]
    passthrough: list[dict[str, Any]] = []
    for item in source_bundle.get("files", []):
        original = Path(item["output"])
        destination = texture_root / original.name
        if original.name != edited_name:
            shutil.copy2(original, destination)
            passthrough.append({"path": str(destination), "sha256": sha256(destination), "sourceSha256": sha256(original)})

    destination = texture_root / edited_name
    with Image.open(source_path) as source_image, Image.open(generated_path) as generated_image:
        source_image.load()
        generated_image.load()
        candidate = condition(source_image, generated_image, structure_weight)
        candidate.save(destination, format="PNG", optimize=True)
        luminance_correlation = correlation(source_image, candidate)
        alpha_exact = source_image.convert("RGBA").getchannel("A").tobytes() == candidate.getchannel("A").tobytes()

    structure_pass = luminance_correlation >= 0.75 and alpha_exact
    manifest = {
        "schemaVersion": 1,
        "artifactType": "GeneratedCandidate",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "gameBuildId": job["gameBuildId"],
        "target": job["target"],
        "parentArtifacts": {
            "generationJob": {"path": str(job_path), "sha256": sha256(job_path)},
            "rawGeneratedImage": {"path": str(generated_path), "sha256": sha256(generated_path)},
        },
        "candidateDirectory": str(texture_root),
        "editedTexture": {
            "path": str(destination),
            "sha256": sha256(destination),
            "source": str(source_path),
            "sourceSha256": sha256(source_path),
            "width": job["task"]["width"],
            "height": job["task"]["height"],
            "structureWeight": structure_weight,
            "luminanceCorrelation": round(luminance_correlation, 6),
            "alphaExact": alpha_exact,
        },
        "passthroughTextures": passthrough,
        "structureStatus": "pass" if structure_pass else "fail",
        "previewStatus": "pending",
    }
    manifest_path = artifact_root / "generated-candidate.json"
    write_json(manifest_path, manifest)
    json.dump({
        "manifest": str(manifest_path),
        "candidateDirectory": str(texture_root),
        "structureStatus": manifest["structureStatus"],
        "luminanceCorrelation": manifest["editedTexture"]["luminanceCorrelation"],
        "alphaExact": alpha_exact,
    }, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if structure_pass else 7


if __name__ == "__main__":
    raise SystemExit(main())
