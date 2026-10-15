"""Routes, markup and browser script for the account Modelling tab.

Four read-only models over data the platform already stores (see
:mod:`ibkr_etl.modelling` for the maths): a Monte Carlo wealth projection, a
what-if rebalancing comparison, portfolio optimisation with a risk budget, and
a Fama-French factor model. Nothing is written to the database.

MODELLING_SCRIPT runs inside the account page script after ANALYTICS_SCRIPT and
relies on its helpers (account, esc, json, draw, pct2, signedPct, weightPct,
money, ratio, toned, analyticsDate, chartColors, reportingCurrency).
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException

from . import fama_french
from .modelling import (
    ModellingInputError,
    factor_model,
    holding_return_matrix,
    optimise,
    project_wealth,
    what_if,
)
from .performance_periods import PerformanceInputError, validated_flows, validated_nav
from .portfolio_analytics import daily_return_series

PRICE_HISTORY_DAYS = 550      # calendar days of marks, enough for 252 sessions
MAX_ADDED_TICKERS = 5
TICKER_PATTERN = re.compile(r"^[A-Za-z0-9.^=\-]{1,20}$")
COMPOSITE_MESSAGE = "Composite sleeves have no security-level price history, so this model uses holdings it cannot see."


def _number(value: str | None, name: str, *, low: float, high: float) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = float(value)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=f"{name} must be a number") from error
    if not math.isfinite(parsed) or not low <= parsed <= high:
        raise HTTPException(status_code=400, detail=f"{name} must be between {low:g} and {high:g}")
    return parsed


def _parse_weights(text: str | None) -> dict[str, float]:
    """``conid:0.1,conid:0.2`` as fractions of NAV."""

    weights: dict[str, float] = {}
    for part in (text or "").split(","):
        if not part.strip():
            continue
        key, _, raw = part.partition(":")
        key = key.strip()
        if not key or len(key) > 40:
            raise HTTPException(status_code=400, detail="Weights must be written as id:fraction")
        weights[key] = _number(raw, "Each weight", low=0.0, high=2.0) or 0.0
    return weights


def _yahoo_closes(symbol: str, start: date, end: date) -> dict[str, float]:
    """Daily closes for a ticker the portfolio does not hold yet, keyed by local trading date."""

    from .market_data import _chart_url, _download_json

    payload = _download_json(_chart_url(symbol, start, end), timeout=20)
    results = (payload.get("chart") or {}).get("result") or []
    if not results:
        raise RuntimeError(f"No prices were found for {symbol}")
    result = results[0]
    try:
        zone = ZoneInfo(str((result.get("meta") or {}).get("exchangeTimezoneName") or "UTC"))
    except Exception:
        zone = timezone.utc
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    closes = {}
    for stamp, close in zip(result.get("timestamp") or [], quotes):
        if close is None or not math.isfinite(float(close)) or float(close) <= 0:
            continue
        closes[datetime.fromtimestamp(int(stamp), timezone.utc).astimezone(zone).date().isoformat()] = float(close)
    if not closes:
        raise RuntimeError(f"No prices were found for {symbol}")
    return closes


def install(
    app: FastAPI,
    *,
    authorized_user: Callable[..., dict[str, str]],
    access_context: Callable[[str, dict], dict],
    parse_as_of: Callable[[str | None], str | None],
    read_database: Callable[[], Any],
    nav_values: Callable[[Any, str, dict, str | None], list[tuple[str, str]]],
    external_flows: Callable[[Any, str, dict], dict[str, float] | None],
    latest_holdings: Callable[[Any, str, str | None], list[dict]],
    enrich_holdings: Callable[..., dict],
    comparison_version: Callable[[str, str], str],
    tenant_id: str,
    risk_analytics_version: str,
    fetch_closes: Callable[[str, date, date], dict[str, float]] = _yahoo_closes,
    load_factors: Callable[[str], dict[str, Any]] = fama_french.load_factors,
) -> None:
    """Add the /api/portfolio/{account_id}/modelling/* routes."""

    def portfolio_returns(connection, account_id: str, access: dict, as_of: str | None):
        nav = validated_nav(nav_values(connection, account_id, access, as_of))
        flows = validated_flows(external_flows(connection, account_id, access) or {})
        returns, failures = daily_return_series(nav, flows)
        if failures:
            raise HTTPException(status_code=409, detail=failures[0])
        return nav, returns

    def risk_free_rate(connection, account_id: str, as_of: str | None) -> float | None:
        from .risk_snapshots import decoded_payload

        row = connection.execute(
            """
            SELECT payload_json FROM risk_analytics_snapshots
            WHERE tenant_id = %s AND subject_id = %s AND analytics_version = %s
              AND (%s::text IS NULL OR as_of_date <= %s)
            ORDER BY as_of_date DESC LIMIT 1
            """,
            (tenant_id, account_id, risk_analytics_version, as_of, as_of),
        ).fetchone()
        if row is None:
            return None
        rate = (decoded_payload(row["payload_json"]).get("metrics") or {}).get("risk_free_annual_rate")
        try:
            return float(rate) if rate is not None and math.isfinite(float(rate)) else None
        except (TypeError, ValueError):
            return None

    def benchmark_returns(connection, account_id: str, as_of: str | None) -> tuple[dict[str, float], str | None]:
        mapping = connection.execute(
            """
            SELECT b.benchmark_id, b.name, m.reporting_currency
            FROM portfolio_benchmark_mapping m
            JOIN benchmark_master b ON b.benchmark_id = m.benchmark_id AND b.tenant_id = m.tenant_id
            WHERE m.tenant_id = %s AND m.subject_id = %s AND b.active = true
              AND m.effective_from <= CURRENT_DATE::text
              AND (m.effective_to IS NULL OR m.effective_to >= CURRENT_DATE::text)
            ORDER BY CASE m.mapping_type WHEN 'primary' THEN 0 ELSE 1 END, b.name
            LIMIT 1
            """,
            (tenant_id, account_id),
        ).fetchone()
        if mapping is None:
            return {}, None
        rows = connection.execute(
            """
            SELECT observation_date, benchmark_return FROM performance_comparison_daily
            WHERE tenant_id = %s AND subject_id = %s AND benchmark_id = %s
              AND calculation_version = %s AND reporting_currency = %s
              AND quality_status IN ('pass', 'warn') AND benchmark_return IS NOT NULL
              AND (%s::text IS NULL OR observation_date <= %s)
            ORDER BY observation_date
            """,
            (tenant_id, account_id, mapping["benchmark_id"],
             comparison_version(account_id, mapping["reporting_currency"]),
             mapping["reporting_currency"], as_of, as_of),
        ).fetchall()
        result = {}
        for row in rows:
            try:
                result[str(row["observation_date"])[:10]] = float(row["benchmark_return"])
            except (TypeError, ValueError):
                continue
        return result, str(mapping["name"])

    def holdings_and_prices(connection, account_id: str, access: dict, as_of: str | None):
        rows = latest_holdings(connection, account_id, as_of)
        if not rows:
            raise HTTPException(status_code=404, detail="No holdings are available for this date")
        view = enrich_holdings(
            connection, account_id, rows, subject_type=str(access.get("subject_type") or "physical_account"),
            reporting_currency=str(access["reporting_currency"]), nav_currency=str(access["reporting_currency"]),
        )
        held = [row for row in view["holdings"] if row.get("weight") is not None]
        snapshot = str(rows[0]["snapshot_date"])[:10]
        start = (date.fromisoformat(snapshot) - timedelta(days=PRICE_HISTORY_DAYS)).isoformat()
        prices: dict[str, dict[str, float]] = {}
        if held:
            for mark in connection.execute(
                """
                SELECT snapshot_date, conid::text AS conid, mark_price_text
                FROM subject_position_series
                WHERE tenant_id = %s AND subject_id = %s AND conid::text = ANY(%s)
                  AND snapshot_date BETWEEN %s AND %s
                """,
                (tenant_id, account_id, [str(row["conid"]) for row in held], start, snapshot),
            ).fetchall():
                try:
                    value = float(mark["mark_price_text"])
                except (TypeError, ValueError):
                    continue
                prices.setdefault(str(mark["conid"]), {})[str(mark["snapshot_date"])[:10]] = value
        return snapshot, held, prices

    def holding_access(account_id: str, user: dict) -> dict:
        access = access_context(account_id, user)
        if str(access.get("subject_type") or "") == "composite_portfolio":
            raise HTTPException(status_code=409, detail=COMPOSITE_MESSAGE)
        return access

    def model_error(error: Exception) -> HTTPException:
        return HTTPException(status_code=422, detail=str(error))

    @app.get("/api/portfolio/{account_id}/modelling/projection")
    def modelling_projection(
        account_id: str,
        as_of: str | None = None,
        years: int = 10,
        monthly_flow: str | None = None,
        target: str | None = None,
        annual_return: str | None = None,
        user: dict[str, str] = Depends(authorized_user),
    ) -> dict:
        """Monte Carlo projection of NAV from the portfolio's own daily returns."""
        access = access_context(account_id, user)
        requested = parse_as_of(as_of)
        flow = _number(monthly_flow, "The monthly contribution", low=-1e12, high=1e12) or 0.0
        goal = _number(target, "The target value", low=0.0, high=1e15)
        assumed = _number(annual_return, "The return assumption", low=-50.0, high=100.0)
        with read_database() as connection:
            try:
                nav, returns = portfolio_returns(connection, account_id, access, requested)
            except PerformanceInputError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
        if not nav:
            raise HTTPException(status_code=404, detail="No NAV history is available for this date")
        last_day = next(reversed(nav))
        try:
            result = project_wealth(
                [value for _, value in returns], start_value=float(nav[last_day]), start_date=last_day,
                years=years, monthly_flow=flow, target=goal or None,
                annual_return=None if assumed is None else assumed / 100.0,
            )
        except ModellingInputError as error:
            raise model_error(error) from error
        return {"account_id": account_id, "currency": access["reporting_currency"],
                "history_start": returns[0][0] if returns else None, **result}

    @app.get("/api/portfolio/{account_id}/modelling/what-if")
    def modelling_what_if(
        account_id: str,
        as_of: str | None = None,
        weights: str | None = None,
        add: str | None = None,
        user: dict[str, str] = Depends(authorized_user),
    ) -> dict:
        """Risk of current holdings against proposed weights (``conid:fraction`` pairs)."""
        access = holding_access(account_id, user)
        requested = parse_as_of(as_of)
        changes = _parse_weights(weights)
        added = _parse_weights(add)
        if len(added) > MAX_ADDED_TICKERS:
            raise HTTPException(status_code=400, detail=f"Add at most {MAX_ADDED_TICKERS} new tickers at a time")
        for symbol in added:
            if not TICKER_PATTERN.match(symbol):
                raise HTTPException(status_code=400, detail=f"{symbol} is not a valid ticker")
        with read_database() as connection:
            snapshot, held, prices = holdings_and_prices(connection, account_id, access, requested)
            risk_free = risk_free_rate(connection, account_id, requested)
            benchmark, benchmark_name = benchmark_returns(connection, account_id, requested)
        current = {str(row["conid"]): float(row["weight"]) for row in held}
        unknown = sorted(set(changes) - set(current))
        if unknown:
            raise HTTPException(status_code=400, detail=f"Not a current holding: {', '.join(unknown[:3])}")
        proposed = {**current, **changes}
        added_status = []
        start = date.fromisoformat(snapshot) - timedelta(days=PRICE_HISTORY_DAYS)
        for symbol, weight in added.items():
            key = f"new:{symbol.upper()}"
            try:
                prices[key] = fetch_closes(symbol.upper(), start, date.fromisoformat(snapshot))
                proposed[key] = weight
                added_status.append({"conid": key, "symbol": symbol.upper(), "weight": weight, "available": True})
            except Exception as error:  # a bad or unknown ticker is reported, not fatal
                added_status.append({"conid": key, "symbol": symbol.upper(), "weight": weight,
                                     "available": False, "message": str(error)[:200]})
        matrix = holding_return_matrix(prices)
        try:
            result = what_if(current, proposed, matrix, risk_free=risk_free or 0.0, benchmark=benchmark or None)
        except ModellingInputError as error:
            raise model_error(error) from error
        coverage = matrix["coverage"]
        return {
            "account_id": account_id,
            "snapshot_date": snapshot,
            "benchmark_name": benchmark_name,
            "risk_free_available": risk_free is not None,
            "holdings": [
                {
                    "conid": str(row["conid"]), "symbol": row.get("symbol") or str(row["conid"]),
                    "description": row.get("description") or "", "current_weight": float(row["weight"]),
                    "proposed_weight": proposed[str(row["conid"])],
                    "sessions": coverage.get(str(row["conid"]), 0),
                    "modelled": str(row["conid"]) in matrix["returns"],
                }
                for row in sorted(held, key=lambda r: -float(r["weight"]))
            ],
            "added": added_status,
            "excluded_price_breaks": matrix["excluded_price_breaks"],
            **result,
        }

    @app.get("/api/portfolio/{account_id}/modelling/optimise")
    def modelling_optimise(
        account_id: str,
        as_of: str | None = None,
        max_weight: str | None = None,
        user: dict[str, str] = Depends(authorized_user),
    ) -> dict:
        """Risk budget, efficient frontier and optimised weights of the current holdings."""
        access = holding_access(account_id, user)
        requested = parse_as_of(as_of)
        cap = _number(max_weight, "The maximum weight", low=1.0, high=100.0)
        with read_database() as connection:
            snapshot, held, prices = holdings_and_prices(connection, account_id, access, requested)
            risk_free = risk_free_rate(connection, account_id, requested)
        current = {str(row["conid"]): float(row["weight"]) for row in held}
        labels = {str(row["conid"]): {"symbol": row.get("symbol"), "description": row.get("description")}
                  for row in held}
        try:
            result = optimise(current, labels, holding_return_matrix(prices), risk_free=risk_free or 0.0,
                              cap=(cap or 25.0) / 100.0)
        except ModellingInputError as error:
            raise model_error(error) from error
        result["excluded"] = [labels[k]["symbol"] or k for k in result["excluded"]]
        return {"account_id": account_id, "snapshot_date": snapshot,
                "risk_free_available": risk_free is not None, **result}

    @app.get("/api/portfolio/{account_id}/modelling/factors")
    def modelling_factors(
        account_id: str,
        as_of: str | None = None,
        region: str = fama_french.DEFAULT_REGION,
        user: dict[str, str] = Depends(authorized_user),
    ) -> dict:
        """Fama-French five-factor plus momentum exposures of the portfolio's returns."""
        access = access_context(account_id, user)
        requested = parse_as_of(as_of)
        if region not in fama_french.REGIONS:
            raise HTTPException(status_code=400, detail="Unknown factor region")
        with read_database() as connection:
            try:
                _, returns = portfolio_returns(connection, account_id, access, requested)
            except PerformanceInputError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
        try:
            factors = load_factors(region)
        except fama_french.FactorDataError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        try:
            result = factor_model(returns, factors["factors"], region_label=factors["region_label"],
                                  source_url=factors["source_url"])
        except ModellingInputError as error:
            raise model_error(error) from error
        return {"account_id": account_id, "currency": access["reporting_currency"],
                "factor_data_through": factors["latest_date"], "momentum_note": factors["momentum_note"],
                "regions": {key: value[0] for key, value in fama_french.REGIONS.items()}, **result}


MODELLING_CARDS = r"""
<div class="card wide" id="model-projection"><div class="label">Wealth projection</div><div class="muted">Thousands of simulated futures built from this portfolio's own month-by-month returns, with regular contributions or withdrawals. A range of outcomes, not a forecast.</div>
<div class="filter-grid"><label><span class="label">Horizon</span><select id="projection-years" aria-label="Projection horizon"><option value="1">1 year</option><option value="3">3 years</option><option value="5">5 years</option><option value="10" selected>10 years</option><option value="20">20 years</option><option value="30">30 years</option></select></label><label><span class="label">Monthly contribution (<span class="model-ccy"></span>, negative to withdraw)</span><input id="projection-flow" type="number" step="any" value="0" aria-label="Monthly contribution or withdrawal"></label><label><span class="label">Target value (<span class="model-ccy"></span>, optional)</span><input id="projection-target" type="number" step="any" min="0" aria-label="Target value"></label><label><span class="label">Return assumption (% a year, blank for history)</span><input id="projection-return" type="number" step="0.1" min="-50" max="100" aria-label="Annual return assumption"></label></div>
<div class="actions"><button id="projection-run" class="button" type="button">Run projection</button></div>
<div id="projection-metrics" class="metrics"></div><div class="chart-wrap"><canvas id="projection-chart" width="1100" height="300" aria-label="Projected NAV range"></canvas><div class="chart-tooltip" role="status" aria-live="polite"></div></div><div id="projection-note" class="muted">Loading…</div></div>
<div class="card wide" id="model-whatif"><div class="label">What-if rebalancing</div><div class="muted">Change the weight of any holding, or add a ticker you do not hold, and compare the risk of the proposed portfolio with today's over the same year of daily prices. Weights are shares of NAV; whatever is left over is cash.</div>
<div class="table-scroll"><table><thead><tr><th class="text-col">Holding</th><th>Current weight</th><th>Proposed weight (%)</th><th>Price history</th></tr></thead><tbody id="whatif-holdings"></tbody></table></div>
<div class="filter-grid"><label><span class="label">Add a ticker (Yahoo Finance symbol)</span><input id="whatif-add-symbol" type="text" maxlength="20" autocomplete="off" placeholder="e.g. VOO or 0700.HK" aria-label="Ticker to add"></label><label><span class="label">Weight (%)</span><input id="whatif-add-weight" type="number" step="0.1" min="0" max="200" aria-label="Weight of the added ticker"></label></div>
<div class="actions"><button id="whatif-add" class="button" type="button">Add ticker</button><button id="whatif-run" class="button" type="button">Compare portfolios</button><button id="whatif-reset" class="button" type="button">Reset to current</button></div>
<div id="whatif-total" class="muted"></div><div class="table-scroll"><table><thead><tr><th class="text-col">Measure</th><th>Current</th><th>Proposed</th><th>Change</th></tr></thead><tbody id="whatif-results"></tbody></table></div><div id="whatif-note" class="muted">Loading…</div></div>
<div class="card wide" id="model-optimise"><div class="label">Optimisation and risk budget</div><div class="muted">How much of the portfolio's risk each holding carries, and three alternative mixes of the same holdings: lowest risk (minimum variance), equal risk from each holding (risk parity) and best return per unit of risk (maximum Sharpe).</div>
<div class="filter-grid"><label><span class="label">Maximum weight per holding (%)</span><input id="optimise-cap" type="number" step="1" min="1" max="100" value="25" aria-label="Maximum weight per holding"></label></div>
<div class="actions"><button id="optimise-run" class="button" type="button">Optimise</button></div>
<div class="table-scroll"><table><thead><tr><th class="text-col">Portfolio</th><th>Expected return</th><th>Volatility</th><th>Sharpe ratio</th><th></th></tr></thead><tbody id="optimise-portfolios"></tbody></table></div>
<div id="optimise-frontier" class="model-frontier" aria-label="Efficient frontier chart"></div>
<div class="table-scroll"><table><thead><tr><th class="text-col">Holding</th><th>Volatility</th><th>Weight now</th><th>Share of risk now</th><th>Minimum variance</th><th>Risk parity</th><th>Maximum Sharpe</th></tr></thead><tbody id="optimise-holdings"></tbody></table></div><div id="optimise-note" class="muted">Loading…</div></div>
<div class="card wide" id="model-factors"><div class="label">Factor model</div><div class="muted">Splits the portfolio's weekly returns into exposure to the market, company size, value, profitability, investment style and momentum (Fama-French five factors plus momentum), and the return those factors do not explain (alpha).</div>
<div class="filter-grid"><label><span class="label">Factor region</span><select id="factor-region" aria-label="Factor region"><option value="developed" selected>Developed markets</option><option value="us">United States</option><option value="asia_pacific_ex_japan">Asia Pacific ex Japan</option></select></label></div>
<div class="actions"><button id="factor-run" class="button" type="button">Run factor model</button></div>
<div id="factor-metrics" class="metrics"></div><div class="table-scroll"><table><thead><tr><th class="text-col">Factor</th><th>Beta</th><th>t-statistic</th><th>Factor return (a year)</th><th>Return contribution (a year)</th></tr></thead><tbody id="factor-exposures"></tbody></table></div><div id="factor-note" class="muted">Loading…</div></div>
"""

MODELLING_CSS = """
.model-frontier{margin:16px 0}.model-frontier svg{width:100%;height:auto;max-height:340px;display:block}
.model-frontier .axis{stroke:var(--line,#d0d5dd)}.model-frontier text{fill:var(--muted,#667085);font-size:12px}
.model-frontier .frontier{fill:none;stroke:var(--chart-portfolio,#2646a6);stroke-width:2}
.model-frontier .point{stroke:var(--surface,#fff);stroke-width:1.5}
[data-section="modelling"] input[type=number]{min-width:0;width:100%;max-width:100%;margin-top:7px;padding:10px 13px;color:var(--ink);background:var(--surface);border:1px solid var(--line-strong);font:inherit}
[data-section="modelling"] .filter-grid label{display:flex;flex-direction:column;justify-content:flex-end}
#whatif-holdings input[type=number]{width:8em;margin-top:0;padding:6px 10px;text-align:right}
"""

MODELLING_SCRIPT = r"""
const modelFail=async(response)=>{let detail='';try{const body=await response.json();detail=typeof body.detail==='string'?body.detail:''}catch(error){}return detail||`The model could not run (error ${response.status}).`};
const modelFetch=async(path,params)=>{const response=await fetch(`/api/portfolio/${account}/modelling/${path}?${new URLSearchParams(params)}`);if(!response.ok)throw new Error(await modelFail(response));return response.json()};
const modelTile=(label,value,hint)=>`<div class="metric"${hint?` title="${esc(hint)}"`:''}><div class="label">${esc(label)}</div><div class="value">${value}</div></div>`;
const modelMoney=n=>n==null||!Number.isFinite(Number(n))?'—':`${money(n)} ${esc(modelState.currency)}`;
const modelState={asOf:null,currency:reportingCurrency,whatif:null,added:{},optimised:null};
document.querySelectorAll('.model-ccy').forEach(x=>x.textContent=reportingCurrency);
const runProjection=async()=>{
  const note=document.querySelector('#projection-note'),metrics=document.querySelector('#projection-metrics');note.textContent='Simulating…';
  const params={as_of:modelState.asOf,years:document.querySelector('#projection-years').value,monthly_flow:document.querySelector('#projection-flow').value||'0'};
  const target=document.querySelector('#projection-target').value,assumed=document.querySelector('#projection-return').value;if(target)params.target=target;if(assumed!=='')params.annual_return=assumed;
  let r;try{r=await modelFetch('projection',params)}catch(error){metrics.innerHTML='';draw('#projection-chart',[],[],{emptyMessage:'No projection.'});note.textContent=error.message;return}
  modelState.currency=r.currency||reportingCurrency;const s=r.summary,a=r.assumptions;
  metrics.innerHTML=[
    modelTile(`Median value in ${r.years} ${r.years===1?'year':'years'}`,modelMoney(s.median_end),'Half of the simulated paths end above this value.'),
    modelTile('Weak case (5th percentile)',modelMoney(s.p5_end),'Only 1 path in 20 ends below this value.'),
    modelTile('Strong case (95th percentile)',modelMoney(s.p95_end),'Only 1 path in 20 ends above this value.'),
    modelTile('Total contributions',r.total_flows?toned(modelMoney(r.total_flows),r.total_flows):'—'),
    r.target?modelTile('Chance of reaching the target',pct2(s.probability_target)):'',
    r.monthly_flow<0?modelTile('Chance of running out',pct2(s.probability_depleted)):modelTile('Chance of ending below money put in',pct2(s.probability_loss)),
    modelTile(a.return_source==='user'?'Assumed return (your input)':'Historical return used',pct2(a.annual_return),'Compound annual growth of the sampled months.'),
    modelTile('Volatility used',pct2(a.annual_volatility),'Annualised spread of the sampled monthly returns.'),
  ].join('');
  draw('#projection-chart',r.series,[{key:'p95',color:'#98a2b3',label:'95th percentile'},{key:'p75',color:'#7a8fd6',label:'75th percentile'},{key:'p50',color:chartColors.portfolio,label:'Median'},{key:'p25',color:'#7a8fd6',label:'25th percentile'},{key:'p5',color:'#98a2b3',label:'5th percentile'}],{yLabel:`NAV (${modelState.currency})`,unit:modelState.currency,niceYTicks:true,targetYIntervals:6,emptyMessage:'No projection.'});
  note.textContent=`${r.method} Starting value ${modelMoney(r.start_value)} on ${analyticsDate(r.start_date)}, sampled from returns since ${analyticsDate(r.history_start)}. ${(r.warnings||[]).join(' ')} ${r.disclaimer}`;
};
const whatifWeights=()=>[...document.querySelectorAll('#whatif-holdings input[data-conid]')].map(input=>[input.dataset.conid,Number(input.value)/100]).filter(([,w])=>Number.isFinite(w));
const whatifTotal=()=>{const total=whatifWeights().reduce((sum,[,w])=>sum+w,0)+Object.values(modelState.added).reduce((sum,w)=>sum+w,0);document.querySelector('#whatif-total').textContent=`Proposed holdings add up to ${weightPct(total)} of NAV, leaving ${weightPct(1-total)} in cash${total>1?' (borrowed)':''}.`};
const renderWhatifHoldings=r=>{
  const added=Object.entries(modelState.added).map(([symbol,weight])=>{const status=(r.added||[]).find(x=>x.symbol===symbol);return `<tr><td class="text-col"><strong>${esc(symbol)}</strong><div class="muted">Added ticker${status&&!status.available?` · ${esc(status.message||'no prices')}`:''}</div></td><td>—</td><td>${esc((100*weight).toFixed(1))} <button type="button" class="button" data-remove="${esc(symbol)}">Remove</button></td><td>${status&&status.available?'Yahoo Finance':'—'}</td></tr>`}).join('');
  document.querySelector('#whatif-holdings').innerHTML=r.holdings.map(h=>`<tr><td class="text-col"><strong>${esc(h.symbol)}</strong><div class="muted">${esc(h.description)}</div></td><td>${weightPct(h.current_weight)}</td><td><input type="number" step="0.1" min="0" max="200" data-conid="${esc(h.conid)}" value="${(100*h.proposed_weight).toFixed(2)}" aria-label="Proposed weight of ${esc(h.symbol)}"></td><td>${h.modelled?`${esc(h.sessions)} sessions`:`<span class="muted">${esc(h.sessions)} sessions, carried at 0%</span>`}</td></tr>`).join('')+added;
  document.querySelectorAll('#whatif-holdings input[data-conid]').forEach(input=>input.addEventListener('input',whatifTotal));
  document.querySelectorAll('#whatif-holdings [data-remove]').forEach(button=>button.addEventListener('click',()=>{delete modelState.added[button.dataset.remove];void runWhatif()}));
  whatifTotal();
};
const whatifRows=[['Annual return (historical)','annual_return',pct2,true],['Volatility','annual_volatility',pct2,false],['Sharpe ratio','sharpe_ratio',ratio,true],['Daily VaR (95%)','var_95_daily',pct2,false],['Daily expected tail loss (95%)','cvar_95_daily',pct2,false],['Maximum drawdown','maximum_drawdown',pct2,true],['Worst month (21 sessions)','worst_month',pct2,true],['Beta to benchmark','beta',ratio,null],['Cash','cash_weight',weightPct,null]];
const runWhatif=async(useInputs=true)=>{
  const note=document.querySelector('#whatif-note');note.textContent='Comparing…';
  const params={as_of:modelState.asOf};
  if(useInputs){const weights=whatifWeights();if(weights.length)params.weights=weights.map(([k,w])=>`${k}:${Math.max(0,w)}`).join(',')}
  const added=Object.entries(modelState.added);if(added.length)params.add=added.map(([s,w])=>`${s}:${w}`).join(',');
  let r;try{r=await modelFetch('what-if',params)}catch(error){note.textContent=error.message;document.querySelector('#whatif-results').innerHTML='';return}
  modelState.whatif=r;renderWhatifHoldings(r);
  document.querySelector('#whatif-results').innerHTML=whatifRows.map(([label,key,format,higherBetter])=>{const d=r.difference[key];const change=d==null?'—':higherBetter==null?format(d):`<span class="${Math.abs(d)<1e-9?'':((d>0)===higherBetter?'gain':'loss')}">${d>0?'+':''}${format(d)}</span>`;return `<tr><td class="text-col">${esc(label)}</td><td>${format(r.current[key])}</td><td>${format(r.proposed[key])}</td><td>${key==='cash_weight'?format(r.proposed[key]-r.current[key]):change}</td></tr>`}).join('');
  note.textContent=`Both portfolios are replayed over ${r.observations} sessions from ${analyticsDate(r.window_start)} to ${analyticsDate(r.window_end)} using each holding's daily price in its own currency (currency moves are not included). Cash earns ${r.risk_free_available?`the ${pct2(r.risk_free_rate)} risk-free rate`:'0% (no risk-free rate on file)'}. ${r.benchmark_name?`Beta is measured against ${r.benchmark_name}.`:'No benchmark is mapped, so beta is not shown.'} Holdings with less than 60% price coverage are carried at 0% return.${r.excluded_price_breaks?` ${r.excluded_price_breaks} one-day moves above 50% were treated as corporate actions and left out.`:''} ${r.disclaimer}`;
};
document.querySelector('#whatif-run').addEventListener('click',()=>void runWhatif());
document.querySelector('#whatif-reset').addEventListener('click',()=>{modelState.added={};void runWhatif(false)});
document.querySelector('#whatif-add').addEventListener('click',()=>{const symbol=document.querySelector('#whatif-add-symbol').value.trim().toUpperCase(),weight=Number(document.querySelector('#whatif-add-weight').value)/100;if(!/^[A-Z0-9.^=\-]{1,20}$/.test(symbol)||!(weight>0)){document.querySelector('#whatif-note').textContent='Enter a ticker and a weight above 0% to add it.';return}if(!modelState.added[symbol]&&Object.keys(modelState.added).length>=5){document.querySelector('#whatif-note').textContent='Add at most 5 tickers at a time.';return}modelState.added[symbol]=weight;document.querySelector('#whatif-add-symbol').value='';document.querySelector('#whatif-add-weight').value='';void runWhatif()});
const portfolioNames={current:'Current mix',minimum_variance:'Minimum variance',risk_parity:'Risk parity',maximum_sharpe:'Maximum Sharpe'};
const portfolioColors={current:chartColors.benchmark,minimum_variance:'#0e9384',risk_parity:'#7a5af8',maximum_sharpe:chartColors.portfolio};
const frontierSvg=r=>{
  const points=[...r.frontier,...Object.values(r.portfolios)],xs=points.map(p=>p.volatility),ys=points.map(p=>p.expected_return);
  const x0=Math.min(...xs)*0.9,x1=Math.max(...xs)*1.05,ylo=Math.min(...ys),yhi=Math.max(...ys),pad=(yhi-ylo)*0.1||0.01,y0=ylo-pad,y1=yhi+pad,W=720,H=320,L=70,R=20,T=16,B=66;
  const X=v=>L+(v-x0)/(x1-x0||1)*(W-L-R),Y=v=>T+(y1-v)/(y1-y0||1)*(H-T-B),ticks=(a,b)=>Array.from({length:5},(_,i)=>a+i*(b-a)/4);
  const path=r.frontier.map((p,i)=>`${i?'L':'M'}${X(p.volatility).toFixed(1)},${Y(p.expected_return).toFixed(1)}`).join('');
  const dots=Object.entries(r.portfolios).map(([k,p])=>`<g><circle class="point" cx="${X(p.volatility).toFixed(1)}" cy="${Y(p.expected_return).toFixed(1)}" r="7" fill="${portfolioColors[k]}"><title>${esc(portfolioNames[k])}: ${pct2(p.expected_return)} return, ${pct2(p.volatility)} volatility</title></circle></g>`).join('');
  const legend=Object.keys(r.portfolios).map((k,i)=>`<g transform="translate(${L+10+i*150},${H-8})"><circle r="5" fill="${portfolioColors[k]}"></circle><text x="10" y="4">${esc(portfolioNames[k])}</text></g>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Efficient frontier: expected return against volatility"><line class="axis" x1="${L}" y1="${H-B}" x2="${W-R}" y2="${H-B}"></line><line class="axis" x1="${L}" y1="${T}" x2="${L}" y2="${H-B}"></line>${ticks(x0,x1).map(v=>`<text x="${X(v)}" y="${H-B+16}" text-anchor="middle">${pct2(v)}</text>`).join('')}${ticks(y0,y1).map(v=>`<text x="${L-8}" y="${Y(v)+4}" text-anchor="end">${pct2(v)}</text>`).join('')}<text x="${(L+W-R)/2}" y="${H-B+34}" text-anchor="middle">Volatility (a year) →</text><path class="frontier" d="${path}"></path>${dots}${legend}</svg>`;
};
const useInWhatif=name=>{const r=modelState.optimised;if(!r||!modelState.whatif)return;const weights=Object.fromEntries(r.holdings.map(h=>[h.conid,h[`${name}_weight`]*r.sleeve_weight]));document.querySelectorAll('#whatif-holdings input[data-conid]').forEach(input=>{if(input.dataset.conid in weights)input.value=(100*weights[input.dataset.conid]).toFixed(2)});whatifTotal();void runWhatif();document.querySelector('#model-whatif').scrollIntoView({behavior:'smooth'})};
const runOptimise=async()=>{
  const note=document.querySelector('#optimise-note');note.textContent='Optimising…';
  let r;try{r=await modelFetch('optimise',{as_of:modelState.asOf,max_weight:document.querySelector('#optimise-cap').value||'25'})}catch(error){note.textContent=error.message;['#optimise-portfolios','#optimise-holdings','#optimise-frontier'].forEach(x=>document.querySelector(x).innerHTML='');return}
  modelState.optimised=r;
  document.querySelector('#optimise-portfolios').innerHTML=Object.entries(r.portfolios).map(([k,p])=>`<tr><td class="text-col"><span aria-hidden="true" style="color:${portfolioColors[k]}">●</span> ${esc(portfolioNames[k])}</td><td>${pct2(p.expected_return)}</td><td>${pct2(p.volatility)}</td><td>${ratio(p.sharpe_ratio)}</td><td>${k==='current'?'':`<button type="button" class="button" data-use="${k}">Try in what-if</button>`}</td></tr>`).join('');
  document.querySelectorAll('#optimise-portfolios [data-use]').forEach(b=>b.addEventListener('click',()=>useInWhatif(b.dataset.use)));
  document.querySelector('#optimise-frontier').innerHTML=frontierSvg(r);
  const riskCell=(w,share)=>`${weightPct(w)}<div class="muted">${weightPct(share)} of risk</div>`;
  document.querySelector('#optimise-holdings').innerHTML=r.holdings.map(h=>`<tr><td class="text-col"><strong>${esc(h.symbol)}</strong><div class="muted">${esc(h.description)}</div></td><td>${pct2(h.annual_volatility)}</td><td>${weightPct(h.current_weight)}</td><td><span class="${h.current_risk>h.current_weight*1.25?'loss':''}">${weightPct(h.current_risk)}</span></td><td>${riskCell(h.minimum_variance_weight,h.minimum_variance_risk)}</td><td>${riskCell(h.risk_parity_weight,h.risk_parity_risk)}</td><td>${riskCell(h.maximum_sharpe_weight,h.maximum_sharpe_risk)}</td></tr>`).join('');
  note.textContent=`Weights are shares of the ${weightPct(r.sleeve_weight)} of NAV in these holdings; cash stays as it is. A share of risk well above the holding's weight is marked red. ${r.method} Risk-free rate ${r.risk_free_available?pct2(r.risk_free_rate):'0% (none on file)'}. Window ${analyticsDate(r.window_start)} to ${analyticsDate(r.window_end)}.${r.excluded.length?` Not optimised (smaller or without a year of prices): ${r.excluded.join(', ')}.`:''} Expected returns from one year of prices are very uncertain, so treat the maximum Sharpe mix as an illustration. ${r.disclaimer}`;
};
document.querySelector('#optimise-run').addEventListener('click',()=>void runOptimise());
const runFactors=async()=>{
  const note=document.querySelector('#factor-note'),metrics=document.querySelector('#factor-metrics'),body=document.querySelector('#factor-exposures');note.textContent='Downloading factor data and fitting…';
  let r;try{r=await modelFetch('factors',{as_of:modelState.asOf,region:document.querySelector('#factor-region').value})}catch(error){note.textContent=error.message;metrics.innerHTML='';body.innerHTML='';return}
  metrics.innerHTML=[modelTile('Alpha (a year)',toned(pct2(r.alpha_annual),r.alpha_annual),`t-statistic ${ratio(r.alpha_t_statistic)}`),modelTile('Explained by factors (R²)',pct2(r.r_squared)),modelTile('Unexplained volatility',pct2(r.residual_volatility_annual)),modelTile('Weeks used',esc(r.weeks))].join('');
  body.innerHTML=r.exposures.map(x=>`<tr><td class="text-col"><strong>${esc(x.label)}</strong>${x.significant?'':' <span class="muted">(not significant)</span>'}</td><td>${ratio(x.beta)}</td><td>${ratio(x.t_statistic)}</td><td>${signedPct(x.factor_annual_return)}</td><td>${toned(pct2(x.return_contribution),x.return_contribution)}</td></tr>`).join('');
  note.textContent=`${r.region} factors from the Kenneth French data library (through ${analyticsDate(r.factor_data_through)}), weeks of ${analyticsDate(r.window_start)} to ${analyticsDate(r.window_end)}. ${r.method} Factor returns are in US dollars; the portfolio is measured in ${r.currency}.${r.momentum_note?' '+r.momentum_note:''} ${r.disclaimer}`;
};
document.querySelector('#factor-run').addEventListener('click',()=>void runFactors());
document.querySelector('#projection-run').addEventListener('click',()=>void runProjection());
const renderModelling=asOf=>{modelState.asOf=asOf;modelState.added={};void runProjection();void runWhatif(false).then(()=>runOptimise());void runFactors()};
"""
