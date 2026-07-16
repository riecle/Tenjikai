#!/usr/bin/env python3
"""Unit layer stub interfaces per v1.2 design spec.

Phase 1.75: schema and interfaces only. No Q_unit computation
is performed because unit_days has 0 rows across all 66 halls.

Activation requires free public unit daily data.

Security: unit_distribution_policy = local_only
  - unit_no, candidate_band, entry_no, Qhat_unit must NEVER
    appear in vault payloads.

Stdlib-only.
"""
from __future__ import annotations

import sqlite3

UNIT_DISTRIBUTION_POLICY = "local_only"

VAULT_FORBIDDEN_FIELDS = frozenset([
    "unit_no",
    "candidate_band",
    "entry_no",
    "Qhat_unit",
    "q_unit_observed",
])

Q_UNIT_FORBIDDEN_COLUMNS = frozenset([
    "position",
    "tail",
    "previous_high",
    "slump",
    "reset",
    "layout",
    "adjacency",
    "machine_score_today",
])


def build_unit_predictions(
    conn: sqlite3.Connection,
    hall_id: str,
    target_dates: list[str],
    cutoff_date: str,
    capabilities: dict,
) -> list[dict]:
    """Build unit predictions for a hall.

    Returns empty list or no-data marker when unit_daily_available=false.
    When data becomes available, this function will compute Q_unit,
    high_proxy, and placement patterns.
    """
    if not capabilities.get("unit_daily_available"):
        return []

    unit_count = 0
    try:
        row = conn.execute(
            """SELECT COUNT(*) FROM unit_days
               WHERE hall_id = ? AND result_date < ?""",
            (hall_id, cutoff_date),
        ).fetchone()
        unit_count = row[0] if row else 0
    except sqlite3.OperationalError:
        pass

    if unit_count == 0:
        return []

    return []


def filter_vault_payload(predictions: list[dict]) -> list[dict]:
    """Remove unit-level fields from predictions before vault upload.

    Enforces unit_distribution_policy = local_only.
    """
    if UNIT_DISTRIBUTION_POLICY != "local_only":
        return predictions

    filtered = []
    for p in predictions:
        if p.get("entity_type") in ("unit_local", "placement_pattern"):
            continue

        clean = {}
        for k, v in p.items():
            if k in VAULT_FORBIDDEN_FIELDS:
                continue
            clean[k] = v
        filtered.append(clean)

    return filtered


def check_vault_safety(payload: dict) -> list[str]:
    """Verify no unit-level data leaks into vault payload.

    Returns list of violations (empty = safe).
    """
    import json

    violations = []
    payload_str = json.dumps(payload, ensure_ascii=False)

    for field in VAULT_FORBIDDEN_FIELDS:
        if f'"{field}"' in payload_str:
            violations.append(f"forbidden field in vault: {field}")

    preds = payload.get("predictions", [])
    for p in preds:
        if p.get("entity_type") in ("unit_local", "placement_pattern"):
            violations.append(
                f"unit entity_type in vault: {p['entity_type']}"
            )

    return violations
