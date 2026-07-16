#!/usr/bin/env python3
"""Extract event_families from evidence_rules and backfill hall_days.

Reads evidence_rules.match_json patterns, derives a family_type for each,
creates event_family records, then tags matching hall_days rows with
event_family_id.

Usage:
    python3 tools/build_event_families.py --db slot_atlas.db

Stdlib-only.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path


def family_type_from_match_json(match: dict) -> str:
    """Map a match_json pattern to a family_type string.

    Uses the same logic as free_source_predictor.family_key() but works
    from the structured match_json instead of label text.
    """
    if not match or match == {} or match.get("always"):
        return "通常"

    if match.get("month_equals_day"):
        return "月=日"

    if match.get("is_repdigit_day"):
        return "ゾロ目"

    if match.get("month_end"):
        return "月末"

    rokuyo = match.get("rokuyo")
    if rokuyo:
        return f"六曜:{rokuyo}"

    event_name = match.get("event_name")
    if event_name:
        return f"イベント:{event_name}"

    event_tag = match.get("event_tag")
    if event_tag:
        return f"タグ:{event_tag}"

    if match.get("event_overlay_count_gte"):
        return "重複イベント"

    weekday = match.get("weekday")
    nth = match.get("nth_weekday")
    if nth is not None and weekday is not None:
        wd_names = ["月", "火", "水", "木", "金", "土", "日"]
        wd_label = wd_names[weekday] if 0 <= weekday < 7 else str(weekday)
        return f"第{nth}{wd_label}曜"

    if weekday is not None and "day" not in match and "day_in" not in match:
        wd_names = ["月", "火", "水", "木", "金", "土", "日"]
        wd_label = wd_names[weekday] if 0 <= weekday < 7 else str(weekday)
        return f"{wd_label}曜日"

    day_mod10 = match.get("day_mod10")
    if day_mod10 is not None:
        return f"{day_mod10}のつく日"

    day_in = match.get("day_in")
    if day_in and isinstance(day_in, list):
        month = match.get("month")
        if month is not None:
            return f"特定日群({month}月)"
        mods = {d % 10 for d in day_in}
        if len(mods) == 1:
            return f"{mods.pop()}のつく日"
        if set(day_in) == {11, 22}:
            return "ゾロ目"
        sorted_days = sorted(day_in)
        return f"日群:{','.join(str(d) for d in sorted_days)}"

    day = match.get("day")
    if day is not None:
        month = match.get("month")
        if month is not None:
            return f"記念日({month}/{day})"
        if day in (11, 22):
            return "ゾロ目"
        return f"{day % 10}のつく日"

    return "その他"


def make_family_id(hall_id: str, family_type: str, rule_json: str) -> str:
    """Generate a stable event_family_id."""
    import hashlib
    key = f"{hall_id}|{family_type}|{rule_json}"
    short = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"ef_{short}"


def date_matches_rule(result_date: str, match: dict) -> bool:
    """Check if a date matches a match_json rule."""
    if not match or match == {} or match.get("always"):
        return True

    try:
        parts = result_date.split("-")
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    except (ValueError, IndexError):
        return False

    if match.get("month_equals_day") and month == day:
        return True

    if match.get("is_repdigit_day") and day in (11, 22):
        return True

    if match.get("month_end") and day >= 28:
        from calendar import monthrange
        _, last = monthrange(year, month)
        if day == last:
            return True

    rule_day = match.get("day")
    rule_month = match.get("month")
    if rule_day is not None:
        if rule_month is not None:
            return day == rule_day and month == rule_month
        return day == rule_day

    day_in = match.get("day_in")
    if day_in and isinstance(day_in, list):
        if rule_month is not None and month != rule_month:
            return False
        if day not in day_in:
            return False
        weekday = match.get("weekday")
        if weekday is not None:
            from datetime import date as dt_date
            if dt_date(year, month, day).weekday() != weekday:
                return False
        return True

    day_mod10 = match.get("day_mod10")
    if day_mod10 is not None:
        return day % 10 == day_mod10

    weekday = match.get("weekday")
    if weekday is not None:
        from datetime import date as dt_date
        actual_wd = dt_date(year, month, day).weekday()
        nth = match.get("nth_weekday")
        if nth is not None:
            occurrence = (day - 1) // 7 + 1
            return actual_wd == weekday and occurrence == nth
        return actual_wd == weekday

    return False


def build_families(conn: sqlite3.Connection) -> tuple[int, int]:
    """Create event_families from evidence_rules. Returns (families, backfilled)."""
    try:
        conn.execute("SELECT 1 FROM evidence_rules LIMIT 1")
    except sqlite3.OperationalError:
        return 0, 0

    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(evidence_rules)")
    }
    conf_col = (
        "positive_rate" if "positive_rate" in cols
        else "confidence" if "confidence" in cols
        else "NULL"
    )
    rules = conn.execute(
        f"""SELECT hall_id, match_json, label, {conf_col}
            FROM evidence_rules
            ORDER BY hall_id, match_json"""
    ).fetchall()

    existing_families = {
        r[0] for r in
        conn.execute("SELECT event_family_id FROM event_families").fetchall()
    }

    seen: dict[str, str] = {}
    families_inserted = 0

    for hall_id, match_json_str, label, confidence in rules:
        try:
            match = json.loads(match_json_str) if match_json_str else {}
        except json.JSONDecodeError:
            continue

        ftype = family_type_from_match_json(match)
        canon_rule = json.dumps(match, sort_keys=True, ensure_ascii=False)
        dedup_key = f"{hall_id}|{canon_rule}"

        if dedup_key in seen:
            continue

        fam_id = make_family_id(hall_id, ftype, canon_rule)
        seen[dedup_key] = fam_id

        if fam_id in existing_families:
            continue

        conn.execute(
            """INSERT OR IGNORE INTO event_families
               (event_family_id, hall_id, family_type, rule_json,
                confidence, source)
               VALUES (?,?,?,?,?,?)""",
            (fam_id, hall_id, ftype, canon_rule, confidence,
             "evidence_rules"),
        )
        families_inserted += 1

    conn.commit()

    backfilled = _backfill_hall_days(conn, seen, rules)
    return families_inserted, backfilled


def _backfill_hall_days(
    conn: sqlite3.Connection,
    family_map: dict[str, str],
    rules: list[tuple],
) -> int:
    """Tag hall_days rows with the best-matching event_family_id."""
    hall_rules: dict[str, list[tuple[dict, str]]] = {}
    for hall_id, match_json_str, _, _ in rules:
        try:
            match = json.loads(match_json_str) if match_json_str else {}
        except json.JSONDecodeError:
            continue
        canon = json.dumps(match, sort_keys=True, ensure_ascii=False)
        dedup_key = f"{hall_id}|{canon}"
        fam_id = family_map.get(dedup_key)
        if fam_id and hall_id not in hall_rules:
            hall_rules[hall_id] = []
        if fam_id:
            already = any(m == match for m, _ in hall_rules[hall_id])
            if not already:
                hall_rules[hall_id].append((match, fam_id))

    hall_dates = conn.execute(
        """SELECT DISTINCT hall_id, result_date
           FROM hall_days
           WHERE event_family_id IS NULL
           ORDER BY hall_id, result_date"""
    ).fetchall()

    updated = 0
    for hall_id, result_date in hall_dates:
        rules_for_hall = hall_rules.get(hall_id, [])
        best_fam = None
        best_specificity = -1

        for match, fam_id in rules_for_hall:
            if not date_matches_rule(result_date, match):
                continue
            spec = _specificity(match)
            if spec > best_specificity:
                best_specificity = spec
                best_fam = fam_id

        if best_fam:
            conn.execute(
                """UPDATE hall_days SET event_family_id = ?
                   WHERE hall_id = ? AND result_date = ?""",
                (best_fam, hall_id, result_date),
            )
            updated += 1

    conn.commit()
    return updated


def _specificity(match: dict) -> int:
    """Higher = more specific rule. Used to pick best match."""
    if not match or match == {} or match.get("always"):
        return 0
    score = 0
    if "month" in match:
        score += 10
    if "day" in match:
        score += 5
    if "day_in" in match:
        score += 3
    if "day_mod10" in match:
        score += 2
    if "nth_weekday" in match:
        score += 4
    if "weekday" in match:
        score += 1
    if match.get("month_equals_day") or match.get("is_repdigit_day"):
        score += 2
    if match.get("event_name") or match.get("event_tag"):
        score += 8
    return score


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build event families from evidence rules"
    )
    ap.add_argument("--db", required=True, help="Path to slot_atlas.db")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"error: {db} not found", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db))
    families, backfilled = build_families(conn)
    conn.close()

    print(f"event_families: {families} inserted")
    print(f"hall_days backfilled: {backfilled} rows")


if __name__ == "__main__":
    main()
