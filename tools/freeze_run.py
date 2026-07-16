#!/usr/bin/env python3
"""Freeze a draft prediction run into canonical JSON + SHA-256.

Usage:
    python3 tools/freeze_run.py build/run_draft.json
    python3 tools/freeze_run.py build/run_draft.json --db slot_atlas.db

The frozen file and its hash are written to predictions/frozen/.
A frozen run is immutable: re-freezing with different content is rejected.
Stdlib-only.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prediction_utils import canonical_json, sha256_hex, validate_draft


def freeze(draft: dict) -> tuple[str, str]:
    """Validate, canonicalize, and hash a draft.

    Returns (canonical_json_string, sha256_hex).
    Raises ValueError on validation failure.
    """
    errors = validate_draft(draft)
    if errors:
        raise ValueError("validation errors:\n  " + "\n  ".join(errors))

    draft["predictions"] = sorted(
        draft["predictions"],
        key=lambda p: (
            p["target_date"], p["hall_id"],
            p["entity_type"], p["entity_id"],
        ),
    )

    cj = canonical_json(draft)
    return cj, sha256_hex(cj.encode("utf-8"))


def insert_run(db_path: Path, draft: dict, payload_hash: str) -> None:
    """Insert or update a prediction_run record.

    Refuses to overwrite a frozen or published run.
    """
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT status FROM prediction_runs WHERE prediction_run_id = ?",
        (draft["prediction_run_id"],),
    ).fetchone()

    if row and row[0] in ("frozen", "published"):
        conn.close()
        raise ValueError(
            f"run {draft['prediction_run_id']} is already {row[0]}"
        )

    conn.execute(
        """INSERT OR REPLACE INTO prediction_runs
           (prediction_run_id, built_at, feature_cutoff_at,
            model_version, config_version,
            source_snapshot_hash, feature_snapshot_hash,
            code_commit, status, published_payload_hash)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            draft["prediction_run_id"],
            draft["built_at"],
            draft["feature_cutoff_at"],
            draft["model_version"],
            draft["config_version"],
            draft["source_snapshot_hash"],
            draft["feature_snapshot_hash"],
            draft.get("code_commit"),
            "frozen",
            payload_hash,
        ),
    )
    conn.commit()
    conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Freeze a prediction run")
    ap.add_argument("draft", help="Path to draft prediction JSON")
    ap.add_argument("--output", help="Output path for frozen JSON")
    ap.add_argument("--hash-output", help="Output path for SHA-256 file")
    ap.add_argument("--db", help="DB path to register the frozen run")
    args = ap.parse_args()

    draft_path = Path(args.draft)
    if not draft_path.exists():
        print(f"error: {draft_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(draft_path, "r", encoding="utf-8") as f:
        draft = json.load(f)

    try:
        cj, sha = freeze(draft)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    run_id = draft["prediction_run_id"]
    out_dir = Path("predictions/frozen")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = Path(args.output) if args.output else out_dir / f"{run_id}.json"
    hash_path = (
        Path(args.hash_output)
        if args.hash_output
        else out_dir / f"{run_id}.sha256"
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    hash_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists():
        existing = out_path.read_text(encoding="utf-8")
        if existing != cj:
            print(
                f"error: {out_path} already frozen with different content",
                file=sys.stderr,
            )
            sys.exit(1)

    out_path.write_text(cj, encoding="utf-8")
    hash_path.write_text(f"{sha}  {out_path.name}\n", encoding="utf-8")
    print(f"frozen: {out_path}")
    print(f"sha256: {sha}")

    if args.db:
        try:
            insert_run(Path(args.db), draft, sha)
            print(f"registered in DB: {args.db}")
        except ValueError as e:
            print(f"DB error: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
