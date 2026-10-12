"""Trace unusable normalized FX to stored parsed Flex evidence, without writes."""
import json
from decimal import Decimal, InvalidOperation
from .util import canonical_json, sha256_text, decimal_text, pick


def rate_status(value):
    try:
        rate = Decimal(str(value).replace(",", ""))
        return "nonfinite" if not rate.is_finite() else "nonpositive" if rate <= 0 else "positive"
    except (InvalidOperation, ValueError, TypeError):
        return "malformed"


def trace_observation(normalized, sources):
    """No broker-origin assertion: sources are stored parsed rows, not raw XML."""
    issues = []
    if len(sources) != 1:
        return {"trace_verified": False, "issues": ["missing_or_ambiguous_parsed_source"],
                "normalized_status": rate_status(normalized["rate_text"])}
    source = sources[0]
    attrs = json.loads(source["data_json"])
    if not isinstance(attrs, dict):
        raise ValueError("Parsed FX attributes must be an object")
    if (source["account_id"] != normalized["account_id"]
            or source["report_date"] != normalized["rate_date"]
            or source["row_sha256"] != normalized["row_sha256"]):
        issues.append("source_identity_mismatch")
    if sha256_text(canonical_json(attrs)) != source["row_sha256"]:
        issues.append("parsed_source_hash_mismatch")
    native = pick(attrs, "fromCurrency", "currency")
    target = pick(attrs, "toCurrency", "baseCurrency", default="BASE")
    source_rate = decimal_text(pick(attrs, "rate", "fxRateToBase", "exchangeRate"))
    if (native != normalized["from_currency"] or target != normalized["to_currency"]
            or source_rate != normalized["rate_text"]):
        issues.append("normalization_mismatch")
    if str(source["record_type"]).lower() not in {"conversionrate", "fxrate", "currencyconversionrate"}:
        issues.append("unexpected_fx_record_type")
    if source.get("ingestion_status") != "success" or not source.get("ingestion_completed_at"):
        issues.append("last_seen_ingestion_not_successful")
    if source["last_seen_run_id"] != normalized["last_seen_run_id"]:
        issues.append("last_seen_lineage_mismatch")
    return {"trace_verified": not issues, "issues": issues,
            "normalized_status": rate_status(normalized["rate_text"]),
            "parsed_source_status": rate_status(source_rate),
            "raw_xml_verified": False}
