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
import sqlite3
from collections import defaultdict

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


def enrich_with_v12(
    free_source: dict,
    atlas_dir: pathlib.Path,
    frozen_run_path: pathlib.Path | None,
) -> None:
    """Add v1.2 capabilities, chain patterns, and predictions to free_source."""
    db_path = atlas_dir / "slot_atlas.db"
    if not db_path.exists():
        return

    conn = sqlite3.connect(str(db_path))
    halls_payload = free_source.get("halls", {})

    for hall_id in halls_payload:
        caps = {}
        for tbl, key in [
            ("hall_days", "hall_daily"),
            ("machine_days", "machine_daily"),
            ("tail_days", "tail_daily"),
            ("unit_days", "unit_daily"),
        ]:
            try:
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {tbl} WHERE hall_id = ?",
                    (hall_id,),
                ).fetchone()[0]
                caps[key] = count > 0
            except sqlite3.OperationalError:
                caps[key] = False
        halls_payload[hall_id]["capabilities"] = caps

    chain_map: dict[str, str] = {}
    try:
        for r in conn.execute(
            "SELECT hall_id, chain_id FROM halls "
            "WHERE chain_id IS NOT NULL AND chain_id != ''"
        ).fetchall():
            chain_map[r[0]] = r[1]
    except sqlite3.OperationalError:
        pass

    patterns_by_chain: dict[str, list] = defaultdict(list)
    try:
        for r in conn.execute(
            """SELECT chain_id, pattern_type, statistic, lift,
                      confidence, explanation_json
               FROM chain_pattern_results
               ORDER BY chain_id, pattern_type"""
        ).fetchall():
            try:
                expl = json.loads(r[5]) if r[5] else {}
            except (json.JSONDecodeError, TypeError):
                expl = {}
            summary = ""
            if isinstance(expl, dict):
                summary = expl.get("summary", expl.get("note", ""))
            patterns_by_chain[r[0]].append({
                "type": r[1],
                "statistic": round(r[2], 3) if r[2] else None,
                "lift": round(r[3], 2) if r[3] else None,
                "confidence": round(r[4], 3) if r[4] else None,
                "summary": summary,
            })
    except sqlite3.OperationalError:
        pass

    for hall_id, hall_data in halls_payload.items():
        cid = chain_map.get(hall_id)
        if cid:
            hall_data["chain_id"] = cid
            hall_data["chain_patterns"] = patterns_by_chain.get(cid, [])

    if frozen_run_path and frozen_run_path.exists():
        with open(frozen_run_path, encoding="utf-8") as f:
            run_data = json.load(f)

        free_source["run_meta"] = {
            "prediction_run_id": run_data.get("prediction_run_id"),
            "feature_cutoff_at": run_data.get("feature_cutoff_at"),
            "model_version": run_data.get("model_version"),
            "config_version": run_data.get("config_version"),
            "built_at": run_data.get("built_at"),
        }

        v12_by_hall: dict[str, dict[str, dict]] = defaultdict(
            lambda: defaultdict(lambda: {"machines": [], "tails": []})
        )
        for pred in run_data.get("predictions", []):
            etype = pred.get("entity_type", "")
            if etype == "hall":
                continue
            hid = pred.get("hall_id")
            td = pred.get("target_date")
            if not hid or not td:
                continue

            if etype in ("machine_event", "machine_organic"):
                v12_by_hall[hid][td]["machines"].append({
                    "id": pred["entity_id"],
                    "name": pred.get("machine_name", pred["entity_id"]),
                    "score": pred["score"],
                    "rank": pred["rank"],
                    "confidence": pred["confidence"],
                    "type": etype,
                    "explanation": pred.get("explanation", []),
                    "warnings": pred.get("warnings", []),
                })
            elif etype == "tail":
                v12_by_hall[hid][td]["tails"].append({
                    "id": pred["entity_id"],
                    "score": pred["score"],
                    "explanation": pred.get("explanation", []),
                    "warnings": pred.get("warnings", []),
                })

        for hid, dates_data in v12_by_hall.items():
            if hid in halls_payload:
                v12 = {}
                for td, day_data in dates_data.items():
                    entry = {}
                    if day_data["machines"]:
                        entry["machines"] = day_data["machines"]
                    if day_data["tails"]:
                        entry["tails"] = day_data["tails"]
                    if entry:
                        v12[td] = entry
                if v12:
                    halls_payload[hid]["v1_2"] = v12

    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atlas-dir", default="../slot-atlas",
                         help="Path to the local slot-atlas project (default: ../slot-atlas)")
    parser.add_argument("--base-plain",
                         help="Reuse an already decrypted Tenjikai payload instead of rebuilding forecast rows")
    parser.add_argument("--no-free-source", action="store_true",
                         help="Skip optional machine/tail/position/unit analysis")
    parser.add_argument("--frozen-run",
                         help="Path to a frozen prediction run JSON for v1.2 data enrichment")
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
        frozen_path = pathlib.Path(args.frozen_run) if args.frozen_run else None
        enrich_with_v12(free_source, atlas_dir, frozen_path)
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
