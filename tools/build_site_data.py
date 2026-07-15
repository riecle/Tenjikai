#!/usr/bin/env python3
"""Convert a local slot-atlas export into the plaintext payload used by encrypt_vault.mjs.

This script does NOT read anything from this repository — it expects the full
slot-atlas project (seed/, exports/, slot_atlas.py) to live outside the repo,
since the repo no longer stores raw hall data. Point it at that project with
--atlas-dir, or place it as a sibling directory named "slot-atlas".

Output (build/plain.json) is git-ignored and must never be committed: it is
the plaintext the login screen is meant to hide. Feed it to encrypt_vault.mjs
to produce data/vault.json.

    python3 tools/build_site_data.py --atlas-dir ../slot-atlas
    SITE_ID=... SITE_PASSWORD=... node tools/encrypt_vault.mjs
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_PLAIN = ROOT / "build" / "plain.json"


def load_candidates(atlas_dir: pathlib.Path) -> list[dict]:
    csv_path = atlas_dir / "exports" / "forecast_candidates_365.csv"
    rows = []
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append({
                "d": row["date"],
                "id": row["hall_id"],
                "h": row["hall_name"],
                "m": row["market"],
                "r": row["rank"],
                "p": float(row["predicted_mean"]),
                "e": float(row["adjusted_edge"]),
                "u": float(row["utility_edge"]),
                "tm": int(row["travel_minutes"]) if row["travel_minutes"] else None,
                "tp": float(row["travel_penalty"]),
                "c": float(row["confidence"]),
                "n": int(row["sample_n"]) if row["sample_n"] else None,
                "why": row["reason"],
                "risk": json.loads(row["risk_flags"] or "[]"),
                "age": int(row["data_age_days"]),
                "stale": row["stale_warning"] or None,
                "hz": row["horizon_warning"] or None,
            })
    return rows


def load_model_version(atlas_dir: pathlib.Path) -> str:
    text = (atlas_dir / "slot_atlas.py").read_text(encoding="utf-8")
    match = re.search(r'MODEL_VERSION\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "unknown"


def build_meta(atlas_dir: pathlib.Path, rows: list[dict]) -> dict:
    halls = json.loads((atlas_dir / "seed" / "halls.json").read_text(encoding="utf-8"))
    dates = sorted({row["d"] for row in rows})
    return {
        "model_version": load_model_version(atlas_dir),
        "as_of": dates[0] if dates else None,
        "date_range": [dates[0], dates[-1]] if dates else None,
        "hall_count": len(halls),
        "active_hall_count": sum(1 for h in halls if h.get("active")),
        "markets": sorted({h["market"] for h in halls}),
        "row_count": len(rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atlas-dir", default="../slot-atlas",
                         help="Path to the local slot-atlas project (default: ../slot-atlas)")
    args = parser.parse_args()
    atlas_dir = pathlib.Path(args.atlas_dir).resolve()
    if not (atlas_dir / "exports" / "forecast_candidates_365.csv").exists():
        raise SystemExit(f"slot-atlas export not found under {atlas_dir}. Pass --atlas-dir.")

    rows = load_candidates(atlas_dir)
    meta = build_meta(atlas_dir, rows)
    payload = {"meta": meta, "rows": rows}

    OUT_PLAIN.parent.mkdir(parents=True, exist_ok=True)
    OUT_PLAIN.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT_PLAIN} ({len(rows)} rows, {OUT_PLAIN.stat().st_size:,} bytes)")
    print("next: SITE_ID=... SITE_PASSWORD=... node tools/encrypt_vault.mjs")


if __name__ == "__main__":
    main()
