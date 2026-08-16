from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any

from common import load_json


def searchable_values(pal: dict[str, Any]) -> list[str]:
    return [str(value).casefold() for value in [pal["palId"], *pal.get("aliases", [])]]


def score(query: str, pal: dict[str, Any]) -> float:
    values = searchable_values(pal)
    if query in values:
        return 1.0
    if any(query in value for value in values):
        return 0.9
    return max((difflib.SequenceMatcher(None, query, value).ratio() for value in values), default=0.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Query a generated Pal asset registry.")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--min-score", type=float, default=0.45)
    arguments = parser.parse_args()

    registry = load_json(Path(arguments.registry))
    query = arguments.query.strip().casefold()
    ranked = sorted(((score(query, pal), pal) for pal in registry.get("pals", [])), key=lambda item: (-item[0], item[1]["palId"].casefold()))
    matches = [{"score": round(value, 4), **pal} for value, pal in ranked[: max(arguments.limit, 1)] if value >= arguments.min_score]
    exact = [item for item in matches if item["score"] == 1.0]
    result = {"gameBuildId": registry.get("gameBuildId"), "query": arguments.query, "unambiguous": len(exact) == 1, "matches": matches}
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
