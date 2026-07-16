#!/usr/bin/env python3
"""Compute machine labels: event_selected_label, organic_active_day,
organic_selected_label.

Reads machine_days and event_families, computes Q_machine, coverage,
positive_rate, and assigns labels per the v1.2 design spec.

Usage:
    python3 tools/build_machine_labels.py --db slot_atlas.db

Stdlib-only.
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from pathlib import Path

ORGANIC_AVG_DIFF_MIN = 800
ORGANIC_POSITIVE_RATE_MIN = 0.70
SELECTED_TOP_QUANTILE = 0.15
Q_MACHINE_ABS_THRESHOLD = 1.0
MIN_UNITS = 2
MIN_COVERAGE = 0.60


def _compute_derived_columns(conn: sqlite3.Connection) -> int:
    """Backfill positive_rate and coverage from existing columns."""
    updated = 0

    cur = conn.execute(
        """UPDATE machine_days
           SET positive_rate = CAST(winning_units AS REAL) / total_units
           WHERE total_units IS NOT NULL AND total_units > 0
             AND positive_rate IS NULL"""
    )
    updated += cur.rowcount

    cur = conn.execute(
        """UPDATE machine_days
           SET coverage = CAST(total_units AS REAL) / units
           WHERE units IS NOT NULL AND units > 0
             AND coverage IS NULL"""
    )
    updated += cur.rowcount

    return updated


def _compute_q_machine(conn: sqlite3.Connection) -> int:
    """Compute Q_machine as a within-day z-score of avg_diff.

    Q_machine = (avg_diff - day_mean) / max(day_std, 1.0)
    This makes Q_machine >= 1.0 mean "one SD above day mean".
    """
    days = conn.execute(
        """SELECT DISTINCT hall_id, result_date
           FROM machine_days
           WHERE avg_diff IS NOT NULL AND q_machine IS NULL"""
    ).fetchall()

    updated = 0
    for hall_id, result_date in days:
        rows = conn.execute(
            """SELECT machine_key, avg_diff FROM machine_days
               WHERE hall_id = ? AND result_date = ?
                 AND avg_diff IS NOT NULL""",
            (hall_id, result_date),
        ).fetchall()

        if len(rows) < 2:
            for mk, ad in rows:
                conn.execute(
                    """UPDATE machine_days SET q_machine = 0.0
                       WHERE hall_id = ? AND result_date = ?
                         AND machine_key = ?""",
                    (hall_id, result_date, mk),
                )
                updated += 1
            continue

        diffs = [r[1] for r in rows]
        mean = sum(diffs) / len(diffs)
        variance = sum((d - mean) ** 2 for d in diffs) / len(diffs)
        std = math.sqrt(variance) if variance > 0 else 1.0
        std = max(std, 1.0)

        for mk, ad in rows:
            q = (ad - mean) / std
            conn.execute(
                """UPDATE machine_days SET q_machine = ?
                   WHERE hall_id = ? AND result_date = ?
                     AND machine_key = ?""",
                (round(q, 4), hall_id, result_date, mk),
            )
            updated += 1

    return updated


def _get_event_family_for_date(
    conn: sqlite3.Connection,
    hall_id: str,
    result_date: str,
) -> str | None:
    """Look up event_family_id from hall_days for the given date."""
    row = conn.execute(
        """SELECT event_family_id FROM hall_days
           WHERE hall_id = ? AND result_date = ?
             AND event_family_id IS NOT NULL
           LIMIT 1""",
        (hall_id, result_date),
    ).fetchone()
    return row[0] if row else None


def _is_event_day(
    conn: sqlite3.Connection,
    hall_id: str,
    result_date: str,
) -> bool:
    """Check if a date is an event day (has a non-通常 family)."""
    fam_id = _get_event_family_for_date(conn, hall_id, result_date)
    if not fam_id:
        return False
    row = conn.execute(
        "SELECT family_type FROM event_families WHERE event_family_id = ?",
        (fam_id,),
    ).fetchone()
    if not row:
        return False
    return row[0] != "通常"


def compute_event_labels(conn: sqlite3.Connection) -> int:
    """Assign event_selected_label on event-family days.

    Criteria (all must be met for label=1):
    - units >= 2
    - coverage >= 0.60
    - Q_machine in top 15% of that day OR Q_machine >= 1.0
    - avg_diff > 0

    Missing data → NULL (unknown), never 0.
    """
    days = conn.execute(
        """SELECT DISTINCT md.hall_id, md.result_date
           FROM machine_days md
           WHERE md.event_selected_label IS NULL"""
    ).fetchall()

    updated = 0
    for hall_id, result_date in days:
        if not _is_event_day(conn, hall_id, result_date):
            continue

        machines = conn.execute(
            """SELECT machine_key, units, coverage, q_machine, avg_diff,
                      total_units
               FROM machine_days
               WHERE hall_id = ? AND result_date = ?""",
            (hall_id, result_date),
        ).fetchall()

        q_values = [
            r[3] for r in machines
            if r[3] is not None
        ]
        if q_values:
            q_values_sorted = sorted(q_values, reverse=True)
            top_15_idx = max(0, int(len(q_values_sorted) * SELECTED_TOP_QUANTILE) - 1)
            q_threshold = q_values_sorted[top_15_idx]
        else:
            q_threshold = Q_MACHINE_ABS_THRESHOLD

        for mk, units, coverage, q, avg_diff, total_units in machines:
            if avg_diff is None or units is None:
                continue

            meets_units = units >= MIN_UNITS
            meets_coverage = (coverage is not None and coverage >= MIN_COVERAGE)
            meets_q = (
                q is not None
                and (q >= q_threshold or q >= Q_MACHINE_ABS_THRESHOLD)
            )
            meets_diff = avg_diff > 0

            label = 1 if (meets_units and meets_coverage
                          and meets_q and meets_diff) else 0

            conn.execute(
                """UPDATE machine_days SET event_selected_label = ?
                   WHERE hall_id = ? AND result_date = ?
                     AND machine_key = ?""",
                (label, hall_id, result_date, mk),
            )
            updated += 1

    return updated


def compute_organic_labels(conn: sqlite3.Connection) -> int:
    """Assign organic_active_day and organic_selected_label.

    organic_active_day = 1 if ANY machine on that (non-event) day meets:
    - avg_diff >= 800
    - positive_rate >= 0.70
    - units >= 2
    - coverage >= 0.60

    organic_selected_label uses same criteria as event_selected_label
    but only on organic_active_day=1 days.
    """
    days = conn.execute(
        """SELECT DISTINCT hall_id, result_date
           FROM machine_days
           WHERE organic_active_day IS NULL"""
    ).fetchall()

    updated = 0
    for hall_id, result_date in days:
        if _is_event_day(conn, hall_id, result_date):
            continue

        machines = conn.execute(
            """SELECT machine_key, units, coverage, q_machine, avg_diff,
                      positive_rate
               FROM machine_days
               WHERE hall_id = ? AND result_date = ?""",
            (hall_id, result_date),
        ).fetchall()

        any_active = False
        for mk, units, coverage, q, avg_diff, pos_rate in machines:
            if (avg_diff is not None and avg_diff >= ORGANIC_AVG_DIFF_MIN
                    and pos_rate is not None
                    and pos_rate >= ORGANIC_POSITIVE_RATE_MIN
                    and units is not None and units >= MIN_UNITS
                    and coverage is not None and coverage >= MIN_COVERAGE):
                any_active = True
                break

        active_val = 1 if any_active else 0

        q_values = [
            r[3] for r in machines
            if r[3] is not None
        ]
        if q_values:
            q_sorted = sorted(q_values, reverse=True)
            top_15_idx = max(0, int(len(q_sorted) * SELECTED_TOP_QUANTILE) - 1)
            q_threshold = q_sorted[top_15_idx]
        else:
            q_threshold = Q_MACHINE_ABS_THRESHOLD

        for mk, units, coverage, q, avg_diff, pos_rate in machines:
            conn.execute(
                """UPDATE machine_days SET organic_active_day = ?
                   WHERE hall_id = ? AND result_date = ?
                     AND machine_key = ?""",
                (active_val, hall_id, result_date, mk),
            )
            updated += 1

            if not any_active or avg_diff is None or units is None:
                continue

            meets_units = units >= MIN_UNITS
            meets_coverage = (coverage is not None and coverage >= MIN_COVERAGE)
            meets_q = (
                q is not None
                and (q >= q_threshold or q >= Q_MACHINE_ABS_THRESHOLD)
            )
            meets_diff = avg_diff > 0

            label = 1 if (meets_units and meets_coverage
                          and meets_q and meets_diff) else 0

            conn.execute(
                """UPDATE machine_days SET organic_selected_label = ?
                   WHERE hall_id = ? AND result_date = ?
                     AND machine_key = ?""",
                (label, hall_id, result_date, mk),
            )

    return updated


def compute_organic_model_gate(
    conn: sqlite3.Connection,
    hall_id: str,
    min_valid_days: int = 20,
    min_activation_rate: float = 0.20,
) -> dict:
    """Check if organic model should be active for a hall.

    Returns dict with gate status and stats.
    """
    normal_days = conn.execute(
        """SELECT COUNT(DISTINCT result_date) FROM machine_days
           WHERE hall_id = ? AND organic_active_day IS NOT NULL""",
        (hall_id,),
    ).fetchone()[0]

    active_days = conn.execute(
        """SELECT COUNT(DISTINCT result_date) FROM machine_days
           WHERE hall_id = ? AND organic_active_day = 1""",
        (hall_id,),
    ).fetchone()[0]

    activation_rate = (
        active_days / normal_days if normal_days > 0 else 0.0
    )

    passes = (
        normal_days >= min_valid_days
        and activation_rate >= min_activation_rate
    )

    return {
        "hall_id": hall_id,
        "valid_normal_days": normal_days,
        "active_days": active_days,
        "activation_rate": round(activation_rate, 4),
        "model_active": passes,
    }


def build_all_labels(conn: sqlite3.Connection) -> dict[str, int]:
    """Run the full label pipeline. Returns counts."""
    derived = _compute_derived_columns(conn)
    q_computed = _compute_q_machine(conn)
    event_labels = compute_event_labels(conn)
    organic_labels = compute_organic_labels(conn)
    conn.commit()

    return {
        "derived_columns": derived,
        "q_machine_computed": q_computed,
        "event_labels": event_labels,
        "organic_labels": organic_labels,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Compute machine labels (event/organic)"
    )
    ap.add_argument("--db", required=True, help="Path to slot_atlas.db")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"error: {db} not found", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db))
    counts = build_all_labels(conn)
    conn.close()

    print("machine labels computed:")
    for k, v in counts.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
