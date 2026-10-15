from __future__ import annotations

from dataclasses import dataclass
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
)


# Sections follow the website's own tabs so a reader can find a number where
# they saw it.  Names are the deep-link keys used by metric cards; renaming one
# breaks existing links, so regroup rather than rename.
_LAYOUT = (
    (
        "overview",
        "Overview",
        "Overview and period returns",
        "Account value, day change, period returns, the money-weighted return and external flows shown on Overview and in the Performance tab's period returns table (performance-periods-v1).",
        ("As of", "Data freshness", "Net asset value (NAV)", "Day change", "Time-weighted return (TWR)",
         "TWR annualised", "IRR annualised", "Net external flows", "Positions", "Reporting currency",
         "Observations"),
    ),
    (
        "benchmark",
        "Benchmark",
        "Performance: benchmark comparison",
        "The comparison chart and statistics use only dates on which both the portfolio and the benchmark have validated values (benchmark-comparison-v1). Portfolio returns adjust for external cash flows; benchmark returns use closing levels.",
        ("Cash-flow-adjusted daily portfolio return", "Daily benchmark return", "Benchmark return basis",
         "Rebased portfolio or benchmark level", "Portfolio total return", "Benchmark total return",
         "Excess total return", "Tracking error", "Information ratio", "Beta", "Correlation",
         "Relative win rate", "Rolling beta and correlation"),
    ),
    (
        "statistics",
        "Statistics",
        "Performance: return statistics, calendar and drawdowns",
        "Institutional statistics on the Performance tab (portfolio-analytics-v1). They use every validated NAV date and the same cash-flow-adjusted returns as the period returns table, so they can differ slightly from the benchmark-aligned Risk tab.",
        ("Sortino ratio", "Calmar ratio", "Maximum drawdown (since inception)", "Current drawdown",
         "Drawdown episode", "Downside deviation", "Skewness", "Excess kurtosis", "Best and worst day",
         "Best and worst month", "Positive months", "Average gain and loss month", "Gain/loss ratio",
         "Monthly return", "Rolling 3-month volatility", "Trailing 12-month return",
         "Contribution to return"),
    ),
    (
        "holdings",
        "Holdings",
        "Holdings and concentration",
        "Position values from the latest validated holdings snapshot on or before As of, converted into the reporting currency with dated FX (holdings-view-v1), plus the concentration measures on the Holdings tab.",
        ("Quantity", "Market value", "Value in reporting currency", "Cost basis", "Unrealized P&L", "P&L %",
         "Holding weight", "RSI (14)", "Sector allocation", "Currency exposure", "Largest holding", "Top 5 weight",
         "Top 10 weight", "Effective number of holdings"),
    ),
    (
        "risk",
        "Risk",
        "Risk and tail-loss analytics",
        "Risk metrics on the Risk tab use validated, cash-flow-adjusted returns aligned to the primary benchmark's trading dates through As of (benchmark-comparison-v1). The contributor tables explain which unchanged USD positions drove Expected Tail Loss on historical tail days.",
        ("Total return", "Annualized return", "Annualized volatility", "Risk-free rate", "Sharpe ratio",
         "Expected maximum drawdown (1 year)", "Actual maximum drawdown (1 year)",
         "Tail maximum drawdown (1 year)", "Crisis maximum drawdown (15/9/2008–9/3/2009)",
         "VaR 95% — daily", "Daily Expected Tail Loss (95%)", "Win rate", "BLUE mean daily return",
         "Expected Tail Loss contribution", "Top 5 ETL contributors", "Bottom 5 ETL contributors",
         "ETL residual contribution", "Holdings correlation"),
    ),
    (
        "cash-trades",
        "Cash and trades",
        "Cash and trades",
        "Cash balances, cash events and executed trades imported from the daily IBKR refresh, and the trading-activity measures built from them.",
        ("Ending cash", "Settled cash", "Cash activity amount", "Trade proceeds", "Commission", "Net cash",
         "Realized P&L", "Trading activity", "Portfolio turnover", "Commissions / NAV"),
    ),
    (
        "reports",
        "Reports",
        "Reports and data quality",
        "Operational labels that decide whether data or report files may be used.",
        ("Ready", "Ready for review", "Generation pending", "Quality status", "Return observations"),
    ),
    (
        "system",
        "System",
        "System health and administration",
        "Platform-level operating indicators. They monitor process health but do not replace financial reconciliation.",
        ("Tracked NAV subjects", "Latest portfolio date", "Latest ingestion run", "Benchmark observations",
         "Assurance checks passed / warnings / failures", "Consecutive passing days", "Open incidents",
         "Portal failure count", "Rollback threshold", "Production authority", "Monthly review due",
         "Last-known-good revision"),
    ),
)
_BY_NAME = {metric.name: metric for metric in _DEFINITIONS}
if len(_BY_NAME) != len(_DEFINITIONS):
    raise RuntimeError("Metric guide names must be unique")
METRIC_SECTIONS = tuple(
    MetricSection(key, short_title, title, introduction, tuple(_BY_NAME[name] for name in names))
    for key, short_title, title, introduction, names in _LAYOUT
)
_placed = [metric.name for section in METRIC_SECTIONS for metric in section.metrics]
if sorted(_placed) != sorted(_BY_NAME):
    raise RuntimeError("Every metric definition must appear in exactly one guide section")


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
}
for _name, _targets in (*METRIC_ALIASES.items(), *RELATED_METRICS.items()):
    if _name not in _BY_NAME:
        raise RuntimeError(f"Unknown metric in guide cross-references: {_name}")
for _name, _targets in RELATED_METRICS.items():
    for _target in _targets:
        if _target not in _BY_NAME or _target == _name:
            raise RuntimeError(f"Invalid related metric for {_name}: {_target}")


METHODOLOGY_VERSIONS = (
    ("benchmark-comparison-v1", "benchmark comparison and Risk tab"),
    ("performance-periods-v1", "period returns, IRR and flows"),
    ("portfolio-analytics-v1", "Performance statistics, calendar, rolling and concentration"),
    ("holdings-view-v1", "holdings values, P&L and weights"),
)

# Conventions that apply across the guide, so individual entries need not
# repeat them.
CALCULATION_CONVENTIONS = (
    ("Returns", "Daily returns remove external deposits and withdrawals with a Modified Dietz half-flow adjustment and are chain-linked into time-weighted returns."),
    ("Annualising", "Volatility-type figures scale daily statistics by √252. Growth rates annualise over calendar days: 365 for period returns and IRR, 365.25 for the Risk tab's annualized return. Periods under a year are not annualised."),
    ("Currency", "Portfolio-level figures are in the reporting currency, converted with the dated FX rate on or before each valuation date."),
    ("Dates", "Benchmark comparisons and the Risk tab use only dates both series share, and unmatched dates are not filled. Performance statistics use every validated NAV date."),
    ("Missing data", "If required data is missing, stale or invalid, the website shows the metric as unavailable instead of estimating it."),
    ("Display", "A stored decimal of 0.05 is displayed as 5.00%. VaR and Expected Tail Loss are shown as positive loss percentages; drawdowns are shown as negative percentages."),
)

ALIASES_SHOWN = 4


def _definition_row(metric: MetricDefinition) -> str:
    client_calculation = CLIENT_CALCULATIONS[metric.name]
    anchor = METRIC_ANCHORS[metric.name]
    aliases = METRIC_ALIASES.get(metric.name, ())
    related = RELATED_METRICS.get(metric.name, ())
    search_text = " ".join(
        (
            metric.name,
            *aliases,
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
            f'<a href="#{escape(METRIC_ANCHORS[name], quote=True)}">{escape(name)}</a>'
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


def render_metrics_guide() -> str:
    sections = []
    for section in METRIC_SECTIONS:
        rows = "".join(_definition_row(metric) for metric in section.metrics)
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
    total = sum(len(section.metrics) for section in METRIC_SECTIONS)
    chips = "".join(
        f'<a href="#{escape(_section_id(section), quote=True)}" data-section-chip="{escape(_section_id(section), quote=True)}">'
        f'{escape(section.short_title)} <span>{len(section.metrics)}</span></a>'
        for section in METRIC_SECTIONS
    )
    conventions = "".join(
        f"<div><dt>{escape(label)}</dt><dd>{escape(text)}</dd></div>"
        for label, text in CALCULATION_CONVENTIONS
    )
    versions = "".join(
        f"<li><code>{escape(version)}</code> {escape(scope)}</li>"
        for version, scope in METHODOLOGY_VERSIONS
    )
    return (
        '<div class="page-view active" data-section="metrics-guide">'
        '<div id="metric-guide-top" class="card wide metric-guide-intro">'
        '<div class="label">Plain-English guide for clients and sales conversations</div>'
        '<div class="value">Understand every number</div>'
        '<p class="muted">Each definition starts with the business meaning, explains '
        'the calculation without jargon and gives a practical interpretation. Open '
        'the technical panel only when you need the exact formula or methodology. '
        'Sections follow the website tabs, so you can find a number where you saw it.</p>'
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
        'placeholder="Try Sharpe, TWR, IRR, drawdown, CVaR or HHI" '
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
