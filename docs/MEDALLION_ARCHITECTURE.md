# Medallion architecture

The platform's data is organised in three layers, with a control plane beside them.
`infra/medallion/catalog.json` records the layer of every database table and view,
blob container and job. `tests/test_medallion_catalog.py` fails when a migration adds a
table or view, or Bicep adds a container, without a catalog entry.

Status: the catalog is in place. Nothing has moved yet, and every table is still in
`public`. The moves happen in stages, each approved separately (see "Stages" below).

## Layers

| Layer | Holds | Rule |
|---|---|---|
| Bronze | IBKR Flex statements and their records, hand-off files, raw market-data responses, live TWS snapshots, uploads (onboarding files and factsheet templates) | Append only, never edited. Data received from IBKR must not be modified, and the same applies to every source. |
| Silver | Accounts, securities, trades, positions, cash, NAV, FX, parsed market data (prices, benchmarks, FX closes, risk-free rates), sectors, derived and composite portfolios | Built only from bronze and rebuildable from it. Corrections happen here and keep a link to the raw record. |
| Gold | Performance and benchmark comparison, risk snapshots, crisis results, frozen report payloads | Built only from silver. The website, renderers and agents read gold, plus the silver read contracts, and never bronze. The one exception is a page or agent reading back a file a person uploaded through it, such as a factsheet template. Each gold figure maps to an entry in the Metrics guide. |
| Ops | Runs, approvals, audit, access, onboarding workflow, configuration, agent state | Control plane, not a data layer. Stays in `public`. |
| Platform | `tenants`, `schema_migrations`, row-level security helpers | Stays in `public`. |

## Where each layer lives

- **Postgres:**
  - Target schemas are `bronze` (14 tables), `silver` (26 tables and 8 read views) and
    `gold` (8 tables).
  - `public` keeps the platform and ops objects, so only the data tables move.
  - The `agent` schema stays as it is: agent state plus account-scoped views over silver
    and gold.
- **Blob storage:**
  - Bronze: `ibkr-raw`, `onboarding-intake`, `input-snapshots` (until v1.1 retires),
    and a planned `bronze-market-data`.
  - Gold: `internal-reports` and `report-drafts`.
  - Ops: the release and audit containers, and a planned `ops-qa`.
  - Containers that database rows point to keep their names and get a layer label.
  - New containers are named `<layer>-<content>`.
- **Jobs:** the catalog records each job's hops (ingest, conform, analytics,
  quality-gate, publish, serve, ops). Job names stay, and a tag carries the hop.

## Adding a table, view or container

Add an entry to `infra/medallion/catalog.json` in the same PR:

- Postgres objects are keyed `schema.name`, with `kind`, `layer`, `target_schema` and an
  optional `note`.
- Containers are keyed `account/container`, with `layer`, `holds` and `plan`.

Choose the layer by what the object holds, not by which job writes it. A raw provider
response is bronze. The table it is parsed into is silver.

## How a table moves

Each batch is one migration:

- `ALTER TABLE public.<name> SET SCHEMA <layer>` keeps the table's data, indexes,
  constraints, row-level security policies, triggers and grants. Views bound to the
  table, such as the agent views, keep working.
- A view with the old name stays in `public` so that existing SQL keeps working. It is
  `security_invoker`, so row-level security and grants apply to the caller.
- The runtime logins list the layer schemas first in `search_path`.
- The migration login keeps `public` first. Otherwise `db.py` would create a second,
  empty `schema_migrations` in a layer schema.
- A migration that changes a moved table names it by its layer and re-creates its
  compatibility view, because a view fixes its column list when it is created.
- A normal bootstrap applies every migration on `main`. So a batch's migration merges
  on the day the batch runs, and that day's bootstrap run applies only it
  (`--only-migration`). [MEDALLION_S3_RUNBOOK.md](MEDALLION_S3_RUNBOOK.md) has the
  steps, the checks and the undo.

## Raw market data and freshness checks

- **Raw capture** (`src/ibkr_etl/raw_capture.py`):
  - Each market-data fetch stores the provider's response, before parsing, in
    `bronze-market-data` as `<feed>/<yyyy>/<mm>/<dd>/<request-key>-<sha256 prefix>.<ext>`.
    It never overwrites a blob.
  - It is off unless the job sets `MARKET_DATA_RAW_CONTAINER` and
    `AZURE_STORAGE_ACCOUNT_URL`.
  - It is fail-soft: the first storage error, or a time budget running out, turns
    capture off for the rest of the run, and the job carries on.
- **Freshness checks** (`src/ibkr_etl/layer_freshness.py`):
  - The autonomous administrator runs explicit read-only rules for each layer and
    raises `layer_freshness:<check>` warnings.
  - It is off unless `LAYER_FRESHNESS_ENABLED=true`.
  - `python -m ibkr_etl.layer_freshness` prints the same checks for a dry run.

## Stages

1. **S0:** this catalog and test (repo only).
2. **S1:** keep all raw data. Copy the v1.1 raw statements into `ibkr-raw`, add the new
   containers and their labels, and add container-scoped storage roles next to the
   current ones.
3. **S2:** keep raw copies of market-data responses, and add freshness checks for each
   layer.
4. **S3:** move the tables in three batches (gold, bronze, silver), rehearsed first on a
   restored copy.
5. **S4:** narrow access by layer once storage logs show nothing needs the wider roles.
6. **S5:** v2 makes its own Flex pull into bronze with v1.1's query, and v1.1 stops. The
   switch is rehearsed first on a restored copy of the database, with v2's loader run over
   v1.1's archived statements and compared with what was imported.
7. **S6:** typed silver views, a replay that rebuilds silver from bronze, and promotion
   gates recorded in the workflow evidence ledger.
8. **S7:** gold tables for every Metrics guide figure, so each surface reads the same
   stored number. The factsheet design bindings (`src/ibkr_etl/factsheet_spec/bindings.py`)
   already name a Metrics guide entry for every figure a design can print, so they are the
   list of figures the factsheet surface needs from gold.
