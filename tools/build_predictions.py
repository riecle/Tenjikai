#!/usr/bin/env python3
"""Generate a draft prediction JSON in v1.2 format.

Phase 0: wraps existing hall-level predictions from slot_atlas.db.
Later phases add machine, tail, chain, and unit predictions.

Usage:
    python3 tools/build_predictions.py --atlas-dir ../slot-atlas
    python3 tools/build_predictions.py --atlas-dir ../slot-atlas \\
        --run-id manual_run_001 --cutoff 2026-07-19T20:59:59+09:00 \\
        --target-dates 2026-07-20,2026-07-21

Stdlib-only.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prediction_utils import (
    canonical_hash,
    canonical_json,
    source_snapshot_hash,
)
from build_machine_scores import build_machine_predictions
from build_tail_zscores import build_tail_predictions
from build_machine_labels import compute_organic_model_gate

RANK_ORDER = {"S": 1, "A": 2, "B": 3, "C": 4, "NO BET": 5}


def get_code_commit() -> str | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def compute_feature_cutoff(conn: sqlite3.Connection,
                            explicit: str | None) -> str:
    """Determine feature_cutoff_at.

    If explicit is given, verify all hall_days are strictly before it.
    Otherwise, derive from the latest result_date in hall_days.
    """
    if explicit:
        # Future rows may exist in the database when reproducing a historical
        # run.  Leakage prevention belongs in every feature query via
        # result_date < cutoff, not in a global "future rows must not exist"
        # assertion.
        return explicit

    row = conn.execute(
        "SELECT MAX(result_date) FROM hall_days"
    ).fetchone()
    if not row or not row[0]:
        raise ValueError("no hall_days data found")
    # All feature queries use result_date < cutoff_date.  Default to the next
    # day so the latest completed business date is included rather than
    # silently dropped from the snapshot.
    from datetime import date as dt_date, timedelta
    next_day = dt_date.fromisoformat(row[0]) + timedelta(days=1)
    return next_day.isoformat() + "T00:00:00+09:00"


def build_features(conn: sqlite3.Connection,
                    cutoff_date: str) -> dict:
    """Build a deterministic manifest of every input used by predictions.

    Raw-source and feature hashes have distinct roles.  This manifest covers
    normalized/derived data and model configuration so label, threshold, chain,
    or capability changes always change feature_snapshot_hash.
    """
    from build_machine_scores import (
        MACHINE_TOP_N, MACHINE_PUBLISH_DAYS_MAX, CONFIDENCE_SAMPLE_K,
        FRESHNESS_TAU_DAYS, MIN_EVENTS_REFERENCE, W_P_EVENT, W_ROTATION,
        W_SIZE_FIT, W_WEEKDAY_FIT, W_RECENT_DEMAND, W_CHAIN_SIGNAL,
        W_LAST_SELECTED_PENALTY,
    )
    from build_tail_zscores import (
        SHRINKAGE_K, Z_CLIP_LO, Z_CLIP_HI, SE_EPSILON,
        Z_STRONG_THRESHOLD, Z_WATCH_THRESHOLD,
    )
    from build_machine_labels import (
        MIN_UNITS, MIN_COVERAGE, SELECTED_TOP_QUANTILE,
        Q_MACHINE_ABS_THRESHOLD, ORGANIC_AVG_DIFF_MIN,
        ORGANIC_POSITIVE_RATE_MIN,
    )

    manifest: dict[str, object] = {}

    def query_rows(sql: str, params: tuple = ()) -> list[tuple]:
        try:
            return conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []

    def table_manifest(
        table: str,
        desired_columns: list[str],
        *,
        date_column: str | None = None,
    ) -> list[dict]:
        """Extract available desired columns without assuming one schema version."""
        try:
            available = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})")
            }
        except sqlite3.OperationalError:
            return []
        columns = [c for c in desired_columns if c in available]
        if not columns:
            return []
        where = ""
        params: tuple = ()
        if date_column and date_column in available:
            where = f" WHERE {date_column} < ?"
            params = (cutoff_date,)
        order_candidates = [
            c for c in ("hall_id", date_column, "machine_key", "tail_key",
                        "event_family_id", "subject_key", "as_of")
            if c and c in columns
        ]
        order = f" ORDER BY {', '.join(order_candidates)}" if order_candidates else ""
        sql = f"SELECT {', '.join(columns)} FROM {table}{where}{order}"
        rows = query_rows(sql, params)
        return [dict(zip(columns, row)) for row in rows]

    rows = query_rows(
        """SELECT hall_id, result_date, avg_diff, total_diff, avg_games,
                  source_name, event_family_id
           FROM hall_days WHERE result_date < ?
           ORDER BY hall_id, result_date, source_name, event_family_id""",
        (cutoff_date,),
    )
    manifest["hall_days"] = [
        {"hall_id": r[0], "result_date": r[1], "avg_diff": r[2],
         "total_diff": r[3], "avg_games": r[4], "source_name": r[5],
         "event_family_id": r[6]} for r in rows
    ]

    manifest["machine_days"] = table_manifest(
        "machine_days",
        [
            "hall_id", "result_date", "machine_key", "machine_name",
            "avg_diff", "avg_games", "units", "total_units", "coverage",
            "positive_rate", "q_machine", "event_selected_label",
            "organic_active_day", "organic_selected_label", "label_status",
            "winning_units", "selected_flag", "source_name", "snapshot_id",
        ],
        date_column="result_date",
    )

    manifest["tail_days"] = table_manifest(
        "tail_days",
        [
            "hall_id", "result_date", "tail_key", "avg_diff",
            "positive_rate", "avg_games", "units", "observed_units",
            "coverage", "source_name", "snapshot_id",
        ],
        date_column="result_date",
    )

    ef_rows = query_rows(
        """SELECT event_family_id, hall_id, family_type, rule_json,
                  valid_from, valid_to, confidence, source,
                  canonical_family_key
           FROM event_families
           ORDER BY event_family_id"""
    )
    manifest["event_families"] = [
        {"event_family_id": r[0], "hall_id": r[1], "family_type": r[2],
         "rule_json": r[3], "valid_from": r[4], "valid_to": r[5],
         "confidence": r[6], "source": r[7],
         "canonical_family_key": r[8]} for r in ef_rows
    ]

    hc_rows = query_rows(
        """SELECT hc.hall_id, hc.as_of, hc.hall_daily_available,
                  hc.machine_daily_available, hc.tail_daily_available,
                  hc.unit_daily_available, hc.counter_metrics_available,
                  hc.layout_available, hc.reset_policy_available,
                  hc.acquisition_methods_json, hc.warnings_json
           FROM hall_capabilities hc
           JOIN (
               SELECT hall_id, MAX(as_of) AS max_as_of
               FROM hall_capabilities
               WHERE substr(as_of, 1, 10) <= ?
               GROUP BY hall_id
           ) latest
             ON latest.hall_id = hc.hall_id
            AND latest.max_as_of = hc.as_of
           ORDER BY hc.hall_id""",
        (cutoff_date,),
    )
    manifest["hall_capabilities"] = [
        {"hall_id": r[0], "as_of": r[1], "hall_daily": r[2],
         "machine_daily": r[3], "tail_daily": r[4], "unit_daily": r[5],
         "counter_metrics": r[6], "layout": r[7], "reset_policy": r[8],
         "acquisition_methods": r[9], "warnings": r[10]}
        for r in hc_rows
    ]

    cp_rows = query_rows(
        """SELECT chain_id, event_family_id, pattern_type, subject_key,
                  valid_from, valid_to, statistic, lift, p_value,
                  evidence_days, confidence, promoted, status,
                  explanation_json, warnings_json
           FROM chain_pattern_results_v2
           WHERE valid_from <= ?
             AND (valid_to IS NULL OR valid_to = '' OR valid_to > ?)
           ORDER BY chain_id, event_family_id, pattern_type,
                    subject_key, valid_from""",
        (cutoff_date, cutoff_date),
    )
    manifest["chain_pattern_results"] = [
        {"chain_id": r[0], "event_family_id": r[1], "pattern_type": r[2],
         "subject_key": r[3], "valid_from": r[4], "valid_to": r[5],
         "statistic": r[6], "lift": r[7], "p_value": r[8],
         "evidence_days": r[9], "confidence": r[10],
         "promoted": r[11], "status": r[12],
         "explanation_json": r[13], "warnings_json": r[14]}
        for r in cp_rows
    ]

    # Persist the gate's effective output in the snapshot as well as its raw
    # source columns.  This catches code/threshold changes that alter eligibility.
    gates = []
    for (hall_id,) in query_rows("SELECT hall_id FROM halls WHERE active = 1 ORDER BY hall_id"):
        try:
            gates.append(compute_organic_model_gate(conn, hall_id, cutoff_date=cutoff_date))
        except sqlite3.OperationalError:
            continue
    manifest["organic_model_gates"] = gates

    manifest["model_config"] = {
        "machine": {
            "top_n": MACHINE_TOP_N,
            "publish_days_max": MACHINE_PUBLISH_DAYS_MAX,
            "confidence_sample_k": CONFIDENCE_SAMPLE_K,
            "freshness_tau_days": FRESHNESS_TAU_DAYS,
            "min_events_reference": MIN_EVENTS_REFERENCE,
            "weights": {
                "p_event": W_P_EVENT, "rotation": W_ROTATION,
                "size_fit": W_SIZE_FIT, "weekday_fit": W_WEEKDAY_FIT,
                "recent_demand": W_RECENT_DEMAND,
                "chain_signal": W_CHAIN_SIGNAL,
                "last_selected_penalty": W_LAST_SELECTED_PENALTY,
            },
        },
        "labels": {
            "min_units": MIN_UNITS, "min_coverage": MIN_COVERAGE,
            "selected_top_quantile": SELECTED_TOP_QUANTILE,
            "q_machine_abs_threshold": Q_MACHINE_ABS_THRESHOLD,
            "organic_avg_diff_min": ORGANIC_AVG_DIFF_MIN,
            "organic_positive_rate_min": ORGANIC_POSITIVE_RATE_MIN,
            "organic_min_valid_days": 20,
            "organic_activation_rate_min": 0.20,
        },
        "tail": {
            "shrinkage_k": SHRINKAGE_K, "z_clip_lo": Z_CLIP_LO,
            "z_clip_hi": Z_CLIP_HI, "se_epsilon": SE_EPSILON,
            "strong_threshold": Z_STRONG_THRESHOLD,
            "watch_threshold": Z_WATCH_THRESHOLD,
        },
        "chain": {
            "min_common_days": 8,
            "date_role_concentration_threshold": 0.60,
            "date_role_effect_threshold": 0.50,
        },
        "distribution": {"unit_distribution_policy": "local_only"},
    }
    return manifest

def load_hall_capabilities(
    conn: sqlite3.Connection,
    cutoff_date: str,
) -> dict[str, dict]:
    """Determine capability flags using only data available before cutoff."""
    caps: dict[str, dict] = {}

    def count_before(table: str, hall_id: str) -> int:
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            date_col = next(
                (c for c in ("result_date", "business_date") if c in cols),
                None,
            )
            if date_col:
                return conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE hall_id = ? AND {date_col} < ?",
                    (hall_id, cutoff_date),
                ).fetchone()[0]
            return conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE hall_id = ?",
                (hall_id,),
            ).fetchone()[0]
        except sqlite3.OperationalError:
            return 0

    for (hid,) in conn.execute("SELECT hall_id FROM halls WHERE active = 1"):
        hd = count_before("hall_days", hid)
        md = count_before("machine_days", hid)
        td = count_before("tail_days", hid)
        ud = count_before("unit_days", hid)

        counter = False
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(unit_days)")}
            date_col = next(
                (c for c in ("result_date", "business_date") if c in cols),
                None,
            )
            counter_cols = [
                c for c in ("bb_count", "rb_count", "at_count", "cz_count", "initial_hit_count")
                if c in cols
            ]
            if counter_cols:
                evidence = " OR ".join(f"{c} IS NOT NULL" for c in counter_cols)
                date_filter = f" AND {date_col} < ?" if date_col else ""
                params = (hid, cutoff_date) if date_col else (hid,)
                counter = conn.execute(
                    f"SELECT COUNT(*) FROM unit_days WHERE hall_id = ? AND ({evidence}){date_filter}",
                    params,
                ).fetchone()[0] > 0
        except sqlite3.OperationalError:
            counter = False

        caps[hid] = {
            "hall_daily_available": hd > 0,
            "machine_daily_available": md > 0,
            "tail_daily_available": td > 0,
            "unit_daily_available": ud > 0,
            "counter_metrics_available": counter,
        }
    return caps


def load_existing_predictions(
    conn: sqlite3.Connection,
    target_dates: list[str] | None,
) -> list[dict]:
    """Read existing hall predictions from the predictions table."""
    sql = """SELECT p.target_date, p.hall_id, p.predicted_mean,
                    p.adjusted_edge, p.utility_edge, p.confidence,
                    p.rank, p.reasons_json
             FROM predictions p
             JOIN (SELECT MAX(run_id) AS run_id FROM model_runs) m
               ON p.run_id = m.run_id"""
    params: list = []
    if target_dates:
        placeholders = ",".join("?" for _ in target_dates)
        sql += f" WHERE p.target_date IN ({placeholders})"
        params = list(target_dates)
    sql += " ORDER BY p.target_date, p.hall_id"

    rows = conn.execute(sql, params).fetchall()
    preds = []
    for r in rows:
        reasons = []
        if r[7]:
            try:
                reasons = json.loads(r[7])
                if isinstance(reasons, str):
                    reasons = [reasons]
            except (json.JSONDecodeError, TypeError):
                reasons = [str(r[7])] if r[7] else []

        rank_text = r[6] if r[6] else "NO BET"
        rank_pos = RANK_ORDER.get(rank_text, 5)

        preds.append({
            "target_date": r[0],
            "hall_id": r[1],
            "entity_type": "hall",
            "entity_id": r[1],
            "score": r[2],
            "rank": rank_pos,
            "confidence": r[5],
            "explanation": reasons,
            "warnings": [],
            "capabilities": {},
        })
    return preds


def load_model_version(atlas_dir: Path) -> str:
    import re
    sa = atlas_dir / "slot_atlas.py"
    if sa.exists():
        m = re.search(r'MODEL_VERSION\s*=\s*"([^"]+)"', sa.read_text())
        if m:
            return m.group(1)
    return "unknown"


def build_draft(
    atlas_dir: Path,
    run_id: str,
    cutoff: str | None,
    target_dates: list[str] | None,
    source_mode: str,
    cutoff_source: str | None = None,
) -> dict:
    db_path = atlas_dir / "slot_atlas.db"
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = None

    cutoff_at = compute_feature_cutoff(conn, cutoff)
    cutoff_date = cutoff_at[:10]

    feature_manifest = build_features(conn, cutoff_date)
    feature_hash = canonical_hash(feature_manifest)

    seed_dir = atlas_dir / "seed"
    if seed_dir.is_dir():
        src_hash = source_snapshot_hash(seed_dir)
    else:
        import hashlib
        h = hashlib.sha256()
        with db_path.open("rb") as db_file:
            for chunk in iter(lambda: db_file.read(1024 * 1024), b""):
                h.update(chunk)
        src_hash = h.hexdigest()

    preds = load_existing_predictions(conn, target_dates)
    caps = load_hall_capabilities(conn, cutoff_date)

    for p in preds:
        hall_cap = caps.get(p["hall_id"], {})
        p["capabilities"] = hall_cap

    organic_gates: dict[str, dict] = {}
    if target_dates:
        for hall_id, hall_cap in caps.items():
            machine_preds = build_machine_predictions(
                conn, hall_id, target_dates, cutoff_date, hall_cap,
            )
            filtered_preds = []
            for mp in machine_preds:
                if mp.get("entity_type") == "machine_organic":
                    if hall_id not in organic_gates:
                        organic_gates[hall_id] = compute_organic_model_gate(
                            conn, hall_id, cutoff_date=cutoff_date,
                        )
                    gate = organic_gates[hall_id]
                    if not gate["model_active"]:
                        mp["warnings"].append(
                            f"organic_gate未通過(有効日{gate['valid_normal_days']}"
                            f"/活性率{gate['activation_rate']:.2f})"
                        )
                        mp["score"] = None
                        mp["rank"] = None
                        continue
                filtered_preds.append(mp)
            preds.extend(filtered_preds)

            tail_preds = build_tail_predictions(
                conn, hall_id, target_dates, cutoff_date, hall_cap,
            )
            preds.extend(tail_preds)

    model_ver = load_model_version(atlas_dir)

    conn.close()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    return {
        "prediction_run_id": run_id,
        "built_at": now,
        "feature_cutoff_at": cutoff_at,
        "resolved_cutoff_source": cutoff_source or ("cli" if cutoff else "computed"),
        "target_dates": target_dates or [],
        "model_version": model_ver,
        "config_version": "v1.2",
        "source_snapshot_hash": src_hash,
        "feature_snapshot_hash": feature_hash,
        "code_commit": get_code_commit(),
        "predictions": preds,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate a draft prediction JSON (v1.2 format)"
    )
    ap.add_argument("--atlas-dir", required=True,
                     help="Path to slot-atlas directory")
    ap.add_argument("--run-id", default="auto",
                     help="Prediction run ID")
    ap.add_argument("--cutoff",
                     help="Feature cutoff datetime (ISO 8601)")
    ap.add_argument("--target-dates",
                     help="Comma-separated target dates (YYYY-MM-DD)")
    ap.add_argument("--cutoff-source", choices=["cli", "target_date", "config", "computed"],
                     help="How the resolved cutoff was determined")
    ap.add_argument("--source-mode", default="free_public",
                     choices=["free_public"],
                     help="Data source mode")
    ap.add_argument("--output", default="build/run_draft.json",
                     help="Output path for draft JSON")
    args = ap.parse_args()

    atlas = Path(args.atlas_dir)
    if not atlas.is_dir():
        print(f"error: {atlas} is not a directory", file=sys.stderr)
        sys.exit(1)

    targets = (
        args.target_dates.split(",") if args.target_dates else None
    )
    run_id = args.run_id
    if run_id == "auto":
        run_id = "run_" + datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        draft = build_draft(atlas, run_id, args.cutoff, targets,
                             args.source_mode, args.cutoff_source)
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)

    n = len(draft["predictions"])
    print(f"draft written: {out} ({n} predictions)")
    print(f"source_snapshot_hash: {draft['source_snapshot_hash']}")
    print(f"feature_snapshot_hash: {draft['feature_snapshot_hash']}")


if __name__ == "__main__":
    main()
