#!/usr/bin/env python3
"""Slot Atlas: deterministic hall-day evidence store and forecast engine."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import pathlib
import sqlite3
from typing import Any, Iterable

ROOT = pathlib.Path(__file__).resolve().parent
MODEL_VERSION = "slot-atlas-0.11.3"
RANK_ORDER = {"NO BET": 0, "C": 1, "B": 2, "A": 3, "S": 4}
TRAVEL_FREE_MINUTES = 10
TRAVEL_PENALTY_PER_MINUTE = 1.0


def read_json(path: pathlib.Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def iso_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def nth_weekday(date: dt.date) -> int:
    return (date.day - 1) // 7 + 1


def is_month_end(date: dt.date) -> bool:
    return (date + dt.timedelta(days=1)).month != date.month


def rule_matches(match: dict[str, Any], date: dt.date) -> bool:
    if "month" in match and date.month != int(match["month"]):
        return False
    if "day" in match and date.day != int(match["day"]):
        return False
    if "day_in" in match and date.day not in {int(x) for x in match["day_in"]}:
        return False
    if "weekday" in match and date.weekday() != int(match["weekday"]):
        return False
    if "nth_weekday" in match and nth_weekday(date) != int(match["nth_weekday"]):
        return False
    if match.get("month_equals_day") and date.month != date.day:
        return False
    if match.get("month_end") and not is_month_end(date):
        return False
    return True


def active_for_date(rule: dict[str, Any], date: dt.date) -> bool:
    if rule.get("valid_from") and date < iso_date(rule["valid_from"]):
        return False
    if rule.get("valid_to") and date > iso_date(rule["valid_to"]):
        return False
    return True


def long_break_label(date: dt.date, holiday_cfg: dict[str, Any]) -> str | None:
    for start, end, label in holiday_cfg.get("long_break_ranges", []):
        if iso_date(start) <= date <= iso_date(end):
            return label
    return None


def confidence(sample_n: int | None, age_days: int) -> float:
    evidence = 0.25 if sample_n is None else sample_n / (sample_n + 8.0)
    freshness = math.exp(-max(0, age_days - 7) / 60.0)
    return round(max(0.08, min(0.95, evidence * freshness)), 4)


def shrink(mean: float, sample_n: int | None, baseline: float) -> float:
    if sample_n is None:
        return mean
    prior_n = 4.0
    return (mean * sample_n + baseline * prior_n) / (sample_n + prior_n)


def base_rank(edge: float, conf: float) -> str:
    if edge >= 120 and conf >= 0.50:
        return "S"
    if edge >= 40 and conf >= 0.35:
        return "A"
    if edge >= 10:
        return "B"
    if edge >= 0:
        return "C"
    return "NO BET"


def downgrade(rank: str, count: int) -> str:
    order = ["NO BET", "C", "B", "A", "S"]
    idx = max(0, order.index(rank) - count)
    return order[idx]


def forecast_one(
    hall: dict[str, Any],
    rules: list[dict[str, Any]],
    date: dt.date,
    run_date: dt.date,
    holiday_cfg: dict[str, Any],
) -> dict[str, Any]:
    travel_minutes = hall.get("travel_minutes")
    travel_penalty = (
        max(0.0, float(travel_minutes) - TRAVEL_FREE_MINUTES) * TRAVEL_PENALTY_PER_MINUTE
        if travel_minutes is not None else 0.0
    )
    if not hall.get("forecast_enabled", True) or hall.get("baseline_mean") is None:
        return {
            "date": date.isoformat(),
            "hall_id": hall["hall_id"],
            "hall_name": hall["name"],
            "market": hall["market"],
            "rule_id": None,
            "reason": "監視中（予測未解禁）",
            "raw_mean": 0.0,
            "predicted_mean": 0.0,
            "decision_floor": hall.get("decision_floor", 0),
            "adjusted_edge": -999.0,
            "utility_edge": -999.0,
            "travel_minutes": travel_minutes,
            "travel_penalty": round(travel_penalty, 1),
            "sample_n": 0,
            "confidence": 0.0,
            "rank": "NO BET",
            "risk_flags": ["日次n不足"],
            "data_through": "",
            "data_age_days": 0,
            "stale_warning": hall.get("forecast_block_reason", "公開日次データ不足"),
            "forecast_horizon_days": max(0, (date - run_date).days),
            "horizon_warning": None,
        }
    matching = [
        r for r in rules
        if r["hall_id"] == hall["hall_id"]
        and r.get("status", "active") == "active"
        and float(r.get("sample_n") or 0) > 0  # v0.10.9: n=0プレースホルダは発火させない
        and active_for_date(r, date)
        and rule_matches(r["match"], date)
    ]
    matching.sort(key=lambda r: (int(r["priority"]), int(r.get("sample_n") or -1)), reverse=True)
    primary = matching[0] if matching else None
    raw_mean = float(primary["mean_diff"] if primary else hall["baseline_mean"])
    sample_n = primary.get("sample_n") if primary else hall.get("baseline_n")
    if primary:
        # v0.7: regime-separated day types (proven anti-correlated with the
        # normal-day regime, e.g. strong 月内ゼロサム halls) shrink toward 0,
        # not toward the hall baseline, so a negative baseline cannot erase
        # a verified positive event signal.
        shrink_target = 0.0 if primary.get("regime_separated") else float(hall["baseline_mean"])
        predicted = shrink(raw_mean, sample_n, shrink_target)
    else:
        predicted = raw_mean
    data_through = iso_date(primary["data_through"] if primary else hall["data_through"])
    age_days = max(0, (run_date - data_through).days)
    horizon_days = max(0, (date - run_date).days)
    horizon_factor = math.exp(-max(0, horizon_days - 31) / 240.0)
    conf = round(confidence(sample_n, age_days) * horizon_factor, 4)
    edge = predicted - float(hall.get("decision_floor", 0))
    utility_edge = edge - travel_penalty
    rank = base_rank(utility_edge, conf)

    contexts = hall.get("context_downgrades", {})
    risks: list[str] = []
    if date.weekday() == 6 and contexts.get("sunday"):
        risks.append("日曜")
    if date.weekday() == 5 and contexts.get("saturday"):
        risks.append("土曜")
    if date.weekday() == 4 and not primary and contexts.get("friday_normal"):
        risks.append("通常金曜")
    holidays = set(holiday_cfg.get("holidays", []))
    if date.isoformat() in holidays and contexts.get("holiday"):
        risks.append("祝日")
    break_label = long_break_label(date, holiday_cfg)
    if break_label and contexts.get("long_break"):
        risks.append(break_label)
    rank = downgrade(rank, min(2, len(risks)))

    stale_warning = None
    if age_days > 21:
        stale_warning = f"データ{age_days}日遅延"
        rank = downgrade(rank, 1)

    horizon_warning = None
    if horizon_days > 180:
        horizon_warning = "180日超のテンプレ予測"
        rank = downgrade(rank, 2)
    elif horizon_days > 90:
        horizon_warning = "90日超のテンプレ予測"
        rank = downgrade(rank, 1)

    reason = primary["label"] if primary else "通常ベース"
    return {
        "date": date.isoformat(),
        "hall_id": hall["hall_id"],
        "hall_name": hall["name"],
        "market": hall["market"],
        "rule_id": primary["rule_id"] if primary else None,
        "reason": reason,
        "raw_mean": round(raw_mean, 1),
        "predicted_mean": round(predicted, 1),
        "decision_floor": hall.get("decision_floor", 0),
        "adjusted_edge": round(edge, 1),
        "utility_edge": round(utility_edge, 1),
        "travel_minutes": travel_minutes,
        "travel_penalty": round(travel_penalty, 1),
        "sample_n": sample_n,
        "confidence": conf,
        "rank": rank,
        "risk_flags": risks,
        "data_through": data_through.isoformat(),
        "data_age_days": age_days,
        "stale_warning": stale_warning,
        "forecast_horizon_days": horizon_days,
        "horizon_warning": horizon_warning,
    }


def select_best(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = list(rows)
    values.sort(key=lambda r: (RANK_ORDER[r["rank"]], r["utility_edge"], r["confidence"]), reverse=True)
    best = dict(values[0])
    if best["rank"] in {"NO BET", "C"}:
        best["selection"] = "見送り"
        best["relative_best_hall"] = best["hall_name"]
    else:
        best["selection"] = best["hall_name"]
        best["relative_best_hall"] = best["hall_name"]
    return best


def init_db(db_path: pathlib.Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
    # v0.5 migration for databases created before forecast state was persisted.
    hall_columns = {row[1] for row in con.execute("PRAGMA table_info(halls)")}
    if "forecast_enabled" not in hall_columns:
        con.execute(
            "ALTER TABLE halls ADD COLUMN forecast_enabled INTEGER NOT NULL DEFAULT 1 "
            "CHECK (forecast_enabled IN (0, 1))"
        )
    if "forecast_block_reason" not in hall_columns:
        con.execute("ALTER TABLE halls ADD COLUMN forecast_block_reason TEXT")
    for column, ddl in {
        "travel_origin": "TEXT",
        "travel_minutes": "INTEGER",
        "travel_status": "TEXT",
        "travel_source_url": "TEXT",
    }.items():
        if column not in hall_columns:
            con.execute(f"ALTER TABLE halls ADD COLUMN {column} {ddl}")
    # v0.7 migration: rule-level regime separation flag.
    rule_columns = {row[1] for row in con.execute("PRAGMA table_info(evidence_rules)")}
    if "regime_separated" not in rule_columns:
        con.execute(
            "ALTER TABLE evidence_rules ADD COLUMN regime_separated INTEGER NOT NULL DEFAULT 0 "
            "CHECK (regime_separated IN (0, 1))"
        )
    prediction_columns = {row[1] for row in con.execute("PRAGMA table_info(predictions)")}
    if "utility_edge" not in prediction_columns:
        con.execute("ALTER TABLE predictions ADD COLUMN utility_edge REAL NOT NULL DEFAULT 0")
    if "travel_minutes" not in prediction_columns:
        con.execute("ALTER TABLE predictions ADD COLUMN travel_minutes INTEGER")
    if "travel_penalty" not in prediction_columns:
        con.execute("ALTER TABLE predictions ADD COLUMN travel_penalty REAL NOT NULL DEFAULT 0")
    con.commit()
    return con


def seed_db(con: sqlite3.Connection, halls: list[dict[str, Any]], rules: list[dict[str, Any]]) -> None:
    for hall in halls:
        con.execute(
            """INSERT OR REPLACE INTO halls
            (hall_id, market, name, active, forecast_enabled, forecast_block_reason,
             slot_count, exchange_label, decision_floor, travel_origin, travel_minutes,
             travel_status, travel_source_url, grand_open_date, baseline_mean,
             baseline_n, data_through, source_kind, source_url, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                hall["hall_id"], hall["market"], hall["name"], int(hall["active"]),
                int(hall.get("forecast_enabled", True)), hall.get("forecast_block_reason"),
                hall.get("slot_count"), hall.get("exchange_label"), hall.get("decision_floor", 0),
                hall.get("travel_origin"), hall.get("travel_minutes"), hall.get("travel_status"),
                hall.get("travel_source_url"),
                hall.get("grand_open_date"), hall.get("baseline_mean"), hall.get("baseline_n"),
                hall.get("data_through"), hall["source_kind"], hall.get("source_url"), hall.get("notes"),
            ),
        )
    for rule in rules:
        con.execute(
            """INSERT OR REPLACE INTO evidence_rules
            (rule_id, hall_id, label, priority, match_json, mean_diff, sample_n, positive_rate,
             valid_from, valid_to, data_through, source_kind, source_url, status, notes,
             regime_separated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rule["rule_id"], rule["hall_id"], rule["label"], rule["priority"],
                json.dumps(rule["match"], ensure_ascii=False, sort_keys=True), rule["mean_diff"],
                rule.get("sample_n"), rule.get("positive_rate"), rule.get("valid_from"), rule.get("valid_to"),
                rule["data_through"], "supplied_or_computed_report", rule.get("source_url"),
                rule.get("status", "active"), rule.get("notes"),
                int(bool(rule.get("regime_separated", False))),
            ),
        )
    con.commit()


def write_csv(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            cooked = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()}
            writer.writerow(cooked)


def import_hall_days(con: sqlite3.Connection, csv_path: pathlib.Path, observed_at: str) -> int:
    required = {"hall_id", "result_date", "avg_diff", "avg_games", "source_name"}
    count = 0
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing columns: {sorted(missing)}")
        for row in reader:
            con.execute(
                """INSERT OR REPLACE INTO hall_days
                (hall_id, result_date, avg_diff, total_diff, avg_games, machine_win_rate,
                 winning_units, total_units, source_name, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["hall_id"], row["result_date"], float(row["avg_diff"]),
                    float(row["total_diff"]) if row.get("total_diff") else None,
                    float(row["avg_games"]) if row.get("avg_games") else None,
                    float(row["machine_win_rate"]) if row.get("machine_win_rate") else None,
                    int(row["winning_units"]) if row.get("winning_units") else None,
                    int(row["total_units"]) if row.get("total_units") else None,
                    row["source_name"], observed_at,
                ),
            )
            count += 1
    con.commit()
    return count


def seed_hall_days(con: sqlite3.Connection, csv_path: pathlib.Path, observed_at: str) -> int:
    """Import bundled raw observations without overwriting a fresher same-source row."""
    if not csv_path.exists():
        return 0
    required = {"hall_id", "result_date", "avg_diff", "avg_games", "source_name"}
    count = 0
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing columns: {sorted(missing)}")
        for row in reader:
            cur = con.execute(
                """INSERT OR IGNORE INTO hall_days
                (hall_id, result_date, avg_diff, total_diff, avg_games, machine_win_rate,
                 winning_units, total_units, source_name, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["hall_id"], row["result_date"], float(row["avg_diff"]),
                    float(row["total_diff"]) if row.get("total_diff") else None,
                    float(row["avg_games"]) if row.get("avg_games") else None,
                    float(row["machine_win_rate"]) if row.get("machine_win_rate") else None,
                    int(row["winning_units"]) if row.get("winning_units") else None,
                    int(row["total_units"]) if row.get("total_units") else None,
                    row["source_name"], observed_at,
                ),
            )
            count += int(cur.rowcount > 0)
    con.commit()
    return count


def seed_machine_scores(con: sqlite3.Connection, csv_path: pathlib.Path) -> int:
    if not csv_path.exists():
        return 0
    count = 0
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            cur = con.execute(
                """INSERT OR IGNORE INTO machine_scores
                (hall_id, as_of_date, machine_key, machine_name, units, baseline_days,
                 baseline_avg_diff, special_selected_n, momentum_selected_n, composite_score,
                 type_label, source_name, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["hall_id"], row["as_of_date"], row["machine_key"], row["machine_name"],
                    int(row["units"]) if row.get("units") else None, int(row["baseline_days"]),
                    float(row["baseline_avg_diff"]) if row.get("baseline_avg_diff") else None,
                    int(row.get("special_selected_n") or 0),
                    int(row.get("momentum_selected_n") or 0),
                    float(row["composite_score"]) if row.get("composite_score") else None,
                    row["type_label"], row["source_name"], row.get("notes"),
                ),
            )
            count += int(cur.rowcount > 0)
    con.commit()
    return count


def seed_machine_days(con: sqlite3.Connection, csv_path: pathlib.Path) -> int:
    """Seed normalized machine-day observations while preserving source identity."""
    if not csv_path.exists():
        return 0
    count = 0
    required = {
        "hall_id", "result_date", "machine_key", "machine_name", "avg_diff", "source_name"
    }
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing machine-day columns: {sorted(missing)}")
        for row in reader:
            cur = con.execute(
                """INSERT OR IGNORE INTO machine_days
                (hall_id, result_date, machine_key, machine_name, units, avg_diff,
                 avg_games, winning_units, total_units, selected_flag, source_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["hall_id"], row["result_date"], row["machine_key"], row["machine_name"],
                    int(row["units"]) if row.get("units") else None,
                    float(row["avg_diff"]) if row.get("avg_diff") else None,
                    float(row["avg_games"]) if row.get("avg_games") else None,
                    int(row["winning_units"]) if row.get("winning_units") else None,
                    int(row["total_units"]) if row.get("total_units") else None,
                    int(row["selected_flag"]) if row.get("selected_flag") else None,
                    row["source_name"],
                ),
            )
            count += int(cur.rowcount > 0)
    con.commit()
    return count


def seed_tail_days(con: sqlite3.Connection, csv_path: pathlib.Path) -> int:
    """Seed tail-day observations; censored negative means remain NULL."""
    if not csv_path.exists():
        return 0
    count = 0
    required = {"hall_id", "result_date", "tail_key", "source_name"}
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing tail-day columns: {sorted(missing)}")
        for row in reader:
            cur = con.execute(
                """INSERT OR IGNORE INTO tail_days
                (hall_id, result_date, tail_key, avg_diff, avg_games,
                 winning_units, total_units, source_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["hall_id"], row["result_date"], row["tail_key"],
                    float(row["avg_diff"]) if row.get("avg_diff") else None,
                    float(row["avg_games"]) if row.get("avg_games") else None,
                    int(row["winning_units"]) if row.get("winning_units") else None,
                    int(row["total_units"]) if row.get("total_units") else None,
                    row["source_name"],
                ),
            )
            count += int(cur.rowcount > 0)
    con.commit()
    return count


def seed_position_signals(con: sqlite3.Connection, csv_path: pathlib.Path) -> int:
    """Store published machine blocks without treating them as full hall-day results."""
    if not csv_path.exists():
        return 0
    count = 0
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            unit_numbers = [int(value) for value in row["unit_numbers"].split("|") if value]
            cur = con.execute(
                """INSERT OR IGNORE INTO position_signals
                (hall_id, result_date, event_name, machine_key, machine_name,
                 unit_numbers_json, unit_count, winning_units, avg_diff, avg_games,
                 rate_scope, source_name, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["hall_id"], row["result_date"], row["event_name"], row["machine_key"],
                    row["machine_name"], json.dumps(unit_numbers, ensure_ascii=False),
                    int(row["unit_count"]),
                    int(row["winning_units"]) if row.get("winning_units") else None,
                    float(row["avg_diff"]) if row.get("avg_diff") else None,
                    float(row["avg_games"]) if row.get("avg_games") else None,
                    row.get("rate_scope") or "unknown", row["source_name"], row.get("notes"),
                ),
            )
            count += int(cur.rowcount > 0)
    con.commit()
    return count


def seed_calendar_flags(con: sqlite3.Connection, json_path: pathlib.Path) -> int:
    if not json_path.exists():
        return 0
    count = 0
    for row in read_json(json_path):
        cur = con.execute(
            """INSERT OR IGNORE INTO calendar_flags
            (flag_date, hall_id, flag_type, flag_name, pre_registered, source_url)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                row["flag_date"], row.get("hall_id", "*"), row["flag_type"], row["flag_name"],
                int(row.get("pre_registered", True)), row.get("source_url"),
            ),
        )
        count += int(cur.rowcount > 0)
    con.commit()
    return count


def seed_validation_queue(con: sqlite3.Connection, json_path: pathlib.Path) -> int:
    if not json_path.exists():
        return 0
    count = 0
    for row in read_json(json_path):
        exists = con.execute(
            """SELECT 1 FROM validation_log
               WHERE target_date=? AND hall_id=? AND claim=? AND verdict='pending' LIMIT 1""",
            (row["target_date"], row["hall_id"], row["claim"]),
        ).fetchone()
        if exists:
            continue
        con.execute(
            """INSERT INTO validation_log
            (run_id, target_date, hall_id, claim, threshold_json, observed_json, verdict, evaluated_at)
            VALUES (NULL, ?, ?, ?, ?, NULL, 'pending', NULL)""",
            (
                row["target_date"], row["hall_id"], row["claim"],
                json.dumps(row["threshold"], ensure_ascii=False, sort_keys=True),
            ),
        )
        count += 1
    con.commit()
    return count


def seed_source_snapshots(con: sqlite3.Connection, json_path: pathlib.Path) -> int:
    """Seed reproducible source checks, including failed public fetches."""
    if not json_path.exists():
        return 0
    count = 0
    for row in read_json(json_path):
        exists = con.execute(
            """SELECT 1 FROM source_snapshots
               WHERE hall_id IS ? AND source_url=? AND fetched_at=? LIMIT 1""",
            (row.get("hall_id"), row["source_url"], row["fetched_at"]),
        ).fetchone()
        if exists:
            continue
        con.execute(
            """INSERT INTO source_snapshots
               (hall_id, source_name, source_url, fetched_at, http_status,
                content_sha256, payload_path, parse_status, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row.get("hall_id"), row["source_name"], row["source_url"], row["fetched_at"],
                row.get("http_status"), row.get("content_sha256"), row.get("payload_path"),
                row["parse_status"], row.get("error_message"),
            ),
        )
        count += 1
    con.commit()
    return count


def generate(
    con: sqlite3.Connection,
    halls: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    holiday_cfg: dict[str, Any],
    start: dt.date,
    days: int,
    run_date: dt.date,
    out_dir: pathlib.Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    end = start + dt.timedelta(days=days - 1)
    cur = con.execute(
        "INSERT INTO model_runs(created_at, model_version, target_start, target_end, data_cutoff, config_json) VALUES (?, ?, ?, ?, ?, ?)",
        (
            dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(), MODEL_VERSION,
            start.isoformat(), end.isoformat(), run_date.isoformat(),
            json.dumps({"shrink_prior_n": 4, "shrink_target_policy": "regime_separated:0 / default:hall_baseline", "rank_thresholds": {"S": 120, "A": 40, "B": 10, "C": 0}, "travel_policy": {"origin": "尾山台", "free_minutes": TRAVEL_FREE_MINUTES, "penalty_per_minute": TRAVEL_PENALTY_PER_MINUTE, "status": "planning_estimate_not_live_timetable"}}, ensure_ascii=False),
        ),
    )
    run_id = int(cur.lastrowid)
    candidates: list[dict[str, Any]] = []
    calendar: list[dict[str, Any]] = []
    for offset in range(days):
        date = start + dt.timedelta(days=offset)
        day_rows = [forecast_one(h, rules, date, run_date, holiday_cfg) for h in halls if h["active"]]
        candidates.extend(day_rows)
        for row in day_rows:
            con.execute(
                """INSERT INTO predictions
                (run_id, target_date, hall_id, rule_id, predicted_mean, adjusted_edge,
                 utility_edge, travel_minutes, travel_penalty, confidence, rank, reasons_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, date.isoformat(), row["hall_id"], row["rule_id"], row["predicted_mean"],
                    row["adjusted_edge"], row["utility_edge"], row["travel_minutes"],
                    row["travel_penalty"], row["confidence"], row["rank"],
                    json.dumps({"reason": row["reason"], "risk_flags": row["risk_flags"], "stale_warning": row["stale_warning"], "horizon_warning": row["horizon_warning"]}, ensure_ascii=False),
                ),
            )
        best_all = select_best(day_rows)
        shibuya = select_best(r for r in day_rows if r["market"] == "渋谷")
        toyoko = select_best(r for r in day_rows if r["market"] == "東横線")
        mizo = select_best(r for r in day_rows if r["market"] == "溝の口")
        fukutoshin = select_best(r for r in day_rows if r["market"] == "副都心線北上")
        ikegami = select_best(r for r in day_rows if r["market"] == "池上線")
        kamata = select_best(r for r in day_rows if r["market"] == "蒲田")
        keikyu = select_best(r for r in day_rows if r["market"] == "京急線")
        ooimachi = select_best(r for r in day_rows if r["market"] == "大井町線")
        yokohama = select_best(r for r in day_rows if r["market"] == "横浜")
        tsurumi = select_best(r for r in day_rows if r["market"] == "鶴見")
        kawasaki = select_best(r for r in day_rows if r["market"] == "川崎")
        center_minami = select_best(r for r in day_rows if r["market"] == "センター南")
        kashimada = select_best(r for r in day_rows if r["market"] == "鹿島田")
        calendar.append({
            "date": date.isoformat(),
            "weekday": "月火水木金土日"[date.weekday()],
            "selection": best_all["selection"],
            "rank": best_all["rank"],
            "market": best_all["market"],
            "relative_best_hall": best_all["relative_best_hall"],
            "predicted_mean": best_all["predicted_mean"],
            "adjusted_edge": best_all["adjusted_edge"],
            "utility_edge": best_all["utility_edge"],
            "travel_minutes": best_all["travel_minutes"],
            "travel_penalty": best_all["travel_penalty"],
            "confidence": best_all["confidence"],
            "reason": best_all["reason"],
            "risk_flags": best_all["risk_flags"],
            "shibuya_pick": shibuya["selection"],
            "shibuya_rank": shibuya["rank"],
            "toyoko_pick": toyoko["selection"],
            "toyoko_rank": toyoko["rank"],
            "mizonokuchi_pick": mizo["selection"],
            "mizonokuchi_rank": mizo["rank"],
            "fukutoshin_pick": fukutoshin["selection"],
            "fukutoshin_rank": fukutoshin["rank"],
            "ikegami_pick": ikegami["selection"],
            "ikegami_rank": ikegami["rank"],
            "kamata_pick": kamata["selection"],
            "kamata_rank": kamata["rank"],
            "keikyu_pick": keikyu["selection"],
            "keikyu_rank": keikyu["rank"],
            "ooimachi_pick": ooimachi["selection"],
            "ooimachi_rank": ooimachi["rank"],
            "yokohama_pick": yokohama["selection"],
            "yokohama_rank": yokohama["rank"],
            "tsurumi_pick": tsurumi["selection"],
            "tsurumi_rank": tsurumi["rank"],
            "kawasaki_pick": kawasaki["selection"],
            "kawasaki_rank": kawasaki["rank"],
            "center_minami_pick": center_minami["selection"],
            "center_minami_rank": center_minami["rank"],
            "kashimada_pick": kashimada["selection"],
            "kashimada_rank": kashimada["rank"],
        })
    con.commit()
    write_csv(out_dir / "forecast_candidates_365.csv", candidates)
    write_csv(out_dir / "calendar_365.csv", calendar)
    (out_dir / "calendar_365.json").write_text(json.dumps(calendar, ensure_ascii=False, indent=2), encoding="utf-8")
    return candidates, calendar


def main() -> None:
    parser = argparse.ArgumentParser(description="Slot Atlas deterministic database and forecast engine")
    parser.add_argument("--db", type=pathlib.Path, default=ROOT / "slot_atlas.db")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    imp = sub.add_parser("import-hall-days")
    imp.add_argument("csv", type=pathlib.Path)
    imp.add_argument("--observed-at", default=dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat())
    gen = sub.add_parser("generate")
    gen.add_argument("--start", default="2026-07-14")
    gen.add_argument("--days", type=int, default=365)
    gen.add_argument("--run-date", default="2026-07-14")
    gen.add_argument("--out", type=pathlib.Path, default=ROOT / "exports")
    args = parser.parse_args()

    halls = read_json(ROOT / "seed" / "halls.json")
    rules = read_json(ROOT / "seed" / "rules.json")
    holiday_cfg = read_json(ROOT / "seed" / "holidays.json")
    con = init_db(args.db)
    seed_db(con, halls, rules)
    seeded_snapshots = seed_source_snapshots(con, ROOT / "seed" / "source_snapshots.json")
    seeded_days = sum(
        seed_hall_days(con, path, "2026-07-14T00:00:00+09:00")
        for path in sorted((ROOT / "seed").glob("*_hall_days.csv"))
    )
    seeded_machine_days = sum(
        seed_machine_days(con, path)
        for path in sorted((ROOT / "seed").glob("*_machine_days.csv"))
    )
    seeded_tail_days = sum(
        seed_tail_days(con, path)
        for path in sorted((ROOT / "seed").glob("*_tail_days.csv"))
    )
    seeded_machines = sum(
        seed_machine_scores(con, path)
        for path in sorted((ROOT / "seed").glob("*_machine_scores.csv"))
    )
    seeded_positions = sum(
        seed_position_signals(con, path)
        for path in sorted((ROOT / "seed").glob("*_position_signals.csv"))
    )
    seeded_flags = seed_calendar_flags(con, ROOT / "seed" / "calendar_flags.json")
    seeded_validations = seed_validation_queue(con, ROOT / "seed" / "validation_queue.json")
    if args.command == "init":
        print(
            f"initialized {args.db} with {len(halls)} halls, {len(rules)} evidence rules, "
            f"{seeded_days} new hall-days, {seeded_machine_days} new machine-days, "
            f"{seeded_tail_days} new tail-days, {seeded_machines} new machine scores and "
            f"{seeded_positions} new position signals, {seeded_flags} new calendar flags and "
            f"{seeded_validations} new validation tasks and {seeded_snapshots} source snapshots"
        )
    elif args.command == "import-hall-days":
        count = import_hall_days(con, args.csv, args.observed_at)
        print(f"imported {count} hall-day rows")
    elif args.command == "generate":
        _, calendar = generate(con, halls, rules, holiday_cfg, iso_date(args.start), args.days, iso_date(args.run_date), args.out)
        playable = sum(1 for row in calendar if row["rank"] in {"S", "A", "B"})
        print(f"generated {len(calendar)} calendar days; playable={playable}; no-bet-or-C={len(calendar)-playable}")


if __name__ == "__main__":
    main()
