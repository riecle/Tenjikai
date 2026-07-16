#!/usr/bin/env python3
"""Populate raw_sources from source_snapshots and machines from machine_days.

Transforms existing data into the v1.2 lineage schema without modifying
the original tables.

Usage:
    python3 tools/normalize_sources.py --db slot_atlas.db

Stdlib-only.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def populate_raw_sources(conn: sqlite3.Connection) -> int:
    """Migrate source_snapshots → raw_sources. Returns rows inserted."""
    try:
        conn.execute("SELECT 1 FROM source_snapshots LIMIT 1")
    except sqlite3.OperationalError:
        return 0

    rows = conn.execute(
        """SELECT snapshot_id, hall_id, source_name, source_url,
                  fetched_at, content_sha256, payload_path,
                  parse_status, error_message
           FROM source_snapshots
           ORDER BY snapshot_id"""
    ).fetchall()

    inserted = 0
    for r in rows:
        raw_id = f"snap_{r[0]}"
        source_type = r[2] or "unknown"
        cur = conn.execute(
            """INSERT OR IGNORE INTO raw_sources
               (raw_source_id, source_type, acquisition_method,
                source_locator, fetched_at, content_hash,
                parser_version, raw_path, parse_status, error_message)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                raw_id,
                source_type,
                "automated_public",
                r[3] or "",
                r[4] or "",
                r[5] or "",
                None,
                r[6] or "",
                r[7] or "unknown",
                r[8],
            ),
        )
        if cur.rowcount > 0:
            inserted += 1

    return inserted


def populate_machines(conn: sqlite3.Connection) -> int:
    """Extract machines master from machine_days. Returns rows inserted."""
    try:
        conn.execute("SELECT 1 FROM machine_days LIMIT 1")
    except sqlite3.OperationalError:
        return 0

    existing = {
        r[0] for r in
        conn.execute("SELECT machine_id FROM machines").fetchall()
    }

    rows = conn.execute(
        """SELECT DISTINCT machine_key, machine_name
           FROM machine_days
           WHERE machine_key IS NOT NULL
           ORDER BY machine_key"""
    ).fetchall()

    inserted = 0
    for machine_key, machine_name in rows:
        if machine_key in existing:
            continue

        conn.execute(
            """INSERT OR IGNORE INTO machines
               (machine_id, canonical_name)
               VALUES (?,?)""",
            (machine_key, machine_name or machine_key),
        )
        inserted += 1

    return inserted


def populate_machine_aliases(conn: sqlite3.Connection) -> int:
    """Create machine_aliases from machine_days name variants."""
    try:
        conn.execute("SELECT 1 FROM machine_days LIMIT 1")
    except sqlite3.OperationalError:
        return 0

    rows = conn.execute(
        """SELECT DISTINCT source_name, machine_name, machine_key
           FROM machine_days
           WHERE machine_key IS NOT NULL AND machine_name IS NOT NULL
           ORDER BY source_name, machine_name"""
    ).fetchall()

    inserted = 0
    for source_name, machine_name, machine_key in rows:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO machine_aliases
                   (source_type, source_name, machine_id, valid_from)
                   VALUES (?,?,?,?)""",
                (source_name or "unknown", machine_name, machine_key, ""),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            pass

    return inserted


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Normalize sources and machines"
    )
    ap.add_argument("--db", required=True, help="Path to slot_atlas.db")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"error: {db} not found", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db))

    n_sources = populate_raw_sources(conn)
    n_machines = populate_machines(conn)
    n_aliases = populate_machine_aliases(conn)

    conn.commit()
    conn.close()

    print(f"raw_sources: {n_sources} inserted")
    print(f"machines: {n_machines} inserted")
    print(f"machine_aliases: {n_aliases} inserted")


if __name__ == "__main__":
    main()
