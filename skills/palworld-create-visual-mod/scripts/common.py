from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, value: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(destination)


def parse_steam_acf(path: str | Path) -> dict[str, str]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    result: dict[str, str] = {}
    for key, value in re.findall(r'^\s*"([^"]+)"\s+"([^"]*)"\s*$', text, re.MULTILINE):
        result.setdefault(key, value)
    return result


def normalize_asset_path(value: str) -> str:
    return value.strip().replace("\\", "/").lstrip("/")
