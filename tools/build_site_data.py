#!/usr/bin/env python3
"""Convert slot-atlas exports into the compact JSON payloads used by index.html.

Run after slot-atlas/exports/forecast_candidates_365.csv is regenerated:

    python3 tools/build_site_data.py
"""

from __future__ import annotations

import csv
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
ATLAS = ROOT / "slot-atlas"
CANDIDATES_CSV = ATLAS / "exports" / "forecast_candidates_365.csv"
HALLS_JSON = ATLAS / "seed" / "halls.json"
SLOT_ATLAS_PY = ATLAS / "slot_atlas.py"
OUT_CANDIDATES = ROOT / "data" / "candidates.json"
OUT_META = ROOT / "data" / "meta.json"


def load_candidates() -> list[dict]:
    rows = []
    with CANDIDATES_CSV.open(encoding="utf-8-sig", newline="") as fh:
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


def load_model_version() -> str:
    text = SLOT_ATLAS_PY.read_text(encoding="utf-8")
    match = re.search(r'MODEL_VERSION\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "unknown"


def build_meta(rows: list[dict]) -> dict:
    halls = json.loads(HALLS_JSON.read_text(encoding="utf-8"))
    dates = sorted({row["d"] for row in rows})
    return {
        "model_version": load_model_version(),
        "as_of": dates[0] if dates else None,
        "date_range": [dates[0], dates[-1]] if dates else None,
        "hall_count": len(halls),
        "active_hall_count": sum(1 for h in halls if h.get("active")),
        "forecast_enabled_count": sum(1 for h in halls if h.get("active") and h.get("forecast_enabled", True)),
        "markets": sorted({h["market"] for h in halls}),
        "row_count": len(rows),
    }


def main() -> None:
    rows = load_candidates()
    OUT_CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    OUT_CANDIDATES.write_text(payload, encoding="utf-8")
    print(f"wrote {OUT_CANDIDATES} ({len(rows)} rows, {OUT_CANDIDATES.stat().st_size:,} bytes)")

    meta = build_meta(rows)
    OUT_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT_META}")
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
