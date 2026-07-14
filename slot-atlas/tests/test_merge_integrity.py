import csv
import datetime as dt
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import slot_atlas


class MergeIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.halls = json.loads((ROOT / "seed" / "halls.json").read_text(encoding="utf-8"))
        cls.rules = json.loads((ROOT / "seed" / "rules.json").read_text(encoding="utf-8"))
        cls.holidays = json.loads((ROOT / "seed" / "holidays.json").read_text(encoding="utf-8"))
        cls.by_id = {h["hall_id"]: h for h in cls.halls}

    def test_merged_hall_set_has_35_unique_halls(self):
        self.assertEqual(len(self.halls), 35)
        self.assertEqual(len(self.by_id), 35)
        self.assertIn("kamata_rakuen", self.by_id)
        self.assertIn("ikebukuro_yasuda7", self.by_id)
        self.assertIn("tsurumi_uno", self.by_id)
        self.assertIn("tsurumi_aviva", self.by_id)
        self.assertIn("tsurumi_maruhan", self.by_id)
        self.assertIn("tsurumi_kintoki", self.by_id)
        self.assertIn("tsurumi_nanbusen_shitte", self.by_id)
        self.assertIn("kawasaki_access", self.by_id)
        self.assertIn("center_minami_ziath", self.by_id)
        self.assertIn("kashimada_asahi2", self.by_id)
        self.assertIn("tsurumi_kiccho_komaoka", self.by_id)
        self.assertIn("tsurumi_kicona_chuo", self.by_id)
        self.assertIn("tsurumi_superseven_namamugi", self.by_id)

    def test_uploaded_kamata1_record_wins_conflict(self):
        hall = self.by_id["kamata_maruhan_mega1"]
        self.assertTrue(hall["forecast_enabled"])
        self.assertEqual(hall["baseline_n"], 130)
        self.assertEqual(hall["slot_count"], 350)
        self.assertEqual(hall["data_through"], "2026-07-13")
        rule_ids = {r["rule_id"] for r in self.rules if r["hall_id"] == hall["hall_id"]}
        self.assertTrue({"k1_day11", "k1_day30", "k1_day7family"}.issubset(rule_ids))

    def test_uploaded_raw_seeds_are_preserved(self):
        expected = {"kamata1_hall_days.csv": 776, "rakuen_kamata_hall_days.csv": 193}
        for name, count in expected.items():
            with (ROOT / "seed" / name).open(encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), count, name)

    def test_rakuen_kamata_stays_monitor_only(self):
        hall = self.by_id["kamata_rakuen"]
        self.assertFalse(hall["forecast_enabled"])
        row = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 8, 22), dt.date(2026, 7, 14), self.holidays
        )
        self.assertEqual(row["rank"], "NO BET")
        self.assertEqual(row["reason"], "監視中（予測未解禁）")

    def test_latest_four_halls_and_uploaded_two_halls_coexist(self):
        ids = {h["hall_id"] for h in self.halls}
        self.assertTrue({
            "kamata_maruhan_mega1", "kamata_rakuen",
            "keikyu_mitoya_omorimachi", "ooimachi_big_dipper",
            "yokohama_123_west", "ikebukuro_yasuda7", "tsurumi_uno", "tsurumi_aviva", "tsurumi_maruhan", "tsurumi_kintoki", "tsurumi_nanbusen_shitte"
        }.issubset(ids))

    def test_maruhan_tsurumi_daily_seed_is_complete(self):
        with (ROOT / "seed" / "maruhan_tsurumi_hall_days.csv").open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 194)
        self.assertEqual(rows[0]["result_date"], "2026-01-01")
        self.assertEqual(rows[-1]["result_date"], "2026-07-13")
        self.assertTrue(all(int(row["total_units"]) == 452 for row in rows))

    def test_maruhan_tsurumi_day7_is_explicit_no_bet(self):
        hall = self.by_id["tsurumi_maruhan"]
        row = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 17), dt.date(2026, 7, 14), self.holidays
        )
        self.assertEqual(row["rule_id"], "mt_day7_family_avoid")
        self.assertEqual(row["rank"], "NO BET")
        self.assertLess(row["utility_edge"], 0)

    def test_maruhan_tsurumi_regular_25_is_not_anniversary(self):
        hall = self.by_id["tsurumi_maruhan"]
        row = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 7, 25), dt.date(2026, 7, 14), self.holidays
        )
        self.assertEqual(row["rule_id"], "mt_day25_regular_avoid")
        self.assertEqual(row["rank"], "NO BET")

    def test_maruhan_tsurumi_anniversary_is_separated(self):
        hall = self.by_id["tsurumi_maruhan"]
        row = slot_atlas.forecast_one(
            hall, self.rules, dt.date(2026, 5, 25), dt.date(2026, 5, 20), self.holidays
        )
        self.assertEqual(row["rule_id"], "mt_anniversary_0525")
        self.assertAlmostEqual(row["predicted_mean"], 123.7, places=1)
        self.assertEqual(row["rank"], "B")


    def test_kintoki_tsurumi_daily_seed_has_single_public_gap(self):
        with (ROOT / "seed" / "kintoki_tsurumi_hall_days.csv").open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 193)
        self.assertEqual(rows[0]["result_date"], "2026-01-01")
        self.assertEqual(rows[-1]["result_date"], "2026-07-13")
        self.assertNotIn("2026-01-29", {r["result_date"] for r in rows})
        self.assertEqual(next(r for r in rows if r["result_date"] == "2026-04-18")["total_units"], "189")
        self.assertEqual(next(r for r in rows if r["result_date"] == "2026-04-19")["total_units"], "209")

    def test_kintoki_regular_five_is_no_bet(self):
        hall = self.by_id["tsurumi_kintoki"]
        row = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 8, 5), dt.date(2026, 7, 14), self.holidays)
        self.assertEqual(row["rule_id"], "kt_day5_monitor")
        self.assertEqual(row["rank"], "NO BET")
        self.assertLess(row["utility_edge"], 0)

    def test_kintoki_sunday_is_explicit_avoid(self):
        hall = self.by_id["tsurumi_kintoki"]
        row = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 7, 19), dt.date(2026, 7, 14), self.holidays)
        self.assertEqual(row["rule_id"], "kt_sunday_avoid")
        self.assertEqual(row["rank"], "NO BET")

    def test_kintoki_anniversary_is_separated_but_not_promoted(self):
        hall = self.by_id["tsurumi_kintoki"]
        row = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 4, 15), dt.date(2026, 4, 10), self.holidays)
        self.assertEqual(row["rule_id"], "kt_anniversary_0415")
        self.assertEqual(row["rank"], "NO BET")
        self.assertLess(row["predicted_mean"], 0)

    def test_nanbusen_shitte_day27_seed_is_complete(self):
        with (ROOT / "seed" / "nanbusen_shitte_hall_days.csv").open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["result_date"], "2026-01-27")
        self.assertAlmostEqual(float(rows[0]["avg_diff"]), -267.8875, places=4)
        self.assertEqual(rows[-1]["result_date"], "2026-06-27")
        self.assertTrue(all(int(row["total_units"]) == 80 for row in rows))

    def test_nanbusen_shitte_day27_is_a_candidate(self):
        hall = self.by_id["tsurumi_nanbusen_shitte"]
        row = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 7, 27), dt.date(2026, 7, 14), self.holidays)
        self.assertEqual(row["rule_id"], "ns_day27")
        self.assertAlmostEqual(row["predicted_mean"], 99.3, places=1)
        self.assertEqual(row["rank"], "A")
        self.assertGreater(row["utility_edge"], 70)

    def test_nanbusen_shitte_day7_and_day17_are_not_promoted(self):
        hall = self.by_id["tsurumi_nanbusen_shitte"]
        for target in (dt.date(2026, 8, 7), dt.date(2026, 8, 17)):
            row = slot_atlas.forecast_one(hall, self.rules, target, dt.date(2026, 7, 14), self.holidays)
            self.assertIsNone(row["rule_id"])
            self.assertEqual(row["rank"], "NO BET")
            self.assertLess(row["utility_edge"], 0)

    def test_nanbusen_shitte_anniversary_stays_no_bet(self):
        hall = self.by_id["tsurumi_nanbusen_shitte"]
        row = slot_atlas.forecast_one(hall, self.rules, dt.date(2027, 6, 20), dt.date(2027, 6, 10), self.holidays)
        self.assertEqual(row["rule_id"], "ns_anniversary_0620_monitor")
        self.assertEqual(row["rank"], "NO BET")

    def test_access_kawasaki_exact_positive_seed_is_not_a_fake_complete_sample(self):
        with (ROOT / "seed" / "access_kawasaki_hall_days.csv").open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["result_date"] for r in rows], ["2026-04-13", "2026-06-25", "2026-07-13"])
        self.assertTrue(all("positive_event_only" in r["source_name"] for r in rows))

    def test_access_kawasaki_13th_and_25th_stay_no_bet(self):
        hall = self.by_id["kawasaki_access"]
        day13 = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 8, 13), dt.date(2026, 7, 14), self.holidays)
        day25 = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 7, 25), dt.date(2026, 7, 14), self.holidays)
        self.assertEqual(day13["rule_id"], "ak_day13_2026_sign_audit")
        self.assertEqual(day25["rule_id"], "ak_day25_2026_sign_audit")
        self.assertEqual(day13["rank"], "NO BET")
        self.assertEqual(day25["rank"], "NO BET")
        self.assertLess(day13["utility_edge"], 0)
        self.assertLess(day25["utility_edge"], 0)

    def test_access_kawasaki_anniversary_is_monitor_only(self):
        hall = self.by_id["kawasaki_access"]
        row = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 11, 13), dt.date(2026, 11, 1), self.holidays)
        self.assertEqual(row["rule_id"], "ak_anniversary_1113_monitor")
        self.assertEqual(row["rank"], "NO BET")

    def test_access_kawasaki_has_four_validation_tasks(self):
        queue = json.loads((ROOT / "seed" / "validation_queue.json").read_text(encoding="utf-8"))
        mine = [r for r in queue if r["hall_id"] == "kawasaki_access"]
        self.assertEqual(len(mine), 4)

    def test_ziath_center_minami_event_seed_is_full_for_11th_and_21st(self):
        with (ROOT / "seed" / "ziath_center_minami_hall_days.csv").open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 14)
        self.assertEqual(len([r for r in rows if r["result_date"].endswith("-11")]), 7)
        self.assertEqual(len([r for r in rows if r["result_date"].endswith("-21")]), 6)
        may11 = next(r for r in rows if r["result_date"] == "2026-05-11")
        jul11 = next(r for r in rows if r["result_date"] == "2026-07-11")
        self.assertAlmostEqual(float(may11["avg_diff"]), -169.1303, places=3)
        self.assertAlmostEqual(float(jul11["avg_diff"]), -318.116, places=3)

    def test_ziath_center_minami_21st_and_7family_are_no_bet(self):
        hall = self.by_id["center_minami_ziath"]
        day21 = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 7, 21), dt.date(2026, 7, 14), self.holidays)
        day7 = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 7, 17), dt.date(2026, 7, 14), self.holidays)
        self.assertEqual(day21["rule_id"], "zc_day21_2026")
        self.assertEqual(day7["rule_id"], "zc_day7_family_2026_sign_audit")
        self.assertEqual(day21["rank"], "NO BET")
        self.assertEqual(day7["rank"], "NO BET")
        self.assertLess(day21["utility_edge"], 0)
        self.assertLess(day7["utility_edge"], 0)

    def test_ziath_center_minami_standard_11th_is_b_but_august_is_downgraded(self):
        hall = self.by_id["center_minami_ziath"]
        sep11 = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 9, 11), dt.date(2026, 7, 14), self.holidays)
        aug11 = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 8, 11), dt.date(2026, 7, 14), self.holidays)
        self.assertEqual(sep11["rule_id"], "zc_day11_2026")
        self.assertAlmostEqual(sep11["predicted_mean"], 67.3, places=1)
        self.assertEqual(sep11["rank"], "B")
        self.assertEqual(aug11["rank"], "NO BET")
        self.assertIn("祝日", aug11["risk_flags"])

    def test_ziath_center_minami_has_five_validation_tasks(self):
        queue = json.loads((ROOT / "seed" / "validation_queue.json").read_text(encoding="utf-8"))
        mine = [r for r in queue if r["hall_id"] == "center_minami_ziath"]
        self.assertEqual(len(mine), 5)

    def test_bellcity_kawasaki_has_complete_2026_8day_sample(self):
        with (ROOT / "seed" / "bellcity_kawasaki_hall_days.csv").open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        rows2026 = [r for r in rows if r["result_date"].startswith("2026-")]
        self.assertEqual(len(rows2026), 19)
        self.assertAlmostEqual(sum(float(r["avg_diff"]) for r in rows2026) / 19, -58.0526, places=3)

    def test_bellcity_weekend_8days_are_killed(self):
        hall = self.by_id["kawasaki_bellcity"]
        jul18 = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 7, 18), dt.date(2026, 7, 14), self.holidays)
        aug8 = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 8, 8), dt.date(2026, 7, 14), self.holidays)
        self.assertEqual(jul18["rule_id"], "bc_8day_saturday_kill")
        self.assertEqual(aug8["rule_id"], "bc_8day_saturday_kill")
        self.assertEqual(jul18["rank"], "NO BET")
        self.assertEqual(aug8["rank"], "NO BET")

    def test_bellcity_weekday_8day_is_a_trial(self):
        hall = self.by_id["kawasaki_bellcity"]
        row = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 8, 18), dt.date(2026, 7, 14), self.holidays)
        self.assertEqual(row["rule_id"], "bc_8day_weekday_recent_regime")
        self.assertAlmostEqual(row["predicted_mean"], 77.0, places=1)
        self.assertEqual(row["rank"], "A")

    def test_bellcity_has_six_validation_tasks(self):
        queue = json.loads((ROOT / "seed" / "validation_queue.json").read_text(encoding="utf-8"))
        mine = [r for r in queue if r["hall_id"] == "kawasaki_bellcity"]
        self.assertEqual(len(mine), 6)

    def test_asahi2_complete_2_12_22_seed_and_reconstructions(self):
        with (ROOT / "seed" / "asahi2_kashimada_hall_days.csv").open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        event_rows = [r for r in rows if r["result_date"].startswith("2026-")]
        self.assertEqual(len(event_rows), 20)
        may22 = next(r for r in rows if r["result_date"] == "2026-05-22")
        jan12 = next(r for r in rows if r["result_date"] == "2026-01-12")
        self.assertAlmostEqual(float(may22["avg_diff"]), -401.4326923, places=4)
        self.assertAlmostEqual(float(jan12["avg_diff"]), -229.0192308, places=4)
        self.assertTrue(all(int(r["total_units"]) == 104 for r in rows))

    def test_asahi2_day2_is_a_candidate(self):
        hall = self.by_id["kashimada_asahi2"]
        row = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 8, 2), dt.date(2026, 7, 14), self.holidays)
        self.assertEqual(row["rule_id"], "a2_day02_2026")
        self.assertAlmostEqual(row["predicted_mean"], 136.6, places=1)
        self.assertEqual(row["rank"], "A")
        self.assertGreater(row["utility_edge"], 100)

    def test_asahi2_day12_and_near_day22_are_not_promoted(self):
        hall = self.by_id["kashimada_asahi2"]
        day12 = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 8, 12), dt.date(2026, 7, 14), self.holidays)
        day22 = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 7, 22), dt.date(2026, 7, 14), self.holidays)
        self.assertEqual(day12["rule_id"], "a2_day12_2026")
        self.assertEqual(day12["rank"], "NO BET")
        self.assertEqual(day22["rule_id"], "a2_day22_2026")
        self.assertEqual(day22["rank"], "C")
        self.assertIsNotNone(day22["stale_warning"])

    def test_asahi2_anniversary_and_validation_queue_are_separated(self):
        hall = self.by_id["kashimada_asahi2"]
        row = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 12, 28), dt.date(2026, 7, 14), self.holidays)
        self.assertEqual(row["rule_id"], "a2_anniversary_1228")
        self.assertEqual(row["rank"], "NO BET")
        queue = json.loads((ROOT / "seed" / "validation_queue.json").read_text(encoding="utf-8"))
        self.assertEqual(len([r for r in queue if r["hall_id"] == "kashimada_asahi2"]), 6)


    def test_kiccho_komaoka_is_monitor_only_with_current_rate_scope(self):
        hall = self.by_id["tsurumi_kiccho_komaoka"]
        self.assertFalse(hall["forecast_enabled"])
        self.assertEqual(hall["slot_count"], 750)
        self.assertIn("5円スロ66台", hall["exchange_label"])
        row = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 7, 18), dt.date(2026, 7, 14), self.holidays)
        self.assertEqual(row["rank"], "NO BET")
        self.assertEqual(row["reason"], "監視中（予測未解禁）")

    def test_kiccho_komaoka_selected_blocks_are_not_full_hall_days(self):
        path = ROOT / "seed" / "kiccho_komaoka_position_signals.csv"
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 14)
        self.assertTrue(all("selected_blocks_not_full_hall" in r["rate_scope"] for r in rows))
        self.assertEqual(next(r for r in rows if r["machine_key"] == "smart_god")["unit_numbers"], "898|899|900|901")

    def test_kiccho_komaoka_has_five_validation_tasks(self):
        queue = json.loads((ROOT / "seed" / "validation_queue.json").read_text(encoding="utf-8"))
        mine = [r for r in queue if r["hall_id"] == "tsurumi_kiccho_komaoka"]
        self.assertEqual(len(mine), 5)
        self.assertEqual({r["target_date"] for r in mine}, {"2026-07-18", "2026-07-25", "2026-07-28", "2026-08-08", "2026-12-26"})


    def test_kicona_tsurumi_chuo_is_monitor_only(self):
        hall = self.by_id["tsurumi_kicona_chuo"]
        self.assertFalse(hall["forecast_enabled"])
        self.assertEqual(hall["slot_count"], 185)
        row = slot_atlas.forecast_one(hall, self.rules, dt.date(2026, 7, 23), dt.date(2026, 7, 14), self.holidays)
        self.assertEqual(row["rank"], "NO BET")
        self.assertEqual(row["reason"], "監視中（予測未解禁）")

    def test_kicona_tsurumi_chuo_rules_cannot_fire(self):
        mine = [r for r in self.rules if r["hall_id"] == "tsurumi_kicona_chuo"]
        self.assertEqual(len(mine), 4)
        self.assertTrue(all(r["status"] != "active" or int(r.get("sample_n") or 0) == 0 for r in mine))

    def test_kicona_tsurumi_chuo_has_five_validation_tasks_and_ten_machine_mentions(self):
        queue = json.loads((ROOT / "seed" / "validation_queue.json").read_text(encoding="utf-8"))
        self.assertEqual(len([r for r in queue if r["hall_id"] == "tsurumi_kicona_chuo"]), 5)
        with (ROOT / "seed" / "kicona_tsurumi_chuo_machine_scores.csv").open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(r["baseline_days"] == "0" for r in rows))

    def test_superseven_namamugi_is_monitor_only(self):
        hall = self.by_id["tsurumi_superseven_namamugi"]
        self.assertFalse(hall["forecast_enabled"])
        self.assertEqual(hall["slot_count"], 126)
        for target in (dt.date(2026, 7, 17), dt.date(2026, 7, 27), dt.date(2027, 7, 7)):
            row = slot_atlas.forecast_one(hall, self.rules, target, dt.date(2026, 7, 14), self.holidays)
            self.assertEqual(row["rank"], "NO BET")
            self.assertEqual(row["reason"], "監視中（予測未解禁）")
            self.assertEqual(row["sample_n"], 0)

    def test_superseven_pending_rules_never_fire(self):
        rules = [r for r in self.rules if r["hall_id"] == "tsurumi_superseven_namamugi"]
        self.assertEqual(len(rules), 4)
        self.assertTrue(all(r["status"] in {"pending", "reframe"} for r in rules))
        self.assertTrue(all(r["sample_n"] >= 0 for r in rules))

    def test_superseven_machine_monitoring_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = slot_atlas.init_db(pathlib.Path(tmp) / "test.db")
            slot_atlas.seed_db(con, self.halls, self.rules)
            count = slot_atlas.seed_machine_scores(con, ROOT / "seed" / "superseven_namamugi_machine_scores.csv")
            self.assertEqual(count, 5)


    def test_prest_hirama2_is_monitor_only(self):
        hall = self.by_id["hirama_prest2"]
        self.assertFalse(hall["forecast_enabled"])
        self.assertEqual(hall["slot_count"], 144)
        for target in (dt.date(2026, 7, 17), dt.date(2026, 7, 23), dt.date(2026, 12, 8)):
            row = slot_atlas.forecast_one(hall, self.rules, target, dt.date(2026, 7, 15), self.holidays)
            self.assertEqual(row["rank"], "NO BET")
            self.assertEqual(row["reason"], "監視中（予測未解禁）")

    def test_prest_hirama2_pending_rules_never_fire(self):
        rules = [r for r in self.rules if r["hall_id"] == "hirama_prest2"]
        self.assertEqual(len(rules), 6)
        self.assertTrue(all(r["status"] in {"pending", "reframe"} for r in rules))

    def test_prest_hirama2_machine_scores_and_validation_tasks(self):
        with (ROOT / "seed" / "prest_hirama2_machine_scores.csv").open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 10)
        queue = json.loads((ROOT / "seed" / "validation_queue.json").read_text(encoding="utf-8"))
        mine = [r for r in queue if r["hall_id"] == "hirama_prest2"]
        self.assertEqual(len(mine), 6)


if __name__ == "__main__":
    unittest.main()
