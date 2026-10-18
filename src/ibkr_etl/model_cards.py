"""Model cards: one short, plain description of every model the platform runs.

Banks and large managers keep a card for each model: what it does, what goes in, how it works,
where it is known to be weak, which version runs, how it is checked, and who approved it. A
figure from a model reaches people only when its card is approved, and the card travels with
the figure (the certified figure's calculation version names the model version).

The cards are code, like the certified-figures register, so a test can hold them to the code:
every model module has a card, and every card's version constant exists. Nothing here reads
data or runs a model, so it is safe to import anywhere.

    cards() -> dict[str, ModelCard]      every card, in this file's order
    card(model_id) -> ModelCard
    version(model_id) -> str             the model's version, read from its module now
    as_dict(model_id) -> dict            a card with its live version, for pages and agents

Status is "approved" (the owner approved it and its figures may be served), "draft" (built,
not yet approved; its figures stay in shadow) or "research" (lives in the research zone and
is never served).
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any

STATUSES = ("approved", "draft", "research")
SERVED_ON = ("risk", "performance", "modelling", "desk", "agents", "reports", "none")


@dataclass(frozen=True)
class ModelCard:
    model_id: str
    name: str
    module: str                     # under ibkr_etl, e.g. "risk_forecasting" or "quant.tail_risk"
    version_constant: str           # the module's version constant
    entry_points: tuple[str, ...]   # the public functions that run the model
    purpose: str
    inputs: str
    method: str
    limits: tuple[str, ...]
    validation: str                 # how it is checked: tests, backtests, comparisons
    served_on: tuple[str, ...]
    status: str = "draft"
    references: tuple[str, ...] = field(default_factory=tuple)


_CARDS: tuple[ModelCard, ...] = (
    ModelCard(
        model_id="historical-risk",
        name="Historical risk snapshot",
        module="benchmark_analytics",
        version_constant="CALCULATION_VERSION",
        entry_points=("portfolio_risk_metrics", "expected_tail_loss_contributors"),
        purpose="Return, volatility, Sharpe, drawdowns, 95% VaR and expected tail loss from the portfolio's own history.",
        inputs="Cash-flow-adjusted daily returns on benchmark dates; the risk-free rate.",
        method="Historical simulation: the 5% worst days give VaR and expected tail loss; annualised with √252.",
        limits=("Sees only past returns, so a holding bought recently barely counts.",
                "One average over the whole history; ignores today's volatility."),
        validation="tests/test_benchmark_analytics.py; Metrics guide reference calculations.",
        served_on=("risk", "agents", "reports"),
        status="approved",
    ),
    ModelCard(
        model_id="crisis-replay",
        name="Crisis replay",
        module="crisis_replay",
        version_constant="CALCULATION_VERSION",
        entry_points=("calculate_fixed_unit_replay", "refresh_crisis_replay"),
        purpose="What today's holdings would have done through past crises.",
        inputs="Holdings as of a date; adjusted price history of each holding and FX.",
        method="Fixed units of today's holdings priced through each crisis window, converted to the reporting currency.",
        limits=("Holdings without history in the window need a proxy (see crisis scenarios).",
                "Assumes no trading during the crisis."),
        validation="tests/test_crisis_replay.py.",
        served_on=("risk", "reports"),
        status="approved",
    ),
    ModelCard(
        model_id="crisis-scenarios",
        name="Beta-scaled crisis scenarios",
        module="crisis_scenarios",
        version_constant="VERSION",
        entry_points=("account_scenarios", "composite_scenarios"),
        purpose="Crisis paths for holdings that did not exist in a crisis, scaled from a market proxy by their beta.",
        inputs="Each holding's own returns and its proxy's; crisis-window proxy prices.",
        method="OLS beta on overlapping history, then the proxy's crisis path scaled by that beta.",
        limits=("A beta from calm years can understate crisis sensitivity.",),
        validation="tests/test_crisis_scenarios.py.",
        served_on=("risk",),
        status="approved",
    ),
    ModelCard(
        model_id="wealth-projection",
        name="Wealth projection",
        module="modelling",
        version_constant="MODEL_VERSION",
        entry_points=("project_wealth",),
        purpose="A range of future NAV paths.",
        inputs="The portfolio's own daily returns.",
        method="Monthly block bootstrap of overlapping 21-session windows.",
        limits=("The future is drawn from the past only.", "No contributions, withdrawals or regimes."),
        validation="tests/test_modelling.py.",
        served_on=("modelling",),
        status="approved",
    ),
    ModelCard(
        model_id="optimiser",
        name="Portfolio optimiser",
        module="modelling",
        version_constant="MODEL_VERSION",
        entry_points=("optimise", "what_if"),
        purpose="Risk contributions, the efficient frontier, and minimum-variance, risk-parity and maximum-Sharpe weights.",
        inputs="A year of daily returns of the current holdings.",
        method="Covariance shrunk 20% to the diagonal; expected returns shrunk 50% to the average holding; long only with a cap.",
        limits=("Fixed shrinkage amounts rather than estimated ones.", "Ignores trading costs."),
        validation="tests/test_modelling.py.",
        served_on=("modelling",),
        status="approved",
    ),
    ModelCard(
        model_id="factor-model",
        name="Fama-French factor model",
        module="modelling",
        version_constant="MODEL_VERSION",
        entry_points=("factor_model",),
        purpose="The portfolio's exposure to market, size, value, profitability, investment and momentum.",
        inputs="Weekly portfolio excess returns; Kenneth French's factor returns or ETF proxies.",
        method="Weekly OLS regression on the five factors plus momentum.",
        limits=("Exposures are averages over the window.", "Proxy factors are used where French data is missing."),
        validation="tests/test_modelling.py.",
        served_on=("modelling",),
        status="approved",
        references=("Fama and French (2015)", "Carhart (1997)"),
    ),
    ModelCard(
        model_id="volatility-forecast",
        name="Volatility forecast and VaR backtest",
        module="risk_forecasting",
        version_constant="MODEL_VERSION",
        entry_points=("volatility_forecast", "fit_volatility", "var_backtest"),
        purpose="Forward volatility, VaR and expected tail loss, and how past VaR held up.",
        inputs="The portfolio's daily returns.",
        method="GARCH(1,1) by maximum likelihood (EWMA when short); filtered historical simulation; Kupiec backtest.",
        limits=("One series: no view of individual holdings.",),
        validation="tests/test_risk_forecasting.py.",
        served_on=("modelling",),
        status="approved",
        references=("Bollerslev (1986)", "Barone-Adesi et al. (1999)", "Kupiec (1995)"),
    ),
    ModelCard(
        model_id="regime-outlook",
        name="Market regime outlook",
        module="risk_forecasting",
        version_constant="MODEL_VERSION",
        entry_points=("regime_outlook", "fit_hmm"),
        purpose="Which market regime we are likely in, and a one-to-three-month range for each.",
        inputs="The portfolio's daily returns.",
        method="Gaussian hidden Markov model chosen by BIC, regime-switching simulation, walk-forward score.",
        limits=("Regimes are statistical, not named events.", "Few regime changes in short histories."),
        validation="tests/test_risk_forecasting.py; its own walk-forward score.",
        served_on=("modelling",),
        status="approved",
        references=("Hamilton (1989)",),
    ),
    ModelCard(
        model_id="tail-risk",
        name="Tail risk and backtests",
        module="quant.tail_risk",
        version_constant="MODEL_VERSION",
        entry_points=("fit_gpd", "evt_var_es", "christoffersen_test", "es_backtest", "traffic_light",
                      "tail_backtest_report"),
        purpose="99% and 99.5% VaR and expected tail loss where history has too few bad days, and strict tests of past VaR and tail-loss forecasts.",
        inputs="The portfolio's daily returns.",
        method="Peaks over threshold with a generalised Pareto fit (plain, and on GARCH-standardised residuals); "
               "Christoffersen clustering tests; Acerbi-Szekely Z2 tail-loss backtest; Basel traffic light.",
        limits=("The Z2 thresholds are published for 250 days at 97.5% only; other windows use the simulated p-value.",
                "Historical VaR on a 250-day window breaches slightly more often than its level at 99%."),
        validation="tests/test_quant_tail_risk.py (reproduces the Basel 1996 table and the published Z2 thresholds).",
        served_on=("risk",),
        references=("McNeil and Frey (2000)", "Christoffersen (1998)", "Acerbi and Szekely (2014)",
                    "Basel Committee (1996)"),
    ),
    ModelCard(
        model_id="stress-engine",
        name="Linked and reverse stress tests",
        module="quant.stress_engine",
        version_constant="MODEL_VERSION",
        entry_points=("conditional_scenario", "scenario_impact", "reverse_stress", "regime_risk"),
        purpose="Shock some factors and let the rest move with them; find the most likely move that causes a given loss; risk in each market regime.",
        inputs="Factor exposures, the factor covariance, and regime probabilities from the regime model.",
        method="Gaussian conditional expectation; minimum-Mahalanobis reverse stress; probability-weighted historical VaR by regime.",
        limits=("Assumes factor moves are jointly normal, so probabilities understate fat tails.",
                "The scenario's horizon is the covariance's horizon."),
        validation="tests/test_quant_stress_engine.py.",
        served_on=("risk", "modelling"),
        references=("Breuer et al. (2009)",),
    ),
    ModelCard(
        model_id="liquidity",
        name="Liquidity risk",
        module="quant.liquidity",
        version_constant="MODEL_VERSION",
        entry_points=("average_daily_volume", "days_to_liquidate", "liquidity_adjusted_var"),
        purpose="How long each holding takes to sell, and VaR that includes the bid-ask cost of selling.",
        inputs="Positions, daily volumes and bid-ask spreads.",
        method="Days at a share of average daily volume; Bangia et al. spread adjustment to VaR.",
        limits=("Spread cost only: no price-impact or liquidation-horizon term yet.",
                "Holdings with no volume count as unsold."),
        validation="tests/test_quant_liquidity.py.",
        served_on=("risk",),
        references=("Bangia, Diebold, Schuermann and Stroughair (1999)",),
    ),
    ModelCard(
        model_id='holdings-risk',
        name='Holdings-based risk model',
        module='quant.holdings_risk',
        version_constant="MODEL_VERSION",
        entry_points=('ledoit_wolf_covariance', 'ex_ante_risk', 'factor_risk_model', 'factor_risk_decomposition', 'statistical_factors',),
        purpose="Forward volatility and tracking error from today's holdings, with each holding's and each factor's share.",
        inputs="Daily returns of today's holdings, factor returns and weights.",
        method='Ledoit-Wolf shrinkage covariance; Euler risk contributions; a factor model B F Bᵀ + D; statistical factors by principal components.',
        limits=(
                "Uses past co-movement of today's holdings; a holding with little history gets a noisy estimate.",
                'Statistical factors have no fixed economic meaning.',
        ),
        validation='tests/test_quant_holdings_risk.py.',
        served_on=('risk',),
        references=('Ledoit and Wolf (2003, 2004)', 'Menchero and Davis (2011)',),
    ),
    ModelCard(
        model_id='dynamic-correlation',
        name='Correlations that move (DCC)',
        module='quant.dynamic_correlation',
        version_constant="MODEL_VERSION",
        entry_points=('dcc_correlation',),
        purpose='How correlations between holdings change over time, and how much they rise on stress days.',
        inputs='Daily returns of the holdings and their weights.',
        method='GARCH(1,1) per holding, then DCC(1,1) fitted by composite likelihood over a seeded sample of pairs.',
        limits=(
                'Fits at most the 30 largest holdings and 10 pairs, for speed; the rest are listed as not modelled.',
                "Stress days apply today's weights to past returns.",
        ),
        validation='tests/test_quant_dynamic_correlation.py.',
        served_on=('risk',),
        references=('Engle (2002)', 'Engle, Shephard and Sheppard (2008)',),
    ),
    ModelCard(
        model_id='macro-factors',
        name='Macro factor exposures',
        module='quant.macro_factors',
        version_constant="MODEL_VERSION",
        entry_points=('align_series', 'macro_exposures', 'macro_sensitivity',),
        purpose='Exposure to rates, the dollar, CNH, oil and credit, and what a given macro shock would do.',
        inputs='Daily portfolio returns and macro levels (yields in percent, FX, oil, indices).',
        method='OLS on macro changes with Newey-West standard errors; shocks mapped through the betas with a 95% range.',
        limits=(
                'Exposures are averages over the window.',
                'Yields must be quoted in percent.',
        ),
        validation='tests/test_quant_macro_factors.py.',
        served_on=('risk', 'modelling'),
        references=('Newey and West (1987)',),
    ),
    ModelCard(
        model_id='decision-attribution',
        name='Decision attribution',
        module='quant.decision_attribution',
        version_constant="MODEL_VERSION",
        entry_points=('trade_value_added', 'timing_cost', 'skill_profile', 'counterfactual_portfolios',),
        purpose='Which buys, sells and timing calls added value, the skill profile behind them, and what simpler policies would have done.',
        inputs='IBKR trades, daily closes, positions, NAV and external flows.',
        method='Each trade against not making it over 5, 21 and 63 sessions; fill against decision price; Spearman sizing skill; buy-and-hold, quarterly-rebalance and benchmark counterfactuals.',
        limits=(
                'Price moves only: dividends and cash interest are not counted.',
                'Counterfactual portfolios pay no trading costs.',
        ),
        validation='tests/test_quant_decision_attribution.py.',
        served_on=('performance',),
        references=('Grinold and Kahn (2000)',),
    ),
    ModelCard(
        model_id='execution-analysis',
        name='Execution analysis',
        module='quant.execution_analysis',
        version_constant="MODEL_VERSION",
        entry_points=('implementation_shortfall', 'vwap_slippage', 'spread_paid', 'square_root_impact', 'fit_impact_coefficient', 'almgren_chriss_schedule',),
        purpose='What each order cost to execute, a market-impact estimate, and the best schedule for a large order.',
        inputs='Orders, fills, decision and arrival prices, quotes, daily volume and volatility.',
        method="Implementation shortfall (Kissell's expanded form); square-root impact law with a fitted coefficient; Almgren-Chriss optimal schedule.",
        limits=(
                'Needs the price at decision and arrival; without quotes around trades it falls back to closes.',
                'The impact coefficient needs at least 10 trades to fit.',
        ),
        validation='tests/test_quant_execution_analysis.py.',
        served_on=('performance', 'modelling'),
        references=('Perold (1988)', 'Almgren and Chriss (2000)', 'Almgren et al. (2005)',),
    ),
    ModelCard(
        model_id='pods',
        name='Sleeve (pod) risk management',
        module='quant.pods',
        version_constant="MODEL_VERSION",
        entry_points=('drawdown_rules', 'pod_risk_budget', 'capital_reallocation', 'exposure_limits_check',),
        purpose='Run each sleeve of a composite like a pod: risk budgets, drawdown rules, capital moved to better sleeves, factor limits.',
        inputs="Each sleeve's performance index, returns, statistics and factor exposures.",
        method='Equal-risk or inverse-volatility budgets; high-water-mark drawdown rules; shrunk information ratios with capped moves.',
        limits=(
                'Rule levels (−5% halve, −7.5% stop) are defaults until the owner sets them.',
                'Use flow-adjusted performance, not raw NAV.',
        ),
        validation='tests/test_quant_pods.py.',
        served_on=('risk', 'modelling'),
        references=('Grinold and Kahn (2000)',),
    ),
    ModelCard(
        model_id='fair-value',
        name='Fair value and relative value',
        module='quant.fair_value',
        version_constant="MODEL_VERSION",
        entry_points=('dual_listing_parity', 'etf_premium', 'engle_granger', 'spread_signal',),
        purpose="Whether a holding trades away from its fair value against a related listing, an ETF's basket, or a cointegrated pair.",
        inputs='Prices of both listings, FX, ETF NAV per share.',
        method='Parity through the conversion ratio and FX; premium z-scores and AR(1) half-lives; Engle-Granger cointegration with MacKinnon (2010) critical values.',
        limits=(
                'Hong Kong and US closes are not simultaneous; quotes taken together are better.',
                'No p-value for cointegration, only critical values.',
        ),
        validation='tests/test_quant_fair_value.py.',
        served_on=('modelling',),
        references=('Engle and Granger (1987)', 'MacKinnon (2010)',),
    ),
    ModelCard(
        model_id="attribution",
        name="Benchmark and factor attribution",
        module="quant.attribution",
        version_constant="MODEL_VERSION",
        entry_points=("brinson_fachler", "link_carino", "link_menchero", "currency_attribution",
                      "factor_attribution", "style_analysis"),
        purpose="Why the portfolio beat or trailed its benchmark: allocation, selection and currency, and how much came from factor exposures.",
        inputs="Segment weights and returns for the portfolio and benchmark, currency returns, factor or style index returns.",
        method="Brinson-Fachler effects linked over time by Carino or Menchero; Karnosky-Singer style currency split; "
               "OLS factor attribution; Sharpe returns-based style analysis.",
        limits=("Benchmark segment weights come from sector ETF proxies until constituent data is available.",
                "The currency split has no forward-premium (cash-rate) term."),
        validation="tests/test_quant_attribution.py (every set of effects sums to the excess return to 1e-12).",
        served_on=("performance",),
        references=("Brinson and Fachler (1985)", "Carino (1999)", "Menchero (2000)", "Karnosky and Singer (1994)",
                    "Sharpe (1992)"),
    ),
    ModelCard(
        model_id="skill-statistics",
        name="Is the skill real?",
        module="quant.skill_statistics",
        version_constant="MODEL_VERSION",
        entry_points=("bootstrap_confidence", "probabilistic_sharpe_ratio", "deflated_sharpe_ratio",
                      "minimum_track_record_length", "skill_summary"),
        purpose="Confidence ranges on Sharpe, alpha and information ratio, and whether the record is long enough to tell skill from luck.",
        inputs="Daily portfolio and benchmark returns; the number of strategies tried.",
        method="Stationary bootstrap ranges; probabilistic and deflated Sharpe ratios; minimum track record length.",
        limits=("Assumes the past return process is stable.", "The deflated Sharpe needs an honest count of trials."),
        validation="tests/test_quant_skill_statistics.py (reproduces the 2014 paper's worked example).",
        served_on=("performance", "agents"),
        references=("Politis and Romano (1994)", "Bailey and López de Prado (2012, 2014)"),
    ),
    ModelCard(
        model_id="optimisers",
        name="Portfolio construction (advanced)",
        module="quant.optimisers",
        version_constant="MODEL_VERSION",
        entry_points=("black_litterman", "hierarchical_risk_parity", "min_cvar_weights",
                      "mean_variance_with_costs", "robust_mean_variance"),
        purpose="Weights that start from the market and your own views, that hold up if expected returns are wrong, "
                "that minimise tail loss, and that you would actually trade to after costs.",
        inputs="Covariance, market weights, views with confidence, historical scenarios, current weights and costs.",
        method="Black-Litterman with Idzorek confidence; hierarchical risk parity; smoothed CVaR minimisation; "
               "mean-variance with an L1 cost penalty and turnover limit; ellipsoidal robust mean-variance.",
        limits=("Long only with a per-holding cap.",
                "Risk-aversion conventions differ by function (with or without the 1/2); see each docstring."),
        validation="tests/test_quant_optimisers.py (matches brute force on small cases).",
        served_on=("modelling",),
        references=("He and Litterman (1999)", "Idzorek (2005)", "López de Prado (2016)",
                    "Rockafellar and Uryasev (2000)", "Goldfarb and Iyengar (2003)"),
    ),
    ModelCard(
        model_id="goals",
        name="Goals-based planning",
        module="quant.goals",
        version_constant="MODEL_VERSION",
        entry_points=("goal_projection", "required_contribution", "monthly_regime_model"),
        purpose="The odds of reaching a target with planned contributions and withdrawals, and the contribution that reaches it.",
        inputs="Start value, monthly return pool or the regime model, contributions, withdrawals, target and horizon.",
        method="Monte Carlo with block bootstrap or regime switching; required contribution solved exactly on common random numbers.",
        limits=("No inflation adjustment.", "Running out of money is final on that path."),
        validation="tests/test_quant_goals.py.",
        served_on=("modelling",),
        references=("Hamilton (1989)",),
    ),
    ModelCard(
        model_id="rebalancing",
        name="Rebalancing bands",
        module="quant.rebalancing",
        version_constant="MODEL_VERSION",
        entry_points=("no_trade_bands", "rebalance_plan"),
        purpose="When to trade: no-trade bands that weigh drift against costs, and a plan that trades only what is outside them.",
        inputs="Covariance, target weights, costs, risk aversion, prices and dividend withholding.",
        method="Cube-root band rule for proportional costs; trade back to the band edge.",
        limits=("Holdings are treated one at a time.", "Board-lot rounding is left to the caller."),
        validation="tests/test_quant_rebalancing.py (simulation confirms the trading rate).",
        served_on=("modelling",),
        references=("Rogers (2001)", "Janeček and Shreve (2004)"),
    ),
    ModelCard(
        model_id="calibration",
        name="Forecast track record",
        module="quant.calibration",
        version_constant="MODEL_VERSION",
        entry_points=("interval_coverage", "quantile_calibration", "pit_histogram", "conformal_factor",
                      "widen_interval", "probability_reliability"),
        purpose="Whether each forecast's ranges and probabilities came true as often as they said, and how much to widen them if not.",
        inputs="Past forecasts (ranges, quantiles, probabilities) and what happened.",
        method="Binomial coverage tests, PIT histograms (Berkowitz transform), split conformal widening, Brier score decomposition.",
        limits=("Needs at least 20 past forecasts.", "Conformal widening assumes the past errors resemble future ones."),
        validation="tests/test_quant_calibration.py.",
        served_on=("risk", "modelling"),
        references=("Vovk et al. (2005)", "Lei et al. (2018)", "Murphy (1973)", "Berkowitz (2001)"),
    ),
    ModelCard(
        model_id='desk-risk',
        name='Desk risk and P&L explain',
        module='quant.desk_risk',
        version_constant="MODEL_VERSION",
        entry_points=('intraday_pnl', 'pnl_explain', 'limit_check', 'desk_snapshot',),
        purpose="Today's P&L from live positions and quotes, where it came from (market, factors, FX, stock-specific) and every live metric against its limit.",
        inputs='live_positions, live_quotes, live_executions and cash from live_account_values; FX rates now and at the prior close; factor betas; limits.',
        method='P&L against the prior close with the FX part on the opening value; fills today from their price; '
               'factor explain through time-series betas, parts adding up exactly; limit usage with green, amber (80%) and red.',
        limits=(
                'The collector writes a quote only when it changes, so a quiet holding or a closed market reads as stale.',
                'Commissions are left out, and fills today are converted at the current FX rate.',
                'Intraday style-factor returns are rarely available; the explain is then market and specific only.',
        ),
        validation='tests/test_quant_desk_risk.py.',
        served_on=('desk',),
        references=('Grinold and Kahn (2000)',),
    ),
    ModelCard(
        model_id='pre-trade',
        name='Pre-trade check',
        module='quant.pre_trade',
        version_constant="MODEL_VERSION",
        entry_points=('pre_trade_check',),
        purpose='What proposed orders would do to risk, tail loss, concentration, factor exposures, liquidity and limits, and what they would cost, before anything is placed.',
        inputs="Today's positions and prices, the proposed orders, daily return history, optionally a covariance or factor model, daily volumes, spreads and limits.",
        method='Orders applied with cash taking the difference; Ledoit-Wolf or factor-model ex-ante volatility; historical VaR and '
               'expected tail loss over the same history; half-spread plus square-root impact cost; limits before and after.',
        limits=(
                'A hypothetical check, not advice; it places nothing.',
                'Holdings without return history count at 0% risk and are listed.',
                'Each order is costed on its own at the default impact coefficient unless a fitted one is passed.',
        ),
        validation='tests/test_quant_pre_trade.py.',
        served_on=('desk', 'modelling', 'agents'),
        references=('Ledoit and Wolf (2004)', 'Almgren et al. (2005)',),
    ),
    ModelCard(
        model_id='crowding',
        name='Crowding and concentration',
        module='quant.crowding',
        version_constant="MODEL_VERSION",
        entry_points=('ownership_overlap', 'correlation_clusters', 'concentration',),
        purpose='How much of the portfolio sits in names large funds also hold, which funds look most like it, which holdings move as one bet, and how concentrated it is.',
        inputs='Portfolio weights, 13F holdings of large funds with shares outstanding, and daily returns of the holdings.',
        method='Ownership share and top-holder crowding score; cosine similarity of fund weights; average-linkage clustering '
               'on correlation distance √(½(1 − ρ)); Herfindahl index and effective number of holdings.',
        limits=(
                '13F filings lag a quarter by up to 45 days and show US-listed longs only.',
                'The cluster cut is on average distance, so a cluster can average a little below the chosen correlation.',
        ),
        validation='tests/test_quant_crowding.py.',
        served_on=('risk',),
        references=('Mantegna (1999)', 'Herfindahl (1950)',),
    ),
)


def cards() -> dict[str, ModelCard]:
    return {item.model_id: item for item in _CARDS}


def card(model_id: str) -> ModelCard:
    try:
        return cards()[model_id]
    except KeyError:
        raise KeyError(f"No model card {model_id!r}; known: {', '.join(cards())}") from None


def version(model_id: str) -> str:
    item = card(model_id)
    module = importlib.import_module(f"ibkr_etl.{item.module}")
    return str(getattr(module, item.version_constant))


def as_dict(model_id: str) -> dict[str, Any]:
    item = card(model_id)
    return {
        "model_id": item.model_id, "name": item.name, "version": version(model_id), "status": item.status,
        "purpose": item.purpose, "inputs": item.inputs, "method": item.method, "limits": list(item.limits),
        "validation": item.validation, "served_on": list(item.served_on), "references": list(item.references),
        "module": f"ibkr_etl.{item.module}", "entry_points": list(item.entry_points),
    }
