import datetime as dt
import pathlib
import random
import sqlite3
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import atlas_plus  # noqa: E402
import slot_atlas  # noqa: E402

HOLIDAYS = {"holidays": [], "long_break_ranges": []}


def make_hall(**over):
    hall = {
        "hall_id": "test_hall", "market": "溝の口", "name": "テスト店", "active": True,
        "slot_count": 300, "exchange_label": "46枚貸・等価", "decision_floor": 0,
        "baseline_mean": -119.0, "baseline_n": 301, "data_through": "2026-07-12",
        "context_downgrades": {},
    }
    hall.update(over)
    return hall


def make_rule(**over):
    rule = {
        "rule_id": "r1", "hall_id": "test_hall", "label": "第2土曜",
        "priority": 90, "match": {"weekday": 5, "nth_weekday": 2},
        "mean_diff": 42.0, "sample_n": 13, "data_through": "2026-07-12",
        "status": "active",
    }
    rule.update(over)
    return rule


class ShrinkPolicyTest(unittest.TestCase):
    def test_regime_separated_shrinks_to_zero(self):
        hall, run = make_hall(), dt.date(2026, 7, 14)
        date = dt.date(2026, 9, 12)  # 第2土曜
        plain = slot_atlas.forecast_one(hall, [make_rule()], date, run, HOLIDAYS)
        flagged = slot_atlas.forecast_one(
            hall, [make_rule(regime_separated=True)], date, run, HOLIDAYS)
        self.assertAlmostEqual(plain["predicted_mean"], (42 * 13 - 119 * 4) / 17, delta=0.1)
        self.assertAlmostEqual(flagged["predicted_mean"], 42 * 13 / 17, delta=0.1)
        self.assertEqual(plain["rank"], "C")  # 旧仕様: 縮約で+4.1 → C見送り
        self.assertIn(flagged["rank"], {"A", "B"})

    def test_default_rules_unchanged(self):
        hall, run = make_hall(baseline_mean=19.0), dt.date(2026, 7, 14)
        row = slot_atlas.forecast_one(
            hall, [make_rule(mean_diff=231.0, sample_n=11, match={"day": 22}, label="22日")],
            dt.date(2026, 7, 22), run, HOLIDAYS)
        self.assertAlmostEqual(row["predicted_mean"], (231 * 11 + 19 * 4) / 15, delta=0.1)


class CusumTest(unittest.TestCase):
    def test_detects_upward_shift(self):
        rng = random.Random(7)
        start = dt.date(2026, 1, 1)
        daily = [(start + dt.timedelta(days=i), rng.gauss(-120, 40)) for i in range(60)]
        daily += [(start + dt.timedelta(days=60 + i), rng.gauss(40, 40)) for i in range(60)]
        changes = atlas_plus.cusum_changes(daily)
        self.assertTrue(changes)
        first = changes[0]
        self.assertEqual(first["direction"], "up")
        detected = dt.date.fromisoformat(first["change_date"])
        self.assertLess(abs((detected - dt.date(2026, 3, 2)).days), 15)

    def test_stable_series_silent(self):
        rng = random.Random(11)
        start = dt.date(2026, 1, 1)
        daily = [(start + dt.timedelta(days=i), rng.gauss(-120, 40)) for i in range(150)]
        self.assertEqual(atlas_plus.cusum_changes(daily), [])

    def test_near_edge_burst_waits_for_confirmation(self):
        start = dt.date(2026, 1, 1)
        daily = [(start + dt.timedelta(days=i), -120.0) for i in range(60)]
        daily += [(start + dt.timedelta(days=60 + i), 400.0) for i in range(5)]
        self.assertEqual(atlas_plus.cusum_changes(daily), [])


class HabitVectorTest(unittest.TestCase):
    def test_zero_sum_r_negative_on_anticorrelated_months(self):
        rng = random.Random(3)
        daily = []
        for m in range(1, 9):
            burst = 400 if m % 2 else 100  # big event month <-> squeezed rest
            rest = -220 if m % 2 else -60
            for day in range(1, 29):
                date = dt.date(2026, m, day)
                val = burst + rng.gauss(0, 20) if day in (23, 24) else rest + rng.gauss(0, 30)
                daily.append((date, val))
        rules = [{
            "rule_id": "event", "hall_id": "x", "label": "23・24日",
            "priority": 90, "match": {"day_in": [23, 24]},
            "mean_diff": 250, "sample_n": 16, "data_through": "2026-08-31",
            "status": "active",
        }]
        vec = atlas_plus.habit_vector(daily, rules, "x")
        self.assertIsNotNone(vec)
        self.assertLess(vec["zero_sum_r"], -0.5)
        self.assertEqual(vec["n_days"], len(daily))

    def test_zero_sum_is_not_invented_without_registered_event_days(self):
        start = dt.date(2026, 1, 1)
        daily = [(start + dt.timedelta(days=i), float((i % 11) - 5)) for i in range(180)]
        vec = atlas_plus.habit_vector(daily, [], "x")
        self.assertIsNone(vec["zero_sum_r"])


class UnitTestsTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        self.con.execute(
            "INSERT INTO halls (hall_id, market, name, source_kind) VALUES ('h1','溝の口','テスト','test')")

    def _insert(self, unit, wins, losses, machine="ジャグラー"):
        day = 1
        for w in range(wins):
            self.con.execute(
                "INSERT INTO unit_days VALUES ('h1',?,?,?,?,NULL,'test','2026-07-14')",
                (f"2026-06-{day:02d}", unit, machine, 500.0))
            day += 1
        for _ in range(losses):
            self.con.execute(
                "INSERT INTO unit_days VALUES ('h1',?,?,?,?,NULL,'test','2026-07-14')",
                (f"2026-06-{day:02d}", unit, machine, -400.0))
            day += 1

    def test_hot_unit_flagged_against_island(self):
        self._insert(101, wins=13, losses=2)   # 87% winner
        for u in (102, 103, 104):
            self._insert(u, wins=4, losses=11)  # island ~27%
        res = atlas_plus.unit_tests(self.con, "h1")
        flagged = [x["unit_no"] for x in res["hot_units"]]
        self.assertIn(101, flagged)
        self.assertNotIn(102, flagged)

    def test_adjacent_pair_co_win(self):
        for day in range(1, 21):
            win = day % 2 == 0
            for unit in (201, 202):
                self.con.execute(
                    "INSERT INTO unit_days VALUES ('h1',?,?,?,?,NULL,'test','2026-07-14')",
                    (f"2026-05-{day:02d}", unit, "カバネリ", 300.0 if win else -300.0))
        res = atlas_plus.unit_tests(self.con, "h1")
        self.assertTrue(any(p["units"] == "201-202" for p in res["adjacent_pairs"]))

    def test_import_roundtrip(self):
        csv_path = pathlib.Path(ROOT / "tests" / "_tmp_units.csv")
        csv_path.write_text(
            "hall_id,result_date,unit_no,machine_name,diff,games,source_name\n"
            "h1,2026-07-01,777,マイジャグラーV,1200,7800,field_note\n",
            encoding="utf-8")
        try:
            n = atlas_plus.import_unit_days(self.con, csv_path, "2026-07-14T00:00:00+09:00")
            self.assertEqual(n, 1)
            row = self.con.execute("SELECT * FROM unit_days WHERE unit_no=777").fetchone()
            self.assertEqual(row["machine_name"], "マイジャグラーV")
        finally:
            csv_path.unlink(missing_ok=True)

    def test_hot_unit_is_split_by_machine_lifecycle(self):
        for day in range(1, 11):
            self.con.execute(
                "INSERT INTO unit_days VALUES ('h1',?,?,?,?,NULL,'test','2026-07-14')",
                (f"2026-05-{day:02d}", 301, "旧機種", 500.0))
        for day in range(1, 11):
            self.con.execute(
                "INSERT INTO unit_days VALUES ('h1',?,?,?,?,NULL,'test','2026-07-14')",
                (f"2026-06-{day:02d}", 301, "新機種", 500.0 if day <= 9 else -300.0))
            for unit in (302, 303, 304):
                self.con.execute(
                    "INSERT INTO unit_days VALUES ('h1',?,?,?,?,NULL,'test','2026-07-14')",
                    (f"2026-06-{day:02d}", unit, "新機種",
                     500.0 if day in (1, 2) else -300.0))
        res = atlas_plus.unit_tests(self.con, "h1")
        row = next(x for x in res["hot_units"] if x["unit_no"] == 301)
        self.assertEqual(row["machine_name"], "新機種")
        self.assertEqual(row["n"], 10)


class MachineLookupTest(unittest.TestCase):
    def test_store_conditional_verdicts(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        con.execute("INSERT INTO halls (hall_id, market, name, source_kind) VALUES ('a','渋谷','渋谷店','t')")
        con.execute("INSERT INTO halls (hall_id, market, name, source_kind) VALUES ('b','溝の口','溝の口店','t')")
        con.execute(
            """INSERT INTO machine_scores (hall_id, as_of_date, machine_key, machine_name, units,
               baseline_days, baseline_avg_diff, special_selected_n, momentum_selected_n,
               composite_score, type_label, source_name)
               VALUES ('a','2026-07-12','tg','東京喰種',6,180,422,3,2,88.0,'A','t')""")
        con.execute(
            """INSERT INTO machine_scores (hall_id, as_of_date, machine_key, machine_name, units,
               baseline_days, baseline_avg_diff, special_selected_n, momentum_selected_n,
               composite_score, type_label, source_name)
               VALUES ('b','2026-07-12','tg','東京喰種',4,180,-680,0,0,12.0,'C','t')""")
        rows = atlas_plus.machine_lookup(con, "喰種")
        self.assertEqual(rows[0]["hall_id"], "a")
        self.assertEqual(rows[0]["verdict"], "候補")
        self.assertEqual(rows[0]["store_score_rank"], 1)
        self.assertEqual(rows[1]["verdict"], "回避")



class ZeroSampleGuardTest(unittest.TestCase):
    def test_n0_rule_never_fires(self):
        hall, run = make_hall(baseline_mean=50.0), dt.date(2026, 7, 14)
        date = dt.date(2026, 9, 12)  # 第2土曜
        armed = slot_atlas.forecast_one(hall, [make_rule(sample_n=13)], date, run, HOLIDAYS)
        placeholder = slot_atlas.forecast_one(hall, [make_rule(sample_n=0)], date, run, HOLIDAYS)
        self.assertIn("第2土曜", str(armed["reason"]))
        self.assertNotIn("第2土曜", str(placeholder["reason"]))
        self.assertAlmostEqual(placeholder["predicted_mean"], 50.0, delta=0.1)


class StaleCheckTest(unittest.TestCase):
    def test_collect_stale_orders_and_filters(self):
        as_of = dt.date(2026, 7, 14)
        halls = [
            {"name": "新鮮店", "data_through": "2026-07-13", "forecast_enabled": 1},
            {"name": "綱島風", "data_through": "2026-06-24", "forecast_enabled": 1},
            {"name": "未取得店", "data_through": None, "forecast_enabled": 0},
        ]
        rules = [
            {"rule_id": "r_old", "label": "古", "data_through": "2026-06-01"},
            {"rule_id": "r_new", "label": "新", "data_through": "2026-07-13"},
        ]
        sh, sr = atlas_plus.collect_stale(halls, rules, as_of, 14)
        self.assertEqual([x[1] for x in sh], ["未取得店", "綱島風"])
        self.assertEqual(sh[1][0], 20)
        self.assertEqual([x[1] for x in sr], ["r_old"])


if __name__ == "__main__":
    unittest.main()
