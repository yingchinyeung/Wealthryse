# Medallion S3 runbook: moving the tables into bronze, silver and gold

Three batches, one a day, each at about 10:00 UTC, outside the daily load:

| Day | Batch | Migration | Moves |
| --- | --- | --- | --- |
| 1 | S3a gold | `068_medallion_gold_layer.sql` | 8 tables |
| 2 | S3b bronze | `069_medallion_bronze_layer.sql` | 14 tables, then the append-only lock |
| 3 | S3c silver | `070_medallion_silver_layer.sql` | 26 tables and 8 views |

`infra/medallion/catalog.json` is the list. How a move works is in
[MEDALLION_ARCHITECTURE.md](MEDALLION_ARCHITECTURE.md#how-a-table-moves).

## Before day 1

1. The code that supports the layers is on `main` and in the image the bootstrap job runs.
   It does nothing until a layer schema exists:
   - `scripts/bootstrap_postgres.py --only-migration`, and its grants and `search_path`
     for the layer schemas;
   - `scripts/install_agent_schema.py`, `src/ibkr_etl/live_collector_access.py` and
     `src/ibkr_etl/specialist_observation_access.py`, which handle moved tables;
   - `scripts/medallion_layers.py`, the snapshot and check.
2. Decide on `055_fx_evidence_versions.sql`. It references `accounts`, so it fails once
   S3c has made `accounts` a view (tested). Apply it alone before S3b, so its five
   `fx_evidence_*` tables move with bronze, or retire it. If it is applied after S3b, its
   tables stay in `public` until a new migration repeats 069's move.
3. Rehearse all three days on a restored copy of the database, with the same commands.
4. Check which jobs run migrations themselves. `initialize()` applies pending migrations
   unless `IBKR_RUN_MIGRATIONS=false`, and only the daily and history jobs set that in
   `infra/`. The Flex discovery and institutional Flex jobs reach it through
   `ingest_content` with the app login. Set the variable on them, or confirm that login
   cannot create in `public`, before a batch's migration reaches their image.
5. A login with only column grants cannot read through a view in `public`: PostgreSQL
   checks every column of a security_invoker view on the table behind it (tested). The
   approval login's freshness check names its tables unqualified, so it reads the layer
   tables, which come first in its `search_path`. Two readers name `public` and are
   refused once their tables move (tested on a rehearsal database): the specialist
   observation reader (`SET LOCAL search_path TO public`; `accounts`, `nav_snapshots`,
   `platform_subjects` and `derived_portfolio_nav` from S3c) and the metadata monitor
   (`public.<table>`; its gold tables from S3a, its silver tables from S3c, and it has no
   USAGE on the layer schemas). If either runs in production, change it to read the
   layer tables before the batch that moves them.

## Each batch day

From the admin VM, in a clone of `main` at the commit the bootstrap image was built from,
with `DATABASE_ADMIN_URL` set:

1. Merge the day's migration to `main` and build the bootstrap image. A plain bootstrap
   run would now also apply the held-back migrations, so run it only as in step 3.
2. Take the snapshot (read-only):

   ```
   python scripts/medallion_layers.py snapshot --account <pilot account> --out before-<batch>.json
   ```

   `pending_migrations` lists what is unapplied. Any `checksum_changed` entry stops the
   day, and so does any `not_in_repository` entry other than
   `067_hong_kong_china_external_market_data_approval.sql`, which was applied outside the
   repository. Row-level security applies to the admin
   login, so the counts are for the tenant given by `--tenant` (the default tenant
   unless set).
3. Run the bootstrap job once with its arguments set to

   ```
   scripts/bootstrap_postgres.py --only-migration <the day's migration>
       --allow-pending <the held-back files still pending, comma-separated>
       --allow-applied-without-file 067_hong_kong_china_external_market_data_approval.sql
   ```

   for example `--allow-pending 054_shadow_workflow_evidence.sql,055_fx_evidence_versions.sql,059_owner_codex_tasks.sql,060_shadow_workflow_date_policies.sql,061_shadow_workflow_leases.sql`.
   It refuses, changing nothing, if any other migration is pending, if an applied
   migration's checksum changed, or if an applied migration other than the one named
   has no file. It ends with
   `Migrations applied: <the day's migration>. Layer schemas: ...`. A second run applies
   nothing. The migration waits at most 20 seconds for each table's lock and then fails
   whole, so a run that meets a long transaction can simply be repeated.
4. Check (read-only; exits 1 on any problem):

   ```
   python scripts/medallion_layers.py check --before before-<batch>.json --batch <batch> --account <pilot account>
   ```

   It compares schema_migrations (one new row, nothing re-run, 062 and the stage-43
   correction included), row counts, every reporting snapshot's stored sha256 and payload
   hash, the stage-43 release state, the burn-in answers and the agent views. It also
   checks each moved table's view in `public` (columns, grants, row counts, row-level
   security under an unknown tenant) and, for bronze, the append-only triggers. The
   live tables' row counts are reported as notes, as the collector writes every few
   minutes.
5. Restart the website so its connections take the runtime logins' new `search_path`
   (layer schemas first); the scheduled jobs take it on their next run. Connections
   opened before the batch keep working through the views in `public`.

## Undo

Undo a batch with a new migration, so the ledger records it, in the reverse order of
the batches when more than one is undone:

```
python scripts/medallion_layers.py undo-sql <batch> > migrations/postgres/<next number>_medallion_<batch>_undo.sql
```

Take a snapshot, merge it and apply it as in step 3 with `--only-migration`, then run
the check with `--applied <the undo migration>` in place of `--batch`. The undo drops each view in `public`, moves the table back and drops the
empty schema; for bronze it first drops the append-only triggers. Data, grants,
policies and constraints move with each table (tested: the layout after undo equals the
layout before the batch). It refuses, changing nothing, if anything has since been built
on a view in `public`. The runtime logins keep the layer names in `search_path`, which is
harmless once the schemas are gone; `ALTER ROLE <login> RESET search_path` removes them.

In an emergency the same SQL can be run directly as the admin login in one transaction
(`psql -1 -f`). The ledger then still lists the batch, so moving the tables again needs a
new migration repeating its body.

## After S3

- A migration that changes a moved table names it by its layer (`silver.nav_snapshots`)
  and re-creates its view in `public`. `tests/test_medallion_layers_db.py` fails if a view
  and its table differ.
- A new foreign key to a moved table names the layer table: `REFERENCES accounts` fails
  once `accounts` is a view.
- Bronze refuses UPDATE, DELETE and TRUNCATE except the code's own changes (see 069). An
  approved repair needs the table owner to disable the trigger for it.
