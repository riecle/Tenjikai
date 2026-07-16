#!/usr/bin/env python3
"""Build compact free-source placement forecasts for the Tenjikai payload.

The module intentionally uses only the Python standard library and accepts
loose CSV/JSON schemas.  It looks for the following logical tables below a
Slot Atlas directory:

- machine_days: hall × date × machine daily aggregates
- tail_days: hall × date × machine-number tail aggregates
- machine_scores: hall × machine summary scores (fallback / SUMMARY layer)
- position_signals: already aggregated positional evidence
- unit_days: hall × date × unit-number daily data (optional, enables unit patterns)

The output is compact: forecasts are stored per hall and date-family rather
than duplicated for every one of the 365 forecast rows.
"""

from __future__ import annotations

import csv
import json
import math
import pathlib
import sqlite3
import re
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

TABLE_STEMS = {
    "machine_days": ("machine_days", "machine-day", "machine_daily"),
    "tail_days": ("tail_days", "tail-day", "tail_daily"),
    "machine_scores": ("machine_scores", "machine-score"),
    "position_signals": ("position_signals", "position-signal"),
    "unit_days": ("unit_days", "unit-day", "unit_daily"),
}

PATTERN_CATALOG = [
    {"id": 1, "name": "全台系", "group": "幾何", "needs": "machine_days"},
    {"id": 2, "name": "並び", "group": "幾何", "needs": "unit_days"},
    {"id": 3, "name": "末尾", "group": "幾何", "needs": "tail_days"},
    {"id": 4, "name": "ゾロ目台番", "group": "幾何", "needs": "unit_days / position_signals"},
    {"id": 5, "name": "角・カドN", "group": "幾何", "needs": "layout"},
    {"id": 6, "name": "散らし", "group": "幾何", "needs": "unit_days"},
    {"id": 7, "name": "オセロ／交互", "group": "幾何", "needs": "unit_days"},
    {"id": 8, "name": "合同", "group": "幾何", "needs": "multi_hall machine_days"},
    {"id": 9, "name": "固定位置", "group": "運用", "needs": "unit_days"},
    {"id": 10, "name": "前回除外", "group": "運用", "needs": "unit_days"},
    {"id": 11, "name": "機種ローテ", "group": "運用", "needs": "machine_days"},
    {"id": 12, "name": "凹み上げ", "group": "運用", "needs": "unit_days"},
    {"id": 13, "name": "据え置き", "group": "運用", "needs": "unit_days"},
    {"id": 14, "name": "リセ恩恵配布", "group": "運用", "needs": "reset observations"},
    {"id": 15, "name": "新台優遇／2週間後解禁", "group": "運用", "needs": "machine_days"},
]

ALIASES = {
    "hall_id": ("hall_id", "store_id", "shop_id", "hall", "id"),
    "date": ("date", "day", "d", "business_date", "target_date"),
    "machine": ("machine_name", "machine", "kishu", "model_name", "model", "name"),
    "avg_diff": (
        "avg_diff", "average_diff", "mean_diff", "avg_medals", "average_medals",
        "diff", "sama", "avg_coin", "avg_difference",
    ),
    "avg_games": ("avg_games", "average_games", "mean_games", "games", "avg_g", "game"),
    "units": ("units", "unit_count", "machine_count", "count", "installed", "n_units", "台数"),
    "selected": ("special_selected", "selected", "is_selected", "all_machine", "is_all_machine"),
    "family": ("event_type", "day_type", "family", "date_family", "special_type", "why"),
    "tail": ("tail", "machine_tail", "unit_tail", "number_tail", "末尾"),
    "z": ("z", "z_score", "zscore", "standard_score"),
    "score": ("score", "machine_score", "rating", "strength", "index"),
    "sample_n": ("sample_n", "n", "samples", "days", "sample_count"),
    "unit_no": ("unit_no", "machine_no", "unit_number", "machine_number", "number", "台番"),
    "signal_type": ("pattern_type", "signal_type", "type", "pattern", "kind"),
    "detail": ("detail", "note", "reason", "description", "memo"),
    "source": ("source", "source_type", "scope", "provenance"),
}


def _norm_key(value: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").strip().lower())


def pick(row: Mapping[str, Any], logical: str, default: Any = None) -> Any:
    normalized = {_norm_key(k): v for k, v in row.items()}
    for alias in ALIASES[logical]:
        key = _norm_key(alias)
        if key in normalized and normalized[key] not in (None, ""):
            return normalized[key]
    return default


def as_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(",", "").replace("枚", "").replace("G", "")
    text = text.replace("％", "%")
    try:
        if text.endswith("%"):
            return float(text[:-1]) / 100.0
        return float(text)
    except ValueError:
        return default


def as_int(value: Any, default: int | None = None) -> int | None:
    number = as_float(value)
    return int(round(number)) if number is not None else default


def as_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "selected", "special", "◎", "○", "yes"}:
        return True
    if text in {"0", "false", "no", "n", "none", "×", "-"}:
        return False
    return None


def parse_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    match = re.search(r"(20\d{2})\D+(\d{1,2})\D+(\d{1,2})", text)
    if match:
        try:
            return date(*map(int, match.groups())).isoformat()
        except ValueError:
            return None
    return None


def family_key(date_text: str, why: str | None = None) -> str:
    """Resolve a compact date-family used by both Python and app.js.

    Explicit labels win; ordinary/base rows stay in the generic family so a
    normal day is not accidentally treated as a special tail-day forecast.
    """
    text = str(why or "").strip()
    compact = text.replace(" ", "")
    if any(token in compact for token in ("通常", "平常", "ベース")):
        return "通常"
    if "周年" in compact:
        return "周年"
    if "月=日" in compact or "月＝日" in compact or "月と日" in compact:
        return "月=日"
    if "ゾロ目" in compact:
        return "ゾロ目"
    match = re.search(r"([0-9０-９])のつく日", compact)
    if match:
        digit = int(match.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789")))
        return f"{digit}のつく日"
    explicit = re.search(r"(?:^|[^0-9])([1-3]?\d)日(?:[^0-9]|$)", compact)
    if explicit:
        day = int(explicit.group(1))
        if day in (11, 22):
            return "ゾロ目"
        return f"{day % 10}のつく日"
    parsed = parse_date(date_text)
    if parsed:
        day = int(parsed[-2:])
        if day in (11, 22):
            return "ゾロ目"
        return f"{day % 10}のつく日"
    return "通常"


def _rows_from_json(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    if not isinstance(value, Mapping):
        return []
    for key in ("rows", "data", "items", "records", "values"):
        if isinstance(value.get(key), list):
            return [dict(item) for item in value[key] if isinstance(item, Mapping)]
    rows: list[dict[str, Any]] = []
    # Support {hall_id: [{...}, ...]} without losing the hall key.
    for hall_id, items in value.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, Mapping):
                row = dict(item)
                row.setdefault("hall_id", hall_id)
                rows.append(row)
    return rows


def load_rows(path: pathlib.Path) -> list[dict[str, Any]]:
    try:
        if path.suffix.lower() == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as fh:
                return [dict(row) for row in csv.DictReader(fh)]
        if path.suffix.lower() == ".json":
            return _rows_from_json(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeError, json.JSONDecodeError, csv.Error):
        return []
    return []


def discover_table(atlas_dir: pathlib.Path, logical_name: str) -> tuple[list[dict[str, Any]], list[str]]:
    stems = TABLE_STEMS[logical_name]
    candidates: list[pathlib.Path] = []
    for root_name in ("seed", "exports", "data", "build"):
        root = atlas_dir / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".csv", ".json"}:
                continue
            stem = path.stem.lower()
            if any(token in stem for token in stems):
                candidates.append(path)
    # Prefer canonical exact names, seed before exports, and JSON before CSV.
    candidates.sort(key=lambda p: (
        0 if p.stem.lower() == logical_name else 1,
        0 if "seed" in p.parts else 1,
        0 if p.suffix.lower() == ".json" else 1,
        len(p.parts),
    ))
    rows: list[dict[str, Any]] = []
    used: list[str] = []
    seen_paths: set[pathlib.Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen_paths:
            continue
        loaded = load_rows(path)
        if loaded:
            rows.extend(loaded)
            used.append(str(path.relative_to(atlas_dir)))
            seen_paths.add(resolved)
    return rows, used


def _safe_mean(values: Iterable[float]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.fmean(clean) if clean else None


def _sigmoid(value: float) -> float:
    value = max(-30.0, min(30.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx <= 0 or sy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sx * sy)


def _z_scores(values: Mapping[int, float]) -> dict[int, float]:
    if len(values) < 2:
        return {key: 0.0 for key in values}
    vals = list(values.values())
    mean = statistics.fmean(vals)
    sd = statistics.pstdev(vals)
    if sd <= 1e-9:
        return {key: 0.0 for key in values}
    return {key: (value - mean) / sd for key, value in values.items()}



def dedupe_rows(rows: Sequence[dict[str, Any]], fields: Sequence[str]) -> list[dict[str, Any]]:
    """Keep the last copy of mirrored seed/export rows."""
    ordered: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(field) for field in fields)
        ordered[key] = row
    return list(ordered.values())

def normalize_machine_days(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for raw in rows:
        hall_id = str(pick(raw, "hall_id", "")).strip()
        day = parse_date(pick(raw, "date"))
        machine = str(pick(raw, "machine", "")).strip()
        avg_diff = as_float(pick(raw, "avg_diff"))
        if not hall_id or not day or not machine or avg_diff is None:
            continue
        units = as_int(pick(raw, "units"))
        explicit = as_bool(pick(raw, "selected"))
        avg_games = as_float(pick(raw, "avg_games"))
        family_raw = str(pick(raw, "family", "") or "")
        selected = explicit if explicit is not None else (
            avg_diff >= 1000 and (avg_games is None or avg_games >= 2500) and (units is None or units >= 2)
        )
        out.append({
            "hall_id": hall_id,
            "date": day,
            "machine": machine,
            "avg_diff": avg_diff,
            "avg_games": avg_games,
            "units": units,
            "selected": bool(selected),
            "explicit_selected": explicit is not None,
            "family": family_key(day, family_raw),
            "source": str(pick(raw, "source", "") or ""),
        })
    return out


def normalize_tail_days(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for raw in rows:
        hall_id = str(pick(raw, "hall_id", "")).strip()
        tail = as_int(pick(raw, "tail"))
        if not hall_id or tail is None or not 0 <= tail <= 9:
            continue
        day = parse_date(pick(raw, "date"))
        avg_diff = as_float(pick(raw, "avg_diff"))
        z = as_float(pick(raw, "z"))
        if avg_diff is None and z is None:
            continue
        family_raw = str(pick(raw, "family", "") or "")
        out.append({
            "hall_id": hall_id,
            "date": day,
            "tail": tail,
            "avg_diff": avg_diff,
            "z": z,
            "n": as_int(pick(raw, "sample_n")),
            "family": family_key(day, family_raw) if day else (family_raw or "通常"),
            "source": str(pick(raw, "source", "") or ""),
        })
    return out


def normalize_machine_scores(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for raw in rows:
        hall_id = str(pick(raw, "hall_id", "")).strip()
        machine = str(pick(raw, "machine", "")).strip()
        score = as_float(pick(raw, "score"))
        avg_diff = as_float(pick(raw, "avg_diff"))
        if not hall_id or not machine or (score is None and avg_diff is None):
            continue
        out.append({
            "hall_id": hall_id,
            "machine": machine,
            "score": score if score is not None else avg_diff,
            "avg_diff": avg_diff,
            "units": as_int(pick(raw, "units")),
            "n": as_int(pick(raw, "sample_n")),
            "source": str(pick(raw, "source", "") or ""),
        })
    return out


def normalize_position_signals(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for raw in rows:
        hall_id = str(pick(raw, "hall_id", "")).strip()
        if not hall_id:
            continue
        out.append({
            "hall_id": hall_id,
            "date": parse_date(pick(raw, "date")),
            "type": str(pick(raw, "signal_type", "") or "").strip(),
            "detail": str(pick(raw, "detail", "") or "").strip(),
            "tail": as_int(pick(raw, "tail")),
            "z": as_float(pick(raw, "z")),
            "source": str(pick(raw, "source", "") or ""),
        })
    return out


def normalize_unit_days(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for raw in rows:
        hall_id = str(pick(raw, "hall_id", "")).strip()
        day = parse_date(pick(raw, "date"))
        unit_no_raw = pick(raw, "unit_no")
        diff = as_float(pick(raw, "avg_diff"))
        if not hall_id or not day or unit_no_raw in (None, "") or diff is None:
            continue
        unit_text = str(unit_no_raw).strip()
        digits = re.sub(r"\D", "", unit_text)
        if not digits:
            continue
        out.append({
            "hall_id": hall_id,
            "date": day,
            "unit_no": unit_text,
            "unit_num": int(digits),
            "diff": diff,
            "games": as_float(pick(raw, "avg_games")),
            "machine": str(pick(raw, "machine", "") or "").strip(),
            "family": family_key(day, str(pick(raw, "family", "") or "")),
        })
    return out


def machine_family_forecast(rows: Sequence[dict[str, Any]], family: str) -> dict[str, Any] | None:
    subset = list(rows) if family == "全日参考" else [row for row in rows if row["family"] == family]
    if not subset:
        return None
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_machine: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in subset:
        by_date[row["date"]].append(row)
        by_machine[row["machine"]].append(row)
    dates = sorted(by_date)
    if len(dates) < 2:
        return None

    selected_by_date: dict[str, set[str]] = {
        d: {row["machine"] for row in items if row["selected"]}
        for d, items in by_date.items()
    }
    selected_dates = sum(bool(items) for items in selected_by_date.values())
    all_machine_rate = selected_dates / len(dates)

    transitions = []
    repeated = 0
    opportunities = 0
    for prev, cur in zip(dates, dates[1:]):
        prev_set, cur_set = selected_by_date[prev], selected_by_date[cur]
        if not prev_set:
            continue
        opportunities += len(prev_set)
        repeated += len(prev_set & cur_set)
        transitions.append(len(prev_set & cur_set) / max(1, len(prev_set | cur_set)))
    repeat_rate = repeated / opportunities if opportunities else None
    if repeat_rate is None:
        rotation_label = "判定保留"
    elif repeat_rate <= 0.30:
        rotation_label = "ローテ型"
    elif repeat_rate >= 0.60:
        rotation_label = "再登場型"
    else:
        rotation_label = "混合型"

    latest_date = dates[-1]
    latest_selected = selected_by_date[latest_date]
    machine_candidates = []
    for machine, items in by_machine.items():
        selected_items = [row for row in items if row["selected"]]
        if not selected_items and len(items) < 3:
            continue
        selected_count = len(selected_items)
        frequency = (selected_count + 1.0) / (len(dates) + 4.0)
        strength_mean = _safe_mean(row["avg_diff"] for row in selected_items) or _safe_mean(row["avg_diff"] for row in items) or 0.0
        strength = _sigmoid((strength_mean - 450.0) / 850.0)
        positive_rate = sum(row["avg_diff"] > 0 for row in items) / max(1, len(items))
        units_values = [row["units"] for row in items if row["units"] is not None]
        units = int(round(statistics.median(units_values))) if units_values else None
        score = 0.52 * frequency + 0.30 * strength + 0.18 * positive_rate
        if rotation_label == "ローテ型" and machine in latest_selected:
            score *= 0.58
        elif rotation_label == "再登場型" and machine in latest_selected:
            score *= 1.10
        if units is not None:
            if 3 <= units <= 6:
                score *= 1.10
            elif units >= 12:
                score *= 0.90
        score = max(0.0, min(0.95, score))
        reason_bits = [f"選抜{selected_count}/{len(dates)}回"]
        if machine in latest_selected and rotation_label == "ローテ型":
            reason_bits.append("前回選抜のため減点")
        if units is not None and 3 <= units <= 6:
            reason_bits.append("少数台補正")
        machine_candidates.append({
            "name": machine,
            "score": round(score * 100, 1),
            "selected_n": selected_count,
            "days_n": len(dates),
            "units": units,
            "avg_selected_diff": round(strength_mean, 1),
            "last_selected": max((row["date"] for row in selected_items), default=None),
            "reason": "・".join(reason_bits),
        })
    machine_candidates.sort(key=lambda row: (row["score"], row["selected_n"], row["avg_selected_diff"]), reverse=True)

    return {
        "family": family,
        "date_n": len(dates),
        "selected_date_n": selected_dates,
        "all_machine_rate": round(all_machine_rate * 100, 1),
        "all_machine_label": "強い" if all_machine_rate >= 0.70 else "候補" if all_machine_rate >= 0.35 else "弱い",
        "rotation_label": rotation_label,
        "repeat_rate": round(repeat_rate * 100, 1) if repeat_rate is not None else None,
        "transition_jaccard": round(statistics.fmean(transitions) * 100, 1) if transitions else None,
        "latest_observed_date": latest_date,
        "latest_selected": sorted(latest_selected),
        "machines": machine_candidates[:6],
    }


def tail_family_forecast(rows: Sequence[dict[str, Any]], family: str) -> list[dict[str, Any]]:
    subset = list(rows) if family == "全日参考" else [row for row in rows if row["family"] == family]
    if not subset:
        return []
    by_tail: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in subset:
        by_tail[row["tail"]].append(row)

    # Prefer true day-level averages.  If only a summary z-score is available,
    # retain it rather than manufacturing an average.
    means = {
        tail: _safe_mean(row["avg_diff"] for row in items if row["avg_diff"] is not None)
        for tail, items in by_tail.items()
    }
    numeric_means = {tail: value for tail, value in means.items() if value is not None}
    computed_z = _z_scores(numeric_means)
    result = []
    for tail, items in by_tail.items():
        explicit_z = _safe_mean(row["z"] for row in items if row["z"] is not None)
        z = explicit_z if explicit_z is not None else computed_z.get(tail)
        n_dates = len({row["date"] for row in items if row["date"]})
        if not n_dates:
            n_dates = max((row["n"] or 0 for row in items), default=0)
        result.append({
            "tail": tail,
            "z": _round(z, 2),
            "avg_diff": _round(means.get(tail), 1),
            "n": n_dates or None,
            "grade": "◎" if z is not None and z >= 2.0 else "○" if z is not None and z >= 1.0 else "△" if z is not None and z >= 0 else "—",
        })
    result.sort(key=lambda row: (row["z"] if row["z"] is not None else -999, row["avg_diff"] if row["avg_diff"] is not None else -999), reverse=True)
    return result[:5]


def summary_machine_candidates(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        score = row["score"] or 0.0
        # Scores vary by source.  Present a relative 0-100 index, not a probability.
        normalized = 100.0 * _sigmoid(score / 500.0) if abs(score) > 1 else max(0.0, min(100.0, score * 100.0))
        result.append({
            "name": row["machine"],
            "score": round(normalized, 1),
            "units": row["units"],
            "n": row["n"],
            "avg_diff": _round(row["avg_diff"], 1),
            "source": row["source"],
        })
    result.sort(key=lambda row: row["score"], reverse=True)
    return result[:8]


def unit_diagnostics(rows: Sequence[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Derive conservative unit-level pattern signals.

    These are deliberately labelled as signals rather than certainties.  Unit
    rows can come from Site Seven or any other daily unit-number source.
    """
    if len(rows) < 20:
        return {}
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_unit: dict[int, dict[str, float]] = defaultdict(dict)
    for row in rows:
        by_date[row["date"]].append(row)
        by_unit[row["unit_num"]][row["date"]] = row["diff"]
    dates = sorted(by_date)
    if len(dates) < 2:
        return {}

    high_by_date: dict[str, set[int]] = {}
    for day, items in by_date.items():
        diffs = [item["diff"] for item in items]
        if len(diffs) < 3:
            continue
        threshold = max(800.0, statistics.fmean(diffs) + statistics.pstdev(diffs))
        high_by_date[day] = {item["unit_num"] for item in items if item["diff"] >= threshold}

    adjacent_x: list[float] = []
    adjacent_y: list[float] = []
    lag2_x: list[float] = []
    lag2_y: list[float] = []
    double_hits = 0
    high_total = 0
    for day, items in by_date.items():
        values = {item["unit_num"]: 1.0 if item["unit_num"] in high_by_date.get(day, set()) else 0.0 for item in items}
        for unit in sorted(values):
            if unit + 1 in values:
                adjacent_x.append(values[unit]); adjacent_y.append(values[unit + 1])
            if unit + 2 in values:
                lag2_x.append(values[unit]); lag2_y.append(values[unit + 2])
            if values[unit] and len(str(unit)) >= 2 and str(unit)[-1] == str(unit)[-2]:
                double_hits += 1
            if values[unit]:
                high_total += 1
    adjacent_phi = _pearson(adjacent_x, adjacent_y)
    lag2_phi = _pearson(lag2_x, lag2_y)

    overlaps = []
    prev_exclusion_rates = []
    ordered_high_dates = sorted(high_by_date)
    for prev, cur in zip(ordered_high_dates, ordered_high_dates[1:]):
        a, b = high_by_date[prev], high_by_date[cur]
        if not a:
            continue
        overlaps.append(len(a & b) / max(1, len(a | b)))
        prev_exclusion_rates.append(len(a - b) / len(a))
    overlap = statistics.fmean(overlaps) if overlaps else None
    exclusion = statistics.fmean(prev_exclusion_rates) if prev_exclusion_rates else None

    dent_cases = dent_hits = carry_cases = carry_hits = 0
    for unit, day_map in by_unit.items():
        unit_dates = sorted(day_map)
        for prev, cur in zip(unit_dates, unit_dates[1:]):
            try:
                consecutive = date.fromisoformat(cur) - date.fromisoformat(prev) == timedelta(days=1)
            except ValueError:
                consecutive = False
            if not consecutive:
                continue
            prev_diff, cur_diff = day_map[prev], day_map[cur]
            if prev_diff <= -500:
                dent_cases += 1
                dent_hits += cur_diff >= 800
            if prev_diff >= 800:
                carry_cases += 1
                carry_hits += cur_diff >= 800
    dent_rate = dent_hits / dent_cases if dent_cases >= 5 else None
    carry_rate = carry_hits / carry_cases if carry_cases >= 5 else None

    result: dict[int, dict[str, Any]] = {}
    if adjacent_phi is not None:
        result[2] = {
            "status": "兆候" if adjacent_phi >= 0.15 else "未検出",
            "strength": _round(adjacent_phi, 3),
            "detail": f"隣接φ={adjacent_phi:+.3f}",
        }
        result[6] = {
            "status": "兆候" if adjacent_phi <= -0.12 else "未検出",
            "strength": _round(-adjacent_phi, 3),
            "detail": f"隣接φ={adjacent_phi:+.3f}（負側が散らし）",
        }
    if adjacent_phi is not None and lag2_phi is not None:
        othello = adjacent_phi <= -0.08 and lag2_phi >= 0.10
        result[7] = {
            "status": "兆候" if othello else "未検出",
            "strength": _round(lag2_phi - adjacent_phi, 3),
            "detail": f"lag1={adjacent_phi:+.3f} / lag2={lag2_phi:+.3f}",
        }
    if high_total >= 10:
        rate = double_hits / high_total
        result[4] = {
            "status": "兆候" if rate >= 0.18 else "未検出",
            "strength": _round(rate, 3),
            "detail": f"高差枚台のゾロ目比率{rate*100:.1f}%（n={high_total}）",
        }
    if overlap is not None:
        result[9] = {
            "status": "兆候" if overlap >= 0.30 else "未検出",
            "strength": _round(overlap, 3),
            "detail": f"イベ日高差枚位置Jaccard={overlap:.2f}",
        }
    if exclusion is not None:
        result[10] = {
            "status": "兆候" if exclusion >= 0.75 and (overlap or 0) <= 0.15 else "未検出",
            "strength": _round(exclusion, 3),
            "detail": f"前回高差枚位置の除外率{exclusion*100:.1f}%",
        }
    if dent_rate is not None:
        result[12] = {
            "status": "兆候" if dent_rate >= 0.25 else "未検出",
            "strength": _round(dent_rate, 3),
            "detail": f"前日-500枚以下→翌日+800枚率{dent_rate*100:.1f}%（n={dent_cases}）",
        }
    if carry_rate is not None:
        result[13] = {
            "status": "兆候" if carry_rate >= 0.25 else "未検出",
            "strength": _round(carry_rate, 3),
            "detail": f"前日+800枚→翌日+800枚率{carry_rate*100:.1f}%（n={carry_cases}）",
        }
    return result


def new_machine_diagnostic(rows: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    by_machine: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_machine[row["machine"]].append(row)
    early: list[float] = []
    late: list[float] = []
    for items in by_machine.values():
        items = sorted(items, key=lambda row: row["date"])
        first = date.fromisoformat(items[0]["date"])
        for row in items:
            age = (date.fromisoformat(row["date"]) - first).days
            if age <= 13:
                early.append(1.0 if row["selected"] else 0.0)
            elif 14 <= age <= 41:
                late.append(1.0 if row["selected"] else 0.0)
    if len(early) < 10 or len(late) < 10:
        return None
    early_rate, late_rate = statistics.fmean(early), statistics.fmean(late)
    delta = early_rate - late_rate
    if delta >= 0.08:
        label = "新台直後優遇"
    elif delta <= -0.08:
        label = "2週間後解禁"
    else:
        label = "差なし"
    return {
        "status": "兆候" if abs(delta) >= 0.08 else "未検出",
        "strength": round(abs(delta), 3),
        "detail": f"導入0-13日{early_rate*100:.1f}% / 14-41日{late_rate*100:.1f}%（{label}）",
    }


def joint_diagnostics(all_machine_rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    selected: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in all_machine_rows:
        if row["selected"]:
            selected[(row["date"], row["machine"])].add(row["hall_id"])
    per_hall_count: dict[str, int] = defaultdict(int)
    examples: dict[str, list[str]] = defaultdict(list)
    for (day, machine), halls in selected.items():
        if len(halls) < 2:
            continue
        for hall in halls:
            per_hall_count[hall] += 1
            if len(examples[hall]) < 3:
                examples[hall].append(f"{day} {machine}（{len(halls)}店）")
    return {
        hall: {
            "status": "兆候" if count >= 2 else "参考",
            "strength": count,
            "detail": f"同日同機種の複数店選抜 {count}件" + (" / " + "、".join(examples[hall]) if examples[hall] else ""),
        }
        for hall, count in per_hall_count.items()
    }


def build_pattern_ledger(
    hall_id: str,
    family_forecast: Mapping[str, Any] | None,
    tails: Sequence[Mapping[str, Any]],
    position_rows: Sequence[dict[str, Any]],
    unit_signals: Mapping[int, Mapping[str, Any]],
    joint_signal: Mapping[str, Any] | None,
    new_machine_signal: Mapping[str, Any] | None,
    has_machine: bool,
    has_tail: bool,
    has_unit: bool,
) -> list[dict[str, Any]]:
    patterns: dict[int, dict[str, Any]] = {}
    if family_forecast:
        rate = float(family_forecast.get("all_machine_rate") or 0.0)
        patterns[1] = {
            "status": "検出" if rate >= 70 else "兆候" if rate >= 35 else "未検出",
            "strength": round(rate / 100.0, 3),
            "detail": f"同族日{family_forecast['selected_date_n']}/{family_forecast['date_n']}日で選抜機種あり",
        }
        repeat = family_forecast.get("repeat_rate")
        patterns[11] = {
            "status": "検出" if family_forecast.get("rotation_label") in {"ローテ型", "再登場型"} and repeat is not None else "判定保留",
            "strength": round(abs(50.0 - float(repeat)) / 50.0, 3) if repeat is not None else None,
            "detail": f"{family_forecast.get('rotation_label')} / 再登場率{repeat}%" if repeat is not None else "系列不足",
        }
    elif has_machine:
        patterns[1] = {"status": "データ不足", "strength": None, "detail": "machine_daysはあるが同族日n不足"}
        patterns[11] = {"status": "データ不足", "strength": None, "detail": "machine_daysはあるが系列不足"}

    if tails:
        best = tails[0]
        z = best.get("z")
        patterns[3] = {
            "status": "検出" if z is not None and z >= 2 else "兆候" if z is not None and z >= 1 else "未検出",
            "strength": _round(float(z) / 3.0, 3) if z is not None else None,
            "detail": f"末尾{best['tail']} z={z:+.2f}" if z is not None else f"末尾{best['tail']}（zなし）",
        }
    elif has_tail:
        patterns[3] = {"status": "データ不足", "strength": None, "detail": "tail_daysはあるが同族日n不足"}

    pattern_text = " ".join((row.get("type", "") + " " + row.get("detail", "")) for row in position_rows).lower()
    if any(token in pattern_text for token in ("ゾロ", "double", "11", "22", "33")) and 4 not in unit_signals:
        patterns[4] = {"status": "兆候", "strength": None, "detail": "position_signalsにゾロ目系記録あり"}

    for pattern_id, signal in unit_signals.items():
        patterns[pattern_id] = dict(signal)
    if joint_signal:
        patterns[8] = dict(joint_signal)
    if new_machine_signal:
        patterns[15] = dict(new_machine_signal)

    result = []
    for item in PATTERN_CATALOG:
        pid = item["id"]
        signal = patterns.get(pid)
        if signal is None:
            if pid == 5:
                signal = {"status": "現地観測", "strength": None, "detail": "島境界・レイアウト未接続"}
            elif pid == 14:
                signal = {"status": "現地観測", "strength": None, "detail": "朝一リセット観測が必要"}
            elif item["needs"].startswith("unit_days") and not has_unit:
                signal = {"status": "ローカル専用", "strength": None, "detail": "有料ソース由来のため本サイト非掲載。ローカルのunit_reportで検定"}
            elif pid in (1, 11, 15) and not has_machine:
                signal = {"status": "データなし", "strength": None, "detail": "machine_days未接続"}
            elif pid == 3 and not has_tail:
                signal = {"status": "データなし", "strength": None, "detail": "tail_days未接続"}
            else:
                signal = {"status": "未検定", "strength": None, "detail": item["needs"] + "が必要"}
        result.append({**item, **signal})
    return result


def load_db_tables(atlas_dir: pathlib.Path, *, include_unit: bool) -> dict[str, list[dict[str, Any]]]:
    """Read optional DB tables without assuming every historical column exists.

    Each logical table is loaded independently.  A missing optional column or
    table must not discard successfully loaded machine/tail data from the same
    DB.  Missing values remain ``None`` and are handled by the normalizers.
    """
    db = atlas_dir / "slot_atlas.db"
    out: dict[str, list[dict[str, Any]]] = {}
    if not db.exists():
        return out

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    def table_exists(name: str) -> bool:
        return bool(con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone())

    def get(row: sqlite3.Row, *names: str, default: Any = None) -> Any:
        keys = set(row.keys())
        for name in names:
            if name in keys:
                return row[name]
        return default

    try:
        if table_exists("machine_days"):
            rows = []
            for r in con.execute("SELECT * FROM machine_days"):
                units = get(r, "units", "total_units")
                rows.append({
                    "hall_id": get(r, "hall_id"),
                    "date": get(r, "result_date", "business_date", "date"),
                    "machine_name": get(r, "machine_name", "machine_key", "machine_id"),
                    "avg_diff": get(r, "avg_diff", "diff"),
                    "units": units,
                    "avg_games": get(r, "avg_games", "games"),
                    "special_selected": get(
                        r, "selected_flag", "event_selected_label", default=0
                    ),
                    "winning_units": get(r, "winning_units"),
                    "total_units": get(r, "total_units", default=units),
                })
            out["machine_days"] = rows

        if table_exists("tail_days"):
            rows = []
            for r in con.execute("SELECT * FROM tail_days"):
                units = get(r, "units", "total_units")
                rows.append({
                    "hall_id": get(r, "hall_id"),
                    "date": get(r, "result_date", "business_date", "date"),
                    "tail": get(r, "tail_key", "tail"),
                    "avg_diff": get(r, "avg_diff", "diff"),
                    "winning_units": get(r, "winning_units"),
                    "total_units": get(r, "total_units", default=units),
                    "avg_games": get(r, "avg_games", "games"),
                })
            out["tail_days"] = rows

        if table_exists("machine_scores"):
            out["machine_scores"] = [
                {
                    "hall_id": get(r, "hall_id"),
                    "machine_name": get(r, "machine_name", "machine_key"),
                    "units": get(r, "units"),
                    "score": get(r, "composite_score", "score"),
                    "avg_diff": get(r, "baseline_avg_diff", "avg_diff"),
                    "special_selected": get(r, "special_selected_n", "special_selected"),
                    "source": get(r, "source_name", default="db"),
                    "notes": get(r, "notes", default=""),
                }
                for r in con.execute("SELECT * FROM machine_scores")
            ]

        if table_exists("position_signals"):
            out["position_signals"] = [
                {
                    "hall_id": get(r, "hall_id"),
                    "date": get(r, "result_date", "date"),
                    "pattern_type": get(r, "event_name", "pattern_type", "type"),
                    "detail": get(r, "notes", "detail", default=""),
                    "machine_name": get(r, "machine_name"),
                    "avg_diff": get(r, "avg_diff"),
                }
                for r in con.execute("SELECT * FROM position_signals")
            ]

        if include_unit and table_exists("unit_days"):
            out["unit_days"] = [
                {
                    "hall_id": get(r, "hall_id"),
                    "date": get(r, "result_date", "business_date", "date"),
                    "unit_no": get(r, "unit_no"),
                    "diff": get(r, "diff", "avg_diff"),
                    "machine_name": get(r, "machine_name", "machine_id"),
                }
                for r in con.execute("SELECT * FROM unit_days")
            ]
    finally:
        con.close()

    return {k: v for k, v in out.items() if v}


def build_free_source_payload(atlas_dir: pathlib.Path, candidate_rows: Sequence[Mapping[str, Any]], *, include_unit: bool = False) -> dict[str, Any]:
    raw_tables: dict[str, list[dict[str, Any]]] = {}
    source_files: dict[str, list[str]] = {}
    for logical in TABLE_STEMS:
        if logical == "unit_days" and not include_unit:
            # ポリシー: 有料ソース(サイトセブン)由来の台番日次はローカル限定。vault/サイトへは載せない。
            raw_tables[logical], source_files[logical] = [], ["<policy-excluded: local-only>"]
        else:
            raw_tables[logical], source_files[logical] = discover_table(atlas_dir, logical)

    for logical, db_rows in load_db_tables(atlas_dir, include_unit=include_unit).items():
        raw_tables[logical] = db_rows
        source_files[logical] = [f"<slot_atlas.db:{logical} {len(db_rows)}行>"]

    machine_days = dedupe_rows(normalize_machine_days(raw_tables["machine_days"]), ("hall_id", "date", "machine"))
    tail_days = dedupe_rows(normalize_tail_days(raw_tables["tail_days"]), ("hall_id", "date", "tail", "family"))
    machine_scores = dedupe_rows(normalize_machine_scores(raw_tables["machine_scores"]), ("hall_id", "machine", "source"))
    position_signals = dedupe_rows(normalize_position_signals(raw_tables["position_signals"]), ("hall_id", "date", "type", "detail"))
    unit_days = dedupe_rows(normalize_unit_days(raw_tables["unit_days"]), ("hall_id", "date", "unit_num"))

    candidate_halls = {str(row.get("id") or row.get("hall_id") or "") for row in candidate_rows}
    candidate_halls.discard("")
    observed_halls = {
        row["hall_id"]
        for table in (machine_days, tail_days, machine_scores, position_signals, unit_days)
        for row in table
    }
    hall_ids = sorted(candidate_halls | observed_halls)

    machine_by_hall: dict[str, list[dict[str, Any]]] = defaultdict(list)
    tail_by_hall: dict[str, list[dict[str, Any]]] = defaultdict(list)
    score_by_hall: dict[str, list[dict[str, Any]]] = defaultdict(list)
    position_by_hall: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unit_by_hall: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in machine_days: machine_by_hall[row["hall_id"]].append(row)
    for row in tail_days: tail_by_hall[row["hall_id"]].append(row)
    for row in machine_scores: score_by_hall[row["hall_id"]].append(row)
    for row in position_signals: position_by_hall[row["hall_id"]].append(row)
    for row in unit_days: unit_by_hall[row["hall_id"]].append(row)

    joint_by_hall = joint_diagnostics(machine_days)
    halls_payload: dict[str, Any] = {}
    for hall_id in hall_ids:
        mrows = machine_by_hall[hall_id]
        trows = tail_by_hall[hall_id]
        srows = score_by_hall[hall_id]
        prows = position_by_hall[hall_id]
        urows = unit_by_hall[hall_id]
        machine_dates = len({row["date"] for row in mrows})
        tail_dates = len({row["date"] for row in trows if row["date"]})
        if machine_dates >= 5 and (tail_dates >= 3 or len(trows) >= 10):
            layer = "FULL"
        elif mrows or srows or trows or prows:
            layer = "SUMMARY"
        else:
            layer = "NONE"

        unit_signals = unit_diagnostics(urows)
        new_machine_signal = new_machine_diagnostic(mrows) if mrows else None
        families = {"全日参考"}
        families.update(row["family"] for row in mrows)
        families.update(row["family"] for row in trows)
        family_payload: dict[str, Any] = {}
        for family in sorted(families):
            machine_forecast = machine_family_forecast(mrows, family) if mrows else None
            tails = tail_family_forecast(trows, family) if trows else []
            if family != "通常" and not machine_forecast and not tails:
                continue
            patterns = build_pattern_ledger(
                hall_id=hall_id,
                family_forecast=machine_forecast,
                tails=tails,
                position_rows=prows,
                unit_signals=unit_signals,
                joint_signal=joint_by_hall.get(hall_id),
                new_machine_signal=new_machine_signal,
                has_machine=bool(mrows),
                has_tail=bool(trows),
                has_unit=bool(urows),
            )
            family_payload[family] = {
                "machine": machine_forecast,
                "tails": tails,
                "patterns": patterns,
            }

        warnings = []
        if layer == "SUMMARY" and srows and not mrows:
            warnings.append("機種スコアは結果後ハイライト由来を含み得るため、着席前確率として扱わない")
        if not urows:
            warnings.append("台番検定は非掲載（有料ソース由来はローカル限定。unit_report/bonus-testsで確認）")
        if not trows:
            warnings.append("tail_days未接続：末尾の無料検定なし")
        if not mrows:
            warnings.append("machine_days未接続：全台系・機種ローテの無料検定なし")

        halls_payload[hall_id] = {
            "layer": layer,
            "counts": {
                "machine_days": len(mrows),
                "machine_dates": machine_dates,
                "tail_days": len(trows),
                "tail_dates": tail_dates,
                "machine_scores": len(srows),
                "position_signals": len(prows),
                "unit_days": len(urows),
                "unit_dates": len({row["date"] for row in urows}),
            },
            "families": family_payload,
            "summary_machines": summary_machine_candidates(srows),
            "warnings": warnings,
        }

    return {
        "v": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "pattern_catalog": PATTERN_CATALOG,
        "source_files": source_files,
        "table_counts": {
            "machine_days": len(machine_days),
            "tail_days": len(tail_days),
            "machine_scores": len(machine_scores),
            "position_signals": len(position_signals),
            "unit_days": len(unit_days),
        },
        "halls": halls_payload,
        "contracts": {
            "machine_days": ["hall_id", "date", "machine_name", "avg_diff", "units?", "avg_games?", "special_selected?"],
            "tail_days": ["hall_id", "date", "tail", "avg_diff or z"],
            "machine_scores": ["hall_id", "machine_name", "score or avg_diff"],
            "position_signals": ["hall_id", "date?", "pattern_type", "detail?"],
        },
    }
