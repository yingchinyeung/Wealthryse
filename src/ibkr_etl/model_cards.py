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
