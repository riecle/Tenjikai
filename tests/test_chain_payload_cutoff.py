"""CUT-CHAIN-01~04: chain pattern payload cutoff filtering tests."""
import hashlib
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from prediction_utils import canonical_hash, canonical_json

# SQL filter matching the one used by build_features in build_predictions.py
CHAIN_FILTER_SQL = """\
SELECT chain_id, event_family_id, pattern_type, subject_key,
       valid_from, valid_to, statistic, lift, p_value,
       evidence_days, confidence, promoted, status,
       explanation_json, warnings_json
  FROM chain_pattern_results_v2
 WHERE promoted = 1 AND status = 'detected'
   AND valid_from <= ?
   AND (valid_to IS NULL OR valid_to = '' OR valid_to > ?)
 ORDER BY chain_id, event_family_id, pattern_type,
          subject_key, valid_from
"""


def _setup_db():
    """Create an in-memory DB with halls and chain_pattern_results_v2.

    Inserts four test patterns:
      #1  valid_from='2026-07-10', valid_to=NULL,        promoted=1, detected
      #2  valid_from='2026-07-20', valid_to=NULL,        promoted=1, detected
      #3  valid_from='2026-07-01', valid_to='2026-07-10', promoted=1, detected
      #4  valid_from='2026-07-10', valid_to=NULL,        promoted=0, not_detected
    """
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE halls (
            hall_id TEXT PRIMARY KEY, name TEXT, chain_id TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE chain_pattern_results_v2 (
            chain_id TEXT NOT NULL,
            event_family_id TEXT NOT NULL DEFAULT '',
            pattern_type TEXT NOT NULL,
            subject_key TEXT NOT NULL DEFAULT '',
            valid_from TEXT NOT NULL,
            valid_to TEXT,
            statistic REAL,
            lift REAL,
            p_value REAL,
            evidence_days INTEGER,
            confidence REAL,
            promoted INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'unknown',
            explanation_json TEXT NOT NULL,
            warnings_json TEXT NOT NULL,
            PRIMARY KEY(chain_id, event_family_id, pattern_type,
                         subject_key, valid_from)
        );
    """)

    # Halls belonging to chain1
    conn.execute("INSERT INTO halls VALUES ('hall_a', 'Hall A', 'chain1', 1)")
    conn.execute("INSERT INTO halls VALUES ('hall_b', 'Hall B', 'chain1', 1)")

    # Pattern #1: active, promoted, valid_from before cutoff, no expiry
    conn.execute(
        "INSERT INTO chain_pattern_results_v2 VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("chain1", "", "joint_machine", "pair:hall_a|hall_b",
         "2026-07-10", None, 0.8, 3.5, 0.01, 20, 0.85,
         1, "detected", "[]", "[]"),
    )

    # Pattern #2: promoted but valid_from is AFTER the test cutoff
    conn.execute(
        "INSERT INTO chain_pattern_results_v2 VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("chain1", "", "machine_split", "pair:hall_a|hall_b",
         "2026-07-20", None, 0.6, 2.0, 0.03, 15, 0.70,
         1, "detected", "[]", "[]"),
    )

    # Pattern #3: promoted but expired (valid_to < cutoff)
    conn.execute(
        "INSERT INTO chain_pattern_results_v2 VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("chain1", "", "intensity_split", "pair:hall_a|hall_b",
         "2026-07-01", "2026-07-10", 0.5, 1.8, 0.04, 10, 0.60,
         1, "detected", "[]", "[]"),
    )

    # Pattern #4: not promoted
    conn.execute(
        "INSERT INTO chain_pattern_results_v2 VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("chain1", "", "date_role_split", "chain:all",
         "2026-07-10", None, 0.3, 1.2, 0.30, 10, 0.20,
         0, "not_detected", "[]", "[]"),
    )

    conn.commit()
    return conn


class TestCUTCHAIN01_FutureChainExcluded(unittest.TestCase):
    """Pattern #2 (valid_from=2026-07-20) excluded by cutoff 2026-07-15."""

    def test_future_pattern_excluded(self):
        conn = _setup_db()
        cutoff = "2026-07-15"
        rows = conn.execute(CHAIN_FILTER_SQL, (cutoff, cutoff)).fetchall()

        # Only pattern #1 should pass: promoted, detected, valid_from<=cutoff,
        # and valid_to is NULL (no expiry)
        chain_ids_and_types = [(r[0], r[2]) for r in rows]
        self.assertEqual(len(rows), 1, f"Expected 1 row, got {len(rows)}")
        self.assertEqual(rows[0][2], "joint_machine")
        # Confirm pattern #2 (machine_split) is absent
        self.assertNotIn(
            ("chain1", "machine_split"),
            chain_ids_and_types,
            "Future pattern should be excluded by cutoff",
        )


class TestCUTCHAIN02_ExpiredChainExcluded(unittest.TestCase):
    """Pattern #3 (valid_to=2026-07-10) excluded by cutoff 2026-07-15."""

    def test_expired_pattern_excluded(self):
        conn = _setup_db()
        cutoff = "2026-07-15"
        rows = conn.execute(CHAIN_FILTER_SQL, (cutoff, cutoff)).fetchall()

        types_found = [r[2] for r in rows]
        self.assertNotIn(
            "intensity_split",
            types_found,
            "Expired pattern (valid_to=2026-07-10) should be excluded "
            "when cutoff is 2026-07-15",
        )


class TestCUTCHAIN03_UnpromotedChainExcluded(unittest.TestCase):
    """Pattern #4 (promoted=0) excluded regardless of dates."""

    def test_unpromoted_excluded(self):
        conn = _setup_db()
        cutoff = "2026-07-15"
        rows = conn.execute(CHAIN_FILTER_SQL, (cutoff, cutoff)).fetchall()

        types_found = [r[2] for r in rows]
        self.assertNotIn(
            "date_role_split",
            types_found,
            "Unpromoted pattern must be excluded",
        )

    def test_unpromoted_excluded_even_with_far_future_cutoff(self):
        """Even with a permissive cutoff, unpromoted patterns stay out."""
        conn = _setup_db()
        cutoff = "9999-12-31"
        rows = conn.execute(CHAIN_FILTER_SQL, (cutoff, cutoff)).fetchall()

        for row in rows:
            self.assertEqual(
                row[11], 1,
                "Only promoted=1 rows should appear in filtered results",
            )


class TestCUTCHAIN04_SameFrozenInputSameResult(unittest.TestCase):
    """Deterministic query + canonical hash must be stable across runs."""

    def test_hash_stability(self):
        conn = _setup_db()
        cutoff = "2026-07-15"

        rows1 = conn.execute(CHAIN_FILTER_SQL, (cutoff, cutoff)).fetchall()
        cols = [
            "chain_id", "event_family_id", "pattern_type", "subject_key",
            "valid_from", "valid_to", "statistic", "lift", "p_value",
            "evidence_days", "confidence", "promoted", "status",
            "explanation_json", "warnings_json",
        ]
        data1 = [dict(zip(cols, row)) for row in rows1]
        h1 = canonical_hash(data1)

        rows2 = conn.execute(CHAIN_FILTER_SQL, (cutoff, cutoff)).fetchall()
        data2 = [dict(zip(cols, row)) for row in rows2]
        h2 = canonical_hash(data2)

        self.assertEqual(
            h1, h2,
            "Same frozen input must produce the same canonical hash",
        )

    def test_hash_changes_with_different_cutoff(self):
        """Different cutoff that includes more rows must change the hash."""
        conn = _setup_db()
        cols = [
            "chain_id", "event_family_id", "pattern_type", "subject_key",
            "valid_from", "valid_to", "statistic", "lift", "p_value",
            "evidence_days", "confidence", "promoted", "status",
            "explanation_json", "warnings_json",
        ]

        cutoff_narrow = "2026-07-15"
        rows_narrow = conn.execute(
            CHAIN_FILTER_SQL, (cutoff_narrow, cutoff_narrow)
        ).fetchall()
        data_narrow = [dict(zip(cols, row)) for row in rows_narrow]
        h_narrow = canonical_hash(data_narrow)

        # With cutoff far in the future, pattern #2 also appears
        cutoff_wide = "9999-12-31"
        rows_wide = conn.execute(
            CHAIN_FILTER_SQL, (cutoff_wide, cutoff_wide)
        ).fetchall()
        data_wide = [dict(zip(cols, row)) for row in rows_wide]
        h_wide = canonical_hash(data_wide)

        self.assertNotEqual(
            h_narrow, h_wide,
            "Including more patterns with a wider cutoff must change the hash",
        )


if __name__ == "__main__":
    unittest.main()
