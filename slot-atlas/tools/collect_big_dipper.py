#!/usr/bin/env python3
"""Collect and normalize public BIG DIPPER Togoshi-ginza reports.

The public list suppresses negative hall totals with ``-``.  Detail pages do
publish average games and payout rate for each machine group, so negative
machine means can be reconstructed as ``games * 3 * (rate - 1)``.  Positive
hall totals are kept exactly as published; only suppressed negative totals are
reconstructed.  The resulting rows deliberately carry a distinct source name.

This adapter is not used by the forecast engine at run time.  It creates
auditable seed CSVs which the stdlib-only core can load without network access.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import hashlib
import html as html_lib
import json
import math
import pathlib
import re
import statistics
import time
import urllib.request
from collections import defaultdict
from typing import Any

from lxml import html


LIST_URL = (
    "https://min-repo.com/tag/"
    "%e3%83%93%e3%83%83%e3%82%af%e3%83%87%e3%82%a3%e3%83%83%e3%83%91%e3%83%bc"
    "%e6%88%b8%e8%b6%8a%e9%8a%80%e5%ba%a7%e5%ba%97/"
)
HALL_ID = "ikegami_big_dipper_togoshi_ginza"
SOURCE_LIST = "min-repo"
SOURCE_RECON = "min-repo_rate_reconstructed"
UA = "Mozilla/5.0 (compatible; SlotAtlasResearch/0.9; public-report-normalizer)"


def number(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip().replace(",", "").replace("+", "")
    if not value or value == "-":
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group()) if match else None


def text_content(node: Any) -> str:
    return "".join(node.itertext()).strip()


def machine_key(name: str) -> str:
    canonical = re.sub(r"[\s　・･‐－―ー]+", "", name).lower()
    return "bd_" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def fetch(url: str, retries: int = 4) -> bytes:
    error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=35) as response:
                body = response.read()
            if not body:
                raise RuntimeError("empty HTTP body")
            return body
        except Exception as exc:  # pragma: no cover - live network branch
            error = exc
            time.sleep(1.25 * (attempt + 1))
    raise RuntimeError(f"fetch failed after {retries} tries: {url}: {error}")


def parse_result_date(label: str, as_of: dt.date) -> dt.date:
    clean = re.sub(r"\([^)]*\)", "", label)
    parts = [int(x) for x in clean.split("/")]
    if len(parts) == 3:
        return dt.date(*parts)
    month, day = parts
    year = as_of.year
    candidate = dt.date(year, month, day)
    if candidate > as_of:
        candidate = dt.date(year - 1, month, day)
    return candidate


def parse_list(body: bytes, as_of: dt.date) -> list[dict[str, Any]]:
    source = body.decode("utf-8", errors="replace")
    pattern = re.compile(
        r'<tr>\s*<td><a href="(https://min-repo\.com/\d+/)">([^<]+)</a></td>(.*?)</tr>',
        re.S,
    )
    rows: list[dict[str, Any]] = []
    for url, label, rest in pattern.findall(source):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", rest, re.S)
        cells = [
            html_lib.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
            for cell in cells
        ]
        if len(cells) < 3:
            continue
        rows.append(
            {
                "date": parse_result_date(label, as_of),
                "url": url,
                "published_total": number(cells[0]),
                "published_avg": number(cells[1]),
                "list_avg_games": number(cells[2]),
                "selection_text": cells[3] if len(cells) > 3 else "",
            }
        )
    rows.sort(key=lambda row: row["date"], reverse=True)
    if not rows:
        raise ValueError("no report rows found in list page")
    return rows


def best_mean(displayed_diff: float, games: float | None, rate: float | None) -> tuple[float, bool]:
    """Return a machine mean and whether rate reconstruction was used."""
    if displayed_diff >= 0 or games is None or rate is None:
        return displayed_diff, False
    # Detail pages round suppressed negative differences to 250-token steps.
    # Payout rate retains one decimal and gives a materially less biased mean.
    return games * 3.0 * (rate / 100.0 - 1.0), True


def parse_detail(body: bytes, meta: dict[str, Any]) -> dict[str, Any]:
    tree = html.fromstring(body)
    summary: dict[str, str] = {}
    for tr in tree.xpath('//table[contains(concat(" ", normalize-space(@class), " "), " sou ")]/tr'):
        cells = tr.xpath("./th|./td")
        if len(cells) >= 2:
            summary[text_content(cells[0])] = text_content(cells[1])

    win_match = re.search(r"(\d+)\s*/\s*(\d+)", summary.get("勝率", ""))
    if not win_match:
        raise ValueError(f"missing hall win ratio for {meta['date']}")
    winning_units, total_units = map(int, win_match.groups())
    avg_games = number(summary.get("平均G数")) or meta["list_avg_games"]
    status = summary.get("状況", "").replace("\n", " ")

    machines: list[dict[str, Any]] = []
    group_tables = tree.xpath(
        '//table[contains(concat(" ", normalize-space(@class), " "), " _2dai ")]'
    )
    if not group_tables:
        raise ValueError(f"missing multi-machine table for {meta['date']}")
    for tr in group_tables[0].xpath(".//tr[@data-count][td]"):
        units = int(tr.get("data-count") or 0)
        if not units:
            continue
        cells = [text_content(cell) for cell in tr.xpath("./td")]
        if len(cells) < 5:
            continue
        displayed = number(cells[1])
        games = number(cells[2])
        ratio = re.search(r"(\d+)\s*/\s*(\d+)", cells[3])
        rate = number(cells[4])
        if displayed is None or not ratio:
            continue
        chosen, reconstructed = best_mean(displayed, games, rate)
        wins, ratio_units = map(int, ratio.groups())
        machines.append(
            {
                "machine_name": cells[0],
                "machine_key": machine_key(cells[0]),
                "units": units,
                "avg_diff": round(chosen, 3),
                "displayed_avg_diff": displayed,
                "avg_games": games,
                "winning_units": wins,
                "total_units": ratio_units,
                "selected_flag": int(cells[0] in meta["selection_text"]),
                "reconstructed": reconstructed,
            }
        )

    variety_tables = tree.xpath('//h2[contains(.,"バラエティ")]/following-sibling::table[1]')
    if not variety_tables:
        raise ValueError(f"missing variety table for {meta['date']}")
    for tr in variety_tables[0].xpath(".//tr[td]"):
        cells = [text_content(cell) for cell in tr.xpath("./td")]
        if len(cells) < 5:
            continue
        displayed = number(cells[2])
        games = number(cells[3])
        rate = number(cells[4])
        if displayed is None:
            continue
        chosen, reconstructed = best_mean(displayed, games, rate)
        machines.append(
            {
                "machine_name": cells[0],
                "machine_key": machine_key(cells[0]),
                "units": 1,
                "avg_diff": round(chosen, 3),
                "displayed_avg_diff": displayed,
                "avg_games": games,
                "winning_units": int(displayed > 0),
                "total_units": 1,
                "selected_flag": int(cells[0] in meta["selection_text"]),
                "reconstructed": reconstructed,
                "unit_no": int(number(cells[1]) or 0),
            }
        )

    parsed_units = sum(row["units"] for row in machines)
    if parsed_units != total_units:
        raise ValueError(
            f"unit mismatch for {meta['date']}: machine tables={parsed_units}, hall={total_units}"
        )

    reconstructed_total = sum(row["avg_diff"] * row["units"] for row in machines)
    published_total = meta["published_total"]
    if published_total is not None:
        total_diff = published_total
        avg_diff = meta["published_avg"] if meta["published_avg"] is not None else total_diff / total_units
        hall_source = SOURCE_LIST
    else:
        total_diff = round(reconstructed_total)
        avg_diff = round(reconstructed_total / total_units, 1)
        hall_source = SOURCE_RECON

    tails: list[dict[str, Any]] = []
    tail_tables = tree.xpath('//h2[contains(.,"末尾別")]/following-sibling::table[1]')
    if tail_tables:
        for tr in tail_tables[0].xpath(".//tr[td]"):
            cells = [text_content(cell) for cell in tr.xpath("./td")]
            if len(cells) < 5:
                continue
            ratio = re.search(r"(\d+)\s*/\s*(\d+)", cells[3])
            if not ratio:
                continue
            wins, units = map(int, ratio.groups())
            tails.append(
                {
                    "tail_key": "z" if "ゾロ" in cells[0] else cells[0],
                    "avg_diff": number(cells[1]),
                    "avg_games": number(cells[2]),
                    "winning_units": wins,
                    "total_units": units,
                }
            )

    return {
        **meta,
        "status": status,
        "avg_games": avg_games,
        "winning_units": winning_units,
        "total_units": total_units,
        "machine_win_rate": round(100.0 * winning_units / total_units, 3),
        "total_diff": total_diff,
        "avg_diff": avg_diff,
        "hall_source": hall_source,
        "machines": machines,
        "tails": tails,
        "reconstructed_total": round(reconstructed_total, 3),
    }


def cached_detail(meta: dict[str, Any], cache_dir: pathlib.Path) -> dict[str, Any]:
    cache_path = cache_dir / f"{meta['date'].isoformat()}_{meta['url'].rstrip('/').split('/')[-1]}.html"
    if cache_path.exists() and cache_path.stat().st_size:
        body = cache_path.read_bytes()
    else:
        body = fetch(meta["url"])
        cache_path.write_bytes(body)
    return parse_detail(body, meta)


def write_csv(path: pathlib.Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def zscores(values: dict[str, float]) -> dict[str, float]:
    if len(values) < 2:
        return {key: 0.0 for key in values}
    mean = statistics.mean(values.values())
    std = statistics.pstdev(values.values())
    if not std:
        return {key: 0.0 for key in values}
    return {key: (value - mean) / std for key, value in values.items()}


def build_machine_scores(details: list[dict[str, Any]], as_of: dt.date) -> list[dict[str, Any]]:
    start90 = as_of - dt.timedelta(days=89)
    start30 = as_of - dt.timedelta(days=29)
    latest = max(details, key=lambda row: row["date"])
    current_names = {row["machine_name"]: row["units"] for row in latest["machines"]}
    observations: dict[str, list[tuple[dt.date, dict[str, Any], bool]]] = defaultdict(list)
    for detail in details:
        if detail["date"] < start90:
            continue
        special = "旧イベント日" in detail["status"]
        for row in detail["machines"]:
            if row["machine_name"] in current_names:
                observations[row["machine_name"]].append((detail["date"], row, special))

    baseline: dict[str, float] = {}
    special_n: dict[str, float] = {}
    momentum_n: dict[str, float] = {}
    for name, obs in observations.items():
        total_units = sum(row["units"] for _, row, _ in obs)
        baseline[name] = sum(row["avg_diff"] * row["units"] for _, row, _ in obs) / total_units
        special_n[name] = float(sum(row["selected_flag"] for _, row, special in obs if special))
        momentum_n[name] = float(sum(row["selected_flag"] for date, row, _ in obs if date >= start30))

    zb, zs, zm = zscores(baseline), zscores(special_n), zscores(momentum_n)
    rows: list[dict[str, Any]] = []
    for name, obs in observations.items():
        days = len({date for date, _, _ in obs})
        if days < 3:
            continue
        score = 0.35 * zb[name] + 0.40 * zs[name] + 0.25 * zm[name]
        if days < 14:
            label = "新台・標本少"
        elif baseline[name] > 0 and special_n[name] >= 2:
            label = "常用+特定日"
        elif special_n[name] >= 2:
            label = "特定日寄り"
        elif baseline[name] > 0:
            label = "常用"
        else:
            label = "様子見"
        rows.append(
            {
                "hall_id": HALL_ID,
                "as_of_date": as_of.isoformat(),
                "machine_key": machine_key(name),
                "machine_name": name,
                "units": current_names[name],
                "baseline_days": days,
                "baseline_avg_diff": round(baseline[name], 1),
                "special_selected_n": int(special_n[name]),
                "momentum_selected_n": int(momentum_n[name]),
                "composite_score": round(score, 4),
                "type_label": label,
                "source_name": "min-repo_machine_detail_90d",
                "notes": (
                    f"90日内{days}日。A=機種表の台数加重平均、B=詳細ページが旧イベント日と明示した日の"
                    "公開優秀機種該当数、C=直近30日の公開優秀機種該当数。負差枚は出率から復元。"
                    "S=0.35z(A)+0.40z(B)+0.25z(C)。"
                ),
            }
        )
    rows.sort(key=lambda row: row["composite_score"], reverse=True)
    return rows


def build_position_signals(details: list[dict[str, Any]], as_of: dt.date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    start = as_of - dt.timedelta(days=89)
    recent = [row for row in details if start <= row["date"] <= as_of]
    per_tail: dict[str, list[tuple[dt.date, dict[str, Any]]]] = defaultdict(list)
    all_numeric, all_rows = 0, 0
    matched_numeric, matched_rows = 0, 0
    for detail in recent:
        for tail in detail["tails"]:
            if tail["tail_key"] == "z":
                continue
            per_tail[tail["tail_key"]].append((detail["date"], tail))
            positive = tail["avg_diff"] is not None
            all_rows += 1
            all_numeric += int(positive)
            if str(detail["date"].day % 10) == tail["tail_key"]:
                matched_rows += 1
                matched_numeric += int(positive)

    position_rows: list[dict[str, Any]] = []
    for key in sorted(per_tail, key=lambda value: int(value)):
        matches = [(date, row) for date, row in per_tail[key] if str(date.day % 10) == key]
        positive = sum(row["avg_diff"] is not None for _, row in matches)
        games = [row["avg_games"] for _, row in matches if row["avg_games"] is not None]
        units = [row["total_units"] for _, row in matches]
        position_rows.append(
            {
                "hall_id": HALL_ID,
                "result_date": as_of.isoformat(),
                "event_name": "日付末尾一致（90日・符号検定）",
                "machine_key": f"tail_{key}",
                "machine_name": f"台番号末尾{key}",
                "unit_numbers": "",
                "unit_count": round(statistics.mean(units)) if units else 0,
                "winning_units": "",
                "avg_diff": "",
                "avg_games": round(statistics.mean(games), 1) if games else "",
                "rate_scope": "20yen",
                "source_name": "min-repo_tail_sign_90d",
                "notes": (
                    f"日付末尾一致{len(matches)}日中、公開平均差枚が正で表示された日{positive}日。"
                    "負の日は差枚値自体が非表示のため、平均差枚を補完せず符号だけで検定。"
                ),
            }
        )
    summary = {
        "window_start": start.isoformat(),
        "window_end": as_of.isoformat(),
        "matched_positive_n": matched_numeric,
        "matched_n": matched_rows,
        "control_positive_n": all_numeric - matched_numeric,
        "control_n": all_rows - matched_rows,
        "matched_positive_rate": matched_numeric / matched_rows if matched_rows else None,
        "control_positive_rate": (
            (all_numeric - matched_numeric) / (all_rows - matched_rows)
            if all_rows > matched_rows else None
        ),
    }
    return position_rows, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default="2026-07-13", help="latest result date, YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=400)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--list-html", type=pathlib.Path)
    parser.add_argument("--cache-dir", type=pathlib.Path, required=True)
    parser.add_argument("--seed-dir", type=pathlib.Path, required=True)
    parser.add_argument("--summary", type=pathlib.Path, required=True)
    args = parser.parse_args()

    as_of = dt.date.fromisoformat(args.as_of)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    list_body = args.list_html.read_bytes() if args.list_html else fetch(LIST_URL)
    entries = [row for row in parse_list(list_body, as_of + dt.timedelta(days=1)) if row["date"] <= as_of]
    entries = entries[: args.days]
    print(f"list rows={len(entries)} range={entries[-1]['date']}..{entries[0]['date']}", flush=True)

    details: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(cached_detail, row, args.cache_dir): row for row in entries}
        for idx, future in enumerate(concurrent.futures.as_completed(futures), 1):
            meta = futures[future]
            try:
                details.append(future.result())
            except Exception as exc:  # pragma: no cover - live network branch
                failures.append({"date": meta["date"].isoformat(), "url": meta["url"], "error": str(exc)})
            if idx % 20 == 0 or idx == len(futures):
                print(f"processed={idx}/{len(futures)} ok={len(details)} failed={len(failures)}", flush=True)
    details.sort(key=lambda row: row["date"])
    if failures:
        raise RuntimeError(f"detail failures: {json.dumps(failures[:5], ensure_ascii=False)}")

    hall_days = [
        {
            "hall_id": HALL_ID,
            "result_date": row["date"].isoformat(),
            "avg_diff": row["avg_diff"],
            "total_diff": row["total_diff"],
            "avg_games": row["avg_games"],
            "machine_win_rate": row["machine_win_rate"],
            "winning_units": row["winning_units"],
            "total_units": row["total_units"],
            "source_name": row["hall_source"],
        }
        for row in details
    ]
    machine_days: list[dict[str, Any]] = []
    tail_days: list[dict[str, Any]] = []
    for detail in details:
        for row in detail["machines"]:
            machine_days.append(
                {
                    "hall_id": HALL_ID,
                    "result_date": detail["date"].isoformat(),
                    "machine_key": row["machine_key"],
                    "machine_name": row["machine_name"],
                    "units": row["units"],
                    "avg_diff": row["avg_diff"],
                    "avg_games": row["avg_games"],
                    "winning_units": row["winning_units"],
                    "total_units": row["total_units"],
                    "selected_flag": row["selected_flag"],
                    "source_name": "min-repo_machine_detail",
                }
            )
        for row in detail["tails"]:
            tail_days.append(
                {
                    "hall_id": HALL_ID,
                    "result_date": detail["date"].isoformat(),
                    "tail_key": row["tail_key"],
                    "avg_diff": "" if row["avg_diff"] is None else row["avg_diff"],
                    "avg_games": row["avg_games"],
                    "winning_units": row["winning_units"],
                    "total_units": row["total_units"],
                    "source_name": "min-repo_tail_detail_censored_negative",
                }
            )

    machine_scores = build_machine_scores(details, as_of)
    position_signals, position_summary = build_position_signals(details, as_of)
    write_csv(
        args.seed_dir / "big_dipper_hall_days.csv",
        ["hall_id", "result_date", "avg_diff", "total_diff", "avg_games", "machine_win_rate", "winning_units", "total_units", "source_name"],
        hall_days,
    )
    write_csv(
        args.seed_dir / "big_dipper_machine_days.csv",
        ["hall_id", "result_date", "machine_key", "machine_name", "units", "avg_diff", "avg_games", "winning_units", "total_units", "selected_flag", "source_name"],
        machine_days,
    )
    write_csv(
        args.seed_dir / "big_dipper_tail_days.csv",
        ["hall_id", "result_date", "tail_key", "avg_diff", "avg_games", "winning_units", "total_units", "source_name"],
        tail_days,
    )
    write_csv(
        args.seed_dir / "big_dipper_machine_scores.csv",
        ["hall_id", "as_of_date", "machine_key", "machine_name", "units", "baseline_days", "baseline_avg_diff", "special_selected_n", "momentum_selected_n", "composite_score", "type_label", "source_name", "notes"],
        machine_scores,
    )
    write_csv(
        args.seed_dir / "big_dipper_position_signals.csv",
        ["hall_id", "result_date", "event_name", "machine_key", "machine_name", "unit_numbers", "unit_count", "winning_units", "avg_diff", "avg_games", "rate_scope", "source_name", "notes"],
        position_signals,
    )

    positive_rows = [row for row in details if row["published_total"] is not None]
    reconstruction_errors = [
        row["reconstructed_total"] / row["total_units"] - row["published_avg"]
        for row in positive_rows
        if row["published_avg"] is not None
    ]
    summary = {
        "hall_id": HALL_ID,
        "data_start": details[0]["date"].isoformat(),
        "data_through": details[-1]["date"].isoformat(),
        "days": len(details),
        "published_positive_days": len(positive_rows),
        "rate_reconstructed_negative_days": sum(row["hall_source"] == SOURCE_RECON for row in details),
        "machine_day_rows": len(machine_days),
        "tail_day_rows": len(tail_days),
        "machine_scores": len(machine_scores),
        "reconstruction_error_mean_on_published_positive_days": round(statistics.mean(reconstruction_errors), 3),
        "reconstruction_error_mae_on_published_positive_days": round(statistics.mean(abs(x) for x in reconstruction_errors), 3),
        "position_sign_test": position_summary,
        "list_sha256": hashlib.sha256(list_body).hexdigest(),
        "negative_reconstruction_formula": "sum(machine_units * machine_games * 3 * (payout_rate/100 - 1)); positive machine means kept as displayed",
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
