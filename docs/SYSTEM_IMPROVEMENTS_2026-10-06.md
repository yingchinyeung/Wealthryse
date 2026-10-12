# Approved system improvements: implementation and acceptance

Status date: 6 October 2026. This records work after the owner's approval of
all findings in [the fresh system review](SYSTEM_REVIEW_2026-10-06.md).
The earlier eight-upgrade programme remains open; urgent corrections below
do not establish completion of that programme.

## Deployed and verified in this stage

### Exchange-calendar correctness

Benchmark comparison validation now uses the same registered exchange-session
policy as the freshness monitor. Shanghai's 30 September observation is valid
during the official 1–7 October closure. The validator still rejects a missing
last open session, a fabricated closed-session observation and an uncovered
future calendar year. It does not forward-fill prices or alter return formulas.

The corrected code is deployed to the website, benchmark job and derived job.
Their existing non-image settings, authentication, schedules and zero immediate
retries were preserved. Calendar coverage currently ends in 2026; verified 2027
exchange calendars must be registered before year-end.

### Missing required portfolio metrics

Read-only diagnosis identified `crisis_maximum_drawdown` as the missing required
metric for China & Hong Kong and Greater China. Both had current NAV/positions
through 5 October but crisis replay only through 2 October. The corrected
benchmark/crisis job generated the current replay, then the two unpublished
risk caches were rebuilt. Independent registered-account analyst assurance
passed for all three active portfolios, with eleven passed checks and zero
warnings/failures per portfolio. The signed-in website showed these results
and updated without a manual reload.

This is current operational assurance, not independent acceptance of every
historical financial figure or report layout.

### Risk-cache read integrity and actionable health UI

Both website risk endpoints now reject snapshots with mismatched subject,
date, effective benchmark, methodology version, quality or canonical payload
hash, and reject non-finite values. Invalid snapshots produce an explicit
unavailable response with `Cache-Control: no-store`; the request does not launch
heavy recalculation. The source-value fingerprint remains a separate check at
build/reuse time. Payload integrity alone is not proof of current source data.

Ordinary website read connections use read-only transactions and bounded
connect, statement and lock timeouts. This does not change the separately
authorized write paths. Health messages identify the affected portfolio and
missing metric, show the last checked time, refresh every sixty seconds while
visible, and explicitly show stale status when refresh fails. Greater China's
live risk page displayed metrics, tail attribution and current crisis replay.

### Operational retirement of the cancelled agentic rollout

The cancelled daily-report job now has a Manual trigger, both report-notice
alerts are disabled, and the cancelled gateway has no ingress and zero minimum
replicas. The separately named owner trial database is verified Stopped.
Protected archives, evidence, identities and the owner's retained storage
permissions were not deleted or revoked. Ordinary portfolio services remain
operational; the six-specialist deployment has not resumed.

The initial action above was reversible operational retirement, not final
resource deletion. The eligible compute/alert deletions recorded below are now
complete; the trial database remains retained. Azure
[automatically restarts stopped PostgreSQL servers after seven days](https://learn.microsoft.com/en-us/azure/postgresql/overview).
Review the trial's retained data and dependency/evidence inventory, then complete
its permanent disposition before relying on indefinite shutdown.

### Bounded historical risk correction checks

The risk worker now checks the full values consumed by each calculation, not
only row counts and metadata. Before reusing or rebuilding a cache it verifies
scope, comparison coverage, registered exchange sessions and finite input
values. Known invalid or stale disposable caches are withheld before expensive
recomputation. A non-finite input is recorded as a validation block rather than
aborting the whole account batch with the previous cache still readable.

Existing historical caches are audited in bounded, oldest-checked-first batches
with per-account/date advisory locks, compare-and-set invalidation and retained
source-check metadata. Default limits are twenty checks and two rebuilds; explicit
rehearsal batches allow at most one hundred. Permanently blocked dates do not
starve repairable entries. At the exact first NAV date, no return observations
means insufficient history: the cache remains unavailable, but the absence is
not misreported as a failed calculation. Later missing coverage still blocks.

The 100-date repair execution `ibkrv2stg-risk-snapshots-6hylxyd` succeeded with
100 checks/rebuilds and zero pending, blocked or busy entries in that batch.
An earlier exact eleven-date repair also succeeded. These are recalculable
analytics, not published financial records; rebuilt historical quality remains
`warn` under the existing methodology. They do not establish that all 1,336
existing cache dates have been revalidated or that all historical metrics pass.

The fingerprint covers consumed materialized comparisons, positions, risk-free
and crisis data. It does not by itself propagate a raw NAV or FX correction
through every upstream producer. Other writers must join the same coordination
policy before the workflow can be called fully serialized. Missing historical
cache dates are not automatically backfilled by this audit.

### Isolated availability rehearsal

The owner approved no fixed additional monthly budget cap for HA and private
endpoints. Existing cost alerts remain; the approval is not a reason to choose
unnecessary capacity. Regional capability and public retail price evidence were
captured before provisioning. These prices are not a tenant-specific cost quote.

A point-in-time restore of the v2 database was created as the separately named
private server `ibkrv2stg-ha-recovery-20261006`, at 13:21:18 UTC on 6 October.
Using the existing restricted application login, read-only probes independently
matched schema, row-value fingerprints across eleven source/reporting tables,
and the latest NAV date (5 October) between source and restore. No administrator
credentials were placed in a job, and no producer was redirected to the clone.

Only that temporary server was resized to General Purpose `Standard_D2ds_v5`.
Its zone-redundant standby reached Healthy. An initial explicit standby-zone
request was rejected; documented automatic zone selection succeeded with no
same-zone fallback. A planned-failover rehearsal has been dispatched against
the temporary server only. Post-failover parity and recovery acceptance remain
pending until recorded below.

The post-failover restricted-login execution
`ibkrv2stg-risk-snapshots-ygep98c` Succeeded and independently matched the same
eleven table fingerprints, schema and latest NAV date. The primary changed from
zone 1 to zone 2. That confirms recovered read access and unchanged scoped data;
it does not establish continuous availability or an outage duration. At the
14:19:54 UTC control-plane check, HA was still RecreatingStandby in zone 1.
Final Healthy status and native successful failover completion are therefore
still required before HA acceptance.

At 14:22:17 UTC the temporary server was Ready, with HA Healthy, primary zone 2
and standby zone 1. Standby protection has therefore recovered and the zone
swap is independently visible. The activity-log completion event was not yet
available at that check; retain that distinction rather than infer a successful
operation event from its earlier Accepted status.

Final independent read-back at 14:23:54 UTC recorded the native restart operation
Succeeded at 14:22:44 UTC, with the same correlation ID, Ready server, Healthy
zone-redundant HA and the swapped zones. This closes the **isolated database
planned-failover qualification**, including the post-failover eleven-table
read-only parity check. The approximately nine-minute control-plane operation
duration is not the application outage duration and is not an end-to-end RTO.
The exact protected evidence is `ha-rehearsal/failover-qualified.json` and
`ha-rehearsal/after_failover-probe-verified.json` under this stage's work folder.

Both production database SKUs, HA and network configurations remain unchanged.
No storage, Key Vault or registry private-endpoint cutover has taken place.
This evidence is database/app-role recovery, not a real owner sign-in,
report-download recovery or measured end-to-end application RTO/RPO. Retain the
temporary server until evidence review, then complete its documented disposition;
its review tag is not an automatic deletion policy.

#### Private-endpoint rollout gates

Private endpoints and public-network restrictions are separate changes.
Creating an endpoint alone does not close the public path. Conversely, disabling
public access before DNS and all callers are qualified can stop normal jobs.
The next network stage must retain the existing boundary until these gates pass:

| Service | Required private-path acceptance before restriction |
| --- | --- |
| Blob storage, both versions | V1/v2 workload and administrative DNS resolution; synthetic create-only upload, pinned-version read and protected-mutation rejection; reporting/download and release-evidence connectivity; legal holds unchanged |
| Key Vaults | Every Container Apps secret-reference identity and administrative recovery path; DNS and secret-reference refresh without logging secret values; existing RBAC and purge protection unchanged |
| Registries | Supported Premium SKU qualification, both REST and image-data endpoint DNS, cold image pulls from each job/app environment, and a qualified private build/push path |
| PostgreSQL production | Supported General Purpose sizing, successful recovery/failover qualification, bounded connection recovery and an explicit application cutover/rollback window |

ACR Private Link requires Premium; the two current Basic registries cannot be
made private simply by adding an endpoint. Managed quick-build networking also
needs qualification through a supported build-agent pool or documented
restricted access design. Do not disable the public registry before proving
fresh builds and cold pulls. See [Microsoft's ACR Private Link guidance](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-private-endpoints).

First test private DNS in an isolated scope. Do not link a new private zone to
production VNets until all required service records and callers are registered.
Keep IBKR/Yahoo/Foundry outbound dependencies, Azure identity/monitoring access
and the owner's protected-evidence access in the egress design. Existing private
PostgreSQL subnets are delegated to the database service and are not generic
private-endpoint subnets. The budget approval removes the fixed spending cap,
not any connectivity, permission or data-integrity acceptance gate.

## Verified release references

| Target | Accepted immutable digest | Independent evidence |
| --- | --- | --- |
| Website | `0786615ed4c2ebc349205e3361c996dc560073eb15f753ffdbba2bdbb9bdb0ed` | ACR `ck88`; revision `ibkrv2stg-web--0000114` Healthy/Running; real owner browser acceptance |
| Benchmark worker | `e031b483982aad3ccdc402ee56345b09c3cc67314e34b12322021d2de0082443` | ACR `ck89`; execution `ibkrv2stg-benchmark-daily-wx1rm3k` Succeeded |
| Derived worker | `c23256fe2830a072558930811edafd50fff930764135379b2e2ac3a53757a64c` | ACR `ck93`; 152 Linux tests, non-root import gate and exact-cache read probe; image pin and unchanged non-image settings verified; no manual portfolio refresh |
| Risk worker current | `af434cdb3876d8c5a7e558b6b9236b1de46e3683902404e7a8d8f13a48a89583` | ACR `ck9f`; zero critical/high/unknown findings; 129 Linux tests and 3,181-case regression; exact native canary and 5,000-path shadow parity; image pin/non-image settings verified; nine withheld caches repaired |
| Risk worker previous rollback reference | `35c752fa6cbc3e93824faf4d94f22906f8ca69df7cdb85958272ee721ae3765f` | ACR `ck8e`; earlier Linux gate and read-only execution; retained for image-only rollback, not current security acceptance |

The previous risk worker checked the latest eligible dates in execution
`ibkrv2stg-risk-snapshots-gtljx4z`: Succeeded, three snapshots unchanged after
input/payload verification, zero blocked dates. This explicitly scoped current
check is separate from the remaining historical backlog.

The existing worker rebuilt two risk snapshots in execution
`ibkrv2stg-risk-snapshots-iiypu3s`. Independent assurance execution
`ibkrv2stg-analyst-assurance-fceoeji` succeeded. Permission-revocation HTTP tests
ran against the deployed Linux image in
`ibkrv2stg-risk-snapshots-a3k2tnd` and passed.

Each release has a retained exact rollback digest, protected intent and read-back
verified receipts. The earlier scoped overlays do not establish a completed
reproducible full-source pipeline, vulnerability scan or SBOM. The dedicated
risk release's later source, scan and SBOM qualification is recorded below;
full reporting-source and pipeline acceptance remain open.

## Acceptance evidence and limitations

- The changed calculation/calendar/risk/web suite passed 106 tests locally and
  in the website ACR Linux build. Worker build gates passed their applicable
  calculation/calendar tests.
- Repository policy validation passed: 293 tracked files, 831 selected source
  files and 59 contiguous migrations. This is not vulnerability scanning.
- The complete Windows regression stalled inside the restricted execution
  environment. Outside that environment it completed 2,969 discovered cases
  in 311.535 seconds, with two OS-specific skips and four launcher errors.
  Three modules could not import the repository's `scripts` package; the local
  launcher also lacked the Windows multiprocessing main-entry guard. Both
  launcher defects were corrected. A standard guarded entrypoint independently
  reran all 21 affected tests, which passed. Across these runs, coverage is
  2,986 distinct cases: 2,984 passing and two platform skips. The original failed
  run is retained; this is not described as a clean single-run full-suite pass.
- HTTP authorization/revocation fixtures now use an actual ASGI transport rather
  than handcrafted request events. Their live Linux acceptance passed; this
  does not replace the clean single-run repository release gate.
- Subsequent guarded full-suite execution passed 3,003 tests with zero failures
  or errors and two OS-specific skips. The final risk Linux gate passed its
  79 applicable tests. The earlier failed launcher/build evidence is retained,
  not rewritten as a pass. Frozen cancelled-trial fixtures were separated from
  growing repository source inventories; the frozen 847-file production trial
  guard was not weakened or retroactively requalified.
- Risk release `ck8b` failed because its test context omitted four repository
  files; it was never deployed. The corrected contexts passed their complete
  applicable gates. Final source manifest, regression log hash, live acceptance
  and exact rollback digest are protected with independent archive read-back.
- Protected release/runtime receipts and the owner-browser screenshot are in
  `.work/system-improvements-20261006/`. Receipt JSON includes its exact private
  archive object, SHA-256, version and independently verified read-back. No
  financial payload or credential is included in the progress receipts.

## Workflow and financial source qualification

The readiness policy can now evaluate separately supplied prerequisite dates.
A closed Shanghai market may legitimately require a 30 September benchmark
while account ingestion and NAV still require 5 October. A complete, typed date
policy is required; partial policies, unknown stages and future-dated completion
evidence are rejected. Dependency fingerprints still invalidate downstream
evidence when an upstream output changes. This remains an evaluation foundation,
not a production dispatcher or a persisted calendar-policy cutover.

A new read-only observer independently reconciles the existing cash-flow return
calculation using Decimal arithmetic. Physical accounts use received transfer
rows with verified source hashes and non-forward-looking IBKR FX; derived
accounts use their scoped external cash ledger. The observer checks daily
returns and cumulative growth under the existing half-period modified-Dietz
convention. It does not introduce a new formula or certify that ingestion or
derived-source lineage is complete.

Targets come from effective active account registrations and benchmark mappings,
not a fixed pilot account list. The observation uses a tenant-scoped, repeatable
READ ONLY transaction and emits only identities, dates, hashes, counts and
validation status. Empty coverage, missing source evidence and mismatched
projections cannot count as successful completion. No Flex or market-provider
request, workflow dispatch or report approval is performed.

The first Linux candidate passed its 42 applicable tests and the then-current
3,020-case full regression, but its live observation failed before producing
results: the FX query incorrectly assumed a tenant column on `fx_rates`.
That failed candidate and sanitized diagnostics are retained under
`.work/system-improvements-20261006/workflow-source-stage/`; it was not promoted
to a production job. The corrected query obtains tenant ownership through
`accounts`, with a real-schema regression test covering account isolation and
future FX exclusion. Its 43 focused local tests passed. The corrected frozen
candidate and subsequent acceptance evidence are kept separately under
`workflow-source-stage-v2/`.

The corrected Linux gate passed in ACR run `ck8g`. Read-only execution
`ibkrv2stg-risk-snapshots-kuk82ux` succeeded at 14:46:25 UTC on 6 October.
All seven effective benchmark mappings across the three active portfolios
passed both independent source-projection and cash-flow return checks, covering
2,924 return intervals. No projection discrepancies were recorded. The cutoff
was 5 October; Shanghai's expected session remained 30 September during the
holiday closure, while HSI, US and Taiwan comparisons required 5 October.
Transaction read-only status was confirmed by PostgreSQL.

The corrected full regression completed 3,021 discovered cases in 294.200
seconds with zero failures or errors and two OS-specific skips. Its 43 applicable
Linux tests passed, and repository policy checks passed for 293 tracked files,
836 selected source files and 59 contiguous migrations. Final source hashes,
regression log hash and immutable Linux build are retained in
`workflow-source-stage-v2/regression-qualified.json`. These checks do not replace
the outstanding full-source vulnerability scanning or staging release gates.

| Active portfolio | Verified benchmarks | Return intervals checked |
| --- | --- | --- |
| China & Hong Kong | HSI, SHCOMP | 826 |
| U16088571-U.S. Technology and Biotechnology | GSPC, IXIC | 856 |
| U16088606-Greater China | HSI, SHCOMP, TAIEX | 1,242 |

The accepted observer image is
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-risk-worker@sha256:9e72e7a79b9894d5f47067b9487f2e68388880bf2e70ba6795d0364e35b7e596`.
It was used only for this one-off observation. The production risk image,
non-image settings, schedule and retry policy remain unchanged. Source and
comparison fingerprints, per-mapping findings and native execution status are
protected with independent archive read-back in
`workflow-source-stage-v2/runtime-verified.json`.

Passing these arithmetic checks does not establish complete IBKR ingestion,
independent derived-sleeve construction or visual/template acceptance. Next,
wire reconciled producer evidence and its corrections into the durable workflow,
persist and version the source-specific date policy, then qualify restart,
concurrency and idempotent dispatch before any scheduler cutover. Production
HA and private-endpoint cutovers remain gated by application recovery and
connectivity acceptance.

## Date policies bound to saved workflow evidence

The shadow ledger now supports immutable, versioned date-policy records with
tenant, account, cycle, cutoff, calendar version and explicit dates for every
stage. New completion records and readiness decisions bind both the exact
policy ID and its canonical hash. Correcting the policy invalidates earlier
completions; restoring the former date values still creates a new policy ID
and requires newly bound completions. Identical current completions remain
idempotent after a restart. Late writers, mismatched scope/hash, future or stale
completions and unbound legacy completions in a dated cycle are rejected.

The existing v1 evidence format is retained for historical/shadow compatibility.
Dated evaluation requires its persisted policy and cannot override that policy
with a different in-memory date map. These records remain producer attestation,
not independent source completeness or financial approval. The trusted
calendar/producer adapter must supply the reviewed dates; this metadata layer
does not infer them from successful job status or the latest available row.

Additive migration 060 creates an append-only, tenant-isolated policy table and
adds policy references to shadow completion/decision records. All shadow
authorization flags remain false. The migration has not been installed
persistently and no production scheduler has been switched to this ledger.
Qualification uses an isolated schema and temporary restricted role, with all
DDL, grants and test rows rolled back.

The first candidate passed 71 Linux tests but failed compilation because its
non-root cache directory was unwritable. Corrected builds compile only into a
temporary cache without widening application-directory permissions. The first
native SQL rehearsal failed with `FeatureNotSupported`; the corrected private
mutation probe includes the temporary dependent tables so the append-only
trigger can be exercised beyond PostgreSQL's foreign-key guard. Both failed
records remain retained and neither candidate was promoted to production.

The final candidate passed 71 Linux tests and non-root compilation in ACR
`ck8k`. Its immutable image is
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-risk-worker@sha256:bc4315224bb6ef68cafce4148889a569a46fa4941221f25392724e3b01b7780a`.
Execution `ibkrv2stg-bootstrap-3z3xjbg` succeeded at 15:16:42 UTC on 6 October
with all 34 PostgreSQL rehearsal checks passed. These covered mixed holiday
dates, completion/decision restart idempotency, concurrent-cycle writer locking,
policy corrections and value reversions, late-writer rejection, wrong-account
and wrong-cycle rejection, cross-tenant RLS, and blocked UPDATE/DELETE/TRUNCATE
operations. The trial role had no application-table write privileges, and the
temporary schema and role were confirmed absent after rollback.

The final guarded full regression passed 3,033 discovered cases in 293.056
seconds with zero failures or errors and two OS-specific skips. Repository
policy checks passed for 293 tracked files, 840 selected source files and
60 contiguous migrations. The exact source manifest and full regression log
hash are retained in `workflow-date-stage-v3/regression-qualified.json` with
the accepted immutable Linux image. These gates do not constitute a full-source
vulnerability scan, production release or application recovery acceptance.

Final source manifests, Linux build and live trial receipts are protected with
independent archive read-back under
`.work/system-improvements-20261006/workflow-date-stage-v3/`. The original
failed build and rehearsal receipts remain in `workflow-date-stage/` and
`workflow-date-stage-v2/`. The persistent bootstrap definition, image, schedules
and retry settings were unchanged; only the diagnostic execution used the
candidate image. No source data, published report or approval was changed.

This qualifies date-policy persistence and completion fencing, not automatic
execution. Still required are real producer adapters, verified cross-subject
lineage and integration of the worker safeguards qualified below with actual work,
followed by a shadow comparison period before any production scheduler cutover.

## Worker leases and interruption recovery

The shadow workflow now has append-only worker claims, heartbeats, relinquishment
and completion events. Claims bind the tenant, account, cycle, stage, input and
methodology fingerprints, exact date policy, prerequisite completion IDs and
previous stage head. PostgreSQL wall-clock time determines expiry. A recovered
worker receives a new generation and token; the former worker cannot renew or
record a late completion. A restarted current claimant does not extend its
expiry implicitly, and recovery is limited to three claims per exact work
identity before review is required.

Completion evidence and its audit transition share a database savepoint. A
journal failure cannot leave completion evidence committed on its own. Replaying
an identical completed request after a lost response returns the same evidence
ID without another write. Changes to its inputs, policy, stage head or parent
completion IDs reject that replay, including a parent replacement with otherwise
valid observations. The first candidate's 27-check trial lacked this last case;
the corrected candidate adds the check and retains the earlier evidence.

ACR build `ck8n` passed all 84 focused Linux tests and non-root compilation.
Its immutable diagnostic image is
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-risk-worker@sha256:bbe00478b192b7c4c57b60eb02b51346b2451bf202962471cb415f88bc21f29e`.
Native execution `ibkrv2stg-bootstrap-tpnq50a` succeeded at 15:37:53 UTC on
6 October with 28 PostgreSQL checks passed. These exercised actual database
expiry, cycle locking across connections, recovery generations, late-worker
rejection, idempotent completion replay, corrections, bounded attempts, journal
failure rollback, tenant isolation and blocked event mutation. The restricted
trial role had no application-table write privileges. Independent checks
confirmed the temporary schema and role absent after rollback. Source, build
and runtime receipts have protected archive read-back under
`.work/system-improvements-20261006/workflow-lease-stage-v2/`.

The final guarded regression passed 3,046 discovered cases in 360.736 seconds,
with zero failures or errors and two OS-specific skips. Repository policy checks
passed for 293 tracked files, 844 selected source files and 61 contiguous
migrations. The exact frozen source hashes, full regression log hash and accepted
Linux image are protected in `workflow-lease-stage-v2/regression-qualified.json`.
These checks are not a vulnerability scan or full-source production release.

Migration 061 has not been installed persistently; these APIs and their event
table remain shadow-only. They permit
only reconciliation, derived calculations, risk and assurance metadata, not
Flex ingestion, external-provider fetching or report release. Worker
relinquishment closes that lease, not a global administrative cancellation.
Leases do not make external effects exactly-once or prove financial correctness.
Complete producer adapters and financial source lineage, restricted execution
identities, private output staging and fenced publication still precede actual
dispatch. Existing job templates, images, schedules and zero-retry settings
were unchanged; no IBKR source, published report or approval was altered.

## Real producer outputs and cross-account lineage observation

A new read-only observer binds the effective approved calculation definition,
its exact registered dependencies, account type, currency and definition ID to
actual stored base-currency NAV history. It rejects missing, overlapping,
modified, unapproved, inactive or cross-tenant dependencies and cycles. The
dependency-first graph and value fingerprints include account and date scope.
Changing a parent's NAV value changes the descendant lineage fingerprint even
if the row count and the descendant's own output remain unchanged. Restoring
the same calculation under a new definition ID also changes its identity.

This is an observation bridge, not a dispatcher or a new approval gate. The
runner requires an explicit non-future cutoff and a tenant-scoped PostgreSQL
READ ONLY, REPEATABLE READ transaction. It returns hashes, identities and
coverage metadata, not NAV values, and rolls back the transaction. No Flex or
market-data provider is called. Synthetic composite tests also cover native
PostgreSQL date/decimal serialization, benchmark currency mismatches and
same-count market-value corrections. Unregistered calendars fail closed.

Two Linux builds (`ck8p`, `ck8q`) failed because their isolated test contexts
omitted repository files read by existing onboarding source-structure tests.
The corrected contexts include those files without executing bootstrap or
installing migrations; no tests were removed or weakened. The third candidate
passed, but was retained without live promotion while market serialization and
currency checks were added. All failed and superseded candidate evidence remains
preserved separately.

The final ACR build `ck8s` passed 101 focused Linux tests and non-root
compilation. Its immutable diagnostic image is
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-risk-worker@sha256:75992762ea94e30062c99eba9d0cb1485130da166860bd0f876eb4f0e51f59fa`.
Native execution `ibkrv2stg-risk-snapshots-maw2x59` succeeded at 16:05:38 UTC
on 6 October. It verified the effective registry and observed coverage through
the explicit 5 October cutoff for all three active portfolios: 446 NAV
observations for U16088571, 446 for U16088606 and 444 for China & Hong Kong.
The latter's registered source-account and cash-ledger edges both resolve to
U16088606. No paused Alpha World account was activated or included.

The final guarded full regression passed 3,062 discovered cases with zero
failures or errors and two OS-specific skips. Repository policy checks passed
for 293 tracked files, 847 selected source files and 61 contiguous migrations.
The exact frozen bridge source hashes, regression log hash and accepted Linux
image are recorded in `workflow-lineage-stage-v4/regression-qualified.json`.
These checks are not a full-source vulnerability scan or financial acceptance.

Source, Linux-build and live-observation receipts have protected archive
read-back under
`.work/system-improvements-20261006/workflow-lineage-stage-v4/`.
The persistent risk-job image, environment, schedule and zero-retry settings
were checked unchanged. Only the one-off read-only diagnostic execution used
the candidate image. The protected archive helper's historical filename does
not mean a cancelled agent or gateway was restarted.

The observer explicitly reports `source_completeness_verified=false`,
`all_consumed_inputs_verified=false` and `execution_authorized=false`.
It does not yet fingerprint/reconcile every raw import, position,
classification, cash-ledger entry or FX input, independently certify derived
financial results, write workflow completions, invalidate production caches or
publish reports. Exact NAV cutoff coverage is not proof of complete ingestion.
Restricted execution identities, complete source adapters, fenced private
publication and a shadow comparison period still precede scheduler cutover.

## Stored financial input fingerprints

The read-only lineage observer now has a separate physical-input adapter for
positions, NAV, cash balances, cash activity, trades, IBKR FX rates, corporate
actions and FX transactions. It fingerprints actual stored values, referenced
instrument metadata, available parsed Flex rows and linked ingestion-run
identity/status/artifact hashes. Each query checks account ownership through
the tenant's account registry; legacy normalized tables do not gain a fictitious
tenant column. Queries have an explicit cutoff, bounded row/identity inventories
and a consistent READ ONLY transaction. Prior FX observations remain available
for non-forward-looking conversion rather than being discarded at the reporting
start date. Undated activity remains visible as an issue.

Parent input manifests now contribute to the derived portfolio's financial
lineage fingerprint. Same-count changes to positions, trades, cash, FX,
instrument metadata or ingestion provenance therefore change the descendant
fingerprint even when its currently stored NAV is unchanged. Cross-account
rows, duplicate identities, unexpected inventory and out-of-scope dates are
rejected. Missing required numerical values, unusable FX, parsed-content hash
defects and missing/unsuccessful provenance are recorded without overwriting
or inventing source values. Output contains hashes, counts and status only.

ACR build `ck8t` passed 114 focused Linux tests and non-root compilation.
Its immutable diagnostic image is
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-risk-worker@sha256:5eb38ac122c5861c6acd915aa686959c5b478c19bd897a884190c8dd1fd40593`.
Execution `ibkrv2stg-risk-snapshots-89xace7` succeeded at 16:27:13 UTC on
6 October, observing both active physical accounts and binding their manifests
to China & Hong Kong. At the explicit 5 October cutoff it fingerprinted 40,510
normalized financial rows for U16088571 and 41,466 for U16088606, across the
eight-table inventory. Their referenced instrument inventories contained 87
and 47 rows respectively; these are account-scoped counts, not a count of
distinct instruments across the whole system.

Successful observation is not successful financial validation. Each physical
account had 190 unusable stored FX observations and zero parsed Flex evidence
rows in this scoped v2 history. The adapter therefore returned
`stored_input_integrity_verified=false`. Received data was preserved, and no
claim was made that these defects were consumed by the current calculations
or explain an incorrect return. Ingestion-run hashes identify transfer/import
artifacts; they are not independently verified original IBKR XML provenance.
At that observation, reference-cache version binding and completeness of the
original query remained unverified. The raw-XML, source-completeness,
all-consumed-input and execution-authorization flags remained false. The later
scoped reference/FX evidence below advances those checks without treating that
original broad adapter result as complete financial acceptance.

The separate FX classifier's first diagnostic failed with PostgreSQL
`UndefinedFunction`: a native registry DATE was compared with a legacy TEXT
observation date. Its failed evidence is retained separately. The corrected
read-only execution `ibkrv2stg-risk-snapshots-tyhencn` succeeded at 16:33:18 UTC
on 6 October. For each physical account it classified the 190 defects as
nonpositive IDR/USD (9 rows, 20–29 January 2025), PHP/USD (131 rows,
20 January–6 July 2025) and stored `RUS`/USD (50 rows, 20 January–4 May 2025).
These currency codes were absent from the checked position, trade and
cash-activity currency fields for the account history through 5 October.
That limited usage check is not proof of every possible FX consumption or
financial impact. No currency identifier was rewritten, no value repaired
or deleted, and no provider substitute was introduced. Protected classification
and failure receipts are under `workflow-financial-defects-probe/` and
`workflow-financial-defects-probe-v2/` in this stage's work folder.

The final guarded full regression passed 3,075 discovered cases in 291.549
seconds, with zero failures or errors and two OS-specific skips. Repository
policy checks passed for 293 tracked files, 850 selected source files and 61
contiguous migrations. Source hashes, accepted Linux image, regression-log hash
and native runtime results have protected archive read-back under
`.work/system-improvements-20261006/workflow-financial-inputs-stage/`.
The persistent risk image, environment, schedule and retry settings remained
unchanged. No source data, published report or approval changed. This is a
qualified diagnostic adapter, not a production scheduler cutover, independent
financial acceptance, vulnerability scan or complete correction propagation.

## Reference-input integrity: deployed

Filtered portfolio calculations no longer silently replace missing or invalid
reference JSON with an empty mapping. The new strict reader rejects malformed,
duplicate-key, oversized, unsupported-schema and non-finite inputs. The worker
downloads a bounded, version/ETag-pinned Blob snapshot, validates the bytes
before replacing its disposable local copy and carries that exact SHA-256 into
the calculation and its run watermark. A changed local file is rejected before
the filtered calculation uses the database. An access denial, concurrent
overwrite or interrupted download cannot trigger a different-source fallback;
only an actual missing primary object permits the existing explicit legacy
source. No reference or IBKR archive is overwritten by this reader.

The actual derived-job identity read the governed legacy artifact in execution
`ibkrv2stg-derived-daily-xf8lvo4`, Succeeded at 17:14:07 UTC on 6 October.
Both the download receipt and calculation reader identified version
`2026-10-06T08:02:20.5907791Z`, ETag `"0x8DF238025BDE535"`, 107,393 bytes
and content SHA-256
`e6eafd6623821bf49f1535448b74d23036b6032337f192e5471d8a769d999a30`.
The cache contains 233 classification records and one risk-free record.
This read-only probe made zero database writes and zero provider requests.
Exact cache identity and format are verified, not historical classification
coverage or provider/financial accuracy.

ACR `ck93` passed 152 focused Linux cases and a non-root import check using the
runtime script/source paths. The guarded full Windows regression passed 3,101
cases in 426.071 seconds, with zero errors/failures and two OS-specific skips;
46 directly relevant local cases also passed outside the restrictive temporary
directory sandbox. Repository policy passed for 293 tracked files, 856 selected
source files and 61 contiguous migrations. Earlier missing-dependency/import
candidates and the unguarded multiprocessing test-launcher failure are retained
as rejected evidence, not counted as accepted releases. No test assertion or
financial formula was weakened to obtain acceptance.

The verified immutable image
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-risk-worker@sha256:c23256fe2830a072558930811edafd50fff930764135379b2e2ac3a53757a64c`
is pinned to `ibkrv2stg-derived-daily`. Its existing command, environment,
identity, schedule, resource settings and zero-immediate-retry configuration
are unchanged. The exact previous platform image remains the rollback target.
Protected qualification, live-read, promotion intent and independent pin
read-back are under `derived-reference-release-v4/` in this stage's work folder.
The scheduled portfolio calculation itself was not manually triggered: this
stage does not claim a new derived-history financial reconciliation.

## FX provenance and dated conversion dependencies: scoped acceptance

Fresh, version/ETag-pinned reads of 113 protected original IBKR XML archives
verified all 380 unusable FX observations in the retained legacy snapshot.
A separate READ ONLY production query then matched each account's 190 current
observations to that verified original scope using account/date/pair/value and
original row hashes. Execution `ibkrv2stg-risk-snapshots-ncrjqlt` Succeeded at
17:10:15 UTC. These values arrived in the received source; they were not an
import-value corruption. No zeros/negative values were repaired, removed or
replaced, and `RUS` was not rewritten to another currency code. Protected proof
is under `invalid-fx-originals/` and `current-fx-original-binding/`.

The conservative account-scoped dependency trace includes positions, NAV,
cash balances/activity, corporate actions, FX transaction currencies, trade
and commission currencies, and both CASH instrument currency-pair legs. It
uses only dated direct quotes or a reciprocal when no direct quote exists;
an explicitly invalid latest direct quote cannot expose an older/inverse value.
IBKR CashReport `BASE_SUMMARY` rows are counted as base-denominated aggregate
rows, not invented currencies; that rule applies only to cash snapshots, with
regression coverage and no source-row mutation.

ACR `ck90` qualified the corrected observer. Execution
`ibkrv2stg-risk-snapshots-7huz8dk` Succeeded at 17:07:49 UTC. Through the explicit
5 October cutoff, U16088571 had no foreign dated demands and U16088606 had 890,
all supplied by valid direct quotes. Each account also had 444 cash-summary
rows. There were no missing, unusable-selected or undated dependencies in this
inventory, and none of the three flagged pairs appeared in those dated demands.
The risk job's persistent image/configuration remained unchanged.

This closes the scoped question of original provenance and observed dated FX
coverage. It does not establish complete original Flex Query coverage, prove
every producer's actual financial consumption, certify a derived cash ledger,
or authorize workflow advancement. The broad physical-input adapter continues
to expose the preserved nonpositive rows rather than falsely claiming every
stored value is usable. Its readiness policy still needs consumption-specific,
verified evidence before scheduler cutover.

## Cancelled compute and notices: permanently retired

Fresh dependency inventory found no references from the other compute
configurations and no running cancelled report execution. Protected full target
configurations and deletion intents were retained before removing exactly:

- Container app `ibkrv2stg-agent-gateway`.
- Job `ibkrv2stg-agent-report`.
- Report-ready alert `ibkr-v2-specialist-daily-report-ready`.
- Report-missing alert `ibkr-v2-specialist-daily-report-missing`.

Independent GET/list verification confirms all four are absent. The 22 other
compute configurations checked at that stage and the role-assignment inventory
were unchanged. The later fully paged inventory below expands that compute
check to 65 configurations; it does not retrospectively turn the earlier
22-configuration review into a complete inventory.
Protected archives, owner storage grants, user-assigned identities and trial
data remain. The retained configurations support controlled recreation; this
does not imply Azure offers an undelete operation for the removed resources.
Evidence is under `retirement-final-compute/`.

The stopped owner-trial database still needs a retention/dependency review and
permanent disposition. That is separate from these completed deletions; do not
claim that all cancelled identities/resources have been removed or that a
stopped PostgreSQL server stays stopped indefinitely.

## Complete inventory and watchdog requalification

The earlier ARM job collection required pagination. A new bounded collection
reader now validates every continuation against the exact ARM origin,
resource-group collection and API version. It rejects foreign scopes, repeated
tokens/pages, duplicate resource IDs, malformed pages and partial success.
Tests cover those failure paths. Both the watchdog verifier and the new complete
inventory use it; a first-page result is no longer accepted as the whole group.

Live REST and Azure CLI identity sets independently agreed on 133 resources,
63 jobs, two container apps and five scheduled-query rules. The job collection
spanned four pages. All 65 compute configurations were checked for references
to the cancelled gateway/report targets. None remained; the four retired
compute/notice resources were still absent. The previously checked 22 compute
configuration hashes were unchanged, and 43 additional configurations were
reviewed. Many of the additional jobs are retained manual trial definitions;
they were not started or deleted by this inventory.

All 15 scheduled jobs were covered by the existing watchdogs and retained
zero-immediate-retry settings. The three watchdog rules were independently
rechecked for exact KQL, enabled state, evaluation settings and the owner action
group. Their live queries returned zero issues at that observation. Empty-log
fault injection detected the expected one legacy daily job, ten v2 daily jobs
and two heartbeat jobs. This is schedule/telemetry detection, not proof that
every job's financial output is fresh, nor a new email-delivery test.

No schedules, alerts, workload settings, identities or permissions were changed.
Protected complete-inventory evidence is under `complete-inventory-v1/`;
watchdog results, source hashes and the final regression log have protected
read-back in `completed-checks-v1.json` in this stage's work folder.

## Historical risk census: completed scope, explicit withheld results

Fourteen separately journalled, bounded cohorts ran against the already
qualified `risk-next-v4` immutable image. The final independent READ ONLY census
`ibkrv2stg-risk-snapshots-gs3125f` verified all 1,336 baseline cache dates had
source checks after `2026-10-06T17:37:19.976806+00:00`. The exact subject/date
scope hash remained
`bce51552f44433b4eef1b72a7f6e1d4d9e68351b351db089a8d4ce0d1c2ac721`.
The scope is existing version-v2 caches for the three active portfolios through
5 October, not every possible historical reporting or missing-cache date.

| Portfolio | Dates checked | Current / warning | Calendar-blocked / failing | Insufficient history / failing |
| --- | ---: | ---: | ---: | ---: |
| China & Hong Kong | 444 | 443 | 1 | 0 |
| U16088571-U.S. Technology and Biotechnology | 446 | 444 | 1 | 1 |
| U16088606-Greater China | 446 | 439 | 7 | 0 |

The 1,326 current caches still carry their actual warning quality; they were not
relabelled as financial acceptance. Nine calendar failures remain withheld:
all three subjects on 1 January 2026, Greater China on 27–31 January 2025,
and Greater China on 10 July 2026. U16088571's first 20 January 2025 cache
remains insufficient-history rather than inventing an opening return.

Failed native batches and their individual blocks are retained as failed
qualification evidence. Advancing a different, completely inspected cohort was
not an immediate retry or a conversion of its failures into success. Some later
cohorts rechecked dates; the final distinct census, not the sum of batch counts,
establishes 1,336-date coverage. This stage only rebuilt/invalidate-checked
unpublished analytics caches. No IBKR source, published snapshot, report file or
owner release decision was changed. Evidence is under `history-complete-v1/`.

## Historical and emergency calendar correction: qualified, not deployed

`exchange-sessions-2025-2026-r2` adds source-reviewed 2025 planned calendars,
Hong Kong's year-end half-day, the cross-year opening boundary and explicitly
registered emergency closures. Dates outside the stated coverage do not become
weekdays-only calendars. The historical fallback test now uses genuinely
uncovered 2024 dates; covered 2025 dates must satisfy the latest session check.
Missing, stale, invented non-session and future observations remain rejected.

The NYSE/Nasdaq archived 2025 PDFs and HKEX's two-page 2025 trading circular
were rendered and visually reviewed in full. The correct TWSE request uses
`date=20250101`, with response year/date and all 21 returned holiday rows checked
against the implementation. Requests using `queryYear=2025` or `114` returned
2026 and were rejected as historical authority. Unavailable HKEX calendar URLs
were not treated as successful captures. Reviewed public source bytes and hashes
are protected separately in `calendar-review-v1.json`.

Emergency closures require market, date, reason, dated announcement and exchange
policy provenance. The reviewed Taiwan July closure is supported by the
[dated securities-association notices](https://www.twsa.org.tw/) and
[TWSE's market-suspension policy](https://www.twse.com.tw/zh/clearing/suspended.html),
not inferred from a missing price. The US January 2025 mourning closure has
separate NYSE and Nasdaq announcements. This registry does not claim to cover
every future or unregistered emergency closure.

Read-only execution `ibkrv2stg-risk-snapshots-45jlrii` compared the old and
candidate policies on all nine withheld production input sets. The old policy
reproduced each recorded failure; the candidate passed readiness for all nine
using the same source-value fingerprints. It required 31 December 2025 at the
new-year boundary, 22 January during the Taiwan Lunar New Year closure and
9 July during the reviewed July closure. Exact native code/image/environment
and resource settings were checked, including independently observed ARM
empty-field/secret-alias serialization against the verified final census.
No credential value was read or displayed. Protected evidence is under
`calendar-diagnostic-v1/`.

This confirms a readiness-policy correction, not new numerical or rendering
acceptance. The production calendar is still the previous version and those
nine caches remain withheld. Promote only through the completed release gates,
then rebuild the withheld dates and re-audit the full 2025–2026 scope under the
stricter policy. Additional stale/non-session dates could be exposed by that
re-audit; do not assume these nine are the only possible post-cutover findings.
Pre-2025 and 2027 calendar qualification remains open.

## Live container/SBOM assessment: release blocked

The current web and dedicated risk image manifests, configuration digests and
all downloaded layers were size/hash verified directly against ACR. Layer
content was scanned locally without running the image or forwarding Azure
credentials to the scanner. The scanner is Trivy 0.75.0, pinned to its official
release asset/checksum and executable SHA-256; no Sigstore verification is
claimed. Raw scan JSON and CycloneDX SBOMs have separate protected receipts.

| Live immutable image | Critical | High | Medium | Low | Unknown | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Web `0786615ed4c2ebc349205e3361c996dc560073eb15f753ffdbba2bdbb9bdb0ed` | 4 | 138 | 143 | 92 | 8 | Blocked |
| Risk `35c752fa6cbc3e93824faf4d94f22906f8ca69df7cdb85958272ee721ae3765f` | 0 | 51 | 90 | 68 | 2 | Blocked |
| Derived `c23256fe2830a072558930811edafd50fff930764135379b2e2ac3a53757a64c` | 4 | 146 | 179 | 100 | 8 | Blocked |

These are package/finding entries, not a count of confirmed exploitable attack
paths. Vendor applicability must be independently reviewed; lack of a fixed
version must not make a high-severity finding disappear. Prioritize removal or
replacement of unnecessary bundled npm dependencies, a minimal supported Node
runtime for the report image, and verified patched OS/Python bases. Preserve
native chart/rendering runtime checks and approved template dependencies.
The Debian tracker currently records the scanned PostCSS/Undici packages in
[CVE-2026-45623](https://security-tracker.debian.org/tracker/CVE-2026-45623),
[CVE-2026-1525](https://security-tracker.debian.org/tracker/CVE-2026-1525),
[CVE-2026-13697](https://security-tracker.debian.org/tracker/CVE-2026-13697) and
[CVE-2026-84961](https://security-tracker.debian.org/tracker/CVE-2026-84961).
Do not infer an upstream-version range or practical exploit solely from a
distribution package label.

The new content assessor validates strict bounded JSON, immutable manifest/
configuration identity, scanner version, recent aware timestamps, non-EOL OS,
full package inventories and SBOM image/PURL coverage. Critical, high and
unknown findings block without an unfixed-vulnerability filter or allowlist.
OS/Python/Node coverage is required for a full report image; an independently
reviewed Python-only risk-image policy requires OS/Python. Missing results cannot
silently select that reduced policy. The first risk assessment correctly failed
the full-image coverage policy and was retained before explicit Python-only
policy assessment; neither result is a passing security gate.

This is a content assessor and owner-side scan, not an authenticated automated
release controller or production approval. The current scanner-adapter trust,
full-source reproduction, secret/dependency scans, isolated staging and rollback
compatibility gates remain open. Other live images, including legacy
workloads, have not yet received this complete scan/SBOM assessment. No image was
promoted on the strength of these blocked results. Evidence is under
`scan-assessment-web-v1.json`, `scan-assessment-risk-v1.json` and their referenced
raw-artifact receipts.

The derived image was assessed independently on 6 October at 19:21 UTC. Despite
its `ibkr-risk-worker` repository name, this digest inherits the full reporting
base and Node/template dependencies; it must not use the reduced Python-only
policy. All 31 manifest blobs and the configuration digest were verified, with
546 scanned packages, 537 package URLs and 547 SBOM components. Full
OS/Python/Node coverage passed, but 158 critical/high/unknown package entries
block release. Raw reports, SBOM and assessment receipts are separately
protected under `image-scan-derived-v1/`. No derived job image was changed.

## Earlier security candidates and release blockers

The initial two isolated ACR candidates reduced the scanned package findings,
but neither passed the security release gate. During those candidate builds,
the production website, risk job, schedules, approvals and received IBKR data
remained unchanged. The later dedicated risk release is recorded below.

| Candidate | Critical | High | Medium | Low | Unknown | Release status |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Risk worker with stable security patches and calendar revision 2 | 0 | 44 | 63 | 62 | 2 | Blocked |
| Reporting runtime with signed Node 24 and stable security patches | 0 | 44 | 63 | 62 | 2 | Blocked |

The risk candidate removes seven high-severity package findings from the live
risk baseline. Its deterministic source archive contains 153 runtime files;
the source manifest, non-root runtime and 117 focused Linux tests passed in the
native build. Exact PCRE2 and OpenSSL security package versions were checked.
The candidate includes the reviewed calendar correction, but no production
calendar or historical cache was changed by this build.

The reporting candidate removes all four critical package findings and reduces
high-severity findings from 138 to 44 against the live web baseline. It removes
the Debian Node/npm dependency tree and uses Node 24.21.0 instead. The downloaded
archive passed upstream release-key signature verification and its signed
SHA-256 check. The inherited approved templates, PowerPoint module and native
chart canvas passed their runtime checks as a non-root user. This isolated
overlay does not establish full-source report reproduction, financial
reconciliation, graph/layout equivalence or staging acceptance.

Official Node guidance covers [supported releases](https://nodejs.org/en/about/previous-releases)
and [binary verification](https://github.com/nodejs/node#verifying-binaries).
The reviewed release-key commit, exact downloads, hashes and native build log
are retained in the protected candidate evidence. No credentials were sent to
the public download hosts or the local scanner.

Both complete image manifests and layers were independently hash verified.
Separate raw scan reports and CycloneDX SBOMs are protected. Risk coverage
requires OS/Python; reporting coverage requires OS/Python/Node. Reporting has
170 scanned packages and 171 SBOM components; risk has 149 scanned packages and
150 SBOM components. The reporting candidate's remaining high/unknown findings
are in OS packages; its scanned Python and Node package inventories contain no
high, critical or unknown findings. Package inventory coverage alone does not
certify every embedded runtime component or practical exploit path.

The remaining util-linux findings cannot be cleared merely by requesting the
latest stable package. Debian lists the reviewed trixie package as vulnerable
for [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408),
[CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409) and
[CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410), with
fixes in unstable/testing. Debian's minor-issue classification does not by
itself qualify these images under the current release policy. No unstable
distribution packages, hidden exceptions or vulnerability suppressions were
introduced. Remaining package applicability, a smaller supported runtime and
compatible patches need qualification before production release.

Candidate evidence is under `risk-security-calendar-v1/` and
`report-security-runtime-v1/`. The risk image digest is
`b929f8f828eb066974e35a07aaac121492d978f42f9ce55b44935b5b99d45744`;
the reporting digest is
`84cb34daf107dbf62719952f1ac603d69e0c73cd460da95a9dfdeeb9f22dc498`.
Neither is a verified production rollback target. The authenticated release
controller, full-source builds, secret scanning and staging/rollback acceptance
remain open. The main reporting Dockerfile has not been silently replaced by
the candidate overlay.

## Smaller risk-runtime qualification: built, but security gate remains blocked

A separate `Dockerfile.risk.minimal` prototype now exports only the Python
runtime and its resolved ELF dependencies from an immutable official Python
3.12.15 / Debian 12 AMD64 base. The main risk and reporting Dockerfiles and
production images have not changed. This is not a generally usable reporting
image: terminal/GUI extensions and native UUID are explicitly excluded; Python
UUID fallback, SQLite, LZMA, numerical libraries, TLS, CA certificates and time
zones remain. All declared OS-library owners, original package versions/source
records and copyrights are retained. Shared loader ownership is not discarded.

The final frozen candidate (`ck9d`, `risk-minimal-runtime-v8/`) contains 157
runtime source files and passed its non-root/source-manifest checks plus 123
focused Linux tests. Its image digest is
`a33ffec4b2ae8b34be37042d678cf69a0b94496ec4d3c7a4e0aaafd24b9d22aa`.
The reviewed runtime archive SHA-256 is
`f79923c13fb3ece5072b9d3e62dd9079fb0078582f0656e27b7592b5b37a4715`.
The native final runtime tested SSL, SQLite, LZMA compression/decompression,
UUID fallback, NumPy, pandas, PostgreSQL, HTTP and Azure identity imports. This
does not establish live database compatibility, financial results or release
authorization.

Six unsuccessful native builds remain recorded with protected logs and exact
inputs. An earlier successful prototype's scan is explicitly rejected because
it identified no OS and omitted OS-package coverage. Trivy's pinned Debian
analyzer requires the original `/etc/debian_version` file; the corrected
assembly copies that file from the base, rather than inventing an OS identity
or altering scanner output. A regression test covers this requirement. See
[the pinned analyzer source](https://github.com/aquasecurity/trivy/blob/v0.75.0/pkg/fanal/analyzer/os/debian/debian.go).

The corrected scan independently verified all eight manifest/config/layer
content digests and covers 24 Debian and 62 Python packages, 86 unique package
PURLs and 87 SBOM components. It reports **two critical, three high, 49 medium,
40 low and zero unknown findings**. All five critical/high blockers remain in
the preserved assessment, with no suppression or unfixed-version filter:

| Package | Blocking findings |
| --- | --- |
| `libsqlite3-0` | CVE-2025-7458; CVE-2026-11822; CVE-2026-11824 |
| `libssl3` | CVE-2026-84782 |
| `zlib1g` | CVE-2023-45853 |

This smaller candidate is therefore **not qualified for production**. The
Debian tracker shows distribution-specific fixes for
[SQLite](https://security-tracker.debian.org/tracker/CVE-2026-11822) and
[OpenSSL](https://security-tracker.debian.org/tracker/CVE-2026-84782);
moving to Debian 13 alone still leaves its separate
[XZ advisory](https://security-tracker.debian.org/tracker/TEMP-1147318-639065).
The tracker also documents a source-versus-binary applicability caveat for
[the zlib finding](https://security-tracker.debian.org/tracker/CVE-2023-45853).
That caveat has not been converted into a release exception. No mixed-release
packages, unsupported distribution, renamed version or excluded scanner class
was used to force acceptance. Compatible fixes and independent applicability
qualification remain required. This experimental candidate's severity profile
is distinct from the earlier patched candidates; it must not be described as
having removed every critical finding system-wide.

All candidate failures, native qualification, rejected scan, raw corrected scan
and CycloneDX SBOM have protected readback receipts. The complete local source
regression passed 3,151 tests with zero failures/errors, two OS-specific skips
and no source changes during the run. Repository policy passed for 293 tracked
files, 869 selected source files and 61 contiguous migrations. Full results
are in `minimal-runtime-regression-v3/`; this focused runtime work does not
complete the entire approved programme or deploy the calendar correction.

## Operational-data alert: deployed and settings verified

`ibkr-v2-operational-data-findings` now evaluates the existing restricted
metadata-monitor logs every 15 minutes and routes actionable findings to the
existing owner action group. It inspects risk, queue and benchmark metadata;
it neither reads raw IBKR files nor writes portfolio data. This closes a scoped
useful-output monitoring gap, not the entire observability programme.

Persistent data findings require two complete captures at least 20 minutes
apart, so normal pipeline sequencing does not immediately page the owner.
Missing completion evidence for two hours, a latest capture older than 75
minutes, malformed timestamps and incomplete/conflicting inventories remain
actionable. Capture timestamps, replica boundaries, exact row/index counts and
typed access-check fields are validated; late delivery of old logs does not
make old evidence fresh. Current benchmark quality warnings remain visible but
are not mislabeled as stale observations. These checks do not certify financial
figures, all producers or a complete source-correction dependency graph.

Twenty-eight synthetic fault cases passed in Azure's actual query engine. The
final live query returned zero issues before and after deployment. ARM readback
verified the exact query, scope, criteria, cadence and enabled owner route.
Protected intent, qualification and readback receipts are retained in
`operational-findings-v4/`. The owner subsequently confirmed receipt of the
synthetic test sent through this rule's existing email route; an enabled route
alone was not used as delivery acceptance. A real rule-trigger incident was
not deliberately induced.

A single owner-only synthetic `LogAlertV2` notification test was attempted
through the existing action group. The CLI timed out after 120 seconds while
waiting for Azure, so its outcome is **unknown**, not successful delivery or
confirmed rejection. The intent is protected in `operational-notification-v1/`.
No duplicate test was sent. Read-only reconciliation verifies the reviewed rule
and enabled owner receiver. The owner directly confirmed: "Yes, received the
test email." This independently confirms synthetic email-route delivery,
without relabeling the timed-out CLI response as native success. The receipt
is protected separately from the unknown command outcome. This test does not
exercise a real operational-data incident or certify all notification paths.

The local `monitor_summary` correction now combines queue/risk findings with
calendar readiness and exposes benchmark-quality warnings and uncovered scope.
This removes a misleading overall summary when calendar details need attention.
These collector/summary code changes are now **deployed and native-verified**
through a separately qualified source-only release. Only the existing monitor's
bundled code arguments and their checksum tag changed; its image, restricted
database role, identity, Key Vault binding, resources, schedule and zero-retry
setting remain unchanged. This does not qualify a new container image or change
any financial producer. The deployed alert still independently reads detail rows.

The frozen payload `59179061ff748532d34efd8fa2c297a7320e75068e84a1693eb8fc73523cf245`
passed the full 3,160-case regression and a one-off native canary
`ibkrv2stg-metadata-monitor-ft9c0tk` before promotion. Exact persistent ARM
readback passed, followed by fresh execution
`ibkrv2stg-metadata-monitor-zwhhjkc`. Both executions returned all 11 detail
records, seven denied-column checks, seven benchmark checks, zero calendar
readiness failures in checked scope and seven explicitly visible quality
warnings. Financial reconciliation and absolute NAV/FX/risk-free freshness remain
uncertified. The r2 calendar is deployed only in this read-only monitor, **not**
in risk or derived-portfolio producers.

The source-only release policy rejects changed images, identities, credentials,
schedules, retry limits, resources, source hashes, incomplete/conflicting logs
and misleading summaries. Native execution aliases are accepted only in the
exact reviewed representation. Unknown writes must be reconciled rather than
redispatched. The original payload remains protected for source-only rollback.
Canary, regression, promotion and fresh-execution receipts are retained in
`monitor-summary-release-v1/`.

The post-deployment paged ARM census agrees with Azure CLI: 134 resources,
63 jobs, two applications and six query rules. All 15 scheduled jobs still have
the expected watchdogs and zero retry limits. The four cancelled targets are
absent and none of the 65 compute definitions references them. All 22 compute
configurations from the earlier retirement baseline remain unchanged; another
43 were inspected without claiming a historical configuration comparison.
This metadata-only verification is protected in `post-alert-inventory-v1/`.

## Retained-permission review: exposure mapped, not security acceptance

The read-only review inspected all 65 compute definitions, 20 managed identities,
86 resource-group/subscription-scoped role assignments and 14 role definitions.
Twenty-one broad action patterns require scope and condition review. This is
not a count of unauthorized grants or a calculation of effective access.
Two manual bootstrap definitions (`ibkr-v11-bootstrap` and
`ibkrv2stg-bootstrap`) reference administrative database bindings; secret values
were not retrieved. Disposition must preserve qualified recovery procedures.
Evidence and a protected readback receipt are in `retained-permissions-v1/`.

Compute-reference classification distinguishes scheduled/application use from
manual-only definitions. An identity with no checked compute reference may
still serve builds, storage or other services and is not automatically unused.
No grants, owner storage permissions, trial data or resources were deleted.
Entra membership, PIM/Conditional Access, management-group/tenant inheritance,
deny assignments and effective permissions remain outside this review.

## Resource ownership and costs: deployed, readback and portal verified

The first read-only resource-group Cost Management query returned HTTP 429.
After a five-minute cooldown, one bounded recheck succeeded with all 338 grouped
daily rows, complete pagination and explicit KRW currency. The qualified
October-to-date pre-tax total at `2026-10-06T20:36:52.279174+00:00` is
**KRW 24,852.95**, not a final invoice, forecast, live counter or spending cap.
It must not be compared with a different billing-period total on the portal.
Credits, unallocated scoped charges and historical charges from retired
resources remain included. No portfolio financial observations were queried.

Nine focused tests verify named response columns, complete pagination, bounded
dates, stale/future/naive captures, scope, row counts, finite amounts, duplicate
rows, separate currencies and exact native workbook readback. The prepared
workbook combines a clearly dated service-cost snapshot with a paged, read-only
resource ownership table and a link to Azure's native live Cost Analysis. The
table exposes only owner/application/purpose tag keys; it does not write tags.
No embedded credentials, new identity, role assignment or recurring collector
is introduced. Unknown or unavailable billing data is not described as zero.

Protected billing evidence is in `cost-observation-v1/`. The new shared
[Azure workbook](https://portal.azure.com/#@wealthryse.ai/resource/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11/providers/Microsoft.Insights/workbooks/027325da-7ff3-48a5-9a90-cd10acb4b8a9/workbook)
is deployed. Creation followed the passing 3,168-case frozen-source regression.
The first strict readback rejected Azure's added `hidden-title` tag; the original
intent and rejection remain preserved. Only that exact tag, equal to the
approved display name, was subsequently qualified with a focused regression
test and the passing 3,169-case full regression. No second PUT was sent.
Exact native content, scope and metadata readback now pass. The completed
paged inventory contains 135 resources, including this one new workbook.

Actual portal inspection verifies the dated total, ten service-cost rows and
live read-only ownership grid. The workbook is left open for the owner. Portal
accessibility evidence and a JPEG screenshot are retained in `cost-workbook-v1/`,
alongside protected `native-qualified.json` and `render-verified.json` receipts.
The screenshot is a UI observation, not independent billing or financial
acceptance. Existing resource definitions, permissions and identities were not
changed. Automated cost refresh, cost enforcement and complete resource
ownership tagging remain outside this scoped dashboard acceptance.

## Candidate blocker review: vendor research, not a release exception

Current vendor records still identify Bookworm SQLite and OpenSSL findings as
unresolved. Trixie has fixed SQLite and a fixed OpenSSL security package, but
its XZ package still requires qualification of the decoder-allocation finding.
Version tables are not proof of candidate bytes or compatibility:
[SQLite tracking](https://security-tracker.debian.org/tracker/CVE-2026-11822),
[OpenSSL tracking](https://security-tracker.debian.org/tracker/CVE-2026-84782),
[XZ tracking](https://security-tracker.debian.org/tracker/TEMP-1147318-639065).

Debian distinguishes the flagged zlib source package from Bookworm binaries
that do not build affected MiniZip code. That merits independent binary/source
applicability evidence, not a silent exception or filtered scan:
[Debian's zlib assessment](https://security-tracker.debian.org/tracker/CVE-2023-45853).
The strict scanner gate, original findings and production image pins are
unchanged. No mixed-distribution binary swap, package rename or suppression was
used. This research has a protected receipt in `blocker-vendor-review-v1.json`.

## Risk worker security and historical calendar deployment

The dedicated database-only risk worker now passes the unfiltered OS/Python
release scan and is deployed. Its immutable image is
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-risk-worker@sha256:af434cdb3876d8c5a7e558b6b9236b1de46e3683902404e7a8d8f13a48a89583`.
The scan records zero critical, high or unknown findings, 40 medium findings and
19 low findings, with 78 package identifiers covered by the CycloneDX SBOM.
This clears this risk-image gate, not every vulnerability or the reporting
images' separate release blockers.

The build uses an immutable official Python Trixie base and its native Debian
security updates. SQLite and TLS remain present and operational. An explicit
database-only mode omits the unused LZMA extension and its XZ dependency;
the default runtime still retains archive support. Original Debian package
ownership and ELF provenance remain recorded. There is no package rename,
mixed-distribution library substitution or vulnerability suppression. The
preceding full-archive Trixie candidate remains blocked by one unknown XZ
finding; its original scan and 127-case Linux result are retained separately.

The deployed candidate passed 129 focused Linux cases and non-root runtime,
SQLite, TLS and missing-archive-capability checks. The full Windows regression
passed 3,181 cases with zero failures/errors, two OS-specific skips and no
source drift. An earlier test-launcher multiprocessing failure and an initial
native-metadata rejection remain preserved. Six new tests qualify only Azure's
exact job-secret alias, container-image descriptor and empty storage metadata;
changed credentials, tenants, commands, images, resources and extra fields
still fail acceptance. The original canary was reconciled, not redispatched.

The native read-only canary checked three active subjects and nine representative
dates. Eight sampled caches were current; the inception cache remained failed
quality because it has no return history. A separate database-enforced read-only
shadow verified input readiness for all nine previously calendar-blocked dates
and recalculated all three current packages using 5,000 paths each. Every
current source fingerprint and result section matched the stored package.
These checks wrote no source, cache or report records.

Manual promotion authenticated version-bound protected source, build, raw scan,
SBOM, regression and native-execution evidence. The exact risk image pin and
unchanged non-image settings passed ARM readback. Schedules, zero retries,
identities, resources and credentials did not change. The former image remains
recorded for image-only rollback; restoring its older calendar requires cache
re-audit and is not a rollback of source data or a database migration.
Production website and derived-job images were not included in this release.

The installed revision-2 calendar then rebuilt exactly the nine withheld,
unpublished risk caches. Every repaired date passed independent consumed-input,
payload-integrity and identity readback. No received IBKR source or published
report was changed. The complete database-enforced read-only audit checked all
1,336 historical active dates: 1,335 current caches, one unavailable inception
date (`U16088571`, 20 January 2025), zero validation blocks and zero missing or
unexpected cache dates. Every usable cache passed consumed-value fingerprint,
payload-integrity and identity checks. The inception absence was not fabricated
into a valid return. This verifies the consumed historical scope, not upstream
raw-correction propagation or independent financial reconciliation.

A fresh execution of the installed normal worker also succeeded: all three
latest packages remained unchanged after validation; twenty historical caches
were source-checked, with no blocked, busy or pending work. This confirms the
ordinary worker path separately from the one-off repair and read-only probes.
Protected build, scan, regression, canary, shadow, promotion and repair receipts
are retained in `risk-minimal-runtime-v10/`. The complete latest regression log
is in `trixie-runtime-regression-v3/`.

## Reporting worker security and release evidence

The dedicated PDF/PPTX-only reporting candidate passes the unfiltered
OS/Python/Node vulnerability gate. Its immutable image is
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:86302b0cd49d0e2ff622fbaaff15644c7fcdc8845aac2fced6d8e5b9cd3b9c08`.
The scan records zero critical, high or unknown findings, 40 medium findings
and 19 low findings; 99 scanned packages and 91 package identifiers are
covered by the 100-component SBOM. The complete scan and SBOM are protected
under `report-minimal-runtime-v5/`. This clears the candidate's security gate,
not the website's image findings or the complete reporting release.

The candidate retains the signed Node runtime, native chart canvas, SQLite,
TLS, standard ZIP compression and the original application and approved
template bytes. Its non-root runtime has no shell. Only the explicit
PDF/PPTX-only mode excludes the unused native LZMA decoder; the default
exporter retains it. This image must not serve the website or general archive
processing. Debian ownership and dependency provenance remain intact; no
scan findings were suppressed or packages renamed.

The native build passed report-runtime checks and 13 Linux monthly-artifact
tests. These synthetic rendering and geometry tests do not establish visual
equivalence to an uploaded design authority or independent financial accuracy.
The application and templates match the original 1,112-file tree, whose SHA-256
is `a4784690d04f7f8cc6c19d75a2e7df73d1189656c19f3f953ef6386f67baf26b`.
This is a runtime reduction of the prior application, not a full rebuild of
the current report source or a current-calendar report release. The candidate
has not been promoted. Production website and job definitions remain unchanged.

Earlier candidates and failures remain preserved: the first minimal build
failed because its helper package was incomplete; the corrected full-archive
candidate passed runtime tests but retained one unknown LZMA finding. The
first PDF/PPTX-only build failed in an import-test harness whose script path
was missing. The corrected harness and monthly-artifact tests passed in the
final candidate. An ambiguous build acknowledgement was reconciled against
the existing native run; no duplicate build was dispatched.

The new protected-evidence reader verifies explicit blob versions, ETags,
bounded content and SHA-256 hashes, with private legal-hold protection checked
before and after every read. Seven unit tests cover tampering, truncation,
wrong scope, missing versions and revoked protection. Live read-only
qualification verified 13 protected risk/report evidence versions under
`protected-release-reader-v1/`. This authenticates the retained bytes and
storage scope, not the independent identity of a build or scan producer;
the reusable automated release pipeline remains incomplete.

The same reader subsequently verified eight explicit protected versions of
the final reporting build, native qualification, scan, raw scan/SBOM, final
intake regression and local-template validation evidence. Their readback
receipt is under `protected-release-reader-v2/`. No release approval or
production promotion was inferred from storage integrity.

The guarded full regression after the runtime exporter and reader changes
passed 3,192 cases with no failures, errors or source drift and two
OS-specific skips. Its complete log and source hashes are protected under
`report-security-regression-v1/`. The subsequent archive-intake change passed
a separate 3,200-case complete regression with no failures, errors or source
drift and the same two OS-specific skips. Its protected full log and source
hashes are under `report-intake-regression-v1/`; this does not certify the
scanned candidate as containing that later source change.

## Presentation archive intake hardening

The source validator now rejects encrypted or unsupported-compression
presentation archives, duplicate or aliased part names, unsafe original ZIP
paths, corrupt contents and invalid deflate streams. Entry-count and expanded
size limits are checked before decompression. Standard stored/deflated PPTX
bytes are retained unchanged. Validating original member names also prevents
Windows path normalization or NUL truncation from concealing unsafe paths.

Eight focused tests and all 15 existing onboarding tests pass. Read-only
qualification of the approved U16088571 presentation and the Greater China
intake presentation also passes with unchanged file hashes. These two local
inputs do not certify every registered template. Their protected evidence is
under `template-archive-intake-v1/`. The intake policy is source-only and has
not been deployed; the scanned reporting candidate predates this change.

Repository policy also passes for 293 tracked files, 884 selected source files
and 61 contiguous migrations. That limited credential-pattern check is not
the complete release secret-scanner gate. A fresh read-only ARM check verifies
the unchanged installed website, benchmark, derived and risk image pins;
the protected receipt is under `report-release-boundary-v1/`. This four-workload
check is not a new census of every compute definition.

## Native diagnostic export baseline

Read-only ARM verification confirms that both production PostgreSQL servers
and all three Key Vaults already have metrics-only exports to the existing
Log Analytics workspace. Log categories remain disabled. This confirms the
configured export destinations, not successful metric delivery, redacted
audit-log ingestion or slow-query coverage. No SQL text, secret values or
new unredacted log stream was collected. The protected observation is in
`diagnostic-baseline-v1.json`; redaction and native-ingestion qualification
remain necessary before enabling database or Key Vault logs.

## Earlier regression and release boundary

The preceding guarded Windows regression discovered 3,169 cases with zero failures
or errors, two OS-specific skips and no source changes during the run. The
minimal risk candidate's 123 focused Linux cases also passed. Repository policy
passed for 293 tracked files, 875 selected files and 61 contiguous migrations; its limited credential-pattern
scan is not a full secret-scanner acceptance. Source hashes and the complete
earlier regression log are protected in `completed-checks-v1.json`; its
candidate qualification records are retained separately in
`security-candidates-completed-v1.json`. That complete log and result are
under `minimal-runtime-regression-v3/`; the earlier alert-stage complete log is
under `operational-alert-regression-v1/`. The source-only monitor-release log,
result and protected receipt are in `monitor-summary-regression-v1/` and
`monitor-summary-release-v1/`. Workbook creation qualified against
`monitor-summary-regression-v2/`; the exact native-title representation then
passed `monitor-summary-regression-v3/`. Its complete result, source hashes and
log are protected in the workbook's native qualification receipt. This does
not certify Linux-only fixtures skipped on Windows or financial correctness.
The 28 live-query fault cases and exact
deployed alert are retained under `operational-findings-v4/`. Git ownership qualification used only
an exact-directory, process-scoped setting; no global Git policy was changed.

The write-page skill guided this surgical execution-record update. Computer-use
skill guidance was used for the existing Azure cost-page inspection and the
deployed workbook's actual portal layout and ownership-grid verification;
cached portal figures were not substituted for the successful native billing query.
The earlier PDF skill use guided full-page inspection of archived exchange-calendar PDFs.
No uploaded design authority, original calendar PDF, IBKR source, published
report, owner approval, production database tier or network boundary changed.
Alpha World MS remains paused and the cancelled agentic programme stays retired.

The earlier read-only ARM verification confirmed unchanged website, risk and
derived-portfolio image digests, and the monitor's exact qualified source-only
payload with all non-source settings preserved. This verification and the
portal observations have protected readback receipts in `render-verified.json`.
It confirms release boundaries, not resolution of the outstanding image findings.

## Remaining programme, in execution order

| Approved area | Current status | Remaining implementation / exit condition |
| --- | --- | --- |
| Dependency-driven workflow | Date-bound shadow/lease safeguards and input fingerprints qualified; governed reference integrity deployed; scoped flagged-FX provenance and dated quote coverage verified; not complete | Complete original/parsed-source and query-scope coverage; independently reconcile derived cash/positions and actual consumed dependencies. Install additive metadata under a restricted producer identity, wire validated evidence into leases with private staging and fenced publication, then shadow-compare readiness before cutover across ingestion → reconciliation → benchmark/FX → derived → risk → reports → assurance. Successful observation or job status alone must never advance readiness. Preserve Flex token-level breakers. |
| Risk correction propagation | Revision-2 calendar deployed to the dedicated risk worker; nine withheld caches rebuilt; complete 1,336-date read-only audit passes with 1,335 current caches, one valid inception unavailability and no missing caches or validation blocks; fresh normal worker succeeded; not complete | Finish upstream raw-correction-to-producer propagation and maintain missing-cache coverage. Qualify pre-2025/2027 and unregistered emergency calendars. Validate composite crisis coverage before Alpha World resumes. |
| Controlled release pipeline | Dedicated database-only risk image deployed after zero critical/high/unknown findings, 129 Linux tests, 3,181-case full regression and exact native shadow parity; separate PDF/PPTX-only report candidate now clears its security gate and 13 Linux report tests but is not deployed; version-bound protected-evidence reader qualified; not complete | Resolve other image findings without suppressing them. Rebuild the current report source, including calendar and intake hardening, then finish staging and independent financial/visual verification, secret/dependency/template/migration checks, authenticated reusable build/scan adapters and compatibility-tested rollback. The runtime candidate and scoped manual risk release do not establish the complete automated release pipeline. No cancelled agent identity may deploy itself. |
| Recovery / availability | Private v2 PITR parity, isolated General Purpose zone-redundant HA, planned failover and post-failover parity verified | Complete isolated application, identity and report-download recovery and measure complete RTO/RPO. Qualify production tier changes and DNS/egress before cutover. No production database was restored over or resized. |
| Security / retirement | Four cancelled compute/notice resources retired; post-alert paged inventory reconciles 134 resources and all 65 compute definitions; scoped 86-assignment exposure review retained; archive-intake safety checks implemented and focused-tested in source only; not complete | Deploy the qualified intake policy through the report/website release process. Qualify effective access and retained bootstrap/trial definitions, identities and trial data before final disposition; address other image findings. Stage and validate DNS/egress before storage/Key Vault/registry restrictions. Entra Conditional Access review remains explicitly deferred, not silently passed. |
| Observability / costs | Actionable account health and 15-job watchdog coverage verified; restricted risk/queue/benchmark data-finding alert deployed after 28 live fault cases; source-only collector-summary correction deployed and native-verified; owner confirmed the alert's synthetic email-route test receipt; cost/ownership workbook deployed after complete regression, native readback and actual portal verification; not complete | Extend absolute freshness and producer-versus-consumed fingerprints across NAV/FX/treasury and downstream jobs, transition-aware dead-letter age/no-new-data detection, redacted database/Key Vault diagnostics and slow-query thresholds. Schedule success and zero scoped findings alone are not end-to-end financial freshness. Qualify additional notification paths and real incident lifecycle. Cost refresh/enforcement and complete ownership tags remain unqualified. |
| Maintainability / scale | Shared calendar and risk validation centralized; not complete | Gradual bounded route/service extraction, realistic concurrent-user/many-account load tests, historical-fixture separation and provider-symbol mapping tests. Do not rewrite protected historical evidence or change IBKR identifiers to satisfy provider aliases. |
| Financial / visual acceptance and onboarding | All three current active portfolios pass operational assurance; not complete | Independently reconcile representative month-end NAV/TWR/cash/FX/derived sleeves against immutable IBKR inputs; apply approved template-family pixel/geometry/font/chart/content tests and account-scoped download integrity. Alpha World MS remains paused until design-authority and composite acceptance are signed off. |

Throughout this stage, **no received IBKR Flex source data was changed**, no
published report or owner release approval was altered, and Alpha World MS
onboarding stayed paused. Bloomberg integration and the cancelled agentic
programme have not been restarted.

## Alpha World MS HKD and HIBOR qualification — 7 October 2026

The owner requested HKD as MS's calculation and reporting currency, and 1-month
HIBOR averaged over published fixings from inception through each factsheet's
actual valuation date. The owner confirmed usage rights covering the system and
factsheets. No further projected financing deduction is applied: interest is
already reflected in source NAV. Alpha World T and other portfolios are unchanged.

Private validation rebuilt 443 MS valuation observations through 30 September
from original recorded sleeve values and dated broker FX, converting each sleeve
to HKD before fixed-unit normalization. This is not relabelling USD NAV. All
1,772 sleeve/FX selections passed independent checks. There are 415 official
HKAB 1M fixings over the full interval. The reviewed non-publication dates
5 August, 8 September and 24 September 2025 are excluded, not forward-filled.
Each earlier factsheet uses only its own inception-to-valuation fixing sample.

All 21 historical factsheets (January 2025–September 2026) were rebuilt privately
with layout v14r8. Their 189 printed rate/metric values, complete plotted paths,
axes, legends and allocation-caption geometry passed checks. All 21,000
Monte Carlo paths passed a separate NumPy-product verification. The 42 local
Poppler pages were visually reviewed; S&P500 matches the adjacent allocation
caption size and baseline. Local source tests now pass 118 cases.

An immutable Azure rehearsal candidate passed 116 scoped Linux tests, native
PDF/chart runtime qualification, and a container/SBOM gate with zero critical,
high or unknown findings (40 medium, 19 low findings remain). Its digest is
`sha256:c696f96e89070829dabde682690091dd93450576548388bccb62e13289969af2`.
Build `ck9w` and private execution `ibkrv2stg-onboard-render-4jbj7kq` succeeded.
Two earlier failed candidates (`ck9u`, `ck9v`) remain excluded and their evidence
retained; test-location/inherited-retired-test discovery was corrected without
dropping the scoped test suite or re-enabling retired agentic features.

Azure rendered all 21 PDFs using the exact verified HKD/HIBOR input hashes.
Their downloaded PDF vector/content checks passed. All 42 actual Azure Poppler
pages are pixel-identical to the visually reviewed local pages. Private outputs are under
`internal-reports/review/alpha-world-ms/hkd-hibor-v14r8-20261007/outputs/`;
local copies are in `output/pdf/AlphaWorld_MS_Azure_HKD_HIBOR_v14r8/`.
The reporting identity performed a create-only input transfer because the
owner's blob login is read-only; no additional access grant was made.

This is a private Azure financial/rendering rehearsal, **not production cutover**.
Both installed reporting-job configurations and image pins remain unchanged;
the live MS contract and metadata remain USD. New source cache-timing tests and
the timing fix postdate this frozen renderer candidate and require the next
coordinated release. HIBOR recurring-fetch qualification is also outstanding:
the HKMA bulk endpoint returned HTTP 405 during a fresh transport check, while
HKAB's official 6 October fixing was directly readable (1M 2.90% p.a.). No
unavailable rate is substituted or manufactured.

Remaining for this change: qualify recurrent official HIBOR ingestion and its
publication-time policy; rehearse/version the HKD metadata/calculation definition,
benchmark comparisons, NAV and cache rebuild; coordinate the relevant derived,
risk, reporting and website runtime updates with compatible rollback; then
verify future month-end generation. Original IBKR files and frozen/published
snapshots remain unchanged. MS onboarding is still paused and no report was
released. This work does not close the other outstanding programme areas.

## Viewer incident investigation — 8 October 2026

The attached `WeatlthRyse Data platform errors.docx` was treated as incident
evidence, not as authority to restart services or alter financial methodology.
The document contains text findings, with no embedded media. Current inspection
covered all 135 resource-group resources, recent executions for 63 jobs,
application images/scale, databases, storage, vaults, registries, 24 enabled
alert rules, alert delivery configuration, network rules and 89 resource/child
role assignments plus inherited roles. No secrets were retrieved for this audit.
Metadata inventory is not exhaustive certification of every runtime or financial
calculation. Evidence is under `.work/viewer-report-investigation-20261008/`.

Confirmed application defects and scoped corrections:

- Derived-portfolio CSVs incorrectly read physical-account tables. Performance,
  holdings and cash exports now use tenant-scoped subject views. MS holdings
  remain component exposures, never fabricated share quantities.
- The AI Analyst hard-coded one pilot account. It now uses existing authorized
  subject membership and rejects mixed-subject grounding; model, regional,
  feature and role controls remain intact. MS's raw PostgreSQL risk-tool path
  additionally required the application's placeholder adapter.
- Derived trade pages ignored their explicit not-applicable status. They now
  explain that the portfolio has no separately executed trades and hide that
  export instead of implying a failed broker feed. Source accounts retain
  205 and 1,320 recorded executions respectively.
- Account-card name spacing, currency-neutral Metrics Guide wording, workspace
  toolbar alignment, benchmark spacing, contribution-note spacing, and the
  irrelevant visible Reports date picker were corrected in the scoped candidate.
- Risk observation counts are explained as aligned valuations versus return
  intervals. N valuations normally yield N-1 returns; different validated market
  samples must not be forced to identical counts.

Shanghai's 30 September observation is expected across the exchange's 1–7
October closure. The existing producer also applies a US cutoff to every market;
the calendar-aware monitor mirrors that scheduled-cycle policy. This does not
certify a fresh 8 October observation or justify restarting a healthy feed.
Website freshness presentation still needs a separate calendar-aware UX update.

Private candidate qualification passed 208 checks across four registered viewers
and four active subjects. Performance and holdings exports were nonempty in every
case; derived trade exports intentionally return an explicit not-applicable
response. MS's zero cash-transaction rows are expected for its fixed-cash
construction, not invented ledger entries. Private tests bypass ingress only
inside the isolated read-only job; actual separate viewer browser logins and
live model inference are not claimed tested. All 82 scoped regression tests also
passed both locally and in the native Azure build. Approved report-asset hashes
remain unchanged, and unrelated dirty report-design work was excluded.

Native build `ckac` produced immutable candidate
`sha256:eaf8aaa324830cfcb63f6b6fb58a7f9bd7b446ddead4f7f42e4c969678308eaf`.
Its missing command acknowledgement was reconciled by tag, digest and dispatch
timestamp, never by repeating the build. Exact-byte scanning passed with zero
critical/high/unknown findings; 40 medium and 19 low findings remain recorded.
The scan does not mean vulnerability-free. Most OS findings lack a scanner-listed
fixed version; pip findings have fixes and need a separate qualified dependency
remediation. Exact-image runtime and baseline recovery qualification are in
progress. No live website image replacement is yet claimed at this checkpoint.

The installed risk worker completed bounded repair execution
`ibkrv2stg-risk-snapshots-0clx0xj`: 100 historical snapshots checked, 18 rebuilt,
zero pending/blocked in that checked set. One inception date has insufficient
history, which is expected. It ended successfully with no blocked latest subjects.
This is not an all-history audit claim. Persisted worker settings, image,
credentials and schedule were rechecked unchanged; broker files and report
approvals were untouched.

Resource-group follow-up remains explicit: two main PostgreSQL servers lack HA,
with 14-day backups and no geo-redundant backup; the healthy isolated HA rehearsal
server is not the production application database. Storage, vaults and registries
retain public-network access, though anonymous blob access is disabled and TLS,
vault RBAC/purge protection and disabled registry admin logins are in place.
No private endpoints exist. The credential broker still uses a mutable image tag.
The metadata audit found no Owner/Contributor/User Access Administrator grants
to service principals, but this alone does not certify least privilege. The email
action group is enabled; no new test email was sent. Subscription diagnostic
settings were absent. Connectivity/recovery qualification is required before
production HA/network/identity changes; no blanket ACL or permission changes were
made. Cancelled agentic resources remain retired; Bloomberg and Alpha World T
were not started.

### Final-review correction and revised candidate

The first candidate was not deployed. Its final review exposed an inherited
hard-coded USD label in AI NAV charts. Candidate v2 derives chart currency from
validated observations, rejects mixed/missing currencies, and also rejects NAV
facts lacking a valid currency. The full narrative path was mock-tested for
authorized non-pilot subjects and for denial before tools on unauthorized
subjects. This does not claim a live model-inference test.

Private execution `ibkrv2stg-onboard-render-gukltfw` passed the revised 208 checks:
all 16 viewer/subject analyst-grounding cases passed; MS charts use HKD and the
other three use USD. All 86 tests pass locally and in native build `ckad`.
Candidate v2 also removes unused pip from the final runtime and restores the
same non-root user; runtime/build checks must still pass. Immutable image:
`sha256:961bc36d5ddff5eff4d00476ef6e48ed9709ef8ba301e2cb1143db95e8c3502d`.
The earlier v1 image and failed diagnostic attempts remain preserved, excluded
from deployment, and not mislabeled as passing current-release evidence.

The capacity query was corrected using confirmed supported Azure metrics;
all four database reads then succeeded. In the 24-hour window the two main
servers had CPU maxima of 100% and 99.93%, memory maxima of 83.97% and 82.49%,
storage maxima around 14%, and maximum 12 active connections. These are peaks,
not sustained-load or throttling conclusions. The stopped trial returned no
capacity measurements, as expected. The three vaults exposed 18 enabled secret
metadata rows with no configured expiry; no secret values were read. Rotation
policy and application-safe rotation qualification remain distinct follow-up.

Exact v1 candidate runtime execution `ibkrv2stg-onboard-render-9s5yksv` passed
208 checks and approved-report hash validation. Original-image recovery execution
`ibkrv2stg-onboard-render-cols6dy` validated imports, unchanged report assets and
all 16 viewer/subject summary reads against the current database. It is a private
original-image rehearsal, not a live website traffic rollback and not proof that
the old image's known export/analyst defects are resolved. For v2, its evidence
may only be reused after exact baseline/auth/report dependencies are verified
unchanged and bound to the new release. New v2 exact-image qualification is
still required before deployment.

### Website release verified; immediate maintenance authorized

Exact v2 execution `ibkrv2stg-onboard-render-ym5xpl4` passed all 208 checks,
installed-source and approved-report hashes, non-root execution and absence of
the unused installer. The exact scan has zero critical/high/unknown findings;
35 medium and 18 low findings remain tracked, not described as vulnerability-free.
Original-image recovery evidence was rebound only after unchanged website,
authentication and report dependencies were verified.

The website-only image replacement reached Healthy with 100% traffic on
`ibkrv2stg-web--viewer-fixes-20261008`, using immutable image
`sha256:961bc36d5ddff5eff4d00476ef6e48ed9709ef8ba301e2cb1143db95e8c3502d`.
Authentication, identity, credentials and scale settings are unchanged; source
observations, approved report files and reporting-worker definitions are unchanged.
The owner Chrome session independently showed the derived-trade explanation,
disabled trade filter and hidden non-applicable trade export; toolbar centering
was confirmed. This is an existing authenticated owner session, not a fresh
viewer sign-in or a database-recovery sign-in test.

The owner selected **now** for the remaining availability/network maintenance
window. That authorizes scoped maintenance, not skipping recovery or connectivity
gates. Read-only dependency, backup and capacity preflight comes first; isolated
application/report recovery must pass before a production database tier/HA change.

The new recovery baseline `ibkrv2stg-onboard-render-2ql3f66` stopped at an
incorrect diagnostic assertion requiring 42 *released* MS files. Live owner
Chrome inspection instead confirms 21 review-ready months and **zero internally
released MS months**. This is not a failed source sync or evidence of lost files.
Registered/accepted historical files must not be described as released reports;
onboarding activation does not approve their distribution. The revised recovery
attempt verifies all 42 owner-review files and the existing release/access state
without changing approvals. The rejected attempt remains preserved. Production
database tier/HA and network access remain unchanged at this checkpoint.

Corrected baseline `ibkrv2stg-onboard-render-9u979sw` Succeeded: 208 viewer
checks, 16 restricted-role data/authorization table fingerprints, 48 existing
released-report downloads and 42 MS **owner-review** downloads. All bytes matched
registered hashes; viewer review-file access and anonymous report access were
denied. Chrome's existing authenticated owner session downloaded September MS
generation run 920, SHA-256
`aca3c6dc71cd7be606af2dcbc16672b287be93ce84eaa20bd9bfce109c427fe3`,
matching that private evidence. No approvals changed.

A separately named private PITR copy `ibkrv2stg-app-recovery-20261008` was
dispatched using restore point `2026-10-08T14:21:12Z`; production was not
redirected or overwritten. Its provisioning/read-back and full parity acceptance
remain required. The new eight local maintenance-control tests passed, including
scope drift, secret/reference preservation, schedule pause and window expiry.
The production v2 HA controller requires that restored parity before preparing
any live change; its plan preserves hostname, credentials, backup and network,
pauses only the three high-frequency v2 workers, verifies reads after sizing and
HA enablement, and restores their original schedules. No such pause, resize or
production HA change has yet occurred at this checkpoint. V1 production and all
public service access boundaries remain unchanged.

Current regional metadata advertises the rehearsed two-core D2ds_v5 with
zone-redundant HA, not a reservation of regional capacity. CPU hourly average
means were approximately 10.7% (v2) and 8.4% (v1), with minimum remaining credits
277 and 281. The HA sizing is for resilience, not a claim of sustained overload.
Standard VNet IP-availability checks are not treated as authoritative allocation
headroom for the managed delegated PostgreSQL subnet. No subnet enlargement or
network restriction was performed on that evidence.

The owner explicitly approved a small private administration/build VM and Azure
Bastion in this resource group, with no public IP on the VM, restricted access
and ongoing cost. Existing public access must remain until the replacement is
tested. The additive controller uses separate unused v2 subnets, owner-only
Entra VM login, password-disabled Ubuntu with secure boot, Bastion-only SSH and
explicit NAT egress; it never modifies public ACLs or existing subnets. The first
proposed D2as_v5 VM has zero family quota; an equivalent two-core D2s_v4 has quota
and no regional SKU restriction. Six private-path and ten maintenance-control
tests pass. Azure validation/what-if and actual login/build proof are still
required before this path qualifies; a provisioned VM alone is not that proof.

The isolated restored server is Ready. Recovery execution
`ibkrv2stg-onboard-render-cdz7hbi` failed because the diagnostic converted its
in-memory URI to a keyword DSN while the website deliberately requires a URI;
the error was identified through exception class and line-number-only logs.
No production connection was changed and no raw credential logs were read.
The failed attempt is preserved, a URI-preserving clone-host override is now
regression tested, and corrected execution `ibkrv2stg-onboard-render-3ap32w7`
is pending acceptance. Production workers are not paused and production
compute/HA are unchanged at this checkpoint.

Corrected isolated recovery `ibkrv2stg-onboard-render-3ap32w7` Succeeded and
passed exact parity with the current-source baseline: 208 viewer checks, 16
data/authorization tables, 48 released files and all 42 MS owner-review files.
Viewer proof SHA-256 `6ab92ff1d35d6d0e3825404cdad0f24fb98b2e43278f4f70bf6808a0d822c509`;
report proof SHA-256 `a8a3225c0def28d6d9c60208052c7911a2026306e3c702a14d2594bf0ee588d1`.
The protected restored-v3 qualification records zero database writes, unchanged
production and unchanged release/access state. This proves restricted-role
application/data/report recovery, not a fresh Entra sign-in or measured RTO/RPO.
The additive private-path template passed Azure validation; its what-if and
deployment remain separate gates.

Production v2 maintenance plan is protected and valid until
`2026-10-08T17:18:48.650370Z`. Only `ibkrv2stg-autonomous-admin`,
`ibkrv2stg-flex-discovery` and `ibkrv2stg-metadata-monitor` were switched to
Manual and read back with unchanged non-schedule settings. The one-shot GP
resize is submitted; read-back reports Updating. Post-resize 208-check/report
parity acceptance is required before HA enablement, followed by post-HA
acceptance and restoration of all three exact original schedules. No data
restore, password change, website redirection or public network restriction is
part of this maintenance.

The first private-path what-if stopped on Azure's unrelated existing-resource
Ignore rows, not on an actual proposed edit. Its controller/plan are preserved.
The revised gate accepts only no-delta Ignore rows in this exact resource group
while requiring all 12 planned new resources to be Create and rejecting every
Modify/Delete/unresolved out-of-scope change. Preparation initially hit local
parallel CLI token-process timeouts before any resource creation; the revised
controller uses one in-memory ARM credential per bounded process and two read
threads. The revised plan is protected under private-administration-v2;
validation, what-if and deployment remain staged and have not restricted public
paths. Bastion/SSH local helpers are installed. VM login and build acceptance
remain unproven at this checkpoint.

Production v2 is now Ready on GeneralPurpose D2ds_v5. Read-only post-resize
execution `ibkrv2stg-onboard-render-pvxx5i0` Succeeded and qualified: all 208
viewer checks, 16 table/schema/access fingerprints and 90 report-file bytes are
unchanged against the baseline. Protected proof hashes are
`78dc22462afd67164c13f88848df9041b51ff1c5c94cd291c542539567cf8c1a`
(viewer) and `2ce6cb6706c08035552323e6d2898a091ab581057bfc7f9fb3df0dd7cd7e6b0f`
(reports). Hostname, credential, network and backup invariants hold. HA is not
yet qualified; the three frequent schedules remain paused during this phase.

Private-path deployment v2 failed before creating the VM: Azure rejected an
Allow rule using the AzurePlatformDNS service tag (only Deny is supported) and
required the Bastion subnet's standard SSH/RDP egress rule to VirtualNetwork.
The new Bastion NSG, NAT and public-IP support resources are retained as this
task's partial deployment; original application/service boundaries are unchanged.
Forward repair v3 preserves the exact owner/key/image/VM scope, removes only the
unsupported DNS rule and corrects only the new Bastion rule destination. The VM
NSG still permits only Bastion-origin SSH. Its protected plan and 22 tests reject
unrelated modifications/deletions and raw/unknown login evidence; actual Azure
forward validation, what-if, provisioning, login and build checks remain gated.

HA enablement was submitted once after post-resize acceptance. Read-back shows
ZoneRedundant/CreatingStandby, primary zone 1; this is not yet Healthy or final
application-qualified. Chrome's existing owner session reloaded the report page
successfully after the GP resize, still showing HKD and 21 MS owner-review
months with zero releases. No report approvals were submitted.

Forward repair v3 what-if was stopped because redeploying the task-created NAT
and public IPs without Azure-populated fields proposed deleting regional/default
and DDoS settings. Those changes were not executed. Forward repair v4 therefore
references the three Ready support resources without redeploying them, preserves
their security/allocation baselines, and permits only the newly created Bastion
NSG rule modification plus eight remaining new components. The earlier plans and
controllers are retained. Twenty-three local maintenance/private-path acceptance
tests pass. V4 validation/what-if must still pass before its separate one-shot
deployment; public service access remains unchanged.

V2 production now reads back Ready, GeneralPurpose D2ds_v5, ZoneRedundant
Healthy, primary zone 1 and standby zone 2. Final post-HA application/report
parity and original schedule restoration are still required before declaring
this maintenance qualified. No production failover was forced; the prior
isolated HA failover proof is retained separately.

Private-path forward plan v4 passed Azure validation and protected what-if:
eight Create components and only the exact new Bastion NSG rule Modify. The
retained NAT/PIPs and all unrelated resources are Ignore, with no proposed
modification/deletion. Its separate one-shot deployment is now being submitted;
provisioning and real login/build acceptance are not yet complete.

Final post-HA execution `ibkrv2stg-onboard-render-ihpzem6` Succeeded: all 208
viewer checks, 16 schema/data/access fingerprints and 90 registered report-file
bytes match the pre-maintenance baseline, with zero database writes. V2 is
ZoneRedundant Healthy on D2ds_v5, primary zone 1 / standby zone 2. Protected final
receipt SHA is `fae53794c802a26e64ad2de0676c38f7967d1a8efbd0add9138bf4ec0e2c2e9b`.
All three original schedules (autonomous-admin, flex-discovery and
metadata-monitor) were restored and independently read-back verified. This
qualifies this production HA change, not fresh gateway sign-in or measured
end-to-end recovery RTO/RPO, and not production v1 HA.

Private path v4 created the VM, NIC, dedicated subnets and Bastion successfully,
but Azure rejected `enableAutomaticUpgrade: true` for AADSSHLoginForLinux. The
extension and dependent owner-only VM login role are still absent. A separate
extension-only forward plan, preserving failed v4 evidence, removes that one
unsupported setting; 27 maintenance/access safeguards pass locally. Its first
preparation stopped on existing-subnet metadata drift before creating a plan or
changing Azure. That difference must be explained and qualified before the
extension-only deployment. No existing public access has been restricted;
actual Entra/Bastion login and private build remain unproven.

The stopped network comparison was traced exactly to delegation `etag` changes
on the two original subnets. Delegation IDs, service names, action permissions,
readiness, routes, ACLs and all other compared fields are identical. V5 uses a
separate semantic check ignoring only etags; the original frozen controllers
and failed evidence are unchanged. Twenty-eight safeguards pass, including
rejection of changed delegation actions/readiness and any existing-resource
Modify/Delete in the extension-only what-if. The protected v5 plan is prepared;
validation and what-if precede a one-shot deployment.

Chrome's existing owner session reloaded the live MS report centre successfully
after final HA acceptance and schedule restoration. It still shows HKD and 21
months ready for owner review with zero internal releases. This is an existing
session check, not a fresh Entra sign-in test; no report release controls were
changed.

Extension-only v5 passed Azure validation and what-if: exactly two absent
resources, the new VM's AADSSHLoginForLinux extension and the owner's VM-scoped
Administrator Login role, are Create. Both provisioned successfully; no VM,
network or application resource was redeployed. The original v5 read-back
stopped because VM GET now includes its newly created extension in `resources`.
That expected child metadata is the only baseline difference. A separate
read-back verifier accepts only this exact extension ID and approved properties,
rejects any additional/different child and any retained VM/network security
change, and binds its own hash into the protected acceptance record. The frozen
v5 controller and deployment evidence remain unchanged. Twenty-nine safeguards
pass. Infrastructure acceptance and new-VM-only host checks are in progress;
public access remains unchanged and real login/build are still unqualified.

The extension-only deployment is now Succeeded and infrastructure acceptance is
protected (receipt SHA `90e86c1f600a61bbdf417e2f5cf241571171ce0afd360bd0cf164d7fa61eaea1`).
The VM has no public IP, password authentication is disabled, and its system
identity/sign-in extension and Standard Bastion tunnel are Ready. The sole new
Administrator Login assignment is the existing owner's object ID at this VM,
not at resource-group/subscription scope; Azure's case-only resource-ID
normalization is accepted, but any other principal/scope/duplicate assignment
is rejected. Thirty local safeguards pass. Separate bounded new-VM host and
official-tool/local-build commands are prepared; no production credentials,
source data, registry push or public-access restriction is included.

The new VM host diagnostic Succeeded: password authentication is `no`, public-key
authentication `yes`, and the production database resolves to `10.50.4.4` inside
its existing private subnet. Host receipt SHA is
`812f8dbfd1d622eabaf48886ac7068a37b7d9fb9fba7bca12fa0347a6afbeb92`.
Official Azure CLI 2.91.0 and Docker 29.1.3 are installed; the network-isolated
synthetic FROM-scratch build passed, image ID
`sha256:40bec3d3c671a38fe139672f3b57c6ef7c4893b077d5bdc7a9357b98a8eb9fa0`,
receipt SHA `9383b862b4a0d956d28cf955e15f915f7f2cc02a4be3f4b6015d26a1b1c10cd2`.
This proves a local build only, not registry push, cold pull or a full release.

The first Bastion SSH CLI test returned exit zero without required remote proof
and was not qualified. Inspection of the installed official extension shows it
prepends StrictHostKeyChecking=no and a null known-hosts file, and does not return
the child SSH exit code. Later options therefore cannot establish a pinned-host
proof. That controller route is disabled. Separate qualification uses Bastion
only as transport plus native OpenSSH with first/only StrictHostKeyChecking=yes,
the management-plane host key pinned, an exact-owner short-lived Entra
certificate, an actual remote exit code/hostname/non-root/sudo proof, and bounded
temporary credential cleanup. No raw authentication output is retained. Real
login remains unqualified until that test passes; public paths remain unchanged.

Real pinned owner login is now qualified. Native OpenSSH's initial local
configuration failure was traced without exposing raw authentication output to
an unquoted certificate path containing spaces. Quoted POSIX-form paths now pass
a local `ssh -G` syntax check before requesting any certificate or connecting.
The final live test completed through the Bastion tunnel with strict host-key
verification, the exact approved Entra owner, non-root UID, expected hostname
and actual passwordless sudo. Protected login receipt SHA is
`26ad88564da408ef4fad7c7e43a9cd83f7af9fd027ae423ebb9a3ae8f9ddcb5b`;
controller SHA `b914c14d9657acc8716f905ca2be7bf6b72a391df9683b931a33ee6cfafcf9ca`.
The tunnel terminated and all test credential directories were removed, including
the narrowly validated three-file directory left by the earlier vendor wrapper.
No account password, production credential or portfolio source was changed.
Thirty-three maintenance/private-access safeguard tests and compilation checks
pass. The VM/Bastion path is qualified for owner administration and local builds,
not yet for registry push, private cold pull or full release builds.

Checkpoint: v2 production HA is Healthy and final application/report parity is
qualified; all three original schedules are restored. The new administration VM
has no public IP and its owner login/local build checks pass. Existing ACR,
storage and Key Vault public service paths remain unchanged. No registry write
permission was added to the VM identity. Registry/build and all-caller private
connectivity qualification must precede any public-access cutover. V1 production
HA, remaining network hardening and fresh gateway-sign-in/measured application
RTO/RPO remain outstanding; this does not certify the full programme complete.
MS report approvals remain unchanged: 21 months/42 files are owner-review only.

### Alpha World T methodology approval and local foundation — 2026-10-08

The owner reprioritized Alpha World T onboarding before the five remaining
infrastructure steps, then explicitly confirmed HKD and the same inception-to-
valuation arithmetic average of actual published 1M HIBOR fixings used by MS.
The earlier USD T definition is historical, not the newly approved target.
T retains the full U16088606 Greater China account NAV, including Taiwan and
original source-account cash; it does not use the filtered China & Hong Kong
sleeve or its replacement cash ledger. Other fixed-unit terms remain unchanged:
91% Greater China, 45% U16088571, -49% Hang Seng price index, -45% SPY unadjusted
official close, constant non-interest-bearing cash 58, inception NAV 100 on
2025-01-20, no daily rebalancing. Interest already in source NAV is retained;
there is no additional simulated financing charge.

Local implementation only: T reference contract is HKD (SHA
`99b9d84e8118632b59dfd73fa64c08c05af409bb05a3daa34dd2fe78173f0209`).
The named T refresh now uses the typed interpreter, converts each source sleeve
to HKD before inception normalization, rejects reference drift/invalid source
values/stale FX before derived writes, and retains previous USD NAV versions.
A dedicated read-only T analytics profile requires complete verified period
HIBOR evidence and tenant-scoped approved HKD metadata. T daily risk excludes
intervals spanning missing US, Hong Kong, Shanghai or Taiwan source-market
sessions; unknown calendar coverage fails closed. Its simulation has its own
subject/version seed and the same 252-session horizon, without extra interest.
MS's public analytics identity, approved contract, evidence and simulation output
are unchanged: contract SHA
`a257272638bf2e99626cacdfcfd3977b2cfade9036b00b485446ac38a95db7fc`,
deterministic synthetic metrics/evidence/simulation SHA
`e87ccf842a34c96e984b4796edc3e9836007149ceb925e3c51ea5a8df26556d2`.

185 local tests pass, including independent NumPy risk reconciliation, varying
FX/full-account T arithmetic, source and MS preservation, old-USD-version
retention, missing-fixing/Treasury rejection, tenant isolation, calendar gaps,
deterministic simulation and onboarding/risk/viewer regression checks. Existing
captured MS historical replay tests also pass; these do not establish T source
history completeness. Scoped diff whitespace checks pass.

No Azure resources, source IBKR observations, live subjects, report approvals,
viewer grants, MS layouts or released files were changed in this step. T's
generic production risk loader/worker now fail closed locally rather than run
the old USD/Treasury/benchmark-intersection path. These safeguards and the new
profile are not deployed. The read-only T profile can be used for private
qualification, but recurring cache/report/comparison consumers remain pending.
Next: inspect and reconcile the actual full-account T history/FX/HIBOR coverage,
then qualify the website comparisons and all monthly report outputs before a
scoped release/rollback check, deployment and separate onboarding/report gates.
The deferred infrastructure programme is neither resumed nor certified complete.

### Alpha World T reusable factsheet qualification — 2026-10-08

Both pages of the owner's uploaded T PDF were inspected as the immutable design
authority, SHA `c7c3c2f199532cdfaf8c2ecac589186b1493a44d5194b97a05d40ed5a2d34291`.
The unchanged authority is archived under `templates/alpha-world-t/`; its
versioned render contract identifies fixed design elements and monthly inputs.
This is a local system renderer, not a personal artifact-template skill (the
Template Creator workflow does not support PDF references).

The owner confirmed January 2025 throughout. Header inception is January 2025;
the approved NAV normalization/history begins 20 January 2025. The source
filename says September 2026 but its sample figures and charts end March 2026;
these sample dates, figures and embedded chart images are removed from the PDF
content rather than merely concealed. Both reporting dates, eight KPIs, actual
period-average 1M HIBOR, chart history, axes, simulation, fitted curves, reference
markers and legends now derive from a frozen, identity-checked T snapshot. HKD
capital starts at hypothetical 100,000 and the rebased path at 100. Interest is
already in source NAV; the simulation applies no extra financing charge.

The allocation diagram's source 91%/-49% branches incorrectly led to the short
and long boxes respectively. The renderer maps 91% to the 130% long full
Hong Kong/China/Taiwan sleeve and -49% to the 70% short HSI sleeve. The 45%/-45%
branches remain the 150% long US Technology & Biotechnology and 150% short
S&P500 sleeves. Boxes, caption spacing and branch geometry are uniform; the
Biotechnology spelling and page-count/footer spacing are corrected. T's title,
Taiwan wording and entire distinct main disclaimer, including its Sprout/index-
advisor paragraph, are retained. They are not replaced with MS's disclaimer.

The owner expressly instructed retention of the supplied About-this-Index prose
pending review. That prose describes dynamic rotation/below-2x leverage while
the approved calculation is fixed units with 230% initial gross risky exposure.
Its text is preserved and verified against the authority. This unresolved
narrative discrepancy remains a publication blocker, not an approved model
change or a certification that the supplied prose is accurate.

201 targeted tests pass across T/MS analytics, PDF accuracy/style/copy, monthly
artifacts, onboarding, risk and viewer access. The reusable T authority rendered
24 synthetic month-ends from January 2025 through December 2026; every output
passed printed-KPI and full-vector checks, 40 full-range histogram-bin checks,
both fitted curves, axis/legend and corrected allocation mapping checks. Short
history, missing sessions, invalid identity/currency/fixings, stale USD inputs,
wrong printed values/captions and overwrite protection are covered. First-month
curve checks reconstruct exact scales from the full sample rather than infer
them from rounded PDF ticks. Source snapshots and the authority remain unchanged.

The final private synthetic September preview is
`output/pdf/AlphaWorld_T_Template_Qualification_v1/AlphaWorld_T_Private_Layout_Preview_2026-09-30_r3.pdf`.
Both pages were rendered and visually inspected; January 2025 page one was also
visually checked for short-history chart/date/legend fit. The earlier private
previews are preserved as review evidence. These are invented layout-test data,
not actual T returns or provider HIBOR fixings; the preview is visibly marked
synthetic and not for release on both pages. Publication is explicitly false.

No Azure resource, IBKR observation, approved MS template/report, report approval,
viewer grant or onboarding state was changed. Actual full-account T history,
component-wise FX and licensed HIBOR evidence must next be reconciled. Azure
asset registration, recurring consumer qualification, scoped release/rollback
checks and separate report/onboarding approvals remain outstanding. Calendar
coverage beyond the verified years fails closed rather than inventing holidays.

### Alpha World MS title subtitle — 2026-10-08

The owner requested `Multi-Tranche Quantitative strategy` beneath the page-one
`Alpha World Enhanced Index` title in all MS monthly factsheets, matching T's
font and size. The shared MS renderer now adds the exact phrase once using the
owner T PDF's embedded ArialMT subset at 9.025 points. Its left edge aligns with
MS's existing title, with the same 14.4-point baseline gap as T. No title, chart,
calculation, benchmark, allocation, disclaimer or T output is otherwise changed.
MS layout version is now v14r14; T's layout version remains unchanged.

The existing MS Arial subset lacks required subtitle glyphs, so the small
embedded font resource is retained under `src/ibkr_etl/assets/` and included in
package data. Resource SHA is
`bfe50ba93225363b632ec7cbe75526819dd7cd692ef77763a8afdb3e54542918`;
the actual embedded font-program SHA is
`abb40c26801f1d70d3b03c3b0682eaa85fa97a084609f5714b742a8bf47a63f6`.
Both are checked; the PDF validator rejects absent/duplicate subtitle text,
wrong font, size, position or font program. There is no host-font substitution.

All 21 pinned HKD/HIBOR historical inputs, January 2025 through September 2026,
were rendered privately under `output/pdf/AlphaWorld_MS_Subtitle_v14r14/`.
Their financial calculation/evidence and copy metadata are identical to the
qualified v14r13 Azure artifacts. Every page is pixel-identical outside the
subtitle region, including all of page two. All 21 new title crops and both
September pages were visually inspected; actual subtitle font programs were
rechecked across all 21 PDFs. The frozen inputs and approved design source
bytes remain unchanged. Sixty-six targeted regression tests and compilation
checks pass, including MS analytics/historical replay and the T template suite.

Read-only Azure image checks confirm that the live website uses
`sha256:961bc36d5ddff5eff4d00476ef6e48ed9709ef8ba301e2cb1143db95e8c3502d`
and both reporting jobs use
`sha256:af5d255d4f71f762db763fa7bb8ab542e9c6f52a211c48b51cb1f44deb488fe9`.
These existing images do not include this new subtitle. The local shared
renderer applies it to future MS renders, but a scoped qualified release with
the new module/font resource and updated source bindings, followed by historical
batch/download verification, is still required for Azure. No existing cloud
report/image, report approval, source observation or onboarding state was changed.
Do not claim that the live Azure files have already been replaced, and do not
include the unqualified local T changes in the MS subtitle release.

### Alpha World MS right-aligned date headers — 2026-10-08

The owner requested right alignment of `As of` at the top of both MS pages and
matching font style for the date beneath it. Both lines now end at x=572 points
and use regular Helvetica. Their existing readable sizes are retained: label
5.8 points, date 8 points. Baselines remain unchanged (page one date 792, label
806; page two date 809, label 823). The shared MS renderer is v14r15; the approved
new subtitle remains present. T and other report families are unchanged.

The inherited date glyphs are removed from the source content in the cloned MS
pages before overlaying the replacements, including nested forms. This prevents
old hidden `As of`/date text from surviving copy/search or appearing twice in
August reports. The actual-PDF validator independently checks exactly one label
and reporting date on each page, their common right edge, regular font, size
and baseline; centered/serif, duplicated or shifted headers fail delivery.

All 21 historical factsheets (January 2025 through September 2026) were rendered
privately under `output/pdf/AlphaWorld_MS_Date_Headers_v14r15/`. Every page is
pixel-identical to v14r14 outside its requested date-header region. Financial
metrics/evidence, corrected disclaimer metadata and the previous subtitle are
unchanged. All 42 date-header crops and both September pages were inspected.
69 targeted tests and compilation/whitespace checks pass, including twelve
different month-name/date-width examples, negative header checks, MS historical
replay and T rendering regressions. Original templates and frozen observations
remain unchanged, and earlier local outputs are preserved.

The accepted local download bundle is
`output/pdf/AlphaWorld_MS_Date_Headers_v14r15_Reviewed.zip`. Its manifest explicitly
names the two date-header comparison regions; the generic subtitle-only field
is false for this date-only comparison. The earlier intermediate bundle is
preserved but is not the delivered review bundle. No Azure update, report
registration/release, activation, source-data change or viewer-access change
was performed. Both the subtitle and date-header changes still require a scoped
qualified Azure release and historical cloud batch/download verification.

## 2026-10-08 - MS v14r15 scoped Azure release qualification

Owner explicitly requested the four release/regeneration/download steps for the
MS subtitle and right-aligned regular-font dates, preserving calculations,
source data and report approvals. Frozen release attempt:
`.work/alpha-ms-headers-v14r15-20261008-r2/`. The initial incomplete preparation
attempt is retained separately and was never built or deployed.

The two qualified candidates inherit the actual installed website/viewer fixes
and reporting-worker images independently. Only the MS layout/validation
modules, subtitle module and hash-bound Arial resource are overlaid; the local
unqualified T dispatch and analytical changes are excluded. All other renderer
AST nodes match the immutable v14r13 baseline. The isolated 21-month outputs
exactly match the previously accepted v14r15 PDFs. There are 65 passing tests
locally and 65 passing native Linux tests on each Azure build.

ACR runs `ckae` (worker) and `ckaf` (website) completed successfully. Missing
CLI dispatch acknowledgements were reconciled against exact tags, source hashes
and native creation times without resubmitting either build. Qualified digests:

- Worker: `sha256:bae3b6cb43d48af8c525e57895c61b74c25e4b9bc688dd90458a2995a954e372`.
- Website: `sha256:ea6d337314269593c8bf214b22dab756ad367b3a65840814c0c65031377d6a70`.

Both exact-image scans have zero critical, high or unknown findings; worker
retains 40 medium/19 low and website 35 medium/18 low findings. Raw scans,
software inventories and source/build receipts are protected and hash-bound.
Read-only Azure qualification rendered all 21 actual active HKD frozen snapshots
with each candidate and each rollback image. All 42 candidate PDF checks/84
page comparisons passed. Website and worker PDF bytes are identical; inputs,
metrics, calculation evidence and disclaimer metadata are unchanged. Only the
approved subtitle/date regions differ from rollback PDFs. Both cloud September
pages were visually inspected using the PDF skill.

Both persisted reporting-worker images have now been updated and independently
verified, retaining their exact non-image settings, identities, credentials and
schedules. The protected website image-only cutover is in progress; do not infer
website completion until its `ibkrv2stg-web-qualified.json` exists. Rollback
templates and exact live authentication baseline are retained in deployment-v1.

Fresh read-only report inventory and the authenticated Chrome report centre
confirm MS is active, its current historical batch is approved, and zero monthly
reports are released to viewers. The next registration is deliberately limited
to 21 new factsheet generation records for owner review, preserving every old
batch, run, approval, release and file; no presentation regeneration or report
release is authorized by this layout-only cutover. Historical registration,
authenticated download hashes and future-entrypoint verification remain open
at this checkpoint. Four pure mutation-boundary tests passed for registration.

## 2026-10-08 - MS v14r15 Azure layout release completed

The scoped subtitle/date-header release is complete. The website and both
reporting workers are installed on the qualified digests recorded above.
Website revision `ibkrv2stg-web--ms-headers-v14r15` is healthy with full traffic.
Final live readback confirms the exact images, authentication, identities,
credentials, schedules and other non-image settings remain as qualified.
The candidate and previous rollback images were independently exercised against
all 21 actual frozen snapshots; rollback templates remain protected.

The one-shot registration `ibkrv2stg-onboard-render-921eamb` succeeded, creating
exactly 21 new factsheet generation records (922 through 942), awaiting owner
approval, plus one audit event. It did not supersede the approved onboarding
batch or modify any original report record, file, approval, presentation,
snapshot, source observation, lifecycle state or viewer permission. Independent
read-only execution `ibkrv2stg-onboard-render-ubd704s` verified all registered
PDF hashes and exact preservation of the frozen pre-registration state except
for the authorized new factsheet records. Zero monthly reports remain released
to viewers; no release checkbox or approval submission was performed.

Using the computer-use skill in the existing authenticated Chrome owner session,
all 21 monthly factsheets were downloaded through their actual website review
links. Every generation ID and filename matches the new registration. All 21
downloaded byte hashes exactly match the accepted cloud-rendered PDFs, and the
PDF skill validators reconfirm the subtitle and both date headers. The report
centre still displays 21 ready-for-owner-review months and zero released months;
the September presentation remains the existing generation 921. Browser
selection was restored to September after the checks.

The installed historical and normal month-end entrypoints share the qualified
v14r15 factsheet renderer, and MS has exactly one active month-end-reporting
registration. Approved template evidence resolves to the new layout. Future
monthly processing therefore uses the corrected layout, subject to the existing
source-quality and approval gates. No future observation was fabricated and no
normal future month-end event was forced merely to demonstrate the configuration.

Completion evidence is create-only and protected:
`.work/alpha-ms-headers-v14r15-20261008-r2/final-completion.json`, binding the
deployment, cloud acceptance, registration baseline/register/readback and actual
Chrome download proofs. Four registration-boundary tests were rerun successfully;
new control scripts compile and the scoped whitespace checks pass. All unrelated
local Alpha World T and analytical work remains excluded from the Azure images.

The downloadable export of the verified website files is
`output/pdf/AlphaWorld_MS_Azure_Headers_v14r15.zip` (21 PDFs plus verification
manifest), with a matching unpacked directory. Earlier local/cloud versions and
the original browser downloads remain intact. This completes only the requested
MS layout release/regeneration/download scope, not the wider improvement programme
or Alpha World T onboarding.

## 2026-10-08 — Alpha World MS readable download filenames: Azure complete

The owner approved clean download names without losing integrity or version
history. The website now returns `AlphaWorld_MS_Factsheet_YYYY-MM-DD.pdf` and
`AlphaWorld_MS_Presentation_YYYY-MM-DD.pptx` for MS monthly downloads. This covers
owner review, authorized released downloads and private onboarding-history
downloads, for existing and future months. Other subjects retain their existing
download names. Chrome can still append a duplicate-copy counter such as `(1)`;
previously downloaded local copies have not been renamed or deleted.

Only the response filename changed. Immutable storage references, full SHA-256
integrity checks and headers, access checks, report bytes, financial calculations,
layout, generation IDs, approval records and release state are unchanged. No
report was regenerated or approved, no IBKR/source or application-database data
was written, and neither reporting worker was updated.

The new image was constructed from the exact crisis-qualified installed website
parent, not the working checkout. Verified OCI layer hashes recovered the parent
source. The scoped patch adds one naming helper and changes only the download
header expression in three existing functions; their remaining logic and all
other module symbols were checked unchanged. Local isolated and native Azure
build suites each passed 108 tests, including actual endpoint permission,
integrity, original-byte and historical/future naming checks. The exact image
security scan passed with zero critical, high, unknown or blocking findings
(35 medium and 18 low findings remain; this is not a claim of zero vulnerabilities).

Website image:
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:0ab0bb3e02d4c884f689d80bfe59174e528129431d0a3dfb7d0a2e68a8abcc77`.
Revision `ibkrv2stg-web--ms-download-names-v1` is healthy with 100 percent traffic.
Protected baseline/rollback evidence retains the previous crisis-qualified image
`sha256:d857da01fa5d03dcd8d5e1214bd3e4696a88bf30bd1d25fd495d6abe6d2aada6`.
Live readback verifies preserved authentication and all non-image settings;
both workers remain on their existing crisis-qualified digest.

All 21 historical month pairs (January 2025 through September 2026) were actually
downloaded through the authenticated Chrome owner-review links after deployment.
Every one of the 42 files has the clean filename pattern, the original generation
ID, the same byte count and exactly the previously qualified SHA-256. The report
centre remains at 21 ready-for-review months and zero released months. No
approval checkbox was selected or submitted, and September is selected again.

Create-only protected completion evidence:
`.work/ms-download-names-20261008-r2/completion.json`, binding actual browser
downloads and the website screenshot. The same directory retains the source
capture, frozen build context, 108-test native log, vulnerability scan and
protected website-only deployment/rollback plan. The unsuccessful local setup
attempt remains separately preserved under `ms-download-names-20261008-r1`;
it did not build or deploy an image. This completes the approved filename scope,
not the wider improvement programme or Alpha World T onboarding.

## 2026-10-08 - MS approval typography false rejection: Azure repair verified

The owner reported that approving the September MS factsheet and presentation
failed with the return-panel typography warning. The installed release endpoint
was applying the generic account's single large Times return-value contract to
the approved MS eight-row Helvetica KPI panel. The generic validator inspected
the chart-heading area, not the MS KPI values. The existing MS factsheet was not
defective and did not need regeneration.

The scoped repair dispatches MS approval validation to its governed full-layout
and independent numerical/plot validator, using the exact frozen database
payload after tenant, subject, period, currency and SHA-256 checks. It retains
the generic typography validator for other subjects. Invalid PDFs, changed
values or fonts, mismatched inputs, corrupt snapshot hashes and existing release
eligibility blockers continue to fail closed. Access, checklist concurrency,
file integrity, immutable uploads and release audit controls were not bypassed.

The candidate was built from the exact installed readable-download-name image,
not unrelated working-checkout changes. Only the web validation dispatch, a new
MS helper and the existing layout reader's support for an in-memory PDF stream
were overlaid. The isolated local and native Azure suites each passed 119 tests,
including all 21 actual historical PDFs and the complete approval function with
its real validator. The exact image scan passed with zero critical, high, unknown
or blocking findings; 35 medium and 18 low findings remain.

Website image:
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:fc1da2c74385c9a288291bf8ca511822a45d1408823e94d6faee76496efbedd1`.
Revision `ibkrv2stg-web--ms-release-validation-v1` is healthy with 100 percent
traffic. Authentication, identities, credentials, settings and both reporting
workers remain unchanged. The exact previous website image and configuration
are retained in the protected rollback plan.

A diagnostic in the installed website used its existing approval role with a
read-only transaction. All 21 months passed the actual installed MS validator;
all 42 real Azure files, their generation IDs and frozen-input hashes exactly
match the protected originals. September's two authenticated browser downloads
also match those originals byte for byte, with the clean download names intact.
The same subject-specific validation applies to future monthly MS approvals.
No report, financial calculation, source observation or frozen snapshot changed.

Chrome was restored to September with both approved files selected and the
submit button enabled. No approval was submitted and no report was released;
all 21 months remain ready for owner review. The final release click remains
with the owner. This distinction prevents a validator repair from silently
changing sensitive report access. Alpha World T and the wider programme are
outside this repair.

Evidence is retained under `.work/ms-release-validation-20261008-r2/`, including
the frozen source context, native test log, protected scan/deployment/rollback
receipts, `cloud-read-only-qualification.json`, actual September browser-download
proof and the handoff screenshot. Earlier diagnostic console connection failures
are retained separately; they made no report or approval writes. The final
all-month diagnostic used a bounded console command and completed successfully.

### 2026-10-09 - Alpha World T actual-history qualification; template wording retained

The owner asked to onboard Alpha World T and then explicitly instructed:
"Do not change the wordings in the factsheet template." The supplied About-this-Index
copy and T-specific legal disclaimer remain unchanged. This does not resolve the
documented difference between its dynamic-rotation/below-2x narrative and the
approved fixed-unit calculation with 230% initial gross risky exposure.

Read-only Chrome inspection found T ready to start, but the installed reference
definition still described USD (hash `2f584e7e30863e5a1b7dda56560c07bd364e68d73ef7f3e2dae0084343d77037`).
No T case was started under that obsolete contract. A scoped one-shot execution
`ibkrv2stg-onboard-render-11lxylk` exported existing source inputs using a
repeatable-read, read-only transaction. It succeeded and the persistent reporting
job configuration hash remained unchanged. The private content-addressed export
is `internal-reports/review/alpha-world-t/onboarding-20261008/live-852fc76db2982ffcb7f61cdc392aa54dd529c0041c3c18c42b342ce8aa04c9b9.json`.
Its hash was checked again after local download. Capture time was
2026-10-08T23:00:49Z; both accounts had 448 NAV dates through 2026-10-07.
No existing T NAV, synthetic components or benchmark mappings were present.

The isolated SQLite replay uses full U16088606 NAV (Taiwan and original account
cash retained), U16088571 NAV, HSI, SPY unadjusted prices, broker FX and stored
qualified 1M HIBOR. Each USD component is converted to HKD before inception
normalization. The unchanged approved formula is 58 + 0.91 GC + 0.45 US - 0.49
HSI - 0.45 SPY, with component indices based at 100 on 2025-01-20. All 448
values matched an independently implemented Decimal calculation within
8.53e-14 NAV units. Repeating the application calculation produced identical
values and left copied source tables unchanged. No live financial writes occurred.

All 21 completed monthly snapshots (January 2025 through September 2026) passed
independent NumPy metric reconciliation and complete inception-to-valuation HIBOR
coverage. None had missing source-market intervals. September has 443 NAV values
and 442 daily risk intervals: total return 65.1025977507%, CAGR 34.4921037702%,
annualized volatility 16.1188564655%, Sharpe 1.6927642179, maximum drawdown
-12.7343544720%, daily VaR 1.4587649510%, daily CVaR 2.2672817964%, win rate
56.3348416290% and mean 1M HIBOR 2.6463836627%.

All 21 private actual-data factsheets rendered using the existing hash-bound T
template. The PDF validator checked printed KPIs, plotted coordinates, axes,
histogram/fits, allocation geometry, exact retained narrative and T disclosure.
All 21 first pages were raster-reviewed on contact sheets, with full-size January
and September page 1 and September page 2 inspections. These are private previews,
not released or registered production reports. Existing template source and MS
reports/calculations were not modified. The targeted suite passed 58 tests,
including 24 synthetic monthly rendering cases and private-history utility
negative controls. An initial test run encountered sandbox temporary-folder
permission errors; rerunning with a workspace-local temporary folder passed.

Evidence: `.work/alpha-world-t-onboarding-20261008/`, including source audit,
`history-v1/reconciliation.json`, 21 input snapshots, factsheet verification,
visual review and `targeted-tests.log`. Preview PDFs are in
`output/pdf/AlphaWorld_T_Private_Actual_History_20261009/`.

T is NOT active or deployed. Remaining work is the HKD T website/worker/cache
integration and scoped Azure qualification, the required T presentation identity
and supporting reference deck, all-month presentation qualification, report
registration and actual activation checks. The existing onboarding document
contract requires a presentation reference as well as the PDF model; no T-specific
reference deck has yet been supplied in this onboarding work. Do not register an
MS deck as T evidence or claim the retained narrative mismatch is resolved.

### 2026-10-09 - T date headers matched to the approved MS screenshot

At the owner's request, T layout v2 now calls the unchanged MS date-header helper
on both pages: regular Helvetica, 5.8 pt "As of", 8 pt reporting date, right edge
572 pt, 14 pt baseline separation, and date baselines 792/809 pt. This changes
styling only, not wording, the financial method, data or MS itself. The original
uploaded T template remains hash-identical and is not overwritten.

All 21 actual-data monthly factsheets were regenerated privately in
`output/pdf/AlphaWorld_T_Private_Date_Headers_v2/`; older previews remain preserved.
The T PDF validator now inspects visible font, size, alignment, spacing and text
uniqueness on each page. It still checks the retained narrative, disclosure,
numbers and plots. All 21 passed; extracted wording and calculation/input metadata
also exactly match the previous batch. September's two pages were rendered with
Poppler and visually checked against the supplied screenshot.

19 targeted tests passed, including the 24-month reusable-template render test
and rejection of bold, centered or incorrectly spaced headers on either page.
Evidence is in `history-v1/factsheet-header-v2-results.json` and
`header-v2-preservation.json` under `.work/alpha-world-t-onboarding-20261008/`.
Future T renders from the updated local renderer use this same header. Azure
deployment and T activation remain pending; this layout-only task changed no
Azure configuration, source data, account access or report approval.

### 2026-10-09 - T value-row alignment checked and visible preview notice removed

The owner requested alignment checks on January 2025 / HKD / HIBOR + Spread /
Multi-Tranche (including Taiwan), and removal of the private-preview notice on
both pages. Inspection confirmed all four values already shared a 715.7 pt
baseline and regular Helvetica 9 pt. T layout v3 centralizes this row and adds
visible-PDF validation for all four fonts, sizes, baselines and column positions.
The requested preview notice no longer appears on either page of actual-data
factsheets. Synthetic-only fixtures retain an explicit SYNTHETIC DATA label.
Removing the notice does not approve publication: the renderer input guard and
PDF publication metadata still say false, and no access/approval state changed.

All 21 actual-data monthly factsheets were regenerated in
`output/pdf/AlphaWorld_T_Factsheets_Layout_v3/`. Each passed full numerical/plot/
header/disclosure validation. Extracted text matches v2 except for the requested
notice removal; input hashes, calculation evidence and metrics match exactly.
September's two pages were rasterized with Poppler and visually inspected.
21 targeted tests passed, including 24 synthetic monthly dates, negative tests
for shifted/mismatched value-row text, and unchanged release controls.

Evidence: `.work/alpha-world-t-onboarding-20261008/history-v1/factsheet-layout-v3-results.json`
and `layout-v3-preservation.json` in the same audit root. Updated local renderer
and template contract cover future renders. Azure deployment remains pending;
no source data, other template wording or Alpha World MS artifacts changed.

### 2026-10-09 - T MS-style sections; main disclaimer explicitly unchanged

Owner requested the MS appearance for About this Index, the KPI table and both
footers, plus the allocation caption "Greater China Portfolio". During work the
owner explicitly withdrew the main disclaimer styling request. All experimental
main-disclaimer edits were reversed before delivery. Its heading, frame, complete
wording (including Sprout), original fonts, sizes and glyph positions remain
unchanged from T layout v3. The original owner template was not modified.

T layout v4 uses a hash-bound native MS static-section asset for the About block,
KPI labels/dividers/title and both footers; dynamic T KPI values use the MS 7 pt,
15 pt row spacing, colours and right edge. The T-specific KPI title uses embedded
Arial Bold at the MS size; the portable subset includes the missing capital T.
No host-font dependency was added to monthly rendering. The owner-retained
About wording remains unchanged, including its existing narrative-review gate.

All 21 actual monthly factsheets were regenerated into
`output/pdf/AlphaWorld_T_Factsheets_Layout_v4/`. Each passed numerical/vector,
date/header, copy and static-style validation. Every report's metrics, input
hash, calculation evidence, methodology and unreleased state equal v3 exactly.
All 21 main disclaimer glyph/style signatures also equal the original. Poppler
144 dpi comparison found zero differing pixels in September's main disclaimer
region (heading and frame included) against v3. September's updated pages and
January's short-history page were visually inspected through the PDF workflow.

26 targeted tests passed, including 24 synthetic monthly dates, altered
disclaimer/style rejection, and MS header/disclaimer regressions. Evidence:
`.work/alpha-world-t-onboarding-20261008/history-v1/factsheet-layout-v4-results.json`.
Experimental style assets are retained only under the local audit folder's
`superseded-style-drafts/`; the template references only the approved-scope asset.
The reusable local renderer applies the same layout to future T renders.
Azure deployment, T publication, onboarding and access/approval state remain
unchanged; no MS renderer, MS PDF or financial source data was altered.

### 2026-10-09 - MS owner-supplied T disclaimer, MS typography retained (v14r16)

Owner supplied replacement page-two disclaimer wording explicitly. The shared
MS renderer now uses that complete text, including the Sprout Asset Management
(Asia) Limited / Index Advisor paragraph, the revised benchmark-methodology
wording, the final bullet's preceding "and", and the combined final paragraph.
Zero-width copy/paste characters were removed and the non-breaking hyphen was
normalized to an ASCII hyphen; the supplied words were not rewritten.

The MS Georgia typeface, 8.999790908 pt size, 12 pt line leading, text widths,
heading, border and list indents remain unchanged. Paragraph gaps are 6 pt
instead of 12 pt so the longer text fits with its bottom glyph at 100.219 pt.
An embedded, hash-bound Georgia subset supplies the L/parentheses absent from
the old subset. Its common glyph advances match the approved MS body font;
subsetting/embedding permissions were checked. It is packaged with the renderer,
which has no runtime dependency on Windows fonts. Footers remain unchanged.

All 21 actual monthly factsheets, January 2025 through September 2026, were
regenerated into `output/pdf/AlphaWorld_MS_Disclaimer_v14r16/`, with a ZIP of the
same name. Every PDF passed full numerical/plot validation and exact disclaimer
wording/order validation. All 21 have the same page-two disclosure drawing
stream. Poppler pixel comparisons against the accepted Azure v14r15 copies
confirm page one is identical and page two differs only inside the disclaimer
body. Financial metadata, source inputs and authority PDFs are unchanged.
September's full pages were visually inspected using the PDF workflow.

Evidence: `output/pdf/AlphaWorld_MS_Disclaimer_v14r16/manifest.json`, plus all
21 before/after raster pairs in `tmp/pdfs/alpha-ms-disclaimer-v14r16/`.
The Alpha World T template, style asset and factsheets were not changed.
The updated local reusable renderer covers future monthly renders, but Azure
remains unchanged: a scoped v14r16 release, qualification/rollback checks and
historical artifact registration are still required before website replacement.
No report approvals, viewer access, activation or broker-source state changed.

40 targeted regression tests passed: MS copy, date headers, PDF accuracy,
subtitle and allocation layout, plus T's unchanged reusable-template suite.

### 2026-10-09 - Authorized v14r16 Azure release qualification in progress

Owner answered yes to deploying this accepted disclaimer, replacing the 21
historical factsheets and updating future rendering. Release evidence is frozen
under `.work/alpha-ms-disclaimer-v14r16-20261009-r2/`. The first local attempt is
retained separately: its test harness omitted a relative-path support script;
no Azure build or live update used that attempt.

The website parent is digest `fc1da2c74385c9a288291bf8ca511822a45d1408823e94d6faee76496efbedd1`;
both worker parents are `1b1583be5be5296e9362220193152426aa341d3db78c26b8ec1eaf69d079eebe`.
Every cached parent image layer was hash-verified before extracting source.
Only the MS disclaimer module, its embedded font PDF and the renderer version
constant are changed. The unqualified T dispatch in the root working tree is
not included. Website operational probes/settings added since the previous
renderer release are explicitly preserved, not reverted to the old release.

Isolated Windows and native Linux suites both passed: 122 website tests and
95 worker tests. Each candidate's 21 local replay PDFs matched the accepted
v14r16 pages exactly. Native ACR builds `ckar` (website) and `ckaq` (worker)
succeeded. Missing CLI acknowledgements were reconciled to the original build
intent/time/tag; no duplicate build was dispatched. Both exact images passed
the protected vulnerability/SBOM gate with zero critical, high or blocking
findings. Existing medium/low findings remain recorded, not represented as zero.

Read-only Azure rollback rendering passed for all 21 months on both parent
images. Candidate cloud replay and PDF acceptance are in progress. This entry
does not claim a live cutover, historical registration or download completion.
Existing approval history is preserved; newly generated bytes remain subject
to the normal owner-release checks.

### 2026-10-09 - v14r16 deployed; all 21 website downloads verified

The authorized disclaimer-only cutover is complete. Website revision
`ibkrv2stg-web--ms-disclaimer-v14r16` is healthy and serves 100 percent of traffic.
Its image is `sha256:fdc6e0c0606a10b2be11cad67bb24a7a0664a7c9f6f1ef10f19165759dadc56a`.
Both reporting workers use
`sha256:c8865ffd07b1c353a16820422190a01573a642190fd7bc8946c33e0f8aa66f0a`.
Authentication, identity, credentials, schedules, operational probes and all
non-image settings match the protected immediate pre-cutover baseline.

Both candidates and both rollback images replayed all 21 actual active HKD
snapshots using read-only transactions. The 42 candidate PDFs passed numerical,
copy, subtitle/date-header and full-page pixel checks: only the requested
page-two disclaimer body changed. Website and worker PDF bytes are identical
for every month. Both September pages were visually inspected with the PDF
workflow, including the retained MS Georgia disclaimer typography.

Exactly 21 replacement factsheet generation records (1027-1047) and one audit
event were added. A separate read-only verification checked every stored hash
and proved preservation of prior records, original files, source data,
presentations and approvals. One active month-end reporting registration uses
the same qualified shared renderer as historical reporting. No fabricated
future period or normal scheduled job was forced.

The existing authenticated Chrome owner session downloaded all 21 replacement
factsheets from the website. Every download matched the accepted Azure hash.
Clean names use `AlphaWorld_MS_Factsheet_YYYY-MM-DD.pdf`. The verified export is
`output/pdf/AlphaWorld_MS_Azure_Disclaimer_v14r16/` and its same-name ZIP.

Publication boundary: these are new immutable factsheet versions awaiting the
normal owner release approval. No approvals were submitted or revoked. The
September replacement is shown as Not released, while its unchanged PowerPoint
remains released; the prior September factsheet release history is retained.
Do not describe this deployment as approval of all 21 new versions for viewers.

Protected final evidence:
`.work/alpha-ms-disclaimer-v14r16-20261009-r2/final-completion.json`.
Its status is `ms_disclaimer_v14r16_deployed_and_download_verified`.
MS calculations, Alpha World T, PowerPoints and onboarding state are unchanged.

### 2026-10-09 - T layout v5 locally qualified against MS v14r16

At the owner's new request, T now uses the latest accepted MS page-two
disclaimer typography and single-line four-column information-band headings.
This supersedes the earlier instruction to preserve the entire disclaimer
appearance, but preserves all T disclaimer wording. The risk-profile value
remains `Multi-Tranche (including Taiwan)`. Header values use a shared baseline
and size; the frozen MS style includes its native labels and separators.

New immutable style asset: `templates/alpha-world-t/ms_style_sections_v5.pdf`,
SHA-256 `a284badf39281008228e5579a5277bec14188014998c55deac7c45537d64ed18`.
The hash-bound renderer, PDF validator and render contract now use T layout v5.
The owner template and old v4 style/output files remain unchanged.

All 21 actual monthly PDFs from January 2025 through September 2026 passed
numerical, text and layout validation. Financial metadata and all input hashes
are unchanged. Raster comparison proves no changes outside the two requested
regions; every disclaimer region matches the accepted MS v14r16 pixels after
normalizing only the QA reference page box to T's slightly different page size.
January and September pages were also visually inspected. All 42 targeted
tests passed, including synthetic monthly rendering through December 2026.

Deliverables: `output/pdf/AlphaWorld_T_Factsheets_Layout_v5/`, its verification
manifest and same-name ZIP. Reproduction tools are
`scripts/prepare_alpha_world_t_style_v5.py` and
`scripts/qualify_alpha_world_t_layout_v5.py`.

Status: local qualification only, not deployed to Azure. Future local T renders
use the updated reusable layout. MS, source data, calculations, approvals and
onboarding state are unchanged. Existing T narrative-review and publication
gates remain in force; this typography update does not authorize publication.

### 2026-10-09 - T layout v6 restores the KPI blue divider

The owner identified the missing blue divider beneath the T KPI strategy title.
The earlier style extraction had clipped out y=665..681 between its table and
heading regions. V6 restores only the native MS v14r16 divider strip, preserving
its original vectors/colors rather than approximating it. Frozen style SHA-256:
`c135be0c7208a3ceddc0dee7f8c64f38f2546a7d9e5ae701cd510c4ce1bb2278`.

All 21 historical T PDFs were regenerated into
`output/pdf/AlphaWorld_T_Factsheets_Layout_v6/` and the same-name ZIP. Every
divider crop matches the accepted MS reference pixel-for-pixel; every other
pixel on both pages is unchanged from T v5. Financial metadata, source hashes,
disclaimer wording and publication gates are unchanged. January and September
pages were visually inspected. A regression test explicitly rejects reuse of
the earlier frozen style with the missing divider.

Validation: the 42-test targeted suite passed, including monthly rendering
through December 2026; the additional missing-divider regression test also
passed (43 tests total).

This is a local reusable-template correction, not an Azure deployment or report
approval. Previous artifacts remain intact. Reproduction and evidence are in
`scripts/prepare_alpha_world_t_style_v6.py`,
`scripts/qualify_alpha_world_t_layout_v6.py` and the output verification manifest.

### 2026-10-09 - T layout v7 matches MS KPI subtitle color

At the owner's request, only the color of `Alpha World T Enhanced Index`
beneath the KPI heading changed. The renderer now uses native MS v14r16
ICC-based components `(0.3216, 0.3373, 0.3686)` with the exact embedded profile
SHA-256 `2b3aa1645779a9e634744faf9b01e9102b0c9b88fd6deced7934df86b949af7e`.
The existing title font, size, position and v6 blue-divider style are unchanged.

All 21 actual monthly PDFs passed numerical and visual qualification in
`output/pdf/AlphaWorld_T_Factsheets_Layout_v7/`; its same-name ZIP contains the
reports and verification manifest. Rendered subtitle gray matches the MS PDF
reference. All pixels outside the title region, including both-page footers,
disclaimer and blue divider, are identical to v6. Inputs and financial metadata
are unchanged. January and September pages were visually inspected.

All 44 targeted tests passed, including future monthly rendering and a new
negative test rejecting black or an incorrect gray subtitle. The PDF validator
checks the actual title paint components and ICC profile, not metadata alone.
Reproduction: `scripts/qualify_alpha_world_t_layout_v7.py`.

Local reusable-template update only: Azure, approvals, source data, MS and
onboarding state are unchanged; existing T publication gates remain in force.
