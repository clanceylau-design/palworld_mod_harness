from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import load_json, parse_steam_acf, write_json


MESH_SIGNALS = (
    "model", "mesh", "silhouette", "shape", "proportion", "geometry",
    "模型", "建模", "轮廓", "体型", "外形", "造型", "比例", "几何",
)

PRESERVE_GEOMETRY_PHRASES = (
    "保留原来的模型", "保留原模型", "保持原来的模型", "保持原模型", "不修改模型", "不改变模型",
    "保留原来的轮廓", "保留原轮廓", "保持原来的轮廓", "保持原轮廓", "不修改轮廓", "不改变轮廓",
    "保留原来的外形", "保留原外形", "保持原来的外形", "保持原外形", "不修改外形", "不改变外形",
    "保留原来的造型", "保留原造型", "保持原来的造型", "保持原造型", "不修改造型", "不改变造型",
    "preserve the original model", "keep the original model", "preserve original model", "keep original model",
    "preserve the original shape", "keep the original shape", "preserve original shape", "keep original shape",
    "preserve the original silhouette", "keep the original silhouette", "preserve original silhouette", "keep original silhouette",
)


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def exact_target(registry: dict[str, Any], query: str) -> dict[str, Any]:
    needle = query.strip().casefold()
    matches: list[dict[str, Any]] = []
    for pal in registry.get("pals", []):
        values = [pal.get("palId"), *pal.get("aliases", []), *pal.get("localizedNames", {}).values()]
        if needle in {str(value).casefold() for value in values if value}:
            matches.append(pal)
    if len(matches) != 1:
        names = ", ".join(str(match.get("palId")) for match in matches) or "none"
        raise SystemExit(f"Target must resolve to exactly one Pal; exact matches: {names}")
    return matches[0]


def replacement_mode(request: str, requested: str) -> tuple[str, str]:
    if requested != "auto":
        return requested, "explicitly selected by the caller"
    folded = request.casefold()
    preserved = [phrase for phrase in PRESERVE_GEOMETRY_PHRASES if phrase.casefold() in folded]
    signal_text = folded
    for phrase in preserved:
        signal_text = signal_text.replace(phrase.casefold(), "")
    signals = [signal for signal in MESH_SIGNALS if signal.casefold() in signal_text]
    if signals:
        return "same_skeleton_mesh", f"request contains mesh-change signals: {', '.join(signals)}"
    if preserved:
        return "texture_only", f"request explicitly preserves source geometry: {', '.join(preserved)}"
    return "texture_only", "no explicit silhouette or geometry change was detected; using the safest path"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile a natural-language Pal visual request into AssetSpec and TargetManifest artifacts.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--query", required=True, help="Exact localized name, alias, or internal Pal ID")
    parser.add_argument("--request", required=True, help="The user's unmodified natural-language visual request")
    parser.add_argument("--mode", choices=("auto", "texture_only", "constrained_mesh", "same_skeleton_mesh"), default="auto")
    parser.add_argument("--output", required=True, help="Artifact root")
    arguments = parser.parse_args()

    config = load_json(Path(arguments.config).resolve())
    registry_path = Path(arguments.registry).resolve()
    registry = load_json(registry_path)
    target = exact_target(registry, arguments.query)
    build_id = parse_steam_acf(Path(config["game"]["manifest"])).get("buildid")
    if not build_id or str(registry.get("gameBuildId")) != build_id:
        raise SystemExit(f"Registry build {registry.get('gameBuildId')} does not match installed build {build_id or 'unknown'}")

    mode, rationale = replacement_mode(arguments.request, arguments.mode)
    generated_at = datetime.now(timezone.utc).isoformat()
    artifact_root = Path(arguments.output).resolve() / f"build-{build_id}" / str(target["palId"])
    artifact_root.mkdir(parents=True, exist_ok=True)

    spec_payload = {
        "schemaVersion": 1,
        "artifactType": "AssetSpec",
        "generatedAt": generated_at,
        "gameBuildId": build_id,
        "request": {
            "raw": arguments.request,
            "targetQuery": arguments.query,
            "replacementMode": mode,
            "modeRationale": rationale,
        },
        "constraints": {
            "preserveOriginalSkeleton": True,
            "preserveBoneNamesAndHierarchy": True,
            "preserveMaterialSlotBindings": True,
            "modifyInstalledGameFiles": False,
        },
        "fallbackOrder": [
            "same_skeleton_mesh",
            "constrained_mesh",
            "validated_attachment",
            "texture_only",
        ],
    }
    asset_spec = {**spec_payload, "contentSha256": canonical_hash(spec_payload)}
    asset_spec_path = artifact_root / "asset-spec.json"
    write_json(asset_spec_path, asset_spec)

    material_metadata = target.get("materialMetadata") or {}
    bindings = material_metadata.get("textureBindings", [])
    editable_bindings = [binding for binding in bindings if binding.get("editableCandidate")]
    deep = target.get("deepMetadata") or {}
    manifest_payload = {
        "schemaVersion": 1,
        "artifactType": "TargetManifest",
        "generatedAt": generated_at,
        "gameBuildId": build_id,
        "parentArtifacts": {"assetSpec": {"path": str(asset_spec_path), "sha256": file_hash(asset_spec_path)}},
        "source": {"registry": str(registry_path), "registrySha256": file_hash(registry_path)},
        "resolution": {"method": "exact", "confidence": 1.0, "query": arguments.query},
        "target": {
            "palId": target.get("palId"),
            "aliases": target.get("aliases", []),
            "localizedNames": target.get("localizedNames", {}),
            "assetRoot": target.get("assetRoot"),
        },
        "replacement": {"mode": mode, "rationale": rationale},
        "assets": {
            "primarySkeletalMesh": target.get("primarySkeletalMesh"),
            "skeleton": target.get("skeleton"),
            "physicsAsset": target.get("physicsAsset"),
            "materialSlots": deep.get("materialSlots", []),
            "textureBindings": editable_bindings,
        },
        "sourceGeometry": {
            "boneCount": deep.get("boneCount"),
            "lodCount": deep.get("lodCount"),
            "lods": deep.get("lods", []),
            "bounds": deep.get("bounds"),
            "hasVertexColors": deep.get("hasVertexColors"),
        },
        "readiness": {
            "sourceBundle": bool(editable_bindings),
            "textureGeneration": False,
            "modelGeneration": False,
            "cookAndPackage": False,
        },
        "warnings": target.get("warnings", []),
    }
    target_manifest = {**manifest_payload, "contentSha256": canonical_hash(manifest_payload)}
    target_manifest_path = artifact_root / "target-manifest.json"
    write_json(target_manifest_path, target_manifest)

    json.dump({
        "assetSpec": str(asset_spec_path),
        "targetManifest": str(target_manifest_path),
        "palId": target.get("palId"),
        "replacementMode": mode,
        "editableTextureBindings": len(editable_bindings),
    }, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
