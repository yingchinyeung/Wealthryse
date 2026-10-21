"""The agents' figure tools today against certified figures, on a restored copy: counts only.

Before AGENT_CERTIFIED_FIGURE_TOOLS goes to fallback (docs/MEDALLION_CERTIFIED_FIGURES.md, "The
agents' figure tools"), run this against a copy restored after the certified build has run:

    python scripts/compare_agent_certified_figures.py [--as-of YYYY-MM-DD] [--subject ID ...]

For every active account and portfolio (or each --subject) it calls each figure tool twice, as
the tool server and the website analyst do: today's way (the setting off) and the fallback way.
The agents' tools (wealthryse_agent.tools, through the agent schema exactly as the tool server
reads it) are get_performance, compare_to_benchmark, get_risk_metrics over the whole history,
get_nav_history and get_sector_allocation; the website analyst's (ibkr_etl.agent_tools) are
compare_with_benchmarks, get_risk_metrics and get_sector_allocation. It prints, per side, tool
and certified figure, how many of the tool's figures were equal to the certified figure, different,
had no certified figure (missing), or could not be read (error); how many calls the tool itself
refused (both ways alike); and how many values the fallback answer changed beyond the certified
tolerance (must be 0).

Counts, tool names and figure ids only: no values, no account ids. The database URL comes from
an environment variable (--url-env, DATABASE_ADMIN_URL by default) and is never printed; every
connection is read-only (default_transaction_read_only), and nothing is written.

Exit codes: 0 nothing differs and nothing failed; 1 some figure differs, a certified read failed
or the fallback changed a value (read the counts); 2 could not run.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ibkr_etl import certified_figures as register  # noqa: E402
from ibkr_etl import certified_tool_figures as core  # noqa: E402

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"
AGENT_CALLS = (("get_performance", {}, "as_of"), ("compare_to_benchmark", {}, "as_of"),
               ("get_risk_metrics", {"from_date": "1900-01-01"}, "to_date"), ("get_nav_history", {}, "to_date"),
               ("get_sector_allocation", {}, "as_of"))
WEB_CALLS = ("compare_with_benchmarks", "get_risk_metrics", "get_sector_allocation")
VOLATILE = ("generated_at", "correlation_id", "certified", core.SOURCE_FIELD)
NOT_COMPARED = (
    "agent get_risk_metrics (default one-year window), get_holdings weights, compare_to_benchmark and "
    "get_nav_history benchmark returns: their definitions differ from the certified figures' "
    "(wealthryse_agent/certified_fallback.py lists each)",
    "website get_holdings, get_performance_series, get_portfolio_summary, get_data_freshness: no figure to compare",
)
QUIET = ("wealthryse_agent.tools", "uvicorn.error.certified_figures", "uvicorn.error.ibkr_agent")


def _conninfo(url: str) -> str:
    from psycopg.conninfo import make_conninfo

    return make_conninfo(url, options="-c default_transaction_read_only=on -c statement_timeout=300000",
                         application_name="compare-agent-certified-figures")


def _leaves(value: Any, at: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if at not in ("", "/data") or key not in VOLATILE:  # the website's block sits in data
                yield from _leaves(item, f"{at}/{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _leaves(item, f"{at}/{index}")
    else:
        yield at, value


def changed_values(tool: str, today: Mapping, fallback: Mapping, pointers: Mapping[tuple[str, str], str]) -> int:
    """Values the fallback answer has that today's does not, beyond the certified figure's tolerance."""
    before, after = dict(_leaves(today)), dict(_leaves(fallback))
    count = 0
    for at in before.keys() | after.keys():
        old, new = before.get(at), after.get(at)
        if old == new:
            continue
        figure_id = pointers.get((tool, at))
        if figure_id and core.same(old, new, register.figure(figure_id).unit):
            continue
        count += 1
    return count


@contextmanager
def _setting(value: str) -> Iterator[None]:
    previous = os.environ.get(core.SETTING)
    os.environ[core.SETTING] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(core.SETTING, None)
        else:
            os.environ[core.SETTING] = previous


class Report:
    def __init__(self) -> None:
        self.outcomes: Counter = Counter()  # (side, tool, figure_id, outcome)
        self.refused: Counter = Counter()  # (side, tool)
        self.changed: Counter = Counter()  # (side, tool)
        self.subjects = 0

    def add(self, side: str, tool: str, counts: Counter) -> None:
        for (_, figure_id, outcome), n in counts.items():
            self.outcomes[(side, tool, figure_id, outcome)] += n

    @property
    def failed(self) -> bool:
        return any(n for (_, _, _, outcome), n in self.outcomes.items() if outcome in ("different", "error")) \
            or any(self.changed.values())


def subjects(url: str, tenant: str, only: list[str]) -> list[str]:
    import psycopg

    with psycopg.connect(_conninfo(url)) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
        rows = connection.execute(
            "SELECT subject_id FROM public.platform_subjects WHERE tenant_id = %s "
            "AND COALESCE(lifecycle_status, 'active') = 'active' ORDER BY subject_id", (tenant,)).fetchall()
    found = [str(row[0]) for row in rows]
    return [subject for subject in found if subject in set(only)] if only else found


def agent_side(url: str, tenant: str, subject_ids: list[str], as_of: str | None, report: Report) -> None:
    """The agents' tools, through the agent schema as the tool server reads it."""
    import psycopg

    from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
    from wealthryse_agent.database import scoped
    from wealthryse_agent.tools import ToolError, run_tool

    connection = PostgresConnection(psycopg.connect(_conninfo(url), row_factory=_hybrid_row_factory))
    try:
        connection.execute("SELECT set_config('search_path', 'agent', false)")
        connection.execute("SELECT set_config('app.tenant_id', ?, false)", (tenant,))
        connection.commit()
        for subject in subject_ids:
            for tool, extra, date_argument in AGENT_CALLS:
                arguments = {"account_id": subject, **extra, **({date_argument: as_of} if as_of else {})}
                try:
                    with _setting("off"), scoped(connection, [subject]):
                        today = run_tool(connection, tool, arguments)
                    pointers: dict = {}
                    with _setting("fallback"), core.observe(pointers) as counts, scoped(connection, [subject]):
                        fallback = run_tool(connection, tool, arguments)
                except ToolError as error:
                    report.refused[("agent", f"{tool} ({error.code})")] += 1
                    continue
                except Exception as error:  # noqa: BLE001 - counted by type; the next call starts afresh
                    report.refused[("agent", f"{tool} ({type(error).__name__})")] += 1
                    continue
                report.add("agent", tool, counts)
                report.changed[("agent", tool)] += changed_values(tool, today, fallback, pointers)
    finally:
        connection.close()


def web_side(url: str, tenant: str, subject_ids: list[str], as_of: str | None, report: Report) -> None:
    """The website analyst's tools, on a connection like the website's (public schema, read-only)."""
    import psycopg
    from psycopg.rows import dict_row

    from ibkr_etl.agent_tools import AgentToolContext, AgentToolError, execute_tool

    with psycopg.connect(_conninfo(url), row_factory=dict_row) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
        connection.commit()
        for subject in subject_ids:
            context = AgentToolContext(tenant, "certified-figures-comparison", frozenset({subject}), "comparison")
            for tool in WEB_CALLS:
                arguments = {"subject_id": subject, **({"as_of": as_of} if as_of else {})}
                try:
                    today = execute_tool(connection, context, tool, arguments)
                    pointers: dict = {}
                    with core.observe(pointers) as counts:
                        fallback = execute_tool(connection, context, tool, arguments, certified_mode="fallback")
                except AgentToolError as error:
                    report.refused[("website", f"{tool} ({error.code})")] += 1
                    continue
                except Exception as error:  # noqa: BLE001 - counted by type; the next call starts afresh
                    report.refused[("website", f"{tool} ({type(error).__name__})")] += 1
                    continue
                finally:
                    if connection.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                        connection.rollback()
                report.add("website", tool, counts)
                report.changed[("website", tool)] += changed_values(tool, today, fallback, pointers)


def render(report: Report, as_of: str | None) -> str:
    lines = [f"Agents' figure tools against certified figures, as of {as_of or 'each subject’s latest valuation'}: "
             f"{report.subjects} subjects", "",
             f"{'side':8} {'tool':24} {'figure':34} {'equal':>7} {'different':>9} {'missing':>7} {'error':>5}"]
    rows: dict[tuple[str, str, str], Counter] = {}
    for (side, tool, figure_id, outcome), n in report.outcomes.items():
        rows.setdefault((side, tool, figure_id), Counter())[outcome] += n
    for (side, tool, figure_id), counts in sorted(rows.items()):
        lines.append(f"{side:8} {tool:24} {figure_id:34} {counts['equal']:>7} {counts['different']:>9} "
                     f"{counts['missing']:>7} {counts['error']:>5}")
    if not rows:
        lines.append("(no figure compared)")
    lines += ["", "Calls the tool refused (today's way and the fallback way alike):"]
    lines += [f"  {side} {tool}: {n}" for (side, tool), n in sorted(report.refused.items())] or ["  none"]
    lines += ["", f"Values the fallback changed beyond the certified tolerance: {sum(report.changed.values())}"]
    lines += [f"  {side} {tool}: {n}" for (side, tool), n in sorted(report.changed.items()) if n]
    lines += ["", "Not compared:"] + [f"  {text}" for text in NOT_COMPARED]
    return "\n".join(lines)


def run(url: str, *, tenant: str = DEFAULT_TENANT, only: list[str] | None = None, as_of: str | None = None,
        read_timeout_ms: int | None = None) -> Report:
    from wealthryse_agent import certified_fallback

    if read_timeout_ms:
        certified_fallback.READ_TIMEOUT_MS = core.WEB_READ_TIMEOUT_MS = int(read_timeout_ms)
    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url  # the website's certified reader opens its own read-only connection
    levels = {name: logging.getLogger(name).level for name in QUIET}
    for name in QUIET:
        logging.getLogger(name).setLevel(logging.CRITICAL)  # the per-call lines would repeat the counts
    try:
        report = Report()
        subject_ids = subjects(url, tenant, list(only or []))
        report.subjects = len(subject_ids)
        agent_side(url, tenant, subject_ids, as_of, report)
        web_side(url, tenant, subject_ids, as_of, report)
        return report
    finally:
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url-env", default="DATABASE_ADMIN_URL", help="environment variable with the copy's URL")
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--subject", action="append", default=[], help="only this subject; repeatable")
    parser.add_argument("--as-of", help="YYYY-MM-DD; default each subject's latest valuation")
    parser.add_argument("--read-timeout-ms", type=int, default=None,
                        help="the certified readers' statement timeout (default: the live 2000 and 1000 ms)")
    args = parser.parse_args(argv)
    url = os.environ.get(args.url_env, "")
    if not url.startswith(("postgres://", "postgresql://")):
        print(f"{args.url_env} is not set to a PostgreSQL URL", file=sys.stderr)
        return 2
    try:
        report = run(url, tenant=args.tenant, only=args.subject, as_of=args.as_of,
                     read_timeout_ms=args.read_timeout_ms)
    except Exception as error:  # noqa: BLE001 - the type only: messages can quote data
        print(f"could not run: {type(error).__name__}", file=sys.stderr)
        return 2
    print(render(report, args.as_of))
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
