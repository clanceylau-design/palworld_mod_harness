from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Condition a generated attachment atlas to a frozen ModelSurfaceContract.")
    parser.add_argument("--surface-contract", required=True)
    parser.add_argument("--raw-image", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    contract_path = Path(args.surface_contract).resolve()
    raw_path = Path(args.raw_image).resolve()
    output = Path(args.output).resolve()
    if not contract_path.is_file() or not raw_path.is_file():
        raise FileNotFoundError("Surface contract or raw image is missing")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("status") != "pass" or contract.get("geometryState") != "frozen_for_texture_generation":
        raise RuntimeError("A passing frozen ModelSurfaceContract is required")

    coverage_path = Path(contract["bakeGuides"]["uvCoverage"]["path"])
    size = int(contract["attachmentSurface"]["atlasSize"])
    coverage = Image.open(coverage_path).convert("L")
    if coverage.size != (size, size):
        raise RuntimeError("Coverage map and surface contract dimensions differ")
    mask = coverage.point(lambda value: 255 if value >= 128 else 0)
    raw = Image.open(raw_path).convert("RGB")
    original_size = raw.size
    if raw.size != (size, size):
        raw = raw.resize((size, size), Image.Resampling.LANCZOS)
    black = Image.new("RGB", (size, size), (0, 0, 0))
    conditioned = Image.composite(raw, black, mask)

    output.mkdir(parents=True, exist_ok=True)
    texture_path = output / "T_ChickenPal_Armor_B.png"
    conditioned.save(texture_path, format="PNG", optimize=True)
    pixels = conditioned.load()
    mask_pixels = mask.load()
    outside_exact = all(
        pixels[x, y] == (0, 0, 0)
        for y in range(size)
        for x in range(size)
        if mask_pixels[x, y] == 0
    )
    checks = {
        "surfaceContractPass": True,
        "dimensionsExact": conditioned.size == (size, size),
        "modeRgb": conditioned.mode == "RGB",
        "outsideCoverageBlack": outside_exact,
        "uniqueAttachmentCells": contract["checks"]["attachmentCellsUnique"],
    }
    report = {
        "schemaVersion": 1,
        "artifactType": "AttachmentTextureCandidate",
        "status": "pass" if all(checks.values()) else "fail",
        "gameBuildId": contract["gameBuildId"],
        "palId": contract["palId"],
        "usageClassification": "model_matched_attachment_base_color",
        "parentSurfaceContract": {"path": str(contract_path), "sha256": sha256(contract_path)},
        "rawGeneratedImage": {"path": str(raw_path), "sha256": sha256(raw_path), "originalSize": list(original_size)},
        "outputTexture": {"path": str(texture_path), "sha256": sha256(texture_path), "size": [size, size], "mode": "RGB"},
        "conditioning": "resize_to_contract_then_exact_coverage_mask",
        "checks": checks,
        "remainingMaps": ["normal", "packed_metallic_roughness_ao"],
        "deliveryStatus": "blender_material_binding_and_preview_pending",
    }
    report_path = output / "attachment-texture-candidate.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
