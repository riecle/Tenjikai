#!/usr/bin/env python3
"""Atlas Plus v0.9: habit vectors, regime changepoints, machine reverse lookup,
and the per-unit (台番) test frame on top of the Slot Atlas database.

Stdlib only. All commands are deterministic and idempotent.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import pathlib
import sqlite3
import statistics as st
from collections import defaultdict
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent
JST = dt.timezone(dt.timedelta(hours=9))


def connect(db_path: pathlib.Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def now_iso() -> str:
    return dt.datetime.now(JST).isoformat()


def load_daily(con: sqlite3.Connection, hall_id: str) -> list[tuple[dt.date, float]]:
    rows = con.execute(
        "SELECT result_date, avg_diff FROM hall_days WHERE hall_id=? ORDER BY result_date",
        (hall_id,),
    ).fetchall()
    return [(dt.date.fromisoformat(r["result_date"]), float(r["avg_diff"])) for r in rows]


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 4 or len(xs) != len(ys):
        return None
    mx, my = st.fmean(xs), st.fmean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


# ---------------------------------------------------------------- habit vector

def habit_vector(daily: list[tuple[dt.date, float]], rules: list[dict[str, Any]],
                 hall_id: str) -> dict[str, Any] | None:
    """5-dim operating-habit vector from daily rows.

    zero_sum_r        : per-month corr(pre-registered event-day mean,
                        rest-of-month mean). Strongly negative = the hall
                        funds event days by squeezing normal days.
    prev_day_squeeze  : mean(diff on day before a top-decile day) - overall mean.
    event_compliance  : win-day rate on the hall's highest-priority positive
                        rule day-type (None if the hall has none).
    weekend_penalty   : mean(Sat+Sun) - mean(weekday).
    burst_recovery_lag_days : days from the hottest month's end until the first
                        month whose mean drops below overall mean - 0.5*sigma_m.
    """
    if len(daily) < 90:
        return None
    diffs = [d for _, d in daily]
    overall = st.fmean(diffs)

    months: dict[tuple[int, int], list[tuple[dt.date, float]]] = defaultdict(list)
    for date, diff in daily:
        months[(date.year, date.month)].append((date, diff))

    try:
        import slot_atlas as sa
    except Exception:
        sa = None
    positive_rules = [
        r for r in rules
        if r["hall_id"] == hall_id
        and r.get("status", "active") == "active"
        and float(r["mean_diff"]) > 0
        and int(r.get("sample_n") or 0) >= 5
        and bool(r.get("match"))
    ]
    ev_means, rest_means = [], []
    for _, rows in sorted(months.items()):
        if len(rows) < 15:
            continue
        event = [
            diff for date, diff in rows
            if sa is not None and any(
                sa.rule_matches(r["match"], date) and sa.active_for_date(r, date)
                for r in positive_rules
            )
        ]
        rest = [
            diff for date, diff in rows
            if not (
                sa is not None and any(
                    sa.rule_matches(r["match"], date) and sa.active_for_date(r, date)
                    for r in positive_rules
                )
            )
        ]
        if event and len(rest) >= 10:
            ev_means.append(st.fmean(event))
            rest_means.append(st.fmean(rest))
    zero_sum_r = pearson(ev_means, rest_means)

    n_top = max(1, len(daily) // 10)
    top_dates = {d for d, _ in sorted(daily, key=lambda t: t[1], reverse=True)[:n_top]}
    by_date = dict(daily)
    prevs = [by_date[d - dt.timedelta(days=1)] for d in top_dates
             if (d - dt.timedelta(days=1)) in by_date]
    prev_day_squeeze = round(st.fmean(prevs) - overall, 1) if len(prevs) >= 5 else None

    event_compliance = None
    try:
        if positive_rules:
            positive_rules.sort(key=lambda r: int(r["priority"]), reverse=True)
            best = positive_rules[0]
            hits = [diff for date, diff in daily
                    if sa.rule_matches(best["match"], date) and sa.active_for_date(best, date)]
            if len(hits) >= 5:
                event_compliance = round(sum(1 for x in hits if x > 0) / len(hits), 3)
    except Exception:
        pass

    wk = [d for date, d in daily if date.weekday() < 5]
    we = [d for date, d in daily if date.weekday() >= 5]
    weekend_penalty = round(st.fmean(we) - st.fmean(wk), 1) if wk and we else None

    burst_lag = None
    m_keys = sorted(k for k, rows in months.items() if len(rows) >= 15)
    m_means = {k: st.fmean([d for _, d in months[k]]) for k in m_keys}
    if len(m_keys) >= 4:
        sigma_m = st.pstdev(list(m_means.values()))
        hot = max(m_keys, key=lambda k: m_means[k])
        hot_end = max(d for d, _ in months[hot])
        for k in m_keys:
            if k <= hot:
                continue
            if m_means[k] < st.fmean(list(m_means.values())) - 0.5 * sigma_m:
                first = min(d for d, _ in months[k])
                burst_lag = (first - hot_end).days
                break

    return {
        "zero_sum_r": round(zero_sum_r, 3) if zero_sum_r is not None else None,
        "prev_day_squeeze": prev_day_squeeze,
        "event_compliance": event_compliance,
        "weekend_penalty": weekend_penalty,
        "burst_recovery_lag_days": burst_lag,
        "n_days": len(daily),
    }


# ----------------------------------------------------------------- changepoint

def cusum_changes(daily: list[tuple[dt.date, float]], k: float = 0.5,
                  h: float = 5.0, warmup: int = 30,
                  min_after: int = 14) -> list[dict[str, Any]]:
    """Two-sided CUSUM with a forward-window confirmation guard.

    CUSUM supplies a candidate direction using only information available at the
    candidate date.  A regime row is emitted only when at least ``min_after``
    later observations exist and their mean moves in the same direction.  This
    prevents a one-day burst near the data edge from being mislabeled as a
    confirmed regime change.
    """
    if len(daily) < warmup + 10:
        return []
    base = [d for _, d in daily[:warmup]]
    mu, sigma = st.fmean(base), st.pstdev(base) or 1.0
    s_hi = s_lo = 0.0
    out: list[dict[str, Any]] = []
    for i, (date, diff) in enumerate(daily[warmup:], start=warmup):
        z = (diff - mu) / sigma
        s_hi = max(0.0, s_hi + z - k)
        s_lo = max(0.0, s_lo - z - k)
        if s_hi > h or s_lo > h:
            trigger_direction = "up" if s_hi > h else "down"
            before = [d for _, d in daily[max(0, i - 30):i]] or [mu]
            after = [d for _, d in daily[i:i + 30]]
            if len(after) < min_after:
                break
            before_mean = st.fmean(before)
            after_mean = st.fmean(after)
            confirmed_direction = "up" if after_mean > before_mean else "down"
            if confirmed_direction != trigger_direction:
                s_hi = s_lo = 0.0
                continue
            out.append({
                "change_date": date.isoformat(),
                "direction": confirmed_direction,
                "cusum_stat": round(max(s_hi, s_lo), 2),
                "window_mean_before": round(before_mean, 1),
                "window_mean_after": round(after_mean, 1),
            })
            # re-anchor on the most recent window so successive regimes are
            # measured against the new level, not the original warmup.
            anchor = after or before
            mu, sigma = st.fmean(anchor), (st.pstdev(anchor) or sigma)
            s_hi = s_lo = 0.0
    return out


# ------------------------------------------------------------- machine lookup

def machine_lookup(con: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    rows = con.execute(
        """WITH latest AS (
               SELECT hall_id, MAX(as_of_date) AS as_of_date
               FROM machine_scores GROUP BY hall_id
           ), ranked AS (
               SELECT m.*,
                      RANK() OVER (
                          PARTITION BY m.hall_id
                          ORDER BY (m.composite_score IS NULL), m.composite_score DESC
                      ) AS store_score_rank,
                      COUNT(*) OVER (PARTITION BY m.hall_id) AS score_population_n
               FROM machine_scores m
               JOIN latest l ON l.hall_id=m.hall_id AND l.as_of_date=m.as_of_date
           )
           SELECT r.hall_id, h.name AS hall_name, r.machine_name, r.units,
                  r.baseline_days, r.baseline_avg_diff, r.composite_score,
                  r.type_label, r.store_score_rank, r.score_population_n,
                  r.as_of_date, r.notes
           FROM ranked r JOIN halls h ON h.hall_id = r.hall_id
           WHERE r.machine_name LIKE ?""",
        (f"%{query}%",),
    ).fetchall()
    out = []
    for r in rows:
        base = r["baseline_avg_diff"]
        if base is not None and base <= -300 and r["type_label"] == "特定日特化":
            verdict = "特定日限定"
        elif base is not None and base <= -300:
            verdict = "回避"
        elif base is not None and base > 0:
            verdict = "候補"
        else:
            verdict = "様子見"
        out.append({**dict(r), "verdict": verdict})
    verdict_order = {"候補": 3, "特定日限定": 2, "様子見": 1, "回避": 0}
    out.sort(
        key=lambda r: (
            verdict_order[r["verdict"]],
            r["baseline_avg_diff"] if r["baseline_avg_diff"] is not None else -math.inf,
            -int(r["store_score_rank"]),
        ),
        reverse=True,
    )
    return out


def top_machines(con: sqlite3.Connection, hall_id: str, limit: int) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT machine_name, units, baseline_avg_diff, composite_score, type_label
           FROM machine_scores WHERE hall_id=?
           ORDER BY (composite_score IS NULL), composite_score DESC LIMIT ?""",
        (hall_id, limit),
    ).fetchall()


# ------------------------------------------------------------- unit-day tests

def import_unit_days(con: sqlite3.Connection, csv_path: pathlib.Path,
                     observed_at: str) -> int:
    required = {"hall_id", "result_date", "unit_no", "machine_name", "diff", "source_name"}
    count = 0
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing columns: {sorted(missing)}")
        for row in reader:
            con.execute(
                """INSERT OR REPLACE INTO unit_days
                   (hall_id, result_date, unit_no, machine_name, diff, games,
                    source_name, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["hall_id"], row["result_date"], int(row["unit_no"]),
                    row["machine_name"], float(row["diff"]),
                    float(row["games"]) if row.get("games") else None,
                    row["source_name"], observed_at,
                ),
            )
            count += 1
    con.commit()
    return count


def binom_sf(wins: int, n: int, p: float) -> float:
    """P(X >= wins) for X ~ Binom(n, p), exact."""
    return sum(math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i)) for i in range(wins, n + 1))


def unit_tests(con: sqlite3.Connection, hall_id: str,
               min_days: int = 10) -> dict[str, list[dict[str, Any]]]:
    rows = con.execute(
        "SELECT result_date, unit_no, machine_name, diff FROM unit_days WHERE hall_id=?",
        (hall_id,),
    ).fetchall()
    per_unit_machine: dict[tuple[int, str], list[tuple[str, float]]] = defaultdict(list)
    island: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for r in rows:
        key = (r["unit_no"], r["machine_name"])
        per_unit_machine[key].append((r["result_date"], r["diff"]))
        island[r["machine_name"]].append((r["unit_no"], r["diff"]))

    hot_units = []
    for (unit, machine), recs in sorted(per_unit_machine.items()):
        if len(recs) < min_days:
            continue
        others = [diff for other_unit, diff in island[machine] if other_unit != unit]
        if len(others) < min_days:
            continue
        p0 = min(0.95, max(0.05, sum(1 for diff in others if diff > 0) / len(others)))
        wins = sum(1 for _, diff in recs if diff > 0)
        pval = binom_sf(wins, len(recs), p0)
        if pval < 0.05 and wins / len(recs) > p0:
            hot_units.append({
                "unit_no": unit, "machine_name": machine, "n": len(recs),
                "win_rate": round(wins / len(recs), 3),
                "island_win_rate": round(p0, 3), "p_value": round(pval, 4),
            })

    latest_machine: dict[int, str] = {}
    for r in sorted(rows, key=lambda row: row["result_date"]):
        latest_machine[r["unit_no"]] = r["machine_name"]
    per_unit = {
        unit: per_unit_machine[(unit, machine)]
        for unit, machine in latest_machine.items()
    }
    pairs = []
    units = sorted(per_unit)
    for u in units:
        if (u + 1) not in per_unit:
            continue
        a = {d: diff > 0 for d, diff in per_unit[u]}
        b = {d: diff > 0 for d, diff in per_unit[u + 1]}
        shared = sorted(set(a) & set(b))
        if len(shared) < min_days:
            continue
        xs = [1.0 if a[d] else 0.0 for d in shared]
        ys = [1.0 if b[d] else 0.0 for d in shared]
        phi = pearson(xs, ys)
        if phi is not None and phi > 0.35:
            pairs.append({"units": f"{u}-{u + 1}", "n": len(shared), "phi": round(phi, 3)})

    tails = []
    cell: dict[tuple[int, int], list[float]] = defaultdict(list)
    for r in rows:
        day = int(r["result_date"][-2:])
        cell[(r["unit_no"] % 10, day % 10)].append(r["diff"])
    for (ut, dtail), vals in sorted(cell.items()):
        if len(vals) >= max(min_days, 15) and st.fmean(vals) > 0:
            tails.append({"unit_tail": ut, "date_tail": dtail, "n": len(vals),
                          "mean_diff": round(st.fmean(vals), 1)})
    tails.sort(key=lambda x: x["mean_diff"], reverse=True)
    return {"hot_units": hot_units, "adjacent_pairs": pairs, "tail_cells": tails[:10]}


# ---------------------------------------------------------------- stale check

def collect_stale(halls, rules, as_of, threshold_days: int):
    """dataの鮮度遅延を店・ルール別に列挙（遅延降順）。日次更新の巡回優先度を機械化する。"""
    stale_h = []
    for h in halls:
        thru = h["data_through"]
        if not thru:
            stale_h.append((9999, h["name"], "未取得", bool(h["forecast_enabled"])))
            continue
        lag = (as_of - dt.date.fromisoformat(str(thru))).days
        if lag > threshold_days:
            stale_h.append((lag, h["name"], str(thru), bool(h["forecast_enabled"])))
    stale_r = []
    for r in rules:
        thru = r["data_through"]
        if not thru:
            continue
        lag = (as_of - dt.date.fromisoformat(str(thru))).days
        if lag > threshold_days:
            stale_r.append((lag, r["rule_id"], r["label"], str(thru)))
    return sorted(stale_h, reverse=True), sorted(stale_r, reverse=True)


# ------------------------------------------------------------------------ CLI

def main() -> None:
    parser = argparse.ArgumentParser(description="Slot Atlas v0.9 extensions")
    parser.add_argument("--db", type=pathlib.Path, default=ROOT / "slot_atlas.db")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("habit-vector")
    sub.add_parser("changepoint")
    ml = sub.add_parser("machine-lookup")
    ml.add_argument("--machine", help="機種名の部分一致（店条件付き逆引き）")
    ml.add_argument("--hall", help="hall_id: 店の上位機種リスト")
    ml.add_argument("--top", type=int, default=10)
    iu = sub.add_parser("import-unit-days")
    iu.add_argument("csv", type=pathlib.Path)
    iu.add_argument("--observed-at", default=now_iso())
    sc = sub.add_parser("stale-check")
    sc.add_argument("--days", type=int, default=14)
    sc.add_argument("--as-of", help="ISO date; default=today")
    pt = sub.add_parser("position-tests")
    pt.add_argument("--hall", required=True)
    args = parser.parse_args()

    con = connect(args.db)
    rules = json.loads((ROOT / "seed" / "rules.json").read_text(encoding="utf-8"))

    if args.command == "habit-vector":
        halls = con.execute("SELECT hall_id, name FROM halls WHERE active=1").fetchall()
        as_of = dt.date.today().isoformat()
        wrote = 0
        for h in halls:
            daily = load_daily(con, h["hall_id"])
            vec = habit_vector(daily, rules, h["hall_id"])
            if vec is None:
                continue
            con.execute(
                """INSERT OR REPLACE INTO habit_vectors
                   (hall_id, as_of_date, zero_sum_r, prev_day_squeeze, event_compliance,
                    weekend_penalty, burst_recovery_lag_days, n_days, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (h["hall_id"], as_of, vec["zero_sum_r"], vec["prev_day_squeeze"],
                 vec["event_compliance"], vec["weekend_penalty"],
                 vec["burst_recovery_lag_days"], vec["n_days"],
                 "初物回収深さはmachine_days日次の取得後に追加"),
            )
            wrote += 1
            print(f"{h['name']}: {json.dumps(vec, ensure_ascii=False)}")
        con.commit()
        print(f"habit vectors written: {wrote} (日次90行以上の店のみ)")

    elif args.command == "changepoint":
        halls = con.execute("SELECT hall_id, name FROM halls WHERE active=1").fetchall()
        total = 0
        for h in halls:
            daily = load_daily(con, h["hall_id"])
            changes = cusum_changes(daily)
            for c in changes:
                con.execute(
                    """INSERT OR IGNORE INTO regime_changes
                       (hall_id, change_date, direction, cusum_stat,
                        window_mean_before, window_mean_after, detected_at, params_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (h["hall_id"], c["change_date"], c["direction"], c["cusum_stat"],
                     c["window_mean_before"], c["window_mean_after"], now_iso(),
                     json.dumps({"k": 0.5, "h": 5.0, "warmup": 30, "min_after": 14})),
                )
                total += 1
                print(f"{h['name']}: {c['change_date']} {c['direction']} "
                      f"(前30日{c['window_mean_before']} → 後30日{c['window_mean_after']})")
        con.commit()
        print(f"regime changes recorded: {total}")

    elif args.command == "machine-lookup":
        if args.machine:
            rows = machine_lookup(con, args.machine)
            if not rows:
                print(f"'{args.machine}' はmachine_scores未登録。日次取込後に再実行。")
            for r in rows:
                score = r["composite_score"] if r["composite_score"] is not None else "-"
                print(f"[{r['verdict']}] {r['hall_name']} | {r['machine_name']} "
                      f"({r['units']}台) {r['baseline_days']}日{r['baseline_avg_diff']} "
                      f"店内S順位={r['store_score_rank']}/{r['score_population_n']} score={score} "
                      f"{r['type_label']} as_of {r['as_of_date']}")
            print("※店条件付き：他店の同機種成績は流用禁止（喰種: 渋谷+422 vs 溝の口-68）")
        elif args.hall:
            for r in top_machines(con, args.hall, args.top):
                print(f"{r['machine_name']} ({r['units']}台) 通算{r['baseline_avg_diff']} "
                      f"score={r['composite_score']} {r['type_label']}")
        else:
            parser.error("--machine か --hall のどちらかを指定")

    elif args.command == "import-unit-days":
        count = import_unit_days(con, args.csv, args.observed_at)
        print(f"imported {count} unit-day rows")

    elif args.command == "stale-check":
        as_of = dt.date.fromisoformat(args.as_of) if args.as_of else dt.date.today()
        halls = con.execute(
            "SELECT hall_id, name, data_through, forecast_enabled FROM halls WHERE active=1"
        ).fetchall()
        rrows = con.execute(
            "SELECT rule_id, hall_id, label, data_through FROM evidence_rules WHERE status='active'"
        ).fetchall()
        stale_h, stale_r = collect_stale(halls, rrows, as_of, args.days)
        print(f"=== 鮮度チェック as of {as_of}（閾値{args.days}日）===")
        print(f"-- 店（{len(stale_h)}件）--")
        for lag, name, thru, fe in stale_h:
            mark = "予測ON" if fe else "監視　"
            print(f"  [{mark}] {name}: data_through {thru}（遅延{lag}日）")
        print(f"-- ルール（{len(stale_r)}件、上位10）--")
        for lag, rid, label, thru in stale_r[:10]:
            print(f"  {rid} 「{label}」: {thru}（遅延{lag}日）")
        if not stale_h and not stale_r:
            print("  問題なし")

    elif args.command == "position-tests":
        res = unit_tests(con, args.hall)
        if not any(res.values()):
            print("unit_daysにデータなし。契約: hall_id,result_date,unit_no,machine_name,"
                  "diff[,games],source_name を import-unit-days で取込。現地観測は1観測1レコード。")
        else:
            print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
