# Certified figures for people and AI agents (U5, the current form of S7)

Design for Ying's approval, 18 Oct 2026. Nothing here is built or switched on. The only
files in this branch are the design, a draft list of figures that no code reads, and a test
that checks the list against the Metrics guide ("The proof in this branch").

## Summary

Today the same number is worked out in several places: each website page calculates its
figures when it loads, the factsheet works them out again from the frozen month-end
snapshot, and the AI agents work them out a third way from their own views of silver. They
usually agree, but not always, and nothing records which one a reader saw. A **certified
figure** is one stored number per Metrics guide figure, per account and per date, written by
one daily build that checks itself before it publishes. It carries its definition (with a
version), its as-of date, how fresh it is, its calculation version and the run that made it.
Pages read these rows instead of calculating; the agents read them through one typed tool,
`get_certified_figures`, and cite each figure they quote. The first list has 71 figures on
seven pages. The layer is built the day after approval and runs in shadow from **Tue 20
Oct**. Pages then move in three batches, each a website release checked with the release
data report: **Thu 22** Overview, Holdings and Cash; **Fri 23** Performance; **Sat 24** Risk,
Combined holdings and the agents' read tool. Sun 25 to Thu 29 are spare days for a page that
failed its report; nothing moves after Thu 29, and month-end (1 to 7 Nov) has no changes.
The factsheet stays on the frozen month-end snapshot until 8 Nov. Performance › Attribution and
the monthly reports move on **Tue 27 Oct** in the automatic fallback, which keeps every report
exactly as today's code makes it ("Monthly reports and Attribution on certified figures").
One setting undoes any page.

## The dates

| When | What | Do people see a change? |
|---|---|---|
| Sun 18 to Mon 19 | Build the parts that are safe before approval: code only, all off ("The build plan") | No |
| Mon 19 | Approval. Merge; apply the migration; rehearse the build and every page's figures on a restored copy | No |
| **Tue 20** | **The layer in shadow:** the build job runs on live, every past date is filled in, and the website shows today's figures while logging any difference from the certified ones | No |
| Wed 21 | No moves (the factsheet job moves, and another team has a step) | No |
| **Thu 22** | **Batch 1:** Accounts list and Overview, Holdings, Cash | Yes: the same figures, with "Certified · as of" |
| **Fri 23** | **Batch 2:** Performance | Yes |
| **Sat 24** | **Batch 3:** Risk, Combined holdings, the agents' read tool | Yes |
| Sun 25 to Thu 29 | Spare days for any page that failed its report | Only for those pages |
| Fri 30 to Sat 31 | Watching; no moves (open question 8) | No |
| 1 to 7 Nov | Month-end, no changes. Undoing a page is the only change allowed (open question 9) | No |
| From 8 Nov | Monthly reports, the factsheet check, Attribution, the charts, the publish gate, flows from silver ("After month-end") | One release at a time |

## Which pages show certified figures

| Batch | Page | Certified figures it shows | Stays as today on that page |
|---|---|---|---|
| 1, Thu 22 | **Accounts list, Overview** and the account page header | As of, Data freshness, NAV, Day change, TWR (MTD, QTD, YTD, each year, since inception), TWR annualised, IRR annualised, Net external flows, Positions, Reporting currency, Observations; the top holdings' weights and the sector allocation | The one-year NAV sparkline (daily NAV records) |
| 1, Thu 22 | **Holdings** | Per holding: Quantity, Market value, Value in reporting currency, Cost basis, Unrealized P&L, P&L %, Holding weight. For the account: Gross market value, Sector allocation, Currency exposure, Largest holding, Top 5 and Top 10 weight, Effective number of holdings | RSI (14), a price indicator |
| 1, Thu 22 | **Cash** | Ending cash and Settled cash, per currency | The cash activity ledger (records from the silver read contract); the model cash of the two composite portfolios (not a guide figure) |
| 2, Fri 23 | **Performance:** NAV chart, Period returns, Monthly returns, Vs benchmark, Contributions | Overview's figures again on Period returns and Contributions; Monthly return (each month and year); per benchmark: Portfolio, Benchmark and Excess total return, Tracking error, Information ratio, Beta, Correlation, Relative win rate; the rebased levels on the NAV chart | The daily return series drawn on the Vs benchmark chart (already stored in gold) |
| 3, Sat 24 | **Risk › Risk snapshot** | The 17 snapshot figures: Total and Annualized return, Annualized volatility, Risk-free rate, Sharpe ratio, the three 1-year drawdowns, Crisis maximum drawdown (2008–09), VaR 95%, Daily Expected Tail Loss, Win rate, BLUE mean daily return, Maximum drawdown (benchmark dates), ETL contribution per holding and residual, Crisis scenario maximum drawdown | The top and bottom 5 contributors are the certified rows sorted |
| 3, Sat 24 | **Risk › Statistics, Drawdowns, Rolling risk, Correlation** | Sortino, Calmar, Maximum and Current drawdown, Annualized volatility (every NAV date), Downside deviation, Skewness, Excess kurtosis, Best and worst day and month, Positive months, Average gain and loss month, Gain/loss ratio, Trailing 12-month return | The drawdown episodes table, the rolling charts, the holdings correlation matrix |
| 3, Sat 24 | **Combined holdings** | Each account's certified holding records, added up when the page is read | The sum and the conversion into the chosen currency are worked out when read, with today's functions, because they depend on who is looking and which currency they pick (open question 11) |
| 3, Sat 24 | **The agents' read tool** | Every figure above whose page is on | |

**Stay on today's path until after month-end (from 8 Nov):**

- **Monthly reports.** They produce the 1 Nov client reports from the frozen month-end snapshot.
  From Tue 27 Oct they compare their figures with the certified ones in the automatic fallback,
  which changes nothing in them ("Monthly reports and Attribution on certified figures").
- **The factsheet renderers.** They read the frozen snapshot, and their job moves on Wed 21.
- **Performance › Attribution.** It uses the monthly reports' engine (`monthly_reporting_analytics`),
  so it moves with them: its own page key `attribution`, batch 4, Tue 27 Oct.
- **The agents' month-end commentary job** (`month_end_drafter`, 2 to 7 Nov). It keeps its own tool list, so its drafts match the frozen reports.

**Out of scope:** Modelling (hypothetical models, labelled as such: open question 7), Trade
history (trade records, and the trading activity, turnover and commission measures worked out
from them), Data exports, the AI analyst chat page itself (its figures come through the read
tool), System health, the Metrics guide, IBKR connections, Account onboarding previews,
Administration and the Agent inbox cards.

The charts, tables and indicators marked "stays as today" keep their current calculation and
carry no "Certified" mark. They move after month-end, each with its own report.

## What the code shows today

Read on 18 Oct from the medallion branch, which includes PR #150 (now merged into main as
`ba3c644`). These findings shape the design.

- **One formula, many copies.** The half-flow daily return, (Vₜ − Vₜ₋₁ − Fₜ) ÷ (Vₜ₋₁ + ½Fₜ),
  is written out in `benchmark_analytics.py`, `performance_periods.py`, `portfolio_analytics.py`,
  `monthly_artifacts.py` (the factsheet), `monthly_reporting_analytics.py`, `report.py`,
  `derived_portfolio.py` and `wealthryse_agent/tools.py`, plus `metrics_guide_reference.py`,
  which is separate on purpose as a check.
- **Most pages calculate when they load.** Only the Risk snapshot (`gold.risk_analytics_snapshots`)
  and the benchmark chart (`gold.performance_comparison_daily`) read stored figures. Overview,
  Period returns, the Vs benchmark summary, Statistics, Drawdowns, Monthly returns and the
  Holdings measures are worked out on every request. The accounts list does it for every
  account on every load.
- **The agents have no free SQL, but they do their own sums.** The tool server takes no SQL
  from the model. But the agent views cover silver tables and one bronze table
  (`agent.flex_rows`), and no gold table, so every figure an agent quotes is worked out by the
  tool itself. `get_risk_metrics` uses the last year and annualises by valuations per year; the
  Risk snapshot uses the whole history on benchmark dates and √252. Both are called "Annualized
  volatility". `compare_to_benchmark` uses raw benchmark prices in the benchmark's own
  currency; the website converts to the reporting currency.
- **Agent tools are shared with month-end.** `ibkr_etl/agent_tools.py` (the website analyst's
  tools) also builds the frozen month-end snapshots (`reporting_snapshot.py`), and the agents'
  month-end commentary job calls `get_performance` and `compare_to_benchmark`. So the existing
  tools must not change before month-end: the new tools go beside them.
- **One guide entry, two calculations.** "Annualized volatility" is benchmark-dated on the Risk
  snapshot and every-NAV-date on Risk › Statistics. The factsheet's "Maximum drawdown" is bound
  to the guide's "Maximum drawdown (since inception)", which says every NAV date, but its value
  comes from the benchmark-dated risk metrics.
- **No last good version to go back to.** The risk snapshot job overwrites its row for a date
  (`ON CONFLICT DO UPDATE`), and the 18 Oct benchmark change rewrote
  `gold.performance_comparison_daily` in place without a version change.
- **The publish gate does not publish yet.** Its draft (`publish_gate.py`, being built in
  shadow) checks a gold build and reports a verdict; it records nothing that readers could
  follow, and the review puts it live from 8 Nov. So the certified layer cannot wait for it
  ("How the build publishes before 8 Nov").
- **Flows still come from bronze.** The flows reader (`provisional_benchmark._external_flows`)
  reads the Transfers rows of bronze `flex_rows`, the known exception in
  [MEDALLION_ARCHITECTURE.md](MEDALLION_ARCHITECTURE.md). The build calls the same reader, so the
  figures stay the same; moving flows to silver is a separate release after month-end.
- **The daily jobs run every day, weekends included.** A weekend build has no new IBKR data,
  so it repeats Friday's figures, and the "same inputs, same answer" check confirms them.
- Smaller: the agent tool `get_performance` promises a 1Y period, which the period engine
  does not calculate.

## What a certified figure is

A number that:

1. means exactly one Metrics guide definition, worked out one way (where the website works
   a definition out two ways, there are two figures with different names);
2. was written by a build that passed its checks before it was published;
3. carries its as-of date, freshness, versions and lineage, so anyone can check it.

Every page and every agent reads the same row.

### Its fields

| Field | Example | Where it comes from |
|---|---|---|
| value, unit | 1.12, `number` | The build. Units as in the factsheet bindings: `ratio` (0.05 = 5%), `number`, `money`, `date`, `text` |
| currency | `USD` | The subject's reporting currency for `money`, or the holding's or balance's own currency where the figure says so |
| subject | `U1234567`, physical account, USD | `platform_subjects` |
| figure id | `risk.sharpe_ratio` | The register (below) |
| period, item, benchmark | `ytd`; a holding, sector, currency or scenario; `GSPC` | Only for figures that have them |
| as-of date | 2026-10-21 | The valuation date the figure is for |
| definition id, version | `metric-sharpe-ratio`, `b2a07c81b2b6` | The guide's anchor, which the guide keeps stable; the version is the first 12 characters of a sha256 of the definition's wording, so any change to the wording changes it |
| calculation version | `risk-analytics-v2+benchmark-comparison-v1` | The method versions of the code and the source row. IBKR's own values (cash, quantity, cost) say `imported` and the source contract's version |
| input fingerprint | sha256 | Of the inputs the build used |
| freshness | `fresh`, `behind` or `late` | Worked out when read (below) |
| lineage | build id, run id, source row | The build's lineage run; the gold or silver row it came from |
| publish status | `published` | The build's own decision ("How the build publishes before 8 Nov") |

**Same inputs, same answer.** If a figure for a past date changes while its inputs and its
calculation version are the same, the build is held for approval. A changed input (IBKR
corrected a statement) is allowed and listed. This turns the review's rule "figures change,
version changes" into a check, and would have caught the 18 Oct benchmark change.

**Freshness**, worked out when read:

- `fresh`: the figure is for the subject's newest validated NAV date.
- `behind`: a newer NAV date exists but its figures are not published yet (the build has not
  run, or it was held). It says how many business days, and why.
- `late`: behind by more than the source contract's promise (daily by 10:00 UTC, at most four
  days), which also raises the layer freshness warning.

Business days follow the guide's "Data freshness" rule (the previous weekday in Hong Kong).

### How it is stored (sketch, not a migration)

```sql
CREATE TABLE gold.certified_builds (
    tenant_id     uuid NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    build_id      uuid NOT NULL,
    started_at    timestamptz NOT NULL,
    status        text NOT NULL CHECK (status IN ('shadow', 'published', 'held', 'rolled_back')),
    published_at  timestamptz,
    checks        jsonb NOT NULL,        -- each check's result; the publish gate's verdict once it has one
    reason        text,                  -- why it was held or rolled back, in plain words
    lineage_run_id uuid,
    PRIMARY KEY (tenant_id, build_id)
);

CREATE TABLE gold.certified_figures (
    tenant_id      uuid NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    build_id       uuid NOT NULL,                  -- the build that wrote it
    subject_id     text NOT NULL,                  -- account or portfolio
    figure_id      text NOT NULL,                  -- 'risk.sharpe_ratio', from the register
    period         text NOT NULL DEFAULT '',       -- '', 'mtd', 'ytd', 'year_2025', 'month_2026-10', ...
    item           text NOT NULL DEFAULT '',       -- '', a holding conid, sector, currency or scenario
    benchmark_id   text NOT NULL DEFAULT '',       -- '' unless the figure compares with a benchmark
    as_of_date     date NOT NULL,
    value          double precision,               -- NULL when unavailable
    value_text     text,                           -- for the date and text units
    unit           text NOT NULL CHECK (unit IN ('ratio', 'number', 'money', 'date', 'text')),
    currency       text,
    status         text NOT NULL CHECK (status IN ('ok', 'unavailable')),
    reason         text,                           -- why it is unavailable, in plain words
    detail         jsonb NOT NULL DEFAULT '{}',    -- facts that travel with it: a date, counts, an FX rate
    period_start   date, period_end date, observations integer,
    definition_id  text NOT NULL,
    definition_version text NOT NULL,
    calculation_version text NOT NULL,
    input_sha256   text NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    source_table   text NOT NULL,                  -- 'gold.risk_analytics_snapshots', 'silver.subject_nav_series'
    source_key     jsonb NOT NULL,                 -- the source row's key, where lineage.trace() starts
    lineage_run_id uuid,                           -- stamped by the same trigger as migration 071's
    written_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, subject_id, figure_id, period, item, benchmark_id, as_of_date, build_id),
    FOREIGN KEY (tenant_id, build_id) REFERENCES gold.certified_builds
);
-- Both tables: row-level security forced, with the tenant_isolation policy every gold table has.
-- Append-only. A build writes a row only for a new date or a value that changed,
-- so history is not copied every day.

-- What readers see: for each figure, the row from the newest published build.
CREATE VIEW gold.v_certified_figures WITH (security_invoker = true) AS
SELECT DISTINCT ON (f.tenant_id, f.subject_id, f.figure_id, f.period, f.item, f.benchmark_id, f.as_of_date)
       f.*, b.status AS build_status, b.published_at
  FROM gold.certified_figures f
  JOIN gold.certified_builds b ON b.tenant_id = f.tenant_id AND b.build_id = f.build_id
 WHERE b.status = 'published'
 ORDER BY f.tenant_id, f.subject_id, f.figure_id, f.period, f.item, f.benchmark_id, f.as_of_date,
          b.published_at DESC;
```

One shared table, not one table per figure as S7 first said (open question 1): 71 tables would
each need a contract, a migration and a reader, while one narrow table holds single numbers,
per-period numbers and small lists alike. Daily series (the benchmark chart) stay in their
gold table. Because rows are never overwritten, "what did the page show on 3 Nov" is the same
view with `published_at` up to 3 Nov, which review item 8 wants too.

### As the read tool returns it

```json
{
  "tool": "get_certified_figures",
  "account_id": "U1234567",
  "as_of": "2026-10-21",
  "subject": {"id": "U1234567", "type": "physical_account", "reporting_currency": "USD"},
  "figures": [
    {
      "ref": "F1",
      "figure_id": "risk.sharpe_ratio",
      "label": "Sharpe ratio",
      "value": 1.12,
      "unit": "number",
      "display": "1.12",
      "as_of_date": "2026-10-21",
      "period": {"start": "2025-01-21", "end": "2026-10-21", "observations": 438,
                 "basis": "benchmark dates"},
      "definition": {"id": "metric-sharpe-ratio", "version": "b2a07c81b2b6", "name": "Sharpe ratio",
                     "link": "/metrics-guide#metric-sharpe-ratio",
                     "what_it_tells_you": "Annualized excess portfolio return per unit of daily return volatility."},
      "calculation_version": "risk-analytics-v2+benchmark-comparison-v1",
      "freshness": {"status": "fresh", "latest_data_date": "2026-10-21", "business_days_behind": 0,
                    "promise": "daily by 10:00 UTC, at most 4 days behind"},
      "lineage": {"build_id": "5d0c…", "run_id": "9b41…",
                  "source": {"table": "gold.risk_analytics_snapshots",
                             "key": {"as_of_date": "2026-10-21", "analytics_version": "risk-analytics-v2"}}},
      "build": {"status": "published", "published_at": "2026-10-22T09:52:11Z", "checks_passed": 41,
                "publish_gate": "shadow"}
    }
  ],
  "unavailable": [
    {"figure_id": "risk.risk_free_rate", "reason": "No validated rate on or before 21 Oct 2026."}
  ]
}
```

The account number and values are examples. The real answer carries the subject the person
may see, and nothing else.

## The figures to certify first

The draft register `infra/medallion/certified-figures.json` lists all 71, page by page and
batch by batch, with each one's guide entry, unit, source and the factsheet value it covers;
`tests/test_certified_figures_register.py` checks it ("The proof in this branch"). 26 come
from gold tables that exist today (18 read directly, 8 worked out from gold rows). 45 are new,
worked out by the build from silver with the same functions the pages call today; 5 of those
are IBKR's own values copied with their as-of date and lineage (cash balances, quantity,
market value, cost basis).

### Batch 1, Thu 22: Accounts list, Overview, Holdings, Cash

| Page | Figures | Source |
|---|---|---|
| Accounts list, Overview | As of, Data freshness, NAV, Day change, TWR, TWR annualised, IRR annualised, Net external flows, Positions, Reporting currency, Observations | New, from `silver.subject_nav_series` and positions (performance-periods-v1), with today's flows reader |
| Holdings | Quantity, Market value, Value in reporting currency, Cost basis, Unrealized P&L, P&L %, Holding weight (per holding); Gross market value, Sector allocation, Currency exposure, Largest holding, Top 5 and Top 10 weight, Effective number of holdings | New, from `silver.subject_position_series`, `silver.fx_rates`, `silver.instrument_classifications` (holdings-view-v1, portfolio-analytics-v1) |
| Cash | Ending cash, Settled cash (per currency) | New, from `silver.subject_cash_balance_series` (imported) |

**Missing today:** a gold home for all of these (worked out on every load, for every account
on the accounts list). **Carried over unchanged:** flows from bronze, and the re-sent FX row
duplicate (review item 8), because the build calls today's readers; fixing either changes
figures, so each is its own release after month-end.

### Batch 2, Fri 23: Performance

| Figures | Source |
|---|---|
| Per benchmark: Portfolio, Benchmark and Excess total return, Tracking error, Information ratio, Beta, Correlation, Relative win rate | Worked out from `gold.performance_comparison_daily` rows (existing) |
| Rebased portfolio and benchmark level (series, NAV chart) | `gold.performance_comparison_daily` (existing) |
| Monthly return (each month and year) | New, from `silver.subject_nav_series` (portfolio-analytics-v1) |
| Overview's figures on Period returns and Contributions | Batch 1 |

**Missing today:** the eight comparison figures are worked out on each load and not stored;
the agents' `compare_to_benchmark` uses another method; the table was rewritten in place on
18 Oct.

### Batch 3, Sat 24: Risk and Combined holdings

| Page | Figures | Source |
|---|---|---|
| Risk snapshot | The 17 snapshot figures (listed under "Which pages show certified figures") | `gold.risk_analytics_snapshots` and `gold.crisis_scenario_results` (existing) |
| Statistics, Drawdowns, Rolling risk, Correlation | Sortino, Calmar, Maximum drawdown (every NAV date), Current drawdown, Annualized volatility (every NAV date), Downside deviation, Skewness, Excess kurtosis, Best and worst day and month, Positive months, Average gain and loss month, Gain/loss ratio, Trailing 12-month return | New, from `silver.subject_nav_series` (portfolio-analytics-v1) |
| Combined holdings | The Holdings page's records for each account | Batch 1 |

**Missing today:** a definition and version per figure; a previous version (the snapshot row
is overwritten); a publish status. The MS and T portfolios have their own payload shapes, so
the build needs three small readers, not one. The guide places "Annualized volatility" on the
Risk snapshot only, and its text still says the every-date version is on the Performance tab
(open question 4).

### Not in the first list

- **Charts, tables, indicators and labels** (12 guide entries, each with a reason in the
  register): daily return series, rolling series, drawdown episodes, the holdings correlation
  matrix, RSI, attribution; and labels carried with other figures (benchmark return basis, the
  top and bottom 5 contributors, crisis stand-ins).
- **Records, not figures** (5): a cash event's amount, and the fields of a trade.
- **Trade history's measures** (3): trading activity, turnover and commissions / NAV, with the
  page that is out of scope.
- **The factsheet** keeps reading the frozen month-end snapshot. Every factsheet value that
  names a guide entry is covered by a figure above, except its own NAV index
  (`normalized_nav`), which starts at the first NAV rather than the first date shared with the
  benchmark.

## How the build publishes before 8 Nov

The review puts the publish gate (item 2) live from 8 Nov, but pages move in October. So the
certified build does "write, check, then publish" for its own table only, and the other gold
tables are untouched:

1. It writes a new build (`gold.certified_builds`, status `shadow`) and its rows.
2. It checks them: the contract rules for the new table (values finite when `ok`; drawdowns at
   most zero; weights and win rates between 0 and 1; correlation between −1 and 1); certified
   equals its source (the gold row, or today's page function on the same inputs); same inputs,
   same answer; every active account and portfolio complete.
3. Only then is it marked `published`, and readers move to it. Otherwise it is `held`, and
   pages keep the last published figures, marked "Not yet updated".

The publish gate's own verdict on the source tables (from `publish_gate.check`, in shadow) is
stored with each build from the start, and becomes binding from 8 Nov with one setting
(open question 3). This is review item 2 built early for one table, so it can be the gate's
first enforced user.

**When it runs.** Twice a day, so pages update as soon as their data does: after the derived
step (about 09:00 UTC) for the NAV, holdings, cash and statistics figures, and after the
crisis step (about 09:45 UTC) for the risk and benchmark figures (open question 10). Each run
first checks that its inputs for the day are there; if not, it publishes nothing new and the
pages say "Not yet updated". Today a page shows new data the minute it lands; a moved page
shows it once the build has published, at most about an hour and a half later.

**How long a run reads.** Reading every page for every past date takes longer than one run
may: on the 18 Oct rehearsal copy, reading the history did not finish in 25 minutes. So each
run first reads the new dates and the last five, oldest first so they follow on from the
published ones, then older dates that have no published figures yet, newest first and one
account or portfolio at a time, and stops reading after 30
minutes (`CERTIFIED_FIGURES_MINUTES`, or `--minutes`). It publishes what it read, and the next
run carries on. Each subject's built dates stay one unbroken run back from its newest date, so
a page asked for an older date finds either that date's figures or none, and then shows
today's figures as it does now. The history fills in over the first few runs; `--backfill`
reads every older date again, newest first, and a backfill that stops at its time limit starts
again from the newest date, so give it `--minutes` long enough for the dates it must reach.

The check that the pages give the same figures on their own connections runs for each account
or portfolio right after its newest date is read, so the two reads are moments apart.
On a day the benchmark's market was shut (the 18 Oct rehearsal found two such days in the
history), the performance page still shows the comparison through the last common date, and the
certified figures for that day are the same, with that date in their source key.

## How agents read them

### The tools

| Tool | Parameters | Answers |
|---|---|---|
| `get_certified_figures` | `account_id` (required); `figures` (1 to 25 figure ids) or `page` (one of the seven pages); `as_of` (optional, default the newest published date); `period` (optional, such as `ytd`, `year_2025`, `month_2026-10`); `benchmark_id` (optional, default the primary benchmark) | The JSON above: each figure with value, unit, definition, versions, freshness, lineage and publish status, plus the figures that are unavailable and why |
| `trace_figure` | `account_id`; `figure_id` with `as_of` (and `period`, `item`, `benchmark_id` where they apply) | Where the number came from, from `lineage.trace()`: the build and its checks, each job run back to the IBKR statement or market-data file, with times, code versions and file sha256. Steps inferred from timing are marked so |
| `list_certified_figures` | none | The register: ids, labels, units, pages, periods and guide links. No client data, so it sits with the guide tools (`guide_tools.py`) |

The tool serves a figure only once its page is on, so an agent never quotes a certified
figure that the website does not show yet. Errors are plain: an unknown figure id (with the
closest ids), an account the caller may not see, a figure whose page has not moved (naming the
tool that still answers it), or no published figure on or before the date (with when the next
build is due). A held build is not an error: the tool returns the last published figures,
marked `behind`, with the reason.

All three are read-only, go through the existing tool server and its audit, and see exactly
one account per call, as every tool does today.

### How an answer cites its figures

- The runtime numbers each certified figure it receives `F1`, `F2` and so on, as it numbers
  tool calls `T1`, `T2` today. The answer cites them inline: "Your Sharpe ratio was 1.12 [F1]".
- The figure check (`validator.py`) becomes stricter for `[F#]`: the number beside a citation
  must match that figure's value, not just any number a tool returned, so a Sharpe ratio
  cannot be quoted as a volatility. Warn-only until 8 Nov.
- The message stores, for each `F#`, the figure id, subject, as-of date, period, build id,
  definition version and value, so the exact figure can be fetched and traced later.
- Under the answer the website lists the sources: "Sharpe ratio 1.12 · certified · as of
  21 Oct 2026 · definition · how it was made", where the last link runs `trace_figure`.
- Model results (projections, what-if, optimisation) keep `[T#]` citations and are called
  hypothetical, as today.

### What changes in the agents' data access, and when

| What | Today | Sat 24 Oct | From 8 Nov |
|---|---|---|---|
| The three new tools | None | On for the chat analyst, the website analyst and the agent jobs, except the month-end commentary job | Also for the month-end commentary job |
| The tools that work figures out (`get_performance`, `compare_to_benchmark`, `get_risk_metrics`, `get_nav_history` returns, `get_holdings` weights, `get_sector_allocation`) | Each works its figures out from the agent views | Unchanged; their descriptions point the model to `get_certified_figures` first | Each reads `agent.certified_figures`, one release at a time with a report |
| The website analyst's tools (`ibkr_etl/agent_tools.py`) | Recompute on every call | New certified tools beside them; the analyst's list points to the new ones | The old ones stay for the month-end snapshot builder |
| The figure check | Any number from any tool | `[F#]` checked, warn-only | Enforced: a figure that has a certified version may not come from elsewhere |
| Access to `agent.flex_rows` (bronze) and `agent.benchmark_prices` | Granted | Granted | Removed, once flows come from silver |
| NAV, positions, FX, instruments and sectors views | Granted | Kept, for the model tools and holding records | Kept |

The agent schema is installed by `scripts/install_agent_schema.py` on purpose, so the new
view (`agent.certified_figures`, through a `SECURITY DEFINER` function like the others) is one
approved run of that script on Tue 20, while the tools are still off. The removed grants later
are another.

## How a page moves

### Three modes per page

The website gets the readers through the Deploy website workflow, which ships only the `.py`
files of `src/ibkr_etl`. The website therefore reads the register from
`certified_figures_register_data.py`, a generated copy of `infra/medallion/certified-figures.json`.

One website setting, `CERTIFIED_FIGURES_PAGES`, for example
`overview=on,holdings=on,cash=on,performance=shadow`. A page not listed is `off`.

- **off:** today's behaviour, exactly. The default, so merging changes nothing.
- **shadow:** the page shows today's figures, also reads the certified ones and logs one line,
  `CERTIFIED_FIGURES {json}`, with the page, the figure ids that differ and counts. No values
  and no account numbers.
- **on:** the page shows the certified figures, with a small line under each card:
  "Certified · as of 21 Oct 2026 · how this is worked out". If today's figures are not
  published yet, it shows the last published ones with "Not yet updated for 22 Oct". The API
  answers gain a `certified` block (as-of date, build, freshness); nothing is removed from them.

### Each move, on its day

1. **The page has been in `shadow` on live since Tue 20.** Its code was merged on Mon 19,
   with the page off.
2. **The release data report on a restored copy** taken that morning
   (`scripts/release_data_report.py`, as in [MEDALLION_RELEASE_REPORT.md](MEDALLION_RELEASE_REPORT.md)):
   a snapshot before and after the build shows that only the certified tables changed. The new
   `figures` command then takes the page's figures the old way and the certified way on the
   same copy, for every account and portfolio, for the latest date, every month-end and a
   fixed sample of other dates. Ratios and numbers must match within the report's relative
   1e-9, money to the cent, dates and text exactly.
3. **Every difference is fixed or explained, and Ying approves.** The full report, with
   figures, stays private, as release reports do; the PR gets counts only. Expected differences
   are written down before the run (for example, a figure the page used to leave blank that the
   build can now fill).
4. **The live shadow log shows no unexplained difference** on any day since Tue 20.
5. **Switch the page on:** one setting, one website revision, through the **Website certified
   pages** workflow (`.github/workflows/website-certified-pages.yml`, stages `shadow`,
   `batch-1` to `batch-3` and `off`). It refuses unless the live website was released from a
   commit whose readers can find the register and 073 is recorded as applied. It also refuses
   between 07:50 and 09:25 UTC and, from 1 to 7 Nov, any move other than back. If the checks
   fail it puts the previous value back.
6. **Check** that afternoon and after the next morning's build that the page, its API and (from
   Sat 24) the read tool show the same figures.

The live shadow is short: three days of builds before batch 1, four before batch 2 and five
before batch 3 (the weekend ones without new IBKR data), against the five business days a
calmer timetable would use. The full-history comparison on a
restored copy is the stronger evidence, and it is the one that must be clean.

### What makes a page miss its day, and wait until 8 Nov

A page that is not ready on its day moves on a spare day (Sun 25 to Thu 29) once it is. It
waits until 8 Nov if, by the end of Thu 29:

- the release data report or the `figures` command still shows a difference that is not fixed,
  or not explained and approved;
- the live shadow log shows a difference nobody can explain;
- a certified build was held or failed in the last two days without a known, fixed cause;
- the backfill does not cover every date the page lets you pick;
- a step it needs has not had its approved run: the migration and the build job for every page;
  the agent schema install for the read tool;
- making its figures match would need a change that alters live figures, such as flows from
  silver, the FX duplicate fix or a guide correction; those are after month-end by design;
- the page it borrows figures from has not moved: Performance needs Overview, and Combined
  holdings needs Holdings;
- Ying cannot review its report before Thu 29.

A page that is switched on and then undone between Fri 30 and 7 Nov stays off until 8 Nov.
The read tool follows its pages: it serves only figures whose page is on.

### Undoing

- **A page:** set it back to `shadow` or `off`. The page calculates as it does today; nothing
  is deleted. The old calculation stays in the code until every page has moved and two
  month-ends have passed cleanly.
- **A bad build:** mark it `rolled_back`, and every page and agent falls back to the previous
  published figures at once. Nothing is rewritten.
- **The agents:** `AGENT_CERTIFIED_FIGURES=off` hides the new tools.
- **The whole layer:** every page `off`, then `CERTIFIED_FIGURES_BUILD=off` on the build job.
  The tables stay, unread.

## Security

- **Sensitivity marks.** The contract for `gold.certified_figures` marks `subject_id`
  `restricted` (it can hold an IBKR account number); values, dates, periods, items and the
  detail `confidential-financial`; figure ids, versions, build and run ids `internal`, as the
  other gold contracts do. Items are holding ids, never names.
- **Tenants.** Both tables have `tenant_id` with forced row-level security and the
  `tenant_isolation` policy, as every gold table; the view is `security_invoker`, so the
  caller's tenant applies.
- **Clients see only their own accounts.** On the website the reader runs only after the
  existing account check (`_portfolio_access_context`) has passed, for one account; the
  accounts list and Combined holdings loop over the person's own accounts with the same check,
  as today. For agents, a new `agent.scoped_certified_figures()` function filters on
  `agent.tenant()` and `agent.scope()` like the other agent views, and the tool server puts
  exactly one account in scope per call. People's access still comes from
  `agent.allowed_subjects` and jobs' from `agent.service_grants`.
- **Traces stay coarse for agents.** `trace_figure` gives job names, times, code versions,
  row counts and file sha256, not storage paths, which can contain account numbers.
- **Reports stay private.** The register and this design hold no client data. The release
  report and the page comparison keep figures in the private full report and counts in the
  public PR summary.
- **Narrower logins later.** With S4b's rights by layer, the website and agent logins need
  only the certified view, not the tables.

## The build plan

### Safe to start before approval

Each part is code or a file only, merges with everything off, and changes nothing until an
approved run or a setting turns it on. If Ying changes an answer, only these parts are redone:
questions 1 and 2 shape parts 2 and 3, so they are the ones to answer first.

| # | Part | Why it is inert | What turns it on |
|---|---|---|---|
| 1 | The register in code (`ibkr_etl/certified_figures.py`) with definition versions; this branch's test moves to it | Nothing reads it | Parts 3 to 7 |
| 2 | The migration: `gold.certified_builds`, `gold.certified_figures`, the view, row-level security, the lineage trigger; their contracts and catalog entries; `trace()` learns the table | Production applies migrations only by their own approved step | Its approved run (`--only-migration`) |
| 3 | The build (`scripts/build_certified_figures.py`): figures, checks, publishing, `--backfill`; tested on a throwaway database with synthetic data | `CERTIFIED_FIGURES_BUILD` is off unless set | The job, with the setting `on` |
| 4 | The job definition in Bicep, a copy of the crisis job's with two schedules | Not deployed | Its deployment |
| 5 | The website readers for the seven pages, the shadow log line and the "Certified · as of" line | `CERTIFIED_FIGURES_PAGES` is empty | The setting, page by page |
| 6 | The release data report's `figures` command and its view comparison | A script people run by hand | Nothing |
| 7 | The agent view (in the install script's files), the three tools, `[F#]` citations and the warn-only check | The script is not run; `AGENT_CERTIFIED_FIGURES` is off | An approved run; the setting |

The build leaves `reporting_snapshot.py`, `ibkr_etl/agent_tools.py`'s existing tools, the
factsheet job and the monthly reports untouched, so it cannot meet the factsheet move on Wed 21.

### After approval: about one day

| When | Step | Approved run or setting | Undo |
|---|---|---|---|
| Mon 19 | Merge parts 1 to 7 | None: everything is off | Revert |
| Mon 19 | Apply the migration | Its approved step | Reverse migration (the tables are empty) |
| Mon 19 to Tue 20 | On a restored copy: run the build (time-limited), the release data report and the `figures` command for all seven pages, which counts dates the build has not reached yet apart from differences; fix or explain each difference; Ying reviews the private report | None on live | Delete the copy |
| Mon 19 to Tue 20 | Deploy the job (`CERTIFIED_FIGURES_BUILD=on`, `LINEAGE_RECORDING=true`) and run it once on live; the scheduled runs fill in the rest of the history | The deployment and one run | Setting off; rows stay unread |
| Tue 20 | First scheduled builds; website release with all seven pages in `shadow`; install the agent view with the tools still off | Website setting; one approved run of the install script | Setting back; the view is unused |
| Thu 22 | Batch 1 | `overview=on,holdings=on,cash=on` | Back to `shadow` |
| Fri 23 | Batch 2 | `performance=on` | Back to `shadow` |
| Sat 24 | Batch 3 | `risk-snapshot=on,risk-statistics=on,combined-holdings=on`; `AGENT_CERTIFIED_FIGURES=on` | Back to `shadow`; tools off |

One day is tight but possible because parts 1 to 7 are built before approval and the
rehearsal tooling for restored copies exists since 17 Oct. If the Mon 19 rehearsal finds a
difference that takes longer, batch 1 uses a spare day, not Wed 21.

### After month-end (from 8 Nov), each its own release with a report

1. The publish gate's verdict becomes binding for the build's sources.
2. Monthly reports and Attribution read certified figures (brought forward to 27 Oct in the
   automatic fallback: "Monthly reports and Attribution on certified figures"); the factsheet stays on the frozen
   snapshot, and the October and November month-end figures are checked against it (open
   question 13).
3. The month-end commentary job gets the read tool; the existing agent figure tools read
   certified figures; the `[F#]` check is enforced.
4. The charts, tables and indicators: daily return series, rolling series, drawdown episodes,
   holdings correlation, RSI.
5. Flows from silver (S6); then the agent reader loses `agent.flex_rows` and
   `agent.benchmark_prices`.
6. The FX duplicate fix (U8).
7. Remove the old page calculations after two clean month-ends (November and December).

### What it needs from the other upgrades

- **Publish gate (U2).** Nothing before 8 Nov: the build publishes on its own checks. From then,
  the verdict of `publish_gate.check()` for the jobs whose tables it reads, which the build
  stores with each build and, once binding, holds on.
- **Contracts (U1).** Contracts for the two new tables, with the rules above.
- **Lineage (U4).** `LINEAGE_RECORDING=true` on the build job (and, for full traces, on the
  jobs it reads); `trace()` learns the new table.
- **Input readiness (U3).** The build checks its own inputs before each run; the shared
  readiness rules can replace that check after month-end.
- **Release data report (U6).** The `figures` command and the view comparison (part 6).
- **"As known on" history (U8).** Nothing before month-end. Certified rows already keep every
  published version of a figure.

## Monthly reports and Attribution on certified figures (from Tue 27 Oct)

Item 2 of "After month-end", brought forward by the owner's decision of 18 Oct. The October
client reports (made 1 to 7 Nov) are made with it switched on, so its first mode, the
automatic fallback, keeps every report exactly as today's code makes it.

### The setting: `MONTHLY_REPORTS_CERTIFIED`

Read by `reporting_snapshot.build_month_end_payload`, which every month-end freeze uses
(`freeze_month_end_snapshot`: the month-end factsheet job's `generate_month_end_package.py`,
`render_onboarding_composite_reports.py` through `composite_reports` and `designed_reports`,
`historical_reporting`, `generate_month_end_factsheet.py` and `scripts/freeze_month_end_snapshot.py`).
The code is `ibkr_etl.certified_reports`.

| Mode | What a month-end report does |
|---|---|
| `off` (the default: unset, empty or any other value) | Today's code exactly. Nothing is read and nothing is logged. |
| `fallback` | Today's report, byte for byte (the same snapshot sha256, so the same snapshot id and factsheet). The certified figures for the report's data date (`data_as_of`, its last NAV date on or before the month end) are read alongside, inside a savepoint on the report's own connection, and every field below is compared. A certified value is used only when it is exactly today's, so nothing changes; a value within the certified tolerance (relative 1e-9, money to the cent) counts as close and today's is kept, as for a different or missing one. One line per report, `CERTIFIED_REPORTS {json}`, gives the mode, the subject masked (`u…01`), the status (`match`, `differ`, `none`, `error`, `not_applicable`) and the counts and figure ids that were close, different or missing: never values. A failed read is logged as `error` and the report is today's. |
| `on` | The certified value replaces today's in every field the mapping writes, wherever one exists. Not used before two clean month-ends (November and December). |

The Alpha World MS and T reports and the profiled design composites are made from their own
approved HKD design, not from the website's figures, so they are not compared
(`not_applicable`).

### Which report fields have a certified version

All are read for `as_of_date = data_as_of`. "Compared only" fields are never written, not even by
`on`, because the report works them out on another basis.

| Report field (snapshot payload) | Certified figure | Period, item, benchmark |
|---|---|---|
| `portfolio_summary.latest_nav.total_text` | `overview.nav` | |
| `portfolio_summary.positions` | `overview.positions` | |
| `risk.metrics.<name>` (15 fields, as on Risk › Risk snapshot) | `risk.*` (`cvar_95_daily` and `daily_expected_tail_loss` are both `risk.etl_95_daily`) | |
| `benchmark_comparisons[].metrics.<name>` (also `performance_comparison`, `secondary_performance_comparison`: the same objects) | `benchmark.portfolio_total_return`, `benchmark_total_return`, `excess_total_return`, `tracking_error`, `information_ratio`, `beta`, `correlation`, `relative_win_rate` | benchmark: that comparison's. The build certifies each subject's default benchmark, so a secondary benchmark's are missing |
| `monthly_reporting.portfolio_monthly_return`, `.equity_contribution`, `.residual_contribution` | `attribution.portfolio_return`, `attribution.holdings_contribution`, `attribution.residual_contribution` | `month_YYYY-MM` of the report month |
| `monthly_reporting.security_contributions[].contribution` | `attribution.holding_contribution` | the month; item: conid |
| `monthly_reporting.sector_contributions[].contribution` | `attribution.sector_contribution` | the month; item: sector |
| Compared only: `month_end_performance[].portfolio_monthly_return` | `performance.monthly_return` | `month_YYYY-MM`. The report's is the ratio of the benchmark comparison's month-end levels (dates shared with the benchmark); the certified one is the time-weighted return over every NAV date |
| Compared only: `sector_allocation.gross_market_value`, `.allocation[].weight` | `holdings.gross_market_value`, `holdings.sector_allocation` (item: sector) | The report converts with its own dated reporting FX |

No certified version (the report keeps today's): `month_end_performance[].benchmark_monthly_return`
and the comparison series themselves (only their last point is certified, as
`benchmark.rebased_level`, a chart figure); `performance.series` (the NAV records); the holding
records (`holdings`, from the agents' holdings tool with the report's own reporting-currency
step); `monthly_reporting.cash_activity` (dividends, interest and fees: cash records);
`freshness`; the crisis replay detail; and the composites' attribution (below). MTD, QTD, YTD and
the other period returns are not in the snapshot: the factsheet works them out from its own NAV
index when it renders, and stays on the frozen snapshot (open question 13).

### Attribution as certified figures

Five figures on their own page key, `attribution` (batch 4, Tue 27 Oct; the website workflow's
`batch-4` stage), all from the engine the reports use: `attribution.portfolio_return`,
`attribution.holdings_contribution` and `attribution.residual_contribution` per month, and
`attribution.holding_contribution` and `attribution.sector_contribution` per month and holding
or sector (the kind `per_period_item`). The build (`certified_build.attribution_rows`) calls the
page's own handler, so certified equals what the page shows. Because the page works a month out
to its calendar end whatever the as-of date, a month is read only on its last NAV date, and the
month in progress on the newest date; other dates give no rows and cost nothing. Its input
fingerprint is every row the handler read, in order. The page reads its month's figures only
(`period`), and never shows figures from an earlier date (`exact`): it keeps today's answer and
logs `behind`. The composite portfolios keep today's figures: their engine adds the sleeves in
set order, which changes from one run to the next, so totals can move in the last digit with the
same inputs, which would hold the build.

### Before switch-on: the rehearsal on the restored copy (Sat 24 / Sun 25)

1. Fill the past month-ends: `CERTIFIED_FIGURES_BUILD=on python scripts/build_certified_figures.py
   --backfill --page attribution` (the daily runs read only the latest dates). It must publish, not hold.
2. The release data report's `figures` command, which now reads the Attribution page on
   month-ends and the newest date: no differences for `attribution`.
3. `DATABASE_URL=<the copy> python scripts/compare_monthly_report_figures.py --month 2026-09 --out
   <private file outside the repository>`. Read-only: the session is `READ ONLY`, every subject is
   rolled back, no snapshot is inserted, nothing is uploaded or rendered. It makes each subject's
   September report with the setting `off`, `fallback` and `on`, prints counts only and exits 0
   when every report today's code can make is identical with `off` and `fallback`. It must show
   **"Identical with MONTHLY_REPORTS_CERTIFIED off and fallback: N of N"** and **"failed only
   with the setting: 0"**. The equal, close, different and missing counts per field say what `on`
   would change; they do not change any report in `fallback`, and each difference in a written
   field is explained before `on` is considered. "Today's payload equals the stored frozen
   snapshot" shows whether today's code still makes September's frozen snapshot from the copy.

### Switch-on and undo

Tue 27 Oct: `MONTHLY_REPORTS_CERTIFIED=fallback` on the month-end factsheet job
(`ibkrv2stg-month-end-factsheet`) and on any other process that freezes snapshots, and the
website workflow's `batch-4` stage (or `attribution=shadow` first). From 1 Nov, watch the
`CERTIFIED_REPORTS` lines. Undo: remove the setting (off), and the workflow's `batch-3` stage for
the page.


## Open questions for Ying

| # | Question | Recommendation |
|---|---|---|
| 1 | One shared figures table, or one table per figure as S7 first said? | **Shared** |
| 2 | Certify every past date a page lets you pick, or only the latest day and month-ends? | **Every date** (the data is small) |
| 3 | Before 8 Nov, let the build publish on its own checks, with the publish gate's verdict recorded but not binding? | **Yes** |
| 4 | Where one guide entry is worked out two ways (volatility, maximum drawdown), keep both as separate figures with the basis in the label? | **Both**, and fix the guide's wording after month-end; merging would change figures people have seen |
| 5 | Keep today's flows reader (bronze) for the October moves, so figures stay the same? | **Yes**; silver after month-end |
| 6 | Leave the charts, tables and RSI as today until after month-end? | **Yes** |
| 7 | Leave Modelling uncertified, labelled hypothetical? | **Yes** |
| 8 | Last day a page may move: Thu 29 or Sat 31? | **Thu 29** |
| 9 | During 1 to 7 Nov, may a page be switched back to off if its figures go wrong? | **Yes** (it restores today's path) |
| 10 | Run the build twice a day (after the derived step and after the crisis step)? | **Yes** |
| 11 | Combined holdings: add up certified account records when read, rather than store sums per viewer? | **When read** |
| 12 | Add the read tool beside today's agent tools on Sat 24, leaving the month-end commentary job unchanged until 8 Nov? | **Yes** |
| 13 | Keep the factsheet on the frozen snapshot, checked against certified figures? | **Yes** |
| 14 | When today's figures are held back, show the last good ones with a note? | **Yes** |
| 15 | Show "Certified · as of" on moved pages? | **Yes** |
| 16 | Start building the inert parts before approval? | **Yes** |

## The proof in this branch

Nothing is built and nothing is switched on. Two files make the list concrete, and no code
reads either:

- `infra/medallion/certified-figures.json`: the draft register of the 71 figures, the seven
  pages and their three batches with dates, the entries left for later, the pages that wait
  until after month-end and the pages out of scope, each with a reason.
- `tests/test_certified_figures_register.py`: checks that every figure names a guide entry the
  guide places on its page (two known gaps are recorded: open question 4); that every entry in
  the covered guide sections is certified or left for later with a reason; that every factsheet
  value naming a guide entry is covered with the same unit; that every source is a silver or
  gold table in the catalog with a contract; that the payload fields named for the risk
  snapshot and the benchmark figures are produced by today's code; that each page's routes and
  APIs exist in `web.py`, and every website page is moving, waiting or out of scope; that a
  page shows another page's figures only once that page has moved; and that the batch dates
  avoid Wed 21 and fall between the shadow start and Thu 29, before the freeze.
