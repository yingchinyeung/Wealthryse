"""Read-only risk producer observation, not a workflow approval or dispatch.

Recomputing the same engine proves reproducibility, not independent financial
correctness. Upstream import/FX/reconciliation lineage must be attested separately
before this observation can contribute to a governed stage completion.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date
from uuid import UUID

from . import risk_snapshots as risk
from .util import canonical_json

OBSERVATION_VERSION = "risk-producer-observation-v1"


def semantic_hash(payload):
    value = copy.deepcopy(risk.decoded_payload(payload))
    # Only the replay build clock is irrelevant to numerical reproducibility.
    if isinstance(value.get("crisis_replay"), dict):
        value["crisis_replay"].pop("generated_at", None)
    serialized = canonical_json(value)
    json.loads(serialized, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite output")))
    return hashlib.sha256(serialized.encode()).hexdigest()


def verify_output(*, tenant_id, subject_id, as_of, expected_comparison_date,
                  inputs, existing, recomputed, recomputed_source_sha256):
    """Compare exact consumed inputs and deterministic outputs; return no values.

    expected_comparison_date must be supplied by a reviewed calendar policy or
    explicitly scoped diagnostic. It is NEVER inferred from job success or from
    the last available row. Missing optional metrics remain visible warnings.
    """
    tenant_id = str(UUID(str(tenant_id)))
    cutoff = date.fromisoformat(as_of)
    if type(expected_comparison_date) is not date or expected_comparison_date > cutoff:
        raise ValueError("Explicit non-future expected comparison date required")
    if inputs["subject_id"] != subject_id or inputs["as_of"] != as_of:
        raise ValueError("Risk input scope mismatch")
    if existing is not None and (str(existing["tenant_id"]) != tenant_id
            or existing["subject_id"] != subject_id or str(existing["as_of_date"]) != as_of
            or existing["analytics_version"] != risk.RISK_ANALYTICS_VERSION):
        raise ValueError("Risk output scope mismatch")
    source_hash = risk.risk_input_fingerprint(inputs)
    reasons = []
    warnings = []
    cache_status = risk.cache_reuse_status(existing, inputs=inputs, source_sha256=source_hash)
    if cache_status != "current":
        reasons.append("cache:" + cache_status)
    days = [date.fromisoformat(row.observation_date) for row in inputs["rows"]]
    if not days or days != sorted(set(days)):
        reasons.append("comparison_dates_missing_duplicate_or_unordered")
    if days and (days[-1] != expected_comparison_date or days[-1] > cutoff):
        reasons.append("comparison_coverage_mismatch")
    if not inputs["benchmark_id"]:
        reasons.append("primary_benchmark_missing")
    if recomputed_source_sha256 != source_hash:
        reasons.append("recomputation_input_mismatch")
    expected_identity = {"account_id": subject_id, "as_of": as_of,
                         "benchmark_id": inputs["benchmark_id"],
                         "analytics_version": risk.RISK_ANALYTICS_VERSION}
    if any(recomputed.get(k) != v for k, v in expected_identity.items()):
        reasons.append("recomputation_identity_mismatch")
    if recomputed.get("quality_status") not in {"pass", "warn"} or not isinstance(recomputed.get("metrics"), dict):
        reasons.append("recomputation_quality_or_structure_invalid")
    recomputed_hash = semantic_hash(recomputed)
    saved_hash = None
    if cache_status == "current":
        saved_hash = semantic_hash(existing["payload_json"])
        if saved_hash != recomputed_hash:
            reasons.append("deterministic_output_mismatch")
    if recomputed.get("quality_status") == "warn":
        warnings.append("producer_quality_warn")
    if not inputs.get("risk_free"):
        warnings.append("risk_free_unavailable")
    else:
        rate_day = date.fromisoformat(str(inputs["risk_free"]["rate_date"]))
        if rate_day > cutoff:
            reasons.append("future_risk_free_observation")
        elif rate_day < expected_comparison_date:
            warnings.append("risk_free_precedes_comparison_date")
    replay = inputs.get("crisis_replay")
    if not replay:
        warnings.append("crisis_replay_unavailable")
    elif date.fromisoformat(str(replay["holdings_as_of"])) > cutoff:
        reasons.append("future_crisis_holdings")
    if isinstance(recomputed.get("metrics"), dict) and any(value is None for value in recomputed["metrics"].values()):
        warnings.append("one_or_more_metrics_unavailable")
    return {"observation_version": OBSERVATION_VERSION, "tenant_id": tenant_id,
            "subject_id": subject_id, "as_of": as_of,
            "expected_comparison_date": expected_comparison_date.isoformat(),
            "observed_through": days[-1].isoformat() if days else None,
            "input_sha256": source_hash, "saved_output_sha256": saved_hash,
            "recomputed_output_sha256": recomputed_hash,
            "output_verified": not reasons, "cache_status": cache_status,
            "reasons": reasons, "warnings": warnings,
            "prerequisites_verified": False, "execution_authorized": False}


def inspect_risk_output(connection, *, tenant_id, subject_id, as_of, expected_comparison_date):
    """Caller owns a READ ONLY REPEATABLE READ transaction; no commits/writes."""
    context = connection.execute("SELECT app_tenant_id() AS tenant_id, "
        "current_setting('transaction_read_only') AS read_only, "
        "current_setting('transaction_isolation') AS isolation").fetchone()
    if (str(context["tenant_id"]) != str(tenant_id) or context["read_only"] != "on"
            or context["isolation"] not in {"repeatable read", "serializable"}):
        raise ValueError("Tenant-scoped consistent READ ONLY transaction required")
    loaded = risk.load_risk_inputs(connection, tenant_id=tenant_id, subject_id=subject_id, as_of=as_of)
    existing = connection.execute("""SELECT tenant_id, subject_id, as_of_date, analytics_version,
        comparison_version, source_data_sha256, payload_sha256, payload_json, quality_status
        FROM risk_analytics_snapshots WHERE tenant_id=? AND subject_id=? AND as_of_date=?
        AND analytics_version=?""", (tenant_id, subject_id, as_of, risk.RISK_ANALYTICS_VERSION)).fetchone()
    payload, timings, source_hash = risk.compute_risk_snapshot(connection, tenant_id=tenant_id,
        subject_id=subject_id, as_of=as_of, loaded_inputs=loaded)
    observation = verify_output(tenant_id=tenant_id, subject_id=subject_id, as_of=as_of,
        expected_comparison_date=expected_comparison_date, inputs=loaded[0], existing=existing,
        recomputed=payload, recomputed_source_sha256=source_hash)
    observation["timings_ms"] = timings
    return observation
