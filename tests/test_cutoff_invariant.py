"""Freeze invariant: feature_cutoff_at must not exceed built_at (v1.2 review fix)."""
from __future__ import annotations
import json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from validate_release import validate_frozen_run

def _run(cutoff: str) -> dict:
    return {
        "prediction_run_id": "t", "built_at": "2026-07-16T12:00:00+00:00",
        "feature_cutoff_at": cutoff, "model_version": "m", "config_version": "c",
        "source_snapshot_hash": "s", "feature_snapshot_hash": "f",
        "resolved_cutoff_source": "test", "target_dates": ["2026-07-20"],
        "predictions": [{"entity_type": "hall", "warnings": []}],
    }

class CutoffInvariantTest(unittest.TestCase):
    def _validate(self, cutoff: str) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "run.json"
            p.write_text(json.dumps(_run(cutoff)), encoding="utf-8")
            return validate_frozen_run(p, None)

    def test_future_cutoff_rejected(self):
        errors = self._validate("2026-07-19T23:59:59+09:00")
        self.assertTrue(any("freeze invariant" in e for e in errors), errors)

    def test_past_cutoff_accepted(self):
        errors = self._validate("2026-07-14T23:59:59+09:00")
        self.assertFalse(any("freeze invariant" in e for e in errors), errors)

if __name__ == "__main__":
    unittest.main()
