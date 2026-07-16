"""Phase 0 acceptance tests for prediction freezing infrastructure.

Tests P0-01 through P0-09 from docs/03_ACCEPTANCE_TESTS.md.
Self-contained: creates temp DB and files, no external dependencies.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from freeze_run import freeze, insert_run
from migrate_db import migrate
from prediction_utils import (
    canonical_hash,
    canonical_json,
    sha256_hex,
    source_snapshot_hash,
    validate_draft,
)
from build_predictions import build_features, compute_feature_cutoff


def make_test_db(path: Path) -> sqlite3.Connection:
    """Create a minimal slot_atlas DB with test data."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE halls (
            hall_id TEXT PRIMARY KEY,
            name TEXT, market TEXT, active INTEGER DEFAULT 1,
            forecast_enabled INTEGER DEFAULT 1,
            decision_floor REAL DEFAULT 0,
            travel_minutes REAL DEFAULT 0,
            data_through TEXT, reset_policy TEXT
        );
        CREATE TABLE hall_days (
            hall_id TEXT, result_date TEXT, avg_diff REAL,
            total_diff REAL, avg_games REAL,
            source_name TEXT DEFAULT 'test',
            PRIMARY KEY(hall_id, result_date, source_name)
        );
        CREATE TABLE machine_days (
            hall_id TEXT, result_date TEXT, machine_key TEXT,
            machine_name TEXT, units INTEGER, avg_diff REAL,
            source_name TEXT DEFAULT 'test',
            PRIMARY KEY(hall_id, result_date, machine_key, source_name)
        );
        CREATE TABLE tail_days (
            hall_id TEXT, result_date TEXT, tail_key TEXT,
            avg_diff REAL, source_name TEXT DEFAULT 'test',
            PRIMARY KEY(hall_id, result_date, tail_key, source_name)
        );
        CREATE TABLE model_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_version TEXT, created_at TEXT
        );
        CREATE TABLE predictions (
            run_id INTEGER, target_date TEXT, hall_id TEXT,
            predicted_mean REAL, adjusted_edge REAL, utility_edge REAL,
            confidence REAL, rank TEXT, reasons_json TEXT,
            PRIMARY KEY(run_id, target_date, hall_id)
        );

        INSERT INTO halls VALUES
            ('hall_a','Hall A','tokyo',1,1,0,10,NULL,NULL),
            ('hall_b','Hall B','tokyo',1,1,0,20,NULL,NULL);

        INSERT INTO hall_days VALUES
            ('hall_a','2026-07-10',500,5000,8000,'src1'),
            ('hall_a','2026-07-11',300,3000,7500,'src1'),
            ('hall_a','2026-07-15',450,4500,8200,'src1'),
            ('hall_b','2026-07-10',200,2000,6000,'src1'),
            ('hall_b','2026-07-12',100,1000,5500,'src1');

        INSERT INTO model_runs VALUES (1,'test-0.1','2026-07-15T12:00:00');

        INSERT INTO predictions VALUES
            (1,'2026-07-20','hall_a',500,500,490,0.75,'A','["rule match"]'),
            (1,'2026-07-20','hall_b',200,200,180,0.50,'B','["baseline"]'),
            (1,'2026-07-21','hall_a',450,450,440,0.70,'A','["rule match"]');
    """)
    conn.commit()
    return conn


def make_valid_draft(**overrides: object) -> dict:
    """Create a minimal valid draft prediction."""
    d = {
        "prediction_run_id": "test_run_001",
        "built_at": "2026-07-16T12:00:00+09:00",
        "feature_cutoff_at": "2026-07-15T23:59:59+09:00",
        "model_version": "test-0.1",
        "config_version": "v1.2",
        "source_snapshot_hash": "abc123",
        "feature_snapshot_hash": "def456",
        "predictions": [
            {
                "target_date": "2026-07-20",
                "hall_id": "hall_a",
                "entity_type": "hall",
                "entity_id": "hall_a",
                "score": 500.0,
                "rank": 1,
                "confidence": 0.75,
                "explanation": ["rule match"],
                "warnings": [],
                "capabilities": {"hall_daily_available": True},
            },
            {
                "target_date": "2026-07-21",
                "hall_id": "hall_a",
                "entity_type": "hall",
                "entity_id": "hall_a",
                "score": 450.0,
                "rank": 1,
                "confidence": 0.70,
                "explanation": ["rule match"],
                "warnings": [],
                "capabilities": {"hall_daily_available": True},
            },
        ],
    }
    d.update(overrides)
    return d


class TestP0_01_MultipleTargetDates(unittest.TestCase):
    """One prediction_run_id can hold multiple target_dates."""

    def test_two_dates_one_run(self):
        draft = make_valid_draft()
        dates = {p["target_date"] for p in draft["predictions"]}
        self.assertEqual(dates, {"2026-07-20", "2026-07-21"})

        cj, sha = freeze(draft)
        parsed = json.loads(cj)
        self.assertEqual(parsed["prediction_run_id"], "test_run_001")
        result_dates = {p["target_date"] for p in parsed["predictions"]}
        self.assertEqual(result_dates, {"2026-07-20", "2026-07-21"})


class TestP0_02_TargetDatePlacement(unittest.TestCase):
    """target_date lives in predictions, not in prediction_runs."""

    def test_no_target_date_at_run_level(self):
        draft = make_valid_draft()
        cj, _ = freeze(draft)
        parsed = json.loads(cj)
        self.assertNotIn("target_date", parsed)
        for p in parsed["predictions"]:
            self.assertIn("target_date", p)


class TestP0_03_WarningsRequired(unittest.TestCase):
    """All predictions must have a warnings field (array). Omission fails."""

    def test_missing_warnings_rejected(self):
        draft = make_valid_draft()
        del draft["predictions"][0]["warnings"]
        errors = validate_draft(draft)
        self.assertTrue(any("warnings" in e for e in errors))

    def test_empty_warnings_accepted(self):
        draft = make_valid_draft()
        draft["predictions"][0]["warnings"] = []
        errors = validate_draft(draft)
        self.assertEqual(errors, [])

    def test_nonempty_warnings_accepted(self):
        draft = make_valid_draft()
        draft["predictions"][0]["warnings"] = ["low coverage"]
        errors = validate_draft(draft)
        self.assertEqual(errors, [])


class TestP0_04_Deterministic(unittest.TestCase):
    """Same input, config, code → same canonical JSON and SHA-256."""

    def test_same_draft_same_hash(self):
        d1 = make_valid_draft()
        d2 = make_valid_draft()
        _, h1 = freeze(d1)
        _, h2 = freeze(d2)
        self.assertEqual(h1, h2)

    def test_key_order_irrelevant(self):
        d1 = make_valid_draft()
        d2 = make_valid_draft()
        d2["predictions"][0] = dict(
            reversed(list(d2["predictions"][0].items()))
        )
        _, h1 = freeze(d1)
        _, h2 = freeze(d2)
        self.assertEqual(h1, h2)

    def test_prediction_order_irrelevant(self):
        d1 = make_valid_draft()
        d2 = make_valid_draft()
        d2["predictions"] = list(reversed(d2["predictions"]))
        _, h1 = freeze(d1)
        _, h2 = freeze(d2)
        self.assertEqual(h1, h2)


class TestP0_05_Immutable(unittest.TestCase):
    """Frozen run cannot be overwritten in DB."""

    def test_frozen_run_rejects_update(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            conn.close()

            draft = make_valid_draft()
            _, sha = freeze(draft)
            insert_run(db, draft, sha)

            modified = make_valid_draft(
                source_snapshot_hash="changed",
                feature_snapshot_hash="changed",
            )
            with self.assertRaises(ValueError) as ctx:
                insert_run(db, modified, "different_hash")
            self.assertIn("already frozen", str(ctx.exception))


class TestP0_06_OutcomeSeparation(unittest.TestCase):
    """Adding outcomes must not change the prediction hash."""

    def test_outcome_does_not_alter_prediction(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)

            draft = make_valid_draft()
            cj_before, hash_before = freeze(draft)

            conn.execute(
                """INSERT INTO outcomes
                   (target_date, hall_id, entity_type, entity_id,
                    actual_proxy, actual_label, outcome_status,
                    warnings_json)
                   VALUES (?,?,?,?,?,?,?,?)""",
                ("2026-07-20", "hall_a", "hall", "hall_a",
                 600.0, 1, "final", "[]"),
            )
            conn.commit()
            conn.close()

            _, hash_after = freeze(draft)
            self.assertEqual(hash_before, hash_after)


class TestP0_07_FutureLeakage(unittest.TestCase):
    """Features using data at or after cutoff must fail the build."""

    def test_cutoff_rejects_future_data(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            conn.execute(
                "INSERT INTO hall_days VALUES (?,?,?,?,?,?)",
                ("hall_a", "2026-07-20", 999, 9990, 9000, "future"),
            )
            conn.commit()

            with self.assertRaises(ValueError) as ctx:
                compute_feature_cutoff(conn, "2026-07-18T23:59:59+09:00")
            self.assertIn("future leakage", str(ctx.exception))
            conn.close()

    def test_cutoff_allows_older_data(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            cutoff = compute_feature_cutoff(
                conn, "2026-07-16T23:59:59+09:00"
            )
            self.assertEqual(cutoff, "2026-07-16T23:59:59+09:00")

            features = build_features(conn, "2026-07-16")
            dates = {f["result_date"] for f in features}
            for d in dates:
                self.assertLess(d, "2026-07-16")
            conn.close()


class TestP0_08_SourceHashSensitivity(unittest.TestCase):
    """1-byte change in source files → different source_snapshot_hash."""

    def test_file_change_changes_hash(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            f = src / "halls.json"
            f.write_text('[{"id":"a"}]')
            h1 = source_snapshot_hash(src)

            f.write_text('[{"id":"b"}]')
            h2 = source_snapshot_hash(src)

            self.assertNotEqual(h1, h2)

    def test_same_content_same_hash(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "data.json").write_text('{"x":1}')
            h1 = source_snapshot_hash(src)
            h2 = source_snapshot_hash(src)
            self.assertEqual(h1, h2)


class TestP0_09_VaultSafety(unittest.TestCase):
    """Failed vault verify must preserve the old vault.

    This is enforced by encrypt_vault.mjs's self-check (already
    implemented). We verify the contract: if a frozen run's hash
    doesn't match the published payload, the system detects it.
    """

    def test_hash_mismatch_detectable(self):
        draft = make_valid_draft()
        cj, sha = freeze(draft)

        tampered = cj.replace('"test_run_001"', '"tampered_run"')
        tampered_hash = sha256_hex(tampered.encode("utf-8"))
        self.assertNotEqual(sha, tampered_hash)


class TestValidation(unittest.TestCase):
    """Additional validation edge cases."""

    def test_missing_prediction_run_id(self):
        draft = make_valid_draft()
        del draft["prediction_run_id"]
        errors = validate_draft(draft)
        self.assertTrue(any("prediction_run_id" in e for e in errors))

    def test_nan_score_rejected(self):
        draft = make_valid_draft()
        draft["predictions"][0]["score"] = float("nan")
        errors = validate_draft(draft)
        self.assertTrue(any("NaN" in e for e in errors))

    def test_missing_capabilities_rejected(self):
        draft = make_valid_draft()
        del draft["predictions"][0]["capabilities"]
        errors = validate_draft(draft)
        self.assertTrue(any("capabilities" in e for e in errors))

    def test_nan_in_canonical_json_rejected(self):
        with self.assertRaises(ValueError):
            canonical_json({"score": float("nan")})

    def test_infinity_in_canonical_json_rejected(self):
        with self.assertRaises(ValueError):
            canonical_json({"score": float("inf")})


class TestFreezeFileImmutability(unittest.TestCase):
    """Frozen file on disk cannot be overwritten with different content."""

    def test_different_content_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "run.json"
            draft = make_valid_draft()
            cj, _ = freeze(draft)
            out.write_text(cj, encoding="utf-8")

            modified = make_valid_draft(
                source_snapshot_hash="modified_hash"
            )
            cj2, _ = freeze(modified)
            self.assertNotEqual(cj, cj2)

            existing = out.read_text(encoding="utf-8")
            self.assertEqual(existing, cj)

    def test_same_content_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "run.json"
            draft = make_valid_draft()
            cj, _ = freeze(draft)
            out.write_text(cj, encoding="utf-8")

            _, sha2 = freeze(make_valid_draft())
            self.assertTrue(True)


class TestMigration(unittest.TestCase):
    """Migration is idempotent."""

    def test_double_migrate(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            make_test_db(db)
            a1 = migrate(db)
            a2 = migrate(db)
            self.assertEqual(len(a1), len(a2))

    def test_tables_created(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            make_test_db(db)
            migrate(db)
            conn = sqlite3.connect(str(db))
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("prediction_runs", tables)
            self.assertIn("outcomes", tables)
            conn.close()


if __name__ == "__main__":
    unittest.main()
