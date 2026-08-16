from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("prepare_target_manifest", SCRIPTS / "prepare_target_manifest.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReplacementModeTests(unittest.TestCase):
    def test_color_request_preserving_shape_stays_texture_only(self) -> None:
        mode, rationale = MODULE.replacement_mode("把瞅什魔改成蓝紫色星空主题，保留原来的造型", "auto")
        self.assertEqual("texture_only", mode)
        self.assertIn("preserves source geometry", rationale)

    def test_explicit_silhouette_change_uses_same_skeleton_mesh(self) -> None:
        mode, _ = MODULE.replacement_mode("把轮廓改成更瘦长的造型", "auto")
        self.assertEqual("same_skeleton_mesh", mode)

    def test_explicit_mode_wins(self) -> None:
        mode, _ = MODULE.replacement_mode("只调整颜色", "constrained_mesh")
        self.assertEqual("constrained_mesh", mode)


if __name__ == "__main__":
    unittest.main()
