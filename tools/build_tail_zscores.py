#!/usr/bin/env python3
"""Tail shrinkage z-score computation per v1.2 design spec.

Computes residual, z_raw, shrinkage, z_shrunk for each tail_key
within a given event_family_id.

Stdlib-only.
"""
from __future__ import annotations

import math
import sqlite3
import statistics

SHRINKAGE_K = 8
Z_CLIP_LO = -4.0
Z_CLIP_HI = 4.0
SE_EPSILON = 0.01
Z_STRONG_THRESHOLD = 2.0
Z_WATCH_THRESHOLD = 1.0


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _days_between(d1: str, d2: str) -> int:
    try:
        from datetime import date as dt_date
        a = dt_date.fromisoformat(d1[:10])
        b = dt_date.fromisoformat(d2[:10])
        return (b - a).days
    except (ValueError, AttributeError):
        return 0


def compute_tail_residuals(
    conn: sqlite3.Connection,
    hall_id: str,
    event_family_id: str | None,
    cutoff_date: str,
) -> dict[str, list[float]]:
    """Compute residuals per tail_key: tail_avg_diff - hall_avg_diff.

    Only uses dates belonging to the given event_family_id (same-family rule).
    Returns {tail_key: [residual_day1, residual_day2, ...]}.
    """
    if event_family_id:
        family_dates = conn.execute(
            """SELECT DISTINCT hd.result_date
               FROM hall_days hd
               WHERE hd.hall_id = ? AND hd.event_family_id = ?
                 AND hd.result_date < ?
               ORDER BY hd.result_date""",
            (hall_id, event_family_id, cutoff_date),
        ).fetchall()
        date_set = {r[0] for r in family_dates}
    else:
        date_set = None

    hall_avg_by_date: dict[str, float] = {}
    rows = conn.execute(
        """SELECT result_date, avg_diff FROM hall_days
           WHERE hall_id = ? AND result_date < ?
             AND avg_diff IS NOT NULL""",
        (hall_id, cutoff_date),
    ).fetchall()
    for rd, ad in rows:
        if date_set is not None and rd not in date_set:
            continue
        hall_avg_by_date[rd] = ad

    if not hall_avg_by_date:
        return {}

    tail_rows = conn.execute(
        """SELECT result_date, tail_key, avg_diff FROM tail_days
           WHERE hall_id = ? AND result_date < ?
             AND avg_diff IS NOT NULL
           ORDER BY tail_key, result_date""",
        (hall_id, cutoff_date),
    ).fetchall()

    residuals: dict[str, list[float]] = {}
    for rd, tk, tail_diff in tail_rows:
        if rd not in hall_avg_by_date:
            continue
        r = tail_diff - hall_avg_by_date[rd]
        residuals.setdefault(tk, []).append(r)

    return residuals


def compute_tail_zscores(
    residuals: dict[str, list[float]],
) -> dict[str, dict]:
    """Compute shrinkage z-scores from residuals.

    Returns {tail_key: {z_raw, z_shrunk, n_eff, shrink, grade}}.
    """
    results: dict[str, dict] = {}
    for tk, res_list in residuals.items():
        n_eff = len(res_list)
        if n_eff < 2:
            results[tk] = {
                "z_raw": 0.0,
                "z_shrunk": 0.0,
                "n_eff": n_eff,
                "shrink": n_eff / (n_eff + SHRINKAGE_K),
                "grade": "unknown",
                "mean_residual": sum(res_list) / n_eff if n_eff else 0.0,
            }
            continue

        mean_r = statistics.mean(res_list)
        se = statistics.stdev(res_list) / math.sqrt(n_eff)
        se = max(se, SE_EPSILON)

        z_raw = mean_r / se
        z_clipped = _clip(z_raw, Z_CLIP_LO, Z_CLIP_HI)

        shrink = n_eff / (n_eff + SHRINKAGE_K)
        z_shrunk = shrink * z_clipped

        if z_shrunk >= Z_STRONG_THRESHOLD:
            grade = "strong"
        elif z_shrunk >= Z_WATCH_THRESHOLD:
            grade = "watch"
        else:
            grade = "unknown"

        results[tk] = {
            "z_raw": round(z_raw, 4),
            "z_shrunk": round(z_shrunk, 4),
            "n_eff": n_eff,
            "shrink": round(shrink, 4),
            "grade": grade,
            "mean_residual": round(mean_r, 2),
        }

    return results


def is_date_pun(target_date: str, tail_key: str) -> bool:
    """Check if the tail_key digit matches the target date's last digit."""
    try:
        day = int(target_date[-2:])
        last_digit = str(day % 10)
        return tail_key == last_digit
    except (ValueError, IndexError):
        return False


def build_tail_predictions(
    conn: sqlite3.Connection,
    hall_id: str,
    target_dates: list[str],
    cutoff_date: str,
    capabilities: dict,
) -> list[dict]:
    """Build tail z-score predictions for a hall across target dates."""
    if not capabilities.get("tail_daily_available"):
        preds = []
        for td in target_dates:
            preds.append({
                "target_date": td,
                "hall_id": hall_id,
                "entity_type": "tail",
                "entity_id": "_no_data",
                "score": None,
                "rank": None,
                "confidence": None,
                "explanation": ["末尾データなし"],
                "warnings": ["tail_daily_available=false"],
                "capabilities": capabilities,
            })
        return preds

    preds = []
    for target_date in target_dates:
        days_ahead = _days_between(cutoff_date, target_date)
        if days_ahead <= 0 or days_ahead > 21:
            continue

        event_fam = _get_target_event_family(conn, hall_id, target_date)

        residuals = compute_tail_residuals(
            conn, hall_id, event_fam, cutoff_date
        )
        zscores = compute_tail_zscores(residuals)

        if not zscores:
            preds.append({
                "target_date": target_date,
                "hall_id": hall_id,
                "entity_type": "tail",
                "entity_id": "_no_data",
                "score": None,
                "rank": None,
                "confidence": None,
                "explanation": ["末尾データなし"],
                "warnings": ["tail_daily_available=true but no tail data"],
                "capabilities": capabilities,
            })
            continue

        sorted_tails = sorted(
            zscores.items(),
            key=lambda x: x[1]["z_shrunk"],
            reverse=True,
        )

        for rank, (tk, info) in enumerate(sorted_tails, 1):
            warnings = []

            if info["grade"] == "strong" and is_date_pun(target_date, tk):
                info["grade"] = "watch"
                warnings.append("date-pun降格: 日付末尾一致は根拠不十分")

            if info["n_eff"] < 10:
                warnings.append("無料検定なし")

            score = round(50.0 + info["z_shrunk"] * 10, 1)
            score = max(0.0, min(100.0, score))

            conf = _tail_confidence(info["n_eff"], days_ahead)

            explanation = _tail_explanation(info)

            preds.append({
                "target_date": target_date,
                "hall_id": hall_id,
                "entity_type": "tail",
                "entity_id": tk,
                "score": score,
                "rank": rank,
                "confidence": conf,
                "z_shrunk": info["z_shrunk"],
                "grade": info["grade"],
                "n_eff": info["n_eff"],
                "explanation": explanation,
                "warnings": warnings,
                "capabilities": capabilities,
            })

    return preds


def _tail_confidence(n_eff: int, data_age_days: int) -> float:
    sample_factor = math.sqrt(n_eff / (n_eff + SHRINKAGE_K))
    freshness_factor = math.exp(-data_age_days / 60.0)
    return round(sample_factor * freshness_factor, 4)


def _tail_explanation(info: dict) -> list[str]:
    parts = []
    if info["grade"] == "strong":
        parts.append("強シグナル")
    elif info["grade"] == "watch":
        parts.append("注目シグナル")
    if info["n_eff"] >= 20:
        parts.append(f"n={info['n_eff']}")
    if info["mean_residual"] > 100:
        parts.append("残差+")
    if not parts:
        parts.append("ベースライン")
    return parts


def _get_target_event_family(
    conn: sqlite3.Connection,
    hall_id: str,
    target_date: str,
) -> str | None:
    import json
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
