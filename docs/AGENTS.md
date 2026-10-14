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
Month-end team job (same image, off until approved): reads review files from
internal-reports, writes agent.month_* tables and agent.inbox only
```

| Piece | Code |
|---|---|
| Database schema | `migrations/agent/*.sql`, installed in name order by `scripts/install_agent_schema.py` |
| Tool server | `src/wealthryse_agent/tool_server.py`, `tools.py`, `access.py` |
| Agent runtime | `src/wealthryse_agent/agent_server.py`, `runtime.py`, `validator.py`, `calculator.py`, `store.py` |
| Jobs | `src/wealthryse_agent/jobs.py` |
| Month-end team | `src/wealthryse_agent/month_team/`, `migrations/agent/002_month_team.sql` |
| Website | `src/ibkr_etl/agent_web.py`, `month_team_web.py` and a hook at the end of `web.py` |
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
- **Bounded.** Owner chat runs at extra-high reasoning with at most 16 tool
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

## Month-end team

Six agents check each month's reports next to today's process. They run in
shadow: they change no file, release nothing, and the release steps on the
Reports tab stay exactly as they are.

| Agent | What it does | Model |
|---|---|---|
| Data | Is the month's data complete? Month-end valuation present, no price gap over 5 days, no one-day move over 20%, exchange rates available. Clean, fixable (waits up to 3 days for the next sync) or blocked (asks Ying). | none |
| Analyst | Builds the figure set from the tools: month, year-to-date and since-inception returns against the benchmark, risk, contributors, sectors. Every figure has an id and a source. | none |
| Writer | Drafts the commentary from the figure set only. Every sentence cites figure ids, and the text makes no numerical claim of its own, as with today's drafter. | `wr-reports-apac` |
| Reviewer | Reads the draft cold and objects sentence by sentence; a code check also catches wording that goes against the cited figure ("rose" on a negative return). Once the package is built, it re-derives every printed factsheet figure from the frozen snapshot with the renderer's own inputs, compares the snapshot with today's database (10 bp), and checks the deck's date and narrative. | `wr-reports-apac` and code |
| Visual QA | Looks at the factsheet pages as images for clipped text, overlaps, blank areas and wrong dates. Each finding carries a page and a region. | `wr-reports-apac` |
| Coordinator | One stage machine per account and month: data, held, words, package, checking, ready. Runs up to two writer and reviewer rounds, sends a dispute to Ying, and checks again when the package is rebuilt. | none |

**Proof, not trust.**

- Files are read only from `internal-reports/review/` and must match the
  sha256 the factsheet job recorded. The snapshot must match its recorded hash.
- The model never sees the snapshot or a file's bytes; visual QA sees page
  images only. The writer sees figure labels and display values only.
- Report content goes only to `wr-reports-apac` (APAC Data Zone); the team
  refuses to start on any other processing scope.
- Its own monthly token budget (`AGENT_MONTH_TEAM_TOKEN_BUDGET`, 20 million by
  default), separate from the daily chat budget. When it runs out the team
  pauses without skipping a check.
- An account that fails three times in a row stops and the others carry on;
  `--retry` restarts it. One account gets at most 15 minutes per run.

**Where Ying sees it** (`MONTH_TEAM_UI=owner` on the website; owners only):

- the "Agent review (Shadow)" panel on each account's Reports tab, above the
  release checklist: verdicts, objections with a page map, the team's
  commentary with its figure chips, sources and every step;
- the Month-end page (`/month-end`): each account's stage for a month, a
  "Needs you" filter, the budget, and the replay results;
- three inbox cards: the month's report, "Hold or proceed" for a blocked
  account, and "Agents disagree" for a dispute. Answers apply on the next run.

**Running it.**

```
python -m wealthryse_agent.jobs month_team                        # shadow, last month
python -m wealthryse_agent.jobs month_team --period 2026-09-30
python -m wealthryse_agent.jobs month_team --replay --from 2026-01-31 --to 2026-08-31
python -m wealthryse_agent.jobs month_team --retry                # restart stopped accounts
```

Replay re-checks months whose factsheet was already released and shows the
results on the Month-end page only, with no inbox cards. If its objections on
months that went out are real, the team is worth a month of shadow.

**Decisions taken by default.** The design's five open questions were not
answered, so this build follows the recommended options; any of them can
change in review.

- Release: when the team takes over, the Release button warns about open
  objections rather than blocking. In shadow it does neither.
- One month of shadow before take-over.
- A historical replay first.
- More capacity for `wr-reports-apac` (an Azure step, below).

**Turning it on** (each step needs Ying's typed approval naming the resources):

1. **Database.** Rerun `scripts/install_agent_schema.py` as in step 3 above,
   adding `--grant month_team=<account>` for each account. It applies
   `002_month_team.sql`.
2. **Image.** Rebuild `Dockerfile.agent`; it now includes poppler for page images.
3. **Capacity.** Raise the tokens-per-minute of `wr-reports-apac` so a month's
   run fits between 15:00 and 20:00 HKT.
4. **Job.** Deploy `infra/agent-apps.bicep` with `deployMonthTeam=true`. This
   creates `ibkrv2stg-agent-month-team` and gives `id-wr-agent-jobs` read
   access to the `internal-reports` container only.
5. **Website.** Set `MONTH_TEAM_UI=owner` on `ibkrv2stg-web` through the
   release process.
6. **Replay, then shadow.** Run a replay of the released months, then let the
   job shadow the next month end.

**Not covered yet.**

- Slides are not looked at as images until the factsheet job saves slide
  images; the deck's text is checked meanwhile.
- The analyst-assurance job's findings are not read.
- No reconciliation against the broker's statements.
- `month_end_drafter` stays until the team's commentary has proven itself.
- Take-over: the team's commentary going into the real package and the
  Release button reading the team's verdict.

## Cost drivers

- Model tokens: each question is a few model calls, each resending tool
  results. Extra-high reasoning can use roughly ten times the tokens of low
  on the same question. The daily budget caps it (`AGENT_DAILY_TOKEN_BUDGET`);
  the reasoning level is `chatReasoningEffort` in `infra/agent-apps.bicep`.
- One private endpoint.
- Two Container Apps that scale to zero, and three short jobs.
- The month-end team, when deployed: an hourly job on six days a month, and
  its own monthly token budget on `wr-reports-apac`.
- No new database server.

## Not yet done

- The month-end drafts are not wired into the factsheet renderer. Approving a
  draft marks it ready for the existing report release process.
- The old AI pieces (`ai_analyst.py`, `agent_tools.py` and related modules,
  and the `ibkrv2stg-ai-…` account) stay in place. `agent_tools.py` still feeds
  the factsheets and reporting snapshots, and the month-end factsheet worker
  uses the old model account, so both are removed only after the 105-report
  regeneration and after their calculations move to a plain reporting module.
