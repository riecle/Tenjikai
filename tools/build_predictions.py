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
        cutoff_date = explicit[:10]
        leak = conn.execute(
            "SELECT COUNT(*) FROM hall_days WHERE result_date >= ?",
            (cutoff_date,),
        ).fetchone()[0]
        if leak:
            raise ValueError(
                f"future leakage: {leak} hall_days rows at or after "
                f"cutoff {cutoff_date}"
            )
        return explicit

    row = conn.execute(
        "SELECT MAX(result_date) FROM hall_days"
    ).fetchone()
    if not row or not row[0]:
        raise ValueError("no hall_days data found")
    return row[0] + "T23:59:59+09:00"


def build_features(conn: sqlite3.Connection,
                    cutoff_date: str) -> list[dict]:
    """Extract features from hall_days, filtered by cutoff.

    Returns a deterministically-ordered list of feature dicts.
    """
    rows = conn.execute(
        """SELECT hall_id, result_date, avg_diff, total_diff, avg_games,
                  source_name
           FROM hall_days
           WHERE result_date < ?
           ORDER BY hall_id, result_date, source_name""",
        (cutoff_date,),
    ).fetchall()

    features = []
    for r in rows:
        features.append({
            "hall_id": r[0],
            "result_date": r[1],
            "avg_diff": r[2],
            "total_diff": r[3],
            "avg_games": r[4],
            "source_name": r[5],
        })
    return features


def load_hall_capabilities(conn: sqlite3.Connection) -> dict[str, dict]:
    """Determine per-hall capability flags from data presence."""
    caps: dict[str, dict] = {}
    for (hid,) in conn.execute("SELECT hall_id FROM halls WHERE active = 1"):
        hd = conn.execute(
            "SELECT COUNT(*) FROM hall_days WHERE hall_id = ?", (hid,)
        ).fetchone()[0]
        md = conn.execute(
            "SELECT COUNT(*) FROM machine_days WHERE hall_id = ?", (hid,)
        ).fetchone()[0]
        td = conn.execute(
            "SELECT COUNT(*) FROM tail_days WHERE hall_id = ?", (hid,)
        ).fetchone()[0]
        ud = 0
        try:
            ud = conn.execute(
                "SELECT COUNT(*) FROM unit_days WHERE hall_id = ?", (hid,)
            ).fetchone()[0]
        except sqlite3.OperationalError:
            pass

        caps[hid] = {
            "hall_daily_available": hd > 0,
            "machine_daily_available": md > 0,
            "tail_daily_available": td > 0,
            "unit_daily_available": ud > 0,
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
) -> dict:
    db_path = atlas_dir / "slot_atlas.db"
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = None

    cutoff_at = compute_feature_cutoff(conn, cutoff)
    cutoff_date = cutoff_at[:10]

    features = build_features(conn, cutoff_date)
    feature_hash = canonical_hash(features)

    seed_dir = atlas_dir / "seed"
    src_hash = (
        source_snapshot_hash(seed_dir) if seed_dir.is_dir()
        else canonical_hash({"db": str(db_path)})
    )

    preds = load_existing_predictions(conn, target_dates)
    caps = load_hall_capabilities(conn)

    for p in preds:
        hall_cap = caps.get(p["hall_id"], {})
        p["capabilities"] = hall_cap

    model_ver = load_model_version(atlas_dir)

    conn.close()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    return {
        "prediction_run_id": run_id,
        "built_at": now,
        "feature_cutoff_at": cutoff_at,
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
                             args.source_mode)
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
