from __future__ import annotations

import argparse
import json
import sys

import bpy
from mathutils import Vector


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description="Report local-space bounds for named Blender vertex groups.")
    parser.add_argument("--groups", nargs="+", required=True)
    args = parser.parse_args(argv)
    mesh = next(obj for obj in bpy.context.scene.objects if obj.type == "MESH")
    result = {}
    for name in args.groups:
        group = mesh.vertex_groups.get(name)
        if group is None:
            result[name] = {"missing": True}
            continue
        weighted = []
        for vertex in mesh.data.vertices:
            membership = next((item for item in vertex.groups if item.group == group.index), None)
            if membership and membership.weight > 0.05:
                weighted.append((vertex.co.copy(), membership.weight))
        if not weighted:
            result[name] = {"vertices": 0}
            continue
        low = Vector(tuple(min(co[index] for co, _ in weighted) for index in range(3)))
        high = Vector(tuple(max(co[index] for co, _ in weighted) for index in range(3)))
        total = sum(weight for _, weight in weighted)
        center = sum((co * weight for co, weight in weighted), Vector((0.0, 0.0, 0.0))) / total
        result[name] = {
            "vertices": len(weighted),
            "lowCm": [round(value, 4) for value in low],
            "highCm": [round(value, 4) for value in high],
            "centerCm": [round(value, 4) for value in center],
            "dimensionsCm": [round(value, 4) for value in high - low],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
