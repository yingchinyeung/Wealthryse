# WealthRyse Portfolio Data Platform — Owner User Manual

**Version:** 29 September 2026

**Owner:** `anthony.ying@wealthryse.ai`

**Scope:** the deployed private multi-account website, self-service onboarding, derived portfolios, and the supporting Azure data/reporting jobs.
**Website:** <https://ibkrv2stg-web.purplerock-8cff24a2.eastasia.azurecontainerapps.io>

> This is an operating manual for the system as it exists on the version date. The name `ibkrv2stg` is a legacy resource name; the website is being used as a private internal-production pilot. The permanent `portfolio.wealthryse.ai` address has **not** been configured. Azure settings, provider terms, and available data can change; verify them before a material decision. Never put a Flex token, database password, Key Vault secret, or raw client statement into an AI question or support screenshot.

**Contents:** [What you own](#1-what-you-own-today) · [First login](#2-first-login-and-the-fastest-useful-workflow) · [Every website function](#3-website-function-by-function-guide) · [Automatic schedule](#4-what-runs-automatically-and-when) · [Owner routine](#5-your-routine-as-owner) · [Azure inspection](#6-how-to-inspect-azure-safely) · [Access and security](#7-manage-access-and-security) · [Troubleshooting](#8-troubleshooting-decision-guide) · [Recovery/change control](#9-backups-recovery-and-change-control) · [Data rules](#10-financial-data-and-publication-rules) · [Limitations](#11-current-limitations-you-should-plan-around) · [Scaling](#12-scale-up-safely-add-accounts-colleagues-and-capacity) · [Glossary](#13-handy-glossary)

## 1. What you own today

The current website is an Entra-signed-in, read-only multi-account portfolio workspace. **U16088571-U.S. Technology and Biotechnology** and **U16088606-Greater China** are the physical-account baselines. The platform provides account overview, benchmark comparisons, risk analytics, cash activity, trade history, holdings, monthly PDF/PPTX reports, CSV/JSON exports, an interactive AI analyst, platform-wide System Health, owner-only Administration, and an owner-controlled self-service onboarding wizard. The website is **not** a brokerage trading interface.

New physical accounts can be discovered from a governed IBKR Flex connection, ordered, imported, reconciled, enriched, historically rendered, privately previewed and activated through the website. Filtered derived portfolios and fixed-unit composites use typed, versioned calculation contracts rather than executable user code. The approved sequence is **China & Hong Kong → Alpha World MS → Alpha World T → master-account test → remaining v1.1 accounts**. Master accounts are currently an administrative directory/membership test and are not published as consolidated investment portfolios until their anti-double-counting acceptance tests pass.

### Approved derived-portfolio contracts

- **China & Hong Kong** starts on 20 January 2025 from U16088606. It excludes a position when an approved country, listing-exchange or currency signal identifies Taiwan. It removes all source-account cash and reconstructs an auditable HKD/USD ledger from HKD 484,300 and USD 0 before activity on the start date. Eligible HKD/USD dividends, fees, commissions, taxes, trades, transfers and actual posted interest are included; TWD and events linked to excluded Taiwan holdings are excluded; no assumed interest is added.
- **Alpha World MS** is `58 + 0.91 × N(China & Hong Kong) + 0.45 × N(U16088571) − 0.49 × N(Hang Seng price index) − 0.45 × N(SPY unadjusted official close)`.
- **Alpha World T** uses the same formula, except the first sleeve is the complete U16088606 account instead of China & Hong Kong.
- In both Alpha portfolios, `N(...)` means a sleeve converted into USD and normalized to 100 on 20 January 2025. The constant 58 is non-interest-bearing cash. Units remain fixed; there is no daily rebalancing. The inception risky exposure is 136 long, 94 short, 230 gross and 42 net, plus 58 cash, producing an initial NAV of 100.

The Account onboarding page displays these terms and their SHA-256 before a case can start. The daily worker independently proves that the approved database JSON, its stored hash and the deployed reference definition are identical before it calculates any result. A mismatch stops the job instead of silently applying a different formula.

### Derived Portfolio Studio

The Account onboarding page also contains a no-code **Derived Portfolio Studio**. It can create new portfolios modelled on the two approved derived structures without Codex:

1. Choose **Filtered account with reconstructed cash** to select one active physical account, country/exchange/currency exclusions, allowed cash currencies, opening balances and actual-interest treatment.
2. Choose **Fixed-unit normalized composite** to add active physical accounts, active derived portfolios and approved market components. Enter a positive coefficient for a long sleeve and a negative coefficient for a short sleeve, then review the live inception NAV, long, short, gross, net and cash preview.
3. Click **Validate and create private case**. This creates a private definition-bound onboarding case only; it does not calculate, publish or activate the portfolio.
4. Complete the normal nine wizard pages. The owner must approve the exact definition fingerprint, external-data scope, historical reports, private preview and final activation.

The Studio accepts structured fields only. It cannot execute Python, SQL, JavaScript or a pasted formula. Dependencies must already be active, account/portfolio sleeves must share the selected normalization date, required market and FX history must exist, and a portfolio cannot contain itself. The scheduled derived worker rebuilds every stored definition through the same typed validators before calculation. Any unknown field, changed hash, currency drift, stale benchmark beyond the approved carry window or incomplete history fails closed.

| Role | Current access |
|---|---|
| Owner — `anthony.ying@wealthryse.ai` | All portfolio pages, System Health, monthly downloads, exports, AI Analyst, and Administration. |
| Viewer — `david.lee@wealthryse.ai`, `anthony.choi@wealthryse.ai`, `natalie.ho@wealthryse.ai`, `chloe.yoo@wealthryse.ai` | `U16088571-U.S. Technology and Biotechnology`, `U16088606-Greater China`, and `China & Hong Kong`: portfolio pages, System Health, monthly downloads, exports, and AI Analyst. No Administration or approval actions. |

The app checks the signed-in Microsoft Entra identity, the active institutional role and an explicit server-side entitlement for each account. Having an Azure subscription role—or Viewer access to one account—does not grant access to another account. A colleague's first real sign-in should still be tested before treating onboarding as complete.

## 2. First login and the fastest useful workflow

1. Open the website URL above in a browser. Sign in with **`anthony.ying@wealthryse.ai`** and complete your organization's Microsoft authentication and MFA prompts.
2. Confirm that the top-right identity says your email and **Owner**. If you see “not authorized,” do **not** create another Azure resource or change a password; use the access checklist in Section 10.
3. On **Accounts**, select the account or portfolio you need. The company logo at the top left is a home button that returns to Accounts from any page. The top navigation also provides **Accounts**, **System health**, **Account onboarding**, **IBKR connections**, and (for you) **Administration**. **Sign out** ends the web session.
4. In the account workspace, use the **As of** dropdown near the top. It contains available account NAV dates; selecting a date refreshes the account view. Check the *effective* data date displayed inside a page: some holdings, cash, benchmark, Treasury, or risk observations can be older than your selected date because of weekends, market holidays, or missing validated data.
5. Start with **Overview** for the account's NAV, observation count, position count, and reporting currency. Continue to **Performance**, **Risk analytics**, and **Holdings**. Check **System health** before using a result in a shareholder discussion.
6. For a file, use **Data exports** or **Monthly reports**. The browser normally saves downloads to its Downloads folder; the website does not write them directly to your OneDrive.

**Example:** To compare U16088571 with the NASDAQ Composite at a historical date, open **Performance**, choose the historical **As of** date, then choose **Secondary — NASDAQ Composite (`^IXIC`)** in **Comparison benchmark**. Read the displayed benchmark metadata and hover over the curves for exact point values. The default benchmark is the **S&P 500 Price Index (`^GSPC`)**. Both are **price indexes**, not total-return indexes or ETFs.

## 3. Website function-by-function guide

### 3.1 Accounts home and navigation

- **Company logo / Accounts:** returns to the account directory. Click **Open account** on U16088571 to enter its workspace.
- **Account subpage tabs:** **Overview**, **Performance**, **Risk analytics**, **Cash**, **Trade history**, **Holdings**, **Monthly reports**, **Data exports**, and **AI analyst**. These are separate account-specific pages, not one long screen.
- **System health:** global platform page, not a U16088571 subpage. All signed-in roles can view it.
- **Administration:** global platform page, visible only to Owner. A guessed URL is still server-side denied to a Viewer.
- **Sign out:** use when finished, especially on a shared computer. The Entra web session has an eight-hour configured lifetime; an expired session requires signing in again.

### 3.2 As-of date — applies across the account workspace

1. Open any U16088571 subpage and choose a date in **As of**.
2. Read the page's own date label, source note, and quality message. The selected date is a cutoff; it is not a promise that every market has an observation on that exact day.
3. **Reselect the date after opening another account subpage.** The current page load defaults the selector to the latest date; it does not preserve your earlier selection across navigation.
4. For a past month-end report, do **not** assume changing As of changes the report download. Monthly reports are separately frozen by reporting period (Section 3.9).

The dropdown is based on available U16088571 NAV snapshots from 20 January 2025 onward. Dates after the latest validated NAV do not appear. If the newest date is stale, investigate the pipeline instead of typing a future date elsewhere.

### 3.3 Overview

The four cards show:

- **NAV:** latest account total NAV on or before the selected date, displayed in USD.
- **Observations:** count of available total-NAV observations through the selected date, not a trade count.
- **Positions:** count of holdings in the selected/effective position snapshot.
- **Reporting currency:** USD for this pilot.

Use the NAV as a quick check, then verify its date against **System health** and the IBKR source before an external decision. An empty position snapshot may be a data gap, not necessarily a liquidated portfolio.

### 3.4 Performance

1. Select the account **As of** date.
2. In **Comparison benchmark**, select **Primary — S&P 500 Price Index (`^GSPC`)** or **Secondary — NASDAQ Composite Price Index (`^IXIC`)**.
3. Read the benchmark description and metadata: provider, price-return convention, license/quality status, and latest observation date. The current benchmark feed is Yahoo Finance through `yfinance` and is provisional for internal/shareholder analysis.
4. Read the comparison metrics shown above the charts. The engine aligns portfolio and benchmark on common valid dates, rebases both to 100 on the first common date on or after 20 January 2025, and does not use a future observation.
5. In **Rebased performance comparison**, the horizontal axis is date and the vertical axis is a rebased index level. Move your pointer across the chart to see the exact date and both curve values.
6. In **NAV history**, the horizontal axis is date and the vertical axis is USD NAV. Hover for the exact date and NAV.

A price index omits reinvested dividends; it is not the S&P 500 Total Return Index. A difference between portfolio and index performance does not by itself establish investment skill; cash flows, fees, currency and benchmark suitability matter.

### 3.5 Risk analytics

1. Select **As of** and read **Risk analytics through** to confirm the calculation date.
2. Read the metrics cards. The page currently includes total return, annualized return, annualized volatility, risk-free rate, Sharpe ratio, expected/actual/tail one-year maximum drawdown, crisis maximum drawdown, daily 95% VaR, daily 95% Expected Tail Loss, win rate, and BLUE mean daily return.
3. Read the **Top 5** and **Bottom 5 Expected Tail Loss contributors** tables. Positive contribution adds to estimated tail loss; negative contribution offsets it. The displayed residual represents cash, fees, FX, trades, and positions that cannot be matched under the attribution rules.
4. Read the notes beneath the tables. They disclose return-observation counts, the risk-free source/date, simulation conventions, and whether crisis replay used direct historical prices or proxies.

Interpretation is important:

- **Risk-free rate:** latest validated U.S. Treasury 3-month par-yield observation **on or before** As of. It is an annualized reference yield, not interest credited to the account. It can legitimately lag on holidays.
- **Sharpe ratio:** annualized risk-adjusted return based on cash-flow-adjusted daily portfolio returns and the as-of-correct Treasury rate; unavailable if the validated inputs are insufficient.
- **Daily VaR 95% / Expected Tail Loss 95%:** historical daily loss measures. Expected Tail Loss is the average loss in the worst 5% of validated daily returns, expressed as a positive loss measure. Neither is a maximum possible loss.
- **Expected and Tail Maximum Drawdown (1 Year):** scenario statistics from 5,000 deterministic 252-session simulations using five-session moving blocks. **Actual Maximum Drawdown (1 Year)** is the observed peak-to-trough decline over up to the latest 252 validated returns.
- **Crisis Maximum Drawdown (15 September 2008–9 March 2009):** a **hypothetical** fixed-unit replay of the holdings at the selected As of date. It uses direct adjusted-close history when complete and recorded sector-ETF/SPY proxies otherwise; residual cash stays flat, with no rebalancing. It is **not** U16088571's realized 2008–2009 return. If required proxy data do not validate, the metric is unavailable rather than guessed.
- **BLUE mean daily return:** the engine's estimated mean of validated daily returns; do not interpret it as a forecast or a guaranteed return.

If a card says **Unavailable**, check the displayed note and System Health. Do not manually insert a zero or copy an earlier value into a report.

### 3.6 Cash

The top cards show cash balances by currency at the latest cash snapshot on or before As of, including **ending cash** and **settled cash**. The activity table is a separate date-range view of IBKR cash events such as deposits, withdrawals, dividends, interest, fees and taxes where present in the imported data.

1. Set **From** and **To** (inclusive; earliest website date is 20 January 2025). The default is the full history through the selected As of date.
2. Click **Apply cash filters**. Read the count and the displayed date range. The table shows every matching activity, with date, time, type, description, currency and amount.
3. Click **Download cash activity CSV** to export the **same complete filtered range**. Open it in a spreadsheet using the CSV import dialogue if dates or symbols are reformatted unexpectedly.

The activity range can differ from the balance snapshot date. If **From** exceeds **To**, correct the dates and apply again.

### 3.7 Trade history

This is a **read-only history of executed IBKR trades**, not an order-entry screen.

1. Set inclusive **From** and **To** dates.
2. Optionally enter **Symbol contains** and choose **All sides**, **Buy**, or **Sell**.
3. Click **Apply trade filters**. Read the count above the table. The screen shows up to the **latest 500** matching rows; if truncated, the note says so.
4. Review date/time, instrument, side, quantity, trade price, currency, proceeds, commission/currency, net cash and realized P&L. Sign conventions follow the imported execution data; reconcile an unusual sign with the original IBKR statement.
5. Click **Download trade history CSV** for the **complete** filtered result, including rows not shown on screen.

**Example:** For all U16088571 sales in March 2026, choose `2026-03-01` and `2026-03-31`, choose **Sell**, click **Apply trade filters**, then download the CSV. The website account is already fixed by the workspace; do not assume this export includes other accounts.

### 3.8 Holdings

Select As of, then check the **Holdings as of** label. The table shows symbol, description, trading currency, quantity and market value for the latest validated position snapshot on or before your cutoff. A security's trading currency can differ from the account's USD reporting currency. This is a **snapshot**, not an intraday quote, tax-lot report or trade instruction. To investigate a historical holding change, compare snapshots at two As of dates and use Trade history and Cash for the intervening activity.

### 3.9 Monthly reports

The report centre holds frozen month-end PDF factsheets and editable PowerPoint decks for active reporting subjects. U16088571 uses the approved August 2026 factsheet layout and v24 presentation layout; other accounts use their approved factsheet design and the governed platform presentation layout. New packages are evaluated daily at 17:00 HKT after the prior month ends. Generation waits for a successful post-month Flex import or validated derived calculation and keeps the PDF/PPTX pair awaiting owner approval.

Monthly reporting commentary for every onboarded account may use the approved regional Foundry deployment. Only selected structured NAV, performance, holding-symbol/value, benchmark and risk fields cross the model boundary; raw IBKR files, Flex and database credentials, and personal information remain excluded. Numerical factsheet and slide-deck figures continue to come from the deterministic reporting engine. If Foundry is unavailable, generation fails closed rather than silently publishing substitute commentary.

1. Open **Monthly reports** and choose a period in the month selector.
2. Interpret the status:
   - **Ready:** both files are internally released and downloadable after integrity verification.
   - **Partially released:** one file is downloadable by authorized users; the other still needs owner approval or was revoked.
   - **Ready for review:** both files passed generation checks and can be downloaded for owner review; **not** yet internally released.
   - **Generation pending:** a frozen snapshot may exist, but the complete PDF/PPTX pair is not registered; the download button remains disabled.
3. For Ready or Ready for review, click **Generate secure downloads**. This checks account, period, release/review scope, blob path, MIME type, file size and SHA-256 integrity. It does **not** re-run report generation.
4. Sign in as the owner and select the month. The **Review and release files** checklist appears automatically. Download and inspect each file before ticking it: verify the as-of date, displayed figures, benchmark variant, holdings, legal footer, slide count and visual layout against the frozen snapshot. The two checkboxes show the **current** release state, not a new approval request.
5. Tick only the files you approve for internal/shareholder access. Untick any previously approved file whose access you want to revoke. Click **Submit approval changes**. There is no separate phrase to type. The server rechecks the exact snapshot, file digests and current approval state before changing access and recording the owner and audit event. If another action changed the state while the page was open, refresh the checklist and decide again.
6. A checked file becomes downloadable by authorized viewers; an unchecked file's existing download link stops working. The original document and prior approval/revocation history remain preserved. You can reverse a revocation by reviewing, ticking and submitting again. A new approval is blocked if the underlying financial-quality or governance checks fail, but revocation remains available. Client distribution remains prohibited.
7. If a file is wrong, leave it unticked or revoke it. Record the period, page/slide, metric, expected source and screenshot. A corrected file needs controlled regeneration and a fresh review. The old pilot-specific release job should not be used for new months.

### 3.10 Data exports

1. Choose the account As of date first.
2. Open **Data exports** and select:

   | Button | What it gives you |
   |---|---|
   | **Performance CSV** | U16088571 NAV history from 20 January 2025 through the selected As of cutoff. |
   | **Holdings CSV** | The latest account holdings snapshot on or before As of. |
   | **Cash activity CSV** | Account cash activity from the start of website history through As of. For a custom range, use the Cash page instead. |
   | **Trade history controls** | Opens Trade history so you can filter and download its complete CSV. |
   | **Frozen snapshot JSON** | The latest frozen month-end reporting payload, not necessarily the month selected in Monthly reports and not necessarily the current As of date. |

3. Check the downloaded filename, period, units and source date before combining exports. CSV/JSON downloads contain company financial data; store them in an access-controlled location and delete temporary local copies under your retention policy.

The website does not offer arbitrary SQL or direct PostgreSQL credentials. The older SQLite export/DB Browser workflow is a separate route to the underlying legacy database; it is not this website's live query interface.

### 3.11 AI analyst

The website AI analyst answers portfolio questions using fixed, account-scoped, read-only tools. Exact facts, metrics and chart series come from deterministic code; GPT-5.6 Sol drafts the explanation. Approved structured U16088571 NAV, performance, holdings, benchmark, risk, freshness and sector results may be processed in the approved Southeast Asia/APAC Data Zone. Raw Flex files, credentials, database passwords and personal information are excluded.

1. Open **AI analyst** and check that the readiness message is present. Select an **As of** date. The current AI request uses the account's default **primary S&P 500 Price Index** benchmark; the Performance page's secondary-benchmark choice is **not preserved** when you navigate to AI analyst. For a reliable NASDAQ Composite comparison, use the Performance page until a visible AI benchmark selector is implemented.
2. Choose one of the five analysis types: **Portfolio overview**, **Performance and benchmark review**, **Holdings review**, **Risk review**, or **Interactive portfolio question**.
3. For the interactive type, enter a specific question (maximum 500 characters), then click **Answer with validated data**. Examples:
   - “Compare U16088571 with the S&P 500 Price Index through 31 August 2026.”
   - “Show the largest holdings and explain the sector concentration at this As of date.”
   - “Plot NAV and the selected benchmark, and explain the worst drawdown period.”
4. Read the answer's **deterministic facts**, generated charts, **citations**, **caveats**, as-of date, and correlation ID. If a figure disagrees with Performance or Risk analytics, trust neither until reconciled; record the correlation ID and ask for investigation.

The AI cannot trade, run arbitrary SQL or shell commands, read secrets, change permissions or formulas, or grant itself more authority. It can explain validated information; it is not a substitute for your final investment/compliance judgment. A failed data or model gate makes the answer unavailable rather than fabricated.

### 3.12 System health — platform-wide

Use the **System health** top-level page daily, especially after the scheduled jobs. It shows environment/status, count of tracked NAV subjects, latest portfolio date, latest ingestion and internal release; benchmark coverage and quality; the latest portfolio-analyst assurance result; and the 14-day acceptance burn-in. The benchmark and NAV coverage is platform-wide, but the **currently displayed assurance and burn-in subject remains U16088571**. Do not infer that they attest future added accounts until those jobs are extended.

If the page says a check failed or a date is stale, note the exact field and its timestamp. A green page is useful evidence, **not** proof that every number is correct. Reconcile material figures to IBKR and the frozen reporting snapshot.

### 3.13 Administration — owner only

The first panel displays the five-minute controlled-autonomy monitor's last run, authority state, open incidents, portal failure count, recovery threshold, registered last-known-good revision, and monthly authority review. The AI supplies restricted diagnostic narrative; **deterministic policy** decides whether a repair is allowed. It may start one cooled-down Treasury or benchmark refresh or activate the exact registered recovery revision after repeated portal-boundary failures, provided owner authority is current. It cannot retry IBKR Flex, change formulas, secrets, permissions or database schema, deploy arbitrary code, or trade.

**Monthly authority review:** on the first of each month, a separate job creates a review. Read the status and incidents, type the exact displayed phrase `APPROVE MONTHLY AUTHORITY REVIEW YYYY-MM`, then click the approval button. This is a substantive owner decision: do not approve an unfamiliar action or open incident without investigating it. If the review becomes overdue, autonomous repairs pause while monitoring continues.

**Owner-approved maintenance actions:** the dropdown currently offers only **Refresh risk-free rate** and **Refresh provisional benchmarks**. To use one:

1. Check whether the observed date is genuinely stale (allow for weekends, U.S. holidays, market close/publication timing, and a provider outage).
2. Select the action and click **Preview action**. Read its target, environment, permitted effect and explicit exclusions. The preview is bound to you and its payload digest; it expires after 10 minutes.
3. If correct, type the exact confirmation phrase from the preview and click **Approve and execute once**. Save the approval/correlation IDs shown.
4. Wait for the named Azure job to finish. Reopen System Health, Performance or Risk analytics and confirm the new observation date and quality. A successful *start* is not proof of a successful data update.

These buttons **do not** initiate an IBKR account refresh or a full replay. Repeated clicks after a provider failure can make things worse; use the troubleshooting guide below.

## 4. What runs automatically, and when

All times below are **Hong Kong Time (HKT, UTC+8)**. Azure Container Apps stores these schedules as UTC cron expressions. A schedule is a **start time**, not a guarantee that data are complete at that minute. Check each job's execution result and the data-as-of date.

| HKT schedule | Azure job | Purpose |
|---|---|---|
| 07:00 daily | `ibkrv2stg-risk-free-daily` | Refresh validated U.S. Treasury 3-month rate, when the source has a new observation. |
| 16:00 daily | `ibkr-v11-daily` | Existing production IBKR Flex import, checks, derived portfolios, dashboard and exports. |
| 16:00 daily | `ibkrv2stg-institutional-daily` | Process active website-managed Flex connections with one statement-generation attempt per source, archive the raw response, partition selected accounts and enqueue downstream work. |
| 16:10 daily | `ibkrv2stg-account-sync-daily` | Validate the production manifest/hash and idempotently synchronize U16088571 into the website database. It has no Flex access. |
| 16:30 daily | `ibkrv2stg-benchmark-daily` | Refresh provisional Yahoo benchmarks and crisis data. |
| 16:45 daily | `ibkrv2stg-sector-classification` | Refresh provisional holdings-sector classifications. |
| 16:50 daily | `ibkrv2stg-derived-daily` | Recalculate every active filtered or composite portfolio in dependency order after its physical sources and market data are available. |
| 17:00 HKT daily | `ibkrv2stg-month-end-factsheet` | When the prior month has a successful post-month source refresh, freeze/validate its data and generate the approved-layout PDF/PPTX pair for owner review. It does not automatically approve release. A late Flex import is picked up on a later day. |
| 17:10 daily | `ibkrv2stg-analyst-assurance` | Reconcile the pilot's portfolio data, analytics and report integrity; record findings. |
| 17:15 daily | `ibkrv2stg-burn-in-monitor` | Record the day's acceptance result during the current 14-consecutive-day test window. |
| Every five minutes | `ibkrv2stg-autonomous-admin` | Detect incidents and perform only pre-authorized, policy-limited repairs. |
| 09:00 on the 1st of each month | `ibkrv2stg-autonomy-review` | Create the monthly owner authority review. |

The scheduled jobs use one replica and **zero platform retries**, except the older manual bootstrap job. The no-retry setting is deliberate to avoid aggravating IBKR's “Too many failed attempts” response. A failed scheduled run should be diagnosed, not blindly restarted.

## 5. Your routine as owner

### Every business day (about five minutes)

1. After approximately **17:15 HKT**, open **System health**. Verify that the latest ingestion date and U16088571 NAV date advanced when the relevant markets/data were available; check benchmark coverage, analyst assurance, and open incidents.
2. Open **Administration**. Check the latest monitor run, production authority, incident count, and whether an unexpected recovery action occurred. Do not approve a month-bound authority review without reading it.
3. Spot-check **Overview** NAV and **Performance** benchmark through dates against the prior day and, for material moves, against IBKR/source data. Check **Risk analytics** for unavailable/stale rate, Sharpe or crisis results.
4. If a check is red, stale or surprising, preserve the date, page, selected As of, expected value, job execution name, and correlation ID. Follow Section 8; do not edit financial figures in the database by hand.

### Weekly

1. In Azure, review the last seven executions of the daily import, sync, benchmark, sector, Treasury, assurance, autonomous-admin and burn-in jobs. A portal page may still display old data after a failed job.
2. Review Azure Monitor alerts and the action group delivery. Confirm the current administrator can receive alerts.
3. Reconcile a sample of NAV, cash, positions and one report/CSV with the original IBKR statement or controlled export. Record discrepancies.
4. Check Azure Cost Management against the approved staging cost ceiling and investigate unexplained growth.

### Monthly and at month end

1. On day 1, inspect the **Administration** authority review. Approve only after reading the incidents and recovery baseline. Complete it before its displayed due date if you want policy-limited autonomous repairs to stay enabled.
2. After the preceding month's last trading-day data are present and the post-month source refresh has succeeded, check **Monthly reports** for **Ready for review**. The job checks daily; do not assume calendar day 1 always succeeds.
3. Download both files, visually compare them with the approved August 2026 two-page v6 factsheet and 20-slide v23 deck, and reconcile their figures to the frozen snapshot. Check legal name **Sprout Asset Management (Asia) Limited**, SFC number **BDW665**, regulated-activity wording, contact details, dates, page/slide count, axis labels and crowded tables.
4. Record formal acceptance or rejection. A review-ready download is **not** an internally released package; ask for the future generic release workflow before treating it as one.
5. Review backup retention, latest successful backup, access list, source/dependency updates, and provider licensing. Keep client distribution blocked until the licensing/disclosure gates are formally cleared.

## 6. How to inspect Azure safely

The Azure subscription is **Microsoft Azure Sponsorship**, ID ending **90eb**, and the resource group is **`rg-ibkr-database-v11`** in East Asia. The website's own database is **`ibkrv2stg-pg-auhecwc4itaoo`**; the legacy production database is a different server. Avoid changing one when you mean the other.

### Check a scheduled job without changing it

1. Open the [Azure portal](https://portal.azure.com/) with your permitted Azure identity; confirm the subscription shown at the top or in the resource filter.
2. Search for **Container Apps Jobs** (or open **Resource groups → rg-ibkr-database-v11** and filter by job). Select the exact job name from Section 4.
3. On **Overview / Executions**, inspect the most recent execution's start, end and status. Open the execution and its container logs. Search for the correlation/run ID or error code; avoid copying secrets into a ticket.
4. Compare the completed time with the website's **System health** dates. A job can succeed yet legitimately import no newer market observation; conversely, an old website date after a job success needs explanation.
5. Check **Monitoring → Alerts** and the Log Analytics workspace if an alert was raised.

Microsoft documents the scheduled-job execution view and the rolling history limit of 100 executions: [Azure Container Apps Jobs](https://learn.microsoft.com/en-us/azure/container-apps/jobs).

### Check the website deployment

1. In the resource group, open Container App **`ibkrv2stg-web`**. Confirm that provisioning succeeded, HTTPS ingress is enabled, and the application URL matches this manual.
2. Under **Revisions**, check which revision receives traffic and whether it is healthy. As of this manual's date, the current revision is **`ibkrv2stg-web--0000058`** in **Single** revision mode. Revision names will change after a deployment.
3. Do not switch traffic to an older revision without comparing its code, access list, report behavior and security settings. The autonomous recovery baseline can lag the newest feature revision; an automatic rollback should be treated as an incident requiring a full feature/access check.

See Microsoft's [Container Apps revisions guide](https://learn.microsoft.com/en-us/azure/container-apps/revisions).

### Check database protection and costs

1. Open PostgreSQL Flexible Server **`ibkrv2stg-pg-auhecwc4itaoo` → Backup and restore**. Confirm backups remain enabled and note the earliest restorable time.
2. The currently deployed website database is **Burstable B1ms**, **32 GB** storage with autogrow, **14-day backup retention**, **no high availability**, and **geo-redundant backup disabled**. This is an economical pilot configuration, not a high-availability guarantee. The retention window does **not** cover the full 2025–present history by itself; raw/export archives and a tested recovery runbook are essential.
3. In **Cost Management**, filter the resource group/tags to compare actual cost and forecast with your approved ceiling. Foundry model calls, storage, logs and job executions can add cost even when the web app scales to zero.
4. For recovery, use a **new isolated server** and reconcile its schema, snapshot counts, NAV totals, report hashes and access before cutting over. Do not restore over the live database casually. Microsoft's [PostgreSQL on-demand backup](https://learn.microsoft.com/en-us/azure/postgresql/backup-restore/how-to-perform-backups) and [point-in-time restore guidance](https://learn.microsoft.com/en-us/azure/postgresql/backup-restore/how-to-restore-custom-restore-point) describe the platform operation.

## 7. Manage access and security

### Add a new Viewer

This is a controlled institutional-registry change, **not** a button on the website.

1. Obtain the colleague's company email, approved role (**Viewer**), and the exact list of accounts the person may access. State whether each entitlement is read, report, analyze, approve or admin; ordinary colleagues should normally receive **analyze** only.
2. In Microsoft Entra ID, verify the exact user account and record its **Object ID**. A display name or email alias is not sufficient. Confirm the person can complete your organization's sign-in/MFA.
3. Export the current institutional identity registry in a protected working session. Preserve every existing identity, role and account entitlement; never construct a replacement manifest containing only the new person.
4. Add the identity and the approved account-specific entitlements to an integrity-bound **replace** manifest. Use exact governed subject IDs. Physical IBKR account IDs are normalized to uppercase; derived IDs such as `hong-kong-china` retain their governed identifier.
5. Validate the manifest and its SHA-256 in a zero-retry dry run. Apply that exact digest through the manual bootstrap job, then verify that the registry revision is `applied` and that every existing entitlement remains present.
6. Test the colleague's real sign-in and every permitted account, download/export and AI Analyst boundary. Also test that an unentitled account returns `Portfolio not available`, and that **Administration** is absent and directly denied. Ask the colleague to sign out and back in if an old session remains. Record approval, date, exact claims, registry digest and test result in the access register.

An Entra user-account replacement can change the Object ID even if the email stays the same. Authentication and account authorization are separate controls: a successful Microsoft sign-in does not bypass the institutional role and entitlement tables. Container Apps' [Entra authentication documentation](https://learn.microsoft.com/en-us/azure/container-apps/authentication-entra) explains the authentication layer.

### Revoke or change access

1. Record the owner decision and exact identity/account scope. Create an integrity-bound replacement manifest that removes only the revoked role or account entitlements while preserving every other approved identity, role and entitlement.
2. Validate and apply the exact digest through the zero-retry registry job, then test denial using a fresh/private browser session. Revoke the person's Microsoft Entra/app assignment as appropriate under company policy. Existing authenticated sessions may not vanish instantaneously; consider Entra session revocation for urgent offboarding.
3. Review file copies already downloaded by that person under your retention/offboarding policy. Removing website access does not recall downloaded files.

The current platform already enforces explicit per-account authorization. Activating a new account does **not** automatically grant it to every Viewer. Never share the Owner login or grant Azure subscription Owner merely to let someone browse the website.

### Keep secrets and permissions safe

- Keep Flex tokens, query IDs requiring protection, database credentials and AI/provider credentials in Key Vault or managed identity, not in source, manuals, chat, screenshots or uploaded templates.
- Keep raw statements in their restricted storage containers. Do not upload them to the AI Analyst; the approved model processing scope is structured results only.
- Treat downloaded CSV/JSON/PDF/PPTX as sensitive financial files. Use company-approved storage, encryption, access controls and retention.
- Review Entra sign-in logs and web security audit entries after unusual 401/403 responses. The app logs hashed identity references and correlation IDs rather than raw credentials.
- Refresh the Entra app registration credential **before** expiry under a tested change procedure. A website outage can occur even when the application container itself is healthy.

## 8. Troubleshooting decision guide

### “The website date did not advance after 16:00 HKT”

1. Wait until the production and downstream jobs have had time to finish; **16:00 is only the start** of the first job. Check market holidays and whether IBKR has published the latest statement.
2. Check `ibkr-v11-daily` first. If it failed, inspect its logs and IBKR error code. If it succeeded, check the production `analytics-reports` manifest and `ibkrv2stg-account-sync-daily` execution.
3. If account sync succeeded, inspect **System health** and Overview's selected/effective dates; then check benchmark, sector and assurance jobs separately.
4. Do not repeatedly start `ibkr-v11-daily` or submit multiple IBKR Flex requests. The one-request/zero-retry protection exists to avoid account lockout.

### IBKR errors 1001 or 1018 / “Too many failed attempts”

1. Stop manual repeat attempts. Preserve the first execution's timestamp, endpoint and IBKR error code; **do not** paste the token into a ticket.
2. **1001** means the statement could not be generated at that time; it can be an IBKR-side processing/availability problem. Verify the query in IBKR Client Portal and allow an appropriate cooldown. A later scheduled run is safer than rapid retries.
3. **1018 / too many failed attempts** indicates a lockout or attempt limit. Check the token/query configuration, circuit-breaker state and IBKR guidance/support before any controlled reset. Do not assume changing the website schedule solves a token-level restriction.
4. After resolution, perform one controlled refresh and verify both the production export and the website sync. Escalate if the same error recurs.

### Risk-free rate or Sharpe says Unavailable

1. Check the risk page's Treasury observation date and note. A weekend or U.S. holiday can legitimately leave the prior business day's rate.
2. Check `ibkrv2stg-risk-free-daily` execution and the Treasury source. If genuinely stale, use the **Administration → Refresh risk-free rate** preview/approval once, then confirm job completion and refreshed risk page.
3. If Sharpe remains unavailable, check that validated portfolio return observations and rate coverage are sufficient. Do not substitute a future rate or enter a manual zero.

### Benchmark comparison or sector data is stale/unavailable

1. Check the benchmark/date/source metadata and the appropriate benchmark or sector job execution. Price-index holidays and Yahoo publication delays can cause legitimate gaps.
2. If the benchmark job is genuinely stuck, use the one-time **Refresh provisional benchmarks** owner workflow and verify the completed result. The action does not create a licensed official close.
3. For missing sector coverage, investigate the sector-classification job and mapping review. Avoid silently assigning an unknown holding to an arbitrary sector.

### AI answer is unavailable or disagrees with the dashboard

1. Record the account, As of date, selected benchmark, analysis type, question and correlation ID.
2. Recheck deterministic Overview/Performance/Risk pages and System Health. Missing/stale inputs or a strict model-response validation failure should block the answer.
3. Check the Foundry model deployment and portal logs through an approved administrator. Do not enable unrestricted model tools or arbitrary SQL as a workaround.
4. Correct the validated source/calculation or escalate for engineering review; never publish the disputed figure based solely on the prose answer.

### Monthly report is Generation pending, wrong, or cannot download

1. Confirm the prior month's last trading-day NAV, benchmark, risk-free and holdings data are present and quality gates pass.
2. Check the `ibkrv2stg-month-end-factsheet` execution (it runs daily at 17:00 HKT), then the report centre status. A frozen snapshot alone is not a complete PDF/PPTX pair.
3. If the preceding month is still pending after the **7th** despite complete data, the automatic attempt window has ended; arrange a controlled catch-up run with an engineer rather than assuming the next daily job will retry it. If Ready for review, use **Generate secure downloads**. A hash/integrity failure is a release blocker; do not download directly from storage to bypass it.
4. For a layout/figure issue, preserve the period, run IDs, snapshot ID, page/slide and expected value. Re-render under the approved template and validate both files. Do not overwrite an already released package.
5. Remember that owner review downloads are not an internal release. The future generic approval/release workflow remains to be built.

### Access denied, login loop or blank page

1. Confirm the user chose the correct WealthRyse identity and can sign into Microsoft Entra.
2. Check the exact email and Object ID allow-lists, the app registration/callback, client-secret validity, and whether the latest app revision is healthy.
3. Test in a private browser window. A 403 from the application differs from an Entra authentication failure; preserve the correlation ID and timestamp.
4. If a deployment caused the issue, use the controlled revision/rollback procedure **only after** checking that the candidate revision preserves today's access and reporting behavior.

## 9. Backups, recovery and change control

1. **Before a change:** record the current image digest/revision, job schedules, non-secret configuration, database schema version, last successful backup/restore point, current System Health, and report artifact hashes. Keep a rollback plan and a clear owner of the change.
2. **Test in isolation:** run automated unit/integration/financial checks and, for report changes, render and inspect every affected PDF page and slide. Test sign-in, Viewer denial of Administration, downloads, AI grounding and date-specific analytics.
3. **Deploy immutable artifacts:** use a pinned image digest and explicit database migration. Never edit a container's source live or change a finance formula silently.
4. **Verify after deployment:** healthy revision/traffic, the account pages, System Health, sample CSV/report hashes, and at least one relevant job execution. Record the new baseline. Watch the next daily run.
5. **Recover carefully:** for a web-only failure, a previously verified revision may be reactivated. For data corruption, stop affected writes, take forensic copies, restore to an isolated PostgreSQL server within the retained window, replay validated archived exports if needed, reconcile counts/NAV/report hashes, then plan cutover. No owner should run a destructive in-place restore from a generic troubleshooting instruction.

**Current hardening gap:** a private remote source repository, automated CI/CD and security scanning were deferred. Local source changes are not yet a substitute for a protected release pipeline. Keep an encrypted, access-controlled copy of the source/release manifests and make completion of source management and CI/CD a priority before expanding accounts or staff.

## 10. Financial-data and publication rules

- U16088571's web/report start is **20 January 2025**; USD is the reporting currency.
- Primary/secondary indexes are `^GSPC` and `^IXIC`, both **price-return** variants. The current feed is `yfinance`/Yahoo Finance, with provisional source/usage status. A future Bloomberg migration must update provider, licensing, lineage, historical reconciliation and report disclosures together; changing a ticker label is not enough.
- The default risk-free reference is **U.S. Treasury Daily Treasury Par Yield Curve, 3-month tenor**. As-of calculations may use only the latest validated rate on or before that date.
- Financial metrics and report numbers are produced by versioned deterministic code and frozen snapshots. The AI may draft narrative but not invent numerical claims. All externally important numbers need independent review.
- Current released/review documents are for approved **internal/shareholder** use. **Client distribution is blocked** at the data/release level until licensed market-data rights and regulatory disclosures are approved. Removing a visible warning from a page does not remove that control.
- For every analysis, note the account, period, actual observation date, currency, exact benchmark variant, source/quality status and calculation version. If any of these are ambiguous, pause publication.

## 11. Current limitations you should plan around

1. The self-service release supports physical accounts, filtered portfolios and fixed-unit composites. Master-account onboarding remains an administrative membership/directory test; consolidated master performance is blocked until duplicate cash, holdings and transfer controls pass.
2. Future monthly pairs are automatically generated **for review**, not auto-approved. Final approval remains an owner decision and cannot be delegated to the AI.
3. No direct PostgreSQL/SQL console is exposed to website users. The older SQLite DB Browser route is separate.
4. The current PostgreSQL tier has no HA and native backup retention is 14 days; a longer-term retention/disaster-recovery design is needed before critical multi-client operation.
5. The benchmark and sector feed is provisional Yahoo data. Client-facing publication requires a licensed provider and renewed reconciliation.
6. The source repository and CI/CD/security pipelines remain incomplete. A passing test suite and one successful onboarding case do not replace daily operational monitoring or financial reconciliation.
7. The autonomous recovery baseline must be reviewed after each deployment. An older exact revision may restore availability while removing newer pages or access changes.

## 12. Scale up safely: add accounts, colleagues and capacity

### A. Start or reuse an IBKR connection

1. Open **IBKR connections**. Reuse an existing validated connection when the new account is already contained in its Flex query; do not create duplicate credentials for the same query.
2. For a new connection, enter a non-secret connection/credential label, Flex Query ID and token in the protected credential form. The token is written directly to the dedicated Key Vault and is never displayed again or sent to the AI.
3. Start one validation. The zero-retry discovery worker makes at most one statement-generation request, archives the validated raw response and lists the accounts found under that query. If IBKR rejects it, investigate before trying again.
4. Select the accounts to onboard and set their order. The first account becomes current; later selected accounts remain locked. Unselected accounts stay **Available later** and can be placed in a future queue without repeating discovery.

### B. Complete the page-by-page onboarding wizard

Complete one subject fully before advancing the connection queue:

1. **Source and history:** confirm the chosen discovered account and reporting start. Existing v1.1 accounts can instead use the governed v1.1 import path.
2. **Financial contract:** enter the display name, reporting currency, start date, benchmark symbols/currencies and price- or total-return convention. Owner approval binds the exact contract.
3. **External-data consent:** approve the bounded ticker-only Yahoo scope if sector and crisis enrichment is required. Raw IBKR data, credentials and personal data remain excluded.
4. **Presentation identity:** owner approves the exact client-facing strategy identity. The account-supplied deck is identity/branding evidence; generated decks use the platform-standard presentation design.
5. **Documents:** upload the factsheet template, presentation template and previously completed factsheet. The server validates file type, content, required identity and hashes.
6. **Reconciliation and analytics:** run deterministic reconciliation, then queue benchmark/sector/crisis analytics. Missing or stale evidence blocks the next page.
7. **Historical reports:** queue every completed past month, wait for the PDF/PPTX pairs, download samples and approve the integrity-bound batch only after visual and numerical review.
8. **Private preview:** inspect every account page, date selector, download and AI response; approve the exact evidence snapshot.
9. **Activation:** type the displayed confirmation phrase and activate. Activation registers daily import, analytics, reports and assurance capabilities, then opens the canonical account Overview page.

For a filtered or composite portfolio, choose the corresponding branch and enter only typed components/rules. The system validates dependencies and cycles, calculates history deterministically, and uses the same analytics, report, preview and activation gates. It never executes arbitrary uploaded SQL or Python.

### C. Reconcile and supervise the new subject

1. Compare opening/latest NAV, positions, trades, cash, fees, dividends and FX with IBKR or the governing source portfolio.
2. Confirm benchmark currency conversion, risk-free coverage, sector coverage and crisis proxy evidence.
3. Review representative historical PDF/PPTX files and the latest private preview before activation.
4. After activation, verify the next 16:00 HKT daily cycle and the next completed month-end package. Use System Health and Administration to investigate a failure; never edit financial values by hand.

### D. Add people, providers and resilience

1. Start each colleague as least-privilege Viewer under the Section 7 process. Introduce per-account roles before adding client or broader staff access. Test real sign-in and offboarding.
2. Replace Yahoo with licensed Bloomberg or another approved source under a formal data-migration/reconciliation plan. Confirm redistribution, caching and archive rights in writing.
3. Capacity-test the web app, PostgreSQL, scheduled-job overlap, Azure Monitor and Foundry usage. Move beyond B1ms/32 GB, 14-day-only recovery and no HA when actual account count, RTO/RPO or shareholder commitments justify it.
4. Complete private source control, CI/CD, dependency/secret/container scanning, migration checks, immutable release and rollback approval before scaling materially.
5. Do one complete month-end cycle for each new account: last-trading-day data, frozen snapshot, AI-drafted but numerically validated commentary, layout QA, owner approval, internal release and download test.

## 13. Handy glossary

| Term | Meaning here |
|---|---|
| **As of** | User-selected cutoff date; a specific dataset may use the latest validated observation on or before it. |
| **NAV** | Net asset value of the account in its reporting currency, as imported/reconciled from IBKR. |
| **Price index** | Index level excluding reinvested dividends; not interchangeable with a total-return index or ETF. |
| **Rebased to 100** | Common-scale chart starting both series at 100 on their first shared valid date. |
| **Frozen snapshot** | Immutable month-end input package used to make/reconcile a report. |
| **Ready for review** | Integrity-checked PDF and PPTX pair available to authorized users, without internal release approval. |
| **Ready** | Internally released report pair; still not client-publication permission. |
| **Correlation ID** | Identifier tying a browser request to security/AI/administrative audit evidence. |
| **Circuit breaker** | Safety state that prevents repeated IBKR generation attempts after a lockout/error. |
| **Last-known-good revision** | Exact pre-registered website revision eligible for policy-limited recovery, not automatically the newest feature release. |

---

**Ownership rule:** if the portal, an Azure job, an AI answer and a report disagree, stop distribution, preserve the evidence and reconcile back to IBKR/source records and the frozen snapshot. Do not “fix” a financial number by directly editing a database row.
