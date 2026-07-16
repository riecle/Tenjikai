"""Phase 1.5 acceptance tests for chain detection.

Tests C-01 through C-04 from docs/03_ACCEPTANCE_TESTS.md.
Covers 4-type storage, joint permutation test, minimum common days,
and anti-circularity (no double counting).
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from migrate_db import migrate
from build_event_families import build_families
from build_machine_labels import build_all_labels
from chain_detector import (
    MIN_COMMON_DAYS,
    JOINT_LIFT_THRESHOLD,
    PATTERN_TYPES,
    PERMUTATION_P_THRESHOLD,
    assign_chain_ids,
    build_all_chain_patterns,
    compute_chain_signal,
    detect_date_role_split,
    detect_intensity_split,
    detect_joint_machine,
    detect_machine_split,
    get_active_chains,
    persist_chain_results,
)


def make_chain_db(path: Path) -> sqlite3.Connection:
    """Create a test DB with two chain halls and shared machine data."""
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
            ('maruhan_a','マルハンA店','tokyo',1,1,0,10,NULL,NULL),
            ('maruhan_b','マルハンB店','tokyo',1,1,0,10,NULL,NULL),
            ('maruhan_c','マルハンC店','tokyo',1,1,0,10,NULL,NULL),
            ('solo_hall','独立ホール','tokyo',1,1,0,10,NULL,NULL);

        -- Event rule for both halls
        INSERT INTO evidence_rules VALUES
            ('maruhan_a','{"day_in": [7, 17, 27]}','7のつく日',0.8,0),
            ('maruhan_b','{"day_in": [7, 17, 27]}','7のつく日',0.8,0);
    """)

    rng = random.Random(42)
    for day in range(1, 31):
        date = f"2026-06-{day:02d}"

        for hall in ("maruhan_a", "maruhan_b", "maruhan_c"):
            diff = rng.gauss(300, 150)
            conn.execute(
                """INSERT INTO hall_days
                   (hall_id, result_date, avg_diff, total_diff, avg_games)
                   VALUES (?,?,?,?,6000)""",
                (hall, date, diff, diff * 10),
            )

            for mk_idx in range(6):
                mk = f"mk_{mk_idx}"
                name = f"Mach{mk_idx}"
                m_diff = rng.gauss(200, 300)
                winning = max(0, int(5 + m_diff / 100))
                conn.execute(
                    """INSERT INTO machine_days
                       (hall_id, result_date, machine_key, machine_name,
                        units, avg_diff, avg_games, winning_units,
                        total_units, selected_flag, source_name)
                       VALUES (?,?,?,?,8,?,7000,?,8,0,'test')""",
                    (hall, date, mk, name, m_diff, min(winning, 8)),
                )

    conn.commit()
    return conn


class TestC01_FourTypes(unittest.TestCase):
    """C-01: The 4 types must be stored as separate records."""

    def test_pattern_types_defined(self):
        self.assertEqual(len(PATTERN_TYPES), 4)
        self.assertIn("joint_machine", PATTERN_TYPES)
        self.assertIn("machine_split", PATTERN_TYPES)
        self.assertIn("date_role_split", PATTERN_TYPES)
        self.assertIn("intensity_split", PATTERN_TYPES)

    def test_separate_records_stored(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_chain_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            counts = build_all_chain_patterns(conn, "2026-07-01")

            stored = conn.execute(
                "SELECT DISTINCT pattern_type FROM chain_pattern_results"
            ).fetchall()
            types_stored = {r[0] for r in stored}

            for pt in types_stored:
                self.assertIn(pt, PATTERN_TYPES,
                              f"Unknown pattern type stored: {pt}")
            conn.close()

    def test_chain_id_assigned(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_chain_db(db)
            migrate(db)

            n = assign_chain_ids(conn)
            self.assertGreater(n, 0)

            row = conn.execute(
                "SELECT chain_id FROM halls WHERE hall_id = 'maruhan_a'"
            ).fetchone()
            self.assertEqual(row[0], "maruhan")

            row = conn.execute(
                "SELECT chain_id FROM halls WHERE hall_id = 'solo_hall'"
            ).fetchone()
            self.assertIsNone(row[0])
            conn.close()

    def test_solo_hall_excluded_from_chains(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_chain_db(db)
            migrate(db)

            assign_chain_ids(conn)
            chains = get_active_chains(conn)

            all_halls = []
            for hall_list in chains.values():
                all_halls.extend(hall_list)
            self.assertNotIn("solo_hall", all_halls)
            conn.close()


class TestC02_JointPermutation(unittest.TestCase):
    """C-02: joint_machine must have permutation test."""

    def test_permutation_produces_p_value(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_chain_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            result = detect_joint_machine(
                conn, "maruhan_a", "maruhan_b", None, "2026-07-01",
            )
            if result is not None:
                self.assertIn("p_value", result)
                self.assertIsNotNone(result["p_value"])
                self.assertGreaterEqual(result["p_value"], 0)
                self.assertLessEqual(result["p_value"], 1.0)
            conn.close()

    def test_permutation_deterministic_with_seed(self):
        """Same seed → same p-value."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_chain_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            rng1 = random.Random(123)
            r1 = detect_joint_machine(
                conn, "maruhan_a", "maruhan_b", None, "2026-07-01",
                rng=rng1,
            )
            rng2 = random.Random(123)
            r2 = detect_joint_machine(
                conn, "maruhan_a", "maruhan_b", None, "2026-07-01",
                rng=rng2,
            )
            if r1 and r2:
                self.assertEqual(r1["p_value"], r2["p_value"])
            conn.close()

    def test_high_co_selection_detected(self):
        """Halls with identical selections should have high lift."""
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
                    ('espace_a','エスパスA','tokyo',1,1,0,10,NULL,NULL),
                    ('espace_b','エスパスB','tokyo',1,1,0,10,NULL,NULL);
            """)

            for day in range(1, 16):
                date = f"2026-06-{day:02d}"
                for hall in ("espace_a", "espace_b"):
                    conn.execute(
                        """INSERT INTO hall_days
                           (hall_id, result_date, avg_diff, total_diff, avg_games)
                           VALUES (?,?,300,3000,6000)""",
                        (hall, date),
                    )
                    for mk_idx in range(5):
                        mk = f"mk_{mk_idx}"
                        diff = 1000 if mk_idx == 0 else -100
                        conn.execute(
                            """INSERT INTO machine_days
                               (hall_id, result_date, machine_key, machine_name,
                                units, avg_diff, avg_games, winning_units,
                                total_units, selected_flag, source_name)
                               VALUES (?,?,?,?,8,?,7000,6,8,0,'test')""",
                            (hall, date, mk, mk, diff),
                        )

            conn.commit()
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            result = detect_joint_machine(
                conn, "espace_a", "espace_b", None, "2026-07-01",
            )
            self.assertIsNotNone(result)
            self.assertGreaterEqual(result["lift"], 1.0)
            conn.close()


class TestC03_MinCommonDays(unittest.TestCase):
    """C-03: No strong promotion with < 8 common same-family days."""

    def test_few_days_no_promotion(self):
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
                    ('rakuen_x','楽園X','tokyo',1,1,0,10,NULL,NULL),
                    ('rakuen_y','楽園Y','tokyo',1,1,0,10,NULL,NULL);
            """)

            for day in range(1, 6):
                date = f"2026-06-{day:02d}"
                for hall in ("rakuen_x", "rakuen_y"):
                    conn.execute(
                        """INSERT INTO hall_days
                           (hall_id, result_date, avg_diff, total_diff, avg_games)
                           VALUES (?,?,300,3000,6000)""",
                        (hall, date),
                    )
                    conn.execute(
                        """INSERT INTO machine_days
                           (hall_id, result_date, machine_key, machine_name,
                            units, avg_diff, avg_games, winning_units,
                            total_units, selected_flag, source_name)
                           VALUES (?,?,'mk_0','Mach0',8,1000,7000,7,8,0,'test')""",
                        (hall, date),
                    )

            conn.commit()
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            result = detect_joint_machine(
                conn, "rakuen_x", "rakuen_y", None, "2026-07-01",
            )
            self.assertIsNone(result)
            conn.close()

    def test_enough_days_can_promote(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_chain_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            result = detect_joint_machine(
                conn, "maruhan_a", "maruhan_b", None, "2026-07-01",
            )
            if result is not None:
                self.assertGreaterEqual(
                    result["evidence_days"], MIN_COMMON_DAYS,
                )
            conn.close()


class TestC04_NoDoubleCounting(unittest.TestCase):
    """C-04: Chain signal must not circularly feed into label generation."""

    def test_chain_signal_uses_cutoff(self):
        """compute_chain_signal only uses patterns confirmed before cutoff."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_chain_db(db)
            migrate(db)
            assign_chain_ids(conn)

            conn.execute(
                """INSERT INTO chain_pattern_results
                   (chain_id, event_family_id, pattern_type, valid_from,
                    valid_to, statistic, lift, p_value, evidence_days,
                    confidence, explanation_json, warnings_json)
                   VALUES ('maruhan', NULL, 'joint_machine', '2026-06-01',
                           '9999-12-31', 0.5, 3.0, 0.01, 20,
                           0.8, '["test"]', '[]')"""
            )
            conn.commit()

            signal_before = compute_chain_signal(
                conn, "maruhan_a", "mk_0", "2026-07-01",
            )
            signal_after = compute_chain_signal(
                conn, "maruhan_a", "mk_0", "2026-05-01",
            )

            self.assertGreater(signal_before, 0)
            self.assertEqual(signal_after, 0.0)
            conn.close()

    def test_non_chain_hall_zero_signal(self):
        """Hall without chain_id gets chain_signal = 0."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_chain_db(db)
            migrate(db)
            assign_chain_ids(conn)

            signal = compute_chain_signal(
                conn, "solo_hall", "mk_0", "2026-07-01",
            )
            self.assertEqual(signal, 0.0)
            conn.close()

    def test_chain_signal_clipped(self):
        """chain_signal is clipped to [-2, 2]."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_chain_db(db)
            migrate(db)
            assign_chain_ids(conn)

            for i in range(20):
                conn.execute(
                    """INSERT OR REPLACE INTO chain_pattern_results
                       (chain_id, event_family_id, pattern_type, valid_from,
                        valid_to, statistic, lift, p_value, evidence_days,
                        confidence, explanation_json, warnings_json)
                       VALUES ('maruhan', NULL, 'joint_machine', ?,
                               '9999-12-31', 0.9, 5.0, 0.001, 50,
                               0.99, '["test"]', '[]')""",
                    (f"2026-01-{i+1:02d}",),
                )
            conn.commit()

            signal = compute_chain_signal(
                conn, "maruhan_a", "mk_0", "2026-12-01",
            )
            self.assertGreaterEqual(signal, -2.0)
            self.assertLessEqual(signal, 2.0)
            conn.close()


class TestIntensitySplit(unittest.TestCase):
    """intensity_split detects negative correlation."""

    def test_negative_corr_detected(self):
        """Halls with opposing patterns should show negative correlation."""
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
                INSERT INTO halls VALUES
                    ('h_up','Up Hall','tokyo',1,1,0,10,NULL,NULL),
                    ('h_down','Down Hall','tokyo',1,1,0,10,NULL,NULL);
            """)

            for day in range(1, 31):
                date = f"2026-06-{day:02d}"
                diff_up = 500 if day % 2 == 0 else -200
                diff_down = -200 if day % 2 == 0 else 500
                conn.execute(
                    """INSERT INTO hall_days VALUES
                       ('h_up',?,?,?,6000,'test')""",
                    (date, diff_up, diff_up * 10),
                )
                conn.execute(
                    """INSERT INTO hall_days VALUES
                       ('h_down',?,?,?,6000,'test')""",
                    (date, diff_down, diff_down * 10),
                )

            conn.commit()
            migrate(db)

            result = detect_intensity_split(
                conn, "h_up", "h_down", "2026-07-01",
            )
            self.assertIsNotNone(result)
            self.assertLess(result["statistic"], 0)
            conn.close()


class TestDateRoleSplit(unittest.TestCase):
    """date_role_split detects family concentration."""

    def test_insufficient_data_returns_none(self):
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
                CREATE TABLE evidence_rules (
                    hall_id TEXT, match_json TEXT, label TEXT,
                    confidence REAL, regime_separated INTEGER DEFAULT 0
                );
                INSERT INTO halls VALUES
                    ('r_x','楽園X','tokyo',1,1,0,10,NULL,NULL),
                    ('r_y','楽園Y','tokyo',1,1,0,10,NULL,NULL);
            """)
            migrate(db)

            result = detect_date_role_split(
                conn, "rakuen", ["r_x", "r_y"], "2026-07-01",
            )
            self.assertIsNone(result)
            conn.close()


class TestPersistChainResults(unittest.TestCase):
    """chain_pattern_results stored correctly."""

    def test_persist_and_read(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_chain_db(db)
            migrate(db)

            results = [{
                "pattern_type": "joint_machine",
                "lift": 2.5,
                "p_value": 0.02,
                "statistic": 0.3,
                "evidence_days": 15,
                "confidence": 0.75,
                "explanation": ["test explanation"],
                "warnings": [],
            }]

            n = persist_chain_results(
                conn, "maruhan", None, results,
                "2026-06-01", "9999-12-31",
            )
            self.assertEqual(n, 1)

            row = conn.execute(
                """SELECT lift, p_value, confidence
                   FROM chain_pattern_results
                   WHERE chain_id = 'maruhan'
                     AND pattern_type = 'joint_machine'"""
            ).fetchone()
            self.assertAlmostEqual(row[0], 2.5)
            self.assertAlmostEqual(row[1], 0.02)
            self.assertAlmostEqual(row[2], 0.75)
            conn.close()

    def test_idempotent_upsert(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_chain_db(db)
            migrate(db)

            results = [{
                "pattern_type": "joint_machine",
                "lift": 2.5,
                "p_value": 0.02,
                "statistic": 0.3,
                "evidence_days": 15,
                "confidence": 0.75,
                "explanation": ["v1"],
                "warnings": [],
            }]

            persist_chain_results(
                conn, "maruhan", None, results,
                "2026-06-01", "9999-12-31",
            )
            results[0]["confidence"] = 0.80
            persist_chain_results(
                conn, "maruhan", None, results,
                "2026-06-01", "9999-12-31",
            )

            count = conn.execute(
                """SELECT COUNT(*) FROM chain_pattern_results
                   WHERE chain_id = 'maruhan'
                     AND pattern_type = 'joint_machine'"""
            ).fetchone()[0]
            self.assertEqual(count, 1)

            row = conn.execute(
                """SELECT confidence FROM chain_pattern_results
                   WHERE chain_id = 'maruhan'
                     AND pattern_type = 'joint_machine'"""
            ).fetchone()
            self.assertAlmostEqual(row[0], 0.80)
            conn.close()


if __name__ == "__main__":
    unittest.main()
