#!/usr/bin/env python3
"""Create a normalized, non-interactive release ZIP.

Rejects Unicode/case-fold path collisions and excludes plaintext vaults,
credentials, caches, and development artifacts.
"""
from __future__ import annotations

import argparse
import os
import unicodedata
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_NAMES = {
    ".DS_Store", "plain.json", "credentials.json", ".env",
}
FORBIDDEN_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".git"}


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in FORBIDDEN_PARTS for part in rel.parts):
            continue
        if path.name in FORBIDDEN_NAMES or path.suffix == ".pyc":
            continue
        if path.name.startswith("decrypted") and path.suffix == ".json":
            continue
        yield path, rel


def normalized_key(rel: Path) -> str:
    return unicodedata.normalize("NFC", rel.as_posix()).casefold()


def build_zip(root: Path, output: Path, archive_root: str) -> None:
    seen: dict[str, str] = {}
    entries = []
    for path, rel in iter_files(root):
        key = normalized_key(rel)
        if key in seen:
            raise SystemExit(f"duplicate normalized path: {seen[key]} / {rel.as_posix()}")
        seen[key] = rel.as_posix()
        entries.append((path, rel))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path, rel in entries:
            arcname = f"{archive_root}/{unicodedata.normalize('NFC', rel.as_posix())}"
            zf.write(path, arcname)
    with zipfile.ZipFile(output) as zf:
        bad = zf.testzip()
        if bad:
            raise SystemExit(f"CRC failure: {bad}")
    print(f"wrote {output} ({len(entries)} files)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--output", required=True)
    ap.add_argument("--archive-root", default="Tenjikai-main")
    args = ap.parse_args()
    build_zip(Path(args.root).resolve(), Path(args.output).resolve(), args.archive_root)


if __name__ == "__main__":
    main()
