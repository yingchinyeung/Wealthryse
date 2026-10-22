# AI Data-Processing Approval — 22 September 2026

## Approved scope

The user approved sending selected structured `U16088571` tool results to a
regional Microsoft Foundry deployment in Southeast Asia. Approved categories
are:

- NAV;
- performance;
- holdings;
- benchmark data and metrics; and
- risk metrics.

The following categories must remain excluded:

- raw IBKR or Flex files;
- Flex credentials or tokens;
- database credentials;
- personal information; and
- any data for an account or portfolio other than `U16088571`.

The user subsequently approved APAC Data Zone processing and deployment of the
pay-as-you-go `gpt-5.6-sol` model. This permits prompts and responses to be
processed within Microsoft's APAC Data Zone; it does not limit processing to
the Southeast Asia region. Global inference remains unapproved. Trading,
arbitrary SQL, shell access, credential retrieval, permission changes and model
self-modification remain prohibited.

## Implemented enforcement

The application uses a fixed analysis-type-to-tool allow-list. It converts each
deterministic tool response into a second, narrower model payload. Account
alias, account type, identity claims, ingestion-run details, database connection
information, raw records and credentials are omitted. The model receives no
tools and cannot query the database. Model responses must match a strict JSON
schema and may cite only citation identifiers supplied with the approved
grounding payload. Calls are stateless (`store=false`). Request and response
bodies are not written to the AI audit event; only hashes, region, deployment,
token counts and correlation ID are recorded.

## Azure boundary and activation state

The keyless Azure OpenAI/Foundry account
`ibkrv2stg-ai-auhecwc4itaoo` was created in `southeastasia`. Local key
authentication is disabled. The staging web managed identity has only the
`Cognitive Services OpenAI User` role at this account's scope.

The regional-only activation blocker was resolved by the later, explicit APAC
Data Zone approval. Deployment `gpt-5-6-sol-apac` now runs `gpt-5.6-sol`
version `2026-07-09`, SKU `DataZoneStandard`, pay-as-you-go capacity 10.
Regional provisioned capacity and Global Standard remain outside the approval.

The first successful live test completed on 22 September 2026 for U16088571 as
of 18 September 2026. It used the approved sanitized portfolio overview,
returned a strict-schema narrative with citations limited to the supplied
grounding set and recorded 1,753 input tokens and 797 output tokens. Prompt and
response bodies were not written to the audit log. An unauthorized subject test
returned HTTP 403 before database or model activity.

## Amendment — 13 October 2026: global processing for the portfolio agent

Ying chose to move the new portfolio agent (`wealthryse_agent`: the
`ibkrv2stg-agent` chat service and the three `ibkrv2stg-agent-*` jobs) from
`gpt-5.6-sol` on APAC Data Zone Standard to `gpt-6-astra` version
`2026-09-03` on **Global Standard**. Azure offers `gpt-6-astra` in Southeast
Asia only as Global Standard; its Data Zone versions exist only in the US.

What this changes:

- Prompts and responses for the agent may be processed in any Azure region,
  not only the APAC Data Zone. The Foundry resource, and data at rest, stay in
  Southeast Asia, and calls stay stateless (`store=False`).
- The agent accepts processing scope `global` as well as `apac-data-zone`.

What does not change:

- The approved data categories and exclusions above.
- The older AI pieces (`ai_analyst.py`, the month-end factsheet worker, the
  portfolio-assurance job and the `ibkrv2stg-ai-auhecwc4itaoo` account) stay on
  APAC Data Zone processing and still reject any other scope.
- Trading, arbitrary SQL, shell access, credential retrieval, permission
  changes and model self-modification remain prohibited.
