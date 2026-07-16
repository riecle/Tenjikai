"""Tail payload preserves model-authored grade/z/confidence fields."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from build_site_data import enrich_with_v12


class TestTailPayloadAuthority(unittest.TestCase):
    def test_tail_fields_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            atlas = Path(td)
            db = sqlite3.connect(str(atlas / "slot_atlas.db"))
            db.executescript("""
                CREATE TABLE halls (hall_id TEXT PRIMARY KEY, chain_id TEXT);
                CREATE TABLE hall_days (hall_id TEXT);
                CREATE TABLE machine_days (hall_id TEXT);
                CREATE TABLE tail_days (hall_id TEXT);
                CREATE TABLE unit_days (hall_id TEXT);
                CREATE TABLE chain_pattern_results_v2 (
                    chain_id TEXT, pattern_type TEXT, statistic REAL, lift REAL,
                    confidence REAL, explanation_json TEXT, promoted INTEGER,
                    status TEXT, subject_key TEXT, warnings_json TEXT
                );
                INSERT INTO halls VALUES ('h1', NULL);
                INSERT INTO hall_days VALUES ('h1');
                INSERT INTO tail_days VALUES ('h1');
            """)
            db.commit(); db.close()
            run = atlas / "run.json"
            run.write_text(json.dumps({
                "prediction_run_id": "r1", "feature_cutoff_at": "2026-01-01",
                "model_version": "m", "config_version": "v1.2", "built_at": "2026-01-01",
                "predictions": [{
                    "target_date": "2026-01-07", "hall_id": "h1",
                    "entity_type": "tail", "entity_id": "7", "score": 88.0,
                    "rank": 1, "confidence": 0.71, "z_shrunk": 2.2,
                    "grade": "watch", "n_eff": 12,
                    "explanation": ["date-pun降格"], "warnings": ["日付こじつけ仮説"]
                }]
            }, ensure_ascii=False), encoding="utf-8")
            payload = {"halls": {"h1": {}}}
            enrich_with_v12(payload, atlas, run)
            tail = payload["halls"]["h1"]["v1_2"]["2026-01-07"]["tails"][0]
            self.assertEqual(tail["grade"], "watch")
            self.assertEqual(tail["z_shrunk"], 2.2)
            self.assertEqual(tail["n_eff"], 12)
            self.assertEqual(tail["confidence"], 0.71)
            self.assertEqual(tail["source"], "v1_2_prediction")


if __name__ == "__main__":
    unittest.main()
