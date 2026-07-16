#!/usr/bin/env python3
"""Compute and persist hall_capabilities from data coverage.

Scans hall_days, machine_days, tail_days, unit_days and other tables
to determine per-hall capability flags. Results are stored in the
hall_capabilities table with as_of timestamp.

Usage:
    python3 tools/build_capabilities.py --db slot_atlas.db

Stdlib-only.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone())


def _count_for_hall(conn: sqlite3.Connection, table: str,
                    hall_id: str) -> int:
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE hall_id = ?",
            (hall_id,),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def compute_capabilities(conn: sqlite3.Connection,
                         as_of: str) -> list[dict]:
    """Compute capability flags for all active halls."""
    halls = conn.execute(
        "SELECT hall_id FROM halls WHERE active = 1 ORDER BY hall_id"
    ).fetchall()

    results = []
    for (hall_id,) in halls:
        hd = _count_for_hall(conn, "hall_days", hall_id)
        md = _count_for_hall(conn, "machine_days", hall_id)
        td = _count_for_hall(conn, "tail_days", hall_id)
        ud = _count_for_hall(conn, "unit_days", hall_id)

        counter = 0
        if _table_exists(conn, "machine_days"):
            try:
                counter = conn.execute(
                    """SELECT COUNT(*) FROM machine_days
                       WHERE hall_id = ? AND avg_games IS NOT NULL""",
                    (hall_id,),
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass

        layout = 0
        if _table_exists(conn, "layouts"):
            layout = _count_for_hall(conn, "layouts", hall_id)

        reset_policy = 0
        try:
            row = conn.execute(
                "SELECT reset_policy FROM halls WHERE hall_id = ?",
                (hall_id,),
            ).fetchone()
            if row and row[0]:
                reset_policy = 1
        except sqlite3.OperationalError:
            pass

        methods = set()
        if _table_exists(conn, "raw_sources"):
            srcs = conn.execute(
                """SELECT DISTINCT rs.acquisition_method
                   FROM raw_sources rs
                   JOIN source_snapshots ss ON rs.raw_source_id = 'snap_' || ss.snapshot_id
                   WHERE ss.hall_id = ?""",
                (hall_id,),
            ).fetchall()
            for (m,) in srcs:
                methods.add(m)
        if not methods and hd > 0:
            methods.add("automated_public")

        warnings = []
        if hd == 0:
            warnings.append("ホール日次データなし")
        if md == 0:
            warnings.append("機種データなし")
        if td == 0:
            warnings.append("末尾データなし")

        results.append({
            "hall_id": hall_id,
            "as_of": as_of,
            "hall_daily_available": int(hd > 0),
            "machine_daily_available": int(md > 0),
            "tail_daily_available": int(td > 0),
            "unit_daily_available": int(ud > 0),
            "counter_metrics_available": int(counter > 0),
            "layout_available": int(layout > 0),
            "reset_policy_available": reset_policy,
            "acquisition_methods_json": json.dumps(
                sorted(methods), ensure_ascii=False
            ),
            "warnings_json": json.dumps(warnings, ensure_ascii=False),
        })

    return results


def persist_capabilities(conn: sqlite3.Connection,
                         caps: list[dict]) -> int:
    """Write capabilities to DB. Returns rows upserted."""
    inserted = 0
    for c in caps:
        conn.execute(
            """INSERT OR REPLACE INTO hall_capabilities
               (hall_id, as_of, hall_daily_available,
                machine_daily_available, tail_daily_available,
                unit_daily_available, counter_metrics_available,
                layout_available, reset_policy_available,
                acquisition_methods_json, warnings_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                c["hall_id"], c["as_of"],
                c["hall_daily_available"],
                c["machine_daily_available"],
                c["tail_daily_available"],
                c["unit_daily_available"],
                c["counter_metrics_available"],
                c["layout_available"],
                c["reset_policy_available"],
                c["acquisition_methods_json"],
                c["warnings_json"],
            ),
        )
        inserted += 1
    conn.commit()
    return inserted


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Compute hall capabilities from data coverage"
    )
    ap.add_argument("--db", required=True, help="Path to slot_atlas.db")
    ap.add_argument("--as-of", help="Capability snapshot date (ISO 8601)")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"error: {db} not found", file=sys.stderr)
        sys.exit(1)

    as_of = args.as_of or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )

    conn = sqlite3.connect(str(db))
    caps = compute_capabilities(conn, as_of)
    n = persist_capabilities(conn, caps)
    conn.close()

    full = sum(1 for c in caps if c["hall_daily_available"]
               and c["machine_daily_available"])
    summary = sum(1 for c in caps if c["hall_daily_available"]
                  and not c["machine_daily_available"])
    none_ = sum(1 for c in caps if not c["hall_daily_available"])

    print(f"hall_capabilities: {n} halls computed")
    print(f"  FULL (hall+machine): {full}")
    print(f"  SUMMARY (hall only): {summary}")
    print(f"  NONE: {none_}")


if __name__ == "__main__":
    main()
