# Institutional onboarding architecture

Status: Reference physical, filtered and fixed-unit-composite branches deployed; master-account acceptance remains pending
Decision date: 28 September 2026
Scope: Physical accounts, master accounts, filtered portfolios and composite portfolios

## Governing principles

1. PostgreSQL is the source of truth for subjects, capabilities, entitlements, approvals and work state.
2. Azure infrastructure and application images must not contain account allow-lists.
3. One Flex statement request is permitted per source run. Queries sharing a credential execute sequentially through one credential ordering key.
4. Once a statement is downloaded, account partitioning, validation, analytics and reporting may execute independently by subject.
5. Every operation is idempotent, tenant-scoped, evidence-bound and auditable.
6. Portfolio figures are produced by deterministic code. AI may explain validated results but may not calculate, override or invent them.
7. No subject is visible to ordinary viewers until all gates pass and an authorized final approver activates it.
8. Raw broker files, Flex credentials, database credentials and personal information never enter AI prompts or workflow payloads.

## Control plane

The website records contracts and approvals. `subject_operating_registrations` defines which approved capabilities run for each subject. A transactional outbox records state-change events, and idempotent work items represent asynchronous processing.

Capabilities are registered independently:

- `daily_ingestion`
- `benchmark_refresh`
- `sector_refresh`
- `month_end_reporting`
- `analyst_assurance`
- `viewer_directory`

An active capability is valid only for an active subject. Preview capability is restricted to an onboarding or preview subject. Registrations are effective-dated and integrity-bound to their configuration SHA-256.

## Work ordering

Flex request work uses `credential:<credential-id>` as its ordering key and permits one attempt. Downstream work uses `subject:<subject-id>`, allowing separate accounts to progress concurrently after the common statement becomes available.

Work dependencies are explicit:

1. Flex statement request
2. Source-account partition
3. Account import and reconciliation
4. Analytics rebuild
5. Benchmark, sector and risk enrichment
6. Historical or month-end reporting
7. Quality assurance and approval

Safe idempotent tasks may be retried. A failed Flex generation request is never retried automatically.

## Approval roles

The target authorization model separates:

- Onboarding operator — prepares cases and uploads evidence.
- Portfolio analyst reviewer — validates financial contracts, benchmarks and reconciliations.
- Compliance reviewer — approves disclosures and external-data transmissions.
- Platform administrator — manages connections and technical configuration.
- Final approver — activates a completed subject.
- Viewer — accesses only explicitly entitled active subjects.

Production activation will require maker-checker separation when more than one authorized approver is available. Every exception requires a reason, risk owner, expiry and evidence digest.

## Subject branches

### Physical account

An account bound to one selected source account in an approved Flex query. The system validates the reporting contract, imports history, reconciles data, validates templates, renders historical reports and activates daily operation.

### Master account

An effective-dated set of child subjects. Consolidated calculations must prevent duplicate holdings, duplicate cash and transfer double-counting. Membership changes create new contract versions.

### Filtered portfolio

A deterministic transformation of one or more source subjects. Filters, currency rules and cash-ledger rules are typed, effective-dated and versioned. China & Hong Kong is the reference acceptance case.

### Composite portfolio

A fixed-unit or otherwise explicitly approved combination of source subjects and benchmarks. Components, signs, units, base-currency conversion, normalization dates and cash treatment are versioned. Cycles and forward-looking data fail closed. Alpha World MS and Alpha World T are the reference acceptance cases.

For the three approved reference derived portfolios, the website shows the exact
server-owned composition and definition SHA-256 before onboarding begins.  The
scheduled calculation worker re-hashes the stored definition and compares it
with the deployed approved reference contract before invoking an allow-listed
calculator.  This runtime binding prevents an approved database record and the
executed financial formula from drifting silently.

The Derived Portfolio Studio generalizes those same contracts without exposing
an executable expression language.  Its filtered-account template accepts only
typed source, exclusion and reconstructed-cash fields.  Its fixed-unit template
accepts only active account/portfolio sleeves and approved market instruments,
with server-derived names, currencies and price conventions.  The browser
creates a validated definition in `subject_calculation_definitions`; the normal
nine-page onboarding workflow separately approves, dry-runs, reconciles,
renders, previews and activates it.  The generic scheduled interpreter
reconstructs each definition through the canonical builders, verifies its
SHA-256, orders derived dependencies, converts every sleeve before
normalization and refuses cycles, unknown components, currency drift, stale
market data or incomplete common-date history.  Reference subjects retain the
additional exact deployed-contract comparison as golden regression cases.

## Approved post-Wave-0 execution sequence

U16088606-Greater China is the completed physical-account Wave 0 baseline. The
remaining branch acceptance work proceeds in this owner-approved order:

1. China & Hong Kong filtered portfolio
2. Alpha World MS fixed-unit composite
3. Alpha World T fixed-unit composite
4. Separate master-account test
5. Remaining v1.1 physical accounts

Dependency validation still applies independently of this programme order. A
subject cannot be activated until every source account, portfolio, benchmark,
FX series and cash-ledger dependency required by its calculation contract is
active and reconciled.

## Release gates

A branch is complete only when:

- A new example can be onboarded without modifying Python, Bicep or environment account lists.
- All required history and completed monthly reports are present.
- Account and tenant authorization tests pass.
- Calculation lineage can be exported as an evidence pack.
- Failure, retry, dead-letter and recovery paths have been tested.
- The account becomes visible only after final activation.

## Migration strategy

Migration 043 introduces the registry and durable-work foundation without changing the active portal or activating U16088606. Existing scheduled jobs remain authoritative until the institutional dispatchers pass staging reconciliation and are deliberately promoted.

Migration 044 adds an integrity-bound identity-registry revision audit plus work dependency and lease indexes. The manifest is supplied only to a controlled one-time provisioning job; PostgreSQL retains its SHA-256 and counts but does not duplicate the manifest or any credentials. Applying the same manifest is idempotent. Replace mode disables identities and removes entitlements that are no longer approved.

Queue workers claim work transactionally with `FOR UPDATE SKIP LOCKED`, an expiring lease and an ordering-key exclusion. A worker may complete only its current lease. Expired non-broker work can be reclaimed within its attempt budget; Flex statement work is dead-lettered after its single attempt. Failed dependencies dead-letter their queued descendants rather than allowing partial onboarding.

The Administration page exposes non-sensitive control-plane counts: registrations by state, work by state, dead letters, registered users, roles and subject entitlements. It never displays object IDs, emails, secrets or raw broker payloads.

The web deployment remains in `legacy` authorization mode until the Entra user,
role and subject-entitlement registry has been populated and reconciled. The
`hybrid` and `database` modes require an explicit deployment parameter; a future
full infrastructure deployment cannot activate them accidentally.

The cutover sequence is deliberately separate:

1. Apply and reconcile the approved identity manifest.
2. Run authenticated browser tests for the owner and every Viewer in `hybrid` mode.
3. Run institutional dispatchers in shadow mode and compare their planned work with the proven scheduled jobs.
4. Approve a production-authority change.
5. Switch authorization to `database`, then separately promote the institutional scheduler.

No identity or scheduling cutover is implied by deploying the tables, worker primitives or monitoring page.
