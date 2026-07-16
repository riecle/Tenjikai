"""CAP-C01~CAP-C04: counter capability uses actual counter columns."""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from build_capabilities import compute_capabilities


def _setup_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE halls (hall_id TEXT PRIMARY KEY, name TEXT, chain_id TEXT,
            active INTEGER DEFAULT 1, market TEXT, reset_policy TEXT);
        CREATE TABLE hall_days (hall_id TEXT, result_date TEXT, avg_diff REAL, total_diff REAL,
            avg_games REAL, source_name TEXT, event_family_id TEXT, snapshot_id TEXT);
        CREATE TABLE machine_days (hall_id TEXT, result_date TEXT, machine_key TEXT,
            machine_name TEXT, avg_diff REAL, avg_games REAL, units INTEGER,
            selected_flag INTEGER, source_name TEXT, snapshot_id TEXT);
        CREATE TABLE tail_days (hall_id TEXT, result_date TEXT, tail_key TEXT,
            avg_diff REAL, avg_games REAL, source_name TEXT, snapshot_id TEXT);
        CREATE TABLE unit_days (hall_id TEXT, result_date TEXT, unit_no TEXT,
            machine_name TEXT, diff REAL, games REAL, source_name TEXT,
            bb_count INTEGER, rb_count INTEGER, at_count INTEGER);
    """)
    return conn


class TestCAPc01_NoUnitDaysCounterFalse(unittest.TestCase):
    def test_zero_unit_days(self):
        conn = _setup_db()
        conn.execute("INSERT INTO halls VALUES ('h1', 'Hall', NULL, 1, 'tokyo', NULL)")
        conn.execute("INSERT INTO hall_days VALUES ('h1', '2026-01-01', 100, 500, 5000, 's', NULL, NULL)")
        conn.execute("INSERT INTO machine_days VALUES ('h1', '2026-01-01', 'mk1', 'M1', 100, 5000, 5, NULL, 's', NULL)")
        conn.commit()

        caps = compute_capabilities(conn, "2026-07-16")
        h1_cap = next(c for c in caps if c["hall_id"] == "h1")
        self.assertEqual(h1_cap["counter_metrics_available"], 0)


class TestCAPc02_AvgGamesNotCounter(unittest.TestCase):
    def test_avg_games_not_counted(self):
        conn = _setup_db()
        conn.execute("INSERT INTO halls VALUES ('h1', 'Hall', NULL, 1, 'tokyo', NULL)")
        conn.execute("INSERT INTO machine_days VALUES ('h1', '2026-01-01', 'mk1', 'M1', 100, 5000, 5, NULL, 's', NULL)")
        conn.commit()
        caps = compute_capabilities(conn, "2026-07-16")
        h1_cap = next(c for c in caps if c["hall_id"] == "h1")
        self.assertEqual(h1_cap["counter_metrics_available"], 0)


class TestCAPc03_ActualCounterTrue(unittest.TestCase):
    def test_bb_count_present(self):
        conn = _setup_db()
        conn.execute("INSERT INTO halls VALUES ('h1', 'Hall', NULL, 1, 'tokyo', NULL)")
        conn.execute("INSERT INTO unit_days VALUES ('h1', '2026-01-01', '101', 'M1', 500, 3000, 's', 5, 3, NULL)")
        conn.commit()
        caps = compute_capabilities(conn, "2026-07-16")
        h1_cap = next(c for c in caps if c["hall_id"] == "h1")
        self.assertEqual(h1_cap["counter_metrics_available"], 1)


class TestCAPc04_NullCountersStillFalse(unittest.TestCase):
    def test_null_counters(self):
        conn = _setup_db()
        conn.execute("INSERT INTO halls VALUES ('h1', 'Hall', NULL, 1, 'tokyo', NULL)")
        conn.execute("INSERT INTO unit_days VALUES ('h1', '2026-01-01', '101', 'M1', 500, 3000, 's', NULL, NULL, NULL)")
        conn.commit()
        caps = compute_capabilities(conn, "2026-07-16")
        h1_cap = next(c for c in caps if c["hall_id"] == "h1")
        self.assertEqual(h1_cap["counter_metrics_available"], 0)


if __name__ == "__main__":
    unittest.main()
