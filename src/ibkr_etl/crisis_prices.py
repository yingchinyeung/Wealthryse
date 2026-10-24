"""Crisis-window prices in silver: the fetch that fills it, and the reader the crisis replays compute from.

CRISIS_PRICES_SOURCE on a job decides where the crisis replay (2008, run by benchmark-daily, derived-daily and the
onboarding jobs) and the crisis scenarios (crisis-scenarios-daily) get their Yahoo Finance closes and FX quotes:

- ``off``, the default and also when unset: fetched from Yahoo while they compute, as before. Nothing new is stored.
- ``silver``: two steps. The fetch runs the calculation once against Yahoo only to learn what it asks for: each reply
  is kept in bronze by raw capture, as before, and its parsed closes are written to silver.market_data_prices and
  committed. That run's results are thrown away. The calculation then runs again reading silver only, and only those
  results are stored. A request that today's fetch did not answer, with nothing in silver for it, stops that subject
  (NotInSilver) rather than being read as "this ticker has no prices".

Silver rows: instrument_id ``yahoo-<ticker in lower case>`` and symbol the ticker; price_type ``adjusted_close`` for
securities and indices (Yahoo's Adj Close, as the replays use) and ``unadjusted_close`` for FX quotes (HKD=X and the
other provisional_benchmark.FX_TICKERS: units of that currency per USD). A reply with closes replaces that ticker's
rows on the dates it asked for; an empty or failed reply changes nothing, so silver keeps the last good answer.

Each step prints one ``CRISIS_PRICES {json}`` line of counts: no price, no ticker and no account.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping

from .provisional_benchmark import fetch_benchmark_yfinance, fetch_fx_to_reporting_yfinance
from .util import utc_now_iso

SOURCE_ENV = "CRISIS_PRICES_SOURCE"
MODES = ("off", "silver")
ADJUSTED = "adjusted_close"
CLOSE = "unadjusted_close"
PROVIDER = "Yahoo Finance via yfinance"
LOG_PREFIX = "CRISIS_PRICES "

Key = tuple[str, str, str, str]  # price_type, ticker, first date, last date


def mode(environ: Mapping[str, str] | None = None) -> str:
    """off or silver; anything else stops the job rather than guessing."""

    raw = (environ if environ is not None else os.environ).get(SOURCE_ENV) or ""
    value = raw.strip().lower() or "off"
    if value not in MODES:
        raise RuntimeError(f"{SOURCE_ENV} must be off or silver")
    return value


def instrument_id(symbol: str) -> str:
    return "yahoo-" + symbol.strip().lower()


def _key(price_type: str, symbol: str, start: date, end: date) -> Key:
    return (price_type, symbol, start.isoformat(), end.isoformat())


def _execute(connection: Any, sql: str, parameters: tuple[Any, ...]):
    if type(connection).__module__.startswith("psycopg"):
        sql = sql.replace("?", "%s")
    return connection.execute(sql, parameters)


class NotInSilver(Exception):
    """Silver has nothing for a request that today's fetch did not answer either.

    Deliberately not a RuntimeError, ValueError or KeyError: the replays read those as "no prices for this
    ticker" and fall back to a stand-in, and a missing fetch must never look like that."""


@dataclass(frozen=True)
class Answer:
    price_type: str
    symbol: str
    currency: str
    name: str
    start: date
    end: date
    rows: dict[str, float]


class Yahoo:
    """Asks Yahoo Finance; raw capture keeps each reply in bronze. With keep, every answer is also kept for silver."""

    def __init__(self, ticker_factory: Callable[[str], Any] | None = None, *, keep: bool = False):
        self.ticker_factory = ticker_factory
        self.keep = keep
        self.answers: list[Answer] = []
        self.answered: dict[Key, dict[str, float]] = {}
        self.raised: set[Key] = set()

    def _record(self, key: Key, answer: Answer) -> None:
        if self.keep:
            self.answered[key] = answer.rows
            self.raised.discard(key)
            self.answers.append(answer)

    def adjusted(self, symbol: str, start: date, end: date, currency: str = "") -> dict[str, float]:
        from . import crisis_replay  # crisis_replay imports this module

        key = _key(ADJUSTED, symbol, start, end)
        try:
            rows = crisis_replay.fetch_adjusted_history(symbol, self.ticker_factory, start_date=start,
                                                        end_date=end, capture_feed=crisis_replay.RAW_FEED)
        except Exception:
            if self.keep:
                self.raised.add(key)
            raise
        self._record(key, Answer(ADJUSTED, symbol, currency, crisis_replay.PROXY_NAMES.get(symbol, symbol),
                                 start, end, dict(rows)))
        return rows

    def quote(self, spec: Any, start_date: date, end_date: date, ticker_factory: Any = None,
              captured: Any = None) -> list[tuple[str, float]]:
        """fetch_benchmark_yfinance for one FX quote (the fetch argument of fetch_fx_to_reporting_yfinance)."""

        key = _key(CLOSE, spec.symbol, start_date, end_date)
        try:
            rows = fetch_benchmark_yfinance(spec, start_date, end_date, self.ticker_factory)
        except RuntimeError:
            # Yahoo answered, with no usable close: the same answer the reader gives back.
            self._record(key, Answer(CLOSE, spec.symbol, spec.currency, spec.name, start_date, end_date, {}))
            raise
        except Exception:
            if self.keep:
                self.raised.add(key)
            raise
        self._record(key, Answer(CLOSE, spec.symbol, spec.currency, spec.name, start_date, end_date, dict(rows)))
        return rows

    def fx(self, native: str, reporting: str, start: date, end: date) -> tuple[list[tuple[str, float]], str]:
        return fetch_fx_to_reporting_yfinance(native, reporting, start, end, self.ticker_factory, fetch=self.quote)


class Silver:
    """Reads the closes the fetch stored, and never calls Yahoo.

    fetched is today's fetch: a request it answered may come back empty (Yahoo has no such prices); any other
    request with nothing in silver raises NotInSilver. raised_is_empty also reads a request whose fetch failed as
    empty, for a caller that has always read a failed fetch as "no prices" (the crisis scenarios)."""

    def __init__(self, connection: Any, fetched: Yahoo | None = None, *, raised_is_empty: bool = False):
        self.connection = connection
        self.fetched = fetched if fetched is not None else Yahoo(keep=True)
        self.raised_is_empty = raised_is_empty
        self.counts: Counter[str] = Counter()

    def _read(self, price_type: str, symbol: str, start: date, end: date) -> dict[str, float]:
        rows = _execute(self.connection, """
            SELECT price_date, price FROM market_data_prices
            WHERE instrument_id = ? AND price_type = ? AND price_date >= ? AND price_date <= ?
            ORDER BY price_date""", (instrument_id(symbol), price_type, start.isoformat(), end.isoformat())).fetchall()
        return {str(r["price_date"]): float(r["price"]) for r in rows}

    def _answer(self, price_type: str, symbol: str, start: date, end: date) -> dict[str, float]:
        key = _key(price_type, symbol, start, end)
        rows = self._read(price_type, symbol, start, end)
        self.counts["reads"] += 1
        if key in self.fetched.answered:
            self.counts["same_as_fetch" if rows == self.fetched.answered[key] else "differs_from_fetch"] += 1
        elif rows:
            self.counts["from_an_earlier_fetch"] += 1
        if rows:
            return rows
        if key in self.fetched.answered or (self.raised_is_empty and key in self.fetched.raised):
            self.counts["empty"] += 1
            return {}
        self.counts["not_in_silver"] += 1
        raise NotInSilver(f"silver.market_data_prices has no {price_type} for {symbol} from {start} to {end}, "
                          "and today's fetch did not answer for it")

    def adjusted(self, symbol: str, start: date, end: date, currency: str = "") -> dict[str, float]:
        return self._answer(ADJUSTED, symbol, start, end)

    def quote(self, spec: Any, start_date: date, end_date: date, ticker_factory: Any = None,
              captured: Any = None) -> list[tuple[str, float]]:
        rows = self._answer(CLOSE, spec.symbol, start_date, end_date)
        if not rows:  # as fetch_benchmark_yfinance on a reply with no close
            raise RuntimeError(f"yfinance returned no {spec.name} observations in the requested period")
        return sorted(rows.items())

    def fx(self, native: str, reporting: str, start: date, end: date) -> tuple[list[tuple[str, float]], str]:
        return fetch_fx_to_reporting_yfinance(native, reporting, start, end, None, fetch=self.quote)


def write(connection: Any, fetched: Yahoo, *, fetched_at: str | None = None) -> dict[str, int]:
    """Today's answers into silver.market_data_prices. The caller commits.

    A reply with closes replaces that ticker's rows on the dates it asked for: new dates are inserted, changed
    closes updated, dates it no longer has deleted. A reply with none changes nothing."""

    stamp = fetched_at or utc_now_iso()
    counts: Counter[str] = Counter()
    for answer in fetched.answers:
        if not answer.rows:
            counts["empty_answers"] += 1
            continue
        counts["answers"] += 1
        iid = instrument_id(answer.symbol)
        existing = {str(r["price_date"]): float(r["price"]) for r in _execute(connection, """
            SELECT price_date, price FROM market_data_prices
            WHERE instrument_id = ? AND price_type = ? AND price_date >= ? AND price_date <= ?""",
            (iid, answer.price_type, answer.start.isoformat(), answer.end.isoformat())).fetchall()}
        for day in sorted(set(existing) - set(answer.rows)):
            _execute(connection, "DELETE FROM market_data_prices WHERE instrument_id = ? AND price_type = ? "
                     "AND price_date = ?", (iid, answer.price_type, day))
            counts["deleted"] += 1
        column = "Adj Close" if answer.price_type == ADJUSTED else "Close"
        source_url = f"yfinance:TICKER({answer.symbol}):history(auto_adjust=False):{column}"
        for day, price in sorted(answer.rows.items()):
            if day not in existing:
                _execute(connection, """
                    INSERT INTO market_data_prices(instrument_id, price_date, symbol, instrument_name, price_type,
                        currency, price, source, source_url, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (iid, day, answer.symbol, answer.name, answer.price_type, answer.currency, float(price),
                          PROVIDER, source_url, stamp))
                counts["inserted"] += 1
            elif existing[day] != float(price):
                _execute(connection, """
                    UPDATE market_data_prices SET price = ?, symbol = ?, currency = ?, source = ?, source_url = ?,
                        fetched_at = ?
                    WHERE instrument_id = ? AND price_type = ? AND price_date = ?""",
                         (float(price), answer.symbol, answer.currency, PROVIDER, source_url, stamp,
                          iid, answer.price_type, day))
                counts["updated"] += 1
            else:
                counts["unchanged"] += 1
    return dict(counts)


def fetch_into_silver(connection: Any, fetched: Yahoo, walk: Callable[[], Any], *, writer: str) -> dict[str, Any]:
    """The fetch step: walk() asks fetched for everything the calculation needs, then the answers go to silver.

    A failure inside walk() is counted, not raised: the calculation then reads what silver holds. A failure to
    write silver is raised, so the job shows it."""

    failure = None
    try:
        walk()
    except NotInSilver:
        raise
    except Exception as error:  # noqa: BLE001 - counted; the silver step decides what it means
        failure = type(error).__name__
    try:
        written = write(connection, fetched)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {"writer": writer, "requests": len(fetched.answered) + len(fetched.raised),
            "failed_requests": len(fetched.raised), "fetch_failure": failure, "written": written}


def log(step: Mapping[str, Any], silver: Silver | None) -> dict[str, Any]:
    """One CRISIS_PRICES line of counts for a fetch and the reads that followed it."""

    line = {"source": "silver", **step, "reads": dict(silver.counts) if silver is not None else {}}
    print(LOG_PREFIX + json.dumps(line, sort_keys=True, separators=(",", ":")), flush=True)
    return line
