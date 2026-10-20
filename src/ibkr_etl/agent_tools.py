from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Callable, Mapping

from .benchmark_analytics import ComparisonRow, comparison_metrics, portfolio_risk_metrics
from .crisis_replay import load_crisis_replay
from .sector_classification import YAHOO_PROVIDER, aggregate_sector_allocation, has_long_name
from .util import canonical_json, utc_now_iso
from .subject_read_contract import ACTIVE_NAV_CURRENCY_SQL, require_ms_hkd_contract, synthetic_exposure


TOOL_AUDIT_LOGGER = logging.getLogger("uvicorn.error.ibkr_agent")
CALCULATION_VERSION = "benchmark-comparison-v1"


class AgentToolError(ValueError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class AgentToolContext:
    tenant_id: str
    actor_object_id: str
    allowed_subject_ids: frozenset[str]
    correlation_id: str


@dataclass(frozen=True)
class AgentToolResult:
    tool_name: str
    subject_id: str
    as_of: str | None
    quality_status: str
    data: Mapping[str, Any]
    citations: tuple[Mapping[str, Any], ...]
    generated_at: str
    calculation_version: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    executor: Callable[[Any, str, str | None, Mapping[str, Any]], AgentToolResult]


def _subject_schema(*, benchmark: bool = False, limit: bool = False) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "subject_id": {
            "type": "string",
            "description": "Authorized account or portfolio identifier.",
        },
        "as_of": {
            "type": ["string", "null"],
            "format": "date",
            "description": "Optional inclusive YYYY-MM-DD cutoff.",
        },
    }
    if benchmark:
        properties["benchmark_id"] = {
            "type": ["string", "null"],
            "description": "Optional configured benchmark identifier.",
        }
    if limit:
        properties["limit"] = {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
            "default": 100,
        }
    return {
        "type": "object",
        "properties": properties,
        "required": ["subject_id"],
        "additionalProperties": False,
    }


def _effective_as_of(connection, subject_id: str, requested: str | None) -> str:
    parsed = date.fromisoformat(requested).isoformat() if requested else None
    row = connection.execute(
        f"""
        SELECT MAX(snapshot_date) AS latest
        FROM subject_nav_series
        WHERE subject_id = %s
          AND currency = {ACTIVE_NAV_CURRENCY_SQL}
          AND (%s::text IS NULL OR snapshot_date <= %s)
        """,
        (subject_id, parsed, parsed),
    ).fetchone()
    if row is None or row["latest"] is None:
        raise AgentToolError(404, "portfolio_data_unavailable", "No NAV is available on or before the requested date.")
    return str(row["latest"])


def _comparison_rows(records: list[Mapping[str, Any]]) -> list[ComparisonRow]:
    return [
        ComparisonRow(
            observation_date=str(row["observation_date"]),
            portfolio_value=float(row["portfolio_value"]),
            benchmark_level=float(row["benchmark_level"]),
            normalized_portfolio=float(row["normalized_portfolio"]),
            normalized_benchmark=float(row["normalized_benchmark"]),
            portfolio_return=float(row["portfolio_return"]) if row["portfolio_return"] is not None else None,
            benchmark_return=float(row["benchmark_return"]) if row["benchmark_return"] is not None else None,
            excess_return=float(row["excess_return"]) if row["excess_return"] is not None else None,
        )
        for row in records
    ]


def _summary(connection, subject_id: str, as_of: str | None, arguments: Mapping[str, Any]) -> AgentToolResult:
    effective = _effective_as_of(connection, subject_id, as_of)
    account = connection.execute(
        """SELECT subject_id AS account_id, display_name AS alias, base_currency, reporting_currency,
                  subject_type AS account_type, updated_at AS last_seen_at,
                  subject_type
           FROM platform_subjects WHERE subject_id = %s""",
        (subject_id,),
    ).fetchone()
    coverage = connection.execute(
        f"""
        SELECT MIN(snapshot_date) AS first_date, MAX(snapshot_date) AS last_date,
               COUNT(*) AS observations
        FROM subject_nav_series
        WHERE subject_id = %s AND snapshot_date <= %s
          AND currency = {ACTIVE_NAV_CURRENCY_SQL}
        """,
        (subject_id, effective),
    ).fetchone()
    nav = connection.execute(
        f"""
        SELECT snapshot_date, currency, total_text
        FROM subject_nav_series
        WHERE subject_id = %s AND snapshot_date = %s
          AND currency = {ACTIVE_NAV_CURRENCY_SQL}
        ORDER BY currency LIMIT 1
        """,
        (subject_id, effective),
    ).fetchone()
    if account and str(account.get("subject_type")) == "composite_portfolio":
        positions = connection.execute(
            """SELECT COUNT(*) AS positions FROM synthetic_portfolio_components
               WHERE portfolio_id = %s
                 AND base_currency = (SELECT COALESCE(reporting_currency,base_currency)
                   FROM platform_subjects WHERE subject_id = %s)
                 AND snapshot_date = (
                 SELECT MAX(snapshot_date) FROM synthetic_portfolio_components
                 WHERE portfolio_id = %s AND snapshot_date <= %s
                   AND base_currency = (SELECT COALESCE(reporting_currency,base_currency)
                     FROM platform_subjects WHERE subject_id = %s))""",
            (subject_id, subject_id, subject_id, effective, subject_id),
        ).fetchone()
    else:
        positions = connection.execute(
            """
            SELECT COUNT(*) AS positions
            FROM subject_position_series
            WHERE subject_id = %s AND snapshot_date = (
              SELECT MAX(snapshot_date) FROM subject_position_series
              WHERE subject_id = %s AND snapshot_date <= %s
            ) AND COALESCE(NULLIF(quantity_text, ''), '0')::numeric <> 0
            """,
            (subject_id, subject_id, effective),
        ).fetchone()
    return AgentToolResult(
        "get_portfolio_summary",
        subject_id,
        effective,
        "pass",
        {
            "account": dict(account) if account else None,
            "nav_coverage": dict(coverage),
            "latest_nav": dict(nav) if nav else None,
            "positions": int(positions["positions"] or 0),
        },
        (
            {"table": "accounts", "subject_id": subject_id},
            {"table": "subject_nav_series", "subject_id": subject_id, "through": effective},
            {"table": "subject_position_series", "subject_id": subject_id, "through": effective},
        ),
        utc_now_iso(),
    )


def _attach_external_flows(connection, subject_id: str, series: list[dict[str, Any]]) -> None:
    """Add each row's net external flow since the previous row, as the website does."""
    if not series:
        return
    series[0]["flow"] = 0.0
    if len(series) < 2:
        return
    from .provisional_benchmark import _external_flows

    flows = _external_flows(connection, subject_id)
    for previous, current in zip(series, series[1:]):
        current["flow"] = sum(
            amount
            for flow_day, amount in flows.items()
            if str(previous["date"]) < flow_day <= str(current["date"])
        )


def _performance(connection, subject_id: str, as_of: str | None, arguments: Mapping[str, Any]) -> AgentToolResult:
    effective = _effective_as_of(connection, subject_id, as_of)
    records = connection.execute(
        f"""
        SELECT snapshot_date, currency, total_text
        FROM subject_nav_series
        WHERE subject_id = %s
          AND currency = {ACTIVE_NAV_CURRENCY_SQL}
          AND snapshot_date BETWEEN COALESCE((
              SELECT history_start_date::text FROM platform_subjects WHERE subject_id = %s
          ), '2025-01-20') AND %s
        ORDER BY snapshot_date
        """,
        (subject_id, subject_id, effective),
    ).fetchall()
    series = [
        {"date": row["snapshot_date"], "currency": row["currency"], "nav": float(row["total_text"])}
        for row in records
        if row["total_text"] not in (None, "")
    ]
    _attach_external_flows(connection, subject_id, series)
    return AgentToolResult(
        "get_performance_series",
        subject_id,
        effective,
        "pass" if series else "fail",
        {"series": series, "observations": len(series)},
        ({"table": "subject_nav_series", "subject_id": subject_id, "through": effective},),
        utc_now_iso(),
    )


def _holdings(connection, subject_id: str, as_of: str | None, arguments: Mapping[str, Any]) -> AgentToolResult:
    effective = _effective_as_of(connection, subject_id, as_of)
    limit = int(arguments.get("limit", 100))
    if not 1 <= limit <= 200:
        raise AgentToolError(400, "invalid_limit", "limit must be between 1 and 200")
    subject = connection.execute(
        "SELECT subject_type FROM platform_subjects WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()
    composite = bool(subject and str(subject["subject_type"]) == "composite_portfolio")
    position_table = "synthetic_portfolio_components" if composite else "subject_position_series"
    id_column = "portfolio_id" if composite else "subject_id"
    currency_filter = (" AND base_currency = (SELECT COALESCE(reporting_currency,base_currency) "
        "FROM platform_subjects WHERE subject_id = %s)") if composite else ''
    snapshot = connection.execute(
        f"SELECT MAX(snapshot_date) AS latest FROM {position_table} WHERE {id_column} = %s AND snapshot_date <= %s" + currency_filter,
        (subject_id, effective, subject_id) if composite else (subject_id,effective),
    ).fetchone()["latest"]
    if composite:
        records = [] if snapshot is None else connection.execute(
            """
            SELECT snapshot_date, component_id AS conid, component_id AS symbol,
                   component_name AS description, component_type AS asset_category,
                   base_currency AS currency, coefficient, source_currency,
                   source_value, fx_rate_to_base, base_value, normalized_value,
                   contribution::text AS market_value_text,
                   direction, observation_date
            FROM synthetic_portfolio_components
            WHERE portfolio_id = %s AND snapshot_date = %s
              AND base_currency = (SELECT COALESCE(reporting_currency,base_currency)
                FROM platform_subjects WHERE subject_id = %s)
            ORDER BY ABS(contribution) DESC LIMIT %s
            """,
            (subject_id, snapshot, subject_id, limit),
        ).fetchall()
    else:
        records = [] if snapshot is None else connection.execute(
        """
        SELECT p.snapshot_date, p.conid, i.symbol, i.description, i.asset_category,
               p.currency, p.quantity_text, p.mark_price_text, p.market_value_text
        FROM subject_position_series p
        LEFT JOIN instruments i ON i.conid = p.conid
        WHERE p.subject_id = %s AND p.snapshot_date = %s
          AND COALESCE(NULLIF(p.quantity_text, ''), '0')::numeric <> 0
        ORDER BY ABS(COALESCE(NULLIF(p.market_value_text, ''), '0')::numeric) DESC
        LIMIT %s
        """,
        (subject_id, snapshot, limit),
        ).fetchall()
    holdings = [synthetic_exposure(row) if composite else dict(row) for row in records]
    return AgentToolResult(
        "get_holdings",
        subject_id,
        str(snapshot) if snapshot else effective,
        "pass" if holdings else "warn",
        {"holdings": holdings, "returned": len(holdings), "limit": limit},
        ({"table": position_table, "subject_id": subject_id, "snapshot_date": snapshot},),
        utc_now_iso(),
    )


def _benchmark(connection, subject_id: str, as_of: str | None, arguments: Mapping[str, Any]) -> AgentToolResult:
    from .website_benchmarks import website_comparison_version
    effective = _effective_as_of(connection, subject_id, as_of)
    # Composite website comparisons use the currently approved HKD mapping,
    # even when a historical NAV cutoff predates that mapping's migration.
    # Observation cutoff and mapping selection are deliberately separate.
    composite = subject_id in ('alpha-world-ms', 'alpha-world-t')
    requested = arguments.get("benchmark_id")
    benchmark = connection.execute(
        """
        SELECT b.benchmark_id, b.symbol, b.name, b.provider, b.return_convention,
               b.license_status, m.mapping_type, m.reporting_currency
        FROM portfolio_benchmark_mapping m
        JOIN benchmark_master b ON b.benchmark_id = m.benchmark_id
        WHERE m.subject_id = %s AND (%s::text IS NULL OR b.benchmark_id = %s)
          AND b.active = true
          AND m.effective_from <= CASE WHEN %s THEN CURRENT_DATE::text ELSE %s END
          AND (m.effective_to IS NULL OR m.effective_to >= CASE WHEN %s THEN CURRENT_DATE::text ELSE %s END)
        ORDER BY CASE m.mapping_type WHEN 'primary' THEN 0 ELSE 1 END, b.name
        LIMIT 1
        """,
        (subject_id, requested, requested, composite, effective, composite, effective),
    ).fetchone()
    if benchmark is None:
        raise AgentToolError(404, "benchmark_unavailable", "The requested benchmark is not configured for this account and date.")
    if subject_id in ('alpha-world-ms','alpha-world-t') and benchmark['reporting_currency']!='HKD':
        raise AgentToolError(409,'ms_benchmark_currency_not_ready','MS benchmark comparison has not been qualified in HKD.')
    comparison_version = website_comparison_version(subject_id, benchmark['reporting_currency'])
    records = connection.execute(
        """
        SELECT observation_date, portfolio_value, benchmark_level,
               normalized_portfolio, normalized_benchmark, portfolio_return,
               benchmark_return, excess_return
        FROM performance_comparison_daily
        WHERE subject_id = %s AND benchmark_id = %s
          AND calculation_version = %s AND quality_status IN ('pass', 'warn')
          AND reporting_currency = %s
          AND observation_date <= %s
        ORDER BY observation_date
        """,
        (subject_id, benchmark["benchmark_id"], comparison_version, benchmark['reporting_currency'], effective),
    ).fetchall()
    rows = _comparison_rows(records)
    quality = "warn" if benchmark["license_status"] != "approved" else "pass"
    return AgentToolResult(
        "compare_with_benchmarks",
        subject_id,
        effective,
        quality if rows else "fail",
        {
            "benchmark": dict(benchmark),
            "reporting_currency": benchmark['reporting_currency'],
            "mapping_policy": "current-approved" if composite else "as-of-date",
            "metrics": comparison_metrics(rows) if rows else {},
            "series": [
                {"date": row.observation_date, "portfolio": row.normalized_portfolio, "benchmark": row.normalized_benchmark}
                for row in rows
            ],
            "publication_allowed": benchmark["license_status"] == "approved",
        },
        (
            {"table": "portfolio_benchmark_mapping", "subject_id": subject_id, "benchmark_id": benchmark["benchmark_id"]},
            {"table": "performance_comparison_daily", "subject_id": subject_id, "benchmark_id": benchmark["benchmark_id"], "through": effective, "calculation_version": comparison_version},
        ),
        utc_now_iso(),
        comparison_version,
    )


def _risk(connection, subject_id: str, as_of: str | None, arguments: Mapping[str, Any]) -> AgentToolResult:
    effective = _effective_as_of(connection, subject_id, as_of)
    if subject_id=='alpha-world-t':
        from .alpha_world_t_analytics import load_risk_snapshot
        from .alpha_world_t_crisis import load as load_crisis
        from .db import PostgresConnection
        portable=PostgresConnection(connection) if type(connection).__module__.startswith('psycopg') else connection
        tenant=arguments.get('_verified_tenant_id')
        snapshot,metrics,evidence=load_risk_snapshot(portable,effective,tenant_id=tenant)
        try:crisis=load_crisis(portable,snapshot,tenant_id=tenant);crisis_error=None
        except ValueError as error:crisis=None;crisis_error=str(error)
        from .qualified_agent_risk import t_agent_payload
        try:
            qualified = t_agent_payload(connection, tenant_id=tenant, as_of=effective,
                source_snapshot=snapshot, core_metrics=metrics, crisis=crisis)
        except ValueError:
            return AgentToolResult('get_risk_metrics', subject_id, effective, 'fail',
                {'metrics': {}, 'reporting_currency': 'HKD', 'crisis_replay': None,
                 'message': 'Exact-date T risk cache needs validated refresh; no stale values substituted.'},
                ({'table':'risk_analytics_snapshots','subject_id':subject_id,'through':effective},),
                utc_now_iso(), evidence['version'])
        metrics = qualified['metrics']
        return AgentToolResult('get_risk_metrics',subject_id,snapshot['data_as_of'],'warn',
            {'metrics':metrics,'risk_free':snapshot['risk']['risk_free'],'analytics_evidence':evidence,
             'reporting_currency':'HKD','crisis_replay':crisis,'crisis_qualification_error':crisis_error},
            ({'table':'subject_nav_series','subject_id':subject_id,'currency':'HKD','through':effective},
             {'table':'risk_free_rates','series_id':'HKAB-HIBOR-1M','currency':'HKD','through':effective}),
            utc_now_iso(),evidence['version'])
    if subject_id == 'alpha-world-ms':
        from .alpha_world_ms_hkd import load_risk_snapshot
        from .db import PostgresConnection
        # Shared HKD/HIBOR queries use portable placeholders. Website tools
        # receive a raw psycopg connection; workers already use the adapter.
        portable = (PostgresConnection(connection)
                    if type(connection).__module__.startswith('psycopg') else connection)
        tenant=arguments.get('_verified_tenant_id')
        snapshot,metrics,evidence=load_risk_snapshot(portable,effective,tenant_id=tenant)
        from .alpha_world_ms_crisis import load as load_ms_crisis
        try:
            crisis=load_ms_crisis(portable,snapshot,tenant_id=tenant,identity_proxy=True)
            crisis_error=None
        except ValueError as error:
            # Missing or inconsistent stress evidence remains unavailable. Never
            # clear the release blocker by falling back to the legacy USD replay.
            crisis=None;crisis_error=str(error)
        from .qualified_agent_risk import ms_agent_payload
        try:
            qualified=ms_agent_payload(connection,tenant_id=tenant,as_of=effective,
                source_snapshot=snapshot,core_metrics=metrics,crisis=crisis)
        except ValueError:
            return AgentToolResult('get_risk_metrics',subject_id,effective,'fail',
                {'metrics':{},'reporting_currency':'HKD','crisis_replay':None,
                 'message':'Exact-date MS risk cache needs validated refresh; no stale values substituted.'},
                ({'table':'risk_analytics_snapshots','subject_id':subject_id,'through':effective},),
                utc_now_iso(),evidence['version'])
        metrics=qualified['metrics']
        return AgentToolResult('get_risk_metrics',subject_id,snapshot['data_as_of'],'warn',
            {'metrics':metrics,'risk_free':snapshot['risk']['risk_free'],'analytics_evidence':evidence,
             'reporting_currency':'HKD','crisis_replay':crisis,'crisis_qualification_error':crisis_error},
            ({'table':'subject_nav_series','subject_id':subject_id,'currency':'HKD','through':effective},
             {'table':'risk_free_rates','series_id':'HKAB-HIBOR-1M','currency':'HKD','through':effective}),
            utc_now_iso(),evidence['version'])
    mapping = connection.execute(
        """
        SELECT benchmark_id FROM portfolio_benchmark_mapping
        WHERE subject_id = %s AND mapping_type = 'primary'
          AND effective_from <= %s AND (effective_to IS NULL OR effective_to >= %s)
        ORDER BY effective_from DESC LIMIT 1
        """,
        (subject_id, effective, effective),
    ).fetchone()
    records = [] if mapping is None else connection.execute(
        """
        SELECT observation_date, portfolio_value, benchmark_level,
               normalized_portfolio, normalized_benchmark, portfolio_return,
               benchmark_return, excess_return
        FROM performance_comparison_daily
        WHERE subject_id = %s AND benchmark_id = %s
          AND calculation_version = %s AND quality_status IN ('pass', 'warn')
          AND observation_date <= %s ORDER BY observation_date
        """,
        (subject_id, mapping["benchmark_id"], CALCULATION_VERSION, effective),
    ).fetchall()
    rate = connection.execute(
        """
        SELECT rate_date, series_id, currency, tenor, annual_rate, provider, quality_status
        FROM risk_free_rates
        WHERE currency = 'USD' AND rate_date <= %s AND quality_status IN ('pass', 'warn')
        ORDER BY rate_date DESC LIMIT 1
        """,
        (effective,),
    ).fetchone()
    rows = _comparison_rows(records)
    metrics = portfolio_risk_metrics(rows, float(rate["annual_rate"]) if rate else None)
    crisis_replay = load_crisis_replay(
        connection, account_id=subject_id, as_of=effective
    )
    if crisis_replay is not None:
        metrics["crisis_maximum_drawdown"] = crisis_replay["maximum_drawdown"]
    return AgentToolResult(
        "get_risk_metrics",
        subject_id,
        effective,
        "pass" if rows and rate else "warn" if rows else "fail",
        {
            "metrics": metrics,
            "risk_free": dict(rate) if rate else None,
            "benchmark_id": mapping["benchmark_id"] if mapping else None,
            "crisis_replay": crisis_replay,
        },
        (
            {"table": "performance_comparison_daily", "subject_id": subject_id, "through": effective, "calculation_version": CALCULATION_VERSION},
            {"table": "risk_free_rates", "currency": "USD", "through": effective},
            {
                "table": "crisis_replay_daily",
                "subject_id": subject_id,
                "through": effective,
                "holdings_as_of": (
                    crisis_replay["holdings_as_of"] if crisis_replay else None
                ),
            },
        ),
        utc_now_iso(),
        CALCULATION_VERSION,
    )


def _freshness(connection, subject_id: str, as_of: str | None, arguments: Mapping[str, Any]) -> AgentToolResult:
    effective = _effective_as_of(connection, subject_id, as_of)
    subject = connection.execute(
        "SELECT subject_type FROM platform_subjects WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()
    composite = bool(subject and str(subject["subject_type"]) == "composite_portfolio")
    portfolio = connection.execute(
        f"""
        SELECT MAX(snapshot_date) AS nav_date,
               (SELECT MAX(snapshot_date) FROM subject_position_series WHERE subject_id = %s AND snapshot_date <= %s) AS position_date,
               (SELECT MAX(snapshot_date) FROM subject_cash_balance_series WHERE subject_id = %s AND snapshot_date <= %s) AS cash_date
        FROM subject_nav_series WHERE subject_id = %s AND snapshot_date <= %s
          AND currency = {ACTIVE_NAV_CURRENCY_SQL}
        """,
        (subject_id, effective, subject_id, effective, subject_id, effective),
    ).fetchone()
    if composite:
        component_date = connection.execute(
            """SELECT MAX(snapshot_date) AS latest FROM synthetic_portfolio_components WHERE portfolio_id = %s AND snapshot_date <= %s
                AND base_currency = (SELECT COALESCE(reporting_currency,base_currency) FROM platform_subjects WHERE subject_id = %s)""",
            (subject_id, effective, subject_id),
        ).fetchone()["latest"]
        portfolio = dict(portfolio)
        portfolio["position_date"] = component_date
        portfolio["cash_date"] = portfolio["nav_date"]
    benchmarks = connection.execute(
        """
        SELECT m.benchmark_id, m.mapping_type, MAX(p.observation_date) AS latest_date,
               MAX(p.quality_status) AS quality_status
        FROM portfolio_benchmark_mapping m
        LEFT JOIN benchmark_prices p ON p.benchmark_id = m.benchmark_id AND p.observation_date <= %s
        WHERE m.subject_id = %s GROUP BY m.benchmark_id, m.mapping_type
        ORDER BY CASE m.mapping_type WHEN 'primary' THEN 0 ELSE 1 END
        """,
        (effective, subject_id),
    ).fetchall()
    if subject_id in ('alpha-world-ms','alpha-world-t'):
        from .hibor import load_period_average
        try:
            if subject_id=='alpha-world-t':
                from .db import PostgresConnection
                portable=PostgresConnection(connection) if type(connection).__module__.startswith('psycopg') else connection
                fixing=load_period_average(portable,'2025-01-20',effective,tenant_id=arguments.get('_verified_tenant_id'))
            else:fixing = load_period_average(connection,'2025-01-20',effective)
            rate = {'latest_date':fixing['rate_date']}
            rate_quality = 'pass'
        except ValueError:
            rate = None
            rate_quality = 'fail'
    else:
        rate = connection.execute(
            "SELECT MAX(rate_date) AS latest_date FROM risk_free_rates WHERE currency = 'USD' AND rate_date <= %s AND quality_status IN ('pass', 'warn')",
            (effective,),
        ).fetchone()
        rate_quality = 'pass' if rate and rate['latest_date'] else 'warn'
    run = connection.execute(
        "SELECT run_id, run_type, completed_at, status, row_count FROM ingestion_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    return AgentToolResult(
        "get_data_freshness",
        subject_id,
        effective,
        rate_quality if portfolio and portfolio["nav_date"] and (not composite or portfolio['position_date']==portfolio['nav_date']) else "fail",
        {"portfolio": dict(portfolio), "benchmarks": [dict(row) for row in benchmarks], "risk_free_date": rate["latest_date"] if rate else None, "latest_ingestion_run": dict(run) if run else None},
        (
            {"table": "subject_nav_series", "subject_id": subject_id},
            {"table": "benchmark_prices", "subject_id": subject_id},
            {"table": "risk_free_rates", "currency": "HKD" if subject_id in ('alpha-world-ms','alpha-world-t') else "USD"},
            {"table": "ingestion_runs"},
        ),
        utc_now_iso(),
    )


def _sector_allocation(connection, subject_id: str, as_of: str | None, arguments: Mapping[str, Any]) -> AgentToolResult:
    effective = _effective_as_of(connection, subject_id, as_of)
    subject = connection.execute(
        "SELECT subject_type FROM platform_subjects WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()
    if subject and str(subject["subject_type"]) == "composite_portfolio":
        snapshot = connection.execute(
            """SELECT MAX(snapshot_date) AS latest FROM synthetic_portfolio_components WHERE portfolio_id = %s AND snapshot_date <= %s
                AND base_currency = (SELECT COALESCE(reporting_currency,base_currency) FROM platform_subjects WHERE subject_id = %s)""",
            (subject_id, effective, subject_id),
        ).fetchone()["latest"]
        records = [] if snapshot is None else connection.execute(
            """
            SELECT component_type AS sector, SUM(ABS(contribution)) AS market_value
            FROM synthetic_portfolio_components
            WHERE portfolio_id = %s AND snapshot_date = %s
              AND base_currency = (SELECT COALESCE(reporting_currency,base_currency) FROM platform_subjects WHERE subject_id = %s)
            GROUP BY component_type ORDER BY market_value DESC
            """,
            (subject_id, snapshot, subject_id),
        ).fetchall()
        total = sum(float(row["market_value"] or 0) for row in records)
        allocation = [
            {
                "sector": str(row["sector"]).replace("_", " ").title(),
                "market_value": float(row["market_value"] or 0),
                "weight": (float(row["market_value"] or 0) / total if total else 0.0),
            }
            for row in records
        ]
        return AgentToolResult(
            "get_sector_allocation", subject_id, effective,
            "pass" if allocation else "fail",
            {
                "available": bool(allocation),
                "publication_allowed": True,
                "position_date": str(snapshot) if snapshot else None,
                "allocation": allocation,
                "allocation_detail": [
                    {
                        "region": "Multi-asset",
                        "sector": row["sector"],
                        "sub_sector": "Approved composite sleeve",
                        "market_value": row["market_value"],
                        "weight": row["weight"],
                        "holdings": 1,
                    }
                    for row in allocation
                ],
                "classifications": [],
                "gross_market_value": total,
                "classified_market_value": total,
                "classification_coverage_pct": 100.0 if total else 0.0,
                "holdings": len(allocation),
                "classified_holdings": len(allocation),
                "unclassified_symbols": [],
                "provider": "approved composite calculation contract",
                "classification_basis": "composite sleeve type",
            },
            ({"table": "synthetic_portfolio_components", "subject_id": subject_id, "snapshot_date": snapshot},),
            utc_now_iso(),
        )
    position_date = connection.execute(
        "SELECT MAX(snapshot_date) AS latest FROM subject_position_series WHERE subject_id = %s AND snapshot_date <= %s",
        (subject_id, effective),
    ).fetchone()["latest"]
    records = [] if position_date is None else connection.execute(
        """
        SELECT p.conid, i.symbol, p.market_value_text, p.currency, p.snapshot_date,
               i.listing_exchange, c.sector, c.industry, c.exchange,
               c.checked_at, c.quality_status, c.license_status{long_name}
        FROM subject_position_series p
        LEFT JOIN instruments i ON i.conid = p.conid
        LEFT JOIN instrument_classifications c
          ON c.conid = p.conid AND c.provider = %s
        WHERE p.subject_id = %s AND p.snapshot_date = %s
          AND COALESCE(NULLIF(p.quantity_text, ''), '0')::numeric <> 0
        ORDER BY ABS(COALESCE(NULLIF(p.market_value_text, ''), '0')::numeric) DESC
        """.format(long_name=", c.long_name" if has_long_name(connection) else ""),
        (YAHOO_PROVIDER, subject_id, position_date),
    ).fetchall()
    data = aggregate_sector_allocation([dict(row) for row in records])
    fx_citations = ()
    if records:
        from .db import PostgresConnection
        from .reporting_currency_holdings import normalize_collected_report_holdings
        currency_row = connection.execute(
            "SELECT COALESCE(reporting_currency, base_currency) AS reporting_currency "
            "FROM platform_subjects WHERE subject_id = %s", (subject_id,),
        ).fetchone()
        currency = str(currency_row["reporting_currency"] or "").upper() if currency_row else ""
        if len(currency) != 3 or not currency.isalpha():
            raise AgentToolError(409, "sector_reporting_currency_unavailable", "Sector allocation requires a validated reporting currency.")
        portable = PostgresConnection(connection) if type(connection).__module__.startswith("psycopg") else connection
        try:
            normalized = normalize_collected_report_holdings(portable, subject_id, currency, {
                "holdings": {"as_of": effective, "data": {"holdings": [dict(row) for row in records]}},
                "sector_allocation": {"data": data},
            })
        except ValueError as error:
            raise AgentToolError(409, "sector_reporting_fx_unavailable", "Sector allocation requires complete, valid dated reporting-currency valuations.") from error
        data = normalized["sector_allocation"]["data"]
        fx_citations = tuple(normalized["sector_allocation"]["citations"])
    data["position_date"] = str(position_date) if position_date else None
    reason = None
    if not records:
        reason = "No classified position rows are available for sector allocation."
    elif not data["available"]:
        reason = "One or more current holdings do not have a Yahoo Finance sector classification."
    elif not data["publication_allowed"]:
        reason = "Yahoo Finance sector classifications are approved for authorized internal and shareholder analysis, but remain prohibited for client distribution."
    if reason:
        data["reason"] = reason
    return AgentToolResult(
        "get_sector_allocation",
        subject_id,
        effective,
        "warn" if records else "fail",
        data,
        (
            {"table": "subject_position_series", "subject_id": subject_id, "snapshot_date": position_date},
            {"table": "instrument_classifications", "provider": YAHOO_PROVIDER},
        ) + fx_citations,
        utc_now_iso(),
    )


TOOL_DEFINITIONS: tuple[AgentToolDefinition, ...] = (
    AgentToolDefinition("get_portfolio_summary", "Return account metadata, NAV coverage, latest NAV and position count.", _subject_schema(), _summary),
    AgentToolDefinition("get_performance_series", "Return the validated daily NAV series through an as-of date.", _subject_schema(), _performance),
    AgentToolDefinition("get_holdings", "Return the largest current holdings on or before an as-of date.", _subject_schema(limit=True), _holdings),
    AgentToolDefinition("compare_with_benchmarks", "Return deterministic portfolio-versus-configured-benchmark metrics and rebased series.", _subject_schema(benchmark=True), _benchmark),
    AgentToolDefinition("get_risk_metrics", "Return deterministic risk metrics using validated portfolio returns and the as-of-correct risk-free rate.", _subject_schema(), _risk),
    AgentToolDefinition("get_data_freshness", "Return portfolio, benchmark, risk-free and ingestion freshness evidence.", _subject_schema(), _freshness),
    AgentToolDefinition("get_sector_allocation", "Return validated sector allocation or an explicit unavailable result without inference.", _subject_schema(), _sector_allocation),
)
TOOL_REGISTRY = {definition.name: definition for definition in TOOL_DEFINITIONS}


def tool_catalog() -> list[dict[str, Any]]:
    return [
        {"name": definition.name, "description": definition.description, "input_schema": definition.input_schema}
        for definition in TOOL_DEFINITIONS
    ]


def _identity_digest(value: str) -> str | None:
    candidate = value.strip().lower()
    return hashlib.sha256(candidate.encode("utf-8")).hexdigest() if candidate else None


def _audit(context: AgentToolContext, tool_name: str, subject_id: str | None, request_hash: str, status: str, response_hash: str | None = None, error_code: str | None = None) -> None:
    event = {
        "event_type": "agent_tool_call",
        "timestamp": utc_now_iso(),
        "correlation_id": context.correlation_id,
        "actor_object_id_sha256": _identity_digest(context.actor_object_id),
        "tool_name": tool_name,
        "subject_id": subject_id,
        "request_sha256": request_hash,
        "response_sha256": response_hash,
        "approval_required": False,
        "status": status,
        "error_code": error_code,
    }
    TOOL_AUDIT_LOGGER.info("AGENT_TOOL_AUDIT %s", json.dumps(event, sort_keys=True, separators=(",", ":")))


def execute_tool(connection, context: AgentToolContext, tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    request_hash = hashlib.sha256(canonical_json(dict(arguments)).encode("utf-8")).hexdigest()
    definition = TOOL_REGISTRY.get(tool_name)
    subject_id = str(arguments.get("subject_id") or "").strip()
    if definition is None:
        _audit(context, tool_name, subject_id or None, request_hash, "denied", error_code="tool_not_allowed")
        raise AgentToolError(404, "tool_not_allowed", "This tool is not in the approved read-only registry.")
    allowed_keys = set(definition.input_schema["properties"])
    unexpected = set(arguments) - allowed_keys
    if unexpected:
        _audit(context, tool_name, subject_id or None, request_hash, "denied", error_code="unexpected_arguments")
        raise AgentToolError(400, "unexpected_arguments", "The request contains unsupported arguments.")
    if not subject_id:
        _audit(context, tool_name, None, request_hash, "denied", error_code="subject_required")
        raise AgentToolError(400, "subject_required", "subject_id is required.")
    if subject_id not in context.allowed_subject_ids:
        _audit(context, tool_name, subject_id, request_hash, "denied", error_code="subject_not_authorized")
        raise AgentToolError(403, "subject_not_authorized", "The caller is not authorized for this account or portfolio.")
    as_of = arguments.get("as_of")
    try:
        if as_of is not None:
            date.fromisoformat(str(as_of))
        executor_arguments=arguments
        if subject_id=='alpha-world-t':
            from .alpha_world_t_analytics import require_contract
            from .db import PostgresConnection
            portable=PostgresConnection(connection) if type(connection).__module__.startswith('psycopg') else connection
            try:require_contract(portable,tenant_id=context.tenant_id)
            except ValueError as error:
                raise AgentToolError(409,'t_hkd_contract_not_ready',str(error)) from error
            executor_arguments={**arguments,'_verified_tenant_id':context.tenant_id}
        if subject_id == 'alpha-world-ms':
            try:
                require_ms_hkd_contract(connection,tenant_id=context.tenant_id)
            except ValueError as error:
                raise AgentToolError(409,'ms_hkd_contract_not_ready',str(error)) from error
            executor_arguments={**arguments,'_verified_tenant_id':context.tenant_id}
        result = definition.executor(connection, subject_id, str(as_of) if as_of else None, executor_arguments)
    except AgentToolError as error:
        _audit(context, tool_name, subject_id, request_hash, "denied" if error.status_code in (401, 403) else "failed", error_code=error.code)
        raise
    except (TypeError, ValueError) as error:
        _audit(context, tool_name, subject_id, request_hash, "failed", error_code="invalid_arguments")
        raise AgentToolError(400, "invalid_arguments", "The request contains invalid argument values.") from error
    payload = result.as_dict()
    response_hash = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    _audit(context, tool_name, subject_id, request_hash, "success", response_hash=response_hash)
    return {"correlation_id": context.correlation_id, **payload}
