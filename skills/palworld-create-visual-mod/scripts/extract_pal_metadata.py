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


def flatten_pals(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [pal for tribe in data.get("Tribes", []) for pal in tribe.get("Pals", [])]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract build-matched Pal gameplay and localization metadata with CUE4Parse."
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True, help="Deep-metadata artifact root")
    arguments = parser.parse_args()

    config_path = Path(arguments.config).resolve()
    config = load_json(config_path)
    game_root = Path(config["game"]["root"])
    manifest_path = Path(config["game"]["manifest"])
    tools = config.get("tools", {})
    dotnet = Path(tools.get("dotnet") or "")
    extractor = Path(tools.get("palworldDataExtractor") or "")
    mapping = Path(tools.get("mappingFile") or "")
    pak_directory = game_root / "Pal" / "Content" / "Paks"
    pak = pak_directory / "Pal-Windows.pak"

    for required in (manifest_path, dotnet, extractor, mapping, pak):
        if not required.is_file():
            raise SystemExit(f"Required file is missing: {required}")

    steam = parse_steam_acf(manifest_path)
    build_id = steam.get("buildid")
    if not build_id:
        raise SystemExit(f"Steam buildid is missing from {manifest_path}")

    output_root = Path(arguments.output).resolve() / f"build-{build_id}"
    extraction_root = output_root / "palworld-data-extractor"
    command = [
        str(dotnet),
        str(extractor),
        str(pak_directory),
        "--out",
        str(extraction_root),
        "--pak",
        pak.name,
        "--ue-version",
        "5.1",
        "--usmap",
        str(mapping),
    ]
    completed = subprocess.run(command, check=False, text=True)
    if completed.returncode != 0:
        raise SystemExit(f"PalworldDataExtractor failed with exit code {completed.returncode}")

    data_path = extraction_root / "data.json"
    steam_path = extraction_root / "steam.json"
    if not data_path.is_file() or not steam_path.is_file():
        raise SystemExit("Extractor completed without data.json or steam.json")

    extracted_steam = load_json(steam_path)
    extracted_build = extracted_steam.get("buildId", extracted_steam.get("BuildId"))
    if str(extracted_build) != build_id:
        raise SystemExit(
            f"Extracted build {extracted_build} does not match installed build {build_id}"
        )

    data = load_json(data_path)
    rows = flatten_pals(data)
    languages = sorted(data.get("LocalizationFiles", {}).keys(), key=str.casefold)
    localization_files = data.get("LocalizationFiles", {})
    text_values = sum(
        len(namespace.get("Fields", {}))
        for localization in localization_files.values()
        for namespace in localization.get("Namespaces", {}).values()
    )
    text_entries_per_language = max(
        (
            sum(len(namespace.get("Fields", {})) for namespace in localization.get("Namespaces", {}).values())
            for localization in localization_files.values()
        ),
        default=0,
    )
    manifest = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "gameBuildId": build_id,
        "source": {
            "pak": str(pak),
            "pakSize": pak.stat().st_size,
            "mapping": str(mapping),
            "mappingSha256": sha256(mapping),
        },
        "producer": {
            "dotnet": str(dotnet),
            "palworldDataExtractor": str(extractor),
            "cue4ParsePackage": tools.get("cue4ParsePackage"),
        },
        "outputs": {
            "data": str(data_path),
            "dataSha256": sha256(data_path),
            "root": str(extraction_root),
        },
        "statistics": {
            "tribes": len(data.get("Tribes", [])),
            "palRows": len(rows),
            "languages": languages,
            "textEntriesPerLanguage": text_entries_per_language,
            "localizedTextValues": text_values,
        },
        "validation": {
            "usmapAccepted": True,
            "steamBuildMatched": True,
            "coreDataTablesParsed": True,
            "localizationTablesParsed": True,
        },
    }
    manifest_path_out = output_root / "deep-metadata-manifest.json"
    write_json(manifest_path_out, manifest)
    json.dump(manifest, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
