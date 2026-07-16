"""Phase 1B acceptance tests for machine labels.

Tests P1-01 through P1-04 from docs/03_ACCEPTANCE_TESTS.md.
Covers event_selected_label, organic_active_day, organic_selected_label,
Q_machine computation, and organic model gate.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from migrate_db import migrate
from build_event_families import build_families
from build_machine_labels import (
    MIN_COVERAGE,
    MIN_UNITS,
    ORGANIC_AVG_DIFF_MIN,
    ORGANIC_POSITIVE_RATE_MIN,
    Q_MACHINE_ABS_THRESHOLD,
    SELECTED_TOP_QUANTILE,
    build_all_labels,
    compute_event_labels,
    compute_organic_labels,
    compute_organic_model_gate,
)


def make_test_db(path: Path) -> sqlite3.Connection:
    """Create a test DB with event families and machine data."""
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
            ('hall_x','Hall X','tokyo',1,1,0,10,NULL,NULL);

        -- Event day: 7のつく日 (7th, 17th, 27th)
        INSERT INTO evidence_rules VALUES
            ('hall_x','{"day_in": [7, 17, 27]}','7のつく日',0.8,0);

        -- hall_days for event days and normal days
        INSERT INTO hall_days VALUES
            ('hall_x','2026-07-07',400,4000,7000,'test'),
            ('hall_x','2026-07-08',200,2000,6000,'test'),
            ('hall_x','2026-07-09',150,1500,5500,'test'),
            ('hall_x','2026-07-17',500,5000,8000,'test'),
            ('hall_x','2026-07-18',180,1800,5800,'test');
    """)

    _insert_machine_day(conn, 'hall_x', '2026-07-07', 'mk_a', 'MachA',
                        10, 1200.0, 8000.0, 7, 10)
    _insert_machine_day(conn, 'hall_x', '2026-07-07', 'mk_b', 'MachB',
                        5, -200.0, 7000.0, 1, 5)
    _insert_machine_day(conn, 'hall_x', '2026-07-07', 'mk_c', 'MachC',
                        8, 600.0, 7500.0, 5, 8)
    _insert_machine_day(conn, 'hall_x', '2026-07-07', 'mk_d', 'MachD',
                        3, 300.0, 6000.0, 2, 3)
    _insert_machine_day(conn, 'hall_x', '2026-07-07', 'mk_e', 'MachE',
                        6, 100.0, 6500.0, 3, 6)
    _insert_machine_day(conn, 'hall_x', '2026-07-07', 'mk_f', 'MachF',
                        4, 50.0, 5000.0, 2, 4)
    _insert_machine_day(conn, 'hall_x', '2026-07-07', 'mk_g', 'MachG',
                        7, 800.0, 7800.0, 5, 7)

    _insert_machine_day(conn, 'hall_x', '2026-07-17', 'mk_a', 'MachA',
                        10, 900.0, 8200.0, 8, 10)
    _insert_machine_day(conn, 'hall_x', '2026-07-17', 'mk_b', 'MachB',
                        5, -100.0, 6800.0, 1, 5)

    _insert_machine_day(conn, 'hall_x', '2026-07-08', 'mk_a', 'MachA',
                        10, 900.0, 8000.0, 8, 10)
    _insert_machine_day(conn, 'hall_x', '2026-07-08', 'mk_b', 'MachB',
                        5, 100.0, 7000.0, 2, 5)
    _insert_machine_day(conn, 'hall_x', '2026-07-08', 'mk_c', 'MachC',
                        8, -300.0, 6000.0, 2, 8)

    _insert_machine_day(conn, 'hall_x', '2026-07-09', 'mk_a', 'MachA',
                        10, 50.0, 7500.0, 5, 10)
    _insert_machine_day(conn, 'hall_x', '2026-07-09', 'mk_b', 'MachB',
                        5, 30.0, 6500.0, 3, 5)

    _insert_machine_day(conn, 'hall_x', '2026-07-18', 'mk_a', 'MachA',
                        10, 100.0, 7000.0, 6, 10)
    _insert_machine_day(conn, 'hall_x', '2026-07-18', 'mk_b', 'MachB',
                        5, 50.0, 6000.0, 2, 5)

    conn.commit()
    return conn


def _insert_machine_day(
    conn, hall_id, date, mk, name, units, avg_diff,
    avg_games, winning, total,
):
    conn.execute(
        """INSERT INTO machine_days
           (hall_id, result_date, machine_key, machine_name,
            units, avg_diff, avg_games, winning_units, total_units,
            selected_flag, source_name)
           VALUES (?,?,?,?,?,?,?,?,?,0,'test')""",
        (hall_id, date, mk, name, units, avg_diff, avg_games,
         winning, total),
    )


class TestP1_01_SameFamilyOnly(unittest.TestCase):
    """Event model training uses same event_family_id rows only."""

    def test_event_label_only_on_event_days(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            event_labeled = conn.execute(
                """SELECT DISTINCT result_date FROM machine_days
                   WHERE hall_id = 'hall_x'
                     AND event_selected_label IS NOT NULL"""
            ).fetchall()
            event_dates = {r[0] for r in event_labeled}

            self.assertIn("2026-07-07", event_dates)
            self.assertIn("2026-07-17", event_dates)

            self.assertNotIn("2026-07-08", event_dates)
            self.assertNotIn("2026-07-09", event_dates)
            self.assertNotIn("2026-07-18", event_dates)
            conn.close()

    def test_normal_days_not_used_as_negatives(self):
        """Normal days must not have event_selected_label = 0."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            normal_with_event_label = conn.execute(
                """SELECT COUNT(*) FROM machine_days
                   WHERE hall_id = 'hall_x'
                     AND result_date IN ('2026-07-08','2026-07-09','2026-07-18')
                     AND event_selected_label IS NOT NULL"""
            ).fetchone()[0]
            self.assertEqual(normal_with_event_label, 0)
            conn.close()


class TestP1_02_UnknownNotNegative(unittest.TestCase):
    """Missing results → label stays NULL, not 0."""

    def test_null_avg_diff_gives_null_label(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)

            conn.execute(
                """INSERT INTO machine_days
                   (hall_id, result_date, machine_key, machine_name,
                    units, avg_diff, avg_games, winning_units, total_units,
                    selected_flag, source_name)
                   VALUES ('hall_x','2026-07-07','mk_null','NullMach',
                           5,NULL,NULL,NULL,NULL,0,'test')"""
            )
            conn.commit()

            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            row = conn.execute(
                """SELECT event_selected_label FROM machine_days
                   WHERE machine_key = 'mk_null'
                     AND result_date = '2026-07-07'"""
            ).fetchone()
            self.assertIsNone(row[0])
            conn.close()

    def test_null_units_gives_null_label(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)

            conn.execute(
                """INSERT INTO machine_days
                   (hall_id, result_date, machine_key, machine_name,
                    units, avg_diff, avg_games, winning_units, total_units,
                    selected_flag, source_name)
                   VALUES ('hall_x','2026-07-07','mk_nounit','NoUnit',
                           NULL,500.0,7000.0,3,5,0,'test')"""
            )
            conn.commit()

            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            row = conn.execute(
                """SELECT event_selected_label FROM machine_days
                   WHERE machine_key = 'mk_nounit'
                     AND result_date = '2026-07-07'"""
            ).fetchone()
            self.assertIsNone(row[0])
            conn.close()


class TestP1_03_OrganicAbsoluteGate(unittest.TestCase):
    """organic_active_day uses absolute thresholds, not relative ranking."""

    def test_high_machine_activates_day(self):
        """Day with avg_diff>=800, pos_rate>=0.70, units>=2, cov>=0.60."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            row = conn.execute(
                """SELECT organic_active_day FROM machine_days
                   WHERE hall_id = 'hall_x' AND result_date = '2026-07-08'
                     AND machine_key = 'mk_a'"""
            ).fetchone()
            self.assertEqual(row[0], 1)
            conn.close()

    def test_low_day_not_active(self):
        """Day without any machine meeting the absolute gate."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            row = conn.execute(
                """SELECT organic_active_day FROM machine_days
                   WHERE hall_id = 'hall_x' AND result_date = '2026-07-09'
                     AND machine_key = 'mk_a'"""
            ).fetchone()
            self.assertEqual(row[0], 0)
            conn.close()

    def test_relative_top_not_enough(self):
        """Top machine on a day still gets organic_active_day=0 if below threshold."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            rows = conn.execute(
                """SELECT machine_key, organic_active_day FROM machine_days
                   WHERE hall_id = 'hall_x' AND result_date = '2026-07-09'"""
            ).fetchall()
            for mk, active in rows:
                self.assertEqual(active, 0,
                                 f"{mk} should not be active on low day")
            conn.close()

    def test_event_days_skip_organic(self):
        """Event days should not get organic_active_day labels."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            row = conn.execute(
                """SELECT organic_active_day FROM machine_days
                   WHERE hall_id = 'hall_x' AND result_date = '2026-07-07'
                     AND machine_key = 'mk_a'"""
            ).fetchone()
            self.assertIsNone(row[0])
            conn.close()

    def test_organic_selected_only_on_active_days(self):
        """organic_selected_label only assigned when organic_active_day=1."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            inactive = conn.execute(
                """SELECT COUNT(*) FROM machine_days
                   WHERE organic_active_day = 0
                     AND organic_selected_label IS NOT NULL"""
            ).fetchone()[0]
            self.assertEqual(inactive, 0)
            conn.close()


class TestP1_04_OrganicModelGate(unittest.TestCase):
    """Organic model needs 20+ valid days and activation_rate >= 0.20."""

    def test_insufficient_days_blocks_model(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            gate = compute_organic_model_gate(conn, "hall_x")
            self.assertFalse(gate["model_active"])
            self.assertLess(gate["valid_normal_days"], 20)
            conn.close()

    def test_sufficient_days_passes_gate(self):
        """Hall with 25+ normal days and 30% activation → model active."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)

            for day_num in range(1, 31):
                if day_num in (7, 8, 9, 17, 18):
                    continue
                date = f"2026-07-{day_num:02d}"
                conn.execute(
                    """INSERT OR IGNORE INTO hall_days
                       (hall_id, result_date, avg_diff, total_diff,
                        avg_games, source_name)
                       VALUES ('hall_x',?,200,2000,6000,'test')""",
                    (date,),
                )

                is_active_day = day_num % 4 == 0
                diff = 1000.0 if is_active_day else 100.0
                wunits = 8 if is_active_day else 3

                _insert_machine_day(
                    conn, 'hall_x', date, 'mk_a', 'MachA',
                    10, diff, 7000.0, wunits, 10,
                )
                _insert_machine_day(
                    conn, 'hall_x', date, 'mk_b', 'MachB',
                    5, 50.0, 6000.0, 2, 5,
                )

            conn.commit()
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            gate = compute_organic_model_gate(conn, "hall_x")
            self.assertTrue(gate["valid_normal_days"] >= 20)
            self.assertTrue(gate["activation_rate"] >= 0.20)
            self.assertTrue(gate["model_active"])
            conn.close()


class TestQMachine(unittest.TestCase):
    """Q_machine is a within-day z-score."""

    def test_q_machine_computed(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            rows = conn.execute(
                """SELECT machine_key, q_machine FROM machine_days
                   WHERE hall_id = 'hall_x' AND result_date = '2026-07-07'
                   ORDER BY q_machine DESC"""
            ).fetchall()
            self.assertTrue(all(r[1] is not None for r in rows))

            top = rows[0]
            self.assertEqual(top[0], "mk_a")
            self.assertGreater(top[1], 0)
            conn.close()

    def test_negative_diff_gets_negative_q(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            row = conn.execute(
                """SELECT q_machine FROM machine_days
                   WHERE hall_id = 'hall_x' AND result_date = '2026-07-07'
                     AND machine_key = 'mk_b'"""
            ).fetchone()
            self.assertLess(row[0], 0)
            conn.close()


class TestDerivedColumns(unittest.TestCase):
    """positive_rate and coverage computed from existing columns."""

    def test_positive_rate_computed(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_all_labels(conn)

            row = conn.execute(
                """SELECT positive_rate FROM machine_days
                   WHERE hall_id = 'hall_x' AND result_date = '2026-07-07'
                     AND machine_key = 'mk_a'"""
            ).fetchone()
            self.assertAlmostEqual(row[0], 7.0 / 10.0)
            conn.close()

    def test_coverage_computed(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_all_labels(conn)

            row = conn.execute(
                """SELECT coverage FROM machine_days
                   WHERE hall_id = 'hall_x' AND result_date = '2026-07-07'
                     AND machine_key = 'mk_a'"""
            ).fetchone()
            self.assertAlmostEqual(row[0], 10.0 / 10.0)
            conn.close()

    def test_zero_total_units_no_crash(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            conn.execute(
                """INSERT INTO machine_days
                   (hall_id, result_date, machine_key, machine_name,
                    units, avg_diff, avg_games, winning_units, total_units,
                    selected_flag, source_name)
                   VALUES ('hall_x','2026-07-08','mk_zero','ZeroMach',
                           0,100.0,5000.0,0,0,0,'test')"""
            )
            conn.commit()
            migrate(db)
            build_all_labels(conn)
            row = conn.execute(
                """SELECT positive_rate, coverage FROM machine_days
                   WHERE machine_key = 'mk_zero'"""
            ).fetchone()
            self.assertIsNone(row[0])
            self.assertIsNone(row[1])
            conn.close()


class TestEventSelectionCriteria(unittest.TestCase):
    """Verify event_selected_label uses all four criteria."""

    def test_high_q_positive_diff_selected(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            row = conn.execute(
                """SELECT event_selected_label FROM machine_days
                   WHERE hall_id = 'hall_x' AND result_date = '2026-07-07'
                     AND machine_key = 'mk_a'"""
            ).fetchone()
            self.assertEqual(row[0], 1)
            conn.close()

    def test_negative_diff_not_selected(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            row = conn.execute(
                """SELECT event_selected_label FROM machine_days
                   WHERE hall_id = 'hall_x' AND result_date = '2026-07-07'
                     AND machine_key = 'mk_b'"""
            ).fetchone()
            self.assertEqual(row[0], 0)
            conn.close()

    def test_single_unit_not_selected(self):
        """Machine with units=1 fails the units >= 2 gate."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            conn.execute(
                """INSERT INTO machine_days
                   (hall_id, result_date, machine_key, machine_name,
                    units, avg_diff, avg_games, winning_units, total_units,
                    selected_flag, source_name)
                   VALUES ('hall_x','2026-07-07','mk_single','SingleUnit',
                           1,2000.0,9000.0,1,1,0,'test')"""
            )
            conn.commit()
            migrate(db)
            build_families(conn)
            build_all_labels(conn)

            row = conn.execute(
                """SELECT event_selected_label FROM machine_days
                   WHERE machine_key = 'mk_single'
                     AND result_date = '2026-07-07'"""
            ).fetchone()
            self.assertEqual(row[0], 0)
            conn.close()


class TestLabelIdempotency(unittest.TestCase):
    """Running labels twice gives same results."""

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)

            counts1 = build_all_labels(conn)
            labels1 = conn.execute(
                """SELECT machine_key, result_date, event_selected_label,
                          organic_active_day, organic_selected_label
                   FROM machine_days ORDER BY machine_key, result_date"""
            ).fetchall()

            conn.execute("UPDATE machine_days SET event_selected_label = NULL")
            conn.execute("UPDATE machine_days SET organic_active_day = NULL")
            conn.execute("UPDATE machine_days SET organic_selected_label = NULL")
            conn.execute("UPDATE machine_days SET q_machine = NULL")
            conn.execute("UPDATE machine_days SET positive_rate = NULL")
            conn.execute("UPDATE machine_days SET coverage = NULL")
            conn.commit()

            counts2 = build_all_labels(conn)
            labels2 = conn.execute(
                """SELECT machine_key, result_date, event_selected_label,
                          organic_active_day, organic_selected_label
                   FROM machine_days ORDER BY machine_key, result_date"""
            ).fetchall()

            self.assertEqual(labels1, labels2)
            conn.close()


if __name__ == "__main__":
    unittest.main()
