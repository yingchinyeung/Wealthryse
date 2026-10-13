# WealthRyse agents

Agents answer questions about holdings and performance, explain daily
changes, draft month-end commentary and flag data problems. They read only
the data the signed-in person may see, every figure they show is checked
against the data, and nothing they write is sent or published automatically.

Out of scope: trading, rebalancing, personal investment advice, and any
write to portfolio data.

## How it fits together

```
Browser → ibkrv2stg-web (chat panel, Explain buttons, /agent-inbox)
            │ internal, website managed identity, names the signed-in person
            ▼
         ibkrv2stg-agent (agent loop, answer validator, conversations)
            │ MCP over internal HTTPS            │ private endpoint
            ▼                                    ▼
         ibkrv2stg-agent-tools               Foundry model deployments
         (10 read-only tools)                (gpt-6-astra, Global Standard)
            │ Entra login, agent_reader role
            ▼
         PostgreSQL: agent schema (scoped views + agent tables)

Scheduled jobs (same image): holdings explainer, month-end drafter, data quality
            → write to agent.inbox only
```

| Piece | Code |
|---|---|
| Database schema | `migrations/agent/001_agent_schema.sql`, installed by `scripts/install_agent_schema.py` |
| Tool server | `src/wealthryse_agent/tool_server.py`, `tools.py`, `access.py` |
| Agent runtime | `src/wealthryse_agent/agent_server.py`, `runtime.py`, `validator.py`, `calculator.py`, `store.py` |
| Jobs | `src/wealthryse_agent/jobs.py` |
| Website | `src/ibkr_etl/agent_web.py` and a hook at the end of `web.py` |
| Azure | `infra/agent-foundation.bicep`, `infra/agent-apps.bicep`, `Dockerfile.agent` |

## Safety properties

- **Read-only by construction.** The tool server's database role
  (`agent_reader`) can read only the `agent` views and append audit rows. It
  has no privilege on any platform table. The views read through definer
  functions that show only the accounts set for the current transaction, and
  the tool server sets exactly one account per call.
- **Access checked every call.** A person's accounts come from the same rules
  the website uses (`agent.allowed_subjects`); a job's come from
  `agent.service_grants`. Nothing the model says can widen them.
- **Same figures as the website.** Tools reuse `performance_periods`,
  `_external_flows`, the reporting-FX normaliser and the sector aggregator
  from `ibkr_etl`.
- **No invented numbers.** `validator.py` rejects any answer containing a
  figure, date or citation that no tool returned. The model gets one retry,
  then the person sees a plain "couldn't verify" message.
- **Arithmetic by code.** When an answer needs a derived figure (a sum,
  ratio, weighted or compounded return), the model calls `calculate`, which
  runs inside the agent service and accepts only numbers that tools already
  returned plus a few fixed constants (100, 252, 365 and so on). Its result
  gets a citation id like any tool result.
- **Charts and tables are data, not pictures.** Answers may carry up to four
  tables or line, bar or pie charts. Every value in them goes through the same
  figure check, every date must be one a tool returned, and the website draws
  them itself. They are saved with the message's citations, so no schema change
  was needed.
- **Private.** The model resource has public access and key sign-in turned
  off and is reached through a private endpoint. Azure keeps no conversation
  state (`store=False`). Chats stay in your Postgres and are deleted after 90
  days by the data-quality job.
- **Where the model runs.** The Foundry resource and its data at rest are in
  Southeast Asia. Since 13 October 2026 the analyst deployment is
  `gpt-6-astra` on Global Standard, so prompts may be processed in any Azure
  region (see `docs/AI_DATA_PROCESSING_APPROVAL_2026-09-22.md`). The older AI
  pieces still use the APAC Data Zone deployment.
- **Bounded.** Chat runs analysis questions (charts, comparisons, risk,
  "why") at extra-high reasoning and plain lookups at low
  (`AGENT_QUICK_REASONING_EFFORT`); a quick draft that fails the figure check
  is retried at extra-high. Chat allows at most 16 tool
  calls, 32,000 output tokens per model call and 210 seconds per question
  (under the 240-second ingress limit; the service sends a keepalive every 15
  seconds while the model thinks). The scheduled jobs stay at low reasoning,
  8 tool calls and 60 seconds. A daily token budget and 2,000-character
  questions apply to both.
- **Off by default.** `AGENT_UI_MODE=off` on the website. `owner` shows it to
  owners only; `all` to everyone with account access.

## Turning it on (each Azure step needs Ying's typed approval)

1. **Model and network** (`infra/agent-foundation.bicep`). Creates the Foundry
   resource, model deployments, private endpoint and three identities. The
   private network thread owns `ibkrv2stg-vnet` and the three privatelink DNS
   zones: get the endpoint subnet and the zone IDs from it and pass them as
   `privateEndpointSubnetName` and `privateDnsZoneIds`. If the zones don't
   exist yet, leave `privateDnsZoneIds` empty and that thread adds the records.
   Once that thread has added the endpoint's `default` zone group, redeploy
   only in Incremental mode, and pass the zone IDs so the template writes the
   same group.
2. **Entra apps.** Register two applications with app ID URIs
   `api://<tenant ID>/wealthryse-agent` and `api://<tenant ID>/wealthryse-agent-tools`
   (the tenant rejects URIs without its ID or a verified domain). No secrets or
   app roles are needed; the services check the caller's object ID.
3. **Database** (`scripts/install_agent_schema.py`), run as the database
   administrator that owns the platform tables, from inside the private
   network. The server must allow Microsoft Entra sign-in. Do this outside the
   migration thread's write windows:
   ```
   --principal id-wr-agent-tools=reader --principal id-wr-agent=writer --principal id-wr-agent-jobs=writer
   --grant holdings_explainer=<account> --grant month_end_drafter=<account> --grant data_quality=<account>
   ```
   Only an Entra administrator can create the logins. If that administrator
   doesn't own the platform tables, run it twice: first as the Entra
   administrator with `--logins-only` and the `--principal` flags, then as the
   table owner with `--existing-logins` and all the flags.
4. **Image.** Build `Dockerfile.agent` on the private build VM and push it to
   the registry as `wealthryse-agent`.
5. **Apps and jobs** (`infra/agent-apps.bicep`) with the outputs of steps 1
   to 4.
6. **Website.** Set `AGENT_API_URL`, `AGENT_API_AUDIENCE` and
   `AGENT_UI_MODE=owner` on `ibkrv2stg-web`. This goes out through the release
   process.
7. **Evaluate before widening.** Run the evaluation inside the agent app,
   acting for the owner:
   ```
   az containerapp exec -n ibkrv2stg-agent -g rg-ibkr-database-v11 \
     --command "python -m wealthryse_agent.evaluate --user <owner's Entra object ID>"
   ```
   It asks 50 built-in questions (holdings, changes, performance, benchmark,
   sectors, cash flows, freshness, refusals and questions naming another
   account), or the rows of `agent.eval_cases` once it has some, and prints
   only case IDs, pass or fail and reasons. Widen to `AGENT_UI_MODE=all` only
   when it prints `READY to widen`: no rejected answers, nothing touching
   another account, and at least 90% passing. Its token use counts against the
   daily budget.

## Cost drivers

- Model tokens: each question is a few model calls, each resending tool
  results. Extra-high reasoning can use roughly ten times the tokens of low
  on the same question. The daily budget caps it (`AGENT_DAILY_TOKEN_BUDGET`),
  and the agent logs `AGENT_BUDGET_WARNING` once a day at 80% of it. The
  reasoning levels are `chatReasoningEffort` and `chatQuickReasoningEffort`
  in `infra/agent-apps.bicep`.
- One private endpoint.
- Two Container Apps that scale to zero, and three short jobs.
- No new database server.

## Not yet done

- The month-end drafts are not wired into the factsheet renderer. Approving a
  draft marks it ready for the existing report release process.
- The old AI pieces (`ai_analyst.py`, `agent_tools.py` and related modules,
  and the `ibkrv2stg-ai-…` account) stay in place. `agent_tools.py` still feeds
  the factsheets and reporting snapshots, and the month-end factsheet worker
  uses the old model account, so both are removed only after the 105-report
  regeneration and after their calculations move to a plain reporting module.
