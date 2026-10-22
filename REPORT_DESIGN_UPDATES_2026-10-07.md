# Post-onboarding report design updates

Status: implemented and tested locally; not deployed or enabled on Azure.
No reports have been released, withdrawn or regenerated in production by this work.
Original IBKR Flex files and source observations are untouched.

## Owner workflow

1. On an active account's Report centre, describe the requested wording/layout changes.
2. The release operator implements, qualifies and deploys a renderer/template candidate. This is not arbitrary browser code execution or an in-browser PowerPoint editor.
3. Select the exact qualified installed design and preview the historical months covered.
4. Explicitly consent to historical regeneration and future use. The immutable plan binds each frozen snapshot ID and SHA-256, the image digest and both template versions.
5. The dedicated worker creates private, content-addressed PDF/PPTX pairs. A failed/interrupted run leaves current released downloads available. Leased retries cannot change the bound input or execute a user-supplied command.
6. Review each replacement in the existing per-file approval checklist. Approval and withdrawal of older downloads happen in the same database transaction, scoped to account, month and report type. Old files and decisions remain archived; superseded links cannot be restored as current downloads.
7. Only when every planned replacement is approved and still current does the worker mark the request complete and activate the selected design for future monthly generation. This does not activate onboarding or permit client distribution.

The website shows requested/queued/running/awaiting-review/completed/failed states. No qualification is claimed merely because a request was submitted.

## Deployment requirements (still outstanding)

- Apply migration 062 in isolated PostgreSQL; exercise RLS, transaction rollback, duplicate retries, concurrent approvals, process interruption and restoration from archived files. Docker/PostgreSQL was not available locally, so migration execution was not verified in this session.
- Install scoped grants: the website approval login can read qualification/active-design rows and create/update request rows, but cannot register qualifications, change the active design or delete evidence. Bootstrap includes assertions for these limits; do not grant broad administrator credentials to the website.
- Qualify the full candidate image with regression, vulnerability, runtime, rollback, historical financial and historical visual receipts. A local unit-test pass or a private preview alone is not sufficient.
- Register the protected receipt using `scripts/register_qualified_report_design.py` with a separately obtained receipt digest and release-service connection. The receipt must identify the account/version/image, exact base factsheet/presentation versions and all six passed gates. Registration is create-only, audited and not exposed to browsers. No qualified version was registered in this session.
- Deploy the website and reporting runtimes through the approved release process, preserving authentication and credentials. Bind `REPORT_RENDERER_IMAGE_SHA256` to the independently verified reporting image digest, not an arbitrary browser value.
- Schedule `scripts/run_report_design_updates.py` separately from the ordinary month-end scheduler. Allow sufficient execution time for a full historical batch (each render has a 20-minute ceiling), or qualify interruption/resume under the chosen Azure job timeout. Confirm the worker's health before enabling the website's queue control.
- Test staging owner/viewer/onboarding screens, desktop/mobile layout, private artifact downloads and old-link withdrawal. The local preview server started, but the in-app browser could not reach it; visual browser acceptance is still outstanding.
- Coordinate future renderer upgrades with the active-design pointer and the normal month-end runtime. A different image or template version fails closed; it does not silently fall back. Qualify this cutover before each later design upgrade, including an update that crosses a month end. Historical consent covers the months displayed in its preview; newly arrived months require the normal qualified workflow or another reviewed batch.
- Keep `REPORT_DESIGN_UPDATES_ENABLED` unset/false until all above checks pass. First enable on a scoped active test account and verify private generation, owner approval, supersession and future-month rendering before rollout. Alpha World MS remains subject to its separate onboarding gates.

## Local verification

Targeted suite: 175 tests and 49 subtests passed, including design planning, exact qualification, owner/account boundaries, required consent, stale preview, immutable input binding, scheduler isolation, partial failures, future-design activation and superseded-link safeguards. Existing monthly releases, historical reporting, onboarding, artifact validation and PostgreSQL-adapter tests passed.

Executable DOM checks passed for request creation, historical preview, explicit consent, queueing, safe description text and rejection of a stale in-flight preview. This is not a substitute for browser visual acceptance.

No live Azure write, email, account activation, commercial release or source-data mutation was performed.

## Real PostgreSQL qualification (12 October 2026)

`tests/test_report_design_postgres.py` now runs the workflow on a real,
disposable PostgreSQL 16 schema built with `scripts/build_disposable_qa_schema.py`
and the unmodified `scripts/bootstrap_postgres.py`. It passed (5 tests, 4
subtests) and covers:

- the website approval role cannot register, edit, activate or delete designs;
- row-level security hides and blocks rows across tenants;
- registration is create-only and cannot be rebound;
- request, preview, queue, worker regeneration, per-month owner approval with
  supersession of old downloads, and activation only after the last month is
  approved; old files stay archived;
- a newer frozen input after consent fails the run without releasing anything;
- the database itself refuses a second live request for the same account.

The renderer is stubbed, so this does not replace the image qualification,
historical visual review or staging browser checks listed above.

`infra/staging-report-design-worker-job.bicep` defines the dedicated worker job
(every 30 minutes, 2-hour replica timeout, disabled by default). It is not
deployed.

Finding: the migrations cannot rebuild an empty server. Early seed migrations
need the tenant row before `initialize()` inserts it, and migration 027 needs
existing subjects. The QA harness seeds both; a production rebuild from code
alone would fail the same way and should be considered in recovery planning.
