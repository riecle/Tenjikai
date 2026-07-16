"""CUT-01~CUT-05: resolved_cutoff unification tests."""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

ROOT = Path(__file__).resolve().parent.parent

from build_free_public_release import resolve_cutoff
from prediction_utils import canonical_hash
from build_predictions import build_features


def _setup_db():
    """Create an in-memory DB with the minimum schema for build_features."""
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE halls (
            hall_id TEXT PRIMARY KEY, name TEXT, chain_id TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE hall_days (
            hall_id TEXT, result_date TEXT, avg_diff REAL, total_diff REAL,
            avg_games REAL, source_name TEXT, event_family_id TEXT,
            snapshot_id TEXT
        );
        CREATE TABLE machine_days (
            hall_id TEXT, result_date TEXT, machine_key TEXT,
            machine_name TEXT, avg_diff REAL, avg_games REAL,
            units INTEGER, selected_flag INTEGER, source_name TEXT,
            snapshot_id TEXT, coverage REAL, label_status TEXT,
            positive_rate REAL, q_machine REAL,
            event_selected_label INTEGER, organic_active_day INTEGER,
            organic_selected_label INTEGER
        );
        CREATE TABLE tail_days (
            hall_id TEXT, result_date TEXT, tail_key TEXT,
            avg_diff REAL, avg_games REAL, source_name TEXT,
            snapshot_id TEXT
        );
        CREATE TABLE event_families (
            event_family_id TEXT PRIMARY KEY, hall_id TEXT,
            family_type TEXT, rule_json TEXT, valid_from TEXT,
            valid_to TEXT, confidence REAL, source TEXT,
            canonical_family_key TEXT
        );
        CREATE TABLE hall_capabilities (
            hall_id TEXT, as_of TEXT,
            hall_daily_available INTEGER,
            machine_daily_available INTEGER,
            tail_daily_available INTEGER,
            unit_daily_available INTEGER,
            counter_metrics_available INTEGER,
            layout_available INTEGER,
            reset_policy_available INTEGER,
            acquisition_methods_json TEXT,
            warnings_json TEXT,
            PRIMARY KEY(hall_id, as_of)
        );
    """)
    conn.execute(
        "INSERT INTO halls VALUES ('h1', 'Hall 1', NULL, 1)"
    )
    conn.execute(
        "INSERT INTO hall_days VALUES "
        "('h1', '2026-07-10', 100, 500, 5000, 's', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO hall_days VALUES "
        "('h1', '2026-07-12', 120, 600, 5200, 's', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO hall_days VALUES "
        "('h1', '2026-07-15', 130, 650, 5300, 's', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO machine_days VALUES "
        "('h1', '2026-07-10', 'mk1', 'M1', 100, 5000, 5, NULL, "
        "'s', NULL, 0.8, NULL, NULL, NULL, NULL, NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO tail_days VALUES "
        "('h1', '2026-07-10', '7', 100, 5000, 's', NULL)"
    )
    conn.commit()
    return conn


class TestCUT01_CutoffPassedToAllSubcommands(unittest.TestCase):
    """resolve_cutoff returns the correct value for both CLI paths."""

    def test_explicit_cutoff_returned(self):
        """With --cutoff, resolved_cutoff equals the supplied string."""
        result = resolve_cutoff("2026-07-20T23:59:59+09:00", None)
        self.assertEqual(result, "2026-07-20T23:59:59+09:00")

    def test_explicit_cutoff_with_target_dates(self):
        """--cutoff takes precedence over --target-dates."""
        result = resolve_cutoff(
            "2026-07-20T23:59:59+09:00", "2026-07-25,2026-07-26"
        )
        self.assertEqual(result, "2026-07-20T23:59:59+09:00")

    def test_cutoff_from_target_dates(self):
        """With --target-dates only, resolved_cutoff = earliest - 1 day."""
        result = resolve_cutoff(None, "2026-07-21,2026-07-22")
        self.assertEqual(result, "2026-07-20T23:59:59+09:00")

    def test_cutoff_from_single_target_date(self):
        """Single target date produces correct cutoff."""
        result = resolve_cutoff(None, "2026-07-21")
        self.assertEqual(result, "2026-07-20T23:59:59+09:00")


class TestCUT02_ChainDetectorNoDefault9999(unittest.TestCase):
    """chain_detector --cutoff CLI default must be None, not '9999-12-31'."""

    def test_default_cutoff_is_none(self):
        source = (ROOT / "tools" / "chain_detector.py").read_text(
            encoding="utf-8"
        )
        # Find the add_argument line for --cutoff and check its default
        lines = source.split("\n")
        found = False
        for i, line in enumerate(lines):
            if "--cutoff" in line and "add_argument" in line:
                # Gather surrounding context (argument may span multiple lines)
                context = "\n".join(lines[max(0, i - 1) : i + 4])
                self.assertIn(
                    "default=None",
                    context,
                    "chain_detector --cutoff should default to None",
                )
                self.assertNotIn(
                    'default="9999',
                    context,
                    "chain_detector --cutoff must not default to 9999-12-31",
                )
                self.assertNotIn(
                    "default='9999",
                    context,
                    "chain_detector --cutoff must not default to 9999-12-31",
                )
                found = True
                break
        self.assertTrue(found, "--cutoff argument not found in chain_detector.py")


class TestCUT03_ReleaseRequiresCutoff(unittest.TestCase):
    """resolve_cutoff(None, None) must raise ValueError."""

    def test_no_cutoff_no_targets_raises(self):
        with self.assertRaises(ValueError):
            resolve_cutoff(None, None)

    def test_empty_string_cutoff_no_targets_raises(self):
        """Empty string cutoff is falsy, so should also raise."""
        with self.assertRaises(ValueError):
            resolve_cutoff("", None)


class TestCUT04_CutoffAfterDataNoHashChange(unittest.TestCase):
    """Data added after the cutoff must not change feature_snapshot_hash."""

    def test_post_cutoff_data_invisible(self):
        conn = _setup_db()
        # Build features with cutoff that includes existing data
        cutoff = "2026-07-16"
        h1 = canonical_hash(build_features(conn, cutoff))

        # Add a hall_days row AFTER the cutoff
        conn.execute(
            "INSERT INTO hall_days VALUES "
            "('h1', '2026-07-20', 200, 1000, 6000, 's', NULL, NULL)"
        )
        conn.commit()

        h2 = canonical_hash(build_features(conn, cutoff))
        self.assertEqual(
            h1, h2,
            "Data after cutoff should not affect feature_snapshot_hash",
        )


class TestCUT05_CutoffBeforeDataChangesHash(unittest.TestCase):
    """Data added before the cutoff must change feature_snapshot_hash."""

    def test_pre_cutoff_data_changes_hash(self):
        conn = _setup_db()
        cutoff = "2026-07-16"
        h1 = canonical_hash(build_features(conn, cutoff))

        # Add a hall_days row BEFORE the cutoff
        conn.execute(
            "INSERT INTO hall_days VALUES "
            "('h1', '2026-07-14', 110, 550, 5100, 's', NULL, NULL)"
        )
        conn.commit()

        h2 = canonical_hash(build_features(conn, cutoff))
        self.assertNotEqual(
            h1, h2,
            "Data before cutoff must change feature_snapshot_hash",
        )


if __name__ == "__main__":
    unittest.main()
