"""Publish gate for gold (U2): check each new gold version before readers see it.

Each gold writer calls `before_commit(connection, {table: partition})` right before it
commits a new version: the rows it just wrote for one subject (the partition is the column
values that pick them). PUBLISH_GATE_MODE, set per job, says what happens:

- off (the default, also when unset): returns at once. No query, no log line.
- shadow: checks the uncommitted version on the writer's own connection, inside a savepoint
  that is always rolled back (READ ONLY, hard time limit), logs one `PUBLISH_GATE {json}`
  line and lets the writer commit.
- enforce: the same, but a version that fails is rolled back and BuildHeldBack is raised.
  Nothing new was committed, so readers keep the last good version. Each job treats it as
  that subject's failure and carries on with the others. This applies to the benchmark
  comparisons and the crisis scenarios, whose readers then still see the earlier rows. The
  risk snapshots and the crisis replay are shown for one exact date, so holding them back
  would show "being prepared" instead; their verdicts are logged only (GoldTable.holds_back).

Checks on each version:

- contract: the table's quality rules from its data contract, each a count of bad rows that
  must be 0, run on the version only. A failing error rule fails it, a warning rule warns.
  The rules ship in publish_gate_rules.json, generated from infra/medallion/contracts by
  `--write-rules` (a test keeps the two equal).
- nav_matches_ibkr (performance_comparison_daily): the NAV in each row equals the books' NAV
  for that day, which for an IBKR account is IBKR's own reported total.
- cash_plus_positions (performance_comparison_daily): positions plus cash equal the NAV on the
  latest day, as the export agent checks it. Warns only.
- returns_continuity (performance_comparison_daily): each return series chains without
  restarts, breaks or jumps. Days the inputs have but the series lacks, and older rows left
  inside the rebuilt range, warn only.
- benchmark_coverage (performance_comparison_daily, risk_analytics_snapshots): a subject with
  a NAV has its primary benchmark comparison. A missing secondary, or a comparison more
  than four days behind the NAV, warns only.

The logged line and the verdict never carry money figures (NAV, positions, cash, the books'
NAV): the line goes to Log Analytics, so gaps are given as a share of NAV instead.

Only a failed check holds a version back; anything that could not be checked warns, so a
slow database never holds back a good version. Tolerances come from the code that already
compares these figures (see below).

Month-end snapshots are written by subprocesses, so the month-end job calls `report()`
afterwards instead: it judges what the run wrote, logs one line, and never stops anything.
`python -m ibkr_etl.publish_gate <job>` prints the same for any job's writes today (UTC).
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time as monotonic_time
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

MODE_ENV = "PUBLISH_GATE_MODE"
DATABASE_URL_ENV = "PUBLISH_GATE_DATABASE_URL"  # optional override of DATABASE_URL
OFF, SHADOW, ENFORCE = "off", "shadow", "enforce"
OFF_VALUES = frozenset({"", "off", "false", "0", "no", "none", "disabled"})
LOG_PREFIX = "PUBLISH_GATE "
EXIT_REJECTED = 65  # EX_DATAERR: a job that held back a version exits with this once it has finished
POLICY_VERSION = "publish-gate-v1"
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"
TIME_BUDGET_SECONDS = 30.0  # for all of one call's checks, connection included
STATEMENT_TIMEOUT_MS = 5000  # as layer_freshness
LOCK_TIMEOUT_MS = 2000
RULES_PATH = Path(__file__).with_name("publish_gate_rules.json")
SAMPLES = 5
MAX_REASONS = 40

PASS, WARN, FAIL = "pass", "warn", "fail"
SKIPPED, UNAVAILABLE = "skipped", "unavailable"  # nothing to judge; could not be judged (counts as warn)
_RANK = {SKIPPED: 0, PASS: 0, WARN: 1, UNAVAILABLE: 1, FAIL: 2}

# Tolerances, taken from the code that already compares these figures.
# comparison_source_evidence.verify_sources: a stored comparison figure may differ from its
# source by float-to-database rounding only.
ROUNDING_RELATIVE = 1e-10
ROUNDING_ABSOLUTE = 1e-8
# benchmark_analytics.build_comparison: review thresholds for one day's return, the longest
# gap between observations before it warns, and how stale the latest observation may be (also
# the risk job's four-day tolerance for the primary comparison).
MAXIMUM_PORTFOLIO_RETURN = 1.0
MAXIMUM_BENCHMARK_RETURN = 0.5
GAP_DAYS = 7
STALENESS_DAYS = 4
# The holdings tie (cash plus positions against NAV) uses export_agent.HOLDINGS_TIE_TOLERANCE:
# within 1% of NAV, and at least one unit of currency; outside it the export agent asks for a review.

ISO_TIME = "^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}"
ISO_DATE = "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
# A finite decimal number as Python's float() reads one ("12", "12.", ".5", "+1e3", " 7 ").
NUMBER = r"^\s*[-+]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][-+]?[0-9]+)?\s*$"


@dataclass(frozen=True)
class GoldTable:
    name: str  # gold.<table>
    written_at: str  # column holding the UTC write time the writer sets
    lineage: bool = False  # has lineage_run_id (migration 071)
    checks: tuple[str, ...] = ()  # reconciliations beyond the contract rules
    # Whether enforce may hold a failing version back: only where readers then still see the
    # last good one. Pages that show one exact date (the risk page's latest NAV date, the
    # crisis replay's latest holdings date) would show "being prepared" instead, so for those
    # the verdict is logged only, until S6 gives readers a fallback.
    holds_back: bool = True


COMPARISON = "gold.performance_comparison_daily"
RISK = "gold.risk_analytics_snapshots"
TABLES: dict[str, GoldTable] = {table.name: table for table in (
    # Readers keep the earlier rows of each series (the writer upserts the same keys).
    GoldTable(COMPARISON, "generated_at", True,
              ("nav_matches_ibkr", "cash_plus_positions", "returns_continuity", "benchmark_coverage")),
    GoldTable("gold.crisis_replay_daily", "generated_at", holds_back=False),
    GoldTable("gold.crisis_replay_components", "fetched_at", holds_back=False),
    # crisis_scenarios.load reads the latest holdings date on or before the one asked for.
    GoldTable("gold.crisis_scenario_results", "generated_at", True),
    GoldTable(RISK, "generated_at", True, ("benchmark_coverage",), holds_back=False),
    # Written by the month-end subprocesses; judged afterwards by report(), which never stops.
    GoldTable("gold.reporting_snapshots", "created_at", holds_back=False),
)}

# The jobs that write gold, and the gold tables each writes (catalog and contract producers).
JOB_TABLES: dict[str, tuple[str, ...]] = {
    "benchmark-daily": (COMPARISON, "gold.crisis_replay_daily", "gold.crisis_replay_components",
                        "gold.crisis_scenario_results"),
    "derived-daily": (COMPARISON, "gold.crisis_replay_daily", "gold.crisis_replay_components", RISK),
    "risk-snapshots": (RISK,),
    "crisis-scenarios-daily": ("gold.crisis_scenario_results",),
    "month-end-factsheet": ("gold.reporting_snapshots",),
}
# benchmark-daily must cover every subject it is asked to, as it picks them.
REGISTERED_SUBJECTS_SQL = """
SELECT r.subject_id FROM subject_operating_registrations r
JOIN platform_subjects s ON s.tenant_id = r.tenant_id AND s.subject_id = r.subject_id
WHERE r.capability = 'benchmark_refresh' AND r.operating_state = 'active' AND r.effective_to IS NULL
  AND s.lifecycle_status = 'active'
ORDER BY r.subject_id"""


def mode(environ: Mapping[str, str] | None = None) -> tuple[str, str | None]:
    """The mode, and a note when the value was not understood (then shadow)."""
    environ = os.environ if environ is None else environ
    value = str(environ.get(MODE_ENV, "")).strip().lower()
    if value in OFF_VALUES:
        return OFF, None
    if value in (SHADOW, ENFORCE):
        return value, None
    return SHADOW, f"unknown {MODE_ENV} value; ran as shadow"


# ---------------------------------------------------------------- contract rules


def contract_rules(contracts_dir: Path) -> dict[str, Any]:
    """The quality rules of each gold table the gate judges (TABLES), read from its data contract.
    Other gold tables, such as the certified figures with their own build checks, are left to the
    administrator's daily layer quality check."""
    from . import medallion_contracts as mc

    tables: dict[str, Any] = {}
    for path, contract in sorted(mc.load_all(contracts_dir).items()):
        if contract.get("domain") != "gold":
            continue
        obj = mc.table_object(contract)
        if obj.get("physicalName") not in TABLES:
            continue
        found = [(None, rule) for rule in obj.get("quality") or []]
        for prop in obj.get("properties") or []:
            found += [(prop.get("name"), rule) for rule in prop.get("quality") or []]
        rules = []
        for column, rule in found:
            if rule.get("type") != "sql" or set(rule) & mc.COMPARATORS != {"mustBe"}:
                raise ValueError(f"{path}: rule {rule.get('name')} is not an SQL count with mustBe")
            rules.append({"name": rule["name"], "severity": rule["severity"], "column": column,
                          "dimension": rule.get("dimension"), "query": " ".join(str(rule["query"]).split()),
                          "mustBe": rule["mustBe"]})
        tables[obj["physicalName"]] = {
            "contract": contract["id"], "version": contract["version"],
            "calculation_version_column": mc.custom(contract.get("customProperties"), "calculationVersionColumn"),
            "rules": sorted(rules, key=lambda rule: (rule["name"], rule["column"] or "")),
        }
    return {"source": "infra/medallion/contracts", "tables": tables}


def rules_text(contracts_dir: Path) -> str:
    return json.dumps(contract_rules(contracts_dir), indent=1, sort_keys=True) + "\n"


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["tables"]


_KEYWORDS = frozenset({"where", "group", "order", "having", "limit", "offset", "join", "left", "right", "inner",
                       "full", "cross", "natural", "on", "using", "union", "except", "intersect", "window",
                       "fetch", "for"})


def scoped(query: str, table: str, condition: str) -> str:
    """`query` with each reference to `table` narrowed to the rows matching `condition`.
    Other tables a rule names are read whole."""
    name = table.split(".", 1)[1]
    parts, position, count = [], 0, 0
    for match in re.finditer(rf"(?<![\w.]){re.escape(table)}(?![\w.])", query):
        parts.append(query[position:match.start()])
        parts.append(f"(SELECT * FROM {table} WHERE {condition})")
        alias = re.match(r"\s+(?:AS\s+)?([A-Za-z_]\w*)", query[match.end():], re.IGNORECASE)
        if not alias or alias.group(1).lower() in _KEYWORDS:
            parts.append(f" AS {name}")
        position = match.end()
        count += 1
    if not count:
        raise ValueError(f"the rule does not name {table}")
    parts.append(query[position:])
    return "".join(parts)


# ---------------------------------------------------------------- which rows the run wrote


@dataclass(frozen=True)
class Scope:
    since: datetime | None = None  # the run's start
    run_id: str | None = None  # the run's lineage id, when its session was marked
    partition: tuple[tuple[str, Any], ...] = ()  # column values that pick one new version

    def condition(self, table: GoldTable, alias: str = "") -> tuple[str, str, dict[str, Any]]:
        """(basis, SQL condition, parameters) that pick this run's rows of `table`."""
        prefix = f"{alias}." if alias else ""
        if self.partition:
            parts, params = [], {}
            for index, (column, value) in enumerate(self.partition):
                if not re.fullmatch(r"[a-z_]+", column):
                    raise ValueError(f"not a column name: {column!r}")
                parts.append(f"{prefix}{column} = %(p{index})s")
                params[f"p{index}"] = value
            return "partition", " AND ".join(parts), params
        if self.run_id and table.lineage:
            return "lineage_run_id", f"{prefix}lineage_run_id = %(run_id)s::uuid", {"run_id": self.run_id}
        if self.since is None:
            raise ValueError("the run's start time is not known")
        column = prefix + table.written_at
        return ("written_since",
                f"(CASE WHEN {column} ~ '{ISO_TIME}' THEN {column}::timestamptz END) >= %(since)s::timestamptz",
                {"since": self.since.astimezone(timezone.utc).replace(microsecond=0)})

    def describe(self) -> dict[str, Any]:
        if self.partition:
            return {"partition": dict(self.partition)}
        return {"since": None if self.since is None else self.since.astimezone(timezone.utc).isoformat(),
                "run_id": self.run_id}


# ---------------------------------------------------------------- judging (pure)


def _worst(states: Iterable[str]) -> str:
    """fail over warn over pass. Only fail holds a version back: could not be judged
    (unavailable, or anything unknown) counts as warn, nothing to judge as pass."""
    worst = PASS
    for state in states:
        state = state if state in (PASS, FAIL) else PASS if state == SKIPPED else WARN
        if _RANK[state] > _RANK[worst]:
            worst = state
    return worst


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def judge_contract(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Each rule's count of bad rows in the build against the 0 it must be."""
    failed, reasons, states = [], [], []
    results = list(evidence.get("results") or [])
    for result in results:
        rule, severity = result.get("rule"), result.get("severity")
        if "error" in result:
            states.append(UNAVAILABLE)
            failed.append({"rule": rule, "severity": severity, "error": result["error"]})
            reasons.append(f"rule {rule} could not run ({result['error']})")
            continue
        count = int(result.get("count") or 0)
        if count != int(result.get("must_be", 0)):
            state = FAIL if severity == "error" else WARN
            states.append(state)
            failed.append({"rule": rule, "severity": severity, "count": count})
            reasons.append(f"rule {rule} ({severity}) found {count} bad row(s)")
    verdict = _worst(states) if results else SKIPPED
    return {"verdict": verdict, "version": evidence.get("version"), "rules": len(results), "failed": failed,
            "reasons": reasons}


NAV_PROBLEMS = {
    # problem: (state, what it means)
    "missing": (FAIL, "has no NAV in the books"),
    "mismatch": (FAIL, "differs from the books' NAV"),
    "ambiguous": (WARN, "matches more than one NAV row in the books"),
    "unreadable": (WARN, "is beside a books NAV that is not a plain number"),
}


def judge_nav(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """The NAV in each comparison row against the books' NAV for that day (IBKR's reported
    total for an IBKR account), read as the writer reads it (subject_nav_series). Missing or
    different fails; books that are ambiguous or unreadable warn, as does a day where IBKR's
    statement has more than one total row (v_daily_nav would count it twice)."""
    problems = {name: dict(item) for name, item in (evidence.get("problems") or {}).items()}
    checked = int(evidence.get("checked") or 0)
    ambiguous = dict(evidence.get("ambiguous_ibkr_total") or {})
    reasons, states = [], []
    for name, (state, label) in NAV_PROBLEMS.items():
        rows = int((problems.get(name) or {}).get("rows") or 0)
        if rows:
            states.append(state)
            reasons.append(f"{rows} row(s) where the NAV {label}")
    if int(ambiguous.get("dates") or 0):
        states.append(WARN)
        reasons.append(f"{ambiguous['dates']} day(s) where IBKR's statement has more than one total NAV row "
                       "(TOTAL with ALL or blank)")
    verdict = _worst(states) if checked else SKIPPED
    for item in problems.values():
        item["samples"] = [_nav_sample(sample) for sample in item.get("samples") or ()]
    return {"verdict": verdict, "checked": checked, "problems": problems, "ambiguous_ibkr_total": ambiguous,
            "tolerance": {"relative": ROUNDING_RELATIVE, "absolute": ROUNDING_ABSOLUTE}, "reasons": reasons}


# Money figures the measuring queries read. Verdicts are logged (PUBLISH_GATE lines in Log
# Analytics) and stored with certified builds, so none of these ever reaches a verdict.
MONEY_KEYS = frozenset({"nav", "positions", "cash", "difference", "gold", "books"})


def _share(part: Any, whole: Any) -> str | None:
    """part as a share of whole, to four places (0.0123 is 1.23%); None if either is unreadable or whole is 0."""
    from decimal import Decimal, InvalidOperation

    try:
        part, whole = Decimal(str(part)), Decimal(str(whole))
        if not whole.is_finite() or not part.is_finite() or whole == 0:
            return None
        return str((part / abs(whole)).quantize(Decimal("0.0001")))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _nav_sample(sample: Any) -> dict[str, Any]:
    """A NAV problem sample without its figures: the gap as a share of the books' NAV instead."""
    sample = dict(sample) if isinstance(sample, Mapping) else {}
    gold, books = sample.get("gold"), sample.get("books")
    kept = {key: value for key, value in sample.items() if key not in MONEY_KEYS}
    if gold is not None and books is not None:
        try:
            kept["gap_share"] = _share(float(gold) - float(books), books)
        except (TypeError, ValueError):
            pass
    return kept


def without_figures(value: Any) -> Any:
    """A copy of a verdict with every money figure taken out, at any depth."""
    if isinstance(value, Mapping):
        return {key: without_figures(item) for key, item in value.items() if key not in MONEY_KEYS}
    if isinstance(value, (list, tuple)):
        return [without_figures(item) for item in value]
    return value


def judge_holdings(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Positions plus cash against NAV on each subject's latest day in the build."""
    from decimal import Decimal, InvalidOperation

    from .export_agent import HOLDINGS_TIE_TOLERANCE

    subjects, reasons, states = [], [], []
    for item in evidence.get("subjects") or []:
        entry = {key: item[key] for key in ("subject_id", "date", "currency") if key in item}
        if "not_applicable" in item or "reason" in item:
            entry["not_checked"] = item.get("not_applicable") or item.get("reason")
            subjects.append(entry)
            continue
        if "error" in item:
            states.append(UNAVAILABLE)
            entry["error"] = item["error"]
            reasons.append(f"{item.get('subject_id')}: could not read the books ({item['error']})")
            subjects.append(entry)
            continue
        try:
            nav, positions, cash = (Decimal(str(item[key])) for key in ("nav", "positions", "cash"))
        except (KeyError, InvalidOperation):
            states.append(UNAVAILABLE)
            reasons.append(f"{item.get('subject_id')}: unreadable figures")
            continue
        difference = positions + cash - nav
        tolerance = max(Decimal(1), abs(nav) * HOLDINGS_TIE_TOLERANCE)
        within = abs(difference) <= tolerance
        states.append(PASS if within else WARN)
        # The verdict is logged to Log Analytics, so it carries how far apart, never the money figures.
        share = _share(difference, nav)
        entry.update(within=within, difference_share=share)
        subjects.append(entry)
        if not within:
            apart = f"{abs(Decimal(share)):.2%}" if share is not None else "more than 1 unit"
            reasons.append(f"{item.get('subject_id')} on {item.get('date')}: positions plus cash differ from NAV "
                           f"by {apart} of NAV (more than 1%)")
    verdict = _worst(states) if states else SKIPPED
    return {"verdict": verdict, "subjects": subjects, "reasons": reasons}


CONTINUITY_FAILURES = {
    "no_start": "the series does not start with an empty return and both indexes at 100",
    "restart": "a return is empty after the first day (the series restarts)",
    "portfolio_jump": f"a daily portfolio return is beyond ±{MAXIMUM_PORTFOLIO_RETURN:g}",
    "benchmark_jump": f"a daily benchmark return is beyond ±{MAXIMUM_BENCHMARK_RETURN:g}",
    "portfolio_chain": "the portfolio index does not chain with the return",
    "benchmark_chain": "the benchmark index does not chain with the return",
    "benchmark_return": "the benchmark return does not follow from the levels",
}


def judge_continuity(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Each return series in the build: breaks and jumps fail; days the inputs have but the
    series lacks, and older rows left inside the rebuilt range, warn. A long gap the inputs
    share is only noted."""
    problems = {name: dict(item) for name, item in (evidence.get("problems") or {}).items()}
    series = int(evidence.get("series") or 0)
    reasons, states = [], []
    for name, label in CONTINUITY_FAILURES.items():
        rows = int((problems.get(name) or {}).get("rows") or 0)
        if rows:
            states.append(FAIL)
            reasons.append(f"{rows} row(s): {label}")
    missing = dict(evidence.get("missing_days") or {})
    if int(missing.get("days") or 0):
        states.append(WARN)
        reasons.append(f"{missing['days']} day(s) with a NAV and a benchmark price are missing from the series")
    left = dict(evidence.get("left_over_rows") or {})
    if int(left.get("rows") or 0):
        states.append(WARN)
        reasons.append(f"{left['rows']} older row(s) inside the rebuilt range were not rewritten by this run")
    verdict = _worst(states) if series else SKIPPED
    return {"verdict": verdict, "series": series, "problems": problems, "missing_days": missing,
            "left_over_rows": left, "long_gaps": int((problems.get("long_gap") or {}).get("rows") or 0),
            "reasons": reasons}


def judge_coverage(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Every subject with a NAV has its primary benchmark comparison, and every usable risk
    snapshot built on a benchmark has that comparison. A missing secondary, or one more than
    four days behind the NAV, only warns: the writers already hold the primary to its
    exchange calendar (weekends, HK and other holidays), which a day count cannot."""
    reasons, states, missing = [], [], []
    expected = 0
    for item in evidence.get("comparisons") or []:
        # performance_comparison_daily: the latest comparison against the latest NAV.
        latest_nav, latest = _day(item.get("latest_nav")), _day(item.get("latest_comparison"))
        if latest_nav is None:
            continue  # no NAV yet, so no comparison is due
        expected += 1
        if latest is None:
            states.append(FAIL if item.get("mapping_type") == "primary" else WARN)
            reasons.append(f"{item.get('subject_id')} has no {item.get('mapping_type')} comparison against "
                           f"{item.get('benchmark_id')}")
        elif (latest_nav - latest).days > STALENESS_DAYS:
            states.append(WARN)
            reasons.append(f"{item.get('subject_id')}'s {item.get('mapping_type')} comparison against "
                           f"{item.get('benchmark_id')} ends {latest}, more than {STALENESS_DAYS} days before "
                           f"its NAV on {latest_nav}")
        else:
            continue
        missing.append(dict(item))
    for item in evidence.get("snapshots") or []:
        # risk_analytics_snapshots: a usable snapshot built on a benchmark has that comparison.
        if not item.get("benchmark_id"):
            continue
        expected += 1
        as_of, latest = _day(item.get("as_of_date")), _day(item.get("latest_comparison"))
        if latest is None:
            states.append(FAIL)
            reasons.append(f"{item.get('subject_id')}'s risk snapshot on {item.get('as_of_date')} has no "
                           f"{item.get('benchmark_id')} comparison")
        elif as_of is None or (as_of - latest).days > STALENESS_DAYS:
            states.append(WARN)
            reasons.append(f"{item.get('subject_id')}'s risk snapshot on {item.get('as_of_date')} uses a "
                           f"{item.get('benchmark_id')} comparison ending {latest}")
        else:
            continue
        missing.append(dict(item))
    verdict = _worst(states) if expected else SKIPPED
    return {"verdict": verdict, "expected": expected, "missing": missing[:SAMPLES], "missing_count": len(missing),
            "reasons": reasons}


JUDGES: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "contract": judge_contract,
    "nav_matches_ibkr": judge_nav,
    "cash_plus_positions": judge_holdings,
    "returns_continuity": judge_continuity,
    "benchmark_coverage": judge_coverage,
}


def judge_table(table: GoldTable, observation: Mapping[str, Any]) -> dict[str, Any]:
    """One table's version, judged. A check that could not run warns."""
    if "error" in observation:
        return {"verdict": _worst([UNAVAILABLE]), "rows": None, "checks": {},
                "reasons": [f"could not be checked ({observation['error']})"]}
    checks: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []
    for name in ("contract", *table.checks):
        evidence = (observation.get("checks") or {}).get(name)
        if evidence is None or "error" in evidence:
            error = "not measured" if evidence is None else evidence["error"]
            result = {"verdict": UNAVAILABLE, "reasons": [f"could not be checked ({error})"]}
        else:
            try:
                result = JUDGES[name](evidence)
            except Exception as error:  # noqa: BLE001 - a broken judge is a reason, never a crash
                result = {"verdict": UNAVAILABLE, "reasons": [f"could not be judged ({type(error).__name__})"]}
        checks[name] = result
        reasons += [f"{name}: {reason}" for reason in result.get("reasons", ())]
    return {"verdict": _worst(check["verdict"] for check in checks.values()), "rows": observation.get("rows"),
            "basis": observation.get("basis"), "reasons": reasons, "checks": checks}


def decide_tables(names: Iterable[str], observations: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The verdict on the versions of these gold tables from what was observed. Pure."""
    tables: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []
    for name in names:
        result = judge_table(TABLES[name], observations.get(name, {"error": "not measured"}))
        tables[name] = result
        reasons += [f"{name}: {reason}" for reason in result["reasons"]]
    verdict = _worst(table["verdict"] for table in tables.values())
    return {"verdict": verdict, "reasons": reasons[:MAX_REASONS], "reasons_total": len(reasons),
            "rows": sum(int(table.get("rows") or 0) for table in tables.values()), "tables": tables,
            "policy_version": POLICY_VERSION}


def decide(job: str, observations: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The verdict on everything one job run wrote. Pure."""
    if job not in JOB_TABLES:
        raise ValueError(f"Unknown job: {job}")
    return decide_tables(JOB_TABLES[job], observations)


# ---------------------------------------------------------------- measuring (read-only SQL)


def _rows(session: Any, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    # Each query under a savepoint, so a failed one does not abort the rest.
    with session.transaction():
        cursor = session.execute(sql, params)
        names = [column.name for column in cursor.description or ()]
        return [dict(row) if isinstance(row, Mapping) else dict(zip(names, row)) for row in cursor.fetchall()]


def _scalar(session: Any, sql: str, params: Mapping[str, Any]) -> Any:
    rows = _rows(session, sql, params)
    return next(iter(rows[0].values())) if rows else None


def _problems(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["problem"]): {"rows": int(row["rows"]), "samples": list(row.get("samples") or [])[:SAMPLES]}
            for row in rows if row.get("problem")}


def _sample(rows: list[dict[str, Any]], count: str) -> dict[str, Any]:
    row = rows[0] if rows else {}
    return {count: int(row.get(count) or 0), "samples": list(row.get("samples") or [])[:SAMPLES]}


def _within(actual: str, expected: str) -> str:
    return f"abs({actual} - {expected}) <= greatest({ROUNDING_ABSOLUTE}, abs({expected}) * {ROUNDING_RELATIVE})"


def _nav(session: Any, build: str, params: dict) -> dict[str, Any]:
    """`build` is the version's rows as a parenthesised relation."""
    checked = _scalar(session, f"SELECT count(*) FROM (SELECT DISTINCT subject_id, observation_date, "
                               f"reporting_currency, portfolio_value FROM {build} AS t) d", params)
    rows = _rows(session, f"""
WITH build AS (SELECT DISTINCT tenant_id, subject_id, observation_date, reporting_currency, portfolio_value
               FROM {build} AS t),
books AS (
    -- As provisional_benchmark._portfolio_values reads the NAV.
    SELECT n.tenant_id, n.subject_id, n.snapshot_date, n.currency, count(*) AS entries,
           min(n.source_kind) AS source_kind,
           min(CASE WHEN n.total_text ~ '{NUMBER}' THEN n.total_text::double precision END) AS total
    FROM subject_nav_series n
    WHERE (n.tenant_id, n.subject_id, n.currency) IN (SELECT tenant_id, subject_id, reporting_currency FROM build)
      AND n.total_text IS NOT NULL AND n.total_text <> ''
    GROUP BY 1, 2, 3, 4),
judged AS (
    SELECT b.subject_id, b.observation_date, b.portfolio_value, k.total, k.source_kind,
           CASE WHEN k.entries IS NULL THEN 'missing'
                WHEN k.entries > 1 THEN 'ambiguous'
                WHEN k.total IS NULL THEN 'unreadable'
                WHEN NOT ({_within('b.portfolio_value', 'k.total')}) THEN 'mismatch' END AS problem
    FROM build b
    LEFT JOIN books k ON k.tenant_id = b.tenant_id AND k.subject_id = b.subject_id
     AND k.snapshot_date = b.observation_date AND k.currency = b.reporting_currency)
SELECT problem, count(*) AS rows,
       (array_agg(json_build_object('subject_id', subject_id, 'date', observation_date, 'gold', portfolio_value,
                                    'books', total, 'source', source_kind)
                  ORDER BY subject_id, observation_date))[1:{SAMPLES}] AS samples
FROM judged WHERE problem IS NOT NULL GROUP BY problem""", params)
    ambiguous = _rows(session, f"""
WITH days AS (SELECT DISTINCT subject_id, observation_date, reporting_currency FROM {build} AS t)
SELECT count(*) AS dates,
       (array_agg(json_build_object('subject_id', d.subject_id, 'date', d.observation_date)
                  ORDER BY d.subject_id, d.observation_date))[1:{SAMPLES}] AS samples
FROM days d
WHERE (SELECT count(*) FROM nav_snapshots n WHERE n.account_id = d.subject_id
       AND n.snapshot_date = d.observation_date AND n.currency = d.reporting_currency
       AND upper(n.asset_class) IN ('TOTAL', 'ALL', '')) > 1""", params)
    return {"checked": int(checked or 0), "problems": _problems(rows),
            "ambiguous_ibkr_total": _sample(ambiguous, "dates")}


def _holdings(session: Any, build: str, params: dict, tenant_id: str, stop: Callable[[], bool]) -> dict[str, Any]:
    """The export agent's holdings tie (export_agent_source.holdings_value) on each subject's
    latest day in the build."""
    from contextlib import nullcontext
    from decimal import Decimal

    from .export_agent_source import PlatformExportSource

    latest = _rows(session, f"""
SELECT b.subject_id, b.reporting_currency AS currency, max(b.observation_date) AS date,
       min(s.subject_type) AS subject_type
FROM {build} AS b
LEFT JOIN platform_subjects s ON s.tenant_id = b.tenant_id AND s.subject_id = b.subject_id
GROUP BY 1, 2 ORDER BY 1, 2""", params)
    subjects = []
    for row in latest:
        entry = {"subject_id": row["subject_id"], "date": row["date"], "currency": row["currency"]}
        if stop():
            break
        if row["subject_type"] == "composite_portfolio":
            subjects.append({**entry, "not_applicable": "a composite's sleeves are reconciled on their own"})
            continue
        try:
            source = PlatformExportSource(
                database=lambda: nullcontext(session), tenant_id=tenant_id, subject_id=str(row["subject_id"]),
                access={"reporting_currency": row["currency"], "subject_type": row["subject_type"]},
                cash_filter=None, trade_filter=None)
            with session.transaction():
                value = source.holdings_value(str(row["date"]))
        except Exception as error:  # noqa: BLE001 - reported per subject
            subjects.append({**entry, "error": _error_label(error)})
            continue
        if "reason" in value:
            subjects.append({**entry, "reason": value["reason"]})
        else:
            subjects.append({**entry, **{key: str(Decimal(value[key])) for key in ("nav", "positions", "cash")}})
    return {"subjects": subjects}


def _continuity(session: Any, table: GoldTable, build: str, params: dict, scope: Scope) -> dict[str, Any]:
    gap = f"to_date(observation_date, 'YYYY-MM-DD') - to_date(previous_date, 'YYYY-MM-DD') > {GAP_DAYS}"
    rows = _rows(session, f"""
WITH ordered AS (
    SELECT subject_id, benchmark_id, calculation_version, observation_date, benchmark_level,
           normalized_portfolio, normalized_benchmark, portfolio_return, benchmark_return,
           row_number() OVER w AS n, lag(observation_date) OVER w AS previous_date,
           lag(normalized_portfolio) OVER w AS previous_portfolio,
           lag(normalized_benchmark) OVER w AS previous_benchmark, lag(benchmark_level) OVER w AS previous_level
    FROM {build} AS t
    WINDOW w AS (PARTITION BY tenant_id, subject_id, benchmark_id, calculation_version ORDER BY observation_date)),
judged AS (
    SELECT *, CASE
        WHEN n = 1 THEN CASE WHEN portfolio_return IS NOT NULL OR benchmark_return IS NOT NULL
                                  OR normalized_portfolio IS DISTINCT FROM 100
                                  OR normalized_benchmark IS DISTINCT FROM 100 THEN 'no_start' END
        WHEN portfolio_return IS NULL OR benchmark_return IS NULL OR normalized_portfolio IS NULL
             OR normalized_benchmark IS NULL OR previous_portfolio IS NULL OR previous_benchmark IS NULL THEN 'restart'
        WHEN abs(portfolio_return) > {MAXIMUM_PORTFOLIO_RETURN} THEN 'portfolio_jump'
        WHEN abs(benchmark_return) > {MAXIMUM_BENCHMARK_RETURN} THEN 'benchmark_jump'
        WHEN NOT ({_within('normalized_portfolio', 'previous_portfolio * (1 + portfolio_return)')})
             THEN 'portfolio_chain'
        WHEN NOT ({_within('normalized_benchmark', 'previous_benchmark * (1 + benchmark_return)')})
             THEN 'benchmark_chain'
        WHEN previous_level > 0 AND NOT ({_within('benchmark_return', '(benchmark_level / previous_level - 1)')})
             THEN 'benchmark_return'
        WHEN observation_date ~ '{ISO_DATE}' AND previous_date ~ '{ISO_DATE}' AND {gap} THEN 'long_gap'
    END AS problem FROM ordered)
SELECT problem, count(*) AS rows,
       (array_agg(json_build_object('subject_id', subject_id, 'benchmark_id', benchmark_id,
                                    'calculation_version', calculation_version, 'date', observation_date,
                                    'previous_date', previous_date)
                  ORDER BY subject_id, benchmark_id, observation_date))[1:{SAMPLES}] AS samples
FROM judged WHERE problem IS NOT NULL GROUP BY problem""", params)
    series = _scalar(session, f"SELECT count(*) FROM (SELECT DISTINCT tenant_id, subject_id, benchmark_id, "
                              f"calculation_version FROM {build} AS t) s", params)
    # Days the inputs have (a NAV and a benchmark price) inside a series' range that it lacks.
    missing = _rows(session, f"""
WITH series AS (
    SELECT tenant_id, subject_id, benchmark_id, calculation_version, reporting_currency,
           min(observation_date) AS first_date, max(observation_date) AS last_date
    FROM {build} AS t GROUP BY 1, 2, 3, 4, 5),
expected AS (
    SELECT s.*, n.snapshot_date
    FROM series s
    JOIN subject_nav_series n ON n.tenant_id = s.tenant_id AND n.subject_id = s.subject_id
     AND n.currency = s.reporting_currency AND n.snapshot_date > s.first_date AND n.snapshot_date < s.last_date
     AND n.total_text ~ '{NUMBER}'
    WHERE EXISTS (SELECT 1 FROM benchmark_prices p WHERE p.tenant_id = s.tenant_id AND p.benchmark_id = s.benchmark_id
                  AND p.observation_date = n.snapshot_date AND p.quality_status <> 'fail'))
SELECT count(*) AS days,
       (array_agg(json_build_object('subject_id', e.subject_id, 'benchmark_id', e.benchmark_id,
                                    'calculation_version', e.calculation_version, 'date', e.snapshot_date)
                  ORDER BY e.subject_id, e.benchmark_id, e.snapshot_date))[1:{SAMPLES}] AS samples
FROM expected e
WHERE NOT EXISTS (SELECT 1 FROM {build} AS b
                  WHERE b.tenant_id = e.tenant_id AND b.subject_id = e.subject_id AND b.benchmark_id = e.benchmark_id
                    AND b.calculation_version = e.calculation_version AND b.observation_date = e.snapshot_date)""",
                    params)
    # Older rows readers still see inside the range this run rebuilt.
    _, this_run, _ = scope.condition(table, "v")
    left = _rows(session, f"""
WITH series AS (
    SELECT tenant_id, subject_id, benchmark_id, calculation_version,
           min(observation_date) AS first_date, max(observation_date) AS last_date
    FROM {build} AS t GROUP BY 1, 2, 3, 4)
SELECT count(*) AS rows,
       (array_agg(json_build_object('subject_id', v.subject_id, 'benchmark_id', v.benchmark_id,
                                    'calculation_version', v.calculation_version, 'date', v.observation_date)
                  ORDER BY v.subject_id, v.benchmark_id, v.observation_date))[1:{SAMPLES}] AS samples
FROM {table.name} v
JOIN series s ON s.tenant_id = v.tenant_id AND s.subject_id = v.subject_id AND s.benchmark_id = v.benchmark_id
 AND s.calculation_version = v.calculation_version
WHERE v.observation_date BETWEEN s.first_date AND s.last_date AND NOT coalesce({this_run}, false)""", params)
    return {"series": int(series or 0), "problems": _problems(rows), "missing_days": _sample(missing, "days"),
            "left_over_rows": _sample(left, "rows")}


def _coverage(session: Any, table: GoldTable, build: str, params: dict, extra_subjects: list[str]) -> dict[str, Any]:
    if table.name == RISK:
        # As the risk job reads its primary comparison (risk_snapshots.load_risk_inputs).
        return {"snapshots": _rows(session, f"""
SELECT b.subject_id, b.as_of_date, b.payload_json->>'benchmark_id' AS benchmark_id,
       (SELECT max(c.observation_date) FROM performance_comparison_daily c
        WHERE c.tenant_id = b.tenant_id AND c.subject_id = b.subject_id
          AND c.benchmark_id = b.payload_json->>'benchmark_id' AND c.calculation_version = b.comparison_version
          AND c.quality_status IN ('pass', 'warn') AND c.observation_date <= b.as_of_date) AS latest_comparison
FROM {build} AS b
WHERE b.quality_status IN ('pass', 'warn')
ORDER BY 1, 2""", params)}
    # Mappings in force as provisional_benchmark.mapped_benchmarks selects them.
    return {"comparisons": _rows(session, f"""
WITH universe AS (
    SELECT DISTINCT tenant_id, subject_id FROM {build} AS t
    UNION SELECT app_tenant_id(), unnest(%(subjects)s::text[])),
mapped AS (
    SELECT u.tenant_id, u.subject_id, m.benchmark_id, m.mapping_type, m.reporting_currency
    FROM universe u
    JOIN portfolio_benchmark_mapping m ON m.tenant_id = u.tenant_id AND m.subject_id = u.subject_id
    JOIN benchmark_master k ON k.tenant_id = m.tenant_id AND k.benchmark_id = m.benchmark_id
    WHERE k.active AND m.effective_from <= CURRENT_DATE::text
      AND (m.effective_to IS NULL OR m.effective_to >= CURRENT_DATE::text)),
nav AS (
    SELECT n.tenant_id, n.subject_id, n.currency, max(n.snapshot_date) AS latest_nav
    FROM subject_nav_series n
    WHERE (n.tenant_id, n.subject_id) IN (SELECT tenant_id, subject_id FROM universe) AND n.total_text ~ '{NUMBER}'
    GROUP BY 1, 2, 3),
built AS (SELECT tenant_id, subject_id, benchmark_id, max(observation_date) AS latest_comparison
          FROM {build} AS t GROUP BY 1, 2, 3)
SELECT m.subject_id, m.benchmark_id, m.mapping_type, n.latest_nav, b.latest_comparison
FROM mapped m
LEFT JOIN nav n ON n.tenant_id = m.tenant_id AND n.subject_id = m.subject_id AND n.currency = m.reporting_currency
LEFT JOIN built b ON b.tenant_id = m.tenant_id AND b.subject_id = m.subject_id AND b.benchmark_id = m.benchmark_id
ORDER BY 1, 2""", {**params, "subjects": extra_subjects})}


def _expected_subjects(session: Any, job: str | None, environ: Mapping[str, str]) -> list[str]:
    """The subjects benchmark-daily is asked to cover, chosen as it chooses them."""
    if job != "benchmark-daily":
        return []
    configured = str(environ.get("BENCHMARK_ACCOUNT_IDS", "")).strip()
    if configured:
        return sorted({value.strip().upper() for value in configured.split(",") if value.strip()})
    return [str(row["subject_id"]) for row in _rows(session, REGISTERED_SUBJECTS_SQL, {})]


# Only comparisons with a benchmark the subject is mapped to are judged. The website-only
# comparisons with the rest of the catalog (WEBSITE_ALL_BENCHMARKS) are best effort and never
# hold back a version; nothing but the website's picker reads them.
CONTRACT_BENCHMARKS_ONLY = (
    "EXISTS (SELECT 1 FROM portfolio_benchmark_mapping AS contract"
    f" WHERE contract.tenant_id = {COMPARISON}.tenant_id"
    f" AND contract.subject_id = {COMPARISON}.subject_id"
    f" AND contract.benchmark_id = {COMPARISON}.benchmark_id)")


def _measure(session: Any, job: str | None, table: GoldTable, scope: Scope, *, rules: Mapping[str, Any],
             tenant_id: str, environ: Mapping[str, str], stop: Callable[[], bool]) -> dict[str, Any]:
    basis, condition, params = scope.condition(table)
    if table.name == COMPARISON:
        condition = f"({condition}) AND {CONTRACT_BENCHMARKS_ONLY}"
    build = f"(SELECT * FROM {table.name} WHERE {condition})"
    observation: dict[str, Any] = {"basis": basis, "checks": {}}
    observation["rows"] = int(_scalar(session, f"SELECT count(*) FROM {build} AS t", params) or 0)
    contract = rules.get(table.name)
    if contract is None:
        observation["checks"]["contract"] = {"error": "no contract rules"}
    else:
        outcome: list[dict[str, Any]] = []
        for rule in contract["rules"]:
            if stop():
                return observation
            item = {"rule": rule["name"], "severity": rule["severity"], "must_be": rule["mustBe"]}
            try:
                sql = scoped(rule["query"].replace("%", "%%"), table.name, condition)
                outcome.append({**item, "count": int(_scalar(session, sql, params) or 0)})
            except Exception as error:  # noqa: BLE001 - this rule is unavailable
                outcome.append({**item, "error": _error_label(error)})
        observation["checks"]["contract"] = {"version": contract.get("version"), "results": outcome}
    for check in table.checks:
        if stop():
            return observation
        try:
            if check == "nav_matches_ibkr":
                evidence = _nav(session, build, params)
            elif check == "cash_plus_positions":
                evidence = _holdings(session, build, params, tenant_id, stop)
            elif check == "returns_continuity":
                evidence = _continuity(session, table, build, params, scope)
            else:
                evidence = _coverage(session, table, build, params, _expected_subjects(session, job, environ))
        except Exception as error:  # noqa: BLE001 - this check is unavailable
            evidence = {"error": _error_label(error)}
        observation["checks"][check] = evidence
    return observation


def observe(session: Any, job: str, scope: Scope, *, rules: Mapping[str, Any], tenant_id: str,
            environ: Mapping[str, str], results: dict[str, dict[str, Any]],
            stop: Callable[[], bool] = lambda: False) -> dict[str, dict[str, Any]]:
    """Measure each gold table the job writes. A table's observation goes into `results`
    only when it is complete, so a caller that stops waiting reads finished tables only.
    Never raises."""
    for name in JOB_TABLES[job]:
        if stop():
            break
        try:
            observation = _measure(session, job, TABLES[name], scope, rules=rules, tenant_id=tenant_id,
                                   environ=environ, stop=stop)
        except Exception as error:  # noqa: BLE001 - this table is unavailable
            observation = {"error": _error_label(error)}
        results[name] = observation
    return results


def _error_label(error: BaseException) -> str:
    from .layer_freshness import _error_label as label

    return label(error)


# ---------------------------------------------------------------- before a writer commits


class BuildHeldBack(RuntimeError):
    """Enforce mode found the new gold version failed. It was not committed, so readers keep
    the last good version; the job records this subject's failure and carries on."""

    def __init__(self, reason: str, verdict: Mapping[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.verdict = dict(verdict or {})


class _Budgeted:
    """The writer's connection, each statement limited to what is left of the time budget."""

    def __init__(self, raw: Any, deadline: float) -> None:
        self._raw = raw
        self._deadline = deadline

    def expired(self) -> bool:
        return monotonic_time.monotonic() >= self._deadline

    def execute(self, sql: str, params: Any = None) -> Any:
        left = int((self._deadline - monotonic_time.monotonic()) * 1000)
        if left <= 0:
            raise TimeoutError("time budget used up")
        self._raw.execute("SELECT set_config('statement_timeout', %s, true)",
                          (str(max(1, min(left, STATEMENT_TIMEOUT_MS))),))
        return self._raw.execute(sql, params)

    def transaction(self) -> Any:
        return self._raw.transaction()


def examine(connection: Any, builds: Mapping[str, Mapping[str, Any]], *,
            environ: Mapping[str, str] | None = None, now: datetime | None = None,
            time_budget_seconds: float = TIME_BUDGET_SECONDS, rules: Mapping[str, Any] | None = None
            ) -> dict[str, Any]:
    """Measure and decide an uncommitted version on the writer's connection, inside a
    savepoint that is always rolled back, READ ONLY and within the time budget. Never raises;
    the writer's transaction is left as it was."""
    environ = os.environ if environ is None else environ
    now = now or datetime.now(timezone.utc)
    started = monotonic_time.monotonic()
    base: dict[str, Any] = {"checked_at": now.astimezone(timezone.utc).isoformat(),
                            "scope": {name: dict(partition) for name, partition in builds.items()}}
    try:
        unknown = sorted(set(builds) - set(TABLES))
        if unknown:
            raise ValueError(f"not a gold table the gate knows: {', '.join(unknown)}")
        rules = load_rules() if rules is None else rules
        raw = getattr(connection, "_connection", connection)  # past db.PostgresConnection's ? placeholders
        observations: dict[str, dict[str, Any]] = {}
        reason = "not measured"
        if getattr(raw, "info", None) is None or not hasattr(raw, "transaction"):
            reason = "not a PostgreSQL connection"
        else:
            import psycopg

            if any("cash_plus_positions" in TABLES[name].checks for name in builds):
                from . import export_agent_source  # noqa: F401 - imported before the clock starts
            session = _Budgeted(raw, started + time_budget_seconds)
            try:
                with raw.transaction():
                    raw.execute("SELECT set_config('lock_timeout', %s, true)", (str(LOCK_TIMEOUT_MS),))
                    raw.execute("SET LOCAL transaction_read_only = on")
                    tenant_id = str(_scalar(session, "SELECT current_setting('app.tenant_id', true)", {}) or
                                    environ.get("PORTFOLIO_TENANT_ID") or DEFAULT_TENANT_ID)
                    for name, partition in builds.items():
                        if session.expired():
                            break
                        scope = Scope(partition=tuple(partition.items()))
                        try:
                            observations[name] = _measure(session, None, TABLES[name], scope, rules=rules,
                                                          tenant_id=tenant_id, environ=environ,
                                                          stop=session.expired)
                        except Exception as error:  # noqa: BLE001 - this table is unavailable
                            observations[name] = {"error": _error_label(error)}
                    raise psycopg.Rollback()  # undo the settings; the writer's rows stay as they are
            except Exception as error:  # noqa: BLE001 - reported below
                reason = _error_label(error)
            if session.expired():
                reason = f"TimeoutError (time budget of {time_budget_seconds:g} seconds)"
        for name in builds:
            observations.setdefault(name, {"error": reason})
        base.update(decide_tables(list(builds), observations))
    except Exception as error:  # noqa: BLE001 - a broken check is a reason, never a crash
        base.update(verdict=_worst([UNAVAILABLE]), reasons=[f"check failed: {type(error).__name__}: {error}"[:300]])
    base["elapsed_ms"] = int((monotonic_time.monotonic() - started) * 1000)
    return base


def before_commit(connection: Any, builds: Mapping[str, Mapping[str, Any]], *, writer: str,
                  rollback: bool = True, environ: Mapping[str, str] | None = None,
                  out: Callable[[str], None] | None = None, **options: Any) -> dict[str, Any] | None:
    """Call right before a writer commits a new gold version. `builds` maps each gold table
    written to the column values that pick the new version's rows.

    Off: returns None, touching nothing. Shadow: logs one line and returns the verdict.
    Enforce, when a version that can be held back fails (GoldTable.holds_back): logs, rolls
    the writer's transaction back (or, with rollback=False, leaves that to the caller's
    savepoint) and raises BuildHeldBack. Otherwise the same as shadow."""
    environ = os.environ if environ is None else environ
    selected, note = mode(environ)
    if selected == OFF:
        return None
    try:
        verdict = examine(connection, builds, environ=environ, **options)
    except Exception as error:  # noqa: BLE001 - never stops a writer by itself
        verdict = {"verdict": _worst([UNAVAILABLE]), "reasons": [f"check failed: {type(error).__name__}"]}
    verdict["writer"] = writer
    job = _job_name()
    if job:
        verdict["job"] = job
    failed = [name for name, table in (verdict.get("tables") or {}).items() if table.get("verdict") == FAIL]
    hold = selected == ENFORCE and any(TABLES[name].holds_back for name in failed)
    if selected == ENFORCE and not hold and failed:
        verdict["not_held_back"] = "its readers show one exact date and have no earlier version to fall back to"
    verdict["decision"] = "hold_back" if hold else "publish"
    _log(verdict, selected, note, out)
    if hold:
        if rollback:
            try:
                connection.rollback()
            except Exception:  # noqa: BLE001 - raising still keeps the writer from committing
                pass
        raise BuildHeldBack("publish gate held back the new version: " +
                            "; ".join(verdict.get("reasons") or ["failed"])[:1000], verdict)
    return verdict


def _job_name() -> str | None:
    from . import lineage

    name = lineage.current().job_name
    return None if name == "none" else name


def _log(verdict: dict[str, Any], selected: str, note: str | None, out: Callable[[str], None] | None) -> None:
    verdict["mode"] = selected
    if note:
        verdict["mode_note"] = note
    line = LOG_PREFIX + json.dumps(without_figures(verdict), sort_keys=True, separators=(",", ":"), default=str)
    try:
        (out or (lambda text: print(text, flush=True)))(line)
    except Exception:  # noqa: BLE001 - logging must not fail the job
        pass


# ---------------------------------------------------------------- after a job (month-end, dry runs)


def check(job: str, *, since: datetime | None = None, run_id: str | None = None,
          environ: Mapping[str, str] | None = None, now: datetime | None = None,
          time_budget_seconds: float = TIME_BUDGET_SECONDS, connect: Callable[[str, str], Any] | None = None,
          rules: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Judge everything a job wrote since a time (or under a lineage run id) in its own READ
    ONLY session, within a hard time limit, connection included. Never raises."""
    environ = os.environ if environ is None else environ
    now = now or datetime.now(timezone.utc)
    started = monotonic_time.monotonic()
    scope = Scope(since, run_id)
    base: dict[str, Any] = {"job": job, "checked_at": now.astimezone(timezone.utc).isoformat(),
                            "scope": scope.describe()}
    try:
        if job not in JOB_TABLES:
            raise ValueError(f"Unknown job: {job}")
        rules = load_rules() if rules is None else rules
        database_url = str(environ.get(DATABASE_URL_ENV) or environ.get("DATABASE_URL") or "").strip()
        tenant_id = str(environ.get("PORTFOLIO_TENANT_ID") or DEFAULT_TENANT_ID).strip()
        results: dict[str, dict[str, Any]] = {}
        if not database_url:
            observations = {name: {"error": "database not configured"} for name in JOB_TABLES[job]}
        else:
            if any("cash_plus_positions" in TABLES[name].checks for name in JOB_TABLES[job]):
                from . import export_agent_source  # noqa: F401 - imported here, not inside the timed thread
            if connect is None:
                from .layer_freshness import open_read_only as connect
            cancelled = threading.Event()
            done = threading.Event()
            failure: list[str] = []

            def work() -> None:
                session = None
                try:
                    session = connect(database_url, tenant_id)
                    observe(session, job, scope, rules=rules, tenant_id=tenant_id, environ=environ,
                            results=results, stop=cancelled.is_set)
                except BaseException as error:  # noqa: BLE001 - reported below
                    failure.append(_error_label(error))
                finally:
                    if session is not None:
                        try:
                            session.rollback()
                            session.close()
                        except Exception:  # noqa: BLE001
                            pass
                    done.set()

            threading.Thread(target=work, name="publish-gate", daemon=True).start()
            finished = done.wait(time_budget_seconds)
            if not finished:
                cancelled.set()
            reason = (failure[0] if failure else None) if finished else \
                f"TimeoutError (time budget of {time_budget_seconds:g} seconds)"
            observations = dict(results)  # finished tables only; the thread may still be running
            for name in JOB_TABLES[job]:
                if name not in observations:
                    observations[name] = {"error": reason or "not measured"}
        base.update(decide(job, observations))
    except Exception as error:  # noqa: BLE001 - a broken check is a reason, never a crash
        base.update(verdict=_worst([UNAVAILABLE]), reasons=[f"check failed: {type(error).__name__}: {error}"[:300]])
    base["elapsed_ms"] = int((monotonic_time.monotonic() - started) * 1000)
    return base


def report(job: str, *, since: datetime | None = None, run_id: str | None = None,
           environ: Mapping[str, str] | None = None, out: Callable[[str], None] | None = None,
           **options: Any) -> dict[str, Any] | None:
    """Judge what a job wrote once it is committed, and log one line. Never stops anything,
    in any mode: the month-end packages are written by subprocesses and must always finish.
    Off: returns None and does nothing else."""
    environ = os.environ if environ is None else environ
    selected, note = mode(environ)
    if selected == OFF:
        return None
    try:
        verdict = check(job, since=since, run_id=run_id, environ=environ, **options)
    except Exception as error:  # noqa: BLE001 - never fails the job
        verdict = {"job": job, "verdict": _worst([UNAVAILABLE]), "reasons": [f"check failed: {type(error).__name__}"]}
    verdict["decision"] = "report_only"
    _log(verdict, selected, note, out)
    return verdict


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m ibkr_etl.publish_gate",
                                     description="Dry run of the gold publish gate (read-only).")
    parser.add_argument("job", nargs="?", choices=sorted(JOB_TABLES))
    parser.add_argument("--since", help="judge rows written at or after this UTC time (default: today 00:00 UTC)")
    parser.add_argument("--run-id", help="judge the rows stamped with this lineage run id")
    parser.add_argument("--write-rules", metavar="CONTRACTS_DIR", nargs="?", const="infra/medallion/contracts",
                        help=f"regenerate {RULES_PATH.name} from the contracts (a checkout only)")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.write_rules:
        RULES_PATH.write_text(rules_text(Path(args.write_rules)), encoding="utf-8")
        print(f"wrote {RULES_PATH}")
        return 0
    if not args.job:
        parser.error("name a job")
    now = datetime.now(timezone.utc)
    since = datetime.fromisoformat(args.since) if args.since else datetime.combine(now.date(), time(0), timezone.utc)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    print(json.dumps(without_figures(check(args.job, since=since, run_id=args.run_id, now=now)), indent=1,
                     sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
