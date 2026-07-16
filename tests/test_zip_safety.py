"""ZIP-01~04 + vault safety: distribution artifact validation."""
import json
import os
import sys
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestZIP01_NoUnicodeDuplicatePaths(unittest.TestCase):
    """No two tracked files should normalize to the same NFC path."""

    def test_no_nfc_duplicates(self):
        # Get all tracked files from the repo
        tracked = []
        for p in ROOT.rglob("*"):
            if ".git" in p.parts:
                continue
            if p.is_file():
                rel = str(p.relative_to(ROOT))
                tracked.append(rel)

        nfc_paths = [unicodedata.normalize("NFC", p) for p in tracked]
        seen = {}
        dupes = []
        for orig, nfc in zip(tracked, nfc_paths):
            if nfc in seen:
                dupes.append((seen[nfc], orig))
            else:
                seen[nfc] = orig
        self.assertEqual(dupes, [], f"NFC duplicate paths: {dupes}")


class TestZIP02_NoCaseFoldDuplicates(unittest.TestCase):
    """No two tracked files should collide under case-folding."""

    def test_no_casefold_duplicates(self):
        tracked = []
        for p in ROOT.rglob("*"):
            if ".git" in p.parts:
                continue
            if p.is_file():
                rel = str(p.relative_to(ROOT))
                tracked.append(rel)

        seen = {}
        dupes = []
        for path in tracked:
            folded = path.casefold()
            if folded in seen:
                dupes.append((seen[folded], path))
            else:
                seen[folded] = path
        self.assertEqual(dupes, [], f"Case-fold duplicate paths: {dupes}")


class TestZIP03_NoForbiddenFiles(unittest.TestCase):
    """Distribution must not contain plaintext vault, credentials, or temp files."""

    FORBIDDEN_PATTERNS = [
        "plain.json",
        "decrypted",
        ".env",
        "credentials",
        ".DS_Store",
        "__pycache__",
    ]

    def test_no_forbidden_in_git(self):
        # Check git-tracked files only
        import subprocess
        result = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True,
            cwd=str(ROOT),
        )
        tracked = result.stdout.strip().split("\n") if result.stdout.strip() else []

        violations = []
        for f in tracked:
            for pat in self.FORBIDDEN_PATTERNS:
                if pat in f.lower():
                    violations.append(f"{f} matches forbidden pattern '{pat}'")
        self.assertEqual(violations, [], f"Forbidden files tracked: {violations}")


class TestZIP04_VaultIsEncrypted(unittest.TestCase):
    """data/vault.json must be encrypted (has v, kdf, salt, iv, ct keys)."""

    def test_vault_is_encrypted(self):
        vault_path = ROOT / "data" / "vault.json"
        if not vault_path.exists():
            self.skipTest("vault.json not present")

        data = json.loads(vault_path.read_text(encoding="utf-8"))
        self.assertIn("v", data, "vault missing 'v' key")
        self.assertIn("kdf", data, "vault missing 'kdf' key")
        self.assertIn("salt", data, "vault missing 'salt' key")
        self.assertIn("iv", data, "vault missing 'iv' key")
        self.assertIn("ct", data, "vault missing 'ct' key")
        # Must NOT have plaintext keys
        self.assertNotIn("rows", data, "vault contains plaintext 'rows'")
        self.assertNotIn("meta", data, "vault contains plaintext 'meta'")


if __name__ == "__main__":
    unittest.main()
