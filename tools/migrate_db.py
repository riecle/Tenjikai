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

PHASE1A_TABLES = [
    """CREATE TABLE IF NOT EXISTS raw_sources (
        raw_source_id TEXT PRIMARY KEY,
        source_type TEXT NOT NULL,
        acquisition_method TEXT NOT NULL,
        source_locator TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        parser_version TEXT,
        raw_path TEXT NOT NULL,
        parse_status TEXT NOT NULL,
        error_message TEXT,
        parent_raw_source_id TEXT,
        UNIQUE(source_type, source_locator, content_hash)
    )""",
    """CREATE TABLE IF NOT EXISTS machines (
        machine_id TEXT PRIMARY KEY,
        canonical_name TEXT NOT NULL,
        machine_version TEXT,
        category TEXT,
        introduced_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS hall_aliases (
        source_type TEXT NOT NULL,
        source_name TEXT NOT NULL,
        hall_id TEXT NOT NULL,
        valid_from TEXT,
        valid_to TEXT,
        PRIMARY KEY(source_type, source_name, valid_from)
    )""",
    """CREATE TABLE IF NOT EXISTS machine_aliases (
        source_type TEXT NOT NULL,
        source_name TEXT NOT NULL,
        machine_id TEXT NOT NULL,
        valid_from TEXT,
        valid_to TEXT,
        PRIMARY KEY(source_type, source_name, valid_from)
    )""",
    """CREATE TABLE IF NOT EXISTS event_families (
        event_family_id TEXT PRIMARY KEY,
        hall_id TEXT,
        family_type TEXT NOT NULL,
        rule_json TEXT NOT NULL,
        valid_from TEXT,
        valid_to TEXT,
        confidence REAL,
        source TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS hall_capabilities (
        hall_id TEXT NOT NULL,
        as_of TEXT NOT NULL,
        hall_daily_available INTEGER NOT NULL,
        machine_daily_available INTEGER NOT NULL,
        tail_daily_available INTEGER NOT NULL,
        unit_daily_available INTEGER NOT NULL,
        counter_metrics_available INTEGER NOT NULL,
        layout_available INTEGER NOT NULL,
        reset_policy_available INTEGER NOT NULL,
        acquisition_methods_json TEXT NOT NULL,
        warnings_json TEXT NOT NULL,
        PRIMARY KEY(hall_id, as_of)
    )""",
]

PHASE1A_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_event_families_hall ON event_families(hall_id)",
    "CREATE INDEX IF NOT EXISTS idx_hall_capabilities_hall ON hall_capabilities(hall_id)",
    "CREATE INDEX IF NOT EXISTS idx_raw_sources_type ON raw_sources(source_type)",
    "CREATE INDEX IF NOT EXISTS idx_machines_name ON machines(canonical_name)",
]

PHASE1A_COLUMNS = [
    "ALTER TABLE hall_days ADD COLUMN event_family_id TEXT",
    "ALTER TABLE machine_days ADD COLUMN coverage REAL",
    "ALTER TABLE machine_days ADD COLUMN label_status TEXT DEFAULT 'unknown'",
]

PHASE1B_COLUMNS = [
    "ALTER TABLE machine_days ADD COLUMN positive_rate REAL",
    "ALTER TABLE machine_days ADD COLUMN q_machine REAL",
    "ALTER TABLE machine_days ADD COLUMN event_selected_label INTEGER",
    "ALTER TABLE machine_days ADD COLUMN organic_active_day INTEGER",
    "ALTER TABLE machine_days ADD COLUMN organic_selected_label INTEGER",
]

PHASE1_5_TABLES = [
    """CREATE TABLE IF NOT EXISTS chain_pattern_results (
        chain_id TEXT NOT NULL,
        event_family_id TEXT,
        pattern_type TEXT NOT NULL,
        valid_from TEXT NOT NULL,
        valid_to TEXT NOT NULL,
        statistic REAL,
        lift REAL,
        p_value REAL,
        evidence_days INTEGER,
        confidence REAL,
        explanation_json TEXT NOT NULL,
        warnings_json TEXT NOT NULL,
        PRIMARY KEY(chain_id, event_family_id, pattern_type, valid_from)
    )""",
]

PHASE1_5_COLUMNS = [
    "ALTER TABLE halls ADD COLUMN chain_id TEXT",
]

PHASE1_5_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_chain_pattern_chain ON chain_pattern_results(chain_id)",
    "CREATE INDEX IF NOT EXISTS idx_halls_chain ON halls(chain_id)",
]

PHASE1_75_TABLES = [
    """CREATE TABLE IF NOT EXISTS unit_outcomes (
        hall_id TEXT NOT NULL,
        business_date TEXT NOT NULL,
        unit_no TEXT NOT NULL,
        q_diff REAL,
        q_counter REAL,
        q_activity REAL,
        q_unit_observed REAL,
        evidence_status TEXT NOT NULL,
        high_proxy INTEGER,
        games_reliability REAL,
        comparison_group TEXT,
        warnings_json TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY(hall_id, business_date, unit_no)
    )""",
    """CREATE TABLE IF NOT EXISTS layouts (
        hall_id TEXT NOT NULL,
        layout_version TEXT NOT NULL,
        effective_from TEXT NOT NULL,
        effective_to TEXT,
        unit_no TEXT NOT NULL,
        island_id TEXT,
        position_in_island INTEGER,
        left_neighbor TEXT,
        right_neighbor TEXT,
        is_corner INTEGER,
        corner_distance INTEGER,
        is_aisle_side INTEGER,
        visibility_class TEXT,
        acquisition_method TEXT NOT NULL,
        source_raw_id TEXT,
        PRIMARY KEY(hall_id, layout_version, unit_no)
    )""",
]

PHASE1_75_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_unit_outcomes_hall ON unit_outcomes(hall_id, business_date)",
    "CREATE INDEX IF NOT EXISTS idx_layouts_hall ON layouts(hall_id)",
]


def migrate(db_path: str | Path) -> list[str]:
    """Run all migrations. Returns list of actions taken."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    actions: list[str] = []

    all_sql = (
        PHASE0_TABLES + PHASE0_INDEXES
        + PHASE1A_TABLES + PHASE1A_INDEXES + PHASE1A_COLUMNS
        + PHASE1B_COLUMNS
        + PHASE1_5_TABLES + PHASE1_5_COLUMNS + PHASE1_5_INDEXES
        + PHASE1_75_TABLES + PHASE1_75_INDEXES
    )
    for sql in all_sql:
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
