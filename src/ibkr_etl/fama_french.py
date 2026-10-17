"""Daily Fama-French factor returns from Kenneth French's data library.

The library publishes free daily five-factor (Mkt-RF, SMB, HML, RMW, CMA, RF)
and momentum files as zipped CSVs, in percent. They are downloaded on demand,
parsed into decimal returns and kept in memory for a day, since the library is
updated about once a month. Nothing is stored in the database.

When the library cannot be reached, :func:`load_factor_data` builds stand-in
factors from US-listed ETFs on Yahoo Finance (which the platform already uses
for benchmarks and crisis replays): the market fund minus the 13-week Treasury
bill, and long-short spreads for size, value, quality, low volatility and
momentum. The result says which source was used.
"""
from __future__ import annotations

import io
import threading
import time
import urllib.request
import zipfile
from datetime import date, timedelta
from typing import Any, Callable, Mapping

BASE_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
REGIONS = {
    "us": ("United States", "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip", "F-F_Momentum_Factor_daily_CSV.zip"),
    "developed": ("Developed markets", "Developed_5_Factors_Daily_CSV.zip", "Developed_Mom_Factor_Daily_CSV.zip"),
    "asia_pacific_ex_japan": (
        "Asia Pacific ex Japan",
        "Asia_Pacific_ex_Japan_5_Factors_Daily_CSV.zip",
        "Asia_Pacific_ex_Japan_MOM_Factor_Daily_CSV.zip",
    ),
}
DEFAULT_REGION = "developed"
CACHE_SECONDS = 24 * 3600
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


class FactorDataError(RuntimeError):
    """The factor library could not be reached or read."""


def parse_french_csv(text: str, *, single_column: str | None = None) -> dict[str, dict[str, float]]:
    """Daily rows of a French data-library CSV as ``{ISO date: {factor: decimal}}``.

    The files start with free-text notes, then a header row whose first cell is
    empty, then rows keyed by an eight-digit date. Monthly or annual sections
    (six- or four-digit keys) and the trailing copyright line are skipped.
    ``single_column`` renames a one-factor file's column (the momentum files
    call it ``Mom`` or ``WML``).
    """

    header: list[str] | None = None
    rows: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.split(",")]
        if len(cells) < 2:
            continue
        if cells[0] == "" and any(cells[1:]):
            if header is None:
                header = cells[1:]
            continue
        key = cells[0]
        if header is None or len(key) != 8 or not key.isdigit():
            continue
        values: dict[str, float] = {}
        for name, raw in zip(header, cells[1:]):
            try:
                value = float(raw)
            except ValueError:
                continue
            if value <= -99.99:  # the library's missing-value marker
                continue
            values[single_column if single_column and len(header) == 1 else name] = value / 100.0
        if values:
            rows[f"{key[:4]}-{key[4:6]}-{key[6:]}"] = values
    if not rows:
        raise FactorDataError("The factor file has no daily rows.")
    return rows


def _download_csv(name: str, timeout: int) -> str:
    request = urllib.request.Request(BASE_URL + name, headers={"User-Agent": "Mozilla/5.0 (WealthRyse factor model)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            member = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
            return archive.read(member).decode("latin-1")
    except Exception as error:  # network, HTTP, zip or missing-member errors all mean "unavailable"
        raise FactorDataError(f"Could not download {name} from the Kenneth French data library: {error}") from error


def load_factors(region: str = DEFAULT_REGION, *, timeout: int = 30, now: float | None = None) -> dict[str, Any]:
    """Merged daily factors for a region; momentum is optional and noted when missing."""

    if region not in REGIONS:
        raise FactorDataError(f"Unknown factor region {region!r}.")
    moment = time.time() if now is None else now
    with _lock:
        cached = _cache.get(region)
        if cached and moment - cached[0] < CACHE_SECONDS:
            return cached[1]
    label, five_file, momentum_file = REGIONS[region]
    factors = parse_french_csv(_download_csv(five_file, timeout))
    momentum_note = ""
    try:
        momentum = parse_french_csv(_download_csv(momentum_file, timeout), single_column="MOM")
        for day, row in factors.items():
            if day in momentum and "MOM" in momentum[day]:
                row["MOM"] = momentum[day]["MOM"]
    except FactorDataError as error:
        momentum_note = f"Momentum is left out: {error}"
    result = {
        "region": region,
        "region_label": label,
        "source_url": BASE_URL.removesuffix("ftp/") + "data_library.html",
        "factors": factors,
        "latest_date": max(factors),
        "momentum_note": momentum_note,
    }
    with _lock:
        _cache[region] = (moment, result)
    return result


# ETF stand-ins ------------------------------------------------------------------------

# factor: (long leg, short leg); the market factor's short leg is the T-bill rate.
PROXY_LEGS = {
    "us": {"Mkt-RF": ("SPY", None), "SMB": ("IWM", "SPY"), "HML": ("IWD", "IWF"),
           "RMW": ("QUAL", "SPY"), "CMA": ("USMV", "SPY"), "MOM": ("MTUM", "SPY")},
    "developed": {"Mkt-RF": ("URTH", None), "SMB": ("SCZ", "EFA"), "HML": ("EFV", "EFG"),
                  "RMW": ("IQLT", "EFA"), "CMA": ("EFAV", "EFA"), "MOM": ("IMTM", "EFA")},
    "asia_pacific_ex_japan": {"Mkt-RF": ("AAXJ", None), "SMB": ("SCZ", "EFA"), "HML": ("EFV", "EFG"),
                              "RMW": ("IQLT", "EFA"), "CMA": ("EFAV", "EFA"), "MOM": ("IMTM", "EFA")},
}
PROXY_LABELS = {
    "Mkt-RF": "Market",
    "SMB": "Size (small caps minus market)",
    "HML": "Value (value minus growth funds)",
    "RMW": "Quality (quality minus market)",
    "CMA": "Low volatility (minimum-volatility minus market)",
    "MOM": "Momentum (momentum minus market)",
}
TREASURY_BILL = "^IRX"  # 13-week Treasury bill yield, in percent a year
PROXY_HISTORY_DAYS = 1200


def _daily_changes(closes: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted((day, value) for day, value in closes.items() if value and value > 0)
    return {day: now / before - 1.0 for (_, before), (day, now) in zip(ordered, ordered[1:])}


def proxy_factors(region: str, closes: Mapping[str, Mapping[str, float]]) -> dict[str, dict[str, float]]:
    """Daily stand-in factor returns from adjusted ETF closes and the T-bill yield.

    A day is kept only when every leg has a return on it; the risk-free rate
    carries the latest earlier T-bill yield forward.
    """

    legs = PROXY_LEGS[region]
    changes = {symbol: _daily_changes(closes.get(symbol) or {})
               for pair in legs.values() for symbol in pair if symbol}
    bills = sorted((day, value) for day, value in (closes.get(TREASURY_BILL) or {}).items() if value is not None)
    if not bills:
        raise FactorDataError("No Treasury bill yield was returned for the risk-free rate.")
    days = sorted(set.intersection(*(set(c) for c in changes.values()))) if changes else []
    rows: dict[str, dict[str, float]] = {}
    cursor, latest = 0, None
    for day in days:
        while cursor < len(bills) and bills[cursor][0] <= day:
            latest = bills[cursor][1]
            cursor += 1
        if latest is None:
            continue
        rf = float(latest) / 100.0 / 252.0
        row = {"RF": rf}
        for factor, (long_leg, short_leg) in legs.items():
            row[factor] = changes[long_leg][day] - (changes[short_leg][day] if short_leg else rf)
        rows[day] = row
    if len(rows) < 60:
        raise FactorDataError("Too few days with prices for every stand-in fund.")
    return rows


def load_proxy_factors(
    region: str,
    fetch_history: Callable[[str, date, date], Mapping[str, float]] | None = None,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Stand-in factors for a region from Yahoo Finance adjusted closes."""

    if region not in PROXY_LEGS:
        raise FactorDataError(f"Unknown factor region {region!r}.")
    if fetch_history is None:
        from .crisis_replay import fetch_adjusted_history

        def fetch_history(symbol: str, start: date, end: date) -> Mapping[str, float]:
            # No raw capture: these are factor stand-ins, not crisis windows.
            return fetch_adjusted_history(symbol, start_date=start, end_date=end, capture_feed=None)

    end = today or date.today()
    start = end - timedelta(days=PROXY_HISTORY_DAYS)
    symbols = sorted({s for pair in PROXY_LEGS[region].values() for s in pair if s} | {TREASURY_BILL})
    closes = {}
    for symbol in symbols:
        try:
            closes[symbol] = fetch_history(symbol, start, end)
        except Exception as error:  # network or provider errors all mean "unavailable"
            raise FactorDataError(f"Could not read {symbol} from Yahoo Finance: {error}") from error
        if not closes[symbol]:
            raise FactorDataError(f"Yahoo Finance returned no prices for {symbol}.")
    factors = proxy_factors(region, closes)
    legs = PROXY_LEGS[region]
    return {
        "region": region,
        "region_label": REGIONS[region][0],
        "source": "etf_proxy",
        "source_url": "https://finance.yahoo.com",
        "factors": factors,
        "labels": PROXY_LABELS,
        "latest_date": max(factors),
        "momentum_note": "",
        "source_note": (
            "Kenneth French's data library could not be reached, so the factors are stand-ins built from "
            "US-listed funds on Yahoo Finance: "
            + "; ".join(f"{PROXY_LABELS[f].split(' (')[0]} {a}" + (f" minus {b}" if b else " minus the 13-week T-bill")
                        for f, (a, b) in legs.items())
            + "." + (" Asia Pacific ex Japan uses the developed-market style funds." if region == "asia_pacific_ex_japan" else "")
        ),
    }


def load_factor_data(region: str = DEFAULT_REGION, *, now: float | None = None,
                     french: Callable[[str], dict[str, Any]] | None = None,
                     proxies: Callable[[str], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Kenneth French's factors when the library answers, otherwise the ETF stand-ins (cached a day)."""

    try:
        result = (french or load_factors)(region)
        return {**result, "source": "kenneth_french", "labels": None, "source_note": ""}
    except FactorDataError as french_error:
        moment = time.time() if now is None else now
        key = f"proxy:{region}"
        with _lock:
            cached = _cache.get(key)
            if cached and moment - cached[0] < CACHE_SECONDS:
                return cached[1]
        try:
            result = (proxies or load_proxy_factors)(region)
        except FactorDataError as proxy_error:
            raise FactorDataError(f"{french_error} The ETF stand-ins were unavailable too: {proxy_error}") from proxy_error
        with _lock:
            _cache[key] = (moment, result)
        return result
