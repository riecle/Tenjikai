import datetime as dt
import csv
import json
import pathlib
import tempfile
import unittest

import slot_atlas


ROOT = pathlib.Path(__file__).resolve().parents[1]


class SlotAtlasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.halls = slot_atlas.read_json(ROOT / "seed" / "halls.json")
        cls.rules = slot_atlas.read_json(ROOT / "seed" / "rules.json")
        cls.holidays = slot_atlas.read_json(ROOT / "seed" / "holidays.json")
        cls.by_id = {h["hall_id"]: h for h in cls.halls}

    def test_rule_match_nth_weekday(self):
        self.assertTrue(slot_atlas.rule_matches({"weekday": 5, "nth_weekday": 2}, dt.date(2026, 8, 8)))
        self.assertFalse(slot_atlas.rule_matches({"weekday": 5, "nth_weekday": 2}, dt.date(2026, 8, 15)))

    def test_oyamadai_travel_cost_demotes_distant_thin_edge(self):
        hall = self.by_id["fukutoshin_maruhan_shinjuku_toho"]
        row = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 14), dt.date(2026, 7, 14), self.holidays
        )
        self.assertEqual(row["adjusted_edge"], 17.1)
        self.assertEqual(row["travel_penalty"], 28.0)
        self.assertEqual(row["utility_edge"], -10.9)
        self.assertEqual(row["rank"], "NO BET")

    def test_dogenzaka_owns_28th_in_shibuya(self):
        date = dt.date(2026, 7, 28)
        rows = [slot_atlas.forecast_one(h, self.rules, date, dt.date(2026, 7, 13), self.holidays) for h in self.halls if h["market"] == "渋谷"]
        self.assertEqual(slot_atlas.select_best(rows)["hall_id"], "shibuya_rakuen_dogenzaka")

    def test_mizonokuchi_23rd_prefers_espace_shinkan(self):
        date = dt.date(2026, 7, 23)
        rows = [slot_atlas.forecast_one(h, self.rules, date, dt.date(2026, 7, 13), self.holidays) for h in self.halls if h["market"] == "溝の口"]
        best = slot_atlas.select_best(rows)
        self.assertEqual(best["hall_id"], "mizonokuchi_espace_shinkan")
        self.assertIn(best["rank"], {"A", "B"})

    def test_honkan_anniversary_is_shrunk_for_n1(self):
        row = slot_atlas.forecast_one(self.by_id["mizonokuchi_espace_honkan"], self.rules, dt.date(2026, 10, 15), dt.date(2026, 7, 13), self.holidays)
        self.assertNotIn(row["rank"], {"S", "A"})

    def test_act_old_event_names_do_not_become_a_bet(self):
        hall = self.by_id["mizonokuchi_slot_act"]
        for date in (dt.date(2026, 7, 17), dt.date(2026, 7, 22), dt.date(2026, 7, 27)):
            row = slot_atlas.forecast_one(
                hall, self.rules, date, dt.date(2026, 7, 13), self.holidays
            )
            self.assertIn(row["rank"], {"NO BET", "C"})

    def test_redrock_event_saturday_is_actionable(self):
        hall = self.by_id["toyoko_redrock_gakugei"]
        row = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 8, 29), dt.date(2026, 7, 13), self.holidays
        )
        self.assertEqual(row["rule_id"], "rr_event_saturday")
        self.assertIn(row["rank"], {"A", "B"})

    def test_redrock_weekday_old_event_is_not_actionable(self):
        hall = self.by_id["toyoko_redrock_gakugei"]
        row = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 19), dt.date(2026, 7, 13), self.holidays
        )
        self.assertIn(row["rank"], {"NO BET", "C"})

    def test_redrock_26th_is_avoid(self):
        hall = self.by_id["toyoko_redrock_gakugei"]
        row = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 26), dt.date(2026, 7, 13), self.holidays
        )
        self.assertEqual(row["rule_id"], "rr_day26_avoid")
        self.assertEqual(row["rank"], "NO BET")

    def test_hinomaru_nakameguro_is_monitor_only(self):
        hall = self.by_id["toyoko_hinomaru_nakameguro"]
        for date in (dt.date(2026, 7, 20), dt.date(2027, 4, 25)):
            row = slot_atlas.forecast_one(
                hall, self.rules, date, dt.date(2026, 7, 13), self.holidays
            )
            self.assertEqual(row["rank"], "NO BET")
            self.assertEqual(row["reason"], "監視中（予測未解禁）")
            self.assertEqual(row["sample_n"], 0)

    def test_oriental_jiyugaoka_is_rule_evidence_but_not_forecast_enabled(self):
        hall = self.by_id["toyoko_oriental_jiyugaoka"]
        for date in (
            dt.date(2026, 7, 15),
            dt.date(2026, 9, 14),
            dt.date(2026, 9, 15),
        ):
            row = slot_atlas.forecast_one(
                hall, self.rules, date, dt.date(2026, 7, 14), self.holidays
            )
            self.assertEqual(row["rank"], "NO BET")
            self.assertEqual(row["reason"], "監視中（予測未解禁）")
            self.assertEqual(row["sample_n"], 0)

    def test_oriental_killed_date_rules_are_not_active(self):
        oriental_rules = [r for r in self.rules if r["hall_id"] == "toyoko_oriental_jiyugaoka"]
        self.assertEqual(sum(r.get("status") == "kill" for r in oriental_rules), 4)
        self.assertEqual(sum(r.get("status") == "reframe" for r in oriental_rules), 1)
        self.assertEqual(sum(r.get("status") == "pending" for r in oriental_rules), 1)

    def test_oriental_position_signals_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = slot_atlas.init_db(pathlib.Path(tmp) / "test.db")
            slot_atlas.seed_db(con, self.halls, self.rules)
            count = slot_atlas.seed_position_signals(
                con, ROOT / "seed" / "oriental_position_signals.csv"
            )
            self.assertEqual(count, 8)
            rows = con.execute(
                "SELECT COUNT(*), COUNT(DISTINCT result_date) FROM position_signals "
                "WHERE hall_id='toyoko_oriental_jiyugaoka'"
            ).fetchone()
            self.assertEqual(rows, (8, 3))

    def test_oriental_validation_queue_seed_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = slot_atlas.init_db(pathlib.Path(tmp) / "test.db")
            slot_atlas.seed_db(con, self.halls, self.rules)
            path = ROOT / "seed" / "validation_queue.json"
            expected = len(slot_atlas.read_json(path))
            self.assertEqual(slot_atlas.seed_validation_queue(con, path), expected)
            self.assertEqual(slot_atlas.seed_validation_queue(con, path), 0)
            count = con.execute(
                "SELECT COUNT(*) FROM validation_log "
                "WHERE hall_id='toyoko_oriental_jiyugaoka' AND verdict='pending'"
            ).fetchone()[0]
            self.assertEqual(count, 3)

    def test_mm_tsunashima_scope_is_resolved_but_candidates_stay_no_bet(self):
        hall = self.by_id["yokohama_slot_mm_tsunashima"]
        self.assertTrue(hall["forecast_enabled"])
        self.assertEqual(hall["slot_count"], 140)
        self.assertIn("20円140台", hall["exchange_label"])

        day16 = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 16), dt.date(2026, 7, 14), self.holidays
        )
        day23 = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 23), dt.date(2026, 7, 14), self.holidays
        )
        day6 = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 8, 6), dt.date(2026, 7, 14), self.holidays
        )
        self.assertEqual(day16["rule_id"], "mm_day16_reframed")
        self.assertAlmostEqual(day16["predicted_mean"], 46.4, places=1)
        self.assertEqual(day16["rank"], "NO BET")
        self.assertLess(day16["utility_edge"], 0)
        self.assertEqual(day23["rule_id"], "mm_day23_reframed")
        self.assertAlmostEqual(day23["predicted_mean"], 50.2, places=1)
        self.assertEqual(day23["rank"], "NO BET")
        self.assertLess(day23["utility_edge"], 0)
        self.assertEqual(day6["rule_id"], "mm_old_event_3_6_union")
        self.assertEqual(day6["rank"], "NO BET")

    def test_mm_tsunashima_raw_seed_is_complete(self):
        path = ROOT / "seed" / "mm_tsuna_hall_days.csv"
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 202)
        self.assertEqual(rows[0]["result_date"], "2025-12-05")
        self.assertEqual(rows[-1]["result_date"], "2026-06-24")
        self.assertEqual({int(r["total_units"]) for r in rows}, {140})

    def test_mm_tsunashima_machine_and_position_seeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = slot_atlas.init_db(pathlib.Path(tmp) / "test.db")
            slot_atlas.seed_db(con, self.halls, self.rules)
            machines = slot_atlas.seed_machine_scores(
                con, ROOT / "seed" / "mm_tsuna_machine_scores.csv"
            )
            positions = slot_atlas.seed_position_signals(
                con, ROOT / "seed" / "mm_tsuna_position_signals.csv"
            )
            self.assertEqual(machines, 64)
            self.assertEqual(positions, 40)

    def test_shinjuku_toho_21st_is_actionable(self):
        hall = self.by_id["fukutoshin_maruhan_shinjuku_toho"]
        row = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 21), dt.date(2026, 7, 14), self.holidays
        )
        self.assertEqual(row["rule_id"], "toho_day21")
        self.assertEqual(row["rank"], "S")

    def test_shinjuku_toho_20th_is_avoid(self):
        hall = self.by_id["fukutoshin_maruhan_shinjuku_toho"]
        row = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 20), dt.date(2026, 7, 14), self.holidays
        )
        self.assertEqual(row["rule_id"], "toho_day20_avoid")
        self.assertEqual(row["rank"], "NO BET")

    def test_shinjuku_toho_31st_is_actionable(self):
        hall = self.by_id["fukutoshin_maruhan_shinjuku_toho"]
        row = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 31), dt.date(2026, 7, 14), self.holidays
        )
        self.assertEqual(row["rule_id"], "toho_day31")
        self.assertEqual(row["rank"], "A")

    def test_shinjuku_toho_raw_seed_is_complete(self):
        path = ROOT / "seed" / "toho_hall_days.csv"
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 365)
        self.assertEqual(rows[0]["result_date"], "2025-07-13")
        self.assertEqual(rows[-1]["result_date"], "2026-07-12")

    def test_shinjuku_toho_position_signals_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = slot_atlas.init_db(pathlib.Path(tmp) / "test.db")
            slot_atlas.seed_db(con, self.halls, self.rules)
            count = slot_atlas.seed_position_signals(
                con, ROOT / "seed" / "toho_position_signals.csv"
            )
            self.assertEqual(count, 11)
            rows = con.execute(
                "SELECT COUNT(*), COUNT(DISTINCT event_name) FROM position_signals "
                "WHERE hall_id='fukutoshin_maruhan_shinjuku_toho'"
            ).fetchone()
            self.assertEqual(rows, (11, 1))

    def test_big_dipper_date_rules_and_travel_utility(self):
        hall = self.by_id["ikegami_big_dipper_togoshi_ginza"]
        normal = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 14), dt.date(2026, 7, 14), self.holidays
        )
        day5 = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 15), dt.date(2026, 7, 14), self.holidays
        )
        day7 = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 17), dt.date(2026, 7, 14), self.holidays
        )
        day20 = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 8, 20), dt.date(2026, 7, 14), self.holidays
        )
        month_day = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2027, 1, 1), dt.date(2026, 12, 31), self.holidays
        )
        self.assertEqual((normal["rank"], normal["utility_edge"]), ("NO BET", -7.1))
        self.assertEqual((day5["rule_id"], day5["rank"]), ("bd_digit_5_absolute", "B"))
        self.assertAlmostEqual(day5["predicted_mean"], 83.8, delta=0.1)
        self.assertEqual((day7["rule_id"], day7["rank"]), ("bd_digit_7_absolute", "B"))
        self.assertEqual((day20["rule_id"], day20["rank"]), ("bd_day_1_20_absolute", "A"))
        self.assertEqual(month_day["rule_id"], "bd_month_equals_day_avoid")
        self.assertEqual(month_day["rank"], "NO BET")

    def test_big_dipper_raw_seed_is_complete_and_negative_days_are_labeled(self):
        path = ROOT / "seed" / "big_dipper_hall_days.csv"
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 225)
        self.assertEqual(rows[0]["result_date"], "2025-12-01")
        self.assertEqual(rows[-1]["result_date"], "2026-07-13")
        self.assertEqual({int(row["total_units"]) for row in rows}, {286, 289, 291, 292, 293})
        sources = {name: sum(row["source_name"] == name for row in rows) for name in {
            "min-repo", "min-repo_rate_reconstructed"
        }}
        self.assertEqual(sources, {"min-repo": 146, "min-repo_rate_reconstructed": 79})

    def test_big_dipper_machine_tail_and_score_seeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = slot_atlas.init_db(pathlib.Path(tmp) / "test.db")
            slot_atlas.seed_db(con, self.halls, self.rules)
            machine_days = slot_atlas.seed_machine_days(
                con, ROOT / "seed" / "big_dipper_machine_days.csv"
            )
            tail_days = slot_atlas.seed_tail_days(
                con, ROOT / "seed" / "big_dipper_tail_days.csv"
            )
            scores = slot_atlas.seed_machine_scores(
                con, ROOT / "seed" / "big_dipper_machine_scores.csv"
            )
            positions = slot_atlas.seed_position_signals(
                con, ROOT / "seed" / "big_dipper_position_signals.csv"
            )
            self.assertEqual((machine_days, tail_days, scores, positions), (20229, 2475, 98, 10))
            self.assertEqual(
                slot_atlas.seed_machine_days(con, ROOT / "seed" / "big_dipper_machine_days.csv"), 0
            )
            self.assertEqual(
                slot_atlas.seed_tail_days(con, ROOT / "seed" / "big_dipper_tail_days.csv"), 0
            )
            censored = con.execute(
                "SELECT COUNT(*) FROM tail_days WHERE hall_id=? AND avg_diff IS NULL",
                ("ikegami_big_dipper_togoshi_ginza",),
            ).fetchone()[0]
            self.assertGreater(censored, 0)

    def test_killed_rule_is_not_used(self):
        hall = self.by_id["mizonokuchi_slot_act"]
        row = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 28), dt.date(2026, 7, 13), self.holidays
        )
        self.assertEqual(row["reason"], "通常ベース")

    def test_generate_has_365_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = pathlib.Path(tmp) / "test.db"
            out = pathlib.Path(tmp) / "out"
            con = slot_atlas.init_db(db)
            slot_atlas.seed_db(con, self.halls, self.rules)
            candidates, calendar = slot_atlas.generate(con, self.halls, self.rules, self.holidays, dt.date(2026, 7, 14), 365, dt.date(2026, 7, 14), out)
            self.assertEqual(len(calendar), 365)
            self.assertEqual(len(candidates), 365 * len([h for h in self.halls if h["active"]]))
            self.assertIn("ikegami_pick", calendar[0])
            self.assertIn("kamata_pick", calendar[0])
            self.assertIn("keikyu_pick", calendar[0])
            self.assertIn("ooimachi_pick", calendar[0])
            self.assertIn("yokohama_pick", calendar[0])
            self.assertIn("tsurumi_pick", calendar[0])
            self.assertIn("kawasaki_pick", calendar[0])
            self.assertIn("kashimada_pick", calendar[0])
            self.assertTrue((out / "calendar_365.csv").exists())

    def test_mitoya_fixed_days_and_travel_utility(self):
        hall = self.by_id["keikyu_mitoya_omorimachi"]
        day4 = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 24), dt.date(2026, 7, 14), self.holidays
        )
        day7 = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 17), dt.date(2026, 7, 14), self.holidays
        )
        anniversary = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 10), dt.date(2026, 7, 14), self.holidays
        )
        normal = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 16), dt.date(2026, 7, 14), self.holidays
        )
        self.assertEqual((day4["rule_id"], day4["rank"]), ("mt_day4_family", "A"))
        self.assertEqual((day7["rule_id"], day7["rank"]), ("mt_day7_family", "A"))
        self.assertEqual((anniversary["rule_id"], anniversary["rank"]), ("mt_anniversary_0710", "B"))
        self.assertEqual(normal["rank"], "NO BET")

    def test_mitoya_raw_seed_is_complete(self):
        path = ROOT / "seed" / "mitoya_omorimachi_hall_days.csv"
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 194)
        self.assertEqual(rows[0]["result_date"], "2026-01-01")
        self.assertEqual(rows[-1]["result_date"], "2026-07-13")
        self.assertEqual({int(row["total_units"]) for row in rows}, {266})


    def test_ooimachi_third_saturday_is_actionable(self):
        hall = self.by_id["ooimachi_big_dipper"]
        row = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 18), dt.date(2026, 7, 14), self.holidays
        )
        self.assertEqual((row["rule_id"], row["rank"]), ("od_third_saturday", "A"))

    def test_ooimachi_day7_is_monitor_only(self):
        hall = self.by_id["ooimachi_big_dipper"]
        row = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 17), dt.date(2026, 7, 14), self.holidays
        )
        self.assertEqual(row["rule_id"], "od_day7_family")
        self.assertEqual(row["rank"], "NO BET")

    def test_ooimachi_raw_seed_is_complete(self):
        path = ROOT / "seed" / "big_dipper_ooimachi_hall_days.csv"
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 194)
        self.assertEqual(rows[0]["result_date"], "2026-01-01")
        self.assertEqual(rows[-1]["result_date"], "2026-07-13")
        self.assertEqual({int(row["total_units"]) for row in rows}, {289})

    def test_ooimachi_machine_score_seed_has_15_rows(self):
        path = ROOT / "seed" / "big_dipper_ooimachi_machine_scores.csv"
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 15)


    def test_yokohama_123_is_monitor_only(self):
        hall = self.by_id["yokohama_123_west"]
        for target in (dt.date(2026, 7, 15), dt.date(2026, 7, 22), dt.date(2026, 12, 21)):
            row = slot_atlas.forecast_one(
                hall, self.rules, target, dt.date(2026, 7, 14), self.holidays
            )
            self.assertEqual(row["rank"], "NO BET")
            self.assertEqual(row["reason"], "監視中（予測未解禁）")
            self.assertIn("全店平均差枚", row["stale_warning"])

    def test_yokohama_selected_machine_scores_have_10_rows(self):
        path = ROOT / "seed" / "yokohama_123_machine_scores.csv"
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(row["baseline_avg_diff"] == "" for row in rows))
        self.assertTrue(all("店全体非代表" in row["type_label"] for row in rows))

    def test_yokohama_selected_position_signals_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = slot_atlas.init_db(pathlib.Path(tmp) / "test.db")
            slot_atlas.seed_db(con, self.halls, self.rules)
            count = slot_atlas.seed_position_signals(
                con, ROOT / "seed" / "yokohama_123_position_signals.csv"
            )
            self.assertEqual(count, 5)
            row = con.execute(
                "SELECT COUNT(*), SUM(unit_count), MIN(result_date), MAX(result_date) "
                "FROM position_signals WHERE hall_id='yokohama_123_west'"
            ).fetchone()
            self.assertEqual(row, (5, 36, "2026-07-07", "2026-07-07"))

    def test_yokohama_has_four_preregistered_validation_tasks(self):
        rows = json.loads((ROOT / "seed" / "validation_queue.json").read_text(encoding="utf-8"))
        mine = [r for r in rows if r["hall_id"] == "yokohama_123_west"]
        self.assertEqual(len(mine), 4)
        self.assertEqual({r["target_date"] for r in mine}, {
            "2026-07-15", "2026-07-22", "2026-07-25", "2026-12-21"
        })


    def test_yasuda7_day7_is_actionable_and_beats_ikebukuro_base(self):
        target = dt.date(2026, 7, 17)
        hall = self.by_id["ikebukuro_yasuda7"]
        row = slot_atlas.forecast_one(
            hall, self.rules, target, dt.date(2026, 7, 14), self.holidays
        )
        self.assertEqual((row["rule_id"], row["rank"]), ("ys7_day7_family", "S"))
        market_rows = [
            slot_atlas.forecast_one(h, self.rules, target, dt.date(2026, 7, 14), self.holidays)
            for h in self.halls if h["market"] == "副都心線北上"
        ]
        self.assertEqual(slot_atlas.select_best(market_rows)["hall_id"], "ikebukuro_yasuda7")

    def test_yasuda7_normal_and_day4_are_no_bet(self):
        hall = self.by_id["ikebukuro_yasuda7"]
        normal = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 16), dt.date(2026, 7, 14), self.holidays
        )
        day4 = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 24), dt.date(2026, 7, 14), self.holidays
        )
        self.assertEqual(normal["rank"], "NO BET")
        self.assertEqual((day4["rule_id"], day4["rank"]), ("ys7_day4_family_avoid", "NO BET"))

    def test_yasuda7_raw_seed_is_complete(self):
        path = ROOT / "seed" / "yasuda7_hall_days.csv"
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 194)
        self.assertEqual(rows[0]["result_date"], "2026-01-01")
        self.assertEqual(rows[-1]["result_date"], "2026-07-13")
        self.assertEqual({int(row["total_units"]) for row in rows}, {255})

    def test_yasuda7_machine_scores_and_validation_tasks(self):
        with (ROOT / "seed" / "yasuda7_machine_scores.csv").open(encoding="utf-8-sig", newline="") as fh:
            scores = list(csv.DictReader(fh))
        self.assertEqual(len(scores), 15)
        queue = json.loads((ROOT / "seed" / "validation_queue.json").read_text(encoding="utf-8"))
        mine = [r for r in queue if r["hall_id"] == "ikebukuro_yasuda7"]
        self.assertEqual(len(mine), 4)
        self.assertEqual({r["target_date"] for r in mine}, {
            "2026-07-17", "2026-07-27", "2026-08-07", "2026-09-10"
        })

    def test_tsurumi_uno_regular_tenth_is_conservative_b(self):
        hall = self.by_id["tsurumi_uno"]
        row = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 9, 10), dt.date(2026, 7, 14), self.holidays
        )
        self.assertEqual((row["rule_id"], row["rank"]), ("tu_day10_regular", "B"))
        self.assertGreater(row["utility_edge"], 0)

    def test_tsurumi_uno_anniversary_and_old_event_separation(self):
        hall = self.by_id["tsurumi_uno"]
        anniversary = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2027, 7, 10), dt.date(2027, 7, 1), self.holidays
        )
        day7 = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 17), dt.date(2026, 7, 14), self.holidays
        )
        day1 = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 8, 1), dt.date(2026, 7, 14), self.holidays
        )
        self.assertEqual(anniversary["rule_id"], "tu_anniversary_0710")
        self.assertEqual((day7["rule_id"], day7["rank"]), ("tu_day7_family_avoid", "NO BET"))
        self.assertEqual((day1["rule_id"], day1["rank"]), ("tu_day1_family_monitor", "NO BET"))

    def test_tsurumi_uno_raw_seed_and_machine_snapshot(self):
        with (ROOT / "seed" / "tsurumi_uno_hall_days.csv").open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 194)
        self.assertEqual(rows[0]["result_date"], "2026-01-01")
        self.assertEqual(rows[-1]["result_date"], "2026-07-13")
        self.assertEqual({int(row["total_units"]) for row in rows}, {281})
        with (ROOT / "seed" / "tsurumi_uno_machine_scores.csv").open(encoding="utf-8-sig", newline="") as fh:
            scores = list(csv.DictReader(fh))
        self.assertEqual(len(scores), 15)
        self.assertTrue(all("予測" in row["notes"] for row in scores))

    def test_tsurumi_uno_has_four_validation_tasks(self):
        queue = json.loads((ROOT / "seed" / "validation_queue.json").read_text(encoding="utf-8"))
        mine = [r for r in queue if r["hall_id"] == "tsurumi_uno"]
        self.assertEqual(len(mine), 4)
        self.assertEqual({r["target_date"] for r in mine}, {
            "2026-08-10", "2026-09-10", "2026-10-10", "2027-07-10"
        })

    def test_aviva_tsurumi_is_monitor_only(self):
        hall = self.by_id["tsurumi_aviva"]
        self.assertFalse(hall["forecast_enabled"])
        self.assertIn("全店平均差枚", hall["forecast_block_reason"])
        for date in (dt.date(2026, 7, 31), dt.date(2026, 8, 8), dt.date(2026, 8, 10), dt.date(2026, 8, 22)):
            row = slot_atlas.forecast_one(
                hall, self.rules, date, dt.date(2026, 7, 14), self.holidays
            )
            self.assertEqual(row["rank"], "NO BET")
            self.assertEqual(row["reason"], "監視中（予測未解禁）")
            self.assertEqual(row["sample_n"], 0)

    def test_aviva_tsurumi_selected_signal_seeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = slot_atlas.init_db(pathlib.Path(tmp) / "test.db")
            slot_atlas.seed_db(con, self.halls, self.rules)
            machines = slot_atlas.seed_machine_scores(
                con, ROOT / "seed" / "aviva_tsurumi_machine_scores.csv"
            )
            positions = slot_atlas.seed_position_signals(
                con, ROOT / "seed" / "aviva_tsurumi_position_signals.csv"
            )
            self.assertEqual((machines, positions), (10, 27))
            dates = con.execute(
                "SELECT COUNT(DISTINCT result_date) FROM position_signals WHERE hall_id=?",
                ("tsurumi_aviva",),
            ).fetchone()[0]
            self.assertEqual(dates, 3)



if __name__ == "__main__":
    unittest.main()
