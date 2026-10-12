# Staging Architecture and Cost Plan

## Decision requested

Approve a monthly Azure staging budget ceiling of **KRW 120,000**, excluding any
licensed benchmark or market-data subscription. This is a ceiling for the pilot,
not an expected invoice. The infrastructure target before AI usage is
approximately **KRW 40,000–55,000 per month**. AI inference is usage-based and
will remain disabled until the model, region, data boundary and spend controls
are separately approved.

The KRW 120,000 staging ceiling was approved on 22 September 2026.

## Read-only production inspection — 22 September 2026

The Azure portal inspection used the signed-in account
`anthony.ying@wealthryse.ai` in the Microsoft Azure Sponsorship subscription.
No resource, permission, secret, schedule or application setting was changed.

### Verified production topology

- resource group: `rg-ibkr-database-v11`
- primary region: East Asia
- 16 resources in the group
- Azure Database for PostgreSQL Flexible Server 16.15
- PostgreSQL configuration: Burstable B1ms, 1 vCore, 2 GiB RAM and 32 GiB storage
- PostgreSQL high availability: disabled
- Container Apps environment: Consumption workload profile
- daily Container App Job: `ibkr-v11-daily`
- daily schedule: `0 8 * * *` UTC, equivalent to 16:00 HKT
- job safety: retry limit 0, parallelism 1, completion count 1 and timeout 3,600 seconds
- supporting services: Basic Container Registry, StorageV2 LRS, Key Vault,
  Log Analytics, managed identity, virtual network, private PostgreSQL DNS,
  action group and failed-execution alert

The 20 executions visible for 3–22 September 2026 all succeeded. The latest
execution started at 08:00 UTC on 22 September and completed in 2 minutes 30
seconds.

### Observed production cost

Azure Cost Management displayed the invoice period 24 August–23 September 2026:

| Item | Observed cost |
| --- | ---: |
| Actual cost at inspection time | KRW 39,658.29 |
| Azure forecast for the billing period | KRW 43,247.26 |
| PostgreSQL | KRW 32,446.43 |
| Basic Container Registry | KRW 6,397.64 |
| Private DNS | KRW 619.64 |
| Storage account resource | KRW 148.38 |
| Key Vault | KRW 46.19 |
| Azure Monitor and Log Analytics | KRW 0.00 |

The production cost is a useful staging baseline, but it is not a binding quote.
Azure prices, exchange rates, usage and sponsorship terms can change.

## Recommended staging topology

## Deployment status — 22 September 2026

The approved staging foundation is deployed inside `rg-ibkr-database-v11` under
the `ibkrv2stg-*` naming convention. Deployment
`ibkr-v2-staging-foundation-20260922` completed successfully in incremental mode.
The deployment created only staging-tagged resources and staging-scoped role
assignments; it did not update or delete production resources.

Deployed foundation:

- PostgreSQL 16 Flexible Server `ibkrv2stg-pg-auhecwc4itaoo`, B1ms, 32 GiB,
  14-day backup, no high availability and no public database access
- Container Apps environment `ibkrv2stg-env` on a dedicated staging VNet
- Basic Container Registry and Standard LRS StorageV2 account
  `ibkrv2stgauhecwc4itaoo`
- Key Vault `ibkrv2stg-auhecwc4itaoo` with RBAC, purge protection and 90-day
  soft delete
- five separate managed identities for web, ingestion, publishing, diagnostics
  and approval-controlled actions
- three non-public blob containers with versioning and 30-day recovery
- Log Analytics workspace and staging-only PostgreSQL private DNS zone
- a monthly KRW 120,000 budget filtered to `environment=staging`, with actual
  cost warnings at KRW 75,000 and KRW 100,000 and a forecast warning at
  KRW 120,000

No staging web app, scheduled job, Flex credential, production data copy or AI
service has been deployed. Those remain gated follow-on steps. Full verification
is recorded in `STAGING_DEPLOYMENT_RECORD_2026-09-22.md`.

### Isolation boundary

- preferred design: create a separate resource group named `rg-ibkr-platform-v2-stg`
- deploy the core data plane in East Asia
- use a separate virtual network, PostgreSQL server, storage account, Key Vault,
  Container Apps environment, identities and logs
- provide no staging identity with permission to write to production
- do not give staging a network route or database credential for production
- seed staging only from a reviewed export, initially without Flex credentials
- preserve the existing production job, image and 16:00 HKT schedule unchanged

The signed-in account is Owner only on `rg-ibkr-database-v11`; Azure returned
`Forbidden` when it attempted to check or create a new resource group at
subscription scope. The user therefore approved the following deployable
alternative on 22 September 2026:

- place staging resources in `rg-ibkr-database-v11` with the `ibkrv2stg-*` prefix
- tag every staging resource with `environment=staging`
- use a separate VNet, delegated PostgreSQL subnet and staging-only private DNS zone
- use separate PostgreSQL, storage, Key Vault, registry, Container Apps environment
  and managed identities
- grant identities access only to explicitly named staging resources
- leave the diagnostic and approved-action identities without permissions until
  their exact tools are implemented and tested
- filter the KRW 120,000 budget to the `environment=staging` tag

This provides resource-level and network-level isolation but not a resource-group
administrative boundary. Moving staging into its own resource group remains a
recommended later hardening step if subscription-level permission becomes
available.

### Data services

- PostgreSQL Flexible Server B1ms, 32 GiB, 14-day backup and no high availability
- private PostgreSQL networking through a delegated subnet and private DNS
- StorageV2 Standard LRS with versioning, 30-day soft delete and lifecycle rules
- separate containers for imported snapshots, report drafts and approved reports
- Standard Key Vault with RBAC, purge protection and 90-day soft delete

### Application services

- Basic Azure Container Registry for staging images
- Container Apps Consumption environment with VNet integration
- private portfolio web/API app, initially minimum replicas 0 and maximum 1
- deterministic analytics and report-generation jobs
- benchmark, FX and risk-free-rate ingestion jobs
- Entra single-user allow-list for `anthony.ying@wealthryse.ai`
- Log Analytics with 30-day retention and restrained application logging

### Identity separation

Use separate managed identities for:

1. website read-only queries
2. ingestion and deterministic calculations
3. report publication
4. diagnostic administration
5. approval-controlled operational actions

The AI layer must not receive database administrator credentials, arbitrary SQL,
shell access, Flex credentials, Key Vault secret values or permission to change
its own role.

### AI region and deployment decision

East Asia is not listed as a supported Microsoft Foundry Agent Service region as
of the latest verification on 22 September 2026. Southeast Asia supports the
Responses API, Agent Service and private networking and remains the closest
recommended candidate for the pilot. A regional/standard model deployment is
preferred over a global deployment because regional processing is narrower.
Moving selected portfolio facts from East Asia to an AI service in Southeast
Asia is a data-processing decision. The user approved only a regional Southeast
Asia deployment on 22 September 2026, limited to structured U16088571 NAV,
performance, holdings, benchmark and risk results. Raw files, credentials and
personal information remain excluded.

The keyless Southeast Asia resource boundary has been deployed, but the live
catalog contains no regional pay-as-you-go text-generation model. Regional text
generation currently requires provisioned capacity with a minimum 25 PTUs and
would exceed this plan's ceiling. Global Standard and Data Zone Standard are not
equivalent to the approved single-region boundary. No model deployment will be
created without a new processing or cost decision.

The user later approved APAC Data Zone processing and specifically selected
pay-as-you-go `gpt-5.6-sol`. The authorized deployment is pinned to model
version `2026-07-09`, uses `DataZoneStandard` capacity 10 and does not authorize
Global Standard or regional provisioned capacity.

For the first staging release, keep AI disabled and build the deterministic API
and approval boundaries first. When approved, use an application-controlled
Foundry integration with narrowly scoped tools. This keeps daily ingestion,
analytics and reporting independent of AI availability.

## Monthly cost envelope

| Component | Planning amount (KRW/month) | Basis |
| --- | ---: | --- |
| PostgreSQL B1ms and 32 GiB | 32,000–36,000 | Current production observation and published B1ms/storage pricing |
| Basic Container Registry | 6,000–7,000 | Current production observation |
| Private DNS, storage and Key Vault | 500–2,000 | Current production observation plus staging headroom |
| Container Apps website and jobs | 0–10,000 | Consumption plan, scale-to-zero and low single-user traffic |
| Log Analytics and alerts | 0–5,000 | Expected to remain within low-volume allowances; alert if ingestion grows |
| AI model and agent usage | 10,000–60,000 after enablement | Usage-dependent reserve for a future pay-as-you-go option; it does not cover regional provisioned capacity |
| **Infrastructure before AI** | **40,000–55,000** | Target operating range |
| **Pilot including AI reserve** | **50,000–115,000** | Target range after AI is enabled |
| **Requested ceiling** | **120,000** | Monthly review threshold; not a hard Azure stop |

Not included:

- licensed `XNDX` or other market-data fees
- material outbound bandwidth
- optional Azure AI Search, code-interpreter or web-search tools
- high availability, disaster-recovery replication or a continuously warm app
- employee access beyond the single-user pilot

Azure Container Apps includes a subscription-level monthly free grant and can
scale to zero, but the grant is shared with other Container Apps resources and
must not be treated as guaranteed zero cost. Foundry-native prompt/workflow
agents have no separate agent-service fee, but model tokens, hosted-agent
compute and optional tools are chargeable.

## Spend controls

On deployment:

- create an Azure budget for the staging resource group
- send warnings at KRW 75,000 and KRW 100,000
- treat KRW 120,000 as the mandatory monthly review threshold
- tag every resource with `environment=staging`, `owner=portfolio-platform` and
  `costCenter=pilot`
- keep Container Apps minimum replicas at zero during development
- cap application logging and retain logs for 30 days
- keep AI disabled by default and record token usage per request after enablement
- review Cost Management weekly during the first month

Azure budgets notify; they do not automatically stop services. Any automated
shutdown policy must be designed separately so it cannot interrupt production.

## Deployment gates

1. User approves the KRW 120,000 monthly staging ceiling. **Completed 22 September 2026.**
2. User approves regional Southeast Asia processing for the future AI layer. **Completed 22 September 2026; model activation remains blocked by regional SKU availability and cost.**
3. Create the private remote source repository and CI security checks.
4. Add versioned migrations and staging infrastructure definitions. **Completed 22 September 2026.**
5. Validate the deployment plan without creating resources. **Completed 22 September 2026.**
6. Deploy the approved logically isolated staging foundation in the existing resource group. **Completed 22 September 2026.**
7. Load a reviewed snapshot and run deterministic reconciliation tests. **Completed for U16088571 on 22 September 2026.**
8. Add Entra authentication and the private website. **Completed 22 September 2026, including approved HTTPS-only ingress and allowed-user browser acceptance.**
9. Approve a licensed `XNDX` source before benchmark publication.
10. Enable the AI layer only after tool permissions and approval tests pass.

## Pricing references

- [Azure Database for PostgreSQL Flexible Server pricing](https://azure.microsoft.com/en-us/pricing/details/postgresql/flexible-server/)
- [Azure Container Apps pricing](https://azure.microsoft.com/en-us/pricing/details/container-apps/)
- [Azure Container Registry pricing](https://azure.microsoft.com/en-us/pricing/details/container-registry/)
- [Azure Monitor pricing](https://azure.microsoft.com/en-us/pricing/details/monitor/)
- [Azure Blob Storage pricing](https://azure.microsoft.com/en-us/pricing/details/storage/blobs/)
- [Azure Key Vault pricing](https://azure.microsoft.com/en-us/pricing/details/key-vault/)
- [Foundry Agent Service pricing](https://azure.microsoft.com/en-us/pricing/details/foundry-agent-service/)
- [Foundry Agent Service regions and limits](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/limits-quotas-regions)
