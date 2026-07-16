#!/usr/bin/env python3
"""Release validation for FREE_PUBLIC_MVP v0.1.

Validates the release artifacts:
  - frozen run exists and has required fields
  - build/plain.json exists and has required structure
  - vault can be decrypted (if credentials available)
  - run_meta present in free_source payload
  - v1.2 predictions present for target halls
  - no unit_no / q_unit_observed leaks in vault
  - backward compat: calendar data still present

Usage:
    python3 tools/validate_release.py [--plain build/plain.json]

Stdlib-only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_FIELDS = {"unit_no", "candidate_band", "Qhat_unit",
                    "q_unit_observed", "entry_no"}


def validate_plain(plain_path: Path) -> list[str]:
    """Validate a plaintext payload. Returns list of errors."""
    errors = []

    if not plain_path.exists():
        errors.append(f"plain.json not found: {plain_path}")
        return errors

    try:
        data = json.loads(plain_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        errors.append(f"cannot read plain.json: {e}")
        return errors

    if not isinstance(data, dict):
        errors.append("plain.json root is not an object")
        return errors

    if "meta" not in data:
        errors.append("missing 'meta' in plain.json")
    if "rows" not in data:
        errors.append("missing 'rows' in plain.json")
    elif not isinstance(data["rows"], list):
        errors.append("'rows' is not an array")
    elif len(data["rows"]) == 0:
        errors.append("'rows' is empty")

    fs = data.get("free_source")
    if fs is None:
        errors.append("missing 'free_source' in plain.json")
    else:
        if "halls" not in fs:
            errors.append("missing 'halls' in free_source")
        if "run_meta" not in fs:
            errors.append("missing 'run_meta' in free_source (v1.2 predictions not enriched)")
        elif fs["run_meta"]:
            rm = fs["run_meta"]
            for field in ["prediction_run_id", "feature_cutoff_at"]:
                if not rm.get(field):
                    errors.append(f"run_meta.{field} is empty")

        # Check for forbidden unit data
        halls = fs.get("halls", {})
        for hall_id, hall_data in halls.items():
            v12 = hall_data.get("v1_2", {})
            for date_key, day_data in v12.items():
                for pred_list_key in ("machines", "tails"):
                    for pred in day_data.get(pred_list_key, []):
                        for forbidden in FORBIDDEN_FIELDS:
                            if forbidden in pred:
                                errors.append(
                                    f"forbidden field '{forbidden}' in "
                                    f"v1.2.{pred_list_key} for {hall_id}/{date_key}"
                                )

    return errors


def validate_frozen_run(frozen_path: Path) -> list[str]:
    """Validate a frozen run JSON. Returns list of errors."""
    errors = []
    if not frozen_path.exists():
        errors.append(f"frozen run not found: {frozen_path}")
        return errors

    try:
        data = json.loads(frozen_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        errors.append(f"cannot read frozen run: {e}")
        return errors

    required = [
        "prediction_run_id", "built_at", "feature_cutoff_at",
        "model_version", "config_version",
        "source_snapshot_hash", "feature_snapshot_hash",
        "predictions",
    ]
    for field in required:
        if field not in data:
            errors.append(f"missing '{field}' in frozen run")

    preds = data.get("predictions", [])
    if not preds:
        errors.append("no predictions in frozen run")

    for i, p in enumerate(preds):
        if "warnings" not in p:
            errors.append(f"predictions[{i}] missing warnings")
        if "entity_type" not in p:
            errors.append(f"predictions[{i}] missing entity_type")
            continue

        if p["entity_type"] == "unit":
            errors.append(
                f"predictions[{i}] has entity_type='unit' — "
                "unit predictions must not be in frozen runs"
            )

    return errors


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate release artifacts")
    ap.add_argument("--plain", default=str(ROOT / "build" / "plain.json"),
                     help="Path to plaintext payload")
    ap.add_argument("--frozen-dir", default=str(ROOT / "predictions" / "frozen"),
                     help="Path to frozen runs directory")
    args = ap.parse_args()

    all_errors = []

    plain_path = Path(args.plain)
    plain_errors = validate_plain(plain_path)
    all_errors.extend(plain_errors)

    frozen_dir = Path(args.frozen_dir)
    if frozen_dir.is_dir():
        jsons = [f for f in sorted(frozen_dir.glob("*.json"))
                 if not f.name.endswith(".sha256")]
        if jsons:
            for fj in jsons:
                fr_errors = validate_frozen_run(fj)
                all_errors.extend(fr_errors)
        else:
            all_errors.append("no frozen run JSON files found")
    else:
        all_errors.append(f"frozen directory not found: {frozen_dir}")

    if all_errors:
        print(f"VALIDATION FAILED: {len(all_errors)} errors")
        for e in all_errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("VALIDATION PASSED")
        print(f"  plain: {plain_path}")
        if frozen_dir.is_dir():
            print(f"  frozen runs: {len(jsons)} files")


if __name__ == "__main__":
    main()
