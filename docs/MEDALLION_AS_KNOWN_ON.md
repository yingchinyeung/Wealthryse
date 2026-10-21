# "As known on" history for IBKR corrections (U8): design for approval

Status: design, 18 Oct 2026, for Ying to approve. Nothing here is switched on. The draft
SQL (`infra/medallion/drafts/u8_silver_history.sql`) is not a migration, and nothing in
production applies it. It runs only in a database test on made-up data
(`tests/test_as_known_on_draft_db.py`). Work starts from 8 Nov, after month-end.

## Summary

Today, when IBKR corrects something it has already sent us, silver overwrites the old
value. Sometimes it keeps a second copy beside the original instead. Either way, nobody
can see what the website showed before the correction. This design gives five silver
tables a history: trades, cash transactions, FX transactions, FX rates and daily NAV.
Positions, cash balances and corporate actions follow two weeks later. Every row keeps
two dates: the date it happened, and the time we learned it, until a newer version
replaced it. The tables people and jobs read today don't change at all: same names, same
columns, same rows. Beside each one, a history table keeps every version, and one
function reads the table as it stood at any past moment. So a published figure can be
rebuilt exactly as it was, and a correction shows up as a new version with the time it
arrived. The re-sent FX row duplicate is fixed by recognising the re-send as a new
version of the same FX event. The history is filled once from the raw IBKR files, which
are all kept. That takes minutes, and it is rehearsed on a restored copy first. A single
database setting turns recording on, and turning it off again is the undo. The plan is
to switch it on in the week of 9 Nov, with the FX fix a week later.

## What goes wrong today

Every daily pull asks IBKR for the last 14 days, so most rows arrive 14 times. The loader
writes each one with "insert, or update if the key exists":

- **A correction under the same key overwrites the old value.** Examples: IBKR restates
  a day's NAV, an FX rate, or a trade's realised P&L. The old value is gone from silver.
  It is also gone from the bronze table `flex_rows`, whose loader updates the record in
  place (the bronze rule's one IBKR exception). Only the raw statement files still have it.
- **A correction under a new key adds a second row.** An FX row is keyed by a hash of all
  its content, amounts included. When IBKR re-sends it with new amounts, it gets a new key
  and lands beside the original. The 17 Oct switch rehearsal saw exactly this on one
  account. The website's NAV and returns don't read FX rows, so no published figure
  changed, but the analyst export and the workflow input checks see the conversion twice.
- **A cancelled and rebooked trade is visible, but not when we learned of it.** IBKR
  cancels a trade by sending a reversing row (`SELL (Ca.)`), then books it again under
  the same trade id with a later report date. All three rows sit in silver and add up
  correctly. But nothing records that on 16 Oct we knew only the first one.
- **So no past figure can be rebuilt.** The month-end factsheets are frozen in gold, so
  what we sent is safe. But nobody can show which inputs produced it, or explain why
  today's figure for the same month differs.

## What changes

### The tables

| Table | Why | When |
|---|---|---|
| `trades` | Cancellations and rebookings; restated P&L and commissions | First wave |
| `cash_transactions` | Restated amounts; deposits and withdrawals drive returns | First wave |
| `fx_transactions` | The re-sent FX duplicate | First wave |
| `fx_rates` | Rates IBKR corrects. They convert every cash flow, so they move returns | First wave |
| `nav_snapshots` | IBKR restates NAV inside the 14-day window, and every return figure starts from NAV | First wave |
| `position_snapshots`, `cash_snapshots`, `corporate_actions` | Rarely restated (positions and cash balances arrive once, for the statement's last day), but a month-end factsheet's holdings need them to be rebuilt exactly | Second wave, two weeks later |

The review named trades, cash and FX. NAV is added because it is the input IBKR restates
most often, and every return starts from it. FX rates are added because they convert
the cash flows. Positions and cash balances can wait two weeks: a past day's positions
are written once and almost never sent again.

Not in scope: accounts and instruments (descriptive, rewritten on every load), the derived
portfolio tables (rebuilt from the tables above), and market data (benchmark prices).
Market data could use the same mechanism later (see the open questions).

### The two dates

Every version of every row carries:

- **Happened on** (`valid_date`): the row's own business date, which the tables already
  hold. The trade date for trades, IBKR's report date for cash and FX transactions, the
  rate date for FX rates, the NAV date for NAV.
- **Known from and known until** (`recorded_from`, `recorded_to`): when that version
  reached silver, and when a newer version replaced it. An empty "until" means it is
  still the current version. Each version also records the ingestion run that brought it
  (`recorded_run_id`), which names the raw statement file and its sha256. When the job
  records lineage (U4), it also records the job run (`lineage_run_id`).

### Where they live

Each table keeps its name, columns, key and rows. Beside it, a new history table
(`silver.trades_history` and so on) holds every version: the table's own columns plus the
dates above, and how each version began and ended. A trigger on the table writes the
history whenever any job writes the table. Nothing that reads or writes these tables
today changes.

### Keys

- The current tables keep their keys.
- A version is identified by the table's key plus the time it was recorded. Only one
  version per key is current.
- **FX transactions gain one column**, `event_key`: the FX event's identity without the
  two amounts IBKR restates. It is what makes a re-sent FX row a new version instead of
  a second row (see "The FX duplicate" below). That is the only change any current
  table gets.

## How each kind of change is recorded

| What IBKR does | What the table shows (as today) | What the history adds |
|---|---|---|
| **Re-sends a row unchanged** (most rows, every day) | The same row; only "last seen by run" moves | Nothing. No new version |
| **Corrects a row under the same key** (a restated NAV, rate or amount) | The new values | The old version gets a "known until" time; the new one starts then |
| **Cancels a trade** | A new reversing row (`SELL (Ca.)`), beside the original | The cancellation as its own row, happened on the trade date, known from the pull that brought it. The original stays current, as IBKR keeps reporting it |
| **Rebooks it** | A new row under the same trade id with a later report date | The rebooking, the same way. As known before the pull, the trade is the original; as known after, the three rows net to the rebooking |
| **Re-sends an FX row with new amounts** | Today a second row; after the FX fix, the same row with new amounts | Today two separate rows; after the fix, a new version of one FX event |
| **Stops sending a row** without cancelling it | The row stays | Nothing at first. A check lists such rows, and only an approved step closes them as "withdrawn" (open question 5) |

### Worked example (made-up data)

Account U…001 sells 100 shares of SYNA on Thursday 15 Oct at 50.00, for 5,000.00. The
pull on Friday 16 Oct (08:20 UTC) brings the sale, a dividend of 12.50 from 14 Oct, an FX
conversion on 15 Oct (proceeds 128.70, realised P&L 0.70) and a NAV of 100,500.00 for
15 Oct. On Friday IBKR re-prices the sale to 49.80. The pull on Saturday 17 Oct brings:

- a cancellation of the sale (+100 shares, −5,000.00) and a rebooking at 49.80 (4,980.00),
  both dated 15 Oct;
- the FX row again, with proceeds 128.60 and realised P&L 0.60;
- 15 Oct's NAV restated to 100,480.00;
- the dividend again, unchanged.

After Saturday's pull, the history holds (times UTC):

| Table | Row | Happened on | Known from | Known until | Began as |
|---|---|---|---|---|---|
| trades | Sale, 5,000.00 | 15 Oct | 16 Oct 08:21 | (current) | first report |
| trades | Cancellation, −5,000.00 | 15 Oct | 17 Oct 08:21 | (current) | first report |
| trades | Rebooking, 4,980.00 | 15 Oct | 17 Oct 08:21 | (current) | first report |
| nav_snapshots | 15 Oct, 100,500.00 | 15 Oct | 16 Oct 08:21 | 17 Oct 08:21 | first report |
| nav_snapshots | 15 Oct, 100,480.00 | 15 Oct | 17 Oct 08:21 | (current) | restatement |
| cash_transactions | Dividend, 12.50 | 14 Oct | 16 Oct 08:21 | (current) | first report (Saturday's identical copy added nothing) |
| fx_transactions (after the FX fix) | Conversion, 128.70 | 15 Oct | 16 Oct 08:21 | 17 Oct 08:21 | first report |
| fx_transactions (after the FX fix) | Conversion, 128.60 | 15 Oct | 17 Oct 08:21 | (current) | restatement |

So:

- "Net proceeds of the 15 Oct sale, as known at noon on 16 Oct" gives 5,000.00. As known
  now, it gives 4,980.00.
- "NAV for 15 Oct, as known at noon on 16 Oct" gives 100,500.00, which is what the website
  showed that day. As known now, it gives 100,480.00.
- The current tables hold exactly what they hold today.

The database test runs this example end to end with today's loader, unchanged.

## Reading the data

### The current view stays as it is

Every job, page, agent view and export keeps reading `silver.trades`, `public.trades` and
so on, and sees exactly what it sees today. That includes the ~250 places in the code
that name `public.` tables and the `agent` views. The history only adds tables and
functions beside the current ones. A daily check proves the two agree: each table equals
its current versions in the history, row for row, apart from the "last seen by run"
column.

### As known on a date

Each table gets a function that returns it as it stood at a moment, with the same
columns:

```sql
-- NAV for 15 Oct as the website knew it at noon UTC on 16 Oct
SELECT total_text FROM silver.nav_snapshots_as_known('2026-10-16 12:00+00')
 WHERE account_id = :account AND snapshot_date = '2026-10-15';

-- Every trade for October, as known when the October factsheet was frozen
SELECT * FROM silver.trades_as_known(:frozen_at)
 WHERE account_id = :account AND trade_date BETWEEN '2026-10-01' AND '2026-10-31';
```

The functions apply the same account check as the tables, so each tenant only ever
sees its own accounts' history.

### What changed since a figure was published

Each history table can answer "what has IBKR changed for this month since we published
it":

```sql
SELECT snapshot_date, total_text, recorded_from
  FROM silver.nav_snapshots_history
 WHERE account_id = :account
   AND valid_date BETWEEN '2026-10-01' AND '2026-10-31'
   AND recorded_from > :frozen_at
 ORDER BY recorded_from;
```

### Reproducing a past month-end factsheet exactly

Each frozen month-end snapshot in `gold.reporting_snapshots` records the moment it read
silver (`as_of_at`). To rebuild it:

1. Restore a copy of the database, as for any rehearsal.
2. On the copy, replace each history-keeping table's rows with its rows as known at
   `as_of_at`, in one transaction (a short script).
3. Run the month-end snapshot build for that subject and month, on the image recorded
   for that run (U4 records the code version).
4. Compare the new payload's sha256 (`data_sha256`) with the frozen one. If they match,
   the factsheet has been rebuilt exactly. If they don't, the query above lists every
   correction that arrived after `as_of_at`.

The frozen payload stays the record of what was sent. This check proves we can still
derive it, and explains any later difference figure by figure. It covers everything the
factsheet takes from IBKR once the second wave is on. Benchmark prices are not covered
by U8, so until they keep history too, a benchmark difference is reported on its own
line.

Two refinements come with later steps. The publish gate (U2) stores, with each gold
version, the newest ingestion run it read, so the rebuild can name exactly which loads
to include even if a load was still running at that moment. The certified figures (U5)
carry the same "as known at" time on every figure.

## The FX duplicate

**Cause.** The parser keys an FX row by a hash of everything in it, proceeds and realised
P&L included. When IBKR re-sends the row with new amounts, as it did after the 15 Oct
re-pricing, the hash and so the key changes, and the loader adds a second row.

**Fix.** Each FX row gets an event key: a hash of the same fields minus the two IBKR
restated (proceeds and realised P&L). The key also carries the row's position among
identical-looking rows in the same statement, so two genuine conversions with the same
time and amount stay apart. The loader then updates the existing row for that event
instead of adding one, and the history keeps the old amounts as a closed version.
`source_key` keeps naming the bronze row of the latest version, so lineage from silver
to bronze still works.

**Existing duplicates.** The backfill replays the raw statements in order. Two FX rows
with the same event key that never appeared in the same statement are one event reported
twice. The FX step keeps the latest report as the current row and closes the older one
as "merged". Both stay in the history.

**Why not change the parser's key instead?** Bronze keeps one record per distinct raw
record, and both reports are real raw records. Changing the parser's key would re-key
bronze, which is append-only. FX rates already work this way: their key leaves out the
rate, so a corrected rate replaces the old one. The history now keeps the old rate too.

**Who sees it.** The website's NAV and returns don't read FX rows. The analyst export, the
workflow input checks and the per-run row counts do, and will count each conversion
once. The release data report shows the exact number of rows removed.

## The FX step in October

Ying's October decision (18 Oct) moves the FX step to **Thu 29 Oct**, a week after recording
goes live (Thu 22 Oct, migration `074_silver_history.sql`). The dates in "Order of work"
below are the design's; this section is what applies now.

**The setting: `IBKR_FX_EVENT_KEY`**, on the jobs that load Flex statements into the live
database (institutional-daily, where the daily 14-day pull brings the re-sends).

- **off** (unset, or anything but `1`, `true`, `yes`, `on`): today's loader, unchanged. It
  never reads or writes `event_key`, and a re-sent FX row with new amounts is a second row.
- **on** (`true`): an FX row whose event (below) already has a current row updates that row
  instead of adding one: new amounts, and `source_key` names the newest bronze record. The
  trigger keeps the old amounts as a closed version. Needs the FX migration; without its
  column the loader refuses before writing anything.

There is no fallback mode: no NAV, return, certified figure, page or agent view reads FX
rows, so no figure can change. The safety is the dry run, the history (a merged row is a
closed version; nothing is lost) and the undo below.

**The rule: when two FX rows are the same FX event.** Same account, IBKR `dateTime`, report
date, functional and FX currency, activity description, model, quantity and cost, and the
same place among rows alike in all of those within one statement. Proceeds and realised
P&L, the two amounts IBKR restated on the re-sent row, are left out. Two rows that were ever
in one statement together are never the same event. Its risks:

- a re-send that also changes an identity field (cost, report date, time, quantity) is not
  recognised and stays a second row, as today. `fx-check` counts rows alike apart from report
  date or cost, never reported together (`possible_resends_outside_the_rule`);
- an FX record sent without `reportDate` takes the statement's end date, so its re-send on
  another day is not recognised either (`fx_records_without_report_date`);
- two different conversions alike in all of the above and never in one statement together
  would be merged. That needs the same second, quantity and cost; the history keeps both;
- look-alike rows that IBKR reorders on the day it restates them can swap amounts between
  their two rows; the number of rows stays right;
- the merge keeps the latest report: v1.1's archived statements in name order (their date
  folders), then v2's in load order. A wrong pick inside the 14-day window is corrected by
  the next pull.

**Who reads `fx_transactions`.** No website page or figure. The analyst export (`SELECT *`:
one row per event after the step, plus the `event_key` column), the workflow input checks
(`workflow_financial_inputs`: row counts and fingerprints change for accounts with merged
rows; `workflow_fx_dependencies`' dated currency demands do not), reconcile's per-run counts,
the layer volume record (recorded, not judged), the release data report and these tools.
derived-daily lists it as a lineage input but reads no FX row.

**The day, in order** (outside 07:55 to 09:20 UTC; recording must be on):

1. `scripts/medallion_silver_history.py fx-check`: the counts before (read-only).
2. The FX migration by itself: `infra/medallion/drafts/u8_fx_event_key.sql` moves unchanged
   to `migrations/postgres/075_fx_event_key.sql`, then
   `bootstrap_postgres.py --only-migration 075_fx_event_key.sql`. With it,
   `silver/fx_transactions.yaml` and `silver/fx_transactions_history.yaml` gain `event_key`.
3. `fx-merge` (a dry run, counts only), then `fx-merge --apply`, which refuses while a raw
   statement is missing (`statement_files_missing`). A second `--apply` finds nothing to
   merge and writes nothing.
4. `IBKR_FX_EVENT_KEY=true` on institutional-daily.
5. After the next daily load: `fx-check` exits 0 and `check` passes.

**What the rehearsal on a restored copy must show** (Sat 24 or Sun 25, with the real raw
statements: `--storage-account-url`, plus `--v11-prefix v11-archive/<tenant>/`):

- `fx-check` before: `events_with_more_than_one_current_row` (expected: the 17 Oct
  duplicate and any like it), `events_with_more_than_one_current_row_after_merge` 0,
  `possible_resends_outside_the_rule` 0, `fx_records_without_report_date` 0, and
  `not_in_any_statement` 0 or each row explained;
- the `fx-merge` dry run's `merged` equals `fx-check`'s `rows_the_merge_removes`;
- after the merge and a load of the newest raw statement with the setting on
  (`ibkr-etl import-file <file>` against the copy, with `IBKR_FX_EVENT_KEY=true`,
  `IBKR_RUN_MIGRATIONS=false` and a local raw folder): `fx-check` exits 0, `rows` fell by
  `rows_the_merge_removes`, `history_rows_closed_as_merged` equals it, and `check` passes;
- the release data report: `fx_transactions` rows removed equal to the merge, the new
  `event_key` column, `fx_transactions_history` rows added, nothing else;
- the undo on the copy: `fx-check` shows the before counts again.

**Undo**, in order: take `IBKR_FX_EVENT_KEY` off (the loader is today's again; new re-sends
become second rows); `fx-unmerge` (dry run), then `fx-unmerge --apply`, which puts each
merged row back from its closed version and is safe to run twice; then
`u8_fx_event_key_undo.sql`, which refuses while a merged row is missing. Recording stays on
throughout, so the merge and its undo are both in the history.

## The migration

### What is built (nothing switched on)

One migration adds, for the first-wave tables:

- the history tables;
- the trigger;
- the "as known" functions;
- the setting `public.silver_history_settings.recording`, which starts off.

With the setting off, the trigger does nothing, so applying the migration changes nothing.
Like the layer migrations, it stays off `main` until its day, and it is applied by
itself (`bootstrap_postgres.py --only-migration`). The draft in
`infra/medallion/drafts/u8_silver_history.sql` is what it will look like.

### Filling the history from bronze

Every raw file is kept, so the history can be rebuilt back to the start of v1.1's
archive. The backfill reuses the replay tool built for the switch rehearsal (it becomes
S6's monthly replay).

1. **From the switch (17 Oct) on:** v2's own raw statements in `ibkr-raw`, each with its
   `bronze.raw_reports` row (sha256 and time received) and its ingestion run. The
   replay loads them in order with today's loader into a throwaway database. After each
   statement, it compares with the state before and writes down every change as a
   version, recorded at the time the real load ran. This is exact: it is the same code
   on the same files.
2. **Before the switch:** v2's silver was then a daily copy of v1.1's tables, made by
   account-sync. Each day's copy is kept in `input-snapshots/daily/<account>/<date>/`.
   Comparing each copy with the one before gives that day's versions, recorded at the
   time account-sync loaded it, which is when v2's website could first show them. v1.1's
   raw statements in `ibkr-raw/v11-archive/` are replayed alongside, so each change is
   matched to the IBKR record that caused it.
3. **Rows the files can't explain:** history older than the archive, or one-off imports.
   Each such row gets one "baseline" version, known from the earliest time we can show it
   was there. They are counted in the report.
4. **The end must equal today.** The replay's final state must equal the live tables row
   for row (S6's replay check). Any difference stops the switch-on until it is
   explained.

**Why the raw files and not `bronze.flex_rows`:** `flex_rows` keeps only the latest copy
of each record (the loader updates it in place when IBKR re-sends). The raw files are
the only complete record.

### How long it takes

Measured on this machine, on made-up statements:

- Today's loader replays into a throwaway database at about 4,300 rows a second.
- Into PostgreSQL, the daily load took 803 ms per statement (10 accounts, 14 days, about
  525 rows) without recording and 792 ms with it. **Recording adds no measurable time.**

The plan's own figures imply about 505 raw statements by 8 Nov: 481 from v1.1 (the
17 Oct NAV check) plus one a day from v2. At S3c, all 21 silver tables held 129,315
rows. Even if each statement held 1,000 rows, the replay is about half a million rows,
roughly two minutes. Downloading the files and comparing the daily copies adds a few
minutes. The live part only inserts the prepared history (well under 150,000 rows) and
checks it: under a minute, during which writes to the five tables wait and reads carry
on. So the switch-on fits easily outside the 07:55 to 09:20 UTC daily cycle. These are
estimates from synthetic data; the rehearsal gives the real numbers.

### Rehearsal on a restored copy first

As with the S3 moves: restore a copy of the live database from after the daily cycle,
then, on the copy:

1. apply the migration alone;
2. run the backfill and switch recording on;
3. run the checks:
   - each table equals its current versions;
   - versions never overlap;
   - "as known now" equals the table;
   - the month-end snapshots' sha256 are unchanged;
4. rebuild the October month-end factsheets as known at their `as_of_at`, as described
   above;
5. make the release data report;
6. delete the copy.

### What the release data report shows

`scripts/release_data_report.py` compares every silver and gold table in the catalog
before and after. The migration adds the five history tables to the catalog. For the
history step, the expected report:

- five new tables;
- every existing silver and gold table unchanged;
- the frozen month-end snapshots unchanged.

Anything else stops it. For the FX step, the expected report:

- `fx_transactions`: rows removed equal to the merged duplicates, and the new
  `event_key` column;
- `fx_transactions_history`: rows added;
- nothing else.

As always, only the count summary goes on the PR. The full report goes to Ying
privately.

### Undo

- **Recording:** one line sets `recording` back to false. The tables were never
  touched, and the history stays, unread.
- **The migration:** a reverse script drops the triggers, functions, history tables and
  the setting. Again, the current tables are untouched.
- **The FX step:** take `IBKR_FX_EVENT_KEY` off institutional-daily. A script puts back
  the merged rows from their closed versions in the history, then the column is
  dropped.

## What changes for the jobs and the contracts

### Jobs that write these tables

- **institutional-daily (the loader):** nothing changes for the history; the trigger
  records its writes. For the FX fix, it gets one new setting, `IBKR_FX_EVENT_KEY=true`,
  on an image that already has the loader change, shipped off.
- **Every other writer:** onboarding imports, the flow backfill, the replay tools,
  account-sync if it is ever restarted as the fallback, and approved repairs. Their
  writes are recorded the same way, with no change to them.
  - An approved repair that deletes rows sets a short note and a reason ("merged" or
    "withdrawn") on its session, and the history keeps both.
  - `TRUNCATE` on these tables is refused while recording is on, because it would skip
    the history.
- **Nobody writes the history directly.** The jobs' logins can read it but not write
  it. The trigger writes it with the owner's rights, and the history refuses edits
  and deletes, as bronze does.
- **One behaviour to know:** these tables have no `tenant_id`. They rely on an
  account-ownership check (`account_belongs_to_current_tenant`), and the history uses
  the same check, so a version is visible to exactly whoever can see the row. One edge
  case: today only a login that bypasses row security can write a row for an account
  missing from `silver.accounts`, and nobody can read it. If the history's owner does
  not bypass row security, such a write is refused while recording is on. The
  rehearsal confirms which login owns the tables.
- **Later migrations:** one that adds a column to one of these tables also adds it to the
  history table and re-creates the "as known" function. A test will check this, as one
  already does for the compatibility views.

### Contracts (U1)

- **Five new contracts at 1.0.0**, one per history table.
  - Grain: one row per version. Key: `version_id`.
  - Rules: one current version per key; "known until" after "known from"; current
    versions equal the table.
- **The five existing contracts:** their wording changes ("keeps every version in
  `<table>_history`"), a patch step. No column changes.
- **`fx_transactions`:**
  - the column `event_key` is added, a minor step (1.1.0);
  - the FX step makes it one row per FX event instead of one per raw record, a major
    step (2.0.0). Its readers are checked first: the analyst export, the workflow input
    and FX checks, and reconcile's row counts.
- **The catalog** gets the five history tables as silver, source `ibkr`.

## Order of work

Each step ships, and can be undone, on its own. Each live step needs a slot from the
coordinator, and runs outside the daily cycle and outside 1 to 7 Nov.

| When | Step | Passes when | Undo |
|---|---|---|---|
| Before 8 Nov (code only, all off) | Migration file (held off `main`), backfill and check tool, loader FX change behind `IBKR_FX_EVENT_KEY`, contracts, catalog, tests. The image with the loader change can ship in any release, since it changes nothing until the setting is on | CI green; database tests pass | Revert |
| Mon 9 Nov | **Rehearsal** on a restored copy: migration, backfill, checks, October factsheet rebuild, release data report | Every check passes; report as expected | Delete the copy |
| Tue 10 Nov | **Migration live**, recording off | Next cycle green; report shows five new empty tables only | Reverse script |
| Wed 11 Nov | **Backfill and recording on** live | Tables equal their current versions; next cycle green | Recording off |
| 11 to 17 Nov | **Shadow week:** daily check that tables equal their current versions; counts of versions by kind | No difference; every "deleted" explained | Recording off |
| Wed 18 Nov | **FX fix:** its own rehearsal and report, then migration B, the merge, and `IBKR_FX_EVENT_KEY=true` on institutional-daily | One row per FX event; report as expected | As above |
| Wed 25 Nov | **Second wave:** positions, cash balances, corporate actions, the same three steps | As above | As above |
| From 1 Dec | Readers start using it: the publish gate (U2) and certified figures (U5) record "as known at"; S6's monthly replay rebuilds the last month-end factsheet | November's factsheets rebuild exactly | Nothing to undo |

## Open questions for Ying

Each is answerable in a word or two. My recommendation is in brackets.

1. First wave: trades, cash, FX transactions, FX rates and NAV? **(Yes)**
2. Second wave: positions, cash balances and corporate actions, two weeks later?
   **(Yes)**
3. Rebuild the history back to v1.1's archive, or start it at switch-on? **(Rebuild)**
4. Merge the re-sent FX rows a week after recording starts? **(Yes)**
5. When IBKR stops sending a row without cancelling it: report only, or close it
   automatically? **(Report only, for now)**
6. Keep showing a cancelled trade and its cancellation on the trade list, as IBKR reports
   them? **(Yes, unchanged until S7)**
7. Keep the history for ever? **(Yes. It is small: about one row per row, plus each
   correction)**
8. Benchmark prices get the same history later? **(Yes, after the second wave)**
9. Give the history tables a `tenant_id` now? **(No. Add it to all eight IBKR tables
   together with U9)**

## Details for the builders

### The history table

The history columns come first, then the table's own columns in the table's order:

- `version_id`
- `valid_date`
- `recorded_from`, `recorded_to`
- `opened_by` (first, restated or baseline)
- `closed_by` (restated, merged, withdrawn or deleted)
- `recorded_run_id`, `lineage_run_id`
- `opened_note`, `closed_note` (from the session's `silver_history.note`, for approved
  steps)

Keeping the table's columns last means a column added later to both tables stays
aligned.

Indexes:

- one unique index on the key where `recorded_to` is empty;
- the key plus `recorded_from`;
- the account plus `valid_date`.

Security is the same as the table's: row-level security, forced, with
`account_isolation`.

### The trigger (`silver.record_version`)

An `AFTER INSERT OR UPDATE OR DELETE` row trigger. It runs as the owner with a fixed
search path, and does nothing unless the setting is on.

- **An update that changes only `last_seen_run_id`:** nothing.
- **Any other update:** it closes the current version as "restated" and opens a new one.
- **An insert:** it opens a version.
- **A delete:** it closes the version as "deleted", or as "merged" or "withdrawn" when the
  session sets `silver_history.reason`.

`recorded_from` is the loading transaction's start time, so a whole statement shares one
time. The backfill inserts its versions directly, as the owner, with the files' times.

### Checks (kept with U7's quality checks once on)

- Each table equals its current versions, apart from `last_seen_run_id`.
- At most one current version per key.
- No version starts before the previous one for the same key ended.
- Every version closed as deleted, merged or withdrawn carries a closing note.

### Alternatives considered

- **History as the main table, with today's tables as views over it.** Rejected for now:
  the loader's "insert or update" cannot write through such a view, so every writer
  would change at once. This design can move there later, once only the loader writes
  these tables.
- **A temporal extension:** not needed, since plain PostgreSQL 16 does it, and nothing
  new has to be allowed on the Azure server.
- **Putting the two dates on the current tables:** this changes every reader's columns
  (`SELECT *` exports, the compatibility and agent views) for no gain.
- **A lakehouse with time travel:** not planned, per the review.

### The proof

`tests/test_as_known_on_draft_db.py` builds a database with every migration, applies the
draft as the owner, and loads made-up statements with today's loader. It shows:

- recording is off by default;
- the worked example above: versions, the "as known" queries, and the FX merge;
- the history is account-scoped, append-only, readable but not writable by a job login,
  and still written when that login updates a table;
- `TRUNCATE` is refused while recording.

It runs only when `AGENT_TEST_PG_ADMIN_DSN` is set, as the other database tests do.

### Verified and inferred

**Verified,** from the code at `c07c43d`:

- the loader's upserts;
- the parser's FX content key;
- the bronze exception for `flex_rows` (migration 069);
- the account-ownership policies (001);
- `as_of_at` on frozen snapshots;
- account-sync keeping a dated daily copy per account;
- the trigger's cost, measured on synthetic statements.

**From the 17 Oct rehearsal record:** IBKR cancels with a reversing `SELL (Ca.)` row that
has no trade id, rebooks under the same trade id with a later report date, and restates
only proceeds and realised P&L on the re-sent FX row.

**Inferred, to confirm in the rehearsal:**

- the statement counts and sizes behind the time estimates;
- how far back `input-snapshots` goes;
- that v1.1's file names carry their receipt time (checked against v1.1's own ingestion
  runs where the final dump has them).

**Worth checking separately:** the onboarding recipe asks new clients' queries to leave
out cancelled trades. If IBKR then simply stops sending a cancelled original, silver
keeps it today, beside the rebooking. The "stopped sending" check (open question 5)
finds this case.
