from __future__ import annotations

import json
import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from bisect import bisect_right
from datetime import date, datetime, time, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from . import lineage, publish_gate, raw_capture
from .benchmark_analytics import (
    BenchmarkContract,
    build_comparison,
)
from .report import FxConverter, _convert, _flex_date, _number
from .util import canonical_json, sha256_text, utc_now_iso
from .website_benchmarks import require_ms_website_benchmarks, version_website_comparison


PROVIDER = "Yahoo Finance via yfinance"
OBSERVATION_STATUS = "provisional_close"
CALCULATION_VERSION = "benchmark-comparison-v1"
US_EASTERN = ZoneInfo("America/New_York")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")


@dataclass(frozen=True)
class BenchmarkSpec:
    benchmark_id: str
    symbol: str
    name: str
    mapping_type: str
    currency: str = "USD"
    reporting_currency: str = "USD"
    return_convention: str = "price"


BENCHMARKS = (
    BenchmarkSpec("GSPC", "^GSPC", "S&P 500 Price Index", "primary"),
    BenchmarkSpec("IXIC", "^IXIC", "NASDAQ Composite Price Index", "secondary"),
)

FX_TICKERS = {
    "CNY": "CNY=X",  # quoted as CNY per USD
    "HKD": "HKD=X",  # quoted as HKD per USD
    "KRW": "KRW=X",  # quoted as KRW per USD
    "TWD": "TWD=X",  # quoted as TWD per USD
}
# Reporting currencies a USD benchmark may be converted into with the direct Yahoo quote.
DIRECT_FROM_USD = {"HKD", "KRW"}


class NoBenchmarkContract(RuntimeError):
    """The subject has no active benchmark mapping, so there is nothing to compare it with."""


def mapped_benchmarks(connection: Any, account_id: str) -> tuple[BenchmarkSpec, ...]:
    """Load the approved account contract instead of assuming the pilot benchmark set."""

    rows = connection.execute(
        """
        SELECT b.benchmark_id, b.symbol, b.name, b.currency,
               b.return_convention, m.mapping_type, m.reporting_currency
        FROM portfolio_benchmark_mapping AS m
        JOIN benchmark_master AS b ON b.benchmark_id = m.benchmark_id
        WHERE m.subject_id = ? AND b.active = true
          AND m.effective_from <= CURRENT_DATE::text
          AND (m.effective_to IS NULL OR m.effective_to >= CURRENT_DATE::text)
        ORDER BY CASE m.mapping_type WHEN 'primary' THEN 0 ELSE 1 END,
                 b.benchmark_id
        """,
        (account_id,),
    ).fetchall()
    if not rows:
        raise NoBenchmarkContract(f"No active benchmark contract is configured for {account_id}")
    specs = tuple(
        BenchmarkSpec(
            benchmark_id=str(row["benchmark_id"]),
            symbol=str(row["symbol"]),
            name=str(row["name"]),
            mapping_type=str(row["mapping_type"]),
            currency=str(row["currency"]).upper(),
            reporting_currency=str(row["reporting_currency"]).upper(),
            return_convention=str(row["return_convention"]),
        )
        for row in rows
    )
    if sum(spec.mapping_type == "primary" for spec in specs) != 1:
        raise RuntimeError(f"{account_id} must have exactly one active primary benchmark")
    reporting_currencies = {spec.reporting_currency for spec in specs}
    if len(reporting_currencies) != 1:
        raise RuntimeError(f"{account_id} benchmark mappings disagree on reporting currency")
    return specs


def convert_levels_to_reporting_currency(
    observations: list[tuple[str, float]],
    fx_to_reporting: list[tuple[str, float]],
    *,
    maximum_fx_age_days: int = 4,
) -> tuple[list[tuple[str, float]], list[dict[str, Any]]]:
    """Convert with the same-day or most recent earlier FX rate, never a future rate."""

    if not fx_to_reporting:
        raise RuntimeError("No validated FX observations are available")
    # bisect requires strict chronological ordering. Never silently collapse
    # duplicate or malformed source rates into an apparently valid conversion.
    previous = None
    for fx_day, fx_rate in fx_to_reporting:
        parsed = date.fromisoformat(fx_day).isoformat()
        if parsed != fx_day or (previous is not None and fx_day <= previous):
            raise RuntimeError("FX observations must have unique ascending ISO dates")
        if not math.isfinite(float(fx_rate)) or float(fx_rate) <= 0:
            raise RuntimeError("FX observations must be finite and positive")
        previous = fx_day
    fx_dates = [day for day, _ in fx_to_reporting]
    fx_values = [float(value) for _, value in fx_to_reporting]
    converted: list[tuple[str, float]] = []
    audit: list[dict[str, Any]] = []
    for observation_date, level in observations:
        index = bisect_right(fx_dates, observation_date) - 1
        if index < 0:
            raise RuntimeError(
                f"No non-forward-looking FX observation is available for {observation_date}"
            )
        fx_date = fx_dates[index]
        age = (date.fromisoformat(observation_date) - date.fromisoformat(fx_date)).days
        if age < 0 or age > maximum_fx_age_days:
            raise RuntimeError(
                f"FX observation for {observation_date} is {age} calendar days old"
            )
        rate = fx_values[index]
        if not math.isfinite(rate) or rate <= 0:
            raise RuntimeError(f"Invalid FX rate selected for {observation_date}")
        converted_level = float(level) * rate
        if not math.isfinite(converted_level) or converted_level <= 0:
            raise RuntimeError(f"Invalid converted benchmark level for {observation_date}")
        converted.append((observation_date, converted_level))
        audit.append(
            {
                "observation_date": observation_date,
                "fx_date": fx_date,
                "fx_age_days": age,
                "fx_to_reporting": rate,
            }
        )
    return converted, audit


def last_completed_us_close(now: datetime | None = None) -> date:
    current = now.astimezone(US_EASTERN) if now else datetime.now(US_EASTERN)
    if current.timetz().replace(tzinfo=None) < time(18, 30):
        return current.date() - timedelta(days=1)
    return current.date()


def normalize_yfinance_history(frame: Any) -> list[tuple[str, float]]:
    rows: dict[str, float] = {}
    for index, values in frame.iterrows():
        raw_close = values.get("Close")
        if raw_close is None:
            continue
        close = float(raw_close)
        if not math.isfinite(close) or close <= 0:
            continue
        observed = (
            index.date()
            if hasattr(index, "date")
            else date.fromisoformat(str(index)[:10])
        )
        rows[observed.isoformat()] = close
    if not rows:
        raise RuntimeError("yfinance returned no valid benchmark closing observations")
    return sorted(rows.items())


def raw_prices_digest(captured: dict[str, Any] | None, received: list[tuple[str, float]]) -> str:
    """raw_sha256 for a benchmark's prices. With lineage recording on and the provider response
    kept in bronze, the sha256 of that raw file; otherwise the digest of the parsed closes, as before."""
    digest = (captured or {}).get("sha256") if lineage.enabled() else None
    return str(digest) if digest else sha256_text(canonical_json(received))


def fetch_benchmark_yfinance(
    spec: BenchmarkSpec,
    start_date: date,
    end_date: date,
    ticker_factory: Callable[[str], Any] | None = None,
    captured: dict[str, Any] | None = None,
) -> list[tuple[str, float]]:
    """Closes in the requested window. When given, captured receives the raw file's blob
    name and sha256 if raw capture stored the response."""
    if end_date < start_date:
        raise ValueError("Benchmark end date is before the start date")
    if ticker_factory is None:
        import yfinance as yf

        ticker_factory = yf.Ticker
    request = dict(
        start=start_date.isoformat(),
        end=(end_date + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=False,
        repair=False,
        actions=False,
        timeout=30,
    )
    requested_at = raw_capture.utc_now()
    frame = ticker_factory(spec.symbol).history(**request)
    blob = raw_capture.capture_frame(
        "yahoo-fx-history" if spec.mapping_type == "fx" else "yahoo-benchmark-history",
        PROVIDER,
        frame=frame,
        request={"symbol": spec.symbol, "method": "history", **request},
        key=raw_capture.request_key(spec.symbol, request["start"], request["end"], request["interval"]),
        requested_at=requested_at,
        history=raw_capture.settled(end_date, now=requested_at),
    )
    digest = raw_capture.sha256_of(blob)
    if captured is not None and blob and digest:
        captured.update(blob=blob, sha256=digest)
    observations = [
        row for row in normalize_yfinance_history(frame)
        if start_date.isoformat() <= row[0] <= end_date.isoformat()
    ]
    if not observations:
        raise RuntimeError(
            f"yfinance returned no {spec.name} observations in the requested period"
        )
    return observations


def fetch_fx_to_reporting_yfinance(
    from_currency: str,
    reporting_currency: str,
    start_date: date,
    end_date: date,
    ticker_factory: Callable[[str], Any] | None = None,
) -> tuple[list[tuple[str, float]], str]:
    """Fetch native-currency-to-reporting-currency closes from Yahoo Finance.

    Yahoo's ``TWD=X``/``CNY=X``/``HKD=X``/``KRW=X`` series are quoted as local
    currency units per USD. Use the reciprocal for local -> USD, but use the
    direct HKD=X or KRW=X quote for USD -> HKD or USD -> KRW. No additional
    cross pairs are approved.
    """

    native = from_currency.upper()
    reporting = reporting_currency.upper()
    if native == reporting:
        raise ValueError("FX retrieval is unnecessary for identical currencies")
    direct_quote = native == "USD" and reporting in DIRECT_FROM_USD
    if not direct_quote and (reporting != "USD" or native not in FX_TICKERS):
        raise RuntimeError(
            f"No approved provisional FX ticker maps {native} to {reporting}"
        )
    symbol = FX_TICKERS[reporting] if direct_quote else FX_TICKERS[native]
    quote_currency = reporting if direct_quote else native
    direct = fetch_benchmark_yfinance(
        BenchmarkSpec(
            benchmark_id=f"USD{quote_currency}",
            symbol=symbol,
            name=f"USD/{quote_currency} FX close",
            mapping_type="fx",
            currency=quote_currency,
            reporting_currency=reporting,
        ),
        start_date,
        end_date,
        ticker_factory,
    )
    converted = direct if direct_quote else [(day, 1.0 / value) for day, value in direct]
    if not all(math.isfinite(value) and value > 0 for _, value in converted):
        raise RuntimeError(f"Invalid FX observation for {native}/{reporting}")
    return converted, symbol


def _store_reference_fx_rates(
    connection: Any,
    *,
    from_currency: str,
    reporting_currency: str,
    rates: list[tuple[str, float]],
    fetched_at: str,
) -> None:
    native = from_currency.upper()
    reporting = reporting_currency.upper()
    for rate_date, native_to_reporting in rates:
        connection.execute(
            """
            INSERT INTO reference_fx_rates(
                rate_date, from_currency, to_currency, rate, provider,
                observation_status, fetched_at, quality_status
            ) VALUES (?, ?, ?, ?, ?, 'provisional_close', ?, 'warn')
            ON CONFLICT(tenant_id, rate_date, from_currency, to_currency, provider)
            DO UPDATE SET rate = excluded.rate,
                observation_status = excluded.observation_status,
                fetched_at = excluded.fetched_at,
                quality_status = excluded.quality_status
            """,
            (
                rate_date,
                native,
                reporting,
                native_to_reporting,
                PROVIDER,
                fetched_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO reference_fx_rates(
                rate_date, from_currency, to_currency, rate, provider,
                observation_status, fetched_at, quality_status
            ) VALUES (?, ?, ?, ?, ?, 'provisional_close', ?, 'warn')
            ON CONFLICT(tenant_id, rate_date, from_currency, to_currency, provider)
            DO UPDATE SET rate = excluded.rate,
                observation_status = excluded.observation_status,
                fetched_at = excluded.fetched_at,
                quality_status = excluded.quality_status
            """,
            (
                rate_date,
                reporting,
                native,
                1.0 / native_to_reporting,
                PROVIDER,
                fetched_at,
            ),
        )


# Accounts reporting in these currencies keep no FX of their own (a Kiwoom won account
# holds only won), so the benchmark run also stores their currency against USD for the
# USD views, such as Combined holdings. HKD subjects already have theirs.
REPORTING_USD_FX = {"KRW"}


def _store_reporting_usd_fx(connection: Any, reporting_currency: str, fx_by_currency: dict,
                            start_date: date, end_date: date, ticker_factory: Callable[[str], Any] | None,
                            fetched_at: str) -> dict[str, Any] | None:
    """Best effort: a missing quote is reported and never holds back the comparison."""

    if reporting_currency not in REPORTING_USD_FX or "USD" in fx_by_currency:
        return None
    try:
        rates, symbol = fetch_fx_to_reporting_yfinance(
            "USD", reporting_currency, start_date - timedelta(days=7), end_date, ticker_factory)
    except Exception as error:  # noqa: BLE001 - reported in the run result
        return {"status": "unavailable", "error": " ".join(str(error).split())[:300]}
    _store_reference_fx_rates(connection, from_currency="USD", reporting_currency=reporting_currency,
                              rates=rates, fetched_at=fetched_at)
    return {"status": "stored", "symbol": symbol, "observations": len(rates)}


def _account_contract(connection: Any, account_id: str) -> tuple[str, str, date]:
    row = connection.execute(
        """
        SELECT s.base_currency, s.reporting_currency, s.history_start_date
        FROM platform_subjects AS s
        WHERE s.subject_id = ?
        """,
        (account_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"No platform financial contract exists for {account_id}")
    base_currency = str(row["base_currency"]).upper()
    reporting_currency = str(row["reporting_currency"] or "").upper()
    if not reporting_currency:
        raise RuntimeError(f"No reporting currency is configured for {account_id}")
    if base_currency != reporting_currency:
        raise RuntimeError(
            f"{account_id} NAV is {base_currency}; portfolio conversion to "
            f"{reporting_currency} is not yet approved"
        )
    contract_start = date.fromisoformat(str(row["history_start_date"])[:10])
    return base_currency, reporting_currency, contract_start


def _portfolio_values(connection: Any, account_id: str, start_date: str,
                      currency: str) -> dict[str, float]:
    return {
        row["snapshot_date"]: float(row["total_text"])
        for row in connection.execute(
            """
            SELECT snapshot_date, total_text
            FROM subject_nav_series
            WHERE subject_id = ?
              AND currency = ?
              AND snapshot_date >= ? AND total_text IS NOT NULL AND total_text <> ''
            ORDER BY snapshot_date
            """,
            (account_id, currency, start_date),
        )
    }


def _external_flows(
    connection: Any, account_id: str, *, subject_type: str | None = None
) -> dict[str, float]:
    if subject_type is None:
        subject = connection.execute(
            "SELECT subject_type FROM platform_subjects WHERE subject_id = ?",
            (account_id,),
        ).fetchone()
        if subject is None:
            raise RuntimeError(f"Unknown subject: {account_id}")
        subject_type = str(subject["subject_type"])
    if subject_type in {"filtered_portfolio", "composite_portfolio"}:
        return {
            str(row["event_date"]): float(row["amount_base"])
            for row in connection.execute(
                """
                SELECT event_date, SUM(amount_base) AS amount_base
                FROM derived_portfolio_cash_ledger
                WHERE portfolio_id = ? AND included = 1 AND external_flow = 1
                  AND amount_base IS NOT NULL
                GROUP BY event_date ORDER BY event_date
                """,
                (account_id,),
            )
        }
    account = connection.execute(
        "SELECT base_currency FROM accounts WHERE account_id = ?", (account_id,)
    ).fetchone()
    if account is None:
        raise RuntimeError(f"Unknown account: {account_id}")
    bases = {account_id: account["base_currency"]}
    converter = FxConverter(
        connection.execute(
            """
            SELECT account_id, rate_date, from_currency, to_currency, rate_text
            FROM fx_rates WHERE account_id = ?
            """,
            (account_id,),
        ),
        bases,
    )
    flows: dict[str, float] = defaultdict(float)
    for row in connection.execute(
        "SELECT data_json FROM flex_rows WHERE account_id = ? AND section = 'Transfers'",
        (account_id,),
    ):
        values = json.loads(row["data_json"])
        flow_date = _flex_date(values.get("date") or values.get("reportDate"))
        if not flow_date:
            continue
        if str(values.get("assetCategory") or "").upper() == "CASH":
            rate = converter.rate(account_id, values.get("currency"), flow_date)
            amount = _convert(values.get("cashTransfer"), rate)
        else:
            amount = _number(values.get("positionAmountInBase"))
            if amount is None:
                rate = converter.rate(account_id, values.get("currency"), flow_date)
                amount = _convert(values.get("positionAmount"), rate)
        if amount is not None:
            flows[flow_date] += amount
    # Bank deposits and withdrawals are reported as CashTransactions, not
    # Transfers. Older Flex files carry no `type`, so fall back to IBKR's
    # description. Consolidated copies (accountId "-") would double count.
    for row in connection.execute(
        "SELECT data_json FROM flex_rows WHERE account_id = ? AND section = 'CashTransactions'",
        (account_id,),
    ):
        values = json.loads(row["data_json"])
        if str(values.get("accountId") or account_id) != account_id:
            continue
        if not _is_external_cash_transaction(values):
            continue
        flow_date = _flex_date(
            values.get("reportDate")
            or values.get("settleDate")
            or str(values.get("dateTime") or "")[:8]
        )
        if not flow_date:
            continue
        rate = converter.rate(account_id, values.get("currency"), flow_date)
        amount = _convert(values.get("amount"), rate)
        if amount is not None:
            flows[flow_date] += amount
    if str(bases[account_id] or "").upper() in KIWOOM_FLOW_CURRENCIES and connection.execute(
        "SELECT 1 FROM flex_rows WHERE account_id = ? LIMIT 1", (account_id,)
    ).fetchone() is None:
        for flow_date, amount in _kiwoom_flows(connection, account_id, converter).items():
            flows[flow_date] += amount
    return dict(flows)


# A Kiwoom account has no Flex file: its deposits and withdrawals are the platform's own cash rows
# (kiwoom_us_account.balance_gaps for a USD account). Korea's KRW account is left out until its own factsheet
# check takes them in, so its figures do not move here.
KIWOOM_FLOW_CURRENCIES = frozenset({"USD"})
KIWOOM_FLOW_TYPES = ("DEPOSIT", "WITHDRAWAL")


def _kiwoom_flows(connection: Any, account_id: str, converter: FxConverter) -> dict[str, float]:
    flows: dict[str, float] = defaultdict(float)
    for row in connection.execute(
        "SELECT effective_date, amount_text, currency FROM cash_transactions "
        "WHERE account_id = ? AND transaction_type IN (?, ?) ORDER BY effective_date",
        (account_id, *KIWOOM_FLOW_TYPES),
    ):
        flow_date = str(row["effective_date"] or "")[:10]
        if not flow_date:
            continue
        rate = converter.rate(account_id, row["currency"], flow_date)
        amount = _convert(row["amount_text"], rate)
        if amount is not None:
            flows[flow_date] += amount
    return dict(flows)


_EXTERNAL_CASH_DESCRIPTION = re.compile(
    r"\b(ELECTRONIC FUND TRANSFERS?|CASH RECEIPTS|DISBURSEMENTS?|WIRE (?:IN|OUT|TRANSFER)|"
    r"DEPOSITS?|WITHDRAWALS?)\b"
)


def _is_external_cash_transaction(values: dict[str, Any]) -> bool:
    kind = str(values.get("type") or "").upper()
    if kind:
        return "DEPOSITS/WITHDRAWALS" in kind or "DEPOSITS & WITHDRAWALS" in kind
    return bool(_EXTERNAL_CASH_DESCRIPTION.search(str(values.get("description") or "").upper()))


def refresh_and_materialize(
    connection: Any,
    *,
    account_id: str = "U16088571",
    start_date: date = date(2025, 1, 20),
    end_date: date | None = None,
    ticker_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    options = dict(account_id=account_id, start_date=start_date,
                   end_date=end_date, ticker_factory=ticker_factory)
    if account_id not in ('alpha-world-ms','alpha-world-t'):
        return _refresh_and_materialize(connection, **options)
    # Keep the caller's run record, but never commit a partial MS benchmark pair.
    savepoint = 'ms_website_benchmark_refresh' if account_id == 'alpha-world-ms' else 't_website_benchmark_refresh'
    connection.execute('SAVEPOINT '+savepoint)
    try:
        result = _refresh_and_materialize(connection, **options, commit=False)
    except Exception:
        connection.execute('ROLLBACK TO SAVEPOINT '+savepoint)
        connection.execute('RELEASE SAVEPOINT '+savepoint)
        raise
    connection.execute('RELEASE SAVEPOINT '+savepoint)
    connection.commit()
    return result


def _refresh_and_materialize(
    connection: Any,
    *,
    account_id: str,
    start_date: date,
    end_date: date | None,
    ticker_factory: Callable[[str], Any] | None,
    commit: bool = True,
) -> dict[str, Any]:
    end_date = end_date or last_completed_us_close()
    fetched_at = utc_now_iso()
    portfolio_currency, reporting_currency, contract_start = _account_contract(
        connection, account_id
    )
    if start_date < contract_start:
        start_date = contract_start
    specs = mapped_benchmarks(connection, account_id)
    if {spec.reporting_currency for spec in specs} != {reporting_currency}:
        raise RuntimeError(
            f"{account_id} benchmark mappings do not match its reporting currency"
        )
    if account_id == 'alpha-world-ms':
        from .subject_read_contract import require_ms_hkd_contract
        require_ms_hkd_contract(connection, tenant_id=os.environ.get(
            'PORTFOLIO_TENANT_ID', '00000000-0000-0000-0000-000000000001'))
        require_ms_website_benchmarks(specs)
    if account_id == 'alpha-world-t':
        from .alpha_world_t_analytics import require_contract
        from .website_benchmarks import require_t_website_benchmarks
        require_contract(connection,tenant_id=os.environ.get(
            'PORTFOLIO_TENANT_ID','00000000-0000-0000-0000-000000000001'))
        require_t_website_benchmarks(specs)
    portfolio_values = _portfolio_values(connection, account_id, start_date.isoformat(), portfolio_currency)
    if not portfolio_values:
        raise RuntimeError(f"No portfolio NAV observations are available for {account_id}")
    cash_flows = _external_flows(connection, account_id)
    received_by_id: dict[str, list[tuple[str, float]]] = {}
    unavailable_by_id: dict[str, str] = {}
    raw_files: dict[str, dict[str, Any]] = {}
    for spec in specs:
        try:
            received_by_id[spec.benchmark_id] = fetch_benchmark_yfinance(
                spec, start_date, end_date, ticker_factory,
                captured=raw_files.setdefault(spec.benchmark_id, {}),
            )
        except Exception as error:
            if spec.mapping_type == "primary" or account_id in ('alpha-world-ms','alpha-world-t'):
                raise RuntimeError(
                    f"Required benchmark {spec.benchmark_id} is unavailable: {error}"
                ) from error
            unavailable_by_id[spec.benchmark_id] = str(error)
    fx_by_currency: dict[str, list[tuple[str, float]]] = {}
    fx_symbols: dict[str, str] = {}
    for currency in sorted({spec.currency for spec in specs} - {reporting_currency}):
        rates, symbol = fetch_fx_to_reporting_yfinance(
            currency,
            reporting_currency,
            start_date - timedelta(days=7),
            end_date,
            ticker_factory,
        )
        fx_by_currency[currency] = rates
        fx_symbols[currency] = symbol
        _store_reference_fx_rates(
            connection,
            from_currency=currency,
            reporting_currency=reporting_currency,
            rates=rates,
            fetched_at=fetched_at,
        )
    usd_fx = _store_reporting_usd_fx(
        connection, reporting_currency, fx_by_currency, start_date, end_date, ticker_factory, fetched_at)
    summaries: list[dict[str, Any]] = []
    for spec in specs:
        if spec.benchmark_id in unavailable_by_id:
            message = (
                f"{spec.name} ({spec.symbol}) is unavailable from {PROVIDER}: "
                f"{unavailable_by_id[spec.benchmark_id]}"
            )
            connection.execute(
                """
                INSERT INTO data_quality_results(
                    run_type, run_reference, subject_id, check_id, status,
                    observed_value, expected_value, message, checked_at
                ) VALUES ('provisional-benchmark', ?, ?, ?, 'fail', NULL, NULL, ?, ?)
                """,
                (
                    f"{spec.benchmark_id}:{fetched_at}",
                    account_id,
                    "benchmark_history_available",
                    message,
                    fetched_at,
                ),
            )
            summaries.append(
                {
                    "benchmark_id": spec.benchmark_id,
                    "mapping_type": spec.mapping_type,
                    "name": spec.name,
                    "symbol": spec.symbol,
                    "native_currency": spec.currency,
                    "reporting_currency": reporting_currency,
                    "available": False,
                    "benchmark_observations": 0,
                    "comparison_rows": 0,
                    "quality_status": "fail",
                    "quality_issues": [
                        {
                            "check_id": "benchmark_history_available",
                            "status": "fail",
                            "message": message,
                        }
                    ],
                    "metrics": {},
                    "raw_sha256": None,
                    "fx_symbol": FX_TICKERS.get(spec.currency),
                    "fx_observations_used": 0,
                    "maximum_fx_age_days": None,
                }
            )
            continue
        received = received_by_id[spec.benchmark_id]
        source_reference = (
            f"yfinance:TICKER({spec.symbol}):"
            "history(interval=1d,auto_adjust=False):Close"
        )
        raw_digest = raw_prices_digest(raw_files.get(spec.benchmark_id), received)
        connection.execute(
            """
            UPDATE benchmark_master
            SET symbol = ?, name = ?, provider = ?, currency = ?, return_convention = ?,
                license_status = 'restricted', active = true, updated_at = ?
            WHERE benchmark_id = ?
            """,
            (
                spec.symbol,
                spec.name,
                PROVIDER,
                spec.currency,
                spec.return_convention,
                fetched_at,
                spec.benchmark_id,
            ),
        )
        for observation_date, level in received:
            connection.execute(
                """
                INSERT INTO benchmark_prices(
                    benchmark_id, observation_date, level, currency,
                    observation_status, provider, source_reference, observed_at,
                    fetched_at, quality_status, raw_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'warn', ?)
                ON CONFLICT(tenant_id, benchmark_id, observation_date, observation_status)
                DO UPDATE SET level = excluded.level, provider = excluded.provider,
                    source_reference = excluded.source_reference,
                    observed_at = excluded.observed_at,
                    fetched_at = excluded.fetched_at,
                    quality_status = excluded.quality_status,
                    raw_sha256 = excluded.raw_sha256
                """,
                (
                    spec.benchmark_id,
                    observation_date,
                    level,
                    spec.currency,
                    OBSERVATION_STATUS,
                    PROVIDER,
                    source_reference,
                    None,
                    fetched_at,
                    raw_digest,
                ),
            )
        fx_audit: list[dict[str, Any]] = []
        if spec.currency == reporting_currency:
            converted_levels = received
        else:
            converted_levels, fx_audit = convert_levels_to_reporting_currency(
                received, fx_by_currency[spec.currency]
            )
        benchmark_levels = dict(converted_levels)
        result = build_comparison(
            subject_id=account_id,
            reporting_currency=reporting_currency,
            portfolio_currency=portfolio_currency,
            portfolio_values=portfolio_values,
            cash_flows=cash_flows,
            benchmark=BenchmarkContract(
                benchmark_id=spec.benchmark_id,
                currency=reporting_currency,
                return_convention=spec.return_convention,
                observation_status=OBSERVATION_STATUS,
                license_status="restricted",
            ),
            benchmark_levels=benchmark_levels,
            start_date=start_date.isoformat(),
            expected_as_of=min(
                end_date, date.fromisoformat(max(portfolio_values))
            ).isoformat(),
            minimum_return_observations=30,
            development_mode=True,
            required_return_convention=spec.return_convention,
        )
        if result.quality_status == "fail":
            failed_checks = ", ".join(
                issue.check_id for issue in result.issues if issue.status == "fail"
            )
            raise RuntimeError(
                f"{spec.benchmark_id} comparison failed data-quality checks: "
                f"{failed_checks}"
            )
        # Never overwrite the retained v1 USD MS results with HKD values.
        result = version_website_comparison(result)
        for row in result.rows:
            connection.execute(
                """
                INSERT INTO performance_comparison_daily(
                    subject_id, benchmark_id, observation_date, reporting_currency,
                    portfolio_value, benchmark_level, normalized_portfolio,
                    normalized_benchmark, portfolio_return, benchmark_return,
                    excess_return, calculation_version, quality_status, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, subject_id, benchmark_id, observation_date, calculation_version)
                DO UPDATE SET portfolio_value = excluded.portfolio_value,
                    benchmark_level = excluded.benchmark_level,
                    normalized_portfolio = excluded.normalized_portfolio,
                    normalized_benchmark = excluded.normalized_benchmark,
                    portfolio_return = excluded.portfolio_return,
                    benchmark_return = excluded.benchmark_return,
                    excess_return = excluded.excess_return,
                    quality_status = excluded.quality_status,
                    generated_at = excluded.generated_at
                """,
                (
                    result.subject_id,
                    result.benchmark_id,
                    row.observation_date,
                    result.reporting_currency,
                    row.portfolio_value,
                    row.benchmark_level,
                    row.normalized_portfolio,
                    row.normalized_benchmark,
                    row.portfolio_return,
                    row.benchmark_return,
                    row.excess_return,
                    result.calculation_version,
                    result.quality_status,
                    fetched_at,
                ),
            )
        run_reference = f"{spec.benchmark_id}:{fetched_at}"
        for issue in result.issues:
            connection.execute(
                """
                INSERT INTO data_quality_results(
                    run_type, run_reference, subject_id, check_id, status,
                    observed_value, expected_value, message, checked_at
                ) VALUES ('provisional-benchmark', ?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    run_reference,
                    account_id,
                    issue.check_id,
                    issue.status,
                    issue.message,
                    fetched_at,
                ),
            )
        summaries.append(
            {
                "benchmark_id": spec.benchmark_id,
                "mapping_type": spec.mapping_type,
                "name": spec.name,
                "symbol": spec.symbol,
                "first_benchmark_date": received[0][0],
                "latest_benchmark_date": received[-1][0],
                "benchmark_observations": len(benchmark_levels),
                "comparison_rows": len(result.rows),
                "quality_status": result.quality_status,
                "quality_issues": [issue.__dict__ for issue in result.issues],
                "metrics": result.metrics,
                "raw_sha256": raw_digest,
                "available": True,
                "native_currency": spec.currency,
                "reporting_currency": reporting_currency,
                "fx_symbol": fx_symbols.get(spec.currency),
                "fx_observations_used": len({item["fx_date"] for item in fx_audit}),
                "maximum_fx_age_days": max(
                    (int(item["fx_age_days"]) for item in fx_audit), default=0
                ),
            }
        )
    # Off by default. Enforce rolls a failing version back and raises BuildHeldBack; without
    # commit, the caller's savepoint undoes it instead.
    publish_gate.before_commit(
        connection, {publish_gate.COMPARISON: {"subject_id": account_id, "generated_at": fetched_at}},
        writer="provisional_benchmark", rollback=commit)
    if commit:
        connection.commit()
    return {
        "status": "success",
        "provider": PROVIDER,
        "account_id": account_id,
        "reporting_currency": reporting_currency,
        "license_status": "restricted",
        "publication_allowed": False,
        "start_date": start_date.isoformat(),
        "requested_end_date": end_date.isoformat(),
        "benchmark_observations": sum(
            int(item["benchmark_observations"]) for item in summaries
        ),
        "comparison_rows": sum(int(item["comparison_rows"]) for item in summaries),
        "quality_status": (
            "warn"
            if any(item["quality_status"] != "fail" for item in summaries)
            else "fail"
        ),
        "benchmarks": summaries,
        **({"reporting_usd_fx": usd_fx} if usd_fx else {}),
    }
