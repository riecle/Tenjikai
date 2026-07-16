"""HASH-01~HASH-05: feature snapshot hash covers all input tables."""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from prediction_utils import canonical_hash
from build_predictions import build_features


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
        CREATE TABLE tail_days (hall_id TEXT, result_date TEXT, tail_key TEXT,
            avg_diff REAL, avg_games REAL, source_name TEXT, snapshot_id TEXT);
        CREATE TABLE event_families (event_family_id TEXT PRIMARY KEY, hall_id TEXT,
            family_type TEXT, rule_json TEXT, valid_from TEXT, valid_to TEXT,
            confidence REAL, source TEXT, canonical_family_key TEXT);
        CREATE TABLE hall_capabilities (hall_id TEXT, as_of TEXT,
            hall_daily_available INTEGER, machine_daily_available INTEGER,
            tail_daily_available INTEGER, unit_daily_available INTEGER,
            counter_metrics_available INTEGER, layout_available INTEGER,
            reset_policy_available INTEGER, acquisition_methods_json TEXT,
            warnings_json TEXT, PRIMARY KEY(hall_id, as_of));
    """)
    conn.execute("INSERT INTO halls VALUES ('h1', 'Hall 1', NULL, 1)")
    conn.execute("INSERT INTO hall_days VALUES ('h1', '2026-01-01', 100, 500, 5000, 's', NULL, NULL)")
    conn.execute("INSERT INTO machine_days VALUES ('h1', '2026-01-01', 'mk1', 'M1', 100, 5000, 5, NULL, 's', NULL, 0.8, NULL, NULL, NULL, NULL, NULL, NULL)")
    conn.execute("INSERT INTO tail_days VALUES ('h1', '2026-01-01', '7', 100, 5000, 's', NULL)")
    conn.commit()
    return conn


class TestHASH01_ManifestIncludesAllTables(unittest.TestCase):
    def test_manifest_keys(self):
        conn = _setup_db()
        manifest = build_features(conn, "9999-12-31")
        self.assertIn("hall_days", manifest)
        self.assertIn("machine_days", manifest)
        self.assertIn("tail_days", manifest)
        self.assertIn("event_families", manifest)
        self.assertIn("hall_capabilities", manifest)


class TestHASH02_HallDaysChangeChangesHash(unittest.TestCase):
    def test_hall_days_change(self):
        conn = _setup_db()
        h1 = canonical_hash(build_features(conn, "9999-12-31"))
        conn.execute("INSERT INTO hall_days VALUES ('h1', '2026-01-02', 200, 1000, 6000, 's', NULL, NULL)")
        conn.commit()
        h2 = canonical_hash(build_features(conn, "9999-12-31"))
        self.assertNotEqual(h1, h2)


class TestHASH03_MachineDaysChangeChangesHash(unittest.TestCase):
    def test_machine_days_change(self):
        conn = _setup_db()
        h1 = canonical_hash(build_features(conn, "9999-12-31"))
        conn.execute("INSERT INTO machine_days VALUES ('h1', '2026-01-02', 'mk2', 'M2', 200, 6000, 3, NULL, 's', NULL, 0.9, NULL, NULL, NULL, NULL, NULL, NULL)")
        conn.commit()
        h2 = canonical_hash(build_features(conn, "9999-12-31"))
        self.assertNotEqual(h1, h2)


class TestHASH04_TailDaysChangeChangesHash(unittest.TestCase):
    def test_tail_days_change(self):
        conn = _setup_db()
        h1 = canonical_hash(build_features(conn, "9999-12-31"))
        conn.execute("INSERT INTO tail_days VALUES ('h1', '2026-01-02', '3', 50, 3000, 's', NULL)")
        conn.commit()
        h2 = canonical_hash(build_features(conn, "9999-12-31"))
        self.assertNotEqual(h1, h2)


class TestHASH05_StableHashOnNoChange(unittest.TestCase):
    def test_idempotent(self):
        conn = _setup_db()
        h1 = canonical_hash(build_features(conn, "9999-12-31"))
        h2 = canonical_hash(build_features(conn, "9999-12-31"))
        self.assertEqual(h1, h2)


if __name__ == "__main__":
    unittest.main()
