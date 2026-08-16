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


def main() -> int:
    parser = argparse.ArgumentParser(description="Export editable target textures as a versioned SourceBundle.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--query", required=True, help="Exact localized name, alias, or internal Pal ID")
    parser.add_argument("--output", required=True, help="SourceBundle artifact root")
    parser.add_argument("--include-unknown", action="store_true", help="Also export unclassified texture bindings")
    arguments = parser.parse_args()

    config = load_json(Path(arguments.config).resolve())
    registry_path = Path(arguments.registry).resolve()
    registry = load_json(registry_path)
    target = exact_target(registry, arguments.query)
    manifest_path = Path(config["game"]["manifest"])
    build_id = parse_steam_acf(manifest_path).get("buildid")
    if not build_id or str(registry.get("gameBuildId")) != build_id:
        raise SystemExit(
            f"Registry build {registry.get('gameBuildId')} does not match installed build {build_id or 'unknown'}"
        )

    bindings = [
        binding for binding in (target.get("materialMetadata") or {}).get("textureBindings", [])
        if binding.get("texturePath") and (binding.get("editableCandidate") or arguments.include_unknown)
    ]
    by_texture: dict[str, list[dict[str, Any]]] = {}
    for binding in bindings:
        by_texture.setdefault(str(binding["texturePath"]), []).append(binding)
    if not by_texture:
        raise SystemExit(f"No editable texture bindings are available for {target.get('palId')}")

    tools = config.get("tools", {})
    dotnet = Path(tools.get("dotnet") or "")
    exporter = Path(tools.get("palworldTextureExport") or "")
    mapping = Path(tools.get("mappingFile") or "")
    game_root = Path(config["game"]["root"])
    pak_directory = game_root / "Pal" / "Content" / "Paks"
    for required in (registry_path, manifest_path, dotnet, exporter, mapping, pak_directory / "Pal-Windows.pak"):
        if not required.is_file():
            raise SystemExit(f"Required file is missing: {required}")

    bundle_root = Path(arguments.output).resolve() / f"build-{build_id}" / str(target["palId"])
    texture_root = bundle_root / "textures"
    texture_root.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [str(dotnet), str(exporter), str(pak_directory), str(mapping), str(texture_root), *by_texture.keys()],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode not in (0, 5):
        if completed.stderr:
            sys.stderr.write(completed.stderr)
        raise SystemExit(f"PalworldTextureExport failed with exit code {completed.returncode}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit(f"PalworldTextureExport returned invalid JSON: {error}") from error

    files: list[dict[str, Any]] = []
    for texture in result.get("textures", []):
        output = Path(texture["output"]).resolve()
        source_path = str(texture["requestedPath"])
        files.append({
            **texture,
            "output": str(output),
            "sha256": sha256(output),
            "bindings": by_texture.get(source_path, []),
        })
    bundle = {
        "schemaVersion": 1,
        "artifactType": "SourceBundle",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "gameBuildId": build_id,
        "target": {
            "palId": target.get("palId"),
            "localizedNames": target.get("localizedNames", {}),
            "primarySkeletalMesh": target.get("primarySkeletalMesh"),
            "skeleton": target.get("skeleton"),
            "physicsAsset": target.get("physicsAsset"),
        },
        "source": {
            "registry": str(registry_path),
            "registrySha256": sha256(registry_path),
            "mapping": str(mapping),
            "mappingSha256": sha256(mapping),
        },
        "producer": {"dotnet": str(dotnet), "textureExporter": str(exporter)},
        "requestedTextureCount": len(by_texture),
        "exportedTextureCount": len(files),
        "errors": result.get("errors", []),
        "files": files,
    }
    bundle_manifest = bundle_root / "source-bundle.json"
    write_json(bundle_manifest, bundle)
    summary = {
        "manifest": str(bundle_manifest),
        "gameBuildId": build_id,
        "palId": target.get("palId"),
        "requested": len(by_texture),
        "exported": len(files),
        "errors": result.get("errors", []),
    }
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if not result.get("errors") else 5


if __name__ == "__main__":
    raise SystemExit(main())
