from __future__ import annotations

import csv
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from free_source_predictor import build_free_source_payload  # noqa: E402


class FreeSourcePredictorTest(unittest.TestCase):
    def test_full_summary_none_and_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            atlas = pathlib.Path(tmp)
            (atlas / "seed").mkdir()
            (atlas / "exports").mkdir()

            machine_rows = []
            dates = [
                "2026-01-10", "2026-01-20", "2026-02-10", "2026-02-20",
                "2026-03-10", "2026-03-20",
            ]
            for index, day in enumerate(dates):
                selected = ("A", "B", "C")[index % 3]
                for machine, units in (("A", 4), ("B", 3), ("C", 12)):
                    machine_rows.append({
                        "hall_id": "full",
                        "date": day,
                        "machine_name": machine,
                        "avg_diff": 1600 if machine == selected else -100,
                        "avg_games": 6000,
                        "units": units,
                        "special_selected": machine == selected,
                    })
            (atlas / "seed" / "machine_days.json").write_text(
                json.dumps(machine_rows, ensure_ascii=False), encoding="utf-8"
            )

            tail_rows = []
            for day in dates:
                for tail in range(10):
                    tail_rows.append({
                        "hall_id": "full", "date": day, "tail": tail,
                        "avg_diff": 500 if tail == 5 else 0,
                    })
            (atlas / "seed" / "tail_days.json").write_text(
                json.dumps(tail_rows, ensure_ascii=False), encoding="utf-8"
            )
            (atlas / "seed" / "machine_scores.json").write_text(
                json.dumps([{"hall_id": "summary", "machine_name": "X", "score": 500}], ensure_ascii=False),
                encoding="utf-8",
            )

            candidates = [
                {"id": "full", "d": "2026-07-20", "why": "20日"},
                {"id": "summary", "d": "2026-07-20", "why": "20日"},
                {"id": "none", "d": "2026-07-20", "why": "20日"},
            ]
            payload = build_free_source_payload(atlas, candidates)

            self.assertEqual(payload["halls"]["full"]["layer"], "FULL")
            self.assertEqual(payload["halls"]["summary"]["layer"], "SUMMARY")
            self.assertEqual(payload["halls"]["none"]["layer"], "NONE")

            family = payload["halls"]["full"]["families"]["0のつく日"]
            self.assertEqual(family["machine"]["all_machine_rate"], 100.0)
            self.assertEqual(family["machine"]["rotation_label"], "ローテ型")
            self.assertEqual(family["tails"][0]["tail"], 5)
            self.assertGreaterEqual(family["tails"][0]["z"], 2.0)



class UnitPolicyGateTest(unittest.TestCase):
    def test_unit_days_excluded_by_default(self):
        import tempfile, csv as _csv
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td)
            (d / "seed").mkdir()
            with (d / "seed" / "unit_days.csv").open("w", newline="", encoding="utf-8") as f:
                w = _csv.DictWriter(f, fieldnames=["hall_id", "date", "unit_no", "diff", "machine_name"])
                w.writeheader()
                w.writerow({"hall_id": "h1", "date": "2026-07-15", "unit_no": 101, "diff": 500,
                            "machine_name": "テスト機"})
            p0 = build_free_source_payload(d, [])
            self.assertEqual(p0["table_counts"]["unit_days"], 0)
            self.assertIn("<policy-excluded: local-only>", p0["source_files"]["unit_days"])
            p1 = build_free_source_payload(d, [], include_unit=True)
            self.assertEqual(p1["table_counts"]["unit_days"], 1)


if __name__ == "__main__":
    unittest.main()
