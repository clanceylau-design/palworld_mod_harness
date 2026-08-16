from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Install and enable the PSK/PSA add-on in an isolated Blender user root.")
    parser.add_argument("--blender", required=True)
    parser.add_argument("--addon-source", required=True)
    parser.add_argument("--user-root", required=True)
    arguments = parser.parse_args()

    blender = Path(arguments.blender).resolve()
    source = Path(arguments.addon_source).resolve()
    user_root = Path(arguments.user_root).resolve()
    if not blender.is_file():
        raise SystemExit(f"Blender executable not found: {blender}")
    if not (source / "__init__.py").is_file():
        raise SystemExit(f"PSK/PSA add-on source is invalid: {source}")

    addon_target = user_root / "scripts" / "addons" / "io_scene_psk_psa"
    addon_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, addon_target, dirs_exist_ok=True)
    config_root = user_root / "config"
    config_root.mkdir(parents=True, exist_ok=True)
    bootstrap = """import bpy
bpy.ops.preferences.addon_enable(module='io_scene_psk_psa')
bpy.ops.wm.save_userpref()
print('PSK_PSA_ADDON_ENABLED')
"""
    with tempfile.TemporaryDirectory(prefix="palworld-blender-") as temporary:
        script = Path(temporary) / "enable_addon.py"
        script.write_text(bootstrap, encoding="utf-8")
        environment = os.environ.copy()
        environment["BLENDER_USER_CONFIG"] = str(config_root)
        environment["BLENDER_USER_SCRIPTS"] = str(user_root / "scripts")
        completed = subprocess.run([str(blender), "--background", "--python", str(script)], env=environment, check=False, capture_output=True, text=True, timeout=120)
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    print(output.strip())
    if completed.returncode != 0 or "PSK_PSA_ADDON_ENABLED" not in output:
        return completed.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
