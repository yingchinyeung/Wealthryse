"""Metrics guide entries for the quant analytics pages (web_quant.py).

They live apart from :mod:`ibkr_etl.metrics_guide` so that the guide, its anchors and the
agents' guide search stay exactly as they are while ``QUANT_ANALYTICS_PAGES`` is unset:
:func:`addition` returns only the sections of the pages switched on for a viewer, and the
Metrics guide page renders them through ``render_metrics_guide(additions)``.

The audit (metrics_guide_audit.py) reads ``LABEL_SOURCES``, ``LABEL_MAP`` and
``WATCHED_SOURCES`` below, so a new quant tile without a definition, or a changed engine
calculation, shows up there like any other.
"""
from __future__ import annotations

from .metrics_guide import METRIC_ANCHORS as BASE_ANCHORS
from .metrics_guide import GuideAddition, MetricDefinition, MetricSection, metric_anchor

PAGES = ("performance", "risk", "modelling")
NOT_ADVICE = "It is not a forecast, a guarantee or investment advice."

_D = MetricDefinition

DEFINITIONS: dict[str, tuple[MetricDefinition, ...]] = {
    "performance": (
        _D(
            "Skill and luck",
            "How sure we can be that the portfolio's risk-adjusted return is skill rather than luck: a range for the Sharpe ratio, alpha and information ratio, and a plain verdict.",
            "Sharpe ratio, alpha (OLS on the primary benchmark, both over cash) and information ratio from daily returns, annualised with 252 days. A stationary bootstrap (Politis and Romano 1994) builds 2,000 resampled histories from runs of consecutive days of random length averaging 10 days, wrapping round the end of the record, with portfolio and benchmark taken from the same days. Each range is the middle 90% of the resampled values (percentile method). The verdict uses the published 95% bar: the probabilistic Sharpe ratio, then the deflated Sharpe ratio, then the alpha range. Model card skill-statistics (quant-skill-statistics-v1), built overnight in quant-analytics-v1.",
            "A range that stays above zero is evidence of a real edge; one that straddles zero means the record cannot yet tell skill from luck. The model assumes the past return process is stable, and the figures describe the past record. " + NOT_ADVICE,
        ),
        _D(
            "Probabilistic Sharpe ratio",
            "The chance that the portfolio's true Sharpe ratio is above zero, given how long the record is and how lopsided and fat-tailed its daily returns are.",
            "Bailey and López de Prado (2012): PSR = Φ[SR·√(n − 1) / √(1 − γ3·SR + (γ4 − 1)/4·SR²)], with SR the daily Sharpe ratio over cash, n the number of daily returns, γ3 their skewness and γ4 their kurtosis (3 for a normal distribution).",
            "95% or more is the usual bar for calling a Sharpe ratio reliably positive. A short record, a few extreme days or returns skewed to the downside lower it. It assumes the return process is stable and says nothing about the future. " + NOT_ADVICE,
        ),
        _D(
            "Deflated Sharpe ratio",
            "The probabilistic Sharpe ratio after allowing for the number of strategies tried before this one was chosen, so a lucky pick from many is not mistaken for skill.",
            "Bailey and López de Prado (2014): the same formula as the probabilistic Sharpe ratio, measured against the Sharpe ratio the best of N skill-less strategies would reach by chance, E[max SR] = √V·[(1 − γ)Φ⁻¹(1 − 1/N) + γΦ⁻¹(1 − 1/(N·e))], with γ the Euler-Mascheroni constant and V the variance of the trials' Sharpe ratios.",
            "The overnight build counts one strategy (this portfolio), so the deflated figure equals the probabilistic one and the page shows it only when more trials are recorded. The figure needs an honest count of trials to mean anything. " + NOT_ADVICE,
        ),
        _D(
            "Track record needed",
            "How many years of returns at today's Sharpe ratio, skewness and kurtosis it takes before the probabilistic Sharpe ratio reaches 95%.",
            "Minimum track record length (Bailey and López de Prado 2012): MinTRL = 1 + (1 − γ3·SR + (γ4 − 1)/4·SR²)·(Φ⁻¹(0.95)/SR)² daily returns, divided by 252 for years. There is no answer when the Sharpe ratio is zero or below.",
            "If the record is shorter than this, the result could still be luck however good it looks. A higher Sharpe ratio needs a shorter record; fat tails need a longer one. " + NOT_ADVICE,
        ),
        _D(
            "Decision value added",
            "What each buy and sell earned or saved against not making it, over the next 5, 21 and 63 trading sessions, after commission.",
            "Each trade is replayed against not making it. A buy earns the move from its fill price to the holding's close h sessions later, times the quantity, less commission; a sell earns the move it avoided (fill price minus the later close). Decision return is that gain before commission divided by the traded value. Price moves only: dividends and interest on the cash are not counted. Horizons that run past the last close are marked unavailable and left out of the totals. Model card decision-attribution (decision-attribution-v1).",
            "Positive means the trades, taken together, did better than standing still over that horizon. A few large trades can dominate, so read it beside the hit rate. It counts price moves only and looks back. " + NOT_ADVICE,
        ),
        _D(
            "Hit rate and payoff ratio",
            "The share of trades that added value over the horizon shown (normally 21 sessions), and how big the average win was against the average loss.",
            "Hit rate is the share of trades whose value added after commission over the horizon is above zero. Payoff ratio is the average winning trade's value added divided by the size of the average losing trade's.",
            "A low hit rate can still pay if the wins are much bigger than the losses (payoff ratio above 1), and a high hit rate can lose money if they are smaller. Both describe past trades only. " + NOT_ADVICE,
        ),
        _D(
            "Sizing skill",
            "Whether the larger positions went on to do better than the smaller ones.",
            "Spearman rank correlation between each holding's size and its return over the next horizon, across the holdings on each date, averaged over dates. Above zero means bigger positions did better.",
            "Near zero means position sizes have not lined up with later returns. It ranks sizes, so one very large position does not dominate it. " + NOT_ADVICE,
        ),
        _D(
            "Sell discipline",
            "Whether the holdings sold went on to do worse than the holdings kept.",
            "For each sale, the sold holding's return after the sale is compared with the size-weighted return of the holdings kept that day, over the same horizon. The advantage is the kept return minus the sold return, averaged over sales; its hit rate is the share of sales where it was positive.",
            "A positive advantage means sales were, on average, of holdings that went on to lag. Sales made for cash needs or tax reasons count too. " + NOT_ADVICE,
        ),
        _D(
            "Timing cost",
            "How much the fill price cost against the price when the decision was taken, before commission.",
            "Timing cost is the fill price minus the decision price, times the quantity, for a buy (the reverse for a sell), so a positive number is a cost. The decision price is the trade's own when recorded, else the previous close. Basis points are of the value at the decision price; totals in basis points are weighted by that value.",
            "It mixes the market's move between the decision and the fill with the cost of trading itself; without quotes at the time of each trade the two cannot be separated. " + NOT_ADVICE,
        ),
        _D(
            "Counterfactual portfolios",
            "What the portfolio would be worth had it kept its starting holdings, rebalanced to them each quarter, or held the benchmark, against what it is actually worth.",
            "Every path starts from the actual NAV on 1 January. Buy-and-hold keeps the start quantities; quarterly rebalance resets to the start weights (cash included) at each calendar quarter's last NAV date, without trading costs; the benchmark path holds only the benchmark. Contributions and withdrawals are added on the day the actual NAV received them, in proportion to what each path holds. Cash earns nothing and dividends are not counted.",
            "A positive actual-minus figure means the decisions actually taken did better than that alternative. The alternatives pay no trading costs, so they flatter standing still slightly. " + NOT_ADVICE,
        ),
        _D(
            "Currency effect",
            "How much of the portfolio's return against its benchmark came from currency moves rather than from the markets and holdings chosen, month to date and year to date.",
            "Karnosky-Singer (1994) style attribution in the reporting currency. Each market's return is split into its local return and the currency's move. Currency allocation is the difference in currency weights (portfolio minus benchmark) times each currency's return; currency compounding is each currency's move on the local gain or loss. The currency effect shown is currency allocation plus currency compounding. Model card attribution (quant-attribution-v1).",
            "Positive means being over- or under-weight in currencies helped. Cash-rate differences between currencies are not split out, so a hedge earns the spot move only. Benchmark segment weights come from sector ETF proxies until constituent data is available. " + NOT_ADVICE,
        ),
        _D(
            "Local market allocation and selection",
            "The part of the return against the benchmark that came from the markets chosen (allocation) and from the holdings chosen within them (selection), in local currency.",
            "Brinson-Fachler on local returns: allocation is (portfolio weight − benchmark weight) × (benchmark segment local return − benchmark local return); selection is the benchmark weight × (portfolio segment local return − benchmark segment local return); interaction is the weight difference times the return difference. With the currency effect they add up to the excess return.",
            "Allocation rewards being in the right markets, selection picking the right holdings in them. The segments use sector ETF proxies for the benchmark until constituent data is available. " + NOT_ADVICE,
        ),
    ),
    "risk": (
        _D(
            "Ex-ante volatility",
            "How volatile today's holdings would be over a year, from how they have moved together in the past.",
            "Annualised volatility √(wᵀΣw) of today's weights under the Ledoit-Wolf shrunk covariance matrix of the holdings' daily returns. Cash left out of the weights is treated as riskless. With a benchmark the same is done for the active weights (portfolio minus benchmark), whose volatility is the ex-ante tracking error. Model card holdings-risk (holdings-risk-v1).",
            "It answers \"how risky is what I hold now\", unlike realised volatility, which reflects what was held in the past. A holding with little history gets a noisy estimate. " + NOT_ADVICE,
        ),
        _D(
            "Holding risk contribution",
            "How much of the portfolio's volatility each holding carries, which can differ a lot from its weight.",
            "A holding's marginal contribution is how much volatility rises per unit of extra weight, (Σw)ᵢ/σ; its contribution is weight × marginal contribution (the Euler allocation), and the contributions add up to the volatility. Share is contribution divided by volatility.",
            "A holding with a large share of risk relative to its weight is volatile or moves with the rest; a negative contribution means it offsets the others. " + NOT_ADVICE,
        ),
        _D(
            "Covariance shrinkage",
            "How far the holdings' sample covariance matrix was pulled toward a simple target to make it more reliable.",
            "Ledoit and Wolf (2004): the sample covariance of the holdings' demeaned daily returns is shrunk toward a scaled identity matrix (the average variance on the diagonal, zero elsewhere). The intensity is the weight on the target that minimises the expected squared error of the matrix, estimated from the same data: 0 keeps the sample matrix, 1 uses the target. Annualised × 252.",
            "A high intensity means the history is short for the number of holdings, so the sample correlations are noisy and the risk figures lean on the simple target. " + NOT_ADVICE,
        ),
        _D(
            "Statistical risk factors",
            "The few hidden common drivers that explain most of how the holdings move together.",
            "Principal components of the correlation matrix of the holdings' daily returns (three components). Each component's eigenvalue divided by the number of holdings is the share of total variance it explains; loadings are each holding's correlation with the component, with signs set so the largest loading is positive.",
            "A first factor that explains a large share means the holdings largely rise and fall together. Statistical factors have no fixed economic meaning; read the loadings to see which holdings drive each one. " + NOT_ADVICE,
        ),
        _D(
            "EVT VaR and expected shortfall",
            "How much the portfolio could lose on a very bad day (99% and 99.5%), from a model of the extreme tail, beside the plain historical estimate.",
            "Peaks over threshold (McNeil and Frey 2000): daily losses beyond their 90% quantile are fitted with a generalised Pareto distribution by maximum likelihood, and VaR and expected shortfall come from its tail formulas. The conditional figures fit the same tail to returns divided by their GARCH volatility and scale it to tomorrow's forecast. Historical VaR and expected shortfall are the plain quantile and tail average of the same returns. Model card tail-risk (tail-risk-v1).",
            "VaR is the loss exceeded on about 1 day in 100 (or 200); expected shortfall is the average loss on those days. The extreme-value fit reaches further into the tail than history, which has only a handful of very bad days to go on. " + NOT_ADVICE,
        ),
        _D(
            "Tail backtest traffic light",
            "Whether past daily VaR figures were breached about as often as they should have been, graded green, amber or red as the Basel rules do.",
            "Historical simulation: each day's VaR is the matching quantile of all earlier daily returns, refitted every 21 sessions after the first 250. Kupiec's test asks whether the number of breaches is right. The Basel traffic light counts breaches of the 99% VaR in the last 250 days: green up to 4, amber 5 to 9, red from 10.",
            "Green means the VaR has held up; amber that it may be too low; red that it has been too low. Historical VaR on a 250-day window breaches slightly more often than its level at 99%. " + NOT_ADVICE,
        ),
        _D(
            "Breach clustering test",
            "Whether VaR breaches came in bunches, which means the risk figure is slow to react when markets turn.",
            "Christoffersen (1998) independence test on the daily breach series: the likelihood of breaches that depend on whether yesterday was a breach, against breaches that do not. Conditional coverage combines it with Kupiec's count test. A p-value below 5% marks clustering.",
            "Clustered breaches mean losses beyond VaR arrive together in stressed spells, which a volatility-aware model handles better than plain history. " + NOT_ADVICE,
        ),
        _D(
            "Expected shortfall backtest (Z2)",
            "Whether losses beyond VaR were as large as expected shortfall said they would be.",
            "Acerbi and Szekely (2014): Z2 = 1 − Σ (loss on each breach day ÷ that day's expected shortfall) / (number of days × tail probability). Zero is a perfect fit; negative means losses beyond VaR were larger or more frequent than predicted. At 97.5% over 250 days the published thresholds apply (amber below −0.70, red at −1.8 or below); elsewhere the zone comes from a simulated p-value.",
            "A clearly negative Z2 means the expected shortfall understated the tail. " + NOT_ADVICE,
        ),
        _D(
            "Risk by market regime",
            "How large a bad day's loss has been in calm, normal and stressed periods of the portfolio's own history.",
            "Historical VaR and expected shortfall (95% and 99%) within each regime of the portfolio's regime model: every past day counts toward a regime in proportion to the chance it was in that regime. Effective days are the sum of those chances. Model card stress-engine (stress-engine-v1).",
            "The stressed regime's figures show what a bad day looks like when markets are rough, which a single all-days VaR averages away. A regime with few effective days, or few in its tail, gives a rough figure. " + NOT_ADVICE,
        ),
        _D(
            "Moving correlation (DCC)",
            "How closely the largest holdings move together now, against their long-run average, from a model in which correlation changes day by day.",
            "Dynamic conditional correlation, DCC(1,1) of Engle (2002), on the five largest holdings (10 pairs). Each holding's daily returns are divided by their GARCH(1,1) volatility; the correlation of those standardised returns then moves each day, a controlling how far one day's co-movement shifts it and b how long the shift lasts, and it reverts to the long-run correlation. The average is weighted by each pair's product of weights. Half-life is how many days a shift takes to fade by half. Model card dynamic-correlation (dynamic-correlation-v1).",
            "Correlation above its long-run level means diversification is weaker than usual right now. At most the 30 largest holdings and 10 pairs are fitted, for speed. " + NOT_ADVICE,
        ),
        _D(
            "Correlation on stress days",
            "How correlated the holdings were on the worst days for today's portfolio, against the calmer days.",
            "Stress days are the 5% of days on which today's weights would have lost the most; calm days are the rest. The figure is the weighted average DCC correlation on each set of days, each day using only earlier data.",
            "If correlation is much higher on stress days, diversification tends to fail exactly when it is needed. Stress days apply today's weights to past returns. " + NOT_ADVICE,
        ),
        _D(
            "Herfindahl index and effective number",
            "How concentrated the portfolio is, as a single number, and how many equal-sized holdings would be just as concentrated, by holding and by sector.",
            "Each holding's size as a share of the gross (shorts count by their size). The Herfindahl index is the sum of the squared shares, from 1/N for N equal holdings to 1 for one; the effective number is one over it. The same is done with sectors in place of holdings. Model card crowding (crowding-v1).",
            "An effective number well below the count of holdings means a few positions dominate. " + NOT_ADVICE,
        ),
        _D(
            "Top 5 share of gross",
            "The share of the portfolio's gross exposure held in its five largest positions.",
            "The five largest absolute weights added up, divided by the gross weight (the sum of absolute weights, so shorts count by their size).",
            "Unlike Top 5 weight, which is a share of NAV, this is a share of gross exposure, so it stays comparable when the portfolio uses leverage or holds shorts. " + NOT_ADVICE,
        ),
        _D(
            "Correlation clusters",
            "Groups of holdings that move so closely together that they are, in effect, one bet.",
            "Correlations of the holdings' daily returns are turned into distances √(½(1 − ρ)) (Mantegna 1999), then average-linkage hierarchical clustering joins the two closest groups, the distance between groups being the average distance between their members, until no two groups are within the distance of a 0.7 correlation. A group's weight is the net of its members'.",
            "A cluster with a large combined weight is a concentration that a count of holdings hides. The cut is on average distance, so a cluster can average a little below 0.7. " + NOT_ADVICE,
        ),
    ),
    "modelling": (
        _D(
            "Advanced optimisers",
            "Five more ways to weight the same holdings, shown beside the Optimisation page's three, each with its expected return, volatility and tail loss.",
            "All five use the holdings, daily prices and expected returns of the Optimisation page: the covariance of a year of daily price returns, annualised and shrunk 20% toward its diagonal, and each holding's historical average return shrunk 50% toward the average holding, less the risk-free rate. Long only and fully invested, with the chosen cap on one holding (hierarchical risk parity is uncapped). Tail loss is the average of the worst 5% of the year's daily returns at those weights. Model card optimisers (quant-optimisers-v1).",
            "Each method answers a different question; none of them knows the future. The result is a hypothetical model built from historical data. " + NOT_ADVICE,
        ),
        _D(
            "Black-Litterman weights",
            "Weights that start from the portfolio as it is and move only as far as your views, and your confidence in them, justify.",
            "He and Litterman (1999). Implied excess returns π = δΣw from today's sleeve weights with risk aversion δ (2.5 by default); the prior on expected returns has covariance τΣ with τ = 0.05. A view is an expected excess return for a holding, or for one holding over another, with a confidence; its uncertainty follows Idzorek (2005), ω = (1 − confidence)/confidence × τ·pΣp'. The weights maximise μ'w − (δ/2)w'Σw, long only, fully invested and capped.",
            "With no views the weights are today's (within the cap): the model only moves where a view tells it to. Strong views at high confidence can move it a long way. " + NOT_ADVICE,
        ),
        _D(
            "Hierarchical risk parity weights",
            "Weights that spread risk across groups of holdings that move alike, without needing expected returns.",
            "López de Prado (2016): holdings are clustered by correlation distance √(½(1 − ρ)) (single linkage), ordered so similar holdings sit together, then weight is split top-down between the two halves of each cluster in inverse proportion to their variance. Long only, fully invested, no cap.",
            "Robust to noisy return estimates because it uses none; a holding unlike the others can still get a large weight. " + NOT_ADVICE,
        ),
        _D(
            "Minimum tail loss weights",
            "Weights that make the average loss on the worst 5% of days in the past year as small as possible.",
            "Minimum conditional value at risk (Rockafellar and Uryasev 2000) over the year's daily returns as scenarios: the average loss in the worst 5% of scenarios is minimised, long only, fully invested and capped. Solved by projected accelerated gradient on a smoothed tail loss; the weights reported have the lowest exact tail loss found.",
            "It targets bad days directly rather than volatility, so it can favour holdings that held up in last year's sell-offs, which may not repeat. " + NOT_ADVICE,
        ),
        _D(
            "Mean-variance weights after costs",
            "The best balance of expected return and risk once the cost of trading from today's weights is charged.",
            "Maximise expected excess return − (δ/2) × variance − cost × turnover, starting from today's sleeve weights, where turnover is buys plus sells as a share of the sleeve and its cost (in basis points) is charged once against a year of expected return. An optional limit caps the turnover. Solved by accelerated proximal gradient, which shrinks small trades to zero.",
            "Higher costs leave more holdings untouched. Net expected return is the expected return less the trading cost. " + NOT_ADVICE,
        ),
        _D(
            "Robust mean-variance weights",
            "Mean-variance weights that hold up if the expected returns are wrong, by planning for the worst case within the estimates' likely error.",
            "Goldfarb and Iyengar (2003); Ceria and Stubbs (2006): expected returns may be wrong anywhere in an ellipsoid of radius κ shaped by their estimation error (the covariance divided by the years of data), with κ² the chi-square quantile at the chosen confidence with one degree of freedom per holding, so the ellipsoid holds the true returns with that confidence. The weights maximise the worst-case return in it − (δ/2) × variance. Plain weights are the same model with κ = 0.",
            "Higher confidence means a wider ellipsoid and weights closer to minimum variance. Worst-case return is what the weights earn if the estimates are as wrong as the confidence allows. " + NOT_ADVICE,
        ),
        _D(
            "No-trade band",
            "How far each holding's weight can drift from its target before trading it back is worth the cost.",
            "Half-width δ = (3ε·w²(1 − w)²/(2γ))^(1/3) for each holding, the cube-root rule for proportional costs (Rogers 2001; Janeček and Shreve 2004), with one-way cost ε and risk aversion γ. The expected time from target to the edge is δ² divided by the yearly variance of the weight's drift, from the covariance. Model card rebalancing (quant-rebalancing-v1).",
            "Wider bands mean less trading. Holdings are treated one at a time, so bands do not allow for one trade offsetting another. " + NOT_ADVICE,
        ),
        _D(
            "Rebalancing plan",
            "The trades that bring each holding back inside its no-trade band toward a target mix, and what they would cost.",
            "Holdings inside their band are left alone; a holding outside it is traded back to the band's nearest edge, not to its target, which is the optimal policy when costs are proportional to the value traded. Cost is the chosen basis points of each trade's value. Drift left is the weight after the trades minus the target. Cash takes the net of the trades and their cost. Share counts are not rounded to board lots.",
            "The saving against a full rebalance is the cost avoided by trading only to the band's edge. The plan is a model: it places no orders. " + NOT_ADVICE,
        ),
        _D(
            "Chance of reaching the goal",
            "The share of simulated futures in which the portfolio reaches a target value by a date, with planned saving or withdrawals, under two models of the markets.",
            "Each of 2,000 simulated paths draws past monthly returns of the portfolio (overlapping 21-session windows) at random, applies each to the wealth, then adds that month's contribution and takes its withdrawal. The regime model instead moves between the portfolio's fitted market regimes with the fitted monthly odds and draws a past month from the regime it is in. A path that cannot meet a withdrawal runs out and stays at zero. The target counts as reached when wealth at the end is at or above it. Amounts are nominal, with no inflation adjustment. Model card goals (quant-goals-v1).",
            "A range of outcomes built from this portfolio's own history, not a promise. If the two models disagree, the regime model is giving weight to how long bad spells last. " + NOT_ADVICE,
        ),
        _D(
            "Monthly saving needed",
            "The extra amount to save at the end of every month for the goal to be reached in the chosen share of simulated futures.",
            "The smallest monthly amount for which at least the chosen share (80% by default) of the same 2,000 simulated paths reach the target. Every candidate amount meets the same simulated markets, so the answer does not move with the random draws.",
            "Zero means the plan is already on track at that level of confidence. Amounts are nominal, with no inflation adjustment. " + NOT_ADVICE,
        ),
        _D(
            "Linked factor scenario",
            "A factor stress test in which the factors you do not set move as they usually have when the ones you set move, instead of staying still.",
            "The factors you shock keep their shocks; every other factor moves by its expected value given them, E[u | s] = Σus Σss⁻¹ s (Kupiec 1998), from the covariance of weekly factor returns over the factor model's window, scaled to a month. The portfolio's move is each factor's move times its fitted beta, summed. \"Named shocks only\" is the Stress test page's figure, with the other factors held still. Model card stress-engine (stress-engine-v1).",
            "The gap between the two figures is what the other factors usually add. It assumes factor moves are jointly normal and uses only the part of returns the factor model explains. " + NOT_ADVICE,
        ),
        _D(
            "Reverse stress test",
            "The most likely combination of factor moves in a month that would cause a loss of the size you choose, and how unusual it would be.",
            "Among all factor moves whose effect through the portfolio's betas is the chosen loss, the one nearest normal in Mahalanobis distance (Breuer et al. 2009), from the monthly factor covariance. Each factor moves in proportion to its covariance with the portfolio. Probability of a loss at least this large is Φ(−loss/√(β'Σβ)).",
            "A loss that needs only modest factor moves is a nearer risk than one that needs extreme ones. The probabilities are rough: real markets have fatter tails than the normal. " + NOT_ADVICE,
        ),
        _D(
            "Implementation shortfall",
            "What trading cost against a paper portfolio that traded at the price when each decision was taken, split into delay, trading, commission and missed trades.",
            "Perold (1988): the gain of a paper portfolio that traded the whole order at the decision price, less the gain of the real one, valued at the final price. Delay cost is the decision-to-arrival move on the quantity filled; trading cost the fills against the arrival price; opportunity cost the move from the decision price to the final price on the quantity not filled. Fills of one holding on one side on one day count as one order. The decision price is the previous close and the final price the day's close. Positive means cost. Basis points are of the order's value at the decision price. Model card execution-analysis (execution-analysis-v1).",
            "Without quotes around each trade it falls back to closes, so delay and market moves are mixed in. It looks back at trades made. " + NOT_ADVICE,
        ),
    ),
}

CLIENT_CALCULATIONS = {
    "Skill and luck": "We replay the portfolio's own history thousands of times in a shuffled order, keeping runs of neighbouring days together, and see how much the Sharpe ratio, alpha and information ratio move. Each range covers the middle 90% of those replays.",
    "Probabilistic Sharpe ratio": "We take the Sharpe ratio and ask how likely it is to be truly above zero once the length of the record and the shape of the returns, including any extreme days, are allowed for.",
    "Deflated Sharpe ratio": "We raise the bar from zero to the best result that luck alone would give among all the strategies tried, then ask the same question.",
    "Track record needed": "We work out how long a record at today's Sharpe ratio must be before we could be 95% sure it is above zero.",
    "Decision value added": "For each trade we compare what happened with what would have happened had the trade not been made, 5, 21 and 63 trading days later, and add up the difference after commission.",
    "Hit rate and payoff ratio": "We count the share of trades that added value after 21 trading days, and compare the average win with the average loss.",
    "Sizing skill": "On each date we rank the holdings by size and by their return over the following weeks, and check whether the two rankings agree.",
    "Sell discipline": "For each sale we compare what the sold holding did next with what the holdings kept did over the same weeks.",
    "Timing cost": "We compare each fill price with the price when the decision was taken, normally the previous close, and add up the difference.",
    "Counterfactual portfolios": "From 1 January we follow three alternatives with the same money and the same deposits and withdrawals: keep the starting holdings, rebalance to them each quarter, or hold the benchmark. We compare each with the actual value.",
    "Currency effect": "We split the return of each market into the local market's move and the currency's move, then measure what the portfolio gained or lost from holding different currencies than the benchmark.",
    "Local market allocation and selection": "Using local-currency returns, we measure what came from choosing markets in different proportions to the benchmark and what came from the holdings chosen within each market.",
    "Ex-ante volatility": "We apply today's weights to how the holdings have moved, alone and together, over the past year, and work out how much the whole portfolio would swing in a typical year.",
    "Holding risk contribution": "We split that volatility between the holdings so the parts add up to the total.",
    "Covariance shrinkage": "Before using past co-movements we blend them with a simpler pattern, more so when the history is short, because noisy estimates exaggerate both risk and diversification.",
    "Statistical risk factors": "We look for the few common patterns that explain most of how the holdings move together and show how strongly each holding follows each one.",
    "EVT VaR and expected shortfall": "We fit a curve to the portfolio's worst days so we can estimate losses rarer than history shows clearly, and show the plain historical figures beside it.",
    "Tail backtest traffic light": "We go back through history, work out each day's VaR from earlier data only, and count how often the next day's loss went beyond it.",
    "Breach clustering test": "We check whether days that broke the VaR tended to follow other days that broke it.",
    "Expected shortfall backtest (Z2)": "On the days that broke the VaR we compare the actual loss with the average loss expected shortfall predicted.",
    "Risk by market regime": "We sort past days into calm, normal and stressed periods using the portfolio's own regime model, then measure bad-day losses within each.",
    "Moving correlation (DCC)": "We measure how closely the largest holdings move together, letting that change from day to day, and compare today's level with the long-run average.",
    "Correlation on stress days": "We pick the days that would have hurt today's portfolio most and measure how closely the holdings moved together on them.",
    "Herfindahl index and effective number": "We square each holding's (or sector's) share of the portfolio and add them up; one over that total is the number of equal holdings that would be as concentrated.",
    "Top 5 share of gross": "We add up the five largest positions, counting shorts by their size, as a share of all positions added up the same way.",
    "Correlation clusters": "We group holdings whose prices moved closely together over the past year, joining the closest first, and stop once groups would join holdings correlated less than 0.7.",
    "Advanced optimisers": "We take the same holdings and price history as the Optimisation page and find five more mixes, each by a different rule, then show their expected return, risk and worst-day losses side by side.",
    "Black-Litterman weights": "We start from the expected returns that would make today's mix the best one, blend in any views you give at the confidence you give, and find the best mix for the blended returns.",
    "Hierarchical risk parity weights": "We group holdings that move alike, then split the money between and within groups so each part carries a similar share of risk.",
    "Minimum tail loss weights": "We search for the mix whose average loss on the worst 5% of last year's days would have been smallest.",
    "Mean-variance weights after costs": "We look for the best trade-off between expected return and risk, charging the cost of every trade away from today's mix.",
    "Robust mean-variance weights": "We assume the expected returns could be wrong by as much as their estimation error allows, and choose the mix that does best in the worst of those cases.",
    "No-trade band": "For each holding we work out how far its weight can drift before the benefit of trading back outweighs the cost.",
    "Rebalancing plan": "We trade only the holdings that have drifted outside their band, and only back to the edge of the band, then add up the trades and their cost.",
    "Chance of reaching the goal": "We simulate 2,000 futures from the portfolio's own past monthly returns, with your saving or withdrawals, and count the share that end at or above the target. The regime version also follows how calm and stressed spells have come and gone.",
    "Monthly saving needed": "We find the smallest extra monthly saving that lifts the share of simulated futures reaching the target to the chosen level.",
    "Linked factor scenario": "You set a move for one or more factors; we move the other factors as they have usually moved alongside them, then pass all the moves through the portfolio's factor sensitivities.",
    "Reverse stress test": "You choose a loss; we find the least unusual combination of factor moves that would cause it and estimate how often a loss that large happens.",
    "Implementation shortfall": "For each trade we compare what it cost with a paper trade at the previous close, and split the difference into the market's move before the trade, the price paid against the open market, commission and any part left unfilled.",
}

ALIASES = {
    "Skill and luck": ("bootstrap", "confidence interval", "Sharpe range", "is it luck"),
    "Probabilistic Sharpe ratio": ("PSR",),
    "Deflated Sharpe ratio": ("DSR", "multiple testing"),
    "Track record needed": ("minimum track record length", "MinTRL"),
    "Decision value added": ("trade value added", "decision attribution"),
    "Hit rate and payoff ratio": ("payoff ratio", "win/loss ratio"),
    "Timing cost": ("slippage",),
    "Counterfactual portfolios": ("buy and hold", "do nothing portfolio"),
    "Currency effect": ("FX effect", "Karnosky-Singer", "currency attribution"),
    "Local market allocation and selection": ("Brinson-Fachler", "allocation effect", "selection effect"),
    "Ex-ante volatility": ("predicted volatility", "forward-looking risk"),
    "Holding risk contribution": ("Euler allocation", "marginal risk"),
    "Covariance shrinkage": ("Ledoit-Wolf",),
    "Statistical risk factors": ("PCA", "principal components"),
    "EVT VaR and expected shortfall": ("extreme value theory", "EVT", "peaks over threshold", "GPD"),
    "Tail backtest traffic light": ("Basel traffic light", "Kupiec test"),
    "Breach clustering test": ("Christoffersen test",),
    "Expected shortfall backtest (Z2)": ("Acerbi-Szekely", "Z2"),
    "Risk by market regime": ("regime VaR", "stressed VaR"),
    "Moving correlation (DCC)": ("DCC", "dynamic conditional correlation"),
    "Herfindahl index and effective number": ("HHI", "Herfindahl"),
    "Correlation clusters": ("hidden bets",),
    "Advanced optimisers": ("portfolio construction",),
    "Black-Litterman weights": ("Black-Litterman", "views"),
    "Hierarchical risk parity weights": ("HRP",),
    "Minimum tail loss weights": ("minimum CVaR", "mean-CVaR"),
    "Mean-variance weights after costs": ("transaction costs", "turnover penalty"),
    "Robust mean-variance weights": ("robust optimisation", "uncertainty set"),
    "No-trade band": ("rebalancing band", "tolerance band"),
    "Chance of reaching the goal": ("goals-based planning", "goal probability"),
    "Monthly saving needed": ("required contribution",),
    "Linked factor scenario": ("conditional scenario",),
    "Reverse stress test": ("reverse stress",),
    "Implementation shortfall": ("trading costs", "execution cost", "Perold"),
}

RELATED = {
    "Skill and luck": ("Probabilistic Sharpe ratio", "Track record needed", "Sharpe ratio", "Information ratio"),
    "Probabilistic Sharpe ratio": ("Skill and luck", "Deflated Sharpe ratio", "Track record needed"),
    "Deflated Sharpe ratio": ("Probabilistic Sharpe ratio",),
    "Track record needed": ("Probabilistic Sharpe ratio", "Skill and luck"),
    "Decision value added": ("Hit rate and payoff ratio", "Timing cost", "Counterfactual portfolios"),
    "Hit rate and payoff ratio": ("Decision value added", "Sizing skill", "Sell discipline"),
    "Sizing skill": ("Hit rate and payoff ratio", "Sell discipline"),
    "Sell discipline": ("Sizing skill", "Decision value added"),
    "Timing cost": ("Implementation shortfall", "Decision value added"),
    "Counterfactual portfolios": ("Decision value added", "Excess total return"),
    "Currency effect": ("Local market allocation and selection", "Excess total return", "Reporting currency"),
    "Local market allocation and selection": ("Currency effect", "Contribution to return"),
    "Ex-ante volatility": ("Holding risk contribution", "Covariance shrinkage", "Annualized volatility"),
    "Holding risk contribution": ("Ex-ante volatility", "Share of risk"),
    "Covariance shrinkage": ("Ex-ante volatility", "Statistical risk factors"),
    "Statistical risk factors": ("Correlation clusters", "Factor beta"),
    "EVT VaR and expected shortfall": ("Tail backtest traffic light", "VaR 95% — daily", "Forward VaR"),
    "Tail backtest traffic light": ("Breach clustering test", "Expected shortfall backtest (Z2)", "VaR backtest"),
    "Breach clustering test": ("Tail backtest traffic light",),
    "Expected shortfall backtest (Z2)": ("Tail backtest traffic light", "EVT VaR and expected shortfall"),
    "Risk by market regime": ("Market regime", "EVT VaR and expected shortfall"),
    "Moving correlation (DCC)": ("Correlation on stress days", "Holdings correlation"),
    "Correlation on stress days": ("Moving correlation (DCC)", "Correlation clusters"),
    "Herfindahl index and effective number": ("Top 5 share of gross", "Effective number of holdings"),
    "Top 5 share of gross": ("Herfindahl index and effective number", "Top 5 weight"),
    "Correlation clusters": ("Herfindahl index and effective number", "Moving correlation (DCC)"),
    "Advanced optimisers": ("Black-Litterman weights", "Hierarchical risk parity weights", "Minimum tail loss weights",
                            "Mean-variance weights after costs", "Robust mean-variance weights", "Efficient frontier"),
    "Black-Litterman weights": ("Advanced optimisers", "Expected return (optimisation)"),
    "Hierarchical risk parity weights": ("Advanced optimisers", "Risk parity portfolio"),
    "Minimum tail loss weights": ("Advanced optimisers", "Daily Expected Tail Loss (95%)"),
    "Mean-variance weights after costs": ("Advanced optimisers", "Rebalancing plan"),
    "Robust mean-variance weights": ("Advanced optimisers", "Mean-variance weights after costs"),
    "No-trade band": ("Rebalancing plan",),
    "Rebalancing plan": ("No-trade band", "Advanced optimisers"),
    "Chance of reaching the goal": ("Monthly saving needed", "Chance of reaching the target", "Market regime"),
    "Monthly saving needed": ("Chance of reaching the goal",),
    "Linked factor scenario": ("Reverse stress test", "Factor stress test impact", "Factor beta"),
    "Reverse stress test": ("Linked factor scenario", "Factor stress test impact"),
    "Implementation shortfall": ("Timing cost", "Commissions / NAV"),
}

# Page › subpage, using the subpage labels of web_quant.QUANT_SUBPAGES.
LOCATIONS = {
    **{name: "Performance › Skill and luck" for name in (
        "Skill and luck", "Probabilistic Sharpe ratio", "Deflated Sharpe ratio", "Track record needed")},
    **{name: "Performance › Decisions" for name in (
        "Decision value added", "Hit rate and payoff ratio", "Sizing skill", "Sell discipline", "Timing cost",
        "Counterfactual portfolios")},
    **{name: "Performance › Currency" for name in ("Currency effect", "Local market allocation and selection")},
    **{name: "Risk › Holdings risk" for name in (
        "Ex-ante volatility", "Holding risk contribution", "Covariance shrinkage", "Statistical risk factors")},
    **{name: "Risk › Tail risk" for name in (
        "EVT VaR and expected shortfall", "Tail backtest traffic light", "Breach clustering test",
        "Expected shortfall backtest (Z2)")},
    "Risk by market regime": "Risk › Regimes",
    **{name: "Risk › Moving correlations" for name in ("Moving correlation (DCC)", "Correlation on stress days")},
    **{name: "Risk › Concentration" for name in (
        "Herfindahl index and effective number", "Top 5 share of gross", "Correlation clusters")},
    **{name: "Modelling › Advanced optimisers" for name in (
        "Advanced optimisers", "Black-Litterman weights", "Hierarchical risk parity weights",
        "Minimum tail loss weights", "Mean-variance weights after costs", "Robust mean-variance weights")},
    **{name: "Modelling › Rebalancing" for name in ("No-trade band", "Rebalancing plan")},
    **{name: "Modelling › Goals" for name in ("Chance of reaching the goal", "Monthly saving needed")},
    **{name: "Modelling › Scenarios" for name in ("Linked factor scenario", "Reverse stress test")},
    "Implementation shortfall": "Modelling › Trading costs",
}

# Each page's section and the key of the guide section it follows.
_SECTION_TEXT = {
    "performance": ("benchmark", "Skill and decisions", "Skill, decisions and currency",
                    "Whether the record shows skill or luck, what the trades added, and how much currency moves "
                    "contributed (quant-analytics-v1, built overnight).",
                    "Performance › Skill and luck, Decisions, Currency"),
    "risk": ("statistics", "Holdings and tail risk", "Holdings risk, tail risk, regimes, correlations and concentration",
             "Risk of today's holdings, the far tail and how well past VaR held up, risk in each market regime, "
             "correlations that move, and hidden concentration (quant-analytics-v1, built overnight).",
             "Risk › Holdings risk, Tail risk, Regimes, Moving correlations, Concentration"),
    "modelling": ("modelling-portfolio", "Advanced modelling", "Advanced optimisers, rebalancing, goals, scenarios and trading costs",
                  "More ways to weight the holdings, when to rebalance, the odds of reaching a goal, linked and "
                  "reverse factor scenarios, and what trading cost. Hypothetical models built from historical data; "
                  "they are not forecasts, guarantees or investment advice.",
                  "Modelling › Advanced optimisers, Rebalancing, Goals, Scenarios, Trading costs"),
}
SECTIONS = {
    page: (after, MetricSection(f"quant-{page}", short, title, introduction, DEFINITIONS[page], location))
    for page, (after, short, title, introduction, location) in _SECTION_TEXT.items()
}
# Pages built overnight (quant-analytics-v1), and the request-time models' own versions.
OVERNIGHT_PAGES = {
    "performance": "Skill and luck, Decisions, Currency",
    "risk": "Holdings risk, Tail risk, Regimes, Moving correlations, Concentration",
    "modelling": "Trading costs",
}
REQUEST_TIME_VERSIONS = (("quant-optimisers-v1", "Advanced optimisers"), ("quant-rebalancing-v1", "Rebalancing"),
                         ("quant-goals-v1", "Goals"), ("stress-engine-v1", "Scenarios"))

_BY_NAME = {metric.name: metric for page in PAGES for metric in DEFINITIONS[page]}
METRIC_ANCHORS = {name: metric_anchor(name) for name in _BY_NAME}
if len(_BY_NAME) != sum(len(DEFINITIONS[page]) for page in PAGES) or len(set(METRIC_ANCHORS.values())) != len(_BY_NAME):
    raise RuntimeError("Quant guide names and anchors must be unique")
if set(METRIC_ANCHORS.values()) & set(BASE_ANCHORS.values()):
    raise RuntimeError("Quant guide anchors must not reuse a Metrics guide anchor")
for _table in (CLIENT_CALCULATIONS, ALIASES, RELATED, LOCATIONS):
    if set(_table) - set(_BY_NAME):
        raise RuntimeError(f"Unknown quant guide names: {sorted(set(_table) - set(_BY_NAME))}")
if set(CLIENT_CALCULATIONS) != set(_BY_NAME) or set(LOCATIONS) != set(_BY_NAME):
    raise RuntimeError("Every quant guide entry needs a plain explanation and a location")
for _name, _targets in RELATED.items():
    for _target in _targets:
        if _target == _name or (_target not in _BY_NAME and _target not in BASE_ANCHORS):
            raise RuntimeError(f"Invalid related metric for {_name}: {_target}")


def page_of(name: str) -> str:
    return next(page for page in PAGES if any(metric.name == name for metric in DEFINITIONS[page]))


def addition(pages: tuple[str, ...] | list[str] | set[str]) -> GuideAddition | None:
    """The guide sections for the pages a viewer may see; None when there are none.

    A "See also" link to an entry on a page the viewer cannot see is dropped.
    """

    shown = [page for page in PAGES if page in pages]
    if not shown:
        return None
    visible = {metric.name for page in shown for metric in DEFINITIONS[page]} | set(BASE_ANCHORS)
    versions = [("quant-analytics-v1", "built overnight: " + "; ".join(OVERNIGHT_PAGES[page] for page in shown))]
    if "modelling" in shown:
        versions += REQUEST_TIME_VERSIONS
    names = {metric.name for page in shown for metric in DEFINITIONS[page]}
    return GuideAddition(
        sections=tuple(SECTIONS[page] for page in shown),
        client_calculations={name: CLIENT_CALCULATIONS[name] for name in names},
        aliases={name: ALIASES[name] for name in names if name in ALIASES},
        related={name: tuple(t for t in RELATED.get(name, ()) if t in visible) for name in names},
        locations={name: LOCATIONS[name] for name in names},
        versions=tuple(versions),
    )


def definition_fields(name: str) -> dict:
    """The audit's view of one entry (as metrics_guide_audit._definition_fields)."""

    metric = _BY_NAME[name]
    return {"what_it_tells_you": metric.meaning, "technical_method": metric.calculation,
            "how_to_explain_it": metric.interpretation, "how_we_work_it_out": CLIENT_CALCULATIONS[name]}


# --- Audit ------------------------------------------------------------------------------

LABEL_SOURCES = (("web_quant.py", None, None, r"quantTile\('([^']+)'"),)

# Tile wording that differs from the entry which explains it.
LABEL_MAP = {
    "Sharpe ratio range": "Skill and luck",
    "Alpha range": "Skill and luck",
    "Information ratio range": "Skill and luck",
    "Years of returns": "Track record needed",
    "Value added (5 sessions)": "Decision value added",
    "Value added (21 sessions)": "Decision value added",
    "Value added (63 sessions)": "Decision value added",
    "Hit rate": "Hit rate and payoff ratio",
    "Payoff ratio": "Hit rate and payoff ratio",
    "Actual minus buy and hold": "Counterfactual portfolios",
    "Actual minus benchmark path": "Counterfactual portfolios",
    "Currency effect (month to date)": "Currency effect",
    "Currency effect (year to date)": "Currency effect",
    "Excess return (year to date)": "Currency effect",
    "Allocation (year to date, local)": "Local market allocation and selection",
    "Selection (year to date, local)": "Local market allocation and selection",
    "Ex-ante tracking error": "Ex-ante volatility",
    "Largest risk contributor": "Holding risk contribution",
    "First statistical factor": "Statistical risk factors",
    "EVT VaR (99%)": "EVT VaR and expected shortfall",
    "EVT expected shortfall (99%)": "EVT VaR and expected shortfall",
    "Historical VaR (99%)": "EVT VaR and expected shortfall",
    "Historical expected shortfall (99%)": "EVT VaR and expected shortfall",
    "EVT VaR (99.5%)": "EVT VaR and expected shortfall",
    "EVT expected shortfall (99.5%)": "EVT VaR and expected shortfall",
    "VaR for tomorrow (99%, volatility-adjusted)": "EVT VaR and expected shortfall",
    "Traffic light (99%, last 250 days)": "Tail backtest traffic light",
    "Regimes found": "Risk by market regime",
    "Stressed regime expected shortfall (99%)": "Risk by market regime",
    "Average correlation now": "Moving correlation (DCC)",
    "Average correlation, long run": "Moving correlation (DCC)",
    "Correlation half-life": "Moving correlation (DCC)",
    "Average correlation on stress days": "Correlation on stress days",
    "Average correlation on calm days": "Correlation on stress days",
    "Effective number (by holding)": "Herfindahl index and effective number",
    "Herfindahl index (by holding)": "Herfindahl index and effective number",
    "Effective number (by sector)": "Herfindahl index and effective number",
    "Clusters of 2 or more": "Correlation clusters",
    "Value to trade": "Rebalancing plan",
    "Expected trading cost": "Rebalancing plan",
    "Saved against a full rebalance": "Rebalancing plan",
    "Largest drift left": "Rebalancing plan",
    "Chance of reaching the goal (regimes)": "Chance of reaching the goal",
    "Chance of running out first": "Chance of reaching the goal",
    "Monthly saving needed (regimes)": "Monthly saving needed",
    "Median end value": "Chance of reaching the goal",
    "Linked scenario impact": "Linked factor scenario",
    "Named shocks only": "Linked factor scenario",
    "Loss tested": "Reverse stress test",
    "Chance of a loss this large": "Reverse stress test",
    "How unusual (Mahalanobis distance)": "Reverse stress test",
    "Orders analysed": "Implementation shortfall",
    "Most likely regime today": "Risk by market regime",
    "Delay cost": "Implementation shortfall",
    "Trading cost (fills against arrival)": "Implementation shortfall",
    "Opportunity cost (unfilled)": "Implementation shortfall",
}

# Engine functions ("module:function") and the entries that describe them.
WATCHED_SOURCES = {
    "ibkr_etl.quant.skill_statistics:skill_summary": (
        "Skill and luck", "Probabilistic Sharpe ratio", "Deflated Sharpe ratio", "Track record needed"),
    "ibkr_etl.quant.decision_attribution:trade_value_added": ("Decision value added",),
    "ibkr_etl.quant.decision_attribution:skill_profile": ("Hit rate and payoff ratio", "Sizing skill", "Sell discipline"),
    "ibkr_etl.quant.decision_attribution:timing_cost": ("Timing cost",),
    "ibkr_etl.quant.decision_attribution:counterfactual_portfolios": ("Counterfactual portfolios",),
    "ibkr_etl.quant.attribution:currency_attribution": ("Currency effect", "Local market allocation and selection"),
    "ibkr_etl.quant.holdings_risk:ledoit_wolf_covariance": ("Covariance shrinkage",),
    "ibkr_etl.quant.holdings_risk:ex_ante_risk": ("Ex-ante volatility", "Holding risk contribution"),
    "ibkr_etl.quant.holdings_risk:statistical_factors": ("Statistical risk factors",),
    "ibkr_etl.quant.tail_risk:evt_var_es": ("EVT VaR and expected shortfall",),
    "ibkr_etl.quant.tail_risk:tail_backtest_report": (
        "Tail backtest traffic light", "Breach clustering test", "Expected shortfall backtest (Z2)"),
    "ibkr_etl.quant.stress_engine:regime_risk": ("Risk by market regime",),
    "ibkr_etl.quant.dynamic_correlation:dcc_correlation": ("Moving correlation (DCC)", "Correlation on stress days"),
    "ibkr_etl.quant.crowding:concentration": ("Herfindahl index and effective number", "Top 5 share of gross"),
    "ibkr_etl.quant.crowding:correlation_clusters": ("Correlation clusters",),
    "ibkr_etl.quant.execution_analysis:implementation_shortfall": ("Implementation shortfall",),
    "ibkr_etl.quant.optimisers:black_litterman": ("Black-Litterman weights",),
    "ibkr_etl.quant.optimisers:hierarchical_risk_parity": ("Hierarchical risk parity weights",),
    "ibkr_etl.quant.optimisers:min_cvar_weights": ("Minimum tail loss weights",),
    "ibkr_etl.quant.optimisers:mean_variance_with_costs": ("Mean-variance weights after costs",),
    "ibkr_etl.quant.optimisers:robust_mean_variance": ("Robust mean-variance weights",),
    "ibkr_etl.quant.rebalancing:no_trade_bands": ("No-trade band",),
    "ibkr_etl.quant.rebalancing:rebalance_plan": ("Rebalancing plan",),
    "ibkr_etl.quant.goals:goal_projection": ("Chance of reaching the goal",),
    "ibkr_etl.quant.goals:required_contribution": ("Monthly saving needed",),
    "ibkr_etl.quant.stress_engine:conditional_scenario": ("Linked factor scenario",),
    "ibkr_etl.quant.stress_engine:reverse_stress": ("Reverse stress test",),
    "ibkr_etl.web_quant:_advanced_optimisers": ("Advanced optimisers", "Robust mean-variance weights"),
}
