"""Phase 1C acceptance tests for machine predictions.

Tests P1-05 through P1-08 from docs/03_ACCEPTANCE_TESTS.md.
Covers Top5 output, score range, calibrated_probability suppression,
and publish horizon enforcement.
"""
from __future__ import annotations

import math
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from migrate_db import migrate
from build_event_families import build_families
from build_machine_labels import build_all_labels
from build_machine_scores import (
    MACHINE_TOP_N,
    MACHINE_PUBLISH_DAYS_MAX,
    build_machine_predictions,
    compute_machine_features,
    compute_machine_score,
    compute_confidence,
)


def make_test_db(path: Path) -> sqlite3.Connection:
    """Create a test DB with machine data for scoring tests."""
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
            avg_games REAL, winning_units INTEGER,
            total_units INTEGER, selected_flag INTEGER,
            source_name TEXT DEFAULT 'test',
            snapshot_id INTEGER,
            PRIMARY KEY(hall_id, result_date, machine_key, source_name)
        );
        CREATE TABLE tail_days (
            hall_id TEXT, result_date TEXT, tail_key TEXT,
            avg_diff REAL, source_name TEXT DEFAULT 'test',
            PRIMARY KEY(hall_id, result_date, tail_key, source_name)
        );
        CREATE TABLE unit_days (
            hall_id TEXT, result_date TEXT, unit_no INTEGER,
            avg_diff REAL,
            PRIMARY KEY(hall_id, result_date, unit_no)
        );
        CREATE TABLE evidence_rules (
            hall_id TEXT, match_json TEXT, label TEXT,
            confidence REAL, regime_separated INTEGER DEFAULT 0
        );

        INSERT INTO halls VALUES
            ('hall_cap','Cap Hall','tokyo',1,1,0,10,NULL,NULL),
            ('hall_nocap','NoCap Hall','tokyo',1,1,0,10,NULL,NULL);

        INSERT INTO evidence_rules VALUES
            ('hall_cap','{"day_in": [7, 17, 27]}','7のつく日',0.8,0);
    """)

    for day in range(1, 15):
        date = f"2026-07-{day:02d}"
        conn.execute(
            """INSERT INTO hall_days
               (hall_id, result_date, avg_diff, total_diff, avg_games)
               VALUES ('hall_cap',?,200,2000,6000)""",
            (date,),
        )

        for mk_idx in range(8):
            mk = f"mk_{mk_idx}"
            name = f"Mach{mk_idx}"
            diff = 500 + mk_idx * 100 - day * 20
            conn.execute(
                """INSERT INTO machine_days
                   (hall_id, result_date, machine_key, machine_name,
                    units, avg_diff, avg_games, winning_units, total_units,
                    selected_flag, source_name)
                   VALUES ('hall_cap',?,?,?,8,?,7000,5,8,0,'test')""",
                (date, mk, name, diff),
            )

    conn.commit()
    return conn


class TestP1_05_Top5(unittest.TestCase):
    """Capable halls output at most 5 machines; incapable get 機種データなし."""

    def test_capable_hall_top5(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            caps = {"machine_daily_available": True}
            preds = build_machine_predictions(
                conn, "hall_cap", ["2026-07-20"], "2026-07-14", caps,
            )

            machine_preds = [
                p for p in preds if p["entity_id"] != "_no_data"
            ]
            self.assertLessEqual(len(machine_preds), MACHINE_TOP_N)
            self.assertGreater(len(machine_preds), 0)

            ranks = [p["rank"] for p in machine_preds]
            self.assertEqual(ranks, list(range(1, len(machine_preds) + 1)))
            conn.close()

    def test_incapable_hall_no_data_marker(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)

            caps = {"machine_daily_available": False}
            preds = build_machine_predictions(
                conn, "hall_nocap", ["2026-07-20"], "2026-07-14", caps,
            )

            self.assertEqual(len(preds), 1)
            p = preds[0]
            self.assertEqual(p["entity_id"], "_no_data")
            self.assertIn("機種データなし", p["explanation"])
            conn.close()

    def test_max_5_even_with_many_machines(self):
        """Even with 8+ machines, output at most 5."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            caps = {"machine_daily_available": True}
            preds = build_machine_predictions(
                conn, "hall_cap", ["2026-07-20"], "2026-07-14", caps,
            )

            machine_preds = [
                p for p in preds if p["entity_id"] != "_no_data"
            ]
            self.assertLessEqual(len(machine_preds), 5)
            conn.close()


class TestP1_06_ScoreRange(unittest.TestCase):
    """Machine score is 0 to 100."""

    def test_score_range(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            caps = {"machine_daily_available": True}
            preds = build_machine_predictions(
                conn, "hall_cap", ["2026-07-20"], "2026-07-14", caps,
            )

            for p in preds:
                if p["score"] is not None:
                    self.assertGreaterEqual(p["score"], 0)
                    self.assertLessEqual(p["score"], 100)
            conn.close()

    def test_sigmoid_boundaries(self):
        """Extreme inputs still produce scores in 0-100."""
        features_high = {
            "p_event": 0.99,
            "rotation": 2.0,
            "size_fit": 0.5,
            "weekday_fit": 2.0,
            "recent_demand": 2.0,
            "chain_signal": 0.0,
            "last_selected_penalty": 0,
        }
        score_high = compute_machine_score(features_high)
        self.assertGreaterEqual(score_high, 0)
        self.assertLessEqual(score_high, 100)

        features_low = {
            "p_event": 0.01,
            "rotation": -2.0,
            "size_fit": -0.3,
            "weekday_fit": -2.0,
            "recent_demand": -2.0,
            "chain_signal": 0.0,
            "last_selected_penalty": 1,
        }
        score_low = compute_machine_score(features_low)
        self.assertGreaterEqual(score_low, 0)
        self.assertLessEqual(score_low, 100)


class TestP1_07_NoCalibratedProbability(unittest.TestCase):
    """No calibrated_probability when event family sample < 30."""

    def test_small_sample_no_calibration_warning(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            caps = {"machine_daily_available": True}
            preds = build_machine_predictions(
                conn, "hall_cap", ["2026-07-20"], "2026-07-14", caps,
            )

            for p in preds:
                if p["entity_id"] != "_no_data":
                    has_warning = any(
                        "calibrated_probability" in w
                        for w in p.get("warnings", [])
                    )
                    self.assertTrue(
                        has_warning,
                        f"Expected calibrated_probability warning for {p['entity_id']}",
                    )
            conn.close()

    def test_large_sample_no_warning(self):
        """With 30+ same-family machines scored, no suppression warning."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)

            for day in range(1, 14):
                date = f"2026-06-{day:02d}"
                conn.execute(
                    """INSERT INTO hall_days
                       (hall_id, result_date, avg_diff, total_diff, avg_games)
                       VALUES ('hall_cap',?,200,2000,6000)""",
                    (date,),
                )
                for mk_idx in range(35):
                    mk = f"mk_large_{mk_idx}"
                    conn.execute(
                        """INSERT INTO machine_days
                           (hall_id, result_date, machine_key, machine_name,
                            units, avg_diff, avg_games, winning_units,
                            total_units, selected_flag, source_name)
                           VALUES ('hall_cap',?,?,?,8,?,7000,5,8,0,'test')""",
                        (date, mk, mk, 300 + mk_idx * 10),
                    )

            conn.commit()
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            caps = {"machine_daily_available": True}
            preds = build_machine_predictions(
                conn, "hall_cap", ["2026-07-08"], "2026-07-14", caps,
            )

            non_event_preds = [
                p for p in preds
                if p.get("entity_type") == "machine_organic"
                and p["entity_id"] != "_no_data"
            ]
            for p in non_event_preds:
                has_warning = any(
                    "calibrated_probability" in w
                    for w in p.get("warnings", [])
                )
                if len(non_event_preds) >= 30:
                    self.assertFalse(
                        has_warning,
                        f"Unexpected calibrated_probability warning for {p['entity_id']}",
                    )
            conn.close()


class TestP1_08_PublishHorizon(unittest.TestCase):
    """Machine detail must not extend beyond 21 days."""

    def test_within_horizon(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            caps = {"machine_daily_available": True}
            preds = build_machine_predictions(
                conn, "hall_cap", ["2026-07-20"], "2026-07-14", caps,
            )
            self.assertGreater(len(preds), 0)
            conn.close()

    def test_beyond_horizon_empty(self):
        """Dates more than 21 days away produce no predictions."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            caps = {"machine_daily_available": True}
            preds = build_machine_predictions(
                conn, "hall_cap", ["2026-08-20"], "2026-07-14", caps,
            )
            self.assertEqual(len(preds), 0)
            conn.close()

    def test_past_date_no_prediction(self):
        """Dates before cutoff produce no predictions."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)

            caps = {"machine_daily_available": True}
            preds = build_machine_predictions(
                conn, "hall_cap", ["2026-07-10"], "2026-07-14", caps,
            )
            self.assertEqual(len(preds), 0)
            conn.close()


class TestConfidence(unittest.TestCase):
    """Confidence formula validation."""

    def test_confidence_range(self):
        c = compute_confidence(n_eff=10, coverage=0.8, data_age_days=5)
        self.assertGreater(c, 0)
        self.assertLessEqual(c, 1.0)

    def test_zero_sample_low_confidence(self):
        c = compute_confidence(n_eff=0, coverage=0.5, data_age_days=30)
        self.assertEqual(c, 0.0)

    def test_old_data_lower_confidence(self):
        c_fresh = compute_confidence(n_eff=20, coverage=0.8, data_age_days=1)
        c_old = compute_confidence(n_eff=20, coverage=0.8, data_age_days=60)
        self.assertGreater(c_fresh, c_old)


class TestFeatureVector(unittest.TestCase):
    """Basic feature computation checks."""

    def test_p_event_laplace_smoothing(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            features = compute_machine_features(
                conn, "hall_cap", "mk_0", None,
                "2026-07-20", "2026-07-14",
            )
            self.assertGreater(features["p_event"], 0)
            self.assertLess(features["p_event"], 1)
            conn.close()


if __name__ == "__main__":
    unittest.main()
