"""Semantic checks required before writing additive FX evidence versions."""
import re
from datetime import date

from .parser import _source_key
from .util import canonical_json, parse_date_text, pick, sha256_text

FIELDS = {"account_id", "canonical_key", "row_sha256", "section", "record_type", "report_date", "data_json"}


def validate_version(row, *, account_id):
    if set(row) != FIELDS or row["account_id"] != account_id:
        raise ValueError("Evidence fields or account scope mismatch")
    attrs = row["data_json"]
    if not isinstance(attrs, dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in attrs.items()):
        raise ValueError("FX evidence must contain original string attributes")
    stated_account = pick(attrs,"accountId","account",default=account_id)
    if stated_account not in (account_id,"-","UNKNOWN"):
        raise ValueError("FX attributes belong to another account")
    if (not isinstance(row["record_type"],str) or row["record_type"].lower() not in
            {"conversionrate","fxrate","currencyconversionrate"}):
        raise ValueError("Unsupported FX record type")
    if not isinstance(row["section"],str) or not 1 <= len(row["section"]) <= 100:
        raise ValueError("Invalid FX section")
    if not isinstance(row["report_date"],str) or date.fromisoformat(row["report_date"]).isoformat() != row["report_date"]:
        raise ValueError("Canonical effective date required")
    source_date = pick(attrs,"reportDate","tradeDate","date","effectiveDate","toDate")
    if source_date and parse_date_text(source_date) != row["report_date"]:
        raise ValueError("FX source date differs from effective date")
    if not isinstance(row["row_sha256"],str) or not re.fullmatch(r"[0-9a-f]{64}",row["row_sha256"]):
        raise ValueError("Invalid content digest")
    if sha256_text(canonical_json(attrs)) != row["row_sha256"]:
        raise ValueError("FX content digest mismatch")
    expected = _source_key(account_id,row["section"],row["record_type"],attrs,
        effective_report_date=row["report_date"])
    if not expected.startswith("fx-rate:v2:") or row["canonical_key"] != expected:
        raise ValueError("FX canonical key mismatch")
    # Invalid rate values remain original evidence; this does not authorize
    # their use in analytics. Archive/run verification is a separate gate.
    return {**row,"data_json":dict(attrs)}
