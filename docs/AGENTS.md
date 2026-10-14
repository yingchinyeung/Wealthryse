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

Six agents check each month's reports and write the deck's commentary. Until
the go-live switch is on they run in shadow: their commentary is shown, never
used. Once live, the month-end package job (`ibkrv2stg-month-end-factsheet`,
which builds both the factsheet and the deck) puts the team's agreed
commentary into the deck, and Release asks for confirmation while the team has
an open objection. Commentary goes in the deck only: the factsheet never
carries it. The team itself never changes a file or releases anything.

| Agent | What it does | Model |
|---|---|---|
| Data | Is the month's data complete? Month-end valuation present, no price gap over 5 days, no one-day move over 20%, exchange rates available. Clean, fixable (waits up to 3 days for the next sync) or blocked (asks Ying). | none |
| Analyst | Builds the figure set from the tools: month, year-to-date and since-inception returns against the benchmark, risk, contributors, sectors. Every figure has an id and a source. | none |
| Writer | Drafts the deck's commentary from the figure set only. Every sentence cites figure ids, and the text makes no numerical claim of its own, as with today's drafter. The summary fits the deck's line (221 characters). Accounts whose deck has no commentary line (Alpha World T) get none. | `wr-reports-apac` |
| Reviewer | Reads the draft cold and objects sentence by sentence; a code check also catches wording that goes against the cited figure ("rose" on a negative return). Once the package is built, it re-derives every printed factsheet figure from the frozen snapshot with the renderer's own inputs, compares the snapshot with today's database (10 bp), and checks the deck's date and narrative. | `wr-reports-apac` and code |
| Visual QA | Looks at the factsheet pages as images for clipped text, overlaps, blank areas and wrong dates. Each finding carries a page and a region. | `wr-reports-apac` |
| Coordinator | One stage machine per account and month: data, held, words, package, checking, ready. Runs up to two writer and reviewer rounds, sends a dispute to Ying, and checks again when the package is rebuilt. | none |

**Proof, not trust.**

- Files are read only from `internal-reports/review/` and must match the
  sha256 the package job recorded. The snapshot must match its recorded hash.
- The model never sees the snapshot or a file's bytes; visual QA sees page
  images only. The writer sees figure labels and display values only.
- Report content goes only to `wr-reports-apac` (APAC Data Zone); the team
  refuses to start on any other processing scope.
- Its own monthly token budget (`AGENT_MONTH_TEAM_TOKEN_BUDGET`, 20 million by
  default), separate from the daily chat budget. When it runs out the team
  pauses without skipping a check.
- An account that fails three times in a row stops and the others carry on;
  `--retry` restarts it. One account gets at most 15 minutes per run.
- Once live, the reviewer checks that the deck prints the team's agreed
  summary; a deck built with the standard commentary is an objection.
- A factsheet that prints commentary (a "Portfolio analyst:" line or any of
  the team's sentences) is an objection, live or not.

**Where Ying sees it** (`MONTH_TEAM_UI=owner` on the website; owners only):

- the "Agent review" panel on each account's Reports tab, above the release
  checklist (marked Shadow until live): verdicts, objections with a page map,
  the team's commentary with its figure chips, sources and every step. Once
  live, Release asks for confirmation when it would release files the team
  still has an open objection to; it never blocks;
- the Month-end page (`/month-end`): each account's stage for a month, a
  "Needs you" filter, the budget, the replay results, and batch release;
- three inbox cards: the month's report, "Hold or proceed" for a blocked
  account, and "Agents disagree" for a dispute. Answers apply on the next run.

**Running it.**

```
python -m wealthryse_agent.jobs month_team                        # last month
python -m wealthryse_agent.jobs month_team --period 2026-09-30
python -m wealthryse_agent.jobs month_team --replay --from 2026-01-31 --to 2026-08-31
python -m wealthryse_agent.jobs month_team --retry                # restart stopped accounts
```

Replay re-checks months whose factsheet was already released and shows the
results on the Month-end page only, with no inbox cards. It does not train the
model: it shows where the team is wrong, its instructions and checks are
tuned, and a second replay shows whether that helped.

**Batch release.** For a live month, the Month-end page offers to release
every report the team passed with nothing open: clean data, agreed
commentary that the deck carries (unless the deck has no commentary line),
printed figures and pages passed, and a file not yet released. Ying's browser then runs each account through the
Reports tab's own release preview and release, so every server-side check
still applies; an account whose files changed after the team checked them is
left for Ying. The page lists what was released and what was not.

**Decisions (Ying, 14 October 2026).**

- No shadow month: the team goes live from November's close once the replay
  of the 105 released reports looks right. Ying still releases every report.
- Release warns on an open objection rather than blocking.
- `wr-reports-apac` goes to about 50,000 tokens a minute (an Azure step, below).
- Batch release of clean reports is ready for November.
- Commentary goes in the deck only. The factsheet never carries commentary.

**Which commentary goes into a live deck** (the rule for the package job
change). The team's, when the writer and reviewer agreed or Ying kept the
writer's text. The standard commentary when Ying agreed with the reviewer,
held the account, or the team has not settled the text when the package job
builds the files. Either way the job passes commentary to the deck renderer
only, as it does today; the factsheet renderer never receives it.

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
6. **Replay.** Run a replay of the 105 released reports and review the results
   on the Month-end page.
7. **Go live.** Set `monthTeamLive=true` on the team job together with the
   package job's team-commentary setting. The package job change is
   separate and waits until the assurance image work is finished.

**Not covered yet.**

- Slides are not looked at as images until the package job saves slide
  images; the deck's text is checked meanwhile.
- The analyst-assurance job's findings are not read.
- No reconciliation against the broker's statements.
- `month_end_drafter` stays until the team's commentary has proven itself.
- The month-end package job does not read the team's commentary yet (see
  "Go live" above).

## Onboarding agents

The onboarding agents work through the four stages of a new-onboarding case
(`ibkr_etl/onboarding_cases.py`). They record the case's own checks with
evidence, and Ying keeps the case's three approvals and the first release.
The intake agent, the reconciliation agent and the history reviewer are built.

**Intake agent** (`wealthryse_agent/onboarding_intake.py`). Before approval 1
it drafts a case's account terms from the client's own documents: the mandate,
the fee terms and any side letter, uploaded to the private `onboarding-intake`
container under `cases/{case_id}/documents/` (PDF, Word or text). For each term
it gives the value with the document, page and exact quote it came from:

- the terms approval 1 binds: the account's name, reporting currency and start
  date;
- the benchmark, management fee, performance fee, hurdle or high-water mark,
  how often fees are charged, the investment objective and restrictions.

The model only proposes. Code checks every quote against the document's own
text and drops any it cannot find, so no term in the draft is invented. Two
documents giving different values for a term, or a reporting currency other
than the case's, fail the draft and name both clauses. A different start date,
a binding term no document states, or a document that could not be read gives
a warning. The draft is written to `agent.onboarding_term_drafts`, bound to the
documents it read; a new or replaced document gets a new draft. It never
changes the case: Ying approves the terms on the case page as before. The
upload on the case page and showing the draft beside approval 1 are PR 57
files, so they go through the onboarding thread.

**Reconciliation agent** (`wealthryse_agent/onboarding_reconcile.py`). In
stage 2 of an IBKR broker account it records the four checks nothing recorded
before. The scheduled Flex worker already loads every account in its
statement, so the agent reads the case account's data through scoped views
that show the accounts of open stage 2 cases and nothing else:

- **Holdings and cash loaded:** a recent day has holdings or cash; older than
  five days is a warning.
- **Transactions loaded:** the daily NAV reaches back to the case's start date
  with no gap of ten days or more, and trades or cash transactions are loaded.
- **Securities mapped:** every security held has its instrument details and
  resolves in the security master. A new security is proposed with its
  identifiers (a warning, acknowledged at approval 1); identifiers that point
  at two securities fail, for Ying to decide.
- **NAV reconciled:** on the latest day and each recent month-end, positions
  plus cash plus accruals, in the base currency at IBKR's FX rates, against
  IBKR's own NAV, within 0.01% or one unit. Each break says which side it is
  on and its likely cause: a corporate action, a missing price, a missing FX
  rate, a settlement, or that nothing loaded explains it. Above 0.5% it fails.

Arithmetic runs in code. The model only words a break's explanation from the
facts code found, and any number in its wording must be one of those facts or
the platform's own wording is used. Results go to
`agent.onboarding_reconciliations`; `ibkr_etl.onboarding_agent_reconcile.record_agent_reconciliations`
records them on the case as checks marked `recorded_by: agent`, with the
proposed security master entries in the evidence.

**History reviewer** (`wealthryse_agent/onboarding_review.py`). It reviews a
composite in the Alpha World MS or T design that is in stage 3 with its
historical reports rendered (`ibkr_etl/composite_reports.py`). It reads every
stored file before approval 2 and checks that:

- every month from the first to the last has a factsheet and a deck, with no gap;
- each stored file matches the hash recorded when it was rendered;
- each factsheet has the design's two pages, prints the composite's own name
  and each page's report date, carries no commentary, and keeps no text from
  the reference design (unless the composite's own name or description uses it);
- each deck names the composite and keeps no MS or T identity text;
- visual QA, looking at every factsheet page against the Alpha World layout's
  checklist, finds no fault. It runs on `wr-reports-apac`, APAC only.

A missing file, a hash mismatch, wrong identity or date text, commentary on a
factsheet, or a high-severity layout fault fails the review. A medium layout
point, or a month that visual QA could not look at, gives a warning, which Ying
acknowledges at approval 2. A clean review passes.

**How the review is recorded.** The agent cannot write a case or a check:

1. The `onboarding` job (`python -m wealthryse_agent.jobs onboarding`, hourly)
   reads the waiting cases through the tool server's
   `list_onboarding_report_cases`. Only that job is offered this tool.
2. It writes one row per case and set of reports to `agent.onboarding_reviews`.
   The row is bound to the digest of the case's `reports_rendered` evidence.
3. The month-end report job, after the composite reports, calls
   `ibkr_etl.onboarding_agent_review.record_agent_reviews`. This records the
   review as the case's `agent_review_complete` check through
   `record_platform_check`, like every other stage 3 check.
4. Until the current reports have a review, the check is pending, so approval
   2 cannot be given. A re-render puts it back to pending.

**Turning it on** (each step needs Ying's typed approval naming the resources):

1. **Database.** Rerun `scripts/install_agent_schema.py` with
   `--platform-reader <the platform's role>`. It applies
   `003_onboarding_review.sql` to `005_onboarding_reconcile.sql` and lets
   that role read the agents' results (and nothing else in the agent schema).
2. **Image.** Rebuild `Dockerfile.agent` and the platform image.
3. **Job.** Deploy `infra/agent-apps.bicep` with `deployOnboarding=true`
   (needs `deployMonthTeam=true` for the report reader). This creates
   `ibkrv2stg-agent-onboarding` and gives the jobs identity read access to the
   `onboarding-intake` container only.
4. **Recording.** A platform job calls `record_agent_reconciliations`, and
   the month-end report job calls `record_agent_reviews` after
   `render_onboarding_composite_reports.py`.

## Cost drivers

- Model tokens: each question is a few model calls, each resending tool
  results. Extra-high reasoning can use roughly ten times the tokens of low
  on the same question. The daily budget caps it (`AGENT_DAILY_TOKEN_BUDGET`);
  the reasoning level is `chatReasoningEffort` in `infra/agent-apps.bicep`.
- One private endpoint.
- Two Container Apps that scale to zero, and three short jobs.
- The month-end team, when deployed: an hourly job on six days a month, and
  its own monthly token budget on `wr-reports-apac`.
- The onboarding agents, when deployed: an hourly job that calls the model
  only for new documents, a NAV break or new reports, with its own monthly budget
  (`onboardingTokenBudget`).
- No new database server.

## Not yet done

- The month-end drafts are not wired into the deck renderer. Approving a
  draft marks it ready for the existing report release process.
- The old AI pieces (`ai_analyst.py`, `agent_tools.py` and related modules,
  and the `ibkrv2stg-ai-…` account) stay in place. `agent_tools.py` still feeds
  the factsheets and reporting snapshots, and the month-end factsheet worker
  uses the old model account, so both are removed only after the 105-report
  regeneration and after their calculations move to a plain reporting module.
