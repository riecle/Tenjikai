"""PK-01~PK-04: chain pattern pair primary key tests (no overwrite)."""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from chain_detector import persist_chain_results, _make_subject_key


def _setup_db():
    conn = sqlite3.connect(":memory:")
    from migrate_db import (
        PHASE1_5_TABLES, FIX_V01_TABLES,
    )
    for sql in PHASE1_5_TABLES + FIX_V01_TABLES:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return conn


class TestPK01_SubjectKeyGeneration(unittest.TestCase):
    def test_pair_key(self):
        r = {"pattern_type": "joint_machine", "halls": ["h_b", "h_a"]}
        self.assertEqual(_make_subject_key(r), "pair:h_a|h_b")

    def test_chain_all_key(self):
        r = {"pattern_type": "date_role_split", "halls": ["h_a", "h_b", "h_c"]}
        self.assertEqual(_make_subject_key(r), "chain:all")


class TestPK02_ThreeHallPairsNotOverwritten(unittest.TestCase):
    def test_three_pairs_preserved(self):
        conn = _setup_db()
        results_ab = [{
            "pattern_type": "joint_machine", "halls": ["h_a", "h_b"],
            "lift": 3.0, "p_value": 0.01, "statistic": 0.8,
            "evidence_days": 20, "confidence": 0.8,
            "promoted": True, "explanation": [], "warnings": [],
        }]
        results_ac = [{
            "pattern_type": "joint_machine", "halls": ["h_a", "h_c"],
            "lift": 2.5, "p_value": 0.02, "statistic": 0.7,
            "evidence_days": 18, "confidence": 0.7,
            "promoted": True, "explanation": [], "warnings": [],
        }]
        results_bc = [{
            "pattern_type": "joint_machine", "halls": ["h_b", "h_c"],
            "lift": 1.5, "p_value": 0.08, "statistic": 0.4,
            "evidence_days": 15, "confidence": 0.4,
            "promoted": False, "explanation": [], "warnings": [],
        }]
        persist_chain_results(conn, "chain1", None, results_ab, "2026-01-01", "9999-12-31")
        persist_chain_results(conn, "chain1", None, results_ac, "2026-01-01", "9999-12-31")
        persist_chain_results(conn, "chain1", None, results_bc, "2026-01-01", "9999-12-31")

        rows = conn.execute(
            """SELECT subject_key, lift FROM chain_pattern_results_v2
               WHERE chain_id='chain1' AND pattern_type='joint_machine'
               ORDER BY subject_key"""
        ).fetchall()
        self.assertEqual(len(rows), 3)
        keys = [r[0] for r in rows]
        self.assertIn("pair:h_a|h_b", keys)
        self.assertIn("pair:h_a|h_c", keys)
        self.assertIn("pair:h_b|h_c", keys)


class TestPK03_SamePairUpdatesNotDuplicates(unittest.TestCase):
    def test_upsert_same_pair(self):
        conn = _setup_db()
        results = [{
            "pattern_type": "joint_machine", "halls": ["h_a", "h_b"],
            "lift": 3.0, "p_value": 0.01, "statistic": 0.8,
            "evidence_days": 20, "confidence": 0.8,
            "promoted": True, "explanation": [], "warnings": [],
        }]
        persist_chain_results(conn, "chain1", None, results, "2026-01-01", "9999-12-31")
        results[0]["lift"] = 4.0
        persist_chain_results(conn, "chain1", None, results, "2026-01-01", "9999-12-31")
        rows = conn.execute(
            "SELECT lift FROM chain_pattern_results_v2 WHERE chain_id='chain1'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0][0], 4.0)


class TestPK04_MixedPatternTypes(unittest.TestCase):
    def test_different_types_same_pair(self):
        conn = _setup_db()
        r1 = [{
            "pattern_type": "joint_machine", "halls": ["h_a", "h_b"],
            "lift": 3.0, "p_value": 0.01, "statistic": 0.8,
            "evidence_days": 20, "confidence": 0.8,
            "promoted": True, "explanation": [], "warnings": [],
        }]
        r2 = [{
            "pattern_type": "intensity_split", "halls": ["h_a", "h_b"],
            "lift": 0.5, "p_value": None, "statistic": -0.4,
            "evidence_days": 20, "confidence": 0.6,
            "promoted": True, "explanation": [], "warnings": [],
        }]
        persist_chain_results(conn, "chain1", None, r1, "2026-01-01", "9999-12-31")
        persist_chain_results(conn, "chain1", None, r2, "2026-01-01", "9999-12-31")
        rows = conn.execute(
            "SELECT pattern_type FROM chain_pattern_results_v2 WHERE chain_id='chain1' ORDER BY pattern_type"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "intensity_split")
        self.assertEqual(rows[1][0], "joint_machine")


if __name__ == "__main__":
    unittest.main()
