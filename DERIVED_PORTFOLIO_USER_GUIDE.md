# Derived Portfolio Guide

This guide covers `China & Hong Kong`, `Alpha World MS`, and `Alpha World T`.

## Alpha World T

`Alpha World T` is a fixed-unit calculated portfolio whose normalized NAV begins
at 100 on **20 January 2025**:

```text
58
+ 0.91 × normalized NAV of U16088606
+ 0.45 × normalized NAV of U16088571
− 0.49 × normalized Hang Seng price index
− 0.45 × normalized SPY settlement-price proxy
```

Every sleeve is converted to the base currency of `U16088606` before
normalization. The 58 cash units earn no interest. The coefficients represent
fixed units and are not reset each day, so actual sleeve weights drift with
performance. At inception the portfolio has 136% long risky exposure, 94% short
risky exposure, 230% gross exposure, 42% net risky exposure, and 58% cash.

Unlike `Alpha World MS`, Alpha World T uses the complete `U16088606` account as
its 0.91 primary sleeve. It therefore retains the source account's Taiwanese
company exposure. Alpha World MS instead uses `China & Hong Kong`, which removes
that exposure.

To inspect Alpha World T history:

```sql
SELECT snapshot_date, currency, total_nav, cash, generated_at
FROM derived_portfolio_nav
WHERE portfolio_id = 'alpha-world-t'
ORDER BY snapshot_date;
```

To audit every Alpha World T calculation input and contribution:

```sql
SELECT snapshot_date,
       component_name,
       direction,
       coefficient,
       source_currency,
       source_value,
       fx_rate_to_base,
       normalized_value,
       contribution,
       observation_date
FROM synthetic_portfolio_components
WHERE portfolio_id = 'alpha-world-t'
  AND snapshot_date BETWEEN '<START_DATE>' AND '<END_DATE>'
ORDER BY snapshot_date, component_id;
```

## Alpha World MS

`Alpha World MS` is a fixed-unit calculated portfolio, not an IBKR brokerage
account. Its normalized NAV begins at 100 on **20 January 2025** and is calculated
as:

```text
58
+ 0.91 × normalized NAV of China & Hong Kong
+ 0.45 × normalized NAV of U16088571
− 0.49 × normalized Hang Seng price index
− 0.45 × normalized SPY settlement-price proxy
```

All four risky sleeves are converted to the `China & Hong Kong` base currency
before they are normalized. The 58 cash units earn no interest. Coefficients are
fixed units, so the strategy is not rebalanced back to its initial weights each
day. Initial risky exposure is 136% long and 94% short, for 230% gross and 42%
net risky exposure, plus 58% cash.

The China & Hong Kong sleeve uses its reconstructed HKD/USD cash ledger. Any
change to that ledger changes the sleeve's normalized path and therefore Alpha
World MS from 20 January 2025 onward. The common normalization date still makes
Alpha World MS equal 100 at inception. Alpha World T is unaffected because its
primary sleeve remains the complete `U16088606` account.

The system downloads and stores daily unadjusted closes for Yahoo symbols `^HSI`
and `SPY`. `^HSI` is used as the Hang Seng price-index series. Because SPY is an
ETF and does not publish a futures-style settlement, its unadjusted official
market close is stored and used as the requested settlement-price proxy. Adjusted
close is deliberately not used.

Portfolio dates are the dates for which both IBKR portfolio sleeves have NAV.
For an exchange holiday, the latest market close on or before the portfolio date
is carried forward for at most seven calendar days. In particular, SPY's
20 January 2025 normalization value uses its 17 January 2025 close because the US
market was closed on 20 January. The actual observation date is stored with every
component row. Missing or stale inputs stop publication rather than silently
producing an incomplete NAV.

Every 16:00 HKT run performs the following
before publishing the dashboard:

1. imports and reconciles IBKR data;
2. downloads the full required `^HSI` and `SPY` close history and upserts it;
3. rebuilds `China & Hong Kong`;
4. rebuilds `Alpha World MS` from the two IBKR sleeves and two market series;
5. rebuilds `Alpha World T` from the two full IBKR account sleeves and the same
   two market series;
6. generates the dashboard, SQLite snapshot, CSV ZIP and manifest.

To inspect the Alpha World MS history in SQLite:

```sql
SELECT snapshot_date, currency, total_nav, cash, generated_at
FROM derived_portfolio_nav
WHERE portfolio_id = 'alpha-world-ms'
ORDER BY snapshot_date;
```

To inspect its calculation inputs and contributions for a period:

```sql
SELECT snapshot_date,
       component_name,
       direction,
       coefficient,
       source_currency,
       source_value,
       fx_rate_to_base,
       normalized_value,
       contribution,
       observation_date
FROM synthetic_portfolio_components
WHERE portfolio_id = 'alpha-world-ms'
  AND snapshot_date BETWEEN '<START_DATE>' AND '<END_DATE>'
ORDER BY snapshot_date, component_id;
```

To inspect the raw market observations used by the calculation:

```sql
SELECT instrument_id, price_date, symbol, price_type, currency, price,
       source, fetched_at
FROM market_data_prices
WHERE price_date BETWEEN '<START_DATE>' AND '<END_DATE>'
ORDER BY instrument_id, price_date;
```

## China & Hong Kong

## What it is

`China & Hong Kong` is a calculated portfolio derived from IBKR account
`U16088606`. It is not a separate brokerage account. Every successful daily Azure
run keeps the source account's non-Taiwan positions, removes Taiwanese-company
exposure, removes the source account's cash, and substitutes an audited HKD/USD
cash ledger. The ledger starts with HKD 484,300 and USD 0 before activity on
20 January 2025. The result is published in the dashboard and SQLite analyst
snapshot.

It is deliberately stored separately from the `accounts` table. The dashboard's
**All accounts** view therefore does not double-count this portfolio and its source
account.

## Daily timing

The Azure Container Apps job stores `0 8 * * *` UTC, which is 16:00 HKT. The
schedule is active and executes automatically every day.
The sequence is:

1. import and reconcile the latest IBKR Flex statement;
2. refresh reference classifications;
3. rebuild the audited HKD/USD cash ledger;
4. rebuild `China & Hong Kong` from `U16088606`;
5. rebuild `Alpha World MS` from the revised normalized portfolio series;
6. generate the dashboard;
7. generate the SQLite snapshot, CSV ZIP and integrity manifest.

If the latest source NAV has no position snapshot for the same date, the derived
portfolio step fails rather than publishing a stale or partially filtered result.

## Taiwan classification

A position is excluded when any of these signals identifies Taiwan exposure:

- cached country classification is Taiwan;
- listing exchange is a recognized Taiwan exchange such as `TWSE` or `TPEX`;
- the instrument or reported position currency is `TWD`.

This also removes TWD corporate-action receivables linked to Taiwanese holdings.
Every removed row records its symbol, description, exchange, currency, reason and
base-currency market value for audit.

## Calculation

For each date with a source position snapshot:

```text
Derived NAV
= U16088606 NAV
− U16088606 cash
− signed base-currency value of excluded Taiwan positions
+ reconstructed HKD/USD cash
```

Reconstructed cash starts at HKD 484,300 and USD 0 before activity on 20 January
2025. Eligible HKD/USD trades, dividends, commissions, custody fees, withholding
taxes, actual posted interest and other cash activity then update the ledger. No
assumed interest is applied. TWD and every other currency are excluded, as are all
cash events linked to excluded Taiwan instruments. All legs of a TWD-linked FX
trade are excluded. Duplicate Flex cash rows are retained for audit but applied
only once.

Stock, options, futures and bonds are reduced by excluded positions in their
corresponding asset class. Short Taiwan positions are removed using their signed
market value, so the adjustment also works for negative exposure.

The initial historical import contains daily NAV but mainly month-end position
snapshots. A one-time exact-date backfill requests a separate IBKR Flex statement
for every source-account NAV date from **20 January 2025** that does not yet have
positions. After that backfill completes, the derived portfolio has broker-reported
daily history on every available IBKR statement date from 20 January 2025 onward.
Weekends or dates for which IBKR has no source statement are not invented or
forward-filled.

The backfill is resumable and idempotent: each completed date is committed
independently, and a rerun skips dates already present. It pauses at least ten
seconds between requests. A database-backed token circuit also serializes all
scheduled, historical and manual Flex operations. Run the one-time cloud backfill
job when bootstrapping the history:

```text
python scripts/run_cloud_historical_backfill.py --from 2025-01-20 --to <YESTERDAY>
```

The script rebuilds and publishes the derived portfolio, dashboard and analyst
exports after it reports `status: complete`. The normal 16:00 HKT job then
maintains the new dates every day; the historical backfill is not a recurring
manual task. If interrupted, rerun the same command and it resumes with the first
missing date.

## Dashboard access

Open the latest dashboard and select **China & Hong Kong (derived)** from
**Portfolio view**. The portfolio snapshot shows:

- included position count;
- Taiwan positions removed;
- excluded base-currency market value;
- current HKD and USD cash balances;
- recalculated NAV, exposure, allocation, return and risk statistics.

The data-quality section states the source account and methodology.

## SQLite tables

Download `IBKR_Analyst_Snapshot_Latest.sqlite` and open it using the SQLite driver in
DBeaver or DB Browser for SQLite.

| Table | Purpose |
|---|---|
| `derived_portfolios` | Name, source account, rule and calculation version |
| `derived_portfolio_nav` | Recalculated NAV and daily exclusion totals |
| `derived_portfolio_positions` | Positions retained in the derived portfolio |
| `derived_portfolio_exclusions` | Positions removed from the source and the reason |
| `derived_portfolio_cash_ledger` | Included and excluded cash events, classification and audit reason |
| `derived_portfolio_cash_balances` | Daily HKD/USD opening, activity, closing balance, FX rate and base value |
| `market_data_prices` | Daily Hang Seng price-index and SPY unadjusted-close observations |
| `synthetic_portfolio_components` | Alpha World MS and Alpha World T sleeve values, FX conversion, normalization and contribution |

Daily HKD/USD cash balances:

```sql
SELECT snapshot_date,
       currency,
       opening_balance,
       activity,
       closing_balance,
       fx_rate_to_base,
       base_value
FROM derived_portfolio_cash_balances
WHERE portfolio_id = 'hong-kong-china'
  AND snapshot_date BETWEEN '<START_DATE>' AND '<END_DATE>'
ORDER BY snapshot_date, currency;
```

Cash-event audit, including excluded TWD and duplicate rows:

```sql
SELECT event_date,
       source_type,
       currency,
       amount,
       included,
       classification,
       description,
       exclusion_reason
FROM derived_portfolio_cash_ledger
WHERE portfolio_id = 'hong-kong-china'
  AND event_date BETWEEN '<START_DATE>' AND '<END_DATE>'
ORDER BY event_date, event_key;
```

Latest derived NAV:

```sql
SELECT portfolio_id,
       snapshot_date,
       currency,
       total_nav,
       source_total_nav,
       excluded_market_value_base,
       included_position_count,
       excluded_position_count,
       generated_at
FROM derived_portfolio_nav
WHERE portfolio_id = 'hong-kong-china'
ORDER BY snapshot_date DESC
LIMIT 1;
```

Included positions for a period:

```sql
SELECT p.snapshot_date,
       i.symbol,
       i.description,
       i.asset_category,
       p.currency,
       CAST(NULLIF(p.quantity_text, '') AS NUMERIC) AS quantity,
       CAST(NULLIF(p.market_value_text, '') AS NUMERIC) AS market_value
FROM derived_portfolio_positions AS p
LEFT JOIN instruments AS i ON i.conid = p.conid
WHERE p.portfolio_id = 'hong-kong-china'
  AND p.snapshot_date BETWEEN '<START_DATE>' AND '<END_DATE>'
ORDER BY p.snapshot_date,
         ABS(CAST(NULLIF(p.market_value_text, '') AS NUMERIC)) DESC;
```

Removed positions for a period:

```sql
SELECT snapshot_date,
       symbol,
       description,
       listing_exchange,
       currency,
       exclusion_reason,
       market_value_base
FROM derived_portfolio_exclusions
WHERE portfolio_id = 'hong-kong-china'
  AND snapshot_date BETWEEN '<START_DATE>' AND '<END_DATE>'
ORDER BY snapshot_date, ABS(market_value_base) DESC;
```

## Daily operator check

After the 16:00 HKT execution finishes:

1. confirm the job execution is `Succeeded`;
2. confirm logs contain `PASS | derived portfolios`;
3. select each derived portfolio in the dashboard;
4. confirm its latest NAV and position date equal the source account date;
5. review the excluded-position count when the source holdings change;
6. use `derived_portfolio_exclusions` to investigate any unexpected removal.

## Flex request safety

Each ETL operation may call IBKR `SendRequest` only once. After IBKR returns a
reference code, the operation polls only `GetStatement` with that same reference.
Azure container retries are disabled for both the daily and historical jobs.

The `flex_request_state` table stores only a SHA-256-derived token fingerprint,
never the Flex token. It coordinates scheduled, historical and manual operations
and records the last endpoint phase, error code, cooldown and manual-reset state.
Error code 1018, invalid credentials, and messages that indicate a lockout open
the circuit and prevent any further request until the underlying IBKR issue has
been resolved.

Check the circuit without contacting IBKR:

```text
ibkr-etl flex-circuit-status
```

Only after replacing or validating the token and confirming that IBKR has lifted
the lockout, clear the circuit explicitly:

```text
ibkr-etl reset-flex-circuit --confirm
```

Neither command resumes the Azure schedule or sends an IBKR request.
