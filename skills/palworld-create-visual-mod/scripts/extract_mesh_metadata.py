from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import load_json, parse_steam_acf, write_json


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compact_metadata(asset: dict[str, Any], source: Path) -> dict[str, Any]:
    return {
        "source": str(source),
        "assetPath": asset.get("assetPath"),
        "skeleton": asset.get("skeleton"),
        "physicsAsset": asset.get("physicsAsset"),
        "materialSlots": asset.get("materialSlots", []),
        "boneCount": asset.get("boneCount"),
        "lodCount": asset.get("lodCount"),
        "lods": asset.get("lods"),
        "bounds": asset.get("bounds"),
        "morphTargetCount": asset.get("morphTargetCount"),
        "socketCount": asset.get("socketCount"),
        "hasVertexColors": asset.get("hasVertexColors"),
        "vertexColorChannels": asset.get("vertexColorChannels"),
    }


def package_asset_path(reference: dict[str, Any] | None) -> str | None:
    if not reference or not reference.get("path"):
        return None
    object_path = str(reference["path"])
    package_path = object_path.rsplit(".", 1)[0] if "." in object_path else object_path
    return package_path + ".uasset"


def infer_texture_role(parameter_name: str, texture_name: str) -> str:
    parameter = parameter_name.casefold().replace(" ", "")
    texture = texture_name.casefold()
    if "normal" in parameter or texture.endswith("_n"):
        return "normal"
    if any(token in parameter for token in ("base", "albedo", "diffuse")) or texture.endswith("_b"):
        return "base_color"
    if any(token in parameter for token in ("metallicroughness", "occlusionspecular", "mros", "orm")) or texture.endswith("_m"):
        return "packed_mros"
    if "subsurface" in parameter or "sss" in parameter or texture.endswith("_sss"):
        return "subsurface"
    if "emiss" in parameter or texture.endswith("_e"):
        return "emissive"
    if any(token in parameter for token in ("opacity", "alpha")):
        return "opacity"
    if "facial" in parameter or texture.endswith("_fm"):
        return "facial_mask"
    if "mask" in parameter:
        return "mask"
    return "unknown"


def material_metadata_for_asset(
    asset: dict[str, Any], materials: dict[str, dict[str, Any]], source: Path
) -> dict[str, Any]:
    slots: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    for slot in asset.get("materialSlots", []):
        reference = slot.get("material") or {}
        material_path = reference.get("path")
        material = materials.get(str(material_path).casefold()) if material_path else None
        slots.append({
            "slotIndex": slot.get("index"),
            "slotName": slot.get("slotName"),
            "materialPath": material_path,
            "parentChain": material.get("parentChain", []) if material else [],
        })
        if not material:
            continue
        for parameter in material.get("effectiveTextureParameters", []):
            texture = parameter.get("texture") or {}
            texture_name = str(texture.get("name") or "")
            role = infer_texture_role(str(parameter.get("name") or ""), texture_name)
            bindings.append({
                "slotIndex": slot.get("index"),
                "slotName": slot.get("slotName"),
                "materialPath": material_path,
                "parameterName": parameter.get("name"),
                "role": role,
                "textureName": texture.get("name"),
                "texturePath": package_asset_path(texture),
                "editableCandidate": role != "unknown",
            })
    return {"source": str(source), "slots": slots, "textureBindings": bindings}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deserialize Pal SkeletalMesh material slots, skeleton hierarchy, LODs, and bounds."
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--output", required=True, help="Deep-metadata artifact root")
    parser.add_argument("--enriched-registry", help="Optional registry output; may equal --registry")
    arguments = parser.parse_args()

    config = load_json(Path(arguments.config).resolve())
    registry_path = Path(arguments.registry).resolve()
    registry = load_json(registry_path)
    game_root = Path(config["game"]["root"])
    manifest_path = Path(config["game"]["manifest"])
    tools = config.get("tools", {})
    dotnet = Path(tools.get("dotnet") or "")
    extractor = Path(tools.get("palworldAssetMetadata") or "")
    mapping = Path(tools.get("mappingFile") or "")
    pak_directory = game_root / "Pal" / "Content" / "Paks"
    pak = pak_directory / "Pal-Windows.pak"
    for required in (manifest_path, registry_path, dotnet, extractor, mapping, pak):
        if not required.is_file():
            raise SystemExit(f"Required file is missing: {required}")

    build_id = parse_steam_acf(manifest_path).get("buildid")
    if not build_id or str(registry.get("gameBuildId")) != build_id:
        raise SystemExit(
            f"Registry build {registry.get('gameBuildId')} does not match installed build {build_id or 'unknown'}"
        )

    completed = subprocess.run(
        [str(dotnet), str(extractor), str(pak_directory), str(mapping), str(registry_path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode not in (0, 5):
        if completed.stderr:
            sys.stderr.write(completed.stderr)
        raise SystemExit(f"PalworldAssetMetadata failed with exit code {completed.returncode}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit(f"PalworldAssetMetadata returned invalid JSON: {error}") from error

    payload.update({
        "gameBuildId": build_id,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "pak": str(pak),
        "mappingSha256": sha256(mapping),
        "registrySha256": sha256(registry_path),
    })
    output_path = Path(arguments.output).resolve() / f"build-{build_id}" / "mesh-metadata.json"
    write_json(output_path, payload)

    if arguments.enriched_registry:
        assets = {str(asset.get("palId", "")).casefold(): asset for asset in payload.get("assets", [])}
        materials = {
            str(material.get("path", "")).casefold(): material
            for material in payload.get("materials", [])
            if material.get("path")
        }
        for pal in registry.get("pals", []):
            asset = assets.get(str(pal.get("palId", "")).casefold())
            pal["deepMetadata"] = compact_metadata(asset, output_path) if asset else None
            pal.setdefault("capabilities", {})["deepMetadata"] = "available" if asset else "unavailable"
            pal["materialMetadata"] = material_metadata_for_asset(asset, materials, output_path) if asset else None
            pal["capabilities"]["textureDependencyGraph"] = "available" if asset else "unavailable"
            if asset:
                resolved_skeleton = package_asset_path(asset.get("skeleton"))
                resolved_physics = package_asset_path(asset.get("physicsAsset"))
                if resolved_skeleton:
                    pal["skeleton"] = resolved_skeleton
                if resolved_physics:
                    pal["physicsAsset"] = resolved_physics
                pal["warnings"] = [
                    warning for warning in pal.get("warnings", [])
                    if not (resolved_skeleton and warning.startswith("No exact skeleton package"))
                    and not (resolved_physics and warning.startswith("No exact Physics Asset"))
                ]
        registry.setdefault("source", {})["meshMetadata"] = str(output_path)
        registry["source"]["meshMetadataSha256"] = sha256(output_path)
        registry.setdefault("scan", {})["deepMetadataCount"] = len(assets)
        registry["scan"]["deepMetadataMethod"] = (
            "CUE4Parse primary SkeletalMesh deserialization plus material-instance parent/parameter traversal"
        )
        registry["scan"]["materialMetadataCount"] = sum(bool(pal.get("materialMetadata")) for pal in registry.get("pals", []))
        registry["scan"]["textureBindingCount"] = sum(
            len(pal.get("materialMetadata", {}).get("textureBindings", []))
            for pal in registry.get("pals", []) if pal.get("materialMetadata")
        )
        enriched_registry_path = Path(arguments.enriched_registry).resolve()
        write_json(enriched_registry_path, registry)
        audit_path = enriched_registry_path.with_name("registry-audit.json")
        if audit_path.is_file():
            audit = load_json(audit_path)
            audit["deepMetadataCount"] = len(assets)
            audit["missingDeepMetadata"] = [
                pal.get("palId") for pal in registry.get("pals", []) if not pal.get("deepMetadata")
            ]
            audit["deepMetadataErrors"] = payload.get("errors", [])
            audit["materialMetadataCount"] = registry["scan"]["materialMetadataCount"]
            audit["textureBindingCount"] = registry["scan"]["textureBindingCount"]
            audit["materialMetadataErrors"] = payload.get("materialErrors", [])
            audit["missingSkeleton"] = [pal.get("palId") for pal in registry.get("pals", []) if not pal.get("skeleton")]
            audit["missingPhysicsAsset"] = [pal.get("palId") for pal in registry.get("pals", []) if not pal.get("physicsAsset")]
            audit["resolvedSkeletonCount"] = sum(bool(pal.get("skeleton")) for pal in registry.get("pals", []))
            audit["resolvedPhysicsAssetCount"] = sum(bool(pal.get("physicsAsset")) for pal in registry.get("pals", []))
            write_json(audit_path, audit)

    summary = {
        "gameBuildId": build_id,
        "output": str(output_path),
        "requested": payload.get("requested"),
        "extracted": payload.get("extracted"),
        "missingPrimaryMesh": payload.get("missingPrimaryMesh", []),
        "errors": payload.get("errors", []),
        "materialCount": payload.get("materialCount"),
        "materialErrors": payload.get("materialErrors", []),
        "textureCount": payload.get("textureCount"),
        "enrichedRegistry": str(Path(arguments.enriched_registry).resolve()) if arguments.enriched_registry else None,
    }
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if not payload.get("errors") else 5


if __name__ == "__main__":
    raise SystemExit(main())
