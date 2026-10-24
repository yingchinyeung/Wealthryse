# Self-service account onboarding

A new account (IBKR, Kiwoom, a Kiwoom U.S. account) goes from its broker login to live on the
website, with no backend run. The owner makes three decisions on the case page; the platform does
the rest. Behind `ONBOARDING_V2_ENABLED`, as the rest of the new onboarding.

The design and the list of backend steps it replaces are in the Claude Doc "Self-service account
onboarding: what was manual and the new design" (24 Oct 2026). Phase 1 is this page set and the
onboarding worker; phase 2 adds IBKR's daily binding and history (below).

## What happens where

| Step | Before | Now |
|---|---|---|
| Register the platform's address with the broker | Asked in chat | Shown at the top of **Broker connections**, with a copy button |
| Save the keys | Website form, then a check the next morning | Same form; the keys still go only to Key Vault. Each connection shows its secret's name, fingerprint, when it was saved and its last check |
| First Kiwoom check | Next 08:20 UTC daily run | **Check now** on the connection, or the worker within 20 minutes |
| Start the case | One account at a time | Up to three open cases. The start form asks for the benchmarks and, for Kiwoom, whether to read the U.S. account too; both are part of the account terms (approval 1) and can change until then |
| Stage 2 checks | Daily run, or a temporary job | The worker, for that case only (Kiwoom); the reconciliation agent's result is recorded by the worker (IBKR) |
| History | Temporary history job and stored-data probes | The worker fills up to 250 past days a run; the case page shows the days loaded, left and not reconciling |
| Names and sectors (Yahoo) | Sector job by hand | The worker, once the owner has approved sending ticker symbols, at most once a day per case |
| Factsheet design | Designer, or a backend submission of a built-in design | Designer, or **Use the built-in … design** on the case page when the platform ships the owner's exact template |
| Old drafts | Archived from the backend | **Archive draft** on the case page (unsubmitted drafts only; versions are kept) |
| Historical reports | Month-end job, or a render run by hand | The worker renders them as soon as the history is built, then at most every six hours |
| Agent review | Recorded by the month-end job | Recorded by the worker on its next run |
| Benchmarks | Added from the backend | Picked in the account terms; written to `portfolio_benchmark_mapping` at approval 1 |
| Viewer access | A script after go-live | Ticked on the go-live form; read access only, an existing grant is left as it is |

**Run now** on a case starts the worker at once. Every case also gets a scheduled run every
20 minutes. The **Platform work** panel shows what Run now would do, the last runs with each step's
outcome, and when the next scheduled run is.

## The onboarding worker

`ibkrv2stg-onboard-worker` (`infra/staging-onboarding-worker-job.bicep`), running
`scripts/run_onboarding_worker.py` (`src/ibkr_etl/onboarding_worker.py`) on the `ibkr-platform`
image. Each step is the function the daily and month-end jobs already call:

1. `kiwoom`: `kiwoom_collector.run` for that case only (`case_ids`), every page stored in
   `bronze.kiwoom_responses` first and silver computed from bronze's copy (the job sets
   `KIWOOM_BRONZE`, `KIWOOM_BRONZE_REQUIRED` and `KIWOOM_SILVER_FROM_BRONZE`, and the script refuses
   to collect without all three). A connection still waiting for its first check is checked too.
   Called while the case's stage still needs Kiwoom; once the history is built the daily job keeps
   it fresh.
2. `reconcile`: `onboarding_agent_reconcile.record_agent_reconciliations` (IBKR stage 2).
3. `design`: `builtin_design_submission.submit_built_in_design`, only when the owner asked.
4. `classify`: `sector_classification.refresh_sector_classifications_range` for the case's subject.
5. `render`: `designed_reports.run_designed_report_cases` for the cases whose history is built.
6. `review`: `onboarding_agent_review.record_agent_reviews`.

One step's failure never stops the others; it is recorded on the run and shown on the case page,
and the job exits 1 so its failed-execution alert shows it.

**Timing.** Nothing runs during the daily chain (07:20–09:45 UTC); a request made then waits for
the first run after it. Kiwoom's past days are asked for only outside its rebuild hours
(16:00–18:00 and 19:00–22:00 UTC); the first load and checks still run then. Two runs never
overlap (a Postgres advisory lock); a second one exits at once.

**Layers.** A loader job (Medallion step 3): the loader login (`database-loader-url`), listed in
`silver_writers.LOADER_JOBS`, tagged `layer: data`, `component: onboarding`, `owner:
portfolio-platform`, `hop: ingest+conform`. It writes silver only through the collector's and the
sector job's own code, and gold through the designed-report render. Its run records are ops
(`public.onboarding_worker_runs`, migration 085). The website reads only those records and the
case tables.

**Identity.** The institutional daily job's identity, which already reads the Kiwoom keys in the
Flex token vault and the loader login. The job's Bicep adds one role: Storage Blob Data
Contributor on the `internal-reports` container only, for the reports' review copies.

**Website.** The web identity may start this job and no other new one (Jobs Operator on the job,
`infra/staging-action-rbac.bicep`). `ONBOARDING_WORKER_JOB_NAME` names it
(`infra/staging-web.bicep`). A start that Azure refuses leaves the request for the next scheduled
run.

The website writes through its approval login, which has no rights schema-wide. Bootstrap gives it
`APPROVAL_ONBOARDING_GRANTS` (`scripts/bootstrap_postgres.py`) once 085 is in. That's SELECT, INSERT and UPDATE on
`onboarding_worker_runs` for Run now and Check now. For viewer access at go-live it's SELECT on the five
`app_users` columns the form lists and SELECT and INSERT on `user_account_access`: it can add read
access and never change or remove a grant.

## Migration 085

`migrations/postgres/085_onboarding_worker.sql`: the run records table, `benchmarks_json` and
`kiwoom_include_us` on `broker_onboarding_cases` (NULL on earlier cases, whose approved terms and
digests stay as they were), and the one-case-at-a-time index dropped (the website checks three, under
an advisory lock). Apply it alone with `bootstrap_postgres.py --only-migration
085_onboarding_worker.sql`, outside 07:20–09:45 UTC, before the website or worker run this code. The
collector reads the new column through `to_jsonb`, so the daily job keeps working on a database
without it. Undo: `infra/medallion/undo/085_onboarding_worker_undo.sql` (with no more than one case
before go-live).

## Deploying

The project's PC sessions cannot build, deploy or change Azure, so these steps go into one
PowerShell line for the owner, with its approval list first:

1. Build `ibkr-platform:main-<sha>` from main on the admin VM (tests run in the build).
2. Apply migration 085 alone with one bootstrap execution, outside 07:20–09:45 UTC.
3. Create `ibkrv2stg-onboard-worker` from its Bicep, with the institutional daily job's identity, the
   Flex token vault, the loader login and its tags; this adds the container-scoped report role.
4. Give the web identity Jobs Operator on that job only (`staging-action-rbac.bicep`), and add the
   job's failed-execution alert (`staging-additional-job-alerts.bicep`, which only adds alerts).
5. Release the website on `web-<sha>` with `ONBOARDING_WORKER_JOB_NAME`.
6. Record the moves in `deploy/deployed-images.json` and 085 in `deploy/applied-migrations.json`.

Undo: stop and delete the worker job (or set `kiwoomEnabled` false and the schedule off), the
website back on its earlier revision, 085's undo SQL.

## Still manual

- At the broker: creating the API keys or Flex token and registering the platform's address.
- A new broker or market: engineering work.
- The owner's three approvals, and the Yahoo consent.

## Phase 2 (next)

IBKR: the daily Flex binding for a new subject at go-live (`subject_flex_source_bindings`, so
`load_active_flex_bindings` loads it), a `history_built` check for IBKR cases, a Flex backfill of
one request per run with no retry, and warnings when rates or FX for the history are missing.
