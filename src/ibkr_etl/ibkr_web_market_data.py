"""Daily bars from the IBKR Web API (OAuth), the second market-data source beside Yahoo.

Benchmarks are named the same way as the TWS live collector's LIVE_BENCHMARKS:
SECTYPE:SYMBOL:EXCHANGE:CURRENCY, for example IND:SPX:CBOE:USD or STK:2800:SEHK:HKD.
Each is resolved to an IBKR contract id with /iserver/secdef/search (or taken from a pinned
conid, SECTYPE:SYMBOL:EXCHANGE:CURRENCY@CONID), then /iserver/marketdata/history gives
daily bars.

Bronze: every history answer is handed to raw_capture as received, in the feed
"ibkr-web-history", before it is parsed (the same rule as the Yahoo feeds). Capture is off
unless the job sets MARKET_DATA_RAW_CONTAINER and AZURE_STORAGE_ACCOUNT_URL.

The history answer's "data" rows carry real prices; its priceFactor only scales the summary
strings (high/low), so it is not applied here.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from . import raw_capture
from .ibkr_web_oauth import IbkrWebClient, OAuthError

RAW_FEED = "ibkr-web-history"
PROVIDER = "IBKR Web API (OAuth 1.0a)"
HISTORY_PATH = "/iserver/marketdata/history"
SEARCH_PATH = "/iserver/secdef/search"
DEFAULT_BENCHMARKS = (
    "IND:SPX:CBOE:USD",
    "IND:COMP:NASDAQ:USD",
    "IND:HSI:HKFE:HKD",
    "IND:K200:KSE:KRW",
    "STK:SPY:ARCA:USD",
    "STK:2800:SEHK:HKD",
)
EXCHANGE_ZONES = {
    "CBOE": "America/New_York", "NASDAQ": "America/New_York", "NYSE": "America/New_York",
    "ARCA": "America/New_York", "SMART": "America/New_York", "CME": "America/Chicago",
    "HKFE": "Asia/Hong_Kong", "SEHK": "Asia/Hong_Kong", "KSE": "Asia/Seoul",
    "TSEJ": "Asia/Tokyo", "OSE.JPN": "Asia/Tokyo", "SGX": "Asia/Singapore",
    "IDEALPRO": "UTC",
}
_SPEC = re.compile(r"^(IND|STK|CASH):([A-Z0-9.&-]{1,12}):([A-Z0-9.]{1,12}):([A-Z]{3})(?:@([0-9]{1,12}))?$")
EMPTY_RETRIES = 2  # IBKR's first history call in a session sometimes answers with no bars


@dataclass(frozen=True)
class Benchmark:
    sec_type: str
    symbol: str
    exchange: str
    currency: str
    conid: str = ""

    @property
    def label(self) -> str:
        return f"{self.sec_type}:{self.symbol}:{self.exchange}:{self.currency}"

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(EXCHANGE_ZONES.get(self.exchange, "UTC"))


@dataclass(frozen=True)
class DailyBar:
    benchmark: str
    conid: str
    bar_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: Decimal | None
    raw_blob: str | None


def parse_benchmark(text: str) -> Benchmark:
    match = _SPEC.match(text.strip().upper())
    if not match:
        raise ValueError(f"benchmarks look like IND:SPX:CBOE:USD or IND:SPX:CBOE:USD@416904, not {text!r}")
    sec_type, symbol, exchange, currency, conid = match.groups()
    return Benchmark(sec_type, symbol, exchange, currency, conid or "")


def parse_benchmarks(text: str | Iterable[str]) -> list[Benchmark]:
    items = text.split(",") if isinstance(text, str) else list(text)
    return [parse_benchmark(item) for item in items if str(item).strip()]


def pick_conid(benchmark: Benchmark, results: Any) -> str | None:
    """The conid among /iserver/secdef/search results whose exchange matches the benchmark."""
    if not isinstance(results, list):
        return None
    wanted = benchmark.exchange
    fallback = None
    for row in results:
        if not isinstance(row, Mapping) or not row.get("conid"):
            continue
        if str(row.get("symbol", "")).upper() not in ("", benchmark.symbol):
            continue
        exchanges = {str(row.get("description") or "").upper()}
        for section in row.get("sections") or []:
            if isinstance(section, Mapping) and section.get("secType") == benchmark.sec_type:
                exchanges.update(part.strip().upper() for part in str(section.get("exchange") or "").split(";"))
                fallback = fallback or str(row["conid"])
        if wanted in exchanges:
            return str(row["conid"])
    # No exact exchange match: only a single same-type candidate is safe to take.
    return fallback if fallback and sum(
        1 for row in results if isinstance(row, Mapping) and any(
            isinstance(s, Mapping) and s.get("secType") == benchmark.sec_type for s in row.get("sections") or [])
    ) == 1 else None


def _number(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def parse_history(benchmark: Benchmark, conid: str, payload: Mapping[str, Any],
                  raw_blob: str | None = None) -> list[DailyBar]:
    """Daily bars from a history answer, dated in the exchange's time zone. Rows with no
    close are dropped: a missing close is a gap, never a zero."""
    bars: dict[date, DailyBar] = {}
    for row in payload.get("data") or []:
        if not isinstance(row, Mapping):
            continue
        close = _number(row.get("c"))
        stamp = row.get("t")
        if close is None or stamp in (None, ""):
            continue
        day = datetime.fromtimestamp(int(stamp) / 1000, tz=timezone.utc).astimezone(benchmark.zone).date()
        bars[day] = DailyBar(benchmark.label, conid, day, _number(row.get("o")), _number(row.get("h")),
                             _number(row.get("l")), close, _number(row.get("v")), raw_blob)
    return [bars[day] for day in sorted(bars)]


class WebMarketData:
    """Resolves benchmarks and reads their daily bars through one signed client."""

    def __init__(self, client: IbkrWebClient, *, sleep: Callable[[float], None] = time.sleep,
                 capture: Callable[..., str | None] = raw_capture.capture_http) -> None:
        self.client = client
        self._sleep = sleep
        self._capture = capture

    def search(self, symbol: str, sec_type: str | None = None, *, by_name: bool = False) -> Any:
        params: dict[str, Any] = {"symbol": symbol}
        if sec_type:
            params["secType"] = sec_type
        if by_name:
            params["name"] = "true"
        return self.client.get_json(SEARCH_PATH, params)

    def resolve(self, benchmark: Benchmark) -> str:
        if benchmark.conid:
            return benchmark.conid
        conid = pick_conid(benchmark, self.search(benchmark.symbol, benchmark.sec_type))
        if not conid:
            raise OAuthError(f"IBKR has no {benchmark.label} contract for this login")
        return conid

    def daily_bars(self, benchmark: Benchmark, *, period: str = "1m", conid: str | None = None) -> list[DailyBar]:
        conid = conid or self.resolve(benchmark)
        params = {"conid": conid, "period": period, "bar": "1d", "outsideRth": "false"}
        payload: Mapping[str, Any] = {}
        blob = None
        for attempt in range(EMPTY_RETRIES + 1):
            requested_at = raw_capture.utc_now()
            answer = self.client.request("GET", HISTORY_PATH, params=params)
            blob = self._capture(
                RAW_FEED, PROVIDER, url=self.client.base_url + HISTORY_PATH + "?" + "&".join(f"{k}={v}" for k, v in params.items()),
                body=answer.body, key=raw_capture.request_key(benchmark.symbol, benchmark.exchange, conid, period, "1d"),
                status=answer.status, content_type="application/json", requested_at=requested_at)
            if answer.status != 200:
                raise OAuthError(f"history for {benchmark.label} refused: HTTP {answer.status}")
            payload = answer.json() or {}
            if payload.get("data"):
                break
            self._sleep(2.0 * (attempt + 1))
        return parse_history(benchmark, conid, payload, blob)

    def fetch(self, benchmarks: Sequence[Benchmark], *, period: str = "1m") -> dict[str, list[DailyBar] | str]:
        """Bars per benchmark label; a benchmark that fails maps to its error text instead."""
        out: dict[str, list[DailyBar] | str] = {}
        for benchmark in benchmarks:
            try:
                out[benchmark.label] = self.daily_bars(benchmark, period=period)
            except OAuthError as error:
                out[benchmark.label] = str(error)
        return out
