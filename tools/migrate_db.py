#!/usr/bin/env python3
"""Idempotent DB migration for FREE_PUBLIC_MVP.

Each migration is wrapped in a try/except so re-running is safe.
Stdlib-only.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PHASE0_TABLES = [
    """CREATE TABLE IF NOT EXISTS prediction_runs (
        prediction_run_id TEXT PRIMARY KEY,
        built_at TEXT NOT NULL,
        feature_cutoff_at TEXT NOT NULL,
        model_version TEXT NOT NULL,
        config_version TEXT NOT NULL,
        source_snapshot_hash TEXT NOT NULL,
        feature_snapshot_hash TEXT NOT NULL,
        code_commit TEXT,
        status TEXT NOT NULL DEFAULT 'draft',
        published_payload_hash TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS outcomes (
        target_date TEXT NOT NULL,
        hall_id TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        actual_proxy REAL,
        actual_label INTEGER,
        actual_rank INTEGER,
        outcome_status TEXT NOT NULL,
        source_raw_id TEXT,
        finalized_at TEXT,
        warnings_json TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY(target_date, hall_id, entity_type, entity_id)
    )""",
]

PHASE0_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_outcomes_date ON outcomes(target_date)",
    "CREATE INDEX IF NOT EXISTS idx_prediction_runs_status ON prediction_runs(status)",
]


def migrate(db_path: str | Path) -> list[str]:
    """Run all migrations. Returns list of actions taken."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    actions: list[str] = []

    for sql in PHASE0_TABLES + PHASE0_INDEXES:
        try:
            conn.execute(sql)
            token = sql.split("(")[0].strip()
            actions.append(f"OK: {token}")
        except sqlite3.OperationalError as e:
            actions.append(f"skip: {e}")

    conn.commit()
    conn.close()
    return actions


def main() -> None:
    ap = argparse.ArgumentParser(description="Run DB migrations")
    ap.add_argument("--db", required=True, help="Path to slot_atlas.db")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"error: {db} not found", file=sys.stderr)
        sys.exit(1)

    for line in migrate(db):
        print(line)
    print(f"migration complete: {db}")


if __name__ == "__main__":
    main()
