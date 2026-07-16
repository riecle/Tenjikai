"""UI-T01~UI-T04: v1.2 tail UI rendering tests (app.js function validation)."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestUIT01_RenderV12TailsFunctionExists(unittest.TestCase):
    def test_function_defined(self):
        app_js = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("function renderV12Tails", app_js)


class TestUIT02_RenderV12TailsCalledInFreeSource(unittest.TestCase):
    def test_called_in_render_free_source(self):
        app_js = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("renderV12Tails(v12day)", app_js)


class TestUIT03_V12TailsHasGradeAndScore(unittest.TestCase):
    def test_grade_and_score_in_function(self):
        app_js = (ROOT / "app.js").read_text(encoding="utf-8")
        start = app_js.index("function renderV12Tails")
        end = app_js.index("function renderFreeSource")
        snippet = app_js[start:end]
        self.assertIn("tail-grade", snippet)
        self.assertIn("score=", snippet)
        self.assertIn("tail-number", snippet)
        self.assertIn("t.grade", snippet)
        self.assertIn("t.z_shrunk", snippet)
        self.assertNotIn("s >= 80", snippet)


class TestUIT04_V12TailsFallsBackToLegacy(unittest.TestCase):
    def test_legacy_fallback(self):
        app_js = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("renderTails(tails)", app_js)
        fallback_count = app_js.count("renderV12Tails(v12day)")
        self.assertGreaterEqual(fallback_count, 2)


if __name__ == "__main__":
    unittest.main()
