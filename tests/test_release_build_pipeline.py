"""REL-01~REL-05: release build pipeline tests."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

ROOT = Path(__file__).resolve().parent.parent


class TestREL01_AutoDetectFrozenRun(unittest.TestCase):
    def test_auto_detect(self):
        from build_site_data import _auto_detect_frozen_run
        frozen_dir = ROOT / "predictions" / "frozen"
        if frozen_dir.is_dir():
            result = _auto_detect_frozen_run()
            if list(frozen_dir.glob("*.json")):
                self.assertIsNotNone(result)
                data = json.loads(result.read_text(encoding="utf-8"))
                self.assertIn("predictions", data)
            else:
                self.assertIsNone(result)
        else:
            result = _auto_detect_frozen_run()
            self.assertIsNone(result)


class TestREL02_AutoDetectReturnsNoneForEmpty(unittest.TestCase):
    def test_no_frozen_dir(self):
        import tempfile
        import os
        from build_site_data import _auto_detect_frozen_run
        old_root = __import__("build_site_data").ROOT
        with tempfile.TemporaryDirectory() as td:
            __import__("build_site_data").ROOT = Path(td)
            result = _auto_detect_frozen_run()
            self.assertIsNone(result)
            __import__("build_site_data").ROOT = old_root


class TestREL03_BuildFreePublicReleaseExists(unittest.TestCase):
    def test_script_exists(self):
        script = ROOT / "tools" / "build_free_public_release.py"
        self.assertTrue(script.exists())


class TestREL04_BuildFreePublicReleaseImportable(unittest.TestCase):
    def test_importable(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "build_free_public_release",
            str(ROOT / "tools" / "build_free_public_release.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(hasattr(mod, "main"))
        self.assertTrue(hasattr(mod, "run_step"))


class TestREL05_FrozenRunMetaComplete(unittest.TestCase):
    def test_frozen_run_has_required_fields(self):
        frozen_dir = ROOT / "predictions" / "frozen"
        if not frozen_dir.is_dir():
            self.skipTest("no frozen dir")
        candidates = sorted(frozen_dir.glob("*.json"), reverse=True)
        jsons = [c for c in candidates if not c.name.endswith(".sha256")]
        if not jsons:
            self.skipTest("no frozen runs")
        data = json.loads(jsons[0].read_text(encoding="utf-8"))
        for field in ["prediction_run_id", "built_at", "feature_cutoff_at",
                      "model_version", "config_version", "predictions"]:
            self.assertIn(field, data, f"missing {field} in frozen run")


class TestREL06_FreezeCommandUsesPositionalDraft(unittest.TestCase):
    def test_freeze_command(self):
        from build_free_public_release import build_freeze_command
        cmd = build_freeze_command("python3", Path("build/run_draft.json"), Path("atlas.db"))
        self.assertIn("build/run_draft.json", cmd)
        self.assertNotIn("--draft", cmd)
        self.assertIn("--db", cmd)
        self.assertEqual(cmd[cmd.index("--db") + 1], "atlas.db")


if __name__ == "__main__":
    unittest.main()
