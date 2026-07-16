"""Phase 1A acceptance tests for normalization and source lineage.

Tests cover: raw_sources, machines master, event_families extraction,
hall_capabilities computation, and hall_days backfill.
Self-contained: creates temp DB and files, no external dependencies.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from migrate_db import migrate
from normalize_sources import (
    populate_machines,
    populate_machine_aliases,
    populate_raw_sources,
)
from build_event_families import (
    build_families,
    date_matches_rule,
    family_type_from_match_json,
)
from build_capabilities import compute_capabilities, persist_capabilities


def make_test_db(path: Path) -> sqlite3.Connection:
    """Create a test DB with hall_days, machine_days, evidence_rules."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE halls (
            hall_id TEXT PRIMARY KEY,
            name TEXT, market TEXT, active INTEGER DEFAULT 1,
            forecast_enabled INTEGER DEFAULT 1,
            decision_floor REAL DEFAULT 0,
            travel_minutes REAL DEFAULT 0,
            data_through TEXT, reset_policy TEXT
        );
        CREATE TABLE hall_days (
            hall_id TEXT, result_date TEXT, avg_diff REAL,
            total_diff REAL, avg_games REAL,
            source_name TEXT DEFAULT 'test',
            PRIMARY KEY(hall_id, result_date, source_name)
        );
        CREATE TABLE machine_days (
            hall_id TEXT, result_date TEXT, machine_key TEXT,
            machine_name TEXT, units INTEGER, avg_diff REAL,
            avg_games REAL,
            source_name TEXT DEFAULT 'test',
            PRIMARY KEY(hall_id, result_date, machine_key, source_name)
        );
        CREATE TABLE tail_days (
            hall_id TEXT, result_date TEXT, tail_key TEXT,
            avg_diff REAL, source_name TEXT DEFAULT 'test',
            PRIMARY KEY(hall_id, result_date, tail_key, source_name)
        );
        CREATE TABLE unit_days (
            hall_id TEXT, result_date TEXT, unit_no INTEGER,
            avg_diff REAL,
            PRIMARY KEY(hall_id, result_date, unit_no)
        );
        CREATE TABLE source_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            hall_id TEXT, source_name TEXT NOT NULL,
            source_url TEXT NOT NULL, fetched_at TEXT NOT NULL,
            http_status INTEGER, content_sha256 TEXT,
            payload_path TEXT, parse_status TEXT NOT NULL,
            error_message TEXT
        );
        CREATE TABLE evidence_rules (
            hall_id TEXT, match_json TEXT, label TEXT,
            confidence REAL, regime_separated INTEGER DEFAULT 0
        );

        INSERT INTO halls VALUES
            ('hall_a','Hall A','tokyo',1,1,0,10,NULL,'据え置き'),
            ('hall_b','Hall B','tokyo',1,1,0,20,NULL,NULL),
            ('hall_c','Hall C','tokyo',1,1,0,15,NULL,NULL);

        INSERT INTO hall_days VALUES
            ('hall_a','2026-07-07',500,5000,8000,'src1'),
            ('hall_a','2026-07-11',300,3000,7500,'src1'),
            ('hall_a','2026-07-17',450,4500,8200,'src1'),
            ('hall_a','2026-07-22',400,4000,8100,'src1'),
            ('hall_b','2026-07-07',200,2000,6000,'src1'),
            ('hall_b','2026-07-11',100,1000,5500,'src1');

        INSERT INTO machine_days VALUES
            ('hall_a','2026-07-07','mk_001','スマスロ北斗',10,500,8000,'src1'),
            ('hall_a','2026-07-07','mk_002','ヴヴヴ',5,300,7000,'src1'),
            ('hall_a','2026-07-11','mk_001','スマスロ北斗',10,600,8500,'src1'),
            ('hall_a','2026-07-11','mk_003','からくり',8,200,6000,'src1');

        INSERT INTO tail_days VALUES
            ('hall_a','2026-07-07','7',600,'src1'),
            ('hall_a','2026-07-11','1',300,'src1');

        INSERT INTO source_snapshots VALUES
            (1,'hall_a','slotdata','https://example.com/a',
             '2026-07-07T10:00:00',200,'abc123','/data/a.json','ok',NULL),
            (2,'hall_b','slotdata','https://example.com/b',
             '2026-07-07T10:01:00',200,'def456','/data/b.json','ok',NULL),
            (3,'hall_a','slotdata','https://example.com/a',
             '2026-07-11T10:00:00',200,'ghi789','/data/a2.json','ok',NULL);

        INSERT INTO evidence_rules VALUES
            ('hall_a','{"day_in": [7, 17, 27]}','7のつく日',0.8,0),
            ('hall_a','{"is_repdigit_day": true}','ゾロ目',0.7,0),
            ('hall_a','{"month_equals_day": true}','月日ゾロ目',0.6,0),
            ('hall_b','{"day": 11}','11日',0.75,0),
            ('hall_b','{"day": 22}','22日',0.75,0),
            ('hall_b','{"month_equals_day": true}','月日ゾロ目',0.6,0);
    """)
    conn.commit()
    return conn


class TestMigrationPhase1A(unittest.TestCase):
    """Phase 1A tables are created by migration."""

    def test_tables_created(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            make_test_db(db)
            migrate(db)
            conn = sqlite3.connect(str(db))
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for t in ("raw_sources", "machines", "hall_aliases",
                       "machine_aliases", "event_families",
                       "hall_capabilities"):
                self.assertIn(t, tables, f"missing table: {t}")
            conn.close()

    def test_backfill_columns_added(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            make_test_db(db)
            migrate(db)
            conn = sqlite3.connect(str(db))
            hd_cols = {
                r[1] for r in conn.execute("PRAGMA table_info(hall_days)")
            }
            self.assertIn("event_family_id", hd_cols)
            md_cols = {
                r[1] for r in conn.execute("PRAGMA table_info(machine_days)")
            }
            self.assertIn("coverage", md_cols)
            self.assertIn("label_status", md_cols)
            conn.close()

    def test_migration_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            make_test_db(db)
            a1 = migrate(db)
            a2 = migrate(db)
            self.assertEqual(len(a1), len(a2))


class TestNormalizeSources(unittest.TestCase):
    """raw_sources populated from source_snapshots."""

    def test_snapshots_migrated(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            n = populate_raw_sources(conn)
            conn.commit()
            self.assertEqual(n, 3)

            rows = conn.execute(
                "SELECT raw_source_id, acquisition_method FROM raw_sources"
            ).fetchall()
            self.assertEqual(len(rows), 3)
            for _, method in rows:
                self.assertEqual(method, "automated_public")
            conn.close()

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            n1 = populate_raw_sources(conn)
            conn.commit()
            n2 = populate_raw_sources(conn)
            conn.commit()
            self.assertEqual(n1, 3)
            self.assertEqual(n2, 0)
            conn.close()


class TestMachinesMaster(unittest.TestCase):
    """machines table populated from machine_days."""

    def test_machines_extracted(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            n = populate_machines(conn)
            conn.commit()
            self.assertEqual(n, 3)

            names = {
                r[0] for r in
                conn.execute("SELECT canonical_name FROM machines")
            }
            self.assertIn("スマスロ北斗", names)
            self.assertIn("ヴヴヴ", names)
            self.assertIn("からくり", names)
            conn.close()

    def test_machine_aliases_created(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            populate_machines(conn)
            n = populate_machine_aliases(conn)
            conn.commit()
            self.assertGreater(n, 0)

            aliases = conn.execute(
                "SELECT source_name, machine_id FROM machine_aliases"
            ).fetchall()
            machine_ids = {a[1] for a in aliases}
            self.assertTrue(len(machine_ids) > 0)
            conn.close()

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            n1 = populate_machines(conn)
            conn.commit()
            n2 = populate_machines(conn)
            conn.commit()
            self.assertEqual(n1, 3)
            self.assertEqual(n2, 0)
            conn.close()


class TestFamilyTypeMapping(unittest.TestCase):
    """family_type_from_match_json maps patterns correctly."""

    def test_always_is_normal(self):
        self.assertEqual(family_type_from_match_json({"always": True}), "通常")
        self.assertEqual(family_type_from_match_json({}), "通常")

    def test_month_equals_day(self):
        self.assertEqual(
            family_type_from_match_json({"month_equals_day": True}), "月=日"
        )

    def test_repdigit(self):
        self.assertEqual(
            family_type_from_match_json({"is_repdigit_day": True}), "ゾロ目"
        )

    def test_day_in_same_mod(self):
        self.assertEqual(
            family_type_from_match_json({"day_in": [7, 17, 27]}),
            "7のつく日",
        )

    def test_day_mod10(self):
        self.assertEqual(
            family_type_from_match_json({"day_mod10": 3}), "3のつく日"
        )

    def test_specific_day_repdigit(self):
        self.assertEqual(
            family_type_from_match_json({"day": 11}), "ゾロ目"
        )
        self.assertEqual(
            family_type_from_match_json({"day": 22}), "ゾロ目"
        )

    def test_specific_day_normal(self):
        self.assertEqual(
            family_type_from_match_json({"day": 17}), "7のつく日"
        )
        self.assertEqual(
            family_type_from_match_json({"day": 5}), "5のつく日"
        )

    def test_anniversary(self):
        self.assertEqual(
            family_type_from_match_json({"day": 15, "month": 7}),
            "記念日(7/15)",
        )

    def test_weekday(self):
        self.assertEqual(
            family_type_from_match_json({"weekday": 5}), "土曜日"
        )
        self.assertEqual(
            family_type_from_match_json({"weekday": 6}), "日曜日"
        )

    def test_nth_weekday(self):
        self.assertEqual(
            family_type_from_match_json({"nth_weekday": 2, "weekday": 5}),
            "第2土曜",
        )

    def test_event_name(self):
        result = family_type_from_match_json(
            {"event_name": "ちゅんげー玉調査隊来店"}
        )
        self.assertTrue(result.startswith("イベント:"))

    def test_month_end(self):
        self.assertEqual(
            family_type_from_match_json({"month_end": True}), "月末"
        )

    def test_rokuyo(self):
        self.assertEqual(
            family_type_from_match_json({"rokuyo": "大安"}), "六曜:大安"
        )


class TestDateMatchesRule(unittest.TestCase):
    """date_matches_rule evaluates match_json against dates."""

    def test_always(self):
        self.assertTrue(date_matches_rule("2026-07-07", {"always": True}))
        self.assertTrue(date_matches_rule("2026-07-07", {}))

    def test_specific_day(self):
        self.assertTrue(date_matches_rule("2026-07-07", {"day": 7}))
        self.assertFalse(date_matches_rule("2026-07-08", {"day": 7}))

    def test_day_with_month(self):
        self.assertTrue(
            date_matches_rule("2026-07-15", {"day": 15, "month": 7})
        )
        self.assertFalse(
            date_matches_rule("2026-08-15", {"day": 15, "month": 7})
        )

    def test_day_in(self):
        self.assertTrue(
            date_matches_rule("2026-07-07", {"day_in": [7, 17, 27]})
        )
        self.assertTrue(
            date_matches_rule("2026-07-17", {"day_in": [7, 17, 27]})
        )
        self.assertFalse(
            date_matches_rule("2026-07-08", {"day_in": [7, 17, 27]})
        )

    def test_day_mod10(self):
        self.assertTrue(
            date_matches_rule("2026-07-07", {"day_mod10": 7})
        )
        self.assertTrue(
            date_matches_rule("2026-07-17", {"day_mod10": 7})
        )
        self.assertFalse(
            date_matches_rule("2026-07-08", {"day_mod10": 7})
        )

    def test_month_equals_day(self):
        self.assertTrue(
            date_matches_rule("2026-07-07", {"month_equals_day": True})
        )
        self.assertFalse(
            date_matches_rule("2026-07-08", {"month_equals_day": True})
        )

    def test_repdigit(self):
        self.assertTrue(
            date_matches_rule("2026-07-11", {"is_repdigit_day": True})
        )
        self.assertTrue(
            date_matches_rule("2026-07-22", {"is_repdigit_day": True})
        )
        self.assertFalse(
            date_matches_rule("2026-07-07", {"is_repdigit_day": True})
        )

    def test_weekday(self):
        # 2026-07-07 is Tuesday (weekday=1)
        self.assertTrue(date_matches_rule("2026-07-07", {"weekday": 1}))
        self.assertFalse(date_matches_rule("2026-07-07", {"weekday": 5}))


class TestEventFamiliesExtraction(unittest.TestCase):
    """event_families extracted from evidence_rules."""

    def test_families_created(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            families, _ = build_families(conn)
            self.assertGreater(families, 0)

            rows = conn.execute(
                "SELECT event_family_id, hall_id, family_type FROM event_families"
            ).fetchall()
            types = {r[2] for r in rows}
            self.assertIn("7のつく日", types)
            self.assertIn("ゾロ目", types)
            self.assertIn("月=日", types)
            conn.close()

    def test_per_hall_families(self):
        """Same family_type can have different IDs per hall."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)

            meq_a = conn.execute(
                """SELECT event_family_id FROM event_families
                   WHERE hall_id = 'hall_a' AND family_type = '月=日'"""
            ).fetchone()
            meq_b = conn.execute(
                """SELECT event_family_id FROM event_families
                   WHERE hall_id = 'hall_b' AND family_type = '月=日'"""
            ).fetchone()
            self.assertIsNotNone(meq_a)
            self.assertIsNotNone(meq_b)
            self.assertNotEqual(meq_a[0], meq_b[0])
            conn.close()

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            f1, _ = build_families(conn)
            f2, _ = build_families(conn)
            self.assertGreater(f1, 0)
            self.assertEqual(f2, 0)
            conn.close()


class TestHallDaysBackfill(unittest.TestCase):
    """hall_days tagged with event_family_id."""

    def test_matching_dates_tagged(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            _, backfilled = build_families(conn)
            self.assertGreater(backfilled, 0)

            tagged = conn.execute(
                """SELECT result_date, event_family_id FROM hall_days
                   WHERE hall_id = 'hall_a' AND event_family_id IS NOT NULL"""
            ).fetchall()
            tagged_dates = {r[0] for r in tagged}
            self.assertIn("2026-07-07", tagged_dates)
            self.assertIn("2026-07-17", tagged_dates)
            conn.close()

    def test_repdigit_tagged(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)

            row = conn.execute(
                """SELECT ef.family_type FROM hall_days hd
                   JOIN event_families ef ON hd.event_family_id = ef.event_family_id
                   WHERE hd.hall_id = 'hall_a' AND hd.result_date = '2026-07-11'"""
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "ゾロ目")

            row2 = conn.execute(
                """SELECT ef.family_type FROM hall_days hd
                   JOIN event_families ef ON hd.event_family_id = ef.event_family_id
                   WHERE hd.hall_id = 'hall_a' AND hd.result_date = '2026-07-22'"""
            ).fetchone()
            self.assertIsNotNone(row2)
            self.assertEqual(row2[0], "ゾロ目")
            conn.close()

    def test_most_specific_wins(self):
        """When multiple rules match, the most specific wins."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            build_families(conn)

            row = conn.execute(
                """SELECT ef.family_type FROM hall_days hd
                   JOIN event_families ef ON hd.event_family_id = ef.event_family_id
                   WHERE hd.hall_id = 'hall_b' AND hd.result_date = '2026-07-11'"""
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "ゾロ目")
            conn.close()


class TestHallCapabilities(unittest.TestCase):
    """hall_capabilities computed from data coverage."""

    def test_capabilities_computed(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            caps = compute_capabilities(conn, "2026-07-16T00:00:00+09:00")
            self.assertEqual(len(caps), 3)

            by_hall = {c["hall_id"]: c for c in caps}

            self.assertEqual(by_hall["hall_a"]["hall_daily_available"], 1)
            self.assertEqual(by_hall["hall_a"]["machine_daily_available"], 1)
            self.assertEqual(by_hall["hall_a"]["tail_daily_available"], 1)
            self.assertEqual(by_hall["hall_a"]["unit_daily_available"], 0)
            self.assertEqual(by_hall["hall_a"]["reset_policy_available"], 1)

            self.assertEqual(by_hall["hall_b"]["hall_daily_available"], 1)
            self.assertEqual(by_hall["hall_b"]["machine_daily_available"], 0)
            self.assertEqual(by_hall["hall_b"]["tail_daily_available"], 0)

            self.assertEqual(by_hall["hall_c"]["hall_daily_available"], 0)
            self.assertEqual(by_hall["hall_c"]["machine_daily_available"], 0)
            conn.close()

    def test_warnings_populated(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            caps = compute_capabilities(conn, "2026-07-16T00:00:00+09:00")

            by_hall = {c["hall_id"]: c for c in caps}

            warnings_c = json.loads(by_hall["hall_c"]["warnings_json"])
            self.assertIn("ホール日次データなし", warnings_c)
            self.assertIn("機種データなし", warnings_c)

            warnings_a = json.loads(by_hall["hall_a"]["warnings_json"])
            self.assertNotIn("ホール日次データなし", warnings_a)
            self.assertNotIn("機種データなし", warnings_a)
            conn.close()

    def test_persist_and_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            caps = compute_capabilities(conn, "2026-07-16")
            n1 = persist_capabilities(conn, caps)
            self.assertEqual(n1, 3)

            caps2 = compute_capabilities(conn, "2026-07-16")
            n2 = persist_capabilities(conn, caps2)
            self.assertEqual(n2, 3)

            count = conn.execute(
                "SELECT COUNT(*) FROM hall_capabilities"
            ).fetchone()[0]
            self.assertEqual(count, 3)
            conn.close()

    def test_acquisition_methods(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = make_test_db(db)
            migrate(db)
            populate_raw_sources(conn)
            conn.commit()
            caps = compute_capabilities(conn, "2026-07-16")

            by_hall = {c["hall_id"]: c for c in caps}
            methods_a = json.loads(
                by_hall["hall_a"]["acquisition_methods_json"]
            )
            self.assertIn("automated_public", methods_a)
            conn.close()


if __name__ == "__main__":
    unittest.main()
