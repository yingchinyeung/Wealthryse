# Approved upgrade programme — execution record

Owner approval: all eight items in `SYSTEM_AUDIT_2026-10-01.md`.
This record distinguishes deployed work from tested foundations and outstanding
acceptance. Approval of the programme is not proof that all eight are complete.

## Agentic deployment cancelled by owner

On 6 October 2026, the owner cancelled the agentic AI deployment programme.
At 11:27 UTC, the two active one-shot qualification process trees were stopped;
fresh process inspection found no matching driver remaining. Read-only Azure
checks returned ResourceNotFound for both prospective jobs,
`ibkrv2stg-owncommit-20261006` and `ibkrv2stg-owntmeta-20261006`.
No deployment, trial restart or subsequent agentic stage is authorized to
continue under the cancelled plan without a new explicit owner request.

Step 3 remains incomplete and unaccepted end to end. Step 4 did not start.
Existing services and ordinary portfolio processing were not stopped. Source
candidates, protected evidence and existing Azure resources are retained; this
cancellation is not an infrastructure deletion or rollback request. Received
IBKR source data remains unchanged. The earlier non-agentic upgrade history is
retained separately and is not evidence of agentic deployment completion.

## Website UI cleanup after cancellation

At the owner's subsequent request, the cancelled rollout's website UI was
removed on 6 October 2026. The AI Operations navigation entry, Codex readiness
panels and specialist activity inbox are no longer rendered. Bookmarked
`/ai-operations` requests return an authenticated HTTP 410 retirement notice;
the former setup/inbox APIs also return HTTP 410 without reading historical
records. Existing Accounts, onboarding, portfolio analysis and the earlier
approval-controlled Administration workspace remain available. Historical
evidence is retained, not deleted.

The website-only image overlay passed its exact live-source baseline check and
79 Linux UI/authorization checks. Full local regression passed 2,980 discovered
tests with two existing skips; repository policy passed with 293 tracked files,
829 scanned source files and 59 contiguous migrations. Two failed build attempts
were retained without promotion; the successful ACR run was `ck87`.
Its immutable image digest is
`sha256:5811e96926d7582e744d22967ba09b3e80558b010ad6a616e49993e39e1f2ec1`.
Revision `ibkrv2stg-web--0000113` is healthy, active and running. Protected release
verification passed at 11:46 UTC, receipt
`25d36b315ef205135e236f559a323a0e018b38ff1cd31e598a3964eaacdcc77c`.
Authentication and all non-image application settings are unchanged. The prior
image and revision are retained for rollback. Signed-in browser inspection
verified the cleaned Accounts/Administration navigation and the retired page.
No schedule, permission, reporting-template or received IBKR data change was
made. This cleanup does not resume any cancelled agentic stage.

## Progress retained at cancellation

**Step 2 — isolated database integration, TLS and fresh restricted-login/model
credential-isolation qualification — accepted on 5 October 2026 at 14:53 UTC.**
The final protected acceptance and additional separate-process native/protected
readback passed, the latter at 14:55 UTC. **Step 3 started at 15:02 UTC and is
now cancelled; it was not deployed or accepted end to end.** Its first durable
execution-control source checkpoint passed full QA and protected readback at
15:23 UTC, as recorded below. Two failed PostgreSQL fence trials are retained.
The separately qualified authority-adapter correction then passed all nine live
SQL checks and 11 permission denials on the private synthetic host at 17:18 UTC.
Fresh independent readback passed at 17:22 UTC. This accepts only the isolated SQL-control
substage, not real worker receipts, process cessation or website integration.
The separate worker-audit source/QA checkpoint passed at 17:50 UTC, with fresh
protected/native readback at 17:53 UTC. Its writer/verifier contract is tested
synthetically but not yet live-qualified or enrolled in production reporting.
The separate audit SQL trial subsequently passed six live checks but failed its
final connection-revocation assertion. Its failed execution and
fresh readback are retained. The separately scoped correction's live trial
also failed, during the predecessor
catalog comparison before behavioral checks. Both failed trials are retained
without restart. The separately scoped read-only catalog diagnostic has now
passed full source QA and live evidence collection: three fresh connections
produced the same fully keyed current catalog fingerprint. Separate fresh
protected/native readback passed at 21:14 UTC. This establishes a current baseline, not the cause of
the historical comparison failure. The prospective v3 audit SQL/revocation
trial subsequently passed all eight checks and 16 permission denials at
21:59 UTC; its separate fresh-process readback passed at 22:04 UTC. The corrected
separate archive writer and independent verifier subsequently passed live
qualification on 6 October at 01:51 and 02:22 UTC respectively. Exact-version
readback and actual reader append denial were verified. This does not yet bind
the archive to a real SQL-committed worker receipt. The native process-tree job
passed registration and started once, but failed a bootstrap assertion before
the CLI/process-tree cases. Its separately protected diagnostic locates the
combined Linux/Python-version assertion at launcher line 5. The assertion's
exact Python patch was borrowed from the distinct SQL image. Independent live
metadata qualification subsequently proved that the Codex image runs Python
3.12.15, not 3.12.14, at 04:12 UTC on 6 October. The prospective native-v2 trial
pins that complete descriptor and executable hash. The failed run is not
restarted. Metadata qualification does not accept process cessation. The dependent
real SQL/archive composition driver was stopped with no effects. Website
and daily-ledger adapters are protected source candidates, not deployed features.
The distinct native-v2 trial passed live offline process-tree qualification
at 05:51 UTC on 6 October. Its dependent SQL/archive-controller-v2 one-shot
driver subsequently stopped before effect intent. Controller-v3 retained a
protected preflight but did not reach job creation. Its saved enriched context
still failed byte-identical launch reconstruction. The SQL/archive composition
is not accepted; the additive controller-v4 qualification was stopped on
owner cancellation before its prospective Azure job was created.
An additional signed owner/service authentication and task-receipt schema
candidate passes 25 local checks, but no API enrollment, live schema or signed
website integration is implied.
Real protected worker receipts, runtime/fence composition and signed website
integration remain outstanding.
On 6 October, the dedicated Owner Tasks API creation and URI update passed
protected readback at 04:40 and 04:51 UTC. Owner-client creation passed protected
readback at 05:02 UTC. The API service principal passed protected readback at
05:13 UTC; the client service principal passed protected readback at 05:24 UTC.
Final independent registration verification passed at 05:30 UTC, receipt
`5b7dc0480e145b0edbc1ae8fa6cf2c874d19e165891b4c790c28cd5aa5d57ab2`.
Registration alone does not accept a signed
owner request. The prospective task-metadata SQL/archive composition and its
exact-version protected source packaging pass 54 local tests. An earlier broader
selected candidate regression run passed 309 tests without failures or skips.
The native-v2 process trial passed complete protected result qualification,
receipt `02929d5a1d1a1f2247874490346af0ed2e173d0e3da98df48a4326c3e17b1ad6`,
version `2026-10-06T05:51:17.7937295Z`. Actual pinned CLI success, cancellation
and deadline cases each proved root wait, pidfd exits, two empty scans and
kernel ECHILD. This accepts offline native cessation, not its live binding to
PostgreSQL fences or a website task. The worker-controller-v2 later stopped
before any effect intent because its persisted launch plan could not rebuild
to identical bytes. Read-only Azure GET confirmed the job absent. Its unused
dependent metadata waiter was stopped. Separately versioned canonical launch
corrections preserve the original worker sources, namespaces and authority;
the worker-controller-v3 preflight remains unused and is not execution acceptance.
The latest selected candidate/driver regression suite passes 495 checks across
48 modules without failures or skips.
The combined worker SQL/archive trial passed protected preflight at 06:17 UTC;
that preflight does not accept the later failed dispatch. Corrected task metadata
composition must wait for the new worker trial's accepted final result. The cookie bridge,
task-bound cessation archive adapter and server-held session vault have
separate protected source checkpoints; they are not live website acceptance.
The task-bound native telemetry/CLI candidate and the server-side device-session
acquisition/HTTP candidate subsequently passed protected source qualification
at 06:59 and 07:07 UTC respectively. Controller-v3 passed protected preflight
at 07:08 UTC but its host driver is no longer running. Read-only reconstruction
of that actual saved plan identifies the final enriched context literal as the
remaining byte mismatch; read-only Azure verification confirms that its planned
job was not created. Controller-v4 canonicalizes the final literal, including
the archive fields, without changing the original worker or its authority.
Its protected source qualification passed at 11:03 UTC. The dependent metadata
controller-v3 source qualification passed at 11:06 UTC. Both one-shot drivers
were stopped on owner cancellation. Metadata effects never became authorized
by an accepted worker final result. Neither source qualification accepts execution.
Signed-session status/logout and sign-in UI controls passed protected source
qualification at 11:14 UTC. A native readback transport passed protected source
qualification and authenticated read-only comparison against the accepted
offline execution at 11:19 UTC. No real Microsoft device code was requested,
no live website task schema or route was installed, and no production or IBKR
source data was changed by these substages.
No result from pending trials is treated as end-to-end acceptance.
Step 4 has not started.

Unfinished Step 3 gates retained for reference only:

| Gate | Accepted work | Remaining acceptance |
| --- | --- | --- |
| Protected task and worker receipts | Restricted SQL and archive permissions; independent exact-version archive readback | Live combined SQL/archive receipts and task links; qualification cancelled before prospective job creation |
| Durable runtime control | Live pinned CLI success, cancellation and deadline cessation; standalone PostgreSQL fencing | Actual separated-runtime/PostgreSQL fence integration and fault acceptance |
| Owner website flow | Dedicated Owner Tasks API registration; tested fresh-authority, signed HTTP and cookie-session adapters | Real server-held session acquisition, restricted live task-store wiring, staged website deployment and signed-owner end-to-end trial |

- All eight fresh restricted-login checks passed, including 21 denied privilege
  probes, tenant/owner isolation, idempotency/CAS and fresh authority revocation.
- Positive certificate/hostname verification used TLS 1.3. The wrong-hostname
  test failed for the required TLS reason, not generic authentication/network failure.
- Complete provider inventories were reviewed. Provider privileges remain
  present, not falsely described as denied. The accepted boundary is trusted
  fixed-scope SQL plus separate model compute without SQL credentials or tools.
- Azure denied the model identity's exact test-secret and server-management
  requests. Five same-UID process probes were denied both before and after one
  successful fixed, tool-free Codex/Azure OpenAI turn.
- Full QA discovered 2,737 tests: 2,735 passed and the same two Windows-only
  fixtures were skipped. Compilation and repository policy passed.

This closes the dedicated private-host, synthetic integration qualification.
It does NOT install production migration 059, accept signed website-owner
authentication or real task-receipt integration, activate the owner runtime or
six specialist runtimes, or certify arbitrary tools/provider routines. Those
remain subsequent deployment/acceptance work. No IBKR-received rows were read or
changed by this qualification. The original eight-upgrade programme is not
therefore declared complete; Alpha World MS remains paused.

### Step 3 durable task execution controls 5 October 2026

The owner's instruction to proceed once Step 2 completed was recorded after a
fresh verification of its protected acceptance, current sources and Azure
resources. The create-only start receipt is
`b756bc93add03d53ef97cfa473dc7b31f0ca98067bc28ed9369efd41949f3276`,
version `2026-10-05T15:02:15.7499275Z`. The historical Step 2 receipts retain
their original false Step 3 flags; this later record establishes the start.

Four additive files implement and qualify a durable execution fence around the
existing worker callback contract. A fence is a persistent reservation that
prevents the same logical task from launching again after a crash, even with a
different attempt identifier, lease or request hash. All 803 previously accepted
source hashes remain unchanged; the new manifest contains 807 files.

- The reservation and worker occupancy are committed atomically before audit
  and inference callbacks. A lost acknowledgement cannot create a fresh winner.
- A worker remains occupied across process restarts until a matching,
  independently verified stopped or not-started outcome is committed. Unknown
  cessation persists quarantine before the outcome audit callback, even when
  that callback fails. There is no reset, expiry, delete or automatic retry API.
- Fresh owner authority, lease and cancellation checks remain at each execution
  boundary. The store separately resolves current worker authority. Audit
  callbacks must still provide genuinely independent protected receipts; the
  fence does not manufacture those claims from its own SQL commit.
- Fixed transition history contains identifiers, hashes and states only.
  Received IBKR data is neither queried nor modified. Successful private
  computation is not report release or result-delivery authorization.

The 27 new tests passed, including four genuinely separate processes competing
for exactly one reservation, fresh-coordinator replay attempts, cancellation,
revocation, lost audit acknowledgements, uncertain starts/stops and persistent
runtime blocking. Full discovery found 2,764 tests: 2,762 passed, with only the
same two pre-existing Windows fixture skips. Compilation and repository policy
passed: 293 tracked files, 788 scanned source files and 59 unchanged migrations.
These are synthetic SQLite/coordinator results, not live PostgreSQL, process
cessation, authentication or independent audit-service acceptance.

The protected source/QA checkpoint is
`1734113b4264995979722db36c45c679fdb1d33166553d645b1b5925f6c435b4`,
version `2026-10-05T15:23:52.6842861Z`, status
`step_3_durable_fence_source_qa_verified`. It binds the Step 2 acceptance and
readback, the Step 3 start, the 807-file manifest, complete QA, and before/after
native verification. Only a reviewed detached source-context copy accepts the
exact four additions; predecessor functions, globals and receipts are not
retargeted. Existing jobs, identities, grants and the accepted model execution
remain unchanged. No native job was created or restarted, no model call or live SQL
operation was dispatched, and no production migration was applied.

A separate process subsequently reread the protected checkpoint and reverified
the current 807-file source manifest, preserved 803-source predecessor and native
resources/execution. The fresh readback receipt is
`51677cea42db49c6dbbe45eab8112ccdcd21fb338e4843b9a642670ea6dd8854`,
version `2026-10-05T15:26:34.6803239Z`, status
`step_3_durable_fence_fresh_readback_verified`. It retains `step_3_complete=false`
and does not accept a live worker, protected worker-audit adapter or website
integration.

The PostgreSQL schema is deliberately in
`scripts/owner_task_execution_fence_schema.sql`, outside automatic production
migrations. It includes forced tenant/worker RLS, immutable task bindings,
non-resettable quarantine and append-only history. It creates no login or grant.
Its live behavior and restricted permissions still require isolated acceptance.

The design keeps durable state and validation at the actual side-effect boundary,
consistent with [OpenAI's guardrails and approval guidance](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals).
This does not add Agents SDK execution, inherit Codex Auto-review, change the
pinned CLI/model, or qualify a new authentication provider.

### Step 3 isolated PostgreSQL execution-fence qualification, 5 October 2026

The owner's instruction to finish Step 3 before Step 4 was recorded after fresh
protected and native verification. The create-only live-start receipt is
`ced04b4c3581dd37f122e12769bd57064e7f028d87565d5fda7aa9416956cb53`,
version `2026-10-05T15:38:46.7976077Z`. It explicitly retains
`step_3_complete=false` and `step_4_started=false`.

Four further additive files prepare a fixed one-shot exercise on the existing
private test host, using a separately named synthetic database
`owner_codex_fence_20261005`, private schema and three new restricted roles.
All 807 previously accepted source hashes must remain unchanged. The original
fence DDL is preserved; additive constraints bind each event to its exact
tenant, owner, task, worker and current state. The trusted controller validates
the actual execution template as well as the job definition. Protected intent
and create-only reservations precede the new job and its single start; consumed
effects cannot be restarted by this controller.

The 23 new offline tests passed, including the actual packed module graph
imported in a separate process without a repository path or SQL calls. Full
regression discovery found 2,787 tests: 2,785 passed and only the same two
pre-existing Windows fixtures were skipped. Compilation and repository policy
passed (293 tracked files, 792 scanned source files, 59 unchanged migrations).
Fresh native guards passed before and after QA. The verified create-only
preflight receipt is
`f5fc38a91ff0550c36198e9a2a22fbee9fe0c7102692e7ea5a5426d3e7e47cd6`,
version `2026-10-05T16:07:06.8568775Z`, binding the 811-file manifest and exact
manual job definition. No live PostgreSQL result is claimed by these checks.
The SQL exercise deliberately uses synthetic receipt fixtures, not the separate
worker-audit service. Even a successful SQL trial will not prove real protected
worker receipts, signed-owner authentication or runtime process cessation.

This stage neither reads nor modifies IBKR-received data, installs production
migrations, activates inference or changes existing schedules. No additional
Azure identity or role assignment is planned for this isolated SQL exercise.
The new job definition was dispatched once after protected intent and a
create-only reservation. Its protected acknowledgement is
`1f502b4487e2cafe736b44c5b70f9c4c0d072a9febf9e2f0c8d86c155af08656`,
version `2026-10-05T16:09:27.6930328Z`. Native job acceptance passed, receipt
`fed1bf9025dd6e37e03acf20f0d3295ca5bc1765413691392d91ba3c54f0977d`,
version `2026-10-05T16:10:50.9345131Z`. The single start acknowledgement is
`33337ac72284262b2d9a37ab8440e5a28f6acfc9acb7281fce9cb037f7340c25`,
version `2026-10-05T16:13:07.1147361Z`.

Execution `ibkrv2stg-ownfence-20261005-i3orqku` failed after installation,
during its first restricted-login check, with `TypeError`; no behavioral check
was accepted. The original collector also rejected two actual Azure execution
defaults: an empty `initContainers` list and a secret reference scoped to the
exact job name. A separate read-only forensic capture preserved the unmodified
native response and full bounded console family, validating only those exact
defaults in a detached shadow. Its protected receipt is
`1dd0e488fcf478cc457d3adbda411f15723b4631d66209e70a9058461ba13883`,
version `2026-10-05T16:21:04.9207102Z`. It explicitly rejects live SQL acceptance.
No source or production row was read or modified and no model was called.
The failed job, execution, database, roles and all 811 source hashes are retained;
the consumed operation is not restarted or reset.

Three additive files define a deterministic corrected artifact derived from the
exact frozen worker/controller hashes. Existing modules, globals, files and
receipts are not patched or retargeted. The separate physical scope is job
`ibkrv2stg-ownfenc2-20261005`, database `owner_codex_fence_20261005_v2`, schema
`owner_codex_fence_trial_v2`, operation
`3f4efdd4-a3d7-4167-97c9-c8da9328fd9b`. Its new source manifest must preserve all
811 predecessor files and contain exactly the three reviewed additions.

The corrected TLS check requires the actual restricted LOGIN and registered
host/database, reads its own backend statistics under that LOGIN, and restores
the NOLOGIN capability inside a transaction/savepoint before fence operations.
It rejects administrator context, missing statistics, false encryption and
unknown protocols. Reading statistics under the effective NOLOGIN capability
can filter out the authenticated backend row; that is consistent with the
observed `TypeError`, but the retained original output does not identify its
line. The new live trial measures capability/login visibility to test this
explanation rather than asserting it from the error class alone. Redacted
failure locations include function names/line numbers, never exception text,
stack locals or credentials.

Twenty new tests passed, including actual generated bundle import with SQL
blocked, recipe/hash rejection, preservation of predecessor globals, exact new
scope, role restoration on failure, and rejection of unregistered execution
defaults. Full discovery for the corrected artifact found 2,807 tests: 2,805
passed and the same two pre-existing Windows fixtures were skipped. Compilation,
policy (293 tracked files, 795 scanned source files, 59 unchanged migrations),
before/after source and native verification passed. The corrected create-only
preflight receipt is
`f462831c30cac558a3d76ee5ae9b00b4b8cba3ade7c3791d03217b31160c9f68`,
version `2026-10-05T16:38:32.7556913Z`, binding the 814-file manifest, exact
generation recipe and new immutable job payload. Native job acceptance passed,
receipt `89276d09cf2b0f6a56ec6f22cd8cbd87929255d326f9ac2c0d33ae12a6381b0f`,
version `2026-10-05T16:43:50.4386951Z`; the single start acknowledgement is
`4cc21847ef0721e6949c58e8f5a453e72392d28233a2eb29c2e49900ef9e79eb`,
version `2026-10-05T16:46:13.3430743Z`.

Execution `ibkrv2stg-ownfenc2-20261005-cperifg` failed after the first seven
ordered checks passed: restricted login/TLS/ACL, durable reconstruction,
three-connection reservation race, tenant/worker RLS, immutable/append-only
denials, launch/stopped-state guards, and persistent quarantine/rearm denial.
The actual failure was `fence_revocation_denial_invalid`, in the fresh worker
revocation check. Protected complete bounded log/native evidence is
`4c35037b993a36716b604a9e36657687ac2ab77c9cdcfc6e673504fb60bc83ec`,
version `2026-10-05T16:53:26.7923254Z`. Seven partial passes are not acceptance
of all nine checks or Step 3. The failed job/database/roles and frozen 814-file
source manifest are retained, not restarted or reset.

Source inspection identifies the adapter mismatch: constructing a
`WorkerAuthority(enabled=False)` raises `OwnerTaskError` before the execution
fence can turn a missing current capability into its fixed `access_denied`
result. The adapter must return no authority for disabled or missing rows;
the fence must still reject the operation. The correction does not permit
revoked workers or accept a generic storage failure as revocation proof.
An independent new-process protected/source/native readback of the failed
second trial passed, receipt
`4989d526c5fc2aa8d019d08012c706e10cb6bc12cc9b8714a481837361a6f8e2`,
version `2026-10-05T16:56:43.7987392Z`. A prior readback script completed its
native guards but referenced the wrong local manifest key before saving;
that script error performed no effect or acceptance. The saved readback uses
the actual `sources` field and verifies the exact failed execution.

Three further additive files implement a separately named authority-adapter
candidate, preserving all 814 preceding source hashes. It derives exact new
worker/controller artifacts from the frozen recipe, without patching earlier
modules, globals, files or receipts. Scope: job `ibkrv2stg-ownfenc3-20261005`,
database `owner_codex_fence_20261005_v3`, schema `owner_codex_fence_trial_v3`,
operation `471be18f-27ab-4eaa-967b-d499a6bf39b0`. Missing or disabled fresh
worker rows return no current capability; enabled identities still undergo
the existing typed validation. All nine SQL checks and the exact revocation
denial expectation remain unchanged. All 13 focused regression tests passed,
including existing-reservation revocation, rollback without any reservation
write, actual generated module-graph import without SQL, frozen predecessor
hashes, exact resource/evidence scopes, and retained activation restrictions.
Full protected preflight passed: 2,820 tests discovered, 2,818 passed and the
same two pre-existing Windows fixture skips; compilation and repository policy
passed (293 tracked files, 798 source files, 59 unchanged migrations). The
817-file manifest preserves every preceding source file. Protected preflight
receipt: `2ba46962e46fad08ec3b83b4af73bd857265ca1eef103a561c4236c6d3589c08`,
version `2026-10-05T17:08:13.7745197Z`. The new job acknowledgement is
`a97df747dd6ee3614806aa22fc7b342a8a62769bd904dea250541bb0d9351b9c`,
version `2026-10-05T17:12:03.3996365Z`. Native job acceptance passed, receipt
`0cde8a0d07a7cbb9d586704c85933d7f290e91f9c9465506776f66ec8a4690f7`,
version `2026-10-05T17:13:40.5369466Z`. The single start acknowledgement is
`ab9c70ebc02b46c0b01463790efb1e9d99413275105af51426cd7e873bb8425a`,
version `2026-10-05T17:16:38.0705214Z`. Execution
`ibkrv2stg-ownfenc3-20261005-44x0ck0` succeeded. All nine ordered live checks
passed, including 11 privilege denials, fresh worker revocation and unchanged
predecessor metadata (hash
`901fee5b75ac832b22d885d4a8b87a08d4b7bba552bcc35295f9c7425a0e624a`).
The actual restricted LOGIN verified TLS 1.3 with certificate/hostname checking;
its own statistics row was invisible under the NOLOGIN capability and visible
under the authenticated LOGIN, confirming that visibility behavior rather than
claiming an administrator connection as TLS proof.
Protected full bounded worker/native observation receipt:
`b5a4260f267360df67996a33537e0286983bd4eadbcb158d3227ef2703cd80f0`,
version `2026-10-05T17:18:57.5952889Z`. Only the isolated restricted SQL fence
is accepted. Synthetic receipts are explicitly labelled; real protected worker
receipts, actual runtime process cessation and website owner authentication
remain false. No model was called, no agent activated, no production migration
applied and no IBKR-received row read or modified.

A separate process freshly reread the protected preflight, job acceptance,
start acknowledgement and full observation, rebuilt the exact generated job
payload, verified all 817 source hashes and native permissions, and confirmed
that the exact successful execution is the job's only execution. It also
rechecked the two retained failures and preserved predecessor jobs/identities.
Final isolated SQL-substage readback receipt:
`de5a2ca52211e823c62d756b3d2c476c1b75d99d8f597f0c5b1fd9b11743d6b1`,
version `2026-10-05T17:22:04.7036364Z`, status
`step_3_isolated_sql_fence_fresh_readback_verified`. Its scope explicitly remains
`isolated_sql_substage_accepted=true`, `step_3_complete=false`,
`step_4_started=false`; real protected worker receipts, actual process cessation
and website owner authentication are not accepted by this record.
The original nine SQL checks remain unchanged apart from authenticated TLS
observation; real audit receipts, actual process cessation and website owner
authentication are still separate acceptance gates.

### Step 3 separate worker-audit source qualification, 5 October 2026

Before this extension, a fresh read of the final SQL-substage receipt and its
native guards confirmed the 817-file predecessor, existing permissions and
retained executions. Four additive files now define a separate owner-worker
audit contract, protected versioned archive adapter, independent reader,
source-only PostgreSQL schema and create-only qualification controller. All
817 predecessor hashes are preserved; the current manifest contains 821 files.

The existing specialist daily ledger is deliberately unchanged. Its evidence
reader does not yet recognize this new schema/build. Inserting unregistered
worker envelopes into that ledger could break daily reporting validation.

- The independent reader checks that the exact logical task/phase row is absent
  and issues a one-use challenge before the writer's single attempt. It then
  rechecks the committed row through a separate read-only connection and the
  exact protected blob version, ETag, size and bytes. Historical equality alone
  cannot establish `newly_created`; replay, lost acknowledgement or a verifier
  restart cannot resume a challenge or rearm a consumed execution fence.
- Evidence binds tenant, owner, task, attempt, worker, request/input fingerprints,
  reviewed source/image fingerprints, registered producer and separate audit
  principals, plus lease fingerprint and revision. It contains no lease token,
  prompts, model output, source values or credentials. Pre-authorization denial
  can be recorded without issuing an execution or source-mutation grant.
- The writer commits and closes its SQL connection before create-only archival.
  Duplicate/conflicting or uncertain writes are not repaired or retried by this
  adapter. No writer acknowledgement alone is a verified worker receipt.
- The source-only schema enforces forced tenant/worker RLS, unique logical
  task/phase bindings and append-only history. It is outside automatic production
  migrations and creates no roles, grants or credentials. Live composition must
  separately qualify writer/reader SQL and archive identities, TLS, ACLs and
  exact scope; these callbacks are not yet installed or accepted in production.

All 42 focused synthetic tests passed. Full discovery found 2,862 tests: 2,860
passed with the same two pre-existing Windows fixture skips. Compilation and
repository policy passed: 293 tracked files, 802 scanned source files and 59
unchanged migrations. SQL/archive fixtures are not live identity qualification.
Existing Azure resources, permissions and the accepted isolated SQL execution
were reverified before and after full QA. No new job/start, live SQL write,
model call, runtime activation or production enrollment was dispatched.

The protected source checkpoint is
`5f6965b7425fdb16ab86bef4bf4ce87eb6c536fbb6de8d04b7f81e391a61e836`,
version `2026-10-05T17:50:56.0546772Z`, status
`step_3_worker_audit_source_qa_verified`. A separate process freshly reread its
protected bytes, rechecked all 821 hashes and the complete native baseline, and
retained fresh readback
`a59fd86d67591d18174201a7a6c58c4ad2e11297dc5d7f9cedf3d42a6d1fcf1c`,
version `2026-10-05T17:53:22.1216789Z`, status
`step_3_worker_audit_source_fresh_readback_verified`. These are source checkpoint
receipts, not live worker task receipts. Both retain
`protected_worker_receipts_verified=false`,
`restricted_audit_writer_reader_live_qualified=false`,
`production_evidence_registry_enrolled=false`,
`runtime_process_cessation_verified=false`, `website_owner_authenticated=false`,
`step_3_complete=false` and `step_4_started=false`. IBKR-received rows read or
modified, model calls and newly activated agents remain zero.

### Step 3 audit SQL trial and connection revocation correction 5 October 2026

Four additive files preserved all 821 predecessor hashes and extended the
manifest to 825 files. Sixteen focused tests passed. Full discovery found 2,878
tests: 2,876 passed, with only the same two Windows fixture skips. Compilation
and repository policy passed (293 tracked files, 806 scanned source files,
59 unchanged migrations). The protected preflight is
`e49b162c9c4f07711725e24a8bb036f82c540e96d17ad33398f0e7e748c6602d`,
version `2026-10-05T18:26:46.0118717Z`.

The exact manual job `ibkrv2stg-ownaudsql-20261005` was created once after
protected intent and a create-only reservation. Its native identity, pinned
image, versioned test-secret reference and zero-retry configuration passed
verification. Protected job acknowledgement, native acceptance and single-start
receipts are respectively
`3afb8dfc29e3e247efef81a55848e36a50a36495999498da371a078b73d3de22`,
`ab7181031a67cd9a3be9b3f082aeb4c3c7cbf2a27e8630484944c37a9b7abeef`,
and `e1a8d9ee14fc4fdb42ec26a2abbe87b42a37f1ed6186d2dae065214ae511b61e`.

Execution `ibkrv2stg-ownaudsql-20261005-6r91p7d` failed its revocation
assertion after six ordered live checks passed: separate restricted TLS logins
and ACLs, committed writer evidence independently read through the SQL reader,
read-only/append-only permission denials, tenant/worker identity-bound RLS,
duplicate logical-phase consumption, and lost-acknowledgement consumption.
The precise recorded failure is `fence_audit_revocation_wrong_cause`; the
wrapper reports no SQLSTATE. This does not prove a permissions bypass, and
six partial passes are not acceptance of the complete audit SQL stage.

The full bounded console/native record is protected under
`3386bd630e471562f1d4a937508132a619274467781a75eb5284b2dab3c28f7c`,
version `2026-10-05T18:40:44.1686747Z`. A separate process verified all 825
hashes, predecessor permissions, exact native job/execution and one-execution
inventory, then retained fresh failure readback
`933377c87b546f468e98428f3fb17f9d01a0e968b6bf14b7713624711f04bb47`,
version `2026-10-05T18:44:05.1525980Z`. The consumed job, database and roles
remain unchanged; there is no restart, reset or deletion.

The connector implementation raises a connection `OperationalError` without
a query result or necessarily a SQLSTATE. Its finished connection can retain
diagnostic metadata, consistent with the [Psycopg error documentation](https://www.psycopg.org/psycopg3/docs/api/errors.html).
That explains a likely mismatch in the original SQLSTATE-only assertion; the
retained failed record does not contain the original provider message, so the
actual server denial reason still requires measurement in the new trial.

Three further additive files preserve all 825 frozen hashes. The fixed new
scope is job `ibkrv2stg-ownaud2-20261005`, database
`owner_codex_audit_sql_20261005_v2`, private schema and five different roles.
The generation recipe is pinned to the exact original worker/controller bytes;
older modules, constants, evidence and effects are not retargeted.

Eleven focused tests passed, including packed import in a separate process.
The correction requires a genuine finished connection failure bound to the
registered host, database and reader login, exact verify-full/CA settings,
the specific role-NOLOGIN server FATAL, and an independent catalog check that
the login is disabled. Timeouts, wrong passwords, other roles/databases,
malformed messages and a still-enabled login are rejected. Raw provider
messages, passwords and connection information are never emitted.

Full discovery for the correction found 2,889 tests: 2,887 passed, with only the
same two Windows fixture skips. Compilation and repository policy passed
(293 tracked files, 809 scanned source files, 59 unchanged migrations).
The corrected 828-file manifest preserves all 825 predecessor hashes.
Its protected preflight is
`3ffd4e2171fcdedfbffe9ef42260f74a58166dee2b4f7eefad670569ab337805`,
version `2026-10-05T18:57:36.9387307Z`. The corrected job was created once
and independently native-verified, with protected receipts
`1a06dfb27428956e206a9d6690512ccbf764eade223df7e7f43a8cba32e2a6b2`
at `2026-10-05T19:03:00.6070455Z` and
`c7716e6891deb5cb28f8710f057ef1f72825836fa3d4a5a6db191d2812f73d37`
at `2026-10-05T19:05:37.3164740Z`. Its single-start acknowledgement is
`be61a8358cfcb128f24b0eb29471485355d6d3b85f5ca4ad97dd88146460735e`,
version `2026-10-05T19:10:10.7729071Z`.

Execution `ibkrv2stg-ownaud2-20261005-t8nw7ks` failed with
`fence_audit_predecessor_drift` during installation. None of its eight behavioral
checks ran, so the corrected connection-revocation helper has not been measured
live. The bounded failed console/native observation is
`1502f7730c4114ee462cc03621b849ba2c3b442145f9646e5d76a24fddc2660b`,
version `2026-10-05T19:13:40.0957763Z`. Separate-process native/protected
readback is `11ae1869eef34d2735e399b7e21ada61ff1f506bbf26cb0628519dfcd8aa1e34`,
version `2026-10-05T19:19:51.4698799Z`. It preserves all 828 source hashes and
explicitly records `predecessor_sql_catalog_unchanged_verified=false` and
`live_sql_accepted=false`. Native evidence verification does not resolve the SQL
catalog discrepancy. The failed job, synthetic database and roles are retained;
no restart, reset or deletion was attempted.

Three additive files now implement fixed, read-only catalog diagnostics. The
catalog membership key includes role, member and grantor OIDs; rows are fully
ordered, duplicate keys and incomplete inventories are rejected, and actual
permission changes remain visible. Role settings, ACL text and comments are
fingerprinted rather than emitted. Thirteen focused tests passed, including
unchanged-predecessor source qualification and sanitized malformed-input errors.
The original comparison omits the grantor and does not fully order tied rows.
That is a possible source of unstable fingerprints, not yet a proven cause of
the live failure. A fresh protected read of the retained Step 2 provider-object
inventory (`b3660df5e7ce215b5d1a71692dcd09e0d732307b14df31a94526f792bd0d1e87`,
version `2026-10-05T12:41:37.7823100Z`) reconstructed and hash-verified both
complete historical catalogs. Each contained 24 memberships, including the same
role/member pair with two different grantors and two permission-option variants.
This establishes that tied keys were present in the earlier observed catalog,
not that current permissions are unchanged or that ordering caused the failed
installation comparison. No live diagnostic SQL has run and no new installation is
accepted on this hypothesis.

The three-file diagnostic extension preserves all 828 predecessor source hashes
and extends the protected manifest to 831 files. Full discovery found 2,902
tests: 2,900 passed, with the same two Windows fixture skips. Compilation and
repository policy passed (293 tracked files, 812 scanned source files,
59 unchanged migrations). The create-only source checkpoint is
`6346a7ed2de59629b84f475a6cccdaaa2d75bbf95755cd777a677966f5946918`,
version `2026-10-05T19:37:10.7879525Z`, status
`step_3_catalog_diagnostic_source_qa_verified`. Before-and-after native checks
preserve the retained failed trials and previous resources. This record accepts
source-only diagnostics, not live catalog reconciliation, archive receipt
integration, actual runtime cessation or website-owner authentication.

A separate process freshly reread that protected source checkpoint, reverified
the 831-file manifest and preserved 828 predecessors, and checked the exact
retained native failed execution and single-execution inventory. Its create-only
readback receipt is
`416d57d4a2ff56b267a5f0cdc8172843966ae6363d498d77c6b63f0606e4773d`,
version `2026-10-05T19:40:15.2697390Z`, status
`step_3_catalog_diagnostic_source_fresh_readback_verified`. It explicitly keeps
live catalog reconciliation, live audit-SQL acceptance, protected worker
receipts, actual process cessation and website authentication unaccepted;
`step_3_complete=false` and `step_4_started=false`.

This is isolated synthetic SQL evidence, not an authenticated Azure archive
writer/verifier, real owner-task receipt, production ledger enrollment, model
activation, runtime cessation or website authentication. Received IBKR rows
read/modified and model calls remain zero; Step 3 is incomplete and Step 4 has
not started.

### Step 3 read only live catalog diagnostic 5 October 2026

The owner requested completion of Step 3 before starting Step 4. Three additive
files now prepare a single, separately reserved catalog observation on the same
private synthetic PostgreSQL host. All 831 previously accepted source hashes
are preserved. The new worker uses three fresh, certificate-verified administrator
connections to `postgres`, with default read-only transactions and fixed bounded
catalog SELECTs. It creates no roles, databases, schemas or grants and queries
no portfolio/source tables.

Complete redacted catalog records use fully keyed membership rows and bounded,
hash-bound transport frames. Missing, duplicate, changed or oversized frames
are rejected. Extra compressed streams cannot be ignored. A stable current
catalog observation does not by itself prove the cause of the historical failed
comparison. The worker and controller explicitly retain that distinction.

Ten focused tests passed, including isolated packed-module import, three closed
fresh connections, exact additive source validation, preservation of predecessor
receipt/native checks and rejection of altered source bytes. Full discovery
found 2,912 tests: 2,910 passed, with the same two Windows fixture skips.
Compilation and repository policy passed (293 tracked files, 815 scanned source
files, 59 unchanged migrations). The 834-file manifest preserves all 831
predecessor hashes. Protected preflight receipt
`69f6d0f49eaea6a2826eff6397a2880437dcbecd75adf0bd3c1cd2d6c5e1f847`,
version `2026-10-05T20:07:15.9834121Z`, binds the exact manual diagnostic job,
worker bytes, runtime and unchanged native predecessors.

The original job-dispatch command stopped before loading its preflight or
creating a new intent/reservation. A reused controller filename check referred
to the old consumed fence trial's marker. The new diagnostic intent and
acknowledgement files are absent; a fresh native absence check is required
before any correction proceeds. No job creation acknowledgement was returned.

Two additional source files correct only the filename guard in a detached
controller context, retain all 834 predecessor bytes and preserve the exact
qualified worker plan. Four focused regression tests passed, including an
explicit filesystem-marker test distinguishing old and new scopes. An additional
in-memory route probe confirmed only the registered diagnostic job PUT and
start POST, without any real cloud or SQL calls.

Full discovery for the correction found 2,916 tests: 2,914 passed, with the same
two Windows fixture skips. Compilation and repository policy passed (293
tracked files, 817 scanned source files, 59 unchanged migrations). The protected
836-file manifest preserves all 834 predecessors and the worker plan. Fresh
native absence checks passed before and after QA. The create-only review is
`046971852d8f8e2c7e435fc4a2229a4008e7be9e643b6e48fceb1e49d2dbf161`,
version `2026-10-05T20:21:18.5394322Z`, status
`step_3_catalog_dispatch_scope_reviewed`. The reviewed diagnostic job was then
created once. Its protected acknowledgement is
`c3db2a373e7bdd6ce91ff59db3ee91f66224c77efff142d026c40df5d88d3d31`,
version `2026-10-05T20:28:26.1436611Z`. Fresh native configuration acceptance
passed with receipt
`34466a38f8a2eff7165fde9fc2c5b4e8283b814c9587d27087efa8a76f8ac90b`,
version `2026-10-05T20:31:55.3441007Z`. A single start was acknowledged with
receipt `9c3a2b628e54c6299dc0092813200fba3d10f4db98fd97d3d626867c024f32c4`,
version `2026-10-05T20:38:09.5640623Z`. Execution
`ibkrv2stg-owncatd-20261005-raclx51` reached native `Succeeded` status.
No second start request has been made.

The original collector stopped before querying logs because Azure's execution
template included `initContainers: []`; the job definition validator expects
that optional field to be absent or null. The exact failed boundary is
`checked_template`, fixed code `fence_native_job_invalid`. A separate protected
native metadata inspection retains the unaccepted result:
`f7d7a3b621f94198b0c7a172175fd06f3b7cfd57da307a00ea03e346ead073ff`,
version `2026-10-05T20:41:46.0394806Z`. It confirms zero initializer containers
and absent volumes, not an unexpected running container.

The first proposed collector correction passed six focused tests but its native
review stopped before full QA: normalizing the empty initializer list exposed
a second difference, the execution record's exact job-scoped secret reference
`cappjob-ibkrv2stg-owncatd-20261005`. A read-only field comparison found no other
template difference after the existing descriptive-default handling. The
protected inspection retains the original, unnormalized execution metadata.

The two additive files now provide a read-only collector correction for both
observed execution fields. An owned execution collector namespace normalizes
the empty initializer list and only that exact job-scoped alias, against the
single registered environment variable and secret. The original job-definition
validator still verifies the pinned Key Vault version and sole identity/secret
configuration. All source, identity, native predecessor, payload and log-frame
checks remain intact. Nonempty initializer lists, volumes, other-job aliases,
retargeted secrets and extra environment values are not silently discarded.
The worker plan, pinned image and executed source remain unchanged. Nine new
focused tests passed, as did the 23 tests covering this correction and its two
predecessor diagnostic controls. Full discovery found 2,925 tests: 2,923 passed,
with the same two Windows fixture skips. Compilation and repository policy
passed (293 tracked files, 819 scanned source files, 59 unchanged migrations).
Fresh before/after native verification passed. The protected 838-file review
preserves all 836 predecessor hashes and the exact executed worker plan:
`0801abb52e067e8c69451d18a55bc1f7c7601d5b63c7d2b34cafbb97711f1bbf`,
version `2026-10-05T21:06:10.1368443Z`.

Collection of the existing execution's complete bounded log family and all
hash-bound transport frames passed. The protected observation is
`f66d0b7fdcfbcfda0b4dd39a4da60a477ac82740abfdcc0b891751573afd5285`,
version `2026-10-05T21:10:27.2062964Z`, status
`step_3_readonly_catalog_observed_not_historical_drift_acceptance`. Three fresh,
closed TLS connections produced the same fingerprint
`1c1a9f3aa79a016ffdeb25d5b3433a330ccfacd1aad0851782897bb7c707ee3e`.
The redacted current catalog contains 11 databases, 41 roles and 55 membership
rows. Six role/member pairs have two different grantors and two option variants;
the complete membership keys distinguish those rows. Both observed differences
are empty. This validates the current observation and confirms the weakness of
the old partial sort key, but missing historical before/after rows still prevent
a claim that ordering was the sole cause of the earlier trial failure.

A separate fresh process reread the exact protected versions, reconstructed
the complete frames and reverified current source/native resources. Its
create-only receipt is
`e4d244187275b9e577d07b2ccc7011211f5d5c76195c747fc7a0382ba04b741f`,
version `2026-10-05T21:14:55.8407877Z`, status
`step_3_catalog_diagnostic_fresh_readback_verified_not_historical_acceptance`.
It binds the 838-file manifest, native job/execution/single inventory and the
fully reconstructed record. No new execution was dispatched, and no SQL or
production change was performed by the diagnostic. Historical drift acceptance, audit SQL acceptance,
real protected worker receipts, runtime cessation and website integration remain
false.

A separate read-only website inspection found a signed-in owner header and the
AI Operations page. The live release explicitly reports that saved task history,
protected worker connection and cancellation are not yet connected. This is
not signed owner-service authentication or end-to-end website task acceptance.
Step 3 remains incomplete; Step 4 has not started.

### Step 3 prospective audit catalog correction 5 October 2026

Three additive files prepare a new, fixed private trial, preserving all 838
accepted predecessor hashes and both consumed failed audit trials. The new
physical scope is `ibkrv2stg-ownaud3-20261005`, synthetic database
`owner_codex_audit_sql_20261005_v3`, and five separately named v3 roles. No
existing database, role, job or receipt is reset or reused.

The first complete catalog snapshot must match the independently reread current
baseline. Comparison uses the full membership key including grantor and hashes
all recorded role/database fields. Only this new database and its five registered
roles are projected out; existing failed-trial roles and their memberships stay
in scope. A new role acting as grantor between existing principals is explicitly
rejected, rather than hidden by the projection. Complete redacted snapshots are
retained even when projection fails, and a final bounded catalog readback is
required before the administrator connection closes. This prospective test does
not retrospectively accept the earlier missing historical comparison.

The exact connection-revocation helper remains unchanged: it requires the actual
finished libpq connection, certificate/hostname configuration, exact role-NOLOGIN
FATAL and independent catalog confirmation. Writer/reader ACL, tenant/worker RLS,
duplicate-phase and lost-acknowledgement checks remain required. These are
synthetic SQL boundaries, not the independent Azure archive receipt service.

Thirteen new focused tests passed; 33 tests covering the new trial, prior
revocation correction and metadata collector passed together. The actual packed
worker modules imported in a fresh isolated process without opening SQL or
calling a model. The new job remains manual, single-replica, zero-retry, on the
same pinned image and existing private-host SQL identity/secret version. Full
QA discovered 2,938 tests: 2,936 passed, with the same two Windows fixture skips.
Compilation and repository policy passed (293 tracked files, 822 scanned source
files, 59 unchanged migrations). Before/after native preflight verification
passed. The protected
841-file preflight is
`435fa728f30dc3a7813a9d5a25d0828396f86fbbb016586b1adb77ef7327ce65`,
version `2026-10-05T21:35:13.1752015Z`. It preserves all 838 predecessor hashes,
the current baseline/readback bindings and existing native resources. The new
manual v3 job was created once after protected intent and a create-only
reservation. Its acknowledgement is
`6c107c7f1facb626530a378995f0afe2cf4c70b3299d64cb2f4c08ce15d2d89c`,
version `2026-10-05T21:42:54.4885592Z`. Independent native acceptance passed,
receipt `84a5d79e8d86a35f6b12e37ea18e9bb775a3be648fdff3bbeb1d09f9762a3a7e`,
version `2026-10-05T21:46:57.9257133Z`. The single start acknowledgement is
`7024a2b38d129f168d4a4c78cdceb66729dfa96bb06d941325eb6f071733fbcf`,
version `2026-10-05T21:54:38.3038442Z`. Execution
`ibkrv2stg-ownaud3-20261005-lalvdq0` succeeded. The complete bounded worker/native
evidence passed all eight required live checks and 16 permission denials,
including the actual finished connection's exact reader-NOLOGIN FATAL, verified
TLS configuration and independent disabled-login catalog observation. Five
complete redacted catalog snapshots passed the fixed predecessor projection
and baseline comparison. These cover the declared database/role/membership
metadata, not every PostgreSQL object or historical failure cause.
Protected observation receipt:
`bc0b6076ceee04ffcf1958477fb0a485e571957d2315ba1e7e335ce3693f5891`,
version `2026-10-05T21:59:30.5141201Z`. Separate fresh-process protected/native
readback passed: `28f63375c5159b166d654916e99bab7d625febe7b77274939e0a68bc2e5bc31e`,
version `2026-10-05T22:04:10.4681444Z`. It reread protected source, job,
start and observation records, reverified the exact native successful execution
and single-execution inventory, replayed the complete catalog frames and all
five snapshot comparisons, and retained the 841-file source manifest.
This accepts only the isolated audit SQL/revocation
substage: real independently authenticated Azure archive receipts, runtime
cessation and website integration remain unaccepted. IBKR rows read/modified
and model calls remain zero. Step 3 is incomplete and Step 4 has not started.

### Step 3 separate archive-boundary worker source 5 October 2026

Three additive files preserve all 841 accepted predecessor hashes: a bounded
archive qualification worker, its regression suite, and a source-only checkpoint
controller. The prospective physical scopes are two separately named identities
and manual jobs, `ibkrv2stg-ownaw-20261005` and `ibkrv2stg-ownar-20261005`.
None has been created or started by this source change. No role was granted.

The worker only exercises new, explicitly synthetic content-addressed archive
records and a read-only identity's expected-denied creation probe in the existing
private legal-hold-protected `specialist-audit-checkpoints` container. It does
not use IBKR containers or query SQL. The two contexts require distinct writer
and verifier principals. Selected token claims are context binding, not proof
of authentication: actual Azure API results and independently verified native
identity/RBAC/job registrations remain required in the live controller.

It checks exact archive versions, ETags, bytes and protection before/after reads,
specific duplicate/overwrite denials, and a deliberately lost upload
acknowledgement. Inspecting that consumed canary does not create a new receipt,
repair the acknowledgement, authorize a runtime retry or prove SQL commit.
The deliberate duplicate probes are negative qualification tests, not an
operational retry path. Generic network, authentication and server errors are
never accepted as read-only permission-denial evidence. Source values, prompts,
lease tokens, credentials and unselected token claims are not retained.

All 21 focused tests passed, including the actual module graph imported in a
fresh isolated process without SQL or cloud calls. Full discovery found 2,959
tests: 2,957 passed with the same two pre-existing Windows fixture skips.
Compilation and repository policy passed (293 tracked files, 825 scanned source
files, 59 unchanged contiguous migrations). Before/after native verification
passed. The protected 844-source checkpoint is
`11ab8298e8719879b5558ccb63af2748c80350fe33a7b866dc4c0818c8c16db3`,
version `2026-10-05T22:27:11.4746256Z`. Separate-process protected/source/native
readback passed, receipt
`3af542b3db1f0678de20930231182eef336261d183ea1fb4b1457ad1769bddb6`,
version `2026-10-05T22:31:28.4066636Z`. This accepts source preparation, not live
archive identity qualification, a real protected worker-task receipt, production
ledger enrollment, process cessation or website authentication. Step 3 remains
incomplete and Step 4 has not started.

Three further additive files preserve all 844 predecessor hashes and prepare
the exact two-identity live archive qualification. The fixed incremental ARM
template contains two new identities and six new grants: each identity receives
registry pull and container-scoped protection metadata read; the writer receives
container-scoped Blob Data Contributor, and the independent verifier receives
Blob Data Reader. No IBKR-container, database, Key Vault or model permission is
included. Two separate manual jobs use the pinned verified image, no secrets or
environment credentials, a single replica and zero retries. Native definitions,
identity registrations, complete direct-grant inventories and exact execution
templates must match the protected plan. Each creation/start requires a protected
intent and create-only reservation, and uncertain effects cannot be redispatched.

All 21 focused launch/controller tests passed. They reject modified sources,
arbitrary executable plans, changed image/identity/secret/retry settings,
unregistered routes, false SQL/runtime success claims and generic permission
errors. The initial preflight rejected an omitted Boolean return in the new
owned source-comparison adapter before any deployment. Read-only diagnostics
identified the exact guard; the unfrozen candidate was corrected and a test now
requires both a true valid comparison and rejection of a changed predecessor.
No native effect reservation was consumed by that rejected preflight.
The corrected deployment preflight passed. Full discovery found 2,980 tests:
2,978 passed with the same two Windows fixture skips; compilation and repository
policy passed (293 tracked files, 828 scanned sources, 59 unchanged migrations).
Fresh before/after verification preserved all 844 predecessor source hashes and
native resources. The 847-source protected preflight is
`712df8892e70bb7604a223544a84afca24cb3fcfa23d2068880f964ebd0dde94`,
version `2026-10-05T22:56:12.2083587Z`. Azure validated exactly the two new identity
IDs and six exact grant IDs, without diagnostics; template hash
`4332227023420165850`. Resource scope and managed-identity references follow
[Azure's ARM role-assignment guidance](https://learn.microsoft.com/en-us/azure/role-based-access-control/role-assignments-template).

The identity-stack deployment was acknowledged once, protected receipt
`b74d34493ea30e11f05eb0786d48be65e0ac6cc76aff7bf10790bda0c9fb22c8`,
version `2026-10-05T23:06:25.0560016Z`. Fresh native acceptance verified a
succeeded incremental deployment, template hash `4332227023420165850`, exactly
eight registered output resources, distinct identities and exactly three scoped
direct grants per identity. Acceptance receipt
`5e21e759dfe38979a2448589b2619b2914e1d8a06bef199c42b93ca658ca002b`,
version `2026-10-05T23:11:17.7439790Z`, preserves the frozen 847-source preflight
and predecessor native state. The writer-job creation is being checked under
its own protected intent/create-only reservation. Actual archive canary results
remain outstanding; the separate SQL-commit/worker-receipt, process cessation
and website gates are not waived by identity registration.

Azure acknowledged the writer-job creation once, receipt
`c656b371a0fe5db2e45546b460a1bf5fcba616316128f359ed084b6fe435ae3b`,
version `2026-10-05T23:21:03.4410879Z`. Its first native-definition acceptance
rejected Azure's `secrets: null` response against the submitted empty list;
the response also omitted the empty container `env` list. The job was not
started. Read-only inspection confirmed the pinned image/script tags, exact
single identity, zero retries and the empty response values. A prospective,
separately hashed owner-side parser in the disposable qualification workspace
normalizes only `secrets: null` and absent/null empty `env`. It preserves all
847 frozen repository sources, the consumed creation and the original rejected
acceptance; its later protected receipts have a separate namespace. Thirteen
focused tests cover exact empty shapes, unchanged input, nonempty/malformed
rejection, all other native boundaries, detached globals, unchanged effect
routes and same-namespace effect consumption. Full regression and fresh
before/after native requalification passed. Full discovery found 2,980 tests:
2,978 passed with the same two Windows fixture skips; compilation and repository
policy passed. The separately hashed parser/test policy is protected receipt
`00b50790d7d75deeda5025102a2a4ac4c2371adc347c45068c728c89b353b3d3`,
version `2026-10-05T23:44:04.1596476Z`. Both parser files are frozen for the live
test alongside the unchanged 847 repository sources. Writer-job prospective
acceptance passed, protected receipt
`b89b7e3c03e9282e5e4e35d18cc90dba981e64d2dcd44513f48e440f0dc23791`,
version `2026-10-05T23:49:23.2323240Z`. Azure acknowledged its single-use start,
receipt `5ea67df3a266502a750ef1226177ae960ed75592f2dfdb2cfbf67d8566231cbb`,
version `2026-10-05T23:59:22.6351421Z`. Execution
`ibkrv2stg-ownaw-20261005-0u6tcru` failed with the fixed worker error
`worker_archive_unverified`. Exact native execution, single-execution inventory,
bound job definition and bounded console family were retained in protected
failure receipt `34bc6b5704809e1b2a6c80cc2955b642614b76cd709a7caf3c90fba0b9e72a33`,
version `2026-10-06T00:05:28.3783021Z`. It is not a successful archive
qualification, and the job cannot be restarted or retrospectively accepted.

Owner-authenticated read-only metadata inspection found `BlobNotFound` for both
exact content-addressed synthetic canary names. This is current metadata
inspection, not proof of the failed worker's authentication or of historical
upload attempts. The current desktop SDK constructor also passed an offline
no-token test; that does not establish container-runtime compatibility. A
separate read-only diagnostic candidate identifies the failing token-context,
ARM protection, storage-read or close stage using fixed error classes and
allowlisted API codes, without emitting credentials, response bodies or traces.
Its only prospective new resource is manual job
`ibkrv2stg-ownard-20261006`, using the existing writer identity and grants, the
same pinned image, no secrets and zero retries. It has no upload, SQL or model
calls and cannot claim archive, runtime or Step 3 acceptance. The first diagnostic
preflight exposed missing keyword defaults in the detached transport function;
no diagnostic job or execution had been created. The prospective controller
now preserves those defaults, with regression coverage. All 16 focused
worker/controller tests passed. Before/after native preflight then passed,
protected receipt
`3496ea9797c37b077959ecda9daf3d1649db4b741f75d99577072a8d122d8a70`,
version `2026-10-06T00:29:44.6209267Z`. All 847 repository sources and both
previously frozen parser files remain unchanged. The three diagnostic files are
separately hash-bound and frozen for the single-use live qualification.

Azure acknowledged the diagnostic job creation, protected receipt
`f812bfff58200ad73fb0b6ff42d0d0bc64f90cdb07bb0aa3ad614b16d47833e2`,
version `2026-10-06T00:39:12.6943721Z`. Independent native job acceptance passed,
receipt `1398b85beaf60232a5c9fb93a416ffb4179b5aeb79806580ec4e9e9c0733b03a`,
version `2026-10-06T00:43:56.8247499Z`. Its exact image, source tags, sole existing
writer identity, empty secret/environment configuration and zero-retry policy
were verified; its execution inventory was empty before start reservation.
The single-use start was acknowledged, receipt
`0c30bda97cf2e29c4ff466cdd1e0a80ddb408b753a497ae06e16da88a5f22892`,
version `2026-10-06T00:53:19.7756063Z`. Execution
`ibkrv2stg-ownard-20261006-aulqb4v` failed at `arm_token_context`, before any
ARM protection or Blob request. The protected bounded result, receipt
`f288c32bdd127ed974b23da5413b8c5fdd7bc4213d334b73d61c8b200f40d7b8`,
version `2026-10-06T00:59:02.7355251Z`, reproduced the client-side rejection:
the embedded JWT expiry differed from SDK expiry metadata. Tenant, principal,
client, time-validity and token-format predicates matched; expiry equality did
not. No token or raw claim values were retained. The diagnostic made zero
uploads, SQL/model calls or IBKR reads/writes and does not qualify the archive.

A separate, not-yet-deployed credential candidate follows Microsoft's
[client token guidance](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens):
Azure resource access tokens are opaque to clients, and their response metadata
provides expiry information. The candidate preserves fixed scopes, registered
identity composition and actual ARM/Blob authorization checks, while removing
undocumented token-content and maximum-lifetime assumptions. Eleven focused
tests passed. A separately scoped controller initially added ten qualification
tests. Its first preflight stopped before retention or effects because the test
subprocess inherited a relative `PYTHONPATH` while changing working directory;
the candidate test could not import the frozen archive module. No job, protected
intent, start or new preflight was created by that failed attempt. The unaccepted
controller now gives only its test subprocess reviewed absolute source paths,
without changing the parent environment. A regression test covers that boundary;
all 22 candidate/controller tests pass from the actual subprocess directory.
The controller restricts effects to the new manual
jobs `ibkrv2stg-ownaw2-20261006` and `ibkrv2stg-ownar2-20261006`, with existing
writer/verifier identities and grants. Its operation is
`3e9cd5f9-192d-48fc-ba8a-fee7c37d5a83`. It requires the protected diagnostic
result, unchanged 847 repository sources and existing native identities,
before/after source/native preflight, protected single-use reservations and
independent native/output acceptance. Its four candidate/controller/test files
are frozen for this new live qualification. The original failed writer and
diagnostic cannot be restarted or relabelled successful. The corrected archive
qualification is in progress, not an accepted SQL/worker receipt, process-control
test, website deployment or Step 3 completion.

The corrected archive preflight passed and was retained with receipt
`703dd3ffedfa7f6fa4d43e440b4f5bbf60a8dab56ed3dc6ece64f835ddda319c`,
version `2026-10-06T01:20:40.7144668Z`. Its single-use writer-creation intent is
`56de1293d3ea9329aeeaa7e23c4ca6458e3c29e622974e81d2aa1dea8b868a5c`,
version `2026-10-06T01:25:35.9382971Z`. Azure acknowledged creation of the new
writer job; protected acknowledgement
`558e9ec87873a7f75d942b9ac5bf4c326b4febf8fb699683e406a7cc36e1ec07`,
version `2026-10-06T01:30:11.2936950Z`. The new writer subsequently passed
native registration, started once, and completed execution
`ibkrv2stg-ownaw2-20261006-9ic37ti`. Its independently retained live observation is
`b139eff247e09124f90a17bb5e446eee005e56378c42c8c210ff2dbd303b5f1f`,
version `2026-10-06T01:51:01.7973899Z`. Exact protected versions, duplicate and
overwrite denials, and lost-acknowledgement non-acceptance were qualified. This
is archive-writer qualification, not a SQL-committed worker receipt. Azure has
acknowledged creation of the separately authenticated verifier job:
`5c813e528f83880520d9d273c4d38aa712f592d974889efacd5bbb9d638ba056`,
version `2026-10-06T02:00:45.1326661Z`. Its native registration was accepted,
receipt `112dc586e592539369949960f62668ba60a92fcfb8e734ce9ec4b8f322be16ea`,
version `2026-10-06T02:05:57.1432496Z`. Its single-use start was acknowledged,
receipt `8dbc47dd061338a33d32396635452d4fc0a224a400681529d0f6f661296d0440`,
version `2026-10-06T02:16:13.3474134Z`. Independent execution/output acceptance
passed, receipt
`6a3e0206104603c1357b18b525352dcb11f0c4795ff4cf1d892c990ee4cfeb5c`,
version `2026-10-06T02:22:52.3323094Z`. The reader qualified exact-version byte
readback and its actual append denial (HTTP 403,
`AuthorizationPermissionMismatch`). Both archive boundaries are qualified;
this does not yet bind a real SQL commit to a protected worker receipt.

Read-only browser inspection still shows the direct owner workspace as being
prepared, with saved conversations and cancellation disconnected. The owner
header is not a fresh signed service-authentication acceptance test. Step 3
remains incomplete and Step 4 has not started.

The pinned Codex runtime is retained for the next process-control gate. Official
[non-interactive execution documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
and [app-server cancellation documentation](https://learn.chatgpt.com/docs/app-server)
were reviewed. Cancellation acknowledgement is not substituted for independently
verified runtime/process cessation; no SDK, model or runtime upgrade was made.

A separate five-file process-control candidate was added under the same `.work`
qualification directory, without changing any of the 847 frozen repository
sources or the archive candidate files. It uses an exclusive Linux subreaper,
PID/start-time metadata and pidfds; two complete empty descendant scans plus a
kernel `ECHILD` observation are required before reporting tree cessation.
Uncertain ownership, PID reuse, unreadable state or cleanup failure quarantines
the single-use runner; runtime composition requires an external durable
quarantine writer and reuses the unchanged fixed-prompt CLI parser/command.
Fifteen kernel-fixture, nine asynchronous-composition and twelve durable-fence
composition tests pass (36 total). They cover
escaped/adopted descendants, hidden children, PID reuse, timeout, cancellation,
single-use execution, unknown cleanup, repeated cancellation during cleanup,
failed quarantine persistence, fresh authority loss and reconstruction of
sticky runtime quarantine. The durable callback binds the existing exact store
and reservation, without adding a reset or receipt API. It makes real SQLite
fixture commits; these remain Windows-hosted tests, not native Linux/Codex or PostgreSQL
acceptance. No child was started, signal sent, database accessed or model called
against Azure or IBKR by these tests. The local Docker daemon was unavailable; it was not started or
installed. The source/test evidence and its six-file manifest (including the
create-only source-evidence recorder) were protected and independently read back:
`093ca121a66b2e5fd6ee91f0a753e7096ab59c868a3fc43616c0256f38748924`,
version `2026-10-06T01:26:12.3692191Z`. These files are now frozen for prospective
qualification. A separate, undeployed offline Linux trial/launch candidate and
their two test files pass seven additional source tests. That harness uses the
exact retained Codex image, binary and model-catalog hashes, an existing isolated
model identity, zero-retry/manual execution and empty secret/environment arrays.
It proposes actual CLI success/cancellation/deadline checks through a fixed,
tool-free loopback fixture; it does not call Azure OpenAI or claim live SQL/
receipt/fence acceptance. No native job was created by those source tests.
An additional inert, exact-scope offline Linux qualification controller and
eight tests were added. The trial/launch/controller family passes 15 tests;
combined with the frozen archive and process families, 102 tests pass with no
failures or skips. The controller admits only one new manual job,
`ibkrv2stg-ownrtree-20261006`, with the existing model identity, exact pinned
runtime image, no secrets/environment values and zero retries. It requires both
protected archive qualifications and their fresh native jobs/executions before
preflight or effects. Each effect requires a create-only protected reservation;
uncertainty cannot trigger a restart. The proposed native acceptance requires
success, cancellation and timeout cases plus actual pidfd exits, root wait,
two empty tree scans and kernel `ECHILD`. It explicitly cannot qualify real
PostgreSQL/fence composition or the full Step 3. Its local wait driver was
stopped before any effects to correct its local JSON-record shape check; the
replacement driver remained gated on archive-verifier acceptance. After that
gate passed, its prospective preflight was protected and independently read
back, receipt
`80c5d2b49e7bd765d7403b9850c1995299bdbf717ba374cf91364cc7bde52984`,
version `2026-10-06T02:33:40.1395761Z`. The six native-trial files are now frozen;
the separately reserved native job registration was acknowledged, receipt
`2763a7c257c269bf6aa1bea5e1402321730cc9a9693e525cfb19fb2c6fa35822`,
version `2026-10-06T02:45:06.1131146Z`. Its fresh native registration was
accepted, receipt
`3bd691294f037c7e0365629a102208316c7c6b6aa224677c32c5619801ae3172`,
version `2026-10-06T02:51:16.8267815Z`. Its separately reserved start was
acknowledged, receipt
`20d4565131f4762ee28d82acfc9514cfa7ec1f1e8614a09a71f2e24927102801`,
version `2026-10-06T03:04:49.6275340Z`. Azure reported the execution failed.
The original collector could not accept/retain a structured worker observation
because the bootstrap stopped before the worker-result prefix existed. No
receipt was manufactured from absent results. A separate read-only diagnostic
retained only fixed error classifications and bounded log hashes, receipt
`6fe1c92415381ead7118c7989cde408724aa75b15bf4e76918cd3f4d58b5f38f`,
version `2026-10-06T03:08:40.5033272Z`. Separate frame readback located the
assertion at `owner-native-tree-trial`, line 5, receipt
`60034405bc970ddc2bb988f502cc5be6649b4cf0a1e04cc99b07dae6bcdbc66f`,
version `2026-10-06T03:11:07.2750505Z`. That line is the combined Linux and
exact-Python-3.12.14 assertion. Python 3.12.14 was established for the different
SQL image, not this retained Codex image. The actual Codex runtime version has
not yet been established by this diagnostic. The original source/consumed
operation is unchanged; no existing job, grant or effect is restarted.

A separate three-file metadata-only launch/controller candidate passes nine
source tests. It proposes one NEW manual zero-retry job
`ibkrv2stg-ownrmeta-20261006`, operation
`f754c06c-c2f7-4d43-9d46-52d0eed98a81`, on the same immutable Codex image and
existing model identity. It reads only platform, Python version/executable hash,
UID/GID and kernel-API availability. It cannot start Codex, discover credentials,
call a model, query SQL, read/write IBKR or qualify process cessation. Its owned
controller namespace reuses unchanged protected preflight/effect reservation
routines without mutating frozen globals. Fresh dependencies include the failed
predecessor's exact native job/execution and protected diagnostic. Before source
retention, a local preflight was stopped to replace a reused reservation prefix
with a distinct metadata prefix; no checkpoint or native effect had occurred.
A regression test verifies that separation. The corrected source preflight was
protected and independently read back, receipt
`636932466ca494b64d3db5257258e447a733dfa571ebc44de666e04f0eaae170`,
version `2026-10-06T03:30:40.2110687Z`. Its three files are now frozen. Job
creation and registration were independently retained at 03:44 and 03:50 UTC;
the separately reserved start was acknowledged with receipt
`e40a4cc65363df1a1b4ca25f76dd0090fa08fdbf16baf2246b3c10ea2a639c15`,
version `2026-10-06T04:04:04.9376285Z`. The one native execution succeeded.
Independent protected observation was accepted with receipt
`739629022bd521dddef87e1dd8ef58e04854d6588a9ab71020b981567f27e3c5`,
version `2026-10-06T04:12:13.1794217Z`. Its actual descriptor is Linux,
Python `[3,12,15,"final",0]`, `/usr/local/bin/python`, executable SHA-256
`4dbf3143240288fb2170257ffaa7bd030cdda5d2703d1f5f30b627042267e2e3`,
UID/EUID/GID/EGID 10001 and available pidfd/waitid APIs. This establishes the
earlier patch-version mismatch; it does not accept tree cessation, SQL or a
model call. All those acceptance/activation flags remain false. Any subsequent
runtime correction needs a distinct reviewed source/operation and actual process
tests; patch-version checks are not silently waived on the failed operation.

A prospective distinct native v2 launch/controller passes eight local tests.
It pins the full independently observed Python version, executable path and
executable hash rather than borrowing another image's patch version. It retains
the original immutable module graph and clones its main function into an owned
operation namespace, without modifying the failed worker. Its new manual
zero-retry job is `ibkrv2stg-ownrtree2-20261006`, operation
`a73a4e0b-ebbf-4da6-86b4-f3b99caecf71`, with a separate reservation prefix.
A conditional driver waited for independently protected metadata acceptance
before its preflight or any native effect; that prerequisite is now accepted.
Its new preflight is underway. Source tests are not process-cessation
acceptance; no actual v2 CLI execution is established yet.

A separate undeployed owner-task HTTP adapter and 17 HTTP/SQLite tests were
also added. The adapter covers saved session/task history, current status,
revision-based cancellation and event retrieval. It rejects browser-selected
tenant/owner/worker/prompt/digest/receipt values, cross-origin writes, duplicate
JSON keys, oversized requests, stale revisions, revoked owner/account scope and
legacy owner fallback. The server constructs the validated input digest; task
submission remains gated on separately accepted trusted service composition.
Running-task cancellation is reported as requested, never falsely as verified
process exit. An initial fixture run exposed SQLite connections not closed by
their transaction context; the fixture now closes all dedicated connections
explicitly and all 17 tests pass. An installed-test-library deprecation warning
was observed; no dependency upgrade or suppression was made. These are local
fixture results, not signed sign-in, live PostgreSQL, deployed UI or activation
acceptance. A separate owner-scoped UI fragment and ten render/Node DOM tests
cover automatic read-only status polling, cancellation-request wording, hidden
page backoff, authorization-loss shutdown and escaped display values. Uncertain
session/task submissions retain their original idempotency key across reloads;
they are never automatically POSTed again or assigned a replacement key. The
HTTP/UI/source-retainer family passed 27 local tests, no failures/skips. Its five
files were protected and independently read back with receipt
`5257b1973674152c81c0385c002623ec293ac484d159448fe45565b113bda62a`,
version `2026-10-06T02:09:48.8732792Z`; they are frozen for prospective
qualification, not installed in the live website. The 847 frozen repository
sources remain unchanged.

An additional seven-file real worker-audit composition/launch/controller/source-retainer
candidate passes 26 local protocol/catalog/native-shape tests. It proposes a NEW synthetic database
`owner_codex_worker_commit_20261006`, five NEW marked roles and one manual
zero-retry job `ibkrv2stg-owncommit-20261006` on the existing private host. No
job/database/role was created by these tests. It binds the existing trusted SQL
producer and distinct archive-writer/verifier identities, requires actual SQL
commit before exact-version archive readback, consumes uncertain-write challenges
without repair/retry, and checks real reader revocation. Only its new login
passwords would be retired; no predecessor role or consumed trial is reset.
The accepted v3 trial's final FULL catalog fingerprint
`c52ee555c84cbb423115b584e320a84ca86ba749aa05cdbcaa1cdbe432d13b3d`
is the proposed fresh baseline, not the older projection that excludes v3.
The source-only launch now binds a fixed module graph, runtime/CA metadata,
three registered identities, a version-pinned initialization-only Key Vault
reference, bounded catalog framing and fixed failure-stage metadata. The scoped
controller requires accepted native CLI evidence and fresh native predecessors,
reserves effects before dispatch and cannot restart a consumed intent.
Tests caught an oversized source encoding and a context-manager closure that
would have retained the old SQL namespace; both were corrected before freezing
or deployment. Another test caught inappropriate use of the archive-only empty-
environment normalizer; this job now requires its exact registered secretRef.
No existing frozen source was modified. Retirement never reads password hashes;
only new marked login retirement commands and public login flags are checked.
The combined selected archive/process/native/HTTP/UI/commit families passed
126 tests with no failures or skips. Its 26-test, seven-file source checkpoint
was protected and independently read back, receipt
`074cd43732f71e3e3a4d2987f9e09c9e9546ca817c094b4af97f84aded30750c`,
version `2026-10-06T02:40:05.6549959Z`. Those seven files are now frozen.
A conditional one-shot driver waited for the independent native CLI observation
before any new commit-trial preflight or effects. It was stopped after the native
bootstrap failed; no new commit-trial preflight, job, database or role was created.
A missing or failed prerequisite cannot create/start the job. A new two-file
controller proposal binds the unconsumed SQL worker to the distinct native v2
verifier, while preserving the original seven frozen source files and QA
checkpoint. Its new source namespace and reservation prefix prevent the old
controller from being rearmed; any old preflight or intent causes rejection.
Eight new controller tests and the original 26 tests pass together. A conditional
one-shot driver waits for protected native v2 acceptance; its preflight and
effects have not started. Source qualification is not SQL/archive acceptance.
Source/native preflight, protected effect reservations, independent live
acceptance and production-ledger enrollment remain outstanding.

A separate inert owner-worker daily-ledger detail/registry candidate passes
12 local tests. It contains only identifiers, fixed states, costs and protected
record/binding/version hashes: no prompts, model results, credentials, source
values or caller-supplied destinations. Its registry defaults to empty, has no
enrollment API and grants no execution or delivery. It distinguishes the
application data tenant from the Entra issuer tenant, binds the exact reviewed
worker/build/image/archive registration, checks fixed event/task/phase metadata
and rejects duplicate/noncanonical JSON, free text, uncertain receipt flags,
wrong roles/builds, source mutation authority and false success after unknown
cessation. It is not installed in the production reporting reader and has not
appended a ledger event. Its three-file source/test checkpoint was protected and
independently read back, receipt
`ce73c95bc1666c52491bd0c937ef7bfd55af91f60d54cc5e0eccc1163ddaf2bc`,
version `2026-10-06T02:51:03.1884186Z`. Those three candidate files are now
frozen. Live producer authentication, production registration, publish/readback
and daily-report integration remain separate acceptance gates.

Integration review identified an additional boundary: the existing website task
store requires `owner_codex_task_v1` metadata receipts, whereas the execution
fence requires independently qualified `owner_codex_worker_v1` receipts. They
cannot be interchanged by renaming a namespace or copying a hash. A separate,
inert read-only task-receipt linking candidate was added without modifying either
frozen contract. It requires the task metadata receipt's own committed row and
exact protected version, plus a separately committed worker row and exact
protected worker bytes for an outcome link. Fresh SQL session/identity/RLS
qualification must be provided by reviewed deployment dependencies on every
read; the candidate does not itself establish live service authentication.
Its 19 SQLite/Blob fixture tests pass, including actual existing task-store
attach/claim/finish calls, fresh owner revocation and a cancellation race.
Successful private computation after owner cancellation retains its successful
immutable worker evidence, while the separate public task decision withholds
the result and closes as cancelled. Unknown cessation cannot be mapped to a
terminal failed/cancelled task. No protected worker record is rewritten.
The reader has no write, repair, enrollment, runtime launch or reset API.
The combined selected candidate families passed 157 tests with no failures or
skips. An initial combined invocation omitted the scripts import path and
misspelled one test-module name; its four import errors were retained in terminal
output, then the corrected command passed without changing frozen source.
Source retention first failed with a connection timeout before the create-only
checkpoint file was opened, so no checkpoint upload was attempted. A separate
bounded repeat of read-only prerequisites/source retention succeeded, with
protected independent readback receipt
`1ce5374c537c1456cef3fa51b867091898c7eeab697182c5b755ad6e50b2c169`,
version `2026-10-06T03:09:04.4323083Z`. Its three candidate files are now frozen.
The repeat did not repeat a task SQL write or native effect. Live task-metadata archival, its separate
schema/ACL qualification, authenticated service composition and deployed
website acceptance remain outstanding. These fixture results are not live
PostgreSQL, protected task receipts or Step 3 acceptance.

A separate append-only task-metadata writer candidate now has 15 passing
SQLite/Blob tests, including calls to the existing task store. It first commits
a unique task/phase attempt and closes the restricted connection, then performs
one protected create-only archive write, and finally inserts a separate immutable
metadata-link row. The independent reader must qualify that row and its own
protected bytes before task attachment or completion. A worker hash is never
renamed into a task receipt. Lost archive/SQL acknowledgements remain consumed;
no repair or repeat write is exposed. Tests cover revocation before and after
archival, duplicate attempts from another writer instance, absent worker evidence,
cancellation after private worker success and unknown cessation. Successful
private worker evidence remains immutable when the public task is cancelled.
Its three-file source checkpoint was protected and independently read back,
receipt `cf5b124c56fd11918ef98e0c8c0abcc8c8e0941dede54a999cbbceb9c9b72470`,
version `2026-10-06T03:38:19.3518305Z`. Those files are now frozen. No live
metadata table, SQL qualifier, production enrollment or website activation is implied.

A separate two-table task-receipt metadata DDL candidate has seven passing
static source checks. It enforces unique task/phase reservations, exact reserved
row bindings, forced tenant/worker RLS, typed envelope/version fields and
append-only mutation guards. It revokes PUBLIC and inherited named default
grants. A separately enrolled restrictive current-user identity policy and fresh
restricted SQL qualification are still mandatory: caller-selected session
settings alone are not principal authority. The SQL has not been parsed or
behaviorally exercised by PostgreSQL, and no table or role was installed.

A separate inert signed-authentication candidate has 18 passing synthetic
RSA-token tests. Its service path requires a reviewed owned-API v2 audience,
fixed Entra issuer, app-only identity type, exact client/principal pair and
fresh exact database enrollment for a fixed service purpose. Token roles,
mutable email and caller-supplied tenant/purpose claims grant no rights. The
delegated-owner path remains separate and resolves an active database Owner on
every request; revoked users and legacy fallback are rejected. Tests cover
invalid signature, wrong audience/issuer/client/principal, delegated/app-only
cross-over, duplicate JSON, non-integer/expired timestamps, expiry during
authority lookup and current revocation. ARM, Graph and Foundry tokens are not
treated as tokens for this owned API. This follows Microsoft's
[owned-API access-token validation guidance](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens)
and [app-only API authorization guidance](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow).
No application, optional claim, consent, permission, route or runtime was
enrolled or changed. Actual v2/idtyp issuance and live service/owner acceptance
remain deployment gates. The combined 25-check source retainer initially failed
before checkpoint creation because sandboxed Azure CLI could not read its
existing profile. The checkpoint file was confirmed absent before a separately
authorized existing-sign-in source-retention attempt. Protected independent
readback passed, receipt
`55034d195308b0c8f9e228e7aeac6a0cb4735071fa1b49521582911752605f93`,
version `2026-10-06T03:58:19.9023954Z`. Its five files are frozen. No consumed
SQL/native operation was repeated.

A separate fresh task-metadata SQL qualifier passes 15 query/decision fixture
tests. It pins the private host, database, search path, login/capability/schema
owner, certificate path and bytes, TLS protocol, role flags and memberships,
durable commit settings and an independently accepted full schema fingerprint.
That fingerprint includes column types/defaults, policies, constraints,
indexes, triggers and function definitions/ACLs. Both task tables require forced
RLS and a separate restrictive identity policy. Writers may INSERT and SELECT
only the conflict-key columns, not protected envelopes or archive descriptors;
verifiers are SELECT-only. Column-level UPDATE/REFERENCES and database/schema
creation are also denied. The conflict-key SELECT requirement follows
[PostgreSQL 16 INSERT documentation](https://www.postgresql.org/docs/16/sql-insert.html).
These are metadata fixtures, not native PostgreSQL permission-denial evidence.
The three-file source family was independently protected and read back,
receipt `2b8e49f2d8744f04920f2d8690a69eadead41153a38238fe15c07b3c61a91a48`,
version `2026-10-06T04:08:24.2095637Z`; those files are frozen. No new table,
login, grant or connection was installed by this source qualification.

A separate signed-HTTP composition passes 12 local RSA/HTTP/task-store tests.
It requires the dedicated `Owner.Tasks` delegated permission rather than
silently reusing `Portfolio.Observe`. Each request verifies the actual local
fixture signature and current database Owner before the unchanged task adapter
rechecks account scope. Tests reach saved history/events/cancellation and reject
forged platform headers, duplicate bearers, cross-owner reads, revoked owner or
account access, app-only tokens and sign-in/configuration drift. A signing-key
outage returns a fixed no-store 503 without task writes. Disabled runtime
submission does not hide existing status or cancellation. Its three files were
independently protected and read back, receipt
`d5e1e61efd8587cc9893b3b76377d9f3de2d3b17c75000ada1fc26e5fae31390`,
version `2026-10-06T04:12:25.3523453Z`; they are frozen. No new scope, consent,
sign-in application or website route was enrolled by this checkpoint.
A read-only live query confirmed the existing dedicated observation API still
exposes only `Portfolio.Observe`, with no access-token optional claims or app
roles; its existing owner client and API boundary pass their unchanged verifier.
This confirms that the new task/service authorization cannot be assumed from
the previous observation acceptance. Existing registrations were not modified.

The combined selected candidate families, including metadata, native v2,
commit-controller v2 and the metadata writer, passed 197 tests with no failures
or skips. A preceding invocation of the commit family's test runner omitted the
tests import directory and produced three import errors; the corrected command
passed all 34 tests without changing frozen files. The library deprecation
warning remains unsuppressed. These results cover source/fixture behaviour,
not live PostgreSQL or signed-owner deployment acceptance.
The subsequent combined suite, including signed authentication/schema, SQL
qualification and signed HTTP, passed 249 tests with no failures or skips.
The 847 previously frozen repository source hashes remain exact.

### Step 3 dedicated task API and protected metadata composition 6 October 2026

The new **WealthRyse Owner Tasks API** is separate from the existing
observation-only API. Its registration exposes the delegated `Owner.Tasks`
scope, requests v2 access tokens and the `idtyp` claim, and adds no application
roles, credentials, redirect URIs, permission grants or tenant-policy changes.
Owner-client creation and both service principals have passed protected
readback; final registration verification passed at 05:30 UTC. Fresh signed owner and
exact current database service enrollment must still pass before task writes;
an issued app-only token or completed registration is not that authority.

The protected preflight is
`fae722a0fa47cc97c4b9028ffa8e1bfacd1c5554430e133e4cb7ea8a05ea9a68`,
version `2026-10-06T04:29:01.1871590Z`. API creation readback is
`74035ca579cb89ae9fd57a644e8f2d5ce27eaceaf998411b5dded0d671895fc8`,
version `2026-10-06T04:40:38.7565362Z`. URI-update readback is
`84eadc80a0afe9a832d668468445cdb190049f811169b05716d83a85ce31dac9`,
version `2026-10-06T04:51:45.3178467Z`. The new application identifier is
`693b133e-f1e1-4a06-b012-61ad8f7ae228`; its existing observation counterpart
and prior consent remain unchanged. Every effect has a separate create-only
protected intent. Lost or uncertain acknowledgements cannot trigger replay.
Owner-client creation readback is
`4e131d00fdbe93175e5b65eff0c0bc8adefa8412c4775d8b41377561d06319f9`,
version `2026-10-06T05:02:27.4498613Z`; its application identifier is
`d9569ff3-0039-4ab2-94c7-73f2b9b3b8d4`.
API service-principal readback is
`5c08852fad43a696adc6194485705c1c13cb52ca632ac80089e98f1290f410de`,
version `2026-10-06T05:13:14.4765807Z`. This does not enroll a service ACL or
qualify actual service-token issuance.
Client service-principal readback is
`de1081e48eaa6b07ce96ffa85a601779c12f354f2312a47cd843f9230cf104c1`,
version `2026-10-06T05:24:02.2458235Z`. Final independent registration
verification is
`5b7dc0480e145b0edbc1ae8fa6cf2c874d19e165891b4c790c28cd5aa5d57ab2`,
version `2026-10-06T05:30:06.7996233Z`, status
`step_3_owner_task_api_registration_verified`. No credential, consent or
permission grant was created. Neither sign-in nor staged routes are accepted
by registration alone.

The prospective native-v2 job creation passed protected readback,
`b831a645eaec3caef1e0e1a3c200119e439da7e2c003e04b2ef9aac7d4f5a91e`,
version `2026-10-06T05:01:53.1512923Z`. Registration qualification passed,
`0ff1b46a4d282c42cc573398e4c7ebc367d366c7b5307902ed73f264b3574b25`,
version `2026-10-06T05:13:57.1606631Z`. Its single start acknowledgement is
`c3dbf788b867cb014a3f585c15bb08097b91b9a775aecb99c9c0e2f74051d488`,
version `2026-10-06T05:37:48.8542506Z`. The read-only native execution query
reports `ibkrv2stg-ownrtree2-20261006-uf40oyr` Succeeded, from 05:37:44 to
05:38:16 UTC. Complete bounded telemetry, current native configuration and
protected full-result qualification subsequently passed, receipt
`02929d5a1d1a1f2247874490346af0ed2e173d0e3da98df48a4326c3e17b1ad6`, version
`2026-10-06T05:51:17.7937295Z`, status
`step_3_offline_native_tree_live_qualified`. Success, cancellation and deadline
cases each verified actual CLI execution and kernel cessation. No model or
SQL call occurred in that runtime. Live SQL-fence composition remains open.

A separate prospective private task-metadata composition passes 54 local
tests. It requires the accepted real worker-commit catalog before any new SQL
effect. Its new fixed database and five marked roles are distinct from all
consumed trials. Actual PostgreSQL probes will test append-only task/phase
reservations, conflict-key-only writer reads, independent restricted-reader
readback, tenant/worker isolation, denied mutations and escalation, and fresh
synthetic owner revocation. Probe records deliberately reference nonexistent
archive objects and cannot be accepted as protected receipts. Subsequent task
intent and cancellation links must independently match both committed SQL and
the exact protected archive versions, including the separately verified worker
outcome. Neither probe assembly nor a locally calculated schema hash accepts
production authority.

The full 24-module source graph and two SQL definitions exceeded Azure's inline
command-size limit. The new packaging preserves all original source bytes in a
324,216-byte document prepared for protected storage and pins its exact version,
size and hashes;
the compact launcher stays below the existing 65,536-byte limit. The verifier
uses only the existing read-only archive identity and fixed destination,
rechecks the legal hold before and after the read, disables redirects/retries,
and verifies every reviewed source before SQL credential acquisition. It
cannot select a latest version or arbitrary download. A separate create-only
bundle intent prevents repair or repeated upload after an uncertain result.
No bundle or metadata SQL job has yet been created. The 13-file source family
was independently protected and read back, receipt
`c6a12cbd798d264ea364e6baa044456891f330e21ace2550bcadebb46eb4e09f`,
version `2026-10-06T04:58:53.0383765Z`. Those files are frozen. This accepts
the source/54-test checkpoint, not the live SQL/archive composition.

The separate one-shot driver waits for that checkpoint and the accepted real
worker commit. Its first invocation stopped with `JSONDecodeError` while a
local scheduling copy was being written. No bundle, preflight, intent or job
was created; a separate Azure GET returned `404 ResourceNotFound` for
`ibkrv2stg-owntmeta-20261006`. The driver was corrected only after that invocation
had finished and the proposal was confirmed untouched. Partial JSON or UTF-8
now remains pending; it never confers authority. Complete failed prerequisites
still stop the driver. A second consumed-stage check after the wait prevents
resuming a proposal consumed by another process. Eleven driver fixture tests
pass, including those races, bounded expiry and unknown-effect refusal.
Each actual stage independently rereads protected evidence and current
authority. The prepared sequence is protected bundle, preflight, new manual job,
registration verification, one start and bounded evidence collection. All
stages preserve `step_3_complete=false` and `step_4_started=false`.

The broader selected candidate suite passed 309 tests with zero failures,
errors or skips, output SHA-256
`27bdccb1fc6827ba9c3c37374f3aaf6c4a8dc98edfb6fd605f029f75ac64a3f8`.
All 847 frozen repository source hashes and 20 candidate checkpoint manifests
matched fresh local byte checks. These checks leave Step 3 unaccepted and Step
4 unstarted. No received IBKR data was read or changed, no model call was made,
and no production runtime, evidence registration or website task route was
activated by this work.

Full repository discovery subsequently ran 2,980 tests: 2,978 passed, zero
failed, and the two existing Windows-limited FIFO/symlink fixtures were skipped.
Output SHA-256 is
`5252651fcc3df303bfaf76657c01e9a91c7de6fc2c15046f08a046a1a48dad4e`.
The repository policy gate separately passed with 293 tracked files, 828
scanned sources and 59 contiguous migrations, without prohibited paths or
secret patterns. Neither result includes live acceptance of the pending
native and signed-website composition.

A new institutional authority reader and signed HTTP composition passed 23
local tests on 6 October. Every lookup requires a fresh, independently qualified
restricted connection, a read-only consistent snapshot, the exact active
database Owner role, and the server's reviewed subject registry. Role expiry is
checked again after connection closure. Existing institutional Owner visibility
is preserved; paused, missing, archived or unrecognized subject states do not
grant task access. Only identifiers, status and role intervals are queried, not
email or IBKR/financial values. Signed tokens, email claims and old observation
consent cannot replace database authority. Tests verify that account pause
filters history and denies direct status/events/cancellation/submission, that
role removal or expiry blocks an otherwise valid signed request, and that
different owners cannot access each other's tasks.

These are RSA/query-adapter/SQLite fixtures, not live PostgreSQL or website
acceptance. An initial source-checkpoint invocation stopped at Azure sign-in
access before any checkpoint write; the scoped invocation using the existing
Azure sign-in subsequently passed protected retention/readback. No authority-reader login,
grant, production role or route has been installed. All previous source and
trial checkpoints remain frozen.

The expanded selected candidate/driver suite passed 346 tests across 33
modules, with zero failures, errors or skips. Output SHA-256:
`eab9cf9c6c244c86652194eb15d430301d1a9497c0587277f8bd1903e77e91bc`.
Repository policy passed again with 293 tracked files, 828 scanned sources and
59 contiguous migrations. The seven new worker-driver tests verify that a
consumed proposal, failed prerequisite, wait timeout or unknown job response
cannot cause a start/restart. Before continuing, the original waiter had to
finish without consuming any proposal marker. At 05:40 UTC it
finished with `TimeoutError`, before any preflight or effect. Separate local
checks found neither original nor v2 consumption markers; a read-only Azure
GET confirmed `ibkrv2stg-owncommit-20261006` is absent. The new driver then
continued this still-unconsumed proposal with a bounded prerequisite wait.
This is not a restart of a failed/consumed trial. It leaves the frozen controller
and every current authority check unchanged. No additional SQL role/database
may be installed while its predecessor-catalog gate is in progress.

The four-file institutional-authority source family subsequently passed
create-only protected storage and independent readback, receipt
`a67b0a5c1156e9b4ff80a331776eaaa7b33c1a35b34186a51815a5ea6793f312`,
version `2026-10-06T05:40:37.8330218Z`, status
`step_3_owner_task_institutional_authority_candidate_source_verified`.
Its 23-test output SHA-256 is retained in the record. Those four files are
frozen. Live authority-reader qualification, real owner sign-in, task routes
and runtime integration remain unaccepted.

A new trusted-side separated-runtime candidate binds the exact owner/task,
attempt, lease hash, revision, observation, operation, image, source,
configuration and runtime identity. Dispatch exposes no SQL connection,
credential, lease token, source values or arbitrary prompt. A separately
reviewed native/protected reader must produce two stable, exact bound proofs
and pass fresh qualification before and after reads. A bare Azure Succeeded
status, offline-canary record, wrong-task proof, drift, nonzero tool count or
missing kernel condition cannot accept cessation. The runtime and SQL
principals must differ. Lost launch acknowledgement cannot reconstruct a
handle or relaunch; repeated cancellation retains one owned stop task.
Seventeen local tests pass, including SQLite composition showing a committed
launch fence before remote dispatch and persistent quarantine after an
unknown acknowledgement. These fixtures do not establish actual remote
authentication, PostgreSQL/runtime composition or staged website acceptance.
Together with the eleven corrected waiter tests, 28 checks pass. Their new
five-file source family passed protected retention and independent readback,
receipt `b132fab0e78bd56344dd1efcb9834278d817602a56dc51f109d9e0d6bc1a8759`,
version `2026-10-06T06:10:01.4880029Z`, status
`step_3_separated_runtime_orchestration_candidate_source_verified`.
Those five files are now frozen; no production adapter is enrolled. The test
driver's printed `fixture_only` stage messages are mock calls, not additional
native effects or accepted live execution.

The existing UI uses same-origin website cookies, while the separately tested
owned-API router requires a signed bearer token. They cannot be mounted
together as a working authentication flow without an explicit bridge. A new
server-held session adapter validates an owned-API `Owner.Tasks` token, matches
its signed object identifier to the verified website session, then resolves
fresh database authority. It rejects browser bearer substitution, unsigned
identity headers, missing/expired sessions, swapped owner tokens and old
observation consent. Tokens are redacted from object representations and are
not returned to the page or browser storage. Eight local RSA/cookie-interface/
SQLite tests pass. The real protected session resolver, OAuth configuration,
restricted PostgreSQL connections and website deployment remain unqualified;
neither router nor that fixture grants a browser new authority.

The session bridge and new task-bound cessation archive adapter subsequently
passed 20 local tests and create-only protected retention/readback, receipt
`8aeeac10b1639d4324ac117a07dfe0554e9a8cd541ae406b4d98b56327587231`, version
`2026-10-06T06:25:55.3972648Z`. The five-file family is frozen. The collector
requires two stable, independently qualified native observations bound to the
exact task request and execution before creating archive bytes. A separate
reader checks current native evidence and the exact held archive version twice.
Cross-task/execution substitution, uncertain write acknowledgements, changed
bytes/version, removed protection and revoked reader authority withhold proof.
The adapter has no dispatch, SQL write, latest-version fallback or repair API.
Its injected native transport, identities and launch-fence qualification still
require live enrollment; the fixtures cannot accept an unbound offline canary.

The concrete server-held session-vault candidate then passed ten local tests
and protected source retention/readback, receipt
`380cbe82fb44edc73ed8caf1183d63c9de92aded609e07ae7c0e06934fde12c3`, version
`2026-10-06T06:29:17.5365678Z`. Its three-file family is frozen. The server
authenticates the dedicated API token and current database Owner before
issuance; the website adapter repeats current Owner checks on use. Only an
opaque random cookie leaves the server. Cookie settings are Secure, HttpOnly,
SameSite Strict, host-only and no-store. Tokens remain in bounded server memory;
expiry, sign-out or restart invalidates the session. Duplicate cookies,
observation-only consent, owner substitution and ambiguous browser credentials
are denied. Token acquisition, real sign-in, staged deployment and restricted
live task-store wiring remain open. No device sign-in was initiated here.

The real worker SQL/archive controller's preflight passed protected readback,
receipt `10b5912b0c4c850fed92d44a5b976047184708980372999aaa59012042542868`,
version `2026-10-06T06:17:37.8886494Z`. Preflight has no SQL/job effect and is
not the worker's final acceptance. Its controller-v2 driver then stopped with
`TrialError` at 06:32 UTC, with no job intent, acknowledgement, registration,
start or observation marker. A separate read-only Azure GET confirmed the exact
job absent. All six metadata effect/bundle markers also remained absent; the
verified local, unused dependent waiter was stopped after its prerequisite failed.
No frozen driver/controller or failed native trial was edited or restarted.

Local reconstruction reproduced a launcher defect: JSON persistence reorders
nested dictionaries, while Python `repr` embeds their insertion order into
source. Rebuilt compressed arguments, full-source hashes and definition hashes
therefore differed. The exact-integrity check rejected the plan before its
intent reservation or native dispatch. A separately versioned stable launcher
canonicalizes input metadata before source generation, preserving the original
worker bytes, image, SQL namespace, operation, identities, privileges and
zero-retry policy. Seven new regression tests prove exact persisted/rebuilt
bytes, nested-order independence and refusal to continue from any old effect.
The combined 41-test source checkpoint passed protected retention/readback,
receipt `f9fbc8056fe82cd57406fe0d67cc482c1bde6121248bf8f926040a3814c12b9f`,
version `2026-10-06T06:42:18.9614230Z`. Its five-file family is frozen. A new
one-shot controller-v3 started fresh qualification; it does not resume the old
preflight or accept any failed effect. All current prerequisite checks remain.

The untouched metadata launcher reproduced the same round-trip defect locally.
Its separately versioned deterministic correction passes seven additional
checks and the combined 61-test metadata suite. It preserves the original
24-module protected bundle, worker source and database/identity boundaries, and
requires the corrected worker's actual accepted final record before effects.
Its five-file source checkpoint passed protected retention and independent
readback, receipt
`c96d1d77310e1cb1eb55f16ffb7dfa0e1685270fd53291dc1bb0ccdf448bc3e3`, version
`2026-10-06T06:45:49.6841951Z`. Those files are frozen. The corrected metadata
one-shot waiter was then started, bounded to four hours and gated by the
corrected worker's accepted final result. At 06:48 UTC neither corrected trial
has an effect intent or accepted job. No metadata job, bundle or SQL action is
accepted by these fixtures. The separate current read-only guard diagnostic
passed at 06:47 UTC; it neither dispatches nor retrospectively accepts the
failed worker-controller-v2 stage.

The later selected suite passed 367 tests across 34 modules, with zero failures,
errors or skips, output SHA-256
`e75ab9e32089f2db0559addba1658810140a31a07a1a5688b89fa3310261969f`.
The repository policy check passed with 293 tracked files, 828 scanned sources
and 59 contiguous migrations. Local comparisons across 22 source manifests
found all 847 repository sources and 80 candidate sources unchanged, with no
hash mismatch or conflicting frozen digest. This run preceded the eight new
session-bridge fixtures; those passed separately.

The combined selection subsequently passed all 375 tests across 35 modules,
with zero failed assertions, test errors or skips, output SHA-256
`8aca0bc63f8cd06a88bf280268a2f5dd19129b0b8c3b81efd5ce6b6571a29db7`.
The run emitted the existing Starlette/httpx deprecation warning and a Windows
Proactor event-loop shutdown diagnostic (`WinError 995`); neither was a failed
live Azure execution or a failed test. Linux runtime acceptance is supported by
the separately retained native execution, not these Windows process fixtures.

The latest selected regression run passed 397 tests across 37 modules, with no
failures, errors or skips; output SHA-256
`879848040f8bf580309c4bde4e59036a742ef8abdfcc6bec6df6ea0746a34b7e`.
Repository policy passed again: 293 tracked files, 828 scanned sources and 59
contiguous migrations. Comparisons across 26 protected source manifests found
all 847 core sources and 95 candidate sources unchanged, with no mismatch or
conflicting frozen digest. Printed fixture stage messages are mock calls, not
native writes or live acceptance. This continuation read/wrote zero received
IBKR rows and made no production runtime, grant, login or route change.

After the two deterministic controller corrections, the selected suite passed
411 tests across 39 modules, with zero failures, errors or skips, output SHA-256
`208bd691d6bc05bd07d9e4bac1b3f820dae7584b52e64bab51078377b22d16ac`.
Repository policy still passes with 293 tracked files, 828 scanned sources and
59 contiguous migrations. The later comparison across 28 protected source
manifests verifies all 847 core files and 105 candidate files unchanged, without
conflicting hashes. These checks do not complete live receipt, runtime/fence or
signed website acceptance. Step 4 remains unstarted.

### Step 3 task-bound native telemetry and website acquisition, 6 October 2026

The owner again requested completion of Step 3 before Step 4. Two additive
five-file families address necessary missing integration boundaries without
altering frozen core sources or activating a production runtime.

The task-bound native family includes a metadata-only start override, strict
raw telemetry parser, independently bracketed native reader and an explicit
offline CLI producer. The static job definition and its reviewed hash remain
separate from the per-task start override, avoiding a self-referential definition
hash. The raw descriptor itself must match canonical expected bytes and its
recomputed digest: copying an expected digest into an unrelated offline canary
does not produce acceptable task evidence. Task, attempt, revision, observation,
operation and runtime identities are bound. Duplicate JSON members, noncanonical
bytes, boolean/integer substitutions, wrong execution/replica/container/time,
duplicate log records, extra environment variables, native drift and authority
revocation are rejected.

The CLI producer reads the owned subprocess's actual return code and requires
completed owned cleanup plus kernel tree cessation. It does not infer exit code
zero from Azure `Succeeded`. Caller cancellation during the loopback-request
wait retains child cleanup ownership. The producer uses only the established
offline tool-free protocol, not actual Foundry inference or portfolio results.
Its reader still requires reviewed, authenticated ARM/log transports and a fresh
matching durable fence. No live task-bound execution or PostgreSQL composition
is accepted by these local fixtures.

All 50 focused checks passed. The create-only protected source record is
`serviceaud-step3-owner-task-native-binding-source-v1.json`, receipt
`366494d5962a71649551096d8485852abdb96ce1fc2a43c4f5ef1f02c47aaf3e`, version
`2026-10-06T06:59:07.3279444Z`. Protected exact-version readback passed. This is
source qualification, not an additional native process trial or a task outcome.

The device-session family implements bounded server-side acquisition for the
already registered dedicated Owner Tasks client/API. It requests only that
owned API's `Owner.Tasks` scope, with no Graph scope, client secret, `openid` or
`offline_access`. A freshly authenticated website owner and independently
qualified current registration are mandatory trusted dependencies. Device codes
remain in server memory; only the Microsoft verification link/user code and an
opaque flow ID may reach the browser. A returned access token is independently
RSA-signature validated and checked against the exact expected owner and fresh
database Owner by the existing server vault before an opaque Secure/HttpOnly/
host-only/SameSite-Strict cookie is issued. No bearer or refresh token enters the
response body or browser storage.

Minimum polling intervals and `slow_down` are respected. Unknown creation/token
acknowledgements remain uncertain and are not automatically exchanged again.
Expiry, cancellation and shutdown clear held device codes. Existing website
authority and registration are rechecked around exchange/issuance. Same-origin
POST endpoints reuse the existing strict body/CSRF boundary; GET, cross-origin,
browser bearer, unsigned identity-header and browser-selected owner inputs
cannot initiate acquisition. The fixed HTTPS transport is bounded, rejects
redirects and does not log provider/token response text.

All 32 focused OAuth/RSA/HTTP/vault fixture checks passed. The create-only source
record is `serviceaud-step3-owner-task-device-session-source-v1.json`, receipt
`ecbaf6aded6f2bcb1135c080aa46485d8680a01494343a87fd219d7fd4b1b3b0`, version
`2026-10-06T07:07:10.5854156Z`. Protected readback passed. No real Microsoft device
code was requested, no consent/application/grant was changed, and no route was
installed in a live website. Native registration, real sign-in, restricted live
task-store wiring and staged end-to-end website acceptance remain outstanding.
The acquisition protocol was checked against
[Microsoft's device authorization documentation](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code)
and [RFC 8628](https://www.rfc-editor.org/rfc/rfc8628.html).

The expanded selected suite passed 454 checks across 43 modules with zero
failures, errors or skips, output SHA-256
`a4c0b607d8f45e8ed0f3fdca1d1cd9bade9003ec6fa36278bd4452bb110b907f`.
Repository policy passed: 293 tracked files, 828 scanned sources and 59
contiguous migrations. The pre-existing Starlette/httpx deprecation warning is
unchanged; it is not a failed test or live-runtime acceptance.
Comparison of source-bearing maps across 59 retained local records found all
847 frozen core files and 115 frozen candidate files unchanged, with zero hash
mismatches or conflicting digests (962 unique files). This integrity comparison
is not a substitute for independently authenticated protected/native acceptance.

The corrected real worker controller passed protected preflight, receipt
`bfc9c1e8bfec971afae226f1ef7fe296b749af53d108b0f1f1e75bb28623ff81`, version
`2026-10-06T07:08:21.2639392Z`. The one-shot driver then began its independent
current-prerequisite checks for job creation. Its final combined SQL/archive
result is still pending; the metadata driver remains gated by that result. No
preflight or printed mock-stage message is treated as a committed SQL receipt.
No received IBKR rows were read or modified by this continuation. Step 3 remains
incomplete and Step 4 remains unstarted.

Remaining Step 3 acceptance work:

The isolated schema/restricted-login, worker/tenant isolation, concurrency,
reconstruction, quarantine and fresh authority-revocation substage is complete
with fresh protected/native readback. The following three gates remain:

1. Deploy and qualify the now source-tested separate protected worker-audit
   writer and independent verifier on the isolated host.
   The read-only diagnostic/current baseline and prospective corrected audit
   SQL/revocation qualification are now complete. The earlier failure's absent
   historical comparison remains unaccepted, rather than retrospectively waived.
   Prove real archive readback, receipt binding and database commit, including
   cancellation, revocation and lost-acknowledgement handling.
   The existing audit permission-check worker is not this receipt adapter.
   Enroll and validate the exact new worker-evidence schema/build before writing
   any new evidence into the production ledger; the current daily reporting
   reader only accepts its reviewed inspection/observation registrations.
   Verify exact archive versions and bytes using a separate authenticated
   read-only boundary, not writer-returned booleans. A replay or lost
   acknowledgement must not fabricate `newly_created` or rearm a consumed task.
2. Bind the accepted runtime to durable task controls and prove actual process
   cessation and persistent runtime quarantine under faults. A synthetic stop
   callback does not establish this.
3. Connect fresh signed owner authentication and website task history, live
   status and cancellation. Complete staged deployment and end-to-end acceptance
   before activating owner or specialist runtimes.

IBKR immutability, daily reporting at 18:00 Hong Kong time, the Alpha World MS
pause and the deferred Entra policy review remain unchanged. Step 3 is underway;
the six specialist runtimes and the entire Agentic AI programme are not declared
operational by this source checkpoint.

### Step 2 final model qualification and bound acceptance, 5 October 2026

The original model trial failed its local audience-format check before any
secret-value request or model call. Its protected failure receipt is
`db54fd982da2594b191a3dcf218e18e86df565c41e96a5011ddd4f54c64f958c`.
A separate metadata-only diagnostic confirmed the endpoint, tenant, principal,
client and expiry, but not a URI-only audience match. It read no secret values,
opened no SQL connections and made no model calls. Its protected receipt is
`02fd5151c19157af14ee119a79266d27adfd0ad3ca7ca5e94301685afd23c54d`.
The literal audience was not logged; no GUID audience is asserted here.
Microsoft documents resource audiences and destination-API token validation:
[access-token guidance](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens),
[audience claim reference](https://learn.microsoft.com/en-us/entra/identity-platform/access-token-claims-reference).

The additive corrected trial preserves all 800 predecessor source hashes;
three new reviewed files produce an 803-source manifest. All consumed jobs and
executions remain unchanged. Azure's `null` representation of an empty job-secret
list is accepted only in a detached comparison copy, with the native original
retained. No source, job, identity or grant was modified to retry an old attempt.
The corrected worker retains fixed resource/client selection and binding checks;
actual Azure responses must validate the token at each destination. It reuses
the exact model identity's two direct registry/model assignments, with no new
database, secret, storage or Owner grant.

Corrected full preflight receipt:
`003b005dccef8f2785e86269a35aff1a0e8d44242f0f0f392a4469416bf32b79`,
version `2026-10-05T14:34:44.2489058Z`. Native job acceptance receipt:
`3a9c345af563e632cd6e4762acb0d66f27f16df9654964cce4861fb8aaa5167a`.
The single execution `ibkrv2stg-ownmod2-20261005-2u4sv88` succeeded from
14:41:03 to 14:41:37 UTC. Independent running/ready capture receipt:
`8fcdbb6b8bfdb020df85ccb375b7c9003736aef53f7f88fd2da9e09b8a765100`.
Complete bounded execution-family/native verification receipt:
`0bdfd6df3b6f26ca5c1b767e69c4444196b7a4caf576d2571d693a16e5d46b24`,
version `2026-10-05T14:43:31.6784552Z`.

The live record verifies genuine `ForbiddenByRbac` secret denial and
`AuthorizationFailed` server-management denial, not wrong-audience, firewall or
generic connection failures. Linux UID/GID 10001, zero effective capabilities,
non-dumpable parent, no-new-privileges and disabled core dumps passed. Parent
environment, memory, descriptors, root and a synthetic memory address all denied
same-UID probes before and after the model turn. Actual credential bytes were
not probed. The pinned runtime's six execution checks and one-request/one-accept
relay passed; no declared tools were forwarded and no SQL credentials were configured.

The final independent review explicitly records `azure_sys`'s nine accessible
relations, 17 executable non-catalog routines and three sequence objects with no
usage/SELECT/UPDATE access. No provider routine or stored monitoring value was
read/executed. These object privileges are not represented as denied or as safe
for arbitrary execution; the model has no SQL credentials and the qualified
task API exposes none of these objects. The owner-standing-authority receipt
remains bound, with IBKR immutability and daily 18:00 Hong Kong reporting preserved.

Two read-only final-review assumptions failed closed before acceptance: the
original TLS observation and later subgate receipt are distinct chain levels;
sequence-object existence is distinct from effective sequence access. Their
protected records are `d10e44d4d358a6f04b94c0b8d823ace32ea525f21a2dc8db3b3e3c11dded93e7`
and `5e86de01b7b3d09c5454601e3f13fe3b15fe1227efd7e846d0342065094d394a`.
Neither caused a native restart or source/runtime modification.

Final bound acceptance receipt:
`65ed4e07115fac9bbd530797b046e6a2e0b8763f9398fd16bb9b0175d891a607`,
version `2026-10-05T14:53:45.4632031Z`, status
`step_2_isolated_integration_accepted`. It freshly binds SQL, TLS, provider,
authority, native ready/terminal evidence, source hashes and QA. Earlier subgate
records' false completion flags are preserved as historical state; only this
aggregate accepts step 2. Step 3's flag remains false.

A new-process final readback again verified the protected acceptance and its
SQL/TLS/provider/authority/model receipts, current private host, exact identities
and direct grants, preserved job definitions, the single successful execution,
and all 803 current source hashes. Final readback receipt:
`8a094fc90ce359dcd58d200f47a4d79afc3854abcd3ba756e98d36bc518fd2aa`,
version `2026-10-05T14:55:31.6982467Z`, status
`step_2_fresh_protected_and_native_readback_verified`. It performed no native
write, SQL operation, model call or Step 3 activation.

### Earlier deployment checkpoints

The chronological checkpoints below retain earlier states and do not supersede
the current scope-limited acceptance above.

Step 1 — secretless live runtime qualification — completed on 5 October 2026 at
00:19 UTC. Protected native ready-container capture, terminal/full-log acceptance
and separate-process fresh live readback passed. Step 2's SELECT-only catalog
execution subsequently succeeded on the dedicated private test host, from
01:50:47 to 01:56:13 UTC. Protected evidence reconstructs all 13 catalog
collections; the catalog connection passed certificate/hostname-verified TLS.
Separate-process protected readback and fresh live verification also passed.
The later approved policy correction passed fresh source QA: 2,508 tests
discovered, 2,506 passed and the same two Windows-only skips. Policy and
compilation passed. All 750 prior source hashes remain unchanged; two additive
policy/test files produce a 752-source manifest. Public network access
remains disabled, the existing test-secret version is pinned, and existing
service definitions are unchanged. The catalog exposed PUBLIC CONNECT/TEMPORARY
defaults on Microsoft-owned system databases. The current exact-one-database
permission specification did not accept those defaults. The owner subsequently
approved an Azure-specific test-policy correction, limited to the two
Microsoft-owned databases on this isolated host. The additive implementation
and its verification are recorded below; the historical collector and shared-host
policy are unchanged. No SQL setup was attempted, and restricted-login and
credential-isolation acceptance remain pending. Step 3 is explicitly on hold
until step 2 is accepted.
The later private SQL trial preflight passed 2,538 discovered tests, with 2,536
passing and the same two Windows-only skips. Eight additive files extend the
752-source policy checkpoint to 760 without changing any prior source hash.
The installer, fresh-login harness and fixed one-shot controller are prepared,
not dispatched. An additional correction accepts Azure's empty descriptive
`ephemeralStorage` field without weakening resource, environment or identity
checks. Its two new source/test files passed separate protected review: 2,545
tests discovered, 2,543 passed and the same two reviewed Windows fixture skips.
Policy and compilation passed. All 760 predecessor source hashes remain
unchanged; the original preflight and physical dispatch scope are preserved.
The dedicated test identity and its two exact registry/secret-leaf grants passed
protected verification. The subsequent job-install request had no verified
acknowledgement; a fresh GET found it absent. Protected validate-only evidence
identified a 33-character job name exceeding Azure's 32-character limit.
That attempt remains consumed. A separate 25-character job continuation passed
nine focused tests and protected provider/full-QA preflight. Complete QA
discovered 2,554 tests, with 2,552 passing and the same two reviewed Windows
fixture skips; policy and compilation passed. Corrected-job installation was
acknowledged. The additive null-descriptor review subsequently passed 2,560
discovered tests, with 2,558 passing and the same two Windows fixture skips.
The installed job passed protected verification. Its single START was
acknowledged, but execution `ibkrv2stg-ownsql-20261005-wrqzuc0` failed with
container exit code 1 and the fixed launcher-failure line. The consumed attempt
has not been restarted. An image-root guard correction passed seven focused
offline tests and complete QA (2,567 discovered, 2,565 passed, the same two
Windows skips). Read-only registry inspection independently verified the exact
pinned image's `/app` default and both content digests. A separately named
corrected continuation subsequently passed 15 focused offline tests and protected
full preflight (2,582 discovered, 2,580 passed, the same two Windows skips).
Azure's validate-only API accepted its proposed definition. Job installation
subsequently passed protected live verification. Its single execution reached
the trusted SQL worker and reported synthetic fixture preparation complete,
then failed the first restricted-login identity/scope check. Full bounded
execution-family logs and native failure are protected. No permission-trial,
restricted-login, provider-behavior or model-isolation pass is established.
A separately named read-only session diagnostic passed nine focused tests;
its complete source preflight subsequently passed (2,591 discovered tests,
2,589 passed and the same two Windows skips). The prepared test fixture and
consumed failed execution are preserved, not recreated or restarted.
The diagnostic execution then succeeded and its protected full-family/native
evidence independently verified the registered fixture markers, 84 column
privileges and the search-path string mismatch. The prepared-fixture
qualification preserves all 777 prior source hashes and passed full QA
(2,601 discovered tests, 2,599 passed, the same two Windows skips), compilation
and policy. Its protected install passed, then a single execution failed during
argument decoding before any canonical SQL-worker record. Native ready-container
and full five-line failure evidence are protected. A separately named safe-encoding
continuation passed 12 focused tests and protected full preflight: 2,613 tests
discovered, 2,611 passed and the same two Windows fixture skips; policy and
compilation passed. All 780 previous source hashes are unchanged. Its installed
definition passed protected verification, its one START was acknowledged and a
native running/ready container was captured. All eight restricted-login checks
then passed, including 21 denied privilege probes, concurrent idempotency/CAS,
forced tenant/owner isolation and fresh authority revocation. Their independent
protected subgate verification passed. The wrapper failed its later positive-TLS
statistics check; neither the hostname nor provider probes was reached. A
separate TLS/provider observer passed 12 focused offline tests and protected
full preflight (2,625 discovered, 2,623 passed, the same two Windows skips).
Its installed definition passed protected verification; its one-shot execution
failed the subsequent restricted-role statistics diagnostic. The positive TLS
routine did not return an acceptance record, and hostname/provider probes were
not reached. Its full bounded family and native failure are protected. A new
observer accepts an absent capability-role statistics row only as a diagnostic,
not TLS proof. Fourteen focused tests passed; all 786 preceding source hashes
remain unchanged, with three additive files producing 789 sources. Its fresh
full preflight passed (2,639 discovered, 2,637 passed, the same two Windows
fixture skips), compilation and policy passed. Its installed definition passed
protected verification, and its single execution Succeeded. Fresh-login
verify-full TLS 1.3 and the wrong-certificate-hostname test passed; registered
synthetic rows and privileges were unchanged. The capability-role statistics
view returned no row, as predicted. Both Microsoft-managed provider databases
accepted the restricted connection, so their object/monitoring boundary remains
unaccepted pending review, rather than being treated as a denial. A new
metadata-only observer passed 17 focused tests and full protected QA (2,656
discovered, 2,654 passed, the same two Windows fixture skips), preserving all
789 predecessor hashes. Its single execution failed during module import before
reaching the SQL worker; that failure is protected and the attempt consumed.
A separately named correction moves the build-only import out of runtime module
initialization and passes 20 focused tests, including isolated execution of the
actual packed module graph. All 792 predecessor hashes remain unchanged; its
795-source full preflight passed (2,676 discovered, 2,674 passed, the same two
Windows fixture skips), compilation and policy passed. Installation of the
separately named corrected observer passed protected verification; its one-shot
execution subsequently Succeeded. Both complete provider inventories and native
execution evidence passed protected verification; a separate fresh-process
protected readback also passed. The inventories exposed the additional object
privileges described above. Provider-object/semantic access and model/process
credential isolation remain unaccepted.
Step 3 is paused.
These are the remaining Agentic AI deployment steps, not a statement
that the original eight system upgrades or complete agent programme are finished.

As of 5 October 2026 (London time), following the native runtime diagnosis,
SQL and scheduled-report acceptance checkpoints, the
specialist agentic platform is still in implementation and verification.
Scheduled daily reporting is active. A pinned, tool-free Codex CLI conversation
has passed using Azure OpenAI. Owner-task safeguards and the restricted review
worker remain source only for production. The isolated synthetic PostgreSQL
behaviour rehearsal passed; fresh non-owner login, credential isolation and
authenticated runtime integration remain pending.
The protected read-only catalog diagnostic succeeded, but the shared database
host failed the restricted trial's isolation gate. No setup was attempted there.
The fixed-scope journal now has local process-restart acceptance and a successful
synthetic Azure storage rehearsal. Separate fresh-process remote verification
passed; neither result qualifies a live SQL or agent-execution adapter.
One separately named private PostgreSQL test host is now Ready and its unique
new test credential stored. Protected host acceptance and separate-process remote
readback passed, including unchanged existing-service definitions. At that host
checkpoint, no SQL setup or catalog worker had been dispatched. The later
5 October catalog execution is recorded below; SQL setup remains pending.
No IBKR data was imported by this workflow. A separate no-identity, no-secret Manual log canary has now run
once and succeeded. All nine protected wire parts now reproduce its exact full
4 MiB synthetic record, with a protected owner-observed transport acceptance.
Separate fresh-process remote verification passed for that original wire record.
The whole observed execution-family protocol inventory now also passed and is
retained; separate fresh-process verification of all protected objects and the
full live stream passed. Original automated capture remains unverified. Complete
observed protocol evidence does not exclude zero-log replicas, platform
replacement or future late delivery. Complete provider inventory and fresh
restricted-login acceptance remain pending. The earlier isolated-host
checkpoint below is historical and is not reused as current source QA.
The pinned-image runtime checker and launch contract passed source QA. The first
ACR quick-run failed during explicit image pull, before the checker started;
that attempt remains consumed. A separately reserved standard pull diagnostic
now succeeded with no database/model credential or forced user override. Its
exact image digest, complete logs and default effective UID/GID passed protected
acceptance and fresh-process live readback. This qualifies observed runtime
metadata in ACR, not the complete Azure Container Apps runtime, SQL/TLS, restricted
login or credential isolation. A secretless Container Apps preflight passed and
Azure acknowledged creation of its separately named managed identity. Strict
verification stopped on the provider's `resourcegroups` URI spelling, rather
than the registered `resourceGroups` spelling. Read-only inspection confirmed
the expected name, location and tags, zero assignments in the three queried
Azure scopes, and absence of the planned registry grant and job. The narrow
continuation passed 109 focused tests, full repository QA and protected live
preflight. All 725 prior source hashes remain unchanged; exactly two additions
bring the manifest to 727. The original identity-create attempt remains
consumed. The separately reserved registry-only AcrPull grant passed fresh
permission acceptance and protected readback. The subsequent installation
attempt returned no verified acknowledgement. Read-only Azure inspection found
an explicit invalid-name rejection: the proposed job name had 35 characters,
exceeding the provider limit. Two validated GETs found that job absent. The
attempt remains consumed; its protected failure evidence is retained. A separate
31-character target subsequently passed preflight and Azure acknowledged its
creation without recreating the identity or grant. Final readback acceptance
stopped on native metadata differences described below. A subsequent separately
reserved execution completed successfully, but its running-replica validation
was not accepted. Protected diagnosis identifies an additional native execution
field, `imageType: ContainerImage`; the final replica inventory was already empty.
A new parser and qualification controller passed source QA and protected
preflight. Its execution capture stopped on a replica whose native containers
array was still empty immediately after startup. That response is retained.
A separate readiness adapter and qualification controller passed 13 focused
tests, full repository QA and protected live preflight. Its fresh execution reached
a ready Running container, but capture stopped because the strict parser did not
yet support the native optional `containerId` field. All three stopped captures
remain unqualified and are preserved. A separate additive compatibility adapter
and new qualification controller passed 16 focused tests; full QA and live
preflight passed. Its single new launch passed live ready-container capture and
terminal/full-log acceptance and separate-process fresh live verification.
This qualifies secretless runtime metadata, not database/TLS integration
or activation of the owner and six specialist runtimes.
Its first unaccepted candidate stopped twice before preflight retention at a
300-second test deadline. Independent local discovery and captured QA passed;
neither substitutes for live acceptance. Fresh Azure/storage absence checks
and a protected diagnosis now permit explicit revision of only the two
unaccepted candidate files. All 727 protected predecessor sources remain
unchanged. The reviewed replacement has a new operation and evidence namespace
and passed protected preflight with bounded 600/90/90-second full-test/policy/compile
limits, without removing tests, relaxing skips or accepting partial results.
Secretless Container Apps runtime metadata is now accepted. No credentialed
worker, database/TLS integration or SQL setup has been accepted at this checkpoint.
The owner production runtime and six autonomous
specialist runtimes are not active.
Use the statuses below rather than treating approved plans or passing unit tests
as deployed capabilities. The earlier checkpoints remain historical evidence.

- **Completed decisions:** six specialist roles, standing operational authority,
  unchanged IBKR source data, direct owner Codex use, and website/email reporting
  at 18:00 Hong Kong time are recorded requirements. The owner selected
  `anthony.ying@wealthryse.ai` as recipient. The owner subsequently approved
  website inbox plus Azure email notices because Exchange administrator access
  is unavailable. The Microsoft 365 sender remains disabled; its earlier
  selection and administrator handoff below are historical, not active prerequisites.
- **Engineering foundations and gateway acceptance:** role/scoping checks, a read-only
  observation broker, source-preservation checks, audit-service handoff, daily
  windows/outbox, verified daily-report compilation and commit-before-send
  dispatch, a read-only owner daily-report inbox with automatic updates, and the
  separate owner Codex setup page. The protected SQL preflight passed 1,515 tests;
  complete post-correction regression passed 1,516 tests. The subsequent
  restricted-login source checkpoint passed 1,603 tests. The final protected
  lifecycle/boundary preflight passed 1,672 tests, compilation and repository
  policy. The protected-journal preflight discovered 1,781 tests: 1,779 passed
  and two OS-specific fixtures were explicitly not exercised. Compilation and
  repository policy passed. The subsequent isolated-host preflight discovered
  1,875 tests: 1,873 passed and the same two reviewed Windows exclusions were not
  exercised. Its frozen manifest covers 697 code/test/SQL files. Exact counts
  and frozen source versions are recorded
  below and in retained evidence; these are
  source checks, not production financial or visual acceptance. The owner inbox
  is now deployed on website revision 112. The actual
  website database login passed private HTTP and tenant-isolation checks; the
  real signed-in Chrome owner session, navigation, inbox, refresh control and
  layout were checked. The verified 2 October daily report is now published and
  its real owner detail view passed; it appeared without a manual refresh. The
  owner confirmed receiving the single Azure test email. The real readiness
  event passed its live query, and the informational rule is now enabled with
  protected acceptance. The owner separately confirmed receiving the real
  report-ready notice. The independent missed-publication watchdog is now
  enabled. A protected rehearsal and production-payload smoke test passed before
  the daily publisher was scheduled at 18:00 Hong Kong time. Exact schedule
  readback and unchanged settings for all 34 other jobs passed. The first
  natural clock-triggered execution on 3 October passed independent protected
  metadata acceptance. Recipient email delivery
  for this particular run has not been confirmed.
  Scheduled reporting does not imply that the six autonomous specialists or
  complete system-wide activity capture are active.
  The observation broker now requires protected audit intent before invoking a
  tool and protected completion before returning its result. A new server-owned
  authority reader reloads current grants on every check. The default-disabled
  interactive gateway validates signed delegated tokens and binds identities to
  server configuration and current database records. Its synthetic integration
  tests pass through the actual broker and deterministic analytics tool. The
  isolated gateway is now deployed. Real owner sign-in, a read of 444 U16088571
  performance observations and independent protected audit verification passed.
  This does not deploy an autonomous specialist or the owner Codex runtime.
- **Account reader permissions and identity verified live:** a temporary NOLOGIN role
  received exactly 41 SELECT column privileges across nine tables/views for user
  authorization and NAV-series reads. The rehearsal checked eight forced-RLS
  tables, rejected four forbidden-column reads and verified no write privileges.
  LIMIT 0 checks retrieved no financial rows. All temporary grants and the role
  were rolled back, with a protected receipt verified. The subsequently installed
  permanent capability/login passed fresh-login provisioning checks. A dedicated
  manual worker and Azure identity passed independent acceptance under their own
  credential, with protected receipts and confirmed provisioner cleanup. This
  first capability covers only the performance-series tool, not every analytics
  tool, and enables no agent.
- **Database foundation deployed:** migrations 056/057 installed the
  audit ledger, immutable daily reports and delivery queue. All three tables
  force tenant isolation. The capability roles cannot log in. The reporting
  capability now has one restricted service login with explicit SET permission,
  no inherited or administrative grant. The audit capability has a separate
  restricted service login with the same membership restrictions; its manual
  access check is verified. One fixed manual producer has recorded a real
  permission-inspection activity; general specialist capture is not enabled. The
  website-reader role remains dormant. PostgreSQL-created ADMIN-only lifecycle links remain with
  the installing administrator. No schedules, website releases or source-data
  changes were introduced.
- **Authority store and reader deployed:** migration 058 added two
  forced-tenant-isolation tables. A subsequent protected enrollment created one
  control row and one grant permitting only U16088571 performance-series reads
  for seven days, with background work disabled. The separate
  NOLOGIN reader capability now has one restricted login with explicit SET
  permission, without inherited or administrative membership. Both identities'
  direct permissions and reachable roles were verified: only the capability can
  SELECT these tables, with no unrelated-table access, privileged-function access
  or schema creation rights. The manual worker passed using its own managed
  identity and private credential, including denial of an unregistered principal
  through the actual authority reader. The reader itself does not authenticate
  incoming callers or enable a specialist; the separate gateway supplies signed
  caller authentication. The actual read and independent audit acceptance passed,
  with protected start and completion records matching the returned result hash.
- **Earlier reporting-identity preflight verified live:** the manual reporting preflight job
  passed under its own managed identity and a fresh restricted database login.
  The temporary administrator-capable provisioner was removed. A subsequent
  read-only execution verified protected audit storage and rendered an empty
  daily window without creating report records, queuing delivery or sending
  email. The installed job definition still performs access checks only; no
  scheduled report producer or email sender was active at that earlier checkpoint.
  The later scheduled publisher and Azure notice activation above supersede that
  historical manual-only state; the installed preflight worker still only checks access.
  The reporting code now also requires detailed evidence for every event and
  previous-period boundary, checked against registered, reviewed producer builds.
  A new live read-only preview passed against the expanded registry, without
  creating a report or sending email. Unknown builds or missing details block
  compilation.
  A later fixed-date manual execution committed one closed daily report and two
  pending delivery entries. Preview, exact replay and fresh-login readback passed;
  no email or website publication occurred. At that earlier checkpoint the
  installed access-check worker definition remained unchanged, and no reporting
  schedule was active. The subsequent daily publisher is now scheduled.
- **Audit identity verified live:** a separate manual worker authenticated with
  its own restricted login and verified append-only ledger privileges, forced
  tenant isolation and the enabled audit guards. Both the login's direct
  permissions and every role it can assume were checked. A subsequent reviewed
  execution committed and protected two events for a real permission inspection,
  with exact replay producing no duplicates. This is a trusted manual rollout
  producer, not an autonomous specialist or a general authenticated ingress API.
  The separate reporting identity independently verified both events and their
  protected details without database writes.
  A later manual trial stopped after the intent commit and resumed in a separate
  worker with the same source and operation ID. It recovered the missing archive
  copy, preserved the original event hash and completed without duplicate events.
- **Completed isolated database rehearsals:** 17 audit-ledger checks and 23
  daily-delivery checks passed with all temporary changes rolled back. Those
  trials did not deploy the services or send daily reports. A subsequent
  committed synthetic rehearsal passed 35 checks for report publication,
  access separation, tenant isolation and delivery safeguards, then removed
  its temporary schema and roles.
  A later authority trial passed 12 live database checks, including stop control,
  tenant isolation, grant expiry and revocation, then removed its temporary schema
  and role. Migration 058 was subsequently installed dormant, as recorded below;
  the isolated trial did not itself activate production authority.
- **Completed operational evidence:** separate database/registered-report
  recovery exercises and the deployed monthly schedule watchdog are recorded
  below. A full application disaster-recovery rehearsal remains outstanding.
- **Isolated Codex runtime verified under service identity:** Codex 0.160.0
  passed a complete synthetic CLI conversation using the existing diagnostics
  managed identity and approved Azure OpenAI deployment. The pinned Linux
  binary, native model metadata, scoped JSONL lifecycle, exact reply, EOF and
  process exit passed. One exact native tool declaration was verified and
  discarded; no tool was forwarded or executed. The earlier experimental
  app-server trial and direct
  Responses request also passed; the local CLI identity's earlier HTTP 401 is
  historical. This new trial sends no portfolio data and enables no tools.
  It does not establish owner website operation or production suitability.
- **Owner-task controls built, not deployed:** migration 059 and a metadata-only
  service implement scoped sessions/tasks, idempotency, revision CAS, worker
  leases, sticky cancellation and protected receipt bindings. Thirty-six service
  tests and twelve static SQL guards pass. Explicit tenant-setting checks now
  reject missing scope even though the older database helper defaults to the
  platform tenant. No production migration installation, worker grant,
  website task endpoint or autonomous activation occurred. Static/SQLite checks
  do not establish live PostgreSQL RLS or production cancellation acceptance.
- **Restricted owner-review worker built, not deployed:** 48 coordinator tests
  pass, independently reviewed. Protected intent precedes observation/inference;
  authority, principal, lease and cancellation are rechecked. Unknown execution
  or unverified cessation quarantines the worker rather than repeating a model
  request. No concrete CLI, database, model, prompt/result or delivery adapter is
  installed. Durable reservation/quarantine and credential isolation remain
  integration gates, not properties proven by in-memory tests.
- **Owner-task isolated SQL rehearsal accepted:** the first execution failed
  during injected-module startup; the reviewed packaging correction passed an
  actual credential-free bundle import test. The next execution stopped during
  disposable-role setup, before any of the eight SQL acceptance checks. Protected
  read-only diagnosis verified separate creator/self-grant records. The exact
  marked, empty synthetic database and NOLOGIN role were subsequently removed,
  with both absences verified and evidence retained. The separately reviewed
  corrected rehearsal subsequently passed all eight synthetic PostgreSQL checks
  on version 16.15. Its exact disposable database and NOLOGIN role were then
  removed, with protected run and cleanup acceptance independently read back.
  This used administrator-authenticated connections under SET ROLE, not a fresh
  non-owner service login. Production migration, runtime activation, credential
  isolation and IBKR data correction are not claimed.
- **Fresh restricted-login stage built, not executed:** three inert components
  now specify exact metadata-only column privileges, trusted preparation and
  eight genuine non-owner login acceptance phases. Eighty-seven new local tests
  cover their contracts and denials. Preparation rejects non-target PUBLIC
  CONNECT before creating objects, verifies permissions with LOGIN disabled,
  and rolls activation back if final validation fails. No restricted login,
  disposable database, secret, grant or cloud resource was created at this
  checkpoint. Fresh-login, protected-receipt, signed-owner and model-credential
  acceptance remain outstanding, as detailed below.
- **Trial lifecycle and exact cleanup built, not deployed:** 29 synthetic lifecycle
  tests and 21 synthetic cleanup tests pass after independent review. Protected
  intent/outcome contracts, fixed-scope replay prevention and separately reviewed
  cleanup are specified, but concrete durable/authenticated adapters are not
  installed. Cleanup rejects partial or unknown scopes, active sessions and unsafe
  catalog lookup before any deletion. No live setup or cleanup ran in this stage.
- **Shared-host boundary observed, not suitable for this trial:** one protected
  catalog-only execution found five non-target databases with PUBLIC CONNECT
  grants. The proposed trial database and roles remain absent. Existing database
  ACLs were not altered to make the gate pass; an isolated registered target and
  provider/schema-owner compatibility acceptance are required next.
- **Journal storage qualified for a synthetic rehearsal only:** a fixed-scope
  reservation/claim/completion chain now rejects competing ownership, lost
  acknowledgements, reconstructed permits and replay under another operation ID.
  Actual local process races and abrupt exits passed. Two Azure thread clients
  produced one conditional-create winner; an acknowledgement lost after a
  committed claim prevented reissue, and six records were retained with sticky
  quarantine. No SQL, portfolio rows or model calls were involved. The concrete
  local and Azure backends always refuse live execution. A protected real-control
  namespace, executing identity and authenticated integration remain outstanding.
- **Next integration work:** review an isolated target and retain the documented
  CLI route as the integration candidate; qualify genuine restricted non-owner
  login and credential boundaries,
  then integrate the separately registered durable task controls and concrete
  protected worker/audit/authentication adapters, owner website history/status/cancellation,
  separate credential isolation and registered model-activity capture. The
  earlier app-server trial remains experimental staging evidence, not a
  production integration grant.
- **Protection partly deployed:** administrator-removable holds are configured
  on both registered raw-source containers and both probe/evidence containers.
  Both synthetic probe suites passed and 464 existing v1.1 versions were
  preserved. After the separately approved container-scoped reader grant,
  v2 `ibkr-raw` inventory readback succeeded with zero objects. No source data
  were mutated; full source-path integration is not claimed complete.
- **Azure report-ready notification accepted:** the informational
  `ibkr-v2-specialist-daily-report-ready` rule targets the existing enabled owner
  email group. Nine Azure query acceptance cases passed. Exact readback and
  protected intent/completion receipts initially verified a disabled rule and
  unchanged email group and manual reporting job. It was subsequently enabled
  after real owner website acceptance. The owner confirmed both the synthetic
  test email and the separate real report-ready notice. A ready event requires
  a fresh integrity read of a committed, published report; telemetry alone is
  not recipient delivery.
- **Daily reporting deployed and first scheduled cycle accepted:** the private
  owner inbox, its reader/denial paths, real report publication and Azure notice
  receipt are accepted. The daily producer and independent missing-publication
  rule are enabled. The natural 18:00 Hong Kong execution on 3 October succeeded;
  independently retained protected metadata binds its published report hash,
  six ledger events and unsuppressed Azure report-ready alert. Desktop-side
  private database report/ledger byte verification and recipient email receipt
  for this run remain unverified. The acceptance covers the specialist ledger,
  not complete system-wide activity capture. Manual executions alone do not
  prove timer operation. The
  pending legacy email outbox does not prove any sender is active. Exchange
  sending is not a prerequisite for the approved replacement channel.
- **Rollout remaining:** deploy/evaluate the six specialists, register tested
  operational actions, rehearse release/rollback and expand resource coverage.
  None of the eight original upgrade workstreams is signed off as fully complete;
  their outstanding acceptance work is listed immediately below.

The owner explicitly approved both the exact two-container storage grants and
Entra policy export/read access on 2 October 2026. Both storage grants were
applied and verified; Entra policy reads still return HTTP 403. Approval does
not itself provide directory permissions. No directory role or security-policy
change was made. The owner subsequently deferred this review and authorized
continuing the agentic programme. It remains unverified, not passed, and does
not authorize weakening existing authentication or access controls.

### Secretless Container Apps runtime preparation 4 October 2026

Six additive code/test files extend the accepted 719-source manifest to 725;
all 719 prior hashes remain unchanged. The 74 focused tests and independent
review passed. Fresh repository QA discovered 2,235 tests: 2,233 passed, with
the same two reviewed Windows fixture exclusions. The actual Windows junction
rejection ran. Compilation and repository policy passed: 293 tracked files,
706 scanned source files and 59 contiguous migrations. These checks do not
establish financial acceptance, live SQL behavior or OS/dependency security
certification.

The protected preflight is SHA-256
`74ec1f9e19244473dc13a3e8e766462c2ff5c22dc361e88bc004b4a51d7de9b9`,
version `2026-10-04T19:04:32.2219643Z`. It binds the previously accepted ACR
runtime and exact image, unchanged existing-service definitions, the private
test-host receipt, all 725 sources and a Manual, zero-retry job definition.
The planned canary contains no database, model or Key Vault secret. Its
identity is configured with lifecycle `None`, and the reviewed runner has a
fixed 120-second hold to permit native running-replica capture.

Azure acknowledged creation of
`ibkrv2stg-ownertrial-runtime-identity` with HTTP 201. The protected intent is
SHA-256 `9bb0ebd0ce764d54c115bf040d18e52e8332ba25d23b962e23e6771160c609d2`,
version `2026-10-04T19:09:04.5826655Z`; its conditional reservation is
SHA-256 `604eb7c26a2b6c469afb959c050c6b9823f4d0bac8a3a8732d3125bbc68a3601`,
version `2026-10-04T19:08:40.1970296Z`. The protected acknowledgement is
SHA-256 `9a243b2f79205589301c7ac3486d1a3fec6af98357de9cabdb4eb29974c512e7`,
version `2026-10-04T19:10:03.3770156Z`.

Fresh verification stopped before any grant because Azure returned the identity
ID using `/resourcegroups/`. Both the acknowledgement and fresh GET retain that
native spelling. Microsoft documents that API readbacks can change casing;
this is a representation mismatch, not evidence of a different resource.
[Azure naming guidance](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/resource-name-rules).
Separate read-only inspection found zero assignments in the complete queried
subscription, resource-group-inherited and registry-inherited inventories.
The planned registry grant and job were absent. This is not tenant-wide
permission-closure proof.

The frozen 725 sources and protected identity history remain unchanged. Two
additive files implement a separate continuation, bringing the manifest to 727.
The adapter accepts only the exact registered MI ID and its observed structural
spelling variant. It validates a copy while preserving all raw evidence,
rejects foreign scopes and duplicate identity entries, and cannot recreate the
identity. Each subsequent effect requires an exact stage, target, API, method,
body, prior receipt and protected reservation. Both local and protected remote
records prevent reuse of an earlier consumed downstream attempt. Only the
native exact `BlobNotFound` 404, represented as a string or the corresponding
SDK enum, establishes reservation absence; permission and other errors block.

The continuation controller is SHA-256
`9338bef5073c8645de672e6a4b6eecea99e543ca32f9d12f7b6a7e12d719e486`;
its 35 guarded offline tests are SHA-256
`e8f7645f47523c3d0c74d05e47fd5a3af9534be7c1dd5e5dda235912f1e5eb3f`.
All 109 focused runtime tests passed. Independent source review found no
remaining blocker. Fresh full QA discovered 2,270 tests: 2,268 passed with the
same two reviewed Windows fixture exclusions and the actual junction rejection
exercised. Compilation and repository policy passed: 293 tracked files,
708 scanned source files and 59 contiguous migrations. The full metadata codec
handles preflights above 64 KiB with a fixed 2 MiB bound; the runtime-log decoder
retains its separate, smaller bound. Those 35 tests prevent credential, network and
subprocess access and do not depend on live `.work` records.

Fresh protected-history, source, permission and existing-service checks passed.
The continuation preflight is SHA-256
`498f325e8ff0b4c01bdc940cdeb0ac1dc4613810f520648a8932240c527edff1`,
version `2026-10-04T19:39:38.8552840Z`, with protected readback verified.
It reuses the consumed, acknowledged identity rather than creating another one.

Azure acknowledged the one registry-only AcrPull assignment with HTTP 201.
The recorded native scope exactly matches registry `ibkrv2stgauhecwc4itaoo`;
there is no new storage, Key Vault, database, model or resource-group permission.
The protected conditional reservation is SHA-256
`882111f7c5a9ed35d999e0fac613f1d2a1277183da64c6ca2fa55aa369c3dfed`,
version `2026-10-04T19:45:43.4772562Z`. The protected intent is SHA-256
`d4fd50eb498aa5274535796cc1a34b2dfa82ea1cf60ed0aa8d8537226fe8dbd3`,
version `2026-10-04T19:46:08.0613699Z`; the acknowledgement is SHA-256
`0ec2684ede92d89d389fb1b94ec4a35dff9b80bd62b04b22b12553297ee31035`,
version `2026-10-04T19:47:12.5934465Z`. These protected readbacks passed.
The grant attempt is consumed. Fresh acceptance passed against the native
assignment, all three complete queried inventories and the built-in role
definition. Its protected receipt is SHA-256
`3cfacaeb900638181e294d606caf118f07bf09c72a88504beb41f0b7025fafee`,
version `2026-10-04T19:53:27.2196314Z`. The queried subscription and registry
inventories contain only the expected assignment; the resource-group-inherited
inventory is empty. Tenant-wide permission closure remains unverified.

The separately reserved installation attempt returned an unverified outcome,
not an acknowledgement. Its protected reservation is SHA-256
`3f9676832f57aab05f85a57451b2a108d9f29971c9941cf19afd82e140bda0b1`,
version `2026-10-04T19:59:49.5122455Z`; the intent is SHA-256
`943f9a6559415a285399ec46cccbd25944502cd3f0614ecb0746d8e5e4b00b3b`,
version `2026-10-04T20:00:13.4912443Z`; the consumed outcome is SHA-256
`5a76fc801474b1fb6917df4e214e3cec48d777e860fcaf59e35d0a43fc90226c`,
version `2026-10-04T20:01:17.6649630Z`. Automatic retry remains disabled.

Read-only diagnosis found the exact Azure `Microsoft.App/jobs/write` operation
started at `2026-10-04T20:01:03.3917952Z` and failed at
`2026-10-04T20:01:04.923118Z`, with correlation
`867625d0-98f7-437e-be41-028bccbaf941`, `BadRequest` and
`ContainerAppInvalidName`. The original job name
`ibkrv2stg-ownertrial-runtime-canary` has 35 characters. The service error
states a 2–32-character bound; the current
[Azure CLI job reference](https://learn.microsoft.com/en-us/cli/azure/containerapp/job?view=azure-cli-latest)
states less than 32. The proposed replacement
`ibkrv2stg-ownerruntime-20261004` has 31 characters and avoids that boundary
ambiguity. Both native GET absence checks passed the frozen exact
`ResourceNotFound` parser. This establishes an observed name-validation
rejection and current absence, not a reconstructed HTTP acknowledgement or
the full cause of the local transport exception.

The protected diagnosis is SHA-256
`8b0f9d190f6fbcb93087475495a6422d7524d8f24e2f750ddb1a889d251aa0fa`,
version `2026-10-04T20:06:48.3111684Z`. It links the intent and consumed outcome,
retains the complete two-event native inventory privately, and records all 727
unchanged sources. All old sources and failed attempt records remain frozen.
The new target requires a distinct controller, preflight and conditional
reservation; no old-target redispatch, identity recreation or additional grant
is authorized by this diagnosis.

Replacement job installation, one reserved start, native replica/log acceptance
and fresh-process verification remain pending.
No IBKR source writes, SQL or model calls occurred in this trial. Production
migration 059, owner production runtime and all six autonomous specialist
runtimes remain inactive.

### Valid-name Container Apps candidate v1 4 October 2026 — superseded, not accepted

Two separately reviewed additive files extend the 727-source manifest to 729;
every prior hash remains unchanged. The new fixed job name is
`ibkrv2stg-ownerruntime-20261004` (31 characters). Its Manual definition, image,
checker, payload, tags, CPU/memory limits, identity lifecycle `None` and
zero-retry settings are byte-identical to the earlier reviewed definition.
Only the registered ARM job target and name-dependent native metadata,
execution, replica and console-query adapters change. Raw native observations
are retained unchanged; copied validation metadata is adapted narrowly.

Operation `2a2cf3cc-fe8a-498b-89a5-7445075cdec4` uses a separate protected
reservation prefix `probes/owner-codex-aca-runtime-named-20261004/`. Its only
deployment effect classes are the exact new-job PUT and START. There is no
identity-create or permission-grant command or mutation route. The controller
pins and rereads the acknowledged identity, accepted registry grant and the
consumed invalid-name installation and diagnosis. Existing services, original
attempts and all earlier evidence remain unchanged.

Controller SHA-256:
`684f5e2aabeef6fad4f431167c462b0ae30292c4f3fe18d2c1506819ea193aec`.
Test SHA-256:
`db0e044f904be4d7861fa6cf0ca2c849ba95a6123eadd7ba8959e431763d9093`.
All 43 guarded offline tests passed, and the main agent independently ran all
152 focused runtime tests successfully. Independent source review passed.
These tests use embedded fixtures, block credential/network/subprocess access
and do not depend on live `.work` records.

Error response bodies within the distinct 1 MiB private-body limit can be
retained completely. Larger bounded responses record their full size/hash and
explicitly mark the body unretained, avoiding base64 expansion beyond the
2 MiB evidence limit. External exception text is sanitized. An unknown effect
acknowledgement remains consumed with no automatic retry even if diagnostic
extraction fails.

Running-replica capture may wait only for explicitly validated `Processing`,
complete empty replica inventory or exact `ResourceNotFound` readiness.
Wrong templates, identities, foreign/extra replicas, restarts and drift cannot
be erased by later clean observations. A structural capture failure consumes
an exclusive local stop intent and conditionally retained protected stop
marker, with pinned readback and a protected fixed-reason diagnostic. Subsequent
start/capture/collection and verification reject local or remote stop evidence.
Only the exact protected-store `BlobNotFound` 404 proves marker absence.
No later START is authorized by readiness polling or failed capture.

The first read-only preflight stopped at `2026-10-04T20:33:00Z` because its
unchanged 300-second full-regression deadline expired. It had not retained a
preflight, reserved an effect or dispatched an installation or START. This
failure is not counted as a passed gate. A separately observed full discovery
then completed at `2026-10-04T20:37:56Z`: 2,313 tests ran in 273.850 seconds,
2,311 passed and the same two reviewed OS-specific fixtures were excluded.
The new 43-test suite restored its clock, credential, HTTP and subprocess mocks;
no state leak was found. Existing complete four-MiB catalog evidence tests were
observed progressing slowly, not hanging. The exact cause of the first timing
overrun is not established.

The second read-only protected live preflight also stopped at
`2026-10-04T20:48:52Z` at the same 300-second discovery deadline, with no
concurrent test run and again before preflight retention or any effect
reservation. Neither failed preflight is accepted, and neither authorizes an
installation or START. No third repetition of that unchanged live preflight
has been launched. A separate local captured-output QA run passed discovery,
compilation and repository policy in 291.869 seconds total: 2,313 discovered,
2,311 passed, the same two reviewed OS-specific exclusions, 293 tracked files,
710 source files scanned and 59 contiguous migrations. That total includes
policy and compilation, so it is not a regression-only measurement or proof
of a capture deadlock. Neither local diagnostic is protected live-preflight
acceptance.

Fresh read-only checks verified exact native absence of both the valid-name
job and the previously rejected job, no local candidate evidence, no candidate
protected-record family, both deployment reservations absent and no capture
stop. All 729 candidate hashes were unchanged during those checks. The first
diagnostic's metadata construction failed before storage on a duplicate
keyword; no deployment or reservation was attempted. Its corrected read-only
diagnosis was retained and reread: SHA-256
`2eab0fb509b6a2cb38203e050bddc094320ede5ea73b9c3bec234ed341cfd43c`,
version `2026-10-04T20:57:57.0738821Z`. It preserves the old 729-source map,
the two timeout outcomes and both successful local diagnostic measurements.

The current unaccepted two-file candidate is explicitly superseded, not
silently rewritten as an accepted release. Only its controller/test files may
be revised; all 727 protected predecessor hashes and all consumed historical
effects remain unchanged. Replacement operation
`6ef9173d-1aa3-4ff7-810a-d2ce23e75d6a` uses
`.work/owner-task-aca-runtime-named-v2-20261004` and protected reservation prefix
`probes/owner-codex-aca-runtime-named-v2-20261004/`. The proposed fixed
600-second complete-regression limit keeps policy/compilation at 90 seconds,
the unchanged result validator, all test and permitted-skip gates, and the
original job body, identity, permissions and one-shot controls.

### Valid-name Container Apps candidate v2 source qualification 4 October 2026

Independent source review passed. The revised pair is frozen at controller
SHA-256 `652097c10b431c1b87525a8b5adcb5774fc78c731d28060869cedee1b7c46d1e`
and test SHA-256
`5f4f03c794bdc624521df1caa9da3b458302bb614c8bf4b9741a79b12cc5c181`.
All 59 guarded candidate tests passed; the main agent independently passed all
168 focused Container Apps runtime tests. The actual 729-file map differs from
the pinned supersession diagnosis only at those two unaccepted candidate files;
all protected 727 predecessor hashes are unchanged. The job body, image and
runtime payload remain byte-identical.

The new preflight records the exact fixed 600/90/90 QA policy separately from
the unchanged nine-field QA result. Timeouts, failed checks, partial discovery
and unapproved skips cannot become passing evidence. Old-candidate absence is
rechecked using a bounded complete single-page protected inventory and exact
missing-reservation checks before reservation and immediately before effects.
The protected v2 evidence namespace is disjoint from the old candidate family.
A fresh complete protected preflight subsequently passed. Full discovery ran
2,329 tests in 277.249 seconds: 2,327 passed, with the same two reviewed
OS-specific fixtures not exercised. Repository policy passed with 293 tracked
files, 710 scanned source files and 59 contiguous migrations; compilation
passed. The actual 729-source map remained unchanged. The protected preflight
was retained and reread at SHA-256
`1e4c6e1d98bfd5223e52b53f6a69ee69d88250d6824a6bd788259e97b58ef255`,
version `2026-10-04T21:22:55.0834538Z`. It binds the pinned supersession
diagnosis, original identity, accepted registry grant, exact job definition,
fixed QA policy and unchanged existing-resource guards. This is preflight
acceptance only, not installation, runtime, SQL, financial/visual or dependency
security certification. Each deployment effect still requires its own
conditional reservation, protected intent and fresh target/permission checks.
The valid-name job's single reserved installation was subsequently acknowledged
by Azure with HTTP 201. Its raw acknowledgement identifies the exact registered
31-character job and reports `InProgress`; this is not final definition or
runtime acceptance. Reservation SHA-256
`7fabbd3526428808d456ee5a3ccf166c4bbe9fa1f881dfe473b7ec0d8a76db43`,
version `2026-10-04T21:32:19.1533258Z`, ETag `"0x8DF225EF7EAD8CA"`,
precedes protected intent
`7d838c096120d677eab3b2f6ddbe73e2697b276479fc0995e41c800fc452f0d0`,
version `2026-10-04T21:32:44.8174965Z`. Protected acknowledgement
`3cd4573bd0a23cadc51d27513e3be13bc1856808a5935908cb3bf4ac6ffa3f4c`,
version `2026-10-04T21:34:04.0914916Z`, was retained and reread. The attempt
is consumed and will not be repeated. All 729 source hashes still match the
preflight. No additional identity or permission grant was made.

The subsequent full `install-verify` command stopped read-only at the frozen
server-metadata timestamp gate. Separate exact native reads found the job
`Succeeded` and a complete empty execution inventory. Azure returns both
`createdAt` and `lastModifiedAt` as `2026-10-04T21:33:45.8403256`, without a
timezone suffix. Further diagnostic comparison identified 161 valid outbound
address strings (the old descriptive-field limit is 64), and registry
`username`/`passwordSecretRef` fields added as empty strings. The full outbound
array SHA-256 is
`080a8f5966396c6439ae8ec19074a0fbf2b07da9a733450ad53f918b904b80fb`.
Missing empty environment/init-container fields and an empty ephemeral-storage
default are already accepted by the unchanged template validator.

A detached diagnostic copy removing only the two descriptive timestamps,
independently parsed full address list and two empty registry defaults passes
the remaining frozen job validator. This is diagnostic isolation of the
differences, not acceptance of modified native evidence. Original values,
validator, request body and all 729 source hashes are preserved. The first
diagnosis stopped before storage when the additional metadata mismatches were
found; a complete fresh protected diagnosis subsequently passed storage and
readback. Its SHA-256 is
`87653f0ce87d2fccd3625b7ac8e35a3bf8c395226029e958538a2937a4299162`,
version `2026-10-04T21:45:54.6465988Z`. It retains unchanged full native job
readbacks before and after, complete empty execution inventories, exact missing
START reservation and capture-stop checks, unchanged resource/permission guards
and all 729 unchanged source hashes. Main-agent independent local pin/source
validation also passed. No installation
retry or START was dispatched. Any correction requires separately reviewed
additive source files, pinned full native diagnosis, fresh QA and its own
reservation. It must not infer UTC, drop unknown/nonempty fields, weaken
execution/replica time validation or claim that parsed addresses establish
network authority.

The metadata correction is implemented and frozen as two additive files extending
the manifest to 731, not a rewrite of any protected source. Operation
`2153fcf3-10a5-4e21-ada1-2bd9c319e0d3` uses
`.work/owner-task-aca-runtime-metadata-20261004` and separate reservation prefix
`probes/owner-codex-aca-runtime-metadata-20261004/`. It must retain the full native
metadata and may validate only those five known paths in a detached shadow.
The controller has no installation, permission or deletion write route;
its only future effect is one separately reserved START after fresh QA and
native definition checks. Its controller SHA-256 is
`b8e3050470cabf1f9f0d65b8b2b77c5071c33e97fa9c8b2f7bb9d94f67862be4`;
its test SHA-256 is
`4d9796510b998cfe3e7a0612be4107808a349c16927b84f425220d9f2cef761b`.
All 43 new offline tests passed in 4.123 seconds; a separate implementer run
passed in 4.287 seconds. Main-agent focused runtime discovery passed all 211
tests in 8.956 seconds. Complete source review found no concrete blocker;
local decoding of the four actual pinned historical records and preservation
of all 729 predecessor hashes passed independently. Offline fixtures do not
qualify remote history, a cloud effect or runtime acceptance. Full repository
QA and live protected preflight subsequently passed. Whole discovery found
2,372 tests: 2,370 passed and the same two reviewed OS-specific fixtures were
not exercised. The actual Windows-junction fixture remained exercised.
Full regression took 282.656 seconds, repository policy 2.543 seconds and
compilation 2.670 seconds. Policy passed with 293 tracked files, 712 scanned
source files and 59 contiguous migrations; no prohibited paths or secret
patterns were found. Bounded 600/90/90-second QA policy and the unchanged
nine-key result schema were retained. This is not financial/visual, live SQL
or OS/dependency-security certification.

Protected preflight SHA-256
`f147b188342613415b85aba9d3c1368a2c0205717b88dff4d8f597c8f941e30f`,
version `2026-10-04T22:15:44.1381539Z`, records the current 731-source manifest,
four exact historical receipts and compact immutable plan/native-job/resource
bindings. Complete protected historical chain and reservation readbacks,
fresh ACR `ck84` show/log/show, old/new family absence, current job/environment,
registry-only permission and archive protection checks passed. Main-agent
separate-process local decoding, full-history relation and current-source
checks passed. All unsupported flags remain false and SQL/model calls zero.
Separate local-only review also passed the exact preflight pin and historical
relation. The one-shot START command completed; its protected reservation
SHA-256 is
`1bd5ea8290df024c24b98f247719667d984c9ed5f6610fdcb4d20d2289fb9d22`,
version `2026-10-04T22:28:47.6848424Z`, ETag `"0x8DF2266DBA3B928"`.
Protected intent SHA-256
`c90c2f2c3088d2984503661e62a119bae9dcf6cf085af30af667fd5742fe31ff`,
version `2026-10-04T22:29:11.6918925Z`, matches the exact registered body and
prior receipts in main-agent local verification. Azure acknowledged HTTP 200 for
`ibkrv2stg-ownerruntime-20261004-fc2ktuf`. Its protected acknowledgement SHA-256 is
`c4d0063ddb54d7abdc00b5ee6cc9e4800b911285f621c4eea37b40075439133c`,
version `2026-10-04T22:39:14.3191347Z`. Azure subsequently reported Succeeded,
with execution times 22:39:00 to 22:41:27 UTC. Capture validation stopped;
the protected stop and diagnostic remain binding. Diagnostic SHA-256
`0217bd85d68f2446641158b6975c71c9f129c6b506a4c3b8d88109de3e71c71d`,
version `2026-10-04T22:40:31.4236616Z`, records no runtime acceptance.

Protected native diagnosis SHA-256
`c0e27e3e3d9435f6ef14508f87a0353b481bd3d437c9972c821f3e9202f03d1b`,
version `2026-10-04T22:47:03.1655030Z`, retains identical terminal readbacks,
the complete execution inventory, installed job and two matching full console
responses. The single console event matches the expected ACR runtime descriptor.
The additional execution container field `imageType: ContainerImage` causes the
strict template rejection; all requested image, command, argument, CPU and memory
values match. A detached diagnostic copy without only that field passes the
frozen execution validator. The retained replica inventory is empty, so neither
this diagnosis nor the matching log proves the earlier Running replica mapping.
The generic earlier capture error did not retain its failing response and cannot
establish the exact historical capture failure point.

Four additive files now provide a native-response adapter, a new qualification
controller and their tests. All 731 predecessor sources remain unchanged. The
parser's 26 focused offline tests and independent review passed. The controller's
33 offline tests also passed; main-agent combined discovery passed all 59 tests
in 5.951 seconds. Controller SHA-256 is
`e3e798f06ceac0097b30b08a2775eb9b952d2356bd10862a7ed942ea2c6a8057`;
controller-test SHA-256 is
`a11d3806ddc8a8b752ce07878fa0de49e3e69080a687e95c70b75ed94847c801`.
The adapter and its tests are frozen at
`6a69a1e3c188b241eab006c4dc7d9acc655bfbfb63b26a988b0f14c9ed239fee`
and `19d1c4d475393c08ad70e843be7dd73eb716dec9808090045a1c08ddc0763530`.
Independent controller review found no concrete blocker. Tests cover an immutable
intent snapshot, consumed unknown dispatch, exact authority and inventory,
bounded native capture with preserved raw diagnosis, durable collection failure
stops and read-only fresh verification. Full QA and protected preflight passed:
2,431 tests were discovered, 2,429 passed and the same two documented Windows
fixture exclusions were not exercised. Regression took 284.520 seconds,
repository policy 1.904 seconds and compilation 2.593 seconds. Repository policy
passed with 293 tracked files, 716 scanned source files and 59 contiguous
migrations. Protected preflight SHA-256 is
`c162a38ba254cd626030110cae64be35042efcce0bbf5c46049c6374e8098b84`,
version `2026-10-04T23:18:45.1521640Z`. Main-agent local receipt, all 731
predecessor hashes and the complete 735-source manifest were independently
verified. On 5 October (London time), the owner asked to complete the remaining
eight deployment steps one at a time, beginning with live runtime qualification.
The new one-shot launch subsequently received HTTP 200 for
`ibkrv2stg-ownerruntime-20261004-lmrdvwe`, with initial Running start time
`2026-10-04T23:27:13+00:00`. Protected acknowledgement SHA-256 is
`496ae463c3fa827751d060b55bc33cb80746926d2624a391455660e3ad8fc752`,
version `2026-10-04T23:27:25.3728923Z`. Capture stopped on the next observation:
the exact native replica had `runningState: NotRunning`, empty details and
`containers: []`. The protected diagnostic retains the complete raw acknowledgement,
Running execution template and startup replica response; SHA-256
`bacbf4aa4453ba99aa69e0fa63d694d39409a66991f71a7ef19b7f0d04f0c492`,
version `2026-10-04T23:27:48.4278467Z`. No acceptance was recorded. The consumed
reservation and this failed capture are preserved, without retry or requalification.
The new operation
uses the same fixed installed job, its own reservation and a distinct execution,
with the prior completed execution and stop explicitly pinned. Capture now
precedes the slower protected archive round trips, and raw observations survive
validation errors. Known incomplete readiness is bounded; successful capture
requires a real native Running replica, ready and started, with zero restarts.
This stage rereads the four directly pinned protected records, the original start
reservation and current resource/identity/permission guards; it does not claim
that every unchanged historical ancestor has been revalidated recursively.

The separate startup-readiness correction adds exactly four files, preserving
all 735 predecessor source hashes and extending the manifest to 739. Empty native
container metadata is classified as pending only after validating the complete
replica scope, timestamp, known state and optional metadata. It cannot supply
Running evidence or log ownership. Populated rows retain the frozen image,
command, readiness, identity and restart checks. The new controller pins the
three protected records from the stopped attempt, its original intent/reservation,
both prior terminal executions and a new distinct execution under operation
`f07accc6-6c65-43c9-a0c4-7444c65b9ff2`. Its namespace is
`probes/owner-codex-aca-readiness-20261005/`; local evidence is in
`.work/owner-task-aca-readiness-20261005`. All 13 focused offline tests passed in
1.169 seconds, including bounded empty-to-ready polling, exact two-prior/one-new
inventory and independent acceptance recomputation. Compilation and source
preservation checks passed. Controller SHA-256 is
`81d877dca2d4585f5331f5473f70317b34857754ac9594cf1c607be9fd6a865a`;
adapter SHA-256 is
`c22e3938fb19bbbb5e51220eae18d6608c7cfbfbaa9bb462407ead9fe0afd924`.
Full QA and protected live preflight passed: 2,444 tests were discovered,
2,442 passed and the same two documented Windows fixture exclusions were not
exercised. Regression took 279.514 seconds, repository policy 1.912 seconds and
compilation 2.305 seconds. Policy passed with 293 tracked files, 720 scanned source
files and 59 contiguous migrations. Protected preflight SHA-256 is
`3a1ec764fd53b1e72b05939ce0175ff6673f66086e78485ca9cc39d9b7662ef1`,
version `2026-10-04T23:46:36.2272145Z`. Main-agent local receipt and all 735
predecessor/739 current source hashes were independently verified. Both prior
executions were observed terminal Succeeded, with the stopped startup execution's
original start time and template unchanged. Current job, environment, identity,
registry-only role and existing-service guards passed. Its fresh execution
`ibkrv2stg-ownerruntime-20261004-ahkz9mj` was acknowledged with HTTP 200.
Protected acknowledgement SHA-256 is
`2200da188abcaf302c3ff6f367fc5adcf64b3733856e858693a291e1c6eb7851`,
version `2026-10-04T23:51:01.7978904Z`. The readiness adapter correctly waited
through empty containers and Waiting/PullingImage metadata, then observed a
Running replica with ready/started true and zero restarts. Validation stopped on
the previously unsupported native `containerId`; no after-Running execution
readback or acceptance was fabricated. The 27 complete observations are retained
in protected diagnostic SHA-256
`40fb1dfaf54be23de9e6b3f585797c3b376b007d830889fd5373ac06caefe56a`,
version `2026-10-04T23:51:25.1378118Z`. This attempt remains consumed/unqualified.

The reviewed compatibility correction adds four files without changing the
739 protected predecessor source hashes. The optional identifier is copied
unchanged and validated as a known bounded containerd hexadecimal string; only
that field is removed in a detached predecessor-validation shadow. It is not
independently observed image-digest evidence. Scope, endpoints, UTC, readiness,
restart and complete console-event checks remain mandatory, including log
ownership validation. The new controller pins the stopped readiness preflight,
acknowledgement and diagnosis, rereads the original reservation and all three
prior terminal executions, and requires a fresh distinct execution. Operation
`9c2c8c7f-ad84-45d1-8e36-a6519b3a4e82` uses
`probes/owner-codex-aca-container-identity-20261005/` and local evidence in
`.work/owner-task-aca-container-identity-20261005`. All 16 focused tests passed
in 1.795 seconds. Full QA and protected live preflight passed. The
initial sandbox invocation could not access the Azure CLI profile and performed
no cloud action; the authorised preflight completed with the required access.
An independent local comparison confirmed every one of the 739 predecessor
hashes is unchanged and the current manifest contains exactly 743 sources.
Adapter SHA-256 is
`e8590fd8241e0e503974991da0dd832a6eb8d9c983917a58a95daf991d469972`;
controller SHA-256 is
`25b8b7c4c55717640f8e73882ff01c63892e095c5b83883f30394977db7d6928`.
The correction deliberately does not rewrite either frozen predecessor or
retroactively accept a consumed execution.
Full discovery found 2,460 tests: 2,458 passed and the same two documented
Windows-specific fixture exclusions remained not exercised. Regression took
288.389 seconds, policy 1.916 seconds and compilation 2.320 seconds. Policy
passed for 293 tracked files, 724 scanned source files and 59 contiguous
migrations. This is not live SQL, financial/visual or OS/dependency security
certification. Protected preflight SHA-256 is
`fb5d8c5f8cb0f83406b6146901937293070c6ff8bdb504f4a821ec9ca2eef2ab`,
version `2026-10-05T00:08:35.6804795Z`. Main-agent independent local digest,
all 739 predecessor/743 current source hashes and QA exclusions were verified.
All three prior executions were reread terminal Succeeded. Current job,
environment, registry-only identity/permissions and existing-service guards
passed. The fresh launch acknowledged
`ibkrv2stg-ownerruntime-20261004-694nva8`. Protected acknowledgement SHA-256 is
`41bd919df300cf70d95ab91edcdcbfbf9baaf858a664ade60c134474f5013cac`,
version `2026-10-05T00:12:57.9753528Z`. Capture observed the correct native
replica Running, ready and started, with zero restarts, and matched the actual
surrounding execution observations. Original `containerId` and endpoint fields
are retained unchanged. Protected ready-container binding SHA-256 is
`fe2129397b64925cf79d4521d4f42a7aada6e4bd6edee6e9599e646059f959fa`,
version `2026-10-05T00:13:23.4341819Z`. Terminal/full-log collection and
acceptance subsequently passed. The execution was Succeeded from
`2026-10-05T00:12:21+00:00` through `2026-10-05T00:14:45+00:00`.
The complete observed console inventory contained exactly one registered runtime
event belonging to the captured native replica. Two bounded complete inventory
observations matched. Protected acceptance SHA-256 is
`fec9ae19101fd9645bd2672b028a53862ef6510a988239ce344d3c0fa8fcbe30`,
version `2026-10-05T00:16:16.3714121Z`. Main-agent local acceptance digest,
current 743-source manifest, runtime record and unsupported-claim flags were
independently checked. Fresh separate-process live verification passed at
`2026-10-05T00:19:05Z`, returning
`independent_native_runtime_readback_verified` against the same protected
acceptance version/hash. Verification recomputed the acceptance from fresh
Azure execution, complete console, job, roles, all three prior execution and
existing-service readbacks; no new acceptance or launch was written. The full
console inventory and observed runtime record matched the protected result.
Step 1 is complete for this explicitly secretless runtime scope. At that
checkpoint, step 2 was unstarted; no database/catalog, website-owner or six-specialist runtime was
activated by this qualification.
The observed runtime is non-root CPython 3.12.14/Linux/x86_64, effective UID/GID
10001, psycopg/binary 3.3.6 and libpq 180006, with the registered CA bundle hash.
Credential reads, database connections, SQL and model calls were zero. Every
prior consumed attempt remains unqualified; none was retroactively accepted.
No independently observed container-engine image digest, TLS connection,
restricted login, model credential containment, absence of all platform-injected
credentials, tenant-wide permission closure, late-delivery exclusion or absence
of unlogged/replacement replicas is claimed. These remain separate acceptance
questions; passing native runtime metadata is not complete agent deployment.

The earlier installation-acceptance record was not fabricated or rewritten.
Secretless native runtime metadata acceptance has now been recorded; no SQL/model
call was made in this qualification. Production migration
059, owner production runtime and the six autonomous specialist runtimes remain
inactive.

A separate live Azure read confirmed that the daily report execution
`ibkrv2stg-agent-report-29851800` succeeded on 4 October from 10:00:00 to
10:00:40 UTC (18:00:00 to 18:00:40 Hong Kong time). This verifies the scheduled
job's status and timing; recipient delivery of that day's email has not been
confirmed.

Read-only next-stage design review identified a necessary catalog transport
change; no new catalog code or cloud/SQL effect was produced. The existing
small-runtime full-log transport is capped at 512 rows and 2 MiB, insufficient
for a maximum 4 MiB catalog record encoded as 1,025 wire frames. The next
additive controller must use complete, unfiltered execution-family/all-container
console headers around bounded indexed payload reads, with exact header
multisets and no removal of stderr, unknown containers or ordinary log rows.
Protected parts must be derived from the actual manifest (at most 128 parts,
each at most 2 MiB), not the synthetic canary's fixed nine-part layout. Fresh
remote reconstruction must reproduce all 13 collector collections exactly.
The trusted fixed-SELECT administrator worker must remain distinct from
restricted-login and model credential-isolation acceptance. Its current
autocommit catalog observations are neither an atomic snapshot nor proof of
all-database object coverage or provider privilege safety. Actual TLS, PUBLIC
privileges, inheritance/escalation and fresh non-owner behavior require later
separate verification; no setup is authorized by a catalog observation alone.

### Step 2 — isolated catalog preparation, 5 October 2026

Owner requested step 2 explicitly. The first checkpoint prepares the registered
private test host for a later SELECT-only provider observation; it does not
install SQL objects or certify a restricted login. The host is
`ibkrv2stg-ownertrial-20261004`, not the shared production database server.
Fresh server metadata confirmed Ready and public network access Disabled;
the one existing test-secret version remains pinned.

All 743 accepted source hashes were preserved. Five additive source/test files
produce the exact 748-source manifest. The two focused suites passed 23 tests.
Full discovery completed 2,483 tests in 313.819 seconds: 2,481 passed and the same
two Windows-only skips; repository policy and compilation passed. These are
source gates, not live database, financial or visual acceptance.

Protected preparation preflight SHA-256 is
`c6ec528d6833bfdd7d1fd8caef99f0e7c66885f639f3706a604c72862b40adff`,
version `2026-10-05T00:47:23.5395062Z`. Its fixed controller is
`scripts/prepare_owner_task_isolated_catalog.py`; records are under
`.work/owner-task-isolated-catalog-20261005`. Every effect has a separate
create-only protected reservation and intent, a single PUT and fresh readback.
An uncertain response consumes the attempt and does not authorize a retry.
The controller has no START, SQL, model, deletion or Key Vault secret-value
read route. It changes only its one new managed identity, two exact assignments
and one new manual catalog job; existing service definitions are guarded.

The only planned assignments are AcrPull on the existing registry and Key Vault
Secrets User on `ownertrial-20261004-admin-url` alone. The latter scope covers
that secret's versions; the job binds only the reviewed version
`88cb24e00839444cb528ffe84427bb20`. No production database or model secret is
bound. The job uses the step-1 verified image, the frozen SELECT-only worker,
zero retries, one replica, 0.5 CPU/1 GiB, and identity lifecycle None. Lifecycle
configuration is not behavioral proof that runtime identity access is absent.

Azure acknowledged creation of `ibkrv2stg-ownertrial-catalog-identity`.
Protected identity acknowledgement SHA-256 is
`be2a5d0f6aef3a4597d1efe1cf47a5684b75fc9068abe7e7c14f1e5620d1e764`,
version `2026-10-05T00:50:59.3338917Z`. Fresh identity verification subsequently
passed with no assignments in the queried subscription inventory. Protected
identity acceptance SHA-256 is
`3bea29bd236b0ff9d7b68dc9c9db9af29ef6df00478086c88283ab5c9c55fe28`,
version `2026-10-05T00:54:21.1465417Z`.

The separately reserved registry grant passed fresh scoped-assignment
verification. Protected acceptance SHA-256 is
`e5f758daeeffcaa4a47ea44d7784fae3245f9b6674f70878fa38f221eb7d6488`,
version `2026-10-05T01:00:32.7510098Z`. The separately reserved test-secret
grant passed fresh verification; its protected acceptance SHA-256 is
`35effc6758c8a6ab801389a95bb6eda65bc6b823f1ce25e4c76512521fbee9e7`,
version `2026-10-05T01:07:05.4558712Z`. These are the only two assignments
in the observed principal-filtered subscription inventory; this does not prove
tenant-wide/group-inherited permission closure.

Azure acknowledged the new manual `ibkrv2stg-ownertrial-catalog` job. Its PUT
acknowledgement was InProgress and is retained unchanged; a later fresh GET was
Succeeded. The provider serializes systemData times with `Z` in the PUT response
and without a timezone suffix in the GET response. GET dates are validated as
descriptive native metadata, not asserted to be UTC. All native originals are
preserved; only validated alias/default fields are removed from detached
comparison shadows. Fresh final definition and exact two-grant verification
passed. Existing service fingerprints and all 748 source hashes matched.
Protected installation acceptance SHA-256 is
`ac35116ef5db927d98eb99f5948a22595011b468592ac4120a1adb236dd83cd0`,
version `2026-10-05T01:14:00.2375585Z`. The controller dispatched no START.
Separate-process final verification subsequently passed:
`fresh_idle_catalog_preparation_verified`. It reloaded protected prerequisite,
identity, assignment, intent/acknowledgement and installation evidence and
rechecked the accepted runtime, private host, role definitions, existing service
fingerprints, current job and exact source manifest. It neither installed nor
started a job and did not rewrite the accepted installation record.

An independently observed complete Azure execution inventory contained
`value: []` with no continuation. Protected absence-observation SHA-256 is
`78b33377691f53d57390277893cacb38d1a03c56b52c483c703d0da55055831b`,
version `2026-10-05T01:18:52.3224602Z`. This proves the returned inventory at
observation, not absence of future/unlogged effects or platform races. Controller
START calls, SQL/model calls and source-row writes are zero. No job execution,
TLS connection, SQL login or model credential isolation is accepted by
installation. Step 2 remains in progress after this verified preparation
checkpoint; the overall Agentic AI deployment is not complete.

The additive log transport is tested to query unfiltered execution-family,
all-container console headers around bounded indexed payload reads. Missing,
duplicate, changed, foreign-container and ordinary nonprotocol rows fail closed.
A full 4 MiB/1,025-frame synthetic record reconstructs through dynamically sized
protected-wire values, each below 2 MiB. The collector has not run on this host,
and no live 13-collection provider inventory is certified yet. Header completeness
still does not exclude late delivery, zero-log replicas or platform replacement.

Remaining within step 2: a separately reserved read-only catalog execution and
fresh protected reconstruction; review of actual provider/PUBLIC and inherited
privileges; reviewed isolated schema/role setup with synthetic data only; genuine
fresh non-owner login, actual permission-denial and TLS checks; and behavioral
credential-isolation tests before any model-facing integration is accepted.
No IBKR data was copied or changed, no SQL setup occurred, and production 059,
owner runtime and all six autonomous specialist runtimes remain inactive.

### Step 2 catalog execution and Azure permission review 5 October 2026

The owner requested completion of step 2 before step 3. All 748 existing
code/test/SQL hashes were preserved. Exactly two additive files provide the
single-dispatch catalog controller and nine focused tests:
`scripts/execute_owner_task_isolated_catalog.py` and
`tests/test_owner_task_isolated_catalog_execution.py`. Full QA discovered
2,492 tests in 295.853 seconds: 2,490 passed and the same two Windows-only skips.
Compilation passed; policy checked 293 tracked files, 731 source files and
59 contiguous migrations without prohibited paths or secret patterns.

Protected execution preflight SHA-256 is
`147669c12da80229741737b2b07f261ca66e311e7be10c5dfdff40ff80ff1699`,
version `2026-10-05T01:42:15.3432585Z`. An initial pre-effect read returned an
unaccepted transport outcome. No START intent or reservation existed and the
complete execution inventory remained empty. A subsequent protected read-only
diagnostic confirmed those absences and matching live guards; its SHA-256 is
`8c1c23a0a602d6961fa7d3c501d48977656106dadfe18c7ad3cd8aacdaa3203d`,
version `2026-10-05T01:47:17.0286672Z`. This was not a failed job or permission
to retry a consumed START.

The still-unused reservation was then consumed by one explicit START of
`ibkrv2stg-ownertrial-catalog-77yxz6j`. Native Running execution and ready replica
observations were captured before completion, with zero container restarts.
The protected acknowledgement SHA-256 is
`09c210324d038f3eaff42bada927b21e6c3b7d23e4ef95a99944ea5267f96d4a`,
version `2026-10-05T01:51:22.1297518Z`. The execution succeeded from
01:50:47 to 01:56:13 UTC. No automatic redispatch or job retry occurred.

Unfiltered execution-family console inventories bracketed bounded indexed
payload reads. All 602 protocol frames reconstruct through five protected wire
parts. They reproduce catalog SHA-256
`873b73ba8c3eb686850aa82d1081c14dcd0a69a0b220122041b32d90aa6eeea6`.
The protected observation acceptance SHA-256 is
`47b59a0872fcace28e52cb35610458e16650a05f97c7202a4ec06e67c61ee260`,
version `2026-10-05T02:01:57.9066174Z`; status is
`protected_catalog_observation_review_required`, not restricted-login acceptance.
Local evidence is under `.work/owner-task-isolated-catalog-execution-20261005`.
Separate-process verification subsequently returned
`fresh_catalog_observation_verified`. It reloaded the protected prerequisite,
preflight, intent, native acknowledgement, header inventories, all five wire
parts and acceptance record; reconstructed the same catalog; and rechecked live
execution, logs, registered job/permissions, private host, source hashes and
existing-service guards. It did not dispatch another execution or rewrite the
accepted record. This verifies the catalog observation, not SQL login acceptance.
Log completeness is limited to observed console records; it does not rule out
late delivery, zero-log replicas or platform replacement.

The catalog contains five databases, 23 database ACL rows, 19 roles, 19
memberships, four schemas, 11 schema ACL rows, three extensions, 3,319 routines
and 6,602 routine ACL rows. Default ACL and relation collections are empty.
There are no user relations in the management `postgres` database and none of
the registered future trial roles/database exist. This does not establish that
every database is empty: schema/relation/routine collection was scoped to
`postgres`. No source rows were imported, read or written. The catalog connection
used verify-full TLS; negative TLS tests and a genuinely authenticated restricted
login remain unperformed. Administrator readback is not a substitute.

The permission review found the immediate acceptance obstacle:

- `postgres`, `template1`, `azure_maintenance` and `azure_sys` allow connections
  and have PUBLIC CONNECT. The first two are owned by `azure_pg_admin`; the
  latter two by Microsoft's `azuresu` superuser.
- `postgres`, `azure_maintenance` and `azure_sys` also grant PUBLIC TEMPORARY.
  The existing permission specification rejects all CONNECT outside the target
  database and any TEMPORARY privilege; no exception is currently allowed.
- The customer administrator is not a superuser and has no observed membership
  path to `azuresu`. A per-login REVOKE cannot subtract privileges inherited
  from PUBLIC. The new private host therefore does not itself solve this gate.
- The observed public schema is owned by `azure_pg_admin`, not
  `pg_database_owner`. The earlier source-only installer assumption is not valid
  for this managed service and must be corrected in a reviewed new installer.

Microsoft documents the restricted superuser and public-schema ownership in
[Azure PostgreSQL access management](https://learn.microsoft.com/en-us/azure/postgresql/security/security-access-control).
It also documents that customer-managed `pg_hba.conf` is unavailable and network
controls operate at server rather than database level in
[Azure PostgreSQL networking](https://learn.microsoft.com/en-us/azure/postgresql/network/concepts-networking-public).
Its [server documentation](https://learn.microsoft.com/en-us/azure/postgresql/configure-maintain/concepts-servers)
describes `azure_maintenance` as inaccessible; that statement is not accepted as
proof of a fresh restricted login's actual denial on this host.

No provider database ACLs were changed, no service-level privilege exception was
silently added, and no production boundary was weakened. The recommended next
decision is an explicit Azure-specific acceptance specification: keep the
dedicated control database separate from IBKR/portfolio stores, prohibit SQL
credentials in model processes, enforce the fixed target in the trusted broker,
and retain strict denial tests for application data, role escalation and DDL.
Any Microsoft-owned defaults would be separately inventoried and behaviorally
tested, not silently removed from evidence. Sensitive unexpected access must
still fail acceptance. The owner subsequently supplied that direction; the
policy implementation is recorded in the next checkpoint.

Remaining step-2 acceptance: that reviewed provider boundary, isolated synthetic
schema/role installation, fresh non-owner login and effective privilege tests,
positive/negative TLS tests, behavioral credential/process isolation, and
protected independently verified results. Step 2 remains incomplete; step 3
has not started. Production owner/six autonomous runtimes remain inactive.

### Step 2 Private SQL trial preflight 5 October 2026

The owner directed completion of step 2 before starting step 3. The new adapters
target only the registered private isolated host. The shared-host installer,
permission specification, restricted-login harness and all 752 previous source
hashes remain unchanged. Six source files and two test files produce a
760-source manifest. No frozen predecessor globals, consumed catalog execution
or operation scope were retargeted.

The trusted installer rechecks all 13 provider catalog collections against the
approved observation before effects. It plans customer-database PUBLIC revokes
only for `postgres` and `template1`, leaves both Microsoft-owned databases
unchanged, creates three initially disabled non-elevated roles and a new
template0 database, and installs the unchanged migration 059 with synthetic
tenant/owner fixtures only. The public-schema revoke runs as the actual Azure
administrator before switching to the new schema owner. Named effective column,
schema, function, role-membership, database and forced-RLS inventories must pass
before the login is enabled. No password is returned or retained as evidence.

The separate fresh-login harness retains the original service, tenant/owner,
concurrent idempotency, revision, cancellation, privilege-denial and real
fixture-revocation checks. Its receipt verifier is explicitly synthetic. It
does not claim signed-owner authentication or real archive verification. The
trusted worker additionally plans a positive verify-full TLS check, a wrong
hostname probe against the same server IP and fresh provider-database probes.
Network, wrong-password or unclassified failures cannot prove authorization
denial; successful provider connections remain unaccepted pending object review.
The installer process contains the test credential, so this trial cannot itself
establish model/process credential isolation.

The fixed launch binds 13 Python sources and the unchanged SQL migration, checks
every source hash and the independently accepted Linux runtime before reading
the one version-pinned test administrator secret, and uses the verified image.
The planned dedicated identity receives only registry AcrPull and secret-leaf
Key Vault Secrets User. The job is manual, has one replica and zero retries,
and configures the identity lifecycle as None; this setting is not behavioral
proof that runtime credential access is unavailable. The controller reserves
each physical effect and protects intent before a single dispatch. Uncertain
outcomes are inspected, not repeated. Production jobs and website definitions
are guarded independently.

Thirty focused offline tests passed. Full QA discovered 2,538 tests: 2,536
passed and the same two Windows fixture skips remained. Repository policy
passed with 293 tracked files, 741 scanned sources and 59 contiguous migrations;
compilation passed. The regression deadline remains 300 seconds. The protected
preflight SHA-256 is
`bb17fc84ca6354c3d8ecf46710ffee37f431768351b43520889e278d22629466`,
version `2026-10-05T06:45:30.4914054Z`, with status
`prepared_private_sql_trial`. It binds the accepted policy, exact launch,
760-source manifest, fresh private-host/catalog and existing-service guards,
and QA. No SQL installation was dispatched at this checkpoint.

Before dispatch, an additive readback correction was prepared for Azure's known
empty `resources.ephemeralStorage` descriptor. It accepts only an absent or
exact empty string, uses a detached validation copy and retains the native
metadata hash. Nonempty/null/boolean resource changes, altered CPU, extra
credentials, enabled runtime identity or retries remain rejected. Seven focused
tests passed. Its first two review attempts exceeded the frozen 300-second
local full-suite deadline before review retention or dispatch. Diagnostic output
showed passing tests still running in the large synthetic log reconstruction
fixtures. Only the two unaccepted candidate files were revised: the new review
explicitly uses 600/90/90-second regression/policy/compilation limits, retains
complete discovery and the unchanged predecessor skip validator, and does not
change the Azure job deadline or zero-retry setting. Its separate protected
review passed complete QA: 2,545 tests discovered, 2,543 passed and the same two
reviewed Windows fixture skips, repository policy (743 scanned sources) and
compilation. The review SHA-256 is
`0c4286fba5ab1649034af1dbf82da913d110c52e87324c3370b41952bcd62482`,
version `2026-10-05T07:15:31.2943950Z`; protected exact-byte readback passed.
The 760-source preflight, operation, physical target and reservations are not
rewritten or reset. Test-only identity/grant/job provisioning is now underway;
this review is not SQL or process-isolation acceptance.

### Step 2 job-name rejection and separate continuation, 5 October 2026

The dedicated `ibkrv2stg-ownertrial-sql-identity` and both exact existing-registry
AcrPull and test-secret-leaf Key Vault Secrets User grants passed protected
verification. Acceptance SHA-256 values are
`2885cb685722db0327e2d42bc9a9d9d967d44156cc97d93198ecdbbb40aa9e9a`
(identity),
`53a19524a6015e734882ae1ed6293b44a1e2b53f32597d8b6e3bee95cd258611`
(registry), and
`6867608db7a15df5798e7487f379dee846e6e6ef3f4b1c0a5ce8f77a02440499`
(secret). Existing service definitions and source data remained unchanged.

The installation of `ibkrv2stg-ownertrial-sql-20261005` had no verified
acknowledgement. Read-only inspection found the job absent. Azure's validate-only
endpoint then rejected the exact frozen definition with `ContainerAppInvalidName`:
the name contains 33 characters, exceeding the 32-character maximum. The
protected diagnostic SHA-256 is
`19ff1341852388478a95993d0cb4d1eec13fdde18f1bede8fc593417f6059d18`,
version `2026-10-05T07:30:22.3155094Z`. No SQL ran. The consumed install intent,
reservation, frozen sources and original physical target remain preserved.

A separate named continuation was prepared with operation
`47fffc91-5697-45e0-b736-84d8d123158b`, job
`ibkrv2stg-ownsql-20261005` (25 characters), and a distinct protected evidence
namespace. Four additive worker/launch/controller/test files preserve all 762
predecessor source hashes. The SQL worker behavior differs only in its declared
new operation, job name/output prefix and explanatory module description.
The existing test identity and two grants are reused, not replaced or expanded.
Only new-job installation and START are allowed effects. Provider validation
must pass before its preflight; name length/shape is checked before source or
runtime use. Nine focused offline tests passed. Complete QA discovered 2,554
tests, with 2,552 passing and the same two reviewed Windows fixture skips.
Policy (747 scanned sources) and compilation passed; all 762 predecessor source
hashes remain unchanged. Azure's validate-only request returned HTTP 200 before
preflight. The protected preflight SHA-256 is
`bcb78f062cf6aeaa868b07e743c843015732f79629236129b8c3fbadef442b5c`,
version `2026-10-05T07:45:15.6993889Z`, with exact-byte readback verified.
Corrected-job installation was acknowledged. Its protected acknowledgement
SHA-256 is `187535ce17745cf6a7e35a53e9ef7562e68d68fc4a69879610ec9f8b92f1af86`,
version `2026-10-05T07:50:09.0261162Z`. Verification stopped on two extra native
template fields: `initContainers: null` and `volumes: null`. No other template
differences remained after the previously reviewed empty-resource/image/secret
reference descriptor normalization. The protected diagnostic SHA-256 is
`5349b4cbe5cb25698640b03bbea57fa26809fc8f1f3f1dc1617482519e2cabfd`,
version `2026-10-05T07:54:00.2858951Z`.

Two additive readback/test files preserve all 766 predecessor sources. They
remove only absent/exact-null fields in a detached copy before the unchanged
named validator; empty or nonempty lists, objects, extra mounts, credentials,
CPU changes and unknown fields remain rejected. Six focused tests passed.
Full review passed: 2,560 tests discovered, 2,558 passed and the same two Windows
fixture skips. Policy (749 scanned sources, 293 tracked files, 59 migrations)
and compilation passed. The protected null-descriptor review SHA-256 is
`e01b2fdc174bdefc5d41266699b7f0aa60f548eaa6a4821ec71e629b4c485aff`,
version `2026-10-05T08:06:30.7854336Z`. All 766 predecessor sources are unchanged;
the manifest contains 768 sources. The installed job passed protected acceptance,
SHA-256 `dd5351f5d8799692bca15dd2b28961e5b0658878c6f3560a00076e8a7a8b3a2a`,
version `2026-10-05T08:09:19.7532679Z`. Its operation, image, secret, grants and
original START reservation remain unchanged. The later one-shot execution and
failure diagnosis are recorded below; no successful live SQL or
model/process-isolation acceptance is claimed.

Step 2 remains incomplete. Live restricted-login, provider access, TLS and
credential/process-isolation acceptance are still required, followed by
protected independent verification. Step 3 has not started and IBKR source data
has not been accessed or changed.

### Step 2 failed SQL launcher and image-root candidate, 5 October 2026

One START was acknowledged and independently protected, SHA-256
`76bce976b7e03204194656b39c6b3bd6bbc2f38a8c7943897a65e66e0d18866d`,
version `2026-10-05T08:13:55.5217614Z`. Execution
`ibkrv2stg-ownsql-20261005-wrqzuc0` started at 08:13:45 UTC and failed.
Its one observed replica had zero restarts and reported ProcessExited, exit
code 1. No ready-running replica was captured, so that acceptance is not
claimed. The protected native observation SHA-256 is
`0cd36082fb6d3c6a387c6716160eb3a339aaa9cded9e138b465fb6e33a080e2f`,
version `2026-10-05T08:14:57.0837125Z`.

The bounded query retained the entire unfiltered execution console-log family,
not only selected successful lines. Its initial result contained one fixed
`OWNER_TASK_ISOLATED_SQL_LAUNCH_FAILED_V1` line, with no canonical SQL worker
result. Protected log SHA-256 is
`789ad13ba523c3e0c40d0faf14eba70268ccd02b4a1d8a23ec57b71ca856645f`,
version `2026-10-05T08:16:22.9223468Z`. This is a failure observation, not proof
of fresh LOGIN, TLS denial, provider authorization or successful SQL setup.
Database state has not yet been independently reinspected. No automatic retry,
cleanup, START reset or production change occurred.
Fresh second native execution/replica/inventory reads still observed one Failed
execution. The exact same unfiltered query window returned the identical one-line
family. The protected second observation SHA-256 is
`55539d27ae59cd7a272a0253b54af44e434ca66619d27d164ef8e42f808ae8aa`,
version `2026-10-05T08:23:01.5031822Z`. This establishes observed stability, not
a guarantee that the external logging service can never deliver a later line.

Source inspection identified a concrete launcher/image contract mismatch: the
Dockerfile sets `IBKR_PROJECT_ROOT=/app`, while the frozen SQL launcher's first
guard rejects the presence of that key before module imports, secret pop or SQL.
All bundled module imports also passed a separate offline replay. The generic
live failure line does not reveal a stage, and the current Dockerfile alone is
not an independent observation of the pinned live image's environment. Later
read-only registry inspection fetched the exact digest-pinned manifest and image
configuration, verified both content SHA-256 digests and config byte size, and
confirmed its inherited `IBKR_PROJECT_ROOT=/app` default. Only that fixed root,
environment key names and non-secret artifact metadata were retained. Registry
tokens, other environment values and the signed storage URL were not retained;
the registry bearer token was not forwarded to the Azure Storage redirect.
The protected image-configuration observation SHA-256 is
`4798d988aa120d1d6796da3e46f3c64f90e25da3a68b85490d8e46f3192f0f48`,
version `2026-10-05T08:28:24.6439720Z`. This verifies the pinned artifact's default,
not behavioral model/process credential isolation.

Two additive files, `scripts/owner_task_isolated_sql_image_root.py` and its test,
produce an inert candidate launcher. It permits only the exact `/app` image
default, rejects missing/other roots and case aliases, and retains strict
libpq/TLS-override rejection before any credential read. No frozen launcher,
worker, job, protected intent or consumed reservation is modified. Any live use
requires a separately reviewed physical continuation, native image-environment
qualification and fresh pre-effect private-database state inspection.

Seven focused tests passed. They replay the frozen first-guard failure, exercise
the candidate with only synthetic modules/credentials, reject unsafe roots and
TLS/libpq overrides, and verify that runtime mismatch blocks secret access.
Complete QA passed: 2,567 tests discovered, 2,565 passed and the same two reviewed
Windows-only skips. Compilation and repository policy passed (751 scanned
sources, 293 tracked files, 59 migrations). The 768 preceding code/SQL hashes
are unchanged; the additive manifest contains 770 sources. Protected candidate
source-review SHA-256 is
`7a528fe2637d283375adac18d24f0ab315f7c7d773d321d465beea0a1535f094`,
version `2026-10-05T08:27:50.2763408Z`. Its historical live-environment flag remains
false; the subsequent image artifact observation is separately bound above.
Independent fresh-process protected-evidence/source/current-service readback
passed. It verified the unchanged 770-source manifest, exact candidate source,
protected image configuration and failed execution records, current native job
definition, private-host configuration and existing service definitions. Its
protected SHA-256 is
`13cdbd7624a55a8a22a68c3b463baf8a9b4c92d9d8e35b32599f4935d7c5de30`,
version `2026-10-05T08:31:48.9942405Z`. SQL state, fresh restricted LOGIN,
provider behavioral access, positive/negative live TLS and model/process
credential isolation remain unaccepted. The reviewed correction is ready for
an explicitly bound new physical continuation, not a restart of the failed job.
Neither a new job nor another SQL START has been dispatched; step 2 remains
incomplete and step 3 paused.

### Step 2 corrected physical SQL continuation 5 October 2026

Four additive worker, launcher, controller and test files preserve all 770
preceding source hashes and produce a 774-source manifest. The new operation
is `66a6e102-95bb-4f63-a583-bef3f337f00e`, job
`ibkrv2stg-ownsql-root-20261005` (30 characters), with a separate intent and
reservation namespace. The failed `ibkrv2stg-ownsql-20261005-wrqzuc0` execution
and its job definition remain fixed and freshly verified. Neither the old
operation nor its consumed START is retargeted or reset.

The new launcher uses the reviewed exact `/app` guard, accepts only previously
reviewed null/empty provider descriptors, and emits fixed secret-free failure
stages. It verifies bundled sources and the accepted Linux runtime before
reading the version-pinned test secret. The SQL worker behavior is unchanged
apart from its new physical operation/job/output-prefix metadata. It checks
the test database/role names and full provider catalog afresh before any
installer effects. If partial state or catalog drift exists, setup stops.

The manual job reuses the same verified immutable image, test identity and two
exact registry/secret-leaf grants. It introduces no grant or identity effects,
retains a single replica, zero retries and runtime identity lifecycle None.
That lifecycle setting alone is not behavioral credential-isolation proof.

Fifteen focused tests passed, including replay of the real serialized bundle up
to Windows runtime rejection without a secret read, exact worker preservation,
unsafe-root/libpq/TLS denial, safe diagnostic stages, native-descriptor checks,
and rejection of old-job mutations and consumed-stage replays. Full QA passed:
2,582 tests discovered, 2,580 passed and the same two Windows-specific skips.
Repository policy (755 scanned sources, 293 tracked files, 59 migrations) and
compilation passed. Provider validate-only returned HTTP 200. Protected preflight
SHA-256 is `b42edf57c510359e27d32b48c6c1ad3603c8efb9f8b1e01332eead1b607d0e45`,
version `2026-10-05T08:48:18.1656014Z`, with exact remote readback verified.
Installation is underway. SQL has not started at this checkpoint; step 2 is
incomplete and step 3 remains paused.

#### Subsequent restricted-login failure and read-only diagnosis

Installation passed protected verification: receipt SHA-256
`576b65e28057ae80d9ab1a48f641b5e18332352934844a6be6fd1553de5fb78a`,
version `2026-10-05T08:55:24.7478347Z`. The single START acknowledgement is
`1b72acab07b7d8d5adfdd45a1970c70a0f0bc4ff4c7d487608c7050af7035f1d`,
version `2026-10-05T09:03:32.4903798Z`. Execution
`ibkrv2stg-ownsql-root-20261005-vfq8yud` started at 09:03:23 UTC and failed
with container exit code 1, no restart and no ready-running replica captured.

Unlike the preceding launcher failure, this execution emitted the canonical
SQL-worker record. It reports preparation of the isolated synthetic fixture as
`prepared`, followed by `fresh_restricted_identity_or_scope_differs` at the
first restricted connection check. The check inventory is empty. Positive
restricted-login TLS, wrong-hostname TLS and provider connection tests were
not reached. This is a failed acceptance test, not a successful fresh-login
qualification. The complete observed log family contains one canonical line;
no late-delivery exclusion is claimed. Native execution/replicas/inventory,
full query response, parsed worker record and original protected START chain
are retained in `failed-trial-observation.json`: SHA-256
`c840f1ec9ec092f9545bd78f6d9bbc58c2310a9dcf205fef937c136ed46863ff`,
version `2026-10-05T09:07:25.4967999Z`. The prepared SQL state is not independently
verified by that log record alone.

Source inspection identifies a likely session-string mismatch: the frozen
connector supplies `owner_codex_059_isolated,pg_catalog`, while the harness
requires `owner_codex_059_isolated, pg_catalog`. The official PostgreSQL 16
[libpq documentation](https://www.postgresql.org/docs/16/libpq-connect.html)
explains that spaces in startup options need backslash escaping. No acceptance
predicate is weakened and no fresh-login success is inferred from this diagnosis.

The additive read-only diagnostic uses a new operation
`16940f5d-a442-40e1-ab49-d587803f9a7b` and job
`ibkrv2stg-ownscope-20261005`. Its own source/controller/test files leave all
774 predecessor source hashes unchanged. It reviews the registered fixture
markers and effective permissions, then compares the two search-path strings
using read-only sessions authenticated as the test administrator, with only
the already authorized schema-owner role. It explicitly does not claim fresh
restricted authentication. No extra membership, password rotation, SQL setup,
source-row read/write, model call, cleanup or old-job restart is authorized.
Nine focused tests passed, including the complete serialized bundle's import
and a guaranteed descriptor rejection before credential acquisition, fixed
routes/replay denial and strict native job readback. The initial diagnostic
preflight was deliberately superseded before any effect when the offline
fixture was made portable to Linux. Its source-drift guard rejected that old
snapshot; it retained no accepted preflight and installed/started nothing.
A fresh full check of the corrected 777-source snapshot passed: 2,591 tests
discovered, 2,589 passed and the same two Windows skips; repository policy
(293 tracked files, 758 scanned sources, 59 migrations) and compilation passed.
All 774 predecessor hashes are unchanged. Protected diagnostic preflight:
SHA-256 `e69abc112461eb935a94d358fc99484b7fffbc4af0fc54cbf24c925f1ea34cdb`,
version `2026-10-05T09:31:29.4199495Z`. Job installation was acknowledged and
passed protected native-definition verification: SHA-256
`0ecef0db6af34c86de795752df0a1ce6883913ea623ef010399683003ac6c959`, version
`2026-10-05T09:37:42.8079868Z`. An independent current-identity read also matched
the original registered principal and verified its two exact direct grants.
The single read-only execution `ibkrv2stg-ownscope-20261005-p2x478n` succeeded.
It independently observed the prepared fixture's registered markers, all 84
expected column privileges and the two search-path representations. Both yield
the same ordered effective schemas; only the spaced representation matches
the frozen verifier. These observations use administrator authentication and
the already authorized schema-owner role, not a fresh restricted login.
There were no data changes, grants or credential rotations, source-row reads or
writes, model calls or agent activation. No ready-running replica was captured.

The complete observed log family, canonical worker record, exact native job,
terminal observations and original protected START chain passed protected
verification in `diagnostic-accepted.json`: SHA-256
`1dbafeec95287bcc0bb05edfa8c0d0d8f72481645f101a54f283064014bc18af`, version
`2026-10-05T09:47:37.3525212Z`. Future late delivery is not excluded. The source
connector mismatch is now reproduced without changing the acceptance predicate.
The next separately reviewed qualification must canonicalize startup options,
verify the existing fixture before any effect and rotate only its disposable
restricted LOGIN password; the lost ephemeral password is not recoverable from
the failed worker. It must not repeat setup, change grants or restart either
consumed execution. Step 2 remains incomplete; step 3 stays paused.

#### Prepared fixture qualification

The new `owner_task_isolated_sql_resume.py` worker and its separate controller
and test preserve all 777 preceding source hashes. The 780-source continuation
uses operation `5064a688-bbac-4f7d-a63b-a4303caf29b1` and job
`ibkrv2stg-owntrial-20261005`, with new protected intent and reservation names.
Its connector backslash-escapes the startup-option space so the unchanged
verifier receives exactly `owner_codex_059_isolated, pg_catalog`.

Before rotating the one lost disposable LOGIN credential, it freshly verifies
the registered markers, all existing permissions and the exact synthetic
fixture in a read-only transaction. It neither repeats setup nor changes
grants. It then runs the unchanged eight-check restricted harness and separate
positive TLS, same-server wrong-hostname TLS and provider connection probes.
Passwords and SCRAM verifiers remain within the trusted test process. No model
or source-data access is introduced. The complete 16-source launcher uses
base85 compression within the existing 64 KiB argument limit; no source or
verification check is omitted to fit.

Ten focused offline tests passed. They cover the startup options, fixed
host/login/database/TLS scope, read-only fixture checks before credential
rotation, one credential rotation, preservation of old attempts, deterministic
complete launcher, strict native descriptors and denial of unauthorized routes.
The complete serialized launcher also rejects the synthetic runtime mismatch
before credential acquisition. Full source QA passed: 2,601 tests discovered,
2,599 passed and the same two reviewed Windows fixture skips. Compilation and
repository policy passed (293 tracked files, 761 scanned sources and 59
contiguous migrations). All 777 predecessor source hashes are unchanged.
Protected preflight SHA-256 is
`02cebd7e39749ce49b1760be3adaa0ba4aa4879f9ee462955d74457f03192368`,
version `2026-10-05T10:13:28.5369696Z`. Main-agent local digest and current-source
verification also passed. Installation is underway; no new SQL execution has
been dispatched at this checkpoint.
This qualification cannot establish model/process credential isolation by
itself. Step 2 is incomplete and step 3 remains paused.

#### Launch encoding diagnosis and correction

The prepared-fixture job subsequently passed protected native-definition
verification: SHA-256
`6f32122aae6808fe06eb4c014bd5f289200ee04d94ad4c687551f2dc3d796baf`,
version `2026-10-05T10:17:31.5033853Z`. Its single START was acknowledged:
SHA-256 `5349eab3e4e4e622b0cce7ccaf75db301ef6fc1ca3a24ffcfcb8fc40c95ca9d1`,
version `2026-10-05T10:20:17.3992767Z`. Execution
`ibkrv2stg-owntrial-20261005-ti8rq5q` started at 10:20:12 UTC. Native observation
captured its ready, started, Running container before it failed. The complete
observed log family contains five traceback lines and no SQL-worker record.
The final line is `ValueError: base85 overflow in hunk starting at byte 1020`.
The initial collector rejected the missing canonical SQL record and wrote no
acceptance. A separate collector retained the full logs, native failure, ready
observation and original protected chain without weakening that requirement.
Protected failure SHA-256 is
`38c25905bcf937f50091810316f7b4c94b963231884626fc19a6330746f379dd`, version
`2026-10-05T10:28:20.2392623Z`. No SQL state or credential rotation is independently
verified by this launch-failure record; the attempt remains consumed.

The argument contains 12 doubled-dollar sequences and six dollar-parenthesis
sequences. The official Kubernetes
[container argument specification](https://kubernetes.io/docs/reference/kubernetes-api/core/pod-v1/)
documents dollar-reference expansion and reduction of doubled dollars. Applying
that reduction locally corrupts the exact protected payload; its original
unmodified bytes decode to the registered source hash. This is consistent with
the observed decoding failure, but the actual runtime argument bytes were not
independently captured, so the platform-expansion cause remains an inference.

The additive safe-encoding worker, controller and test preserve all 780 previous
source hashes and produce 783 sources. Operation
`06078342-70df-45d9-b69e-6cec37710780` uses new job
`ibkrv2stg-ownsafe-20261005`, intent and reservation names. Its lossless codec
substitutes the otherwise unused colon character for every encoded dollar sign.
The complete outer argument has no dollar character; the decoder restores it
only inside the running Python process. All 16 sources, source hashes, runtime
guards, fixed SQL/TLS/fixture checks and the original 64 KiB argument limit are
retained. Neither the previous job nor its consumed START is retargeted.

Twelve focused tests passed, including full launcher execution to pre-credential
runtime rejection, exact codec/source reconstruction and replay of predecessor
corruption under argument expansion. Full QA and protected preflight subsequently
passed: 2,613 tests discovered, 2,611 passed and the same two Windows fixture
skips; compilation and policy passed (764 scanned sources, 293 tracked files,
59 migrations). Protected preflight SHA-256
`5168ff99b3c4fc712346e78c8e2b1169d3e10281558b7e4ad98fa003e58c9a80`, version
`2026-10-05T10:38:10.7851301Z`, binds the 783-source manifest and 61,807-byte
complete dollar-free argument. Installed-job verification SHA-256
`8576ef020cefe19b4848d89d353fff14c8214bf0e37da9e79368675ae17df7f9`, version
`2026-10-05T10:42:35.3310615Z`. Single START acknowledgment SHA-256
`b11ed11a5b3ed2cf9ee5a4e95c30e16b33eae9c19ede4e080c5a7416c1a8e9f1`, version
`2026-10-05T10:45:35.2055873Z`. Execution
`ibkrv2stg-ownsafe-20261005-mk8nf8t` was independently captured Running, ready
and started. Its observation SHA-256
`c2070b9224c50ac22af895c862f28e97b95341f9fd059ba83d0a3881f55dfec6`, version
`2026-10-05T10:46:03.0198792Z`.

#### Restricted SQL subgate and TLS diagnosis

The execution later failed, but its sole observed canonical worker record
reports all eight restricted-login checks passed under PostgreSQL 16.15. It
verified the same 84 column privileges, 21 denied privilege probes, scoped CRUD,
tenant/owner/missing-scope/pool-reset isolation, concurrent idempotency/CAS,
cancelled outcomes with explicitly synthetic receipts, fresh authority revocation
and final privilege readback. The test rotated only the disposable LOGIN
credential once; source reads/writes, production changes, grants, model calls
and activated agents remained zero. The later positive-TLS check rejected its
statistics observation; hostname and provider probes were not reached.

The first collector rejected native execution aliases rather than accepting an
unverified template. Read-only inspection found an empty `initContainers` list
and the sole secret reference `cappjob-ibkrv2stg-ownsafe-20261005`. The original
installed definition still passed its strict validator. A separate failure
collector protected the complete bounded observed family, terminal native
state, ready observation and original protected dispatch chain, without granting
whole-execution acceptance. Failure SHA-256
`51c62a8de52f598277c50014bc4009625d7f9dd0b02b9f85952a7245facd6510`, version
`2026-10-05T10:52:41.1648117Z`. The execution remains consumed, not restarted.

A fresh independent protected readback verified all eight subgate checks and
their manifest hash, exact native failure and installed secret version. It
accepted only those two exact execution aliases and found every other executed
field unchanged. Restricted-SQL subgate SHA-256
`0066eb52dedc8a27333765089b1e2ecb1bb5a13436d657ba1588788a64285b22`, version
`2026-10-05T10:55:32.8446156Z`. This does not accept TLS, provider access, signed
owner authentication, real receipt integration, model isolation or step 2.
Bounded observed logs do not exclude later delivery or zero-log replicas.

PostgreSQL's [statistics implementation](https://github.com/postgres/postgres/blob/REL_16_STABLE/src/backend/utils/adt/pgstatfuncs.c)
checks the effective role's privileges on the authenticated session role and
masks SSL fields when that check fails. The capability role does not inherit
the LOGIN's privileges. This explains a plausible statistics-visibility failure,
not an independently established live TLS cause. The additive observer will
measure SSL/TLS as the fresh authenticated LOGIN and separately compare the
capability-role observation in a read-only local-role transaction. Actual
certificate/hostname-verified TLS and supported protocol must pass; null
statistics are not accepted as TLS proof. No `pg_read_all_stats` or other
monitoring privilege is added.

Three new files, `owner_task_isolated_tls_observation.py`, its deployment
controller and test, preserve all 783 previous hashes and produce 786 sources.
Operation `c0f4ac37-55b6-4fa8-9a58-7fcb7e48c31d` uses new Manual job
`ibkrv2stg-owntls-20261005` and new protected intent/reservation names. It checks
post-trial markers/ACL/revoked synthetic authority and fingerprints registered
synthetic rows before one disposable LOGIN rotation. It reads only fixed TLS,
provider identity and synthetic-fixture metadata, then rechecks the fingerprint.
It cannot repeat setup/CRUD, reset fixtures, change grants or restart previous
jobs. Its 15-source launcher preserves the fixed image/runtime guards,
dollar-free encoding and 64 KiB limit. Twelve focused offline tests passed;
full protected preflight subsequently passed: 2,625 tests discovered, 2,623
passed and the same two Windows fixture skips. Policy (767 scanned sources,
293 tracked files, 59 migrations) and compilation passed. Protected preflight
SHA-256 `8766660189f287e4e68d9e8ac833db46a7f319ba64aa97bfe943b6a17f790d22`,
version `2026-10-05T11:09:53.9127656Z`, binds the 786-source manifest and
60,800-byte complete argument. All 783 previous hashes were independently
checked unchanged. Job installation passed protected native verification:
SHA-256 `32e978784aca1417bf28547c3e8c5b0710d611b39791210d762781c64d01d758`,
version `2026-10-05T11:15:01.9495557Z`. An independent read-only native observer
was started before the protected one-shot START; dispatch is in progress at
this historical checkpoint. No TLS/provider result or model-isolation acceptance
was established. The subsequent outcome is recorded below.

The single START acknowledgement SHA-256
`af1a9c821c53c1c34734433f3d3a7cfc5b25c28d90d475bee7ec530a436f47d3`,
version `2026-10-05T11:17:14.7739430Z`, names
`ibkrv2stg-owntls-20261005-alr1oal`. A native ready/running container was captured;
the execution subsequently Failed. Protected full bounded family, dispatch
chain and fresh live/native verification SHA-256
`8167726fab11c6f71810b237146a5a39d5e93b54abb418788e54cc1094a8ae57`, version
`2026-10-05T11:22:04.5051398Z`. The sole canonical worker reason is
`isolated_sql_tls_role_observation_invalid`, after the fixture fingerprint and
one disposable LOGIN rotation. No positive-TLS acceptance field was returned;
hostname and provider probes were not reached. No fixture writes, grants,
production/source changes, model calls or activated agents were reported. The
attempt remains consumed. This failure does not invalidate the independently
verified restricted-SQL subgate.

PostgreSQL's [statistics view definition](https://github.com/postgres/postgres/blob/REL_16_STABLE/src/backend/catalog/system_views.sql)
filters `pg_stat_ssl` on a non-null `client_port`. Its permission-masked backend
record can therefore disappear from that view, rather than appear as a row with
null SSL fields. The observed failure did not retain that row, so the explanation
remains an inference until measured by the new observer. The new fixed observer
allows either this no-row diagnostic or the exact reviewed masked row; the
fresh LOGIN's own positive TLS check still requires a real SSL-enabled row with
TLS 1.2/1.3, pinned CA and hostname verification. No monitoring privilege is added.

New operation `6d2bc1c1-b0c3-4bdd-aac3-bfc11ecece71`, job
`ibkrv2stg-owntlsv-20261005`, uses separate intent/reservation names and the
additive `owner_task_isolated_tls_view_observation.py`, controller and test.
All 786 previous source hashes remain unchanged; its manifest has 789 sources.
Fourteen focused tests passed, including absent own-login statistics rejection
and the capability-view no-row diagnostic. A fixed known post-trial synthetic
fingerprint must match before credential rotation. Full protected preflight
subsequently passed: 2,639 tests discovered, 2,637 passed and the same two Windows
fixture skips; compilation and policy (770 scanned sources, 293 tracked files,
59 migrations) passed. SHA-256
`807fd2f2dc30ca91b00483384ce556bb8160a917a6a5d11aa7dcc5fe93df3ffc`, version
`2026-10-05T11:38:38.9479498Z`, binds the 789-source manifest, 15 runtime sources
and 60,910-byte dollar-free argument. The new job-install dispatch is in progress
at this checkpoint. No START or live TLS result is yet established. Step 2 remains
incomplete; step 3 is paused.

The view observer later passed installation readback SHA-256
`b07a2f9ab14cadff2a881eac2988dc8f108562cfddba51c346b6fd973cd2939a`, version
`2026-10-05T11:42:46.3291214Z`. Its single START acknowledgement SHA-256
`2433c23614cb352c1b39a71c34dcd1b2226faefaf2066cd02899138205dba2ff`, version
`2026-10-05T11:45:21.9166152Z`, names
`ibkrv2stg-owntlsv-20261005-191rlke`. Native Running/ready/started capture
SHA-256 `e3e83ce5d018e862a8bf6158f0ba6ac13400f14dd2b964af6c4ec29b6e85968a`,
version `2026-10-05T11:45:48.4187891Z`. Full bounded family, remote protected
dispatch chain and fresh native/live checks subsequently verified the sole
canonical worker result and native Succeeded state. SHA-256
`e5275d73244000a412b46529b7ab72ea2033827b4e21d3bf2fe961055c8e7015`, version
`2026-10-05T11:48:18.5068248Z`, status
`tls_verified_provider_and_model_acceptance_pending`. TLS 1.3/verify-full and the
same-server wrong-hostname rejection passed. Capability statistics were actually
absent; no monitoring grants were added. Both before/after fixture SHA-256
remain `9aa54f84f2b59dd1c18fb9535bf81d7249df13f6dc4a589436df3940460897e6`.
The same 84-column permission hash is unchanged. Provider connections actually
succeeded as the fresh LOGIN to both `azure_maintenance` and `azure_sys`; only
identity/transport metadata was read there. No source values, models, agents,
fixture writes or production changes occurred. No provider-boundary acceptance
is claimed. Bounded logs do not exclude later delivery or zero-log replacements.

Separate operation `1f5ed29a-1dce-40a0-aab4-f5b50fd2e467`, job
`ibkrv2stg-ownobj-20261005`, adds only the provider-object metadata worker,
controller and tests. All 789 previous code/SQL/test hashes remain unchanged;
there are 792 sources. Seventeen focused tests passed. The 16-source runtime
bundle has a 63,438-byte complete dollar-free argument. It rechecks the exact
post-trial fixture, rotates only the disposable LOGIN once, and plans complete
bounded catalog/effective-object permissions for both provider databases as
the fresh restricted login. Full inventories are compressed, numbered and
hash-bound, not sampled or truncated. It invokes no provider routines, views or
tables and reads no stored monitoring values. Unknown accessible objects remain
unaccepted until semantic review; metadata alone cannot prove behavioral denial.
Full protected preflight subsequently passed: 2,656 tests discovered, 2,654
passed and the same two Windows fixture skips; policy (773 scanned sources,
293 tracked files, 59 migrations) and compilation passed. SHA-256
`f65a517c6ec4dcd14078cd89b4028f1eff943a4032b4a279441948837a94bfa1`, version
`2026-10-05T12:03:42.8399750Z`. Installation is in progress, not yet verified.
A separate fresh-process TLS subgate verification reloaded both protected
TLS records, verified the exact positive/negative results and unchanged fixture,
admitted only the three reviewed additive sources and rechecked live Azure/source
guards and the exact native successful execution. SHA-256
`6812f71fe8c71d4e4f77cbee8ee0cf1f171d802b2676053a9b32f6f100cc2a2a`, version
`2026-10-05T12:02:54.0722468Z`, status
`tls_subgate_fresh_protected_and_native_verification_passed`. This accepts TLS,
not provider object access or model/process isolation. Step 2 remains incomplete;
step 3 is paused.

The provider metadata job installation passed protected native verification:
SHA-256 `60b795e00258b089248809f5b0b819f6fd42698c40a50328309fb37e92efade7`,
version `2026-10-05T12:08:26.6819262Z`. Single START acknowledgement SHA-256
`7b0002276ed342f965d7d1d2d31a97046dc81e99e96db232a2e2bdd182f9c0c1`, version
`2026-10-05T12:11:08.7005951Z`, names
`ibkrv2stg-ownobj-20261005-7fj0f2b`. The native observer found Failed before
capturing a ready container. Native/protected-chain/full bounded log verification
SHA-256 `3f57b0db4147b84b2eef5f1808cb0ba5b1280da4978312d5fc8e874bbd8e8111`,
version `2026-10-05T12:17:51.2782336Z`, preserves the sole launcher record:
`stage=module_import`, `sql_worker_entered=false`, `automatic_retry=false`.
There is no canonical worker or provider wire record. Missing records are not
treated as database/fixture or provider acceptance. The consumed execution is
not restarted. Bounded logs do not exclude later delivery or zero-log replicas.

Isolated offline execution of the frozen packed source module set reproduced
`ModuleNotFoundError` for the exact build-only helper
`owner_task_isolated_sql_root_launch`; that helper is not a runtime source.
The new worker defers this local build import until `build()` rather than runtime
initialization. New operation `cb085a51-d6c1-4d1d-819f-031f09a2d4d3`, job
`ibkrv2stg-ownobjv-20261005`, uses new worker/controller/test files and separate
protected intent/reservation names. All 792 preceding source hashes remain
unchanged; the manifest has 795 sources. Twenty focused tests passed, including
the preserved predecessor failure, successful isolated import of all corrected
modules and the actual decoded packed module graph, with SQL connection attempts
rejected during those imports. Compilation alone is not accepted as dependency
closure. Its 16-source runtime argument is 63,453 bytes and dollar-free. Full
preflight subsequently passed: 2,676 discovered tests, 2,674 passed and the same
two Windows fixture skips; compilation and policy (776 scanned sources,
293 tracked files, 59 migrations) passed. Protected preflight SHA-256
`ac1aa81bc185c85e405702c6b214f7099e8bb9ed988da974bffd1732cb364a21`, version
`2026-10-05T12:28:31.9056433Z`, binds the 795-source manifest and reviewed
argument. Installation is in progress; no corrected START has yet been dispatched
at that historical checkpoint. Its installed definition subsequently passed
protected verification: SHA-256
`819e5ea8e05990a00334460570307484e9e0542b2a34aedf4d2c30138c379ba4`, version
`2026-10-05T12:34:32.7485069Z`. A read-only native observer was started before
the protected one-shot START. Its acknowledgement SHA-256
`2102865dc53232efa37a32227ca5f8614f5aa85c299df1948789450f7b3a7576`, version
`2026-10-05T12:38:06.3470560Z`, names execution
`ibkrv2stg-ownobjv-20261005-n9y23ym`. A running, ready container was captured;
first native observation SHA-256
`1a791efd5778a6109f8a8c9af4168f4005652dc93dc17af240f8999b1cd801b7`, version
`2026-10-05T12:38:32.0098210Z`. The execution subsequently Succeeded. Complete
bounded execution-family logs reconstructed all 13 collections for each database,
including all effective object, routine, schema and sequence privilege inventories.
Both compressed records passed full frame/count/type/hash/coverage checks. Native
execution before/after readbacks, the single-execution inventory and protected
dispatch/preflight/installation chain passed verification. Protected observation
SHA-256 `b3660df5e7ce215b5d1a71692dcd09e0d732307b14df31a94526f792bd0d1e87`,
version `2026-10-05T12:41:37.7823100Z`, status
`provider_metadata_verified_semantic_and_model_acceptance_pending`. A separate
fresh-process protected lookup subsequently verified this hash, status, identical
terminal native observations, complete reconstruction and zero object-value reads
or routine invocations. This does not exclude future late logs or zero-log
replicas.

`azure_maintenance` exposes no effective SELECT-accessible non-catalog relation
or executable non-catalog routine to the tested LOGIN. `azure_sys` exposes nine
SELECT-accessible relations and 17 executable non-catalog routines; all observed
relation write permissions, schema CREATE permissions and sequence privileges
remain denied. None of the executable routines is SECURITY DEFINER, but that
does not establish safety: provider C routines may apply internal checks. In
particular `pg_azure_cause_crash`, `pg_postmaster_memory` and
`azure_roles_authtype` were NOT invoked. Metadata EXECUTE is not evidence that
their underlying effects are permitted. Likewise no monitoring values or
cross-user query data were retrieved.

Microsoft's [Query Store documentation](https://learn.microsoft.com/en-us/azure/postgresql/monitor/concepts-query-store)
states that the store covers all users/databases and its views are available to
PostgreSQL's public role. This documents a material provider-default boundary,
not a demonstrated incident or leak from this trial. It cannot be accepted under
the owner's earlier CONNECT/TEMPORARY-only metadata exception. No provider ACL
has been changed, and no broader permission exception has been silently added.
Recommended revised acceptance is credential-free, separate agent compute plus
a trusted broker enforcing fixed database, typed tools and scoped authorization;
models must never receive arbitrary SQL access or the broker credential. This
requires a recorded human authority decision and live adversarial credential/process
separation evidence, not an environment-variable inventory alone. The subsequent
human confirmation below resolves the authority question; no behavioral denial or
credential-isolation pass is established by it. Step 2 remains incomplete;
step 3 is paused.

### Owner reconfirmed system wide operational authority 5 October 2026

The owner directly stated: "The only rule is that all data we received from IBKR
Flex Query should be treated as the source of truth and these data from IBKR must
not be modified, other than this I am happy to authorize the agents to have full
access to the entire system and entire resource group."

This confirms standing operational authority for the six specialists across the
existing platform and `rg-ibkr-database-v11`; further per-permission confirmation
is not the outstanding step-2 blocker. IBKR-received files and values remain
authoritative and must not be overwritten, deleted or replaced by estimates.
Corrections to parsing, mappings, derived analytics and caches must retain the
originals and record lineage to them rather than rewriting the received source.
Previously requested action-by-action documentation and daily reporting at
18:00 Asia/Hong_Kong remain requirements. The original indefinite source-storage
protection approval is not revoked.

Protected authority confirmation SHA-256
`e264c8a291a9a046ad986939aa48ab8fb5470cb8509e003604f981c04b61f90c`, version
`2026-10-05T13:04:43.1283010Z`, retains the exact human statement, its content
hash, six role names, fixed resource-group scope, source-preservation/reporting
requirements and preceding provider observation receipt. Protected storage and
readback passed; all 795 frozen source hashes remained unchanged. This evidence
records human permission, not a new deployment, Azure grant or passing runtime
test. No Azure role/database ACL changed, no agent was activated and no model
or provider routine was invoked by this confirmation.

The chosen broker design separates operational capability from exposure of raw
SQL/cloud credentials. Broad access is implemented through independently audited
execution services; agents do not need unrestricted Owner credentials, the
ability to erase their records or the ability to bypass source protection to
operate the system. These are engineering controls supporting the owner's
requirements, not a claim that the owner requested narrower business authority.
Provider permissions are recorded honestly as present, not falsely described as
denied. Potentially destructive provider routines remain uninvoked in the trial.
Live model/process separation, credential-access denial and the final bound
step-2 acceptance record are still pending. Step 3 remains paused.

### Step 2 Azure permission policy approval 5 October 2026

The owner explicitly replied, "Yes, you may approve", to the proposed correction
limited to Microsoft-managed databases on the registered isolated host. This is
approval to correct the test specification, not a claim that live database
acceptance passed, nor authority to apply the exception to production hosts.

`scripts/owner_task_azure_database_boundary.py` implements the additive policy.
Only `azure_maintenance` and `azure_sys` may retain the observed PUBLIC CONNECT
and TEMPORARY metadata privileges. Their identities, Microsoft ownership and
non-grantable privilege sets must match the exact previously accepted catalog
hash. Unknown databases, catalog drift, provider CREATE privileges or an altered
host/scope fail closed. All 13 collections still pass the original complete
collector validation; neither its historical no-exception flag nor its original
records are rewritten. The older shared-host permission policy is unchanged.

The customer-owned `postgres` and `template1` databases remain outside the
exception. The new policy produces a fixed installer plan to revoke their
PUBLIC grants on the isolated host only. It also requires the target database's
public-schema revoke to run as the actual administrator, before SET ROLE to the
synthetic schema owner, correcting the Azure ownership assumption. These are
unexecuted SQL plans; no database ACL, schema or role changed at this checkpoint.

The named effective-role inventory must be complete, unique and strictly typed.
Outside the two provider databases, only the registered login can CONNECT to
the registered target; neither runtime role may CREATE databases or obtain
TEMPORARY privileges in an ordinary database. This database-level policy does
not substitute for later column grants, forced row-level security, role-membership
or actual privilege-denial tests.

The initial provider behavioral gate accepts only fresh authorization denials
using a credential already demonstrated to authenticate as the target login.
Wrong-password, network, missing-database and TLS failures are rejected as
evidence of authorization. A successful provider connection is not silently
accepted: it requires a further exact object and monitoring-data access review.
Even validated denial records still require protected authenticated provenance;
this source-only validator does not manufacture live acceptance from dictionaries.

Sixteen focused offline tests passed, including original-policy preservation,
catalog mutation, provider/customer privilege distinctions, spoofed identities,
malformed inventories, public-schema installer order and false-denial cases.
The policy also reviewed the actual retained 13-collection catalog without
mutating it. All 750 existing code/test/SQL hashes were preserved; exactly the
policy and its test file extend the manifest to 752 sources. Full source QA
discovered 2,508 tests: 2,506 passed and the same two reviewed Windows-only skips.
Compilation and policy passed, with 293 tracked files, 733 scanned source files
and 59 contiguous migrations; the complete local verification took 303.990
seconds including regression, policy and compilation. This elapsed total does
not change the regression subprocess's 300-second deadline. Local review is
retained in `.work/owner-task-azure-boundary-policy-20261005/local-review.json`.
The policy review was then retained with protected readback. SHA-256 is
`0aeaebb91de14707a753e083e199ee2885415011d9f4e6f38a317e2ff199345a`,
version `2026-10-05T05:45:46.5196938Z`, with status
`source_policy_verified_live_restricted_tests_pending`. Its record binds the
owner's direct human approval, exact 752-source manifest, passing QA and the
previous catalog receipt chain. The trusted reviewer reread the protected
preparation, preflight, intent, native acknowledgement, all five wire parts,
header inventories and accepted observation. Fresh complete logs reconstructed
the same catalog, native terminal metadata matched, and the isolated host,
scoped identity/grants, job and existing-service definitions passed fresh guards.
No existing frozen helper's globals, target or consumed reservation were changed.
Separate-process remote readback subsequently returned
`fresh_protected_policy_source_review_verified`. It verified the exact retained
policy receipt, current source manifest, passing QA, approved exception scope
and registered Azure owner. Both installation-dispatched and step-2-complete
flags remain false. This is source-policy acceptance, not a live SQL adapter.

The approval is a direct instruction in this chat; the record explicitly does
not claim an authenticated website approval. Restricted setup has not been
dispatched. Step 2 is still in progress and step 3 remains on hold. No IBKR data
was accessed or changed.

### Standard pull diagnostic and runtime acceptance 4 October 2026

The owner authorized continuation. Two additive files extend the protected
717-file manifest to 719; every prior code/test/SQL hash remains unchanged.
The new controller and its 34 focused offline tests passed independent review.
Fresh whole-repository QA discovered 2,161 tests: 2,159 passed, with the same two
reviewed Windows fixture exclusions. The actual junction rejection ran.
Compilation and repository policy passed (293 tracked files, 700 scanned source
files and 59 contiguous migrations), retaining the unchanged 300-second
regression deadline. This is not financial/visual, live SQL or OS/dependency
security certification.

Before preparing this diagnostic, the controller reread all four version-pinned
records from the failed `ck83` attempt, its consumed reservation and its full
live failure log. The historical failed attempt was neither reset nor accepted.
The existing website/job definitions and private test host passed fresh guards.
Protected preflight SHA-256:
`313d68f2b27b951ffbce9424b4b9adb5b986c297560bc18e7a1ba7caa64d4090`,
version `2026-10-04T18:17:57.1494470Z`.

The exact reviewed task changes were `pull: true` to `pull: false` and omission
of the forced `user: 10001:10001` setting. The pinned image, checker, payload,
timeouts, empty explicit environment and zero retries/repeats were unchanged.
The immutable checker still requires effective UID and GID 10001; removing the
override tests the image's default effective identity rather than relaxing that
requirement. Microsoft's
[pinned public builder implementation](https://github.com/Azure/acr-builder/blob/17c8ea88980eb0bc9bad34c26739381543ec8276/builder/builder.go)
shows that explicit pull invokes a separate helper. The
[ACR task reference](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-tasks-reference-yaml)
documents the pull and user settings. These support the diagnostic design, not
proof of which builder binary ran or of the earlier authentication error's
complete root cause.

New dispatch operation `ee2817f9-4bc5-4740-88f7-3e2187994620` has a distinct,
conditionally created protected reservation. Its payload intentionally retains
the original probe-family ID `3d3a5fca-b9d6-4323-b81b-64eaa6c4f23d`; evidence
separately binds the new operation, task and acknowledged run. Protected intent
SHA-256:
`9e8501b63724c5f5d27d0bd3ac51dd30c854cccfd6a094263517b60ef2ee0436`.
One exact, zero-retry, non-redirecting ARM POST acknowledged QuickRun `ck84`.
Protected acknowledgement SHA-256:
`2611cd429ffffea273cd2120dfd98286e4b64d07e575da77ba2191cb25245325`,
version `2026-10-04T18:23:07.9613358Z`.

The run succeeded from `2026-10-04T18:22:56.347493+00:00` to
`2026-10-04T18:23:20.633307+00:00`. Complete logs contain 5,672 bytes with
SHA-256 `10100c917174a5a42157a9bf14551d967fc1cd97d9d53ea9b495518a5555d2fc`.
They show an actual download and exactly one whole engine digest line matching
`sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c`,
followed by exactly one source-bound runtime record. Substring matches,
conflicting/malformed digest lines, duplicate records and cache-hit logs without
digest evidence are rejected. Unaccepted terminal output is retained as a
diagnostic and consumes the attempt; it never authorizes another request.

Observed runtime metadata was Linux x86_64, CPython 3.12.14, effective UID/GID
10001, psycopg and psycopg-binary 3.3.6, and binary libpq runtime/build code
180006. The fixed CA bundle contained 224,449 bytes and 150 certificates, with
SHA-256 `714d457d580922dbf1d0be8bd35ba236a842b50b0072ae791582a19adef772a5`.
Checker source SHA-256:
`161db0ff2d9145f3fe72a2d96b0c6e8a2a71eec3fb2a3b0cff70e06aa85ec63d`.
The checker reported no PG environment overrides, credential-value reads,
database connections, SQL calls or model calls. No database/model credential,
custom identity, Key Vault reference, source context, build or push was supplied.
Registry authentication remained platform-managed; absence of all injected
platform authority is not certified.

Protected acceptance SHA-256:
`34f5eb8949ca6c5b34e9b436416ba292e630fd70b64de2df1cbdee2d4548355f`,
version `2026-10-04T18:27:48.9637188Z`. Separate fresh-process read-only
verification completed at 18:32 UTC with status
`independent_standard_pull_runtime_verified`. It reread the protected chain and
reservation, complete live logs and identical before/after terminal metadata,
and repeated current 719-source/resource guards. It dispatched nothing, created
no evidence or directory, and returned the same protected receipt. A separate
reviewer independently observed the same complete log hash and descriptor.

This acceptance establishes effective default identity and runtime metadata in
ACR, not independently retrieved OCI `Config.User`, Azure Container Apps
equivalence, live TLS, provider semantics, restricted login, model credential
containment or security certification. Those gates remain unverified.

The next deployment must first qualify a separately registered **secretless**
Azure Container Apps canary using the same image/checker and no user override,
Key Vault reference or database-secret environment variable. Azure injects
configured secrets before Python starts: the existing trampoline's runtime
check before secret lookup does not prove credential delivery was prevented.
After that gate, the trusted catalog controller must bind the expected descriptor
and fifth source hash to fresh protected acceptance/current sources, and identity
GUIDs to exact ARM readback plus effective inherited permissions. Caller/model
metadata is not authority. A secret-scoped Key Vault grant covers versions;
legacy `AcrPull` covers the registry, not one image digest. A read-only startup
setting on an administrator connection does not create a restricted login.

No identity/grant, credentialed catalog worker, SQL setup, IBKR source mutation
or production activation occurred. The existing daily schedule remains 18:00
Hong Kong time. The private trial host remains Ready and billable. The owner
production runtime, six specialist runtimes and production migration 059 remain
inactive; none of the eight original upgrade workstreams is fully signed off.

### Runtime-preflight and consumed failed probe checkpoint 4 October 2026

The owner authorized continuation. Six additive code/test files extend the
previous frozen 711-file manifest to 717; every old hash is unchanged. The new
runtime checker, exact catalog-worker launch contract and one-shot ACR controller
passed 64 focused offline tests and independent review. Imports are inert. The
checker rejects ambient `PG*` and TLS override keys without copying their values,
checks Linux/Python/psycopg/libpq compatibility, and bounds and fingerprints the
fixed CA bundle. The candidate launch rejects runtime mismatch before reading
its fixed secret environment reference. These are tested source contracts, not
live TLS, SQL, credential-containment or agent-activation acceptance.

Fresh whole-repository QA discovered 2,127 tests: 2,125 passed and the same two
reviewed Windows-specific fixtures were not exercised. The actual Windows
junction rejection ran. Compilation and repository policy passed (293 tracked
files, 698 scanned source files, 59 contiguous migrations). The unchanged
300-second regression deadline was retained. Financial/visual, live SQL and
OS/dependency-security acceptance flags remain false.

Current source hashes, the registered private host, completed canary and the
34 existing-service definitions passed before protected preflight retention.
All four historical execution-family receipts and the original canary preflight
were reread as historical evidence, not recertified as current QA. Protected
runtime-preflight receipt: SHA-256
`d54afd8d01a2821e462b41731c2e966ae0e1de9db2aa2cdb20622b3807c9a8b7`,
version `2026-10-04T17:23:15.1924313Z`.

The controller uses one exact ARM `scheduleRun` POST, with zero HTTP retries,
redirects or asynchronous polling. It conditionally creates and verifies a
fixed protected reservation, then retains protected intent before that request.
Unknown acknowledgement or cleanup effects consume the attempt; a fixed
non-sensitive outcome records uncertainty, and failed outcome retention cannot
rearm it. Azure documents both 200 and 202 responses; this controller deliberately
treats anything other than a fully bound 200 acknowledgement as unknown, not as
permission to repeat the request. See the
[ScheduleRun API](https://learn.microsoft.com/en-us/rest/api/container-registry-tasks/registries/schedule-run?view=rest-container-registry-tasks-2019-04-01).

The one request was acknowledged as ACR QuickRun `ck83`, operation
`3d3a5fca-b9d6-4323-b81b-64eaa6c4f23d`. It supplied no database/model credential,
custom managed identity, Key Vault reference, source context, build or push.
Registry pull authentication was host-managed; absence of all platform-injected
credentials is not claimed. The task specified the exact pinned image, Linux
amd64, empty explicit environment, zero step retries/repeats, and user
`10001:10001`. Protected intent SHA-256:
`d140c9892df1edb39815ad9ca29c6027904b98814ed40d3eb25c661c84ef0a10`.
Protected acknowledgement SHA-256:
`3bca25cdfbec4accdffc8d52f5ed4f771f8d0f694c6be3184c0fe515b49531e6`,
version `2026-10-04T17:28:18.3648798Z`.

The run failed from `2026-10-04T17:28:06.764381+00:00` to
`2026-10-04T17:28:09.566852+00:00`. Its complete 1,226-byte engine log shows
registry login followed by an explicit pull rejecting authentication for the
pinned manifest. No runtime protocol record or pulled-image digest was emitted.
The normal acceptance path refused the failed terminal status and wrote no
`acceptance.json`. No Python runtime, installed-library, CA-bundle, database or
model acceptance is inferred from this attempt.

A separate diagnostic bound the original preflight, intent, acknowledgement
and consumed reservation, retained the complete log and matching raw terminal
metadata before/after collection, and repeated current source/resource guards.
Status is `observed_failed_image_pull_runtime_not_qualified`; root cause and
runtime execution remain unverified. Protected diagnostic SHA-256:
`63b8c19750b6dc77591d827d16bf86e501f5bc0afe9a6854e0d3f679256f7c9c`,
version `2026-10-04T17:39:23.0924191Z`.

Separate fresh-process read-only verification passed at 17:47 UTC, returning
`independent_failed_runtime_diagnostic_verified` and that same version-pinned
receipt. It rechecked the exact acknowledgement resource ID/name, native
Linux/amd64 platform with `variant: null`, complete live log bytes/hash,
protected chain, consumed reservation, absence of an acceptance object and both
current 717-source/resource guards. It wrote no audit evidence and dispatched
nothing. An earlier read-only verification rejected the omitted nullable variant;
the completed verifier checked the actual native schema without rewriting the
diagnostic or accepting the run. Omitted diagnostic fields do not certify
absence of all effects or platform authority.

The forced-user explanation was investigated rather than adopted as a fix.
Microsoft's public builder implementation uses a separate explicit-pull helper,
not the original step's user configuration; removing only the user setting is
therefore not an evidence-backed authentication correction. The exact deployed
builder commit is unverified. The current public
[pull-helper implementation](https://github.com/Azure/acr-builder/blob/main/builder/builder.go)
also warrants a separately reviewed standard/implicit-pull diagnostic. That
diagnostic needs a new operation/reservation and exact task/log/source bindings;
the consumed `ck83` reservation must never be reset. No registry permission,
admin-user setting or credential policy was broadened to make this probe pass.

The image's default user is also still unqualified. A separate read-only ACR
manifest check verified the pinned manifest digest, but the config-blob request
returned HTTP 307. Redirects were not followed, and no token or signed blob URL
was printed or persisted. Local Dockerfiles are not authoritative provenance for
that base digest. A forced-user ACR probe would not prove the future ACA worker's
default user. Qualify it from digest-bound configuration or an exact secretless
ACA trial before credential delivery.

An ancillary read-only check observed the existing 4 October daily-report
execution `ibkrv2stg-agent-report-29851800` succeeding at 18:00:00–18:00:40 HKT.
The registered producer reported website publication and fresh-login readback
passing; its report hash was
`9881072aae43910b5a9d703613e9d4a16255fca9bcacf9d12b600c3966063c1e`.
The informational readiness alert fired at 18:02:04 HKT without action
suppression. Recipient delivery remains unconfirmed. These are bounded live
metadata/registered telemetry observations, not a new protected daily-cycle
acceptance or an independent desktop/browser read of report bytes. The cycle
explicitly covers `specialist-ledger-only; complete-system capture not established`,
recorded zero source-row changes and has no source-mutation authority.

The website, existing job definitions and daily 18:00 HKT schedule remain
unchanged. No IBKR source data was touched, no SQL/model call occurred in this
qualification, and the owner production runtime, six specialist runtimes and
production migration 059 remain inactive. The private trial host remains Ready
and billable. None of the eight original upgrade workstreams is fully signed off.

### Execution-family rehearsal and catalog-worker source checkpoint 4 October 2026

The owner authorized continuation. Four new files extend the historical
707-file code/test/SQL manifest to 711; every historical source hash is unchanged.
The new catalog-only worker has 18 passing offline tests. It accepts only the
registered private host and exact canonical test connection, requires
certificate-verified TLS, read-only startup settings, a safe catalog path and
timeouts, and closes the connection before emitting the complete bounded
catalog record. It has no CLI, ambient credential discovery or SQL-write path.
This is source-only work: no credentialed worker, new identity/permission or
database setup has been deployed. Restricted login, provider semantics and
model credential containment remain unqualified.

The new execution-family gate has 42 passing focused tests, including separate
independent review. It requests all observed protocol headers under the exact
execution prefix, without container/binding/version filters or sampling. Two
complete inventories enclose the existing full-payload reader; extra groups,
containers, versions, frames and observed drift deny acceptance. Rows are sorted
without deduplication so benign response reordering is tolerated. Both complete
inventory objects must fit their unchanged 2 MiB retention caps before the
first write. Partial local records remain consumed and cannot be overwritten.
The fresh verification path performs no audit-evidence writes or
evidence-directory creation.

Fresh whole-repository QA passed: 2,063 tests were discovered, 2,061 passed and
the two previously reviewed Windows-specific fixtures were explicitly not
exercised. Compilation and repository policy passed (293 tracked files,
692 scanned source files, 59 contiguous migrations). Financial/visual, live SQL
and OS/dependency-security acceptance flags remain false. The protected current
preflight is SHA-256
`779fbec4ebc57033825b3a8d0e9ee0c1ff4c4894874dde74e992629b17c32dcf`,
version `2026-10-04T15:57:29.6210213Z`. Current source/runtime, private-host
metadata, completed canary and the 34 existing-service definitions passed their
guards; the previous full 4 MiB protected wire and historical receipts were
reread rather than treated as current QA.

Complete live family collection and protected retention passed. Both complete
header inventories contain all 1,025 frames and retain execution/container,
timestamps, exact parsed headers, raw-line SHA-256 and byte lengths. Each
inventory is less than the unchanged 2 MiB cap. Their normalized inventory hash
is `295a3d63ed751887fa2a43d194341b6686dc657b58df0d2c570cf8e2b107b254`,
matching the previously accepted captured-replica inventory. The full live wire
also equals every previously protected part and reconstructs exactly 4,194,304
bytes with record SHA-256
`5c7101833c90c9539fd9fa1a158053dfe04bee71a4746ceb42eecd10981a35b5`.

Protected before-inventory receipt:
`a4e17a29b4d4714270e19d7eb93dc4512618668be658ed17c2a4b3c13d4c2fcd`,
version `2026-10-04T16:06:57.2363278Z`.
Protected after-inventory receipt:
`c37337eccb6dea1c5bd391b032decaeea08f3b7860299fb0a9b45e8eea4c6a1b`,
version `2026-10-04T16:07:38.5903279Z`.
Protected family acceptance receipt:
`ba676b557fb0879b8982f84847548d653fe34bb0c164e04478a37035742bcd0f`,
version `2026-10-04T16:09:23.8232896Z`.
Status is `observed_execution_family_transport_rehearsal_retained`; all four
new evidence records use version-pinned, content-addressed protected storage.
Both source/resource guard passes succeeded. No new canary dispatch occurred.

Separate fresh-process verification passed at 16:22 UTC on 4 October 2026,
returning `independent_observed_execution_family_rehearsal_verified` and the same
version-pinned family acceptance receipt. The `-B` invocation reread the new
preflight, all original historical evidence and consumed reservations, the
previous full-wire acceptance and all nine complete wire parts, both new whole
family inventories, the new acceptance and the full live protocol stream. Both
current source/runtime/resource guard passes matched. It created no audit
records or evidence directories and dispatched no jobs. The returned result is
separate-process tool evidence; no additional protected verification object is
claimed. Observed protocol completeness does not prove absence of zero-log
replicas, Azure replacement or future late delivery; those flags remain false. The
owner production runtime, six specialist runtimes and production migration 059
remain inactive. IBKR source data is untouched; the daily schedule remains
18:00 Hong Kong time. None of the eight workstreams is fully signed off.

Next controlled stage: qualify the pinned psycopg/CA runtime and trusted launch
environment, exact credential-reference/identity permissions and one catalog-only
dispatch on the registered isolated host. The worker's four actual source hashes
must be bound independently. Inherited `PG*` connection settings must be rejected.
The completed family gate is a prerequisite, not authority for SQL setup,
restricted-login approval, model credential containment or specialist activation.
The private test host remains Ready and billable; it was not stopped or deleted.

### Read-only synthetic transport acceptance checkpoint 4 October 2026

The owner authorized continuation. A separate, fixed read-only verifier now
continues the already consumed, successful canary execution; it has no job
PUT/START, SQL, secret-value or model route. It validates the protected historical
chain and consumed creation/start reservations without changing them. Measured
UTC timestamps may use `Z` or `+00:00`; nonzero/ambiguous offsets are rejected.
The stored owner-observed replica identity is checked directly. Missing raw
replica-response bytes or a second execution capture are not reconstructed.
Provenance remains **protected owner-observed replica**, not retrospective
original automated capture or exclusion of platform replacement.

Only two new code/test files extend the historical 705-file manifest. Every old
hash remains unchanged, including the original canary controller and its tests.
The current manifest covers **707 code/test/SQL files**. New verifier SHA
`25b04e2afa8949d2288bf5408df262b1e2cb15aa004a442135af8bc260deeaff`,
new test SHA
`b4170a0f6f7978b3986f8ecc4f7933dbacddaa48402137752e3533639c135f6f`.
Independent focused runs passed **25/25 offline tests**. The first full preflight
hit the unchanged 300-second regression deadline before retaining any preflight
artifact. A test-only adjustment isolated already-tested codec work in repeated
late metadata-rejection cases. The real full 4 MiB collect-and-fresh-verify test
remains unpatched. Neither the verifier nor the deadline was weakened; both
subsequent focused runs passed in approximately 122 seconds.

The subsequent full preflight discovered **2,003 tests: 2,001 passed and the same
two reviewed Windows fixtures were not exercised**. Compilation and repository
policy passed (293 tracked files, 688 scanned sources and 59 migrations).
Fresh protected historical readback, current private-host/runtime checks,
exact terminal execution/template and unchanged existing-service definition
guards passed. Protected preflight SHA
`2d6e612a1d0a2ad441cc33012717e72e1a837d1c2684c6f26efc270efaea0678`,
version `2026-10-04T14:46:50.9540129Z`.

All **nine wire parts** are now retained with exact protected versions: eight
parts of 128 frames and one terminal-manifest frame. Strict pinned remote
readback reconstructs all **1,025 frames / 1,024 chunks** to exactly **4,194,304
bytes**, record SHA
`5c7101833c90c9539fd9fa1a158053dfe04bee71a4746ceb42eecd10981a35b5`.
The final source/runtime/current-state guards passed. Protected acceptance SHA
`617c2942b943ab607fc77062e77eb4447edbdfadba2634e0c3276515374c1cf1`,
version `2026-10-04T15:02:04.4349565Z`, binds every part's receipt, the current
preflight, all historical receipts, exact execution/replica, record hash and
header-inventory SHA
`295a3d63ed751887fa2a43d194341b6686dc657b58df0d2c570cf8e2b107b254`.
Independent local review also reproduced the complete exact record and checked
the part bindings; local review alone is not remote certification.

At the 15:11 UTC readback, a separate fresh process reported
`independent_owner_observed_transport_readback_verified` with the exact accepted
SHA/version above. It freshly reread all nine pinned remote parts, reconstructed
the full record, queried both complete live header inventories and all payload
ranges, matched the live/protected raw frames and inventory hash, and rechecked
current source/runtime/host and existing-service guards. This verification stage
saved nothing and performed no job dispatch. Independent local review separately
checked the complete manifest, receipt chains and strict false/zero flags. The
local wire parts alone cannot rederive the timestamp-covered log-inventory hash;
the fresh live-query comparison supplies that check. This is complete transport
acceptance for the **protected owner-observed replica only**, not automated
original capture, execution-family completeness, provider semantics or SQL
credential isolation.

The verifier caps all parts before its first
write, uses create-only protected records, and binds every part to the exact
preflight, owner observation, execution and replica. An incomplete local marker
or uncertain write acknowledgement is inspection-only, never overwrite or
redispatch authority. The final verification stage rereads every pinned remote
part and compares a fresh complete live-log reconstruction; it saves nothing.

Provider SQL authority, provider semantic acceptance, fresh restricted login,
model credential containment, production migration 059 and owner/six-specialist
runtime activation remain **false/pending**. Financial/visual and OS/dependency
security acceptance are not inferred from these code tests. Next is a separately
reviewed catalog-only worker for the new private host, followed by actual
privilege/ownership review and new isolated-target adapters. Existing shared-host
trial helpers must not simply be retargeted. The earlier canary checkpoint below
records historical evidence, not completion of this new acceptance stage.
No IBKR source writes, SQL/model calls, infrastructure or permission changes, job
redispatch, production release, authentication change or schedule change occurred
in this continuation. Existing 34 other job definitions and 18:00 Hong Kong daily
reporting settings passed the final guards. The private test host remains running
and incurs compute/storage costs; it has not been stopped or deleted. This
checkpoint does not complete all eight original upgrade workstreams or activate
the six specialists.

### Catalogue transport and single synthetic canary checkpoint 4 October 2026

The owner authorized continuation of the agentic programme. The shared codec now
supports a complete canonical catalogue up to 4 MiB using 4 KiB chunks and one
final manifest. The provider collector retains every category and its existing
query bounds; it does not sample routines or ACLs. Its complete synthetic
3,250-routine/6,500-ACL record (2,633,802 bytes) now round-trips through the codec.
An oversized complete catalogue is rejected rather than silently shortened.

The log reader is restricted to the existing registered workspace and a captured
actual execution/replica/container/time window. Two complete header inventories
enclose fixed 128-index payload queries. Duplicate, missing, altered, truncated,
partial and changing responses are rejected. A decoded record is transport
evidence, not database/provider semantics or proof that no later logs can arrive.
Captured-replica scope does not exclude platform replacement or other replicas;
an execution-family completeness gate remains required before provider SQL work.

The new `ibkrv2stg-ownertrial-log-canary` job is Manual, one replica, zero retries,
600-second timeout, 0.5 CPU and 1 GiB memory. It has no managed identity, secrets,
registry credentials, SQL, model or IBKR access. Its in-memory worker is bound to
an immutable public Python image and exact three-file source payload. It emits
exactly 1,024 chunks and one manifest for a deterministic 4 MiB synthetic record,
then holds for 300 seconds to allow independent live replica capture. The hold
is a diagnostic timing aid, not an identity or transport-completeness proof.

The original protected preflight discovered **1,963 tests: 1,961 passed and two
reviewed Windows fixtures were not exercised**. Compilation and repository
policy passed (293 tracked files, 686 scanned sources and 59 migrations).
Its frozen manifest covers **705 code/test/SQL files**. Original preflight SHA
`ae7e1836cc94f7e5995bf2ec0524e2bca7e5b0abcd59a8ed29eb9c33aa42a34c`,
version `2026-10-04T12:38:53.2891355Z`. The job was created once after protected
intent and a fixed conditional reservation. Creation acknowledgement SHA
`94f94a4e0a42cab5ddb381e38490024ce8e6490c093930fc7b4fab290b43e9c8`,
version `2026-10-04T12:44:28.0266417Z`. These records remain unchanged.

Initial readback failed safely before any start intent/reservation/POST because
Azure returned `East Asia` instead of `eastasia`, then an additional empty
`ephemeralStorage` resource field. Read-only inspection established that the
image, command, arguments and worker binding were unchanged and the execution
inventory was empty. The narrow readback correction accepts only those measured
metadata defaults; changed commands, nonempty/null/boolean storage values and
unknown resource keys are rejected. Outgoing creation/start bodies remain the
original exact definition. Requested-template and actual-readback hashes are
recorded separately. The correction cannot recreate the job or rearm a consumed
start; it requires a fresh protected full regression and unchanged live baseline.
The independent focused controller review passed **43/43 offline tests**.

The fresh correction checkpoint subsequently passed **1,978 discovered tests:
1,976 passed and the same two reviewed Windows fixtures were not exercised**.
Compilation and repository policy passed; the current manifest retains 705
code/test/SQL files. Correction SHA
`38f731712ac04556ab62655edff490f74695f86738303a38b1340b2c699e9d87`,
version `2026-10-04T13:22:16.8421209Z`. Fresh protected readback and unchanged
existing-service/host guards passed. Independent local receipt review verified
the original preflight/install chain, exact two-file source delta, unchanged
runtime/worker definition and strict QA result; local review is not a separate
remote-state certification. Final controller SHA
`bdbac18bfa59a3f01bef8d4ce6e5c824ccb42b433e0420d2453ea90cc2f82eb7`,
controller-test SHA
`d114f8fe06fcb714d72e1cb501e89f7a270b974bbd30847eda6e07ad206c386e`.

Protected installed-job acceptance subsequently passed: SHA
`9b3224795c395ea57b4bdca3c8fdbbf059364a12be9769fa827e5e52fa9a83e2`,
version `2026-10-04T13:28:01.1233365Z`. One start was dispatched after protected
intent, a separate conditional start reservation and fresh unchanged guards.
The acknowledged execution is `ibkrv2stg-ownertrial-log-canary-9hh541u`, with
start acknowledgement SHA
`6c66a35159f71646f88505a308eb1292b328517634e5747867d33d00741c2ae3`,
version `2026-10-04T13:31:42.5808438Z`. No start was repeated.

Actual Running metadata exposed two further provider differences: execution
timestamps use `+00:00` rather than `Z`, and this populated replicas response
uses a `value` array rather than the previously observed empty `replicas` shape.
The original automated capture rejected the timestamp before creating any
capture acceptance. The owner-side read-only diagnostic observed the actual
replica `ibkrv2stg-ownertrial-log-canary-9hh541u-87mk2`, with exactly the expected
ready/started container and restart count zero. Protected observation SHA
`0255cb4a2c9b05c6dfea127a44309fe45c95497ea171e0c39dec68b825345106`,
version `2026-10-04T13:35:47.7516538Z`. It explicitly records
`running_metadata_observed_not_accepted`. Independent local review verified its
canonical receipt hash, start-ACK pointer and all 705 source hashes. The record
contains one execution response and derived replica identity, not both raw
execution reads or full replica-response bytes. Its provenance is therefore
**protected owner-observed replica**, not retrospective automated capture
acceptance or proof of no platform replacement.

Subsequent exact execution readback observed **Succeeded**, starting
`2026-10-04T13:31:29+00:00` and ending `2026-10-04T13:36:54+00:00`.
The strict complete header response contained all 1,025 expected V1 frames in
the expected container (6,150,718 total raw-line bytes). An initial complete
reader attempt stopped on `logs_transport_failed`; the exact underlying cause
was not retained and is not claimed resolved or diagnosed. A separate explicit
read-only diagnostic then passed all ten HTTP-200 queries: two full inventories
and all eight payload ranges. Each response remained below the unchanged 2 MiB
HTTP cap (largest 787,705 bytes). Complete decoding reproduced exactly
**4,194,304 bytes**, record SHA
`5c7101833c90c9539fd9fa1a158053dfe04bee71a4746ceb42eecd10981a35b5`.
This is actual complete synthetic transport reconstruction using the reviewed
codec/reader, not a successful original controller capture or provider SQL proof.

A further explicit read-only diagnostic repeated the complete reconstruction,
verified the same terminal execution/template and passed current frozen
source/runtime, protected historical receipts and unchanged baseline guards.
Its non-secret summary is protected as SHA
`f55690b650554ea502829b6d579bb5b38c0eaf2b850b5b1fc51d3280ef8710a2`,
version `2026-10-04T13:50:32.1977619Z`; exact remote summary readback passed.
The summary binds the observed-replica receipt, final execution, 705-source
manifest, diagnostic-driver hash, stable inventory hash, all ten query
status/schema/byte/count diagnostics and exact reconstructed-record hash.
It explicitly records controller acceptance, complete wire-parts retention,
platform-replacement exclusion, SQL/provider authority and runtime activation
as false. Only the summary/observed metadata are protected here; no full wire
parts have yet been retained, and this is not fresh-process full reconstruction.

Formal acceptance remains pending: a reviewed read-only continuation must use
fresh frozen QA, independently reconcile the protected historical/current
execution evidence, strictly handle the measured timestamp/replica schema,
retain all nine protected wire parts and reconstruct them in a fresh process.
It must expose no job PUT/START, SQL or credential path. An execution-family
completeness gate and separate provider semantic/restricted-login qualification
remain required before any SQL or agent authority. Neither missing metadata nor
failed log retrieval may rearm or restart this execution.
No SQL/model calls or IBKR source writes occurred. No production release,
existing-job definition, schedule, authentication or readiness rule changed.
The separate PostgreSQL host remains running and incurs compute/storage costs;
it has not been stopped or deleted. This checkpoint does not activate the owner
production runtime, six specialists or production migration 059, and does not
complete any of the eight outstanding acceptance workstreams.

### Protected isolated-host acceptance checkpoint 4 October 2026

The owner authorized continuation. This checkpoint registers one exact new,
private PostgreSQL 16 test host, inventories provider metadata in source-only
code, and provisions the host through a separate trusted owner-side controller.
It does not install production migration 059, dispatch SQL, activate an agent or
give a model administrator credentials. The existing shared-host rejection and
consumed synthetic journal namespaces remain unchanged.

The new target is `ibkrv2stg-ownertrial-20261004` in `rg-ibkr-database-v11`.
Its frozen Default-create request specifies Standard_B1ms, 32 GiB, disabled
storage auto-growth, seven-day backup retention, no HA or geo-backup, password
authentication, disabled public access and the existing private subnet/DNS zone.
No restore, replica source or production-data copy was requested. A unique new
credential was generated in memory and stored as the new Key Vault secret
`ownertrial-20261004-admin-url`. Audit records retain version metadata and hashes,
not its password or DSN. No existing secret value was read or changed.

The controller has exactly two resource-write endpoints: this new secret and
this new server. Before each single PUT it checks frozen source/runtime, the
existing-service baseline, private dependencies and exact target absence. A
fixed create-if-absent protected reservation coordinates cooperating controllers;
it cannot fence an unrelated Azure administrator, and the Azure resource PUTs
are not represented as compare-and-set APIs. Unknown acknowledgements consume
the attempt and require inspection, not regeneration or automatic redispatch.

The first prepare run failed safely before retaining a preflight or changing a
resource: Azure's deleted-secret metadata continuation contained the exact vault
host with explicit port 443. The reviewed correction allows only that canonical
same-vault continuation and normalizes it before the strict read transport.
Other ports, hosts, paths and secret-value reads remain rejected. The fresh full
preflight then passed and was independently reviewed against all frozen files.

Full discovery found **1,875 tests: 1,873 passed and two explicitly not exercised**:
the POSIX FIFO fixture and unavailable unprivileged Windows symlink creation.
Compilation and repository policy passed: 293 tracked files, 678 source files
scanned and 59 contiguous migrations. The source manifest covers **697
code/test/SQL files**. These are not live SQL, financial/visual or OS/dependency
security acceptance. The registry has 25 passing tests, provider collector 19,
and host controller 50. All six final files received independent source review.

Protected records are in `.work/owner-task-isolated-host-20261004/`:

- Preflight SHA `894b816e6f5f00ae0faf1fe978d047e6c0dbb4a1c968bf3148f2bd27f562c368`,
  version `2026-10-04T11:29:14.2414654Z`.
- Provision intent SHA `205652a3692e300b9a8b4993943e154b7e309b117fcb3b492fed4472dcb8b64b`,
  version `2026-10-04T11:32:31.9619390Z`.
- Secret intent SHA `3171df545559a51470745bb1b98b5b9f855d808b86fcf1c0fbf294630c02668b`,
  version `2026-10-04T11:34:03.2501911Z`.
- Secret acknowledgement SHA `b80039ed19c20e389fdeaa044075e432adf777bf95b6d3e6d71acca65e1ad1c8`,
  version `2026-10-04T11:34:37.8563282Z`.
- Server intent SHA `5405705ca87c5c390bb1aebc3d840bc049effdae770441db0eb8f611d221647f`,
  version `2026-10-04T11:35:46.8967394Z`.
- Server acknowledgement SHA `1bdebf8d92bc976a840d038b6f0ae32f23b734b702d04798f4286ecd3f2e5e15`,
  version `2026-10-04T11:36:27.5423331Z`.
- Host acceptance SHA `e8e103df668996a00fe8d07a9e45f71f23078c358d6586f97609817d27f92589`,
  version `2026-10-04T11:45:48.1413402Z`.

The secret returned HTTP 200 and the server HTTP 202. The six provisioning receipts
passed exact remote readback, and separate local review verified their canonical
hashes, links, source, target, runtime and absence of credential payloads. These
acknowledgements are not readiness or SQL acceptance. A subsequent targeted,
read-only GET showed `Provisioning`, the exact new FQDN and expected private
subnet/DNS with public access disabled. Incomplete provisioning metadata was not
accepted by the readiness gate; no PUT was repeated or guard relaxed. The later
authenticated inspection observed **Ready** with the exact registered desired
configuration and exactly one version of the new test secret. Protected host
acceptance then passed. At 11:49 UTC a separate fresh process reread all protected
records, the fixed reservation and live metadata and returned
`independent_host_readback_verified`. Source/runtime remained unchanged. The
website/authentication, 18:00 Hong Kong publisher, readiness alert and all 34 other
job definitions matched their pre-create baseline. GET does not retain Default
creation mode; original request provenance is established through the protected
linked request/acknowledgement chain, not inferred from that missing GET field.

Only the new host/credential, expected incidental private DNS/IP allocation and
protected non-secret evidence are in this resource delta. No new managed identity,
job, RBAC grant or application/trial SQL database, role or schema was installed;
SQL/model calls and IBKR source writes are zero. The small test host incurs
compute/storage charges while
active. It has not been stopped or deleted; no standing cost cap is claimed.

The complete provider collector is **source only**. Its realistic synthetic
3,250-routine/6,500-ACL catalogue is 2,632,295 bytes; its complete canonical record
is 2,633,802 bytes and correctly fails the present 2 MiB bound. Sampling or
omitting routines is not a solution. The next reviewed transport must preserve
the complete canonical inventory through bounded chunks,
reject partial/duplicate/truncated logs, retain all protected parts, and independently
reconstruct the full hash. A separate no-secret/no-SQL Azure log canary must first
qualify that path. Missing evidence must not trigger another SQL dispatch.

Final independently reviewed SHA-256s:

- Target registry: `de25392695ccd4e12e6fa3e861a7c853dbdc605b73b9e2f7cee0a8de163de7a2`.
- Registry tests: `0ad3a4b092dd63d21d151826cda5614b1c6f97774475133bcaf66e218dd77f2c`.
- Provider collector: `913db2f9b527cbc74eb243851f74ececdf378f26268a79af18b8d37ef26f1ece`.
- Collector tests: `d23b565418ef304ce345fd058359a5b24f20e0a2f8b9fa0fa9cc267bc5077c6c`.
- Host controller: `5ca960a9d0669e083099d60a4755b521d08ccb82c0b5718ef77084dda1586652`.
- Host tests: `c8e871008b077fed1b7bfb258ed7470203ceee3278fc4c59a428d142ccfd3ff8`.

Narrative edits remain outside the frozen source manifest. This checkpoint does
not qualify blank SQL contents, provider privilege exceptions, fresh restricted
LOGIN, credential containment, cancellation or owner/specialist runtime access.
All eight original upgrade workstreams still need their outstanding acceptance.

### Protected journal checkpoint 4 October 2026

The owner authorized continuation. This checkpoint adds restart-safe fixed-scope
journal controls and qualifies their storage behaviour using synthetic metadata.
It does not install production migration 059, provision a database, change a
permission or activate the owner runtime or any specialist.

The journal uses a bounded canonical hash chain, atomic create-if-absent slots,
full-chain readback and single-use permits held only by the original successful
producer. Matching existing bytes are a lost claim, not permission to proceed.
Restarted processes can inspect the chain but cannot reconstruct execution
authority. A lost acknowledgement consumes the scope; quarantine blocks future
steps rather than claiming to stop an already-running external executor.

The local backend requires an explicitly registered temporary directory and
publishes a complete, fsynced record through an atomic no-replace hard link.
Actual four-process races, abrupt exits after reservation/claim and partial
staging writes passed. UNC and mapped-drive paths are rejected before metadata
access; an actual Windows junction rejection also passed. These checks establish
local process-restart behaviour, not immutable storage, host-administrator
containment or portable power-loss durability.

The Azure backend is fixed to the private, legally held
`ibkrv2stgauhecwc4itaoo/specialist-audit-checkpoints` container and the synthetic
`probes/owner-codex-trial-journal-20261004/` prefix. It uses bounded conditional
block-blob creation with no automatic retries, redirects, overwrites, deletion,
append route, SAS or secondary endpoint. Each acknowledgement is checked against
exact version, ETag, bytes and container protection. This follows the conditional
write model in [Azure's concurrency documentation](https://learn.microsoft.com/en-us/azure/storage/blobs/concurrency-manage);
the observed rehearsal below, not that documentation alone, supplies acceptance.

One reviewed run passed with two separate Azure thread clients, exactly two
conditional-create attempts, one winner and one loser. After synthetic SETUP
metadata completed, the harness injected loss of the acknowledgement only after
the RUN claim had actually committed. Fresh journal instances refused to reissue
the reservation or reconstruct its permit. The resulting six-record chain has
one synthetic acceptance and sticky quarantine in this probe namespace.
Azure process-crash recovery was not exercised: actual process-restart tests were
local; Azure reconciliation used fresh store instances within the rehearsal.

The frozen preflight covers **691 code/test/SQL files**. Full discovery found
**1,781 tests: 1,779 passed and two explicitly not exercised**. The exclusions are
the POSIX FIFO fixture on Windows and unprivileged symlink creation unavailable
under this Windows account. The actual junction and remote-path negatives passed.
Unknown skips, expected failures or missing negative coverage block the QA gate.
Compilation and repository policy passed: 293 tracked files, 672 source files
scanned and 59 contiguous migrations, with no prohibited paths or secret patterns.
These checks are not financial/visual or OS/dependency-security certification.

Protected evidence is in `.work/owner-task-journal-20261004/`:

- Preflight SHA `01fc1bde07f5a4d1edb9ca923dc2bd16eb787f33fe853cdcf26d840ea90bd3ef`,
  version `2026-10-04T10:19:42.1514764Z`.
- Intent SHA `7ed8ac7fdede1571122be7d9fa8c2bcfdb758c9d7223e2ab0c08d9c046de04cb`,
  version `2026-10-04T10:22:07.9186609Z`.
- Acceptance SHA `577e4ee07818a0463159543057809559dec1dfd8e6d733221dabbed59975808c`,
  version `2026-10-04T10:29:56.0948279Z`.

Exact protected readback passed during retention. Separate fresh-process remote
verification passed against all three receipts, the independent six-record
snapshot, frozen source and runtime context. The run checked the accepted
website/authentication,
18:00 Hong Kong publisher and all 34 other job definitions before and after the
rehearsal; their definitions were unchanged. Only synthetic probe records and
their protected evidence were added. They remain under administrator-removable
hold; nothing was deleted. Source rows read/written, SQL calls, model calls and
agents activated were all zero. Narrative edits are outside the frozen source
manifest and do not retroactively become tested code.

Final independently reviewed SHA-256s:

- Journal core: `b05f5b70d221020a5a4be901898fad4308fe31cc5ca308bd3b5129d07eef52bd`.
- Core tests: `60114c1ccdd200b7c9ed4ec2aa71692238413fe562f399174dd4dbaa93668218`.
- Local backend: `9a617ba3b9b8f5cb954a91777ab5f7a3f71f1a5304ee126ddd9d414e0da401c1`.
- Process tests: `4f983025dcd16534192d42ad63faa0f091f1986597867c190a9aaeeb1a2d91a7`.
- Synthetic Azure backend: `12d520e7ab9c78b083d62fc211b9b3a4aab2156baa538ef4e5b436f81cb326dc`.
- Azure tests: `c492ce04ce7ef646fe947742f41209bbe95ca26644ba69594d67284a2e494c74`.
- Probe controller: `14eea7d4fc7cefd46517672583f6cd24b503be5e320d4971bf6505e5bd812350`.
- Probe tests: `548ec443362373bd3f512d8cc1950122a4fddde54ab71e56f20f902877c82568`.
- QA result parser: `3be945e836b897b162c37613ae038e2169a2a2b85b9aeacc8575514909b7eff3`.
- QA parser tests: `0d74a1ca48648843d19ba298f60ddadc085ea86579469cc729dc73043f8816b2`.

The next meaningful integration gate is a genuine restricted LOGIN trial on a
separately registered, blank private PostgreSQL target. A new server is not
automatically compatible: provider-only databases, owners, ACL grantors, schema
ownership and executable routines must first be inventoried and reviewed. Any
unavoidable provider-only CONNECT exception needs an explicit new contract;
the shared-host rejection remains unchanged. The provisioner and worker must
receive only their separate new test credentials, with no production, IBKR or
Flex secrets. Signed owner authentication, model credential containment and
verified cancellation are later gates, not properties proven by fresh SQL login.
The synthetic backend cannot be promoted by flipping a live flag or reusing the
consumed probe namespace. No new target has been provisioned here.

### Protected lifecycle and boundary checkpoint 4 October 2026

The owner authorized continuation. This checkpoint adds guarded trial lifecycle
and cleanup source plus a separately controlled read-only catalog diagnostic.
It does not activate a specialist, install production migration 059 or authorize
a model to execute SQL or receive administrator credentials.

- `owner_codex_trial_lifecycle.py` specifies setup/run/cleanup ordering, an exact
  reviewed source/target/identity binding, fixed-scope durable reservation and CAS,
  independently read-back protected intent before effects, and protected outcome
  before completion. Uncertain results quarantine the scope without retry. Cleanup
  requires its own reviewed receipt bound to successful setup and a definitive
  run outcome. SOURCE_ONLY invokes no adapters. Its 29 tests use synthetic
  adapters; typed acknowledgements are not actual durable or authentication proof.
- `cleanup_owner_task_restricted_trial.py` accepts only the complete marked
  synthetic scope and a separately reviewed database/role OID manifest. It checks
  inventories, privileges, recipients, memberships, dependencies and sessions;
  it uses no FORCE, CASCADE, termination or broad ownership cleanup. Review added
  qualified `pg_catalog.unnest` and a first safe query checking exact catalog-only
  search path and strict zero temporary-schema OID before any casts. Twenty-one
  tests cover refusals and partial/uncertain outcomes. A fresh final connection
  verifies absence, using the same trusted installer, not an independent security
  principal. Candidate metadata is not cleanup authorization.
- `inspect_owner_task_restricted_boundary.py` and its protected dispatcher allow
  only four catalog SELECTs in a fresh TLS/read-only PostgreSQL 16 connection.
  The dispatcher uses the existing private bootstrap's fixed image and secret
  reference, not a new job definition, model tool or setup/cleanup adapter. An
  exclusive protected intent prevents redispatch after an uncertain response.
  Nineteen offline tests cover actual worker packaging, exact execution/source
  binding, strict logs, unchanged definitions and lost-response handling.

The final 69 focused tests and full **1,672-test** local suite passed. Compilation
and repository policy passed: 293 tracked files, 663 source files scanned and 59
contiguous migrations. This is source acceptance, not financial/visual acceptance
or OS/dependency vulnerability certification.

The first protected preflight passed 1,671 tests before the final source-only
temporary-schema correction. It remains intact, unused and superseded:
SHA `25fd7012430bbc3626dcafe4d656fd89ae928cfa0a93fca4d69fa2cb7a87320f`,
version `2026-10-04T09:15:42.3236190Z`. No intent or execution was dispatched from
that checkpoint. Fresh final QA was retained in
`.work/owner-task-restricted-boundary-20261004-r2/preflight.json`, SHA
`3bfb0d74adeba9564fb51e78033ed647d340721c39eb0e237843391c858685c7`,
version `2026-10-04T09:20:33.1824369Z`. Exact protected remote readback passed.
Earlier SQL-run and exact-cleanup receipts were also independently re-read.

One read-only execution, `ibkrv2stg-bootstrap-gv0vq9k`, subsequently Succeeded.
Independent verification matched its actual image, command, stage, secret
reference, capacity, frozen source and single strict catalog record. It found
PUBLIC CONNECT grants on `azure_maintenance`, `azure_sys`, `ibkr_staging`,
`postgres` and `template1`. The registered restricted trial database and all
three proposed roles were absent. The record is **observed / boundary ineligible**,
not passed setup or restricted-login acceptance. PUBLIC here denotes every
database role, not public internet access; CONNECT is separate from table access
and connection-authentication restrictions, as described in
[PostgreSQL's privilege documentation](https://www.postgresql.org/docs/16/ddl-priv.html).
The diagnostic connected only to the registered management database `postgres`
and read catalogs, not portfolio rows. An individual login REVOKE cannot subtract
a PUBLIC grant. No existing
database ACL was changed, no role/database/secret was created and no cleanup was
run. This is a point-in-time catalog finding, not continuous isolation or an
exclusive-controller lease.

Protected intent preceded dispatch, and the completion record received exact
version-pinned remote readback:

- Intent SHA `dcadd6c2f2d16a2f56c3d928ac6486b1ec9195a47f5451548abdbe0f7a06c38b`,
  version `2026-10-04T09:23:33.5841748Z`.
- Dispatch SHA `cfcd72be80fe69006336072500f6bfc1c18e1e9550955d7addc6f33a5a6b9021`,
  version `2026-10-04T09:24:46.5357707Z`.
- Observation SHA `16dc91f661a85c81b68bac1e5a990afd8ea12201e47832c5fd8554ac01f68000`,
  version `2026-10-04T09:29:56.5532161Z`; local receipt:
  `.work/owner-task-restricted-boundary-20261004-r2/acceptance.json`.

Source read/write counts, DDL, grant changes and model calls were all zero. The
existing 34 other job definitions, accepted website baseline and 18:00 Hong Kong
daily publisher settings passed unchanged readback. No job definition, image,
schedule, resource-group permission or IBKR source data changed. Production
migration 059, the owner production runtime, all six autonomous specialists and
full system-wide action capture remain inactive. Alpha World MS remains paused.
The next live trial cannot reuse this fixed shared-host registration: first
review and register an isolated environment and its actual provider ownership,
network/database boundaries and bounded lifecycle. No new environment was
provisioned at this checkpoint.

Final reviewed source SHA-256s:

- Lifecycle: `b1966d70a4f070845af664681928f7333677277e87c79d161166b6be29199afa`.
- Lifecycle tests: `543b5802ef09dcd1ed6176f66e1ff0e02ed0ff32b9b634e59d0517fa9be436f9`.
- Cleanup: `c4068c3d5faa54f7eec676f895d16795f8b97f29b30b7b91ebf75e1eea3a1fb6`.
- Cleanup tests: `d1e52804ad4b1111ebfc0eac461d871f64b1bc4f72d5dfa7843cab683625b571`.
- Catalog collector: `34e9926833ea560e073211e8893e1723babf9e627adbbb8cf5d121147a87e710`.
- Read-only dispatcher: `fca5cd38d819eee3324a27a4219765c54f9a379401f6ca2459f8b00a3aa33b7a`.
- Diagnostic tests: `2e140b2d21cebf842669f38a128ad6b3acc31c881bc86b446bbf4d805507ece5`.

Concrete integration remains separate. OwnerCodexTasks supplies useful revision
CAS and atomic event patterns, but is not a fixed-scope trial journal and cannot
store durable cleanup authority inside the database being removed. The next
candidate is a separately protected fixed-scope reservation/claim/completion
journal, tested for competing processes, lost responses and restarts. Ordinary
content-addressed receipts alone do not prove durable single-use CAS. Existing
observation registrations do not authorize SQL setup. If that bounded journal
cannot satisfy the contract, a separately reviewed control store is required;
there is no implicit production migration installation.

### Restricted-login source checkpoint 4 October 2026

This checkpoint advances the next acceptance stage; it is not a deployment or
successful live restricted-login test. The earlier administrator-authenticated
SET ROLE rehearsal remains separate evidence. The new source is inert on import
and has no executable dispatch entry point, credential discovery, Azure client,
network connector, automatic retry or cleanup path.

Three components were added:

- `src/ibkr_etl/owner_codex_permissions.py` returns eleven exact composable grants
  covering 84 metadata column privileges, schema USAGE and the invoker tenant
  helper. It audits named capability/login permissions, full grantor/grantee
  matrices, PostgreSQL 16 creator and installer memberships, separate ownership
  and forced RLS. It rejects source/credential columns, table-wide rights,
  unintended function execution, schema creation, temporary tables and
  non-target database CONNECT. The login has a four-connection limit; the
  installer identity must come from trusted preparation, not browser state or
  database context supplied by a model. PostgreSQL built-in routines and catalog
  reads remain normal engine capabilities, not a process sandbox.
- `scripts/prepare_owner_task_restricted_trial.py` accepts only trusted fresh
  encrypted connections to the registered management/disposable scope and the
  exact normalized migration 059 SHA-256. It creates three marked roles initially
  NOLOGIN, initializes synthetic fixtures and grants under explicit schema-owner
  authority, then audits effective privileges before setting a password or
  enabling LOGIN. Final role validation is inside the activation transaction, so
  failure rolls LOGIN/password/options back. Provider errors become fixed codes.
  Password-derived material is neither returned nor printed; the proposed
  trusted path uses libpq's explicit SCRAM password encryption before constructing
  SQL, following [PostgreSQL's password-encryption guidance](https://www.postgresql.org/docs/16/libpq-misc.html).
  It has no implicit cleanup of a partially created scope.
- `scripts/run_owner_task_restricted_trial.py` requires a fresh underlying LOGIN
  rather than an administrator SET ROLE session, TLS, PostgreSQL 16, exact
  database/schema/role markers and a complete permission audit. Its eight phases
  cover actual service CRUD, tenant/owner/missing-scope/pool-reset isolation,
  21 privilege denial probes, concurrent idempotency and revision CAS,
  cancellation with synthetic receipt bindings, independently reread authority
  revocation, and final permission readback. Unexpectedly permitted SQL probes
  always roll back; deferred integrity constraints are checked before rollback.
  The restricted capability cannot update the authority fixtures: a separately
  trusted test-control callback performs the exact one-row synthetic revocation.

The fixed proposed scope is `owner_codex_restricted_20261004`, schema
`owner_codex_059_restricted`, with marker
`wealthryse-owner-task-restricted-059-20261004-v1`. It is different from production
and the already removed 3 October trial. These names were not provisioned.

The preparation checks PUBLIC CONNECT on other databases before any DDL. A
per-login REVOKE cannot subtract a PUBLIC grant. This checkpoint did not inspect
live PostgreSQL database ACLs or establish that the shared server meets the new
boundary. If the gate fails, a separately reviewed and registered isolated host
and lifecycle are required; changing shared production ACLs merely to pass the
rehearsal is not permitted by this helper. The present fixed-host specification
cannot silently move to another host.

Acceptance limits remain explicit: synthetic receipt bindings are not protected
archive readback or process-stop proof. Tenant/owner GUCs are context, not
authentication, and direct SQL metadata writes are not safe model tools. Neither
these tests nor a future successful fresh SQL login establishes signed website
owner authorization, managed-identity containment or a credential-isolated
Codex/model process. Trusted broker credentials must remain outside the model
execution environment, consistent with [OpenAI's environment-security guidance](https://developers.openai.com/api/docs/guides/agents-api/environments/security).

Local QA passed 33 permission tests, 28 preparation/contract tests and 26 harness
tests, including real cross-component scope, grant-manifest, validator-result
and revocation-acknowledgment contracts using explicitly synthetic fixtures.
The complete discovery passed **1,603 tests** in 65.401 seconds. Compilation and
repository policy passed: 293 tracked files, 656 source files scanned and 59
contiguous migrations, with no prohibited paths or secret patterns. These are
source tests; they do not create new live acceptance receipts or supersede the
previous frozen 1,515/1,516-test evidence.

Independent read-only review read all six frozen files and separately passed all
87 focused tests. No blocker was identified for retaining this inert source.
Azure template0/public-schema ownership remains a live compatibility check;
the helper must fail safely if explicit owner authority cannot revoke PUBLIC.
The permission-manifest hash identifies the intended specification, not every
catalog value or continuous isolation. Live pre/post validators are still
required; matching specification hashes alone are not catalog-equality proof.

Source SHA-256s at this checkpoint:

- Permission module: `cdb129d6f74387609caa9f9a63ba4e83282d3c5285720d18a2ca410da08e67af`.
- Trusted preparation: `fd8870a45ffbc1ae81b083e623c40a835a09ef2233765d48bee8d2f13dcdc5fd`.
- Restricted harness: `b540072ace10318807b0d99bd9cda21da605582316080a6e13c902cf7838c68a`.
- Permission tests: `8d2ba4583a5a0b749a8f55ef74178b9824b72b52835ca16e257037be61a7a153`.
- Preparation tests: `3432e385907b9148f5b60df8fcd5dab4ce48fec100eaa0de43f68f998c19fdc1`.
- Harness tests: `927ed4bbded67e0c0ddfe4532deea7f9921b04c83f96d36f5fdb7a74c813d549`.

Read-only Azure ARM checks confirmed website revision 112 remains ready, with
provisioning Succeeded. The daily publisher retains `0 10 * * *` UTC (18:00 Hong
Kong), zero retries and the same verified image digest
`d0614cf32c524117bae1df447478b5f5fd85e253db45e742a9017d2db6084fe4`.
No authenticated browser acceptance was repeated here. No Azure, database-role,
secret, schedule, production migration or IBKR source change was made.

Remaining for this stage: qualify the target's network/database boundary;
implement protected preparation/dispatch/failure/cleanup orchestration; execute
the frozen synthetic trial using a genuine restricted login; then separately
qualify managed identity and model/broker credential separation. Durable worker
reservation/recovery, real audit/authentication adapters, owner website
history/status/cancellation and system-wide action capture remain subsequent
integration work. Migration 059 remains uninstalled in production; the owner
production runtime and all six autonomous specialist runtimes remain inactive.
Alpha World MS stays paused pending financial/composite/design acceptance.

### Website inbox and Azure notification deployment 3 October 2026

The owner approved the website inbox and Azure email notices as the replacement
for the blocked Microsoft 365 sender. No Exchange administrator sign-in is
required for this route. The earlier mailbox identity and Graph sender remain
disabled and ungranted; their historical handoff is not a current blocker.

The informational `ibkr-v2-specialist-daily-report-ready` Azure rule was initially
installed disabled; its later guarded activation is recorded below. Nine live
KQL cases passed: duplicated valid messages produced one
matching report, while unpublished reports, the wrong producer or tenant, forged
keys, incorrect delivery claims or types, stale reports and future reports
produced none. Azure's automatic date conversion initially rejected valid
events; extracting the exact report date as a string corrected this before any
resource was created. The rule uses the existing enabled
`ibkr-v11-daily-alerts` receiver for `anthony.ying@wealthryse.ai`. The notification
group and reporting job configurations were verified unchanged. No readiness
event or email was sent during that installation. Protected intent and completion receipts are recorded in
`.work/specialist-notifications-20261003/acceptance.json`.

Ready-event construction requires an accepted website release, the most recently
closed Hong Kong daily window, and a fresh integrity read of an already committed
published report. It permits only exact tenant, date, hash and status metadata.
No source files, credentials, portfolio figures or model-authored text are
included. Stateful per-date alerting deduplicates log copies within the window;
this does not establish exactly-once email delivery. Website availability, Azure
telemetry and recipient delivery must remain distinct statuses.

Read-only image inspection `ibkrv2stg-agent-report-49eacv9` succeeded against live
website revision `ibkrv2stg-web--0000111`, image digest beginning `8f3a771c`.
It confirmed that the live image has no daily-inbox routes or supporting inbox
modules. No database connection was opened, and no financial values were logged.
Protected intent and completion receipts, code fingerprints and the comparison
are in `.work/specialist-inbox-release-20261003/acceptance.json`. The installed
reporting worker and website remained unchanged.

An isolated ACR candidate retains that exact live base image, private templates,
fonts and renderer dependencies while adding reviewed source and tests. The
first build, `ck7s`, correctly failed before producing a release: tests relied on
a locally generated gateway manifest, an excluded synthetic parser fixture and
a workstation-only approved presentation path. These test portability issues
were corrected without removing the checks. Gateway construction now uses an
isolated test manifest. The presentation check verifies the approved SHA-256
and native design evidence at either the local or registered container path.
Only the exact hash-pinned fictional-account parser example may enter the
build; no arbitrary XML, operational files or new private templates were uploaded.

Corrected candidate build `ck7t` succeeded with 1,123 discovered tests: 1,116
passed and seven Windows-only tests were skipped in Linux. Those seven passed
locally. The protected 544-file context has manifest SHA-256
`85aa1182a7919dd986245c41c3e31a292dd4a065031cbc887759db0c6f20a887`.
The candidate is pinned to
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:d0614cf32c524117bae1df447478b5f5fd85e253db45e742a9017d2db6084fe4`.
Build acceptance and protected receipts are in
`.work/ai-inbox-candidate-20261003-r2/acceptance.json`; the failed build's evidence
is retained separately. Local build-context integrity verification is not a
claim of uploaded-source readback. Corrected evidence receipts preserve and
supersede the earlier labeling and failed-build skipped-count receipt.

The latest local suite includes eight additional access and release-control
tests, for 1,134 passes. The candidate has now been promoted to website revision
`ibkrv2stg-web--0000112`, with all traffic on that revision. Azure revision
health is Healthy and Running. Non-image settings and the Easy Auth
configuration match their protected pre-release fingerprints. The previous
verified image and revision 111 remain recorded as the rollback target. No
other existing application or job image was changed.

The verified website login is `ibkr_stg_web`. An initial private check safely
rejected the assumed login name before any grant or source query; its failed
receipt was retained. A separately registered corrected check proved forced
tenant isolation, no report mutation privileges and no raw specialist-audit
ledger access. The login then received only SELECT on
`specialist_daily_reports` and `specialist_report_deliveries`, with no role
membership changes. A fresh private candidate test passed nine HTTP cases:
owner page/list requests returned 200, the unpublished report returned 404,
and all six viewer/anonymous requests returned 403. An unknown tenant saw zero
report and delivery rows. These identity headers were simulated inside a
non-ingress manual job; they were not a browser sign-in test.

The real signed-in Chrome owner subsequently opened the live AI Operations page
from Accounts. The daily inbox successfully processed its response, displayed
the correct no-published-reports state, and completed an explicit refresh.
The layout was visually inspected and workspace status updated automatically.
The owner Codex controls correctly remain unavailable. Public requests to
`/healthz` return 401 under the unchanged Easy Auth configuration; that proves
the public authentication boundary, not backend health. Azure revision health
and the owner browser check provide the separate application acceptance.

Access intent, execution and protected completion evidence are recorded in
`.work/specialist-inbox-access-20261003-r2/`. The temporary reader-grant job was
removed after independent fresh-login acceptance. Its exact definition and
execution evidence are retained for recovery; the approved read grants, shared
identity and secret were preserved. Website release and real owner-browser
receipts are in `.work/specialist-inbox-web-release-20261003/`.
The prepublication release readback also matched the reporting worker and existing
notification-group fingerprints to their earlier acceptance. The worker remained
Manual and the new informational rule remained disabled at that checkpoint.
The protected result is recorded as
`disabled-controls-acceptance.json` in the same release folder. The final source
policy scan passed across 578 source files and 58 contiguous migrations; this
is not a complete vulnerability or financial acceptance certification.

This website-image release did not itself publish reports or submit email. The
subsequent publication and communication checks are recorded immediately below.
Existing ingestion schedules, Flex circuit
breakers, zero immediate retries and all IBKR source values were preserved.
Alpha World MS remains paused. The six autonomous specialists and owner Codex
runtime are not deployed by this communication checkpoint. This does not sign
off the complete eight-upgrade programme or full financial and visual acceptance.

### Daily reporting schedule and watchdog activation 3 October 2026

The deterministic daily publisher is now scheduled at 18:00 Asia/Hong_Kong.
Azure evaluates job schedules in UTC, so the accepted expression is `0 10 * * *`.
The reporting start is 3 October 2026. This is the product's Azure job, not a
Codex reminder. The restricted identity, single database secret, one replica,
zero immediate retries and existing registry binding are preserved. CPU and
memory remain 0.5 cores and 1 GiB; the bounded execution timeout is 600 seconds.
The original manual preflight definition is retained for rollback. Earlier
preflight deployment helpers remain historical; the current staged helper is
`scripts/deploy_specialist_daily_schedule.py`.

The worker uses PostgreSQL's clock, a tenant-specific session lock and the
durable website-delivery queue. It selects missing dates in order rather than
using the largest date, so a gap cannot be skipped. Each cycle processes at most
seven dates; the pending count is the bounded next worklist, not a total backlog
count. Each report must match its protected preview, publish with an exact date
and hash, and pass a fresh-login readback before readiness telemetry. Replays
verify immutable content without a second website delivery attempt. Old catch-up
dates never receive a falsely relabelled current-day notice. Direct email sends,
model calls and IBKR source-table access are absent from this worker.

The protected rehearsal replayed the previously accepted 2 October report and
matched its existing hash. The installed production payload then passed a
pre-deadline smoke test with no new publication and no duplicate notice. Both
executions were verified independently through Azure execution definitions and
sanitized logs. Their retained receipts and the original rollback definition are
in `.work/specialist-daily-schedule-20261003/`.

The separate Azure watchdog detects an expected report even when no job ever
starts. It requires validated current publication telemetry after the 18:00
cutoff and 15-minute grace; ordinary job success is insufficient. Nine synthetic
cases passed in the live Azure query engine, including wrong tenant/hash,
unpublished/future records and absence. The rule is enabled, with the existing
owner email group unchanged and no current missing-publication match. It is an
independent telemetry check, not a second database reader or proof of recipient
delivery. Its receipts are in `.work/specialist-daily-watchdog-20261003/`.

Strict rollout checks caught two Azure representation differences. Unused
registry credentials are returned as empty strings; only these precise empty
defaults are normalized, and nonempty or incorrectly typed credentials remain
rejected. The alert API replaces its tags map rather than merging a partial map.
The exact planned tags were restored under a separately protected repair intent,
without repeating activation or changing functional alert settings. Regression
tests cover both cases. Azure's job inventory also has multiple pages; the
installation and schedule checks now follow every registered continuation and
verify all 34 other jobs. The earlier rehearsal's first-page snapshot is not
misrepresented as a complete inventory.

The final local suite passes 1,168 tests. Source policy passes across 598 source
files and 58 contiguous migrations; compilation passes. The website, existing
report-ready rule, email group, ingestion schedules, Flex circuit breakers and
IBKR source values are unchanged. No new identity or grant was introduced.
The first natural scheduled run has not occurred at this checkpoint and must
still be verified. Protected completion evidence explicitly records that the
six autonomous specialists and owner Codex runtime are not active. Full system
activity capture, the remaining eight-upgrade acceptance work and Alpha World MS
design/analytics sign-off remain outstanding; Alpha World MS remains paused.

Microsoft's [job scheduling documentation](https://learn.microsoft.com/en-us/azure/container-apps/jobs)
defines the UTC schedule, and its [job update API](https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/jobs/update?view=rest-resource-manager-containerapps-2024-03-01)
supports the scoped configuration update used here. Next are acceptance of the
first natural daily cycle and the isolated specialist/owner Codex runtime rollout.

### First natural 18:00 HKT daily-report cycle — accepted 4 October 2026

Scheduled execution `ibkrv2stg-agent-report-29850360` started at
`2026-10-03T10:00:00Z` (18:00 HKT) and succeeded at `10:00:56Z`. Its exact
deployed image, payload, execution template and protected schedule/source
prerequisites passed read-only verification. One successful cycle record links
the 3 October report to six specialist-ledger events and report SHA-256
`cec90e711932419324d088e6e11a3cf8bc6a4dd93d72079ac380ec0bc05897ed`.
The worker reported successful fresh-login readback and website availability;
no desktop database credential was retrieved or expanded to repeat that read.

The exact matching readiness event fired informational Azure alert
`ebbf5f36-5972-202b-e11f-e44b5616001c` at `10:02:05.5396924Z`, unsuppressed.
Both readiness and missed-publication rules remained enabled; the missed query
was empty. The owner action group's enabled recipient and protected properties
hash were verified before and after collection. This proves the configured
report-ready notice fired, not that the recipient received this particular
email. The full report is on the website, not emailed. Earlier 2 October
recipient confirmations remain separate historical evidence.

The initial read-only acceptance checker stopped because Azure CLI adds
`TableName: PrimaryResult` to its query rows. The reviewed correction accepts
only that exact three-string-field wrapper and copies its two metadata fields
into the unchanged strict parser. Unknown fields, row counts, types, producers,
dates, hashes and coverage still fail closed. Twenty focused tests passed
independently; the full post-correction suite passed 1,516 tests, compilation
and repository policy. Core tests do not depend on private `.work` artifacts.

Create-only protected acceptance:
`acceptance/gateway-read-first-natural-daily-cycle-20261003-e36ad860c2a9a41c163d8647a473bad4acf6c5458f5b3fdd4b37f825c4e2bfcd.json`,
version `2026-10-03T23:33:04.7154966Z`, was independently read back by exact
version, length, ETag and bytes with container legal-hold checks. Collection
and retention dispatched no jobs, opened no database connections, made no model
calls, changed no schedule/RBAC and sent no email. The first report's contents
cover only the specialist ledger; complete-system activity capture and the six
autonomous specialist runtimes are not established. Independent desktop report
and ledger byte readback remain explicitly unverified. This verifier is bounded
to the first report's 24-hour window; later cycles need their own reviewed
acceptance rather than relaxing or replaying this fixed proof.

### Successful isolated SQL rehearsal and cleanup — 4 October 2026

The separately reviewed attempt in `.work/owner-task-postgres-20261003-r2`
passed. Its create-only protected preflight binds the earlier 1,438-test source
checkpoint, accepted empty-scope cleanup, all 18 frozen SQL dependencies and
the complete current Python/SQL/document manifest. The new preflight passed
1,515 tests, compilation and repository policy: 293 tracked files, 650 source
files scanned and 59 contiguous migrations. This is not financial/visual
acceptance or OS/dependency security certification.

Execution `ibkrv2stg-bootstrap-ybfle7f` succeeded on PostgreSQL 16.15 with the
exact eight registered checks: scope/catalog, forced tenant/owner isolation,
immutable requests and atomic deferred events, trigger-schema decoys,
concurrent idempotency, cancellation/CAS/expiry, late cancellation and fresh
authority revocation, and committed catalog readback. The root and independent
reviewer checked the actual execution template and safe record against protected
intent/source/dispatch receipts. Migration 059 SHA-256 remained
`b4a78f64969d328f6b3ce1ce71b73cd3c033606fc8580ed1a933dd47857e9861`.

Separate cleanup `ibkrv2stg-bootstrap-bwcqbym` passed after exact catalog,
ownership, ACL and no-live-connection checks. Only the marked synthetic
`owner_codex_acceptance_20261003` database and
`ibkr_owner_codex_trial_20261003` NOLOGIN role were removed. Absence was checked;
no FORCE, connection termination or broad ownership cleanup was used. Synthetic
fixtures are reproducible from the frozen harness, and the evidence remains
protected. Final verification rechecked the unchanged website/authentication,
manual bootstrap definition, 18:00 HKT daily schedule and all 34 other job
definitions. No IBKR/source rows were read or written, no existing service grants
changed, no production migration was installed and no model/agent was activated.

Protected receipts, all independently read back by exact version and ETag:

- Preflight SHA `92143a50e477e162186f50091c35319a60c0ae0bde24e23c86ada3b6cafd3c82`,
  version `2026-10-03T23:13:02.4257122Z`.
- Accepted run SHA `02977b9ed05c3d5a9b678d95dd16835a921d0a9fbe2333b57f27c087fe9cd9d8`,
  version `2026-10-03T23:20:34.8070542Z`.
- Accepted cleanup SHA `f4d9908be269e6dc9f792e8e35a96686c9cb73806dd16e7ba74978618af0b001`,
  version `2026-10-03T23:27:58.9018429Z`.

These are `acceptance/gateway-read-owner-task-pg-reviewed-resume-<stage>-<sha>.json`
blobs in `specialist-audit-checkpoints`, where stages are `preflight`,
`run-accepted` and `cleanup-accepted`. Original failed attempts and their
diagnosis/cleanup were preserved, not rewritten as passes. Source remained
frozen through cleanup acceptance; later parser/document edits are not a
retroactive change to that accepted execution. The paginated idle check rejects
busy/unknown runs and malformed/foreign continuations on every bounded page.
It is a point-in-time check, not an atomic shared multi-operator dispatch lock.

This trial still used administrator-authenticated connections beneath `SET ROLE`
and a role owning its synthetic schema. `RESET ROLE` would reach the underlying
administrator; raw connections must never be exposed to models. RLS GUCs are
caller-selected context, not authenticated owner identity. SQL receipt-shape
checks are not proof that a real protected receipt exists or a process stopped.
Fresh-login, non-owner permission and credential-containment claims remain false.

The next controlled stage must use a separate fresh service login, NOLOGIN
DML-only capability, separate managed identity and only its restricted secret.
It must prove exact metadata column grants, denied DDL/role escalation/source
access, signed owner scope and fresh authority, genuine protected receipts and
revocation. Production PUBLIC CONNECT cannot be denied by a per-login revoke;
do not alter production public ACLs to make a disposable trial pass. Keep any
unproved database-isolation boundary explicit. Before owner runtime activation,
also complete durable task reservation/quarantine, lease/stop recovery,
credential-isolated model execution, authenticated audit capture and private
website delivery/cancellation. The six specialist runtimes and complete-system
activity capture remain inactive; the eight-upgrade programme is not complete.

### Restricted owner worker and failed SQL rehearsal checkpoint 3 October 2026

`owner_codex_worker.py` now coordinates one fixed private
`performance_series_review`; its 48 tests pass independently. This is source
only, with no installed inference, database, network or result-delivery adapter.
Execution authority is separate from metadata authority and binds the current
owner, account scope, worker principal and policy. The worker requires protected
intent before effects, fresh lease/authority/cancellation checks, and protected
outcome before returning a private artifact hash. Producer cessation, exit and
tool counts must be explicit, not assumed. A late or uncertain result is
discarded; logical-task replay and unknown-start/stop states are quarantined.
Quarantine is not durably persisted by this in-memory coordinator, and constructing
a new instance does not prove that an earlier runtime stopped.

Migration 059 and the owner service now require an explicit matching tenant
setting in addition to `app_tenant_id()`. The older helper's default-tenant
fallback must not let an unset scope reach owner tasks. The service rejects
autocommit or an already active transaction before authority/SQL work. Thirty-six
service tests and twelve static SQL contract tests pass. These changes are not
installed in the production database.

The live rehearsal deliberately targeted only
`owner_codex_acceptance_20261003`, NOLOGIN role
`ibkr_owner_codex_trial_20261003`, schema `owner_codex_059_trial`, and marker
`wealthryse-owner-task-synthetic-059-20261003-v1`. The trusted wrapper used the
existing manual bootstrap's administrator binding with an execution override;
it changed no installed image, job definition, schedule or existing service
grant. Effective-role connections remained administrator-authenticated underneath
`SET ROLE`, and the trial role owned its synthetic schema. Neither the factory
nor these connections may be exposed to a model. This rehearsal cannot qualify
fresh restricted login permissions, non-owner privilege boundaries or credential
containment even after its SQL checks eventually pass.

Execution `ibkrv2stg-bootstrap-8jrej7c` failed before setup because dynamically
injected modules lacked `__file__`. Protected reconciliation retained the failure;
read-only probe `ibkrv2stg-bootstrap-tnaonoc` then verified both disposable
objects absent. Corrected injection now assigns deterministic module filenames.
The regression imports the actual frozen bundle in an isolated, credential-free
subprocess with database/network calls blocked; compilation alone is not the gate.
The harness's policy dependency query also uses `DISTINCT` because USING and
WITH CHECK can record separate dependencies.

The one reviewed startup retry, `ibkrv2stg-bootstrap-85v8wm8`, failed during
setup, before the eight behavioral SQL checks. Read-only diagnostic execution
`ibkrv2stg-bootstrap-0lslyiu` observed two grantor-specific membership rows:
an ADMIN-only bootstrap lifecycle link and a separate operational self-grant.
The connector's default inherited attribute had reached the self-grant, and the
wrapper had incorrectly expected one merged row. The reviewed source now
preserves the creator link while explicitly requesting ADMIN false, INHERIT
false and SET true for the separate operational link. The disposable public
schema is owned through Azure's administrator role, so its PUBLIC revoke must
occur as the trusted administrator before switching to the trial role. These
corrected settings have passed local tests but not a new live setup trial.

Separate cleanup `ibkrv2stg-bootstrap-gxjblt5` succeeded only after fresh,
read-only metadata matched the protected diagnosis exactly. Both schemas were
registered and every user relation/function count was zero, including sequences.
No connection was terminated; no FORCE, CASCADE or broad ownership cleanup was
used. Only the exact marked empty synthetic database and its NOLOGIN role were
removed, with final absence readback. No financial rows had been created and no
IBKR values were read or written. The diagnostic and cleanup evidence remains
protected in `specialist-audit-checkpoints`:

- Diagnosis:
  `acceptance/gateway-read-owner-task-pg-metadata-observed-fb61f2c996e2124dd4e18778c47746826a84c65e8c94737589120c9e6a6d2ff2.json`,
  version `2026-10-03T08:33:49.1741949Z`.
- Accepted exact cleanup:
  `acceptance/gateway-read-owner-task-pg-failed-cleanup-accepted-cd9d6f1acef0b9cea9f819aeb7ed3244d8f58607518a01ec5e4ab5fea9678bc8.json`,
  version `2026-10-03T08:47:42.4073163Z`.

Normal post-acceptance cleanup source is also tightened: literal namespace
prefix matching, private-schema ownership, all user relation/function inventory,
exact six tables and ten index signatures, the two named secondary indexes,
five owner-bound zero-argument invoker functions, and all four database ACL
entries' grantor and non-grantability. Unexpected objects or privileges block
cleanup. Index signatures are not complete index-semantic verification, and
standalone types/operators/extensions are outside this relation/function guard.
The existing retry gate rejects the newly changed wrapper intentionally; the
old intent cannot be overwritten or consumed as another successful attempt.
The next live trial needs a separately frozen, independently reviewed bundle.

The source checkpoint script reruns the complete local suite, compilation and
repository policy; freezes Python/SQL and this execution record; independently
rereads the protected trial intent/dispatch, diagnosis and cleanup; and verifies
the unchanged accepted website image/readiness/authentication, all 34 other
jobs and the 18:00 HKT daily definition. It checks source hashes again after
protected checkpoint readback. Evidence is reserved create-only at
`.work/owner-task-postgres-20261003-retry1/source-checkpoint.json` with status
`validated-source-live-sql-pending`. If this final step fails, the retained
reservation requires review, not automatic replacement. This is not a complete
container/configuration/dependency release fingerprint or a new signed-in
browser/natural-timer acceptance test.

Next integration gates remain explicit:

1. Qualify live PostgreSQL behavior, then independently scoped non-owner
   logins/ACLs and durable execution reservation, lease recovery and quarantine.
2. Authenticate the worker handoff, current owner/account execution grant,
   global stop and expiry. Service task intent must precede `attach_intent()` and
   claim; the resulting server-owned lease supplies `ReviewWork`, then a separate
   worker execution intent is required. `owner_codex_task_v1` and
   `owner_codex_worker_v1` receipts are not interchangeable.
3. Install the fixed prompt/observation/result schemas and credential-isolated
   pinned CLI/Azure execution, image/catalog/context validation, egress limits
   and independent owned-process-tree stop proof.
4. Register authenticated model activity and verify exact protected evidence
   through the daily ledger. After owner revocation, quarantine/audit uses a
   separate restricted operations path; do not restore owner access merely to
   record a worker outcome.
5. Integrate private artifacts, authenticated website history/status/cancel and
   freshly authorized delivery. Record actual delivery or withholding separately;
   successful private computation is not permission to deliver a result.

The owner runtime and six specialist runtimes remain inactive. Complete activity
capture, the first natural 18:00 HKT daily cycle, financial/visual acceptance and
the eight-upgrade programme remain incomplete. Entra review and Bloomberg work
remain deferred; Alpha World MS stays paused pending its sign-off.

### Stable CLI acceptance and source-only owner-task controls 3 October 2026

The integration candidate now uses `codex exec --json`, which OpenAI lists as a
stable developer command. The earlier experimental app-server acceptance below
is retained as historical staging evidence, not silently promoted.
See [the command-status documentation](https://learn.chatgpt.com/docs/developer-commands).

ACR build `ck82` accepted the exact source manifest
`7ba4f97fa7ef4caf8ef5450e2a176cb41bdee3a2cad1a1d314c978159c31669b`
and produced
`ibkrv2stgauhecwc4itaoo.azurecr.io/codex-synthetic-runtime@sha256:6511542c49001ba34c2639d4a90d6d3ea28ab1c50cee5be05dc09bd414d34748`.
Its eight-file context contains only pinned runtime installation code, the
synthetic adapters/runner and public model metadata. No portfolio documents,
IBKR values, database/Flex credentials or desktop configuration are included.
The non-root image must pass the actual Linux CLI offline conversation before
ACR can push it.

The deployment alias uses native `gpt-5.6-sol` metadata extracted from pinned
Codex 0.160.0. Only the slug and automatic-upgrade field change. The Linux build
independently compares its own bundled native entry and the custom alias, rather
than assuming that an Azure deployment name implies model capabilities. This
follows [OpenAI's custom gateway/catalog guidance](https://learn.chatgpt.com/docs/enterprise/roll-out-a-gateway).

The strict gates caught two rejected builds, `ck80` and `ck81`, before Azure
inference. Their protected failure receipts are
`acceptance/gateway-read-codex-exec-build-failure-bfc4b67f988017295e6d108ae40fcc1baddc0239b41cb3231079161bc06672e3.json`
and
`acceptance/gateway-read-codex-exec-build-failure-8d37459a9b7f47a91b02034badc67f81948a78adc09449c63e84d2b28b85a16f.json`.
Separate offline shape checks retained sanitized type/key/hash evidence only.
The corrections remove a deprecated feature alias, supply the exact native
catalog, frame stdin with EOF without changing the prompt, and account for the
pinned CLI's measured integer `cache_write_input_tokens` field. Error events and
unexpected fields remain rejected.

The native runtime also emits a developer `additional_tools` declaration even
with ordinary tool features disabled. The CLI-specific relay discards only one
first-position declaration with the exact outer shape and the pinned 14,561-byte
tools hash `449a364fe16bf52da8798ab2f76ab5e5618319de87447719d753d66140ff074d`.
It then invokes the unchanged shared fixed-prompt validator. Changed, duplicate,
missing or reordered declarations, actual tool calls, assistant/reasoning inputs
and unexpected attachments remain denied. No general tool allowance was added.

Execution `ibkrv2stg-institutional-shadow-ln6kdsi` used only a one-execution
override of the existing manual diagnostic worker. Independent acceptance
verified its exact immutable image, command, environment and capacity; four
scoped JSONL events; the expected reply; EOF and process exit zero. There was
exactly one accepted Azure request, zero forwarded tools and no portfolio data.
The protected completion is
`acceptance/gateway-read-codex-exec-azure-completed-8d6f52ea30f873b682fd40d37185aca2dc4e5c2d2e6dce3e25a6b09aa4879966.json`,
version `2026-10-03T04:14:23.1075199Z`.
The frozen attempts and staged receipts remain in
`.work/codex-exec-runtime-20261003/`.

New CLI stage consumption independently rereads the exact remote audit-file
version. Checks require the fixed storage/container ARM identity, a strict
administrator-removable legal hold, exact size/version, conditional ETag read
and byte equality, with protection checked before and after. A local receipt
flag is no longer sufficient for these stages. No new storage permission or
retention change was introduced.

The owner-task foundation is source only: `owner_codex_tasks.py` and migration
059 store typed request hashes and sanitized status metadata, not prompts,
results, reasoning, credentials or IBKR source values. Every operation requires
fresh owner/account authority; worker transitions require separate server-owned
worker authority, disabled by default. Revision CAS and atomic events bind
idempotency, leases, heartbeat, protected intent/outcome and result delivery.
An uncertain expired lease becomes `needs_reconciliation`, never an automatic
repeat model request. Cancellation is sticky across expiry and reconciliation;
success cannot be delivered after cancellation wins. The source SQL forces both
tenant and owner isolation, protects immutable request/event history, and
strips default grants. It creates no login, role or execution grant.

Thirty-six owner service tests and ten static PostgreSQL contract tests pass;
the full regression suite passes 1,287 tests. Windows process fixtures now use
the actual interpreter, with owned-PID and EOF checks, rather than a venv
launcher whose PID differed from the interpreter holding the workspace. Their
ordinary scheduling allowance was corrected without relaxing the explicit
0.1-second deadline test or the runtime's limits. Compilation, source policy
and whitespace checks pass. The final read-only checkpoint separately reruns
the suite and checks source hashes before/after, protected stage bindings,
unchanged daily configuration and healthy authenticated website revision 112.
Its `final-qa.json` is retained beside the exact source attempt's stage receipts;
the document, Python/SQL tree and acceptance-script hashes are bound into it.

The preliminary final checker rejected its daily-worker comparison because it
used a different ARM metadata projection from the earlier protected QA. Read-only
comparison independently reproduced the exact accepted fingerprint using
identity, configuration and template; the worker had not changed. The checker
now uses that same projection alongside its full typed definition validation.
A regression test preserves the distinction between operational settings and
ARM response metadata. The rejected comparison and failed Windows-fixture summary
remain protected at
`acceptance/gateway-read-codex-exec-qa-rejected-0b4b642c590cf61eacb2612ace95a0021129e07c9b4a15c7a7b18991f8cc78c0.json`.
No model request was repeated and no worker setting was rewritten to clear the check.

Remaining acceptance is explicit: real disposable PostgreSQL DDL/RLS/default
ACL/concurrency tests; restricted non-owner worker permissions; concrete signed
caller and protected audit adapters; durable prompt/result storage and website
session/history/status/cancel endpoints; stopped-process-tree evidence, including
revoked-owner outcome quarantine; separate worker/credential/egress boundaries;
and a registered model-activity schema integrated into the daily ledger. The
synthetic relay buffers and validates one tool-free response; it is not full
gateway qualification for continuation, real tools or live token streaming.
Environment filtering and direct-child termination alone do not prove those
production boundaries. No owner runtime or autonomous specialist is enabled.
IBKR source data, Flex controls and the 34 other job definitions were untouched.
The daily publisher remains scheduled at 18:00 HKT; its first natural execution
is still future and unverified at this checkpoint. Full activity capture and
the eight-upgrade programme remain incomplete; Entra/Bloomberg work is deferred
and Alpha World MS remains paused pending its acceptance.

### Earlier isolated Codex runtime acceptance 3 October 2026

One complete synthetic Codex conversation now passes on Azure. The runtime is
pinned to 0.160.0, with the official npm archive's SHA-512 integrity and both
installed executable hashes checked. The image uses a digest-pinned public
Python base, not the portfolio application's image. Its five-file build context
contains no report templates, portfolio data, database/Flex credentials or
desktop configuration. It runs as a non-root user. A complete offline
conversation is a required image-build gate.

The accepted source manifest is
`59fec618c2f014d1180e3af5406ec9cecf76fd920083b5f3175c27f1e120b6a1`.
ACR build `ck7y` produced the immutable image
`ibkrv2stgauhecwc4itaoo.azurecr.io/codex-synthetic-runtime@sha256:00f3f12ca1dead98dae5587e436d6240471fc518edaf48b59468180991a1b666`.
Execution `ibkrv2stg-institutional-shadow-wh8ql4c` used this image through a
single-execution override of the existing manual diagnostic worker. Its
installed job definition, identity and zero-retry setting were not changed.
The approved `gpt-5-6-sol-apac` deployment's model version, SKU and capacity were
verified before dispatch. No new identity, RBAC grant, database migration or
website release was introduced.

The trusted parent obtains the diagnostic managed-identity token in memory.
The Codex child receives only a random local relay capability, a synthetic
workspace and clean configuration directories; it does not receive the Azure
token or identity endpoint/header. The relay permits one exact synthetic
request, pins the model and destination, disables storage and forwards no tools.
The runtime still advertises five built-in tool schemas, which the relay removes
before forwarding. Every upstream item is checked before delivery to Codex;
tool calls and unexpected server execution requests are rejected. This proves
tool-free compatibility, not safe operation of an arbitrary coding task.

Independent acceptance checked the exact execution image, command, environment
and capacity, one sanitized completion record, actual thread/turn handshake,
scoped output deltas, terminal completion and the exact expected reply. Exactly
one model request was accepted. A management-API verification timeout was
recovered by repeating the read only, without redispatching the execution or
repeating inference. The successful acceptance is protected at
`acceptance/gateway-read-codex-runtime-azure-completed-19545f7c6e0c9d44aa523b786d5128d738a9c33c41dfe210815ea026abee9381.json`,
version `2026-10-03T03:03:23.6488791Z`, in `specialist-audit-checkpoints`.
Its source and staged intent/build/dispatch records are retained in
`.work/codex-runtime-20261003/59fec618c2f0/`.

The build and runtime gates also caught failures before any production
promotion. Three initial builds stopped on Dockerfile selection, package-member
validation and a wire-enum mismatch respectively. The corrected Dockerfile path
is absolute; only the two verified package members are installed; and the
pinned binary's generated schema confirms the `read-only` wire enum. These
failures and their separately frozen attempts remain protected, not erased.
The first Azure trial then stopped before inference because its identity guard
accepted only numeric loopback. A separate metadata-only inspection verified
that ACA injects `localhost`. The guard now permits only these two verified
loopback representations and canonicalizes requests to numeric loopback without
DNS, proxies or redirects. No token or model request was made by the inspection.
The failed runtime receipt is
`acceptance/gateway-read-codex-runtime-azure-failure-d4746b57dca7821a97e5d1db3a275fbe436c855981cc34d02c67a8c3150bc82b.json`;
the inspected shape is protected at
`acceptance/gateway-read-codex-runtime-identity-shape-completed-cb2ce6a0dc7b324ff47c7ca204e717d82668e1e057b336dbbee9a38bfe235712.json`.

Azure also adds an empty `ephemeralStorage` field to execution resources. The
verifier normalizes only this observed empty default; nonempty storage, changed
CPU/memory, extra resources, mounts, probes, sidecars and credentials remain
rejected. Twenty-five focused runtime/deployment tests pass. The full suite
passes 1,193 tests; source policy passes across 293 tracked and 607 source files,
with 58 contiguous migrations, and compilation passes. The existing website
and all 34 other job definitions remained unchanged through build and runtime
acceptance. IBKR source values, ingestion schedules and circuit breakers were
not touched. The separately scheduled daily publisher and missing-publication
watchdog remain active; the first natural daily execution is not yet verified.

The [OpenAI app-server documentation](https://learn.chatgpt.com/docs/app-server)
describes its command and WebSocket transport as experimental and unsupported
for production workloads. This trial uses local stdio only, with no public RPC
listener, and explicitly records `production_compatible=false`. Next is a
production integration assessment, including the documented CLI/SDK path,
followed by protected tool dispatch, durable owner conversations, cancellation,
fault recovery and activity-ledger integration. These manual rollout receipts
do not establish complete daily capture of all system operations. Neither the
owner runtime nor any autonomous specialist was enabled. The eight-upgrade
programme is not signed off; Entra policy review remains deferred, Bloomberg
work remains deferred, and Alpha World MS stays paused pending acceptance.

### Earlier daily report publication and Azure email acceptance 3 October 2026

Restricted manual execution `ibkrv2stg-agent-report-715ttvj` published only the
previously verified 2 October report. Immutable content and message hashes match
the stored build. A fresh reporting login verified committed website availability,
one completed website attempt, and the untouched pending email record with zero
attempts. Protected evidence was rechecked; raw-source tables were inaccessible.
The installed reporting job remains Manual with its configuration unchanged.
Protected intent and completion receipts are retained in
`.work/specialist-daily-publication-20261003/`.

The generic publication function now accepts an exact date-and-hash binding.
Selecting another queued day or a different report raises within the database
transaction, rolling back the lease before publication. Three added regression
tests cover mismatched targets, malformed bindings and valid exact release. The
manual execution used the reviewed code; the website image was not changed.

In the real Chrome owner session, the report appeared without a manual refresh
and opened successfully. Its period, zero-event count, six role counts and explicit
coverage limitation were verified. Zero events is correct for that closed window;
this does not establish complete system capture or autonomous execution. The
owner view and screenshot fingerprint have a separate protected browser receipt.
Website publication is not financial-report release or email delivery.

One synthetic Azure Monitor email test was submitted only to the existing
`anthony.ying@wealthryse.ai` receiver. Azure returned HTTP 202. The response
Location failed the initial strict status-route guard and was not retained, so
provider completion cannot be independently polled for this attempt. It was not
resent. The owner directly answered “Yes, received” to the exact test question;
that human receipt is protected separately in
`.work/specialist-email-test-20261003/owner-receipt.json`. Future accepted
responses retain a credential-free routing shape and fingerprint before route
validation. No mailbox grant was added, no full daily report was emailed, and
the legacy email outbox was not falsely marked delivered.

The test uses Microsoft's documented [action-group test notification API](https://learn.microsoft.com/en-us/rest/api/monitor/action-groups/create-notifications-at-action-group-resource-level?view=rest-monitor-2021-09-01).
Provider acceptance, provider completion and a reported human receipt remain
distinct. The subsequent restricted execution `ibkrv2stg-agent-report-prk30p3`
freshly verified the published report and emitted only tenant/date/hash/status
metadata. Its real readiness query matched exactly one report and the correct
notification key. Protected acceptance is retained in
`.work/specialist-ready-event-20261003/acceptance.json`; no database writes,
direct email submission, source access or installed worker change occurred.

Deterministic overdue-publication checks are implemented and tested but not yet
connected to a live monitor. They distinguish a disabled schedule, publication
within its bounded grace period, availability and genuine overdue status. A
fresh integrity failure raises instead of becoming empty or successful output.
An available report does not imply an active schedule or delivered email.

Only the accepted informational report-ready rule was subsequently enabled.
Readback passed for its query, destinations, severity, timing and other functional
settings, as well as the unchanged existing notification group and Manual worker.
The first strict comparison correctly flagged `createdWithApiVersion` changing
from the original 2023 API to the exact 2021 API used for the partial update.
That specific server-maintained provenance pair is now checked explicitly;
arbitrary metadata or functional drift remains rejected. Activation required the
protected publication, real owner detail, confirmed test-email receipt and exact
current readiness result. Protected intent and readback are retained in
`.work/specialist-notification-enable-20261003/`. The rule uses Microsoft's
documented [partial update](https://learn.microsoft.com/en-us/rest/api/monitor/scheduled-query-rules/update?view=rest-monitor-2021-08-01)
to enable the flag; no existing job, ingestion timer or mailbox grant changed.
Rule enablement is not itself evidence of real report-ready email receipt. The
daily report schedule remains disabled pending a live watchdog and scheduled
worker acceptance.

Azure subsequently created a single Sev3 informational report-ready alert at
01:22:06 UTC. Its condition is Fired and its action status is not suppressed;
neither means that the recipient received it. The exact workspace-scoped alert
metadata and protected observation receipt are in
`.work/specialist-notification-enable-20261003/alert-observation.json`. The owner
separately confirmed “Yes, received the report-ready notice” for this second,
real notice. That human confirmation is protected in `owner-receipt.json` in
the same folder; it is not inferred from an alert or provider status. The full
report remains private on the website, and the legacy email outbox was not
modified. An initial server-side name filter returned no alert; a
bounded workspace inventory with exact local ARM-ID matching found the event
without fetching financial context. No alert or source record was modified.

The full local suite passes 1,146 tests. The final source policy check, including
the protected human-receipt recorder, passes across 590 source files and 58
contiguous migrations. These are engineering checks,
not full financial, visual, vulnerability or agent-autonomy acceptance. Next are
live missed-publication monitoring, then the accepted 18:00 Hong Kong daily
worker rollout. The six specialist
runtimes and interactive owner Codex remain outstanding. Alpha World MS remains
paused, and IBKR source values are unchanged.

### Account read and stored daily report acceptance 3 October 2026

The replacement owner sign-in completed successfully. The real gateway returned
444 U16088571 performance observations with its quality status passing; the
tampered signature request returned HTTP 401. No token or financial values were
saved in acceptance logs. The separate reporting identity verified all six audit
records, including protected start and completion records for this account read,
and matched the completion fingerprint to the actual HTTP result. This verifier
had no source-table access, made no database writes and sent no messages.

One fixed-date report build subsequently stored the latest closed report window,
ending at 18:00 Hong Kong time on 2 October. Its zero-event count is correct:
the six retained ledger events occurred after that cutoff and belong to the
next window. The read was not omitted or falsely included in a closed earlier
period. No report for the still-open 3 October window was created.

The builder uses the reporting service's restricted managed identity and database
login. It verifies protected evidence, compares a read-only preview with stored
content, performs an exact replay, then checks the committed report and both
pending delivery records through a fresh restricted login. All checks passed.
The report and its outbox entries persist; they are not a rolled-back synthetic
rehearsal. The report explicitly covers only registered specialist-ledger events,
not every system action or a claim that all six autonomous agents are running.
No sender credential, email submission, website publication, existing worker
configuration change, schedule change or IBKR source change was introduced.

Both manual dispatches retained protected intent before execution and protected
completion after acceptance. The first read-verification check ran while the
execution was not yet successful; inspection of the same execution subsequently
showed success, and final acceptance passed without redispatch. Reporting worker
settings were independently checked against their pre-dispatch fingerprint.

Email transport remains unconfigured. Resource inventory found no Communication
Services resource or Azure mail connector in this resource group. Microsoft now
lists ACS Email for retirement on 30 September 2028 and recommends moving away
from it rather than onboarding new solutions. No ACS email resource was created.
Microsoft 365/Exchange is the selected transport; the owner subsequently chose
`anthony.ying@wealthryse.ai` as sender. No Microsoft 365 mail permissions were granted.
See [Microsoft retirement guidance](https://learn.microsoft.com/en-us/azure/communication-services/acs-retirement-and-breaking-changes-guide?WT.mc_id=M365-MVP-9501).

Local evidence is in `.work/specialist-gateway-20261002` and
`.work/specialist-daily-build-20261003`. Protected completion receipts are:

- Account-read verification: `acceptance/gateway-read-completed-ae6c542f57e22b185fbfa020c00d5631881a4c4de438663853e63f69d2a24379.json`.
- Fixed-date daily build: `acceptance/gateway-read-daily-build-completed-15ca46f483378def42e5a91674ddbcfd5a714cc6b9b3c7ec40fe3fa5122e42ab.json`.

The full local suite passed 1088 tests. Website inbox release, email-provider
acceptance and delivery evidence, missed-delivery alerts, 18:00 schedule activation,
the owner Codex runtime and the six autonomous specialist runtimes remain
outstanding. Alpha World MS remains paused, and the Entra policy review is still
deferred. Neither this checkpoint nor the test count completes the eight-upgrade
programme.

### Daily email sender preparation 3 October 2026

The owner selected `anthony.ying@wealthryse.ai` as the sender. The recipient
remains the same address and the reporting cutoff remains 18:00 Hong Kong time.
Directory readback confirmed this user's ID as
`8617fd1c-2280-40f2-92dd-45686e9d686c`; this does not verify an Exchange mailbox
or confer permission to send mail.

One dedicated managed identity, `ibkrv2stg-specialist-mail-identity`, was created
with protected intent retained before creation. Its client ID is
`cc80dc26-9f62-4231-bc63-dbe8276a0eca` and service-principal ID is
`eb172a60-a283-4ad0-be96-515a60561615`. Fresh readback confirmed zero Microsoft
Graph application-role assignments and zero Azure role assignments, including
inherited assignments. No worker attachment, database grant, mail credential,
mailbox permission or reporting schedule was added. No email was submitted and
no IBKR source was changed. Exchange permissions remain unverified. The pinned
Microsoft Exchange module 3.10.1 was subsequently loaded from an isolated
workspace copy after both module signatures verified as Microsoft-signed and
valid. Human device sign-in succeeded, and the expected tenant check passed.
The read-only preflight then stopped at administration capability discovery:
one or more required Exchange administration commands were unavailable to the
signed-in account. The session was disconnected. No grant was attempted, and
the existing mailbox itself was not yet verified because that check follows
the capability check. This is not a failed device code or an HTTP 403 finding.

The new `agent_daily_graph_mail.py` adapter is tested locally, not deployed or
enabled. Its deployment factory selects only this dedicated managed identity;
it never falls back to owner, CLI or shared application credentials. The adapter
fixes the sender, recipient and Graph endpoint, validates the report date,
delivery key and fingerprint, submits only deterministic plain text, saves a
Sent Items copy, disables redirects and performs no send retries. Ambiguous
provider acknowledgment cannot claim success. Graph HTTP 202 with a valid
request reference means submission accepted, not delivery to the recipient.
The existing commit-before-send dispatcher still owns outbox transitions and
uncertain-send handling. A subsequent safeguard checks transport configuration
before any queue transaction, so a disabled sender cannot claim a report, consume
an attempt or convert a pending delivery to uncertain. Eleven adapter tests,
seven synthetic PowerShell preflight tests and the new disabled-queue regression
passed; the full suite passed 1107. The synthetic tests do not sign in or grant
permissions. They check wrong tenants, unavailable administration commands,
wrong or duplicate recipients, external mail users and untrusted signatures.
The existing application, observation gateway and manual reporting worker were
not redeployed.

The next mail prerequisite must be applied through an authorized Exchange
administrator session, not through the portfolio device-sign-in client:

1. Verify the tenant and existing mailbox against the directory ID above. Preview
   a management scope filtered by
   `ExternalDirectoryObjectId -eq '8617fd1c-2280-40f2-92dd-45686e9d686c'`
   and require exactly the selected mailbox before assigning any permission.
2. Register the Exchange service-principal pointer using the dedicated mail
   identity's client ID and service-principal ID above. Assign only
   `Application Mail.Send` using that exact custom resource scope. Do not add
   `Mail.Read`, `Mail.ReadWrite`, Exchange full access or tenant-wide Graph
   `Mail.Send`. Existing grants must be inspected before any change; no unrelated
   grant is authorized for removal by this procedure.
3. Verify scoped authorization for the approved mailbox and denial for a real
   out-of-scope mailbox. Independently recheck Entra application grants because
   Exchange scope tests exclude separately granted Entra permissions. Allow for
   permission propagation; do not test denial by sending email as another user.
4. Attach the mail identity only to the isolated, reviewed delivery worker;
   verify its own identity and no source access, then perform one approved report
   submission. Confirm actual receipt and retained evidence separately before
   claiming delivery or enabling the daily schedule. Uncertain sends require
   reconciliation, not automatic resubmission.

Exchange resource scopes constrain which mailboxes the identity can send from;
the fixed recipient is enforced by the reporting application. The plan follows
[Microsoft's Exchange application RBAC guidance](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac)
and [Graph sendMail acknowledgment semantics](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0).
These documents describe the intended setup, not live authorization acceptance.

#### Administrator handoff

An administrator with the required Exchange commands must perform this one-time
setup in tenant `0578a50b-a7a1-40a4-905b-8b20dba84124`. Do not elevate the owner
or the agent identities to general Exchange administration just to send this
report. The existing single-mailbox approval is sufficient for the intended
grant; the missing item is an authenticated administrator with effective access.

Use scope name `WealthRyse-Specialist-Owner-Mail` and assignment name
`WealthRyse-Specialist-Owner-MailSend`. Before the creation commands below,
inspect existing service principals, the named scope and all assignments for
the mail identity, and independently verify that it has no unscoped Entra
application permissions. Require exactly the intended mailbox in the scope
preview. If any object already exists, inspect it instead of blindly recreating
or replacing it. After an ambiguous command outcome, read back before any retry.

These commands are a handoff, not a record of executed or tested live grants:

```powershell
# In an authenticated administrator session, first confirm the expected tenant.
# Inspect existing objects and permission assignments before creating any object.
$mailScopeFilter = "ExternalDirectoryObjectId -eq '8617fd1c-2280-40f2-92dd-45686e9d686c'"
$mailScopePreview = @(Get-Recipient -Filter $mailScopeFilter -ResultSize 2)
if ($mailScopePreview.Count -ne 1 -or
    [string]$mailScopePreview[0].PrimarySmtpAddress -ne 'anthony.ying@wealthryse.ai' -or
    [string]$mailScopePreview[0].RecipientTypeDetails -ne 'UserMailbox') {
    throw 'Scope does not identify the exact approved user mailbox'
}

# Only for absent objects after the checks above:
New-ServicePrincipal -AppId 'cc80dc26-9f62-4231-bc63-dbe8276a0eca' -ObjectId 'eb172a60-a283-4ad0-be96-515a60561615' -DisplayName 'WealthRyse Specialist Daily Mail'
New-ManagementScope -Name 'WealthRyse-Specialist-Owner-Mail' -RecipientRestrictionFilter $mailScopeFilter
New-ManagementRoleAssignment -Name 'WealthRyse-Specialist-Owner-MailSend' -Role 'Application Mail.Send' -App 'eb172a60-a283-4ad0-be96-515a60561615' -CustomResourceScope 'WealthRyse-Specialist-Owner-Mail'

Test-ServicePrincipalAuthorization -Identity 'eb172a60-a283-4ad0-be96-515a60561615' -Resource 'anthony.ying@wealthryse.ai'
```

The administrator must also test a real, different existing mailbox and verify
that it is out of scope. Neither test sends email. Return the scoped assignment
and authorization evidence for independent review; do not return passwords,
tokens or mailbox contents. Permission propagation and a separate real delivery
trial still precede email or schedule activation.

Local evidence is in `.work/specialist-mail-20261003`. The protected completion
receipt is
`acceptance/gateway-read-mail-identity-completed-e242860ce3534c11310c8a938f869dfac93c9f5a2f89d106c4790a93dca9a58d.json`.
The live read-only Exchange preflight, including the script and pinned package
fingerprints, is retained separately at
`acceptance/gateway-read-mail-exchange-preflight-1c69d040fe71706f5562687b703caab92d14b8d980917fcb26492c2c2056c341.json`.
The source-policy check passed with 58 contiguous migrations and no prohibited
paths or detected secret patterns. Live website delivery, email delivery and
18:00 reporting remain disabled pending their respective acceptance checks.

### Isolated authenticated gateway rollout 2 October 2026

The dedicated observation API and public owner client were registered in Entra
without changing the existing website registration, authentication policies or
tenant-wide consent. A real owner-completed device sign-in verified the Microsoft
signature and registered client. Before enrollment the same authenticated request
was denied with HTTP 403; a modified signature was rejected with HTTP 401. Tokens
were kept only in process memory and discarded after the acceptance test.

The isolated gateway uses separate existing restricted observation, authority and
audit identities and database logins. Startup verifies all three fresh logins and
protected audit storage before serving requests. Only performance-series reads
are deployed. One process and one replica enforce one active request, no waiting
queue and six requests per user per minute. HTTPS is required, and missing,
spoofed or invalid credentials are denied. Live configuration and startup checks
passed. The main website, existing jobs, ingestion schedules and retries were
not changed.

The sanitized ACR build contained code and synthetic tests, not financial files,
credentials, report outputs or repository history. The first build failed before
publishing because its Dockerfile path resolved outside the prepared context.
The corrected build passed 31 gateway and admission tests and published the
pinned isolated image. The deployment, source manifest, registry and sign-in
acceptance were retained with protected version-specific readback.

A subsequent typed enrollment inserted only two authority rows. It permits the
observation principal to read U16088571 performance history until 9 October 2026
at 22:35:58 UTC, with background work disabled. Fresh independent authority-reader
acceptance passed. No source rows, previous grants or roles were modified. Azure
completed removal of the temporary administrator-capable enrollment worker; its
definition and protected intent/completion receipts remain recoverable.

The reporting identity also independently accepted the expanded evidence registry
against all four existing manual activity records. Its closed daily window was
empty; no report records, deliveries or emails were created. The installed manual
reporting worker definition was unchanged.

The enrolled read and independent verifier were accepted in the subsequent
3 October checkpoint above. The first test deliberately discarded its token;
the later successful sign-in supplied a fresh token held only in memory. No model
inference, six-agent background runtime, owner Codex session or 18:00 daily delivery
was enabled by this gateway rollout.

Acceptance evidence is retained locally under
`.work/specialist-gateway-20261002` and in the protected checkpoint container.
The accepted image digest is
`sha256:21f183cc51b1502e14f4868db63d0b3b5ce0ac435d638997c827e337eec5ac68`,
produced by ACR run `ck7r`. The registered source manifest is
`dbe5ee61a08a6b2f350949c03463c5d080fd4f1f4cf849aa2a966817e410f247`.
Protected receipt identifiers are:

- Gateway deployment: `acceptance/specialist-gateway-d71dc3e0f08a893da82ae66ea254cdacc680d200396bf1141361b2e4b8b1be98.json`.
- Enrollment intent: `acceptance/gateway-enrollment-intent-efb08f292b0ba71e2febd260331df24117627c6977db1ec756f2d410a7eb9724.json`.
- Enrollment completion: `acceptance/gateway-enrollment-completed-aad7bf5c637b0e621a5852d494c84b51ecf52bec262113ce0fa14ff06eccd4a6.json`.
- Enrollment-worker removal: `acceptance/gateway-read-enrollment-cleanup-5dd86377deda3dbfc193203551e2c0c3a8909428469e2222dee2a979c9bbb759.json`.
- Independent registry preview: `acceptance/ibkrv2stg-agent-report-pabza11-86e541058044bacf23e07dc2d34863fa7a5b9773851cc41cf2a8e803cc38bdd8.json`.

The full local suite passed 1084 tests; this is code acceptance, not proof of
autonomous operation or completion of the eight original workstreams.

### Restricted reporting worker identity 2 October 2026

Prepared a dedicated managed identity, `ibkrv2stg-specialist-report-identity`,
and a separately generated database credential stored only in Key Vault under
`database-specialist-report-url`. The identity's new grants are registry pull
on the exact existing registry and secret read on that exact new secret. The
later protected reporting rehearsal added two read-only grants on the exact
audit container, as recorded below. No raw-storage or resource-group write
permissions were granted.
The initial overlong provisioning-job name was rejected; preparation resumed
with a shorter name after checking the existing identity, active secret metadata
and grant scopes. No credential rotation or identity replacement was performed.

Provisioning execution `ibkrv2stg-agent-report-provision-snr1ekw` created
`ibkr_specialist_report_login`, limited to two connections, without superuser,
database/role creation, replication or RLS bypass. It can explicitly assume
only the reporting capability granted by this provisioning step; it receives
neither ADMIN nor INHERIT on that membership. The verifier uses an actual fresh
login, not an administrator's SET ROLE session. It checks unrelated table and
column grants, callable privileged functions, schema creation, forced RLS and
the expected audit/report table permissions. All checks passed. On a failed
post-creation verification, the provisioner disables only its newly created
login; an existing login is never implicitly rotated, replaced or disabled.

The manual job `ibkrv2stg-agent-report` is installed with the dedicated managed
identity, one restricted Key Vault reference, an immutable image digest, exact
source payload and zero retries. Its installed entrypoint is access verification
only: no build switch, schedule, report generation, email or raw-source access.
This is not yet the scheduled daily-report producer or an authenticated audit
ingress service. The provisioner receipt was retained and read back at
`specialist-audit-checkpoints/acceptance/ibkrv2stg-agent-report-provision-snr1ekw-7326b04b3ad242086262b814dbecf70544f4ad607600017404302add2aa1d15f.json`.

Execution `ibkrv2stg-agent-report-h7gz63v` succeeded under the dedicated managed
identity. Verification matched the executed image, source payload, identity,
environment and live Key Vault binding. Its payload SHA-256 is
`4dbfabf7a6b57521538a3f0cfaf143b8a88fc7a4e16868f36e5d03039c93f131`.
The authenticated run confirmed access checks passed, zero reports created and
zero messages sent. Its receipt was retained with exact-byte readback at
`specialist-audit-checkpoints/acceptance/ibkrv2stg-agent-report-h7gz63v-7e5260210147262d6e5971275ec447f0da0c80f50418b5eeb3927c57cbb9ae96.json`.
After that verification and terminal-execution checks, the exact temporary
`ibkrv2stg-agent-report-provision` job was removed. Its reviewed definition,
Azure logs and protected receipt remain available; the restricted login,
credential, identity and manual worker were retained.

Local validation: 911 tests passed. The source-policy scan covered 488 source
files and 57 migrations without prohibited paths or matched secret patterns.
At this checkpoint, remaining integration included authenticated audit ingress,
protected-archive verification under the reporting identity, the full report producer,
website inbox release, email receipts and missed-delivery alerts. The 18:00
Hong Kong schedule remains disabled; Entra policy review remains deferred.

### Protected reporting rehearsal 2 October 2026

The dedicated reporting identity received Storage Blob Data Reader and ARM
Reader only on `ibkrv2stgauhecwc4itaoo/specialist-audit-checkpoints`. Both exact
assignments were read back and checked against the registered identity and
container. These grants provide neither archive writes nor raw-source access.
The storage adapter selects that user-assigned identity explicitly, uses fixed
Azure destinations, and has no upload or repair operation. It checks private
access and legal-hold protection before and after bounded, exact-byte reads.
Readiness also requires the pinned version and hash of a retained acceptance
receipt, even when the audit ledger is empty.

The report builder now rejects missing or unverified archive readiness before
and after checking its ledger slice. Previously, an empty ledger could skip
archive verification and still set the verification flag. The read-only preview
uses a PostgreSQL READ ONLY transaction, renders the same deterministic message
format, and does not enqueue or publish a report. Regression tests cover an
empty ledger, absent storage, lost protection, incorrect hashes/versions, bounded
reads, explicit managed-identity selection and unexpanded permission scopes.

The first launch request was rejected with HTTP 400 and created no execution.
The launch payload was compressed and empty/default ARM fields were removed.
Execution `ibkrv2stg-agent-report-akiyray` then failed at managed-identity
authentication. Explicit selection of the registered client ID corrected the
authentication path; no developer/owner credential fallback was introduced.
The failed run and its local receipt remain available for investigation.

Execution `ibkrv2stg-agent-report-87gnobq` succeeded with reviewed payload SHA-256
`4d2ad9b6268cc98b9e22d760baa89bfeb27ccceeaffa84ad7b0d141cd8138d97`.
It verified restricted database access and protected archive reads, then rendered
the closed 2 October Hong Kong reporting window. That window contained zero
specialist audit events. Report and delivery inventories were unchanged: zero
reports created, zero queued channels and zero messages sent. The executed
image, payload, credential reference and resources matched the reviewed override;
the installed manual worker configuration remained unchanged.

The successful dispatch and verification receipt was stored with exact-byte,
pinned-version readback in
`specialist-audit-checkpoints/acceptance/ibkrv2stg-agent-report-87gnobq-63b24f72f0e179d0e98276768d3ae66599b8663b63df84d695fd028ccba3803e.json`.
The protected blob version is `2026-10-02T17:31:08.3865801Z`.

This is live acceptance of protected read access and empty-window report
compilation, not proof that every system action is captured or that daily reports
are being delivered. Authenticated audit producers, committed report production,
website release, email provider/receipts, missed-delivery alerts and the 18:00
schedule remain to be integrated and accepted. The six specialists and owner
Codex runtime are not yet operating autonomously across the resource group.
IBKR source data, financial reports and existing job schedules were not changed.

Local validation: 923 tests passed; the source-policy check scanned 493 source
files and 57 contiguous migrations without prohibited paths or matched secret
patterns. No broad deployment of the working tree was performed.

### Dedicated audit identity verification 2 October 2026

Created the separate `ibkrv2stg-specialist-audit-identity` and a new credential in
Key Vault, `database-specialist-audit-url`. The credential was generated in memory
and was not printed. Its cloud grants are limited to pulling the pinned registry
image and reading this exact secret. No storage permission, resource-group write
role, public endpoint or scheduled trigger was added.
Its verified principal ID is `60313d53-6387-46d4-9bb8-dc5f8ab925a5` and client ID
is `7135fc5a-3714-41d8-a73a-71a22e56a29a`.

Provisioning execution `ibkrv2stg-agent-audit-provision-ld2c2hc` created
`ibkr_specialist_audit_login`, with at most two connections, no inherited role
access, and no superuser, role/database creation, replication or RLS bypass.
The only capability membership added is explicit SET access to
`ibkr_specialist_audit_writer`, without ADMIN or INHERIT. A fresh credential-based
connection passed verification; this was not an administrator impersonating the
service through SET ROLE. Existing logins are never implicitly replaced or
rotated. A newly created login is disabled if its verification fails.

The verifier checks every role the session login can assume, exact membership
options, and direct privileges of both the login and capability role. It rejects
access to unrelated tables, column-level access, callable privileged functions,
schema creation, audit UPDATE/DELETE/TRUNCATE permissions, missing forced tenant
isolation, and missing or disabled append-only guards. The worker reads metadata
only; it does not insert synthetic activity into the production audit ledger.

The provisioning source hash is
`33864c3e231aadd0c66309f124b17bb83a9e03ceea745aca14037b1cca2b84b0`.
Its receipt was retained and read back at
`specialist-audit-checkpoints/acceptance/ibkrv2stg-agent-audit-provision-ld2c2hc-6ad2bc16e89dd19796060adddf2e9452e6b0f5405aaa6e4af7f8c4ef63e3cca0.json`.

Installed the manual-only job `ibkrv2stg-agent-audit`, using the dedicated identity,
one restricted credential reference, the existing pinned image, one worker and
zero retries. Execution `ibkrv2stg-agent-audit-14pi1gl` succeeded; the exact executed
payload, image and credential bindings were verified. Its payload SHA-256 is
`ae8dbad148fa5cefd56b64b134f7f66ded099bfd65bc19469aed7a2bfbed5dce`.
The result confirms append-only ledger access, verified reachable roles, no
source-table access, zero events written and zero messages sent.
The worker receipt was retained with pinned-version exact-byte readback at
`specialist-audit-checkpoints/acceptance/ibkrv2stg-agent-audit-14pi1gl-91f64c3f99686109c1c4f2a676fc8ca7f2353e5a045ecfc6d031487534c2d556.json`,
version `2026-10-02T18:51:44.8984459Z`.

After the successful dedicated-worker verification, the exact temporary job
`ibkrv2stg-agent-audit-provision` was removed. A separate Azure read confirmed it
is absent and the retained worker remains Manual with zero retries. Its reviewed
definition is retained under `.work/specialist-audit-worker-20261002/`, alongside
local receipts; protected provisioning evidence and Azure logs remain available.
The restricted login, identity, credential and manual worker were preserved.

This establishes the audit service's database authentication and permission
boundary, not authentication of incoming producers or proof of complete activity
capture. Remaining work includes trusted producer identity and scope binding,
protected evidence writes and recovery, then an actual activity-capture trial.
The report producer, website release, email receipts and 18:00 Hong Kong delivery
schedule remain inactive. Entra policy review remains explicitly deferred.

Local validation passed 934 tests. The source-policy scan covered 498 source
files and 57 migrations. IBKR data and existing application/job definitions were
not changed, and no broad release of the working tree was performed. The shared
job verifier gained a trusted expected-definition parameter; its existing
report-worker behavior remains covered by the regression suite.

### Account reader identity rollout 2 October 2026

Installed the dedicated managed identity
`ibkrv2stg-specialist-observation-identity`, with principal ID
`bbe76059-bdd6-4c52-a644-877460a24f7f` and client ID
`c3a6ea22-bb1b-4f5b-af02-b3cbf6f4955c`. Readback verified exactly two Azure
assignments: registry pull on the existing registry and secret read on
`database-specialist-observation-url` only. No storage or resource-group write
permissions were granted. The credential remains in Key Vault and was not
printed or saved in local evidence.

Created the rehearsed `ibkr_specialist_observation_reader` NOLOGIN capability and
the separate `ibkr_specialist_observation_login` with NOINHERIT, a two-connection
limit and no elevated role attributes. The login can explicitly SET only that
capability, without ADMIN or INHERIT membership. Fresh connections require the
fixed database, host and tenant, a READ COMMITTED READ ONLY transaction, short
timeouts, the expected column privileges and unchanged NAV-view hash. Each
connection rechecks the login's direct permissions and reachable roles, as well
as the capability's columns, forced RLS, policies and privileged-function access.
Individual user/account access still belongs to the broker; this is not a claim
of per-user database isolation within the tenant.

The initial provisioning execution `ibkrv2stg-observe-prov-pnccch2` failed with
`InvalidGrantOperation` while the new provisioner attempted an unnecessary
administrator self-grant. Independent read-only inspection
`ibkrv2stg-observe-prov-m0xldfh` confirmed that neither new role remained after
the failed transaction. The corrected provisioner verifies the automatic
ADMIN-only creator membership instead of attempting to re-grant it, consistent
with [PostgreSQL's role-creation behavior](https://www.postgresql.org/docs/17/role-attributes.html).
The original and corrected definitions and failed-execution evidence are retained.

Corrected provisioning execution `ibkrv2stg-observe-prov-3yxd9e7` succeeded and
verified an actual fresh service login, not administrator SET ROLE. Its source
payload hash is
`40c05e5e54d9d73355246c785389f1f6fe12ba12951ffb05c2183da072492540`.
It confirmed the 41-column registry, zero write privileges, eight forced-RLS
tables and unchanged view hash, with no financial rows retrieved. The protected
provisioning receipt was read back at
`specialist-audit-checkpoints/acceptance/ibkrv2stg-observe-prov-3yxd9e7-da4f5ab0c5acf506fc2184196d676e1e3527ec68f31e2e823e80d8a2f583a3fb.json`,
version `2026-10-02T21:56:05.0234729Z`.

The permanent `ibkrv2stg-observe-reader` worker is Manual, single-worker,
zero-retry, 0.5 CPU/1 GiB, with a 180-second limit and the existing pinned image.
It uses only its own managed identity and restricted credential. It has no
ingress, schedule, model call, authority enrollment or financial-row fetch.
Execution `ibkrv2stg-observe-reader-vcelf7n` passed under that identity and its own
credential. Its detailed log arrived after the job had already completed; no
success was accepted from job status alone. Nine LIMIT 0 relation checks passed,
with zero financial rows read, source/authority rows written, agents enabled or
messages sent. The verified worker source hash is
`d801769f1a3b31469f0dca0c207fa5cca4b8359375524ddf47cd7cc9e0c64a58`.
Its protected acceptance receipt was read back at
`specialist-audit-checkpoints/acceptance/ibkrv2stg-observe-reader-vcelf7n-21803881cb313dd439e6d2ce1665b9192a98dbf71c53b4578668420805320d34.json`,
version `2026-10-02T22:00:53.1812633Z`.

After both receipts were protected, the exact temporary
`ibkrv2stg-observe-prov` job was removed. A separate ARM read confirmed its absence
and revalidated the permanent worker's Manual, zero-retry configuration. The
temporary provisioner can be recreated from its retained definitions; no
database roles, credential, permanent worker, user data or evidence were removed.
Definitions and local receipts remain under
`.work/specialist-observation-worker-20261002/`.

Local validation passed 1064 tests, including 13 new runtime/deployment tests.
The source-policy scan passed for 534 source files and 58 migrations. IBKR source
data, existing application/job definitions and Entra sign-in settings were not
changed. This rollout does not enable the gateway, six specialists, owner Codex
runtime or 18:00 Hong Kong website/email delivery. Live gateway authentication,
audited authority enrollment and protected observation-evidence integration
remain necessary before a permitted account-read trial.

### Account reader permission rehearsal 2 October 2026

The first restricted observation capability now has an explicit column registry
in `specialist_observation_access.py`. It supports the authorization checks and
`get_performance_series` tool without granting access to raw-report paths,
Flex credentials, authority changes or unrelated analytics payloads. This is a
proposed capability, not an enabled agent. Tenant isolation is enforced by the
database; individual user/account entitlement remains enforced by the broker.

Live execution `ibkrv2stg-bootstrap-ubicoyd` passed using the existing isolated
manual diagnostic path. It created one temporary NOLOGIN capability inside a
transaction, granted only the registered columns, switched into the restricted
role and checked actual PostgreSQL permissions. The result was 41 permitted
columns, nine successful LIMIT 0 relation checks, four rejected forbidden-column
checks, eight tables with forced RLS and exact expected policies, no broad table
grants, no privileged application functions, and no write or schema-creation
rights. It verified that the unified NAV view uses caller privileges. No
financial rows, raw statement contents or credentials were retrieved.

The transaction rolled back all temporary grants and the trial role; a separate
post-transaction lookup confirmed the role was absent. No source rows, database
policies, view definitions or authority records were changed. The existing job
definition remained unchanged, including its zero-retry setting. This is a
SET ROLE permission rehearsal, not authentication as a separately provisioned
service login and not an end-to-end audited account-read trial.

The execution payload SHA-256 is
`7b440ed8650b0a65326f68f6aed03808f0f054f7f58a7b0a24293b7cfe7507fc`.
The unchanged job-settings hash is
`8104fa7ee4b3235616cc9c5fc201433ee3c08c5e266f6d5f389aeaa7fc03407a`.
The observed NAV-view definition hash is
`cb08102f309992e2cd7dc097a9f0ad7a361480fbf67b1a27303ece79168c329d`;
this records the definition observed during rehearsal and is not by itself a
complete semantic review of that view.
The verified acceptance receipt is stored at
`specialist-audit-checkpoints/acceptance/ibkrv2stg-bootstrap-ubicoyd-11106baf61a0d099538679f093077eed64429174763ca28433e3f3a9de9600ad.json`,
version `2026-10-02T21:38:55.4857695Z`. The archive helper checks the exact trial
outcome and rollback evidence before accepting this new receipt type.
Local dispatch and readback records remain under
`.work/specialist-observation-reader-20261002/`.

A read-only Entra registration query found `IBKR Portfolio Platform Staging`,
application ID `4694ed1b-aa08-406a-9e3f-b46c57bd342d`, with no identifier URI or
delegated API scopes. Its sign-in settings were left unchanged. This does not
establish a tenant-wide absence of other applications; a dedicated gateway API
registration and real delegated-token acceptance remain to be implemented.

All 1051 local tests passed, including nine new capability/receipt tests. The
source-policy check passed for 529 source files and 58 contiguous migrations.
Next: provision and verify the restricted account-reader identity and connection,
then integrate gateway authentication, audited authority enrollment and protected
observation evidence. The six autonomous specialists, owner Codex runtime and
18:00 Hong Kong daily website/email delivery remain inactive. Alpha World MS
onboarding remains paused; the Entra policy review remains deferred.

### Interactive gateway source acceptance 2 October 2026

Implemented `agent_gateway_auth.py` and `agent_gateway.py` as a separate,
disabled-by-default interactive observation service. It is not mounted on the
website, deployed to Azure or connected to production accounts. No Azure
resources, database records, grants, schedules or IBKR source data changed in
this checkpoint.

The token validator requires a signed Entra v2 delegated access token for a
dedicated API audience, from the configured tenant and registered frontend
client, with the required delegated scope. It checks signature, issuer,
audience, expiry, issuance and not-before times. Signing keys come from the
fixed tenant endpoint, with bounded responses, no redirects and no stale-key
fallback after refresh failure. Duplicate JSON members, unsupported algorithms,
token-selected key URLs, app-only tokens and missing scopes are rejected.
These controls follow Microsoft's guidance on
[validating access-token claims](https://learn.microsoft.com/en-us/entra/identity-platform/claims-validation).

The caller cannot supply its database tenant, specialist identity, user identity
or roles in the request. The server binds one reviewed specialist principal;
the signed object ID selects a current active database user. Email and role
claims are not authorization inputs. The broker still independently checks
current specialist authority, user roles and account access. A delegated user
token does not authenticate a background specialist; that requires a separate
service-identity and delegation integration.

All 26 gateway tests passed using ephemeral signing keys and synthetic records.
The integrated test reaches the actual observation broker, database-user
directory and deterministic performance-series tool. It produces synthetic NAV
100.25 and checks intent/completion receipts through a test audit adapter.
Other tests verify rejection of forged or expired tokens, request identity
overrides, revoked users, removed account access and unregistered agents.
An intent-archive failure prevents portfolio reads; a completion-archive
failure suppresses the HTTP result. This is local adapter integration, not a
live Entra-token, PostgreSQL or protected-storage acceptance claim.

The full suite passed 1042 tests. Source-policy validation passed for 526 source
files and 58 contiguous migrations. PyJWT with cryptographic support is now an
explicit dependency; the refreshed lock retains the already selected 2.15.1
package version. The only full-suite diagnostics were existing expected
negative-test messages and deprecation warnings.

Before enabling the gateway, remaining work includes the dedicated Entra API
and client registration/scopes, a real delegated-token acceptance test, isolated
deployment and request-rate/concurrency controls, a restricted account-reader
identity, audited authority enrollment, observation-evidence registration and
the protected live audit integration. Current gateway logs are sanitized but
are not durable audit receipts; early HTTP rejections are not yet in the
protected ledger. No claim of complete activity capture is made.

The six autonomous specialists, owner Codex runtime and scheduled 18:00 Hong
Kong website/email delivery remain inactive. The Entra policy review remains
explicitly deferred, and Alpha World MS onboarding remains paused.

### Authority reader identity acceptance 2 October 2026

The new manual worker `ibkrv2stg-authority` passed a live acceptance check using
its dedicated managed identity, `ibkrv2stg-specialist-authority-identity`, and the
new restricted database login `ibkr_specialist_authority_login`. Its credential
is stored in Key Vault as `database-specialist-authority-url`; no credential was
printed or saved in local deployment evidence.

The managed identity has two verified RBAC assignments: registry pull on the
exact existing registry and secret read on that exact new secret. No storage,
resource-group write or administrator-secret access was granted to this identity.
Its principal ID is `15cc67b9-ba8d-473e-b537-20c0eaace8c1` and client ID is
`d427801c-4a16-441f-9539-e73283ee043e`. The subscription assignment inventory was
filtered to this principal and checked against the two expected scopes; this is
not a claim that the deferred Entra policy review is complete.

The database login is NOINHERIT, has a two-connection limit and no elevated role
attributes. It can explicitly SET only the SELECT-only authority-reader
capability, with no ADMIN or INHERIT membership. A fresh connection starts a
READ COMMITTED, READ ONLY transaction, binds the fixed tenant and applies short
statement/lock timeouts. Every factory invocation rechecks direct and reachable
permissions, forced RLS and exact tenant policies before permitting a read.
Neither the login nor its capability can change authority, access unrelated
tables, execute application security-definer functions or create schema objects.

Provisioning execution `ibkrv2stg-authority-provision-1tzi91m` succeeded after
testing a fresh login, not merely SET ROLE under the administrator. The
provisioner disables a newly created login if its post-creation checks fail and
refuses implicit changes to an existing login. Its verified source payload is
`ba64a2658d2e49856fa14de0a6435adb0263d8102e00f187dfb7697e264c8d51`.
The protected provisioning receipt was read back at
`specialist-audit-checkpoints/acceptance/ibkrv2stg-authority-provision-1tzi91m-00bf80b9c0099ec96ab1693913d8e4091aa40a7c0f555d7a9e3d8357badaee83.json`,
version `2026-10-02T21:05:36.6689708Z`.

Independent worker execution `ibkrv2stg-authority-is0tq91` succeeded under the
new managed identity. It verified zero tenant-visible control/grant rows and
passed an unregistered principal through `PostgresAuthoritySource` using a
second fresh restricted connection. The result correctly remained disabled,
with no grant. No synthetic principal was inserted. The verified worker payload
is `8837865be9a91d1c346885ff366a6eee5561ef4306a538aac368c14ba1bb5795`.
Its protected receipt was read back exactly at
`specialist-audit-checkpoints/acceptance/ibkrv2stg-authority-is0tq91-300327b47355a653a95f987d06874cb7144475f332e57338ce9c5281c531d7b1.json`,
version `2026-10-02T21:08:19.6774753Z`.
The worker is Manual, single-worker, zero-retry, 0.5 CPU/1 GiB, with a 180-second
execution limit and the existing pinned image. There is no HTTP ingress, agent
enrollment, model call, source read, authority write, report publication or email
delivery in this worker. Existing application/job definitions were not replaced.

After successful worker acceptance, the exact temporary
`ibkrv2stg-authority-provision` job was removed. A separate ARM read confirmed it
is absent and revalidated the permanent worker's Manual, zero-retry definition.
The temporary job can be recreated from its retained definition if needed;
the restricted identity, credential, login, permanent worker, logs and protected
receipts were preserved. No user data were removed.

All 1016 local tests passed, including 14 authority-runtime/deployment tests.
The source policy check passed for 523 source files and 58 contiguous migrations.
Local definitions and execution evidence are retained in
`.work/specialist-authority-worker-20261002/`. No broad source-tree release was
performed, and IBKR source data were unchanged.

Next: connect and verify the authenticated caller gateway and audited authority
administration, register observation evidence, and run a permitted account read
through the full broker and protected audit path. The six autonomous specialists,
owner Codex runtime and 18:00 Hong Kong website/email delivery remain inactive.
The earlier dormant-only installer intentionally rejects an installation with
new service membership; runtime verification is now the appropriate access check.

### Dormant authority deployment 2 October 2026

Migration 058 is now installed in the live `ibkr_staging` database. It adds only
the observation controls and grants tables. Both force tenant isolation, with
the stop switch on and individual grants disabled by default. No control or
grant rows, service credentials, enrollment or runtime were created.

The separate `ibkr_specialist_authority_reader` capability is NOLOGIN and
NOINHERIT, without elevated role attributes. Its effective privileges were
checked for SELECT-only access to the two authority tables, no unrelated-table
or column access, no executable security-definer functions, and no schema
creation rights. It has no executable or inherited memberships. PostgreSQL's
ADMIN-only lifecycle link to the installing administrator remains intact.
This is a prepared reader capability, not an authenticated service login.

The reviewed installer uses one transaction for the migration, migration record,
role and grants. Changed/partial existing installations are rejected rather
than silently repaired. Validation failure before commit rolls back the new
objects. A separate read-only transaction verifies committed state. An existing
valid installation is checked without repeating DDL or granting new access.

Live evidence:

- Preflight `ibkrv2stg-bootstrap-3r9wsus` succeeded and found no existing target
  objects. It performed no installation.
- Installation `ibkrv2stg-bootstrap-knzw32n` succeeded, with committed readback,
  zero tenant-visible authority rows and all permission checks passing.
- Separate read-only verification `ibkrv2stg-bootstrap-jwc545x` succeeded and
  returned `already_installed`, without changes.
- Every execution verified the dispatched source/image/credential references
  and unchanged installed diagnostic-job settings. No website image, existing
  schedule, IBKR data, financial report, audit event or daily-report row was
  changed by this installation. No agent or email delivery was enabled.

The migration SHA-256 is
`f89488834b9a66f3a53f75dca3ff97bd557623da8da60743c421e8b25ebffbc0`.
The installation payload SHA-256 is
`e46c17af812bacd4337b1e1b8b25e3ea847fa8b3074555d010428fdb43ff892a`;
the preflight/read-only payload differs only in its explicit apply setting.
The protected installation receipt was read back exactly in
`specialist-audit-checkpoints` at
`acceptance/ibkrv2stg-bootstrap-knzw32n-9231c5af4fd64dae579a48e6029bc8bf495e3e88c7f6c4d5c97b16f9abe2aafc.json`,
version `2026-10-02T20:52:13.6779342Z`.
The separate read-only verification receipt was also protected and read back at
`acceptance/ibkrv2stg-bootstrap-jwc545x-79ac6c980035be6256495a2da049d9cc9d1e10bc5de10a58b93ca1a4315c6627.json`,
version `2026-10-02T20:54:44.8737899Z`.
Local dispatch and result evidence is under
`.work/specialist-authority-install-20261002/`.

All 1002 local tests passed, including 11 installer tests for exact scope,
permission expansion, existing enrollment, rollback, repeat-run behavior and
post-commit verification. The policy scan passed for 518 source files and 58
contiguous migration sources. No broad working-tree release was performed.

Next: provision and independently verify a dedicated authority-reader login and
runtime, connect trusted caller authentication, register observation evidence,
then exercise an authorized account read through the full broker/audit path.
Production enrollment and authority changes also need audited administration.
The six autonomous specialists, owner Codex runtime, website daily-report release,
email delivery and 18:00 Hong Kong reporting schedule remain outstanding.

### Observation authority and audit integration 2 October 2026

The disabled observation broker previously emitted ordinary logs only. It now
requires a separate protected audit adapter. Before a deterministic tool runs,
the adapter retains bounded detail, commits the intent and verifies protected
event storage. Completion must also pass that handoff before the broker returns
a result. Missing, mismatched or unavailable audit receipts stop execution or
suppress delivery. There are no immediate retries or substitute operation IDs.

Evidence contains scoped tool/account metadata and identity, request and response
fingerprints, not raw analytics results, prompts, credentials, email addresses or
exception bodies. The analytics reader and audit writer use separate connections.
The broker rechecks agent authority and user access around archive operations as
well as tool execution. A computation-completed event is not a client-delivery
receipt: revocation during final archival records a denial and suppresses the
result. Requests rejected before a registered audit intent still use ordinary
sanitized logs; complete rejection capture remains an integration requirement.

`PostgresAuthoritySource` reads server-owned control/grant rows on a new guarded
read-only connection for every check. It verifies exact tenant/principal scope,
role/tool restrictions, explicit enabled/stop controls, server-observed expiry,
policy version and bounded account/resource lists. It cannot create or change a
grant and does not authenticate tokens. Trusted caller authentication, enrollment
administration and recorded authority changes must be connected separately.

Migration 058 defines two forced-tenant-isolation tables, with the stop control
on and grants disabled by default. It inserts no grants and grants no service
access. It strips named default permissions as well as PUBLIC access. At this
earlier integration checkpoint it was source-only for production; the later
dormant deployment is recorded above. No identity enrollment was installed.

Azure execution `ibkrv2stg-bootstrap-rwlzvnu` passed 12 isolated database checks:
disabled defaults, stop control, exact grant loading, cross-tenant exclusion,
restricted reader identity, inability to change authority or create objects,
forced RLS on both tables, uncached revocation, server expiry, unknown-tool
rejection and cleanup. The exact newly created `specialist_authority_trial` schema
and `ibkr_specialist_authority_trial` role were removed after testing; only
disposable synthetic test data were removed. The migration source and acceptance
evidence remain available to reproduce the test. Installed job settings were
unchanged. The reviewed payload SHA-256 was
`237987a734b371fcfed8bd42b4dd16f0267ce17310eb0ba17d36d965d5e9a342`.

The acceptance receipt was retained and read back exactly in
`specialist-audit-checkpoints` at
`acceptance/ibkrv2stg-bootstrap-rwlzvnu-8644fdbe1fce6244c23f018f4da57c9bdd4357e9ddb1b3a926a78caf601d8a72.json`,
version `2026-10-02T20:35:59.2723381Z`.

All 991 local tests passed. Coverage includes the existing deterministic tool
through the actual append/commit/archive functions with synthetic connections,
audit failure before execution, completion-archive failure, conflicting retries,
revocation during both archive phases and sanitized failure records. This local
adapter integration is not a claim of a deployed specialist observation service.
Final local hardening also covers malformed caller/tool fields and correctly
identifies a failed terminal audit write in sanitized logs; these final broker
changes have local acceptance only, not a live authenticated gateway trial.
The source policy check covered 516 files and 58 contiguous migration sources;
only migrations through 057 were installed at that checkpoint.

The next work identified at that checkpoint was to deploy the dormant authority
store with a dedicated reader, connect and
verify the authenticated caller gateway, register the new observation producer
and its detailed evidence schema, then exercise an authorized live account read
before enabling a specialist. The current reporting evidence registry still
accepts only the two prior manual inspection builds; it has not been broadened
to accept unregistered observation records. The six autonomous specialists,
website report publication, email sender and 18:00 Hong Kong schedule remain
inactive. No IBKR source data or financial report was changed.

### Registered evidence reporting checks 2 October 2026

Closed a reporting validation gap: archived event records were checked, but the
daily compiler did not require the detailed evidence referenced by each event.
It now requires a verified detail receipt bound to the event's evidence hash,
including the previous-period boundary on otherwise empty days. Missing, altered
or unregistered detail prevents report persistence and delivery queue creation.

The read-only evidence adapter validates the protected content hash, exact schema,
tenant, operation, phase, producer principal, job, database login, role, action,
scope and reviewed source manifest. It rejects unknown fields and distinguishes
boolean values from integers. Storage protection is checked before and after
detail reads. It cannot repair or upload evidence.

Registry `specialist-evidence-v1` initially recognizes only the two independently
accepted builds of the manual audit permission-inspection producer. Registration
requires a reviewed source change; event payloads cannot enroll themselves.
This is an evidence acceptance registry, not a general producer-authentication
service or an execution grant. Authenticated producer enrollment and six-agent
runtime integration remain outstanding.

The manual reporting rehearsal additionally inspects a repeatable-read, read-only
snapshot of the current ledger, capped at 1,000 events. It rejects larger
inventories rather than silently truncating; pagination is required before this
rehearsal can support larger histories. This inventory is explicitly distinct
from the closed daily reporting window and never advances its cutoff.

Live execution `ibkrv2stg-agent-report-tzxihfk` succeeded using the restricted
reporting identity. It verified all four current event details against the
registry, without database writes. The separate closed 2 October report preview
correctly contained zero events; the four current events belong to the still-open
3 October Hong Kong window. It created no reports or delivery rows and sent no
messages. Source-table access and source-mutation authority remained absent.
The installed manual job and its schedule were unchanged; the new reporting
checks ran as a reviewed per-execution override, not a scheduled-service release.

The payload SHA-256 was
`bfbfe5ecf3c96c878eb9add7c4dfaf03d0766ad1b0fd87d40996eb4728d8e48d`.
The acceptance receipt was retained and read back exactly in
`specialist-audit-checkpoints` at
`acceptance/ibkrv2stg-agent-report-tzxihfk-db4526490dfba5164b8c89c2fc1f7fc59c383a65ed5601c4f5008815fb052fd8.json`,
version `2026-10-02T19:45:16.0760031Z`.

Local validation passed 966 tests. New regression coverage includes unknown
producer/build/scope claims, altered detail bytes, unexpected fields, wrong
JSON types, lost protection, invalid receipt acknowledgements, empty-window
boundaries, cross-tenant requests and bounded inventory checks. The source-policy
scan covered 510 files and 57 contiguous migrations without prohibited paths or
secret patterns. The isolated synthetic report-builder adapter remains explicitly
synthetic; it is not used as live storage or producer evidence.

### Interrupted activity capture recovery 2 October 2026

The narrow permission-inspection producer now has a deliberate interruption
rehearsal. It is restricted to the existing read-only inspection action; it is
not a recovery mechanism for financial writes or infrastructure mutations.
The first execution exits unsuccessfully after the start event commits and
before its protected archive copy is written. The next explicitly dispatched
execution uses the same operation, source and configuration and repairs that
handoff. The installed manual job definition and zero-retry policy remain intact.

Operation `f6b76b3a-8140-482e-8b7d-eeb85d2a7191` was exercised live.
Execution `ibkrv2stg-agent-audit-niq9ul6` reported the planned interruption and
exactly one committed start event, sequence 3. Execution
`ibkrv2stg-agent-audit-z8myhcn` then succeeded with two events for this operation,
sequences 3 and 4. Its start hash remained
`25c11068b9500b7c1ec7c4b0243bcd5f570d6f910e67fde08d42e1f120c36d46`.
Its completed event hash is
`64e39e794b7f66b9df6990ecc6057762c9713498a7eae542d4171981ca67d32d`.
Exact event replay also passed without creating additional rows.

Both executions used payload SHA-256
`e501118c608d912976a24c06d522ab76675085c33d9d4702a9d4b1f7e8f63768`
and source-manifest SHA-256
`6ce56d1f26c472c03e39f82397c380e7d0e77b69fdc6f202e36e051a3956334b`.
The combined failed-predecessor and successful-recovery receipt was retained in
`specialist-audit-checkpoints` and read back exactly at
`acceptance/ibkrv2stg-agent-audit-z8myhcn-bab2101e3a8f062592abb1f3037e151f7fb7a2230efabcc1b112e2ecb2f98e2e.json`,
version `2026-10-02T19:33:56.1826126Z`.

Independent reporting execution `ibkrv2stg-agent-report-o9p465m` passed with
two verified events and two verified detail files, with zero database writes.
Its payload hash was
`3686919ad277c2f2ef07a558505fd37b12c5b32765bfee621be0541c8c4bd739`.
Its receipt was retained and read back at
`acceptance/ibkrv2stg-agent-report-o9p465m-2377d7f457a955ecac0ed3c253c1473437dedc7a953ea17d99ff7ca9ae87fa28.json`,
version `2026-10-02T19:35:53.2759521Z`, in the same protected audit container.

Local validation passed 956 tests. The policy check covered 508 source files
and 57 contiguous migrations without prohibited paths or secret patterns.
New tests bind the failed and recovered executions, reject changed intent
hashes and verify that the interruption cannot falsely acknowledge an archive
write. This rehearsal does not establish recovery of arbitrary external effects,
automatic orphan detection, general producer authentication or full system capture.

Daily-report preparation still requires a closed 18:00 Hong Kong window. These
events belong to the 3 October window; they must not be backdated into the
2 October report. Report publication, website release, email delivery and its
receipts remain separate acceptance work. No email was sent, no schedule was
enabled and no IBKR source data or financial report was changed.

### Protected activity capture trial 2 October 2026

The dedicated audit identity now has Storage Blob Data Contributor and ARM
Reader only on `ibkrv2stgauhecwc4itaoo/specialist-audit-checkpoints`. Exact scope,
principal and role assignments were read back. These grants do not cover raw
IBKR containers or authorize removal of the audit container's legal hold. The
application adapter exposes create-only evidence storage and exact bounded
readback, with private-access and legal-hold checks before and after storage.
The role itself has blob mutation operations, but the existing legal hold blocks
overwriting/deleting retained data; no such mutation was attempted on real evidence.

Implemented one fixed producer: a trusted manual audit-database permission
inspection. The reviewed payload binds the audit service identity, database
login, tenant, platform-operations role, inspect action and exact scope. It
does not accept model/browser-supplied SQL, role, account, timestamps or evidence
bodies. The deployment records an operation intent before launching; recovery
must explicitly reuse that operation and the same source/configuration hashes.
This is not a general remote producer-authentication API.

For each phase, detailed evidence is retained before the database event is
inserted. The event must then commit and pass protected archive readback before
the operation proceeds. Repeating an identical event preserves its original
sequence, timestamp and hash. Changed retries and archive collisions fail;
existing evidence is not overwritten. A failed archive handoff is not reported
as success. This read-only inspection is safe to repeat; the recovery approach
must not be generalized to financial or infrastructure actions with side effects.

Live execution `ibkrv2stg-agent-audit-uk15szh` succeeded for operation
`71963415-8457-41a9-a665-2b37c793b32d`. Payload SHA-256:
`e93bc2bfa10871b3c473cdc1adcd2fa9ddae909f2cc722c34c579b60a40b7c99`.
Reviewed source-manifest hash:
`ba01bb2995738985b2c96f936cec3dcfc3eed13ff742091cc5d71d0db6bcd1b6`.
It committed exactly two events: sequence 1 started, sequence 2 succeeded. Each
phase was replayed identically and the operation still had exactly two events.
These are real inspection records, not invented financial activity. Their detail
files explicitly identify a trusted manual rollout worker, not an autonomous AI
specialist. No model call was made; infrastructure cost was not measured.

The protected detail hashes are
`13bb48bfa75d2bb068a4a67e4008a20ced26dfe5785aa25e9bc6c920bee7c467`
and `db57530b744d6eb6b5647fa019947bc2496832ffe1f369e70ba9b90d4afed477`.
Events are stored under the tenant's `events/` prefix and details under its
`evidence/` prefix in the protected audit container. The dispatch and acceptance
receipt was independently retained and read back at
`acceptance/ibkrv2stg-agent-audit-uk15szh-a49aa4b2c05f0f3baa946a6f4e180e0c2d8ada41af90e07663d83c30f7c038c4.json`.

Independent execution `ibkrv2stg-agent-report-r6fe9eb` also succeeded under the
separate reporting identity, with database read-only mode enabled. It verified
both event hashes, sequence linkage, protected event bytes, detailed evidence
hashes, producer identity, scope and source manifest. It confirmed two events
and two detail records, with zero database writes or messages sent. Its payload
hash is `a0d519abbb1d0363fd58c6abed56e5f2980cd25337fcb2857f24ac37a9dbc626`.
Its acceptance receipt was retained and read back in the protected container at
`acceptance/ibkrv2stg-agent-report-r6fe9eb-fafb24a29149d67b6afce137aac2f1d5eb632f3f7b1848f3e548b29a2af7e8a3.json`,
version `2026-10-02T19:15:52.2248243Z`. The receipt SHA-256 is
`fafb24a29149d67b6afce137aac2f1d5eb632f3f7b1848f3e548b29a2af7e8a3`.
Both installed manual job definitions remained unchanged; these were exact
reviewed execution overrides, not a broad application release.

Local validation passed 950 tests, including capture, missing-intent stop,
duplicate replay, collision preservation, scope tampering and independent-reader
checks. The source-policy scan covered 506 source files and 57 migrations.
The daily-message formatter now states that recorded events can include trusted
rollout checks and do not prove autonomous specialist execution. This wording
change remains source-only until report-production release.

General producer enrollment/authentication, complete system-wide capture,
interrupted-operation recovery rehearsals, daily-report publication, website
inbox release and email receipt handling remain outstanding. The six specialists
and owner Codex runtime are not yet autonomous, and the 18:00 Hong Kong delivery
schedule remains inactive. IBKR data, financial reports and existing schedules
were unchanged. Entra policy review remains deferred by the owner.

### Daily report inbox and dormant database deployment 2 October 2026

The owner inbox is implemented in source, with separate list/detail endpoints.
Each request requires current database-backed owner authorization; viewers,
revoked owners and emergency fallback cannot access it. Only reports published
to the website appear. Report details are hash-checked again, unavailability
returns a sanitized non-cacheable error, and automatic refresh stops exposing
content when access is denied. Email acceptance is explicitly not delivery.
Publication commits the verified report's website delivery state atomically;
website availability is not evidence that the owner read the report.

Azure rehearsal `ibkrv2stg-bootstrap-9bwtuiv` passed 35 checks, including actual
role-level rejection of unauthorized report/audit writes and isolation of
another tenant. Synthetic schema and role cleanup was verified. The immutable
receipt is in `specialist-audit-checkpoints/acceptance/ibkrv2stg-bootstrap-9bwtuiv-a323ff0bcd47eed0afc4236126485e5a2de5c719735f9916701d61303dea2551.json`.
The protected receipt's bytes were read back successfully.

Preflight `ibkrv2stg-bootstrap-xttysxv` found no conflicting objects. The first
installation `ibkrv2stg-bootstrap-md4ch6a` stopped at the membership check and
rolled back: PostgreSQL automatically gives the role creator an administrative
membership. A fresh inspection, `ibkrv2stg-bootstrap-klp1c49`, confirmed no
tables, functions, roles or migration records remained from that attempt.
The check now permits only the installing administrator's ADMIN-only link with
both SET and INHERIT disabled, preserving role management without granting a
service execution access. Tests reject all other membership forms.

Installation `ibkrv2stg-bootstrap-wkc8f5a` then succeeded, with separate
post-commit readback of the exact migration hashes, three forced-RLS tables,
three non-login roles, zero executable memberships and three creator ADMIN-only
links. Migrations 054/055 were not applied by this installation. Existing job
configuration remained unchanged. IBKR source rows changed: zero. Service
logins created, emails sent and schedules enabled: zero.
Its protected deployment receipt is
`specialist-audit-checkpoints/acceptance/ibkrv2stg-bootstrap-wkc8f5a-47c9bc96787225eef6563271f1de44fda80a1cc02ef9e061eeb25423cecf6237.json`,
with exact-byte readback verified. Future isolated rehearsals use explicitly
suffixed `_trial` roles and cannot drop or reuse the installed capability roles.
The post-install rehearsal `ibkrv2stg-bootstrap-o2nrei8` passed all 35 checks
using those separate roles, then verified cleanup. Its result is recorded at
`.work/specialist-report-builder-20261002/ibkrv2stg-bootstrap-o2nrei8.json`.
The final receipt was also retained and read back under
`specialist-audit-checkpoints/acceptance/ibkrv2stg-bootstrap-o2nrei8-d20ec70d9c859a09be7d688966566f203ab2edaec50af67f6732f83a54b16387.json`.
Local validation passed 901 tests; the source-policy scan covered 483 source
files and 57 migrations without prohibited paths or matched secret patterns.

The database installation is not an audit-service deployment or a released
website inbox. Next are scoped service identities, authenticated worker
boundaries, protected-archive integration, website release and complete email
delivery/receipt tests before enabling 18:00 Hong Kong reporting. The sender
preference remains pending. Entra policy review remains explicitly deferred.

### Verified daily report builder and dispatcher 2 October 2026

Added a trusted worker that builds each closed 18:00 Hong Kong reporting window
from the committed audit ledger. It serializes with audit writers, verifies
each selected event and the previous boundary against protected archive bytes,
rejects missing/tampered/oversized slices, and commits the immutable report plus
the two channel queue entries together. The archive's new read-only verifier
does not create or repair missing evidence on the report builder's behalf.

No-activity days produce an explicit report. Deterministic plain-text and escaped
HTML summaries use the approved owner recipient and all six role counts, while
disclosing that coverage is limited to recorded specialist-ledger events. They
do not claim every system action is already instrumented or equate events with
completed tasks. Raw IBKR values, credentials and model prose are not included.

The email dispatcher validates the stored content hash and report date, commits
its lease and sending state before invoking a trusted sender, and records
provider acceptance separately from recipient delivery. Missing/invalid
acknowledgements and provider timeouts become uncertain, with no blind resend.
The provider adapter and delivery-receipt handling remain integration work;
the website inbox is now implemented but unreleased, as recorded above. This
code does not itself enable email or the daily schedule.

Azure rehearsal `ibkrv2stg-bootstrap-3q9ls74` passed 12 checks using actual
commits in its own synthetic schema, then removed that schema and its role.
It verified closed-window boundaries, idempotency, no-activity reporting,
missing-evidence rejection, two independent channels and no production-table
write privileges. The archive adapter was an exact-byte simulator, not a live
protected-archive integration. No emails were sent or job definitions changed.
Evidence: `.work/specialist-report-builder-20261002/ibkrv2stg-bootstrap-3q9ls74.json`.

The extended rehearsal `ibkrv2stg-bootstrap-3pxvzbf` then passed **18 checks**,
including the committed sending state before provider invocation, acceptance
versus delivery, simulated provider timeout becoming uncertain, no subsequent
resend, and independent website queue state. Its sender was synthetic: actual
messages sent remained zero. The private trial schema and role were removed;
job definitions and production data were unchanged.
Evidence: `.work/specialist-report-builder-20261002/ibkrv2stg-bootstrap-3pxvzbf.json`.
Both rehearsal results and their dispatch hashes were retained create-only in
`specialist-audit-checkpoints/acceptance/98a6a078-a93d-4423-93b4-a3181b2f7a21.json`.
Version-pinned readback matched SHA-256
`3d224612d8e44c2e3377043babbc2a6be6c1d4bbf8f327243975ddcf9cc6670e`.

The resource-group inventory found the existing alert action group but no
Communication Services or Logic Apps mail transport. An owner preference was
requested for an Azure-managed notification sender versus a company-branded,
domain-verified sender. Neither was provisioned and no DNS/mailbox permissions
were changed. This delivery dependency is separate from the deferred Entra
policy review.

Validation: **882 local tests passed**; repository policy check scanned 476
source files and 57 contiguous source migrations. Persistent audit/outbox
deployment, independent protected archive integration, website delivery,
provider receipt acceptance, missed-delivery alerts and activation of the
18:00 schedule remain outstanding. The six autonomous specialists are not
declared deployed by these tests.

### Owner approvals and service-identity verification — 2 October 2026

The retained Storage Blob Data Contributor grants apply only to
`anthony.ying@wealthryse.ai` (principal
`8617fd1c-2280-40f2-92dd-45686e9d686c`) at these container scopes:

- `ibkrv115rcc62xxqblay/source-protection-probes`
- `ibkrv2stgauhecwc4itaoo/specialist-audit-checkpoints`

Both role-assignment responses matched the exact principal and container scope.
The second private container was created. Grant evidence:
`.work/source-protection-20261002/907f1caf-1e7b-4bbe-bf81-81c27da39223/`.

Azure service-identity inference execution
`ibkrv2stg-institutional-shadow-kebgn1h` succeeded: one synthetic Responses
request, HTTP 200, expected response verified, 2.626 seconds. No portfolio data,
tools, Flex calls or database commands were involved. The execution used the
existing diagnostics identity and pinned image through a per-execution override;
the manual job definition hash was unchanged. Evidence:
`.work/codex-managed-identity/ibkrv2stg-institutional-shadow-kebgn1h.json`.
This does not establish streaming, coding isolation, owner workspace delivery
or deployed specialist workers. Local CLI inference failure remains separate.

The first protection rollout stopped safely after ARM reported a hold active
but a synthetic overwrite still succeeded. Both synthetic versions were retained;
neither IBKR source container had been changed at that point. A later synthetic
probe under the settled hold blocked both overwrite and deletion with
`BlobImmutableDueToLegalHold`, while new-object creation/readback passed.
Evidence: `.work/source-protection-20261002/ae5c7260-110b-4b2e-ad3f-0f6dedb38d57/`
and `.work/source-protection-20261002/608cd116-7180-4f0d-b80e-1cf382fcebdf/`.
The rollout now permits at most four propagation checks, 15 seconds apart,
using separate synthetic names only in the registered probe containers. It
retains all prior evidence, stops immediately on unrelated failures, and never
continues to source protection if those checks fail. Ten focused tests passed.

The resumed rollout `3a95a53c-be81-4295-a16e-f1b424ef4029` configured retained,
administrator-removable legal holds on all four registered containers. On v2,
the first synthetic overwrite was allowed during propagation; the bounded
follow-up blocked both mutation types and verified new-object creation. The
existing v1.1 inventory remained exactly 464 versions with the same metadata
hash `6517a593d865439c363fc670af9ad01a1be5cd76597d3a2d68f47cc6ed451bb6`.
The new private `ibkr-raw` container was created and its hold read back through
ARM, but the subsequent blob inventory read returned 403
`AuthorizationPermissionMismatch`. Therefore the script stopped before its
normal completion marker. No broader role was granted to work around this.
That inventory blocker was subsequently resolved by the owner's explicit
approval of Storage Blob Data Reader on `ibkrv2stgauhecwc4itaoo/ibkr-raw`,
not Contributor on the storage account. The exact built-in reader role,
principal and container scope were independently read back after assignment.
Grant evidence: `.work/source-protection-20261002/0eceb009-5a65-43e2-b6b6-59b2a8e0f7d0/`.
The read-only verification then completed successfully: 464 unchanged v1.1
versions, zero versions in the new v2 archive, both source holds active,
unchanged job definitions and no source mutation attempts or Flex requests.
Verification: `.work/source-protection-20261002/9c8297ec-e246-4c16-bf9c-b60752e56a05/`.
Twelve focused source-protection tests passed, including exact reader-grant
scope and wrong-operator rejection. The full-suite count above remains the
previous run, not a claim that the full suite was rerun for this access change.
Container creation/protection alone does not wire every v2 ingestion path to
this new archive. Those writer/reader integrations still require acceptance.

Independent post-rollout readback confirmed all four holds and unchanged job
definition hashes. The complete grant/probe/partial-rollout records, including
failed attempts, were archived create-only in the protected evidence container
and version-pinned readback matched SHA-256
`4472ad6713dce3b3beea0152fba9d28a8ee4d2ce399949e9f1da2822547f56d4`.
Local receipt:
`.work/source-protection-20261002/729f96ba-b5b1-4bfa-91a3-3e953df590a1/`.
No received IBKR source mutations, live Flex requests, schedule changes or
retry-policy changes were performed.

Full local suite: **864 tests passed**. Repository policy check: 471 source
files scanned, 57 contiguous source migrations, no prohibited paths or secret
patterns detected. This is not a full security certification or deployment.

The approved Entra recheck returned HTTP 403 for both Conditional Access and
security defaults. An Entra administrator must export both policies, or arrange
read access through a supported role (for example Security Reader for Conditional
Access) and a client with delegated `Policy.Read.All` consent. No policy-write
permission is requested. References:
[Conditional Access policy reads](https://learn.microsoft.com/en-us/graph/api/conditionalaccessroot-list-policies?view=graph-rest-1.0)
and [security-defaults policy reads](https://learn.microsoft.com/en-us/graph/api/identitysecuritydefaultsenforcementpolicy-get?view=graph-rest-1.0).

## Latest verified checkpoint — 2 October 2026

The owner's request to complete and deploy the programme is recorded. It does
not make the engineering and acceptance gates below complete. This checkpoint
supersedes earlier test counts and the earlier statement that no blob recovery
or monthly watchdog has been performed. The detailed chronology below remains
as historical evidence.

- **Deployed:** `ibkr-v2-monthly-autonomy-schedule-watchdog`, an enabled,
  severity-2, 15-minute Azure query rule using the existing action group.
  ARM what-if accepted only creation of this rule; all other resources were
  unchanged. Live and synthetic query checks passed. Original monthly job
  settings and the original IBKR ingestion schedule/retry policy were preserved.
  Evidence: `.work/upgrade-deployments-20261002/monthly-watchdog-20261002T015326Z-evidence.json`.
- **Recovery verified:** `ibkrv2stg-bootstrap-3bo5wxr` succeeded. All 57 unique
  registered files (released reports and current templates; 32,474,084 bytes)
  matched registry SHA-256 values. Each isolated copy was deliberately changed,
  recovered from its prior version, and read back with the original hash.
  Each had three distinct scratch versions; original source ETags stayed
  unchanged. The database was read-only and the manual job definition unchanged.
  The measured 13.616 seconds is the file-copy/version exercise, **not a portal
  RTO or complete disaster recovery measurement**. Scope was `internal-reports`
  and `onboarding-intake`, not every storage object or historical revoked report.
  Private local evidence: `.work/report-blob-recovery-20261002/ibkrv2stg-bootstrap-3bo5wxr.json`.
  Isolated copies remain under the exact `input-snapshots` prefix
  `00000000-0000-0000-0000-000000000001/recovery-drills/fb784f4e-8fd8-4d41-ab67-3e84394525ff/`
  for review. No original PDF/PPTX, release approval or template was changed.
- **Security inventory refreshed:** 85 resource records and 55 role assignments
  within the resource-group/subscription inheritance scope. Both storage
  accounts disable anonymous blob access and shared-key authorization and
  require TLS 1.2, but their network default is Allow. All three vaults use
  RBAC, purge protection and 90-day soft deletion, but public network access
  remains enabled. Network isolation and role minimization are therefore not
  claimed complete. Evidence: `.work/upgrade-posture-20261002/20261002T015312Z.json`.
- **Concrete security blocker:** Graph returned 403 for Conditional Access and
  security-defaults policy reads. The signed-in Azure portal also displayed
  "You don't have access" / insufficient privileges for Conditional Access.
  The current sign-in used MFA; this does not prove tenant-wide enforcement,
  exclusions, session limits or application coverage. An Entra administrator
  must provide an export of these policies or arrange appropriate read access.
  No directory role was self-granted and no security policy was changed.

### Work still required before programme sign-off

1. **Recovery:** full isolated application reconnect/failover rehearsal with
   measured RTO/RPO, retained-copy disposition and an operational recurring
   drill policy. Database and registered-file drills have passed separately.
2. **Workflow:** real producer adapters, cross-account/source dependencies,
   durable dispatch/outbox/leases, shadow-cycle acceptance and controlled
   scheduler cutover. Migration 054 remains a tested source-only foundation.
3. **Risk:** deploy the reviewed correction-evidence migration/import/reader
   transition and backfill, validate historical invalidation and composite
   coverage. Preserve immutable published reporting snapshots. Migration 055
   is not persistently applied merely because its rehearsal passed.
4. **Release:** operational ACR build/scanners/SBOM, trusted evidence collection,
   isolated staging acceptance, protected owner approval and restricted
   deployment/rollback control. A private source archive is not a verified
   application release. Do not deploy the dirty source tree wholesale.
5. **Security:** resolve the Entra read blocker, finish identity-scope review,
   design and stage-test egress/DNS/private endpoints before firewall cutover.
6. **Observability:** full daily producer-cycle acceptance, email delivery
   drill, reviewed freshness/backlog/slow-query paging, maintenance handling,
   log retention/redaction and cost/tag dashboards. The new monthly alert's
   bounded 48-hour detection window is not durable month-level incident state.
7. **Maintainability/scale:** remaining route/service extraction and centralized
   typed contracts/layouts, realistic concurrent/many-account load tests and
   reviewed obsolete-job retirement.
8. **Financial/visual acceptance:** independent financial reconciliation and
   per-template visual/geometry acceptance, followed by owner sign-off. Alpha
   World MS remains paused. No approval was inferred from this upgrade request.

The first file-drill execution, `ibkrv2stg-bootstrap-0zn9fuw`, stopped safely
before recording any recovered asset, with a ValueError but no granular reason.
Inspection found that the original report-only allowlist excluded uploaded
templates. The corrected trial permits `onboarding-intake` for registered current
templates only and adds safe phase diagnostics. It succeeded without loosening
report hash/version checks; the first run's exact exception is not independently
proven. That failed diagnostic may trigger the existing failure notification;
it was not a Flex request or production outage.

Completion telemetry, dispatch metadata and the exact executed probe source
were archived create-only in private `release-evidence` under
`recovery/fb784f4e-8fd8-4d41-ab67-3e84394525ff/`, with independent version-pinned
download/hash verification. The local receipt ends in `-verified.json` beside
the execution evidence. The validator rejects missing/duplicate/mixed-run
records, altered asset counts, versions, hashes, bytes and scratch scope. This
is preservation of drill evidence, not a report approval or deployment permit.
Full local suite at this checkpoint: **743 tests passed** (19 additions over the
previous 724-test baseline). A successful unit suite does not replace the
outstanding production/load/financial/visual acceptance checks above.

Limited source policy check passed for 441 source files and 55 contiguous source
migrations (not 55 deployed migrations). The 418-file sanitized source archive
was saved create-only to private Azure storage and version-pinned readback
verified. Source SHA-256:
`3d6f17cc91fe806a48cd06980c6ae60d4273e876714ee046f3cecea5cb2c01dc`;
archive receipt SHA-256:
`56c736961ee8ebaaeb50577b9cbf1fdd75e861011697a0e808a51f2f2c38c5bd`.
Its state remains `archived_not_approved`, not build-ready or deployed.

## Approved agentic operations extension

The owner approved extending this programme with agentic operation of the
platform and resource group. The original observation-first approval was later
expanded to standing operational authorization, subject to preserving IBKR Flex
source data, documenting every action and reporting daily. The updated policy
below supersedes the earlier assumption that each routine production action
must receive a fresh owner approval. Authorization does not by itself deploy
agents or prove that source preservation, auditing and execution controls work.

### Approved Codex with Azure OpenAI integration

On 2 October 2026, the owner requested that Codex with Azure OpenAI be added
to the approved programme for the entire platform and resource group. This
extends the six-specialist architecture rather than replacing it or introducing
an unrestricted seventh administrator. **Status: owner setup page and provider
configuration foundation implemented and locally tested; no live Codex worker
or production deployment yet.**

The target is Azure-hosted Codex workers using an explicitly selected Azure
OpenAI deployment, with tasks initiated through the website's AI Operations
page or the durable coordinator. Codex must be available directly to the owner
as well as to all six specialist roles for appropriate coding, investigation
and automation tasks. Existing
deterministic financial tools remain authoritative for calculations; introducing
Codex does not require replacing every analytical model call with a coding
session. The website must remain usable without opening the desktop Codex app.

**Direct owner use is an explicit acceptance requirement.** The owner,
`anthony.ying@wealthryse.ai`, must have a personal Codex workspace within the
website's AI Operations area, authenticated through the existing sign-in. This
is interactive use of Codex with Azure OpenAI, not merely a dashboard of work
performed by the six specialists. The owner must be able to:

- Chat directly with Codex, ask questions and request investigations, analysis,
  code changes or operational tasks across the authorized system/resource group.
- Select the account, resources and files relevant to a task; start a new
  conversation, continue saved conversations and provide follow-up instructions.
- View live progress, results, proposed changes, tests, costs and audit evidence;
  pause or cancel eligible work, and use the existing release/control interface.
- Work directly with Codex or assign a task to a specialist, with clear labels
  distinguishing owner-initiated work from background specialist activity.

Owner sessions use the same protected execution service and standing-authority
policy, not shared specialist credentials or unmediated production shell access.
IBKR source immutability, account authorization, action auditing and the 18:00
Hong Kong daily digest apply equally to direct owner sessions. No API keys are
exposed to the browser. Other users do not inherit this owner entitlement; their
access requires explicit role/account authorization. Before acceptance, verify
an owner sign-in, direct conversation, scoped task, reconnect/history recovery,
cancellation and corresponding audit/digest entry through the website. This
requirement remains incomplete: the owner setup page is implemented, but direct
conversations and task execution are not yet implemented or activated.

#### Owner workspace implementation checkpoint

On 2 October 2026, added the separate `/ai-operations` owner setup page and
`/api/codex/status` endpoint. Neither substitutes the existing administrator
narrative chat for Codex. Both require a freshly database-authorized owner;
legacy/emergency fallback and other roles are rejected. Responses are marked
`no-store`. The page shows explicit setup gaps, checks status automatically
while visible, bounds request timeouts/backoff, and stops checking after an
authorization denial. There is no task submission endpoint or enabled composer.

The new `codex_workspace.py` provider foundation validates an exact server-owned
Azure hostname allowlist, deployment and API-version syntax. It generates
credential-free configuration for a future isolated service home, using the
Responses transport and a fixed broker-token environment reference. It does not
read credentials, edit desktop Codex settings, launch a process, call a model or
grant execution rights. Configuration fingerprints do not prove compatibility;
even valid configuration and enable-like environment flags cannot activate work.
Credential isolation must be implemented and tested before using that
configuration in a coding worker.

Read-only Azure inventory found the `ibkrv2stg-ai-auhecwc4itaoo` OpenAI account
in Southeast Asia and the successfully provisioned `gpt-5-6-sol-apac` deployment
(`gpt-5.6-sol`, version `2026-07-09`, DataZoneStandard capacity 10). This is
management-plane inventory, not proof of inference authorization, available
runtime quota or Codex compatibility. No provider was configured or activated
in the live website during this checkpoint.

Validation: **849 tests passed**, including 12 new tests covering provider
validation, credential non-disclosure, rejected fallback access, and HTTP-level
access revocation. Source policy checks passed for 466 source files and 57
contiguous source migrations; this does not imply live migrations were applied.
A local Chrome preview with a synthetic owner displayed the setup checks,
successfully refreshed them through the button, and updated its timestamp
automatically 30 seconds later. Production browser sign-in,
Codex inference, persistent history, cancellation, independent audit recording
and daily report delivery remain acceptance work. No production resources,
IBKR data, source protections or role assignments were changed by this work.

#### Synthetic Azure connection checkpoint

Added a bounded diagnostic in `codex_compatibility.py` and
`check_codex_azure_compatibility.py`. The script checks the expected tenant,
subscription, account endpoint, region, model version and deployment SKU before
making one synthetic Responses request. It sends no financial data, enables no
tools, disables response storage, rejects redirects, bounds output size and
does not retry or switch providers automatically. Credentials and raw response
text are excluded from evidence. Configuration explicitly distinguishes the
dated preview API from `/openai/v1/`; their fingerprints differ.

Three separate checks returned HTTP 401: the dated preview API and v1 with an
AI audience token, followed by v1 with a Cognitive Services audience token from
the same existing Azure CLI identity. No role grants or key changes were made.
This does not establish the exact authentication failure cause or imply that
the application's separate managed-identity inference path is broken. It does
mean that a successful Codex/Azure connection cannot yet be claimed from this
identity. No model output or coding task was produced.

Sanitized local evidence:
`.work/codex-compatibility/a948f7a7-d67f-47c4-b6b9-845920273175.json`,
`.work/codex-compatibility/a0293396-d07e-453c-b279-ca0857d4a702.json`, and
`.work/codex-compatibility/095a4ccc-2e5d-4384-a975-30614b7df40c.json`.
Eight new compatibility tests passed; the full suite passed **857 tests** and
the source scan passed for **469 files and 57 source migrations**. These are
not deployment or live Codex acceptance results. The next connection check
must diagnose the authentication path rather than repeatedly retry this one.

The integration follows the documented
[Codex app-server thread/turn protocol](https://learn.chatgpt.com/docs/app-server).
Official OpenAI documentation describes an
[Azure custom provider using the Responses API](https://learn.chatgpt.com/docs/config-file/config-advanced#azure-provider-and-per-provider-tuning),
[noninteractive Codex execution](https://learn.chatgpt.com/docs/non-interactive-mode),
and [server-side SDK integration](https://learn.chatgpt.com/docs/codex-sdk).
These establish a documented integration path, not compatibility evidence for
this subscription, model deployment or installed desktop build. The endpoint,
deployment name, API version, authentication, tools, streaming and runtime
versions must be verified together before rollout. No particular model is
selected merely because it appears in an example.

Implementation work added to the approved sequence:

1. **Compatibility and model routing.** Inventory the existing Azure OpenAI
   deployments, regions, quota and network access. Pin a tested Codex runtime
   and compatible deployment. Create an isolated service configuration, leaving
   the owner's desktop settings unchanged. Verify authentication and refresh;
   prefer managed identity/Entra where the tested integration supports it, or
   use a protected credential broker with Key Vault-backed credentials. Never
   expose inference credentials to generated code or build/test subprocesses.
   Record provider and deployment in every task; prohibit silent fallback to a
   different endpoint or data destination.
2. **Shared specialist task interface.** Add a Codex adapter to the existing
   coordinator, with role, account/resource scope, source version, task ID,
   expiry and budget. Use isolated Azure workers with bounded workspaces and
   per-task lifecycle. Resume through durable task state, not a user's active
   browser, desktop session or personal sign-in token. All resources receive an
   explicit coverage classification; coverage is not blanket write permission.
3. **Controlled operations.** Supply authenticated, typed system/Azure tools
   through the existing execution service. Respect the owner's standing
   authorization for tested operation classes without repeated routine approval.
   Keep code execution isolated from production credentials; release through
   the selected ACR Tasks and Azure-controlled staging/deployment process.
   Codex cannot grant itself permissions or remove source/audit protections.
4. **Source protection and evidence.** Preserve received IBKR Flex bytes and
   values unchanged, including earlier versions when IBKR supplies corrections.
   Apply source protection to direct tools, generated code, migrations, cleanup
   and ancestor-resource operations. Independently record every attempted tool
   call, command, file change and consequential action, including failures and
   denials. Retain sanitized inputs/results, versions, hashes, costs and rollback
   references, not credentials or private model reasoning. Codex event output
   alone is not proof that every external effect was captured.
5. **Website and daily reporting.** Show Codex tasks, live status, evidence,
   affected resources, changes, costs and cancellation/pause controls in AI
   Operations. Include their activity in the same website/email digest at
   **18:00 Asia/Hong_Kong**, including inactive and incomplete-coverage days.
   Preserve existing user/account entitlements and the independent emergency
   stop. Ordinary ingestion continues if Codex or Azure OpenAI is unavailable.
6. **Acceptance and expansion.** Test Azure inference and tool compatibility,
   account isolation, source-mutation denial, prompt injection, credential
   isolation, stop/revocation, restart recovery, duplicate-effect prevention,
   audit failures, budgets and model outages. Rehearse a real maintenance change
   through tests, immutable image build, staging, deployment and compatible
   rollback. Verify report delivery before enabling unattended coordination,
   then expand accepted action classes across the registered resource inventory.

This addition does not resolve the outstanding storage permission scopes,
Entra policy visibility, email transport or eight-upgrade acceptance work.
Alpha World MS remains paused and Bloomberg integration remains deferred.
No Azure resource, role assignment, model deployment, paid inference call,
Codex configuration or reporting schedule was changed by this plan amendment.

### Owner instruction on greater autonomy and daily reporting

The owner grants the six specialists substantially greater autonomy across the
system and resource group. IBKR Flex data is the authoritative source for the
facts it supplies and must never be altered by agents. Original bytes and
received values must remain available with their original provenance. Agents
may validate, quarantine, explain inconsistencies and rebuild separate derived
datasets, but may not substitute estimates or external figures for IBKR source
values. A later correction received from IBKR is retained as a new source
version, with the earlier version preserved. Benchmarks and other external
inputs remain separately labelled; the instruction does not turn IBKR into a
source for facts that it does not supply.

Standing authority is intended to cover analysis, isolated research, rebuilding
derived analytics, report production and validated release, pipeline recovery,
infrastructure maintenance/scaling, software preparation, verified deployment
and rollback. The existing owner interface remains available for intervention
and reversal. Tested registered operations should not repeatedly ask for the
same owner approval. Role-specific executors and machine-enforced validation
are implementation safeguards, not a return to approval for every routine task.

This cannot be implemented by giving every specialist unrestricted Azure Owner
or database-superuser credentials: those privileges could delete source storage
or disable its protections. The deployment design must protect source objects,
database source records, their ancestor resources and the audit service, while
granting the necessary operational permissions elsewhere. Infrastructure and
release operations must be checked for indirect effects on protected sources.
Agent permissions cannot include removing those protections or erasing history.
Trading or money movement is outside this system-operations request. Existing
Flex circuit breakers and zero-immediate-retry behavior remain safety controls
for ingestion; no schedule or retry changes were requested here.

Every agent tool call and consequential operation must have independently
recorded identity, scope, reason summary, exact input/version hashes, policy and
tool/model versions, timestamps, effects, costs, verification and recovery
references. Record denied attempts, partial work, failures, cancellations and
rollbacks as well as successes. Protect detailed evidence separately; do not
log credentials or private model reasoning. An audit-service failure must block
new agent mutations and be reported, without stopping ordinary deterministic
ingestion. Daily reports must link to the full event history and disclose
missing coverage, unresolved work, source-integrity checks and delivery failure.
Critical incidents should be escalated immediately, not held for the next digest.

Requested delivery is daily, through the product rather than a Codex-dependent
reminder. The owner confirmed **18:00 Asia/Hong_Kong** (10:00 UTC) on 2 October
2026, with website/email delivery retained. The configured email destination is
`anthony.ying@wealthryse.ai`. Each reporting window covers the preceding 24 hours
ending at 18:00 Hong Kong time; London daylight-saving changes do not shift it.
Delivery is not yet active. A no-activity day must still report.
The report needs a durable outbox, retry/deduplication, independent day-boundary
checkpoints and a missed-delivery alert before it is considered operational.

Implemented source in this continuation: `specialist_autonomy.py` provides the
expanded standing-action policy, retained-source manifest comparison, and a
daily activity digest verifier. The policy intentionally reports
`execution_authorized=false`: it is not the missing production action service.
The digest verifies event counts, sequence, hashes and trusted boundary
checkpoints, rejects altered/missing entries, and does not claim that every
system action was captured or that any message was sent. A hash chain without
independently protected checkpoints/storage is not tamper-proof evidence.

The existing ingestion archive path was also hardened: exclusive local creation
prevents overwriting an existing filename, reuse requires matching bytes, and
replays reject content that differs from the retained file before parsing or
loading it. Cloud raw uploads retain `overwrite=false`. This addresses an actual
local archive/replay gap; it does not establish Azure WORM retention, revoke
existing write grants or protect every database source column. Those are still
deployment work. No historic file or received value was rewritten.

Validation: 18 new tests passed, and the full suite passed **801 tests**. This
continuation does not deploy the six-agent runtime, enable autonomous report
release, change Azure permissions or activate daily delivery. The next work is
source-store/access enforcement and independent audit persistence, followed by
registered executors and the product's daily-report job. No new human approval
is needed to continue that authorized implementation. The existing Entra
policy-access blocker remains a separate open item.

### Confirmed evening schedule and database audit rehearsal

The 18:00 Hong Kong delivery preference is implemented in
`agent_daily_schedule.py` as explicit Asia/Hong_Kong reporting windows, UTC
conversion and bounded oldest-first catch-up from a durable cursor. Stable
tenant/date/channel delivery keys support retries without redefining report
identity. Tests cover the exact due boundary, London summer/winter time, missed
days and separate website/email keys. These functions do not create a running
Azure schedule or send email; reliable delivery still needs a persisted outbox,
provider integration and delivery acknowledgements.

Migration 056 adds the append-only `specialist_audit_events` ledger. Forced
tenant RLS, sequence/hash-link guards and mutation triggers reject updates,
deletes and truncation. The schema rejects unstructured extra event fields and
unsupported roles/phases. Public and inherited named grants are removed; no
agent is granted table access. `specialist_audit.py` supplies a transaction-scoped
writer lock, server-observed time, strict event validation and idempotent append.
An existing event ID with different content is rejected. The caller must commit
an intent before starting an external action; returning from the append helper
alone is not proof of durable commit or authority to execute.

The final isolated PostgreSQL rehearsal,
`ibkrv2stg-bootstrap-0p3qyi8`, **succeeded with 17 checks**. It verified chained
append, identical retry, conflicting retry rejection, direct sequence/hash-link
rejection, invalid payload rejection, UPDATE/DELETE/TRUNCATE rejection, tenant
isolation, writer lock exclusion and absence of production-table write access
for the temporary role. Temporary schema, fixture data, role and grants were
all rolled back and their absence verified. The existing Azure job definition
hash was unchanged. No Flex request, IBKR source mutation, source deletion,
application rollout, report release or persistent migration was performed.

Evidence: `.work/specialist-audit-20261002/ibkrv2stg-bootstrap-0p3qyi8.json` and
the adjacent dispatch record. Executed source SHA-256:
`285ebf4fe74d5d34d87662ae6567000e01d73206e51a88ce982f61ef67aa164e`.
An earlier 13-check rehearsal also passed; the 17-check run tests the strengthened
final schema. The local suite passed **814 tests**, including 13 new schedule
and writer tests. The limited policy scan passed for 456 source files and 56
contiguous source migrations, not 56 persistently applied migrations.

Outstanding before daily delivery and expanded autonomy can be called live:
deploy the separately scoped audit service with protected external checkpoints;
enforce immutable IBKR storage/database access and ancestor-resource protection;
connect authenticated executors to commit-before-effect auditing; persist report
windows and outbox state; add per-channel dispatch, acknowledgements and a
missed-delivery watchdog. Exercise end-to-end failures and only then enable the
18:00 product schedule. A database owner could still defeat table triggers, so
agents must not receive owner rights over the audit/source stores. Migration 056
remains source-only after this rollback-only acceptance test.

### Source protection and reliable delivery continuation

The owner explicitly approved indefinite, administrator-removable protection
for existing and future IBKR files in v1.1 `raw-statements`, the new v2
`ibkr-raw` container, and separate audit/probe containers. This is recorded
authorization, **not a claim that protection has been enabled**.

Live inspection found 463 retained raw objects/versions in v1.1, no existing
container immutability policy, and an absent v2 `ibkr-raw` container despite
that path appearing in the configured Flex jobs. Both accounts have versioning,
30-day soft deletion and a lifecycle rule deleting old versions after 30 days.
The existing v2 daily synchronization imports v1.1 exports; no new Flex query
was issued to test the missing direct-ingestion container.

`protect_specialist_sources.py` now confines rollout to exact registered
containers, records source version/ETag/size inventories, tests synthetic
overwrite/delete denial and successful new-object creation, and verifies that
existing versions and job definitions are unchanged. It retains evidence and
does not attempt mutations against actual IBKR files. The design uses
[Azure container-level WORM protection](https://learn.microsoft.com/en-us/azure/storage/blobs/immutable-container-level-worm-policies),
with protected append modifications disabled. Broader identity/ancestor and
database-source access protection remains separate work.

The first authorized rollout created only the private v1.1
`source-protection-probes` container, then stopped on a 403
`AuthorizationPermissionMismatch` while attempting the first synthetic upload.
No hold was applied and no source was modified. Safety review subsequently
required explicit approval for retaining Anthony's Blob Data Contributor grant
on these exact two new scopes:

- `ibkrv115rcc62xxqblay/source-protection-probes`
- `ibkrv2stgauhecwc4itaoo/specialist-audit-checkpoints`

That scope-specific question is pending; no role assignment was made. The
original indefinite-retention approval remains valid. Readback retained the
same 463 version IDs, ETags and sizes. The evidence is under
`.work/source-protection-20261002/3beb2c4d-315f-496e-82aa-cad5f298c301/`
and the later readback directory
`.work/source-protection-20261002/162e938f-a2d0-44c1-af27-4acf402a322d/`.
Source-content rehashing was not performed in this metadata-preservation check.

The audit service handoff now requires a dedicated idle connection, a completed
database commit and an exact create-only protected-archive readback before it
returns success. A failed archive write leaves the committed event available
for an identical retry; a conflicting archive cannot replace history. This is
tested service code, not a deployed authenticated agent endpoint or execution
permit. Protected archive creation, runtime identity and migration deployment
remain outstanding.

Migration 057 and `agent_daily_outbox.py` add immutable daily-report contents,
separate website/email delivery records, stable retry identity, fenced worker
leases, bounded pre-send attempts and a 18:00 Hong Kong availability boundary.
An expired in-flight send becomes uncertain rather than being blindly resent.
Provider acceptance is distinct from confirmed delivery. These transitions
still require connection to the real sender, website inbox, receipt processing,
reconciliation and missed-delivery monitoring.

Final PostgreSQL rehearsal `ibkrv2stg-bootstrap-cvb0di4` succeeded with **23
checks**, including deduplication, content immutability, tenant isolation,
stale-worker rejection, uncertain-send handling, future-date exclusion and
retry exhaustion. All isolated schema/role/data changes were rolled back; no
messages were sent and the existing job definition was unchanged. Source hash:
`3196528738a83ac1862ab112ad404c61379e52479e5a1fc5249d03c94f85b8c6`.
Evidence: `.work/specialist-delivery-20261002/ibkrv2stg-bootstrap-cvb0di4.json`.
An earlier 20-check rehearsal also passed. Full local validation passed **837
tests**; the limited policy scan passed for 464 source files and 57 contiguous
source migrations, not 57 deployed migrations.

The Azure inventory found the existing alert action group but no dedicated
Communication Services or Logic Apps email resource. A reliable daily digest
transport is not established by alert notifications. **Daily delivery, audit
service deployment and expanded six-specialist execution remain inactive.**

### Roles and separation of duties

Six specialist roles are planned: Data Analyst, Data Scientist, Data Engineer,
Financial Analyst, Platform Operations and Software Maintenance. The coordinator
will use durable workflow state. Independent code checks and human approvals
where required remain separate from agent proposals. Another agent agreeing
with a proposal is not independent financial or security acceptance.

The initial role catalogue reuses existing deterministic subject tools for
analytical observations. Data Engineering initially receives freshness
inspection only. Platform Operations and Software Maintenance have a planned
metadata-inspection capability, not financial-data, shell or deployment tools.
The metadata runtime adapter is not implemented by merely naming that tool.
More capabilities require explicit registration, tests and rollout.

### First implementation checkpoint

`agent_governance.py` adds immutable role definitions and a non-executing
observation preflight. Disabled and expired grants, changed policies, identity
mismatches, unknown tools, missing/ambiguous scopes, cross-account access and
unregistered resource IDs are rejected. Interactive calls require explicit
current user entitlements; background calls require separately registered
background authority. The default kill switch is on. Exact ARM IDs are checked;
resource-group prefixes and wildcard grants cannot substitute for exact scope.

Every decision retains `execution_authorized=false` and
`mutation_authorized=false`, even when an observation is eligible for a later
adapter check. This module neither authenticates callers nor grants Azure/DB
access. Future adapters must load grants server-side, recheck revocation for
each call, enforce database/account isolation and validate all tool arguments.
Never build a trusted grant from browser fields, chat content or model output.
Existing website authorization and administrative controls are unchanged.

The metadata-only preparation script processed the captured 2 October Azure
inventory into a review of six disabled/planned roles and 85 resources. All
85 resources remain explicitly unreviewed, not silently classified as managed.
It checks capture age, expected subscription/resource group and duplicate IDs,
and excludes raw role assignments, secrets and financial payloads. This is a
review of the captured inventory, not a fresh cloud inspection or runtime grant.
Evidence: `.work/agentic-phase1/56996ef02c29f55a59c554d2978dc3632d92ed3b47d9ea2191ed19df33f7d8fc.json`.
Seventeen focused governance tests passed at this checkpoint. The full local
suite passed all 760 tests. The limited repository policy scan passed for 444
source files and 55 contiguous source migrations; no new migration was deployed.

No new agent runtime, managed identity, role assignment, scheduler, model call,
report approval or production application revision was created by this change.
The existing agents and ordinary jobs retain their previous behaviour.

### Read only adapter checkpoint

The owner requested execution of the approved agentic programme. The next
source implementation, `agent_observation.py`, connects the governance
preflight to the existing deterministic subject tools. It is **not deployed**
and ships disabled, with a deny-all authority provider. No browser endpoint,
background worker or model can enable it through tool arguments.

The broker requires a trusted authenticated gateway caller, fresh server-side
agent authority, an active database user/role, an active subject and analyst
feature, and the intersection of user and agent account permissions. Agent
authority is checked before opening the reader, immediately before dispatch,
and after the tool returns. User permissions are rechecked before returning
data. Revocation during work therefore suppresses delivery when observed by
these checks; this is not a promise of instantaneous cancellation of an
already-running query.

The dedicated database factory uses a new READ COMMITTED, read-only transaction
with transaction-local tenant scope, 15-second statement timeout and two-second
lock timeout. The broker verifies those reader properties and rejects
superuser/BYPASSRLS connections. Deployment still requires independent database
grant, view and RLS validation; transaction checks alone do not prove isolation.
Typed arguments, result identity and a two-megabyte response limit are enforced.
Audit events contain correlation IDs and identity/request/result hashes, not
email, prompt text, financial payloads or raw exception messages. This is log
instrumentation, not yet a protected durable agent evidence ledger.

Validation: **23 new tests passed; 783 tests passed in the full local suite**.
Tests cover kill switch, revocation before dispatch and before delivery,
cross-account/tenant denial, user role and entitlement checks, invalid reader
settings, argument injection, response scope, size limits and sanitized errors.
One test executes the real deterministic performance tool against a synthetic
reader. These are local unit/integration tests, **not a live PostgreSQL or
Foundry evaluation**. The limited source policy scan passed for 446 source
files and 55 source migrations. No migration was added or applied.

Read-only Azure inspection was refreshed at
`.work/upgrade-posture-20261002/20261002T035001Z.json`: **86 resources and 55 role
assignments**. The extra resource versus the earlier capture is the previously
deployed monthly watchdog. Existing roles include secret/data write, job
execution, deployment and access-administration privileges; none are inherited
as specialist-agent authority. Resource-level least-privilege review remains
open. The refreshed six-role planning inventory is
`.work/agentic-phase1/8b5fc75b1b1ef56b65c1e0177f14f6dd99c7557dd18e6855f4a501354e1c7a5c.json`;
all 86 resources remain unreviewed/unauthorized for agent execution. Graph
policy reads still return 403. No role grant, network boundary, schedule, retry
setting, application revision or report release was changed in this stage.

Next gates are a persistent revocation-aware registration/stop store, verified
gateway authentication, resource-metadata adapters, a least-privilege database
rehearsal, query/workload budgets, and the controlled staging release. Background
service scopes and cost limits must be enforced before background agents run.
The AI Operations interface, specialist model evaluation and autonomous action
service remain outstanding. Alpha World MS remains paused.

### Delivery sequence and acceptance

1. Finish the resource/action/identity matrix and cost baseline. Resolve Entra
   policy visibility with the directory administrator. Review legacy and shared
   resources explicitly; a resource's name or staging tag is not permission.
2. Complete the eight-upgrade foundations: dependencies, correction propagation,
   release evidence, recovery, monitoring and financial/visual checks.
3. Verify the approved Codex/Azure OpenAI provider and isolated worker path;
   connect authenticated read-only adapters and persistent agent registrations;
   evaluate answer grounding, account isolation, prompt injection and revocation
   using authorized data. Verify actual Foundry deployment/region/quota first.
4. Build the restricted action/approval service and system-wide AI Operations
   interface, including owner controls and an independent emergency stop.
5. Rehearse bounded operational runbooks, then enable only accepted classes.
   Test duplicate dispatch, interrupted tasks, model outages and escalation.
6. Integrate reporting and onboarding coordination with versioned contracts,
   live status, design validation and the existing owner approval checklist.
7. Introduce Codex-backed isolated maintenance patches and ACR build/staging validation.
   Production release requires the protected approval policy and verified,
   database-compatible rollback; agents cannot modify their own controls.
8. Expand coverage after load, recovery, financial, visual, cost and security
   acceptance. Establish periodic authority reviews and owner documentation.

Each stage must distinguish implemented source, deployed runtime and verified
behaviour. A successful unit suite does not establish unattended operation.
The Entra policy blocker limits security sign-off; it does not prevent local
development of read-only adapters. Alpha World MS remains paused and Bloomberg
integration remains deferred. Existing report approvals, live Flex safeguards
and immutable published snapshots remain protected throughout the programme.

## Safety boundaries

- Preserve the original 16:00 HKT IBKR ingestion and zero immediate retries.
- No extra Flex requests, token rotations, formula changes or report approvals.
- Alpha World MS onboarding remains paused. Computing a private analytics cache
  does not activate the portfolio or approve its design authority.
- Bloomberg integration remains deferred. No VM, HA or premium tier was added.
- Existing dirty working-tree changes belong to the owner and are preserved.

## 1. Recovery exercise — database drill passed

Restored v2 PostgreSQL to the separately named private
`ibkrv2stg-recovery-20261001`, using point `2026-10-01T16:45:06Z`.
Public network access remained disabled; the clone used the existing delegated
PostgreSQL subnet and private DNS zone. No live application was pointed at it.

Source probe: `ibkrv2stg-bootstrap-osqqyo2`.
Restore probe: `ibkrv2stg-bootstrap-xd8ub2k`.
Both used enforced read-only, repeatable-read transactions.

Column-schema fingerprints and row counts/value fingerprints matched for all
eleven checked tables, including 53 migrations, 886 NAV rows, 32,053 position
rows, 2,644 cash transactions, 882 derived NAV rows, 4,518 derived cash ledger
rows, 329 reporting snapshots, 835 report-generation records, 48 artifact-release
records, 12 template registrations and four registered subjects.

Restore verification finished at 16:55:28 UTC: 10 minutes 22 seconds after the
selected restore point. This is a conservative observed recovery-check window,
not a guaranteed application RTO or an actual production failover measurement.
No data loss was observed in the checked tables versus the captured baseline;
this is not a guarantee of zero RPO for arbitrary future failures. Latest NAV
remained 30 September 2026.

Evidence: `.work/recovery-20261001/verified-recovery.json`.
After review of these results and the authorization checks, the temporary
server was removed to stop its ongoing charges. Cleanup verified both original
servers still exist. Original backups and all production data were retained.
Evidence: `.work/recovery-20261001/cleanup.json`.

Remaining recovery acceptance: storage-blob/version recovery, complete portal
failover/reconnection timing, and a recurring drill policy. This drill checked
report database manifests, not restoration of every referenced PDF/PPTX blob.

## 2. Dependency-driven workflow — tested foundation; no schedule cutover

Added `workflow_readiness.py` and regression tests for prerequisite chains,
cross-account rejection, incomplete evidence, old successful/no-change runs and
transitive invalidation after source corrections. Expected observation dates
must come from approved calendars, not simply the execution date.

Found the ingestion worker could mark `analytics_rebuild` complete after only
counting imported rows. The local correction prevents that worker from claiming
or certifying analytics tasks. It is NOT deployed to the institutional worker
yet: a real downstream consumer and shadow reconciliation must be completed
before its rollout, otherwise those tasks would legitimately remain queued.

Remaining: durable stage evidence/outbox integration, leases and recovery,
multi-source fan-in, comparison/FX/derived corrections, real consumers,
calendar-aware freshness, shadow-run acceptance and controlled schedule cutover.
Existing timers remain authoritative until then.

### Subsequent continuation: transactional shadow evidence ledger

Implemented `workflow_ledger.py` and additive migration
`054_shadow_workflow_evidence.sql`. This is a shadow-only foundation, **not an
activated replacement for daily timers**. The migration exists in source but
has not been persistently applied or recorded in live `schema_migrations`.
There are still 53 live migrations; the source tree now contains 54.

The ledger retains append-only, tenant/cycle/account-scoped stage completions
with input/output fingerprints, exact prerequisite evidence IDs, producer,
methodology-policy version and observed-through date. A correction appends a
new completion; it cannot overwrite an older one. Dependencies referring to a
superseded completion are rejected transitively, even when the corrected output
hash happens to be unchanged. Returning to a previous value creates a fresh
record and still requires downstream reconciliation.

Cooperating writers use transaction-scoped account/cycle advisory locks and an
expected-previous-ID check. This prevents a late result from overwriting a newer
completion; it is not yet a worker lease/recovery implementation. Identical
current completions are idempotent only while their prerequisite chain remains
valid. Decisions are stored separately and constrained by PostgreSQL to
`execution_authorized=false`, even when readiness is true. No dispatch, Flex
request, retry, approval or activation function exists in this module.

Database-enforced append-only triggers reject UPDATE, DELETE and TRUNCATE.
Both proposed tables use forced tenant RLS and revoke PUBLIC table privileges.
No persistent application-role grants were introduced.

Live PostgreSQL rehearsal `ibkrv2stg-bootstrap-9ri5ggn` passed its initial 21
checks. The final-source rehearsal `ibkrv2stg-bootstrap-l5qcmju` passed **24
checks**, including a second-connection concurrent-lock test, stale transitive
completion rejection, cross-tenant read/write denial, cross-cycle/account
isolation, idempotency, correction/reversion handling, append-only enforcement,
and inability to set execution authority. The temporary NOLOGIN test role had
no writes to application tables outside the two test ledger tables.

Both rehearsals created their tables/function/role only inside a forced-rollback
transaction. Each verified that those objects were absent afterward. No schema
migration registration, persistent data/role change, financial write, report
change or schedule cutover occurred. Private dispatch/readback evidence is in
`.work/workflow-ledger-20261001/`. Final trial source SHA-256:
`ecee26c84979172c5e6381b7f50e40871b89a22a5e85f3e25e01813f5500d884`.
Migration SHA-256:
`5839be5363a9cb84fe3801b48ff4a670f96eded78cced41e54d2d9d6a12bc0f2`.

Full local suite: **535 tests passed**. Source policy check: 383 source files,
54 contiguous source migrations, no prohibited paths or matched secret
patterns. These checks are not a vulnerability certification or financial
acceptance.

Next prerequisite before operational rollout: reviewed producer adapters must
bind evidence to actual completed imports/reconciliation/FX/derived/risk output
values and approved source-specific observation deadlines. A producer's claim
of `validated=true` is not independent financial proof. This initial ledger
models the same-subject stage chain; cross-account derived lineage, shared Flex
sources, multi-source calendars, durable outbox integration and lease recovery
remain required before schedule replacement. Do not populate successful
evidence from job exit codes or row counts alone. Deploying empty ledger tables
by itself would not complete this upgrade.

### Subsequent continuation: real risk-output producer observation

Added `workflow_risk_evidence.py`, a read-only observer of actual saved risk
packages and their consumed deterministic inputs. It requires a tenant-scoped
READ ONLY, REPEATABLE READ (or SERIALIZABLE) transaction. It checks the exact
account/date/methodology identity, current input fingerprint, saved JSON hash,
explicit expected comparison date, ordered/unique comparison dates and a fresh
deterministic recalculation of the complete output. Only the replay build clock
is excluded from the semantic comparison. Optional missing metrics and existing
producer quality warnings remain explicit; they are not silently made green.

This observation has `prerequisites_verified=false` and
`execution_authorized=false` even when the saved output matches. It does not
invent upstream reconciliation evidence or append a successful ledger stage.
Re-running the same financial engine verifies reproducibility, not independent
financial correctness. Raw-source correction propagation, independently
validated crisis components, full historical coverage and approved
source-specific calendars remain separate acceptance requirements.

Live Azure execution `ibkrv2stg-risk-snapshots-so6hdit` **Succeeded**. The
database-enforced read-only probe found all **three active subjects** and
verified their **2026-09-30** packages: U16088571, U16088606 and hong-kong-china
(display name China & Hong Kong). Each saved semantic output hash exactly
matched the fresh deterministic output hash; consumed input hashes were current
and comparison history reached the explicitly scoped 30 September date.
All three retained `producer_quality_warn`. The probe excluded non-active
Alpha World MS and did not change its paused status.

The exact read-only probe source SHA-256 was
`f29b2d6c5c45a3c53e6228351fbaadd290b5d9e70423768daeb06221639041c4`.
It ran on the pinned Python-only risk image
`sha256:2a84e1f5859edf1899c30ef1ffbb4df6989eda68be96051e965a05ba839633af`
using a one-off command override. No persistent image, schedule, secret, role,
schema, financial data, reporting snapshot or document was changed. No external
market-data or Flex request was made. Private evidence:
`.work/risk-producer-20261001/ibkrv2stg-risk-snapshots-so6hdit.json` and the
corresponding `-dispatch.json`.

Added 16 targeted regression tests covering stale/corrupt/wrong-account outputs,
changed values with unchanged row counts, future observations, explicit date
requirements, missing-metric warnings, output disagreement and read-only
transaction enforcement. Full local suite: **551 tests passed**. Limited source
policy scan: **387 source files**, 54 contiguous source migrations, no prohibited
paths or matched secret patterns. This does not certify third-party dependencies.

Next: connect verified ingestion/reconciliation and benchmark/FX/derived
producer evidence, including source calendars and cross-account lineage, before
any persistent shadow-ledger rollout or scheduler replacement. Existing timers
remain authoritative. This one-date rehearsal is not a full daily-cycle or
historical acceptance test, and the eight-upgrade programme remains incomplete.

### Subsequent continuation: independent comparison-source reconciliation

Added `comparison_source_evidence.py` and `probe_comparison_sources.py`. These
are read-only diagnostics, not a deployed scheduler replacement. The loader
requires tenant-scoped READ ONLY REPEATABLE READ/SERIALIZABLE isolation and
explicitly scopes every financial query by tenant/account/benchmark/date. A
missing or ambiguous effective benchmark mapping fails closed.

The checker independently uses 40-digit Decimal arithmetic, rather than calling
the production comparison builder, to verify:

- Comparison portfolio values against `subject_nav_series` NAV values.
- Benchmark levels against stored native index levels multiplied by the same
  day's or latest earlier native-to-reporting-currency FX, no more than four
  calendar days old. Future FX is never used.
- Exact common-date inventory, expected latest observation, benchmark returns
  and benchmark growth-of-100 normalization.
- Portfolio growth accumulation and excess-return arithmetic against the saved
  portfolio returns. This does **not** yet validate those returns' underlying
  external-flow classification or amounts.

Duplicate/ambiguous observations, unreviewed source/currency/status, failed
quality, nonfinite/nonpositive source values, wrong-account comparisons and
missing/stale FX cannot pass. Numerical comparison uses the explicit rounding
bound `max(1e-8, abs(expected) * 1e-10)`. Exact source/output fingerprints remain
sensitive to corrected values even when record counts and dates are unchanged.
No observation can authorize execution or claim ingestion/cash-flow validation.

Azure rehearsal **`ibkrv2stg-risk-snapshots-4pmkz4i` Succeeded**. All seven active
account/benchmark mappings passed through the explicitly scoped **2026-09-30**:

| Subject | Benchmark | Checked common-date observations |
| --- | --- | ---: |
| U16088571 | GSPC | 426 |
| U16088571 | IXIC | 426 |
| U16088606 | HSI | 418 |
| U16088606 | SHCOMP | 412 |
| U16088606 | TAIEX | 410 |
| China & Hong Kong (`hong-kong-china`) | HSI | 416 |
| China & Hong Kong (`hong-kong-china`) | SHCOMP | 410 |

Total: **2,918 account/benchmark observations**, no discrepancies within the
declared tolerance. This is reconciliation against stored database sources,
not confirmation against an independently obtained IBKR statement or market
vendor dataset. Provisional-source and pending cash-flow-reconciliation warnings
remain explicit. The expected date is fixed for this rehearsal; it is not a
general multi-exchange calendar implementation.

The database read-only setting was checked at completion. The one-off command
override preserved the pinned risk image and persistent job configuration; no
NAV, FX, benchmark, risk cache, report, schema or role was written. No external
market-data/Flex request, approval, release, activation or schedule change was
made. Alpha World MS remains paused and was excluded from active-account scope.

Probe source SHA-256:
`af3631837af77e4f00f2432d145220e1065a92c621310a36c28bd2b258c4b45a`.
Private evidence: `.work/comparison-source-20261001/`, execution JSON and its
`-dispatch.json`. The diagnostic launcher now accepts `--probe comparison`;
the existing default risk probe is retained.

Added **12** regression tests. Full suite: **563 tests passed**. Limited source
policy scan: **390 source files**, 54 contiguous source migrations, no prohibited
paths or matched secret patterns. Live migration count remains unchanged.

Next: reconcile external flows/import lineage and derived source dependencies,
then connect correction-aware producer evidence to the shadow ledger. Automatic
invalidation/dispatch and published-snapshot protections still need end-to-end
acceptance before replacing timers. None of the eight upgrades is newly declared
complete by this diagnostic.

### Subsequent continuation: cash-flow policy/return reconciliation

Added `cashflow_source_evidence.py` and a tenant-scoped, database-enforced
read-only rehearsal (`--probe cashflow`). The independent 40-digit Decimal
calculation checks the implemented half-period external-flow formula:
`(closing NAV - opening NAV - net external flow) / (opening NAV + 0.5 * net external flow)`.
Flows are included over `(previous common observation date, current date]`;
opening-date flows are not counted twice.

Physical-account sources follow the current Flex Transfers policy, including
cash conversion and explicit base-currency amounts for security transfers.
Consumed transfers must have a successful, completed last-seen ingestion run.
Derived sources follow the included/external-flow flags in the private cash
ledger and verify native-amount-times-FX against the recorded base amount.
Malformed dates/amounts, missing or invalid consumed FX, duplicate identities,
wrong-account/mixed-benchmark input, failed comparison quality and nonpositive
return denominators cannot pass. This module does not change economic-flow
classification, import data or modify the existing formula.

The first two read-only diagnostic runs (`ibkrv2stg-risk-snapshots-exs7wh9` and
`ibkrv2stg-risk-snapshots-dzpjz79`) stopped on a nonpositive stored FX observation
before completing the physical-account checks. The checker was corrected to
distinguish consumed from unused rates, with a regression test. It still rejects
an invalid rate if a transfer requires it and never silently substitutes an
earlier valid rate. Unused invalid rows remain visible warnings. These failed
diagnostics may cause failure-alert emails; neither attempted an IBKR refresh
or database write.

Corrected rehearsal `ibkrv2stg-risk-snapshots-g5gegp1` emitted all seven successful
mapping results. The final source-inventory rehearsal
**`ibkrv2stg-risk-snapshots-d3fiyyh` Succeeded**, confirming **2,911 return
intervals** across the seven mappings through **2026-09-30**, with no return
discrepancies within `max(1e-8, abs(expected) * 1e-10)`. The NAV/benchmark/FX
source checks were also rerun and passed.

Important limits and findings:

- Both physical accounts have zero stored Flex Transfers rows. A supplementary
  keyword scan of cash transaction types/descriptions for deposits, withdrawals,
  transfers, contributions or redemptions found zero in-period candidates. This
  is a diagnostic scan, **not proof of complete imports or economic
  classification**. A missing upstream section cannot be certified from absence.
- China & Hong Kong has 4,518 in-period ledger entries, with no included external
  flow days under its current flags. This verifies return arithmetic against
  those flags, not the independent correctness of every flag.
- Therefore this live sample contains no nonzero external-flow days. Deposits,
  withdrawals, multiple flows and security-transfer conversion are covered by
  synthetic unit tests, not claimed as live financial acceptance.
- Each physical account contains 190 nonpositive FX observations in the loaded
  account-rate history: IDR/USD 9, PHP/USD 131, and the stored code RUS/USD 50.
  None was consumed by the checked transfer-return calculations. Their original
  source/import representation and possible use by other consumers still need
  investigation. No rate was fabricated, overwritten or deleted.
- The existing physical transfer converter has no maximum prior-FX-age rule.
  The diagnostic reports rates older than four days rather than silently changing
  that methodology. No such consumed-rate warning occurred in this sample.

No execution authority or ingestion-completeness attestation is granted. Source
and reconstructed-flow fingerprints retain correction detection. Raw statement
content, transaction amounts and credentials were not logged. The final probe
source SHA-256 was
`86cfcafeb6c45e01cafd65be0967b9bce45aa2ea0e9458e2ef7648945f01960f`.
Private evidence: `.work/cashflow-source-20261001/`, including the final execution
and dispatch JSON. No persistent job image/configuration, schedule, schema,
financial data, risk cache or published report changed. Alpha World MS remains
paused; no external market-data/Flex requests were made.

Added **14 regression tests**; full suite **577 passed**. Limited source scan:
393 source files, 54 contiguous source migrations, no prohibited paths or matched
secret patterns. Next priorities are explaining the nonpositive historical FX
rows, proving imported section/date completeness against raw stored evidence,
and validating derived source lineage before wiring producer observations into
automatic correction propagation. All eight upgrades remain tracked separately.

## 3. Risk cache — value fingerprints deployed; propagation programme partial

### FX source tracing and shared selection guard — subsequent continuation

Investigated the 190 nonpositive FX records per active physical account (380
total). Read-only Azure probes `ibkrv2stg-risk-snapshots-prpq5ix`,
`ibkrv2stg-risk-snapshots-7hjqid3` and final
**`ibkrv2stg-risk-snapshots-81zyi7w` Succeeded**. Their completed diagnostic
status must not be mistaken for successful source-lineage verification:
**none of the 380 rows had a matching stored parsed Flex record**. There were
zero ambiguous matches; all were missing.

Per account, affected histories were IDR/USD 9 observations (20–29 January
2025), PHP/USD 131 (20 January–6 July 2025), and stored code RUS/USD 50
(20 January–4 May 2025). No position, cash-snapshot, cash-transaction or trade
rows in those currencies were found in either physical account through
30 September 2026. All invalid rows' last-seen run metadata pointed to a
successful `staging-snapshot-import` run for the corresponding account. This
identifies their import path into v2; it does **not** establish whether their
original broker XML, an earlier transformation or the v1.1 source contained the
nonpositive values. No replacement rate was assumed, fabricated or written.

Code inspection confirmed an evidence gap in `pilot_snapshot.ACCOUNT_TABLES`
and `pilot_import`: normalized tables are copied, but `flex_rows`, including the
Transfers section, are not. This explains why snapshot-import completeness
cannot be certified from the live absence of parsed transfers. It does not prove
that real transfers were omitted from the original broker history. Before
workflow cutover, the snapshot contract must preserve required scoped evidence
and section completeness; existing imports need reconciliation against available
v1.1/raw artifacts. The previous cash-flow check remains deliberately labelled
implemented-policy verification, not full economic-flow or ingestion acceptance.

Implemented **source-only** `fx_selection.py`, shared by `report.FxConverter`,
`derived_portfolio._FxRates`, `derived_portfolio._CrossFxRates` and
`monthly_reporting_analytics._FxHistory`. Previously these readers dropped bad
rates when loading history, allowing an older positive rate to become the
apparent latest quote. They now retain unusable observations and raise the typed
`InvalidFxObservation` when the latest eligible quote is nonpositive, nonfinite,
malformed or ambiguous. They do not silently switch to an older quote, reciprocal
or another account after such an invalid preferred quote. An absent direct
series may still use its existing approved reciprocal/fallback logic.

Unrelated invalid currencies do not block valid conversions. Future invalid
observations do not alter an earlier cutoff. A later positive observation
restores selection. Same-currency conversions remain 1.0. The helper keeps
binary-search lookup on histories sorted once by each consumer, avoiding a new
linear scan per valuation. No new staleness threshold or portfolio formula was
introduced. Raw invalid observations are retained for audit rather than deleted.

The final live read-only rehearsal exercised the helper against 2,648 distinct
account currency/date demands from stored position/cash/trade histories:
882 identity conversions, 884 available conversions and 882 unchanged missing
lookups. Every missing lookup was the synthetic `BASE_SUMMARY` label (441 per
account), not an actual currency requiring an FX quote. No valid selected quote
changed and no currently demanded conversion was newly blocked. This is a
scoped input-selection compatibility test, not full report-renderer or
cross-account production release acceptance. Alpha World was not activated.

**Deployment status:** the guard and integrations are implemented and tested
locally, but are **not yet in the website or scheduled job images**. Promotion
must use the controlled application release with consumer error-path acceptance;
the Python-only risk image must not be repurposed for the website/report jobs.
The live probe injected the helper for a one-off read-only check only. All
production schedules, data, reports, identities and approvals are unchanged.

Added 16 regression tests (10 selection/consumer tests, six source-lineage
tests); full suite **593 passed**. Limited source-policy scan: **398 files**,
54 contiguous source migrations, no prohibited paths or matched secret patterns.
Private evidence: `.work/fx-origin-20261001/`. Final probe source SHA-256:
`3c4ad9523469e6f70f2d23aaf3c9b6e145e41555691cecbdc7d93638693ca12a`.

Next: repair and version the snapshot evidence/completeness contract, validate
available historical raw evidence, then promote the FX consumer guard through
the appropriate release. Do not mark full import lineage or financial acceptance
complete on the strength of these diagnostics.

### Snapshot financial-evidence contract — local continuation

Implemented an **opt-in, review-only v3 transfer format** in `pilot_snapshot`
and the small `snapshot_evidence` module. The default build remains v2, so no
existing scheduled import switches formats automatically. CLI opt-in:
`scripts/build_pilot_snapshot.py --include-financial-evidence`.

V3 preserves account-scoped `Transfers` rows and recognized conversion-rate
records from the source `flex_rows` table, alongside the existing normalized
tables. It carries restricted original ingestion-run provenance (IDs, type,
dates, status and raw SHA-256), not raw paths, error messages, credentials or
unrelated AccountInformation records. These are private local artifacts; none
were sent to Foundry, Yahoo or another external service.

Controls implemented and exercised:

- A read transaction binds copied tables/evidence to one SQLite view. The source
  main-file hash is explicitly labelled as such; transferred-value fingerprints
  bind each output table, including its column inventory and duplicate rows.
- Evidence JSON hashes, embedded account identity, date scope, unique identities
  and source-run links are checked. Malformed source dates cannot vanish through
  date filtering. Undated evidence is preserved and flagged; undated/malformed
  normalized observations block v3 creation.
- Unknown format versions, null account IDs and duplicate normalized identities
  are rejected. Same-count changes to financial values fail fingerprint checks.
- A new export is built in an adjacent temporary file and validated before
  atomic replacement. A failed attempt retains the preceding valid export.
- Both versions distinguish structural integrity from financial completeness.
  V3 explicitly retains `source_completeness=unverified`,
  `raw_xml_verified=false` and `reconciliation_authorized=false`. Zero rows do
  not certify that the original query included an empty Transfers section.
- The importer rejects v3 **before connecting to PostgreSQL**, rather than
  silently dropping evidence or treating source run IDs as destination IDs.
  V2 import results now include the explicit legacy evidence warning; import
  success is not reclassified as financial/source-completeness acceptance.

Added 13 regression tests; full suite **606 passed**. Limited source-policy
scan: **399 source files**, 54 contiguous source migrations, no prohibited
tracked paths or matched secret patterns. This is not a full security scan.

Local real-data rehearsal used the existing, read-only
`.work/reconciliation-v11-source-20260925.sqlite`, generated
25 September 2026 at 16:02:31 HKT, with NAV through 24 September. Source SHA-256:
`4b577bee928458d1790cae14a95a7b0434d60425bf8762c2dd080aacc8bfb93a`.
Both account exports passed structure/value-integrity validation:

| Account | Preserved FX records | Transfers records | Output SHA-256 |
| --- | ---: | ---: | --- |
| U16088571 | 516 | 0 | `06395e89334873f38d769f85036cce65f86f6a7542e7db3ea9bb1acccf8075cf` |
| U16088606 | 516 | 0 | `55fa42522800aaf5612a5361c52681dd198781bd7b556f47c7dad8ecb721caa0` |

Private output directory: `.work/snapshot-evidence-20261001/`. Both evidence
inventories span 20 January 2025–24 September 2026, with no undated rows and
complete stored run-metadata links. This does **not** establish every date or
currency was covered. The 190 nonpositive FX rows per account (IDR 9, PHP 131,
stored code RUS 50) also exist in this cached v1.1 export, with zero matching
parsed records by account/date/row hash. The gap therefore predates this v2
rehearsal; original XML and query-section coverage remain unverified. No rate
or transfer was manufactured and no historical financial value was corrected.

**Not deployed:** no Azure resources, schedules, database tables, live imports,
reports or approvals were modified in this continuation. Alpha World MS remains
paused. Next is an evidence-aware importer with destination-run remapping,
tenant-scoped value readback and rollback-only PostgreSQL acceptance, plus
original raw-history/section-completeness verification. Only then consider
enabling v3 in the daily transfer workflow. These additions do not complete
upgrades 2, 3 or 8, or certify full financial reconciliation.

### Evidence-import component — PostgreSQL rollback acceptance

Implemented `snapshot_evidence_import.py` as a separately testable transaction
component. **It is not yet called by the normal importer or scheduled jobs**;
`import_snapshot` continues rejecting v3. This is not a complete replacement
for the normalized-table importer and does not certify historical cash flows.

The source loader requires the externally recorded artifact SHA-256, copies the
verified bytes into a private temporary snapshot, validates its v3 structure and
value fingerprints, and reads evidence from that frozen copy. Bundle validation
runs again before any database operation. Missing source-table evidence,
undated records or incomplete stored ingestion-run lineage block this importer.
Empty evidence remains explicitly unverified, not a no-transfers certificate.

The PostgreSQL component requires an existing caller-owned transaction and uses
a nested savepoint. It checks the session tenant, account ownership, destination
run type/status/start date and exact artifact digest. A transaction advisory
lock serializes cooperating evidence importers for the tenant/account; the
account, run and selected evidence rows are also locked. All target tables are
schema-qualified and all lookups/receipt writes are tenant-scoped.

New evidence receives local destination run IDs. Identical existing rows retain
their original local first/last-seen IDs; source-system run IDs and restricted
metadata are preserved in the import receipt, not reused as foreign keys. Values
are read back and compared field by field, including parsed JSON, rather than
accepted on row counts alone. Conflicting existing values are **not overwritten**.
An exact receipt retry validates current values/provenance; missing or changed
prior evidence requires review. Receipt and evidence writes share the savepoint.
The component does not commit, mark a run successful or advance onboarding.

Local tests: **615 passed** (nine additional tests), plus compiled launcher and
rehearsal scripts. Source-policy scan: **403 files**, 54 contiguous source
migrations, no prohibited tracked paths or matched secret patterns. Frozen-copy
loading of both prior real-data v3 artifacts also passed: 516 evidence rows each,
zero database writes. Those artifacts are dated through 24 September 2026, not a
new current-data capture.

Live Azure execution **`ibkrv2stg-bootstrap-du0b7n3` Succeeded**. Its sanitized
completion record contains **20 passing checks**. The rehearsal created an
isolated schema with tables shaped from the existing account/run/Flex tables,
used synthetic data only, and exercised a temporary non-login, non-bypass-RLS
role with forced tenant policies. All trial DDL/data/role changes were enclosed
in `transaction(force_rollback=True)`; schema and role absence were checked
afterward. No public financial tables or their sequences were written.

Verified: new-row/value readback; destination-ID remapping; source provenance;
no run-success/workflow authority; same-run idempotence; wrong session tenant,
foreign run, wrong digest and wrong account rejection; tenant row isolation;
conflicting-value rejection; partial-insert and failed-receipt rollback; original
value preservation; reuse without rewriting existing local lineage; receipt
drift detection; empty-evidence uncertainty; complete trial-object rollback.

Private evidence: `.work/snapshot-evidence-import-20261001/`.
Dispatched source SHA-256:
`5aedd16f7a961330090601195c7caa9d3f71d38a3d0528c18738aa760c2500fa`.
Runtime: the existing bootstrap image
`ibkr-platform@sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c`;
the reviewed component was injected only for this diagnostic execution. The
launcher used an execution-only override, preserved zero retries and did not
update any persistent job template or schedule. No source financial data were
sent in the rehearsal. No Flex request, published report change, account
activation, persistent role/schema addition or new migration occurred.

Remaining before operational v3 enablement: compose normalized-table and
evidence import in one governed transaction, verify scoped destination values
and existing-archive compatibility, define reviewed correction/staleness
handling, and run combined rollback acceptance. Isolated-schema tests are not
proof of the deployed application's full authorization behavior or historical
source completeness. Original XML/query-section verification and source-value
correction propagation remain outstanding; Alpha World MS stays paused.

### Combined normalized-data/evidence import — rollback acceptance

Implemented `snapshot_atomic_import.py`, composing the preceding evidence
component with all eight normalized account tables and the referenced instrument
catalog in **one caller-owned transaction/savepoint**. This remains a staging
component: the default importer still rejects v3, no scheduled producer calls
it, and it neither commits nor marks an ingestion run successful.

`read_combined_snapshot` verifies the externally recorded artifact digest and
reads financial rows and evidence from the same frozen private copy. Validation
checks the exact nine-table inventory, account/date scope, duplicate identities,
instrument-reference coverage, forbidden destination-run/tenant fields, and
bundle value fingerprints. The database stage requires source/destination
column-contract agreement and the destination account's base currency to match.
The approved account must already exist; this component does not create or
reassign accounts or overwrite account metadata.

Only missing records are inserted. Existing conflicting values stop the whole
stage and require correction review. Existing normalized observation provenance
must belong to the tenant and a successful run (or this current running import).
Instrument discovery timestamps may differ and are retained; all instrument
descriptive fields must agree. No existing record is overwritten or deleted,
including newer/out-of-scope history. Imported rows are read back field by field;
the receipt records per-table value and destination-lineage fingerprints.
Repeats verify both receipts and current values; missing previously staged rows
are not silently recreated. Source completeness remains `unverified` and
`reconciliation_authorized=false`, independently of successful value transfer.

Local suite: **623 tests passed** (eight additional tests). Policy scan:
**405 source files**, 54 contiguous source migrations; no prohibited tracked
paths or matched secret patterns. Both saved real-account v3 artifacts also
passed combined loading locally, with zero database writes:

| Table | U16088571 rows | U16088606 rows |
| --- | ---: | ---: |
| instruments | 87 | 47 |
| trades | 205 | 1,298 |
| cash_transactions | 643 | 1,967 |
| corporate_actions | 3 | 13 |
| position_snapshots | 17,964 | 13,785 |
| cash_snapshots | 437 | 1,748 |
| nav_snapshots | 439 | 439 |
| fx_rates | 20,124 | 20,124 |
| fx_transactions | 0 | 1,406 |

Each also preserved 516 parsed FX evidence rows. These are the previously
recorded 25 September exports (NAV through 24 September), not refreshed data,
and do not resolve the original XML/Transfers completeness gap.

Live execution **`ibkrv2stg-bootstrap-uwisxgo` Succeeded**, with **36 passing
checks**: the previous 20 evidence checks plus 16 combined-import checks. The
expanded trial used 12 isolated tables shaped from the deployed schema and
synthetic values only. Forced tenant policies covered normalized account tables;
the shared instrument catalog had no tenant policy, matching its shared nature.
The test deliberately seeded a late NAV conflict after earlier inserts: new
evidence, instruments, financial rows and both receipts all rolled back, while
the conflicting pre-existing synthetic NAV was retained. Additional checks
verified nine-table value readback, out-of-scope history preservation,
idempotence, account-currency rejection, same-count value-corruption detection,
missing-row review, and the absence of automatic run-success authority.

All trial DDL, rows and role membership were rolled back, with schema/role
absence verified afterward. No public financial table or sequence, current
report, account activation or persistent job configuration changed. Original
16:00 HKT ingestion and zero-retry settings remain untouched. Alpha World MS
stays paused. Private evidence:
`.work/snapshot-evidence-import-20261001/ibkrv2stg-bootstrap-uwisxgo.json`.
Dispatched source SHA-256:
`c78e15423e97b7eac38b6eda53e90d1f7dc72b102460ad9916624752e85dfca1`.
The existing bootstrap image was used with a one-execution code override; no
application image was built or promoted.

**Next:** verify original raw-history/query-section completeness, test candidate
snapshots against current destination values in read-only preflight, and wire
the reviewed combined component into an explicit staging run lifecycle before
controlled release. Automatic correction/staleness handling and downstream
cache invalidation still need acceptance. This trial establishes transactional
behavior on synthetic fixtures, not full historical financial accuracy, load
capacity, or completion of the eight-upgrade programme.

### Subsequent continuation: live value preflight and archived-source sample

Added `snapshot_preflight.py`, a repeatable-read, enforced-read-only comparison
of candidate v3 snapshots with the existing database. It checks account/currency,
all nine normalized-table column contracts, natural identities, values, JSON
semantics and destination ingestion lineage. Missing, conflicting and extra
destination rows are distinguished. It neither imports records nor authorizes
an import. Archive diagnostics verify byte size and SHA-256, restrict blob URLs
to the approved host/container, reject DTDs and distinguish missing Transfers
sections from present-but-empty sections. Logs contain diagnostic counts/hashes,
not raw statements, credentials or financial amounts.

Live execution `ibkrv2stg-account-sync-daily-kf34i72` **Succeeded** using a
one-execution override of the existing account-sync worker. Source SHA:
`89f4423e4504ad584ca13581fe21ba2dcfa73448ed6d5a5da589bf1ac57d6c20`.
The export was generated `2026-10-01T16:02:35+08:00`, SQLite SHA:
`c181220e93c60a629f70ea402a7e41ee013edbde3496b70882b5311b68f78e9d`.
Its age was 14.61 hours, accepted only by the explicit 72-hour diagnostic
tolerance. The normal scheduled sync's 12-hour acceptance policy is unchanged.

For both U16088571 and U16088606 every normalized source row matched the
destination: zero missing rows, zero value conflicts and zero extra rows in
the scoped comparisons. Currency/schema checks and origin-run lineage passed.
Each candidate also contains **521 parsed FX evidence rows absent from v2**;
these 1,042 evidence rows were not inserted. `execution_authorized` remains
false and source completeness remains unverified. This establishes value
compatibility, not source financial correctness or approval to activate v3.
Evidence: `.work/snapshot-preflight-20261001/`.

The export registers 452 raw files. Five archive accesses using the scheduled
account-sync identity returned HTTP 403; its permissions were not broadened.
An additional bounded read using the owner's existing Azure CLI authorization
succeeded without role changes. This second check used the already retained
25 September export (not the latest export), SHA:
`4b577bee928458d1790cae14a95a7b0434d60425bf8762c2dd080aacc8bfb93a`.
All five sampled archive hashes/byte sizes verified (source runs 456, 457, 7,
85 and 141). Both accounts' sampled statements explicitly contained empty
Transfers sections. The January, April and June samples also matched **66 of
the 380 invalid normalized FX observations** by original attribute hash and
decimal-normalized raw rate value. Thus those sampled invalid values exist in
the archived XML; they were not introduced solely by normalization. No rate was
replaced, guessed or deleted. This bounded sample is not proof of all-history
coverage, all invalid values' origin, or absence of transfers across history.
Evidence: `.work/snapshot-preflight-20261001/archive-sample-user-rbac.json`.

Separately reproduced and corrected an FX parsed-evidence key collision in
source: distinct currency pairs on the same day previously received the same
generic snapshot key. New versioned FX keys include pair, effective date,
account, model, section and record type; rates are excluded so corrections keep
their natural key. The normalized currency/date aliases and statement-date
fallback are respected. Unresolved identities retain distinct content instead
of collapsing it. Six new regression tests cover supported record types,
detail-level collisions, pair/account/date/model isolation, corrected values,
aliases, malformed evidence and idempotent SQLite ingestion.

**This parser change is not deployed.** An audited legacy-key transition and
raw-archive evidence backfill must precede rollout: merely changing keys cannot
restore evidence already overwritten, and legacy/new keys must not be treated
as two independent source observations. Existing normalized rates and published
reports remain untouched. The preflight adds 13 tests; the parser adds six:
**642 local tests passed**. The repository policy check passed for 411 source
files and 54 contiguous source migrations (still 53 live migrations). This
limited check is not a vulnerability certification or financial/visual acceptance.

Next: extend raw-source coverage with existing authorized read access, plan and
rehearse the legacy evidence-key transition, then connect the combined importer
to an explicit staging lifecycle. Do not give the scheduled worker broader
archive permissions merely to bypass its current boundary. No schedules,
production images, live migrations, account activations or report approvals
changed in this continuation. Alpha World MS remains paused.

### Subsequent continuation: full frozen-inventory FX evidence rehearsal

Added `archive_evidence_rehearsal.py` and
`scripts/rehearse_archive_evidence.py`. This is a read-only source verification
and in-memory transition rehearsal, **not a production migration**. The input
is the same SHA-pinned 25 September v1.1 SQLite export above, covering source
history through 24 September; it is not the latest production database.

The checker reads the registered archive inventory using existing user RBAC,
verifies tenant/successful-run provenance, stored byte counts, exact content
hashes and blob ETags, and rejects DTDs, conflicting FX identities and unresolved
keys. Expected observations match by account, date, currency pair, normalized
value, original attribute hash and exact successful source run. Failed files
leave no partial candidate evidence. Logs/evidence contain counts, hashes and
diagnostic metadata, not raw statement content, amounts or credentials.

The transition model retains canonical FX keys, distinct content versions and
separate source-run links. Surviving legacy keys become explicitly verified
aliases to matching canonical versions rather than additional observations.
Corrections retain old versions. Replaying identical content is idempotent.
The original legacy rows and their first/last-seen metadata are not mutated;
how to persist the transition in production remains a separate migration gate.

Two diagnostic issues were identified and corrected without production changes:

- The first full pass had four Azure CLI credential acquisition failures under
  concurrent reads (runs 1, 4, 7 and 8). The reader now serializes token refresh
  and caches the storage-scoped SDK token only in memory. The failed evidence
  was retained. No Azure role or storage permission was changed.
- The second pass verified all 446 registered archives, but correctly withheld
  acceptance for 390 normalized FX rows and ten legacy rows whose last-seen
  ingestion runs have no separate `raw_reports` entry. Runs 29 and 432 are
  successful repeat ingestions of the exact content registered by runs 4 and
  35 respectively. The new explicit reuse rule requires matching tenant,
  successful/completed ingestion and identical recorded content SHA-256; it
  never guesses lineage from matching dates or financial values. Each repeat
  ingestion retains its own provenance link, not a duplicate content version.

Private diagnostic records are retained in
`.work/snapshot-preflight-20261001/archive-full-rehearsal.json` and
`archive-full-rehearsal-r2.json`. These first two runs did **not** pass the
rehearsal and must not be used as migration approval.

Final rehearsal `archive-full-rehearsal-r3.json` **passed**, completed
`2026-10-01T23:02:59.836224+00:00` (2 October in local BST/HKT):

- **446/446 registered archives** verified; zero read/parse/identity failures.
- **448 source-run/archive links**, including the two verified repeat ingestions.
- **40,248/40,248 normalized FX observations** matched their original source
  run, currency pair, date, attribute hash and value; zero unmatched.
- **1,032/1,032 legacy evidence aliases** matched canonical source versions;
  zero unmatched. Candidate inventory: 40,248 keys/content versions and 84,006
  source-lineage records. A larger evidence inventory is not new financial data.
- **380/380 invalid normalized observations** match the original archived XML.
  They were preserved for diagnosis, not turned into valid rates. Existing
  fail-closed FX selection and unresolved-source safeguards remain necessary.
- Scoped Transfers sections were present and empty throughout the verified
  archive inventory. The summary's 448 statements per account includes the two
  repeated ingestions; it does not mean 448 distinct archived files. This is
  evidence about these files, not independent certification that the broker
  query included every required transfer type or that no files were omitted
  before the inventory was created.
- Two repeated replay checks left the candidate inventory unchanged. The input
  SQLite's SHA-256 remained unchanged. No production database writes, raw-blob
  writes, raw downloads retained on disk, new Flex calls, or permission changes.

Candidate version-manifest SHA-256:
`fc9d5b334802509727e12c04b7bae9c2d01c4cb95c929c6d81890846b89e22db`.
Candidate lineage-manifest SHA-256:
`e5cc1580d85bef1539cc29570342e1c903eaaf4968c3c1699fc7b18bff24bc59`.
These are hashes of the in-memory rehearsal inventories, not evidence of a
persisted production migration or a released report package.

**654 local tests passed**, including 12 new archive/transition/credential
regression tests. Repository policy check: 414 source files, 54 contiguous
source migrations; live schema remains at 53. No images, schedules, approvals
or financial formulas changed. All eight upgrades are not yet complete.

Next controlled work: extend the same checks through the latest export delta,
implement an additive persisted evidence-version/legacy-alias transition with
rollback acceptance, and wire its verified receipts into the combined staging
import lifecycle. Preserve legacy provenance and published snapshots; do not
deploy the new parser alone as a substitute for this transition. Financial
source/query completeness and visual acceptance remain separate gates, and
Alpha World MS remains paused.

### 2 October continuation: latest-source verification and additive schema rehearsal

Captured the latest registered export using existing read-only user RBAC.
Manifest generation time remains `2026-10-01T16:02:35+08:00`; the SQLite digest
is `c181220e93c60a629f70ea402a7e41ee013edbde3496b70882b5311b68f78e9d`.
The capture verifies the manifest before/after download, blob ETag and content
SHA-256. Its 72-hour diagnostic tolerance does not change scheduled import
freshness policy. Evidence: `.work/archive-evidence-20261002/capture.json`.

Full latest-inventory rehearsal **passed**:

- 452/452 registered archives verified, with 454 source-run/archive links.
- 40,638/40,638 normalized FX observations and 1,042/1,042 surviving legacy
  evidence aliases matched exact source lineage/content; zero unmatched.
- All 380 invalid normalized FX observations still match original archived
  values; they remain invalid, not repaired or approved for analytics.
- 40,638 candidate canonical versions and 86,580 source-lineage records.
- No read/parse/key failures; replay idempotence and unchanged source hash passed.

Compared directly with the prior frozen export: six additional registered
archives and 390 additional FX observations, **zero removed rows/archives and
zero changed existing FX values/content hashes or registered archive hashes**.
312 existing FX rows have newer last-seen run IDs, illustrating why ingestion
provenance must be retained separately from immutable content versions.
Evidence: `latest-rehearsal.json` and `export-delta.json` in the same folder.
This establishes registered-inventory traceability, not independent broker-query
completeness, correction of invalid rates, or full financial acceptance.

Implemented additive source migration `055_fx_evidence_versions.sql` with five
tables: verified source-run metadata, immutable FX content versions, source-run
observations, legacy aliases and non-authorizing batch receipts. Composite
foreign keys bind observations and aliases to their exact account/tenant/version
and source run. One run cannot contain two values for the same canonical key.
Original legacy first-seen metadata can be retained without pretending it is
the first observation of a corrected currency-pair key. Repeated source runs
can reference one content version without creating duplicate financial values.

All five tables force row-level security, check account ownership, revoke PUBLIC
table access and reject UPDATE, DELETE and TRUNCATE. Receipts cannot authorize
execution. No application-role grants or active-reader cutover are part of this
migration. It does not update legacy tables, normalized FX or published reports.
`fx_evidence_contract.py` additionally validates original string attributes,
account/date/type, canonical key and content hash before a future importer may
write a version. These checks are separate from archive-byte/run verification;
SQL structure alone is not proof of source validity.

Live isolated-schema rehearsal **`ibkrv2stg-bootstrap-xxqcp11` Succeeded** with
**30 checks**, including successful synthetic persistence, rejection of unknown
source/version references, retained corrections, no duplicate version for a
repeat run, preserved legacy first-seen metadata, cross-account/tenant denial,
all 15 update/delete/truncate prohibitions, late-failure rollback and final
schema/role absence. Every test object/row/role was forcibly rolled back. Only
synthetic data were inserted into the isolated schema; no public financial table
or production sequence was written. The existing maintenance image and a
one-execution override were used, not a scheduled-template update.
Dispatch source SHA-256:
`c301ad6348152932558a7f7d4d45e7676101014d71007714e6c9add9a793f2e9`.
Evidence: `.work/fx-evidence-schema-20261002/`.

**661 local tests passed**; policy scan passed for 420 source files and 55
contiguous source migrations. Live schema still has 53 migrations: neither 054
nor 055 was persistently installed by this rehearsal. The parser correction,
new evidence schema and semantic validator are **not deployed to normal
ingestion**. This is schema/rollback acceptance, not completed migration of real
evidence or completion of all eight upgrades.

Next: implement the verified archive-to-bundle writer and atomic persistence
component, check real candidate receipts in an isolated staging lifecycle, then
perform a controlled deployment/backfill and reader cutover. Keep prior evidence
available for rollback; never re-key live rows in place. Existing schedules,
zero-retry settings, financial formulas and report approvals remain unchanged.
Alpha World MS remains paused pending its separate acceptance gates.

### 2 October continuation: atomic FX-evidence importer

Implemented `fx_evidence_bundle.py` and `fx_evidence_import.py`. The builder
accepts verified archive bytes and successful source-run metadata, scopes every
version to the requested account/tenant, verifies raw/content hashes, rejects
conflicting run/key values, and binds surviving legacy aliases to exact source
observations. It produces deterministic, fingerprinted internal bundles.
The bundle reader pins both artifact bytes and bundle contents, rejects duplicate
JSON members and checks the expected tenant/account before returning data.
These are trusted-worker interfaces, not a new user-upload/API permission.

The staging component requires a caller-owned PostgreSQL transaction, checks
session tenant and account ownership, obtains an account-scoped advisory lock,
and inserts runs, versions, observations and aliases inside a savepoint. Every
stored value is read back and compared, including JSON contents and normalized
completion timestamps. Conflicts stop the batch without overwriting anything.
A non-authorizing receipt is inserted only after all four groups verify. Repeat
imports preserve the original receipt and add no rows. Missing previously
receipted evidence is rejected for review, not silently recreated. The component
does not commit, mark an ingestion successful, advance workflow, activate a
portfolio or release a report. Archive verification does not make an invalid
FX rate suitable for analytics.

Adjusted the still-undeployed migration 055 alias primary key to include the
source snapshot digest. A later verified snapshot can now retain its own alias
provenance alongside the earlier one, instead of either overwriting history or
failing merely because a new snapshot was captured. This creates no duplicate
financial observation: versions and observations retain their existing keys.

Live isolated trial **`ibkrv2stg-bootstrap-i1sagdo` Succeeded** with **42 checks**:
the prior 30 schema/security checks plus 12 component tests. Verified all four
groups were inserted, receipt authority stayed false, repetition was idempotent,
new snapshots retained prior aliases, and a late alias conflict rolled back new
runs/observations and wrote no receipt. Original receipts remained unchanged.
The trial also detected same-row-count content corruption and refused to repair
a missing previously staged alias. Corruption was simulated only by the
temporary fixture owner inside the isolated schema; no production trigger was
disabled. All trial rows, schema and role were forcibly rolled back and their
absence checked. No persistent migration or scheduled template was changed.
Dispatch source SHA-256:
`0c1eb07ed2ad39947d7ca2a65a5bcdddce2c4bbdab90b09aa02499704a4639b3`.
Evidence: `.work/fx-evidence-schema-20261002/ibkrv2stg-bootstrap-i1sagdo.json`.

**671 local tests passed** (ten new tests for assembly, hash/scope validation,
source provenance and transaction preconditions). The artifact-reader guards
were added and tested locally after the live trial; the atomic staging behavior
was exercised live as above. Policy check passed for 423 source files and 55
source migrations. Live migrations remain at 53. Parser, evidence schema and
importer are still not connected to normal ingestion.

Full real-data bundles were then assembled from all 452 verified registered
archives plus the two verified repeat-run links, and saved only in private
`.work/archive-evidence-20261002/bundles/`. Each physical account bundle contains
454 source runs, 20,319 content versions, 43,290 observations and 521 legacy
aliases. These are FX evidence records, not new portfolio financial values.
No raw XML was retained locally and no bundle was imported into PostgreSQL.

Artifact SHA-256 values:

- U16088571: `004f396fb118d5ef284c20bacc604cc1cbd7be9b67f6476e61ac11a4d70617e6`.
- U16088606: `f908d513eade699e6a28fa621528eab6b5c0ab2cee45a10cd07a41cf6b53322c`.

Both 16,047,992-byte artifacts subsequently passed the independent file-reader
path: artifact digest, bundle digest, expected tenant/account, all version
contents, references and alias checks. Read/validation took approximately 0.79
and 0.83 seconds locally; this is **not** database insertion timing. Evidence:
`bundle-assembly.json` and `bundle-readback.json` in the same private folder.
The original assembly record's `completed_at` marks archive verification before
bundle construction; the separate readback record confirms completed artifacts
at `2026-10-01T23:28:42.909078+00:00` (2 October locally). The script now separates
archive-verification and overall completion timestamps and emits per-account
assembly progress for subsequent runs. The earlier evidence was retained.

Next: full-size isolated staging/throughput acceptance, optimize database batch
I/O if required, connect receipts to the existing import lifecycle, then review
controlled rollout/backfill and reader cutover. Synthetic rollback acceptance
does not prove many-account throughput or complete all eight upgrades. No
production image, persistent migration, financial record, published snapshot,
report approval or schedule was changed. Alpha World MS remains paused.

### 2 October continuation: bounded FX-evidence batch I/O

Replaced per-record lookup/insert/readback with bounded SQL batches (default
500 records, accepted range 1–2,000). Each query retains explicit tenant/account
and applicable source-namespace scope. Every returned value is compared with
the pinned bundle; duplicate identities, conflicting contents and missing
previously receipted evidence still fail closed. Account locks, savepoints,
append-only rules and receipt semantics are unchanged. Query counts and group
timings are returned separately from immutable receipt contents.

Added regression checks for batch limits, chunk boundaries, database return
order, explicit scope predicates and same-count content corruption. **674 local
tests passed**; policy check passed for 423 source files and 55 source migrations.

The isolated schema verifier now also exercises two synthetic accounts, each
matching the real bundles' cardinalities: 454 runs, 20,319 versions, 43,290
observations and 521 aliases (64,584 evidence records per account). It checks
full insertion/readback followed by an identical repeat with no new rows or
receipt changes. Synthetic currency labels and values are used; no real account
bundle is sent to this trial. This is sequential full-size component acceptance,
not concurrent many-account load testing or financial reconciliation.

Trial `ibkrv2stg-bootstrap-egmd9jt` was dispatched with source SHA-256
`c9a96dcc6d7b7e8c7832e1db6304cadc0f724741058f97a01782fcba497b7ec5`.
The execution **Succeeded with 53 checks**, including full-size insertion,
identical repeated receipts, zero duplicate rows, the prior security/conflict
checks, and confirmed absence of the temporary schema and role after forced
rollback. Evidence:
`.work/fx-evidence-schema-20261002/ibkrv2stg-bootstrap-egmd9jt.json`.
The reader now returns separate progress records while retaining the final
verification record as the acceptance condition.

Measured sequential timings (seconds):

| Synthetic account | Initial insert and full readback | Repeat full verification |
| --- | ---: | ---: |
| trial-scale-A | 100.957 | 90.702 |
| trial-scale-B | 106.103 | 92.957 |

Each initial import used 393 evidence-group queries; each repeat used 131.
The prior per-record implementation would require 193,752 and 64,584 respective
group queries at these cardinalities. Those are structural query-count
comparisons, not a measured runtime speedup; fixed transaction/receipt queries
are excluded. Version/observation handling dominates the measured runtime.
These results establish a background-history baseline, not a website latency
target or proof that no further query-plan optimization is needed.

No persistent migration, normal ingestion integration, financial-data correction,
production image, scheduled job configuration or report approval was changed.
Alpha World MS remains paused. Real candidate staging, lifecycle integration,
controlled backfill and reader cutover remain separate outstanding steps.

### 2 October continuation: run-bound FX staging lifecycle

Added `fx_import_lifecycle.py`. A trusted caller must prepare an explicit
`staging-fx-evidence-import` run with an exact contract binding tenant, account,
source namespace, source snapshot, artifact/bundle SHA-256, observed date range
and record counts. Staging verifies the existing transaction/session, account
ownership, run type/status, dates, artifact hash and stored contract before
calling the evidence importer. The same savepoint encompasses evidence writes,
the batch receipt and the ingestion-run receipt. Receipt conflicts fail closed;
there is no overwrite or silent repair. Full run readback checks that unrelated
reconciliation state and all other run fields remain unchanged. Exact repetition
does not rewrite the receipt.

The component does not create runs, commit, mark a run successful, dispatch
downstream analytics, certify source completeness, activate accounts or release
reports. Its recorded state is `evidence_staged` with both execution and
reconciliation authority false; the containing run remains `running` for its
separate orchestrator to resolve. This is a reviewed staging component, not a
new scheduled ingestion cutover or UI endpoint.

Live isolated execution **`ibkrv2stg-bootstrap-n0y8r6t` Succeeded with 57 checks**:
43 prior schema/component checks and 14 lifecycle checks. Verified exact receipt
binding, repeated imports, unrelated-state preservation, rejection of incorrect
account/artifact/completed/foreign-tenant runs, and rollback of new evidence and
its batch receipt after a late ingestion-receipt conflict. Earlier receipts
remained valid. The temporary schema and role were absent after forced rollback.
This component-only run did not repeat the prior full-size synthetic workload.
Source SHA-256:
`5d157d93b23eb6e3339d036dc65c2a00232c9cc593f345cebda24c0e55249269`.
Evidence: `.work/fx-evidence-schema-20261002/ibkrv2stg-bootstrap-n0y8r6t.json`.

**684 local tests passed**, including ten new lifecycle regression tests.
Policy check: 426 source files, 55 source migrations; live migrations remain 53.
The prepared real-bundle trial extension also compiles, but has **not run live**.
It downloads only pinned private input artifacts, checks ETags/byte counts and
both digests, stages under isolated account/run fixtures, repeats full readback,
and forcibly rolls back. It does not write production financial tables.

Real-data trial currently blocked by a specific storage permission: the owner's
existing identity can read the private `input-snapshots` container's metadata, but its first
content-addressed upload returned **HTTP 403 AuthorizationPermissionMismatch**
at `2026-10-01T23:58:07Z`. No role grant, SAS, account-key workaround or alternate
credential was attempted. `prepare_fx_trial_inputs.py` deliberately refuses
overwrites and verifies stored bytes after successful upload. The two reviewed
real bundles remain locally available; no real-bundle database trial was started.
An exact-path blob-existence check also returned 403, so blob-level read access
and independent confirmation of remote absence were not established. There was
no successful upload response or completed private-input manifest.
The bootstrap managed identity's read access to these new objects still needs
verification after they exist; owner container-metadata access does not establish that.

Next prerequisite: owner-approved, temporary, container-scoped upload access (or
an owner-performed upload of the two exact pinned artifacts), then the real-data
rollback trial. Persistent migration/backfill and analytics-reader cutover remain
separate release gates. Existing production images, financial data, published
reports, approvals, schedules and zero-retry settings are unchanged. Alpha World
MS remains paused. This checkpoint does not complete all eight upgrades.

### 2 October continuation: approved persistent input access and real-bundle trial

Owner explicitly approved Storage Blob Data Contributor for
`anthony.ying@wealthryse.ai` and instructed that it need not be removed afterward.
Applied and independently listed the persistent assignment at **only**:
`ibkrv2stgauhecwc4itaoo/blobServices/default/containers/input-snapshots`.
No subscription/resource-group/storage-account-wide assignment was added.
Principal object: `8617fd1c-2280-40f2-92dd-45686e9d686c`.
Role definition: `ba92f5b4-2d11-453d-a403-e96b0029c9fe`.
Assignment: `4ff1af44-48c5-46ab-8f28-75fac337ee31`.
This supersedes the preceding temporary-access proposal; do not automatically
remove this owner-approved grant after the trial.

Both frozen real-account FX bundles were uploaded with overwrite disabled to
the existing non-public `input-snapshots` container and independently downloaded
with pinned ETags. Exact file SHA-256 and byte counts matched: **16,047,992 bytes
per account**. Paths are content-addressed below the platform tenant's
`fx-evidence-trials/<account>/<artifact-sha256>.json` prefix. Artifact/bundle
digests are unchanged from the prior verified assembly. The private input
manifest is `.work/fx-evidence-schema-20261002/real-inputs.json`, prepared at
`2026-10-02T00:13:24Z`. The blobs are retained private diagnostic inputs, not
published reports or raw IBKR XML; no existing blob was replaced.

Read-only inspection found that `ibkrv2stg-bootstrap` uses the existing
`ibkrv2stg-ingestion-identity` user-assigned identity, which already has Storage
Blob Data Contributor on this storage account. No worker grant was added.
The one-off trial explicitly selects that identity via its observed client ID;
it does not fall back to other credentials. Its normal job template, timeout,
image, identity and zero-retry configuration are not updated. The launcher now
records a hash of persistent identity/configuration/template settings and
compares them during result readback.

Started isolated real-bundle execution `ibkrv2stg-bootstrap-ov5j7cq`, source SHA:
`8740d2cc1f9909564db98d68a09d15b4ad887aca97ab09c2d750753671f4a076`.
The input loader verifies private container status, exact account/path scope,
ETags, byte counts, artifact and bundle hashes, source snapshot identity, and
uses temporary files that are removed after validation. Added six local
transport tests, including rejection of public containers, changed metadata,
changed contents and cross-account paths.

Execution **Succeeded with 69 checks**: 57 schema/component/lifecycle checks and
12 real-bundle checks. Each account staged and read back 454 runs, 20,319 FX
versions, 43,290 observations and 521 aliases, then repeated full verification
with zero new rows and identical receipts. The run/receipt bindings and explicit
non-authorizing status were verified. All temporary database rows, tables,
schema and trial role were forcibly rolled back; absence was checked. Actual
artifact access by the selected worker identity succeeded with existing RBAC.

| Real frozen account bundle | Initial staging/readback (seconds) | Repeat verification (seconds) |
| --- | ---: | ---: |
| U16088571 | 107.051 | 93.608 |
| U16088606 | 109.085 | 95.003 |

Evidence: `.work/fx-evidence-schema-20261002/ibkrv2stg-bootstrap-ov5j7cq.json`.
Persistent job settings matched the pre-dispatch hash
`004c5cf7dbf10633914e914760360ceb41b7875aacc28850da0d99dae36ad361`.
**690 local tests passed**; policy check passed for 427 source files and 55
source migrations. No persistent migration was installed (live remains 53).

This establishes isolated real-bundle staging and repeatability, not independent
certification of broker query completeness, valid financial FX rates or a
production backfill. The previously identified invalid source observations
remain evidence, not approved analytics. No financial values, published reports,
normal ingestion schedule, zero-retry setting or production image was changed.
Alpha World MS remains paused. Only the explicitly approved owner role and the
two private content-addressed input blobs persist from this continuation.

Next: prepare the controlled schema/backfill release and reader transition,
with source-freshness revalidation, retained legacy evidence, matching receipts
and explicit rollback/acceptance gates. All eight upgrades remain a programme
in progress; this resolves the preceding storage-access blocker.

### 2 October continuation: deployment-default privilege gap closed in source

Read-only rollout preflight `ibkrv2stg-bootstrap-hqmtqyu` succeeded with
`transaction_read_only=on`. It confirmed 53 live migrations (latest 053), no
existing public workflow/FX evidence objects, and the migration owner's actual
public-schema default ACLs: `ibkr_stg_app` inherits SELECT/INSERT/UPDATE/DELETE
on new tables and EXECUTE on functions; `ibkr_stg_web` inherits SELECT and
EXECUTE. Thus the earlier migrations' `REVOKE ... FROM PUBLIC` alone would not
keep these new objects restricted to their owner. This was a **pending-release
permissions gap**, not evidence that the absent tables had exposed live data.
Preflight evidence:
`.work/fx-evidence-schema-20261002/ibkrv2stg-bootstrap-hqmtqyu.json`.

The latest registered v1 manifest was also reread without downloading another
database or calling Flex. It still identifies the 1 October 16:02:35 HKT export,
SHA `c181220e93c60a629f70ea402a7e41ee013edbde3496b70882b5311b68f78e9d`, matching
the verified real bundles. This is an inventory/source-identity check using the
diagnostic age policy, not a relaxation of normal scheduled freshness rules.

Changed only the **unapplied** migrations 054/055 to remove all direct named
non-owner grants on their seven tables and two guard functions as well as
PUBLIC access. No installed migration checksum was edited. Current source SHA:

- 054: `d071f1f77d376fa12b7fe881fb55af56c41983774127ad3c4c1cc0c899de7e9d`.
- 055: `05e61dfb1add3cb3952b5246ab74dca6ce6beb2bda9808874403ed52d088059e`.

Added `evidence_acl.py`: a transaction-bound, exact-object/schema-scoped
quarantine with explicit identifier quoting and complete ACL readback. It
revokes no grants on unrelated objects and authorizes no runtime access. A
later reviewed evidence reader/writer cutover must deliberately replace this
owner-only quarantine. Owners/administrators remain outside the application
role boundary; this does not restrict database administrators.

Updated the bootstrap source to commit its compatibility grants and final
evidence quarantine in one transaction, instead of exposing intermediate grants
through autocommit. This prevents a future ordinary bootstrap from silently
restoring application access to the protected evidence objects. The source
change is **not yet deployed**; the production bootstrap was not executed.

Isolated rehearsal **`ibkrv2stg-bootstrap-ety4x8m` Succeeded with 64 checks**.
It recreated named default table/function grants in the temporary schema,
applied both pending migrations, verified all protected grants absent, simulated
a later broad bootstrap grant, then verified quarantine, unchanged control
table/function access and idempotence. The previous schema/component/lifecycle
checks also passed. All test roles, default ACLs, schema and rows rolled back;
the schema/role absence check passed. Persistent job settings matched the
pre-dispatch hash. Source SHA:
`5dbf27d6f1842cfb1689f8f973c4f261f53dbe09e2ac0ae8066fcd9ef1a8429c`.
Evidence: `.work/fx-evidence-schema-20261002/ibkrv2stg-bootstrap-ety4x8m.json`.
This was component/security acceptance, not a rerun of the full-size real-data
trial against these revised migrations.

**698 local tests passed**, including eight new ACL regression tests. Source
policy check passed for 430 files and 55 contiguous source migrations. Existing
production financial data, reports, images, schedules, zero retries and live
permissions were unchanged. The owner's separately approved persistent
`input-snapshots` grant remains intact; Alpha World MS remains paused.

Next: bind the revised migration hashes and current-source evidence into the
controlled release/backfill, retest the exact candidate, and only then apply
the additive schema with a separately reviewed reader transition. The rollout
must not use the old pre-quarantine migration checksums or the general bootstrap
script as an unreviewed shortcut. This closes a deployment prerequisite but does
not complete any remaining end-to-end programme acceptance by itself.

### 2 October continuation: revised real-bundle acceptance and archive provenance

The revised 054/055 candidate, including the named-default-ACL fix and bootstrap
quarantine component, passed its real-data rollback rehearsal:
**`ibkrv2stg-bootstrap-zm4zjjm` Succeeded, 76 checks passed**. Executed source SHA:
`ddf805c99f40b09577fa665d7fcfc0f65d41cb25a2664732f29868d1999d987c`.
The dispatch used the unchanged diagnostic image and settings recorded in the
preceding trials. Evidence:
`.work/fx-evidence-schema-20261002/ibkrv2stg-bootstrap-zm4zjjm.json` and its
`-dispatch.json` companion.

Both real, hash/ETag-pinned account bundles were exercised: U16088571 and
U16088606, each with 454 source runs, 20,319 versions, 43,290 observations and
521 aliases (64,584 records per account). Initial staging took 110.500/113.101
seconds; exact reruns took 97.286/96.735 seconds and inserted no duplicate
evidence. Both retained their expected receipts and non-authorizing state.
The source snapshot and input hashes match the earlier verified real-input
record. `full_size_exercised: false` refers to the skipped synthetic workload;
the two real bundle results are explicitly recorded separately.

The trial confirmed forced rollback and absence of the temporary schema and
role. Persistent job settings were unchanged. **No public migration was applied,
no normal reader/parser was switched, and no production financial data or report
approval was changed.** Source completeness remains unverified; import success
does not certify that the Flex query supplied every required source section.

For upgrade 4, extended release evidence validation to accept actual frozen
source-archive bytes and their verified per-file inventory/tree instead of
requiring a fictitious clean Git revision for the chosen Azure archive workflow.
Archive-mode gates must match the image, source archive/tree, build inputs,
templates and migrations. Missing scans, different-source results, tampering,
ambiguous provenance and invalid rollback identities fail closed. This validator
does not authenticate approvals or deploy; the protected Azure release controller
and trusted gate-artifact verification still need implementation. See the release
foundation record for exact boundaries.

**708 local tests passed**, including ten new release-provenance regression
tests; source policy check passed for 430 files/55 source migrations. The
407-file source archive SHA is
`ec35d9a1e8784b1fd50f9d956551c49dfcc09dcf466f2679dd40ceb56c8bec6a`.
Create-only upload and version-pinned readback succeeded for the source, record
and receipt in the existing private release containers. Receipt version:
`2026-10-02T01:22:13.8433289Z`. State remains **archived_not_approved**, not
build-ready; dirty-tree provenance is retained. No access grant was changed.

Next: bind verified build/scan artifacts and a protected approval to this exact
candidate, implement the separately controlled additive schema/backfill rollout,
then verify the reader transition before enabling downstream workflow execution.
This is progress on upgrades 2/3/4/5, not completion of all eight. Alpha World MS
remains paused; ingestion schedules, zero retries and published reports remain
unchanged. No manual owner action is required for the completed trial.

The risk worker now fingerprints the actual comparison rows, FX-converted tail
positions, risk-free observation and loaded crisis result. Same dates/counts
with corrected values no longer reuse the old cache. Cached JSON integrity is
checked before reuse. Missing/failed/corrupt-hash entries are recalculated.
Each date is read with repeatable-read consistency. Historical `--all-dates`
mode now checks input fingerprints instead of blindly skipping existing dates.

Live candidate image:
`sha256:a3f878df32c6b708448b4e67c2b2666bad8b1fc025dd265710ab1bfb1d4d7463`.
Build `ck7n` passed the non-root reporting-runtime build smoke test.

- `ibkrv2stg-risk-snapshots-xkw5vcr`: succeeded; rebuilt four latest snapshots.
- `ibkrv2stg-risk-snapshots-l4oh2s0`: succeeded; four unchanged snapshots reused,
  zero rewrites/bootstrap reruns.
- Pinned only `ibkrv2stg-risk-snapshots` to this verified digest, retaining its
  16:55 HKT schedule, zero retries, environment, identity and capacity; the
  non-image configuration hash matched. Previous image saved in
  `.work/recovery-20261001/risk-release.json`.

Important remaining work: automatic invalidation of all affected historical
dates and raw-source corrections propagated through comparison/FX/crisis
producers. Input fingerprints identify changes to consumed inputs; they cannot
repair an upstream producer that has not yet incorporated a correction. No
published reporting snapshot or approved artifact was rewritten. Composite
crisis acceptance and Alpha World activation remain separate gates.

### Subsequent cache-integrity hardening and live historical sample

Added explicit cache payload identity, structure, comparison-version and
non-finite/JSON validation. Malformed JSON now requests a rebuild rather than
aborting the date loop during cache reuse. This source change passed tests and
was initially exercised only in one-off Azure diagnostics. It was **not yet in
the persistent scheduled image at that checkpoint**; the renderer-free release
below subsequently deployed it. No risk command was changed.

Read-only execution `ibkrv2stg-risk-snapshots-45w5vn8` inventoried all 1,327 NAV
dates across the three active subjects: 443 U16088571, 443 U16088606 and 441
China & Hong Kong. All dates had a cache entry; presence alone is not acceptance.
First/middle/latest sampling found three latest caches current, five historical
fingerprint mismatches and one failed-quality opening-date cache.

Enhanced read-only execution `ibkrv2stg-risk-snapshots-divpqtn` recalculated the
six non-current samples **in memory**. All six matched the saved metrics,
tail-contribution, crisis, risk-free and quality-status sections (ignoring only
generation timestamps). Thus those five hash mismatches did not demonstrate
changed financial outputs; legacy fingerprint-format differences remain a
possible explanation. The U16088571 opening date, 20 January 2025, has zero
comparison rows and unavailable metrics, not invented zero-valued metrics.
U16088571 also had no crisis replay for sampled 25 November 2025; this is an
upstream historical coverage gap, not something a risk-cache rehash repairs.

Evidence is private under `.work/risk-cache-audit-20261001/`. All three audit
executions used enforced read-only repeatable-read transactions; no cache,
NAV, reporting snapshot or PDF/PPTX was changed. This is a sampled same-engine
consistency check, **not** independent IBKR reconciliation or acceptance of all
historical dates. Full propagation, bounded backfill and upstream crisis
coverage remain outstanding. Suite after this continuation: 512 tests passed.

### Renderer-free risk worker and historical replay preflight — deployed / partial

ACR build `ck7p` succeeded using `Dockerfile.risk` and an allowlisted 76-file
source-only context. The Python base is digest-pinned, Python packages use the
hashed lockfile, `pip check` passed, and the image's build smoke runs as UID
10001. No Node renderer, client document or report template is copied. Runtime
checks verify source-manifest file hashes and synthetic cache acceptance,
malformed JSON rejection and cross-account rejection. This is NOT an OS or
dependency vulnerability certification, nor completion of the full release
pipeline.

Verified immutable image:
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-risk-worker@sha256:2a84e1f5859edf1899c30ef1ffbb4df6989eda68be96051e965a05ba839633af`.

Two live executions, `ibkrv2stg-risk-snapshots-nwwgvnh` and
`ibkrv2stg-risk-snapshots-44txkoi`, succeeded against that exact image and the
normal risk command. Each reused four latest snapshots and wrote zero snapshots.
The narrow promotion script checks both execution images, commands, successful
status and structured cache-reuse logs before applying an image-only change.

Only `ibkrv2stg-risk-snapshots` was then pinned. The before/after non-image
configuration fingerprint matched: 16:55 HKT daily, zero retries, identity,
secret references, resources and command preserved. Readback separately
confirmed original ingestion `ibkr-v11-daily` remains `0 8 * * *` UTC (16:00 HKT)
with zero retries. No extra Flex or Yahoo requests were made.

Rollback/evidence:
`.work/risk-worker-release/promotion-20261001T192155Z.json` retains the previous
`ibkr-platform@sha256:a3f878df32c6b708448b4e67c2b2666bad8b1fc025dd265710ab1bfb1d4d7463`
image. Roll back only this risk job's image if necessary; do not deploy the
renderer-free image to the web app or reporting jobs. The infrastructure source
now allows the separate risk repository explicitly.

Read-only historical preflight `ibkrv2stg-risk-snapshots-tkceimf` established
that the apparent U16088571 / 2025-11-25 crisis gap is version-related: 41 exact
same-date component mappings exist, and all 37 required stored USD
adjusted-close series cover the 121 replay observations. Stored daily rows use
`as-of-holdings-crisis-replay-v2`; the current loader requires v3.

Further read-only execution `ibkrv2stg-risk-snapshots-frm6zzt` reconciled the
exact component values/weights to the date's USD positions and NAV and
recalculated all 121 observations in memory. **The recomputed normalized values
and/or drawdowns do not match the legacy replay at 1e-9 tolerance.** This is not
proof that the legacy reports are wrong: corrected stored prices or methodology
differences need investigation. The preflight refuses missing/duplicate/bad
prices, mismatched holdings and non-USD inputs rather than guessing mappings
or FX. No historical replay row was relabelled, replaced or accepted; no
published artifact was rewritten. A provenance-aware reconciliation and
controlled historical recalculation remain outstanding.

The current full local suite is **521 tests passed**, including release binding
and stored-replay preflight tests. Test output includes intentionally simulated
failure cases; these were not live Flex failures. A first `pytest` invocation
was unavailable in the local environment; the project's unittest suite above
ran successfully without installing dependencies.

### Priority repair: U16088571 crisis replay on 25 November 2025 — completed

The owner requested this repair before further upgrade work. Other upgrade
tasks were held during the investigation and correction.

Detailed read-only measurement found the legacy maximum drawdown was
`-0.4399837561753318`, while replaying the validated stored inputs gave
`-0.43998380426412886`: a difference of approximately **0.00000481 percentage
points**. Both display as -44%. Across all 121 daily observations, the largest
drawdown difference was `1.787991524837551e-7` in fractional units. This establishes
that the numerical difference is immaterial at displayed precision, not the
exact historical cause of that drift. The confirmed availability defect was
the old v2 replay version being excluded by the current v3 loader.

Preview `ibkrv2stg-risk-snapshots-zkzmv3o` independently checked every daily
valuation and drawdown with 40-digit Decimal arithmetic using fixed units and
flat residual cash. It agreed with the current engine within 1e-12. All 41
same-date holdings/mappings reconciled to NAV; all 37 stored USD series were
complete. No new provider calls, invented prices or proxy substitutions occurred.

Applied execution `ibkrv2stg-risk-snapshots-cjxrrsf` succeeded. The repair was
restricted to this account/date, bound to the preview input fingerprint
`3e01d7a309849ee8ce305bcb8e32a9672084400f756a5cfb3c515aef6163efe0`, and refused any
change greater than the reviewed numerical bound. One serializable transaction:

- Preserved complete old replay rows, old risk cache, consumed holdings,
  mappings and price inputs in tenant audit event **66**
  (`crisis_replay_repair_20261001`).
- Recomputed and updated exactly 121 mutable replay rows to the current v3
  version; it did not merely relabel the old numbers.
- Rebuilt the exact-date risk snapshot and checked that its crisis metric was
  present, matched the independently verified replay, and passed cache integrity.
- Verified reporting-snapshot, report-generation and artifact-release manifest
  fingerprints were unchanged. No report/PDF/PPTX, NAV, positions or approval
  record was edited. Quality remains `warn` for provisional/proxy analytics;
  the repair does not certify those inputs as licensed or independently sourced.

Separate post-commit read-only verification
`ibkrv2stg-risk-snapshots-wpg3k0u` succeeded: all 121 stored values and drawdowns
reproduced exactly (maximum differences **0**), crisis metric available and
risk cache `current`. Chrome verification while signed in as the owner selected
2025-11-25 through the website date selector and displayed **-44%**, with
33 direct holdings, eight recorded proxies and the correct holdings date.
The initial `?as_of=` page URL alone did not select the historical date; browser
verification used the visible selector, not the URL as proof.

Private dispatch/readback evidence and the browser screenshot are in
`.work/crisis-repair-20261001/`. Screenshot:
`verified-risk-2025-11-25.jpg`. Full-page capture was unavailable; viewport
capture succeeded. Complete regression suite: **527 tests passed**. The source
policy check scanned 378 files and 53 contiguous migrations; it is not a full
vulnerability audit.

Rollback, if explicitly required, must use audit event 66's scoped before-image
in a reviewed transaction, first checking that no later correction has replaced
this account/date. Do not restore other dates or overwrite published reports.
This repair resolves the reported date; it does not certify every historical
date or complete the separate historical correction-propagation programme.
Alpha World MS remains paused and no schedules were changed.

## 4. Controlled release pipeline — Azure-native foundation; not operational

Owner selected ACR Tasks plus Azure-controlled releases instead of Azure DevOps.
See `AZURE_NATIVE_RELEASE_FOUNDATION_2026-10-01.md` for the subsequent additive
deployment, verified source archive and scoped uploader grants. The next input
review found that the bundled presentation runtime licence restricts production
use without written permission. Production build work is held for verification
of permission or a replacement renderer; 438 local tests pass, but no new image
or complete pipeline is claimed. The repository-dependent plan below is
historical, not the current implementation choice.

Subsequently separated and released the Python-only risk worker through ACR
build `ck7p` (see section 3), without including the document renderer. This
bounded release does not resolve the separate renderer input review or finish
the release pipeline's scanning, approval and SBOM requirements.

Added tested immutable-image/clean-source/evidence/SBOM/template/migration/
rollback validation policy. Missing, failed or wrong-image gates fail closed.
It does not deploy or interpret caller-supplied approval as owner authorization.
The repository policy scan now includes new non-ignored source files, not only
already tracked files.

Azure DevOps profile exists for the owner, but there is no configured Git remote
and no organization/repository has been created. Browser setup reached the
explicit Terms of Service / Privacy Statement / Code of Conduct acceptance
screen; Continue has NOT been clicked. Requested action-time owner confirmation.
Marketing remains unticked.

Current Azure-native remaining work: secure sanitized source/build-input
provisioning (a new Azure DevOps organization is not required), native protected
environment approval, actual Python/Node/OS/container scanners, SBOM generation,
isolated staging, signed/hashed release manifests and automatic verified
rollback registration. The existing pipeline is not claimed production-ready.

### 2 October continuation: actual artifact and target verification

Reviewed the existing administrator action broker. Its allowlist covers two
bounded maintenance jobs, not deployment; no deployment action or identity was
added to that broker. Release approval must remain a separate protected workflow.

Added `release_package.py`, a bounded offline verification component that checks
actual source and evidence bytes rather than accepting declared hashes alone.
Every gate's content must be a passing result for the entire candidate identity,
including its deployment-target manifest and rollback digest. Scan/test output
bytes, SBOM, template, migration and build-input artifact bytes must all be
present and hash-correct. Unreferenced artifacts and ambiguous JSON are rejected.

The component requires separately supplied producer attestations and server-owned
per-gate producer policy; an identity claimed inside uploaded JSON is insufficient.
It also requires fresh target-readback bytes to match the candidate and an aware
server clock. Gate evidence expires strictly at its deadline, cannot come from
the future and may not declare a validity window longer than 24 hours.

These trusted inputs still require an authenticated Azure adapter. **There is no
new live approval endpoint, authenticated attestation collector or deployment
controller in this change.** It does not run scans, prove their coverage, create
an approval or authorize deployment. Its receipt explicitly records
`deployment_authorized: false` and `approval_recorded: false`. Test producers and
scan outputs used by regression tests are fixtures, not real release evidence.

Sixteen new regression tests cover every missing gate/attestation, tampering,
candidate/target drift, false pass claims, expiry, untrusted producers and input
bounds. Full suite: **724 tests passed**. Limited source-policy scan: 432 files,
55 source migrations. No live Flex call, financial-data mutation, public database
migration, schedule change, role grant or report approval occurred.

Prepared 409-file sanitized archive (1,198,337 bytes), SHA
`250917b08ce79a0ea3eb9ac1e39624cca3cf2b54937344923a6a48816443c182`.
Create-only Azure upload and independent version-pinned readback succeeded for
the source archive, source record and receipt. Receipt version:
`2026-10-02T01:36:11.5929498Z`; SHA
`7df43e24ccddb917dfad8279cc46fd9ee9d52a9466c5005758e667ebf9bcd4ad`.
State remains `archived_not_approved`; this is a private backup, not deployment.
The earlier 407-file archive remains intact and does not contain this component.
All eight upgrades remain in progress; Alpha World MS remains paused. Next is
the trusted Azure evidence collector and separately persisted owner approval,
including revocation, atomic single-use claiming and target-drift rechecks at
dispatch. This component is a prerequisite, not that end-to-end completion.

## 5. Security — database-only authorization deployed

Integration execution `ibkrv2stg-bootstrap-7qjzzex` ran exclusively against the
restored server. It checked all five active identities, disabled-user denial,
role-removal denial and removal of all fourteen subject entitlements: 29 checks
passed. All clone mutations were rolled back. Production identities/grants
were never changed by these tests; no colleague was impersonated.

Set live web `AUTHORIZATION_MODE=database`. Revision
`ibkrv2stg-web--0000111` retains the previous verified application image
`sha256:8f3a771c67ce4dc5f98aa425f5e6bf4f1b77981447538ed2d165df25b774aa64`.
Authenticated owner browser confirmed database mode, five users/five roles,
fourteen entitlements and a healthy supervisor. Database failures now fail
closed for the owner too; no legacy owner fallback is active.

Changed the infrastructure default to database mode to prevent accidental
fallback on future deployments. Verified rollback baseline registered as
revision 0000111 by `ibkrv2stg-burn-in-monitor-wn49pq2`. Autonomy authority,
monthly review deadlines and report approval gates were not changed.

Remaining: full managed-identity/RBAC scope inventory, Entra MFA/session review,
private endpoint/firewall egress/DNS design and testing, and colleague browser
acceptance. Old explicit recovery/migration modes still exist in source; they
are not the active production authorization mode.

## 6. Observability — metrics and schedule-watchdog alerts deployed; partial

Deployment `safe-metrics-20261001` succeeded. AllMetrics diagnostics enabled for
both PostgreSQL servers and all three Key Vaults into `ibkrv2stg-logs`.
All PostgreSQL log categories, including SQL text, remain disabled in this
setting. No secret-access event bodies or query text were exported.

After the owner chose to work on other upgrades while runtime permission review
is pending, added three native Azure query alerts covering 11 daily jobs and two
five-minute workers. Deployment `schedule-watchdogs-20261001-r2` succeeded. Live
queries showed no missing telemetry; synthetic empty-stream checks detected all
13 targets. Local suite now has 448 passing tests. See
`OBSERVABILITY_WATCHDOGS_2026-10-01.md` for exact semantics, limitations, rollback
and the distinction between execution telemetry and actual data freshness.

Subsequently completed a metadata-only, enforced-read-only live database probe
and tested queue/risk-date monitoring policy. At 2026-10-01 18:10 UTC all three
active subjects had NAV and usable risk snapshots through 2026-09-30; mapped
benchmarks/comparisons and USD risk-free data also covered that date. Risk and
provisional benchmark warnings were retained, not mistaken for missing data.
Institutional work/outbox tables were empty. Alpha World remains non-active.
See `OPERATIONAL_DATA_MONITORING_2026-10-01.md`. This is a diagnostic baseline;
the new database checks are NOT yet periodic Azure alerts, and calendar-aware
absolute freshness has not been established. No financial data were changed.

Further continuation added official-source 2026 exchange-session calendars and
shadow benchmark deadlines reflecting the actual producer cut-off. All seven
active benchmark mappings matched the expected dates in the captured live
snapshot. Unsupported calendars remain unverified. A live, rollback-only
metadata-reader permission rehearsal passed: 12 metadata sections, seven denied
sensitive-column reads, no application-table writes or callable public-schema
SECURITY DEFINER functions. The collector no longer relies on a financial view
that would require balance access. Suite: 488 tests. No new persistent database
role, login or automated monitor had been deployed at that checkpoint.

Next continuation deployed `ibkrv2stg-metadata-monitor`: an isolated Azure
identity, its own single-secret Key Vault read scope, registry pull scope and
a column-restricted PostgreSQL login. Live provisioning `hd3gzzk` and monitor
`plylkui` passed. The monitor is now scheduled at minutes 07 and 37 of each hour,
in **shadow mode**, with zero retries, 0.25 CPU/0.5 GiB and a 180-second timeout.
It logs date/status findings; it cannot retry Flex, remediate workflows, approve
reports or write application tables. Every run checks database authority and
seven sensitive-column denials. This is a trusted system-operator metadata
identity, not an end-user/multi-tenant authorization implementation. It queries
the fixed platform tenant and must not be exposed as an interactive user tool.

The fresh 18:41:34 UTC capture found no issues within its checked scope: active
NAV/risk dates aligned and seven active benchmark mappings were current for the
scheduled cycle. Existing quality warnings remain. No new data-quality paging
is enabled: a full daily shadow cycle, maintenance handling, remaining source
calendars and persistent-incident acceptance still need completion. Automated
schedule readback and original 16:00 HKT/zero-retry ingestion preservation were
checked separately. See the operational monitoring record for evidence and
rollback. Full local suite: **498 tests passed**.

Later added the monitor's own native Azure heartbeat alert:
`ibkr-v2-metadata-monitor-watchdog`, deployment
`metadata-monitor-watchdog-20261001` succeeded and enabled settings were read
back. It checks for a verified monitor completion within 75 minutes (two
half-hour opportunities plus 15 minutes of grace), evaluates every 15 minutes,
and uses the existing `ibkr-v11-daily-alerts` action group. Live query: zero
missing completions; empty-stream simulation: one detected missing monitor.
This alerts on monitor liveness only; financial/data-quality findings remain
non-paging. No alert email drill or full daily shadow-cycle acceptance is claimed.

Scheduled monitor execution `ibkrv2stg-metadata-monitor-29848027`, starting
1 October 2026 at 19:07 UTC, subsequently succeeded. The live verifier accepted
all 12 expected metadata/access/completion records, not just the execution
status. Private evidence:
`.work/operational-observability-20261001/ibkrv2stg-metadata-monitor-29848027.json`.
This verifies one automatic dispatch; a full daily producer cycle and end-to-end
email notification test are still outstanding. Findings remain in shadow mode.

Remaining: reviewed redaction/retention for diagnostic logs; calendar-aware
data-freshness and monthly missed-run alerts; dead-letter-age alerts; slow-query
policy; cost/resource-tag dashboard; maintenance handling and an end-to-end
notification drill. Successful execution alone does not establish current data.

## 7. Maintainability/scale — not complete

Readiness and release evidence were placed in small independent policy modules.
Large web-route extraction, central typed contracts/layouts, realistic load
tests and audited retirement of old one-off jobs remain outstanding. No old
recovery job or production workload was deleted.

## 8. Financial/visual acceptance — not complete

Financial formulas were not changed. No historical report package was newly
approved or released. Independent month-end NAV/TWR/cash/FX/sleeve reconciliation
and per-template pixel/geometry suites still need execution. Alpha World MS
remains paused until design-authority and composite-analytics acceptance.

## Test notes / alerts

Local suite at this checkpoint: 426 tests passed. Limited repository policy
scan: 329 source files checked, 53 contiguous migrations. This is not an
OS/Node vulnerability certification or full visual acceptance.

Two diagnostic attempts failed safely:
- `ibkrv2stg-burn-in-monitor-ju9ym0t`: read-only diagnostic role lacked migration
  table access. Replaced with the existing maintenance identity under enforced
  read-only access; subsequent recovery probe passed.
- `ibkrv2stg-risk-snapshots-nty6swe`: Azure CLI's one-off override omitted its
  DATABASE_URL environment. Stopped before database connection/write. Added an
  ARM-template-preserving candidate launcher; both corrected tests passed.

These may generate test failure emails; they are not additional IBKR failures.
There were no live Flex calls. The original daily ingestion remains 16:00 HKT.

## Alpha World MS step 1 — HIBOR/calendar foundation (7 October 2026)

Scope: the first step of the post-calculation-review sequence only. This is not
completion of the eight-upgrade programme or permission to resume the cancelled
agentic deployment. Alpha World T and original IBKR observations are unchanged.

Implemented in the local candidate:

- Official HKMA ingestion with dated HKAB fallback for unavailable transport or
  missing observations. No immediate retry; at most 32 dated fallback requests.
  Denied access, malformed data, wrong dates and unofficial fallback fixings
  stop processing rather than substitute a rate.
- Complete inception-to-valuation fixing coverage is checked before any write.
  Recent cached observations are re-observed; retained historical observations
  keep their original provider/fetch provenance. Failed upserts roll back.
- A versioned 15:45 HKT readiness cutoff allows for exceptional publication and
  corrections. This is an operating policy, not a publication-time guarantee.
  Bank fixing dates remain separate from exchange-specific emergency closures.
- Hong Kong and NYSE calendar coverage extends through 2027. Other markets
  without verified 2027 schedules, and all unreviewed later years, fail closed.

Sources: [HKAB specifications](https://www.hkab.org.hk/files/page/interest-rates/1/Specifications_of_HKD_HIBOR_(30_Sep_2024)-1727681289.pdf),
[NYSE calendar](https://www.nyse.com/trade/hours-calendars),
[Hong Kong 2027 gazette](https://www.info.gov.hk/gia/general/202605/15/P2026051400300p.htm)
and [HKEX trading rules](https://www.hkex.com.hk/Global/Exchange/FAQ/Securities-Market/Trading?sc_lang=en).
Hong Kong half-days are derived from the published rule and gazetted dates.

Verification: 194 focused regression tests passed; the five touched runtime and
qualification modules compile. Tests cover missing/invalid rates, HTTP failure,
publication cutoffs, calendar boundaries, duplicate-safe writes and rollback.

Azure rehearsal uses frozen candidate code, the protected licensed historical
bootstrap and in-memory SQLite only. DATABASE_URL is removed from the one-off
execution. Persistent job definitions, production PostgreSQL, account currency,
IBKR sources and report release state are not changed.

The initial execution `ibkrv2stg-onboard-render-vuoojq0` succeeded and validated
419 fixings through 7 October 2026; the latest fixing was 2.83905% per annum.
HKMA served a partial history; four dated HKAB calls completed the period, with
zero immediate retries. The 415 historical fixings through September retained
their verified value hash. Private evidence SHA-256:
`26a6d7905942b93fda26e3da0c5f292f0786357fad82a30824a0aa9e6c90da9d`.

The final provenance-preserving revision is frozen in
`.work/alpha-ms-hibor-step1-20261007-r2/frozen-code.json`, SHA-256
`6d35f6ca49b094a00139eea3598123cc9d2566d4b13eea7929ef5dd14a718acf`.
Execution `ibkrv2stg-onboard-render-5xl2ny2` succeeded with persistent job settings
unchanged. Its protected evidence was independently downloaded and hash-verified
as `verified-evidence.json` in the same local folder, SHA-256
`322046be890ff99cd740fef786b4edf1be358fa697c487825bcfc5c219005f1e`.
It validates 419 fixings, preserves the historical period value hash, and passes
provenance and duplicate-safe storage checks. The SQLite rehearsal does not
qualify PostgreSQL connectivity or the installed recurring job.

Deployment status: implementation and isolated Azure rehearsal are separate
from recurring installation. No persistent HIBOR schedule or HKD currency cutover
was installed in this step. Next is step 2: consistent HKD account metadata and
calculation/website/report consumers, followed by cache validation, staging
acceptance and rollback qualification before the coordinated release.

## Alpha World MS step 2 — HKD consumer consistency (7 October 2026)

Status: local implementation and recorded-data qualification complete; not
deployed. Production metadata, approved definitions, source observations and
frozen/published report versions were not changed. No Azure execution was
dispatched in this step. MS remains in onboarding; Alpha World T's calculation
and base currency are unchanged.

Implemented:

- Shared read guard checks MS's subject/base/reporting/derived currencies,
  inception date and the exact hash-bound current approved calculation contract.
  Inconsistent or missing settings withhold results instead of relabelling USD.
- Tool NAV/as-of/coverage reads and website performance, summary and date lists
  filter the configured currency. Composite holdings and allocation reads also
  filter their base currency. Stored USD/HKD comparison rows cannot be mixed.
- Composite exposures expose coefficient, normalized source value, source
  currency, FX and contribution explicitly. They do not invent share quantities
  or mark prices. The website labels the coefficient as not shares; exposure
  conversion and contribution arithmetic are checked before returning rows.
- MS freshness uses complete inception-to-valuation 1M HIBOR fixing evidence,
  not the latest Treasury date. Missing fixing coverage fails the readiness check.
- The old standalone MS calculator now uses the same approved HKD typed
  interpreter as the scheduled worker. MS USD/custom contract overrides fail
  before calculation. Source sleeves are converted before normalization;
  positive finite values and the previously validated four-day FX carry are
  enforced. Only the recalculable HKD NAV version is replaced, retaining legacy
  USD NAV observations for the coordinated cutover/rollback.
- Newly assembled MS reporting payloads must agree on HKD inception history,
  summary NAV, holding currency, HIBOR evidence and recomputed metrics. Month-end
  points come from the full MS NAV series, not the benchmark intersection.
  Formula-version metadata follows the actual MS methodology. Existing frozen
  artifacts are not rewritten or automatically approved.

Verification: 274 targeted regression tests passed, including website, tools,
reporting, benchmarks, derived calculations, HIBOR, calendars, risk, parser/chart
checks and PostgreSQL adapter tests. Eight new integration tests exercise actual
consumer SQL in disposable SQLite fixtures and the actual derived interpreter.
The latter reproduces all 443 pinned private HKD NAV observations to ten decimal
places and checks that USD history survives. Fixture FX changes verify conversion
before normalization. These are not live PostgreSQL or new visual acceptance
tests, nor independent certification against every original statement/provider.

Full repository discovery stalled at the existing retired-agent test
`test_agent_daily_inbox.InboxWebTests.test_http_inbox_revocation_rechecks_owner`.
Both attempts were stopped; no full-suite pass is claimed. That test and the
cancelled agentic programme were not altered to obtain the targeted pass.

Candidate evidence: `.work/alpha-ms-step2-20261007/candidate-record.json`, SHA-256
`eeb929a1627f5b190f7f5675d5ad2f37cbcf146bc0f3a3f07afa15f9e87805a3`.
It binds the candidate source files, pinned live-baseline capture and proposed
next definition version. The proposed HKD configuration SHA-256 is
`a257272638bf2e99626cacdfcfd3977b2cfade9036b00b485446ac38a95db7fc`.
This is a plan, not an executed migration or a newly created approval record.

Next: step 3, methodology-aware cache validation and affected analytics rebuild.
The website's cached risk entry points still need that work. Isolated PostgreSQL
and full staging/PDF acceptance, compatible rollback and coordinated deployment
remain later gates. HIBOR + Spread's exact benchmark spread/accrual convention is
still unresolved; currency consistency does not certify that comparison or
authorize inventing its financial definition.

### 2026-10-07 — Alpha World MS step 3: methodology-aware cache candidate

Status: implemented and privately qualified; not deployed. No live PostgreSQL,
Azure job, subject approval, onboarding lifecycle, raw IBKR observation or
published report was changed. Alpha World T's methodology was not changed.

MS cache reads now require HKD, the deployed fixed-unit contract hash, MS
analytics/formula versions, exchange-calendar version, inception date and
actual-source-NAV-only interest basis. The contained risk-free observations must
independently reproduce the complete inception-to-valuation 1M HIBOR average,
fixing calendar, count and source-value hash. Legacy USD/Treasury caches fail
even if their stored payload checksum is correct. This is an integrity and
methodology guard, not a claim of live source freshness; the background worker's
full-value fingerprint separately detects source corrections.

The MS worker consumes full HKD NAV history rather than legacy benchmark-common
rows. HIBOR + Spread comparison, security-level tail attribution, crisis replay
and one-year drawdown remain explicitly unqualified, not substituted with GSPC
or physical USD holdings. Their empty structures match the website contract.
Worker and website date selection use the registered reporting currency.
Calendar changes also invalidate fingerprints. Actual recorded valuations on
joint-market closure dates are retained, while history older than the latest
required session is rejected; no source price is invented or removed.

Both website risk endpoints share the methodology guard. The MS page displays
inception drawdown and the actual daily CVaR field, period-average HIBOR and
explicit limitations. If the core and detailed responses differ in payload
hash or availability, the page withholds their results instead of combining
different cache versions. JavaScript parsing and this race condition were
tested with Node. Invalid MS inputs become bounded-worker withheld results;
the inception classifier no longer rethrows the same missing-input failure.

Verification: 303 targeted tests passed, including 11 new cache tests using
actual disposable SQLite SQL, correction/replay tests, website routes, worker
invalidation, calendar/contract faults and executable JavaScript checks.
Nine changed Python files compiled successfully. Existing fixture/runtime
ResourceWarnings and PDF-library deprecation warnings remain; no full repository
suite or new visual acceptance pass is claimed.

The private offline rehearsal used pinned copies of 443 HKD NAV observations and
415 official HIBOR fixings. It rebuilt 441 eligible daily caches and all 441 were
unchanged on replay. The first two inception dates have insufficient return
history and produce no eligible cache. September's nine metrics reproduce the
previous validated HKD results within 1e-12. Pinned inputs and the immutable
published-report fixture remain unchanged. This is not a fresh independent
certification against every original IBKR/provider file.

Evidence: `.work/alpha-ms-step3-20261007-r5/cache-qualification.json`, SHA-256
`18ae1870c2f3b357f1e4eabcfbe177337fa7448e540d86f01ec0b4244b9c18bf`.
It records per-date fingerprints, metrics, methodology identity and candidate
source-file hashes. Earlier rehearsal revisions are retained, not overwritten.

Next gate: step 4, isolated PostgreSQL integration and staging numerical/PDF
acceptance, including service permissions and consistent transaction snapshots.
Step 5 remains compatible rollback rehearsal and coordinated production release.
Recurring HIBOR installation, live HKD cutover and replacement-report release
have not happened. The exact HIBOR + Spread benchmark convention is still a
separate unresolved financial definition; no spread is fabricated.

### 2026-10-07 — MS allocation diagram: two owner-selected geometry fixes

Before step 4, the owner authorized only (1) correcting the wider S&P500 box
and uneven edge, and (2) consistent widths, caption spacing and branch-label
positioning. The MS-only renderer now clears the entire inherited diagram area
and redraws it with four 91-by-55-point lower boxes, two equal 140-by-41-point
upper boxes, equal within-pair gaps, centered connector endpoints and symmetric
branch labels. Headings share a baseline; one- and two-line caption blocks share
a center with an 11-point line interval. Title, wording (including BioTechnology),
percentages, structure, cash presentation and calculation definitions are not
changed. Alpha World T retains its existing drawing path.

The layout candidate version is v14r9. Geometry and PDF caption validation were
updated together; invalid widths and stale/shifted S&P500 captions are rejected.
Five new regression tests cover boxes, captions, labels/connectors, invalid
geometry and MS-only isolation. All 308 targeted tests and four-file Python
compilation passed. This is not a full repository suite pass.

Private review PDF:
`output/pdf/AlphaWorld_MS_Allocation_Layout_Review_v14r9_r1/AlphaWorld_MS_Factsheet_2026-09-30_Allocation_Layout_Review.pdf`.
SHA-256: `a167c3a8b7bc3bbb6cd94b3729218af74534c3217ac7f7c07b1525effdaa2069`.
The adjacent manifest binds inputs, renderer source hashes and validation.
All nine metric values and numerical evidence match the previous Azure PDF,
apart from the already-existing candidate calendar-version metadata revision;
the pinned step-3 source-file manifest still matches. No financial input was
changed by these geometry edits.

PDF-skill QA rendered both pages with Poppler and visually inspected them.
At a 1500-pixel page height, page 1 has zero changed pixels; page 2 has zero
changed pixels outside the allocation clearing region. The changed pixels lie
within (221,161)-(886,410). Section heading, disclaimer and footer remain intact.
The temporary before/after rasters are retained under
`tmp/pdfs/alpha-ms-allocation-layout-20261007/` for owner review.

Status: local renderer candidate and separate private preview only. Existing
factsheets were not overwritten, reports were not released, Azure was not
changed and step 4 was not started. Deployment remains behind the existing
staging/rollback gates.

### 2026-10-07 - MS allocation geometry applied to historical and future rendering

The owner requested the same two geometry fixes in all past and future monthly
MS factsheets. All 21 completed months (January 2025 through September 2026)
were regenerated privately using v14r9. The HKD/period-average-HIBOR review
batch is in `output/pdf/AlphaWorld_MS_Allocation_Layout_v14r9/`; its download
archive is `output/pdf/AlphaWorld_MS_Allocation_Layout_v14r9_All_21_Months.zip`
(SHA-256 `7f15a87cfd201e62157fa8fcd8a1f72cf026f60d83e15e5e5a0eee40456fbe50`).
No existing or published artifact was overwritten.

To keep this deployment geometry-only, the installed v14r7 Azure financial
code was backported with only the new allocation drawing/version and its
geometry/caption validation. The financial analytics, exchange calendar and
month-end package module remain byte-identical to the installed baseline.
An AST gate verifies that other renderer definitions are unchanged. This is
not the pending HKD/1M-HIBOR production cutover, nor completion of financial
step 4 or step 5. Alpha World T's drawing path is unchanged.

First native build `ck9x` failed two tests because the legacy caption-font
filter overlooked an extra old caption after the redraw. The failed attempt
was preserved; the second frozen context removes that blind spot and checks
the approved caption font separately. Native build `ck9y` passed 51 Linux
regressions, report-runtime smoke and both historical/future entrypoint imports.
The exact candidate image is
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:fbd1253223195f1a5ee5f2d83c4cefa8b9948ce660b3c5efa13e9877c17f6441`.
Its verified Trivy/SBOM gate passed with 0 critical, 0 high and 0 unknown
findings; 40 medium and 19 low findings remain recorded, not dismissed.

Azure historical execution `ibkrv2stg-onboard-render-e78zg95` succeeded and
rendered all 21 private factsheets under
`internal-reports/review/alpha-world-ms/azure-geometry-v14r9-20261007-r2/`.
Downloaded qualification artifacts are in
`output/pdf/AlphaWorld_MS_Azure_Allocation_Geometry_v14r9/`. They deliberately
retain the installed USD/as-of-rate baseline, rather than implying that the
pending HKD methodology was deployed.

Both complete batches passed vector geometry, immutable-input and all-nine-
metric equality checks against their respective previous versions. Poppler
raster comparisons at 1000 pixels show zero page-one changes for every month
and zero page-two changes outside the allocation clearing region. The PDF
skill was used to visually inspect both 21-month allocation contact sheets
and representative complete pages; the equal boxes, caption spacing, branch
labels and clean edges passed. QA records are retained in
`tmp/pdfs/alpha-ms-allocation-history-local-v14r9/verification.json` and
`tmp/pdfs/alpha-ms-allocation-history-azure-v14r9/verification.json`.

Before promotion, scheduled-identity candidate canary
`ibkrv2stg-month-end-factsheet-0p4us5i` and rollback-image canary
`ibkrv2stg-month-end-factsheet-wxiyv76` both passed. The verified rollback image
is the previous `sha256:ad7058ad550c7fc34e33439ad9375ce36997d34606e98588d978e8c9b993eedf`.
The two existing workers `ibkrv2stg-onboard-render` and
`ibkrv2stg-month-end-factsheet` were then updated image-only. Their schedules,
identities, credentials, resources and zero-retry settings are unchanged.

Post-installation canary `ibkrv2stg-month-end-factsheet-gyyd1a3` succeeded and
its artifact/source/image/read-only proofs passed. Azure returned HTTP 503
while retaining its acknowledgement; the original execution was reconciled
by exact image/command/arguments, never dispatched again. All canaries made
zero database writes and released no report. Protected release evidence is
under `.work/alpha-ms-azure-geometry-v14r9-20261007-r2/`.

Future renders through both installed Azure entrypoints now use v14r9's
approved allocation geometry. Future monthly execution still requires the
existing validated inputs, registration and activation controls. MS onboarding
remains paused and website downloads/client-facing reports are not replaced.
IBKR source observations and archived originals remain unchanged. There is
no claim that an unseen future input or the pending financial cutover has
already passed acceptance.

### 2026-10-07 - MS benchmark header font and baseline correction

The owner requested that HIBOR + Spread match Multi-Tranche's font, size and
line alignment. The approved completed-example PDF already has those matching
values; a later 8-point Helvetica benchmark overlay was covering its embedded
Arial text. The local v14r10 renderer removes that overlay, preserving the
exact original embedded font instead of approximating it with another font.

Private September replacement:
`output/pdf/AlphaWorld_MS_Header_Typography_v14r10/AlphaWorld_MS_Factsheet_2026-09-30.pdf`.
Both values use `/AAAAAD+ArialMT`, effective size 9.146064340308 points and
baseline 725.5492475628 points. Runtime validation now independently walks
nested PDF forms and checks actual glyph transforms, font identity, size and
baseline. Three new rejection tests cover wrong font, smaller font and shifted
baseline; all 46 targeted PDF/layout/readiness regressions passed.

The PDF skill was used to render and visually inspect the header close-up and
both complete pages. Independent pdfplumber checks also confirm matching font,
size and top alignment. At 1500 pixels, page-one changes are restricted to
(533,195)-(655,211), entirely inside the benchmark value; page two is pixel-
identical. All metrics, calculation version and calculation evidence are exact
matches to the previous v14r9 September HKD review. Pinned financial inputs and
the approved design sources remain unchanged.

Status: shared local renderer and one private corrected review PDF. The other
20 historical copies and the installed Azure v14r9 image have not yet been
updated for this new header correction; no report was published or overwritten.
This does not change the existing step-4/step-5 financial qualification gates.
Evidence is in the adjacent preview manifest and
`tmp/pdfs/alpha-ms-header-typography-v14r10/verification.json`.

### 2026-10-07 - MS benchmark typography applied to historical and future Azure renders

The owner approved the header preview and requested the same correction for
all historical and future monthly MS factsheets rendered on Azure. The v14r10
header-only release is now installed and qualified on both existing workers:
`ibkrv2stg-onboard-render` and `ibkrv2stg-month-end-factsheet`.
Their schedules, identities, credentials, resource settings and zero-immediate-
retry configuration are unchanged. Alpha World T's renderer is unchanged.

Native build `cka0` passed 54 Linux regressions, report-runtime smoke and both
historical/future entrypoint imports. The exact installed image is
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:5a9949f5a90b13b36b69cc453b9330070945f85325a23853469afefb190c2f71`.
Its verified Trivy/SBOM gate records 0 critical, 0 high and 0 unknown findings;
40 medium and 19 low findings remain recorded, not dismissed. The verified
rollback target is the previous v14r9 image
`sha256:fbd1253223195f1a5ee5f2d83c4cefa8b9948ce660b3c5efa13e9877c17f6441`.

Azure historical execution `ibkrv2stg-onboard-render-xeac5ig` succeeded and
rendered all 21 completed months, January 2025 through September 2026, under
`internal-reports/review/alpha-world-ms/azure-header-v14r10-20261007/`.
The downloaded, hash-verified cloud batch is in
`output/pdf/AlphaWorld_MS_Azure_Header_Typography_v14r10/`. Its archive,
`output/pdf/AlphaWorld_MS_Azure_Header_Typography_v14r10_All_21_Months.zip`,
has SHA-256 `fa489f2bcb5ab47e4c7436f7f20d02bba5850d6753629e9b5aa056bd6619b4dd`.
Every archived PDF matches the corresponding Azure manifest hash.

The separate private HKD/period-average-HIBOR review batch was also regenerated
for all 21 months in `output/pdf/AlphaWorld_MS_Header_Typography_History_v14r10/`.
Its archive is `output/pdf/AlphaWorld_MS_Header_Typography_v14r10_All_21_Months.zip`
(SHA-256 `bc6f3a4459f50ebd71d45e687b2f9221b03660c6b75cdd1f375437bc35b73dbb`).
The Azure batch deliberately retains its installed USD/as-of-rate numerical
baseline. These batches are not interchangeable evidence of a financial cutover.

Both complete batches passed exact financial-metadata equality against their
respective v14r9 predecessors, embedded-font/baseline validation, independent
pdfplumber typography checks and allocation geometry validation. HIBOR + Spread
and Multi-Tranche use `/AAAAAD+ArialMT`, effective size 9.146064340308 points,
at baseline 725.5492475628 points. All-month Poppler raster comparison at
1000 pixels restricts page-one changes to (355,130)-(437,141), within the
benchmark value; page two is pixel-identical for every month. The PDF skill
was used to inspect both all-month header contact sheets and representative
complete pages. QA evidence is in
`tmp/pdfs/alpha-ms-header-history-local-v14r10/verification.json` and
`tmp/pdfs/alpha-ms-header-history-azure-v14r10/verification.json`.

Scheduled-identity candidate canary `ibkrv2stg-month-end-factsheet-3g0frw0`,
rollback canary `ibkrv2stg-month-end-factsheet-vs0nep8`, and post-installation
canary `ibkrv2stg-month-end-factsheet-elamzut` all passed native execution,
source, image, artifact-integrity and read-only verification. They made zero
database writes, released no reports and did not activate onboarding. Final
live verification confirms both workers use the exact v14r10 image and retain
their original non-image configuration. Protected release records are under
`.work/alpha-ms-azure-header-v14r10-20261007/`.

The installed release is limited to the benchmark typography/version and
its validation. Financial analytics, exchange calendar and month-end package
code are byte-identical to the installed v14r9 baseline. IBKR source data and
archived originals remain unchanged. Existing published files and website
downloads were not overwritten. Subsequent renders through both Azure paths
use the approved v14r10 typography and reject mismatched font, size or baseline.
Rendering still requires validated inputs and the existing registration and
activation controls; MS onboarding remains paused. The pending HKD/1M-HIBOR
production cutover and step-4/step-5 financial qualification are not completed
by this layout-only deployment.

### 2026-10-07 - Owner-authorized MS HKD / period-average HIBOR Azure replacement

The owner subsequently instructed that the private HKD review version replace
the current Azure calculation version. The coordinated financial replacement
is now installed in the website and five existing jobs: onboard rendering,
registry month-end factsheets, daily derived calculations, risk snapshots and
risk-free ingestion. The exact image is
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:06c44ff756bbc7da0f41f14c9bfe08fff2ecb16e06a94c8576654bf176a00038`;
website revision `ibkrv2stg-web--ms-hkd-v14r11` is healthy and receives the
existing 100% single-revision traffic rule. Authentication stays enabled;
anonymous health requests correctly return 401. Schedules, identities,
credentials, resource settings and retries are preserved. The only new
standing environment setting enables the owner-confirmed licensed HIBOR feed.

The current approved calculation contract is version 2, SHA-256
`a257272638bf2e99626cacdfcfd3977b2cfade9036b00b485446ac38a95db7fc`.
MS subject/reporting/derived currency is HKD. Each source sleeve is translated
to HKD before inception normalization; this is not a USD total with a changed
label. Risk-free metrics use the arithmetic average of actual published 1M
HIBOR fixings, inception through the valuation date, without forward-filled
fixings. Interest already in source NAV is not charged again. The prior USD
contract configuration, legacy USD NAV rows and original frozen payloads are
retained as noncurrent historical/rollback evidence, not a second current
calculation option.

Native ACR build `cka3` passed 159 Linux regressions, report-runtime smoke and
all applicable future entrypoint imports. Three additional local release-glue
checks pass. The exact-image Trivy/SBOM gate has zero critical, high or unknown
findings; 40 medium and 19 low findings remain recorded. Two failed build/probe
iterations and an initial rehearsal query error remain in their separate
create-only evidence records; none was promoted as qualified evidence.

Read-only PostgreSQL probe `ibkrv2stg-onboard-render-5hjz000` matched all 443
historical HKD NAV observations through September. Atomic/rollback rehearsal
`ibkrv2stg-onboard-render-4suw1t3` tested contract versioning, 419 HIBOR fixings
through 7 October, 445 historical risk caches, 21 month-end snapshots and a
complete scoped restoration. These rehearsals used actual PostgreSQL temporary
shadow tables, not a separately restored server, and made no persistent writes.

Atomic live execution `ibkrv2stg-onboard-render-bifnbkm` committed the qualified
version after archiving and reading back the full MS rollback backup. Backup
SHA-256 is `3778beaaa586599124c1ba26adb834e51196681571d56eb16956eace645a2410`.
The commit creates 447 HKD NAV observations through 6 October, 445 eligible
risk caches and 21 new immutable HKD reporting snapshots. The first two
inception dates lack sufficient daily-risk history and are explicitly withheld.
Source-value fingerprints confirm IBKR NAV, FX and positions, other portfolios,
market inputs, published snapshots and MS operating registrations are unchanged.

The September HKD factsheet now has total return 24.7057968251%, CAGR
13.9385969253%, annualized volatility 15.0718760205%, Sharpe 0.7372126539,
maximum drawdown -12.9524277437%, and period-average 1M HIBOR 2.6463836627%.
These match the independently approved review within 1e-12 numerical tolerance.

Post-installation execution `ibkrv2stg-month-end-factsheet-60zmwhp` rendered all
21 actual persistent HKD reporting snapshots using the installed scheduled
identity. It did not substitute archived review JSON. All 42 resulting PDF
pages are pixel-identical to the accepted HKD review at 1000-pixel resolution,
with independent embedded-font/baseline, allocation geometry, currency,
metric and source-integrity checks. The PDF skill was used for rendered-page
visual inspection. Files are under
`output/pdf/AlphaWorld_MS_Azure_Installed_HKD_HIBOR_v14r11/`; the all-month ZIP
has SHA-256 `59e33e511e5305ea6394cdf4ec5089bea9ade01a30793c72b212bbb4b028e94b`.
Every archived PDF matches its Azure manifest hash.

The archived-input qualification batch and the installed live-data batch are
separate evidence stages of this same new financial release. Old USD outputs
remain historical evidence only. Existing client/shared files were not
overwritten, no reports were published and onboarding was not activated.
HIBOR+Spread remains the approved factsheet heading; its comparison methodology,
composite crisis replay and security-level tail attribution remain explicitly
unqualified and are not replaced by legacy USD/GSPC analytics. Future rendering
uses the installed HKD/HIBOR method and approved v14r10 visual geometry, while
automatic issuance still requires validated data and existing registration,
activation and release controls. This does not finish the broader eight-upgrade
programme or resume the cancelled agentic programme/Bloomberg integration.

Detailed scope, rollback and limitations are in
[the HKD cutover record](ALPHA_WORLD_MS_HKD_CUTOVER_2026-10-07.md).
Protected completion and native receipts are under
`.work/alpha-ms-hkd-cutover-v14r11-20261007-r3/`. The actual website revision
health/sign-in boundary is verified; a separate internal exec probe was
unavailable because its Azure websocket returned 404 before execution and is
not counted as passed. No new signed-in browser session was tested.
