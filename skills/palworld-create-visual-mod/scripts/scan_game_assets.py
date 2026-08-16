from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from common import load_json, normalize_asset_path, parse_steam_acf, write_json


ASSET_EXTENSIONS = {".uasset", ".uexp", ".ubulk", ".uptnl"}


def list_pak(repak: Path, pak: Path) -> tuple[list[str], str]:
    process = subprocess.Popen(
        [str(repak), "list", str(pak)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace"
    )
    assert process.stdout is not None
    paths: list[str] = []
    digest = hashlib.sha256()
    for line in process.stdout:
        normalized = normalize_asset_path(line)
        if not normalized:
            continue
        paths.append(normalized)
        digest.update(normalized.encode("utf-8"))
        digest.update(b"\n")
    stderr = process.stderr.read() if process.stderr else ""
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"repak list failed with exit code {return_code}: {stderr.strip()}")
    return paths, digest.hexdigest()


def get_pak_entry(repak: Path, pak: Path, entry: str) -> bytes:
    completed = subprocess.run(
        [str(repak), "get", str(pak), entry], check=False, capture_output=True, timeout=60
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"repak get failed for {entry}: {message.strip()}")
    return completed.stdout


def parse_pal_name_table(payload: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    pattern = re.compile(rb"PAL_NAME_([A-Za-z0-9_]+)_TextData\x00")
    for match in pattern.finditer(payload):
        offset = match.end()
        if offset + 4 > len(payload):
            continue
        length = struct.unpack_from("<i", payload, offset)[0]
        offset += 4
        if length > 0:
            end = offset + length
            if end > len(payload):
                continue
            value = payload[offset:end].rstrip(b"\x00").decode("utf-8", errors="strict")
        elif length < 0:
            end = offset + (-length * 2)
            if end > len(payload):
                continue
            value = payload[offset:end].decode("utf-16le", errors="strict").rstrip("\x00")
        else:
            value = ""
        if value:
            result[match.group(1).decode("ascii")] = value
    return result


def extract_localizations(repak: Path, pak: Path, all_paths: list[str]) -> dict[str, dict[str, str]]:
    available = set(all_paths)
    localized: dict[str, dict[str, str]] = defaultdict(dict)
    for locale in ("en", "zh-Hans", "zh-Hant"):
        entry = f"Pal/Content/L10N/{locale}/Pal/DataTable/Text/DT_PalNameText_Common.uexp"
        if entry not in available:
            continue
        for pal_id, display_name in parse_pal_name_table(get_pak_entry(repak, pak, entry)).items():
            localized[pal_id][locale] = display_name
    return dict(localized)


def classify_uasset(path: str) -> str:
    name = PurePosixPath(path).name.lower()
    if name.startswith("sk_") and "skeleton" not in name:
        return "skeletalMeshes"
    if "skeleton" in name:
        return "skeletons"
    if name.startswith("pa_") or "physicsasset" in name:
        return "physicsAssets"
    if name.startswith(("mi_", "ml_", "m_")):
        return "materials"
    if name.startswith("t_"):
        return "textures"
    if name.startswith("abp_"):
        return "animationBlueprints"
    return "otherAssets"


def choose_primary(paths: list[str], exact_name: str) -> str | None:
    exact = [path for path in paths if PurePosixPath(path).name.lower() == exact_name.lower()]
    candidates = exact or paths
    return sorted(candidates, key=lambda value: (len(value), value.lower()))[0] if candidates else None


def load_aliases(path: str | None) -> dict[str, list[str]]:
    if not path:
        return {}
    raw = load_json(path)
    result: dict[str, list[str]] = {}
    for key, value in raw.items():
        values = value if isinstance(value, list) else [value]
        result[key.lower()] = sorted({str(item).strip() for item in values if str(item).strip()})
    return result


def load_gameplay_tribes(path: str | None, build_id: str) -> tuple[dict[str, dict[str, Any]], Path | None]:
    if not path:
        return {}, None
    metadata_path = Path(path).resolve()
    data = load_json(metadata_path)
    extracted_build = str(data.get("SteamManifest", {}).get("BuildId", ""))
    if extracted_build != build_id:
        raise SystemExit(
            f"Gameplay metadata build {extracted_build or 'unknown'} does not match installed build {build_id}"
        )
    tribes = {
        str(tribe.get("Name", "")).casefold(): tribe
        for tribe in data.get("Tribes", [])
        if tribe.get("Name")
    }
    return tribes, metadata_path


def gameplay_reference(code_name: str, tribes: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    tribe = tribes.get(code_name.casefold())
    match = "direct"
    base_pal_id = code_name
    if tribe is None:
        base_pal_id = re.sub(r"_(?:Skin\d+|SkinDark|Pink|Form\d+)$", "", code_name, flags=re.IGNORECASE)
        tribe = tribes.get(base_pal_id.casefold())
        match = "inherited-visual-variant"
    if tribe is None:
        return None
    rows = tribe.get("Pals", [])
    return {
        "match": match,
        "tribeName": tribe.get("Name"),
        "basePalId": base_pal_id,
        "variantCount": len(rows),
        "characterRows": [
            {
                "characterId": row.get("Name"),
                "zukanIndex": row.get("ZukanIndex"),
                "zukanIndexSuffix": row.get("ZukanIndexSuffix"),
                "isBoss": row.get("IsBoss"),
                "isTowerBoss": row.get("IsTowerBoss"),
                "isPredator": row.get("IsPredator"),
            }
            for row in rows
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a versioned Pal asset registry from the installed Palworld Pak index.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True, help="Registry output root")
    parser.add_argument("--aliases", help="Optional JSON map of Pal code name to localized aliases")
    parser.add_argument("--gameplay-metadata", help="Optional build-matched PalworldDataExtractor data.json")
    arguments = parser.parse_args()

    config = load_json(arguments.config)
    game_root = Path(config["game"]["root"])
    manifest_path = Path(config["game"]["manifest"])
    repak = Path(config["tools"]["repak"])
    pak = game_root / "Pal" / "Content" / "Paks" / "Pal-Windows.pak"
    for required in (manifest_path, repak, pak):
        if not required.is_file():
            raise SystemExit(f"Required file is missing: {required}")

    manifest = parse_steam_acf(manifest_path)
    build_id = manifest.get("buildid")
    if not build_id:
        raise SystemExit(f"Steam buildid is missing from {manifest_path}")
    gameplay_tribes, gameplay_metadata_path = load_gameplay_tribes(arguments.gameplay_metadata, build_id)
    all_paths, index_hash = list_pak(repak, pak)
    localized_names = extract_localizations(repak, pak, all_paths)
    asset_root = normalize_asset_path(config.get("registry", {}).get("palAssetRoot", "Pal/Content/Pal/Model/Character/Monster")).rstrip("/")
    prefix = asset_root + "/"
    grouped: dict[str, list[str]] = defaultdict(list)
    global_uassets: dict[str, list[str]] = defaultdict(list)
    for path in all_paths:
        pure = PurePosixPath(path)
        if pure.suffix.lower() == ".uasset":
            global_uassets[pure.name.lower()].append(path)
        if not path.lower().startswith(prefix.lower()):
            continue
        relative = path[len(prefix):]
        parts = relative.split("/")
        if len(parts) < 2 or pure.suffix.lower() not in ASSET_EXTENSIONS:
            continue
        grouped[parts[0]].append(path)

    aliases = load_aliases(arguments.aliases)
    pals: list[dict[str, Any]] = []
    for code_name in sorted(grouped, key=str.lower):
        members = sorted(grouped[code_name], key=str.lower)
        categories: dict[str, list[str]] = defaultdict(list)
        for path in members:
            if PurePosixPath(path).suffix.lower() == ".uasset":
                categories[classify_uasset(path)].append(path)

        skeletons = sorted(set(categories["skeletons"] + global_uassets.get(f"sk_{code_name}_skeleton.uasset".lower(), [])), key=str.lower)
        physics_assets = sorted(set(categories["physicsAssets"] + global_uassets.get(f"pa_{code_name}_physicsasset.uasset".lower(), [])), key=str.lower)
        meshes = sorted(categories["skeletalMeshes"], key=str.lower)
        textures = sorted(categories["textures"], key=str.lower)
        materials = sorted(categories["materials"], key=str.lower)
        primary_mesh = choose_primary(meshes, f"SK_{code_name}.uasset")
        primary_skeleton = choose_primary(skeletons, f"SK_{code_name}_Skeleton.uasset")
        primary_physics = choose_primary(physics_assets, f"PA_{code_name}_PhysicsAsset.uasset")

        warnings: list[str] = []
        if not primary_mesh:
            warnings.append("No primary skeletal mesh was inferred.")
        if not primary_skeleton:
            warnings.append("No exact skeleton package was inferred from package names; deep metadata extraction is required.")
        if not primary_physics:
            warnings.append("No exact Physics Asset was inferred.")
        if not textures:
            warnings.append("No texture packages were found under the Pal model folder.")

        names = localized_names.get(code_name, {})
        inferred_aliases = set(aliases.get(code_name.lower(), []))
        inferred_aliases.update(names.values())
        gameplay = gameplay_reference(code_name, gameplay_tribes)
        pals.append({
            "palId": code_name,
            "aliases": sorted(inferred_aliases, key=str.casefold),
            "localizedNames": names,
            "assetRoot": f"{asset_root}/{code_name}",
            "primarySkeletalMesh": primary_mesh,
            "skeleton": primary_skeleton,
            "physicsAsset": primary_physics,
            "skeletalMeshes": meshes,
            "materials": materials,
            "textures": textures,
            "animationBlueprints": sorted(categories["animationBlueprints"], key=str.lower),
            "otherAssets": sorted(categories["otherAssets"], key=str.lower),
            "packageMemberCount": len(members),
            "gameplayMetadata": gameplay,
            "capabilities": {
                "textureReplacement": "candidate" if textures and primary_mesh else "needs_review",
                "meshConstrainedReplacement": "candidate" if primary_mesh and primary_physics else "needs_review",
                "gameplayMetadata": "available" if gameplay else "unavailable",
                "deepMetadata": "unavailable"
            },
            "warnings": warnings
        })

    registry = {
        "schemaVersion": 1,
        "game": "Palworld",
        "platform": "Windows-Steam",
        "gameBuildId": build_id,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": {
            "manifest": str(manifest_path), "pak": str(pak), "pakSize": pak.stat().st_size,
            "pakModifiedAt": datetime.fromtimestamp(pak.stat().st_mtime, timezone.utc).isoformat(),
            "pakEntryCount": len(all_paths), "pakIndexSha256": index_hash, "repak": str(repak),
            "gameplayMetadata": str(gameplay_metadata_path) if gameplay_metadata_path else None,
            "gameplayMetadataSha256": hashlib.sha256(gameplay_metadata_path.read_bytes()).hexdigest() if gameplay_metadata_path else None
        },
        "scan": {
            "palAssetRoot": asset_root, "palFolderCount": len(pals),
            "localizedPalCount": sum(bool(pal["localizedNames"]) for pal in pals),
            "gameplayMetadataDirectCount": sum(bool(pal["gameplayMetadata"] and pal["gameplayMetadata"]["match"] == "direct") for pal in pals),
            "gameplayMetadataInheritedCount": sum(bool(pal["gameplayMetadata"] and pal["gameplayMetadata"]["match"] == "inherited-visual-variant") for pal in pals),
            "locales": ["en", "zh-Hans", "zh-Hant"],
            "method": "Pak path and filename inference plus direct FString parsing of build-matched Pal name tables, optionally joined to build-matched CUE4Parse gameplay DataTables; mesh payload internals are not deserialized"
        },
        "pals": pals
    }
    destination = Path(arguments.output).resolve() / f"build-{build_id}" / "pal-assets.json"
    write_json(destination, registry)
    audit = {
        "schemaVersion": 1,
        "gameBuildId": build_id,
        "palFolderCount": len(pals),
        "localizedPalCount": sum(bool(pal["localizedNames"]) for pal in pals),
        "textureCandidates": sum(pal["capabilities"]["textureReplacement"] == "candidate" for pal in pals),
        "meshCandidates": sum(pal["capabilities"]["meshConstrainedReplacement"] == "candidate" for pal in pals),
        "gameplayMetadataDirect": [pal["palId"] for pal in pals if pal["gameplayMetadata"] and pal["gameplayMetadata"]["match"] == "direct"],
        "gameplayMetadataInherited": [pal["palId"] for pal in pals if pal["gameplayMetadata"] and pal["gameplayMetadata"]["match"] == "inherited-visual-variant"],
        "missingGameplayMetadata": [pal["palId"] for pal in pals if not pal["gameplayMetadata"]],
        "missingPrimaryMesh": [pal["palId"] for pal in pals if not pal["primarySkeletalMesh"]],
        "missingSkeleton": [pal["palId"] for pal in pals if not pal["skeleton"]],
        "missingPhysicsAsset": [pal["palId"] for pal in pals if not pal["physicsAsset"]],
        "missingTextures": [pal["palId"] for pal in pals if not pal["textures"]],
        "interpretation": "Missing links are unresolved path-level inferences, not proof that the runtime asset has no dependency. Resolve them with build-matched deep metadata extraction."
    }
    audit_destination = destination.with_name("registry-audit.json")
    write_json(audit_destination, audit)
    summary = {
        "registry": str(destination), "audit": str(audit_destination), "gameBuildId": build_id, "pakEntryCount": len(all_paths),
        "palFolderCount": len(pals),
        "localizedPalCount": sum(bool(pal["localizedNames"]) for pal in pals),
        "textureCandidates": sum(pal["capabilities"]["textureReplacement"] == "candidate" for pal in pals),
        "meshCandidates": sum(pal["capabilities"]["meshConstrainedReplacement"] == "candidate" for pal in pals),
        "gameplayMetadataCount": sum(bool(pal["gameplayMetadata"]) for pal in pals),
        "pakIndexSha256": index_hash
    }
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
