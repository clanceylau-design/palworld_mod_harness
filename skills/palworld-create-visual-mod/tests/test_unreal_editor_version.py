from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DOCTOR = load_module("doctor", "doctor.py")
PREPARE = load_module("prepare_unreal_model_import", "prepare_unreal_model_import.py")


class UnrealEditorVersionTests(unittest.TestCase):
    def make_editor(self, root: Path, value: object) -> Path:
        editor = root / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"
        editor.parent.mkdir(parents=True)
        editor.write_bytes(b"")
        version = root / "Engine" / "Build" / "Build.version"
        version.parent.mkdir(parents=True)
        if isinstance(value, str):
            version.write_text(value, encoding="utf-8")
        else:
            version.write_text(json.dumps(value), encoding="utf-8")
        return editor

    def test_exact_version_is_read_consistently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            editor = self.make_editor(
                Path(directory),
                {"MajorVersion": 5, "MinorVersion": 1, "PatchVersion": 1},
            )
            self.assertEqual({"major": 5, "minor": 1, "patch": 1}, DOCTOR.unreal_version(editor))
            self.assertEqual("5.1.1", PREPARE.read_editor_version(editor))

    def test_malformed_version_is_not_permitted_as_unknown_experimental_editor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            editor = self.make_editor(Path(directory), "{not-json")
            self.assertIsNone(DOCTOR.unreal_version(editor))
            self.assertIsNone(PREPARE.read_editor_version(editor))

    def test_missing_patch_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            editor = self.make_editor(Path(directory), {"MajorVersion": 5, "MinorVersion": 8})
            self.assertIsNone(DOCTOR.unreal_version(editor))
            self.assertIsNone(PREPARE.read_editor_version(editor))


if __name__ == "__main__":
    unittest.main()
