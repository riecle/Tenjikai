"""Phase 1D acceptance tests for tail analysis.

Tests T-01 through T-04 from docs/03_ACCEPTANCE_TESTS.md.
Covers same-family filtering, shrinkage, date-pun suppression,
and 無料検定なし warning.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from migrate_db import migrate
from build_event_families import build_families
from build_tail_zscores import (
    SHRINKAGE_K,
    Z_STRONG_THRESHOLD,
    build_tail_predictions,
    compute_tail_residuals,
    compute_tail_zscores,
    is_date_pun,
)


def make_tail_db(path: Path) -> sqlite3.Connection:
    """Create a test DB with tail data across event and normal days."""
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
            ('hall_t','Tail Hall','tokyo',1,1,0,10,NULL,NULL),
            ('hall_notail','No Tail Hall','tokyo',1,1,0,10,NULL,NULL);

        -- Event rule: 7のつく日
        INSERT INTO evidence_rules VALUES
            ('hall_t','{"day_in": [7, 17, 27]}','7のつく日',0.8,0);
    """)

    event_dates = [f"2026-0{m}-{d:02d}" for m, d in [
        (1, 7), (1, 17), (1, 27), (2, 7), (2, 17), (2, 27),
        (3, 7), (3, 17), (3, 27), (4, 7), (4, 17), (4, 27),
        (5, 7), (5, 17), (5, 27), (6, 7), (6, 17), (6, 27),
        (7, 7),
    ]]
    normal_dates = [f"2026-0{m}-{d:02d}" for m, d in [
        (1, 5), (1, 10), (1, 15), (2, 5), (2, 10), (2, 15),
        (3, 5), (3, 10), (3, 15), (4, 5), (4, 10), (4, 15),
        (5, 5), (5, 10), (5, 15), (6, 5), (6, 10), (6, 15),
        (7, 5), (7, 10),
    ]]

    import random
    rng = random.Random(42)

    for date in event_dates + normal_dates:
        hall_diff = rng.gauss(300, 100)
        conn.execute(
            """INSERT INTO hall_days
               (hall_id, result_date, avg_diff, total_diff, avg_games)
               VALUES ('hall_t',?,?,?,6000)""",
            (date, hall_diff, hall_diff * 10),
        )

        for tail in range(10):
            tk = str(tail)
            if tk == "7":
                tail_diff = hall_diff + rng.gauss(200, 50)
            elif tk == "3":
                tail_diff = hall_diff + rng.gauss(-100, 50)
            else:
                tail_diff = hall_diff + rng.gauss(0, 80)

            conn.execute(
                """INSERT INTO tail_days
                   (hall_id, result_date, tail_key, avg_diff, source_name)
                   VALUES ('hall_t',?,?,?,'test')""",
                (date, tk, tail_diff),
            )

    conn.commit()
    return conn


class TestT01_SameFamilyFiltering(unittest.TestCase):
    """T-01: Tail z is computed using only the same event family."""

    def test_event_family_only(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_tail_db(db)
            migrate(db)
            build_families(conn)

            fam_row = conn.execute(
                """SELECT event_family_id FROM event_families
                   WHERE hall_id = 'hall_t' AND family_type != '通常'
                   LIMIT 1"""
            ).fetchone()
            self.assertIsNotNone(fam_row)
            event_fam_id = fam_row[0]

            residuals_fam = compute_tail_residuals(
                conn, "hall_t", event_fam_id, "2026-07-14",
            )
            residuals_all = compute_tail_residuals(
                conn, "hall_t", None, "2026-07-14",
            )

            self.assertGreater(len(residuals_fam), 0)
            self.assertGreater(len(residuals_all), 0)

            for tk in residuals_fam:
                self.assertLess(
                    len(residuals_fam[tk]),
                    len(residuals_all.get(tk, [])),
                    f"Family filter should reduce samples for tail {tk}",
                )
            conn.close()

    def test_normal_dates_excluded_from_event_family(self):
        """Event family residuals exclude normal (non-event) dates."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_tail_db(db)
            migrate(db)
            build_families(conn)

            fam_row = conn.execute(
                """SELECT event_family_id FROM event_families
                   WHERE hall_id = 'hall_t' AND family_type != '通常'
                   LIMIT 1"""
            ).fetchone()
            event_fam_id = fam_row[0]

            event_date_count = conn.execute(
                """SELECT COUNT(DISTINCT result_date) FROM hall_days
                   WHERE hall_id = 'hall_t' AND event_family_id = ?""",
                (event_fam_id,),
            ).fetchone()[0]

            residuals = compute_tail_residuals(
                conn, "hall_t", event_fam_id, "2026-07-14",
            )

            for tk, res_list in residuals.items():
                self.assertLessEqual(
                    len(res_list), event_date_count,
                    f"Tail {tk}: more residuals than event dates",
                )
            conn.close()


class TestT02_Shrinkage(unittest.TestCase):
    """T-02: Small sample z-scores are shrunk."""

    def test_shrinkage_reduces_z(self):
        residuals = {"7": [100.0, 120.0]}
        results = compute_tail_zscores(residuals)
        info = results["7"]
        self.assertEqual(info["n_eff"], 2)

        expected_shrink = 2 / (2 + SHRINKAGE_K)
        self.assertAlmostEqual(info["shrink"], expected_shrink, places=3)
        self.assertAlmostEqual(expected_shrink, 0.20, places=2)

        self.assertLess(abs(info["z_shrunk"]), abs(info["z_raw"]))

    def test_large_sample_less_shrinkage(self):
        residuals_small = {"x": [100.0, 120.0, 110.0]}
        residuals_large = {"x": [100.0] * 50}
        z_small = compute_tail_zscores(residuals_small)
        z_large = compute_tail_zscores(residuals_large)

        self.assertGreater(z_large["x"]["shrink"], z_small["x"]["shrink"])

    def test_shrinkage_formula(self):
        for n in [2, 5, 10, 20, 50, 100]:
            expected = n / (n + SHRINKAGE_K)
            residuals = {"t": [100.0] * n}
            results = compute_tail_zscores(residuals)
            self.assertAlmostEqual(
                results["t"]["shrink"], expected, places=3,
                msg=f"Shrinkage wrong for n={n}",
            )


class TestT03_NoDatePun(unittest.TestCase):
    """T-03: Date-digit coincidence alone cannot produce strong judgment."""

    def test_date_pun_detection(self):
        self.assertTrue(is_date_pun("2026-07-17", "7"))
        self.assertTrue(is_date_pun("2026-07-23", "3"))
        self.assertFalse(is_date_pun("2026-07-17", "3"))
        self.assertFalse(is_date_pun("2026-07-20", "7"))

    def test_date_pun_downgrades_strong(self):
        """When tail_key matches target date digit, strong → watch."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_tail_db(db)
            migrate(db)
            build_families(conn)

            caps = {"tail_daily_available": True}
            preds = build_tail_predictions(
                conn, "hall_t", ["2026-07-17"], "2026-07-14", caps,
            )

            tail_7_preds = [
                p for p in preds if p["entity_id"] == "7"
            ]
            for p in tail_7_preds:
                self.assertNotEqual(
                    p.get("grade"), "strong",
                    "Tail 7 on the 17th (date-pun) must not be 'strong'",
                )
            conn.close()

    def test_non_pun_can_be_strong(self):
        """Tail key NOT matching the date digit CAN be strong."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_tail_db(db)
            migrate(db)
            build_families(conn)

            caps = {"tail_daily_available": True}
            preds = build_tail_predictions(
                conn, "hall_t", ["2026-07-20"], "2026-07-14", caps,
            )

            tail_7_preds = [
                p for p in preds if p["entity_id"] == "7"
            ]
            for p in tail_7_preds:
                if p.get("z_shrunk", 0) >= Z_STRONG_THRESHOLD:
                    self.assertEqual(p["grade"], "strong")
            conn.close()


class TestT04_FreeDataWarning(unittest.TestCase):
    """T-04: Hypotheses without free-data testing → 無料検定なし warning."""

    def test_small_sample_gets_warning(self):
        """Tails with n_eff < 10 must have 無料検定なし warning."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = sqlite3.connect(str(db))
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
                    ('hall_few','Few Hall','tokyo',1,1,0,10,NULL,NULL);
            """)

            for day in range(1, 6):
                date = f"2026-07-{day:02d}"
                conn.execute(
                    """INSERT INTO hall_days
                       (hall_id, result_date, avg_diff, total_diff, avg_games)
                       VALUES ('hall_few',?,200,2000,6000)""",
                    (date,),
                )
                for tail in range(10):
                    conn.execute(
                        """INSERT INTO tail_days
                           (hall_id, result_date, tail_key, avg_diff)
                           VALUES ('hall_few',?,?,?)""",
                        (date, str(tail), 200 + tail * 10),
                    )

            conn.commit()
            migrate(db)

            caps = {"tail_daily_available": True}
            preds = build_tail_predictions(
                conn, "hall_few", ["2026-07-10"], "2026-07-06", caps,
            )

            for p in preds:
                if p["entity_id"] != "_no_data":
                    has_free_warning = any(
                        "無料検定なし" in w for w in p.get("warnings", [])
                    )
                    self.assertTrue(
                        has_free_warning,
                        f"Tail {p['entity_id']} (n={p.get('n_eff')}) "
                        f"missing 無料検定なし warning",
                    )
            conn.close()

    def test_no_tail_data_marker(self):
        """Hall without tail capability gets explicit marker."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = sqlite3.connect(str(db))
            conn.executescript("""
                CREATE TABLE halls (
                    hall_id TEXT PRIMARY KEY,
                    name TEXT, market TEXT, active INTEGER DEFAULT 1,
                    forecast_enabled INTEGER DEFAULT 1,
                    decision_floor REAL DEFAULT 0,
                    travel_minutes REAL DEFAULT 0,
                    data_through TEXT, reset_policy TEXT
                );
                INSERT INTO halls VALUES
                    ('hall_no','NoTail','tokyo',1,1,0,10,NULL,NULL);
            """)
            migrate(db)

            caps = {"tail_daily_available": False}
            preds = build_tail_predictions(
                conn, "hall_no", ["2026-07-20"], "2026-07-14", caps,
            )

            self.assertEqual(len(preds), 1)
            self.assertEqual(preds[0]["entity_id"], "_no_data")
            self.assertIn("末尾データなし", preds[0]["explanation"])
            conn.close()


class TestTailScoreRange(unittest.TestCase):
    """Tail scores should be in valid range."""

    def test_scores_0_to_100(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_tail_db(db)
            migrate(db)
            build_families(conn)

            caps = {"tail_daily_available": True}
            preds = build_tail_predictions(
                conn, "hall_t", ["2026-07-20"], "2026-07-14", caps,
            )

            for p in preds:
                if p["score"] is not None:
                    self.assertGreaterEqual(p["score"], 0)
                    self.assertLessEqual(p["score"], 100)
            conn.close()


if __name__ == "__main__":
    unittest.main()
