# Pilot Data Contract — U16088571

## Confirmed pilot scope

- Pilot account: `U16088571`
- Current account base currency in the production export: USD
- Account type in the production export: Institution Client
- Available daily NAV history in the inspected snapshot: 17 January 2025 through 18 September 2026
- Website and report start date: 20 January 2025, aligned with the existing platform normalization date
- Reporting currency: USD
- Primary benchmark: S&P 500 Price Index (`GSPC`; yfinance symbol `^GSPC`)
- Secondary benchmark: NASDAQ Composite Price Index (`IXIC`; yfinance symbol `^IXIC`)
- Benchmark currency: USD
- Benchmark convention: price return for both configured indexes
- Legal issuer: Sprout Asset Management (Asia) Limited
- SFC central entity number: `BDW665`
- Regulated activities stated in the approved presentation: Type 1 (dealing in securities), Type 4 (advising on securities), Type 6 (advising on corporate finance), and Type 9 (asset management)
- Contact email: `inquiry@www.sprout-asset.com`
- Initial access: only `anthony.ying@wealthryse.ai`
- Current production ingestion continues daily at 16:00 HKT during development

## Deterministic reporting rules

All published figures must come from versioned calculation code and a validated reporting snapshot. The language model may explain results and draft commentary, but it must not invent, estimate, or independently calculate published financial figures.

Every website view, factsheet, and deck must disclose:

- account or portfolio identifier
- period start and end
- data as-of timestamp
- reporting currency
- benchmark identifier and exact benchmark variant
- price-return or total-return convention
- official-close or intraday status
- FX source and conversion convention
- formula version
- data-quality status

## Approved benchmark contract

- Reporting currency: USD
- Official reporting start: 20 January 2025
- Primary benchmark: S&P 500 Price Index (`GSPC`)
- Secondary benchmark: NASDAQ Composite Price Index (`IXIC`)
- Benchmark return convention: price return for both benchmarks
- Benchmark observations: official USD closing index levels
- Comparison method: align portfolio and benchmark on common valid dates, normalize both to 100 on the first common observation on or after 20 January 2025, and never use forward-looking prices or FX rates
- Default risk-free-rate series: U.S. Treasury Daily Treasury Par Yield Curve
  Rates, 3-month tenor (`UST-PAR-3M`), stored as a decimal annualized par yield
- Risk-free date convention: use the latest validated Treasury observation on
  or before the selected portfolio as-of date; never forward-fill from a future
  observation
- Sharpe convention: convert the selected annual rate to a geometric 252-day
  daily rate, subtract it from the arithmetic mean of validated cash-flow-
  adjusted portfolio daily returns, and annualize by the square root of 252
- Daily Expected Tail Loss convention: historical 95% Expected Shortfall,
  reported as the positive mean loss across the worst 5% of validated daily
  cash-flow-adjusted portfolio returns through the selected as-of date
- Expected Maximum Drawdown (1 Year): arithmetic mean maximum drawdown across
  5,000 deterministic five-session moving-block bootstrap paths, each containing
  252 sessions, sampled from validated daily portfolio returns available through
  the selected as-of date
- Actual Maximum Drawdown (1 Year): realized maximum peak-to-trough drawdown
  over no more than the latest 252 validated return observations through the
  selected as-of date
- Tail Maximum Drawdown (1 Year): arithmetic mean of the worst 5% of maximum
  drawdowns across the same deterministic 5,000-path simulation
- Crisis Maximum Drawdown: hypothetical fixed-unit replay of the latest
  validated holdings from 15 September 2008 through 9 March 2009 inclusive.
  Each sleeve opens at its latest market-value-to-NAV weight; cash is the
  residual NAV weight and remains flat and non-interest-bearing; positions are
  not rebalanced. Use the holding's Yahoo Finance adjusted-close series only
  when it covers every SPY crisis-calendar observation and both endpoints.
  Otherwise use the recorded sector ETF proxy (`XLK`, `XLI`, `XLV`, `XLY` or
  `XLB`) or broad-market `SPY` fallback for the entire sleeve. The metric is the
  maximum peak-to-trough decline of the resulting normalized replay. Record the
  holdings date, direct/proxy mapping, current market value, opening weight,
  source, price type, proxy reason and calculation version. Missing or
  incomplete proxy data must make the metric unavailable; no silent splice or
  estimate is permitted. This is a hypothetical stress test, not the account's
  realized 2008–2009 performance.
- Expected Tail Loss position contribution: on portfolio tail-return dates,
  attribute previous-to-current market-value change only to carried USD
  positions whose aggregate quantity and currency are unchanged across both
  adjacent validated dates; rank the five largest loss contributors and five
  largest offsets, and disclose a residual equal to portfolio Expected Tail
  Loss less attributed position contribution for cash, fees, FX, trades and
  unmatched positions
- Website comparisons: official daily closes only; intraday values, if added later, must be clearly separated

### Temporary staging-only benchmark exception

Until the planned Bloomberg integration is available, staging may retrieve the
S&P 500 Price Index symbol `^GSPC` and NASDAQ Composite Price Index symbol
`^IXIC` from Yahoo Finance through `yfinance`. The index variants are correct,
but both feeds are marked `restricted`
and `provisional_close` because provider licensing and official-close validation
are not approved. It must not be used in a client-facing factsheet,
presentation, published report or approved month-end snapshot.
- Data provider and redistribution rights: pending provider and licensing approval; the production system must not rely on an unlicensed scrape

## Required user materials

Received:

1. U16088571 monthly factsheet template in PDF format.
2. U16088571 presentation-deck template in editable PowerPoint format.
3. One previously completed U16088571 factsheet.
4. Primary and secondary benchmark price-return conventions.
5. USD reporting-currency approval.

Resolved:

1. The legal issuer is Sprout Asset Management (Asia) Limited.
2. The confirmed contact email is `inquiry@www.sprout-asset.com`.
3. Both supplied factsheet PDFs already name Sprout Asset Management (Asia) Limited.
4. The latest PowerPoint upload no longer contains Wang On Asset Management Limited.
5. The latest PowerPoint disclaimer identifies Sprout with CE number `BDW665`; `BJU500` is absent.
6. The latest PowerPoint disclaimer lists Types 1, 4, 6 and 9, matching both supplied factsheet PDFs.

Still required before publishing client-facing documents:

1. Preferably, the editable PowerPoint source for the monthly factsheet. The supplied PDF is not a fillable form, so an exact automated factsheet would otherwise need to be rebuilt from the visual reference.
2. Licensing approval for production Bloomberg sources of official S&P 500 Price Index and NASDAQ Composite Price Index closing levels.

Place the files in the local folder `private-inputs/U16088571`. This folder is
excluded from Git. Files should be provided without passwords, API keys, Flex
tokens, or other secrets.

## Acceptance criteria

- Every printed number reconciles to the frozen reporting snapshot.
- Account-level access checks are enforced server-side.
- Missing, stale, ambiguous, or unvalidated data is never silently estimated.
- Factsheet and deck remain drafts until user approval.
- AI tool calls and approved administrative actions are fully audited.
