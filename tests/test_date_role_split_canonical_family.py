"""DR-01~DR-05: date_role_split canonical_family_key tests."""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from build_event_families import canonical_family_key_from_match
from chain_detector import detect_date_role_split


def _setup_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE halls (hall_id TEXT PRIMARY KEY, name TEXT, chain_id TEXT, active INTEGER DEFAULT 1);
        CREATE TABLE hall_days (hall_id TEXT, result_date TEXT, avg_diff REAL, total_diff REAL,
            avg_games REAL, source_name TEXT, event_family_id TEXT, snapshot_id TEXT);
        CREATE TABLE event_families (event_family_id TEXT PRIMARY KEY, hall_id TEXT,
            family_type TEXT, rule_json TEXT, valid_from TEXT, valid_to TEXT,
            confidence REAL, source TEXT, canonical_family_key TEXT);
    """)
    return conn


class TestDR01_CanonicalKeyGeneration(unittest.TestCase):
    def test_day_mod10(self):
        self.assertEqual(canonical_family_key_from_match({"day_mod10": 7}), "day_mod10:7")

    def test_zoro(self):
        self.assertEqual(canonical_family_key_from_match({"is_repdigit_day": True}), "zoro_date")

    def test_month_eq_day(self):
        self.assertEqual(canonical_family_key_from_match({"month_equals_day": True}), "month_eq_day")

    def test_anniversary(self):
        self.assertEqual(canonical_family_key_from_match({"month": 7, "day": 7}), "anniversary:7/7")

    def test_normal(self):
        self.assertEqual(canonical_family_key_from_match({}), "normal")
        self.assertEqual(canonical_family_key_from_match({"always": True}), "normal")

    def test_day_in_same_mod(self):
        self.assertEqual(canonical_family_key_from_match({"day_in": [7, 17, 27]}), "day_mod10:7")

    def test_weekday(self):
        self.assertEqual(canonical_family_key_from_match({"weekday": 5}), "weekday:5")

    def test_nth_weekday(self):
        self.assertEqual(canonical_family_key_from_match({"weekday": 6, "nth_weekday": 1}), "nth_weekday:1_6")


class TestDR02_SameFamilyCrosHall(unittest.TestCase):
    def test_same_canonical_key_different_hall_ids(self):
        key_a = canonical_family_key_from_match({"day_mod10": 7})
        key_b = canonical_family_key_from_match({"day_mod10": 7})
        self.assertEqual(key_a, key_b)


class TestDR03_DetectDateRoleSplitUsesCanonical(unittest.TestCase):
    def test_two_halls_same_canonical_not_falsely_concentrated(self):
        conn = _setup_db()
        conn.execute("INSERT INTO halls VALUES ('h_a', 'Hall A', 'chain1', 1)")
        conn.execute("INSERT INTO halls VALUES ('h_b', 'Hall B', 'chain1', 1)")

        conn.execute("INSERT INTO event_families VALUES ('ef_a1', 'h_a', '7のつく日', '{\"day_mod10\":7}', NULL, NULL, 0.9, 'test', 'day_mod10:7')")
        conn.execute("INSERT INTO event_families VALUES ('ef_b1', 'h_b', '7のつく日', '{\"day_mod10\":7}', NULL, NULL, 0.9, 'test', 'day_mod10:7')")
        conn.execute("INSERT INTO event_families VALUES ('ef_a2', 'h_a', 'ゾロ目', '{\"is_repdigit_day\":true}', NULL, NULL, 0.9, 'test', 'zoro_date')")
        conn.execute("INSERT INTO event_families VALUES ('ef_b2', 'h_b', 'ゾロ目', '{\"is_repdigit_day\":true}', NULL, NULL, 0.9, 'test', 'zoro_date')")

        for d in range(7, 28, 10):
            for m in range(1, 4):
                date = f"2026-0{m}-{d:02d}"
                conn.execute("INSERT INTO hall_days VALUES ('h_a',?,0,0,0,'s','ef_a1',NULL)", (date,))
                conn.execute("INSERT INTO hall_days VALUES ('h_b',?,0,0,0,'s','ef_b1',NULL)", (date,))
        for d in [11, 22]:
            for m in range(1, 4):
                date = f"2026-0{m}-{d:02d}"
                conn.execute("INSERT INTO hall_days VALUES ('h_a',?,0,0,0,'s','ef_a2',NULL)", (date,))
                conn.execute("INSERT INTO hall_days VALUES ('h_b',?,0,0,0,'s','ef_b2',NULL)", (date,))

        conn.commit()
        result = detect_date_role_split(conn, "chain1", ["h_a", "h_b"], "9999-12-31")

        if result is not None:
            self.assertLessEqual(result["statistic"], 0.60,
                "When both halls share same canonical families evenly, concentration should be <= 0.60")
            self.assertFalse(result["promoted"])


class TestDR04_ConcentratedFamiliesDetected(unittest.TestCase):
    def test_strength_based_family_roles_detected(self):
        conn = _setup_db()
        conn.execute("INSERT INTO halls VALUES ('h_a', 'Hall A', 'chain1', 1)")
        conn.execute("INSERT INTO halls VALUES ('h_b', 'Hall B', 'chain1', 1)")

        families = [
            ('ef_a7', 'h_a', '7のつく日', 'day_mod10:7'),
            ('ef_b7', 'h_b', '7のつく日', 'day_mod10:7'),
            ('ef_az', 'h_a', 'ゾロ目', 'zoro_date'),
            ('ef_bz', 'h_b', 'ゾロ目', 'zoro_date'),
        ]
        for fid, hid, ftype, key in families:
            conn.execute(
                "INSERT INTO event_families VALUES (?,?,?,?,NULL,NULL,0.9,'test',?)",
                (fid, hid, ftype, '{}', key),
            )

        # Baseline days provide hall-specific variance.
        for day in range(1, 13):
            date = f"2026-01-{day:02d}"
            conn.execute("INSERT INTO hall_days VALUES ('h_a',?, ?,0,0,'s',NULL,NULL)",
                         (date, -100 if day % 2 else 100))
            conn.execute("INSERT INTO hall_days VALUES ('h_b',?, ?,0,0,'s',NULL,NULL)",
                         (date, -120 if day % 2 else 120))

        # Same canonical family is observed in both halls, but the winning hall
        # switches by family: 7-days -> A, zoro -> B.
        for i, date in enumerate(['2026-02-07','2026-02-17','2026-02-27']):
            conn.execute("INSERT INTO hall_days VALUES ('h_a',?,900,0,0,'s','ef_a7',NULL)", (date,))
            conn.execute("INSERT INTO hall_days VALUES ('h_b',?,50,0,0,'s','ef_b7',NULL)", (date,))
        for date in ['2026-02-11','2026-02-22','2026-03-11']:
            conn.execute("INSERT INTO hall_days VALUES ('h_a',?,40,0,0,'s','ef_az',NULL)", (date,))
            conn.execute("INSERT INTO hall_days VALUES ('h_b',?,950,0,0,'s','ef_bz',NULL)", (date,))

        conn.commit()
        result = detect_date_role_split(conn, "chain1", ["h_a", "h_b"], "9999-12-31")
        self.assertIsNotNone(result)
        self.assertTrue(result["promoted"])
        self.assertGreaterEqual(result["statistic"], 0.60)
        self.assertIn("strength_based=true", result["explanation"])

    def test_registration_count_only_does_not_promote(self):
        conn = _setup_db()
        conn.execute("INSERT INTO halls VALUES ('h_a', 'Hall A', 'chain1', 1)")
        conn.execute("INSERT INTO halls VALUES ('h_b', 'Hall B', 'chain1', 1)")
        conn.execute("INSERT INTO event_families VALUES ('ef_a', 'h_a', '7のつく日', '{}', NULL,NULL,0.9,'test','day_mod10:7')")
        conn.execute("INSERT INTO event_families VALUES ('ef_b', 'h_b', '7のつく日', '{}', NULL,NULL,0.9,'test','day_mod10:7')")
        for day in range(1, 20):
            date = f"2026-01-{day:02d}"
            value = -100 if day % 2 else 100
            conn.execute("INSERT INTO hall_days VALUES ('h_a',?,?,0,0,'s',NULL,NULL)", (date, value))
            conn.execute("INSERT INTO hall_days VALUES ('h_b',?,?,0,0,'s',NULL,NULL)", (date, value))
        for date in ['2026-02-07','2026-02-17','2026-02-27']:
            conn.execute("INSERT INTO hall_days VALUES ('h_a',?,200,0,0,'s','ef_a',NULL)", (date,))
            conn.execute("INSERT INTO hall_days VALUES ('h_b',?,200,0,0,'s','ef_b',NULL)", (date,))
        conn.commit()
        result = detect_date_role_split(conn, "chain1", ["h_a", "h_b"], "9999-12-31")
        self.assertIsNone(result)  # only one shared family; count imbalance is irrelevant


class TestDR05_MissingCanonicalKeySkipped(unittest.TestCase):
    def test_null_canonical_excluded(self):
        conn = _setup_db()
        conn.execute("INSERT INTO halls VALUES ('h_a', 'A', 'c1', 1)")
        conn.execute("INSERT INTO halls VALUES ('h_b', 'B', 'c1', 1)")
        conn.execute("INSERT INTO event_families VALUES ('ef_x', 'h_a', '不明', '{}', NULL, NULL, 0.5, 'test', NULL)")
        for i in range(10):
            conn.execute("INSERT INTO hall_days VALUES ('h_a', ?, 0, 0, 0, 's', 'ef_x', NULL)",
                         (f"2026-01-{i+1:02d}",))
        conn.commit()
        result = detect_date_role_split(conn, "c1", ["h_a", "h_b"], "9999-12-31")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
