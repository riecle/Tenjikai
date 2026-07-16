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
    """Detect date_role_split: date series divided across chain halls.

    Builds event_family x hall intensity matrix and checks if
    specific event families concentrate at specific halls.
    """
    family_hall_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    total_by_family: dict[str, int] = defaultdict(int)

    for hall_id in chain_halls:
        rows = conn.execute(
            """SELECT hd.event_family_id, COUNT(*)
               FROM hall_days hd
               JOIN event_families ef ON hd.event_family_id = ef.event_family_id
               WHERE hd.hall_id = ? AND hd.result_date < ?
                 AND hd.event_family_id IS NOT NULL
                 AND ef.family_type != '通常'
               GROUP BY hd.event_family_id""",
            (hall_id, cutoff_date),
        ).fetchall()

        for fam_id, cnt in rows:
            family_hall_counts[fam_id][hall_id] += cnt
            total_by_family[fam_id] += cnt

    if not family_hall_counts:
        return None

    concentrations = []
    for fam_id, hall_counts in family_hall_counts.items():
        total = total_by_family[fam_id]
        if total < 3:
            continue
        max_share = max(hall_counts.values()) / total
        concentrations.append(max_share)

    if len(concentrations) < 2:
        return None

    mean_concentration = statistics.mean(concentrations)
    n_families = len(concentrations)
    expected_even = 1.0 / len(chain_halls)

    promoted = (
        mean_concentration > DATE_ROLE_CONCENTRATION_THRESHOLD
        and n_families >= 3
    )

    confidence = _chain_confidence(
        n_families * len(chain_halls),
        0.05 if promoted else 0.5,
        mean_concentration / expected_even if expected_even > 0 else 1.0,
    )

    explanation = [
        f"mean_concentration={mean_concentration:.3f}",
        f"n_families={n_families}",
        f"expected_even={expected_even:.3f}",
    ]
    if promoted:
        explanation.insert(0, "promoted")

    warnings = []
    if n_families < 5:
        warnings.append("少標本")

    return {
        "pattern_type": "date_role_split",
        "halls": chain_halls,
        "lift": round(mean_concentration / expected_even, 4) if expected_even > 0 else None,
        "p_value": None,
        "statistic": round(mean_concentration, 4),
        "evidence_days": sum(total_by_family.values()),
        "confidence": confidence,
        "promoted": promoted,
        "explanation": explanation,
        "warnings": warnings,
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


def persist_chain_results(
    conn: sqlite3.Connection,
    chain_id: str,
    event_family_id: str | None,
    results: list[dict],
    valid_from: str,
    valid_to: str,
) -> int:
    """Store chain pattern results. Returns rows inserted."""
    inserted = 0
    fam_key = event_family_id or ""
    for r in results:
        conn.execute(
            """INSERT OR REPLACE INTO chain_pattern_results
               (chain_id, event_family_id, pattern_type, valid_from,
                valid_to, statistic, lift, p_value, evidence_days,
                confidence, explanation_json, warnings_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                chain_id,
                fam_key,
                r["pattern_type"],
                valid_from,
                valid_to,
                r.get("statistic"),
                r.get("lift"),
                r.get("p_value"),
                r.get("evidence_days"),
                r.get("confidence"),
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
                cutoff_date, "9999-12-31",
            )
            total_stored += stored

    counts["total_stored"] = total_stored
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Detect chain patterns (4-type)"
    )
    ap.add_argument("--db", required=True, help="Path to slot_atlas.db")
    ap.add_argument("--cutoff", default="9999-12-31",
                     help="Feature cutoff date")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"error: {db} not found", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db))
    counts = build_all_chain_patterns(conn, args.cutoff)
    conn.close()

    print("chain patterns detected:")
    for k, v in counts.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
