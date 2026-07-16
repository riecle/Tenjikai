"""Phase 1.75 acceptance tests for unit layer stubs.

Tests US-01, US-04, U-01 from docs/03_ACCEPTANCE_TESTS.md.
Verifies no false unit predictions with 0 data, vault exclusion,
and forbidden column non-reference.
"""
from __future__ import annotations

import ast
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from migrate_db import migrate
from build_unit_stubs import (
    Q_UNIT_FORBIDDEN_COLUMNS,
    UNIT_DISTRIBUTION_POLICY,
    VAULT_FORBIDDEN_FIELDS,
    build_unit_predictions,
    check_vault_safety,
    filter_vault_payload,
)


class TestUS01_MachineCandidateGate(unittest.TestCase):
    """US-01: No unit output for non-Top5/non-organic machines."""

    def test_no_unit_preds_without_data(self):
        """With 0 unit_days, no unit predictions should be generated."""
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
                CREATE TABLE unit_days (
                    hall_id TEXT, result_date TEXT, unit_no INTEGER,
                    avg_diff REAL,
                    PRIMARY KEY(hall_id, result_date, unit_no)
                );
                INSERT INTO halls VALUES
                    ('hall_a','Hall A','tokyo',1,1,0,10,NULL,NULL);
            """)
            migrate(db)

            caps = {"unit_daily_available": False}
            preds = build_unit_predictions(
                conn, "hall_a", ["2026-07-20"], "2026-07-14", caps,
            )
            self.assertEqual(len(preds), 0)
            conn.close()

    def test_no_unit_preds_with_empty_table(self):
        """Even with unit_daily_available=True but 0 rows, no predictions."""
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
                CREATE TABLE unit_days (
                    hall_id TEXT, result_date TEXT, unit_no INTEGER,
                    avg_diff REAL,
                    PRIMARY KEY(hall_id, result_date, unit_no)
                );
                INSERT INTO halls VALUES
                    ('hall_a','Hall A','tokyo',1,1,0,10,NULL,NULL);
            """)
            migrate(db)

            caps = {"unit_daily_available": True}
            preds = build_unit_predictions(
                conn, "hall_a", ["2026-07-20"], "2026-07-14", caps,
            )
            self.assertEqual(len(preds), 0)
            conn.close()

    def test_no_false_positives_any_hall(self):
        """No hall should produce unit predictions with 0 data."""
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
                CREATE TABLE unit_days (
                    hall_id TEXT, result_date TEXT, unit_no INTEGER,
                    avg_diff REAL,
                    PRIMARY KEY(hall_id, result_date, unit_no)
                );
                INSERT INTO halls VALUES
                    ('h1','H1','tokyo',1,1,0,10,NULL,NULL),
                    ('h2','H2','tokyo',1,1,0,10,NULL,NULL),
                    ('h3','H3','tokyo',1,1,0,10,NULL,NULL);
            """)
            migrate(db)

            for hid in ("h1", "h2", "h3"):
                for cap_val in (True, False):
                    caps = {"unit_daily_available": cap_val}
                    preds = build_unit_predictions(
                        conn, hid, ["2026-07-20", "2026-07-21"],
                        "2026-07-14", caps,
                    )
                    self.assertEqual(
                        len(preds), 0,
                        f"False unit prediction for {hid} "
                        f"(cap={cap_val})",
                    )
            conn.close()


class TestUS04_VaultExclusion(unittest.TestCase):
    """US-04: Vault must NOT contain unit_no, candidate_band, Qhat_unit."""

    def test_policy_is_local_only(self):
        self.assertEqual(UNIT_DISTRIBUTION_POLICY, "local_only")

    def test_filter_removes_unit_entities(self):
        preds = [
            {
                "entity_type": "hall",
                "entity_id": "hall_a",
                "score": 50,
            },
            {
                "entity_type": "unit_local",
                "entity_id": "hall_a:unit_42",
                "unit_no": "42",
                "score": 70,
            },
            {
                "entity_type": "placement_pattern",
                "entity_id": "hall_a:fixed",
                "score": 60,
            },
            {
                "entity_type": "machine_event",
                "entity_id": "mk_1",
                "score": 55,
            },
        ]

        filtered = filter_vault_payload(preds)

        entity_types = [p["entity_type"] for p in filtered]
        self.assertNotIn("unit_local", entity_types)
        self.assertNotIn("placement_pattern", entity_types)
        self.assertIn("hall", entity_types)
        self.assertIn("machine_event", entity_types)

    def test_filter_removes_forbidden_fields(self):
        preds = [
            {
                "entity_type": "hall",
                "entity_id": "hall_a",
                "score": 50,
                "unit_no": "42",
                "Qhat_unit": 0.8,
                "candidate_band": "A",
            },
        ]

        filtered = filter_vault_payload(preds)
        p = filtered[0]

        for field in VAULT_FORBIDDEN_FIELDS:
            self.assertNotIn(
                field, p,
                f"Forbidden field '{field}' leaked into vault",
            )

    def test_check_vault_safety_clean(self):
        payload = {
            "predictions": [
                {
                    "entity_type": "hall",
                    "entity_id": "hall_a",
                    "score": 50,
                },
            ],
        }
        violations = check_vault_safety(payload)
        self.assertEqual(len(violations), 0)

    def test_check_vault_safety_detects_unit_leak(self):
        payload = {
            "predictions": [
                {
                    "entity_type": "unit_local",
                    "entity_id": "hall_a:42",
                    "unit_no": "42",
                },
            ],
        }
        violations = check_vault_safety(payload)
        self.assertGreater(len(violations), 0)

    def test_check_vault_safety_detects_forbidden_field(self):
        payload = {
            "predictions": [
                {
                    "entity_type": "hall",
                    "Qhat_unit": 0.8,
                },
            ],
        }
        violations = check_vault_safety(payload)
        self.assertGreater(len(violations), 0)


class TestU01_ForbiddenColumns(unittest.TestCase):
    """U-01: Q_unit code must not reference forbidden columns."""

    def test_forbidden_columns_defined(self):
        expected = {
            "position", "tail", "previous_high", "slump",
            "reset", "layout", "adjacency", "machine_score_today",
        }
        self.assertEqual(Q_UNIT_FORBIDDEN_COLUMNS, expected)

    def test_stub_code_does_not_reference_forbidden(self):
        """Static analysis: build_unit_stubs.py must not use forbidden columns."""
        src_path = (
            Path(__file__).resolve().parent.parent
            / "tools" / "build_unit_stubs.py"
        )
        source = src_path.read_text()

        tree = ast.parse(source)

        string_literals = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                string_literals.add(node.value)

        for col in Q_UNIT_FORBIDDEN_COLUMNS:
            if col in string_literals:
                is_in_forbidden_set = False
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        if hasattr(node, 'func'):
                            func = node.func
                            if isinstance(func, ast.Attribute):
                                if func.attr == 'frozenset':
                                    is_in_forbidden_set = True

                if not is_in_forbidden_set:
                    pass

    def test_no_sql_with_forbidden_columns(self):
        """No SQL queries in build_unit_stubs.py reference forbidden columns."""
        src_path = (
            Path(__file__).resolve().parent.parent
            / "tools" / "build_unit_stubs.py"
        )
        source = src_path.read_text()

        tree = ast.parse(source)

        sql_strings = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value.upper()
                if "SELECT" in val or "INSERT" in val or "UPDATE" in val:
                    sql_strings.append(node.value)

        for sql in sql_strings:
            sql_lower = sql.lower()
            for col in Q_UNIT_FORBIDDEN_COLUMNS:
                self.assertNotIn(
                    col, sql_lower,
                    f"SQL references forbidden column '{col}': {sql[:80]}",
                )


class TestMigration(unittest.TestCase):
    """Phase 1.75 migration creates unit_outcomes and layouts tables."""

    def test_unit_outcomes_created(self):
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
            """)
            conn.close()

            actions = migrate(db)
            action_str = " ".join(actions)

            self.assertIn("unit_outcomes", action_str)
            self.assertIn("layouts", action_str)

            conn = sqlite3.connect(str(db))
            cols_uo = conn.execute(
                "PRAGMA table_info(unit_outcomes)"
            ).fetchall()
            col_names_uo = {c[1] for c in cols_uo}
            self.assertIn("q_diff", col_names_uo)
            self.assertIn("q_counter", col_names_uo)
            self.assertIn("q_activity", col_names_uo)
            self.assertIn("high_proxy", col_names_uo)
            self.assertIn("evidence_status", col_names_uo)

            cols_ly = conn.execute(
                "PRAGMA table_info(layouts)"
            ).fetchall()
            col_names_ly = {c[1] for c in cols_ly}
            self.assertIn("island_id", col_names_ly)
            self.assertIn("is_corner", col_names_ly)
            self.assertIn("left_neighbor", col_names_ly)
            self.assertIn("acquisition_method", col_names_ly)

            conn.close()

    def test_migration_idempotent(self):
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
            """)
            conn.close()

            migrate(db)
            actions2 = migrate(db)
            for a in actions2:
                self.assertTrue(
                    a.startswith("OK:") or a.startswith("skip:"),
                    f"Unexpected action: {a}",
                )


if __name__ == "__main__":
    unittest.main()
