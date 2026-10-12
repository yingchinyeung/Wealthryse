# Staging Deployment Record — 22 September 2026

## Approval and scope

The user approved deployment of a logically isolated staging environment inside
`rg-ibkr-database-v11`, subject to the previously approved monthly staging ceiling
of KRW 120,000. A separate resource group remains preferable, but the signed-in
account does not currently have subscription-scope permission to create one.

The deployment was deliberately limited to the staging foundation. It did not
deploy an application, scheduled job, IBKR Flex credential, market-data
credential, production data copy or AI service.

## Deployment

- subscription: `3956e821-cee4-403f-8708-e84e2ed490eb`
- resource group: `rg-ibkr-database-v11`
- deployment: `ibkr-v2-staging-foundation-20260922`
- deployment mode: incremental
- result: succeeded
- deployment operations: 41 total, 0 failed and 0 running after completion
- infrastructure source: `infra/staging.bicep`
- environment tag: `staging`

The pre-deployment what-if reported 33 resource changes and explicitly ignored
17 existing resources. It reported no production update or deletion.

## Deployed resources

| Component | Name | Verified configuration |
| --- | --- | --- |
| PostgreSQL Flexible Server | `ibkrv2stg-pg-auhecwc4itaoo` | PostgreSQL 16, B1ms, 32 GiB, 14-day backups, no HA, public access disabled, state Ready |
| Database | `ibkr_staging` | UTF-8 staging database |
| Container Apps environment | `ibkrv2stg-env` | Dedicated `container-apps` staging subnet, provisioning succeeded |
| Virtual network | `ibkrv2stg-vnet` | `10.50.0.0/16` with separate Container Apps and PostgreSQL subnets |
| Private DNS | `ibkrv2stg.postgres.database.azure.com` | Linked only to the staging VNet |
| Container Registry | `ibkrv2stgauhecwc4itaoo` | Basic tier, admin user disabled |
| Storage account | `ibkrv2stgauhecwc4itaoo` | Standard LRS, HTTPS only, TLS 1.2 minimum, shared keys disabled, public blob access disabled |
| Key Vault | `ibkrv2stg-auhecwc4itaoo` | Azure RBAC, purge protection, 90-day soft delete |
| Log Analytics | `ibkrv2stg-logs` | 30-day retention |
| Managed identities | five `ibkrv2stg-*-identity` resources | Separate web, ingestion, publishing, diagnostics and approved-actions identities |

## Security and recovery verification

- PostgreSQL is reachable only through the delegated staging subnet and private
  DNS zone.
- Storage uses Microsoft Entra/OAuth by default. Shared-key access and anonymous
  blob access are disabled.
- `input-snapshots`, `report-drafts` and `approved-reports` exist with public
  access set to `None`.
- Blob versioning is enabled. Blob and container deletion recovery are both 30
  days; previous versions are lifecycle-deleted after 30 days.
- Key Vault contains only the expected database secret names. Secret values were
  not displayed during verification.
- The web identity can pull images, read required secrets and read blobs.
- Ingestion and publishing identities can pull images, read required secrets and
  contribute blobs.
- Diagnostic and approved-action identities have no role assignments yet. Their
  permissions will be added only with the corresponding tested tools.
- No `ibkrv2stg` Container App, Container App Job, Cognitive Services resource or
  Machine Learning resource exists.

Public service endpoints for the staging registry, storage control/data endpoint
and Key Vault remain enabled but require Azure authentication; their data is not
anonymous. PostgreSQL is private. Private endpoints or network ACL restrictions
for the remaining services are a later hardening option when the application
egress design is implemented.

## Cost control verification

Budget `ibkrv2stg-monthly-budget` is active and filtered to resources tagged
`environment=staging`:

- KRW 75,000: actual-cost email warning
- KRW 100,000: actual-cost email warning
- KRW 120,000: forecast email warning
- notification recipient: `anthony.ying@wealthryse.ai`

Azure budgets provide notifications, not a hard spending stop. Cost Management
should be reviewed weekly during the pilot.

## Production non-regression check

Production job `ibkr-v11-daily` remained provisioned with:

- cron `0 8 * * *` UTC, equivalent to 16:00 HKT
- retry limit 0
- parallelism 1
- timeout 3,600 seconds

Its scheduled executions on 20, 21 and 22 September 2026 all succeeded. The 22
September execution started at 08:00 UTC and completed at 08:02:30 UTC. This is
direct evidence that the staging deployment did not interrupt the production
refresh.

## Source validation

- the deployed Bicep template compiled successfully after deployment;
- the existing application test suite passed all 39 tests;
- the infrastructure example file contains placeholders only and no deployed
  password or token;
- Git whitespace validation passed.

## Remaining gates

The foundation is healthy, but the upgraded website is not yet available. Before
staging can serve portfolio data, the project still needs:

1. a private remote repository and CI security checks;
2. versioned database migrations and application-role bootstrap;
3. a reviewed, non-secret U16088571 staging snapshot;
4. deterministic reconciliation tests;
5. the private API and Entra-authenticated single-user website;
6. a licensed Nasdaq-100 Total Return (`XNDX`) data source;
7. separate approval for any AI region and cross-region data processing.

Production remains the current operating database. The new staging foundation
has no route, credentials or role assignment permitting it to modify production.
