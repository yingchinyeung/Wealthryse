"""Hypothetical crisis scenarios for every account, portfolio and composite.

Each scenario replays the subject's current holdings, held as fixed units,
through one historical sell-off. Holdings with their own prices for the window
use them; the rest use a stand-in ETF (crisis_replay's proxy map), scaled by
the holding's own sensitivity to that stand-in: its beta on weekly returns over
the two years to the holdings date, kept between 0.25 and 3. Composites
combine their sleeves' scenario paths, the Hang Seng and SPY hedges and HKD
FX, the way their own NAV combines them.

The Lehman window is the one the existing replay uses, 15 September 2008 to
9 March 2009. That replay, its stored rows and the MS and T qualified replays
are not changed by this module; these results are stored separately and are
shown on the website. Internal/shareholder use only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import json
import math
from typing import Any, Callable, Iterable, Mapping

from .crisis_replay import (
    CRISIS_END, CRISIS_START, PROVIDER, PROXY_BY_CURRENCY, PROXY_BY_SYMBOL,
    _execute, _fx_rate_as_of, fetch_adjusted_history, resolve_holdings_as_of,
)
from .provisional_benchmark import convert_levels_to_reporting_currency, fetch_fx_to_reporting_yfinance
from .sector_classification import candidate_symbols
from .util import canonical_json, utc_now_iso

VERSION = "holdings-crisis-scenarios-v1"
SWITCH_NAME = "CRISIS_SCENARIOS_ENABLED"  # the website reads the table only once its migration is applied


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    label: str
    start: date
    end: date


SCENARIOS = (
    Scenario("lehman_2008", "Lehman collapse and global financial crisis", CRISIS_START, CRISIS_END),
    Scenario("china_2015", "China and Hong Kong market crash", date(2015, 6, 12), date(2016, 2, 11)),
    Scenario("covid_2020", "COVID-19 crash", date(2020, 2, 19), date(2020, 3, 23)),
    Scenario("rates_2022", "Rate rises and technology sell-off", date(2022, 1, 3), date(2022, 10, 31)),
)
LOOKBACK_DAYS = 10            # a prior close for markets shut on a window's first day
DIRECT_COVERAGE = 0.9         # own closes on at least 90% of the window's sessions
BETA_HISTORY_DAYS = 730       # two years to the holdings date
BETA_STEP = 5                 # weekly returns, so cross-market close times line up
BETA_MIN_RETURNS = 40         # about ten months of weekly returns
BETA_BOUNDS = (0.25, 3.0)
HEDGES = {"hang-seng-price-index": ("^HSI", "HKD"), "spy-settlement": ("SPY", "USD")}
SLEEVE_TYPES = {"account", "portfolio", "filtered_portfolio"}
PARAMOUNT_CONID = "804144296"
LIMITATIONS = ("Hypothetical replay of current holdings held as fixed units; not actual performance. "
               "Provisional Yahoo Finance prices; stand-ins scaled by each holding's two-year weekly beta. "
               "Cash flat; no rebalancing, financing or trading costs. Internal/shareholder use only.")


# Pure arithmetic ---------------------------------------------------------------


def align(series: Mapping[str, float], calendar: list[str]) -> dict[str, float] | None:
    """Carry the latest earlier close across holidays; None when the window opens before the series."""

    days = sorted(series)
    aligned, cursor, latest = {}, 0, None
    for day in calendar:
        while cursor < len(days) and days[cursor] <= day:
            latest = float(series[days[cursor]])
            cursor += 1
        if latest is None:
            return None
        aligned[day] = latest
    return aligned


def has_own_history(series: Mapping[str, float], calendar: list[str]) -> bool:
    """A holding is replayed directly only when it traded through the window itself."""

    if not calendar or not series or min(series) > calendar[0] or max(series) < calendar[-1]:
        return False
    own = sum(1 for day in calendar if day in series)
    return own >= DIRECT_COVERAGE * len(calendar)


def estimate_beta(stock: Mapping[str, float], proxy: Mapping[str, float]) -> dict[str, Any]:
    """Weekly-return beta of a holding on its stand-in, bounded; 1.0 when history is too short."""

    common = sorted(set(stock) & set(proxy))[::-1][::BETA_STEP][::-1]
    pairs = [(stock[b] / stock[a] - 1, proxy[b] / proxy[a] - 1) for a, b in zip(common, common[1:])]
    if len(pairs) < BETA_MIN_RETURNS:
        return {"beta": 1.0, "weekly_returns": len(pairs), "basis": "unscaled-short-history"}
    mean_s = sum(s for s, _ in pairs) / len(pairs)
    mean_p = sum(p for _, p in pairs) / len(pairs)
    variance = sum((p - mean_p) ** 2 for _, p in pairs)
    if variance <= 0:
        return {"beta": 1.0, "weekly_returns": len(pairs), "basis": "unscaled-flat-stand-in"}
    raw = sum((s - mean_s) * (p - mean_p) for s, p in pairs) / variance
    beta = min(max(raw, BETA_BOUNDS[0]), BETA_BOUNDS[1])
    return {"beta": beta, "raw_beta": raw, "weekly_returns": len(pairs),
            "basis": "bounded" if beta != raw else "estimated"}


def scaled_path(prices: Mapping[str, float], calendar: list[str], beta: float) -> list[float]:
    """The stand-in's daily returns times beta, compounded; exactly its own path when beta is 1."""

    if beta == 1.0:
        return [prices[day] / prices[calendar[0]] for day in calendar]
    path = [1.0]
    for before, after in zip(calendar, calendar[1:]):
        step = prices[after] / prices[before] - 1
        path.append(path[-1] * max(0.0, 1 + beta * step))
    return path


def combine(weights: Mapping[str, float], paths: Mapping[str, list[float]], calendar: list[str]) -> dict[str, Any]:
    """Fixed units plus flat cash: each day's value per opening NAV of one, and its drawdown."""

    cash = 1.0 - sum(weights.values())
    daily, peak = [], 1.0
    for i, day in enumerate(calendar):
        value = max(0.0, cash + sum(weight * paths[key][i] for key, weight in weights.items()))
        if not math.isfinite(value):
            raise ValueError(f"The scenario produced an invalid value on {day}")
        peak = max(peak, value)
        daily.append({"date": day, "value": value, "drawdown": value / peak - 1})
    return {"cash_weight": cash, "daily": daily, "maximum_drawdown": min(r["drawdown"] for r in daily)}


# Market data -------------------------------------------------------------------


class Prices:
    """Yahoo Finance closes and FX, fetched once per symbol and window."""

    def __init__(self, ticker_factory: Callable[[str], Any] | None = None, cache: dict | None = None):
        self.ticker_factory = ticker_factory
        self.cache = cache if cache is not None else {}

    def history(self, symbol: str, start: date, end: date) -> dict[str, float]:
        key = ("price", symbol, start.isoformat(), end.isoformat())
        if key not in self.cache:
            try:
                self.cache[key] = fetch_adjusted_history(symbol, self.ticker_factory, start_date=start, end_date=end)
            except Exception:  # an unknown or delisted ticker has no history, nothing more
                self.cache[key] = {}
        return self.cache[key]

    def fx(self, native: str, reporting: str, start: date, end: date) -> list[tuple[str, float]]:
        key = ("fx", native, reporting, start.isoformat(), end.isoformat())
        if key not in self.cache:
            self.cache[key], _ = fetch_fx_to_reporting_yfinance(native, reporting, start, end, self.ticker_factory)
        return self.cache[key]

    def in_currency(self, symbol: str, native: str, reporting: str, start: date, end: date) -> dict[str, float]:
        series = self.history(symbol, start, end)
        if not series or native == reporting:
            return series
        converted, _ = convert_levels_to_reporting_currency(list(series.items()),
                                                            self.fx(native, reporting, start, end))
        return dict(converted)

    def calendar(self, scenario: Scenario) -> list[str]:
        spy = self.history("SPY", scenario.start, scenario.end)
        days = sorted(spy)
        if not days or days[0] != scenario.start.isoformat() or days[-1] != scenario.end.isoformat():
            raise RuntimeError(f"SPY does not cover the {scenario.label} window")
        return days


def _fx_path(prices: Prices, native: str, reporting: str, scenario: Scenario, calendar: list[str]) -> list[float]:
    if native == reporting:
        return [1.0] * len(calendar)
    rows = prices.fx(native, reporting, scenario.start - timedelta(days=LOOKBACK_DAYS), scenario.end)
    aligned = align(dict(rows), calendar)
    if aligned is None:
        raise RuntimeError(f"No {native}/{reporting} FX at the start of the {scenario.label} window")
    return [aligned[day] / aligned[calendar[0]] for day in calendar]


# Accounts and portfolios ---------------------------------------------------------


def _holdings(connection, account_id: str, latest: str) -> tuple[float, list[dict]]:
    sqlite = type(connection).__module__.startswith("sqlite3")
    nav_table = "nav_snapshots" if sqlite else "subject_nav_series"
    position_table = "position_snapshots" if sqlite else "subject_position_series"
    column = "account_id" if sqlite else "subject_id"
    total = "AND asset_class = 'TOTAL'" if sqlite else ""
    row = _execute(connection, f"SELECT total_text FROM {nav_table} WHERE {column} = ? AND snapshot_date = ? {total}",
                   (account_id, latest)).fetchone()
    if row is None:
        raise RuntimeError(f"No total NAV is available for {account_id} on {latest}")
    rows = _execute(connection, f"""
        SELECT p.conid, COALESCE(NULLIF(i.symbol, ''), p.conid) AS symbol,
               COALESCE(i.listing_exchange, '') AS listing_exchange, p.currency,
               SUM(CAST(p.market_value_text AS DOUBLE PRECISION)) AS market_value
        FROM {position_table} p LEFT JOIN instruments i ON i.conid = p.conid
        WHERE p.{column} = ? AND p.snapshot_date = ?
        GROUP BY p.conid, COALESCE(NULLIF(i.symbol, ''), p.conid), COALESCE(i.listing_exchange, ''), p.currency
        ORDER BY p.conid""", (account_id, latest)).fetchall()
    if not rows:
        raise RuntimeError(f"No holdings are available for {account_id} on {latest}")
    return float(row["total_text"]), [dict(r) for r in rows]


def _holding_scenario(prices: Prices, holding: Mapping[str, Any], scenario: Scenario, calendar: list[str],
                      latest: date) -> tuple[list[float], dict[str, Any]]:
    symbol, currency = holding["symbol"], holding["currency"]
    window = (scenario.start - timedelta(days=LOOKBACK_DAYS), scenario.end)
    candidates = candidate_symbols(symbol, holding["listing_exchange"], currency)
    paramount = holding["conid"] == PARAMOUNT_CONID and scenario.start < date(2025, 8, 7)
    if not paramount:
        for candidate in candidates:
            try:
                own = prices.in_currency(candidate, currency, "USD", *window)
            except RuntimeError:
                continue
            if has_own_history(own, calendar):
                aligned = align(own, calendar)
                if aligned is not None:
                    return scaled_path(aligned, calendar, 1.0), {"source_symbol": candidate, "method": "direct"}
    stand_in = "SPY" if paramount else PROXY_BY_SYMBOL.get(symbol, PROXY_BY_CURRENCY.get(currency, "SPY"))
    proxy = align(prices.history(stand_in, *window), calendar)
    if proxy is None:
        raise RuntimeError(f"Stand-in {stand_in} has no prices at the start of the {scenario.label} window")
    recent = (latest - timedelta(days=BETA_HISTORY_DAYS), latest)
    stand_in_recent = prices.history(stand_in, *recent)
    beta = {"beta": 1.0, "weekly_returns": 0, "basis": "unscaled-no-recent-history"}
    for candidate in candidates:
        try:
            own_recent = prices.in_currency(candidate, currency, "USD", *recent)
        except RuntimeError:
            continue
        if own_recent:
            beta = {**estimate_beta(own_recent, stand_in_recent), "beta_symbol": candidate}
            break
    reason = ("Paramount Skydance's predecessor lineage is unverified; owner-approved SPY stand-in"
              if paramount else f"{symbol} has no prices of its own for this window")
    return scaled_path(proxy, calendar, beta["beta"]), {"source_symbol": stand_in, "method": "market_proxy",
                                                        "reason": reason, **beta}


def account_scenarios(connection, account_id: str, prices: Prices, *, as_of: str | None = None) -> dict[str, Any]:
    """Every scenario for an account or portfolio, in USD per opening NAV of one."""

    latest = resolve_holdings_as_of(connection, account_id=account_id, as_of=as_of)
    nav, holdings = _holdings(connection, account_id, latest)
    values = {}
    for holding in holdings:
        holding["symbol"] = str(holding["symbol"]).strip().upper()
        holding["currency"] = str(holding["currency"] or "").strip().upper()
        holding["conid"] = str(holding["conid"])
        if holding["symbol"].endswith(".REC"):
            continue  # a receivable is in NAV already; it stays in the flat residual
        rate = _fx_rate_as_of(connection, from_currency=holding["currency"], to_currency="USD", as_of=latest)
        values[holding["conid"]] = float(holding["market_value"]) * rate
    results = []
    for scenario in SCENARIOS:
        try:
            calendar = prices.calendar(scenario)
            paths, evidence = {}, []
            for holding in holdings:
                if holding["conid"] not in values:
                    continue
                paths[holding["conid"]], mapping = _holding_scenario(prices, holding, scenario, calendar,
                                                                     date.fromisoformat(latest))
                evidence.append({"conid": holding["conid"], "symbol": holding["symbol"],
                                 "weight": values[holding["conid"]] / nav, **mapping})
            replay = combine({k: v / nav for k, v in values.items()}, paths, calendar)
            results.append(_result(scenario, replay, evidence, "USD"))
        except (RuntimeError, ValueError) as error:
            results.append(_unavailable(scenario, str(error)))
    return {"subject_id": account_id, "holdings_as_of": latest, "reporting_currency": "USD", "scenarios": results}


# Composites ------------------------------------------------------------------------


def _composite_holdings(connection, subject_id: str, as_of: str | None) -> tuple[str, str, float, list[dict]]:
    subject = _execute(connection, "SELECT reporting_currency FROM platform_subjects WHERE subject_id = ?",
                       (subject_id,)).fetchone()
    if subject is None:
        raise RuntimeError(f"{subject_id} is not a registered subject")
    currency = str(subject["reporting_currency"]).upper()
    row = _execute(connection, """
        SELECT MAX(c.snapshot_date) AS latest FROM synthetic_portfolio_components c
        WHERE c.portfolio_id = ? AND (CAST(? AS TEXT) IS NULL OR c.snapshot_date <= ?)
          AND EXISTS (SELECT 1 FROM subject_nav_series n WHERE n.subject_id = c.portfolio_id
                      AND n.snapshot_date = c.snapshot_date AND n.currency = ?)""",
                   (subject_id, as_of, as_of, currency)).fetchone()
    if row is None or not row["latest"]:
        raise RuntimeError(f"No component snapshot with same-day {currency} NAV for {subject_id}")
    latest = str(row["latest"])[:10]
    nav = _execute(connection, "SELECT total_text FROM subject_nav_series WHERE subject_id = ? AND snapshot_date = ? "
                   "AND currency = ?", (subject_id, latest, currency)).fetchone()
    components = _execute(connection, """
        SELECT component_id, component_type, contribution FROM synthetic_portfolio_components
        WHERE portfolio_id = ? AND snapshot_date = ? ORDER BY component_id""", (subject_id, latest)).fetchall()
    return latest, currency, float(nav["total_text"]), [dict(r) for r in components]


def composite_scenarios(connection, subject_id: str, prices: Prices, sleeve_results: Callable[[str, str], Mapping],
                        *, as_of: str | None = None) -> dict[str, Any]:
    """Every scenario for a fixed-unit composite in its own currency, from its sleeves' scenarios."""

    latest, currency, nav, components = _composite_holdings(connection, subject_id, as_of)
    if currency not in {"USD", "HKD"}:
        raise RuntimeError(f"{subject_id} reports in {currency}; scenarios support USD and HKD")
    sleeves = {}
    for component in components:
        if component["component_type"] in SLEEVE_TYPES:
            key = str(component["component_id"])
            try:
                sleeves[key] = sleeve_results(key, latest)
            except Exception as error:  # the composite's scenarios say why, one by one
                sleeves[key] = {"scenarios": [], "holdings_as_of": None, "error": " ".join(str(error).split())}
    results = []
    for scenario in SCENARIOS:
        try:
            calendar = prices.calendar(scenario)
            usd = _fx_path(prices, "USD", currency, scenario, calendar)
            weights, paths, evidence = {}, {}, []
            for component in components:
                key, kind = str(component["component_id"]), component["component_type"]
                if kind == "cash":
                    continue
                if kind in SLEEVE_TYPES:
                    found = {r["scenario_id"]: r for r in sleeves[key]["scenarios"]}.get(scenario.scenario_id)
                    if not found or not found.get("available"):
                        why = sleeves[key].get("error") or (found or {}).get("reason") or "not computed"
                        raise RuntimeError(f"Sleeve {key} has no {scenario.label} result: {why}")
                    if [r["date"] for r in found["daily"]] != calendar:
                        raise RuntimeError(f"Sleeve {key}'s {scenario.label} dates differ")
                    path = [r["value"] * f for r, f in zip(found["daily"], usd)]
                    detail = {"method": "sleeve scenario", "sleeve_holdings_as_of": sleeves[key]["holdings_as_of"],
                              "direct_components": found["direct_components"],
                              "proxy_components": found["proxy_components"],
                              "scaled_components": found["scaled_components"]}
                elif key in HEDGES:
                    symbol, native = HEDGES[key]
                    series = align(prices.history(symbol, scenario.start - timedelta(days=LOOKBACK_DAYS),
                                                  scenario.end), calendar)
                    if series is None:
                        raise RuntimeError(f"{symbol} has no prices at the start of the {scenario.label} window")
                    fx = _fx_path(prices, native, currency, scenario, calendar)
                    path = [series[d] / series[calendar[0]] * f for d, f in zip(calendar, fx)]
                    detail = {"method": "hedge close", "source_symbol": symbol}
                else:
                    raise RuntimeError(f"No scenario series for component {key}")
                weights[key] = float(component["contribution"]) / nav
                paths[key] = path
                evidence.append({"component_id": key, "weight": weights[key], **detail})
            results.append(_result(scenario, combine(weights, paths, calendar), evidence, currency))
        except (RuntimeError, ValueError, KeyError) as error:
            results.append(_unavailable(scenario, str(error)))
    return {"subject_id": subject_id, "holdings_as_of": latest, "reporting_currency": currency, "scenarios": results}


def _result(scenario: Scenario, replay: Mapping[str, Any], evidence: list[dict], currency: str) -> dict[str, Any]:
    def count(method):
        return sum(1 for r in evidence if r.get("method") == method) + sum(
            int(r.get(f"{method}_components") or 0) for r in evidence if r.get("method") == "sleeve scenario")
    direct = count("direct") + sum(1 for r in evidence if r.get("method") == "hedge close")
    proxies = count("market_proxy")
    scaled = sum(1 for r in evidence if r.get("method") == "market_proxy" and r.get("beta", 1.0) != 1.0) + sum(
        int(r.get("scaled_components") or 0) for r in evidence if r.get("method") == "sleeve scenario")
    return {"scenario_id": scenario.scenario_id, "label": scenario.label, "available": True,
            "crisis_start": scenario.start.isoformat(), "crisis_end": scenario.end.isoformat(),
            "reporting_currency": currency, "observations": len(replay["daily"]),
            "maximum_drawdown": replay["maximum_drawdown"], "cash_weight": replay["cash_weight"],
            "direct_components": direct, "proxy_components": proxies, "scaled_components": scaled,
            "daily": replay["daily"], "components": evidence}


def _unavailable(scenario: Scenario, reason: str) -> dict[str, Any]:
    return {"scenario_id": scenario.scenario_id, "label": scenario.label, "available": False,
            "crisis_start": scenario.start.isoformat(), "crisis_end": scenario.end.isoformat(),
            "reason": " ".join(reason.split())[:300]}


# Storage and reading ----------------------------------------------------------------


def website_enabled(environ: Mapping[str, str] | None = None) -> bool:
    import os

    return (environ if environ is not None else os.environ).get(SWITCH_NAME, "").strip().lower() == "true"


def store(connection, result: Mapping[str, Any]) -> None:
    generated_at = utc_now_iso()
    _execute(connection, "DELETE FROM crisis_scenario_results WHERE subject_id = ? AND holdings_as_of = ? "
             "AND calculation_version = ?", (result["subject_id"], result["holdings_as_of"], VERSION))
    for item in result["scenarios"]:
        _execute(connection, """
            INSERT INTO crisis_scenario_results(subject_id, holdings_as_of, scenario_id, calculation_version,
                reporting_currency, crisis_start, crisis_end, available, maximum_drawdown, result_json, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                 (result["subject_id"], result["holdings_as_of"], item["scenario_id"], VERSION,
                  result["reporting_currency"], item["crisis_start"], item["crisis_end"], bool(item["available"]),
                  item.get("maximum_drawdown"), canonical_json(item), generated_at))


def load(connection, subject_id: str, *, as_of: str | None = None, tenant_id: str | None = None,
         daily: bool = False) -> dict[str, Any] | None:
    """The latest stored scenarios on or before as_of; daily paths only when asked for."""

    tenant = "tenant_id = ? AND " if tenant_id else ""
    args = (tenant_id,) if tenant_id else ()
    row = _execute(connection, f"""
        SELECT MAX(holdings_as_of) AS latest FROM crisis_scenario_results
        WHERE {tenant}subject_id = ? AND calculation_version = ? AND (CAST(? AS TEXT) IS NULL OR holdings_as_of <= ?)""",
                   (*args, subject_id, VERSION, as_of, as_of)).fetchone()
    if row is None or not row["latest"]:
        return None
    latest = str(row["latest"])
    rows = _execute(connection, f"""
        SELECT reporting_currency, result_json, generated_at FROM crisis_scenario_results
        WHERE {tenant}subject_id = ? AND calculation_version = ? AND holdings_as_of = ?""",
                    (*args, subject_id, VERSION, latest)).fetchall()
    order = [s.scenario_id for s in SCENARIOS]
    items = []
    for r in rows:
        item = r["result_json"]
        item = json.loads(item) if isinstance(item, str) else dict(item)
        if not daily:
            item.pop("daily", None)
            item.pop("components", None)
        items.append(item)
    items.sort(key=lambda r: order.index(r["scenario_id"]) if r["scenario_id"] in order else len(order))
    return {"subject_id": subject_id, "holdings_as_of": latest, "calculation_version": VERSION,
            "reporting_currency": str(rows[0]["reporting_currency"]) if rows else None,
            "generated_at": str(rows[0]["generated_at"]) if rows else None, "provider": PROVIDER,
            "limitations": LIMITATIONS, "scenarios": items}


# Refresh ---------------------------------------------------------------------------------


def refresh(connection, *, accounts: Iterable[str], composites: Iterable[str],
            ticker_factory: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """Accounts first, then composites (sleeves before the composites that hold them).

    One subject's failure is recorded and never stops the others.
    """

    prices = Prices(ticker_factory)
    done: dict[str, Mapping[str, Any]] = {}
    failures: dict[str, str] = {}
    composite_ids = set(composites)

    def run(subject_id: str, stack: tuple[str, ...] = ()) -> Mapping[str, Any]:
        if subject_id in done:
            return done[subject_id]
        if subject_id in failures:
            raise RuntimeError(failures[subject_id])
        if subject_id in stack:
            raise RuntimeError("Composite scenario dependency cycle: " + " -> ".join((*stack, subject_id)))
        if subject_id in composite_ids:
            result = composite_scenarios(connection, subject_id, prices,
                                         lambda sleeve, _: run(sleeve, (*stack, subject_id)))
        elif subject_id in account_ids:
            result = account_scenarios(connection, subject_id, prices)
        else:
            raise RuntimeError(f"{subject_id} is not approved for crisis-period price retrieval")
        store(connection, result)
        connection.commit()
        done[subject_id] = result
        return result

    account_ids = set(accounts)
    for subject_id in [*sorted(account_ids), *sorted(composite_ids)]:
        if subject_id in done:
            continue
        try:
            run(subject_id)
        except Exception as error:  # recorded per subject; never fails the caller's job
            connection.rollback()
            failures[subject_id] = " ".join(str(error).split())[:300]
    return {"version": VERSION, "subjects": {k: {"holdings_as_of": v["holdings_as_of"],
                                                 "available": sum(1 for s in v["scenarios"] if s["available"])}
                                             for k, v in done.items()},
            "failures": failures}
