from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from html import escape
import re
import unicodedata


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    meaning: str
    calculation: str
    interpretation: str


@dataclass(frozen=True)
class MetricSection:
    key: str
    short_title: str
    title: str
    introduction: str
    metrics: tuple[MetricDefinition, ...]
    location: str = ""


@dataclass(frozen=True)
class GuideAddition:
    """Sections a switched-on feature adds to the rendered guide page (metrics_guide_quant.py).

    ``sections`` pairs each section with the key of the guide section it follows. The guide's own
    entries, anchors and search are unchanged; with no additions the page is exactly as before.
    """

    sections: tuple[tuple[str, MetricSection], ...] = ()
    client_calculations: Mapping[str, str] = field(default_factory=dict)
    aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    related: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    locations: Mapping[str, str] = field(default_factory=dict)
    versions: tuple[tuple[str, str], ...] = ()


_DEFINITIONS = (
    MetricDefinition(
        "As of",
        "The user-selected cutoff date for an account view.",
        "The page uses observations dated on or before the selected date. A dataset may therefore have an earlier effective date.",
        "Always read the effective date shown beside holdings, cash, risk or benchmark data; As of is a cutoff, not a promise that every market reported that day.",
    ),
    MetricDefinition(
        "Net asset value (NAV)",
        "The selected account or portfolio's total net value in its registered reporting currency.",
        "Physical accounts use the validated IBKR total NAV. Filtered portfolios use their validated included holdings and reconstructed cash; fixed-unit composites use their approved component-unit values and cash. Each value is expressed in the portfolio's registered reporting currency for the effective date.",
        "A point-in-time account value. It is not a return, cash balance, intraday quote or liquidation estimate.",
    ),
    MetricDefinition(
        "Observations",
        "The number of validated total-NAV observations from the account's first validated NAV date through As of.",
        "Count of validated TOTAL asset-class NAV rows within the account's reporting period. The Performance and Risk notes state the observation counts their own figures use.",
        "A coverage count, not a count of calendar days, holdings or trades.",
    ),
    MetricDefinition(
        "Positions",
        "The number of holdings in the latest validated position snapshot on or before As of.",
        "Count of position rows in the selected effective holdings snapshot. Shown beside Top holdings on Overview.",
        "Multiple lots or model rows can affect counts; use the Holdings table for the actual constituents.",
    ),
    MetricDefinition(
        "Reporting currency",
        "The common currency in which portfolio-level values and analytics are presented.",
        "Set in each account's or portfolio's data contract, for example USD or HKD. Holdings and cash in other currencies are converted with the dated FX rate on or before the valuation date before any portfolio-level figure is calculated.",
        "A holding's trading currency can differ from the portfolio reporting currency.",
    ),
    MetricDefinition(
        "Rebased portfolio or benchmark level",
        "A common-scale growth index used to compare two differently sized series.",
        "Start at 100 on the first common valid date. Each later value is Previous rebased value × (1 + current validated daily return).",
        "A value of 112 means 12% growth since the common start. It is not NAV or an investable price.",
    ),
    MetricDefinition(
        "Cash-flow-adjusted daily portfolio return",
        "The portfolio's daily investment return after removing external contributions and withdrawals.",
        "rₜ = (Vₜ − Vₜ₋₁ − Fₜ) ÷ (Vₜ₋₁ + 0.5 × Fₜ), where V is NAV and F is the sum of external flows after the prior common date through the current date.",
        "The half-flow denominator is a Modified-Dietz-style timing assumption. Fees, dividends and trading effects remain in performance unless classified as external flows.",
    ),
    MetricDefinition(
        "Daily benchmark return",
        "The change in the selected benchmark index between adjacent common valid dates.",
        "bₜ = Bₜ ÷ Bₜ₋₁ − 1, using the selected benchmark's validated closing level.",
        "Whether dividends are included depends on the benchmark's return basis. Price indexes such as the S&P 500 and NASDAQ Composite price series exclude them.",
    ),
    MetricDefinition(
        "Portfolio total return",
        "Compounded portfolio performance over the displayed common-date period.",
        "Ending rebased portfolio level ÷ starting rebased portfolio level − 1; equivalently ∏(1 + rₜ) − 1.",
        "Uses cash-flow-adjusted daily returns and may differ from simple NAV change when external flows occur.",
    ),
    MetricDefinition(
        "Benchmark total return",
        "Compounded benchmark performance over the same common dates as the portfolio.",
        "Ending rebased benchmark level ÷ starting rebased benchmark level − 1; equivalently ∏(1 + bₜ) − 1.",
        "For a price index this is price return, not total return including dividends. The benchmark card says which basis applies.",
    ),
    MetricDefinition(
        "Excess total return",
        "The portfolio's total-return advantage or shortfall versus the selected benchmark.",
        "Portfolio total return − benchmark total return.",
        "Positive means the portfolio outperformed over the period. This arithmetic difference is not a compounded daily excess-return series.",
    ),
    MetricDefinition(
        "Tracking error",
        "Annualized variability of daily active returns around the benchmark.",
        "Standard deviation of (rₜ − bₜ) × √252.",
        "Lower means returns stayed closer to the benchmark. It does not say whether active returns were positive.",
    ),
    MetricDefinition(
        "Information ratio",
        "Annualized average active return per unit of tracking error.",
        "Mean(rₜ − bₜ) ÷ standard deviation(rₜ − bₜ) × √252.",
        "Positive is favorable relative performance; unavailable when active-return volatility is zero or there are too few observations.",
    ),
    MetricDefinition(
        "Beta",
        "Sensitivity of portfolio daily returns to movements in the selected benchmark.",
        "Covariance(rₜ, bₜ) ÷ Variance(bₜ).",
        "Beta above 1 indicates greater historical sensitivity; below 1 indicates lower sensitivity. It is benchmark- and sample-dependent, not a forecast.",
    ),
    MetricDefinition(
        "Correlation",
        "Strength and direction of the linear relationship between portfolio and benchmark daily returns.",
        "Covariance(rₜ, bₜ) ÷ (standard deviation(rₜ) × standard deviation(bₜ)).",
        "Ranges from −1 to +1. High correlation does not imply equal return or causal exposure.",
    ),
    MetricDefinition(
        "Relative win rate",
        "Share of measured common-date periods in which the portfolio beat the benchmark.",
        "Count(rₜ − bₜ > 0) ÷ number of measured excess returns.",
        "A high win rate can coexist with underperformance if losing periods are much larger than winning periods.",
    ),
    MetricDefinition(
        "Total return",
        "Compounded portfolio return across all validated return observations in the risk period.",
        "∏(1 + rₜ) − 1.",
        "Includes the effect of compounding and is cash-flow-adjusted.",
    ),
    MetricDefinition(
        "Annualized return",
        "Geometrically annualized portfolio growth over the measured calendar-day span.",
        "(1 + total return)^(365.25 ÷ calendar days between period start and end) − 1.",
        "Annualization can be unstable over short periods and is unavailable for a non-positive growth factor.",
    ),
    MetricDefinition(
        "Annualized volatility",
        "Annualized sample standard deviation of daily portfolio returns.",
        "Standard deviation(rₜ) × √252. The Risk tab uses benchmark-aligned dates; the Performance tab statistics use every validated NAV date, so the two can differ slightly.",
        "A dispersion measure, not a loss forecast. It treats upside and downside variation equally.",
    ),
    MetricDefinition(
        "Risk-free rate",
        "Annual reference yield used in the Sharpe calculation, matched to the portfolio's reporting currency.",
        "USD portfolios: latest validated U.S. Treasury Daily Treasury Par Yield Curve 3-month observation on or before As of. HKD composites: arithmetic mean of the published HKAB 1-month HIBOR fixings from inception to the valuation date. Stored as a decimal annual rate.",
        "It is not the interest earned by the account. It can legitimately lag on weekends and public holidays; the Risk note names the source and observation date.",
    ),
    MetricDefinition(
        "Sharpe ratio",
        "Annualized excess portfolio return per unit of daily return volatility.",
        "First convert annual risk-free rate Rf to rf,daily = (1 + Rf)^(1/252) − 1. Then Sharpe = (mean(rₜ) − rf,daily) ÷ standard deviation(rₜ) × √252.",
        "Higher is better only within comparable methods and periods. It is unavailable without a validated rate, sufficient returns or non-zero volatility.",
    ),
    MetricDefinition(
        "Expected maximum drawdown (1 year)",
        "Average simulated worst peak-to-trough decline over a 252-session horizon.",
        "Mean maximum drawdown across 5,000 deterministic moving-block bootstrap paths. Each path has 252 sessions sampled in five-session blocks from validated historical returns.",
        "Displayed as a negative percentage. It is a model-based expectation, not a guaranteed worst case.",
    ),
    MetricDefinition(
        "Actual maximum drawdown (1 year)",
        "Worst realized peak-to-trough decline over up to the latest 252 validated return observations.",
        "Build cumulative wealth from the latest 252 rₜ values; calculate wealth ÷ running peak − 1 each day; take the most negative value.",
        "Displayed as a negative percentage. It describes the observed window only.",
    ),
    MetricDefinition(
        "Tail maximum drawdown (1 year)",
        "Average of the most severe 5% of simulated one-year maximum drawdowns.",
        "Mean of bootstrap-path maximum drawdowns at or below the distribution's 5th percentile, using the same 5,000 paths and 252-session horizon.",
        "Displayed as a negative percentage. It estimates severe simulated paths, not the theoretical maximum loss.",
    ),
    MetricDefinition(
        "Crisis maximum drawdown (15/9/2008–9/3/2009)",
        "Hypothetical drawdown from replaying the holdings selected by As of through the specified global-financial-crisis period.",
        "Use each holding's opening market-value-to-NAV weight and a fixed number of units. Use complete Yahoo adjusted-close history when available, otherwise the recorded sector ETF or SPY proxy. Residual cash stays flat; no rebalancing. Take the most negative wealth ÷ running peak − 1.",
        "Not the account's realized 2008–2009 performance. Proxy choices, today's holdings and Yahoo data materially affect the result.",
    ),
    MetricDefinition(
        "VaR 95% — daily",
        "Historical one-day loss threshold exceeded by approximately the worst 5% of validated daily returns.",
        "max(0, −5th percentile of rₜ), using linearly interpolated sample quantiles.",
        "Displayed as a positive loss percentage. It does not describe losses beyond the threshold and is not a maximum possible loss.",
    ),
    MetricDefinition(
        "Daily Expected Tail Loss (95%)",
        "Average historical one-day loss among returns at or below the 5th-percentile cutoff; also called 95% Expected Shortfall or CVaR.",
        "max(0, −mean(rₜ for rₜ ≤ 5th percentile)).",
        "Displayed as a positive loss percentage. It reflects historical tail observations and can understate unobserved crises.",
    ),
    MetricDefinition(
        "Win rate",
        "Share of validated daily portfolio returns that were strictly positive.",
        "Count(rₜ > 0) ÷ number of validated daily returns.",
        "Zero-return days are not wins. Win rate ignores the size of gains and losses.",
    ),
    MetricDefinition(
        "BLUE mean daily return",
        "Arithmetic sample mean of validated daily portfolio returns, labelled as the best linear unbiased estimator under standard equal-mean, independent-error assumptions.",
        "Σrₜ ÷ N. Its standard error, retained by the engine, is standard deviation(rₜ) ÷ √N.",
        "A historical estimate, not a forecast. The BLUE interpretation weakens when returns are dependent, heteroskedastic or structurally changing.",
    ),
    MetricDefinition(
        "Expected Tail Loss contribution",
        "A holding's average contribution to portfolio loss across identified tail-return dates.",
        "For each tail date, use −(current market value − prior market value) ÷ prior portfolio NAV, but only when aggregate quantity and USD currency are unchanged across the adjacent snapshots. Average across all portfolio tail dates.",
        "Positive added to loss; negative offset loss. Trading days, non-USD positions and unmatched pairs are excluded from direct attribution.",
    ),
    MetricDefinition(
        "Top 5 ETL contributors",
        "The five eligible holdings with the largest positive Expected Tail Loss contributions.",
        "Sort eligible position contributions from largest to smallest and take five.",
        "Ranks historical contribution under the attribution rules, not standalone forward-looking risk.",
    ),
    MetricDefinition(
        "Bottom 5 ETL contributors",
        "The five eligible holdings with the smallest contributions, generally the largest offsets to loss.",
        "Sort eligible position contributions from smallest to largest and take five.",
        "A negative value means the holding offset portfolio loss on tail days; it does not guarantee protection in another crisis.",
    ),
    MetricDefinition(
        "ETL residual contribution",
        "The part of portfolio Expected Tail Loss not explained by eligible unchanged USD positions.",
        "Portfolio Daily Expected Tail Loss − sum of all attributed position contributions.",
        "Includes cash, fees, FX, trades, non-USD holdings and unmatched or changed positions. A large residual limits holding-level interpretation.",
    ),
    MetricDefinition(
        'Sortino ratio',
        'Return earned per unit of downside volatility, using a 0% minimum acceptable return.',
        'Mean daily return ÷ downside deviation of daily returns × √252, where downside deviation is √(Σ min(rₜ, 0)² ÷ N). Uses every validated NAV date with the same cash-flow-adjusted returns as the period returns table.',
        'Higher is better. Unlike Sharpe it does not penalise upside volatility, so it suits portfolios with asymmetric returns.',
    ),
    MetricDefinition(
        'Calmar ratio',
        'Annualised return earned per unit of maximum drawdown.',
        'Annualised time-weighted return ÷ |maximum drawdown since inception|. Shown only when at least one year of NAV history exists.',
        'Higher is better. It links return directly to the worst loss an investor would have lived through.',
    ),
    MetricDefinition(
        'Maximum drawdown (since inception)',
        'The largest fall from a previous high in cash-flow-adjusted portfolio value since the first NAV.',
        'Minimum over all NAV dates of (wealth index ÷ running peak − 1), where the wealth index chain-links daily Modified Dietz returns.',
        'Uses every NAV date, so it can differ slightly from figures on the Risk tab, which use benchmark-aligned dates.',
    ),
    MetricDefinition(
        'Current drawdown',
        'How far the portfolio currently sits below its highest cash-flow-adjusted value.',
        'Wealth index on the latest NAV date ÷ running peak − 1.',
        'Zero means the portfolio is at a new high.',
    ),
    MetricDefinition(
        'Downside deviation',
        'Annualised volatility of losses only.',
        '√(Σ min(rₜ, 0)² ÷ N) × √252 across all validated daily returns, with a 0% threshold.',
        'Gains do not raise it. It is the denominator of the Sortino ratio.',
    ),
    MetricDefinition(
        'Skewness',
        'Asymmetry of the daily return distribution.',
        'Bias-adjusted sample skewness: N ÷ ((N − 1)(N − 2)) × Σ((rₜ − mean) ÷ standard deviation)³.',
        'Negative values mean occasional large losses dominate; positive values mean occasional large gains dominate. Zero is symmetric.',
    ),
    MetricDefinition(
        'Excess kurtosis',
        'How fat the tails of the daily return distribution are compared with a normal distribution.',
        'Bias-adjusted sample excess kurtosis, so a normal distribution scores 0.',
        'Positive values mean extreme days happen more often than a normal distribution predicts, so volatility alone understates tail risk.',
    ),
    MetricDefinition(
        'Best and worst day',
        'The largest single-day gain and loss in cash-flow-adjusted portfolio return.',
        'Maximum and minimum of the validated daily Modified Dietz returns, with their NAV dates.',
        'Single observations; they show the range of daily outcomes, not typical behaviour.',
    ),
    MetricDefinition(
        'Best and worst month',
        'The highest and lowest calendar-month time-weighted return.',
        'Maximum and minimum of the monthly returns calendar cells that passed validation, including the current month to date.',
        'A partial first month or the current month to date can appear here; the calendar marks them.',
    ),
    MetricDefinition(
        'Positive months',
        'Share of measured calendar months with a positive time-weighted return.',
        'Count of monthly returns above zero ÷ number of measured months.',
        'Ignores the size of gains and losses; read it with the gain/loss ratio.',
    ),
    MetricDefinition(
        'Average gain and loss month',
        'The average return of positive months and of negative months.',
        'Arithmetic mean of monthly returns above zero, and of monthly returns below zero.',
        'Shows how large typical winning and losing months are.',
    ),
    MetricDefinition(
        'Gain/loss ratio',
        'Size of the average winning month relative to the average losing month.',
        'Average gain month ÷ |average loss month|.',
        'Above 1 means winning months are larger than losing months on average.',
    ),
    MetricDefinition(
        'Monthly return',
        'Time-weighted return for one calendar month or year in the monthly returns calendar.',
        'Chain-linked daily Modified Dietz returns from the last NAV before the period starts to the last NAV on or before it ends, identical to the period returns table.',
        'External deposits and withdrawals are removed, so cells measure investment performance only.',
    ),
    MetricDefinition(
        'Rolling 3-month volatility',
        'Annualised volatility measured over a moving window of the latest 63 daily returns.',
        'Sample standard deviation of the latest 63 validated daily returns × √252, recalculated on each NAV date.',
        'Shows whether risk is rising or falling over time rather than one average for the whole history.',
    ),
    MetricDefinition(
        'Trailing 12-month return',
        'Time-weighted return over the 12 months to each NAV date.',
        'Wealth index on the date ÷ wealth index on the last NAV at least 365 days earlier − 1.',
        'Smooths out short-term noise and shows how the one-year result has evolved.',
    ),
    MetricDefinition(
        'Largest holding',
        'The single position with the largest absolute weight.',
        'Holding with the maximum |weight| in the latest validated holdings snapshot.',
        'A quick check on single-name risk.',
    ),
    MetricDefinition(
        'Top 5 weight',
        'Combined absolute weight of the five largest positions.',
        'Sum of the five largest |weights| in the latest validated holdings snapshot.',
        'Higher values mean results depend more on a few names.',
    ),
    MetricDefinition(
        'Top 10 weight',
        'Combined absolute weight of the ten largest positions.',
        'Sum of the ten largest |weights| in the latest validated holdings snapshot.',
        'A common institutional concentration limit check.',
    ),
    MetricDefinition(
        'Effective number of holdings',
        'The number of equal-sized positions that would give the same concentration.',
        '1 ÷ Herfindahl index, where the Herfindahl index is Σ(|wᵢ| ÷ Σ|w|)² across weighted holdings.',
        'Much lower than the actual holdings count means a few large positions dominate.',
    ),
    MetricDefinition(
        'Currency exposure',
        'Share of the portfolio held in securities of each trading currency.',
        "Sum of holding weights grouped by each holding's trading currency; with a NAV weight basis the remainder is cash and other NAV components.",
        'Shows foreign-exchange risk relative to the reporting currency. Cash balances by currency are on the Cash tab.',
    ),
    MetricDefinition(
        'Contribution to return',
        "How much a holding or sector added to or subtracted from the portfolio's return in one month.",
        'Each day: prior-day units × change in mark price (converted to the reporting currency) ÷ the cash-flow-adjusted NAV denominator. Daily effects are Frongello-linked so holdings plus residual add exactly to the compounded monthly return. Same engine as the monthly factsheet.',
        'The residual captures cash, interest, fees, FX on cash, intramonth trading and unmatched movements.',
    ),
    MetricDefinition(
        'Portfolio turnover',
        'How much of the portfolio was traded, excluding new money that was simply invested.',
        'Lesser of purchases and sales (in the reporting currency) ÷ average NAV. The headline figure covers the latest 12 months, scaled to a year when less history exists.',
        '100% means the equivalent of the whole portfolio was replaced once in the year.',
    ),
    MetricDefinition(
        'Commissions / NAV',
        'Broker commissions as a share of average portfolio value.',
        'Sum of commissions converted to the reporting currency ÷ average NAV, over the latest 12 months or one month.',
        'A direct measure of trading cost drag on returns.',
    ),
    MetricDefinition(
        'Trading activity',
        'Counts and values of executed trades: purchases, sales, commissions and realised P&L by month.',
        "Executed IBKR trades from the daily refresh, converted to the reporting currency with the latest FX rate on or before each trade date. Realised P&L uses IBKR's lot accounting.",
        'Trades without a usable FX rate are left out and listed.',
    ),
    MetricDefinition(
        'Rolling beta and correlation',
        'Sensitivity to, and co-movement with, the selected benchmark over a moving six-month window.',
        'For each date, beta = covariance(portfolio, benchmark) ÷ variance(benchmark) and the Pearson correlation, over the latest 126 common-date daily returns.',
        "Shows whether the portfolio's market exposure has changed over time.",
    ),
    MetricDefinition(
        'Holdings correlation',
        'How closely the daily price moves of the largest holdings track each other.',
        "Pearson correlation of daily mark-price returns in each holding's own currency over the latest 252 dates, using days both were held (at least 40). One-day moves above 50% are treated as corporate actions and excluded.",
        'High correlations mean the holdings diversify each other less than the count suggests.',
    ),
    MetricDefinition(
        "Ending cash",
        "Currency-specific cash balance in the selected effective cash snapshot.",
        "Imported IBKR ending-cash value for the account, currency and snapshot date.",
        "Can include unsettled amounts and therefore differ from settled cash.",
    ),
    MetricDefinition(
        "Settled cash",
        "Currency-specific cash that IBKR reports as settled in the selected snapshot.",
        "Imported IBKR settled-cash value for the account, currency and snapshot date.",
        "Useful for settlement/liquidity review; it is not the same as total NAV or available margin.",
    ),
    MetricDefinition(
        "Cash activity amount",
        "Amount of one imported cash event in its stated currency.",
        "Imported event amount; positive and negative signs follow the source record.",
        "Interpret with event type and description. Dividends, fees, taxes, interest, deposits and withdrawals have different economic meaning.",
    ),
    MetricDefinition(
        "Quantity",
        "Number of units in a holding snapshot or executed trade.",
        "Imported IBKR position or execution quantity.",
        "Check asset type and source sign convention; quantity alone does not measure exposure.",
    ),
    MetricDefinition(
        "Market value",
        "Value assigned to a holding in the position snapshot.",
        "Imported IBKR market-value field for the effective snapshot; displayed with the row's currency context.",
        "A snapshot value, not a live executable price. Portfolio-level comparison requires the approved reporting-currency treatment.",
    ),
    MetricDefinition(
        "Trade proceeds",
        "Gross cash effect of an execution before the displayed commission and other net-cash components.",
        "Imported IBKR proceeds field; sign follows the source execution convention.",
        "Review together with side, quantity, price, commission and net cash.",
    ),
    MetricDefinition(
        "Commission",
        "Broker commission recorded for an execution, with its commission currency.",
        "Imported IBKR commission amount; sign follows the source record.",
        "It may be in a different currency from the account's reporting currency.",
    ),
    MetricDefinition(
        "Net cash",
        "Net cash impact recorded for an execution after the source's included components.",
        "Imported IBKR net-cash field.",
        "Reconcile unusual values to the original statement; do not infer the formula solely from screen columns.",
    ),
    MetricDefinition(
        "Realized P&L",
        "Profit or loss that IBKR attributes as realized for an execution.",
        "Imported IBKR realized-P&L field, using IBKR's lot and accounting treatment.",
        "It is not total return and can differ from tax or management-accounting P&L.",
    ),
    MetricDefinition(
        "Ready",
        "A factsheet and presentation pair is internally released and available for an integrity-checked download.",
        "Both required report types have registered release artifacts with matching account, period, path, MIME type, size and SHA-256 evidence.",
        "Ready does not grant client-publication permission.",
    ),
    MetricDefinition(
        "Ready for review",
        "Both report files passed generation checks and are available to the owner for review, but are not internally released.",
        "Both required report types have validated private review artifacts and no complete released pair.",
        "Download and reconcile both files; do not treat the status as approval.",
    ),
    MetricDefinition(
        "Generation pending",
        "The frozen month-end snapshot exists but a complete validated PDF/PPTX pair is not registered.",
        "At least one required report type lacks a completed review or released artifact.",
        "Investigate the month-end generation job and data-quality gates; the download control remains disabled.",
    ),
    MetricDefinition(
        "Quality status",
        "Result of deterministic validation checks attached to a dataset, benchmark, snapshot or process.",
        "Derived from configured pass, warning and failure rules for coverage, freshness, convention, reasonableness, licensing and integrity.",
        "A warning can permit internal analysis while still prohibiting client publication. A failure is a stop condition.",
    ),
    MetricDefinition(
        "Return observations",
        "Number of validated daily returns actually used by a performance or risk calculation.",
        "Count of common-date comparison rows after the initial base observation and validation exclusions.",
        "Can be smaller than NAV observations. Short samples make annualized and tail statistics less reliable.",
    ),
    MetricDefinition(
        "Tracked NAV subjects",
        "Number of distinct accounts or portfolios represented in the platform NAV coverage query.",
        "Count of distinct NAV subject identifiers in the validated platform dataset.",
        "A coverage count, not proof that every subject is current or fully onboarded.",
    ),
    MetricDefinition(
        "Latest portfolio date",
        "Most recent total-NAV snapshot date found across tracked subjects.",
        "Maximum validated total-NAV snapshot date in the platform coverage query.",
        "The platform-wide maximum can be newer than a particular account. Check the account's own effective date.",
    ),
    MetricDefinition(
        "Latest ingestion run",
        "Completion timestamp of the latest recorded successful ingestion process.",
        "Latest qualifying completed pipeline-run record.",
        "A completed process can still import no newer observation; compare it with data dates and row counts.",
    ),
    MetricDefinition(
        "Benchmark observations",
        "Count of stored observations for a configured benchmark series.",
        "Number of benchmark-price rows within the platform coverage query.",
        "Coverage count does not prove licensing, freshness or the correct index variant; read quality and metadata too.",
    ),
    MetricDefinition(
        "Assurance checks passed / warnings / failures",
        "Counts from the latest deterministic portfolio-analyst assurance run.",
        "Count each configured assurance result by its recorded status after reconciling data, analytics and report controls.",
        "Any failure blocks that day's acceptance. Warnings require review and can preserve publication restrictions.",
    ),
    MetricDefinition(
        "Consecutive passing days",
        "Number of uninterrupted daily acceptance results credited in the active burn-in window.",
        "Count sequential HKT dates with one passing monitor result; a missing or failed day breaks the sequence.",
        "The target is currently 14 consecutive days. It is an operational acceptance gate, not an investment-performance KPI.",
    ),
    MetricDefinition(
        "Open incidents",
        "Number of unresolved findings recorded by the controlled-autonomy monitor.",
        "Count of incident records whose resolution/closure state is still open.",
        "Read each severity, check ID, last-seen time and occurrence count; a zero count does not certify financial accuracy.",
    ),
    MetricDefinition(
        "Portal failure count",
        "Number of consecutive portal-boundary failures currently held by the recovery controller.",
        "Increment on a qualifying portal failure; reset by a qualifying success under controlled-autonomy-v1.",
        "Used only for the restricted recovery decision. It is not a count of all application errors.",
    ),
    MetricDefinition(
        "Rollback threshold",
        "Consecutive qualifying portal failures required before exact-revision recovery becomes eligible.",
        "Configured deterministic policy value; currently three.",
        "Reaching the threshold does not authorize arbitrary deployment—only the registered last-known-good revision can be activated while owner authority is current.",
    ),
    MetricDefinition(
        "Production authority",
        "Whether policy-limited autonomous repair authority is currently active.",
        "True only when the controlled-autonomy activation, monthly owner review and other policy gates are current.",
        "It does not give the AI general administrative power and does not permit IBKR retries, formula changes, trading or secret access.",
    ),
    MetricDefinition(
        "Monthly review due",
        "Deadline for the next owner review of autonomous administrator authority.",
        "Calculated from the approved monthly review policy and most recent approval.",
        "If overdue, repairs pause while observation and incident recording continue.",
    ),
    MetricDefinition(
        "Last-known-good revision",
        "Exact pre-registered website revision eligible for restricted recovery.",
        "Stored immutable revision name plus approved image/release evidence.",
        "It may lag the newest features. Any rollback requires a post-recovery access, feature and data check.",
    ),
    MetricDefinition(
        "Data freshness",
        "The badge at the top of an account page showing the date of its latest data and whether the daily refresh is on time.",
        "Compare the latest NAV date with the previous weekday in Hong Kong time. On or after it: current. On or after the weekday before that: one business day behind. Older: daily refresh overdue, with a link to System health.",
        "It checks the timing of the data, not its accuracy. A market holiday can make a correct refresh show as one business day behind.",
    ),
    MetricDefinition(
        "Day change",
        "The portfolio's time-weighted return for the latest valuation day, shown beside NAV on Overview.",
        "Time-weighted return from the last validated NAV before As of to the NAV on As of, using the period returns engine. External flows dated in that interval are removed with the half-flow Modified Dietz adjustment.",
        "After a weekend or holiday it covers every day since the previous valuation. It is not the change in NAV, which also moves with deposits and withdrawals.",
    ),
    MetricDefinition(
        "Time-weighted return (TWR)",
        "Investment performance over a period with the effect of deposits and withdrawals removed. Used for month, quarter and year to date, each calendar year, since inception and custom periods.",
        "∏(1 + rₜ) − 1, chain-linking daily Modified Dietz returns rₜ = (Vₜ − Vₜ₋₁ − Fₜ) ÷ (Vₜ₋₁ + Fₜ ÷ 2) between consecutive NAV dates. Each period runs from the last NAV strictly before its first day (for example the prior month-end for month to date) to the last NAV on or before its last day. Without an earlier NAV the period starts at inception and is marked partial. One invalid sub-period fails the whole period instead of being skipped.",
        "The standard way to judge investment results, because the timing and size of client deposits and withdrawals do not affect it. Partial periods are flagged.",
    ),
    MetricDefinition(
        "TWR annualised",
        "The time-weighted return expressed as an equivalent yearly rate.",
        "(1 + TWR)^(365 ÷ calendar days in the period) − 1. Shown only when the period covers at least 365 days and the growth factor is positive.",
        "Periods shorter than a year are deliberately not annualised, because stretching a few months to a year exaggerates good and bad runs.",
    ),
    MetricDefinition(
        "IRR annualised",
        "The money-weighted return: the yearly rate that reflects when, and how much, money was added or withdrawn.",
        "XIRR: the rate that makes Σ CFᵢ ÷ (1 + IRR)^(daysᵢ ÷ 365) = 0 for the opening NAV invested, each external flow invested on its date and the closing NAV returned. The Overview tile is since inception. For a period under one year the annualised figure extrapolates and carries a note; when no rate solves the cash flows it is shown as unavailable.",
        "It differs from TWR when large deposits or withdrawals come before strong or weak markets. TWR judges the investment decisions; IRR describes the investor's own experience.",
    ),
    MetricDefinition(
        "Net external flows",
        "Money the owner added to (contributions) or took out of (withdrawals) the account, as opposed to investment income or trading.",
        "Sum of dated external cash flows in the period, with contributions positive and withdrawals negative. Overview shows the year to date; the Performance tab lists each flow with monthly and yearly totals. Not applicable to fixed-unit composites.",
        "Flows change NAV but are not performance, which is why time-weighted returns remove them. Dividends, interest, fees and trades are not external flows.",
    ),
    MetricDefinition(
        "Benchmark return basis",
        "Whether a benchmark's returns include reinvested dividends (total return) or only price changes (price return).",
        "Taken from the benchmark's registered return convention. Price: price return, excluding dividends. Gross, net or fund total return: total return with dividends reinvested (net after withholding tax). An unregistered convention is shown as not registered.",
        "Portfolio returns include dividends and interest, so against a price index the comparison flatters the portfolio by roughly the index's dividend yield. The benchmark card and chart legend state the basis.",
    ),
    MetricDefinition(
        "Drawdown episode",
        "One fall from a high to a low and the climb back to that high, as listed in the drawdown episodes table.",
        "From the cash-flow-adjusted wealth index: the episode starts at the last high (peak), its depth is the lowest wealth ÷ peak − 1 reached (trough), and it ends on the first date wealth regains the peak (recovery). Days to trough, days to recover and length are calendar days. The table lists the five deepest episodes; one still open is marked ongoing.",
        "Shows not just how far the portfolio fell but how long it took to recover, which matters as much to most investors.",
    ),
    MetricDefinition(
        "Value in reporting currency",
        "A holding's market value converted into the portfolio's reporting currency so holdings can be added up and compared.",
        "Market value in the holding's own currency × the dated FX rate on or before the holdings date (a direct rate, or the reciprocal of the reverse rate). When no usable rate exists the row keeps its own-currency figures, the converted value is left blank and the row is flagged.",
        "Moves with both the security price and the exchange rate. Combined holdings convert every account into the currency you choose with each account's own dated FX history.",
    ),
    MetricDefinition(
        "Cost basis",
        "What was paid for the units currently held, as recorded by IBKR.",
        "Imported IBKR cost-basis field for the position, in the holding's own currency on the account Holdings tab. Combined holdings show it converted into the chosen currency.",
        "Uses IBKR's lot accounting. It is the starting point for unrealized P&L, not a tax figure.",
    ),
    MetricDefinition(
        "Unrealized P&L",
        "The gain or loss on units still held, before they are sold.",
        "IBKR's reported unrealized P&L when present; otherwise market value − cost basis. In the holding's own currency on the account Holdings tab and in the chosen currency on Combined holdings.",
        "A paper gain or loss that changes with the market. It becomes realized P&L only when the position is sold.",
    ),
    MetricDefinition(
        "P&L %",
        "Unrealized P&L as a percentage of what the position cost.",
        "Unrealized P&L ÷ |cost basis|. Blank when cost basis is missing or zero.",
        "Shows how far a position is above or below its cost. It ignores how long the position has been held, so it is not an annual return.",
    ),
    MetricDefinition(
        "Holding weight",
        "A holding's share of the portfolio.",
        "Value in reporting currency ÷ NAV on the holdings date. When NAV is unavailable or zero, weights use gross holdings value (Σ|value|) instead and the note says so. Combined holdings always use gross holdings value because cash is not aggregated across accounts.",
        "Weights on a NAV basis can sum to less or more than 100% because cash, margin and other NAV components are not holdings.",
    ),
    MetricDefinition(
        "RSI (14)",
        "A momentum gauge from 0 to 100 showing whether a holding's price has recently risen or fallen strongly.",
        "Wilder's 14-day relative strength index from the holding's daily closing mark prices in its own currency, on weekdays it was held: average gain and loss are seeded with the mean of the first 14 daily changes and then smoothed with weight 1/14; RSI = 100 − 100 ÷ (1 + average gain ÷ average loss). Up to 300 calendar days of marks are used. A one-day move above 50% (a likely split) or a gap of more than 14 days (a closed and reopened position) restarts the series. Blank when fewer than 15 marks remain or there is no mark on the holdings date.",
        "Above 70 is marked overbought and below 30 oversold. These are conventional signals of stretched momentum, not forecasts or recommendations, and a newly bought position has no RSI until it has 15 daily marks.",
    ),
    MetricDefinition(
        "Sector allocation",
        "How the holdings are spread across economic sectors, with sub-sectors and regions in the detail table.",
        "Group holdings by their stored sector classification and divide each group's absolute market value by gross holdings value; cash is excluded. Classification coverage is the share of gross holdings value that has a sector, and unclassified holdings are named.",
        "Classifications come from an external provider and are for internal analysis. Low coverage means the chart describes only part of the portfolio.",
    ),
    # Risk tab: stored crisis scenarios (holdings-crisis-scenarios-v1).
    MetricDefinition(
        "Crisis scenario maximum drawdown",
        "Hypothetical worst fall from a high if today's holdings had been held through one of four historical sell-offs.",
        "Windows: Lehman collapse and global financial crisis (15/9/2008–9/3/2009), China and Hong Kong market crash (12/6/2015–11/2/2016), COVID-19 crash (19/2/2020–23/3/2020) and rate rises and technology sell-off (3/1/2022–31/10/2022), on SPY's trading calendar. Holdings at the latest holdings date keep fixed units from their opening market-value-to-NAV weights; cash stays flat; no rebalancing, financing or trading costs. A holding with Yahoo Finance adjusted closes on at least 90% of the window's sessions uses its own prices; otherwise it uses its recorded stand-in ETF, scaled by the holding's beta to that ETF on weekly returns over the two years to the holdings date (kept between 0.25 and 3). Composites combine their sleeves' paths with the Hang Seng and SPY hedges and HKD FX the way their own NAV combines them. Result = most negative value ÷ running peak − 1.",
        "Not the account's actual performance in those periods. Stand-ins and provisional Yahoo prices drive much of the result, so read it with the Stand-ins column. Internal and shareholder use only.",
    ),
    MetricDefinition(
        "Crisis scenario stand-ins (scaled)",
        "How many holdings in a crisis scenario were replayed on a stand-in fund instead of their own prices, and how many of those were scaled by beta.",
        "Count of holdings (including composite sleeve components) replayed by the market-proxy method; the bracketed figure counts those whose beta was not 1. Holdings on their own prices and hedge closes are not counted.",
        "The larger the count relative to the number of holdings, the more the scenario describes the stand-ins rather than the holdings themselves.",
    ),
    # Modelling tab: wealth projection, risk outlook and market regimes (portfolio-modelling-v1).
    MetricDefinition(
        "Wealth projection range",
        "The spread of possible future portfolio values after regular contributions or withdrawals, from thousands of simulated futures.",
        "Monthly returns are the compounded returns of every overlapping 21-session window of the portfolio's cash-flow-adjusted daily returns (a block bootstrap; at least 63 daily returns). Each of 2,000 paths draws one such month at random for every month of the 1–30 year horizon, applies it and then adds the monthly contribution (negative to withdraw) at month end; a path that reaches zero stays there. Bands are the 5th (weak case), 25th, 50th (median), 75th and 95th (strong case) percentiles of value at each month. An optional return assumption shifts every sampled month in log terms so the average compounds to that annual rate while keeping the historical spread. The random seed is fixed by the inputs, so the same inputs give the same result.",
        "The median is the middle outcome, not an expected or promised value; only 1 path in 20 ends below the weak case or above the strong case. A short history or a historical growth rate above 20% a year can make the range look too good, and the page warns when either applies.",
    ),
    MetricDefinition(
        "Chance of reaching the target",
        "Share of simulated futures in which the portfolio ends the horizon at or above the target value entered.",
        "Count of the 2,000 wealth-projection paths whose final value ≥ target ÷ 2,000. Blank when no target is entered.",
        "A model-based likelihood from past monthly returns, not a guarantee. Try a lower return assumption to see how sensitive it is.",
    ),
    MetricDefinition(
        "Chance of running out",
        "Share of simulated futures in which withdrawals take the portfolio's value to zero before the horizon ends.",
        "Count of wealth-projection paths whose value reaches zero in any month ÷ 2,000. Such a path stays at zero for the rest of the horizon.",
        "Mainly relevant when the monthly amount is a withdrawal. A small percentage is not zero risk.",
    ),
    MetricDefinition(
        "Chance of ending below money put in",
        "Share of simulated futures that end below the starting value plus all contributions, net of withdrawals.",
        "Count of wealth-projection paths with final value < starting value + monthly amount × number of months, ÷ 2,000.",
        "A plain-language measure of the chance of losing money over the whole horizon, after the money added or taken out.",
    ),
    MetricDefinition(
        "Projection return and volatility used",
        "The annual growth rate and volatility built into the wealth projection's sampled months.",
        "Return used = exp(12 × mean of the log monthly returns sampled) − 1, labelled historical or your assumption. Volatility used = sample standard deviation of the sampled monthly returns × √12.",
        "Check these before reading the fan: if the historical return looks unusually high, the projection inherits it.",
    ),
    MetricDefinition(
        "Forecast volatility",
        "How volatile the portfolio is expected to be from today, annualised, given how recent moves compare with its normal level.",
        "Daily cash-flow-adjusted returns minus their average (at least 126). With at least 250 returns a GARCH(1,1) model σ²ₜ₊₁ = ω + α·r²ₜ + β·σ²ₜ is fitted by maximum likelihood with variance targeting: ω = sample variance × (1 − α − β). With less history, or when GARCH fits no better than constant volatility, an EWMA with λ = 0.94 is used. Next-day forecast = σₜ₊₁ × √252. Next-month forecast = √(average expected daily variance over the next 21 sessions × 252), where expected variance moves back toward the long-run level by the factor α + β each day (EWMA stays flat).",
        "Above the long-run level means recent moves have been larger than usual and the model expects that to fade. It is a model estimate, not a measured figure, and says nothing about direction.",
    ),
    MetricDefinition(
        "Long-run volatility",
        "The portfolio's normal level of volatility, which the volatility forecast drifts back to.",
        "√(mean of squared demeaned daily returns over the whole history × 252). The page also shows realised volatility over the latest 252 returns for comparison.",
        "The daily risk watch raises an alert when next-month forecast volatility reaches 1.5 times this level.",
    ),
    MetricDefinition(
        "Shock half-life",
        "How many trading days it takes for half of a volatility spike to fade, according to the fitted model.",
        "ln(0.5) ÷ ln(α + β) from the GARCH(1,1) fit. Unavailable when the EWMA fallback is used, because EWMA does not revert to a long-run level.",
        "A longer half-life means turbulence tends to linger. It describes the past fit, not a promise about the next shock.",
    ),
    MetricDefinition(
        "Forward VaR",
        "The loss the portfolio is expected to exceed on about 1 day in 20 (95%) or 1 day in 100 (99%), and in about 1 month in 20, at today's forecast volatility.",
        "Filtered historical simulation: divide each past demeaned return by that day's model volatility to get standardised returns. One-day VaR = −(5th or 1st percentile of standardised returns) × next-day forecast volatility. One-month VaR: 2,000 simulated 21-session paths, each day drawing a standardised return at random and rescaling it by the path's evolving GARCH volatility; VaR is the loss at the 5th percentile of compounded path returns.",
        "Shown as a positive loss. Unlike the Risk tab's historical VaR it moves with current conditions, so it rises after turbulent days. It does not say how large losses beyond it can be.",
    ),
    MetricDefinition(
        "Forward expected tail loss",
        "The average loss on the days (or months) that go beyond the forward VaR.",
        "Mean of the standardised returns at or below the VaR cutoff × next-day forecast volatility for one day; mean of the simulated 21-session returns at or below the 5th percentile for one month. Shown as a positive loss.",
        "Always at least as large as the matching forward VaR. Like any simulation from history, it can understate a crisis unlike anything in the data.",
    ),
    MetricDefinition(
        "VaR backtest",
        "Whether the 95% one-day VaR line would have held in the past: how often losses actually broke it compared with the 5% expected.",
        "Expanding window: after the first 250 returns (at least 63 more are needed to test), each day's VaR uses only earlier returns, with the model refitted every 63 sessions. A breach is a return below −VaR. Kupiec's proportion-of-failures test compares breaches with the expected 5% (likelihood ratio, chi-squared with one degree of freedom); p ≥ 0.05 is in line with the model.",
        "More breaches than expected means the VaR has been too low; fewer means it has been cautious. The monthly risk model check in the Agent inbox repeats this verdict.",
    ),
    MetricDefinition(
        "Drawdown odds (next 12 months)",
        "The chance of a fall of at least 10%, 20% or 30% from a high at some point in the next year, with the typical and bad-case worst fall.",
        "2,000 simulated 252-session paths built from random overlapping 21-session stretches of the portfolio's own daily returns; each path records its worst value ÷ running peak − 1. Probability = share of paths whose worst fall reaches the threshold; typical worst fall = median, bad case = 5th percentile. Shown twice: at historical volatility, and with each return's distance from the average rescaled by next-month forecast volatility ÷ long-run volatility.",
        "Differs from the Risk tab's expected maximum drawdown, which uses five-session blocks and 5,000 paths over benchmark-aligned dates. A gap between the two rows shows how much today's conditions change the picture.",
    ),
    MetricDefinition(
        "Market regime",
        "Whether the portfolio is in a calm, normal or stressed period, judged from the pattern of its own daily returns.",
        "A Gaussian hidden Markov model with two or three states, fitted by Baum-Welch to up to five years (1,260 sessions; at least 252) of cash-flow-adjusted daily returns. The number of states with the lower BIC is used, provided every state covers at least 3% of the time; states are named by volatility. Chance now = filtered probability using data up to today only; chance in a month = today's probabilities moved 21 steps through the fitted transition matrix; typical length = 1 ÷ (1 − probability of staying); share of time = average filtered probability.",
        "A description of the portfolio's own behaviour, not a market call. Stressed means stressed relative to this portfolio's history, and labels can shift when the model is refitted on new data.",
    ),
    MetricDefinition(
        "Regime outlook range",
        "A range for the portfolio's return over the next one, two and three months that allows for switching between regimes.",
        "2,000 simulated paths start from today's regime probabilities, switch regime each day with the fitted odds and draw each day's return from past days assigned to that regime (from the regime's normal distribution when it has fewer than 20 days). Bands are the 5th (weak case), 25th, 50th (middle), 75th and 95th (strong case) percentiles of compounded return after 21, 42 and 63 sessions; chance of a loss = share of paths below zero.",
        "Read the range, not the middle line, unless the outlook score shows directional skill.",
    ),
    MetricDefinition(
        "Outlook score",
        "How well past one-month outlooks would have held up: how often the actual result landed in the range, and whether the middle line called the direction.",
        "Walk-forward: after two years of history, at each later 21-session step (at least 12 tested), refit on the data available then (every 63 sessions), simulate 500 paths and compare with the next 21 sessions' actual return. 90% range held = share inside the 5th–95th percentiles (80%–97% counts as well calibrated); middle 50% range held = share inside the 25th–75th. Direction right = share where the median's sign matched the outcome, against simply assuming the more common past direction; directional skill needs more hits than that rule and a one-sided binomial p below 0.1.",
        "A guard against over-trusting the model. No reliable directional skill is a common, honest result: use the range only.",
    ),
    # Modelling tab: what-if, optimisation, factor model and stress test (portfolio-modelling-v1).
    MetricDefinition(
        "What-if comparison",
        "Risk and return of a proposed set of weights against today's, both replayed over the same year of daily prices.",
        "Holding price returns over the latest 252 shared dates (at least 63). A date without a mark counts as 0%, so the next return covers the whole move; one-day moves above 50% (likely splits) are dropped; holdings with returns on fewer than 60% of dates are left out and listed. Each mix's daily return = Σ weight × holding return + (1 − Σ weights) × daily risk-free rate (a negative remainder is borrowing at that rate). Weights are shares of NAV, long only, up to 200% in total; up to five tickers not held can be added from Yahoo Finance. Measures: annual return (growth^(252 ÷ N) − 1), volatility (standard deviation × √252), Sharpe ((annual return − risk-free) ÷ volatility), daily VaR and expected tail loss (95%), maximum drawdown, worst 21-session month and beta to the benchmark (at least 40 common dates).",
        "Both columns use holding prices, not the account's NAV, so the current column can differ from the Risk tab. A lower-risk proposal over one past year is not evidence it will be lower risk in future, and nothing here is a recommendation.",
    ),
    MetricDefinition(
        "Worst month (21 sessions)",
        "The weakest return over any run of 21 trading sessions in the what-if window.",
        "Minimum of the compounded returns of every overlapping 21-session window of the mix's daily returns.",
        "Rolling 21-session windows can start mid-month, so this can be worse than the worst calendar month.",
    ),
    MetricDefinition(
        "What-if crisis replay",
        "How today's mix and the proposed mix would each have fared through the stored crisis scenarios.",
        "For each crisis scenario, each holding reuses the price path the stored scenario used (its own prices, or its beta-scaled stand-in). A ticker added in the what-if uses its own prices if it traded through the window, otherwise SPY at a beta of 1, as does a holding the stored run did not cover. Weights are shares of NAV; the rest is flat cash. Worst fall = most negative value ÷ running peak − 1; whole window = value at the end of the window − 1.",
        "Hypothetical, with the same stand-in and provisional-price limits as the crisis scenarios on the Risk tab.",
    ),
    MetricDefinition(
        "Share of risk",
        "How much of the portfolio's total volatility each holding is responsible for.",
        "wᵢ × (Σw)ᵢ ÷ w′Σw, which sums to 100%. Σ is the annualised covariance of daily price returns over the latest 252 shared dates, with off-diagonal terms shrunk 20% toward zero. Weights are shares of the invested sleeve for up to the 20 largest holdings with enough price history; cash and the rest stay outside.",
        "A holding can carry far more risk than its weight when it is volatile or moves with the rest. The weekly brief mentions a holding whose share of risk stands out.",
    ),
    MetricDefinition(
        "Expected return (optimisation)",
        "The return assumption the optimiser uses for each holding and each mix.",
        "Each holding's average daily price return over the window × 252, shrunk 50% toward the average across the holdings, because one year of averages is noisy. A mix's expected return = Σ weight × holding expected return.",
        "A modelling input, not a forecast. Shrinking stops one strong year from dominating the optimised mixes.",
    ),
    MetricDefinition(
        "Minimum variance portfolio",
        "The mix of the current holdings with the lowest modelled volatility.",
        "Minimise w′Σw subject to long only, fully invested and at most the chosen cap per holding (default 25%, raised to at least 1 ÷ number of holdings), solved by accelerated projected gradient.",
        "Lowest risk on this covariance estimate, not necessarily the best return. A hypothetical alternative, not a recommendation.",
    ),
    MetricDefinition(
        "Risk parity portfolio",
        "The mix of the current holdings in which each holding carries an equal share of risk.",
        "Equal-risk-contribution weights by cyclical coordinate descent on Σ, long only and fully invested. The per-holding cap does not apply.",
        "Gives more weight to calmer holdings. Equal risk shares are not equal weights.",
    ),
    MetricDefinition(
        "Maximum Sharpe portfolio",
        "The mix of the current holdings with the best expected return per unit of risk on the efficient frontier.",
        "The frontier point with the highest (expected return − risk-free rate) ÷ volatility, under the same long-only, fully invested and capped constraints.",
        "The most sensitive of the three mixes to the expected-return assumption; small changes in inputs can move it a lot.",
    ),
    MetricDefinition(
        "Efficient frontier",
        "The curve of the lowest modelled volatility available for each level of expected return from the current holdings.",
        "Up to 30 points minimising w′Σw − λ·μ′w for increasing λ, long only, fully invested and capped. The chart also marks today's mix and the three optimised mixes.",
        "A mix to the right of the curve takes more risk than needed for its expected return, on these estimates.",
    ),
    MetricDefinition(
        "Factor beta",
        "The portfolio's sensitivity to each of six well-known return drivers: market, size, value, profitability, investment style and momentum.",
        "Weekly ordinary least squares with an intercept: portfolio weekly return (cash-flow-adjusted, compounded Monday to Friday) minus the risk-free rate, regressed on weekly Fama-French five-factor plus momentum returns over the latest 26–156 complete weeks. Factors come from Kenneth French's data library for the chosen region (United States, Developed markets or Asia Pacific ex Japan), or, when it cannot be reached, from long-short ETF stand-ins on Yahoo Finance; the page says which.",
        "A beta of 1.1 to the market means the portfolio has tended to move 1.1% for each 1% market move beyond cash. Weekly data avoids mismatched closing times across markets.",
    ),
    MetricDefinition(
        "Factor t-statistic",
        "How clearly each factor beta is distinguished from zero.",
        "Beta ÷ its standard error from the regression. |t| ≥ 2 is marked significant.",
        "A large beta with a small t-statistic may be noise. Significance is about the past window only.",
    ),
    MetricDefinition(
        "Alpha (factor model)",
        "The average annual return the six factors do not explain.",
        "The regression intercept × 52, with its t-statistic.",
        "Positive alpha with |t| below 2 is not strong evidence of skill. Alpha also absorbs fees and anything the factors miss.",
    ),
    MetricDefinition(
        "Explained by factors (R²)",
        "The share of the portfolio's weekly return variation that the factors explain.",
        "1 − sum of squared residuals ÷ total sum of squares; the adjusted R² is also kept.",
        "Low R² means the factor betas and stress tests describe only part of the portfolio's behaviour.",
    ),
    MetricDefinition(
        "Unexplained volatility",
        "Annualised volatility of the part of weekly returns the factors do not explain.",
        "Standard deviation of the regression residuals × √52.",
        "Higher means more stock-specific or unmodelled risk.",
    ),
    MetricDefinition(
        "Factor return contribution",
        "How much of the portfolio's annual return over the window came from each factor exposure.",
        "Factor beta × the factor's average weekly return over the window × 52.",
        "Separates return from broad drivers from what is left in alpha. It describes the window, not future returns.",
    ),
    MetricDefinition(
        "Factor stress test impact",
        "The instant portfolio move implied by its factor betas for a one-off shock to the factors.",
        "Σ factor beta × shock, using preset scenarios (market falls 20%; growth and momentum sell-off; flight to quality; small-cap and value rally; rotation from growth to value) or custom shocks between −60% and +60%. Alpha and the risk-free rate are left out; factors not in the fitted model are ignored and listed.",
        "Only as good as the factor fit: read it with R² and the typical unexplained move. Not a prediction that the scenario will happen.",
    ),
    MetricDefinition(
        "Typical unexplained move in a month",
        "How far the real outcome of a stress test could differ from the factor estimate, either way, in a typical month.",
        "1.645 × annual unexplained volatility ÷ √12, roughly the 90% range of the monthly residual.",
        "A reminder that the stress test explains only part of the portfolio's moves.",
    ),
    # Exports tab: the export agent's checks before download.
    MetricDefinition(
        "Export check result",
        "Whether an export built from a plain-language request passed the checks that run before it can be downloaded.",
        "Every file is read back and checked: Fail if any check fails, Review if any needs review, otherwise Pass. Info and Not checked lines explain what was or could not be tied out and do not change the result. Download rebuilds the files and serves them only if they match the checked fingerprint byte for byte; a failed export cannot be downloaded and one needing review is downloaded only after a person releases it.",
        "Pass means the file ties to the website's own records, not that it has been reviewed for client distribution.",
    ),
    MetricDefinition(
        "Last value matches the books",
        "Whether the last account value in an exported performance file equals the account's value on that day.",
        "Compare the final total value in the file with the stored validated NAV for that date; they must be equal. Not checked when the books have no value for that day.",
        "A mismatch fails the export, because the file would disagree with the website.",
    ),
    MetricDefinition(
        "Holdings and cash add up to the account's value",
        "Whether exported holdings plus cash reconcile to the account's value on the holdings date.",
        "Positions + cash − NAV on the snapshot date; pass when the difference is within the larger of 1 unit or 1% of NAV, otherwise review. Not checked for composites or for files holding only some currencies or symbols.",
        "Accrued interest and dividends make up most of a small difference.",
    ),
    MetricDefinition(
        "Cash balances roll forward",
        "Whether opening cash plus cash activity and trades reaches closing cash in each currency over an exported period.",
        "Closing − opening − cash activity − trade cash per currency; pass within the larger of 1 unit or 0.5% of closing cash, otherwise shown as information. Broker accounts only, and only when the file holds every kind of cash activity.",
        "Currency conversions booked elsewhere on the statement are the usual reason for a residue, so it is shown rather than held.",
    ),
    MetricDefinition(
        "Totals tie to the file",
        "Whether the totals file in an export equals the sums of its detail file.",
        "Every total is recomputed from the detail file (or the selected rows, when the file leaves out columns the totals need) with exact decimal arithmetic and compared with the written totals.",
        "Any difference fails the export.",
    ),
    MetricDefinition(
        "No long gaps in the value history",
        "Whether an exported value history skips more than ten days between values.",
        "Largest number of calendar days between consecutive values in the file; review when it is more than 10.",
        "Long gaps usually mean missing valuations for that stretch, worth checking before the file is used.",
    ),
    MetricDefinition(
        "Export pack",
        "A saved export request that is re-run every month, quarter or year.",
        "When a pack's period closes, a daily job leaves an Agent inbox card; opening it re-runs the plan for the new dates on the Exports tab, with the same checks before download.",
        "Nothing is built or sent automatically; a person still checks and downloads each run.",
    ),
    # Agent inbox: the daily risk watch.
    MetricDefinition(
        "Risk alert",
        "An Agent inbox card raised on weekday mornings when an account's risk models flag a notable change.",
        "Three fixed rules, checked in code: a VaR breach (the latest day's return broke the 95% one-day VaR that the VaR backtest forecast the day before from earlier data); a move into the stressed regime (the regime model puts the account in its stressed regime at least 60% likely, when it was not there a week ago); and a volatility jump (next-month forecast volatility at least 1.5 times long-run volatility, at most once a week). An agent then writes an explanation from the account's figures; if the explanation cannot be checked against them it is left out.",
        "A prompt to look, not an instruction to trade. Open the Modelling tab's Risk outlook and Market regimes for the detail.",
    ),
    MetricDefinition(
        "Risk model check",
        "A monthly Agent inbox card saying whether each account's risk models have held up against what actually happened.",
        "Lists an issue when the VaR backtest's Kupiec p-value is below 0.05, or when the regime outlook's 90% range held the next month's outcome outside 80%–97% of the time; otherwise the check reads in line. It also says whether the outlook shows directional skill.",
        "When the check lists an issue, treat the matching forward VaR, tail-loss or outlook figures with caution until it is back in line.",
    ),
)


# Sections follow the website's own tabs and subpages so a reader can find a
# number where they saw it.  Keys are the section deep-link keys; metric anchors
# come from the metric names, so regroup rather than rename.  Each section names
# where its figures appear; METRIC_LOCATIONS narrows that for single metrics.
_LAYOUT = (
    (
        "overview",
        "Overview",
        "Overview and period returns",
        "Account value, day change, period returns, the money-weighted return and external flows shown on Overview and on the Performance tab's Period returns and Contributions pages (performance-periods-v1).",
        "Overview · Performance › Period returns",
        ("As of", "Data freshness", "Net asset value (NAV)", "Day change", "Time-weighted return (TWR)",
         "TWR annualised", "IRR annualised", "Net external flows", "Positions", "Reporting currency",
         "Observations"),
    ),
    (
        "benchmark",
        "Performance",
        "Performance: NAV chart, monthly returns, attribution and benchmark",
        "The Performance tab's NAV chart, Monthly returns, Attribution and Vs benchmark pages. Benchmark comparisons use only dates on which both the portfolio and the benchmark have validated values (benchmark-comparison-v1); portfolio returns adjust for external cash flows and benchmark returns use closing levels. Monthly returns and attribution use every validated NAV date (portfolio-analytics-v1).",
        "Performance › Vs benchmark",
        ("Cash-flow-adjusted daily portfolio return", "Daily benchmark return", "Benchmark return basis",
         "Rebased portfolio or benchmark level", "Portfolio total return", "Benchmark total return",
         "Excess total return", "Tracking error", "Information ratio", "Beta", "Correlation",
         "Relative win rate", "Monthly return", "Contribution to return"),
    ),
    (
        "holdings",
        "Holdings",
        "Holdings and concentration",
        "Position values from the latest validated holdings snapshot on or before As of, converted into the reporting currency with dated FX (holdings-view-v1), plus the concentration measures on the Holdings tab.",
        "Holdings",
        ("Quantity", "Market value", "Value in reporting currency", "Cost basis", "Unrealized P&L", "P&L %",
         "Holding weight", "RSI (14)", "Sector allocation", "Currency exposure", "Largest holding", "Top 5 weight",
         "Top 10 weight", "Effective number of holdings"),
    ),
    (
        "risk",
        "Risk snapshot",
        "Risk: snapshot, tail loss and crisis scenarios",
        "The Risk tab's Risk snapshot page. Its metrics use validated, cash-flow-adjusted returns aligned to the primary benchmark's trading dates through As of (benchmark-comparison-v1). The contributor tables explain which unchanged USD positions drove Expected Tail Loss on historical tail days, and the crisis scenarios replay today's holdings through four past sell-offs (holdings-crisis-scenarios-v1).",
        "Risk › Risk snapshot",
        ("Total return", "Annualized return", "Annualized volatility", "Risk-free rate", "Sharpe ratio",
         "Expected maximum drawdown (1 year)", "Actual maximum drawdown (1 year)",
         "Tail maximum drawdown (1 year)", "Crisis maximum drawdown (15/9/2008–9/3/2009)",
         "VaR 95% — daily", "Daily Expected Tail Loss (95%)", "Win rate", "BLUE mean daily return",
         "Expected Tail Loss contribution", "Top 5 ETL contributors", "Bottom 5 ETL contributors",
         "ETL residual contribution", "Crisis scenario maximum drawdown", "Crisis scenario stand-ins (scaled)"),
    ),
    (
        "statistics",
        "Risk statistics",
        "Risk: statistics, drawdowns, rolling risk and correlation",
        "The Risk tab's Statistics, Drawdowns, Rolling risk and Correlation pages (portfolio-analytics-v1). They use every validated NAV date and the same cash-flow-adjusted returns as the period returns table, so they can differ slightly from the benchmark-aligned Risk snapshot.",
        "Risk › Statistics",
        ("Sortino ratio", "Calmar ratio", "Maximum drawdown (since inception)", "Current drawdown",
         "Drawdown episode", "Downside deviation", "Skewness", "Excess kurtosis", "Best and worst day",
         "Best and worst month", "Positive months", "Average gain and loss month", "Gain/loss ratio",
         "Rolling 3-month volatility", "Trailing 12-month return", "Rolling beta and correlation",
         "Holdings correlation"),
    ),
    (
        "modelling-outlook",
        "Modelling: outlook",
        "Modelling: wealth projection, risk outlook and market regimes",
        "The Modelling tab's forward-looking pages (portfolio-modelling-v1). Every figure is a hypothetical model built from the portfolio's own cash-flow-adjusted daily returns. None is a forecast, a guarantee or investment advice, and nothing is written to the database. Simulations use a seed fixed by the inputs, so the same data gives the same result.",
        "Modelling › Risk outlook",
        ("Wealth projection range", "Chance of reaching the target", "Chance of running out",
         "Chance of ending below money put in", "Projection return and volatility used", "Forecast volatility",
         "Long-run volatility", "Shock half-life", "Forward VaR", "Forward expected tail loss", "VaR backtest",
         "Drawdown odds (next 12 months)", "Market regime", "Regime outlook range", "Outlook score"),
    ),
    (
        "modelling-portfolio",
        "Modelling: portfolio",
        "Modelling: what-if, optimisation, factor model and stress test",
        "The Modelling tab's pages that work from the holdings' own prices or from factor data (portfolio-modelling-v1). They are hypothetical and are not recommendations. Holdings models are not available for composite sleeves, which have no security-level price history.",
        "Modelling › What-if",
        ("What-if comparison", "Worst month (21 sessions)", "What-if crisis replay", "Share of risk",
         "Expected return (optimisation)", "Minimum variance portfolio", "Risk parity portfolio",
         "Maximum Sharpe portfolio", "Efficient frontier", "Factor beta", "Factor t-statistic",
         "Alpha (factor model)", "Explained by factors (R²)", "Unexplained volatility",
         "Factor return contribution", "Factor stress test impact", "Typical unexplained move in a month"),
    ),
    (
        "cash-trades",
        "Cash and trades",
        "Cash and trades",
        "Cash balances, cash events and executed trades imported from the daily IBKR refresh, and the trading-activity measures built from them.",
        "Cash · Trade history",
        ("Ending cash", "Settled cash", "Cash activity amount", "Trade proceeds", "Commission", "Net cash",
         "Realized P&L", "Trading activity", "Portfolio turnover", "Commissions / NAV"),
    ),
    (
        "reports",
        "Reports",
        "Reports and data quality",
        "Operational labels that decide whether data or report files may be used.",
        "Monthly reports",
        ("Ready", "Ready for review", "Generation pending", "Quality status", "Return observations"),
    ),
    (
        "exports",
        "Exports",
        "Data exports and the checks before download",
        "The Data exports tab. The export agent turns a plain-language request into a plan, but every row, total and check is produced by code from the same reads as the CSV buttons, and the files are checked against the books before anyone can download them.",
        "Data exports",
        ("Export check result", "Last value matches the books", "Holdings and cash add up to the account's value",
         "Cash balances roll forward", "Totals tie to the file", "No long gaps in the value history",
         "Export pack"),
    ),
    (
        "agents",
        "Agent inbox",
        "Agent inbox: risk alerts and model checks",
        "Cards the daily risk watch leaves in the Agent inbox. The rules that raise them are fixed in code; an agent only writes the explanation, and nothing is sent, published or traded.",
        "Agent inbox",
        ("Risk alert", "Risk model check"),
    ),
    (
        "system",
        "System",
        "System health and administration",
        "Platform-level operating indicators. They monitor process health but do not replace financial reconciliation.",
        "System health",
        ("Tracked NAV subjects", "Latest portfolio date", "Latest ingestion run", "Benchmark observations",
         "Assurance checks passed / warnings / failures", "Consecutive passing days", "Open incidents",
         "Portal failure count", "Rollback threshold", "Production authority", "Monthly review due",
         "Last-known-good revision"),
    ),
)

# Where a single metric appears when it is not on its section's main page.
METRIC_LOCATIONS = {
    "Net external flows": "Overview · Performance › Contributions",
    "Data freshness": "Account page header · System health",
    "Rebased portfolio or benchmark level": "Performance › NAV chart · Performance › Vs benchmark",
    "Monthly return": "Performance › Monthly returns",
    "Contribution to return": "Performance › Attribution",
    "Holding weight": "Holdings · Overview",
    "Sector allocation": "Holdings · Overview",
    "Crisis scenario maximum drawdown": "Risk › Risk snapshot, Crisis scenarios",
    "Crisis scenario stand-ins (scaled)": "Risk › Risk snapshot, Crisis scenarios",
    "Drawdown episode": "Risk › Drawdowns",
    "Rolling 3-month volatility": "Risk › Rolling risk",
    "Trailing 12-month return": "Risk › Rolling risk",
    "Rolling beta and correlation": "Risk › Rolling risk",
    "Holdings correlation": "Risk › Correlation",
    "Wealth projection range": "Modelling › Wealth projection",
    "Chance of reaching the target": "Modelling › Wealth projection",
    "Chance of running out": "Modelling › Wealth projection",
    "Chance of ending below money put in": "Modelling › Wealth projection",
    "Projection return and volatility used": "Modelling › Wealth projection",
    "VaR backtest": "Modelling › Risk outlook · Agent inbox",
    "Market regime": "Modelling › Market regimes",
    "Regime outlook range": "Modelling › Market regimes",
    "Outlook score": "Modelling › Market regimes · Agent inbox",
    "Share of risk": "Modelling › Optimisation",
    "Expected return (optimisation)": "Modelling › Optimisation",
    "Minimum variance portfolio": "Modelling › Optimisation",
    "Risk parity portfolio": "Modelling › Optimisation",
    "Maximum Sharpe portfolio": "Modelling › Optimisation",
    "Efficient frontier": "Modelling › Optimisation",
    "Factor beta": "Modelling › Factor model",
    "Factor t-statistic": "Modelling › Factor model",
    "Alpha (factor model)": "Modelling › Factor model",
    "Explained by factors (R²)": "Modelling › Factor model",
    "Unexplained volatility": "Modelling › Factor model",
    "Factor return contribution": "Modelling › Factor model",
    "Factor stress test impact": "Modelling › Stress test",
    "Typical unexplained move in a month": "Modelling › Stress test",
    "Export pack": "Data exports · Agent inbox",
    "Return observations": "Risk › Statistics · Monthly reports",
}
_BY_NAME = {metric.name: metric for metric in _DEFINITIONS}
if len(_BY_NAME) != len(_DEFINITIONS):
    raise RuntimeError("Metric guide names must be unique")
METRIC_SECTIONS = tuple(
    MetricSection(key, short_title, title, introduction, tuple(_BY_NAME[name] for name in names), location)
    for key, short_title, title, introduction, location, names in _LAYOUT
)
_placed = [metric.name for section in METRIC_SECTIONS for metric in section.metrics]
if sorted(_placed) != sorted(_BY_NAME):
    raise RuntimeError("Every metric definition must appear in exactly one guide section")


def definition(name: str) -> MetricDefinition:
    """One definition by its exact name (KeyError if the guide has no such entry)."""

    return _BY_NAME[name]


def metric_anchor(name: str) -> str:
    """Return the stable URL fragment used to deep-link one definition."""

    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return f"metric-{slug}"


METRIC_ANCHORS = {
    metric.name: metric_anchor(metric.name)
    for section in METRIC_SECTIONS
    for metric in section.metrics
}
if len(METRIC_ANCHORS) != sum(len(section.metrics) for section in METRIC_SECTIONS):
    raise RuntimeError("Metric guide names and deep-link anchors must be unique")


# These explanations deliberately sit beside, rather than replace, the exact
# methodology above.  The short wording is suitable for a client conversation;
# the expandable technical method remains the calculation source of truth.
CLIENT_CALCULATIONS = {
    "As of": "Choose a date. The website then uses the newest validated information available on or before that date.",
    "Net asset value (NAV)": "Use the total account value reported in the validated IBKR snapshot for the selected date.",
    "Observations": "Count the validated NAV dates available from the account's first valuation up to the selected date.",
    "Positions": "Count the holding records in the latest validated portfolio snapshot available by the selected date.",
    "Reporting currency": "Use the account's agreed reporting currency and convert values into it before portfolio-level comparisons are made.",
    "Rebased portfolio or benchmark level": "Give both series a starting value of 100, then apply each day's percentage change. This makes their growth easy to compare on the same scale.",
    "Cash-flow-adjusted daily portfolio return": "Compare today's NAV with the previous NAV after removing external money added to or withdrawn from the account. The method assumes cash flows occurred halfway through the period.",
    "Daily benchmark return": "Compare today's validated benchmark closing level with the previous common-date closing level.",
    "Portfolio total return": "Link all validated daily portfolio returns together so gains and losses compound over time.",
    "Benchmark total return": "Link all validated daily benchmark returns together over exactly the same comparison dates.",
    "Excess total return": "Subtract the benchmark's total return from the portfolio's total return for the same period.",
    "Tracking error": "Measure how much the portfolio's daily return difference versus the benchmark varies, then express that variability on an annual basis.",
    "Information ratio": "Divide the portfolio's average return above or below the benchmark by tracking error, then annualize the result.",
    "Beta": "Compare how the portfolio and benchmark moved together to estimate how sensitive the portfolio was to benchmark movements.",
    "Correlation": "Measure how consistently the portfolio and benchmark moved in the same or opposite direction, on a scale from −1 to +1.",
    "Relative win rate": "Count the days when the portfolio beat the benchmark and divide by all common comparison days.",
    "Total return": "Link all validated daily portfolio returns together, allowing each day's gain or loss to build on the previous day's value.",
    "Annualized return": "Convert the portfolio's total growth over the measured period into an equivalent yearly growth rate.",
    "Annualized volatility": "Measure the typical variation in daily returns and scale it to a 252-trading-day year.",
    "Risk-free rate": "Use the latest validated 3-month U.S. Treasury yield for USD portfolios, or the average published 1-month HIBOR fixing for HKD composites.",
    "Sharpe ratio": "Subtract the daily equivalent of the risk-free rate from average daily portfolio return, then divide by daily volatility and annualize.",
    "Expected maximum drawdown (1 year)": "Create 5,000 realistic one-year return paths from the available history and average the worst fall in each path.",
    "Actual maximum drawdown (1 year)": "Look across the latest year of validated returns and find the largest fall from a previous high to a later low.",
    "Tail maximum drawdown (1 year)": "Run the same one-year simulations, keep the worst 5% of outcomes and average their maximum drawdowns.",
    "Crisis maximum drawdown (15/9/2008–9/3/2009)": "Hold the selected portfolio's positions at fixed units and replay them through the 2008–2009 crisis. Use documented market proxies where a holding lacks sufficient history, while cash stays unchanged.",
    "VaR 95% — daily": "Order the validated daily returns from worst to best and identify the loss level around the worst 5% boundary.",
    "Daily Expected Tail Loss (95%)": "Take all validated daily losses beyond the 95% VaR boundary and calculate their average loss.",
    "Win rate": "Divide the number of positive-return days by the total number of validated return days.",
    "BLUE mean daily return": "Add all validated daily returns and divide by the number of returns. The website also measures the statistical uncertainty around this average.",
    "Expected Tail Loss contribution": "On portfolio tail-loss days, estimate how much each eligible unchanged USD holding contributed to the loss, then average those contributions.",
    "Top 5 ETL contributors": "Rank eligible holdings from the largest positive tail-loss contribution to the smallest and show the first five.",
    "Bottom 5 ETL contributors": "Rank eligible holdings from the strongest tail-loss offset upward and show the first five.",
    "ETL residual contribution": "Subtract the contributions explained by eligible holdings from the portfolio's total Expected Tail Loss.",
    "Ending cash": "Read the end-of-period cash balance for each currency directly from the validated IBKR cash snapshot.",
    "Settled cash": "Read the settled cash balance for each currency directly from the validated IBKR cash snapshot.",
    "Cash activity amount": "Use the signed amount imported for each cash event. Positive and negative values retain IBKR's source convention.",
    "Quantity": "Use the number of units reported by IBKR for the holding or executed trade.",
    "Market value": "Use the holding value reported by IBKR for the effective snapshot and show it with the relevant currency.",
    "Trade proceeds": "Use the gross cash proceeds recorded by IBKR for the execution before the displayed commission and other net-cash items.",
    "Commission": "Use the broker commission recorded by IBKR, together with its commission currency.",
    "Net cash": "Use the final cash impact that IBKR recorded for the execution after the components included in its source calculation.",
    "Realized P&L": "Use the realized profit or loss supplied by IBKR under its lot-accounting treatment.",
    "Ready": "Confirm that both the factsheet and presentation have been released internally and that their account, period, file type, size and digital fingerprints all match.",
    "Ready for review": "Confirm that both report files were generated and validated, but have not yet received internal release approval.",
    "Generation pending": "Check whether either the factsheet or presentation is still missing a completed, validated file.",
    "Quality status": "Run the applicable data checks and assign pass, warning or failure according to coverage, freshness, methodology, reasonableness, licensing and file-integrity rules.",
    "Return observations": "Count only the validated daily return records that were actually included in the calculation.",
    "Tracked NAV subjects": "Count the distinct account and portfolio identifiers represented in the validated NAV dataset.",
    "Latest portfolio date": "Find the most recent validated total-NAV date across all tracked accounts and portfolios.",
    "Latest ingestion run": "Find the latest data-import process that reached a successful completed state.",
    "Benchmark observations": "Count the stored price or index-level records for the selected benchmark series.",
    "Assurance checks passed / warnings / failures": "Count the results of the latest automated assurance review in each status category.",
    "Consecutive passing days": "Count uninterrupted Hong Kong calendar days with one passing acceptance result. A missing or failed day restarts the sequence.",
    "Open incidents": "Count system findings that have not yet been marked resolved or closed.",
    "Portal failure count": "Count consecutive qualifying website-boundary failures; reset the count after a qualifying success.",
    "Rollback threshold": "Use the fixed safety-policy threshold—currently three consecutive qualifying portal failures—before restricted recovery can become eligible.",
    "Production authority": "Check that controlled autonomy is enabled, the owner's monthly approval is current and every required policy gate remains satisfied.",
    "Monthly review due": "Calculate the next owner-approval deadline from the review policy and the most recent approved review.",
    "Last-known-good revision": "Read the exact pre-approved website revision and image evidence registered for restricted recovery.",
    'Sortino ratio': 'Divide the average daily return by how much returns typically fall below zero, then scale it to a year.',
    'Calmar ratio': 'Divide the yearly growth rate by the largest fall from a previous high.',
    'Maximum drawdown (since inception)': "Track the portfolio's value after removing deposits and withdrawals and find the biggest fall from a high to a later low.",
    'Current drawdown': "Compare today's adjusted value with the highest adjusted value reached so far.",
    'Downside deviation': 'Measure how large the losing days typically are and scale it to a year.',
    'Skewness': 'Check whether the biggest surprises in daily returns tend to be losses or gains.',
    'Excess kurtosis': 'Check how often very large up or down days happen compared with a bell curve.',
    'Best and worst day': 'Find the day with the biggest gain and the day with the biggest loss.',
    'Best and worst month': 'Look across the monthly returns calendar and pick the best and worst months.',
    'Positive months': 'Count the months that made money and divide by all months measured.',
    'Average gain and loss month': 'Average the winning months, then average the losing months.',
    'Gain/loss ratio': 'Divide the average winning month by the size of the average losing month.',
    'Monthly return': 'Link the adjusted daily returns inside each month together.',
    'Rolling 3-month volatility': 'Measure how much returns varied over roughly the last three months, and repeat that for each date.',
    'Trailing 12-month return': 'For each date, link the adjusted daily returns of the previous 12 months.',
    'Largest holding': 'Find the holding that makes up the biggest share of the portfolio.',
    'Top 5 weight': 'Add up the weights of the five biggest holdings.',
    'Top 10 weight': 'Add up the weights of the ten biggest holdings.',
    'Effective number of holdings': "Square each holding's share of the portfolio, add them up and take the reciprocal.",
    'Currency exposure': 'Group the holdings by the currency they trade in and add up their weights.',
    'Contribution to return': "For each day, value the change in each holding's price on the units held the day before, then link the days into a monthly total.",
    'Portfolio turnover': 'Take the smaller of what was bought and what was sold and divide by the average portfolio value.',
    'Commissions / NAV': 'Add up the commissions paid and divide by the average portfolio value.',
    'Trading activity': 'Group the executed trades by month and add up their values in the reporting currency.',
    'Rolling beta and correlation': 'Measure how the portfolio moved with the benchmark over roughly the last six months, and repeat for each date.',
    'Holdings correlation': "Compare each pair of top holdings' daily price changes and score how often they move together.",
    "Data freshness": "Check whether the latest data is from the last business day in Hong Kong.",
    "Day change": "Compare the latest value with the previous valuation after taking out any money added or withdrawn.",
    "Time-weighted return (TWR)": "Link each day's return together after removing money added or withdrawn, so only investment gains and losses count.",
    "TWR annualised": "Convert the total return over a period of a year or more into the equivalent yearly growth rate.",
    "IRR annualised": "Find the single yearly rate that turns the money put in, on the dates it went in, into today's value.",
    "Net external flows": "Add up the deposits and subtract the withdrawals made during the period.",
    "Benchmark return basis": "Check whether the benchmark counts dividends or only price moves before comparing it with the portfolio.",
    "Drawdown episode": "Find each fall from a high, its lowest point and the date the portfolio got back to the high.",
    "Value in reporting currency": "Convert each holding's value with the exchange rate for the holdings date.",
    "Cost basis": "Use what IBKR records as paid for the units still held.",
    "Unrealized P&L": "Subtract what the units cost from what they are worth now.",
    "P&L %": "Divide the unrealized gain or loss by what the position cost.",
    "Holding weight": "Divide each holding's value by the total value of the portfolio.",
    "RSI (14)": "Compare the size of the holding's recent daily price rises with its recent falls and score the balance from 0 to 100.",
    "Sector allocation": "Group the holdings by sector and add up each sector's share of the holdings value.",
    "Crisis scenario maximum drawdown": "Keep today's holdings at fixed units and replay them through each past crisis on their own prices, or on a similar fund scaled to how strongly the holding moves with it, then find the biggest fall from a high.",
    "Crisis scenario stand-ins (scaled)": "Count the holdings that had no prices of their own for the crisis and were replayed on a similar fund, and in brackets how many of those were scaled up or down.",
    "Wealth projection range": "Build thousands of possible futures by stringing together real months from the portfolio's own history at random, adding the monthly contribution each month, and show the range of where the value could end up.",
    "Chance of reaching the target": "Count the simulated futures that end at or above the target and divide by all of them.",
    "Chance of running out": "Count the simulated futures in which withdrawals use up the whole portfolio and divide by all of them.",
    "Chance of ending below money put in": "Count the simulated futures that end below the starting value plus everything added, and divide by all of them.",
    "Projection return and volatility used": "Show the yearly growth rate and the spread of monthly returns the simulation is built on, from history or from your own return assumption.",
    "Forecast volatility": "Look at how big recent daily moves have been compared with normal, and estimate how volatile the portfolio is likely to be next, assuming unusual calm or turbulence gradually fades back to normal.",
    "Long-run volatility": "Measure how much the portfolio's daily returns have typically varied over its whole history and scale it to a year.",
    "Shock half-life": "Estimate how many trading days it takes for half of a burst of volatility to die away.",
    "Forward VaR": "Take the portfolio's own past days, adjust each one to today's level of volatility, and find the loss that is beaten on only 1 day in 20 (or 1 in 100), and the same for a month.",
    "Forward expected tail loss": "Average the losses on the days or months that go beyond the forward VaR.",
    "VaR backtest": "Go back through history and, using only the data available at each point, check how often the next day's loss broke the VaR line compared with the 1 day in 20 expected.",
    "Drawdown odds (next 12 months)": "Simulate thousands of possible next years from the portfolio's own month-long stretches of returns and count how often each one falls 10%, 20% or 30% from a high.",
    "Market regime": "Let the model sort the portfolio's history into calm and stressed periods from the size of its daily moves, then estimate which one it is in today and how likely a switch is.",
    "Regime outlook range": "Simulate thousands of next few months that can switch between calm and stressed periods with the odds seen in the past, and show the range of outcomes.",
    "Outlook score": "Replay the outlook at past month-ends, using only data available then, and check how often the next month landed inside the range and whether the middle line got the direction right.",
    "What-if comparison": "Replay today's weights and your proposed weights over the same year of daily prices, with any money not invested held as cash, and compare their risk and return.",
    "Worst month (21 sessions)": "Find the weakest stretch of 21 trading days in the year being compared.",
    "What-if crisis replay": "Replay both today's mix and the proposed mix through each stored crisis on the same price paths, and compare their worst fall and their return over the whole window.",
    "Share of risk": "Work out how much of the portfolio's ups and downs come from each holding, allowing for how the holdings move together.",
    "Expected return (optimisation)": "Take each holding's average return over the past year and pull it halfway toward the average of all the holdings, so one strong year does not dominate.",
    "Minimum variance portfolio": "Find the mix of the same holdings with the smallest expected ups and downs, with a cap on any single holding.",
    "Risk parity portfolio": "Find the mix of the same holdings in which each one contributes the same amount of risk.",
    "Maximum Sharpe portfolio": "Find the mix of the same holdings with the most expected return for each unit of risk, with a cap on any single holding.",
    "Efficient frontier": "Plot the lowest-risk mix of the same holdings for each level of expected return.",
    "Factor beta": "Compare the portfolio's weekly returns with six well-known market drivers and measure how strongly it has moved with each one.",
    "Factor t-statistic": "Divide each sensitivity by its uncertainty to see whether it is clearly different from zero.",
    "Alpha (factor model)": "Take the part of the portfolio's average return the six drivers do not explain and express it per year.",
    "Explained by factors (R²)": "Measure how much of the portfolio's weekly ups and downs the six drivers account for.",
    "Unexplained volatility": "Measure how much the portfolio's weekly returns vary in ways the six drivers do not explain, scaled to a year.",
    "Factor return contribution": "Multiply each sensitivity by how much that driver returned over the period, per year.",
    "Factor stress test impact": "Multiply each driver's shock by the portfolio's sensitivity to it and add the results.",
    "Typical unexplained move in a month": "Estimate how far the real result could differ from the stress-test figure in a typical month, either way, because of moves the drivers do not explain.",
    "Export check result": "Run every check on the built files; the export fails if any check fails, needs review if any needs review, and passes otherwise.",
    "Last value matches the books": "Compare the last value in the exported file with the account's recorded value on that day.",
    "Holdings and cash add up to the account's value": "Add up the exported holdings and the cash and compare the total with the account's value on that day.",
    "Cash balances roll forward": "Start from the opening cash, add the cash activity and trades in the file and check that it reaches the closing cash in each currency.",
    "Totals tie to the file": "Add up the detail file again and check every figure in the totals file matches.",
    "No long gaps in the value history": "Look for the longest stretch between two values in the exported history.",
    "Export pack": "Save an export request once and have it offered again each month, quarter or year, with the same checks each time.",
    "Risk alert": "Check each account every weekday morning for a loss beyond its VaR, a switch into its stressed period or a jump in forecast volatility, and explain what happened when one occurs.",
    "Risk model check": "Once a month, check whether each account's VaR line and outlook ranges have held up against what actually happened.",
}

# Extra words a reader might type: abbreviations, synonyms and common names.
# They are searched but only the first few are shown as "Also called".
METRIC_ALIASES = {
    "Net asset value (NAV)": ("NAV", "portfolio value", "account value"),
    "Data freshness": ("stale", "data as of", "last updated", "refresh"),
    "Day change": ("daily change", "1D", "today"),
    "Time-weighted return (TWR)": ("TWR", "MTD", "QTD", "YTD", "since inception", "period return", "calendar year", "custom period"),
    "TWR annualised": ("annualized TWR", "compound annual growth rate", "CAGR"),
    "IRR annualised": ("IRR", "MWR", "money-weighted return", "XIRR", "internal rate of return"),
    "Net external flows": ("contributions", "withdrawals", "deposits", "subscriptions", "redemptions", "cash flows"),
    "Cash-flow-adjusted daily portfolio return": ("Modified Dietz", "daily return"),
    "Benchmark return basis": ("price return", "total return", "dividends", "index convention"),
    "Rebased portfolio or benchmark level": ("growth of 100", "indexed to 100"),
    "Excess total return": ("active return", "alpha", "outperformance", "relative return"),
    "Tracking error": ("TE", "active risk"),
    "Information ratio": ("IR",),
    "Beta": ("market sensitivity", "systematic risk"),
    "Relative win rate": ("hit rate", "batting average"),
    "Rolling beta and correlation": ("rolling beta", "rolling correlation"),
    "Sortino ratio": ("downside risk-adjusted return",),
    "Calmar ratio": ("return over max drawdown", "MAR"),
    "Maximum drawdown (since inception)": ("MDD", "max drawdown", "peak to trough"),
    "Drawdown episode": ("recovery time", "underwater period", "time to recover"),
    "Downside deviation": ("downside volatility", "semi-deviation"),
    "Excess kurtosis": ("fat tails", "kurtosis"),
    "Monthly return": ("monthly returns calendar", "heat map"),
    "Contribution to return": ("attribution", "CTR", "contributors", "detractors"),
    "Holding weight": ("allocation", "position size", "weight"),
    "Effective number of holdings": ("HHI", "Herfindahl", "diversification"),
    "Currency exposure": ("FX exposure", "currency risk"),
    "Sector allocation": ("industry", "GICS", "classification coverage"),
    "Unrealized P&L": ("paper gain", "unrealised profit"),
    "RSI (14)": ("RSI", "relative strength index", "overbought", "oversold", "momentum"),
    "Annualized volatility": ("standard deviation", "vol", "risk"),
    "Annualized return": ("CAGR", "annualised return"),
    "Risk-free rate": ("T-bill", "Treasury yield", "HIBOR"),
    "Sharpe ratio": ("risk-adjusted return",),
    "VaR 95% — daily": ("VaR", "value at risk"),
    "Daily Expected Tail Loss (95%)": ("ETL", "CVaR", "expected shortfall", "ES", "conditional VaR"),
    "Expected Tail Loss contribution": ("risk contribution", "tail attribution"),
    "Crisis maximum drawdown (15/9/2008–9/3/2009)": ("stress test", "GFC", "2008"),
    "Holdings correlation": ("correlation matrix", "co-movement"),
    "Portfolio turnover": ("turnover",),
    "Commissions / NAV": ("trading costs", "fees", "cost drag"),
    "Realized P&L": ("realised profit", "closed P&L"),
    "Settled cash": ("liquidity",),
    "Quality status": ("pass", "warning", "failure", "data quality"),
    "Crisis scenario maximum drawdown": ("stress test", "Lehman", "COVID", "2022 sell-off", "China crash", "scenario analysis"),
    "Wealth projection range": ("Monte Carlo", "fan chart", "projection", "retirement plan", "block bootstrap"),
    "Chance of reaching the target": ("probability of success", "goal probability"),
    "Chance of running out": ("depletion risk", "ruin probability", "sequence risk"),
    "Forecast volatility": ("GARCH", "EWMA", "conditional volatility", "volatility forecast"),
    "Long-run volatility": ("unconditional volatility", "realised volatility"),
    "Forward VaR": ("filtered historical simulation", "FHS", "99% VaR", "monthly VaR"),
    "Forward expected tail loss": ("forward CVaR", "forward expected shortfall"),
    "VaR backtest": ("Kupiec", "breaches", "exceptions", "proportion of failures"),
    "Drawdown odds (next 12 months)": ("probability of drawdown", "chance of a fall"),
    "Market regime": ("hidden Markov model", "HMM", "calm", "stressed", "regime switching"),
    "Regime outlook range": ("outlook", "1-3 month range", "scenario range"),
    "Outlook score": ("walk-forward", "calibration", "coverage", "directional skill"),
    "What-if comparison": ("rebalancing", "scenario weights", "proposed portfolio", "pro forma"),
    "Share of risk": ("risk budget", "risk contribution", "marginal risk"),
    "Minimum variance portfolio": ("min variance", "lowest risk"),
    "Risk parity portfolio": ("equal risk contribution", "ERC"),
    "Maximum Sharpe portfolio": ("tangency portfolio", "max Sharpe"),
    "Efficient frontier": ("mean-variance", "Markowitz", "optimisation", "optimization"),
    "Factor beta": ("Fama-French", "factor exposure", "smart beta", "size", "value", "momentum"),
    "Alpha (factor model)": ("Jensen's alpha", "unexplained return"),
    "Explained by factors (R²)": ("R-squared", "R2", "goodness of fit"),
    "Unexplained volatility": ("residual volatility", "idiosyncratic risk", "specific risk"),
    "Factor stress test impact": ("scenario shock", "what if markets fall", "factor shock"),
    "Export check result": ("export agent", "checker", "download checks"),
    "Export pack": ("scheduled export", "recurring export", "monthly pack"),
    "Risk alert": ("risk watch", "inbox alert", "VaR breach", "volatility jump"),
    "Risk model check": ("model validation", "model monitoring"),
}

# Closely linked definitions shown as "See also" links under each entry.
RELATED_METRICS = {
    "Net asset value (NAV)": ("Day change", "Net external flows", "Reporting currency"),
    "Day change": ("Time-weighted return (TWR)", "Net asset value (NAV)"),
    "Time-weighted return (TWR)": ("TWR annualised", "IRR annualised", "Cash-flow-adjusted daily portfolio return", "Monthly return"),
    "TWR annualised": ("Time-weighted return (TWR)", "Annualized return"),
    "IRR annualised": ("Time-weighted return (TWR)", "Net external flows"),
    "Net external flows": ("Time-weighted return (TWR)", "IRR annualised", "Cash activity amount"),
    "Cash-flow-adjusted daily portfolio return": ("Time-weighted return (TWR)", "Net external flows"),
    "Daily benchmark return": ("Benchmark return basis", "Benchmark total return"),
    "Benchmark return basis": ("Benchmark total return", "Excess total return"),
    "Benchmark total return": ("Benchmark return basis", "Portfolio total return"),
    "Excess total return": ("Tracking error", "Information ratio", "Benchmark return basis"),
    "Tracking error": ("Information ratio", "Excess total return"),
    "Information ratio": ("Tracking error", "Sharpe ratio"),
    "Beta": ("Correlation", "Rolling beta and correlation"),
    "Correlation": ("Beta", "Holdings correlation"),
    "Rolling beta and correlation": ("Beta", "Correlation"),
    "Sortino ratio": ("Downside deviation", "Sharpe ratio", "Calmar ratio"),
    "Calmar ratio": ("Maximum drawdown (since inception)", "TWR annualised"),
    "Maximum drawdown (since inception)": ("Current drawdown", "Drawdown episode", "Actual maximum drawdown (1 year)"),
    "Current drawdown": ("Maximum drawdown (since inception)", "Drawdown episode"),
    "Drawdown episode": ("Maximum drawdown (since inception)", "Current drawdown"),
    "Downside deviation": ("Sortino ratio", "Annualized volatility"),
    "Skewness": ("Excess kurtosis", "Best and worst day"),
    "Excess kurtosis": ("Skewness", "Daily Expected Tail Loss (95%)"),
    "Best and worst month": ("Monthly return", "Positive months"),
    "Positive months": ("Gain/loss ratio", "Average gain and loss month"),
    "Gain/loss ratio": ("Average gain and loss month", "Positive months"),
    "Monthly return": ("Time-weighted return (TWR)", "Contribution to return"),
    "Rolling 3-month volatility": ("Annualized volatility",),
    "Trailing 12-month return": ("Time-weighted return (TWR)",),
    "Contribution to return": ("Monthly return", "Holding weight"),
    "Value in reporting currency": ("Market value", "Reporting currency", "Currency exposure"),
    "Cost basis": ("Unrealized P&L", "P&L %"),
    "Unrealized P&L": ("Cost basis", "P&L %", "Realized P&L"),
    "P&L %": ("Unrealized P&L", "Cost basis"),
    "Holding weight": ("Value in reporting currency", "Top 10 weight", "Effective number of holdings"),
    "Sector allocation": ("Holding weight", "Currency exposure"),
    "RSI (14)": ("P&L %", "Holdings correlation"),
    "Effective number of holdings": ("Top 10 weight", "Largest holding", "Holdings correlation"),
    "Currency exposure": ("Reporting currency", "Value in reporting currency"),
    "Annualized volatility": ("Rolling 3-month volatility", "Downside deviation", "Sharpe ratio"),
    "Sharpe ratio": ("Risk-free rate", "Annualized volatility", "Sortino ratio"),
    "Risk-free rate": ("Sharpe ratio",),
    "VaR 95% — daily": ("Daily Expected Tail Loss (95%)",),
    "Daily Expected Tail Loss (95%)": ("VaR 95% — daily", "Expected Tail Loss contribution"),
    "Expected Tail Loss contribution": ("ETL residual contribution", "Top 5 ETL contributors"),
    "Holdings correlation": ("Effective number of holdings", "Correlation"),
    "Trading activity": ("Portfolio turnover", "Commissions / NAV", "Realized P&L"),
    "Portfolio turnover": ("Trading activity", "Commissions / NAV"),
    "Commissions / NAV": ("Commission", "Portfolio turnover"),
    "Realized P&L": ("Unrealized P&L", "Trading activity"),
    "Return observations": ("Observations",),
    "Crisis maximum drawdown (15/9/2008–9/3/2009)": ("Crisis scenario maximum drawdown",),
    "Crisis scenario maximum drawdown": ("Crisis scenario stand-ins (scaled)", "Crisis maximum drawdown (15/9/2008–9/3/2009)", "What-if crisis replay"),
    "Crisis scenario stand-ins (scaled)": ("Crisis scenario maximum drawdown",),
    "Wealth projection range": ("Projection return and volatility used", "Chance of reaching the target", "Chance of ending below money put in"),
    "Chance of reaching the target": ("Wealth projection range",),
    "Chance of running out": ("Wealth projection range", "Chance of ending below money put in"),
    "Chance of ending below money put in": ("Wealth projection range",),
    "Projection return and volatility used": ("Wealth projection range", "Annualized return"),
    "Forecast volatility": ("Long-run volatility", "Shock half-life", "Forward VaR", "Annualized volatility"),
    "Long-run volatility": ("Forecast volatility", "Risk alert"),
    "Shock half-life": ("Forecast volatility",),
    "Forward VaR": ("Forward expected tail loss", "VaR backtest", "VaR 95% — daily"),
    "Forward expected tail loss": ("Forward VaR", "Daily Expected Tail Loss (95%)"),
    "VaR backtest": ("Forward VaR", "Risk model check"),
    "Drawdown odds (next 12 months)": ("Expected maximum drawdown (1 year)", "Forecast volatility"),
    "Market regime": ("Regime outlook range", "Outlook score", "Risk alert"),
    "Regime outlook range": ("Market regime", "Outlook score"),
    "Outlook score": ("Regime outlook range", "Risk model check"),
    "What-if comparison": ("Worst month (21 sessions)", "What-if crisis replay", "Share of risk"),
    "What-if crisis replay": ("Crisis scenario maximum drawdown", "What-if comparison"),
    "Share of risk": ("Holding weight", "Risk parity portfolio", "Expected Tail Loss contribution"),
    "Minimum variance portfolio": ("Efficient frontier", "Risk parity portfolio"),
    "Risk parity portfolio": ("Share of risk", "Minimum variance portfolio"),
    "Maximum Sharpe portfolio": ("Efficient frontier", "Expected return (optimisation)", "Sharpe ratio"),
    "Efficient frontier": ("Minimum variance portfolio", "Maximum Sharpe portfolio"),
    "Factor beta": ("Factor t-statistic", "Factor return contribution", "Beta"),
    "Factor t-statistic": ("Factor beta", "Alpha (factor model)"),
    "Alpha (factor model)": ("Explained by factors (R²)", "Excess total return"),
    "Explained by factors (R²)": ("Unexplained volatility", "Factor stress test impact"),
    "Unexplained volatility": ("Explained by factors (R²)", "Typical unexplained move in a month"),
    "Factor stress test impact": ("Factor beta", "Typical unexplained move in a month", "Crisis scenario maximum drawdown"),
    "Typical unexplained move in a month": ("Factor stress test impact", "Unexplained volatility"),
    "Export check result": ("Last value matches the books", "Holdings and cash add up to the account's value", "Cash balances roll forward"),
    "Export pack": ("Export check result",),
    "Risk alert": ("Forward VaR", "Market regime", "Forecast volatility"),
    "Risk model check": ("VaR backtest", "Outlook score"),
}
for _name, _targets in (*METRIC_ALIASES.items(), *RELATED_METRICS.items()):
    if _name not in _BY_NAME:
        raise RuntimeError(f"Unknown metric in guide cross-references: {_name}")
for _name, _targets in RELATED_METRICS.items():
    for _target in _targets:
        if _target not in _BY_NAME or _target == _name:
            raise RuntimeError(f"Invalid related metric for {_name}: {_target}")
for _name in METRIC_LOCATIONS:
    if _name not in _BY_NAME:
        raise RuntimeError(f"Unknown metric in guide locations: {_name}")


METHODOLOGY_VERSIONS = (
    ("benchmark-comparison-v1", "benchmark comparison and Risk tab"),
    ("performance-periods-v1", "period returns, IRR and flows"),
    ("portfolio-analytics-v1", "Performance statistics, calendar, rolling and concentration"),
    ("holdings-view-v1", "holdings values, P&L and weights"),
    ("holdings-crisis-scenarios-v1", "crisis scenarios on the Risk tab"),
    ("portfolio-modelling-v1", "Modelling tab models and the risk watch"),
)

# Conventions that apply across the guide, so individual entries need not
# repeat them.
CALCULATION_CONVENTIONS = (
    ("Returns", "Daily returns remove external deposits and withdrawals with a Modified Dietz half-flow adjustment and are chain-linked into time-weighted returns."),
    ("Annualising", "Volatility-type figures scale daily statistics by √252. Growth rates annualise over calendar days: 365 for period returns and IRR, 365.25 for the Risk tab's annualized return. Periods under a year are not annualised."),
    ("Currency", "Portfolio-level figures are in the reporting currency, converted with the dated FX rate on or before each valuation date."),
    ("Dates", "Benchmark comparisons and the Risk tab use only dates both series share, and unmatched dates are not filled. Performance statistics use every validated NAV date."),
    ("Missing data", "If required data is missing, stale or invalid, the website shows the metric as unavailable instead of estimating it."),
    ("Models", "Modelling figures, crisis scenarios and risk alerts are hypothetical models built from history. They are not forecasts, guarantees or investment advice, and each states its method and the data it used."),
    ("Display", "A stored decimal of 0.05 is displayed as 5.00%. VaR and Expected Tail Loss are shown as positive loss percentages; drawdowns are shown as negative percentages."),
)

_SECTION_OF = {metric.name: section for section in METRIC_SECTIONS for metric in section.metrics}
_FIGURE = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?")


def metric_location(name: str) -> str:
    """Where the website shows a metric, such as "Modelling › Risk outlook"."""

    return METRIC_LOCATIONS.get(name, _SECTION_OF[name].location)


def definition_payload(name: str) -> dict:
    """One definition as data, for the agents (and so for the figure check).

    ``figures_in_text`` lists the numbers the wording quotes (252 sessions, 95%, 2,000 paths) so
    an answer may repeat them.
    """

    metric = _BY_NAME[name]
    section = _SECTION_OF[name]
    texts = (metric.meaning, metric.calculation, metric.interpretation, CLIENT_CALCULATIONS[name])
    figures = sorted({float(match.replace(",", "")) for text in texts for match in _FIGURE.findall(text)})
    return {
        "name": name,
        "section": section.title,
        "where_you_see_it": metric_location(name),
        "link": f"/metrics-guide#{METRIC_ANCHORS[name]}",
        "what_it_tells_you": metric.meaning,
        "how_we_work_it_out": CLIENT_CALCULATIONS[name],
        "technical_method": metric.calculation,
        "how_to_explain_it": metric.interpretation,
        "also_called": list(METRIC_ALIASES.get(name, ())),
        "see_also": list(RELATED_METRICS.get(name, ())),
        "figures_in_text": figures,
    }


def _words(text: str) -> set[str]:
    """Lower-case words, long ones cut to six letters so "concentrated" meets "concentration"."""

    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return {word[:6] for word in re.findall(r"[a-z0-9]+", ascii_text) if len(word) > 1}


_STOP_WORDS = frozenset("""the and for why did does what how is are was my our this that with of to in on it its
    an be me can you your from mean means do when which who will would should account portfolio go up get got
    has have had am""".split())


def search(query: str, limit: int = 5) -> list[dict]:
    """Definitions that best match a plain-language question, best first.

    An exact name or alias in the query counts most, then words in the name and aliases, then
    words in the explanations.
    """

    text = query.lower()
    words = _words(query) - _STOP_WORDS
    scored = []
    for name, metric in _BY_NAME.items():
        aliases = METRIC_ALIASES.get(name, ())
        score = 0.0
        for term in (name, *aliases):
            pattern = r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])"
            if re.search(pattern, text):
                score += 10 + len(term) / 10
        heading = _words(" ".join((name, *aliases)))
        body = _words(" ".join((metric.meaning, CLIENT_CALCULATIONS[name], metric.interpretation)))
        own = _words(name)
        # Of two names that both match, the one the query covers more of comes first ("regime" → Market regime).
        score += 3 * len(words & heading) + 4 * len(words & own) / max(len(own), 1) + 0.5 * len(words & body)
        if score > 0:
            scored.append((score, name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        {"name": name, "where_you_see_it": metric_location(name), "link": f"/metrics-guide#{METRIC_ANCHORS[name]}",
         "how_we_work_it_out": CLIENT_CALCULATIONS[name]}
        for _, name in scored[:max(1, min(limit, 10))]
    ]


ALIASES_SHOWN = 4


_NO_ADDITION = GuideAddition()


def _definition_row(metric: MetricDefinition, section: MetricSection, addition: GuideAddition | None = None) -> str:
    extra = addition or _NO_ADDITION
    client_calculation = extra.client_calculations.get(metric.name) or CLIENT_CALCULATIONS[metric.name]
    anchor = metric_anchor(metric.name)
    aliases = extra.aliases.get(metric.name) or METRIC_ALIASES.get(metric.name, ())
    related = extra.related.get(metric.name) or RELATED_METRICS.get(metric.name, ())
    location = extra.locations.get(metric.name) or METRIC_LOCATIONS.get(metric.name, section.location)
    search_text = " ".join(
        (
            metric.name,
            *aliases,
            location,
            metric.meaning,
            client_calculation,
            metric.calculation,
            metric.interpretation,
        )
    ).lower()
    alias_html = (
        '<div class="metric-aliases"><span>Also called</span>'
        + "".join(f"<em>{escape(alias)}</em>" for alias in aliases[:ALIASES_SHOWN])
        + "</div>"
        if aliases
        else ""
    )
    related_html = (
        '<div class="metric-related"><span>See also</span>'
        + "".join(
            f'<a href="#{escape(metric_anchor(name), quote=True)}">{escape(name)}</a>'
            for name in related
        )
        + "</div>"
        if related
        else ""
    )
    return (
        f'<article id="{escape(anchor, quote=True)}" class="metric-definition" '
        f'data-metric-search="{escape(search_text, quote=True)}" tabindex="-1">'
        '<div class="metric-definition-heading">'
        f'<h3>{escape(metric.name)}</h3>'
        f'<button type="button" class="metric-copy" data-anchor="{escape(anchor, quote=True)}" '
        f'aria-label="Copy a link to {escape(metric.name, quote=True)}">Copy link</button></div>'
        f'<div class="metric-where"><span>Where you see it</span>{escape(location)}</div>'
        f'{alias_html}'
        '<div class="metric-explanation-grid">'
        '<div><div class="metric-explanation-label">What it tells you</div>'
        f'<p>{escape(metric.meaning)}</p></div>'
        '<div><div class="metric-explanation-label">How we work it out</div>'
        f'<p>{escape(client_calculation)}</p></div>'
        '<div><div class="metric-explanation-label">How to explain it</div>'
        f'<p>{escape(metric.interpretation)}</p></div></div>'
        '<details class="metric-technical"><summary>View technical formula and methodology</summary>'
        f'<div><code>{escape(metric.calculation)}</code></div></details>'
        f'{related_html}</article>'
    )


def _section_id(section: MetricSection) -> str:
    return f"guide-{section.key}"


def render_metrics_guide(additions: Sequence[GuideAddition] = ()) -> str:
    placed: list[tuple[MetricSection, GuideAddition | None]] = [(section, None) for section in METRIC_SECTIONS]
    for addition in additions:
        for after, extra in addition.sections:
            index = next((i + 1 for i, (s, _) in enumerate(placed) if s.key == after), len(placed))
            placed.insert(index, (extra, addition))
    sections = []
    for section, addition in placed:
        rows = "".join(_definition_row(metric, section, addition) for metric in section.metrics)
        section_id = escape(_section_id(section), quote=True)
        sections.append(
            f'<section id="{section_id}" class="card wide metric-guide-section" '
            f'aria-labelledby="{section_id}-title">'
            '<div class="metric-guide-section-head">'
            f'<h2 id="{section_id}-title" class="label">{escape(section.title)}</h2>'
            f'<span class="metric-guide-count">{len(section.metrics)} definitions</span></div>'
            f'<p class="muted">{escape(section.introduction)}</p>'
            f'<div class="metric-guide-list">{rows}</div>'
            '<a class="metric-back" href="#metric-guide-top">Back to top <span aria-hidden="true">↑</span></a>'
            '</section>'
        )
    total = sum(len(section.metrics) for section, _ in placed)
    chips = "".join(
        f'<a href="#{escape(_section_id(section), quote=True)}" data-section-chip="{escape(_section_id(section), quote=True)}">'
        f'{escape(section.short_title)} <span>{len(section.metrics)}</span></a>'
        for section, _ in placed
    )
    conventions = "".join(
        f"<div><dt>{escape(label)}</dt><dd>{escape(text)}</dd></div>"
        for label, text in CALCULATION_CONVENTIONS
    )
    versions = "".join(
        f"<li><code>{escape(version)}</code> {escape(scope)}</li>"
        for version, scope in (*METHODOLOGY_VERSIONS, *(item for addition in additions for item in addition.versions))
    )
    return (
        '<div class="page-view active" data-section="metrics-guide">'
        '<div id="metric-guide-top" class="card wide metric-guide-intro">'
        '<div class="label">Plain-English guide for clients and sales conversations</div>'
        '<div class="value">Understand every number</div>'
        '<p class="muted">Each definition starts with the business meaning, explains '
        'the calculation without jargon and gives a practical interpretation. Open '
        'the technical panel only when you need the exact formula or methodology. '
        'Sections follow the website tabs and their pages, and each definition says where you see it.</p>'
        '<div class="methodology-callout"><strong>A simple way to read this guide</strong><br>'
        '“What it tells you” explains the purpose. “How we work it out” describes the '
        'calculation in everyday language. “How to explain it” highlights the main '
        'message and the important limitation. The expandable technical method remains '
        'the calculation source of truth, and each section names its methodology version, '
        'such as <code>benchmark-comparison-v1</code>.</div>'
        '<details class="metric-conventions" open><summary>Calculation conventions used throughout</summary>'
        f'<dl>{conventions}</dl>'
        f'<div class="metric-versions"><strong>Methodology versions</strong><ul>{versions}</ul></div>'
        '</details></div>'
        '<div class="card wide metric-guide-toolbar">'
        '<div class="metric-guide-toolbar-row">'
        '<label class="metric-search-label"><span class="visually-hidden">Find a metric or term</span>'
        '<input id="metric-search" type="search" autocomplete="off" '
        'placeholder="Try Sharpe, TWR, GARCH, regime, what-if or CVaR" '
        'aria-describedby="metric-search-status"></label>'
        '<button type="button" id="metric-expand" class="metric-expand" aria-pressed="false">Show all formulas</button>'
        '</div>'
        f'<div id="metric-search-status" class="muted" aria-live="polite">Showing all {total} definitions.</div>'
        f'<nav class="metric-jump" aria-label="Metrics guide sections">{chips}</nav>'
        '</div>'
        + "".join(sections)
        + '</div>'
    )


METRICS_GUIDE_SCRIPT = r"""
<script>
(()=>{
const metricSearch=document.querySelector('#metric-search');
const metricRows=[...document.querySelectorAll('.metric-definition')];
const metricSections=[...document.querySelectorAll('.metric-guide-section')];
const metricStatus=document.querySelector('#metric-search-status');
const metricChips=[...document.querySelectorAll('[data-section-chip]')];
const metricDetails=[...document.querySelectorAll('.metric-technical')];
const expandButton=document.querySelector('#metric-expand');
const updateMetricFilter=()=>{
  const raw=metricSearch.value.trim(),query=raw.toLowerCase();
  let visible=0;
  metricRows.forEach(row=>{
    const match=!query||row.dataset.metricSearch.includes(query);
    row.hidden=!match;
    if(match)visible+=1;
  });
  metricSections.forEach(section=>{
    const shown=section.querySelectorAll('.metric-definition:not([hidden])').length;
    section.hidden=!shown;
    const chip=metricChips.find(c=>c.dataset.sectionChip===section.id);
    if(chip){chip.querySelector('span').textContent=shown;chip.classList.toggle('empty',!shown);}
  });
  metricStatus.textContent=!query
    ?`Showing all ${metricRows.length} definitions.`
    :visible
      ?`${visible} definition${visible===1?'':'s'} match “${raw}”.`
      :`No definitions match “${raw}”. Try an abbreviation such as TWR, IRR or ETL, or clear the search.`;
};
metricSearch.addEventListener('input',updateMetricFilter);
metricSearch.addEventListener('keydown',event=>{if(event.key==='Escape'&&metricSearch.value){metricSearch.value='';updateMetricFilter();}});
const initialQuery=new URLSearchParams(location.search).get('q');
if(initialQuery){metricSearch.value=initialQuery;updateMetricFilter();}
const setExpanded=open=>{
  metricDetails.forEach(d=>{d.open=open;});
  expandButton.setAttribute('aria-pressed',String(open));
  expandButton.textContent=open?'Hide all formulas':'Show all formulas';
};
expandButton.addEventListener('click',()=>setExpanded(expandButton.getAttribute('aria-pressed')!=='true'));
let printState=null;
window.addEventListener('beforeprint',()=>{printState=metricDetails.map(d=>d.open);metricDetails.forEach(d=>{d.open=true;});});
window.addEventListener('afterprint',()=>{if(printState)metricDetails.forEach((d,i)=>{d.open=printState[i];});printState=null;});
const copyText=text=>navigator.clipboard&&window.isSecureContext
  ?navigator.clipboard.writeText(text)
  :new Promise((resolve,reject)=>{const area=document.createElement('textarea');area.value=text;area.setAttribute('readonly','');area.style.position='fixed';area.style.opacity='0';document.body.appendChild(area);area.select();const ok=document.execCommand('copy');area.remove();ok?resolve():reject(new Error('copy failed'));});
document.addEventListener('click',event=>{
  const button=event.target.closest('.metric-copy');if(!button)return;
  const url=`${location.origin}${location.pathname}#${button.dataset.anchor}`;
  copyText(url).then(()=>{button.textContent='Copied';button.classList.add('copied');},()=>{button.textContent='Copy failed';})
    .finally(()=>setTimeout(()=>{button.textContent='Copy link';button.classList.remove('copied');},1600));
});
const revealTarget=()=>{
  const target=location.hash&&document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if(target&&target.closest('[hidden]')){metricSearch.value='';updateMetricFilter();target.scrollIntoView();}
};
window.addEventListener('hashchange',revealTarget);
})();
</script>
"""


# Desk page (web_desk.py) -------------------------------------------------------------------
# DESK_PAGE ("owners" or "on") switches the Desk on. Until then the guide does not describe it:
# with the setting unset this block adds nothing, so the guide, its anchors and every account
# page stay exactly as they were. The setting is read once at import, like the website's other
# settings, so a new revision picks up a change.
DESK_DEFINITIONS = (
    MetricDefinition(
        "Intraday P&L",
        "Today's profit or loss on the account since the prior close, from the live IBKR positions and quotes, in the reporting currency.",
        "For each holding with a fresh quote: quantity × multiplier × (live price − prior close) at today's FX rate, plus the FX part on the opening value, quantity × multiplier × prior close × (FX rate now − FX rate at the prior close). The live price is the last trade, else the bid-ask midpoint. Each fill since the prior close adds signed quantity × multiplier × (prior close − fill price), and foreign cash adds balance × the change in its FX rate. Holdings whose quotes are stale, missing or unusable are listed with the reason and left out of the total, never filled in (desk-risk-v1).",
        "A live estimate beside the book of record: the daily IBKR statement stays the official figure. Commissions are left out.",
    ),
    MetricDefinition(
        "Intraday P&L share of NAV",
        "Today's P&L as a share of the account's value at the prior close.",
        "Intraday P&L ÷ NAV at the prior close. NAV at the prior close is the live net liquidation value less IBKR's own P&L for today, converted into the reporting currency at the prior-close FX rate. Checked against a loss limit of 3% of NAV by default (DESK_LIMITS pnl_return, a range from −3% to +100%).",
        "Shows how much of the account today's move represents. It turns amber at 80% of the loss limit and red beyond it; gains are not limited in practice.",
    ),
    MetricDefinition(
        "Trading P&L from today's fills",
        "The part of today's P&L that comes from trades made since the prior close, measured from each fill's price.",
        "Σ over the fills since the prior close of signed quantity × multiplier × (prior close − fill price), at today's FX rate, before commissions. A buy at 102 of a stock that closed at 100 starts at −2 a share and earns from there. The prior close is 16:10 Hong Kong time for HKD and CNH instruments and 16:00 New York time for others, on the last weekday before the Desk's as-of date in that market; holidays are not modelled. Fills after the live snapshot and currency conversions are left out. Until the live fills view exists the Desk counts no fills and says so.",
        "With the move of today's quantity it makes up the price part of today's P&L, so a holding bought today earns from its fill price, not from yesterday's close.",
    ),
    MetricDefinition(
        "Intraday FX P&L",
        "The part of today's P&L that comes from currency moves on holdings and cash in currencies other than the reporting currency.",
        "Opening value in the holding's currency × (FX rate now − FX rate at the prior close), plus each foreign cash balance × the same change. The rates are the daily reference rates (reference_fx_rates, at most 4 days old): the latest dated before the Desk's as-of date for the prior close, and the latest on or before it for now. Until that day's rate is published both are the same rate, so the FX part is zero.",
        "Usually small for HKD and USD, which are pegged. A holding with no FX rate is counted in its own currency only and listed.",
    ),
    MetricDefinition(
        "P&L explain",
        "Today's P&L split into the market's move, currency moves and the stock-specific rest.",
        "Each counted holding's return since the prior close, weighted by its prior-close value, splits into beta × the primary benchmark's return today (its live price ÷ prior close − 1), its currency's move, and the rest as stock-specific. Trading P&L from today's fills and the FX revaluation of cash are shown beside them, so the parts add up to the intraday P&L exactly. The benchmark quote must be within the quote age limit of the Desk's as-of time. Without it there is no market part, and the market's move stays in stock-specific.",
        "Says whether today was the market or the holdings themselves. The split rests on betas estimated from history, so it is a model, not a fact.",
    ),
    MetricDefinition(
        "Beta to the primary benchmark",
        "How much each holding has tended to move for each 1% move in the account's primary benchmark.",
        "Ordinary least squares, with an intercept, of each holding's daily price return on the primary benchmark's daily return, over the dates both have within the latest 252 sessions. Holding returns come from a year of daily marks (subject_position_series); a date a holding has no mark counts as 0%. Benchmark returns come from the benchmark comparison (performance_comparison_daily). At least 20 common days are needed. The account's beta is the sum of live weight × beta.",
        "A beta of 1.2 means the holding has tended to move about 1.2% for each 1% benchmark move. It describes the past year and can change.",
    ),
    MetricDefinition(
        "Gross exposure (live)",
        "The size of all positions at live prices, long and short added together, as a share of NAV.",
        "Σ |quantity × multiplier × live price × FX rate| ÷ NAV at the prior close. A holding with a stale quote keeps its last price, so it still counts. Default limit 150% of NAV.",
        "Above 100% means borrowing or shorting; the limit caps leverage.",
    ),
    MetricDefinition(
        "Net exposure (live)",
        "Long positions less short positions at live prices, as a share of NAV.",
        "Σ quantity × multiplier × live price × FX rate ÷ NAV at the prior close. Default limit 120% of NAV either way.",
        "For an account that only holds long positions it is the share invested; the rest is cash.",
    ),
    MetricDefinition(
        "Largest single name (live)",
        "The largest single position at live prices, as a share of NAV.",
        "max |quantity × multiplier × live price × FX rate| ÷ NAV at the prior close. Default limit 25% of NAV.",
        "A concentration check at today's prices. It can differ from Largest holding on Holdings, which uses the validated end-of-day snapshot.",
    ),
    MetricDefinition(
        "One-day VaR 99% (live)",
        "The loss on a bad day, one day in a hundred, for today's live positions.",
        "Today's live weights (value ÷ NAV at the prior close) applied to each holding's daily price returns over the past year (subject_position_series, up to 252 sessions, at least 20); the 1st percentile of that daily history, shown as a positive share of NAV. A holding with no history counts at 0% and is listed; currency moves are left out. Default limit 5% of NAV.",
        "A historical estimate, not a worst case: about one day in a hundred should lose more. The pre-trade check uses the same measure, before and after the orders.",
    ),
    MetricDefinition(
        "Quote age",
        "How long ago a holding's live quote last changed.",
        "The Desk's as-of time, which is the newest quote on the account, less the time of the holding's latest quote. The collector writes a quote only when it changes, so this is the time since the last change. Older than 15 minutes (DESK_LIMITS max_quote_age_seconds) is stale: the holding is left out of the P&L but keeps its last price in the exposures. Delayed IBKR data is marked.",
        "Outside a market's hours its quotes stop changing and read as stale, and a quiet stock can read as stale while its market is open.",
    ),
    MetricDefinition(
        "Stale quote share",
        "The share of holdings whose live quote is stale or missing.",
        "Holdings held now whose quote is older than the quote age limit, missing, without a time or later than the as-of time ÷ holdings held now. Default limit 10%.",
        "When it is high, today's P&L leaves out much of the account; read it with the positions table.",
    ),
    MetricDefinition(
        "Desk limit status",
        "Each live measure against its limit, shown green, amber or red.",
        "Usage = |value| ÷ limit for a single limit, the most the measure may be either way. For a range that includes zero, usage runs from zero to the bound on the value's side. Red is outside the limit, amber is 80% of it or more, green is below. Limits are DESK_LIMITS (JSON) over the defaults: gross exposure 150%, net exposure 120%, largest single name 25%, one-day VaR 99% 5%, today's P&L −3% to +100% of NAV and stale quote share 10%.",
        "A prompt to look, not an instruction to trade.",
    ),
    MetricDefinition(
        "Pre-trade check",
        "What proposed orders would do to the account's exposure, risk and limits, before anything is placed.",
        "The orders are applied to today's live quantities at today's live prices in the NAV's currency, and cash takes up the difference, so NAV is unchanged. Before and after: gross and net exposure, cash, the largest holding, top 5 weight, ex-ante volatility (a Ledoit-Wolf covariance of the past year's daily returns), one-day VaR and expected tail loss at 99% over the same history, beta to the primary benchmark and the Desk limits, with the breaches the orders would add (pre-trade-v1). A limit price on a held instrument is in its own currency; an instrument not held is priced at the limit price given, read in the NAV's currency, and has no return history.",
        "A hypothetical check: it sends nothing to IBKR, places no order and is not advice.",
    ),
    MetricDefinition(
        "Pre-trade trading cost",
        "The estimated cost of trading the proposed orders.",
        "Half the live relative bid-ask spread, (ask − bid) ÷ midpoint ÷ 2, × each order's value. Square-root market impact needs average daily volumes, which are not in silver yet, so impact is not estimated and the cost is the half-spread only. An instrument without a live bid and ask has no cost estimate and is listed.",
        "A lower bound on the cost: large orders also move the price, and commissions are extra.",
    ),
)
DESK_CLIENT_CALCULATIONS = {
    "Intraday P&L": "Take each holding's move since the prior close at its latest live price, convert it into the reporting currency, and add what today's trades and currency moves made.",
    "Intraday P&L share of NAV": "Divide today's P&L by what the account was worth at the prior close.",
    "Trading P&L from today's fills": "For each trade since the prior close, compare its price with that close; with the move since the trade, that is what the trade has made so far.",
    "Intraday FX P&L": "Value each foreign holding and cash balance at the prior close, then measure how much the change in its exchange rate added or took away.",
    "P&L explain": "Estimate how much of each holding's move today the market explains, from how it has moved with the benchmark over the past year; the currency's move and what is left over make up the rest.",
    "Beta to the primary benchmark": "Compare how each holding and the benchmark moved, day by day, over the past year.",
    "Gross exposure (live)": "Add up the value of every position at live prices, ignoring whether it is long or short, and divide by the account's value.",
    "Net exposure (live)": "Add up the long positions at live prices, take away the short ones and divide by the account's value.",
    "Largest single name (live)": "Find the biggest position at today's prices and divide it by the account's value.",
    "One-day VaR 99% (live)": "Replay the past year's daily moves on today's positions and take the loss that only the worst 1% of days went beyond.",
    "Quote age": "Compare the time of each holding's latest price with the newest price on the account.",
    "Stale quote share": "Count the holdings whose price has not changed recently or is missing, and divide by the number of holdings.",
    "Desk limit status": "Compare each live measure with its limit and show how much of the limit is used.",
    "Pre-trade check": "Apply the orders to today's positions on paper and compare the figures before and after.",
    "Pre-trade trading cost": "Take half the gap between the live bid and ask and apply it to the size of each order.",
}
DESK_ALIASES = {
    "Intraday P&L": ("today's P&L", "daily P&L", "live P&L", "P&L since the close"),
    "Trading P&L from today's fills": ("fills", "executions", "trading P&L"),
    "Intraday FX P&L": ("FX P&L", "currency P&L", "cash revaluation"),
    "P&L explain": ("P&L attribution", "market move", "stock-specific", "idiosyncratic"),
    "Beta to the primary benchmark": ("holding beta", "market beta"),
    "Gross exposure (live)": ("gross exposure", "leverage"),
    "Net exposure (live)": ("net exposure", "net long"),
    "Largest single name (live)": ("single name limit", "concentration limit"),
    "One-day VaR 99% (live)": ("99% VaR", "live VaR"),
    "Quote age": ("stale quote", "last changed", "delayed"),
    "Desk limit status": ("risk limits", "limit breach", "traffic light"),
    "Pre-trade check": ("pre-trade", "what if I trade", "order check"),
    "Pre-trade trading cost": ("bid-ask spread", "slippage", "transaction cost"),
}
DESK_RELATED = {
    "Intraday P&L": ("P&L explain", "Intraday P&L share of NAV", "Quote age"),
    "Intraday P&L share of NAV": ("Intraday P&L", "Desk limit status"),
    "Trading P&L from today's fills": ("Intraday P&L",),
    "Intraday FX P&L": ("Intraday P&L", "Reporting currency"),
    "P&L explain": ("Beta to the primary benchmark", "Intraday P&L", "Intraday FX P&L"),
    "Beta to the primary benchmark": ("P&L explain", "Beta"),
    "Gross exposure (live)": ("Net exposure (live)", "Desk limit status"),
    "Net exposure (live)": ("Gross exposure (live)",),
    "Largest single name (live)": ("Largest holding", "Desk limit status"),
    "One-day VaR 99% (live)": ("VaR 95% — daily", "Pre-trade check"),
    "Quote age": ("Stale quote share", "Intraday P&L"),
    "Stale quote share": ("Quote age", "Desk limit status"),
    "Desk limit status": ("Gross exposure (live)", "One-day VaR 99% (live)", "Stale quote share"),
    "Pre-trade check": ("Pre-trade trading cost", "Desk limit status", "What-if comparison"),
    "Pre-trade trading cost": ("Pre-trade check",),
}
DESK_SECTION = MetricSection(
    "desk",
    "Desk",
    "Desk: live P&L, limits and the pre-trade check",
    "The Desk page (desk-risk-v1 and pre-trade-v1). Its figures are live, from the IBKR connection, and sit beside the As-of pages: the daily IBKR statement stays the book of record. Nothing on the Desk places an order.",
    DESK_DEFINITIONS,
    "Desk",
)


def desk_guide_shown(environ: dict[str, str] | None = None) -> bool:
    """Whether the guide describes the Desk: DESK_PAGE is "owners" or "on", as web_desk.desk_mode reads it."""

    import os

    return (os.environ if environ is None else environ).get("DESK_PAGE", "").strip().lower() in ("owners", "on")


def _add_desk_section() -> None:
    """Place the Desk section after the Modelling sections, with the same checks as the rest of the guide."""

    global METRIC_SECTIONS, METHODOLOGY_VERSIONS
    from .quant import desk_risk, pre_trade

    for metric in DESK_DEFINITIONS:
        if metric.name in _BY_NAME:
            raise RuntimeError("Metric guide names must be unique")
        _BY_NAME[metric.name] = metric
        METRIC_ANCHORS[metric.name] = metric_anchor(metric.name)
        _SECTION_OF[metric.name] = DESK_SECTION
    if len(set(METRIC_ANCHORS.values())) != len(METRIC_ANCHORS):
        raise RuntimeError("Metric guide names and deep-link anchors must be unique")
    if set(DESK_CLIENT_CALCULATIONS) != {metric.name for metric in DESK_DEFINITIONS}:
        raise RuntimeError("Every Desk definition needs its plain-language calculation")
    CLIENT_CALCULATIONS.update(DESK_CLIENT_CALCULATIONS)
    METRIC_ALIASES.update(DESK_ALIASES)
    RELATED_METRICS.update(DESK_RELATED)
    for name, targets in DESK_RELATED.items():
        for target in targets:
            if target not in _BY_NAME or target == name:
                raise RuntimeError(f"Invalid related metric for {name}: {target}")
    keys = [section.key for section in METRIC_SECTIONS]
    at = keys.index("modelling-portfolio") + 1 if "modelling-portfolio" in keys else len(keys)
    METRIC_SECTIONS = METRIC_SECTIONS[:at] + (DESK_SECTION,) + METRIC_SECTIONS[at:]
    METHODOLOGY_VERSIONS = METHODOLOGY_VERSIONS + (
        (desk_risk.MODEL_VERSION, "Desk live P&L, explain and limits"),
        (pre_trade.MODEL_VERSION, "Desk pre-trade check"),
    )


if desk_guide_shown():
    _add_desk_section()
