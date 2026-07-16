#!/usr/bin/env python3
"""Chain pattern detection: 4-type series-cross analysis per v1.2 spec.

Detects:
  joint_machine    - co-selection of same machine across chain halls
  machine_split    - complementary machine allocation across chain halls
  date_role_split  - date series divided across chain halls
  intensity_split  - same-day intensity imbalance within chain

Usage:
    python3 tools/chain_detector.py --db slot_atlas.db

Stdlib-only.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

PATTERN_TYPES = [
    "joint_machine",
    "machine_split",
    "date_role_split",
    "intensity_split",
]

MIN_COMMON_DAYS = 8
JOINT_LIFT_THRESHOLD = 2.0
PERMUTATION_P_THRESHOLD = 0.05
PERMUTATION_COUNT = 10000
INTENSITY_CORR_THRESHOLD = -0.30
DATE_ROLE_CONCENTRATION_THRESHOLD = 0.60
DATE_ROLE_EFFECT_THRESHOLD = 0.50
MIN_DATE_ROLE_DAYS_PER_HALL = 3
MIN_DATE_ROLE_FAMILIES = 2

CHAIN_PATTERNS: dict[str, list[str]] = {
    "maruhan": ["maruhan"],
    "espace": ["espace", "エスパス"],
    "rakuen": ["rakuen", "楽園"],
    "bigdipper": ["big_dipper", "bigdipper"],
    "mitoya": ["mitoya", "みとや"],
    "uno": ["_uno", "UNO"],
    "juraku": ["juraku", "ジュラク"],
    "kiccho": ["kiccho", "吉兆"],
    "aviva": ["aviva", "アビバ"],
}


def assign_chain_ids(conn: sqlite3.Connection) -> int:
    """Assign chain_id to halls based on name patterns. Returns count updated."""
    halls = conn.execute(
        "SELECT hall_id, name FROM halls"
    ).fetchall()

    updated = 0
    for hall_id, name in halls:
        chain_id = _detect_chain(hall_id, name or "")
        conn.execute(
            "UPDATE halls SET chain_id = ? WHERE hall_id = ?",
            (chain_id, hall_id),
        )
        if chain_id is not None:
            updated += 1

    conn.commit()
    return updated


def _detect_chain(hall_id: str, name: str) -> str | None:
    """Detect chain from hall_id and name."""
    hid_lower = hall_id.lower()
    name_lower = name.lower() if name else ""

    for chain_id, patterns in CHAIN_PATTERNS.items():
        for pat in patterns:
            if pat.lower() in hid_lower or pat in name_lower:
                return chain_id

    return None


def get_chain_halls(
    conn: sqlite3.Connection,
    chain_id: str,
) -> list[str]:
    """Get all hall_ids belonging to a chain."""
    rows = conn.execute(
        "SELECT hall_id FROM halls WHERE chain_id = ? AND active = 1",
        (chain_id,),
    ).fetchall()
    return [r[0] for r in rows]


def get_active_chains(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Get all chains with 2+ active halls."""
    rows = conn.execute(
        """SELECT chain_id, hall_id FROM halls
           WHERE chain_id IS NOT NULL AND active = 1
           ORDER BY chain_id, hall_id"""
    ).fetchall()

    chains: dict[str, list[str]] = defaultdict(list)
    for chain_id, hall_id in rows:
        chains[chain_id].append(hall_id)

    return {k: v for k, v in chains.items() if len(v) >= 2}


def detect_joint_machine(
    conn: sqlite3.Connection,
    hall_a: str,
    hall_b: str,
    event_family_id: str | None,
    cutoff_date: str,
    rng: random.Random | None = None,
) -> dict | None:
    """Detect joint_machine pattern between two halls.

    Co-selection lift with permutation test.
    Returns result dict or None if insufficient data.
    """
    if event_family_id:
        dates_a = set(r[0] for r in conn.execute(
            """SELECT DISTINCT md.result_date
               FROM machine_days md
               JOIN hall_days hd ON md.hall_id = hd.hall_id
                    AND md.result_date = hd.result_date
               WHERE md.hall_id = ? AND hd.event_family_id = ?
                 AND md.result_date < ?""",
            (hall_a, event_family_id, cutoff_date),
        ).fetchall())
        dates_b = set(r[0] for r in conn.execute(
            """SELECT DISTINCT md.result_date
               FROM machine_days md
               JOIN hall_days hd ON md.hall_id = hd.hall_id
                    AND md.result_date = hd.result_date
               WHERE md.hall_id = ? AND hd.event_family_id = ?
                 AND md.result_date < ?""",
            (hall_b, event_family_id, cutoff_date),
        ).fetchall())
    else:
        dates_a = set(r[0] for r in conn.execute(
            """SELECT DISTINCT result_date FROM machine_days
               WHERE hall_id = ? AND result_date < ?""",
            (hall_a, cutoff_date),
        ).fetchall())
        dates_b = set(r[0] for r in conn.execute(
            """SELECT DISTINCT result_date FROM machine_days
               WHERE hall_id = ? AND result_date < ?""",
            (hall_b, cutoff_date),
        ).fetchall())

    common_dates = sorted(dates_a & dates_b)
    if len(common_dates) < MIN_COMMON_DAYS:
        return None

    selected_a = _get_selected_machines_by_date(
        conn, hall_a, common_dates, event_family_id,
    )
    selected_b = _get_selected_machines_by_date(
        conn, hall_b, common_dates, event_family_id,
    )

    co_selected = 0
    total_a = 0
    total_b = 0
    for d in common_dates:
        sa = selected_a.get(d, set())
        sb = selected_b.get(d, set())
        co_selected += len(sa & sb)
        total_a += len(sa)
        total_b += len(sb)

    n_common = len(common_dates)
    if total_a == 0 or total_b == 0 or n_common == 0:
        return None

    co_select_rate = co_selected / n_common
    select_rate_a = total_a / n_common
    select_rate_b = total_b / n_common
    expected_rate = select_rate_a * select_rate_b

    if expected_rate == 0:
        return None

    lift = co_select_rate / expected_rate

    if rng is None:
        rng = random.Random(42)

    p_value = _permutation_test_joint(
        selected_a, selected_b, common_dates,
        co_selected, rng,
    )

    promoted = (
        lift >= JOINT_LIFT_THRESHOLD
        and p_value < PERMUTATION_P_THRESHOLD
        and n_common >= MIN_COMMON_DAYS
    )

    confidence = _chain_confidence(n_common, p_value, lift)

    explanation = [
        f"lift={lift:.2f}",
        f"co_select={co_selected}/{n_common}",
        f"p={p_value:.4f}",
    ]
    if promoted:
        explanation.insert(0, "promoted")

    warnings = []
    if n_common < 15:
        warnings.append("少標本")

    return {
        "pattern_type": "joint_machine",
        "halls": [hall_a, hall_b],
        "lift": round(lift, 4),
        "p_value": round(p_value, 4),
        "statistic": round(co_select_rate, 4),
        "evidence_days": n_common,
        "confidence": confidence,
        "promoted": promoted,
        "explanation": explanation,
        "warnings": warnings,
    }


def _get_selected_machines_by_date(
    conn: sqlite3.Connection,
    hall_id: str,
    dates: list[str],
    event_family_id: str | None,
) -> dict[str, set[str]]:
    """Get selected machine_keys per date."""
    result: dict[str, set[str]] = {}
    label_col = "event_selected_label" if event_family_id else "organic_selected_label"

    for d in dates:
        rows = conn.execute(
            f"""SELECT machine_key FROM machine_days
                WHERE hall_id = ? AND result_date = ?
                  AND {label_col} = 1""",
            (hall_id, d),
        ).fetchall()
        result[d] = {r[0] for r in rows}

    return result


def _permutation_test_joint(
    selected_a: dict[str, set[str]],
    selected_b: dict[str, set[str]],
    common_dates: list[str],
    observed_co: int,
    rng: random.Random,
) -> float:
    """Date-correspondence shuffle permutation test for joint machine."""
    if observed_co == 0:
        return 1.0

    dates_list = list(common_dates)
    n = len(dates_list)
    count_ge = 0

    for _ in range(PERMUTATION_COUNT):
        shuffled = dates_list[:]
        rng.shuffle(shuffled)

        co = 0
        for i, d in enumerate(dates_list):
            sa = selected_a.get(d, set())
            sb = selected_b.get(shuffled[i], set())
            co += len(sa & sb)

        if co >= observed_co:
            count_ge += 1

    return (count_ge + 1) / (PERMUTATION_COUNT + 1)


def detect_machine_split(
    conn: sqlite3.Connection,
    hall_a: str,
    hall_b: str,
    event_family_id: str | None,
    cutoff_date: str,
) -> dict | None:
    """Detect machine_split: complementary machine allocation.

    Measures whether two halls tend to select DIFFERENT machines
    on the same day (negative overlap vs expected).
    """
    if event_family_id:
        dates_a = set(r[0] for r in conn.execute(
            """SELECT DISTINCT md.result_date
               FROM machine_days md
               JOIN hall_days hd ON md.hall_id = hd.hall_id
                    AND md.result_date = hd.result_date
               WHERE md.hall_id = ? AND hd.event_family_id = ?
                 AND md.result_date < ?""",
            (hall_a, event_family_id, cutoff_date),
        ).fetchall())
        dates_b = set(r[0] for r in conn.execute(
            """SELECT DISTINCT md.result_date
               FROM machine_days md
               JOIN hall_days hd ON md.hall_id = hd.hall_id
                    AND md.result_date = hd.result_date
               WHERE md.hall_id = ? AND hd.event_family_id = ?
                 AND md.result_date < ?""",
            (hall_b, event_family_id, cutoff_date),
        ).fetchall())
    else:
        dates_a = set(r[0] for r in conn.execute(
            """SELECT DISTINCT result_date FROM machine_days
               WHERE hall_id = ? AND result_date < ?""",
            (hall_a, cutoff_date),
        ).fetchall())
        dates_b = set(r[0] for r in conn.execute(
            """SELECT DISTINCT result_date FROM machine_days
               WHERE hall_id = ? AND result_date < ?""",
            (hall_b, cutoff_date),
        ).fetchall())

    common_dates = sorted(dates_a & dates_b)
    if len(common_dates) < MIN_COMMON_DAYS:
        return None

    selected_a = _get_selected_machines_by_date(
        conn, hall_a, common_dates, event_family_id,
    )
    selected_b = _get_selected_machines_by_date(
        conn, hall_b, common_dates, event_family_id,
    )

    overlaps = []
    for d in common_dates:
        sa = selected_a.get(d, set())
        sb = selected_b.get(d, set())
        union = sa | sb
        if not union:
            continue
        overlap_rate = len(sa & sb) / len(union)
        overlaps.append(overlap_rate)

    if len(overlaps) < MIN_COMMON_DAYS:
        return None

    mean_overlap = statistics.mean(overlaps)

    all_machines_a = set()
    all_machines_b = set()
    for d in common_dates:
        all_machines_a |= selected_a.get(d, set())
        all_machines_b |= selected_b.get(d, set())

    all_machines = all_machines_a | all_machines_b
    if not all_machines:
        return None

    base_overlap = len(all_machines_a & all_machines_b) / len(all_machines)

    split_score = base_overlap - mean_overlap
    promoted = split_score > 0.15 and len(common_dates) >= MIN_COMMON_DAYS

    confidence = _chain_confidence(
        len(common_dates), 0.05 if promoted else 0.5, 1.0 + split_score,
    )

    explanation = [
        f"mean_overlap={mean_overlap:.3f}",
        f"base_overlap={base_overlap:.3f}",
        f"split_score={split_score:.3f}",
    ]
    if promoted:
        explanation.insert(0, "promoted")

    warnings = []
    if len(common_dates) < 15:
        warnings.append("少標本")

    return {
        "pattern_type": "machine_split",
        "halls": [hall_a, hall_b],
        "lift": round(1.0 + split_score, 4),
        "p_value": None,
        "statistic": round(split_score, 4),
        "evidence_days": len(common_dates),
        "confidence": confidence,
        "promoted": promoted,
        "explanation": explanation,
        "warnings": warnings,
    }


def detect_date_role_split(
    conn: sqlite3.Connection,
    chain_id: str,
    chain_halls: list[str],
    cutoff_date: str,
) -> dict | None:
    """Detect date_role_split from *strength*, not event-registration counts.

    For each canonical event family shared by at least two halls, daily hall
    intensity is standardized against that hall's own historical distribution.
    A family contributes evidence only when every compared hall has enough
    observed days.  The detector then asks whether different canonical
    families have reproducibly different winning halls.

    This prevents the structural false positive where hall-specific
    event_family_id values (or different registered rule counts) mechanically
    produce concentration=1.0.
    """
    eligible_halls = sorted(set(chain_halls))
    if len(eligible_halls) < 2:
        return None

    # Hall-specific baseline: removes the chain hall's general strength level.
    hall_baselines: dict[str, tuple[float, float]] = {}
    for hall_id in eligible_halls:
        vals = [
            float(r[0]) for r in conn.execute(
                """SELECT avg_diff FROM hall_days
                   WHERE hall_id = ? AND result_date < ?
                     AND avg_diff IS NOT NULL
                   ORDER BY result_date""",
                (hall_id, cutoff_date),
            ).fetchall()
        ]
        if len(vals) < MIN_DATE_ROLE_DAYS_PER_HALL:
            continue
        mean_v = statistics.mean(vals)
        stdev_v = statistics.stdev(vals) if len(vals) >= 2 else 0.0
        if stdev_v <= 0:
            continue
        hall_baselines[hall_id] = (mean_v, stdev_v)

    if len(hall_baselines) < 2:
        return None

    # canonical_key -> hall -> daily normalized intensities.  Duplicate source
    # rows for the same hall/date/family are averaged before entering evidence.
    observations: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    placeholders = ",".join("?" for _ in hall_baselines)
    rows = conn.execute(
        f"""SELECT hd.hall_id, hd.result_date, ef.canonical_family_key,
                   AVG(hd.avg_diff) AS day_intensity
            FROM hall_days hd
            JOIN event_families ef
              ON hd.event_family_id = ef.event_family_id
            WHERE hd.hall_id IN ({placeholders})
              AND hd.result_date < ?
              AND hd.avg_diff IS NOT NULL
              AND ef.family_type != '通常'
              AND ef.canonical_family_key IS NOT NULL
              AND ef.canonical_family_key NOT IN ('', 'normal', 'other')
            GROUP BY hd.hall_id, hd.result_date, ef.canonical_family_key
            ORDER BY ef.canonical_family_key, hd.hall_id, hd.result_date""",
        (*hall_baselines.keys(), cutoff_date),
    ).fetchall()

    for hall_id, _date, canon_key, day_intensity in rows:
        baseline = hall_baselines.get(hall_id)
        if baseline is None or day_intensity is None:
            continue
        mean_v, stdev_v = baseline
        observations[canon_key][hall_id].append(
            (float(day_intensity) - mean_v) / stdev_v
        )

    family_results: list[dict] = []
    for canon_key, hall_values in sorted(observations.items()):
        compared = {
            hall_id: vals
            for hall_id, vals in hall_values.items()
            if len(vals) >= MIN_DATE_ROLE_DAYS_PER_HALL
        }
        if len(compared) < 2:
            continue

        mean_scores = {
            hall_id: statistics.mean(vals)
            for hall_id, vals in compared.items()
        }
        ranked = sorted(mean_scores.items(), key=lambda item: item[1], reverse=True)
        top_hall, top_score = ranked[0]
        second_score = ranked[1][1]
        effect = top_score - second_score

        # Softmax converts standardized strength into a bounded concentration.
        max_score = max(mean_scores.values())
        exp_scores = {
            h: math.exp(max(-20.0, min(20.0, score - max_score)))
            for h, score in mean_scores.items()
        }
        denom = sum(exp_scores.values())
        concentration = exp_scores[top_hall] / denom if denom else 0.0

        family_results.append({
            "canonical_family_key": canon_key,
            "winner": top_hall,
            "concentration": concentration,
            "effect": effect,
            "evidence_days": sum(len(v) for v in compared.values()),
            "hall_scores": mean_scores,
            "qualified": (
                concentration >= DATE_ROLE_CONCENTRATION_THRESHOLD
                and effect >= DATE_ROLE_EFFECT_THRESHOLD
            ),
        })

    qualified = [f for f in family_results if f["qualified"]]
    if len(family_results) < MIN_DATE_ROLE_FAMILIES:
        return None

    distinct_winners = sorted({f["winner"] for f in qualified})
    mean_concentration = (
        statistics.mean(f["concentration"] for f in qualified)
        if qualified else 0.0
    )
    mean_effect = (
        statistics.mean(f["effect"] for f in qualified)
        if qualified else 0.0
    )
    evidence_days = sum(f["evidence_days"] for f in family_results)

    promoted = (
        len(qualified) >= MIN_DATE_ROLE_FAMILIES
        and len(distinct_winners) >= 2
        and mean_concentration >= DATE_ROLE_CONCENTRATION_THRESHOLD
        and mean_effect >= DATE_ROLE_EFFECT_THRESHOLD
        and evidence_days >= MIN_COMMON_DAYS
    )

    lift = (
        mean_concentration / (1.0 / len(hall_baselines))
        if hall_baselines else None
    )
    confidence = _chain_confidence(
        evidence_days,
        0.05 if promoted else 0.5,
        lift if lift is not None else 1.0,
    )

    winner_summary = ", ".join(
        f"{f['canonical_family_key']}→{f['winner']}"
        for f in qualified[:6]
    )
    explanation = [
        f"strength_based=true",
        f"qualified_families={len(qualified)}/{len(family_results)}",
        f"distinct_winners={len(distinct_winners)}",
        f"mean_concentration={mean_concentration:.3f}",
        f"mean_effect={mean_effect:.3f}",
    ]
    if winner_summary:
        explanation.append(winner_summary)
    if promoted:
        explanation.insert(0, "promoted")

    warnings = []
    if len(family_results) < 3:
        warnings.append("少標本")
    unqualified = len(family_results) - len(qualified)
    if unqualified:
        warnings.append(f"強度差未達family={unqualified}")

    return {
        "pattern_type": "date_role_split",
        "halls": sorted(hall_baselines),
        "lift": round(lift, 4) if lift is not None else None,
        "p_value": None,
        "statistic": round(mean_concentration, 4),
        "evidence_days": evidence_days,
        "confidence": confidence,
        "promoted": promoted,
        "explanation": explanation,
        "warnings": warnings,
        "family_results": family_results,
    }

def detect_intensity_split(
    conn: sqlite3.Connection,
    hall_a: str,
    hall_b: str,
    cutoff_date: str,
) -> dict | None:
    """Detect intensity_split: same-day intensity imbalance.

    Standardize each hall's daily avg_diff and check for negative correlation.
    """
    rows_a = conn.execute(
        """SELECT result_date, avg_diff FROM hall_days
           WHERE hall_id = ? AND result_date < ? AND avg_diff IS NOT NULL
           ORDER BY result_date""",
        (hall_a, cutoff_date),
    ).fetchall()
    rows_b = conn.execute(
        """SELECT result_date, avg_diff FROM hall_days
           WHERE hall_id = ? AND result_date < ? AND avg_diff IS NOT NULL
           ORDER BY result_date""",
        (hall_b, cutoff_date),
    ).fetchall()

    diffs_a = {r[0]: r[1] for r in rows_a}
    diffs_b = {r[0]: r[1] for r in rows_b}

    common_dates = sorted(set(diffs_a.keys()) & set(diffs_b.keys()))
    if len(common_dates) < MIN_COMMON_DAYS:
        return None

    vals_a = [diffs_a[d] for d in common_dates]
    vals_b = [diffs_b[d] for d in common_dates]

    z_a = _standardize(vals_a)
    z_b = _standardize(vals_b)

    if z_a is None or z_b is None:
        return None

    corr = _pearson_correlation(z_a, z_b)
    if corr is None:
        return None

    winner_concentration = _compute_winner_concentration(z_a, z_b)

    promoted = (
        corr < INTENSITY_CORR_THRESHOLD
        and len(common_dates) >= MIN_COMMON_DAYS
    )

    confidence = _chain_confidence(
        len(common_dates),
        0.05 if promoted else 0.5,
        abs(corr) / abs(INTENSITY_CORR_THRESHOLD) if promoted else 0.5,
    )

    explanation = [
        f"corr={corr:.3f}",
        f"winner_concentration={winner_concentration:.3f}",
        f"n={len(common_dates)}",
    ]
    if promoted:
        explanation.insert(0, "promoted")

    warnings = []
    if len(common_dates) < 20:
        warnings.append("少標本")

    return {
        "pattern_type": "intensity_split",
        "halls": [hall_a, hall_b],
        "lift": round(abs(corr), 4),
        "p_value": None,
        "statistic": round(corr, 4),
        "evidence_days": len(common_dates),
        "confidence": confidence,
        "promoted": promoted,
        "explanation": explanation,
        "warnings": warnings,
    }


def _standardize(values: list[float]) -> list[float] | None:
    """Standardize values to z-scores."""
    if len(values) < 2:
        return None
    m = statistics.mean(values)
    s = statistics.stdev(values)
    if s == 0:
        return None
    return [(v - m) / s for v in values]


def _pearson_correlation(x: list[float], y: list[float]) -> float | None:
    """Compute Pearson correlation coefficient."""
    n = len(x)
    if n < 3:
        return None

    sum_xy = sum(a * b for a, b in zip(x, y))
    return sum_xy / (n - 1)


def _compute_winner_concentration(z_a: list[float], z_b: list[float]) -> float:
    """Fraction of days where one hall has z > 0 and the other z < 0."""
    opposite = sum(
        1 for a, b in zip(z_a, z_b) if (a > 0) != (b > 0)
    )
    return opposite / len(z_a) if z_a else 0.0


def _chain_confidence(
    n_evidence: int,
    p_value: float | None,
    lift: float,
) -> float:
    """Compute chain pattern confidence."""
    sample_factor = math.sqrt(n_evidence / (n_evidence + 8))

    if p_value is not None and p_value < 0.05:
        p_factor = 1.0
    elif p_value is not None:
        p_factor = max(0.3, 1.0 - p_value)
    else:
        p_factor = 0.5

    lift_factor = min(1.0, lift / 3.0)

    return round(sample_factor * p_factor * lift_factor, 4)


def compute_chain_signal(
    conn: sqlite3.Connection,
    hall_id: str,
    machine_key: str,
    cutoff_date: str,
) -> float:
    """Compute chain_signal feature for a machine.

    Only uses patterns confirmed before cutoff_date (C-04 anti-circularity).
    Returns value clipped to [-2, 2].
    """
    chain_row = conn.execute(
        "SELECT chain_id FROM halls WHERE hall_id = ?",
        (hall_id,),
    ).fetchone()
    if not chain_row or not chain_row[0]:
        return 0.0

    chain_id = chain_row[0]

    try:
        results = conn.execute(
            """SELECT pattern_type, lift, p_value, confidence
               FROM chain_pattern_results_v2
               WHERE chain_id = ?
                 AND valid_from < ?
                 AND promoted = 1
                 AND confidence > 0""",
            (chain_id, cutoff_date),
        ).fetchall()
    except sqlite3.OperationalError:
        try:
            results = conn.execute(
                """SELECT pattern_type, lift, p_value, confidence
                   FROM chain_pattern_results
                   WHERE chain_id = ?
                     AND valid_from < ?
                     AND confidence > 0""",
                (chain_id, cutoff_date),
            ).fetchall()
        except sqlite3.OperationalError:
            return 0.0

    if not results:
        return 0.0

    signal = 0.0
    for ptype, lift, p_val, conf in results:
        if ptype == "joint_machine" and lift and lift >= JOINT_LIFT_THRESHOLD:
            if p_val is not None and p_val < PERMUTATION_P_THRESHOLD:
                signal += 0.5 * conf
        elif ptype == "machine_split":
            signal -= 0.3 * (conf or 0)
        elif ptype == "intensity_split":
            signal += 0.2 * (conf or 0)

    return max(-2.0, min(2.0, signal))


def _make_subject_key(result: dict) -> str:
    """Generate subject_key for pair identification."""
    halls = result.get("halls", [])
    ptype = result.get("pattern_type", "")
    if ptype == "date_role_split":
        return "chain:all"
    if len(halls) == 2:
        pair = sorted(halls)
        return f"pair:{pair[0]}|{pair[1]}"
    return "chain:all"


def persist_chain_results(
    conn: sqlite3.Connection,
    chain_id: str,
    event_family_id: str | None,
    results: list[dict],
    valid_from: str,
    valid_to: str | None,
) -> int:
    """Store chain pattern results to v2 table. Returns rows inserted."""
    inserted = 0
    fam_key = event_family_id or ""
    for r in results:
        subject_key = _make_subject_key(r)
        promoted = 1 if r.get("promoted") else 0
        status = "detected" if promoted else "not_detected"
        conn.execute(
            """INSERT OR REPLACE INTO chain_pattern_results_v2
               (chain_id, event_family_id, pattern_type, subject_key,
                valid_from, valid_to, statistic, lift, p_value,
                evidence_days, confidence, promoted, status,
                explanation_json, warnings_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                chain_id,
                fam_key,
                r["pattern_type"],
                subject_key,
                valid_from,
                valid_to,
                r.get("statistic"),
                r.get("lift"),
                r.get("p_value"),
                r.get("evidence_days"),
                r.get("confidence"),
                promoted,
                status,
                json.dumps(r.get("explanation", []), ensure_ascii=False),
                json.dumps(r.get("warnings", []), ensure_ascii=False),
            ),
        )
        inserted += 1
    conn.commit()
    return inserted


def build_all_chain_patterns(
    conn: sqlite3.Connection,
    cutoff_date: str,
) -> dict[str, int]:
    """Run chain detection for all active chains. Returns counts per type."""
    assign_chain_ids(conn)
    chains = get_active_chains(conn)

    counts: dict[str, int] = {pt: 0 for pt in PATTERN_TYPES}
    total_stored = 0

    for chain_id, hall_ids in chains.items():
        results = []

        for i, ha in enumerate(hall_ids):
            for hb in hall_ids[i + 1:]:
                jm = detect_joint_machine(
                    conn, ha, hb, None, cutoff_date,
                )
                if jm:
                    results.append(jm)
                    counts["joint_machine"] += 1

                ms = detect_machine_split(
                    conn, ha, hb, None, cutoff_date,
                )
                if ms:
                    results.append(ms)
                    counts["machine_split"] += 1

                ins = detect_intensity_split(
                    conn, ha, hb, cutoff_date,
                )
                if ins:
                    results.append(ins)
                    counts["intensity_split"] += 1

        drs = detect_date_role_split(
            conn, chain_id, hall_ids, cutoff_date,
        )
        if drs:
            results.append(drs)
            counts["date_role_split"] += 1

        if results:
            stored = persist_chain_results(
                conn, chain_id, None, results,
                cutoff_date, "",
            )
            total_stored += stored

    counts["total_stored"] = total_stored
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Detect chain patterns (4-type)"
    )
    ap.add_argument("--db", required=True, help="Path to slot_atlas.db")
    ap.add_argument("--cutoff", default=None,
                     help="Feature cutoff date (required for release)")
    ap.add_argument("--allow-all-history-for-test", action="store_true",
                     help="Allow running without cutoff (dev/test only)")
    args = ap.parse_args()

    if args.cutoff is None and not args.allow_all_history_for_test:
        print(
            "error: --cutoff is required (use --allow-all-history-for-test "
            "to run without cutoff in dev/test mode)",
            file=sys.stderr,
        )
        sys.exit(1)

    cutoff = args.cutoff if args.cutoff is not None else "9999-12-31"

    db = Path(args.db)
    if not db.exists():
        print(f"error: {db} not found", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db))
    counts = build_all_chain_patterns(conn, cutoff)
    conn.close()

    print("chain patterns detected:")
    for k, v in counts.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
