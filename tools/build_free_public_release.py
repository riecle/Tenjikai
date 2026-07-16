#!/usr/bin/env python3
"""One-command release build pipeline for FREE_PUBLIC_MVP v0.1.

Runs the full pipeline:
  migrate → normalize → canonicalize → labels → gate → capabilities
  → chain → predict → freeze → build site → encrypt → verify

Usage:
    python3 tools/build_free_public_release.py --atlas-dir ../slot-atlas \
        --target-dates 2026-07-20,2026-07-21

Stdlib-only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import shutil
import sys
from datetime import date as dt_date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


def resolve_cutoff(cutoff_arg: str | None, target_dates_arg: str | None) -> str:
    """Resolve the feature cutoff datetime.

    If cutoff_arg is provided, return it directly.
    If target_dates_arg is provided, return (earliest target - 1 day)
    at 23:59:59+09:00.
    If neither is given, raise ValueError.
    """
    if cutoff_arg:
        return cutoff_arg
    if target_dates_arg:
        dates = [
            dt_date.fromisoformat(d.strip())
            for d in target_dates_arg.split(",")
        ]
        earliest = min(dates)
        cutoff_date = earliest - timedelta(days=1)
        return cutoff_date.isoformat() + "T23:59:59+09:00"
    raise ValueError("Either --cutoff or --target-dates must be specified")


def run_step(label: str, cmd: list[str], cwd: Path | None = None) -> None:
    print(f"\n{'='*60}")
    print(f"[STEP] {label}")
    print(f"  cmd: {' '.join(cmd)}")
    print(f"{'='*60}")
    result = subprocess.run(
        cmd, cwd=str(cwd or ROOT),
        capture_output=False, text=True,
    )
    if result.returncode != 0:
        print(f"[FAIL] {label} (exit {result.returncode})", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] {label}")



def build_freeze_command(py: str, draft_path: Path, db_path: Path) -> list[str]:
    """Return the freeze command using the CLI's positional draft argument."""
    return [
        py, str(TOOLS / "freeze_run.py"), str(draft_path),
        "--db", str(db_path),
    ]

def main() -> None:
    ap = argparse.ArgumentParser(
        description="One-command release build pipeline"
    )
    ap.add_argument("--atlas-dir", required=True,
                     help="Path to slot-atlas directory")
    ap.add_argument("--target-dates", required=True,
                     help="Comma-separated target dates (YYYY-MM-DD)")
    ap.add_argument("--run-id", help="Prediction run ID (auto-generated if omitted)")
    ap.add_argument("--cutoff", help="Feature cutoff datetime (ISO 8601)")
    ap.add_argument("--skip-encrypt", action="store_true",
                     help="Skip vault encryption (for testing)")
    args = ap.parse_args()

    atlas_dir = Path(args.atlas_dir).resolve()
    db_path = atlas_dir / "slot_atlas.db"
    if not db_path.exists():
        print(f"error: {db_path} not found", file=sys.stderr)
        sys.exit(1)

    # --- Resolve cutoff ---
    target_dates: list[str] = []
    if args.target_dates:
        target_dates = [d.strip() for d in args.target_dates.split(",") if d.strip()]

    if args.cutoff:
        resolved_cutoff = args.cutoff
        resolved_cutoff_source = "cli"
    elif target_dates:
        earliest = min(dt_date.fromisoformat(d) for d in target_dates)
        resolved_cutoff = (earliest - timedelta(days=1)).isoformat() + "T23:59:59+09:00"
        resolved_cutoff_source = "target_date"
    else:
        print(
            "error: Release requires --cutoff or --target-dates "
            "to determine resolved_cutoff",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"[INFO] resolved_cutoff: {resolved_cutoff} (source: {resolved_cutoff_source})")

    run_id = args.run_id or "run_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    py = sys.executable

    # 1. Migrate DB
    run_step("migrate_db", [py, str(TOOLS / "migrate_db.py"), "--db", str(db_path)])

    # 2. Normalize sources
    run_step("normalize_sources", [py, str(TOOLS / "normalize_sources.py"), "--db", str(db_path)])

    # 3. Build event families (includes canonical key)
    run_step("build_event_families", [py, str(TOOLS / "build_event_families.py"), "--db", str(db_path)])

    # 4. Build machine labels
    run_step("build_machine_labels", [
        py, str(TOOLS / "build_machine_labels.py"),
        "--db", str(db_path), "--cutoff", resolved_cutoff,
    ])

    # 5. Build capabilities
    run_step("build_capabilities", [
        py, str(TOOLS / "build_capabilities.py"),
        "--db", str(db_path), "--as-of", resolved_cutoff,
    ])

    # 6. Chain detection
    chain_cmd = [
        py, str(TOOLS / "chain_detector.py"), "--db", str(db_path),
        "--cutoff", resolved_cutoff,
    ]
    run_step("chain_detector", chain_cmd)

    # 7. Build predictions
    pred_cmd = [
        py, str(TOOLS / "build_predictions.py"),
        "--atlas-dir", str(atlas_dir),
        "--run-id", run_id,
        "--output", str(ROOT / "build" / "run_draft.json"),
        "--cutoff", resolved_cutoff,
        "--cutoff-source", resolved_cutoff_source,
    ]
    if target_dates:
        pred_cmd.extend(["--target-dates", ",".join(target_dates)])
    run_step("build_predictions", pred_cmd)

    # 8. Freeze run
    draft_path = ROOT / "build" / "run_draft.json"
    run_step("freeze_run", build_freeze_command(py, draft_path, db_path))

    # 9. Find the frozen run file
    frozen_dir = ROOT / "predictions" / "frozen"
    frozen_files = sorted(frozen_dir.glob(f"{run_id}*.json"), reverse=True)
    if not frozen_files:
        frozen_files = sorted(frozen_dir.glob("*.json"), reverse=True)
    frozen_path = frozen_files[0] if frozen_files else None
    if not frozen_path:
        print("[FAIL] no frozen run found", file=sys.stderr)
        sys.exit(1)
    print(f"[INFO] frozen run: {frozen_path}")

    # 10. Build site data with frozen run
    site_cmd = [
        py, str(TOOLS / "build_site_data.py"),
        "--atlas-dir", str(atlas_dir),
        "--frozen-run", str(frozen_path),
        "--cutoff", resolved_cutoff,
    ]
    run_step("build_site_data", site_cmd)

    # 11. Encrypt vault and verify by an independent decrypt pass.
    if not args.skip_encrypt:
        site_id = os.environ.get("SITE_ID")
        site_password = os.environ.get("SITE_PASSWORD")
        if site_id and site_password:
            vault_path = ROOT / "data" / "vault.json"
            backup_path = ROOT / "build" / "vault.pre_release.backup.json"
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            had_vault = vault_path.exists()
            if had_vault:
                shutil.copy2(vault_path, backup_path)
            try:
                run_step("encrypt_vault", [
                    "node", str(TOOLS / "encrypt_vault.mjs"),
                ])
                run_step("decrypt_vault_verify", [
                    "node", str(TOOLS / "decrypt_vault.mjs"),
                ])
            except SystemExit:
                if had_vault and backup_path.exists():
                    shutil.copy2(backup_path, vault_path)
                    print("[ROLLBACK] restored previous data/vault.json")
                elif vault_path.exists():
                    vault_path.unlink()
                raise
            finally:
                if backup_path.exists():
                    backup_path.unlink()
        else:
            print(
                "error: SITE_ID and SITE_PASSWORD are required for a formal release; "
                "use --skip-encrypt only for a non-release test build",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        print("[SKIP] encrypt_vault: --skip-encrypt (non-release test build)")

    # 12. Validate the exact release artifacts before plaintext cleanup.
    plain_path = ROOT / "build" / "plain.json"
    validate_cmd = [
        py, str(TOOLS / "validate_release.py"),
        "--plain", str(plain_path),
        "--frozen-run", str(frozen_path),
        "--cutoff", resolved_cutoff,
        "--atlas-db", str(db_path),
    ]
    if not args.skip_encrypt:
        validate_cmd.append("--fail-on-skip")
    else:
        validate_cmd.append("--skip-test-suite")
    run_step("validate_release", validate_cmd)

    # 13. Verify
    if plain_path.exists():
        data = json.loads(plain_path.read_text(encoding="utf-8"))
        run_meta = data.get("free_source", {}).get("run_meta", {})
        n_halls = len(data.get("free_source", {}).get("halls", {}))
        n_rows = len(data.get("rows", []))
        print(f"\n{'='*60}")
        print("[VERIFY] Release build complete")
        print(f"  run_id:              {run_meta.get('prediction_run_id', 'N/A')}")
        print(f"  cutoff:              {run_meta.get('feature_cutoff_at', 'N/A')}")
        print(f"  resolved_cutoff_src: {resolved_cutoff_source}")
        print(f"  target_dates:        {target_dates or 'N/A'}")
        print(f"  halls:               {n_halls}")
        print(f"  rows:                {n_rows}")
        print(f"  frozen:              {frozen_path}")
        print(f"{'='*60}")
    else:
        print("[WARN] build/plain.json not found — cannot verify")

    # 14. Plaintext cleanup — remove intermediate build artifacts
    for cleanup_file in (ROOT / "build" / "plain.json", ROOT / "build" / "run_draft.json"):
        if cleanup_file.exists():
            cleanup_file.unlink()
            print(f"[CLEANUP] deleted {cleanup_file}")


if __name__ == "__main__":
    main()
