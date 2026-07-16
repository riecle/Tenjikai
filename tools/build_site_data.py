#!/usr/bin/env python3
"""Convert a local slot-atlas export into the plaintext payload used by encrypt_vault.mjs.

This script does NOT read anything from this repository — it expects the full
slot-atlas project (seed/, exports/, slot_atlas.py) to live outside the repo,
since the repo no longer stores raw hall data. Point it at that project with
--atlas-dir, or place it as a sibling directory named "slot-atlas".

Output (build/plain.json) is git-ignored and must never be committed: it is
the plaintext the login screen is meant to hide. Feed it to encrypt_vault.mjs
to produce data/vault.json.

    # Normal rebuild from the full Slot Atlas project
    python3 tools/build_site_data.py --atlas-dir ../slot-atlas

    # Or preserve the rows already stored in the encrypted vault and only add
    # the optional machine/tail/unit analyses
    SITE_ID=... SITE_PASSWORD=... node tools/decrypt_vault.mjs
    python3 tools/build_site_data.py --atlas-dir ../slot-atlas --base-plain build/plain.json

    SITE_ID=... SITE_PASSWORD=... node tools/encrypt_vault.mjs
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re

from free_source_predictor import build_free_source_payload

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_PLAIN = ROOT / "build" / "plain.json"
SW_JS = ROOT / "sw.js"


def stamp_sw(model_version: str, as_of: str) -> None:
    """データ更新のたびにSWキャッシュ名へ版数と日付を刻み、PWAの静的資産の古残りを防ぐ。"""
    sw = SW_JS.read_text(encoding="utf-8")
    stamp = f'const CACHE = "slot-atlas-{model_version.split("-")[-1]}-{as_of}";'
    sw2, n = re.subn(r'const CACHE = "[^"]+";', stamp, sw, count=1)
    if n != 1:
        raise SystemExit("sw.js: CACHE定義が見つからない/複数ある")
    SW_JS.write_text(sw2, encoding="utf-8")
    print(f"sw.js cache -> {stamp}")


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


def load_base_payload(path: pathlib.Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read base plaintext payload {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("meta"), dict) or not isinstance(payload.get("rows"), list):
        raise SystemExit(f"Base plaintext payload {path} must contain object keys: meta and rows")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atlas-dir", default="../slot-atlas",
                         help="Path to the local slot-atlas project (default: ../slot-atlas)")
    parser.add_argument("--base-plain",
                         help="Reuse an already decrypted Tenjikai payload instead of rebuilding forecast rows")
    parser.add_argument("--no-free-source", action="store_true",
                         help="Skip optional machine/tail/position/unit analysis")
    args = parser.parse_args()
    atlas_dir = pathlib.Path(args.atlas_dir).resolve()

    if args.base_plain:
        base_path = pathlib.Path(args.base_plain).resolve()
        payload = load_base_payload(base_path)
        rows = payload["rows"]
        meta = dict(payload["meta"])
    else:
        if not (atlas_dir / "exports" / "forecast_candidates_365.csv").exists():
            raise SystemExit(
                f"slot-atlas export not found under {atlas_dir}. "
                "Pass --atlas-dir, or decrypt the current vault and pass --base-plain build/plain.json."
            )
        rows = load_candidates(atlas_dir)
        meta = build_meta(atlas_dir, rows)
        payload = {"meta": meta, "rows": rows}

    # include_unit=False は恒久ポリシー（有料ソース由来はローカル限定・vault非掲載）
    free_source = None if args.no_free_source else build_free_source_payload(atlas_dir, rows, include_unit=False)
    if free_source is not None:
        meta["free_source_table_counts"] = free_source.get("table_counts", {})
        meta["free_source_full_halls"] = sum(
            1 for hall in free_source.get("halls", {}).values() if hall.get("layer") == "FULL"
        )
        payload["free_source"] = free_source
    else:
        payload.pop("free_source", None)
        meta.pop("free_source_table_counts", None)
        meta.pop("free_source_full_halls", None)

    payload["meta"] = meta
    payload["rows"] = rows
    OUT_PLAIN.parent.mkdir(parents=True, exist_ok=True)
    OUT_PLAIN.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT_PLAIN} ({len(rows)} rows, {OUT_PLAIN.stat().st_size:,} bytes)")
    stamp_sw(str(meta.get("model_version", "x")), str(meta.get("as_of") or "na"))
    if free_source is not None:
        counts = free_source.get("table_counts", {})
        layers = {"FULL": 0, "SUMMARY": 0, "NONE": 0}
        for hall in free_source.get("halls", {}).values():
            layers[hall.get("layer", "NONE")] = layers.get(hall.get("layer", "NONE"), 0) + 1
        print("free-source:", ", ".join(f"{k}={v}" for k, v in counts.items()))
        print("layers:", ", ".join(f"{k}={v}" for k, v in layers.items()))
    print("next: SITE_ID=... SITE_PASSWORD=... node tools/encrypt_vault.mjs")


if __name__ == "__main__":
    main()
