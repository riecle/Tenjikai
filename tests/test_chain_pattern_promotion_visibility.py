"""CP-01~CP-04: chain pattern promoted/status visibility tests."""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from chain_detector import persist_chain_results
from migrate_db import migrate


def _setup_db():
    conn = sqlite3.connect(":memory:")
    migrate_actions = []
    from migrate_db import (
        PHASE0_TABLES, PHASE0_INDEXES,
        PHASE1A_TABLES, PHASE1A_INDEXES, PHASE1A_COLUMNS,
        PHASE1B_COLUMNS,
        PHASE1_5_TABLES, PHASE1_5_COLUMNS, PHASE1_5_INDEXES,
        PHASE1_75_TABLES, PHASE1_75_INDEXES,
        FIX_V01_COLUMNS, FIX_V01_TABLES, FIX_V01_INDEXES,
    )
    all_sql = (
        PHASE0_TABLES + PHASE0_INDEXES
        + PHASE1A_TABLES + PHASE1A_INDEXES + PHASE1A_COLUMNS
        + PHASE1B_COLUMNS
        + PHASE1_5_TABLES + PHASE1_5_COLUMNS + PHASE1_5_INDEXES
        + PHASE1_75_TABLES + PHASE1_75_INDEXES
        + FIX_V01_COLUMNS + FIX_V01_TABLES + FIX_V01_INDEXES
    )
    for sql in all_sql:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    conn.execute("CREATE TABLE IF NOT EXISTS halls (hall_id TEXT PRIMARY KEY, name TEXT, chain_id TEXT, active INTEGER DEFAULT 1)")
    conn.commit()
    return conn


class TestCP01_PromotedStoredInV2(unittest.TestCase):
    def test_promoted_true_stored(self):
        conn = _setup_db()
        results = [{
            "pattern_type": "joint_machine",
            "halls": ["h_a", "h_b"],
            "lift": 3.5, "p_value": 0.01, "statistic": 0.8,
            "evidence_days": 20, "confidence": 0.85,
            "promoted": True, "explanation": ["promoted"], "warnings": [],
        }]
        persist_chain_results(conn, "chain1", None, results, "2026-01-01", "9999-12-31")
        row = conn.execute(
            "SELECT promoted, status FROM chain_pattern_results_v2 WHERE chain_id='chain1'"
        ).fetchone()
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], "detected")

    def test_not_promoted_stored(self):
        conn = _setup_db()
        results = [{
            "pattern_type": "joint_machine",
            "halls": ["h_a", "h_b"],
            "lift": 1.2, "p_value": 0.3, "statistic": 0.3,
            "evidence_days": 10, "confidence": 0.2,
            "promoted": False, "explanation": [], "warnings": [],
        }]
        persist_chain_results(conn, "chain1", None, results, "2026-01-01", "9999-12-31")
        row = conn.execute(
            "SELECT promoted, status FROM chain_pattern_results_v2 WHERE chain_id='chain1'"
        ).fetchone()
        self.assertEqual(row[0], 0)
        self.assertEqual(row[1], "not_detected")


class TestCP02_OnlyPromotedInQuery(unittest.TestCase):
    def test_query_filters_promoted(self):
        conn = _setup_db()
        results = [
            {"pattern_type": "joint_machine", "halls": ["h_a", "h_b"],
             "lift": 3.0, "p_value": 0.01, "statistic": 0.8,
             "evidence_days": 20, "confidence": 0.8,
             "promoted": True, "explanation": [], "warnings": []},
            {"pattern_type": "intensity_split", "halls": ["h_a", "h_b"],
             "lift": 0.5, "p_value": 0.5, "statistic": -0.1,
             "evidence_days": 10, "confidence": 0.1,
             "promoted": False, "explanation": [], "warnings": []},
        ]
        persist_chain_results(conn, "chain1", None, results, "2026-01-01", "9999-12-31")
        promoted_rows = conn.execute(
            "SELECT * FROM chain_pattern_results_v2 WHERE promoted=1 AND status='detected'"
        ).fetchall()
        all_rows = conn.execute("SELECT * FROM chain_pattern_results_v2").fetchall()
        self.assertEqual(len(promoted_rows), 1)
        self.assertEqual(len(all_rows), 2)


class TestCP03_SubjectKeyInV2(unittest.TestCase):
    def test_subject_key_stored(self):
        conn = _setup_db()
        results = [{
            "pattern_type": "joint_machine", "halls": ["h_a", "h_b"],
            "lift": 3.0, "p_value": 0.01, "statistic": 0.8,
            "evidence_days": 20, "confidence": 0.8,
            "promoted": True, "explanation": [], "warnings": [],
        }]
        persist_chain_results(conn, "chain1", None, results, "2026-01-01", "9999-12-31")
        row = conn.execute(
            "SELECT subject_key FROM chain_pattern_results_v2 WHERE chain_id='chain1'"
        ).fetchone()
        self.assertEqual(row[0], "pair:h_a|h_b")


class TestCP04_DateRoleSplitSubjectKey(unittest.TestCase):
    def test_date_role_uses_chain_all(self):
        conn = _setup_db()
        results = [{
            "pattern_type": "date_role_split",
            "halls": ["h_a", "h_b", "h_c"],
            "lift": 2.0, "p_value": None, "statistic": 0.7,
            "evidence_days": 30, "confidence": 0.6,
            "promoted": True, "explanation": [], "warnings": [],
        }]
        persist_chain_results(conn, "chain1", None, results, "2026-01-01", "9999-12-31")
        row = conn.execute(
            "SELECT subject_key FROM chain_pattern_results_v2 WHERE chain_id='chain1'"
        ).fetchone()
        self.assertEqual(row[0], "chain:all")


if __name__ == "__main__":
    unittest.main()
