"""Shared utilities for the prediction freezing pipeline.

Stdlib-only. No third-party dependencies.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
from pathlib import Path
from typing import Any


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: keys sorted, compact, UTF-8, no trailing newline."""

    def _check(v: Any) -> Any:
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            raise ValueError(f"NaN/Infinity not allowed in canonical JSON: {v}")
        return v

    def _walk(o: Any) -> Any:
        if isinstance(o, dict):
            return {k: _walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_walk(v) for v in o]
        return _check(o)

    return json.dumps(_walk(obj), sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_hash(obj: Any) -> str:
    return sha256_hex(canonical_json(obj).encode("utf-8"))


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def source_snapshot_hash(source_dir: Path) -> str:
    entries = []
    for p in sorted(source_dir.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            entries.append({
                "path": str(p.relative_to(source_dir)),
                "sha256": file_sha256(p),
                "size": p.stat().st_size,
            })
    return canonical_hash(entries)


def validate_draft(draft: dict) -> list[str]:
    """Validate a draft prediction run. Returns list of errors (empty = valid)."""
    errors: list[str] = []
    required_meta = [
        "prediction_run_id", "built_at", "feature_cutoff_at",
        "model_version", "config_version",
        "source_snapshot_hash", "feature_snapshot_hash",
    ]
    for key in required_meta:
        if key not in draft:
            errors.append(f"missing required field: {key}")

    if "built_at" in draft and "feature_cutoff_at" in draft:
        try:
            if (_dt.datetime.fromisoformat(str(draft["feature_cutoff_at"]))
                    > _dt.datetime.fromisoformat(str(draft["built_at"]))):
                errors.append(
                    "feature_cutoff_at is after built_at (freeze invariant: cutoff <= built)")
        except ValueError:
            errors.append("built_at/feature_cutoff_at not ISO-8601 parseable")

    if "predictions" not in draft:
        errors.append("missing predictions array")
        return errors
    if not isinstance(draft["predictions"], list):
        errors.append("predictions must be an array")
        return errors

    for i, pred in enumerate(draft["predictions"]):
        if "warnings" not in pred:
            errors.append(f"predictions[{i}]: missing warnings field")
        elif not isinstance(pred["warnings"], list):
            errors.append(f"predictions[{i}]: warnings must be an array")

        if "capabilities" not in pred:
            errors.append(f"predictions[{i}]: missing capabilities field")

        for field in ("target_date", "hall_id", "entity_type", "entity_id"):
            if field not in pred:
                errors.append(f"predictions[{i}]: missing {field}")

        if "score" in pred and pred["score"] is not None:
            if isinstance(pred["score"], float):
                if math.isnan(pred["score"]) or math.isinf(pred["score"]):
                    errors.append(f"predictions[{i}]: score is NaN/Infinity")

    return errors
