"""ORG-01~ORG-05: organic model uses only normal (non-event) days + gate."""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from build_machine_scores import compute_machine_features
from build_machine_labels import compute_organic_model_gate


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


class TestORG01_ExcludesEventDays(unittest.TestCase):
    def test_event_days_filtered(self):
        conn = _setup_db()
        conn.execute("INSERT INTO event_families VALUES ('ef1', 'h1', '7のつく日', '{\"day_mod10\":7}', NULL, NULL, 0.9, 'test', 'day_mod10:7')")
        event_dates = ["2026-01-07", "2026-01-17", "2026-01-27"]
        normal_dates = ["2026-01-05", "2026-01-15", "2026-01-25"]

        for d in event_dates:
            conn.execute("INSERT INTO hall_days VALUES ('h1', ?, 100, 500, 5000, 's', 'ef1', NULL)", (d,))
            conn.execute(
                "INSERT INTO machine_days VALUES ('h1', ?, 'mk1', 'M1', 200, 5000, 5, NULL, 's', NULL, 0.8, 'computed', 0.5, 0.3, 1, NULL, 1)",
                (d,),
            )
        for d in normal_dates:
            conn.execute("INSERT INTO hall_days VALUES ('h1', ?, 100, 500, 5000, 's', NULL, NULL)", (d,))
            conn.execute(
                "INSERT INTO machine_days VALUES ('h1', ?, 'mk1', 'M1', 50, 5000, 5, NULL, 's', NULL, 0.8, 'computed', 0.5, 0.3, NULL, 1, 0)",
                (d,),
            )
        conn.commit()

        features = compute_machine_features(conn, "h1", "mk1", None, "2026-02-05", "2026-02-01")
        self.assertEqual(features["eligible_days"], 3)


class TestORG02_NormalDaysOnly(unittest.TestCase):
    def test_all_event_days_returns_zero(self):
        conn = _setup_db()
        conn.execute("INSERT INTO event_families VALUES ('ef1', 'h1', '7のつく日', '{\"day_mod10\":7}', NULL, NULL, 0.9, 'test', 'day_mod10:7')")
        for d in ["2026-01-07", "2026-01-17", "2026-01-27"]:
            conn.execute("INSERT INTO hall_days VALUES ('h1', ?, 100, 500, 5000, 's', 'ef1', NULL)", (d,))
            conn.execute(
                "INSERT INTO machine_days VALUES ('h1', ?, 'mk1', 'M1', 200, 5000, 5, NULL, 's', NULL, 0.8, 'computed', 0.5, 0.3, 1, NULL, 1)",
                (d,),
            )
        conn.commit()
        features = compute_machine_features(conn, "h1", "mk1", None, "2026-02-05", "2026-02-01")
        self.assertEqual(features["eligible_days"], 0)


class TestORG03_OrganicGatePass(unittest.TestCase):
    def test_gate_passes(self):
        conn = _setup_db()
        for i in range(25):
            d = f"2026-01-{i+1:02d}"
            active = 1 if i % 4 == 0 else 0
            conn.execute(
                "INSERT INTO machine_days VALUES ('h1', ?, 'mk1', 'M1', 50, 5000, 5, NULL, 's', NULL, 0.8, 'computed', 0.5, 0.3, NULL, ?, NULL)",
                (d, active),
            )
        conn.commit()
        gate = compute_organic_model_gate(conn, "h1")
        self.assertTrue(gate["model_active"])
        self.assertGreaterEqual(gate["valid_normal_days"], 20)
        self.assertGreaterEqual(gate["activation_rate"], 0.20)


class TestORG04_OrganicGateFail(unittest.TestCase):
    def test_gate_fails_insufficient_days(self):
        conn = _setup_db()
        for i in range(10):
            d = f"2026-01-{i+1:02d}"
            conn.execute(
                "INSERT INTO machine_days VALUES ('h1', ?, 'mk1', 'M1', 50, 5000, 5, NULL, 's', NULL, 0.8, 'computed', 0.5, 0.3, NULL, 1, NULL)",
                (d,),
            )
        conn.commit()
        gate = compute_organic_model_gate(conn, "h1")
        self.assertFalse(gate["model_active"])


class TestORG05_OrganicGateFailLowActivation(unittest.TestCase):
    def test_gate_fails_low_activation(self):
        conn = _setup_db()
        for i in range(30):
            d = f"2026-01-{i+1:02d}" if i < 28 else f"2026-02-{i-27:02d}"
            active = 1 if i < 3 else 0
            conn.execute(
                "INSERT INTO machine_days VALUES ('h1', ?, 'mk1', 'M1', 50, 5000, 5, NULL, 's', NULL, 0.8, 'computed', 0.5, 0.3, NULL, ?, NULL)",
                (d, active),
            )
        conn.commit()
        gate = compute_organic_model_gate(conn, "h1")
        self.assertFalse(gate["model_active"])


if __name__ == "__main__":
    unittest.main()
