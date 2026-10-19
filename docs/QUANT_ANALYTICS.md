# Quant analytics in gold (`gold.quant_analytics_snapshots`)

Wiring design sections 1 and 2 (`frontier-analytics/wiring-design.md`, 18 Oct 2026), as built.
Everything here is **off**: the table is a held migration that no bootstrap or job applies, and the
job does nothing unless `QUANT_ANALYTICS_BUILD=on`. Nothing changes live until the switch-on steps
below are run, in order, on their day.

| Part | File |
|---|---|
| Held migration (076 on Wed 11 Nov) and its undo | `infra/medallion/drafts/quant_analytics.sql`, `quant_analytics_undo.sql` |
| The Desk page's two silver views, a separate held migration (077) and its undo | `infra/medallion/drafts/desk_live_views.sql`, `desk_live_views_undo.sql` |
| Loaders, builders, writer, reader | `src/ibkr_etl/quant_snapshots.py` |
| The job | `scripts/build_quant_analytics.py`, `infra/staging-quant-analytics-job.bicep` (`ibkrv2stg-quant-analytics`, 09:30 UTC) |
| Contracts | `infra/medallion/contracts/gold/quant_analytics_snapshots.yaml`, `silver/subject_live_executions.yaml`, `silver/benchmark_live_quotes.yaml` |
| Tests | `tests/test_quant_snapshots.py` (no database), `tests/test_quant_snapshots_db.py` (PostgreSQL) |
| Shadow run on a restored copy (Fri 30 Oct), temporary | `scripts/quant_shadow_run.py`, `infra/staging-quant-shadow-job.bicep` (`ibkrv2stg-quant-shadow`, started by hand, deleted the same day), `tests/test_quant_shadow_run.py`, `tests/test_quant_shadow_run_db.py` |

## What the table holds

One row per tenant, subject, NAV date, **section** and analytics version
(`quant-analytics-v1`), so a section that cannot be computed never holds back the others.

| Column | What it is |
|---|---|
| tenant_id | Row security filters on it (`tenant_isolation`, forced), as on every gold table |
| subject_id | An IBKR account number, or a portfolio or composite slug |
| as_of_date | The NAV date the section is as of (`yyyy-MM-dd`) |
| section | One of the nine below (a CHECK) |
| analytics_version | `quant_snapshots.QUANT_ANALYTICS_VERSION`; part of the key |
| model_id, model_version | The model card (`ibkr_etl.model_cards`) and its engine's `MODEL_VERSION` |
| source_data_sha256 | sha256 of the section's canonical inputs and the versions; an unchanged one is not rebuilt |
| payload_sha256, payload_json | The section payload and the sha256 of its canonical JSON |
| quality_status | `pass` (every engine call worked), `warn` (the section or one call is unavailable, or a number that was not finite was stored as null), `fail` (never shown) |
| generated_at, duration_ms, timings_json | When it was written, how long the section took, and the load, prepare and build times |
| lineage_run_id | The recorded job run that wrote the row (071's trigger) |

Every payload has the same keys: `section`, `model_id`, `model_version`, `as_of`, `available`,
`reason` (why it is unavailable, else null), `inputs` (a short summary: observations, window,
holdings count) and `results`. `results` holds each engine's own output, unchanged, under the name
of the call, so page code reads the fields each engine documents. A call that fails with the
engine's `ModellingInputError` is stored as `{"available": false, "reason": "<its message>"}` under
its name; the section is available when at least one of its calls worked.

The table is created in public and moved into gold with a `security_invoker` view of the same name
left in public, as 073 did. The reader, `quant_snapshots.read_section(connection, *, tenant_id,
subject_id, section, as_of=None)`, takes the latest row of this analytics version on or before the
date asked for whose quality is not `fail`. A day that was held back or failed therefore shows the
day before.

## The sections

Inputs are read from silver and gold only, never bronze, with the same reads the risk snapshot and
the Modelling page use today. Money is in the subject's reporting currency, converted with
`reference_fx_rates` (a rate at most four days old); holding returns use each holding's own
daily marks.

| Section | Page, card (model_id) | Engine calls (`results` keys) | Inputs |
|---|---|---|---|
| `skill` | Performance, skill-statistics | `skill_summary`: daily portfolio against the primary benchmark, the risk-free rate as a daily rate, one trial, level 0.90, 2000 bootstrap samples (seeded, so a rerun gives the same answer) | `performance_comparison_daily` on the primary benchmark (`benchmark-comparison-v1`, pass or warn), the latest USD `risk_free_rates` |
| `decisions` | Performance, decision-attribution | `trade_value_added` (5, 21 and 63 sessions), `skill_profile` (21 sessions), `timing_cost` (decision price = the prior close), `counterfactual_portfolios` (no trades since 1 Jan, and the benchmark) | The last year of `trades` (physical accounts), the daily marks and holdings from `subject_position_series`, `subject_nav_series`, external flows |
| `currency` | Performance, attribution | `currency_attribution` for month to date and year to date (`mtd`, `ytd`), opening holdings grouped by currency, cash and the benchmark as their own segments | Holdings at the period start, marks and FX to the date, NAV, the benchmark levels |
| `tail` | Risk, tail-risk | `evt_var_es` (0.99, 0.995), `tail_backtest_report` (0.975, 0.99), and `forecasts`: today's one-day VaR and ES, stored so later calibration can read the history | The daily portfolio returns (EVT needs about 300, the backtest 500) |
| `regime` | Risk, stress-engine | `regime_model` (risk_forecasting's state probabilities, summarised) and `regime_risk` | Up to 1260 daily portfolio returns (at least 126) |
| `holdings_risk` | Risk, holdings-risk | `ledoit_wolf_covariance`, `ex_ante_risk` (contributions by holding; they add up to the total), `statistical_factors` (3) | Weights at the latest snapshot on or before the date (reporting value over NAV); 252 daily returns per holding |
| `correlation` | Risk, dynamic-correlation | `dcc_correlation` on the five largest holdings (10 pairs) | As holdings_risk (DCC needs 250 observations) |
| `concentration` | Risk, crowding | `concentration` by holding and by sector, `correlation_clusters` (threshold 0.7) | As holdings_risk, plus `instrument_classifications` (classified, pass or warn) |
| `execution` | Modelling, execution-analysis | `implementation_shortfall` per order (fills of one instrument, side and day; decision = arrival = the prior close, final = the day's close), `by_month`, and `skipped` (orders it could not price) | The last year's trades, at most the 400 latest orders; the daily marks |

Who is covered: physical accounts and derived (non-profiled) subjects. The Alpha World MS and T
and profiled composites report through their own HKD pipelines, so their nine sections are stored
as unavailable with that reason. Trades are stored for physical accounts only, so a derived
subject's `decisions` and `execution` are unavailable, with that reason. A subject with no primary
benchmark comparison has `skill`, `tail` and `regime` unavailable.

## The job

`scripts/build_quant_analytics.py` (job `ibkrv2stg-quant-analytics`) at **09:30 UTC**, after the
risk snapshots (08:55) and crisis step (09:20), and over by 09:40, before the second certified run
(09:45).

- **Off by default.** Unless `QUANT_ANALYTICS_BUILD=on` it prints `{"status": "off", ...}` and
  exits 0 without connecting or recording a lineage run.
- **What it builds.** The subjects the risk snapshot job builds (active, benchmark refresh
  registered, with NAV), each for its latest NAV date. `--subject ID` (repeatable) names the
  subjects instead; `--since YYYY-MM-DD` adds every NAV date from that day (a bounded backfill).
  The newest dates go first.
- **One subject and date at a time.** Each is built under its own advisory lock (a date another
  worker holds is left to it and reported as blocked) and in **one transaction**: the nine
  sections are written, the publish gate is called, then the commit. A reader never sees a
  half-written day. With `PUBLISH_GATE_MODE=enforce`, a failing day is rolled back whole and the
  job exits 65 at the end; readers keep the day before (`holds_back`). An unchanged section (same
  fingerprint, not failed) is skipped, so a rerun writes nothing.
- **Time budget.** It starts no new subject-date once `QUANT_ANALYTICS_MINUTES` (default 8) are
  spent, and lists what it skipped (`skipped_for_time`); the next run picks them up. The Bicep
  `replicaTimeout` is 600 seconds, so a run started at 09:30 is stopped by 09:40 at the latest.
- **Quality.** After writing it runs the table's own contract rules (from
  `publish_gate_rules.json`, generated from the contract) on the dates it built and reports each
  rule's count. A rule of severity error that finds rows makes the exit 1. These checks run in this
  job only: the job is **not** in `certified_build.GATE_JOBS`, so a failure here never holds the
  certified build for other pages.
- **Output.** One JSON line per subject-date it rebuilt (each section's status and build time),
  then a summary: status (`success`, `budget_spent`, `blocked`, `failed`, `held_back`), counts,
  `seconds_per_subject_date`, `skipped_for_time`, `blocked`, `held_back`, `failed` and
  `quality_rules`. Exit 0, 1 (a failure, a blocked date or a broken rule) or 65 (held back).
- **Lineage.** With `LINEAGE_RECORDING=true` the run is recorded under its job name with its
  inputs, and every row it writes carries the run id.

### Run time

Measured by `tests/test_quant_snapshots_db.py` on the local test server (load, build, write and
lock, for one subject and date):

| Subject | Seconds |
|---|---|
| 60 holdings, three years of daily data, a trade every other day | 2.1 |
| 8 holdings, 560 days | 0.5 |

The expensive sections, for the 60-holding subject (milliseconds): the 2000-sample bootstrap
(`skill`) 120; DCC (`correlation`) 87; EVT and its backtest (`tail`) 193; the regime model
(`regime`) 801; `holdings_risk` 258. Without a database the nine sections build in about 2
seconds (`tests/test_quant_snapshots.py`, `PerformanceTests`). At these times one run covers well
over a hundred subject-dates inside the 8 minutes; the real figure is measured on the restored
copy on Fri 30 Oct, and if it cannot fit, the schedule moves to 10:00 (Medallion review, note 3).

## Settings

| Setting | Where | Values | Default |
|---|---|---|---|
| `QUANT_ANALYTICS_BUILD` | job (Bicep `quantAnalyticsBuild`) | `off`, `on` (anything else is off) | `off` |
| `QUANT_ANALYTICS_MINUTES` | job | minutes the run may start new subject-dates in | `8` |
| `LINEAGE_RECORDING` | job (Bicep `lineageRecording`) | `true`, `false` | `true` in the Bicep |
| `PUBLISH_GATE_MODE` | job | `off`, `shadow`, `enforce` (`MEDALLION_ARCHITECTURE.md`, the publish gate) | unset (off) |
| `MEDALLION_DIR`, `IBKR_RUN_MIGRATIONS` | job | `/app/medallion`, `false` | as in the Bicep |

The website's own settings for the quant cards (`QUANT_ANALYTICS_PAGES`) and the Desk page
(`DESK_PAGE`) belong to those changes; they read this table and the Desk views.

## Grants and registration

The migration grants nothing itself. `bootstrap_postgres.py`, which runs it, gives every login the
same rights as on the comparable gold table (`risk_analytics_snapshots`), on the gold table and on
its public view: the job's app login SELECT, INSERT, UPDATE and DELETE (the schema-wide grants), the
website login SELECT, and the approval login SELECT on `as_of_date` only (its count and freshness
column, `layer_quality.COUNT_COLUMNS`). The Desk views get SELECT for the app and website logins, as
every silver view does.

Registered, held: `catalog.json` (the table and both views with `"held"`, and the job), the three
contracts, `layer_quality.VOLUMES` (`held=True`), `layer_freshness.RULES`
(`gold.quant_analytics_snapshots`, due 10:00 UTC, 4 days; `held=True`, so it is not checked at all
until the table exists, and **not required**, so an empty table never raises an incident while the
job is off), `publish_gate.TABLES` (lineage, holds back) and `JOB_TABLES["quant-analytics"]`, and
`lineage.TRACEABLE` and `JOB_INPUTS`. Checks on held objects wait for their migration, so the
administrator's checks and reports read as today until 076 runs.

## Shadow run on a restored copy (Fri 30 Oct)

Wiring design section 5. On **Fri 30 Oct from 11:00 UTC** the build runs on a restored copy of the
v2 server, never the live server and never through the bootstrap job. It does three things:

1. It shows that the held migrations, 076 (`quant_analytics.sql`) and 077 (`desk_live_views.sql`),
   apply cleanly on real data.
2. It measures the daily job's run time. The job must fit 09:30 to 09:40 UTC; if it cannot, it
   moves to 10:00 (Medallion review, note 3).
3. It gives a review file with counts only: subjects appear as `u01`, `u02`, ... and there are no
   client figures (note 5).

The copy is deleted the same day. Medallion has priority on the admin VM that day, so the
temporary job is the main path and the admin VM is the fallback. Nothing here runs between 07:50
and 09:25 UTC.

**Can a job reach the copy? Yes.**

- The jobs run in `ibkrv2stg-env`, on the container-apps subnet `10.50.0.0/23` of `ibkrv2stg-vnet`.
- Copies are restored into the `upgrade-recovery` subnet (`10.50.11.0/27`), as
  `ibkrv2stg-upgrade-recovery-1009` was (`scripts/upgrade_fresh_recovery_20261009.py`).
- That subnet's NSG, `ibkrv2stg-upgrade-recovery-nsg`, allows port 5432 from `10.50.0.0/23` (the
  jobs), from `10.50.9.0/27` (the admin VM) and from itself, and denies all other inbound traffic.
- A restored copy keeps the source's private DNS zone, which is linked to the VNet, so its name
  resolves from the jobs.
- Container Apps executions in this environment reached the recovery copy at 10.50.11.4 on 10 Oct.
  `scripts/control_upgrade_g04_preflight_20261010.py` ran
  `scripts/probe_upgrade_g04_preflight_20261010.py` as a job execution, and the probe checked that
  the copy's name resolved inside `10.50.11.0/27`.
- Medallion's own rehearsals (`MEDALLION_RELEASE_REPORT.md`) ran from the admin VM. That remains
  the fallback below.

### The tool: `scripts/quant_shadow_run.py`

```
python scripts/quant_shadow_run.py preflight  --copy-host <copy FQDN> [--copy-subnet 10.50.11.0/27]
python scripts/quant_shadow_run.py apply-held --copy-host ...
python scripts/quant_shadow_run.py run        --copy-host ... [--max-minutes 180] [--history-days 365]
python scripts/quant_shadow_run.py review     --copy-host ... --out DIR
python scripts/quant_shadow_run.py undo       --copy-host ...
```

**The guard** comes first in every command, before any connection. It reads the URL in
`DATABASE_ADMIN_URL` (or the variable named by `--url-env`). It refuses, with exit 2 and
`Refused: ... Nothing was connected.`, when:

- the URL does not name exactly one host, or the host is not `--copy-host`;
- the host is a live server: `ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com`, the alias
  `ibkrv2stg.postgres.database.azure.com`, or any host in `LIVE_DATABASE_HOSTS` (the server name
  is compared too, so another domain for the live server is also refused);
- the server name contains none of `-recovery-`, `-restore-` or `-shadow-`;
- with `--copy-subnet`, the name resolves to an address outside that subnet;
- the URL carries `hostaddr` or `service` (or `PGHOSTADDR` or `PGSERVICE` is set), or the tool
  runs inside the bootstrap job.

When the guard passes, it prints `Restored copy: host ..., database ...` and only then connects.

**The commands:**

| Command | What it does |
|---|---|
| `preflight` | Read-only. Prints the server version and whether the 076 and 077 objects exist. It also prints the subjects by type and lifecycle, the latest NAV date of each subject the daily job builds (masked), and row counts of the inputs the build reads. |
| `apply-held` | Applies `quant_analytics.sql`, then `desk_live_views.sql`, from `infra/medallion/drafts`, each in one transaction. Each one is recorded, with its sha256 and time, in `quant_shadow.draft_events` on the copy. It never touches `schema_migrations`, `migrations/postgres` or the bootstrap job. A draft whose objects are all there already is left alone, so running it again changes nothing. A half-applied draft is refused ("run undo first"). |
| `run` | First, the daily job's pass. The same subjects, order, lock, transaction and `QUANT_ANALYTICS_MINUTES` budget as `scripts/build_quant_analytics.py`, then the contract rules on those dates, timed from the first connection. Then the past year of NAV dates of each subject, newest first. A subject-date that already has all nine sections is skipped, so a second run carries on where a spent `--max-minutes` stopped, and retries a failed section. The build setting is forced on in this process only, and the publish gate is off. Progress lines name subjects as `u01`, ... The run log is kept in `quant_shadow.passes` and `quant_shadow.builds` on the copy. |
| `review --out DIR` | Writes `review.md` and `review.json` (mode 600), and prints both between `QUANT_SHADOW_REVIEW md BEGIN sha256=... lines=N` and `QUANT_SHADOW_REVIEW md END` (the same for `json`), one numbered line each, so they can be rebuilt from the job's logs. |
| `undo` | Runs the two undo drafts and clears the run log, for a second rehearsal on the same copy. |

Exit codes: 0 done; 1 something failed (a draft, a subject-date or a section); 2 refused or could
not connect; 3 `run` stopped at `--max-minutes` with dates left (run it again to carry on).

**The review** holds counts, names, dates and timings only:

- rows by section and quality, and unavailable sections;
- the unavailable reasons, grouped, with counts. Dates become `<date>` and numbers `<n>`;
- per-subject coverage;
- the input row counts;
- the nine contract rules on every built date;
- the 077 views' row counts;
- each draft's outcome;
- the errors, masked and cleaned the same way.

Subjects are `u01`, `u02`, ... in sorted order. Any other account-like id in a message becomes
`u??`. The mapping is never written anywhere; the real ids exist only in the run log on the copy,
and they go when the copy is deleted.

**Timing and the window.** These come from cold builds only (every section built):

- seconds per subject-date (p50, p95, max), overall and for the daily job's pass;
- the load time;
- each section's milliseconds.

The daily job's pass fits when it skipped nothing for time and took at most 600 s. That is, when
09:30:00 plus its total is 09:40:00 or earlier. The review gives the end time and the seconds to
spare. The container's start comes on top of that total, so read a small margin as not fitting.
If it does not fit, the review says the job moves to 10:00. The job runs at the daily job's size
(0.5 CPU, 1 GiB) and budget (8 minutes), so the timing is the job's. On the admin VM it is only an
estimate: the review names the runner and its CPU count.

### On the day: one PowerShell line

**Approve once, before the line runs.** It does the following, in this order:

1. **Checks the time.** It refuses to start outside 11:00 to 20:00 UTC. A run takes at most 4
   hours, so nothing runs between 07:50 and 09:25 UTC.
2. **Checks the identity.** It confirms that `ibkrv2stg-ingestion-identity` can already read
   secrets in `ibkrv2stg-auhecwc4itaoo`. It assigns no role.
3. **Restores the copy.** It makes a point-in-time restore of `ibkrv2stg-pg-auhecwc4itaoo` from 5
   minutes earlier (after the daily cycle and the 09:45 certified run). The copy is
   `ibkrv2stg-quant-shadow-1030`, in the `upgrade-recovery` subnet with the source's private DNS
   zone. The live server is only read from.
   - **Reusing Medallion's copy of that day instead:** set `$copy` to its name and `$ours=$false`.
     Do this only if they agree and have finished with it, since our run adds the 076 and 077
     objects and the `quant_shadow` schema. The line then neither restores the copy nor deletes it,
     but it does give the copy a new admin password (step 4).
4. **Sets a new admin password on the copy only.** It is random and never printed, so the live
   admin password is never copied anywhere.
5. **Writes the Key Vault secret.** It puts the copy's admin URL in `quant-shadow-copy-admin-url`.
6. **Builds and pushes the image** `ibkr-platform:quant-shadow-<commit>` with ACR build, from a
   clean `git worktree` of the current commit, so only tracked files are uploaded.
7. **Deploys the job.** It deploys `infra/staging-quant-shadow-job.bicep` as
   `ibkrv2stg-quant-shadow`: manual trigger only, no schedule and no role assignments. The job
   runs `preflight`, `apply-held`, `run` and `review` against the copy with
   `--copy-subnet 10.50.11.0/27`.
8. **Starts one execution and waits** for it, for up to 4 hours (`replicaTimeoutSeconds` 14400;
   `run` stops starting subject-dates 30 minutes before that).
9. **Fetches the review.** It reads the review lines back from Log Analytics, rebuilds both files,
   checks their sha256, and writes `quant-shadow-review-2026-10-30.md` and `.json` in the current
   folder. **Ying uploads both into the thread.** The integrator then files them under
   `frontier-analytics/shadow-2026-10-30/`.
10. **Cleans up.** It always deletes the job and the secret. The vault's purge protection keeps
    a deleted secret recoverable for 90 days, but this one holds only the copy's own password. It
    deletes the copy only when the execution succeeded and the review was fetched. Otherwise the
    copy is left for the admin VM variant and the line says so; the cleanup line below deletes
    it. **It must still be deleted the same day.**

Do not record the job in `deploy/deployed-images.json`. It is gone before Monday's drift check.

Run it in PowerShell, with `az login` done, from the root of a clone at the commit that has these
files:

```powershell
$ErrorActionPreference='Stop'; $path='job'; $copy='ibkrv2stg-quant-shadow-1030'; $ours=$true; function A { $o = & az @args; if ($LASTEXITCODE) { throw "az $($args[0..2] -join ' ') failed" }; ($o -join "`n").Trim() }; $now=(Get-Date).ToUniversalTime(); if ($now.TimeOfDay -lt [TimeSpan]'11:00' -or $now.TimeOfDay -gt [TimeSpan]'20:00') { throw 'Start between 11:00 and 20:00 UTC: never 07:50 to 09:25 UTC, and the run takes up to 4 hours.' }; if (-not (Test-Path scripts/quant_shadow_run.py)) { throw 'Run this at the root of a clone of the commit with scripts/quant_shadow_run.py.' }; $rg='rg-ibkr-database-v11'; $live='ibkrv2stg-pg-auhecwc4itaoo'; if ($copy -eq $live -or $copy -notmatch '-(recovery|restore|shadow)-') { throw "$copy is not the name of a restored copy: stop here." }; $kv='ibkrv2stg-auhecwc4itaoo'; $acr='ibkrv2stgauhecwc4itaoo'; $job='ibkrv2stg-quant-shadow'; $secret='quant-shadow-copy-admin-url'; $law='5264a124-10cd-45f6-93a5-6d65c37e9bfb'; $commit=(git rev-parse --short=12 HEAD); $tag="quant-shadow-$commit"; $src=Join-Path $env:TEMP $tag; $ok=$false; try { $identity = A identity show -g $rg -n ibkrv2stg-ingestion-identity --query id -o tsv; $principal = A identity show -g $rg -n ibkrv2stg-ingestion-identity --query principalId -o tsv; $vault = A keyvault show -n $kv --query id -o tsv; if ([int](A role assignment list --assignee $principal --scope $vault --include-inherited --query "length([?roleDefinitionName=='Key Vault Secrets User'])" -o tsv) -lt 1) { throw 'ibkrv2stg-ingestion-identity cannot read the vault: stop here.' }; if ($ours) { $subnet = (A postgres flexible-server show -g $rg -n $live --query network.delegatedSubnetResourceId -o tsv) -replace '/subnets/[^/]+$','/subnets/upgrade-recovery'; if ((A network vnet subnet show --ids $subnet --query addressPrefix -o tsv) -ne '10.50.11.0/27') { throw 'The upgrade-recovery subnet is not 10.50.11.0/27.' }; $zone = A postgres flexible-server show -g $rg -n $live --query network.privateDnsZoneArmResourceId -o tsv; Write-Host "Restoring $copy from $live (about 20 minutes)"; A postgres flexible-server restore -g $rg -n $copy --source-server $live --restore-time ($now.AddMinutes(-5).ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")) --subnet $subnet --private-dns-zone $zone -o none }; $fqdn = A postgres flexible-server show -g $rg -n $copy --query fullyQualifiedDomainName -o tsv; $login = A postgres flexible-server show -g $rg -n $copy --query administratorLogin -o tsv; if ($fqdn -notmatch '^[a-z0-9-]*-(recovery|restore|shadow)-[a-z0-9-]*\.postgres\.database\.azure\.com$' -or $fqdn -like "$live.*") { throw "$fqdn does not look like a restored copy." }; $bytes = New-Object byte[] 24; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes); $pw = 'Qs1x' + ([Convert]::ToBase64String($bytes) -replace '[+/=]','x'); A postgres flexible-server update -g $rg -n $copy --admin-password $pw -o none; A keyvault secret set --vault-name $kv -n $secret --value "postgresql://${login}:$pw@${fqdn}:5432/ibkr_staging?sslmode=require" -o none; Remove-Variable pw, bytes; if ($path -eq 'vm') { Write-Host "Copy ready: $fqdn. Secret $secret is set. Now the admin VM steps, then the cleanup line."; $ok=$true } else { git worktree add --detach $src HEAD; if ($LASTEXITCODE) { throw 'git worktree add failed.' }; & az acr build -r $acr -t "ibkr-platform:$tag" -f (Join-Path $src 'Dockerfile.azure') --platform linux/amd64 $src -o none; if ($LASTEXITCODE) { throw 'The image build failed.' }; A deployment group create -g $rg -n $tag -f (Join-Path $src 'infra/staging-quant-shadow-job.bicep') -p registryName=$acr keyVaultName=$kv identityResourceId=$identity imageTag=$tag copyUrlSecretName=$secret copyHost=$fqdn -o none; $exec = A containerapp job start -g $rg -n $job --query name -o tsv; Write-Host "Execution $exec started at $((Get-Date).ToUniversalTime().ToString('HH:mm')) UTC; waiting (up to 4 hours)"; $until = (Get-Date).AddHours(4.5); do { Start-Sleep 120; $state = & az containerapp job execution show -g $rg -n $job --job-execution-name $exec --query properties.status -o tsv } until ($state -in 'Succeeded','Failed','Stopped','Degraded' -or (Get-Date) -gt $until); Write-Host "Execution $exec $state"; $q = "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '$exec-' | where Log_s startswith 'QUANT_SHADOW_REVIEW ' | project Log_s"; $files = @{}; foreach ($try in 1..30) { Start-Sleep 60; $logs = @(((& az monitor log-analytics query -w $law --analytics-query $q --timespan P1D -o json) -join "`n" | ConvertFrom-Json) | ForEach-Object { $_.Log_s }); foreach ($kind in 'md','json') { $head = @($logs -match "^QUANT_SHADOW_REVIEW $kind BEGIN ")[-1]; if ($head -notmatch 'sha256=([0-9a-f]{64}) lines=(\d+)') { continue }; $sha = $Matches[1]; $n = [int]$Matches[2]; $body = @{}; foreach ($line in $logs) { if ($line -match "^QUANT_SHADOW_REVIEW $kind (\d{5}) ?(.*)$") { $body[[int]$Matches[1]] = $Matches[2] } }; if ($body.Count -ne $n) { continue }; $text = ((1..$n | ForEach-Object { $body[$_] }) -join "`n") + "`n"; $hash = -join ([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($text)) | ForEach-Object { $_.ToString('x2') }); if ($hash -eq $sha) { $files[$kind] = $text } }; if ($files.Count -eq 2) { break } }; if ($files.Count -ne 2) { throw "The review is not in the logs of $exec (state $state): read its console log in the portal." }; $out = Join-Path (Get-Location) 'quant-shadow-review-2026-10-30'; [IO.File]::WriteAllText("$out.md", $files['md']); [IO.File]::WriteAllText("$out.json", $files['json']); $t = ($files['json'] | ConvertFrom-Json).timing.latest_pass; Write-Host "Review: $out.md and $out.json. Upload both into the thread. Daily job pass: $($t.seconds) s, fits 09:30 to 09:40: $($t.fits_window), move to 10:00: $($t.move_to_10_00)."; $ok = $state -eq 'Succeeded' } } finally { $ErrorActionPreference='Continue'; if ($path -eq 'job') { & az containerapp job delete -g $rg -n $job --yes -o none; & az keyvault secret delete --vault-name $kv -n $secret -o none; if (Test-Path $src) { git worktree remove --force $src } }; if ($path -eq 'job' -and $ok -and $ours) { & az postgres flexible-server delete -g $rg -n $copy --yes; Write-Host "Deleted the job, the secret and the copy $copy." } else { Write-Host "Still there: the copy $copy$(if ($path -eq 'vm') { ' and the secret' }). Delete them today with the cleanup line (with Medallion, if the copy is theirs)." } }
```

**The cleanup line**, for whatever the line above left in place. It can be run again; what is
already gone only gives a "not found". Leave out the last command when the copy is Medallion's:

```powershell
az containerapp job delete -g rg-ibkr-database-v11 -n ibkrv2stg-quant-shadow --yes -o none; az keyvault secret delete --vault-name ibkrv2stg-auhecwc4itaoo -n quant-shadow-copy-admin-url -o none; az postgres flexible-server delete -g rg-ibkr-database-v11 -n ibkrv2stg-quant-shadow-1030 --yes
```

### The admin VM variant (fallback)

Use it when the execution cannot reach the copy (its `preflight` prints `Could not connect`), or
when the job path cannot be used. Run it only after Medallion has finished with the VM, and never
between 07:50 and 09:25 UTC.

1. **Make the copy and its secret.** Run the line with `$path='vm'`. It does approval steps 1 to
   5, prints the copy's host, and stops without deleting anything. If the job path already left our
   copy in place, also set `$ours=$false`, so the line keeps that copy (as it would Medallion's)
   instead of restoring another.
2. **Run the tool on the VM**, through Bastion, in a clone at the same commit, as Medallion runs
   its tools. Use the same settings as the job:

   ```
   export DATABASE_ADMIN_URL="$(az keyvault secret show --vault-name ibkrv2stg-auhecwc4itaoo -n quant-shadow-copy-admin-url --query value -o tsv)"
   export LIVE_DATABASE_HOSTS=ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com,ibkrv2stg.postgres.database.azure.com,ibkrv11-pg-5rcc62xxqblay.postgres.database.azure.com
   export IBKR_RUN_MIGRATIONS=false QUANT_ANALYTICS_MINUTES=8 PORTFOLIO_TENANT_ID=00000000-0000-0000-0000-000000000001
   c="--copy-host <the copy's host> --copy-subnet 10.50.11.0/27"
   python3 scripts/quant_shadow_run.py preflight $c && python3 scripts/quant_shadow_run.py apply-held $c && python3 scripts/quant_shadow_run.py run $c --runner admin-vm --max-minutes 210; python3 scripts/quant_shadow_run.py review $c --out ~/quant-shadow-review
   ```

   If the VM cannot read the vault, paste the copy's URL into `DATABASE_ADMIN_URL` by hand, as
   Medallion's steps do. Never paste the live server's URL; the guard would refuse it anyway.
3. **Bring back `~/quant-shadow-review/review.md` and `review.json`** as Medallion brings back its
   reports, and upload both into the thread. Then delete that folder and `unset DATABASE_ADMIN_URL`.
4. **Run the cleanup line** from the PC (with Medallion, if the copy is theirs).

The VM is not the job's 0.5 CPU, so its window verdict is an estimate. The review says
`admin-vm` and gives the CPU count. If the estimate is close to 600 s, treat it as not fitting.

**A second rehearsal on the same copy:** `undo`, then `apply-held` and `run` again. This works
from the VM, or from an execution of the job whose arguments are overridden for that execution
only. `undo` drops the 076 and 077 objects and clears the run log, so the next daily-job pass is
timed cold again.

## Switching on, in order

Each step is its own day's change. Stop at any step that does not end as described.

1. **The table (Wed 11 Nov), alone.**
   - Move `infra/medallion/drafts/quant_analytics.sql` to
     `migrations/postgres/076_quant_analytics.sql`, and in the same change drop `"held"` from the
     catalog entry `public.quant_analytics_snapshots` (`test_medallion_catalog` asks for this).
     The undo stays in drafts.
   - Take the snapshot and run the bootstrap job once, as `MEDALLION_S3_RUNBOOK.md` steps 2 and 3
     do, with `--only-migration 076_quant_analytics.sql` and `--allow-pending` naming every held
     file still pending (077 among them, if it is in `migrations/postgres` by then). It ends with
     `Migrations applied: 076_quant_analytics.sql.` A second run applies nothing.
   - Check as the runbook's step 4 does. The table is empty; the job is still not deployed.
2. **Deploy the job with the build off.** Deploy `infra/staging-quant-analytics-job.bicep` with
   `quantAnalyticsBuild=off` (the default). Its 09:30 run prints `{"status": "off", ...}` and
   exits 0, and nothing is written.
3. **Run it once by hand, build on, for one subject.** Start one execution of
   `ibkrv2stg-quant-analytics` with `QUANT_ANALYTICS_BUILD=on` and the arguments
   `scripts/build_quant_analytics.py --subject <one pilot account>` (an override for that
   execution only), outside 09:05 to 09:50.
4. **Check, then set it on.**
   - The summary: `status` `success`, every entry in `quality_rules` passed, and
     `seconds_per_subject_date` well inside the budget.
   - The rows: nine for the subject and date, none `fail`, and each unavailable section with a
     reason that is expected (`SELECT section, quality_status, payload_json->>'available',
     payload_json->>'reason' FROM gold.quant_analytics_snapshots WHERE subject_id = ... AND
     as_of_date = ...`).
   - The publish gate's read of it: `python -m ibkr_etl.publish_gate quant-analytics --since
     <today>` reports no "could not" reasons.
   - Then redeploy the Bicep with `quantAnalyticsBuild=on`. The next 09:30 run builds every
     subject's latest date. Older dates can be filled in by hand with `--since`, a few weeks at a
     time, inside the budget.
   - Once it has run for a week, the freshness rule can be made required (`layer_freshness.RULES`).
5. **The Desk views (077), on their own day.** Move `desk_live_views.sql` to
   `migrations/postgres/077_desk_live_views.sql`, drop `"held"` from its two catalog entries, and
   run it alone the same way (`--only-migration 077_desk_live_views.sql`). It needs nothing from
   076 and changes nothing existing: `agent.live_executions` and the other live views keep reading
   what they read today.

## Undoing

- Lighter: redeploy with `quantAnalyticsBuild=off`. The rows stay; the website's cards are turned
  off by their own setting.
- Whole: `quant_analytics_undo.sql` drops the table and its view (with their index, policy,
  trigger and grants), and nothing else; `desk_live_views_undo.sql` drops the two Desk views and
  nothing else. Run each as its own numbered migration with `--only-migration`, or by psql. Keep a
  copy first if the payloads may be wanted (`pg_dump -t gold.quant_analytics_snapshots`), and
  delete the 076 or 077 ledger row by hand if it should run again later. The tests apply and undo
  the two drafts in either order and find nothing left.

## Not in v1, and why

These need data that is not in silver yet (phase 3). Each needs a bronze capture and a silver
table, so each is its own change with Medallion:

- **Liquidity:** daily traded volumes.
- **Macro exposures:** macro series.
- **Factor attribution:** factor returns.
- **Crowding from 13F filings:** the filings.
- **Fair value:** dual-listing pairs.
- **Spread paid:** quotes at the time of each fill.

Also left out of v1: the Alpha World MS and T and the profiled composites (their own HKD
pipelines), and the trade sections for derived subjects (no trades are stored for them).

## Tests

- `tests/test_quant_snapshots.py`, no database: every builder on synthetic rows, with known
  answers (equal weights give a Herfindahl of 1/n, ex-ante contributions add up to the total,
  currency effects add up to the excess return, a hand-worked order's shortfall); the unavailable
  paths; fingerprint stability; the setting off doing nothing; JSON-ready payloads with finite
  numbers; and the 60-holding, three-year build time.
- `tests/test_quant_snapshots_db.py`, PostgreSQL (`AGENT_TEST_PG_ADMIN_DSN`): each held draft
  applied alone with bootstrap as its day will run it; the logins' grants and row security; the
  Desk views' rows; the loader reading back exactly what was written; the job's full build of two
  synthetic accounts, each payload equal to the pure builders' output; an unchanged rerun writing
  nothing and a one-input change rebuilding only its sections; a failing day held back whole by
  the publish gate; the reader's fallback; the time budget and the lock; lineage; and both undos in
  either order leaving no trace.
- `tests/test_quant_shadow_run.py`, no database, covers the shadow-run tool:
  - the guard refuses, before anything connects, the live server, its alias, the hosts in
    `LIVE_DATABASE_HOSTS`, a name that is not a copy's, a host other than `--copy-host`, a host
    outside the subnet, and `hostaddr` and `service`;
  - the masking;
  - the percentile and window maths on known values;
  - the review's log framing;
  - the Bicep: manual trigger only, no role assignment, and a required image tag.
- `tests/test_quant_shadow_run_db.py`, PostgreSQL, runs the shadow-run tool against the local
  server, using the harness above:
  - `preflight` changes nothing;
  - `apply-held` is idempotent and never writes to `schema_migrations`;
  - `run` with tiny budgets on a test clock carries on where it stopped, and retries a failed
    section;
  - the review: every id is masked (no `U9...` anywhere), no number from a stored payload appears,
    and the timing maths is checked on known timings;
  - `undo`, including after a half-applied or failing draft, leaves the database as it was, and a
    second rehearsal runs.
