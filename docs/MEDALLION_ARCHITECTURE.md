# Medallion architecture

The platform's data is organised in three layers, plus a control plane beside them.
`infra/medallion/catalog.json` records the layer of every database table and view,
blob container and job. `tests/test_medallion_catalog.py` fails when a migration adds a
table or view, or Bicep adds a container, without a catalog entry.

Status: the catalog is in place. Nothing has moved yet; every table is still in `public`.
The moves happen in stages, each approved separately (see "Stages" below).

## Layers

| Layer | Holds | Rule |
|---|---|---|
| Bronze | IBKR Flex statements, hand-off files, market data as fetched, live TWS snapshots, uploads | Append only, never edited. Data received from IBKR must not be modified; the same applies to every source. |
| Silver | Accounts, securities, trades, positions, cash, NAV, FX, benchmarks, sectors, derived and composite portfolios | Built only from bronze and rebuildable from it. Corrections happen here and keep a link to the raw record. |
| Gold | Performance and benchmark comparison, risk snapshots, crisis results, frozen report payloads | Built only from silver. The website, renderers and agents read gold (and silver read contracts), never bronze. |
| Ops | Runs, approvals, audit, access, onboarding workflow, configuration, agent state | Control plane, not a data layer. |
| Platform | `tenants`, `schema_migrations`, row-level security helpers | Stays in `public`. |

## Where each layer lives

- **Postgres:** target schemas `bronze`, `silver`, `gold` and `ops`. `public` keeps the
  platform objects. The `agent` schema stays as it is: agent state plus account-scoped
  views over silver and gold.
- **Blob storage:**
  - Bronze: `ibkr-raw`, `onboarding-intake`, `input-snapshots` (until v1.1 retires), and
    a planned `market-raw`.
  - Gold: `internal-reports` and `report-drafts`.
  - Ops: the release and audit containers.
  - Containers that database rows point to keep their names and get a layer label.
- **Jobs:** each job's hops (ingest, conform, analytics, quality-gate, publish, serve, ops)
  are recorded in the catalog. Job names stay; a tag carries the hop.

## Adding a table, view or container

Add an entry to `infra/medallion/catalog.json` in the same PR:

- Postgres objects are keyed `schema.name`, with `kind`, `layer`, `target_schema` and an
  optional `note`.
- Containers are keyed `account/container`, with `layer`, `holds` and `plan`.

Choose the layer by what the object holds, not by which job writes it. Raw provider
responses are bronze even when one job fetches and parses them in the same run.

## Stages

1. **M0:** this catalog and test.
2. **M1:** keep all raw IBKR data (copy the v1.1 raw statements into `ibkr-raw`).
3. **M2:** raw copies of market-data responses.
4. **M3:** move tables into layer schemas in four batches, leaving compatibility views in
   `public`:
   - The compatibility views use `security_invoker`, so row-level security still applies
     to the caller.
   - The layer schemas come first in `search_path`.
   - The bootstrap grants are extended to the layer schemas.
5. **M4:** storage roles scoped to containers instead of the whole account.
6. **M5:** v2 makes its own Flex pull into bronze and stops reading the v1.1 export.
7. **M6:** typed silver views, validated market data, and a replay that rebuilds silver
   from bronze.
8. **M7:** gold marts, so every surface reads the same stored figures.
