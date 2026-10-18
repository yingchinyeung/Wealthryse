# The research zone

The research zone is where experiments run:

- point-in-time feature exports;
- backtests with walk-forward checks;
- the experiment log;
- later, model training.

It sits outside the served data path. It reads bronze and silver as they were known on each
date, writes only to its own server and containers, and nothing in the website, the agents
or gold reads it. A research result reaches people only as a gold certified figure, through
the publish gate, with its model card, its validation and the owner's approval.

The rules are Medallion's eight conditions (frontier-analytics `plan.md`). In this
repository:

- the layer and hop `research` in `infra/medallion/catalog.json`;
- the isolation test `tests/test_research_isolation.py`;
- `infra/research.bicep`, the only place the zone's resources are declared.

**Status: off.** Nothing here is deployed. With nothing switched on, no page, API, job or
login changes. The switch-on is planned for **Mon 9 Nov**.

## What lives where

| Thing | Where | Notes |
|---|---|---|
| Research server | `ibkrv2stg-research-pg`, database `research` | Postgres 16 Flexible Server, Burstable B1ms by default. Stopped when idle. Private access only, in the live server's delegated subnet and private DNS zone. Password sign-in like the live server. 7-day backups, no geo copy, no HA. |
| Run register | `research.job_runs`, `research.feature_exports` on the research server | One row per run and per exported dataset, with tenant row security. |
| Feature exports | container `research-features` | Parquet files and manifests, named `<dataset>/<as_known_on>/<subjects>/data.parquet` and `manifest.json`; `manifests/<as_known_on>/<subjects>.json` for each export. |
| Experiment log, reports | container `research-experiments` | `experiments/log.jsonl` is an append blob, one block per trial, never rewritten. `reports/<family>/<name>/report.md` and `report.json`. |
| Vendor raw data | container `bronze-vendor-data` | Bronze, append only; empty until a vendor is chosen (phase 4). |
| The job | Container Apps job `ibkrv2stg-research` | Manual runs by default. 2 vCPU and 4 GiB, a 4-hour limit, image `ibkr-research`. |
| Idle stop | Container Apps job `ibkrv2stg-research-idle-stop` | 21:15 UTC daily. Stops the server unless a run is going. |
| Identity | `ibkrv2stg-research-identity` | Used only by the two jobs. |
| Code | `src/ibkr_etl/research/`, `scripts/research/` | `feature_store.py`, `signals.py`, `blob_store.py`, plus the existing `point_in_time.py`, `backtester.py`, `validation.py` and `experiment_log.py`. |
| Image | `Dockerfile.research`, `requirements-research.lock` | The only image with scipy and pyarrow. Release steps: `scripts/research/RELEASE.md`. |

**Network choice.** The research server uses the live server's network pattern
unchanged:

- the same delegated subnet `postgres`;
- the same private DNS zone, `ibkrv2stg.postgres.database.azure.com`, which is linked to
  the VNet of the Container Apps environment;
- no public access.

The subnet has 11 usable addresses, and each server without HA uses one. A subnet of its
own would mean redeploying the VNet, which `staging.bicep` declares with its subnets inline.
If the shared subnet ever runs short, change `postgresSubnetName`.

## Who can read and write what

| Who | Reads | Writes | Never |
|---|---|---|---|
| Research identity (the job's) | `ibkr-raw`, `bronze-market-data`, `bronze-vendor-data` (Storage Blob Data Reader, on each container) | `research-features`, `research-experiments` (Storage Blob Data Contributor, on each container) | The storage account as a whole, any other container, any other secret |
| Research identity, other roles | Pulls images (AcrPull, registry). Reads its own Key Vault secrets only (Key Vault Secrets User on `research-db-url`, `research-live-reader-url` and each vendor secret named). | Starts and stops the research server (custom role, scoped to that server alone: read, start and stop) | Any other server, job or resource |
| `research_reader` (live server login) | Silver tables and views, and the U8 `silver.<table>_as_known` functions and history tables. Its own tenant only (row security). | Nothing: read-only by default, with no write grant | Ops, credential, gold, bronze and agent tables; the `public` tables |
| `research_job` (research server login) | `research.job_runs`, `research.feature_exports` | INSERT and UPDATE on `research.job_runs`; INSERT on `research.feature_exports` | DDL, other schemas, other tenants' rows |
| `research_owner` (research server role, no login) | Owns schema `research` | DDL, through the administrator | Signing in |
| Website, agents, gold jobs, every existing identity | Nothing in the research zone | Nothing | Any role on the `research-*` containers or the research server |

`infra/research.bicep` grants nothing to any existing identity. It also never touches the
storage roles Medallion narrows on Thu 22 Oct.

### The research_reader grants, exactly

`python scripts/research/live_reader_role.py --dry-run` prints the SQL. The lists live
in that file and are checked by `tests/test_research_job.py`.

- **Role attributes:**
  - `LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 3`;
  - `search_path = silver, public`;
  - `default_transaction_read_only = on`;
  - statement timeout 300 s, idle-in-transaction timeout 120 s, lock timeout 5 s.
- **Database and schemas:** `CONNECT` on `ibkr_staging`; `USAGE` on schemas `silver` and
  `public`. `public` is needed for the two row security functions only; there are no table
  grants there.
- **Row security functions:** `EXECUTE` on `public.app_tenant_id()` and
  `public.account_belongs_to_current_tenant(text)`.
- **Silver tables:** `SELECT` on the 21 silver tables (`SILVER_TABLES`):
  - `accounts`, `benchmark_prices`, `cash_snapshots`, `cash_transactions`, `corporate_actions`;
  - `derived_portfolio_cash_balances`, `derived_portfolio_cash_ledger`,
    `derived_portfolio_exclusions`, `derived_portfolio_nav`, `derived_portfolio_positions`;
  - `fx_rates`, `fx_transactions`, `instrument_classifications`, `instruments`,
    `market_data_prices`;
  - `nav_snapshots`, `position_snapshots`, `reference_fx_rates`, `risk_free_rates`,
    `synthetic_portfolio_components`, `trades`.
- **Silver views:** `SELECT` on 6 of the 8 silver views (`SILVER_VIEWS`):
  - `subject_cash_activity_series`, `subject_cash_balance_series`;
  - `subject_nav_series`, `subject_position_series`;
  - `v_daily_nav`, `v_latest_positions`.

  `subject_live_positions` and `subject_live_summary` are left out. They read the bronze
  `live_*` tables with the caller's rights, and the reader has no bronze access.
- **History (U8):**
  - `SELECT` on `silver.<t>_history` and `EXECUTE` on `silver.<t>_as_known(timestamptz)`;
  - for cash transactions, FX rates, FX transactions, NAV snapshots and trades;
  - granted only when they exist.
- **Nothing else.** `--check` prints every privilege the login has, through the
  `has_*_privilege` functions:
  - tables and views it can read or write;
  - sequences, schemas and functions;
  - security-definer functions it can reach;
  - its database rights, attributes, memberships and settings.

  It fails if anything beyond the list appears. That includes any write, any gold, bronze,
  ops or agent table, and any role membership.

Like the other logins, the reader has no `BYPASSRLS`. The job connects with
`app.tenant_id` set to `PORTFOLIO_TENANT_ID`, so it sees one tenant's rows. Research
across clients would first need an owner policy, as condition 4 says.

## What the job does

The job's entry point is `scripts/research/run_research_job.py`; `--help` lists everything.

**`export-features --as-known-on YYYY-MM-DD --subject U… [--subject …] [--dataset …]`**
writes what was known at the end of that day (UTC):

- **The five U8 tables** (cash transactions, FX rates, FX transactions, NAV snapshots,
  trades) are read with `silver.<table>_as_known(end of day)` through `research_reader`,
  in one read-only snapshot.
- **Positions, instruments and market prices** have no history. They are rebuilt by
  replaying bronze in the order it was stored, with `point_in_time.replay_order`:
  - raw Flex statements from `ibkr-raw`, counted from each blob's creation time. The v1.1
    archive under `v11-archive/` counts only from 17 Oct 2026, and only with
    `--include-v11-archive`.
  - Yahoo chart captures from `bronze-market-data`, counted from the later of creation
    time and `received_at`.
- **Output and manifests:**
  - each dataset is written as Parquet with a manifest: row count, sha256 of the rows and
    of the file, and the sources with their own sha256;
  - an export manifest carries the data fingerprint that the backtest trials record.
- **Re-runs:**
  - re-running the same export reuses identical files;
  - a different result for the same date stops with a conflict instead of overwriting it.
- **Timing:** the live server is never read between 07:50 and 09:25 UTC.
- **pyarrow** is imported only when Parquet is written or read.

**`backtest --spec spec.json`** runs a signal over every combination of its grid, using
`signals.py` (`momentum`, `low_volatility`, `mean_reversion`):

- Each trial goes through `backtester.run_backtest`, with costs and an execution lag.
- Every trial is recorded in the experiment log, including earlier runs and failed ideas.
- **Walk-forward:** the best trial on each training window is picked, and only its next
  test window counts.
- **Report:** `report.md` and `report.json`, with the deflated Sharpe ratio of the selected
  trial. The ratio uses the number of trials and the trial Sharpe variance of the whole
  family.

An example spec:

```json
{"family": "momentum-us-large", "hypothesis": "12-1 momentum beats equal weight after costs",
 "signal": "momentum", "parameters": {"skip": 21}, "grid": {"lookback": [126, 252], "top": [5, 10]},
 "prices": {"export_manifest": "manifests/2026-11-06/U9100001.json"},
 "backtest": {"rebalance": "monthly", "execution_lag": 1, "commission_bps": 5, "spread_bps": 5},
 "walk_forward": {"train": 252, "test": 63}}
```

**Server control:**

- `start-server` and `stop-server` start and stop the server.
- Every other command starts it first and stops it at the end, also on failure or at the
  time limit (SIGTERM).
- If another recorded run is still going, the server is left on for that run, which stops
  it when it ends.
- The nightly `stop-server --if-idle` catches a server that Azure restarted by itself.
  Azure restarts a stopped server after 7 days.

**`check`** confirms the switch-on worked. It checks the logins and their limits, the
reads, and one test file written to each of the two research containers.

A manual run with arguments:

```
az containerapp job start -g rg-ibkr-database-v11 -n ibkrv2stg-research --container-name research --image <the job's image> --args export-features --as-known-on 2026-11-06 --subject U…
```

## Switch-on, Mon 9 Nov

Each step below is one PowerShell line. Every step can be run again: what exists is kept.
Without `--apply`, each step prints what it would do.

**Before the day, in PRs reviewed as usual:**

- the lock's scipy and pyarrow hashes, from
  `python scripts/research/lock_research_requirements.py --fill`;
- the `deploy/website_release.py` ignore entries for the two image files (see
  `scripts/research/RELEASE.md`);
- this code merged to `main`.

Do not run steps 3 or 6 between 07:50 and 09:25 UTC.

| # | Where | PowerShell line | What it does |
|---|---|---|---|
| 1 | Ying's PC, clone of `main`, `az login` | `python scripts/research/switch_on.py secrets --apply` | Creates the 4 research secrets in Key Vault with random passwords, if missing. Prints nothing secret. |
| 2 | Ying's PC | `python scripts/research/switch_on.py deploy --apply` | Deploys `infra/research.bicep` without an image: the server, the 3 containers, the identity, its roles and the custom start/stop role. The jobs come in step 5. |
| 3 | Admin VM (Bastion), clone of `main`, `az login` | `python3 scripts/research/switch_on.py roles --apply` | Creates `research_reader` on the live server and the `research` schema, `research_owner` and `research_job` on the research server. Prints every privilege of each. Refuses inside the daily window. |
| 4 | Admin VM | `python3 scripts/research/release_research_image.py build --apply` | Builds and pushes `ibkr-research:main-<commit>` after the in-image check. Prints the digest. |
| 5 | Ying's PC | `python scripts/research/switch_on.py deploy --apply --image-digest sha256:<digest> --code-version <commit>` | Adds the two jobs, with the identity's Key Vault roles already in effect. |
| 6 | Ying's PC | `az containerapp job start -g rg-ibkr-database-v11 -n ibkrv2stg-research` | Runs `check`: logins, limits and container access. Ends by stopping the server. |
| 7 | PR | record both jobs in `deploy/deployed-images.json` | So `deploy/drift_check.py` stays clean. |

If step 2 says the custom role definition does not exist yet, wait a minute and run it
again. New role definitions take a moment to reach every region.

**After U8 goes live (backfill and recording on Wed 11 Nov),** run step 3 again so the
reader is granted the five `_as_known` functions and history tables. Until then,
`export-features` can export only the replayed datasets:

```
--dataset positions --dataset instruments --dataset market_prices
```

**Undo**, in this order. Nothing outside the research zone needs undoing.

1. Delete the two jobs.
2. Delete the research server.
3. Drop `research_reader` on the live server (`DROP OWNED BY research_reader; DROP ROLE
   research_reader;`).
4. Delete the identity's role assignments, the custom role and the identity.
5. Delete the research Key Vault secrets.
6. Delete the containers, or keep them; they hold only research files.

## Costs a month at the default size

These are approximate East Asia list prices in USD, for a month with the server on for
about 40 hours and the job running for about 20 hours. Check them with the Azure pricing
calculator before relying on them.

| Item | Running | Stopped | Month, as assumed |
|---|---|---|---|
| Research server compute, B1ms | about $0.026 an hour | nothing | about $1 |
| Server storage, 32 GiB | about $4 a month | still charged | about $4 |
| Backups, 7 days | free up to the provisioned size | | $0 |
| Research job, 2 vCPU and 4 GiB | about $0.22 an hour | nothing | about $4.50 |
| Idle stop job, about 1 minute a day | | | under $0.10 |
| Blob storage and transactions | | | under $1 |
| Logs, Key Vault reads | | | under $1 |
| **Total** | | | **about $10 to $12** |

A server left running all month would add about $19. The idle stop exists to prevent
that.

## The licence rule for vendor data

This is condition 5. Vendor data follows its licence:

1. **Check the licence first.** Before a vendor's data is captured, someone checks the
   licence for:
   - how long raw data may be kept;
   - whether it may be put under legal hold;
   - whether it may be passed on.

   Some licences forbid keeping or passing on raw data at all. The finding is recorded with
   the vendor's entry in `infra/medallion/catalog.json`.
2. **Capture it like market data.** Raw responses go to `bronze-vendor-data` the way
   market data goes to `bronze-market-data` (`src/ibkr_etl/raw_capture.py`, setting
   `MARKET_DATA_RAW_CONTAINER`):
   - content-hashed names;
   - append only;
   - each capture stored once, with its received time.
3. **Holds and retention follow the licence.** `infra/research.bicep` sets no immutability
   or legal hold. Only the separately authorised administrator sets or clears one, and only
   after the licence check. Data whose licence forbids long retention gets a lifecycle rule
   instead.
4. **One secret per vendor.** Each vendor's key is its own Key Vault secret. It is named in
   `vendorSecretNames`, so the research identity can read exactly those secrets and
   nothing else.
5. **Raw vendor data never leaves the zone.** It is never shown, exported or passed on. Only
   derived results leave, through the publish gate as certified figures, where the licence
   allows. A result that rests on licensed data says so in its model card.
6. **Market data:** the Yahoo data already captured is for internal use only
   (`dataLicense: yfinance-internal-only` on the job).
