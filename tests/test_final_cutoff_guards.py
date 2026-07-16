import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from build_capabilities import compute_capabilities
from build_machine_labels import compute_organic_model_gate
from build_predictions import compute_feature_cutoff, load_hall_capabilities


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE halls (
            hall_id TEXT PRIMARY KEY, name TEXT, chain_id TEXT,
            active INTEGER DEFAULT 1, reset_policy TEXT
        );
        CREATE TABLE hall_days (
            hall_id TEXT, result_date TEXT, avg_diff REAL,
            total_diff REAL, avg_games REAL, source_name TEXT,
            event_family_id TEXT
        );
        CREATE TABLE machine_days (
            hall_id TEXT, result_date TEXT, machine_key TEXT,
            organic_active_day INTEGER
        );
        CREATE TABLE tail_days (
            hall_id TEXT, result_date TEXT, tail_key TEXT
        );
        CREATE TABLE unit_days (
            hall_id TEXT, result_date TEXT, unit_no TEXT,
            bb_count INTEGER, rb_count INTEGER, at_count INTEGER
        );
    """)
    conn.execute("INSERT INTO halls VALUES ('h1','Hall',NULL,1,NULL)")
    return conn


class TestOrganicGateCutoff(unittest.TestCase):
    def test_future_organic_days_do_not_enable_past_model(self):
        conn = make_db()
        for day in range(1, 11):
            conn.execute(
                "INSERT INTO machine_days VALUES ('h1',?,?,?)",
                (f"2026-01-{day:02d}", "m1", 1 if day <= 2 else 0),
            )
        for day in range(1, 21):
            conn.execute(
                "INSERT INTO machine_days VALUES ('h1',?,?,1)",
                (f"2026-08-{day:02d}", "m1"),
            )
        past = compute_organic_model_gate(
            conn, "h1", min_valid_days=20, min_activation_rate=0.2,
            cutoff_date="2026-07-01",
        )
        full = compute_organic_model_gate(
            conn, "h1", min_valid_days=20, min_activation_rate=0.2,
            cutoff_date="2026-09-01",
        )
        self.assertFalse(past["model_active"])
        self.assertTrue(full["model_active"])
        conn.close()


class TestCapabilityCutoff(unittest.TestCase):
    def test_future_only_data_is_not_available_in_past(self):
        conn = make_db()
        conn.execute("INSERT INTO machine_days VALUES ('h1','2026-08-01','m1',NULL)")
        conn.execute("INSERT INTO tail_days VALUES ('h1','2026-08-01','7')")
        conn.execute("INSERT INTO unit_days VALUES ('h1','2026-08-01','101',5,NULL,NULL)")
        past = compute_capabilities(conn, "2026-07-01T00:00:00+09:00")
        future = compute_capabilities(conn, "2026-09-01T00:00:00+09:00")
        p = past[0]
        f = future[0]
        self.assertEqual(p["machine_daily_available"], 0)
        self.assertEqual(p["tail_daily_available"], 0)
        self.assertEqual(p["unit_daily_available"], 0)
        self.assertEqual(p["counter_metrics_available"], 0)
        self.assertEqual(f["machine_daily_available"], 1)
        self.assertEqual(f["tail_daily_available"], 1)
        self.assertEqual(f["unit_daily_available"], 1)
        self.assertEqual(f["counter_metrics_available"], 1)
        conn.close()

    def test_prediction_capability_loader_uses_cutoff(self):
        conn = make_db()
        conn.execute("INSERT INTO hall_days VALUES ('h1','2026-01-01',1,1,1,'s',NULL)")
        conn.execute("INSERT INTO tail_days VALUES ('h1','2026-08-01','7')")
        caps = load_hall_capabilities(conn, "2026-07-01")
        self.assertTrue(caps["h1"]["hall_daily_available"])
        self.assertFalse(caps["h1"]["tail_daily_available"])
        conn.close()


class TestHistoricalRunReproduction(unittest.TestCase):
    def test_future_rows_may_exist(self):
        conn = make_db()
        conn.execute("INSERT INTO hall_days VALUES ('h1','2026-08-01',1,1,1,'s',NULL)")
        cutoff = compute_feature_cutoff(conn, "2026-07-01T00:00:00+09:00")
        self.assertEqual(cutoff, "2026-07-01T00:00:00+09:00")
        conn.close()


if __name__ == "__main__":
    unittest.main()
