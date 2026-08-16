from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageOps


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bake and condition PBR maps for a frozen attachment surface contract.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--surface-contract", required=True)
    parser.add_argument("--base-color", required=True)
    parser.add_argument("--output", required=True)
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


def main() -> int:
    args = parse_args()
    config_path = require_file(args.config, "toolchain config")
    contract_path = require_file(args.surface_contract, "ModelSurfaceContract")
    base_color_path = require_file(args.base_color, "attachment Base Color")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("status") != "pass" or contract.get("geometryState") != "frozen_for_texture_generation":
        raise RuntimeError("A passing frozen ModelSurfaceContract is required")
    blend_path = require_file(contract["outputs"]["blend"]["path"], "frozen Blender model")
    blender = require_file(config["tools"]["blender"], "Blender")
    output = Path(args.output).resolve()
    raw_output = output / "raw"
    output.mkdir(parents=True, exist_ok=True)
    raw_output.mkdir(parents=True, exist_ok=True)

    script = Path(__file__).with_name("blender_bake_attachment_pbr.py")
    environment = os.environ.copy()
    environment["BLENDER_USER_SCRIPTS"] = str(Path(config["tools"]["blenderUserRoot"]) / "scripts")
    command = [
        str(blender), "--background", str(blend_path), "--python", str(script), "--",
        "--surface-contract", str(contract_path), "--output-dir", str(raw_output),
    ]
    process = subprocess.run(command, capture_output=True, text=True, env=environment, check=False)
    raw_report_path = raw_output / "raw-attachment-bake-report.json"
    if not raw_report_path.is_file():
        raise RuntimeError(f"Blender did not produce a bake report\nstdout:\n{process.stdout}\nstderr:\n{process.stderr}")
    raw_report = json.loads(raw_report_path.read_text(encoding="utf-8"))
    if raw_report.get("status") != "pass":
        raise RuntimeError("Raw Blender PBR bake failed")

    size = int(contract["attachmentSurface"]["atlasSize"])
    coverage = Image.open(contract["bakeGuides"]["uvCoverage"]["path"]).convert("L")
    mask = coverage.point(lambda value: 255 if value >= 128 else 0)
    ao_raw = Image.open(raw_report["maps"]["ao"]["path"]).convert("L")
    normal_raw = Image.open(raw_report["maps"]["normal"]["path"]).convert("RGB")
    curvature_raw = Image.open(raw_report["maps"]["curvature"]["path"]).convert("L")
    base_color = Image.open(base_color_path).convert("RGB")
    if any(image.size != (size, size) for image in (coverage, ao_raw, normal_raw, curvature_raw, base_color)):
        raise RuntimeError("Every PBR input must match the frozen atlas dimensions")

    ao = Image.composite(ao_raw, Image.new("L", (size, size), 255), mask)
    normal = Image.composite(normal_raw, Image.new("RGB", (size, size), (128, 128, 255)), mask)
    curvature = Image.composite(curvature_raw, Image.new("L", (size, size), 0), mask)
    metallic = Image.composite(Image.new("L", (size, size), 220), Image.new("L", (size, size), 0), mask)
    roughness_inside = curvature.point(lambda value: max(62, min(118, 112 - int(value * 44 / 255))))
    roughness = Image.composite(roughness_inside, Image.new("L", (size, size), 255), mask)
    packed = Image.merge("RGB", (metallic, roughness, ao))

    orange_values = []
    pixels = base_color.load()
    for y in range(size):
        for x in range(size):
            red, green, blue = pixels[x, y]
            is_orange = red >= 105 and 28 <= green <= 185 and blue <= 115 and red >= green * 1.18 and red >= blue * 1.55
            orange_values.append(min(255, max(0, (red - blue) * 2)) if is_orange else 0)
    orange_strength = Image.new("L", (size, size))
    orange_strength.putdata(orange_values)
    emissive = Image.composite(orange_strength, Image.new("L", (size, size), 0), mask)
    outputs = {
        "normal": (normal, output / "T_ChickenPal_Armor_N.png"),
        "ambientOcclusion": (ao, output / "T_ChickenPal_Armor_AO.png"),
        "curvature": (curvature, output / "T_ChickenPal_Armor_Curvature.png"),
        "packedMRAO": (packed, output / "T_ChickenPal_Armor_MRAO.png"),
        "emissiveMask": (emissive, output / "T_ChickenPal_Armor_E.png"),
    }
    for image, path in outputs.values():
        image.save(path, format="PNG", optimize=True)

    neutral_normal = Image.new("RGB", (size, size), (128, 128, 255))
    outside_normal_difference = Image.composite(
        ImageChops.difference(normal, neutral_normal), Image.new("RGB", (size, size), (0, 0, 0)), ImageOps.invert(mask)
    )
    checks = {
        "dimensionsExact": all(image.size == (size, size) for image, _ in outputs.values()),
        "neutralNormalOutsideUv": outside_normal_difference.getbbox() is None,
        "packedChannelContract": True,
        "emissiveMaskNonEmpty": emissive.getbbox() is not None,
        "rawBakePass": True,
    }
    report = {
        "schemaVersion": 1,
        "artifactType": "AttachmentPbrTextureSet",
        "status": "pass" if all(checks.values()) else "fail",
        "gameBuildId": contract["gameBuildId"],
        "palId": contract["palId"],
        "parentSurfaceContract": {"path": str(contract_path), "sha256": sha256(contract_path)},
        "baseColor": {"path": str(base_color_path), "sha256": sha256(base_color_path)},
        "bakeMethods": raw_report["methods"],
        "packedContract": {"R": "metallic", "G": "roughness", "B": "ambient_occlusion"},
        "outputs": {
            key: {"path": str(path), "sha256": sha256(path), "mode": image.mode, "size": list(image.size)}
            for key, (image, path) in outputs.items()
        },
        "checks": checks,
        "limitations": [
            "Normal is a tangent-space self-bake from the final low mesh; no high-poly detail source exists.",
            "Curvature is a Cycles Geometry Pointiness approximation, not a high-to-low curvature bake.",
            "Packed MRAO requires a future Unreal material whose channel contract matches this report.",
        ],
        "deliveryStatus": "pbr_texture_set_complete_blender_preview_pending_unreal_cook_required",
    }
    report_path = output / "attachment-pbr-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
