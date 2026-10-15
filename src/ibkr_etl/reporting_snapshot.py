from __future__ import annotations

import calendar
import math
import uuid
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Mapping

from .agent_tools import AgentToolContext, execute_tool
from .monthly_reporting_analytics import build_monthly_reporting_analytics
from .util import canonical_json, sha256_text, utc_now_iso


SNAPSHOT_VERSION = "month-end-v4"
FORMULA_VERSION = "benchmark-comparison-v1"
SNAPSHOT_NAMESPACE = uuid.UUID("4b865e0b-f1c7-48aa-a9a4-b2da09ec4f68")


class ReportingSnapshotError(ValueError):
    pass


@dataclass(frozen=True)
class ReportingSnapshotResult:
    snapshot_id: str
    subject_id: str
    period_start: str
    period_end: str
    data_as_of: str
    quality_status: str
    publication_allowed: bool
    publication_blockers: tuple[str, ...]
    data_sha256: str
    inserted: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _require_calendar_month_end(value: str) -> date:
    parsed = date.fromisoformat(value)
    expected = calendar.monthrange(parsed.year, parsed.month)[1]
    if parsed.day != expected:
        raise ReportingSnapshotError("period_end must be the final calendar day of a month")
    return parsed


def active_month_end_snapshot_id(
    connection,
    *,
    tenant_id: str,
    subject_id: str,
    period_end: str,
) -> str | None:
    """Return the one active immutable snapshot without creating a replacement.

    Document rendering must reuse an already-frozen reporting snapshot. Re-freezing
    merely to render a file can supersede the snapshot that an existing companion
    artifact (for example, a presentation) is bound to.
    """

    _require_calendar_month_end(period_end)
    rows = connection.execute(
        """
        SELECT snapshot_id
        FROM reporting_snapshots
        WHERE tenant_id = ? AND subject_id = ? AND period_end = ?
          AND status IN ('frozen', 'approved')
        ORDER BY CASE WHEN status = 'approved' THEN 0 ELSE 1 END,
                 created_at DESC
        LIMIT 2
        """,
        (tenant_id, subject_id, period_end),
    ).fetchall()
    if len(rows) > 1:
        raise ReportingSnapshotError(
            "More than one active reporting snapshot exists for the month end"
        )
    return None if not rows else str(rows[0]["snapshot_id"])


def _strip_generated_at(value: Any) -> Any:
    """Remove volatile timestamps recursively so identical inputs hash identically."""

    if isinstance(value, Mapping):
        return {
            key: _strip_generated_at(item)
            for key, item in value.items()
            if key != "generated_at"
        }
    if isinstance(value, list):
        return [_strip_generated_at(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_generated_at(item) for item in value)
    return value


def _month_end_points(series: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    points: dict[str, dict[str, Any]] = {}
    for raw in series:
        day = str(raw.get("date") or "")
        if len(day) < 7:
            continue
        points[day[:7]] = {
            "date": day,
            "portfolio": float(raw["portfolio"]),
            "benchmark": float(raw["benchmark"]),
        }
    ordered = [points[key] for key in sorted(points)]
    for previous, current in zip(ordered, ordered[1:]):
        current["portfolio_monthly_return"] = (
            current["portfolio"] / previous["portfolio"] - 1.0
        )
        current["benchmark_monthly_return"] = (
            current["benchmark"] / previous["benchmark"] - 1.0
        )
    if ordered:
        ordered[0]["portfolio_monthly_return"] = None
        ordered[0]["benchmark_monthly_return"] = None
    return ordered


def assemble_month_end_payload(
    *,
    subject_id: str,
    period_start: str,
    period_end: str,
    reporting_currency: str,
    tool_results: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], str, bool, tuple[str, ...]]:
    requested_end = _require_calendar_month_end(period_end)
    required = {
        "summary",
        "performance",
        "holdings",
        "primary_benchmark",
        "secondary_benchmark",
        "risk",
        "freshness",
        "sector_allocation",
    }
    missing = required - set(tool_results)
    if missing:
        raise ReportingSnapshotError(f"Missing snapshot inputs: {sorted(missing)}")

    sanitized = {
        name: _strip_generated_at(result) for name, result in tool_results.items()
    }
    data_as_of = str(sanitized["summary"].get("as_of") or "")
    if not data_as_of:
        raise ReportingSnapshotError("Portfolio summary has no as-of date")
    effective = date.fromisoformat(data_as_of)
    staleness = (requested_end - effective).days
    if staleness < 0 or staleness > 7:
        raise ReportingSnapshotError(
            "The latest portfolio observation is outside the allowed seven-day month-end window"
        )

    primary = sanitized["primary_benchmark"]
    secondary = sanitized["secondary_benchmark"]
    benchmark_results = [primary, secondary] + [
        sanitized[key]
        for key in sorted(sanitized)
        if key.startswith("additional_benchmark_")
    ]
    sector = sanitized["sector_allocation"]
    risk = sanitized["risk"]
    blockers: list[str] = []
    for label, result in sanitized.items():
        if result.get("quality_status") == "fail":
            blockers.append(f"{label}:quality_fail")
    for index, result in enumerate(benchmark_results):
        label = "primary" if index == 0 else f"secondary_{index}"
        if not bool(result.get("data", {}).get("publication_allowed")):
            blockers.append(f"{label}_benchmark:not_licensed_for_publication")
    if not bool(sector.get("data", {}).get("available")):
        blockers.append("sector_allocation:validated_classification_unavailable")
    elif not bool(sector.get("data", {}).get("publication_allowed")):
        blockers.append("sector_allocation:not_licensed_for_publication")
    crisis = risk.get("data", {}).get("crisis_replay")
    if not crisis:
        blockers.append("risk:crisis_replay_unavailable")
    else:
        if subject_id in ('alpha-world-ms','alpha-world-t') and reporting_currency=='HKD':
            if subject_id=='alpha-world-t':
                from .alpha_world_t_crisis import validate
            else:
                from .alpha_world_ms_crisis import validate
            validate(crisis,as_of=effective.isoformat(),holdings=sanitized['holdings']['data']['holdings'],
                     nav=sanitized['summary']['data']['latest_nav']['total_text'])
        elif str(crisis.get('calculation_version') or '').startswith('alpha-world-design-composite-'):
            from .profiled_crisis import validate as validate_profiled_crisis
            validate_profiled_crisis(crisis, as_of=effective.isoformat(),
                                     holdings=sanitized['holdings']['data']['holdings'],
                                     nav=sanitized['summary']['data']['latest_nav']['total_text'])
        crisis_as_of = date.fromisoformat(str(crisis["holdings_as_of"]))
        if crisis_as_of > effective:
            raise ReportingSnapshotError(
                "Crisis replay holdings date is after the portfolio data as-of date"
            )

    blockers = sorted(set(blockers))
    warnings_present = any(
        result.get("quality_status") == "warn" for result in sanitized.values()
    )
    quality_status = (
        "fail"
        if any(value.endswith(":quality_fail") for value in blockers)
        else "warn"
        if blockers or warnings_present
        else "pass"
    )
    publication_allowed = quality_status == "pass" and not blockers
    primary_data = primary.get("data", {})
    primary_series = list(primary_data.get("series") or [])
    payload = {
        "snapshot_version": SNAPSHOT_VERSION,
        "formula_version": FORMULA_VERSION,
        "subject_id": subject_id,
        "period_start": date.fromisoformat(period_start).isoformat(),
        "period_end": requested_end.isoformat(),
        "data_as_of": data_as_of,
        "reporting_currency": reporting_currency.upper(),
        "quality_status": quality_status,
        "publication_allowed": publication_allowed,
        "publication_blockers": blockers,
        "benchmark": primary_data.get("benchmark"),
        "secondary_benchmark": secondary.get("data", {}).get("benchmark"),
        "benchmark_comparisons": [
            result.get("data", {}) for result in benchmark_results
        ],
        "portfolio_summary": sanitized["summary"].get("data"),
        "performance": sanitized["performance"].get("data"),
        "performance_comparison": primary_data,
        "secondary_performance_comparison": secondary.get("data"),
        "month_end_performance": _month_end_points(primary_series),
        "holdings": sanitized["holdings"].get("data"),
        "sector_allocation": sector.get("data"),
        "risk": risk.get("data"),
        "monthly_reporting": sanitized.get("monthly_reporting", {}).get("data"),
        "freshness": sanitized["freshness"].get("data"),
        "source_citations": {
            name: result.get("citations", []) for name, result in sanitized.items()
        },
    }
    design = sanitized.get('report_profile') if subject_id not in ('alpha-world-ms','alpha-world-t') else None
    if design is not None:
        from .profiled_composite import analyze as analyze_profiled, ANALYTICS_VERSION
        evidence_data = design.get('data') or {}
        payload['report_profile'] = evidence_data.get('report_profile')
        payload['methodology_identity'] = evidence_data.get('methodology_identity')
        inception = (payload['report_profile'] or {}).get('inception')
        _check_hkd_design(payload, label="The composite", analyze=analyze_profiled, version=ANALYTICS_VERSION,
                          reporting_currency=reporting_currency, inception=inception)
    if subject_id in ('alpha-world-ms','alpha-world-t'):
        if subject_id=='alpha-world-t':
            from .alpha_world_t_analytics import analyze,VERSION,methodology_identity
            payload['methodology_identity']=methodology_identity()
        else:
            from .alpha_world_ms_analytics import analyze
            from .alpha_world_ms_hkd import VERSION
        label='T' if subject_id=='alpha-world-t' else 'MS'
        _check_hkd_design(payload, label=label, analyze=analyze, version=VERSION,
                          reporting_currency=reporting_currency, inception='2025-01-20')
    return payload, quality_status, publication_allowed, tuple(blockers)


def _check_hkd_design(payload, *, label, analyze, version, reporting_currency, inception):
    """MS, T and design composites: recompute every metric from the full HKD history."""
    if reporting_currency.upper() != 'HKD' or payload['period_start'] != inception:
        raise ReportingSnapshotError(label+' requires the approved HKD inception contract')
    account = (payload['portfolio_summary'] or {}).get('account') or {}
    nav = (payload['portfolio_summary'] or {}).get('latest_nav') or {}
    if account.get('base_currency') != 'HKD' or nav.get('currency') != 'HKD':
        raise ReportingSnapshotError(label+' summary currency differs from the reporting contract')
    series = (payload['performance'] or {}).get('series') or []
    if not series or not math.isclose(float(nav['total_text']),float(series[-1]['nav']),rel_tol=1e-12):
        raise ReportingSnapshotError(label+' summary NAV differs from its full history')
    if any(row.get('currency') != 'HKD' for row in (payload['holdings'] or {}).get('holdings', [])):
        raise ReportingSnapshotError(label+' holdings contain another reporting currency')
    metrics,_,evidence = analyze(payload)
    stored = (payload['risk'] or {}).get('metrics') or {}
    for field,value in metrics.items():
        recorded = stored.get(field)
        if (recorded is None) != (value is None) or (value is not None and
                (not math.isfinite(float(recorded)) or not math.isclose(float(recorded),value,rel_tol=1e-10,abs_tol=1e-12))):
            raise ReportingSnapshotError(label+' reported metric differs from full HKD history: '+field)
    payload['formula_version'] = version
    payload['risk']['analytics_evidence'] = evidence
    points = {}
    for row in series:
        points[row['date'][:7]] = {'date':row['date'],'portfolio':float(row['nav'])}
    payload['month_end_performance'] = [points[key] for key in sorted(points)]


def _collect_tool_results(
    connection,
    *,
    tenant_id: str,
    subject_id: str,
    period_end: str,
    reporting_currency: str,
) -> dict[str, Mapping[str, Any]]:
    if subject_id=='alpha-world-t':
        if reporting_currency!='HKD':
            raise ReportingSnapshotError('T snapshots cannot be collected under a USD contract')
        from .alpha_world_t_reporting import load
        snapshot=load(connection,period_end,tenant_id=tenant_id)
        def result(data,quality='pass'):
            return {'as_of':snapshot['data_as_of'],'quality_status':quality,'data':data,
                'citations':[{'subject_id':subject_id,'currency':'HKD','through':snapshot['data_as_of'],
                    'tables':['subject_nav_series','synthetic_portfolio_components','risk_free_rates'],
                    'calculation_version':snapshot['formula_version']}]}
        return {'summary':result(snapshot['portfolio_summary']),
            'performance':result(snapshot['performance']),'holdings':result(snapshot['holdings']),
            'risk':result(snapshot['risk'],'warn'),'sector_allocation':result(snapshot['sector_allocation']),
            'monthly_reporting':result(snapshot['monthly_reporting']),
            'primary_benchmark':result(snapshot['performance_comparison'],'warn'),
            'secondary_benchmark':result(snapshot['secondary_performance_comparison'],'warn'),
            'freshness':result({'portfolio':{'nav_date':snapshot['data_as_of'],'position_date':snapshot['data_as_of']},
                'risk_free_date':snapshot['risk']['risk_free']['rate_date']})}
    if subject_id == 'alpha-world-ms' and reporting_currency != 'HKD':
        raise ReportingSnapshotError('MS snapshots cannot be collected under a USD contract')
    if subject_id == 'alpha-world-ms':
        # These factsheet metrics do not depend on a benchmark intersection.
        # HIBOR + Spread's comparison convention and crisis attribution remain
        # unqualified: expose that limitation without querying legacy USD/GSPC
        # comparisons or converting their absence into missing core NAV metrics.
        context = AgentToolContext(tenant_id=tenant_id,
            actor_object_id='system-reporting-snapshot',
            allowed_subject_ids=frozenset({subject_id}),correlation_id=str(uuid.uuid4()))
        common={'subject_id':subject_id,'as_of':period_end}
        results={name:execute_tool(connection,context,tool,common) for name,tool in (
            ('summary','get_portfolio_summary'),('performance','get_performance_series'),
            ('holdings','get_holdings'),('risk','get_risk_metrics'),
            ('freshness','get_data_freshness'),('sector_allocation','get_sector_allocation'))}
        for name in ('primary_benchmark','secondary_benchmark'):
            results[name]={'as_of':results['summary']['as_of'],'quality_status':'warn',
                'data':{'available':False,'series':[],'metrics':{},'publication_allowed':False,
                    'reason':'HIBOR + Spread comparison methodology is not qualified'},
                'citations':[]}
        results['monthly_reporting']=build_monthly_reporting_analytics(connection,
            subject_id=subject_id,period_end=period_end,reporting_currency=reporting_currency)
        return results
    if reporting_currency == 'HKD':
        from .profiled_composite import is_profiled
        if is_profiled(connection, subject_id, tenant_id=tenant_id):
            # A composite in the Alpha World MS or T design: T's reporting
            # inputs from its own approved definition, never the USD tools.
            from .profiled_reporting import load as load_profiled, tool_results
            try:
                return tool_results(load_profiled(connection, subject_id, period_end, tenant_id=tenant_id))
            except ValueError as error:
                raise ReportingSnapshotError(str(error)) from error
    mappings = connection.execute(
        """
        SELECT benchmark_id, mapping_type
        FROM portfolio_benchmark_mapping
        WHERE subject_id = ? AND effective_from <= ?
          AND (effective_to IS NULL OR effective_to >= ?)
        ORDER BY CASE mapping_type WHEN 'primary' THEN 0 ELSE 1 END,
                 benchmark_id
        """,
        (subject_id, period_end, period_end),
    ).fetchall()
    benchmark_ids = [str(row["benchmark_id"]) for row in mappings]
    if len(benchmark_ids) < 2:
        if subject_id == "U16088571":
            benchmark_ids = ["GSPC", "IXIC"]
        else:
            raise ReportingSnapshotError(
                "At least one primary and one secondary benchmark must be configured"
            )
    context = AgentToolContext(
        tenant_id=tenant_id,
        actor_object_id="system-reporting-snapshot",
        allowed_subject_ids=frozenset({subject_id}),
        correlation_id=str(uuid.uuid4()),
    )
    common = {"subject_id": subject_id, "as_of": period_end}
    results = {
        "summary": execute_tool(connection, context, "get_portfolio_summary", common),
        "performance": execute_tool(connection, context, "get_performance_series", common),
        "holdings": execute_tool(
            connection, context, "get_holdings", {**common, "limit": 200}
        ),
        "primary_benchmark": execute_tool(
            connection,
            context,
            "compare_with_benchmarks",
            {**common, "benchmark_id": benchmark_ids[0]},
        ),
        "secondary_benchmark": execute_tool(
            connection,
            context,
            "compare_with_benchmarks",
            {**common, "benchmark_id": benchmark_ids[1]},
        ),
        "risk": execute_tool(connection, context, "get_risk_metrics", common),
        "freshness": execute_tool(connection, context, "get_data_freshness", common),
        "sector_allocation": execute_tool(
            connection, context, "get_sector_allocation", common
        ),
    }
    for index, benchmark_id in enumerate(benchmark_ids[2:], start=1):
        results[f"additional_benchmark_{index}"] = execute_tool(
            connection,
            context,
            "compare_with_benchmarks",
            {**common, "benchmark_id": benchmark_id},
        )
    results["monthly_reporting"] = build_monthly_reporting_analytics(
        connection,
        subject_id=subject_id,
        period_end=period_end,
        reporting_currency=reporting_currency,
    )
    from .reporting_currency_holdings import normalize_collected_report_holdings
    return normalize_collected_report_holdings(connection, subject_id, reporting_currency, results)


def freeze_month_end_snapshot(
    connection,
    *,
    tenant_id: str,
    subject_id: str,
    period_start: str,
    period_end: str,
    reporting_currency: str,
    reject_quality_fail: bool = False,
) -> ReportingSnapshotResult:
    tool_results = _collect_tool_results(
        connection,
        tenant_id=tenant_id,
        subject_id=subject_id,
        period_end=period_end,
        reporting_currency=reporting_currency,
    )
    payload, quality, publication_allowed, blockers = assemble_month_end_payload(
        subject_id=subject_id,
        period_start=period_start,
        period_end=period_end,
        reporting_currency=reporting_currency,
        tool_results=tool_results,
    )
    if reject_quality_fail and (
        quality == "fail" or not (payload.get("monthly_reporting") or {}).get("available")
    ):
        raise ReportingSnapshotError(
            "Month-end data-quality or reporting-analytics checks have not passed"
        )
    payload_json = canonical_json(payload)
    digest = sha256_text(payload_json)
    snapshot_id = str(
        uuid.uuid5(
            SNAPSHOT_NAMESPACE,
            f"{tenant_id}:{subject_id}:{period_end}:{digest}",
        )
    )
    primary = payload.get("benchmark") or {}
    source_manifest = canonical_json(
        {
            "snapshot_version": SNAPSHOT_VERSION,
            "sources": payload["source_citations"],
            "benchmark_license_status": primary.get("license_status"),
            "publication_allowed": publication_allowed,
        }
    )
    row = connection.execute(
        """
        INSERT INTO reporting_snapshots(
            snapshot_id, tenant_id, subject_id, period_start, period_end,
            as_of_at, reporting_currency, formula_version,
            source_manifest_json, snapshot_payload_json, data_sha256,
            quality_status, status, publication_allowed,
            publication_blockers_json, benchmark_id, benchmark_convention,
            benchmark_provider, data_as_of, created_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'frozen', ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT (tenant_id, subject_id, period_end, data_sha256) DO NOTHING
        RETURNING snapshot_id
        """,
        (
            snapshot_id,
            tenant_id,
            subject_id,
            payload["period_start"],
            payload["period_end"],
            utc_now_iso(),
            payload["reporting_currency"],
            payload['formula_version'],
            source_manifest,
            payload_json,
            digest,
            quality,
            publication_allowed,
            canonical_json(list(blockers)),
            primary.get("benchmark_id"),
            primary.get("return_convention"),
            primary.get("provider"),
            payload["data_as_of"],
            utc_now_iso(),
        ),
    ).fetchone()
    inserted = row is not None
    if inserted:
        connection.execute(
            """
            UPDATE reporting_snapshots
            SET status = 'superseded'
            WHERE tenant_id = ? AND subject_id = ? AND period_end = ?
              AND snapshot_id <> ? AND status = 'frozen'
            """,
            (tenant_id, subject_id, payload["period_end"], snapshot_id),
        )
    connection.commit()
    return ReportingSnapshotResult(
        snapshot_id=snapshot_id,
        subject_id=subject_id,
        period_start=payload["period_start"],
        period_end=payload["period_end"],
        data_as_of=payload["data_as_of"],
        quality_status=quality,
        publication_allowed=publication_allowed,
        publication_blockers=blockers,
        data_sha256=digest,
        inserted=inserted,
    )
