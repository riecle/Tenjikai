"""ROT-01~ROT-05: machine rotation uses selected dates only."""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from build_machine_scores import compute_machine_features


def _setup_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE halls (hall_id TEXT PRIMARY KEY, name TEXT, chain_id TEXT, active INTEGER DEFAULT 1);
        CREATE TABLE hall_days (hall_id TEXT, result_date TEXT, avg_diff REAL, total_diff REAL,
            avg_games REAL, source_name TEXT, event_family_id TEXT, snapshot_id TEXT);
        CREATE TABLE machine_days (hall_id TEXT, result_date TEXT, machine_key TEXT,
            machine_name TEXT, avg_diff REAL, avg_games REAL, units INTEGER,
            selected_flag INTEGER, source_name TEXT, snapshot_id TEXT,
            coverage REAL, label_status TEXT, positive_rate REAL, q_machine REAL,
            event_selected_label INTEGER, organic_active_day INTEGER,
            organic_selected_label INTEGER);
        CREATE TABLE event_families (event_family_id TEXT PRIMARY KEY, hall_id TEXT,
            family_type TEXT, rule_json TEXT, valid_from TEXT, valid_to TEXT,
            confidence REAL, source TEXT, canonical_family_key TEXT);
    """)
    conn.execute("INSERT INTO halls VALUES ('h1', 'Hall 1', NULL, 1)")
    return conn


class TestROT01_RotationUsesSelectedDates(unittest.TestCase):
    def test_only_selected_dates_used_for_gaps(self):
        conn = _setup_db()
        conn.execute("INSERT INTO event_families VALUES ('ef1', 'h1', '7のつく日', '{\"day_mod10\":7}', NULL, NULL, 0.9, 'test', 'day_mod10:7')")
        dates = ["2026-01-07", "2026-01-17", "2026-01-27",
                 "2026-02-07", "2026-02-17", "2026-02-27",
                 "2026-03-07", "2026-03-17"]
        for d in dates:
            conn.execute("INSERT INTO hall_days VALUES ('h1', ?, 100, 500, 5000, 's', 'ef1', NULL)", (d,))
            selected = 1 if d.endswith("07") else 0
            conn.execute(
                "INSERT INTO machine_days VALUES ('h1', ?, 'mk1', 'Machine1', 100, 5000, 5, NULL, 's', NULL, 0.8, 'computed', 0.5, 0.3, ?, NULL, NULL)",
                (d, selected),
            )
        conn.commit()

        features = compute_machine_features(conn, "h1", "mk1", "ef1", "2026-04-07", "2026-04-01")
        self.assertAlmostEqual(features["rotation"], 0.0, delta=2.1)
        self.assertEqual(features["hit_days"], 3)


class TestROT02_NoSelectedDatesZeroRotation(unittest.TestCase):
    def test_no_selected_gives_zero(self):
        conn = _setup_db()
        conn.execute("INSERT INTO event_families VALUES ('ef1', 'h1', '7のつく日', '{\"day_mod10\":7}', NULL, NULL, 0.9, 'test', 'day_mod10:7')")
        for d in ["2026-01-07", "2026-01-17", "2026-01-27"]:
            conn.execute("INSERT INTO hall_days VALUES ('h1', ?, 100, 500, 5000, 's', 'ef1', NULL)", (d,))
            conn.execute(
                "INSERT INTO machine_days VALUES ('h1', ?, 'mk1', 'Machine1', 100, 5000, 5, NULL, 's', NULL, 0.8, 'computed', 0.5, 0.3, 0, NULL, NULL)",
                (d,),
            )
        conn.commit()
        features = compute_machine_features(conn, "h1", "mk1", "ef1", "2026-02-07", "2026-02-01")
        self.assertEqual(features["rotation"], 0.0)


class TestROT03_SingleSelectedDateZeroRotation(unittest.TestCase):
    def test_single_selected(self):
        conn = _setup_db()
        conn.execute("INSERT INTO event_families VALUES ('ef1', 'h1', '7のつく日', '{\"day_mod10\":7}', NULL, NULL, 0.9, 'test', 'day_mod10:7')")
        for i, d in enumerate(["2026-01-07", "2026-01-17", "2026-01-27"]):
            conn.execute("INSERT INTO hall_days VALUES ('h1', ?, 100, 500, 5000, 's', 'ef1', NULL)", (d,))
            selected = 1 if i == 0 else 0
            conn.execute(
                "INSERT INTO machine_days VALUES ('h1', ?, 'mk1', 'Machine1', 100, 5000, 5, NULL, 's', NULL, 0.8, 'computed', 0.5, 0.3, ?, NULL, NULL)",
                (d, selected),
            )
        conn.commit()
        features = compute_machine_features(conn, "h1", "mk1", "ef1", "2026-02-07", "2026-02-01")
        self.assertEqual(features["rotation"], 0.0)


class TestROT04_OrganicRotationSelectedDates(unittest.TestCase):
    def test_organic_selected_dates(self):
        conn = _setup_db()
        dates = ["2026-01-05", "2026-01-15", "2026-01-25",
                 "2026-02-05", "2026-02-15"]
        for d in dates:
            conn.execute("INSERT INTO hall_days VALUES ('h1', ?, 100, 500, 5000, 's', NULL, NULL)", (d,))
            selected = 1 if d.endswith("05") else 0
            conn.execute(
                "INSERT INTO machine_days VALUES ('h1', ?, 'mk1', 'Machine1', 100, 5000, 5, NULL, 's', NULL, 0.8, 'computed', 0.5, 0.3, NULL, NULL, ?)",
                (d, selected),
            )
        conn.commit()
        features = compute_machine_features(conn, "h1", "mk1", None, "2026-03-05", "2026-03-01")
        self.assertEqual(features["hit_days"], 2)


class TestROT05_SelectedGapMedianCorrect(unittest.TestCase):
    def test_gap_median(self):
        conn = _setup_db()
        conn.execute("INSERT INTO event_families VALUES ('ef1', 'h1', '7のつく日', '{\"day_mod10\":7}', NULL, NULL, 0.9, 'test', 'day_mod10:7')")
        selected_dates = ["2026-01-07", "2026-02-07", "2026-03-07"]
        nonselected_dates = ["2026-01-17", "2026-01-27", "2026-02-17", "2026-02-27"]
        for d in selected_dates + nonselected_dates:
            conn.execute("INSERT INTO hall_days VALUES ('h1', ?, 100, 500, 5000, 's', 'ef1', NULL)", (d,))
            sel = 1 if d in selected_dates else 0
            conn.execute(
                "INSERT INTO machine_days VALUES ('h1', ?, 'mk1', 'Machine1', 100, 5000, 5, NULL, 's', NULL, 0.8, 'computed', 0.5, 0.3, ?, NULL, NULL)",
                (d, sel),
            )
        conn.commit()
        features = compute_machine_features(conn, "h1", "mk1", "ef1", "2026-04-07", "2026-04-01")
        self.assertNotEqual(features["rotation"], 0.0)
        self.assertEqual(features["hit_days"], 3)


if __name__ == "__main__":
    unittest.main()
