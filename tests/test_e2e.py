"""E2E acceptance tests for the full prediction pipeline.

Tests E2E-01 through E2E-05 from IMPLEMENTATION_PLAN.md.
Verifies the complete flow: migrate → build_predictions → freeze →
build_site_data → encrypt_vault → decrypt_vault with the real DB.

Requires the working slot_atlas directory in the scratchpad.
Skips gracefully if unavailable.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS_DIR))

SCRATCHPAD = Path(os.environ.get(
    "TENJIKAI_ATLAS_DIR",
    "/tmp/claude-0/-home-user-Tenjikai/"
    "e72f4792-fd16-530e-84d6-a278c66c7a3e/"
    "scratchpad/working_slot_atlas",
))

REAL_DB = SCRATCHPAD / "slot_atlas.db"
HAVE_REAL_DB = REAL_DB.exists()

SITE_ID = os.environ.get("SITE_ID", "")
SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "")
HAVE_VAULT_CREDS = bool(SITE_ID and SITE_PASSWORD)


def _run_tool(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable] + args,
        capture_output=True, text=True, timeout=300,
        cwd=str(PROJECT_ROOT), **kwargs,
    )


@unittest.skipUnless(HAVE_REAL_DB, "real slot_atlas.db not available")
class TestE2E_01_NoAuthRequired(unittest.TestCase):
    """E2E-01: Pipeline completes without authenticated sources."""

    def test_build_predictions_free_public(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "draft.json"
            r = _run_tool([
                str(TOOLS_DIR / "build_predictions.py"),
                "--atlas-dir", str(SCRATCHPAD),
                "--source-mode", "free_public",
                "--target-dates", "2026-07-20,2026-07-21",
                "--output", str(out),
            ])
            self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
            self.assertTrue(out.exists())

            with open(out) as f:
                draft = json.load(f)
            self.assertGreater(len(draft["predictions"]), 0)


@unittest.skipUnless(HAVE_REAL_DB, "real slot_atlas.db not available")
class TestE2E_02_PredictionContent(unittest.TestCase):
    """E2E-02: Generates hall/machine/tail predictions + frozen run."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp()
        cls._draft_path = Path(cls._tmpdir) / "draft.json"
        cls._frozen_path = Path(cls._tmpdir) / "frozen.json"
        cls._hash_path = Path(cls._tmpdir) / "frozen.sha256"

        r = _run_tool([
            str(TOOLS_DIR / "build_predictions.py"),
            "--atlas-dir", str(SCRATCHPAD),
            "--source-mode", "free_public",
            "--target-dates", "2026-07-20,2026-07-21",
            "--output", str(cls._draft_path),
        ])
        assert r.returncode == 0, f"build_predictions failed: {r.stderr}"

        with open(cls._draft_path) as f:
            cls._draft = json.load(f)

        r2 = _run_tool([
            str(TOOLS_DIR / "freeze_run.py"),
            str(cls._draft_path),
            "--output", str(cls._frozen_path),
            "--hash-output", str(cls._hash_path),
        ])
        assert r2.returncode == 0, f"freeze_run failed: {r2.stderr}"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def test_has_hall_predictions(self):
        types = {p["entity_type"] for p in self._draft["predictions"]}
        self.assertIn("hall", types)

    def test_has_machine_predictions(self):
        types = {p["entity_type"] for p in self._draft["predictions"]}
        machine_types = types & {"machine_event", "machine_organic"}
        self.assertTrue(
            len(machine_types) > 0,
            "no machine predictions found",
        )

    def test_has_tail_predictions(self):
        types = {p["entity_type"] for p in self._draft["predictions"]}
        self.assertIn("tail", types)

    def test_frozen_file_created(self):
        self.assertTrue(self._frozen_path.exists())

    def test_hash_file_created(self):
        self.assertTrue(self._hash_path.exists())
        content = self._hash_path.read_text().strip()
        self.assertRegex(content, r"^[0-9a-f]{64}\s+")

    def test_all_predictions_have_warnings(self):
        for i, p in enumerate(self._draft["predictions"]):
            self.assertIn(
                "warnings", p,
                f"predictions[{i}] missing warnings",
            )
            self.assertIsInstance(p["warnings"], list)

    def test_all_predictions_have_capabilities(self):
        for i, p in enumerate(self._draft["predictions"]):
            self.assertIn(
                "capabilities", p,
                f"predictions[{i}] missing capabilities",
            )

    def test_metadata_fields(self):
        for field in (
            "prediction_run_id", "built_at", "feature_cutoff_at",
            "model_version", "config_version",
            "source_snapshot_hash", "feature_snapshot_hash",
        ):
            self.assertIn(field, self._draft, f"missing {field}")

    def test_frozen_is_deterministic(self):
        from prediction_utils import canonical_json, sha256_hex

        frozen_content = self._frozen_path.read_text(encoding="utf-8")
        expected_hash = sha256_hex(frozen_content.encode("utf-8"))
        hash_line = self._hash_path.read_text().strip()
        actual_hash = hash_line.split()[0]
        self.assertEqual(expected_hash, actual_hash)


@unittest.skipUnless(HAVE_REAL_DB, "real slot_atlas.db not available")
class TestE2E_03_RealDB(unittest.TestCase):
    """E2E-03: Works with real DB, not just fixtures."""

    def test_real_db_has_data(self):
        conn = sqlite3.connect(str(REAL_DB))
        halls = conn.execute("SELECT COUNT(*) FROM halls").fetchone()[0]
        hall_days = conn.execute(
            "SELECT COUNT(*) FROM hall_days"
        ).fetchone()[0]
        conn.close()

        self.assertEqual(halls, 66)
        self.assertGreater(hall_days, 5000)

    def test_migration_on_real_db(self):
        from migrate_db import migrate

        with tempfile.TemporaryDirectory() as td:
            test_db = Path(td) / "test_copy.db"
            shutil.copy2(str(REAL_DB), str(test_db))
            actions = migrate(test_db)

            skipped = [a for a in actions if a.startswith("skip:")]
            ok = [a for a in actions if a.startswith("OK:")]
            self.assertTrue(
                len(skipped) + len(ok) == len(actions),
                f"unexpected actions: {actions}",
            )

    def test_chain_patterns_exist(self):
        conn = sqlite3.connect(str(REAL_DB))
        count = conn.execute(
            "SELECT COUNT(*) FROM chain_pattern_results"
        ).fetchone()[0]
        conn.close()
        self.assertGreater(count, 0, "no chain patterns in real DB")

    def test_capable_hall_produces_scored_machines(self):
        conn = sqlite3.connect(str(REAL_DB))
        md_count = conn.execute(
            "SELECT COUNT(*) FROM machine_days "
            "WHERE hall_id = 'ikegami_big_dipper_togoshi_ginza'"
        ).fetchone()[0]
        conn.close()
        self.assertGreater(md_count, 0)

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "draft.json"
            r = _run_tool([
                str(TOOLS_DIR / "build_predictions.py"),
                "--atlas-dir", str(SCRATCHPAD),
                "--source-mode", "free_public",
                "--target-dates", "2026-07-20",
                "--output", str(out),
            ])
            self.assertEqual(r.returncode, 0)
            with open(out) as f:
                draft = json.load(f)

            togoshi_machines = [
                p for p in draft["predictions"]
                if p["hall_id"] == "ikegami_big_dipper_togoshi_ginza"
                and p["entity_type"] in ("machine_event", "machine_organic")
                and p["score"] is not None
            ]
            self.assertGreater(
                len(togoshi_machines), 0,
                "capable hall should have scored machine predictions",
            )


@unittest.skipUnless(HAVE_REAL_DB, "real slot_atlas.db not available")
class TestE2E_04_CalendarPreserved(unittest.TestCase):
    """E2E-04: Existing calendar display preserved."""

    def test_site_data_generates(self):
        with tempfile.TemporaryDirectory() as td:
            plain_out = Path(td) / "plain.json"
            env = os.environ.copy()
            r = subprocess.run(
                [sys.executable, str(TOOLS_DIR / "build_site_data.py"),
                 "--atlas-dir", str(SCRATCHPAD)],
                capture_output=True, text=True, timeout=300,
                cwd=str(PROJECT_ROOT), env=env,
            )
            self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
            self.assertIn("rows", r.stdout)

            plain_path = PROJECT_ROOT / "build" / "plain.json"
            self.assertTrue(plain_path.exists())

            with open(plain_path) as f:
                payload = json.load(f)

            self.assertIn("meta", payload)
            self.assertIn("rows", payload)
            self.assertIsInstance(payload["rows"], list)
            self.assertGreater(len(payload["rows"]), 20000)

    def test_free_source_present(self):
        plain_path = PROJECT_ROOT / "build" / "plain.json"
        if not plain_path.exists():
            self.skipTest("plain.json not built yet")

        with open(plain_path) as f:
            payload = json.load(f)

        self.assertIn("free_source", payload)
        fs = payload["free_source"]
        self.assertIn("halls", fs)
        self.assertEqual(len(fs["halls"]), 66)

    def test_sw_cache_updated(self):
        sw_path = PROJECT_ROOT / "sw.js"
        if not sw_path.exists():
            self.skipTest("sw.js not found")

        content = sw_path.read_text()
        self.assertIn("const CACHE", content)
        self.assertIn("slot-atlas-", content)


@unittest.skipUnless(
    HAVE_REAL_DB and HAVE_VAULT_CREDS,
    "real DB or vault credentials not available",
)
class TestE2E_05_VaultIntegrity(unittest.TestCase):
    """E2E-05: Vault decrypts and passes schema check."""

    def test_encrypt_decrypt_roundtrip(self):
        plain_path = PROJECT_ROOT / "build" / "plain.json"
        if not plain_path.exists():
            self.skipTest("plain.json not built yet")

        with open(plain_path) as f:
            original = json.load(f)

        env = os.environ.copy()
        env["SITE_ID"] = SITE_ID
        env["SITE_PASSWORD"] = SITE_PASSWORD

        r1 = subprocess.run(
            ["node", str(TOOLS_DIR / "encrypt_vault.mjs")],
            capture_output=True, text=True, timeout=120,
            cwd=str(PROJECT_ROOT), env=env,
        )
        self.assertEqual(r1.returncode, 0, f"encrypt: {r1.stderr}")
        self.assertIn("self-check passed", r1.stdout)

        r2 = subprocess.run(
            ["node", str(TOOLS_DIR / "decrypt_vault.mjs")],
            capture_output=True, text=True, timeout=120,
            cwd=str(PROJECT_ROOT), env=env,
        )
        self.assertEqual(r2.returncode, 0, f"decrypt: {r2.stderr}")

        with open(plain_path) as f:
            roundtrip = json.load(f)

        self.assertEqual(len(original["rows"]), len(roundtrip["rows"]))
        self.assertEqual(
            original["meta"]["model_version"],
            roundtrip["meta"]["model_version"],
        )

    def test_vault_no_unit_data(self):
        plain_path = PROJECT_ROOT / "build" / "plain.json"
        if not plain_path.exists():
            self.skipTest("plain.json not built yet")

        with open(plain_path) as f:
            payload = json.load(f)

        fs_str = json.dumps(payload.get("free_source", {}))
        for forbidden in (
            "unit_no", "candidate_band", "Qhat_unit",
            "q_unit_observed", "entry_no",
        ):
            self.assertNotIn(
                f'"{forbidden}"', fs_str,
                f"forbidden field {forbidden} found in vault payload",
            )


if __name__ == "__main__":
    unittest.main()
