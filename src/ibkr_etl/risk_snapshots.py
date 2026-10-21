from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date, timedelta
from dataclasses import asdict
from typing import Any, Mapping

from .benchmark_analytics import (
    CALCULATION_VERSION,
    ComparisonRow,
    expected_tail_loss_contributors,
    expected_tail_loss_dates,
    portfolio_risk_metrics,
)
from .crisis_replay import load_crisis_replay
from . import publish_gate
from .util import canonical_json, utc_now_iso
from .market_calendar import BENCHMARK_MARKETS, CALENDARS, CALENDAR_VERSION, benchmark_session_on_or_before


LOGGER = logging.getLogger(__name__)
RISK_ANALYTICS_VERSION = "risk-analytics-v2"
# Reporting currencies with their own risk-free series in risk_free_rates (see korea_cd_rate).
OWN_CURRENCY_RISK_FREE = {"KRW": "KRW"}
# A won rate older than this is not used: the monthly OECD series runs one to two months
# behind, so a quarter without a new month means the feed has stopped.
OWN_RATE_MAX_AGE_DAYS = 100


def own_rate_cutoff(as_of: str) -> str:
    return (date.fromisoformat(str(as_of)[:10]) - timedelta(days=OWN_RATE_MAX_AGE_DAYS)).isoformat()


def risk_free_rate(connection, *, tenant_id: str, reporting_currency: str, as_of: str):
    """The latest validated risk-free observation for a subject reporting in this currency.

    A won account takes the won CD rate once it is loaded and recent; until then, and for
    every other currency here, the U.S. 3-month Treasury rate as before.
    """
    own_rate = OWN_CURRENCY_RISK_FREE.get(str(reporting_currency or "USD").upper(), "USD")
    return connection.execute(
        """
        SELECT rate_date, series_id, currency, tenor, annual_rate, provider,
               quality_status
        FROM risk_free_rates
        WHERE tenant_id = ? AND currency IN (?, 'USD') AND rate_date <= ?
          AND (currency = 'USD' OR rate_date >= ?)
          AND quality_status IN ('pass', 'warn')
        ORDER BY (currency = ?) DESC, rate_date DESC LIMIT 1
        """,
        (tenant_id, own_rate, as_of, own_rate_cutoff(as_of), own_rate),
    ).fetchone()


class RiskInputsNotReady(ValueError):
    """Validated upstream observations are absent or stale; never bootstrap."""


def validate_risk_input_readiness(inputs: Mapping[str, Any]) -> None:
    cutoff = date.fromisoformat(inputs["as_of"])
    if inputs['subject_id'] == 'alpha-world-t':
        from .alpha_world_t_runtime import validate_inputs
        try:validate_inputs(inputs)
        except (KeyError,ValueError,TypeError,OverflowError) as error:
            raise RiskInputsNotReady('T HKD/HIBOR inputs are not qualified: '+str(error)) from error
        return
    if 'profiled_hkd_snapshot' in inputs:
        from .profiled_composite import validate_inputs as validate_profiled
        try:validate_profiled(inputs)
        except (KeyError,ValueError,TypeError,OverflowError) as error:
            raise RiskInputsNotReady('Composite HKD/HIBOR inputs are not qualified: '+str(error)) from error
        return
    if inputs['subject_id'] == 'alpha-world-ms':
        from .alpha_world_ms_analytics import analyze
        from .alpha_world_ms_cache import methodology_identity
        try:
            snapshot = inputs['ms_hkd_snapshot']
            if (snapshot.get('methodology_identity') != methodology_identity()
                    or snapshot.get('reporting_currency') != 'HKD'
                    or snapshot.get('period_start') != '2025-01-20'
                    or snapshot.get('period_end') != inputs['as_of']
                    or snapshot.get('subject_id') != inputs['subject_id']
                    or inputs['risk_free'] != snapshot['risk']['risk_free']):
                raise ValueError('MS input identity or risk-free evidence differs')
            required = max(benchmark_session_on_or_before(b, cutoff) for b in ('GSPC', 'HSI'))
            # Actual broker/derived valuations may exist on a joint-market
            # closure (cash/FX effects). Preserve them; never invent a price.
            if not required <= date.fromisoformat(snapshot['data_as_of']) <= cutoff:
                raise ValueError('MS HKD NAV does not cover the latest required joint-market session')
            analyze(snapshot)
        except (KeyError, ValueError, TypeError, OverflowError) as error:
            raise RiskInputsNotReady('MS HKD/HIBOR inputs are not ready: ' + str(error)) from error
        return
    days = [date.fromisoformat(row.observation_date) for row in inputs["rows"]]
    if not inputs["benchmark_id"] or not days:
        raise RiskInputsNotReady("Validated primary-benchmark comparison observations are missing")
    if days != sorted(set(days)) or days[-1] > cutoff:
        raise RiskInputsNotReady("Comparison observations are duplicate, unordered or future dated")
    market = BENCHMARK_MARKETS.get(inputs["benchmark_id"])
    if market and cutoff >= CALENDARS[market].coverage_start:
        try:
            required = benchmark_session_on_or_before(inputs["benchmark_id"], cutoff)
            valid_session = CALENDARS[market].is_session(days[-1])
        except ValueError as error:
            raise RiskInputsNotReady(str(error)) from error
        if days[-1] != required or not valid_session:
            raise RiskInputsNotReady("Primary-benchmark comparison does not cover the required exchange session")
    elif (cutoff-days[-1]).days > 4:
        raise RiskInputsNotReady("Primary-benchmark comparison exceeds the approved historical four-day tolerance")
    rate = inputs.get("risk_free")
    replay = inputs.get("crisis_replay")
    if rate and date.fromisoformat(str(rate["rate_date"])) > cutoff:
        raise RiskInputsNotReady("Risk-free observation is future dated")
    if replay and replay.get("holdings_as_of") and date.fromisoformat(str(replay["holdings_as_of"])) > cutoff:
        raise RiskInputsNotReady("Crisis replay holdings are future dated")


def _rows(records: list[Mapping[str, Any]]) -> list[ComparisonRow]:
    return [
        ComparisonRow(
            observation_date=str(record["observation_date"]),
            portfolio_value=float(record["portfolio_value"]),
            benchmark_level=float(record["benchmark_level"]),
            normalized_portfolio=float(record["normalized_portfolio"]),
            normalized_benchmark=float(record["normalized_benchmark"]),
            portfolio_return=(
                float(record["portfolio_return"])
                if record["portfolio_return"] is not None
                else None
            ),
            benchmark_return=(
                float(record["benchmark_return"])
                if record["benchmark_return"] is not None
                else None
            ),
            excess_return=(
                float(record["excess_return"])
                if record["excess_return"] is not None
                else None
            ),
        )
        for record in records
    ]


def _tail_position_dates(rows: list[ComparisonRow]) -> list[str]:
    tail_dates = expected_tail_loss_dates(rows)
    previous_by_date = {
        current.observation_date: previous.observation_date
        for previous, current in zip(rows, rows[1:])
    }
    required = set(tail_dates)
    required.update(
        previous_by_date[day] for day in tail_dates if day in previous_by_date
    )
    return sorted(required)


def load_risk_inputs(
    connection,
    *,
    tenant_id: str,
    subject_id: str,
    as_of: str,
) -> tuple[dict[str, Any], dict[str, float]]:
    """Read actual calculation inputs; do not run the costly bootstrap yet."""
    date.fromisoformat(as_of)
    timings: dict[str, float] = {}
    started = time.perf_counter()

    if subject_id=='alpha-world-t':
        from .alpha_world_t_analytics import load_risk_snapshot
        from .alpha_world_t_crisis import load as load_t_crisis
        try:
            snapshot,_,_=load_risk_snapshot(connection,as_of,tenant_id=tenant_id)
            crisis=load_t_crisis(connection,snapshot,tenant_id=tenant_id)
            inputs={'subject_id':subject_id,'subject_type':'composite_portfolio','as_of':as_of,
                'benchmark_id':None,'rows':[],'positions':[],'risk_free':snapshot['risk']['risk_free'],
                'crisis_replay':crisis,'t_hkd_snapshot':snapshot}
            validate_risk_input_readiness(inputs)
        except (ValueError,KeyError,TypeError) as error:
            raise RiskInputsNotReady('T qualified stored inputs unavailable: '+str(error)) from error
        return inputs,{'input_query_ms':round((time.perf_counter()-started)*1000,3),'tail_position_query_ms':0.}

    query_started = time.perf_counter()
    subject = connection.execute(
        """
        SELECT subject_type, COALESCE(reporting_currency, base_currency) AS reporting_currency
        FROM platform_subjects
        WHERE tenant_id = ? AND subject_id = ?
        """,
        (tenant_id, subject_id),
    ).fetchone()
    subject_type = str(subject["subject_type"]) if subject else "physical_account"
    # The NAV, comparison and holdings below are in this currency; so is the risk-free rate.
    reporting_currency = str((subject["reporting_currency"] if subject else None) or "USD").upper()
    from .profiled_composite import is_profiled
    if (subject_type == 'composite_portfolio' and subject_id not in ('alpha-world-ms','alpha-world-t')
            and is_profiled(connection, subject_id, tenant_id=tenant_id)):
        # A composite reporting in the Alpha World MS or T design: full HKD
        # NAV and HIBOR, never the USD benchmark comparison path below.
        from .profiled_composite import load_risk_snapshot as load_profiled
        try:
            snapshot,_,_=load_profiled(connection,subject_id,as_of,tenant_id=tenant_id)
            from .profiled_crisis import load as load_profiled_crisis
            # As MS: without exact-date sleeve evidence the replay is shown as unavailable.
            try:crisis=load_profiled_crisis(connection,snapshot,tenant_id=tenant_id)
            except (ValueError,KeyError,TypeError):crisis=None
            inputs={'subject_id':subject_id,'subject_type':subject_type,'as_of':as_of,
                'benchmark_id':None,'rows':[],'positions':[],'risk_free':snapshot['risk']['risk_free'],
                'crisis_replay':crisis,'profiled_hkd_snapshot':snapshot}
            validate_risk_input_readiness(inputs)
        except (ValueError,KeyError,TypeError) as error:
            raise RiskInputsNotReady('Composite qualified stored inputs unavailable: '+str(error)) from error
        return inputs,{'input_query_ms':round((time.perf_counter()-started)*1000,3),'tail_position_query_ms':0.}
    if subject_id == 'alpha-world-ms':
        from .alpha_world_ms_hkd import load_risk_snapshot
        from .alpha_world_ms_crisis import load as load_ms_crisis
        try:
            snapshot, _, _ = load_risk_snapshot(connection, as_of, tenant_id=tenant_id)
        except ValueError as error:
            raise RiskInputsNotReady(str(error)) from error
        timings['input_query_ms'] = round((time.perf_counter()-started)*1000, 3)
        timings['tail_position_query_ms'] = 0.0
        try: crisis = load_ms_crisis(connection, snapshot, tenant_id=tenant_id, identity_proxy=True)
        except ValueError: crisis = None
        # Benchmark comparison is a separate, unqualified capability. Never
        # intersect full HKD NAV metrics with legacy USD/GSPC comparison rows.
        return {'subject_id':subject_id, 'subject_type':subject_type, 'as_of':as_of,
            'benchmark_id':None, 'rows':[], 'positions':[],
            'risk_free':snapshot['risk']['risk_free'], 'crisis_replay':crisis,
            'ms_hkd_snapshot':snapshot}, timings
    benchmark = connection.execute(
        """
        SELECT benchmark_id FROM portfolio_benchmark_mapping
        WHERE tenant_id = ? AND subject_id = ? AND mapping_type = 'primary'
          AND effective_from <= ?
          AND (effective_to IS NULL OR effective_to >= ?)
        ORDER BY effective_from DESC LIMIT 1
        """,
        (tenant_id, subject_id, as_of, as_of),
    ).fetchone()
    records = [] if benchmark is None else connection.execute(
        """
        SELECT observation_date, portfolio_value, benchmark_level,
               normalized_portfolio, normalized_benchmark, portfolio_return,
               benchmark_return, excess_return
        FROM performance_comparison_daily
        WHERE tenant_id = ? AND subject_id = ? AND benchmark_id = ?
          AND calculation_version = ?
          AND quality_status IN ('pass', 'warn')
          AND observation_date <= ?
        ORDER BY observation_date
        """,
        (
            tenant_id,
            subject_id,
            benchmark["benchmark_id"],
            CALCULATION_VERSION,
            as_of,
        ),
    ).fetchall()
    risk_free = risk_free_rate(connection, tenant_id=tenant_id, reporting_currency=reporting_currency, as_of=as_of)
    rows = _rows(records)
    timings["comparison_and_rate_query_ms"] = round(
        (time.perf_counter() - query_started) * 1000, 3
    )

    positions_started = time.perf_counter()
    required_dates = _tail_position_dates(rows)
    position_records = []
    if subject_type != "composite_portfolio" and required_dates:
        position_records = connection.execute(
            """
            SELECT p.snapshot_date, p.conid, p.model_code,
                   CASE
                     WHEN UPPER(COALESCE(p.currency, '')) = ? THEN ?
                     WHEN fx.rate IS NOT NULL THEN ?
                     ELSE p.currency
                   END AS currency,
                   p.quantity_text,
                   CASE
                     WHEN UPPER(COALESCE(p.currency, '')) = ?
                       THEN p.market_value_text
                     WHEN fx.rate IS NOT NULL
                       THEN (COALESCE(NULLIF(p.market_value_text, ''), '0')::numeric * fx.rate)::text
                     ELSE p.market_value_text
                   END AS market_value_text,
                   i.symbol, i.description
            FROM subject_position_series AS p
            LEFT JOIN instruments AS i ON i.conid = p.conid
            LEFT JOIN LATERAL (
                SELECT r.rate
                FROM reference_fx_rates AS r
                WHERE r.tenant_id = ?
                  AND r.from_currency = UPPER(COALESCE(p.currency, ''))
                  AND r.to_currency = ?
                  AND r.quality_status IN ('pass', 'warn')
                  AND r.rate_date <= p.snapshot_date
                  AND r.rate_date >= (p.snapshot_date::date - INTERVAL '4 days')::text
                ORDER BY r.rate_date DESC
                LIMIT 1
            ) AS fx ON UPPER(COALESCE(p.currency, '')) <> ?
            WHERE p.tenant_id = ? AND p.subject_id = ?
              AND p.snapshot_date = ANY(?::text[])
              AND UPPER(COALESCE(i.symbol, '')) NOT LIKE '%%.REC'
            ORDER BY p.snapshot_date, p.conid, p.model_code
            """,
            (reporting_currency, reporting_currency, reporting_currency, reporting_currency,
             tenant_id, reporting_currency, reporting_currency, tenant_id, subject_id, required_dates),
        ).fetchall()
    timings["tail_position_query_ms"] = round(
        (time.perf_counter() - positions_started) * 1000, 3
    )

    crisis_started = time.perf_counter()
    crisis_replay = load_crisis_replay(connection, account_id=subject_id, as_of=as_of)
    timings["crisis_replay_query_ms"] = round(
        (time.perf_counter() - crisis_started) * 1000, 3
    )
    timings["input_query_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return {
        "subject_id": subject_id, "subject_type": subject_type, "as_of": as_of,
        "benchmark_id": benchmark["benchmark_id"] if benchmark else None,
        "reporting_currency": reporting_currency,
        "rows": rows, "positions": [dict(row) for row in position_records],
        "risk_free": dict(risk_free) if risk_free else None,
        "crisis_replay": dict(crisis_replay) if crisis_replay else None,
    }, timings


def risk_input_fingerprint(inputs: Mapping[str, Any]) -> str:
    """Hash values, not counts. Exclude only the replay's wall-clock build time.

    These are the consumed deterministic inputs. Upstream reconciliation must
    propagate raw NAV/FX/benchmark corrections before this stage is dispatched.
    Published reporting snapshots are deliberately outside this cache lifecycle.
    """
    replay = dict(inputs["crisis_replay"]) if inputs.get("crisis_replay") else None
    if replay:
        replay.pop("generated_at", None)
    manifest = {
        "fingerprint_version": 1, "analytics_version": RISK_ANALYTICS_VERSION,
        "comparison_version": CALCULATION_VERSION,
        "subject_id": inputs["subject_id"], "subject_type": inputs["subject_type"],
        "as_of": inputs["as_of"], "benchmark_id": inputs["benchmark_id"],
        "comparisons": [asdict(row) for row in inputs["rows"]],
        "positions": inputs["positions"], "risk_free": inputs["risk_free"],
        "crisis_replay": replay,
        "calendar_version": CALENDAR_VERSION,
    }
    if 'ms_hkd_snapshot' in inputs:
        from .alpha_world_ms_analytics import VERSION
        from .alpha_world_ms_runtime import VERSION as RUNTIME_VERSION
        manifest['ms_hkd_snapshot']=inputs['ms_hkd_snapshot']
        manifest['ms_methodology_version']=VERSION
        manifest['ms_runtime_version']=RUNTIME_VERSION
    if 't_hkd_snapshot' in inputs:
        from .alpha_world_t_runtime import VERSION
        manifest['t_hkd_snapshot']=inputs['t_hkd_snapshot']
        manifest['t_runtime_version']=VERSION
    if 'profiled_hkd_snapshot' in inputs:
        from .profiled_composite import ANALYTICS_VERSION, RUNTIME_VERSION
        manifest['profiled_hkd_snapshot']=inputs['profiled_hkd_snapshot']
        manifest['profiled_methodology_version']=ANALYTICS_VERSION
        manifest['profiled_runtime_version']=RUNTIME_VERSION
    # Reject NaN/infinity rather than giving an invalid calculation a cache key.
    serialized = canonical_json(manifest)
    json.loads(serialized, parse_constant=lambda value: (_ for _ in ()).throw(
        ValueError(f"Non-finite risk input: {value}")))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_risk_snapshot(
    connection, *, tenant_id: str, subject_id: str, as_of: str,
    loaded_inputs: tuple[dict[str, Any], dict[str, float]] | None = None,
) -> tuple[dict[str, Any], dict[str, float], str]:
    """Calculate one complete risk package outside the web request path."""
    inputs, timings = loaded_inputs or load_risk_inputs(
        connection, tenant_id=tenant_id, subject_id=subject_id, as_of=as_of)
    if inputs['subject_id'] != subject_id or inputs['as_of'] != as_of:
        raise RiskInputsNotReady('Risk input identity does not match the requested subject and date')
    if subject_id == 'alpha-world-t' or 'profiled_hkd_snapshot' in inputs:
        # Supplied loaded_inputs must not bypass the same T qualification gate
        # enforced by load_risk_inputs and the ordinary background worker.
        validate_risk_input_readiness(inputs)
    rows = inputs["rows"]
    risk_free = inputs["risk_free"]
    position_records = inputs["positions"]
    crisis_replay = inputs["crisis_replay"]
    source_sha256 = risk_input_fingerprint(inputs)

    if subject_id=='alpha-world-t':
        from .alpha_world_t_runtime import build_payload
        started=time.perf_counter();payload=build_payload(inputs)
        timings['deterministic_metrics_ms']=round((time.perf_counter()-started)*1000,3)
        timings['total_ms']=round(timings['input_query_ms']+timings['deterministic_metrics_ms'],3)
        return payload,timings,source_sha256

    if 'profiled_hkd_snapshot' in inputs:
        from .profiled_composite import build_payload as build_profiled
        started=time.perf_counter();payload=build_profiled(inputs)
        timings['deterministic_metrics_ms']=round((time.perf_counter()-started)*1000,3)
        timings['total_ms']=round(timings['input_query_ms']+timings['deterministic_metrics_ms'],3)
        return payload,timings,source_sha256

    if subject_id == 'alpha-world-ms':
        from .alpha_world_ms_runtime import build_payload
        validate_risk_input_readiness(inputs)
        analytics_started=time.perf_counter()
        payload=build_payload(inputs)
        timings['deterministic_metrics_ms']=round((time.perf_counter()-analytics_started)*1000,3)
        timings['total_ms']=round(timings['input_query_ms']+timings['deterministic_metrics_ms'],3)
        return payload,timings,source_sha256

    analytics_started = time.perf_counter()
    metrics = portfolio_risk_metrics(
        rows, float(risk_free["annual_rate"]) if risk_free else None
    )
    contributors = expected_tail_loss_contributors(
        rows, position_records, reporting_currency=str(inputs.get("reporting_currency") or "USD")
    )
    timings["deterministic_metrics_ms"] = round(
        (time.perf_counter() - analytics_started) * 1000, 3
    )

    if crisis_replay is not None:
        metrics["crisis_maximum_drawdown"] = crisis_replay["maximum_drawdown"]
    timings["total_ms"] = round(timings["input_query_ms"] + timings["deterministic_metrics_ms"], 3)

    quality_status = "warn" if rows else "fail"
    payload: dict[str, Any] = {
        "account_id": subject_id,
        "as_of": as_of,
        "benchmark_id": inputs["benchmark_id"],
        "metrics": metrics,
        "expected_tail_loss_contributors": contributors,
        "crisis_replay": dict(crisis_replay) if crisis_replay else None,
        "risk_free": dict(risk_free) if risk_free else None,
        "quality_status": quality_status,
        "message": (
            "Risk metrics use validated, cash-flow-adjusted portfolio returns on common primary-benchmark trading dates. Sharpe is unavailable until a validated risk-free series is loaded."
            if rows and risk_free is None
            else (
                "Risk metrics use validated, cash-flow-adjusted portfolio returns on common primary-benchmark trading dates."
                if rows
                else "Insufficient validated comparison data are available for risk analytics."
            )
        ),
        "analytics_version": RISK_ANALYTICS_VERSION,
    }
    return payload, timings, source_sha256


def store_risk_snapshot(
    connection,
    *,
    tenant_id: str,
    subject_id: str,
    as_of: str,
    payload: Mapping[str, Any],
    timings: Mapping[str, float],
    source_data_sha256: str,
) -> None:
    payload_json = canonical_json(payload)
    payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    if subject_id in ('alpha-world-ms','alpha-world-t') or _profiled_payload(payload):
        status = snapshot_read_status({'payload_json':payload, 'payload_sha256':payload_sha256,
            'quality_status':payload.get('quality_status'), 'comparison_version':CALCULATION_VERSION},
            subject_id=subject_id, as_of=as_of)
        if status != 'current':
            label = {'alpha-world-t':'T','alpha-world-ms':'MS'}.get(subject_id,'Composite')
            raise RiskInputsNotReady(label+' output did not pass cache validation: ' + status)
    connection.execute(
        """
        INSERT INTO risk_analytics_snapshots(
            tenant_id, subject_id, as_of_date, analytics_version,
            comparison_version, source_data_sha256, payload_sha256,
            payload_json, quality_status, generated_at, duration_ms,
            timings_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?, ?, ?, ?::jsonb)
        ON CONFLICT (tenant_id, subject_id, as_of_date, analytics_version)
        DO UPDATE SET
            comparison_version = excluded.comparison_version,
            source_data_sha256 = excluded.source_data_sha256,
            payload_sha256 = excluded.payload_sha256,
            payload_json = excluded.payload_json,
            quality_status = excluded.quality_status,
            generated_at = excluded.generated_at,
            duration_ms = excluded.duration_ms,
            timings_json = excluded.timings_json
        """,
        (
            tenant_id,
            subject_id,
            as_of,
            RISK_ANALYTICS_VERSION,
            CALCULATION_VERSION,
            source_data_sha256,
            payload_sha256,
            payload_json,
            str(payload.get("quality_status") or "fail"),
            utc_now_iso(),
            float(timings.get("total_ms") or 0.0),
            canonical_json(dict(timings)),
        ),
    )


def build_risk_snapshot(
    connection,
    *,
    tenant_id: str,
    subject_id: str,
    as_of: str,
    only_if_changed: bool = False,
) -> dict[str, Any]:
    loaded = load_risk_inputs(connection, tenant_id=tenant_id, subject_id=subject_id, as_of=as_of)
    if loaded[0]["subject_id"] != subject_id or loaded[0]["as_of"] != as_of:
        raise RiskInputsNotReady("Risk input identity does not match the requested subject and date")
    validate_risk_input_readiness(loaded[0])
    try:
        source_sha256 = risk_input_fingerprint(loaded[0])
    except ValueError as error:
        # Let the worker withhold this date and continue other accounts rather
        # than aborting before the old cache can be marked unavailable.
        raise RiskInputsNotReady("Risk input values failed deterministic validation") from error
    if only_if_changed:
        existing = connection.execute(
            """SELECT source_data_sha256, payload_sha256, payload_json, quality_status, comparison_version
               FROM risk_analytics_snapshots WHERE tenant_id = ? AND subject_id = ?
               AND as_of_date = ? AND analytics_version = ?""",
            (tenant_id, subject_id, as_of, RISK_ANALYTICS_VERSION),
        ).fetchone()
        if cache_reuse_status(existing, inputs=loaded[0], source_sha256=source_sha256) == "current":
            connection.commit()
            return {"subject_id": subject_id, "as_of": as_of, "status": "unchanged",
                    "source_data_sha256": source_sha256}
    payload, timings, source_sha256 = compute_risk_snapshot(
        connection, tenant_id=tenant_id, subject_id=subject_id, as_of=as_of, loaded_inputs=loaded
    )
    store_risk_snapshot(
        connection,
        tenant_id=tenant_id,
        subject_id=subject_id,
        as_of=as_of,
        payload=payload,
        timings=timings,
        source_data_sha256=source_sha256,
    )
    # The publish gate (off by default). It logs its verdict only: the risk page shows one
    # exact date, so holding this back would show "being prepared" instead.
    publish_gate.before_commit(connection, {"gold.risk_analytics_snapshots": {
        "subject_id": subject_id, "as_of_date": as_of, "analytics_version": RISK_ANALYTICS_VERSION}},
        writer="risk_snapshots")
    connection.commit()
    LOGGER.info(
        "risk_snapshot subject=%s as_of=%s total_ms=%.3f tail_position_ms=%.3f",
        subject_id,
        as_of,
        timings["total_ms"],
        timings["tail_position_query_ms"],
    )
    return {
        "status": "rebuilt",
        "subject_id": subject_id,
        "as_of": as_of,
        "quality_status": payload["quality_status"],
        "timings_ms": timings,
        "source_data_sha256": source_sha256,
    }


def decoded_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Risk analytics snapshot payload is not a JSON object")


def _profiled_payload(payload: Any) -> bool:
    from .profiled_composite import RUNTIME_VERSION
    return isinstance(payload, Mapping) and payload.get('runtime_version') == RUNTIME_VERSION


def snapshot_read_status(existing, *, subject_id: str, as_of: str,
                         benchmark_id: str | None = None) -> str:
    """Validate a cached response without querying source data or bootstrapping.

    This verifies stored integrity, not current input freshness. Correction
    propagation remains the background worker's separate responsibility.
    """
    if existing is None:
        return "missing"
    if subject_id == 'alpha-world-t':
        try:
            from .alpha_world_t_runtime import validate_payload
            validate_payload(decoded_payload(existing['payload_json']))
        except (KeyError,ValueError,TypeError,OverflowError):return 't_methodology_unqualified'
    if existing.get("quality_status") not in {"pass", "warn"}:
        return "failed_quality"
    if existing.get("comparison_version") != CALCULATION_VERSION:
        return "comparison_version_changed"
    try:
        payload = decoded_payload(existing["payload_json"])
        serialized = canonical_json(payload)
        json.loads(serialized, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (KeyError, ValueError, TypeError, OverflowError):
        return "invalid_payload"
    expected = {"account_id":subject_id, "as_of":str(as_of),
                "analytics_version":RISK_ANALYTICS_VERSION}
    if benchmark_id is not None:
        expected["benchmark_id"] = benchmark_id
    if any(payload.get(key) != value for key, value in expected.items()):
        return "payload_identity_mismatch"
    profiled = _profiled_payload(payload)
    if (not isinstance(payload.get("metrics"), dict)
            or (subject_id not in ('alpha-world-ms','alpha-world-t') and not profiled and
                (not isinstance(payload.get("benchmark_id"), str) or not payload["benchmark_id"]))
            or payload.get("quality_status") != existing["quality_status"]):
        return "invalid_payload_structure"
    if hashlib.sha256(serialized.encode("utf-8")).hexdigest() != existing.get("payload_sha256"):
        return "payload_hash_mismatch"
    if subject_id == 'alpha-world-ms':
        try:
            if payload.get('runtime_version'):
                from .alpha_world_ms_runtime import validate_payload
            else:
                from .alpha_world_ms_cache import validate_payload
            validate_payload(payload)
        except (KeyError, ValueError, TypeError, OverflowError):
            return 'ms_methodology_mismatch'
    if profiled:
        try:
            from .profiled_composite import validate_payload as validate_profiled
            validate_profiled(payload)
        except (KeyError, ValueError, TypeError, OverflowError):
            return 'composite_methodology_mismatch'
    return "current"


def cache_reuse_status(existing, *, inputs: Mapping[str, Any], source_sha256: str) -> str:
    """Classify a disposable cache; never authorize changing published reports.

Corruption is a cache miss, not a reason to stop rebuilding other dates. Database
errors and input-validation failures are deliberately not swallowed here.
"""
    status = snapshot_read_status(existing, subject_id=inputs["subject_id"],
                                  as_of=inputs["as_of"], benchmark_id=inputs["benchmark_id"])
    if status != "current":
        return status
    if existing["source_data_sha256"] != source_sha256:
        return "inputs_changed"
    return "current"
