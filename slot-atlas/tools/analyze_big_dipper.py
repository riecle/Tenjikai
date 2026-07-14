#!/usr/bin/env python3
"""Reproduce the registered date-pattern audit for BIG DIPPER Togoshi-ginza."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib
import random
import statistics
from collections import defaultdict
from typing import Callable


Pattern = tuple[str, str, Callable[[dt.date], bool], str]


PATTERNS: list[Pattern] = [
    ("day_1_or_20", "1日・20日", lambda day: day.day in {1, 20}, "pre_registered_positive"),
    ("digit_3", "3のつく日", lambda day: day.day in {3, 13, 23}, "pre_registered_positive"),
    ("digit_5", "5のつく日", lambda day: day.day in {5, 15, 25}, "pre_registered_positive"),
    ("digit_7", "7のつく日", lambda day: day.day in {7, 17, 27}, "pre_registered_positive"),
    ("digit_9", "9のつく日", lambda day: day.day in {9, 19, 29}, "pre_registered_positive"),
    ("day_11_or_22", "11日・22日", lambda day: day.day in {11, 22}, "pre_registered_positive"),
    ("month_equals_day", "月=日", lambda day: day.month == day.day, "pre_registered_positive"),
    ("saturday", "土曜", lambda day: day.weekday() == 5, "context_check"),
    ("sunday", "日曜", lambda day: day.weekday() == 6, "context_check"),
    ("day_12", "12日", lambda day: day.day == 12, "exploratory_not_forecast"),
    ("day_16", "16日", lambda day: day.day == 16, "exploratory_not_forecast"),
]


def load_rows(path: pathlib.Path) -> list[tuple[dt.date, float, float]]:
    rows: list[tuple[dt.date, float, float]] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                (
                    dt.date.fromisoformat(row["result_date"]),
                    float(row["avg_diff"]),
                    float(row["avg_games"]),
                )
            )
    return sorted(rows)


def mean(values: list[float]) -> float:
    return statistics.mean(values) if values else float("nan")


def residualize(rows: list[tuple[dt.date, float, float]]) -> list[float]:
    """Remove additive calendar-month and weekday means without dependencies."""
    values = [row[1] for row in rows]
    grand = mean(values)
    month_effect: dict[str, float] = {}
    weekday_effect: dict[int, float] = {}
    for _ in range(20):
        month_buckets: dict[str, list[float]] = defaultdict(list)
        for (date, value, _), residual in zip(rows, values):
            month_buckets[date.strftime("%Y-%m")].append(value - weekday_effect.get(date.weekday(), 0.0))
        month_effect = {key: mean(bucket) - grand for key, bucket in month_buckets.items()}
        weekday_buckets: dict[int, list[float]] = defaultdict(list)
        for date, value, _ in rows:
            weekday_buckets[date.weekday()].append(value - month_effect[date.strftime("%Y-%m")])
        weekday_effect = {key: mean(bucket) - grand for key, bucket in weekday_buckets.items()}
    return [
        value - grand - month_effect[date.strftime("%Y-%m")] - weekday_effect[date.weekday()]
        for date, value, _ in rows
    ]


def stratified_permutation_p(
    rows: list[tuple[dt.date, float, float]],
    residuals: list[float],
    mask: list[bool],
    iterations: int,
    seed: int,
) -> float:
    rng = random.Random(seed)
    groups: dict[tuple[str, int], list[int]] = defaultdict(list)
    for idx, (date, _, _) in enumerate(rows):
        groups[(date.strftime("%Y-%m"), date.weekday())].append(idx)
    observed = mean([residuals[i] for i, flag in enumerate(mask) if flag]) - mean(
        [residuals[i] for i, flag in enumerate(mask) if not flag]
    )
    exceed = 0
    original = list(mask)
    for _ in range(iterations):
        shuffled = original.copy()
        for indices in groups.values():
            flags = [original[i] for i in indices]
            rng.shuffle(flags)
            for index, flag in zip(indices, flags):
                shuffled[index] = flag
        effect = mean([residuals[i] for i, flag in enumerate(shuffled) if flag]) - mean(
            [residuals[i] for i, flag in enumerate(shuffled) if not flag]
        )
        exceed += int(abs(effect) >= abs(observed))
    return (exceed + 1) / (iterations + 1)


def bh_qvalues(pairs: list[tuple[str, float]]) -> dict[str, float]:
    ordered = sorted(pairs, key=lambda item: item[1])
    total = len(ordered)
    result: dict[str, float] = {}
    running = 1.0
    for rank in range(total, 0, -1):
        key, pvalue = ordered[rank - 1]
        running = min(running, pvalue * total / rank)
        result[key] = min(1.0, running)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--permutations", type=int, default=20_000)
    args = parser.parse_args()

    rows = load_rows(args.input)
    residuals = residualize(rows)
    midpoint = len(rows) // 2
    audits: list[dict[str, object]] = []
    pvalues: list[tuple[str, float]] = []
    for index, (key, label, predicate, registration) in enumerate(PATTERNS):
        mask = [predicate(date) for date, _, _ in rows]
        selected = [row for row, flag in zip(rows, mask) if flag]
        control = [row for row, flag in zip(rows, mask) if not flag]
        first = [row[1] for row in rows[:midpoint] if predicate(row[0])]
        second = [row[1] for row in rows[midpoint:] if predicate(row[0])]
        adjusted = mean([value for value, flag in zip(residuals, mask) if flag]) - mean(
            [value for value, flag in zip(residuals, mask) if not flag]
        )
        pvalue = stratified_permutation_p(
            rows, residuals, mask, args.permutations, 20260714 + index
        )
        pvalues.append((key, pvalue))
        audits.append(
            {
                "pattern_id": key,
                "label": label,
                "registration": registration,
                "n": len(selected),
                "mean_diff": round(mean([row[1] for row in selected]), 1),
                "median_diff": round(statistics.median([row[1] for row in selected]), 1),
                "positive_rate": round(sum(row[1] > 0 for row in selected) / len(selected), 4),
                "avg_games": round(mean([row[2] for row in selected]), 1),
                "raw_lift_vs_complement": round(
                    mean([row[1] for row in selected]) - mean([row[1] for row in control]), 1
                ),
                "month_weekday_adjusted_lift": round(adjusted, 1),
                "stratified_permutation_p_two_sided": round(pvalue, 6),
                "first_half_n": len(first),
                "first_half_mean": round(mean(first), 1),
                "second_half_n": len(second),
                "second_half_mean": round(mean(second), 1),
            }
        )
    qvalues = bh_qvalues(pvalues)
    for audit in audits:
        audit["bh_q_all_tested_patterns"] = round(qvalues[str(audit["pattern_id"])], 6)

    strong_days = {1, 3, 5, 7, 9, 13, 15, 17, 19, 20, 23, 25, 27, 29}
    baseline = [row for row in rows if row[0].day not in strong_days]
    payload = {
        "method": {
            "pre_registration": "external old-event candidates were fixed before testing; day 12/16 are exploratory only",
            "negative_days": "public machine payout rates reconstructed suppressed negative hall means",
            "adjustment": "additive calendar-month and weekday residualization",
            "p_value": f"two-sided permutation within calendar-month x weekday strata, B={args.permutations}",
            "multiplicity": "Benjamini-Hochberg across every pattern in this audit",
            "stability": "chronological first/second half shown separately",
        },
        "data": {
            "start": rows[0][0].isoformat(),
            "through": rows[-1][0].isoformat(),
            "n": len(rows),
            "all_day_mean": round(mean([row[1] for row in rows]), 1),
            "all_day_median": round(statistics.median([row[1] for row in rows]), 1),
            "all_day_positive_rate": round(sum(row[1] > 0 for row in rows) / len(rows), 4),
        },
        "conservative_normal_baseline": {
            "definition": "exclude only pre-registered positive patterns: 1/20 and 3/5/7/9 endings; keep avoid patterns in the conservative prior",
            "n": len(baseline),
            "mean_diff": round(mean([row[1] for row in baseline]), 1),
            "median_diff": round(statistics.median([row[1] for row in baseline]), 1),
            "positive_rate": round(sum(row[1] > 0 for row in baseline) / len(baseline), 4),
        },
        "patterns": audits,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
