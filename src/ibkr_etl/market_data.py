from __future__ import annotations

import json
import math
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import raw_capture
from .db import is_postgres, transaction


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = "IBKR-Portfolio-Analytics/2.1"


@dataclass(frozen=True)
class MarketInstrument:
    instrument_id: str
    symbol: str
    name: str
    currency: str
    price_type: str


ALPHA_WORLD_MARKET_INSTRUMENTS = (
    MarketInstrument(
        instrument_id="hang-seng-price-index",
        symbol="^HSI",
        name="Hang Seng Index",
        currency="HKD",
        price_type="price_index_close",
    ),
    MarketInstrument(
        instrument_id="spy-settlement",
        symbol="SPY",
        name="SPDR S&P 500 ETF Trust",
        currency="USD",
        price_type="unadjusted_close",
    ),
)


def _ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def _epoch(day: date) -> int:
    return int(
        datetime.combine(day, datetime_time.min, tzinfo=timezone.utc).timestamp()
    )


def _chart_url(symbol: str, start_date: date, end_date: date) -> str:
    query = urllib.parse.urlencode(
        {
            "period1": _epoch(start_date),
            "period2": _epoch(end_date + timedelta(days=1)),
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "false",
        }
    )
    return f"{YAHOO_CHART_URL.format(symbol=urllib.parse.quote(symbol, safe=''))}?{query}"


def _download_json(
    url: str, timeout: int = 30, attempts: int = 3, *, capture_key: str | None = None
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            requested_at = raw_capture.utc_now()
            with urllib.request.urlopen(
                request, timeout=timeout, context=_ssl_context()
            ) as response:
                body = response.read()
                status = getattr(response, "status", None)
            if capture_key:
                raw_capture.capture_http("yahoo-chart", "Yahoo Finance chart API", url=url,
                                         body=body, key=capture_key, status=status,
                                         extension="json", requested_at=requested_at)
            return json.loads(body.decode("utf-8"))
        except Exception as error:  # network and malformed-response failures retry alike
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"Market data request failed after {attempts} attempts: {url}") from last_error


def fetch_daily_closes(
    instrument: MarketInstrument,
    start_date: date,
    end_date: date,
    *,
    timeout: int = 30,
) -> tuple[str, list[tuple[str, float]]]:
    """Return unadjusted daily closes using each exchange's local trading date."""

    url = _chart_url(instrument.symbol, start_date, end_date)
    payload = _download_json(
        url,
        timeout=timeout,
        capture_key=raw_capture.request_key(
            instrument.symbol, start_date.isoformat(), end_date.isoformat(), "1d"
        ),
    )
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(
            f"Market data provider rejected {instrument.symbol}: {chart['error']}"
        )
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"No market data returned for {instrument.symbol}")
    result = results[0]
    meta = result.get("meta") or {}
    returned_currency = str(meta.get("currency") or "").upper()
    if returned_currency and returned_currency != instrument.currency:
        raise RuntimeError(
            f"Unexpected currency for {instrument.symbol}: "
            f"{returned_currency}, expected {instrument.currency}"
        )
    timestamps = result.get("timestamp") or []
    quote_blocks = (result.get("indicators") or {}).get("quote") or []
    closes = quote_blocks[0].get("close") if quote_blocks else []
    if len(timestamps) != len(closes or []):
        raise RuntimeError(f"Incomplete market data response for {instrument.symbol}")
    timezone_name = str(meta.get("exchangeTimezoneName") or "UTC")
    try:
        exchange_timezone = ZoneInfo(timezone_name)
    except Exception:
        exchange_timezone = timezone.utc
    rows: dict[str, float] = {}
    for timestamp, raw_close in zip(timestamps, closes):
        if raw_close is None:
            continue
        close = float(raw_close)
        if not math.isfinite(close) or close <= 0:
            continue
        price_date = datetime.fromtimestamp(
            int(timestamp), timezone.utc
        ).astimezone(exchange_timezone).date()
        if start_date <= price_date <= end_date:
            rows[price_date.isoformat()] = close
    if not rows:
        raise RuntimeError(f"No valid daily closes returned for {instrument.symbol}")
    return url, sorted(rows.items())


def refresh_alpha_world_market_data(
    connection: Any,
    *,
    start_date: date = date(2025, 1, 13),
    end_date: date | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """Fetch and upsert the HSI price index and SPY unadjusted close history."""

    end_date = end_date or datetime.now(timezone.utc).date()
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    conflict_target = (
        "tenant_id, instrument_id, price_date, price_type"
        if is_postgres(connection)
        else "instrument_id, price_date, price_type"
    )
    summary: dict[str, Any] = {}
    for instrument in ALPHA_WORLD_MARKET_INSTRUMENTS:
        source_url, rows = fetch_daily_closes(instrument, start_date, end_date)
        with transaction(connection):
            for price_date, price in rows:
                connection.execute(
                    f"""
                    INSERT INTO market_data_prices(
                        instrument_id, price_date, symbol, instrument_name,
                        price_type, currency, price, source, source_url, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT({conflict_target}) DO UPDATE SET
                        symbol = excluded.symbol,
                        instrument_name = excluded.instrument_name,
                        currency = excluded.currency,
                        price = excluded.price,
                        source = excluded.source,
                        source_url = excluded.source_url,
                        fetched_at = excluded.fetched_at
                    """,
                    (
                        instrument.instrument_id,
                        price_date,
                        instrument.symbol,
                        instrument.name,
                        instrument.price_type,
                        instrument.currency,
                        price,
                        "Yahoo Finance chart API",
                        source_url,
                        fetched_at,
                    ),
                )
        summary[instrument.instrument_id] = {
            "symbol": instrument.symbol,
            "price_type": instrument.price_type,
            "currency": instrument.currency,
            "rows_received": len(rows),
            "first_date": rows[0][0],
            "latest_date": rows[-1][0],
        }
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "fetched_at": fetched_at,
        "instruments": summary,
    }
