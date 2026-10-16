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
- **Where people find it.** When it is on for someone: "Agent inbox" in the top
  navigation with a count of new items; the "Ask about this account" button and
  Explain buttons on every account tab, with starter questions for that tab.
  While the older analyst's model (`AI_ANALYST_ENABLED`) is off, its AI analyst
  tab is left out and old links to it open Overview with the chat open. The
  Agent inbox and Month-end pages use the website's own header and navigation,
  and Month-end is under Operations. System health opens with an "At a glance"
  summary and the open incidents, each with the operations agent's diagnosis,
  its draft fix and (for the owner) "Ask the administrator about this", which
  opens Administration with the question filled in. In the Administration chat,
  each update says whether the administrator, the operations agent or the
  connection agent wrote it.

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

Six agents check each month's factsheet. Until the go-live switch is on they
run in shadow. Once live, Release asks for confirmation while the team has an
open objection. The team itself never changes a file or releases anything.

**Per-account decks are no longer built** (Ying, 14 October 2026). The
month-end package job (`ibkrv2stg-month-end-factsheet`) builds the factsheet
only and makes no commentary call; a month is complete, releasable and healthy
with its factsheet alone. Decks built before stay downloadable, and a new
factsheet released for a month withdraws that month's older deck. Stage 3 of
new onboarding renders factsheets only. With `decks` off in the team's
settings (the default), the team checks the factsheet, ignores any deck and
writes no commentary, since the factsheet never carries it; the writer,
reviewer and deck checks below stay in the code for the all-accounts summary
deck that comes next. The older manual onboarding render jobs
(`run_self_service_onboarding_reports.py`,
`render_onboarding_historical_reports.py`) still build decks and are not run.

| Agent | What it does | Model |
|---|---|---|
| Data | Is the month's data complete? Month-end valuation present, no price gap over 5 days, no one-day move over 20%, exchange rates available. Clean, fixable (waits up to 3 days for the next sync) or blocked (asks Ying). | none |
| Analyst | Builds the figure set from the tools: month, year-to-date and since-inception returns against the benchmark, risk, contributors, sectors. Every figure has an id and a source. | none |
| Writer | Drafts the deck's commentary from the figure set only. Every sentence cites figure ids, and the text makes no numerical claim of its own, as with today's drafter. The summary fits the deck's line (221 characters). Accounts whose deck has no commentary line (Alpha World T) get none. | `wr-reports-apac` |
| Reviewer | Reads the draft cold and objects sentence by sentence; a code check also catches wording that goes against the cited figure ("rose" on a negative return). Once the package is built, it re-derives every printed factsheet figure from the frozen snapshot with the renderer's own inputs, compares the snapshot with today's database (10 bp), and checks the deck's date and narrative. | `wr-reports-apac` and code |
| Visual QA | Looks at the factsheet pages as images for clipped text, overlaps, blank areas and wrong dates, with each page's printed text beside them. Each finding carries a page and a region. Code sets aside a finding that quotes a date or figure the page does not print, estimates a value from a chart, does arithmetic (the reviewer's job) or objects to the month's last trading day; set-aside findings stay in the evidence with the reason. Code also checks the printed text for dates after the report date (stale or hidden text), one label printed two ways and unevenly spaced chart date labels, and notes template wording once (no YTD, no currency stated, an unnamed benchmark, a benchmark spread never stated, the 12-month note). The reviewer also lists the factsheet's own statements that its printed figures contradict ("diversified" beside one large holding, a leverage cap the exposures exceed); one stands only if the statement and every figure it quotes are printed, and they are kept as template notes. | `wr-reports-apac` and code |
| Coordinator | One stage machine per account and month: data, held, words, package, checking, ready. Runs up to two writer and reviewer rounds, sends a dispute to Ying, and checks again when the package is rebuilt. | none |

**Proof, not trust.**

- Files are read only from `internal-reports/review/` and must match the
  sha256 the package job recorded. The snapshot must match its recorded hash.
- The model never sees the snapshot or a file's bytes; visual QA sees page
  images and the pages' printed text. The writer sees figure labels and display values only.
- Report content goes only to `wr-reports-apac` (APAC Data Zone); the team
  refuses to start on any other processing scope.
- Its own monthly token budget (`AGENT_MONTH_TEAM_TOKEN_BUDGET`: deployed at 40
  million since Ying doubled it on 15 October 2026, 20 million if unset),
  separate from the daily chat budget. When it runs out the team
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
python -m wealthryse_agent.jobs month_team --replay --round 2 --from 2026-01-31   # the next round
python -m wealthryse_agent.jobs month_team --retry                # restart stopped accounts
```

Replay re-checks months whose factsheet was already released and shows the
results on the Month-end page only, with no inbox cards. It does not train the
model: it shows where the team is wrong, its instructions and checks are
tuned, and a second replay shows whether that helped.

**Replay rounds** (`013_replay_rounds.sql`). Each replay belongs to a round.
A new round (`--round 2`, then 3, ...) checks every released month in the
range afresh, in runs of its own, so earlier rounds stay as they were; running
the same round again carries on where it stopped. The replays done before
rounds existed are round 1. On the Month-end page, "Replay of released months"
shows the newest round, a Round menu picks another, and a table compares the
rounds: months checked and finished, data verdicts, objections raised (by
file and how each was settled) and commentary disputes. A later round with
fewer false objections and the same real ones shows the tuning helped. A
replay never writes an inbox card, changes a file or releases anything.

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
- Per-account decks are no longer built; an all-accounts summary deck follows
  the onboarding work.

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

- every month from the first to the last has a factsheet, with no gap (decks are
  no longer built, and one listed from before is not read);
- each stored file matches the hash recorded when it was rendered;
- each factsheet has the design's two pages, prints the composite's own name
  and each page's report date, carries no commentary, and keeps no text from
  the reference design (unless the composite's own name or description uses it);
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

**Go-live coordinator.** Two parts, neither of which calls a model:

- **Readiness pack** (`ibkr_etl/onboarding_readiness.py`, `readiness_pack`).
  Everything for approvals 2 and 3 in one place: every check of every stage
  so far with its evidence and whether an agent or the platform recorded it,
  the warnings already acknowledged, the approved name, currency and start
  date beside what the intake agent read in the client's documents, exactly
  what GO LIVE switches on, and what still stands in the way. It is computed
  from the case each time, with a digest. The case page shows it above the
  GO LIVE box (the onboarding thread's part).
- **First-month watch** (`wealthryse_agent/onboarding_watch.py`, in the
  hourly `onboarding` job, even when the model budget is used up). For each
  case that went live and waits for its first month-end, it posts an inbox
  card once for: the first daily data and checks after go-live, five days
  without data, failed daily checks, the month-end team lacking access (with
  the `--grant month_team=<account>` command; the grant stays your step), and
  the first month-end released. It reads only dates and statuses through
  `list_live_onboarding_cases` (`007_onboarding_watch.sql`).

**Connection agent** (`wealthryse_agent/connection_watch.py`, in the same
hourly `onboarding` job, also plain code). A new IBKR login is not contacted
until a check runs. For each login never checked, the agent queues its first
check through `agent.queue_first_connection_check()`: the same one-attempt
request as the "Check now" button, which the Flex discovery job
(`ibkrv2stg-flex-discovery`) then runs. It reports once per check, in plain
words: the accounts the login sees (they appear under Available), or the
exact problem with IBKR's error code and what to do, or that a check has
waited 30 minutes, meaning the discovery job is not running. It never
retries, and never queues while the login is cooling down or held for a
reset: IBKR locks a token after repeated failures, so every later check is
the owner's. It sees statuses, codes and counts through `list_broker_logins`,
never the token or its Key Vault name (`009_connection_checks.sql`). Its
reports go to `agent.connection_reports`, which the administrator posts to
its system updates conversation.

When a check succeeds, the discovery job also compares the statement with the
platform's Flex recipe (`ibkr_etl.flex_recipe`) and keeps what the query lacks:
section names, statuses and messages, never statement content
(`recipe_summary`, stored with the check's result). The agent's report then
names exactly which sections or fields to tick, and for which accounts, with
the steps to fix the query (`011_connection_recipe.sql`).

**Turning it on** (each step needs Ying's typed approval naming the resources):

1. **Database.** Rerun `scripts/install_agent_schema.py` with
   `--platform-reader <the platform's role>`. It applies
   `003_onboarding_review.sql` to `005_onboarding_reconcile.sql` (and `007_onboarding_watch.sql`, `009_connection_checks.sql` and `011_connection_recipe.sql`) and lets
   that role read the agents' results (and nothing else in the agent schema).
2. **Image.** Rebuild `Dockerfile.agent` and the platform image.
3. **Job.** Deploy `infra/agent-apps.bicep` with `deployOnboarding=true`
   (needs `deployMonthTeam=true` for the report reader). This creates
   `ibkrv2stg-agent-onboarding` and gives the jobs identity read access to the
   `onboarding-intake` container only.
4. **Recording.** A platform job calls `record_agent_reconciliations`, and
   the month-end report job calls `record_agent_reviews` after
   `render_onboarding_composite_reports.py`.

## Operations agent

The autonomous administrator (`scripts/run_autonomous_administrator.py`)
checks the platform every five minutes and opens an incident for each failing
check. It is still the only thing that acts. The operations agent adds the
investigation it lacked, so that it can say what the exact problem is.

**What it reads.** Every 5 minutes, for each open warning or critical
incident it has not yet looked at (once per episode: an incident that resolves
and comes back is looked at again):

- the incident and the administrator's evidence, the administrator's runs and
  actions over 7 days, and import runs over 14 days, through the tool server
  (`list_open_incidents`, `get_operations_history`; only the `operations` job
  is offered them);
- Azure telemetry, read with read-only roles: error lines from the container
  apps' logs (Log Analytics), each job's recent executions, resource health,
  fired alerts, the build VM's metrics and the resource group's daily cost.

Anything that looks like a credential is removed before the model or the
agent's table sees it. A source that cannot be read is recorded as unread and
the others carry on.

**What it writes.** The model (on `wr-reports-apac`) writes a summary, the
likely cause, a confidence, the evidence and up to four steps for the owner.
Code keeps only evidence whose source was read and whose every number appears
in that source; with none left, the diagnosis is inconclusive and low
confidence. The diagnosis goes to `agent.ops_investigations` with a digest of
each source, never the logs themselves.

**Draft pull requests.** When the cause is a defect in this repository's code
(a traceback naming a file under `src/` or `scripts/`) and a GitHub token is
configured, it writes the fix and a test and opens them as a draft pull
request on `yingchinyeung/Wealthryse` only. Code checks the patch: only the
files the diagnosis named, under `src/`, `scripts/` or `tests/`, a test under
`tests/test_*.py`, valid Python, nothing that looks like a credential. It
never touches workflows, infrastructure, migrations or configuration, opens at
most three a day, and cannot merge or deploy. A refused patch or a GitHub
failure keeps the diagnosis.

**Where you see it.** The administrator posts each new diagnosis once in the
admin chat's "System administrator updates" conversation, and the System
health page lists open incidents with the latest diagnosis and any draft fix
(`ibkr_etl/ops_agent_posts.py`). Until the platform role is granted the
agent's table, both simply show nothing from the agent.

**Turning it on** (each step needs Ying's typed approval naming the resources):

1. **Database.** Rerun `scripts/install_agent_schema.py` with
   `--platform-reader <the platform's role>`. It applies `006_operations.sql`
   and lets that role read `agent.ops_investigations`.
2. **Image.** Rebuild `Dockerfile.agent` and the platform image.
3. **Job.** Deploy `infra/agent-apps.bicep` with `deployOperations=true`. This
   creates `ibkrv2stg-agent-ops` and gives the jobs identity Reader and
   Cost Management Reader on the resource group and Log Analytics Reader on
   `ibkrv2stg-logs`.
4. **Draft pull requests (optional).** Put a fine-grained GitHub token limited
   to this repository's contents and pull requests in a Key Vault secret, give
   the jobs identity Key Vault Secrets User on it (the network thread's step),
   and redeploy with `opsGithubTokenSecretUri`. Without it the agent diagnoses
   but never opens a pull request.

## Personal analyst: memory and weekly briefs

**Memory.** Chat remembers only the current conversation. Memory adds four
preferences per person: answer length (short or detailed), tone (plain or
technical), the benchmark they compare with, and what they care about. A
person sets them under "What I remember" in the chat panel, or by asking the
analyst to remember something; the analyst then confirms it in the chat. Code
checks each value (fixed choices, or up to 60 or 200 characters of plain
text) before saving it to `agent.memories`. Each person sees, changes and
deletes only their own, and retention does not delete them. They go to the
model after the rules, marked as data about how to write, so they never change
the rules, the figures or which account it answers about.

**Weekly brief.** On Saturdays at 09:30 HKT, `weekly_brief` writes four to
eight sentences per account on its latest week of data: the value and
return against the benchmark, the holdings that moved it most, deposits and
withdrawals, and any data gaps. It uses the same tools, model
(`wr-analyst-astra`) and figure check as chat, and it skips a brief that
fails the check. Briefs appear only in the website inbox, to the people who
can see that account. Nothing is emailed. Like the other scheduled jobs, it
covers only the accounts granted to it (`--grant weekly_brief=<account>`, or
`--grant weekly_brief=all` for every active account and portfolio, including
ones onboarded later; `012_all_accounts_grants.sql`).

**Turning it on** (each step needs Ying's typed approval naming the resources):

1. **Database.** Rerun `scripts/install_agent_schema.py`. It applies
   `008_chat_memory_and_briefs.sql`.
2. **Image.** Rebuild `Dockerfile.agent` and the platform image (the chat
   panel's "What I remember" view).
3. **Job.** Deploy `infra/agent-apps.bicep`. This adds
   `ibkrv2stg-agent-weekly-brief`, and grant the accounts that should get
   briefs.

## Live market agent

It builds on PR 58's read-only IBKR data: the collector on the gateway VM
stores live account values, positions, quotes and executions. The agent reads
only those stored rows (`010_live_market.sql`): it never runs on the VM and
never connects to the gateway, so it has no route to an order. Like the
website, it shows an account's live data only when that account's
`live_view` switch is `active`; the `preview` pilot stays the owner's own
website view.

- **Chat.** Two new tools for website chat. `get_live_snapshot` gives today's
  net liquidation value, P&L and return, cash, the biggest movers with their
  change since the prior close, and today's executions count, or says live
  data is not available. `run_stress_test` revalues the latest statement
  holdings under hypothetical moves ("Hong Kong falls 10%", "the yen falls 5%")
  by market, currency, sector, asset class or symbol, and returns the total
  impact, its share of NAV and the holdings most affected. Arithmetic runs in
  code, and the answer's figures pass the same check as every chat answer.
  Live positions and chat use `wr-analyst-astra`, as decided.
- **Alerts.** The `live_market` job (`ibkrv2stg-agent-live-market`) runs every
  15 minutes on weekdays in Hong Kong and US trading hours. For each account
  granted to it with its live view on, it posts an inbox card, once a day per
  rule, when the account is up or down 2% today or one position adds or takes
  0.75% of its value. The analyst writes a short explanation from the live
  tools; if the explanation fails the figure check, the card says what moved
  and leaves the explanation out. Nothing is sent outside the website.
- **Website.** The "Live now" card gets an Explain button that asks the
  analyst what moved the account today, and a badge linking to the inbox when
  today has alerts for that account.

**Turning it on** (each step needs Ying's typed approval naming the resources):
PR 58's gateway VM and collector first, per `docs/LIVE_IBKR_DATA.md`; then
rerun `scripts/install_agent_schema.py` (applies `010_live_market.sql`), rebuild
the agent and platform images, deploy `infra/agent-apps.bicep`, and grant
accounts to the job with `--grant live_market=<account>` (or `=all`).

## Modelling tools

The analyst can use the website's four Modelling-tab models (`ibkr_etl.modelling`). Each one is a read-only tool
over the same agent views:

- `project_wealth`: a Monte Carlo projection with contributions, withdrawals, a target and a return assumption.
- `run_what_if`: compares current holdings against hypothetical weights given by symbol (current holdings
  only, with no new tickers).
- `optimise_portfolio`: gives each holding's share of risk, plus minimum-variance, risk-parity and
  maximum-Sharpe mixes. It flags `risk_concentrated_in` for any holding carrying at least 10% of the risk
  and more than 1.25 times its weight.
- `get_factor_exposures`: fits the Fama-French factors from Kenneth French's library. When that site can't be
  reached, it uses fund stand-ins from Yahoo Finance.

Cash earns 0% in these tools, because the agent has no view of the risk-free rate. Each result says so.

The analyst may chain the tools: for example, optimise, then run a what-if on the minimum-variance weights,
then project. It presents every result as a hypothetical model and never as a recommendation. Asking which
mix to hold is still out of scope.

The weekly brief calls `optimise_portfolio` once. It adds one sentence only when a holding's share of risk
stands out.

Turning it on needs a rebuild of the agent image only. There are no schema or infrastructure changes.

## Risk and predictive modelling

The Modelling tab gets two new cards and a stress section, all in `ibkr_etl.risk_forecasting` (standard library
only, run on the account's daily returns):

- **Risk outlook.** A GARCH(1,1) volatility forecast fitted by maximum likelihood. It falls back to EWMA
  (λ 0.94) when there are fewer than 250 returns or GARCH fits no better. One-day and one-month 95% and 99% VaR
  and expected tail loss come from filtered historical simulation. A VaR backtest refits every 63 days and is
  judged with Kupiec's test. Drawdown odds (10%, 20% and 30% over three to twelve months) come from a
  volatility-scaled block bootstrap.
- **Market regimes.** A Gaussian hidden Markov model with two or three states, chosen by BIC. It gives today's
  regime and its odds, and a 1, 2 and 3 month outlook simulated from the regimes. A walk-forward score shows
  how often the 90% and 50% ranges held, and whether the direction beat a naive rule.
- **Factor stress.** Fitted factor betas times a factor shock, from presets or custom shocks capped at ±60%.

Analyst tools: `get_risk_outlook`, `get_market_regimes` and `run_factor_stress` (preset `scenario`, or `shocks`
as `[{factor, change}]`). The analyst chains them for forward-looking risk questions. It turns plain-language
scenarios ("rates jump and small caps sell off") into factor shocks and says which shocks it chose. The weekly
brief adds one sentence when the regime has changed since last week.

The `risk_watch` job (`ibkrv2stg-agent-risk-watch`) runs at 01:15 UTC on weekdays. For each account granted to
it, it applies fixed rules first, then the analyst explains any alert:

- **Alerts** (`risk_alert`, once per trigger): the latest day lost more than its 95% VaR forecast; the account
  moved into its most stressed regime (at least 60% likely, and not a week ago); or forecast volatility is at
  least 1.5 times its long-run level (once a week). An explanation that fails the figure check is left out.
- **Model check** (`model_check`, once a month): the VaR backtest and the outlook score, with a flag when VaR
  breaches fail Kupiec's test or the 90% range held outside 80% to 97% of the time.

**Turning it on** (each step needs Ying's typed approval): rerun `scripts/install_agent_schema.py` (applies
`014_risk_watch.sql`), grant accounts with `--grant risk_watch=<account>` (or `=all`), rebuild the agent and
web images, and deploy `infra/agent-apps.bicep` for the new job.

## Export agent

Each account's Exports tab gets an export agent panel (wherever `AGENT_UI_MODE` shows the agents). The model plans; code
builds and checks.

1. **Plan.** The person describes the export in words. The agent service's planner (`wealthryse_agent.exports`,
   `EXPORT_PLANNER`) returns a plan under a strict JSON schema: which of the four datasets (value history, holdings,
   cash activity, trades), dates, columns, filters (cash activity types, currencies, symbol, side) and totals. It sees
   only the account's shapes and names, never balances or rows. The website validates the plan against the account
   (`ibkr_etl.export_agent.validate_plan`): dates outside the history move to its edges and say so; anything unknown is
   refused, never guessed. The plan is shown in sentences written by code.
2. **Build and check.** The website builds the files from the Exports tab's own reads (`subject_exports`), in a fixed
   order, and totals them with Decimal, one currency at a time. Fixed checks then run: each file reads back row for row,
   every total equals the sum of the detail file, every row is inside the plan's dates and filters, no duplicate
   executions, dates or holdings, amounts are numbers, and ties to the books (the last value equals the account's value
   that day; no gap over 10 days in the value history; holdings plus cash within 1% of the account's value; cash rolls
   forward from opening to closing balances, shown as a note when it doesn't). The checker agent (`EXPORT_CHECKER`)
   reads the results through one tool and writes the cover note; the usual validator rejects any figure not in them,
   and a code-written note is used instead. It can add concerns, which turn a pass into a review, but can't clear a
   failure. Each check is saved in `agent.export_checks` with a fingerprint of the files.
3. **Download.** The website rebuilds the files and serves a zip (README with the cover note and checks,
   `manifest.json` with SHA-256 hashes, the data and totals CSVs) only if the fingerprint matches. A failed export can't
   be downloaded; one that needs review is released by the person, and the record says so. CSV cells that would run as
   spreadsheet formulas are quoted; negative numbers stay numbers.

**Recurring packs.** A plan can be saved to run every month, quarter or year (`agent.export_packs`). The `export_packs`
job (`ibkrv2stg-agent-export-packs`, 01:45 UTC daily, no model) leaves one `export_pack` inbox card per pack and closed
period, shown only to the person who saved it; the card opens the panel, which builds that period through the same
checks. A new pack starts with the last period already closed. Nothing is emailed or sent.

**Turning it on** (each step needs Ying's typed approval): rerun `scripts/install_agent_schema.py` (applies
`015_export_agent.sql`), grant accounts with `--grant export_packs=all` (or per account), rebuild the agent and web
images, and deploy `infra/agent-apps.bicep` for the new job.

## Metrics guide agents

Four pieces keep the Metrics guide (`ibkr_etl/metrics_guide.py`) right and put it to work. All of them read
only, and none sends anything to a client.

- **Keeper (self-maintaining guide).** `scripts/check_metrics_guide.py` reads the website's source for every
  figure label it renders (metric cards, Modelling tiles, the what-if table) and checks each one against the
  guide's names, aliases and `LABEL_MAP`. It also fingerprints the calculation functions each definition
  describes (`WATCHED_SOURCES` in `metrics_guide_audit.py`) against `metrics_guide_sources.json`. CI runs it as
  a warning. The `Metrics guide keeper` workflow runs it after pushes to main and on Mondays. On drift it has
  Claude draft the definitions from the code (source files only), runs the guide tests and opens a pull
  request for review. It needs the `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN`) secret and the repo
  setting that lets Actions open pull requests. Without a secret the run goes red and lists the drift. After
  updating the guide by hand, run `python scripts/check_metrics_guide.py --refresh`.
- **Explain with your numbers.** Each definition on `/metrics-guide` gets a "Work it through with my numbers"
  button. `/v1/explain-metric` runs `METRIC_EXPLAINER` with `get_metric_definition`, the account tools for that
  definition's section (`guide_tools.SECTION_TOOLS`) and the calculator. The analyst recomputes the figure step
  by step and says where the tool's own figure differs. The definition's numbers (`figures_in_text`) count as
  tool figures, so the guide's constants pass the figure check.
- **Ask the guide.** A box at the top of `/metrics-guide` answers plain-language questions through
  `/v1/guide-question` (`GUIDE_ANALYST`). The person can pick an account, or none for definitions only. The
  answer links to the definitions it used (`guide_links` on the answer event). The account chat also has
  `search_metrics_guide` and `get_metric_definition`. The guide tools wrap the tool client in the agent service
  (`guide_tools.GuideTools`), so the tool server is unchanged.
- **Independent formula checker.** The `guide_check` job (`ibkr_etl/metrics_guide_reference.py`,
  `ibkrv2stg-agent-guide-check`) runs at 02:00 UTC on Mondays. Its job-only tool `get_guide_conformance` recomputes
  each granted account's statistics from the guide's technical formulas with a separate implementation. It
  compares them with the website's figures (Performance statistics, drawdowns, win rate, VaR and expected tail
  loss) and with the analyst's `get_risk_metrics` over its own window. When anything differs (beyond 1e-6
  relative), a `guide_check` inbox card lists it, with the analyst's explanation from the definitions. When
  everything matches, one all-clear card goes up a month. The analyst annualises volatility by valuations a year
  rather than the guide's 252, so expect that difference on its rows.

**Turning it on.** Explain with your numbers and Ask the guide ship with the website and agent images, wherever
`AGENT_UI_MODE` shows the agents. The guide check needs Ying's typed approval for each step: rerun
`scripts/install_agent_schema.py` (applies `016_guide_check.sql`), grant accounts with `--grant guide_check=all`
(or per account), rebuild the agent image, and deploy `infra/agent-apps.bicep` for the new job.

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
- The operations agent, when deployed: a job every 5 minutes that calls the
  model only for a new incident, with its own monthly budget (`opsTokenBudget`).
- Weekly briefs: one model run per granted account per week, within the
  daily token budget.
- Live market alerts: a short job every 15 minutes in trading hours that
  reads stored rows only and calls the model once per alert (at most one per
  rule, account and day), within the daily token budget.
- No new database server.

## Not yet done

- The month-end drafts are not wired into the deck renderer. Approving a
  draft marks it ready for the existing report release process.
- The old AI pieces (`ai_analyst.py`, `agent_tools.py` and related modules,
  and the `ibkrv2stg-ai-…` account) stay in place. `agent_tools.py` still feeds
  the factsheets and reporting snapshots, and the month-end factsheet worker
  uses the old model account, so both are removed only after the 105-report
  regeneration and after their calculations move to a plain reporting module.
