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
    title: str
    introduction: str
    metrics: tuple[MetricDefinition, ...]


METRIC_SECTIONS = (
    MetricSection(
        "Account overview and chart values",
        "Core account values shown on Overview, Holdings and the two performance charts.",
        (
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
                "The number of available total-NAV observations from 20 January 2025 through As of.",
                "Count of validated TOTAL asset-class NAV rows within the website reporting period.",
                "A coverage count, not a count of calendar days, holdings or trades.",
            ),
            MetricDefinition(
                "Positions",
                "The number of holdings in the latest validated position snapshot on or before As of.",
                "Count of position rows in the selected effective holdings snapshot.",
                "Multiple lots or model rows can affect counts; use the Holdings table for the actual constituents.",
            ),
            MetricDefinition(
                "Reporting currency",
                "The common currency in which portfolio-level values and analytics are presented.",
                "Configured by the account data contract. U16088571 is USD; required FX conversion must occur before portfolio comparison calculations.",
                "A holding's trading currency can differ from the portfolio reporting currency.",
            ),
            MetricDefinition(
                "Rebased portfolio or benchmark level",
                "A common-scale growth index used to compare two differently sized series.",
                "Start at 100 on the first common valid date. Each later value is Previous rebased value × (1 + current validated daily return).",
                "A value of 112 means 12% growth since the common start. It is not NAV or an investable price.",
            ),
        ),
    ),
    MetricSection(
        "Return engine and benchmark comparison",
        "Performance uses validated common dates. Portfolio returns adjust for external cash flows; benchmark returns use closing index levels.",
        (
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
                "The configured S&P 500 and NASDAQ Composite series are price indexes, so reinvested dividends are not included.",
            ),
            MetricDefinition(
                "Portfolio total return",
                "Compounded portfolio performance over the displayed common-date period.",
                "Ending rebased portfolio level ÷ starting rebased portfolio level − 1; equivalently ∏(1 + rₜ) − 1.",
                "Uses cash-flow-adjusted daily returns and may differ from simple NAV change when external flows occur.",
            ),
            MetricDefinition(
                "Benchmark total return",
                "Compounded price-index performance over the same common dates.",
                "Ending rebased benchmark level ÷ starting rebased benchmark level − 1; equivalently ∏(1 + bₜ) − 1.",
                "This is price return for the configured indexes, not total return including dividends.",
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
        ),
    ),
    MetricSection(
        "Portfolio return and risk analytics",
        "Risk metrics use validated, cash-flow-adjusted U16088571 returns aligned to common primary-benchmark trading dates through As of.",
        (
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
                "Standard deviation(rₜ) × √252.",
                "A dispersion measure, not a loss forecast. It treats upside and downside variation equally.",
            ),
            MetricDefinition(
                "Risk-free rate",
                "Annual reference yield used in the Sharpe calculation.",
                "Latest validated U.S. Treasury Daily Treasury Par Yield Curve 3-month observation on or before As of, stored as a decimal annual rate.",
                "It is not the interest earned by the account. It can legitimately lag on weekends and U.S. holidays.",
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
                "Not U16088571's realized 2008–2009 performance. Proxy choices, today's holdings and Yahoo data materially affect the result.",
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
        ),
    ),
    MetricSection(
        "Expected Tail Loss attribution",
        "The contributor tables explain which unchanged USD positions drove portfolio Expected Tail Loss on historical tail days.",
        (
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
        ),
    ),
    MetricSection(
        "Cash, trade and holding fields",
        "Account records imported from the daily IBKR refresh. These are values and transaction fields rather than risk-model estimates.",
        (
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
        ),
    ),
    MetricSection(
        "Monthly reporting, quality and availability statuses",
        "Operational labels that determine whether data or report files may be used.",
        (
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
        ),
    ),
    MetricSection(
        "System Health and Administration KPIs",
        "Platform-level operating indicators. They monitor process health but do not replace financial reconciliation.",
        (
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
        ),
    ),
)


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
    "Observations": "Count the validated NAV dates available from 20 January 2025 up to the selected date.",
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
    "Risk-free rate": "Use the latest validated U.S. 3-month Treasury yield available on or before the selected date.",
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
}


def _definition_row(metric: MetricDefinition) -> str:
    client_calculation = CLIENT_CALCULATIONS[metric.name]
    anchor = METRIC_ANCHORS[metric.name]
    search_text = " ".join(
        (
            metric.name,
            metric.meaning,
            client_calculation,
            metric.calculation,
            metric.interpretation,
        )
    ).lower()
    return (
        f'<article id="{escape(anchor, quote=True)}" class="metric-definition" '
        f'data-metric-search="{escape(search_text, quote=True)}" tabindex="-1">'
        '<div class="metric-definition-heading">'
        f'<h3>{escape(metric.name)}</h3><span>Client-friendly explanation</span></div>'
        '<div class="metric-explanation-grid">'
        '<div><div class="metric-explanation-label">What it tells you</div>'
        f'<p>{escape(metric.meaning)}</p></div>'
        '<div><div class="metric-explanation-label">How we work it out</div>'
        f'<p>{escape(client_calculation)}</p></div>'
        '<div><div class="metric-explanation-label">How to explain it</div>'
        f'<p>{escape(metric.interpretation)}</p></div></div>'
        '<details class="metric-technical"><summary>View technical formula and methodology</summary>'
        f'<div><code>{escape(metric.calculation)}</code></div></details></article>'
    )


def render_metrics_guide() -> str:
    sections = []
    for section in METRIC_SECTIONS:
        rows = "".join(_definition_row(metric) for metric in section.metrics)
        sections.append(
            '<div class="card wide metric-guide-section">'
            f'<div class="label">{escape(section.title)}</div>'
            f'<p class="muted">{escape(section.introduction)}</p>'
            f'<div class="metric-guide-list">{rows}</div></div>'
        )
    total = sum(len(section.metrics) for section in METRIC_SECTIONS)
    return (
        '<div class="page-view active" data-section="metrics-guide">'
        '<div class="card wide metric-guide-intro">'
        '<div class="label">Plain-English guide for clients and sales conversations</div>'
        '<div class="value">Understand every number</div>'
        '<p class="muted">Each definition starts with the business meaning, explains '
        'the calculation without jargon and gives a practical interpretation. Open '
        'the technical panel only when you need the exact formula or methodology.</p>'
        '<div class="methodology-callout"><strong>A simple way to read this guide</strong><br>'
        '“What it tells you” explains the purpose. “How we work it out” describes the '
        'calculation in everyday language. “How to explain it” highlights the main '
        'message and the important limitation. The expandable technical method remains '
        'the calculation source of truth for version <code>benchmark-comparison-v1</code>.</div>'
        '<div class="methodology-callout secondary"><strong>Important calculation convention</strong><br>'
        'Portfolio analytics use validated returns after adjusting for external money '
        'added or withdrawn, and benchmark comparisons use matching dates. If required '
        'data is missing, stale or invalid, the website shows the metric as unavailable '
        'instead of estimating it. A stored decimal of 0.05 is displayed as 5.00%.</div>'
        '<label class="metric-search-label"><span class="label">Find a metric or term</span>'
        '<input id="metric-search" type="text" autocomplete="off" '
        'placeholder="Try Sharpe, drawdown, cash, benchmark or report status" '
        'aria-describedby="metric-search-status"></label>'
        f'<div id="metric-search-status" class="muted">Showing all {total} definitions.</div>'
        '</div>'
        + "".join(sections)
        + '</div>'
    )


METRICS_GUIDE_SCRIPT = r"""
<script>
const metricSearch=document.querySelector('#metric-search');
const metricRows=[...document.querySelectorAll('.metric-definition')];
const metricSections=[...document.querySelectorAll('.metric-guide-section')];
const metricStatus=document.querySelector('#metric-search-status');
const updateMetricFilter=()=>{
  const query=metricSearch.value.trim().toLowerCase();
  let visible=0;
  metricRows.forEach(row=>{
    const match=!query||row.dataset.metricSearch.includes(query);
    row.hidden=!match;
    if(match)visible+=1;
  });
  metricSections.forEach(section=>{
    section.hidden=!section.querySelector('.metric-definition:not([hidden])');
  });
  metricStatus.textContent=query
    ?`${visible} definition${visible===1?'':'s'} match “${metricSearch.value.trim()}”.`
    :`Showing all ${metricRows.length} definitions.`;
};
metricSearch.addEventListener('input',updateMetricFilter);
</script>
"""
