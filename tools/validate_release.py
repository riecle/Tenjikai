#!/usr/bin/env python3
"""Validate FREE_PUBLIC_MVP release artifacts and optional real-DB E2E.

The validator is intentionally a release gate, not a report generator:
invalid payloads, mismatched cutoffs, failed tests, or skipped tests under
--fail-on-skip produce a non-zero exit code.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import datetime as _dt
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_FIELDS = {
    "unit_no", "candidate_band", "Qhat_unit", "q_unit_observed", "entry_no"
}


def _read_json(path: Path) -> tuple[dict | None, list[str]]:
    if not path.exists():
        return None, [f"file not found: {path}"]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"cannot read {path}: {exc}"]
    if not isinstance(value, dict):
        return None, [f"JSON root is not an object: {path}"]
    return value, []


def validate_plain(path: Path, expected_cutoff: str | None) -> tuple[list[str], dict]:
    data, errors = _read_json(path)
    stats = {
        "calendar_rows": 0, "v12_halls": 0, "machine_predictions": 0,
        "tail_predictions": 0, "chain_patterns": 0,
    }
    if data is None:
        return errors, stats
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        errors.append("rows is missing or empty")
    else:
        stats["calendar_rows"] = len(rows)
    fs = data.get("free_source")
    if not isinstance(fs, dict):
        errors.append("free_source is missing")
        return errors, stats
    run_meta = fs.get("run_meta")
    if not isinstance(run_meta, dict) or not run_meta:
        errors.append("free_source.run_meta is missing or empty")
    else:
        for key in ("prediction_run_id", "feature_cutoff_at", "model_version"):
            if not run_meta.get(key):
                errors.append(f"run_meta.{key} is missing")
        if expected_cutoff and run_meta.get("feature_cutoff_at") != expected_cutoff:
            errors.append(
                f"cutoff mismatch: expected {expected_cutoff!r}, "
                f"got {run_meta.get('feature_cutoff_at')!r}"
            )
    halls = fs.get("halls", {})
    if not isinstance(halls, dict):
        errors.append("free_source.halls is not an object")
        return errors, stats
    for hall_id, hall in halls.items():
        if not isinstance(hall, dict):
            continue
        v12 = hall.get("v1_2", {})
        if isinstance(v12, dict) and v12:
            stats["v12_halls"] += 1
        patterns = hall.get("chain_patterns", [])
        if isinstance(patterns, list):
            stats["chain_patterns"] += len(patterns)
            for pattern in patterns:
                if not pattern.get("promoted") or pattern.get("status") != "detected":
                    errors.append(f"non-promoted chain pattern leaked for {hall_id}")
        if not isinstance(v12, dict):
            continue
        for date_key, day in v12.items():
            if not isinstance(day, dict):
                continue
            machines = day.get("machines", [])
            tails = day.get("tails", [])
            if isinstance(machines, list):
                stats["machine_predictions"] += len(machines)
            if isinstance(tails, list):
                stats["tail_predictions"] += len(tails)
            for key, predictions in (("machines", machines), ("tails", tails)):
                if not isinstance(predictions, list):
                    errors.append(f"{hall_id}/{date_key}/{key} is not a list")
                    continue
                for pred in predictions:
                    if "warnings" not in pred or not isinstance(pred["warnings"], list):
                        errors.append(f"warnings missing for {hall_id}/{date_key}/{key}")
                    leaked = FORBIDDEN_FIELDS.intersection(pred)
                    if leaked:
                        errors.append(
                            f"forbidden fields {sorted(leaked)} in {hall_id}/{date_key}/{key}"
                        )
    if stats["v12_halls"] == 0:
        errors.append("no v1.2 hall payloads")
    if stats["machine_predictions"] == 0:
        errors.append("no v1.2 machine predictions")
    if stats["tail_predictions"] == 0:
        errors.append("no v1.2 tail predictions")
    return errors, stats


def validate_frozen_run(path: Path, expected_cutoff: str | None) -> list[str]:
    data, errors = _read_json(path)
    if data is None:
        return errors
    required = (
        "prediction_run_id", "built_at", "feature_cutoff_at", "model_version",
        "config_version", "source_snapshot_hash", "feature_snapshot_hash",
        "resolved_cutoff_source", "target_dates", "predictions",
    )
    for key in required:
        if key not in data:
            errors.append(f"frozen run missing {key}")
    if expected_cutoff and data.get("feature_cutoff_at") != expected_cutoff:
        errors.append("frozen run cutoff mismatch")
    try:
        if (data.get("built_at") and data.get("feature_cutoff_at")
                and _dt.datetime.fromisoformat(str(data["feature_cutoff_at"]))
                > _dt.datetime.fromisoformat(str(data["built_at"]))):
            errors.append("feature_cutoff_at is after built_at (freeze invariant violated)")
    except ValueError:
        errors.append("built_at/feature_cutoff_at not ISO parseable")
    predictions = data.get("predictions", [])
    if not isinstance(predictions, list) or not predictions:
        errors.append("frozen run has no predictions")
        return errors
    for idx, pred in enumerate(predictions):
        if "warnings" not in pred or not isinstance(pred.get("warnings"), list):
            errors.append(f"prediction[{idx}] warnings missing")
        if pred.get("entity_type") in {"unit", "unit_local"}:
            errors.append(f"prediction[{idx}] contains unit data")
    return errors


def validate_database(db_path: Path, cutoff: str | None) -> tuple[list[str], dict]:
    errors: list[str] = []
    stats: dict[str, int] = {}
    if not db_path.exists():
        return [f"atlas DB not found: {db_path}"], stats
    try:
        conn = sqlite3.connect(str(db_path))
        for table in ("halls", "hall_days", "machine_days", "tail_days"):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not exists:
                errors.append(f"required table missing: {table}")
                continue
            stats[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if cutoff:
            cutoff_date = cutoff[:10]
            stats["hall_days_before_cutoff"] = conn.execute(
                "SELECT COUNT(*) FROM hall_days WHERE result_date < ?", (cutoff_date,)
            ).fetchone()[0]
            if stats["hall_days_before_cutoff"] == 0:
                errors.append("no hall_days before cutoff")
        conn.close()
    except sqlite3.Error as exc:
        errors.append(f"database validation failed: {exc}")
    return errors, stats


def run_test_suite(atlas_db: Path, fail_on_skip: bool) -> tuple[list[str], dict]:
    env = os.environ.copy()
    env["TENJIKAI_ATLAS_DIR"] = str(atlas_db.parent)
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=900,
    )
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    ran = re.search(r"Ran\s+(\d+)\s+tests?", output)
    skipped = re.search(r"skipped=(\d+)", output)
    stats = {
        "tests_run": int(ran.group(1)) if ran else 0,
        "tests_skipped": int(skipped.group(1)) if skipped else 0,
        "test_returncode": proc.returncode,
    }
    errors: list[str] = []
    if proc.returncode != 0:
        tail = "\n".join(output.splitlines()[-40:])
        errors.append(f"test suite failed:\n{tail}")
    if fail_on_skip and stats["tests_skipped"]:
        errors.append(f"test suite skipped {stats['tests_skipped']} tests")
    return errors, stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plain", default=str(ROOT / "build" / "plain.json"))
    parser.add_argument("--frozen-run", help="Exact frozen run to validate")
    parser.add_argument("--frozen-dir", default=str(ROOT / "predictions" / "frozen"))
    parser.add_argument("--cutoff")
    parser.add_argument("--atlas-db")
    parser.add_argument("--fail-on-skip", action="store_true")
    parser.add_argument("--skip-test-suite", action="store_true")
    args = parser.parse_args()

    errors: list[str] = []
    report: dict[str, object] = {}
    plain_errors, plain_stats = validate_plain(Path(args.plain), args.cutoff)
    errors.extend(plain_errors)
    report["payload"] = plain_stats

    if args.frozen_run:
        frozen = Path(args.frozen_run)
    else:
        candidates = sorted(Path(args.frozen_dir).glob("*.json"))
        frozen = candidates[-1] if candidates else Path("__missing_frozen_run__")
    errors.extend(validate_frozen_run(frozen, args.cutoff))
    report["frozen_run"] = str(frozen)

    if args.atlas_db:
        db_errors, db_stats = validate_database(Path(args.atlas_db), args.cutoff)
        errors.extend(db_errors)
        report["database"] = db_stats
        if not args.skip_test_suite:
            test_errors, test_stats = run_test_suite(
                Path(args.atlas_db), args.fail_on_skip
            )
            errors.extend(test_errors)
            report["tests"] = test_stats
    elif args.fail_on_skip:
        errors.append("--fail-on-skip requires --atlas-db")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        print(f"VALIDATION FAILED: {len(errors)} error(s)", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        raise SystemExit(1)
    print("VALIDATION PASSED")


if __name__ == "__main__":
    main()
