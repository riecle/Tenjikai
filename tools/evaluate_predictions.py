#!/usr/bin/env python3
"""Evaluate frozen predictions against outcomes.

Reads a frozen prediction JSON and outcomes from the DB,
joins them, and produces an evaluation report.

Usage:
    python3 tools/evaluate_predictions.py \\
        --run-file predictions/frozen/manual_run_001.json \\
        --db ../slot-atlas/slot_atlas.db

Stdlib-only.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def load_outcomes(conn: sqlite3.Connection,
                   target_dates: list[str]) -> dict[tuple, dict]:
    """Load outcomes keyed by (target_date, hall_id, entity_type, entity_id)."""
    if not target_dates:
        return {}

    placeholders = ",".join("?" for _ in target_dates)
    rows = conn.execute(
        f"""SELECT target_date, hall_id, entity_type, entity_id,
                   actual_proxy, actual_label, actual_rank,
                   outcome_status, warnings_json
            FROM outcomes
            WHERE target_date IN ({placeholders})""",
        target_dates,
    ).fetchall()

    result = {}
    for r in rows:
        key = (r[0], r[1], r[2], r[3])
        result[key] = {
            "actual_proxy": r[4],
            "actual_label": r[5],
            "actual_rank": r[6],
            "outcome_status": r[7],
            "warnings": json.loads(r[8]) if r[8] else [],
        }
    return result


def evaluate(run: dict, outcomes: dict[tuple, dict]) -> dict:
    """Join predictions with outcomes and compute metrics."""
    preds = run["predictions"]
    matched = 0
    unmatched = 0
    by_type: dict[str, dict] = {}

    results = []
    for p in preds:
        key = (p["target_date"], p["hall_id"],
               p["entity_type"], p["entity_id"])
        outcome = outcomes.get(key)

        entry = {
            "target_date": p["target_date"],
            "hall_id": p["hall_id"],
            "entity_type": p["entity_type"],
            "entity_id": p["entity_id"],
            "predicted_score": p.get("score"),
            "predicted_rank": p.get("rank"),
            "confidence": p.get("confidence"),
        }

        if outcome:
            matched += 1
            entry["actual_proxy"] = outcome["actual_proxy"]
            entry["actual_label"] = outcome["actual_label"]
            entry["actual_rank"] = outcome["actual_rank"]
            entry["outcome_status"] = outcome["outcome_status"]
        else:
            unmatched += 1
            entry["outcome_status"] = "pending"

        et = p["entity_type"]
        if et not in by_type:
            by_type[et] = {"total": 0, "matched": 0, "hits": 0}
        by_type[et]["total"] += 1
        if outcome:
            by_type[et]["matched"] += 1
            if outcome.get("actual_label") == 1:
                by_type[et]["hits"] += 1

        results.append(entry)

    summary = {
        "prediction_run_id": run["prediction_run_id"],
        "total_predictions": len(preds),
        "matched_outcomes": matched,
        "pending_outcomes": unmatched,
        "by_entity_type": {},
    }
    for et, counts in sorted(by_type.items()):
        hit_rate = (
            counts["hits"] / counts["matched"]
            if counts["matched"] > 0 else None
        )
        summary["by_entity_type"][et] = {
            "total": counts["total"],
            "matched": counts["matched"],
            "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
        }

    return {"summary": summary, "details": results}


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate predictions")
    ap.add_argument("--run-file", required=True,
                     help="Path to frozen prediction JSON")
    ap.add_argument("--db", required=True, help="Path to slot_atlas.db")
    ap.add_argument("--output", help="Output path for evaluation report")
    args = ap.parse_args()

    run_path = Path(args.run_file)
    if not run_path.exists():
        print(f"error: {run_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(run_path, "r", encoding="utf-8") as f:
        run = json.load(f)

    conn = sqlite3.connect(args.db)
    target_dates = list({p["target_date"] for p in run["predictions"]})
    outcomes = load_outcomes(conn, target_dates)
    conn.close()

    report = evaluate(run, outcomes)

    out_path = (
        Path(args.output) if args.output
        else run_path.with_suffix(".eval.json")
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    s = report["summary"]
    print(f"evaluation: {s['prediction_run_id']}")
    print(f"  predictions: {s['total_predictions']}")
    print(f"  matched: {s['matched_outcomes']}")
    print(f"  pending: {s['pending_outcomes']}")
    for et, m in s["by_entity_type"].items():
        hr = f"{m['hit_rate']:.1%}" if m["hit_rate"] is not None else "n/a"
        print(f"  {et}: {m['total']} total, {m['matched']} matched, "
              f"hit_rate={hr}")
    print(f"report: {out_path}")


if __name__ == "__main__":
    main()
