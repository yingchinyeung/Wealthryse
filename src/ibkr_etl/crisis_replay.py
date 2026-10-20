from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Mapping

from . import publish_gate, raw_capture
from .provisional_benchmark import (
    convert_levels_to_reporting_currency,
    fetch_fx_to_reporting_yfinance,
)
from .sector_classification import candidate_symbols
from .util import utc_now_iso
from .crisis_identity_policy import apply_identity_proxy


CRISIS_START = date(2008, 9, 15)
CRISIS_END = date(2009, 3, 9)
CRISIS_OPENING_LOOKBACK_DAYS = 10
PROVIDER = "Yahoo Finance via yfinance"
# Raw-capture feed for crisis-window prices. Callers name it explicitly, so other
# users of fetch_adjusted_history (such as fama_french) never file under it.
RAW_FEED = "yahoo-crisis-history"
PRICE_TYPE = "adjusted_close"
CALCULATION_VERSION = "as-of-holdings-crisis-replay-v3"

# These mappings are used only when the holding itself does not have a complete
# crisis-period series. Every unmapped security falls back to broad-market SPY.
PROXY_BY_SYMBOL = {
    "ANET": "XLK", "SOUN": "XLK", "AVGO": "XLK", "RGTI": "XLK",
    "PAYC": "XLK", "HPE": "XLK", "QBTS": "XLK",
    "JOBY": "XLI",
    "OSCR": "XLV", "PCRX": "XLV", "ABBV": "XLV",
    "WRBY": "XLY",
    "SOLS": "XLB",
}
PROXY_BY_CURRENCY = {
    "TWD": "EWT",
    "HKD": "EWH",
    "CNY": "FXI",
    "CNH": "FXI",
}
PROXY_NAMES = {
    "SPY": "SPDR S&P 500 ETF Trust",
    "XLK": "Technology Select Sector SPDR Fund",
    "XLI": "Industrial Select Sector SPDR Fund",
    "XLV": "Health Care Select Sector SPDR Fund",
    "XLY": "Consumer Discretionary Select Sector SPDR Fund",
    "XLB": "Materials Select Sector SPDR Fund",
    "EWT": "iShares MSCI Taiwan ETF",
    "EWH": "iShares MSCI Hong Kong ETF",
    "FXI": "iShares China Large-Cap ETF",
}


@dataclass(frozen=True)
class ReplayComponent:
    conid: str
    symbol: str
    description: str
    currency: str
    market_value: float
    source_symbol: str
    mapping_method: str
    proxy_reason: str | None


def _normalized_adjusted_history(
    frame: Any,
    *,
    start_date: date = CRISIS_START,
    end_date: date = CRISIS_END,
) -> dict[str, float]:
    values: dict[str, float] = {}
    for index, row in frame.iterrows():
        raw = row.get("Adj Close")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value <= 0:
            continue
        observed = index.date() if hasattr(index, "date") else date.fromisoformat(str(index)[:10])
        if start_date <= observed <= end_date:
            values[observed.isoformat()] = value
    return dict(sorted(values.items()))


def fetch_adjusted_history(
    symbol: str,
    ticker_factory: Callable[[str], Any] | None = None,
    *,
    start_date: date = CRISIS_START,
    end_date: date = CRISIS_END,
    capture_feed: str | None = None,
) -> dict[str, float]:
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
    frame = ticker_factory(symbol).history(**request)
    if capture_feed:
        raw_capture.capture_frame(
            capture_feed,
            PROVIDER,
            frame=frame,
            request={"symbol": symbol, "method": "history", **request},
            key=raw_capture.request_key(symbol, request["start"], request["end"], request["interval"]),
            requested_at=requested_at,
            history=raw_capture.settled(end_date, now=requested_at),
        )
    return _normalized_adjusted_history(
        frame,
        start_date=start_date,
        end_date=end_date,
    )


def calculate_fixed_unit_replay(
    *,
    components: Iterable[ReplayComponent],
    portfolio_nav: float,
    prices: Mapping[str, Mapping[str, float]],
    calendar: Iterable[str],
) -> dict[str, Any]:
    if not math.isfinite(portfolio_nav) or portfolio_nav <= 0:
        raise ValueError("Portfolio NAV must be finite and positive")
    dates = list(calendar)
    if len(dates) < 2 or dates[0] != CRISIS_START.isoformat() or dates[-1] != CRISIS_END.isoformat():
        raise ValueError("Crisis replay calendar does not cover both required endpoints")
    component_rows = list(components)
    for component in component_rows:
        series = prices.get(component.source_symbol, {})
        if any(day not in series for day in dates):
            raise ValueError(f"Incomplete replay prices for {component.source_symbol}")
    invested_weight = sum(component.market_value / portfolio_nav for component in component_rows)
    cash_weight = 1.0 - invested_weight
    daily: list[dict[str, float | str]] = []
    peak = 1.0
    maximum_drawdown = 0.0
    for day in dates:
        value = cash_weight
        for component in component_rows:
            series = prices[component.source_symbol]
            value += component.market_value / portfolio_nav * (
                float(series[day]) / float(series[dates[0]])
            )
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"Crisis replay produced an invalid value on {day}")
        peak = max(peak, value)
        drawdown = value / peak - 1.0
        maximum_drawdown = min(maximum_drawdown, drawdown)
        daily.append({"observation_date": day, "normalized_value": value, "drawdown": drawdown})
    return {
        "maximum_drawdown": maximum_drawdown,
        "cash_weight": cash_weight,
        "invested_weight": invested_weight,
        "daily": daily,
    }


def _complete(series: Mapping[str, float], calendar: list[str]) -> bool:
    return bool(calendar) and all(day in series for day in calendar)


def convert_crisis_prices_to_usd(
    series: Mapping[str, float],
    currency: str,
    fx_to_usd: list[tuple[str, float]] | None = None,
) -> dict[str, float]:
    """Convert native adjusted closes before calculating replay returns."""

    native = currency.upper()
    if native == "USD":
        return dict(series)
    if not fx_to_usd:
        raise RuntimeError(f"No crisis-period {native}/USD FX series is available")
    converted, _ = convert_levels_to_reporting_currency(
        list(series.items()), fx_to_usd
    )
    return dict(converted)


def _iso_date(value: str | date | None) -> str | None:
    if value is None:
        return None
    return date.fromisoformat(str(value)).isoformat()


def _execute(connection: Any, sql: str, parameters: tuple[Any, ...]):
    """Execute portable question-mark SQL on wrapped, raw PostgreSQL, or SQLite."""

    module_name = type(connection).__module__
    if module_name.startswith("psycopg"):
        sql = sql.replace("?", "%s")
    return connection.execute(sql, parameters)


def _fx_rate_as_of(
    connection: Any,
    *,
    from_currency: str,
    to_currency: str,
    as_of: str,
    maximum_age_days: int = 4,
) -> float:
    source = from_currency.upper()
    target = to_currency.upper()
    if source == target:
        return 1.0
    row = _execute(
        connection,
        """
        SELECT rate_date, rate
        FROM reference_fx_rates
        WHERE from_currency = ? AND to_currency = ?
          AND rate_date <= ? AND quality_status IN ('pass', 'warn')
        ORDER BY rate_date DESC LIMIT 1
        """,
        (source, target, as_of),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"No validated {source}/{target} FX rate is available on or before {as_of}"
        )
    age = (date.fromisoformat(as_of) - date.fromisoformat(str(row["rate_date"]))).days
    if age < 0 or age > maximum_age_days:
        raise RuntimeError(
            f"The {source}/{target} FX rate is {age} days old at {as_of}"
        )
    rate = float(row["rate"])
    if not math.isfinite(rate) or rate <= 0:
        raise RuntimeError(f"The {source}/{target} FX rate is invalid")
    return rate


def resolve_holdings_as_of(
    connection: Any,
    *,
    account_id: str,
    as_of: str | date | None = None,
) -> str:
    """Return the latest holdings date with a same-day total NAV at the cutoff."""

    requested = _iso_date(as_of)
    sqlite = type(connection).__module__.startswith("sqlite3")
    composite = False
    if not sqlite:
        subject = _execute(
            connection,
            "SELECT subject_type FROM platform_subjects WHERE subject_id = ?",
            (account_id,),
        ).fetchone()
        composite = bool(
            subject and str(subject["subject_type"]) == "composite_portfolio"
        )
    if composite:
        row = _execute(
            connection,
            """
            SELECT MAX(c.snapshot_date) AS latest
            FROM synthetic_portfolio_components c
            WHERE c.portfolio_id = ?
              AND (CAST(? AS TEXT) IS NULL OR c.snapshot_date <= ?)
              AND EXISTS (
                  SELECT 1 FROM subject_nav_series n
                  WHERE n.subject_id = c.portfolio_id
                    AND n.snapshot_date = c.snapshot_date
              )
            """,
            (account_id, requested, requested),
        ).fetchone()
        latest = row["latest"] if row else None
        if not latest:
            raise RuntimeError(
                f"No composite component snapshot with same-day total NAV is available for {account_id}"
            )
        return str(latest)
    position_table = "position_snapshots" if sqlite else "subject_position_series"
    nav_table = "nav_snapshots" if sqlite else "subject_nav_series"
    subject_column = "account_id" if sqlite else "subject_id"
    nav_total_filter = "AND n.asset_class = 'TOTAL'" if sqlite else ""
    row = _execute(
        connection,
        f"""
        SELECT MAX(p.snapshot_date) AS latest
        FROM {position_table} p
        WHERE p.{subject_column} = ?
          AND (CAST(? AS TEXT) IS NULL OR p.snapshot_date <= ?)
          AND EXISTS (
              SELECT 1 FROM {nav_table} n
              WHERE n.{subject_column} = p.{subject_column}
                AND n.snapshot_date = p.snapshot_date
                {nav_total_filter}
          )
        """,
        (account_id, requested, requested),
    ).fetchone()
    latest = row["latest"] if row else None
    if not latest:
        suffix = f" on or before {requested}" if requested else ""
        raise RuntimeError(
            f"No position snapshot with same-day total NAV is available for "
            f"{account_id}{suffix}"
        )
    return str(latest)


def _align_to_calendar(series: Mapping[str, float], calendar: list[str]) -> dict[str, float]:
    """Carry the most recent prior market close across another market's holidays.

    The caller must provide a short pre-window lookback when the source market
    can be closed on the first day of the target calendar.  This deliberately
    refuses to backfill from a future close, which would introduce look-ahead
    bias into the crisis replay.
    """

    source_dates = sorted(series)
    aligned: dict[str, float] = {}
    cursor = 0
    latest: float | None = None
    for day in calendar:
        while cursor < len(source_dates) and source_dates[cursor] <= day:
            latest = float(series[source_dates[cursor]])
            cursor += 1
        if latest is not None:
            aligned[day] = latest
    if any(day not in aligned for day in calendar):
        raise RuntimeError(
            "A composite crisis component has no non-forward-looking opening observation"
        )
    return aligned


def refresh_composite_crisis_replay(
    connection: Any,
    *,
    account_id: str,
    holdings_as_of: str | date | None = None,
    ticker_factory: Callable[[str], Any] | None = None,
    price_cache: dict[str, dict[str, float]] | None = None,
    persisted_price_symbols: set[str] | None = None,
    component_stack: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Replay an approved fixed-unit composite through the crisis window."""

    if account_id in component_stack:
        lineage = " -> ".join((*component_stack, account_id))
        raise RuntimeError(f"Composite crisis replay dependency cycle: {lineage}")
    next_stack = (*component_stack, account_id)
    latest = resolve_holdings_as_of(
        connection, account_id=account_id, as_of=_iso_date(holdings_as_of)
    )
    nav_row = _execute(
        connection,
        "SELECT total_text FROM subject_nav_series WHERE subject_id = ? AND snapshot_date = ?",
        (account_id, latest),
    ).fetchone()
    if nav_row is None:
        raise RuntimeError(f"No composite NAV is available for {account_id} on {latest}")
    portfolio_nav = float(nav_row["total_text"])
    rows = _execute(
        connection,
        """
        SELECT component_id, component_name, component_type, direction,
               contribution, source_currency
        FROM synthetic_portfolio_components
        WHERE portfolio_id = ? AND snapshot_date = ?
        ORDER BY component_id
        """,
        (account_id, latest),
    ).fetchall()
    cache = price_cache if price_cache is not None else {}

    def prices(
        symbol: str,
        *,
        start_date: date = CRISIS_START,
    ) -> dict[str, float]:
        current = cache.get(symbol)
        needs_opening_lookback = bool(
            start_date < CRISIS_START
            and (
                not current
                or not any(day <= CRISIS_START.isoformat() for day in current)
            )
        )
        if current is None or needs_opening_lookback:
            fetched = fetch_adjusted_history(
                symbol,
                ticker_factory,
                start_date=start_date,
                end_date=CRISIS_END,
                capture_feed=RAW_FEED,
            )
            cache[symbol] = {**fetched, **(current or {})}
        return cache[symbol]

    spy = prices("SPY")
    calendar = list(spy)
    if not calendar or calendar[0] != CRISIS_START.isoformat() or calendar[-1] != CRISIS_END.isoformat():
        raise RuntimeError("SPY does not provide the complete composite crisis calendar")
    replay_components: list[ReplayComponent] = []
    replay_prices: dict[str, dict[str, float]] = {}
    for row in rows:
        component_type = str(row["component_type"])
        component_id = str(row["component_id"])
        if component_type == "cash":
            continue
        source_symbol = component_id
        if component_type in {"account", "portfolio", "filtered_portfolio"}:
            component_replay = load_crisis_replay(
                connection, account_id=component_id, as_of=latest
            )
            if component_replay is None:
                if component_type == "account":
                    component_replay = refresh_crisis_replay(
                        connection,
                        account_id=component_id,
                        holdings_as_of=latest,
                        ticker_factory=ticker_factory,
                        price_cache=cache,
                        persisted_price_symbols=persisted_price_symbols,
                    )
                else:
                    subject_row = _execute(
                        connection,
                        "SELECT subject_type FROM platform_subjects WHERE subject_id = ?",
                        (component_id,),
                    ).fetchone()
                    subject_type = str(subject_row["subject_type"]) if subject_row else ""
                    if subject_type == "composite_portfolio":
                        component_replay = refresh_composite_crisis_replay(
                            connection,
                            account_id=component_id,
                            holdings_as_of=latest,
                            ticker_factory=ticker_factory,
                            price_cache=cache,
                            persisted_price_symbols=persisted_price_symbols,
                            component_stack=next_stack,
                        )
                    elif subject_type == "filtered_portfolio":
                        component_replay = refresh_crisis_replay(
                            connection,
                            account_id=component_id,
                            holdings_as_of=latest,
                            ticker_factory=ticker_factory,
                            price_cache=cache,
                            persisted_price_symbols=persisted_price_symbols,
                        )
                    else:
                        raise RuntimeError(
                            f"Composite component {component_id} has unsupported subject type "
                            f"{subject_type or 'unknown'}"
                        )
            if component_replay is None:
                raise RuntimeError(
                    f"The crisis replay for composite component {component_id} could not be built"
                )
            source_rows = _execute(
                connection,
                """
                SELECT observation_date, normalized_value
                FROM crisis_replay_daily
                WHERE subject_id = ? AND holdings_as_of = ?
                  AND calculation_version = ?
                ORDER BY observation_date
                """,
                (
                    component_id,
                    component_replay["holdings_as_of"],
                    CALCULATION_VERSION,
                ),
            ).fetchall()
            series = {
                str(item["observation_date"]): float(item["normalized_value"])
                for item in source_rows
            }
        elif component_id == "hang-seng-price-index":
            source_symbol = "^HSI"
            lookback_start = CRISIS_START - timedelta(
                days=CRISIS_OPENING_LOOKBACK_DAYS
            )
            native = prices(source_symbol, start_date=lookback_start)
            fx_rows, _ = fetch_fx_to_reporting_yfinance(
                "HKD", "USD", lookback_start, CRISIS_END, ticker_factory
            )
            series = convert_crisis_prices_to_usd(native, "HKD", fx_rows)
        elif component_id == "spy-settlement":
            source_symbol = "SPY"
            series = spy
        else:
            raise RuntimeError(
                f"Unsupported composite crisis component: {component_id}"
            )
        try:
            replay_prices[source_symbol] = _align_to_calendar(series, calendar)
        except RuntimeError as error:
            raise RuntimeError(
                f"Composite crisis component {component_id} cannot be aligned: {error}"
            ) from error
        replay_components.append(
            ReplayComponent(
                conid=component_id,
                symbol=component_id,
                description=str(row["component_name"]),
                currency="USD",
                market_value=float(row["contribution"]),
                source_symbol=source_symbol,
                mapping_method="direct",
                proxy_reason=None,
            )
        )
    replay = calculate_fixed_unit_replay(
        components=replay_components,
        portfolio_nav=portfolio_nav,
        prices=replay_prices,
        calendar=calendar,
    )
    generated_at = utc_now_iso()
    _execute(
        connection,
        "DELETE FROM crisis_replay_components WHERE subject_id = ? AND holdings_as_of = ?",
        (account_id, latest),
    )
    for component in replay_components:
        _execute(
            connection,
            """
            INSERT INTO crisis_replay_components(
                subject_id, holdings_as_of, conid, holding_symbol, description,
                currency, current_market_value, opening_weight, source_symbol,
                mapping_method, proxy_reason, price_type, provider, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'direct', NULL, ?, ?, ?)
            """,
            (
                account_id, latest, component.conid, component.symbol,
                component.description, component.currency, component.market_value,
                component.market_value / portfolio_nav, component.source_symbol,
                PRICE_TYPE, PROVIDER, generated_at,
            ),
        )
    _execute(
        connection,
        "DELETE FROM crisis_replay_daily WHERE subject_id = ? AND holdings_as_of = ?",
        (account_id, latest),
    )
    for item in replay["daily"]:
        _execute(
            connection,
            """
            INSERT INTO crisis_replay_daily(
                subject_id, holdings_as_of, observation_date, normalized_value,
                drawdown, crisis_start, crisis_end, calculation_version,
                quality_status, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'warn', ?)
            """,
            (
                account_id, latest, item["observation_date"],
                item["normalized_value"], item["drawdown"],
                CRISIS_START.isoformat(), CRISIS_END.isoformat(),
                CALCULATION_VERSION, generated_at,
            ),
        )
    _before_commit(connection, account_id, latest)
    connection.commit()
    loaded = load_crisis_replay(connection, account_id=account_id, as_of=latest)
    if loaded is None:
        raise RuntimeError("The composite crisis replay could not be reloaded")
    return loaded


def _before_commit(connection: Any, account_id: str, latest: str) -> None:
    """The publish gate on the replay just written (off by default). It logs its verdict
    only: readers show the replay for one exact holdings date."""
    partition = {"subject_id": account_id, "holdings_as_of": latest}
    publish_gate.before_commit(
        connection, {"gold.crisis_replay_daily": partition, "gold.crisis_replay_components": partition},
        writer="crisis_replay")


def load_crisis_replay(
    connection: Any,
    *,
    account_id: str,
    as_of: str | date | None,
) -> dict[str, Any] | None:
    """Load the replay for the exact holdings snapshot resolved at the cutoff."""

    requested = _iso_date(as_of)
    try:
        holdings_as_of = resolve_holdings_as_of(
            connection, account_id=account_id, as_of=requested
        )
    except RuntimeError:
        return None
    row = _execute(
        connection,
        """
        SELECT holdings_as_of AS latest
        FROM crisis_replay_daily
        WHERE subject_id = ?
          AND holdings_as_of = ?
          AND calculation_version = ?
        LIMIT 1
        """,
        (account_id, holdings_as_of, CALCULATION_VERSION),
    ).fetchone()
    if row is None:
        return None
    daily = _execute(
        connection,
        """
        SELECT MIN(drawdown) AS maximum_drawdown, COUNT(*) AS observations,
               MIN(crisis_start) AS crisis_start, MAX(crisis_end) AS crisis_end,
               MAX(calculation_version) AS calculation_version,
               MAX(generated_at) AS generated_at
        FROM crisis_replay_daily
        WHERE subject_id = ? AND holdings_as_of = ?
          AND calculation_version = ?
        """,
        (account_id, holdings_as_of, CALCULATION_VERSION),
    ).fetchone()
    if daily is None or daily["maximum_drawdown"] is None:
        return None
    component_rows = _execute(
        connection,
        """
        SELECT mapping_method, COUNT(*) AS components,
               SUM(opening_weight) AS opening_weight,
               MAX(provider) AS provider, MAX(price_type) AS price_type
        FROM crisis_replay_components
        WHERE subject_id = ? AND holdings_as_of = ?
        GROUP BY mapping_method ORDER BY mapping_method
        """,
        (account_id, holdings_as_of),
    ).fetchall()
    counts = {str(item["mapping_method"]): int(item["components"]) for item in component_rows}
    invested_weight = sum(float(item["opening_weight"] or 0.0) for item in component_rows)
    return {
        "requested_as_of": requested,
        "holdings_as_of": str(holdings_as_of),
        "crisis_start": str(daily["crisis_start"]),
        "crisis_end": str(daily["crisis_end"]),
        "maximum_drawdown": float(daily["maximum_drawdown"]),
        "observations": int(daily["observations"]),
        "direct_components": counts.get("direct", 0),
        "proxy_components": counts.get("market_proxy", 0),
        "cash_weight": 1.0 - invested_weight,
        "provider": component_rows[0]["provider"] if component_rows else None,
        "price_type": component_rows[0]["price_type"] if component_rows else None,
        "calculation_version": str(daily["calculation_version"]),
        "generated_at": str(daily["generated_at"]),
        "publication_status": "blocked_provisional_market_data",
    }


# A Korean-won account (Kiwoom) is replayed in won, on its own market: each KRX
# holding's adjusted close, or the KOSPI or KOSDAQ index for a holding with no
# complete crisis history. The window and its trading days are the same SPY
# calendar every other account uses; a KRX close is carried across Korean
# holidays (Chuseok closed KRX on 15 September 2008) from the latest earlier
# close, never a later one.
KRX_PROXY_BY_SUFFIX = {".KS": "^KS11", ".KQ": "^KQ11"}
KRX_PROXY_NAMES = {"^KS11": "KOSPI Composite Index", "^KQ11": "KOSDAQ Composite Index"}
PROXY_NAMES.update(KRX_PROXY_NAMES)


def _subject_reporting_currency(connection: Any, account_id: str, sqlite: bool) -> str:
    if sqlite:
        return "USD"
    row = _execute(
        connection, "SELECT reporting_currency FROM platform_subjects WHERE subject_id = ?", (account_id,),
    ).fetchone()
    return str((row["reporting_currency"] if row else None) or "USD")


def _krx_complete(series: Mapping[str, float], market_days: list[str]) -> bool:
    """A KRX close on or before the window opens, and one on every KRX trading day in it."""
    return bool(series) and min(series) <= CRISIS_START.isoformat() and all(day in series for day in market_days)


def _krw_components(
    holdings: Iterable[Mapping[str, Any]],
    ticker_factory: Callable[[str], Any] | None,
    cache: dict[str, dict[str, float]],
) -> tuple[list[ReplayComponent], dict[str, dict[str, float]], list[str]]:
    start = CRISIS_START - timedelta(days=CRISIS_OPENING_LOOKBACK_DAYS)

    def load(symbol: str, *, lookback: bool) -> dict[str, float]:
        key = f"{symbol}@lookback" if lookback else symbol
        if key not in cache:
            cache[key] = fetch_adjusted_history(
                symbol, ticker_factory, start_date=start if lookback else CRISIS_START, capture_feed=RAW_FEED)
        return cache[key]

    spy = load("SPY", lookback=False)
    calendar = list(spy)
    if not _complete(spy, calendar) or calendar[0] != CRISIS_START.isoformat() or calendar[-1] != CRISIS_END.isoformat():
        raise RuntimeError("SPY does not provide a complete crisis-period calendar")
    kospi = load("^KS11", lookback=True)
    market_days = [day for day in kospi if CRISIS_START.isoformat() <= day <= CRISIS_END.isoformat()]
    if not market_days or min(kospi) > CRISIS_START.isoformat():
        raise RuntimeError("The KOSPI index does not cover the crisis window")
    fetched: dict[str, dict[str, float]] = {"^KS11": _align_to_calendar(kospi, calendar)}
    if not _krx_complete(kospi, market_days):
        raise RuntimeError("Market proxy ^KS11 lacks complete crisis-period data")

    def proxy_for(suffix: str) -> str:
        # KOSDAQ names take the KOSDAQ index when Yahoo has it for the whole window, else KOSPI.
        proxy = KRX_PROXY_BY_SUFFIX[suffix]
        if proxy not in fetched:
            series = load(proxy, lookback=True)
            if not _krx_complete(series, market_days):
                return "^KS11"
            fetched[proxy] = _align_to_calendar(series, calendar)
        return proxy
    components: list[ReplayComponent] = []
    for row in holdings:
        symbol = str(row["symbol"]).strip().upper()
        currency = str(row["currency"] or "").strip().upper()
        if currency != "KRW":
            raise RuntimeError(f"{symbol} is held in {currency or 'no currency'}; a won replay takes KRW holdings only")
        source_symbol, listed_suffix = "", ".KS"
        for candidate in candidate_symbols(symbol, str(row["listing_exchange"] or ""), currency):
            suffix = next((s for s in KRX_PROXY_BY_SUFFIX if candidate.endswith(s)), None)
            if suffix is None:
                continue
            series = load(candidate, lookback=True)
            if series and suffix == ".KQ":
                listed_suffix = ".KQ"
            if _krx_complete(series, market_days):
                source_symbol = candidate
                fetched[candidate] = _align_to_calendar(series, calendar)
                break
        if source_symbol:
            mapping_method, reason = "direct", None
        else:
            source_symbol = proxy_for(listed_suffix)
            mapping_method = "market_proxy"
            reason = f"{symbol} lacks a complete adjusted-close series for the crisis window"
        components.append(ReplayComponent(
            conid=str(row["conid"]), symbol=symbol, description=str(row["description"] or ""),
            currency="KRW", market_value=float(row["market_value"]), source_symbol=source_symbol,
            mapping_method=mapping_method, proxy_reason=reason,
        ))
    used = {component.source_symbol for component in components}
    return components, {symbol: series for symbol, series in fetched.items() if symbol in used}, calendar


def refresh_crisis_replay(
    connection: Any,
    *,
    account_id: str = "U16088571",
    holdings_as_of: str | date | None = None,
    ticker_factory: Callable[[str], Any] | None = None,
    price_cache: dict[str, dict[str, float]] | None = None,
    persisted_price_symbols: set[str] | None = None,
    reporting_currency: str | None = None,
) -> dict[str, Any]:
    requested = _iso_date(holdings_as_of)
    latest = resolve_holdings_as_of(
        connection, account_id=account_id, as_of=requested
    )
    sqlite = type(connection).__module__.startswith("sqlite3")
    nav_table = "nav_snapshots" if sqlite else "subject_nav_series"
    position_table = "position_snapshots" if sqlite else "subject_position_series"
    subject_column = "account_id" if sqlite else "subject_id"
    nav_total_filter = "AND asset_class = 'TOTAL'" if sqlite else ""
    nav_row = _execute(
        connection,
        f"SELECT total_text FROM {nav_table} WHERE {subject_column} = ? AND snapshot_date = ? {nav_total_filter}",
        (account_id, latest),
    ).fetchone()
    if nav_row is None:
        raise RuntimeError(f"No total NAV is available for {account_id} on {latest}")
    portfolio_nav = float(nav_row["total_text"])
    holdings = _execute(
        connection,
        f"""
        SELECT p.conid, COALESCE(NULLIF(i.symbol, ''), p.conid) AS symbol,
               COALESCE(i.description, '') AS description,
               COALESCE(i.listing_exchange, '') AS listing_exchange, p.currency,
               SUM(CAST(p.market_value_text AS DOUBLE PRECISION)) AS market_value
        FROM {position_table} p LEFT JOIN instruments i ON i.conid = p.conid
        WHERE p.{subject_column} = ? AND p.snapshot_date = ?
        GROUP BY p.conid, COALESCE(NULLIF(i.symbol, ''), p.conid),
                 COALESCE(i.description, ''), COALESCE(i.listing_exchange, ''),
                 p.currency
        ORDER BY p.conid
        """,
        (account_id, latest),
    ).fetchall()
    if not holdings:
        raise RuntimeError(f"No holdings are available for {account_id} on {latest}")
    cache = price_cache if price_cache is not None else {}
    reporting = (reporting_currency or _subject_reporting_currency(connection, account_id, sqlite)).upper()
    if reporting == "KRW":
        components, fetched, calendar = _krw_components(holdings, ticker_factory, cache)
        return _store_replay(
            connection, account_id=account_id, requested=requested, latest=latest,
            components=components, prices=fetched, calendar=calendar, portfolio_nav=portfolio_nav,
            persisted_price_symbols=persisted_price_symbols, currency="KRW",
        )
    fx_cache: dict[str, list[tuple[str, float]]] = {}

    def load_prices(symbol: str) -> dict[str, float]:
        if symbol not in cache:
            cache[symbol] = fetch_adjusted_history(symbol, ticker_factory, capture_feed=RAW_FEED)
        return cache[symbol]

    def prices_in_usd(symbol: str, currency: str) -> dict[str, float]:
        """Return an adjusted-close series in the account reporting currency.

        Regional direct listings are quoted in their native currency.  Using
        those raw levels in a USD risk replay would omit crisis-period FX
        movement, so each direct series is converted using same-day or the
        latest non-forward-looking Yahoo FX close before normalization.
        """

        native = currency.upper()
        series = load_prices(symbol)
        if native == "USD":
            return convert_crisis_prices_to_usd(series, native)
        if native not in fx_cache:
            fx_cache[native], _ = fetch_fx_to_reporting_yfinance(
                native,
                "USD",
                CRISIS_START,
                CRISIS_END,
                ticker_factory,
            )
        return convert_crisis_prices_to_usd(series, native, fx_cache[native])

    spy = load_prices("SPY")
    calendar = list(spy)
    if not _complete(spy, calendar) or calendar[0] != CRISIS_START.isoformat() or calendar[-1] != CRISIS_END.isoformat():
        raise RuntimeError("SPY does not provide a complete crisis-period calendar")
    proxy_symbols = (
        set(PROXY_BY_SYMBOL.values()) | set(PROXY_BY_CURRENCY.values()) | {"SPY"}
    )
    fetched: dict[str, dict[str, float]] = {"SPY": spy}
    for symbol in sorted(proxy_symbols - {"SPY"}):
        series = load_prices(symbol)
        if not _complete(series, calendar):
            raise RuntimeError(f"Market proxy {symbol} lacks complete crisis-period data")
        fetched[symbol] = series

    components: list[ReplayComponent] = []
    for row in holdings:
        symbol = str(row["symbol"]).strip().upper()
        if symbol.endswith(".REC"):
            # A receivable is already reflected in NAV but is not an equity
            # unit.  Leaving it out of components makes it part of the flat
            # cash/residual sleeve instead of assigning an equity-market proxy.
            continue
        currency = str(row["currency"] or "").strip().upper()
        fx_rate = _fx_rate_as_of(
            connection,
            from_currency=currency,
            to_currency="USD",
            as_of=latest,
        )
        market_value = float(row["market_value"]) * fx_rate
        identity = None
        if account_id == 'U16088571':
            identity = apply_identity_proxy({'conid':str(row['conid']), 'holding_symbol':symbol},
                                            subject_id=account_id, crisis_start=CRISIS_START.isoformat())
        if identity and identity.get('proxy_policy_version'):
            # Apply verified identity policy BEFORE vendor history can appear
            # complete. A historical ticker path alone is not lineage proof.
            components.append(ReplayComponent(
                conid=str(row['conid']), symbol=symbol,
                description=str(row['description'] or ''), currency='USD',
                market_value=market_value, source_symbol=identity['source_symbol'],
                mapping_method=identity['mapping_method'], proxy_reason=identity['proxy_reason']))
            continue
        source_symbol = ""
        for candidate in candidate_symbols(
            symbol, str(row["listing_exchange"] or ""), currency
        ):
            try:
                direct = prices_in_usd(candidate, currency)
            except RuntimeError:
                # A USD-denominated market proxy remains preferable to silently
                # replaying a native-currency series without its FX movement.
                direct = {}
            if _complete(direct, calendar):
                source_symbol = candidate
                fetched[candidate] = direct
                break
        if source_symbol:
            mapping_method = "direct"
            reason = None
        else:
            source_symbol = PROXY_BY_SYMBOL.get(
                symbol, PROXY_BY_CURRENCY.get(currency, "SPY")
            )
            mapping_method = "market_proxy"
            reason = f"{symbol} lacks a complete adjusted-close series for the crisis window"
        components.append(
            ReplayComponent(
                conid=str(row["conid"]),
                symbol=symbol,
                description=str(row["description"] or ""),
                currency="USD",
                market_value=market_value,
                source_symbol=source_symbol,
                mapping_method=mapping_method,
                proxy_reason=reason,
            )
        )

    return _store_replay(
        connection, account_id=account_id, requested=requested, latest=latest,
        components=components, prices=fetched, calendar=calendar, portfolio_nav=portfolio_nav,
        persisted_price_symbols=persisted_price_symbols, currency="USD",
    )


def _store_replay(
    connection: Any,
    *,
    account_id: str,
    requested: str | None,
    latest: str,
    components: list[ReplayComponent],
    prices: Mapping[str, Mapping[str, float]],
    calendar: list[str],
    portfolio_nav: float,
    persisted_price_symbols: set[str] | None,
    currency: str,
) -> dict[str, Any]:
    """Calculate the fixed-unit replay and store it with its components and prices."""

    fetched = prices
    replay = calculate_fixed_unit_replay(
        components=components,
        portfolio_nav=portfolio_nav,
        prices=fetched,
        calendar=calendar,
    )
    generated_at = utc_now_iso()
    used_symbols = sorted({component.source_symbol for component in components})
    persisted = persisted_price_symbols if persisted_price_symbols is not None else set()
    for source_symbol in used_symbols:
        if source_symbol in persisted:
            continue
        instrument_id = f"crisis-replay-{source_symbol.lower()}"
        instrument_name = PROXY_NAMES.get(source_symbol, source_symbol)
        for price_date, price in fetched[source_symbol].items():
            _execute(
                connection,
                """
                INSERT INTO market_data_prices(
                    instrument_id, price_date, symbol, instrument_name, price_type,
                    currency, price, source, source_url, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, instrument_id, price_date, price_type)
                DO UPDATE SET price = excluded.price, source = excluded.source,
                    source_url = excluded.source_url, fetched_at = excluded.fetched_at
                """,
                (instrument_id, price_date, source_symbol, instrument_name, PRICE_TYPE,
                 currency, price, PROVIDER,
                 f"yfinance:TICKER({source_symbol}):history(auto_adjust=False):Adj Close",
                 generated_at),
            )
        persisted.add(source_symbol)
    _execute(
        connection,
        "DELETE FROM crisis_replay_components WHERE subject_id = ? AND holdings_as_of = ?",
        (account_id, latest),
    )
    for component in components:
        _execute(
            connection,
            """
            INSERT INTO crisis_replay_components(
                subject_id, holdings_as_of, conid, holding_symbol, description,
                currency, current_market_value, opening_weight, source_symbol,
                mapping_method, proxy_reason, price_type, provider, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, latest, component.conid, component.symbol,
             component.description, component.currency, component.market_value,
             component.market_value / portfolio_nav, component.source_symbol,
             component.mapping_method, component.proxy_reason, PRICE_TYPE,
             PROVIDER, generated_at),
        )
    _execute(
        connection,
        "DELETE FROM crisis_replay_daily WHERE subject_id = ? AND holdings_as_of = ?",
        (account_id, latest),
    )
    for row in replay["daily"]:
        _execute(
            connection,
            """
            INSERT INTO crisis_replay_daily(
                subject_id, holdings_as_of, observation_date, normalized_value,
                drawdown, crisis_start, crisis_end, calculation_version,
                quality_status, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'warn', ?)
            """,
            (account_id, latest, row["observation_date"], row["normalized_value"],
             row["drawdown"], CRISIS_START.isoformat(), CRISIS_END.isoformat(),
             CALCULATION_VERSION, generated_at),
        )
    _before_commit(connection, account_id, latest)
    connection.commit()
    direct_count = sum(component.mapping_method == "direct" for component in components)
    proxy_count = len(components) - direct_count
    return {
        "status": "success",
        "subject_id": account_id,
        "requested_as_of": requested,
        "holdings_as_of": latest,
        "crisis_start": CRISIS_START.isoformat(),
        "crisis_end": CRISIS_END.isoformat(),
        "maximum_drawdown": replay["maximum_drawdown"],
        "cash_weight": replay["cash_weight"],
        "components": len(components),
        "direct_components": direct_count,
        "proxy_components": proxy_count,
        "observations": len(replay["daily"]),
        "provider": PROVIDER,
        "price_type": PRICE_TYPE,
        "calculation_version": CALCULATION_VERSION,
        "reporting_currency": currency,
        "publication_allowed": False,
    }
