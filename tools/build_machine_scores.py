#!/usr/bin/env python3
"""Machine scoring and Top5 ranking per the v1.2 design spec.

Computes the 7-feature vector, sigmoid score, and Top5 per
(hall_id, target_date, event_family_id).

Stdlib-only.
"""
from __future__ import annotations

import json
import math
import sqlite3
import statistics
from collections import defaultdict

MACHINE_TOP_N = 5
MACHINE_PUBLISH_DAYS_MAX = 21
CONFIDENCE_SAMPLE_K = 8
FRESHNESS_TAU_DAYS = 60
MIN_EVENTS_REFERENCE = 6

W_P_EVENT = 1.20
W_ROTATION = 0.55
W_SIZE_FIT = 0.45
W_WEEKDAY_FIT = 0.35
W_RECENT_DEMAND = 0.30
W_CHAIN_SIGNAL = 0.25
W_LAST_SELECTED_PENALTY = -0.60


def _sigmoid(x: float) -> float:
    x = max(-30.0, min(30.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p: float) -> float:
    p = max(0.001, min(0.999, p))
    return math.log(p / (1.0 - p))


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _percentile_rank(value: float, values: list[float]) -> float:
    """Return percentile rank of value within values (0.0 to 1.0)."""
    if not values:
        return 0.5
    below = sum(1 for v in values if v < value)
    return below / len(values)


def compute_machine_features(
    conn: sqlite3.Connection,
    hall_id: str,
    machine_key: str,
    event_family_id: str | None,
    target_date: str,
    cutoff_date: str,
) -> dict:
    """Compute the 7-feature vector for a machine."""

    if event_family_id:
        same_fam_rows = conn.execute(
            """SELECT md.result_date, md.avg_diff, md.avg_games,
                      md.event_selected_label, md.units
               FROM machine_days md
               JOIN hall_days hd ON md.hall_id = hd.hall_id
                    AND md.result_date = hd.result_date
               WHERE md.hall_id = ? AND md.machine_key = ?
                 AND hd.event_family_id = ?
                 AND md.result_date < ?
               ORDER BY md.result_date""",
            (hall_id, machine_key, event_family_id, cutoff_date),
        ).fetchall()
    else:
        same_fam_rows = conn.execute(
            """SELECT md.result_date, md.avg_diff, md.avg_games,
                      md.organic_selected_label, md.units
               FROM machine_days md
               LEFT JOIN hall_days hd ON md.hall_id = hd.hall_id
                    AND md.result_date = hd.result_date
               WHERE md.hall_id = ? AND md.machine_key = ?
                 AND md.result_date < ?
                 AND (hd.event_family_id IS NULL OR hd.event_family_id = '')
               ORDER BY md.result_date""",
            (hall_id, machine_key, cutoff_date),
        ).fetchall()

    # Unknown labels are not negatives.  Only explicit 0/1 rows contribute
    # to hit rates, weekday rates, rotation history, or sample confidence.
    known_rows = [r for r in same_fam_rows if r[3] in (0, 1)]
    eligible = len(known_rows)
    hits = sum(1 for r in known_rows if r[3] == 1)
    p_event = (hits + 1) / (eligible + 4)

    selected_dates = [r[0] for r in known_rows if r[3] == 1]
    if len(selected_dates) >= 2:
        gaps = []
        for i in range(1, len(selected_dates)):
            d1 = _date_to_ordinal(selected_dates[i - 1])
            d2 = _date_to_ordinal(selected_dates[i])
            if d1 is not None and d2 is not None:
                gaps.append(d2 - d1)

        if gaps:
            median_gap = statistics.median(gaps)
            mad = statistics.median(
                [abs(g - median_gap) for g in gaps]
            ) or 1.0
            last_selected = selected_dates[-1]
            target_ord = _date_to_ordinal(target_date)
            last_ord = _date_to_ordinal(last_selected)
            if target_ord and last_ord:
                days_since = target_ord - last_ord
                rotation = _clip(
                    (days_since - median_gap) / max(mad, 1.0), -2, 2
                )
            else:
                rotation = 0.0
        else:
            rotation = 0.0
    else:
        rotation = 0.0

    was_selected_last = 0
    if known_rows:
        last_label = known_rows[-1][3]
        was_selected_last = 1 if last_label == 1 else 0

    units_list = [r[4] for r in same_fam_rows if r[4] is not None]
    if units_list:
        typical_units = statistics.median(units_list)
        if typical_units <= 6:
            size_fit = 0.5
        elif typical_units <= 12:
            size_fit = 0.0
        else:
            size_fit = -0.3
    else:
        size_fit = 0.0

    from datetime import date as dt_date
    try:
        td = dt_date.fromisoformat(target_date)
        wd = td.weekday()
    except (ValueError, AttributeError):
        wd = None

    if wd is not None and eligible >= MIN_EVENTS_REFERENCE:
        wd_matches = [r for r in known_rows
                       if _weekday_of(r[0]) == wd]
        wd_hits = sum(1 for r in wd_matches if r[3] == 1)
        wd_total = len(wd_matches)
        if wd_total >= 2:
            wd_rate = (wd_hits + 0.5) / (wd_total + 1)
            overall_rate = p_event
            weekday_fit = _clip(_logit(wd_rate) - _logit(overall_rate), -2, 2)
        else:
            weekday_fit = 0.0
    else:
        weekday_fit = 0.0

    avg_games_list = [r[2] for r in same_fam_rows
                       if r[2] is not None]
    if avg_games_list:
        all_games = conn.execute(
            """SELECT avg_games FROM machine_days
               WHERE hall_id = ? AND result_date < ?
                 AND avg_games IS NOT NULL""",
            (hall_id, cutoff_date),
        ).fetchall()
        all_g = [r[0] for r in all_games]
        latest_games = avg_games_list[-1]
        recent_demand = _clip(
            _percentile_rank(latest_games, all_g) * 2 - 1, -2, 2
        )
    else:
        recent_demand = 0.0

    try:
        from chain_detector import compute_chain_signal
        chain_signal = compute_chain_signal(
            conn, hall_id, machine_key, cutoff_date,
        )
    except (ImportError, sqlite3.OperationalError):
        chain_signal = 0.0

    return {
        "p_event": p_event,
        "rotation": rotation,
        "last_selected_penalty": was_selected_last,
        "size_fit": size_fit,
        "weekday_fit": weekday_fit,
        "recent_demand": recent_demand,
        "chain_signal": chain_signal,
        "eligible_days": eligible,
        "hit_days": hits,
    }


def compute_machine_score(features: dict) -> float:
    """Apply the v1.2 sigmoid scoring formula. Returns 0-100."""
    L = (
        W_P_EVENT * _logit(features["p_event"])
        + W_ROTATION * features["rotation"]
        + W_SIZE_FIT * features["size_fit"]
        + W_WEEKDAY_FIT * features["weekday_fit"]
        + W_RECENT_DEMAND * features["recent_demand"]
        + W_CHAIN_SIGNAL * features["chain_signal"]
        + W_LAST_SELECTED_PENALTY * features["last_selected_penalty"]
    )
    return round(100.0 * _sigmoid(L), 1)


def compute_confidence(
    n_eff: int,
    coverage: float | None,
    data_age_days: int,
    backtest_lift: float = 1.0,
) -> float:
    """Compute confidence score per v1.2 spec."""
    sample_factor = math.sqrt(n_eff / (n_eff + CONFIDENCE_SAMPLE_K))
    coverage_factor = min(1.0, coverage) if coverage is not None else 0.5
    freshness_factor = math.exp(-data_age_days / FRESHNESS_TAU_DAYS)
    validation_factor = _clip(backtest_lift / 1.5, 0.5, 1.0)
    return round(
        sample_factor * coverage_factor * freshness_factor * validation_factor,
        4,
    )


def build_machine_predictions(
    conn: sqlite3.Connection,
    hall_id: str,
    target_dates: list[str],
    cutoff_date: str,
    capabilities: dict,
) -> list[dict]:
    """Build machine predictions for a hall across target dates."""
    if not capabilities.get("machine_daily_available"):
        preds = []
        for td in target_dates:
            preds.append({
                "target_date": td,
                "hall_id": hall_id,
                "entity_type": "machine_event",
                "entity_id": "_no_data",
                "score": None,
                "rank": None,
                "confidence": None,
                "explanation": ["機種データなし"],
                "warnings": ["machine_daily_available=false"],
                "capabilities": capabilities,
            })
        return preds

    machine_keys = [
        r[0] for r in conn.execute(
            """SELECT DISTINCT machine_key FROM machine_days
               WHERE hall_id = ? AND result_date < ?""",
            (hall_id, cutoff_date),
        ).fetchall()
    ]

    if not machine_keys:
        return []

    preds = []
    for target_date in target_dates:
        if not _within_publish_horizon(target_date, cutoff_date):
            continue

        event_fam = _get_target_event_family(conn, hall_id, target_date)

        scored: list[tuple[str, float, dict, float | None]] = []
        for mk in machine_keys:
            features = compute_machine_features(
                conn, hall_id, mk, event_fam, target_date, cutoff_date
            )
            if features["eligible_days"] < 1:
                continue

            score = compute_machine_score(features)

            last_date_row = conn.execute(
                """SELECT MAX(result_date) FROM machine_days
                   WHERE hall_id = ? AND machine_key = ?
                     AND result_date < ?""",
                (hall_id, mk, cutoff_date),
            ).fetchone()
            last_date = last_date_row[0] if last_date_row else None
            age = (
                _days_between(last_date, cutoff_date) if last_date else 30
            )

            cov_row = conn.execute(
                """SELECT AVG(coverage) FROM machine_days
                   WHERE hall_id = ? AND machine_key = ?
                     AND result_date < ? AND coverage IS NOT NULL""",
                (hall_id, mk, cutoff_date),
            ).fetchone()
            avg_cov = cov_row[0] if cov_row and cov_row[0] else None

            conf = compute_confidence(
                features["eligible_days"], avg_cov, age
            )

            scored.append((mk, score, features, conf))

        scored.sort(key=lambda x: x[1], reverse=True)
        top5 = scored[:MACHINE_TOP_N]

        entity_type = (
            "machine_event" if event_fam else "machine_organic"
        )
        has_calibrated = (
            len(scored) >= 30 if event_fam else False
        )

        for rank, (mk, score, features, conf) in enumerate(top5, 1):
            name_row = conn.execute(
                """SELECT machine_name FROM machine_days
                   WHERE hall_id = ? AND machine_key = ?
                   LIMIT 1""",
                (hall_id, mk),
            ).fetchone()
            machine_name = name_row[0] if name_row else mk

            explanation = _build_explanation(features)
            warnings = []
            if not has_calibrated:
                warnings.append("calibrated_probability未算出(サンプル不足)")

            preds.append({
                "target_date": target_date,
                "hall_id": hall_id,
                "entity_type": entity_type,
                "entity_id": mk,
                "score": score,
                "rank": rank,
                "confidence": conf,
                "explanation": explanation,
                "warnings": warnings,
                "capabilities": capabilities,
                "machine_name": machine_name,
            })

    return preds


def _build_explanation(features: dict) -> list[str]:
    parts = []
    if features["p_event"] > 0.3:
        parts.append("同族日選抜率")
    if abs(features["rotation"]) > 0.5:
        parts.append("ローテ適合" if features["rotation"] > 0 else "ローテ非適合")
    if features["size_fit"] > 0.3:
        parts.append("少数台適合")
    if features["weekday_fit"] > 0.3:
        parts.append("曜日適合")
    if features["recent_demand"] > 0.3:
        parts.append("最近需要高")
    if not parts:
        parts.append("ベースライン")
    return parts


def _get_target_event_family(
    conn: sqlite3.Connection,
    hall_id: str,
    target_date: str,
) -> str | None:
    """Find the event_family_id for a target date by matching rules."""
    try:
        from build_event_families import date_matches_rule
    except ImportError:
        return None

    rows = conn.execute(
        """SELECT event_family_id, rule_json, family_type
           FROM event_families
           WHERE hall_id = ?
           ORDER BY event_family_id""",
        (hall_id,),
    ).fetchall()

    best_fam = None
    best_spec = -1
    for fam_id, rule_json_str, ftype in rows:
        if ftype == "通常":
            continue
        try:
            rule = json.loads(rule_json_str)
        except (json.JSONDecodeError, TypeError):
            continue
        if date_matches_rule(target_date, rule):
            spec = len(rule)
            if spec > best_spec:
                best_spec = spec
                best_fam = fam_id

    return best_fam


def _within_publish_horizon(target_date: str, cutoff_date: str) -> bool:
    days = _days_between(cutoff_date, target_date)
    return 0 < days <= MACHINE_PUBLISH_DAYS_MAX


def _days_between(d1: str, d2: str) -> int:
    try:
        from datetime import date as dt_date
        a = dt_date.fromisoformat(d1[:10])
        b = dt_date.fromisoformat(d2[:10])
        return (b - a).days
    except (ValueError, AttributeError):
        return 0


def _date_to_ordinal(d: str) -> int | None:
    try:
        from datetime import date as dt_date
        return dt_date.fromisoformat(d[:10]).toordinal()
    except (ValueError, AttributeError):
        return None


def _weekday_of(d: str) -> int | None:
    try:
        from datetime import date as dt_date
        return dt_date.fromisoformat(d[:10]).weekday()
    except (ValueError, AttributeError):
        return None
