# Live Azure state vs. Bicep

Checked read-only against rg-ibkr-database-v11 on 2026-10-12 (`az containerapp
job list`, `az containerapp list`, `az containerapp auth show`). Every file in
`infra/` builds with Bicep 0.48 (CI checks this).

## Covered by Bicep

- **Web app and sign-in:** `staging-web.bicep` and `staging-web-auth.bicep`
  cover ibkrv2stg-web and its Entra sign-in.
- **Credential broker:** `staging-credential-broker.bicep` covers
  ibkrv2stg-credential-broker.
- **v1.1 jobs:** `job.bicep`, `bootstrap-job.bicep` and `history-job.bicep`
  cover the 3 jobs.
- **v2 jobs:** 23 of the 30 jobs in ibkrv2stg-env have a `staging-*.bicep`
  file. The live schedules match the files.

## Not in Bicep

The 7 jobs below run a Python program embedded in the job definition as
base64 (`python -c "exec(base64...)"`), not a script from the image. Their
source is not in this repository. To bring them under Bicep, add each
program to `scripts/`, build it into the image and point the job at it.

| Job | Trigger | Identity |
|---|---|---|
| ibkrv2stg-metadata-monitor | `7,37 * * * *` | metadata-monitor |
| ibkrv2stg-secret-metadata | `15 6 * * *` | secret-metadata |
| ibkrv2stg-agent-audit | Manual | specialist-audit |
| ibkrv2stg-authority | Manual | specialist-authority |
| ibkrv2stg-observe-reader | Manual | specialist-observation |
| ibkrv2stg-inbox-acceptance | Manual | web |
| ibkrv2stg-inbox-acceptance-r2 | Manual | web |

The ibkrv2stg-authority payload hash does not match its `payloadSha256` tag.

## Bicep files with no live job

These files describe one-off upgrade jobs that no longer exist in Azure:

- `staging-factsheet-v6-upgrade-job.bicep`
- `staging-historical-factsheet-v6-job.bicep`
- `staging-historical-presentation-v24-job.bicep`
- `staging-historical-report-job.bicep`
- `staging-nav-axis-upgrade-job.bicep`
- `staging-presentation-v24-upgrade-job.bicep`

## Other drift

- **Images:** jobs run on 20 different image digests, so redeploying from one
  image parameter would change most of them.
- **Pinned secret versions:** five jobs pin a Key Vault secret version
  instead of using the latest:
  - burn-in-monitor, autonomous-admin, autonomy-review and
    institutional-shadow pin `database-approval-url`.
  - analyst-assurance pins `database-app-url`.

  Rotating those secrets will not reach these jobs.
- **Inline secrets:** three Container Apps secrets are stored inline rather
  than as Key Vault references:
  - `microsoft-provider-authentication-secret` and `credential-broker-auth`
    on the web app.
  - `broker-auth` on the broker.
- **Stale job manifest:** the job manifest inside ibkrv2stg-institutional-shadow
  lists the month-end factsheet schedule as `0 9 1-7 * *`, but the live job
  runs `0 9 * * *`.
