"""Exact, fail-closed reference inputs for governed portfolio calculations.

Hashes identify the bytes consumed; they do not certify provider accuracy or
historical classification coverage. Never substitutes a missing cache with {}.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024
VERSION = "governed-reference-input-v1"


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate reference input key")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("Non-finite reference input")


def inspect_reference_bytes(content: bytes, *, expected_sha256: str | None = None):
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
        raise ValueError("Missing or oversized reference input")
    actual = hashlib.sha256(content).hexdigest()
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
        or actual != expected_sha256
    ):
        raise ValueError("Reference input hash mismatch")
    try:
        data = json.loads(content.decode("utf-8-sig"), object_pairs_hook=_pairs,
                          parse_constant=_constant)
        # Also reject overflowed JSON numbers such as 1e999, which parse_constant
        # does not see. No special floating point values may enter the manifest.
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False).encode()
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ValueError("Invalid governed reference input") from error
    if not isinstance(data, dict) or type(data.get("version", 1)) is not int or data.get("version", 1) not in (1, 2):
        raise ValueError("Unsupported reference input schema")
    if any(not isinstance(data.get(key), dict) for key in ("sectors", "risk_free")):
        raise ValueError("Incomplete reference input schema")
    for conid, row in data["sectors"].items():
        if not conid or not isinstance(row, dict):
            raise ValueError("Invalid reference classification")
        for key in ("country", "sector", "symbol"):
            if key in row and type(row[key]) is not str:
                raise ValueError("Invalid reference classification field")
        if "country_checked" in row and type(row["country_checked"]) is not bool:
            raise ValueError("Invalid reference classification status")
    if any(not isinstance(row, dict) for row in data["risk_free"].values()):
        raise ValueError("Invalid reference risk-free record")
    manifest = {"version": VERSION, "content_sha256": actual,
                "semantic_sha256": hashlib.sha256(canonical).hexdigest(),
                "bytes": len(content), "cache_version": data.get("version", 1),
                "classification_rows": len(data["sectors"]),
                "risk_free_rows": len(data["risk_free"]),
                "input_format_verified": True, "financial_accuracy_verified": False,
                "historical_classification_coverage_verified": False}
    return data, manifest


def load_reference_snapshot(path: Path, *, expected_sha256: str | None = None):
    try:
        with path.open("rb") as source:
            content = source.read(MAX_BYTES + 1)
    except OSError as error:
        raise ValueError("Governed reference input is unavailable") from error
    return inspect_reference_bytes(content, expected_sha256=expected_sha256)
