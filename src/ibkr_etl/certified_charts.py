"""Charts, tables and indicators on certified figures (medallion upgrade U5, after month-end item 4).

docs/MEDALLION_CERTIFIED_FIGURES.md ("The charts") explains it. The register's `charts` page has
eight figures:

- series, stored one row per date (the as-of date's point, which is the same whatever later date
  the chart is drawn to), so a chart through a date is the rows up to it:
  series.portfolio_daily_return and series.benchmark_daily_return (the Vs benchmark chart),
  series.rolling_volatility_63d, series.rolling_beta_126d and series.rolling_correlation_126d
  (Risk › Rolling risk);
- tables, stored for each as-of date: table.drawdown_episodes (item: the episode's peak date),
  matrix.holdings_correlation (item: "conid|conid", the lower first) and indicator.rsi_14
  (item: the holding's conid).

The charts also draw two figures the build already stores for every date for other pages: the
rebased levels (benchmark.rebased_level, Performance) and the trailing 12-month return
(stats.trailing_12m_return, Risk › Statistics). They are only compared until their own page is on.

Every value is read from the payload of the website handler that draws it today (the build's
readers below, and the same extraction for the website's comparison), so certified equals what
the page shows.

Two settings, both off by default:

- CERTIFIED_FIGURES_CHARTS, for the build job: off | fallback | on. off: the build reads no
  chart, exactly as before. fallback: it reads them too, and a chart row that breaks a rule,
  changes with the same inputs, or reads differently the second time is left out and counted;
  a chart never holds the build. on: chart rows are checked like every other figure, so a
  problem holds the build; not before two clean month-ends.
- the page key ``charts`` in CERTIFIED_FIGURES_PAGES, for the website: off | shadow | on.
  shadow: today's charts, and one ``CERTIFIED_FIGURES`` line that counts the differences.
  on is the automatic fallback: today's and the certified points are both read, and a
  figure's certified points are shown only when every one equals today's (within the
  certified comparisons' tolerances); otherwise today's, with one log line. The log lines
  hold counts and figure ids only: no values, and the subject masked as u…01.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Mapping

from . import certified_build as build
from . import certified_figures as cf
from . import certified_pages

SETTING = "CERTIFIED_FIGURES_CHARTS"
MODES = ("off", "fallback", "on")
PAGE = "charts"

DAILY_PORTFOLIO = "series.portfolio_daily_return"
DAILY_BENCHMARK = "series.benchmark_daily_return"
ROLLING_VOLATILITY = "series.rolling_volatility_63d"
ROLLING_BETA = "series.rolling_beta_126d"
ROLLING_CORRELATION = "series.rolling_correlation_126d"
EPISODES = "table.drawdown_episodes"
HOLDINGS_CORRELATION = "matrix.holdings_correlation"
RSI = "indicator.rsi_14"
# Other pages' figures the charts draw, and the page that owns each.
REBASED = "benchmark.rebased_level"
TRAILING = "stats.trailing_12m_return"
BORROWED = {REBASED: "performance", TRAILING: "risk-statistics"}

SERIES = frozenset({DAILY_PORTFOLIO, DAILY_BENCHMARK, ROLLING_VOLATILITY, ROLLING_BETA, ROLLING_CORRELATION})
TABLES = frozenset({EPISODES, HOLDINGS_CORRELATION, RSI})
FIGURES = SERIES | TABLES
# Figures read as one point per date: certified rows on dates the chart has no point for are not extra.
ONE_POINT_A_DATE = SERIES | frozenset(BORROWED)
# Figures whose other columns are shown too (dates, day counts, observations, signal): compared exactly.
DETAILED = TABLES

# As the website reads them (web.holdings_correlation_view and portfolio_analytics.RSI_LOOKBACK_DAYS).
CORRELATION_LOOKBACK_DAYS = 550
RSI_LOOKBACK_DAYS = 300
ROLLING_BENCHMARK_OBSERVATIONS = 126

# The website's read: one query on its own connection, cut short after this long.
READ_TIMEOUT_MS = 1000
LOGGER = certified_pages.LOGGER

Key = tuple[str, str]           # (date, item)
Value = tuple[Any, Any]         # (value, detail)


def build_mode(environ: Mapping[str, str] | None = None) -> str:
    """The build's charts mode from CERTIFIED_FIGURES_CHARTS; anything unknown is off."""
    text = str((os.environ if environ is None else environ).get(SETTING, "")).strip().lower()
    return text if text in MODES else "off"


# --- What a page's payload draws ------------------------------------------------------------


@dataclass
class Points:
    """The chart figures one payload shows: {figure: {(date, item): (value, detail)}}."""

    effective: str | None = None
    benchmark_id: str = ""
    figures: dict[str, dict[Key, Value]] = field(default_factory=dict)

    def first(self, figure_ids: Iterable[str]) -> str | None:
        days = [key[0] for figure_id in figure_ids for key in self.figures.get(figure_id, {}) if key[0]]
        return min(days, default=self.effective)


def _day(value: Any) -> str | None:
    return None if value in (None, "") else str(value)[:10]


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _change(current: Any, previous: Any) -> float | None:
    current, previous = _number(current), _number(previous)
    if current is None or not previous:
        return None
    return current / previous - 1.0


def episode_detail(episode: Mapping, rank: int) -> dict:
    """What the Deepest drawdowns table shows of an episode besides its depth."""
    return {"rank": rank, "trough_date": _day(episode.get("trough_date")),
            "recovery_date": _day(episode.get("recovery_date")), "recovered": bool(episode.get("recovered")),
            "days_to_trough": episode.get("days_to_trough"), "days_to_recover": episode.get("days_to_recover"),
            "length_days": episode.get("length_days")}


def rsi_detail(row: Mapping) -> dict:
    return {"signal": row.get("rsi_signal"), "observations": row.get("rsi_observations"),
            "reason": row.get("rsi_reason"), "restarted": bool(row.get("rsi_series_restarted"))}


def analytics_points(payload: Mapping) -> Points:
    """Risk › Drawdowns and Rolling risk (/analytics): rolling volatility and the trailing return
    on every NAV date, and the deepest episodes on the as-of date."""
    if not isinstance(payload, Mapping) or not payload.get("available"):
        return Points()
    effective = _day(payload.get("effective_as_of"))
    found: dict[str, dict[Key, Value]] = {ROLLING_VOLATILITY: {}, TRAILING: {}, EPISODES: {}}
    for point in payload.get("rolling") or []:
        day = _day(point.get("date"))
        found[ROLLING_VOLATILITY][(day, "")] = (_number(point.get("volatility")), None)
        found[TRAILING][(day, "")] = (_number(point.get("trailing_return")), None)
    for rank, episode in enumerate((payload.get("drawdown") or {}).get("episodes") or [], 1):
        found[EPISODES][(effective, _day(episode.get("peak_date")))] = (_number(episode.get("depth")),
                                                                        episode_detail(episode, rank))
    return Points(effective, "", found)


def benchmark_points(payload: Mapping) -> Points:
    """The Vs benchmark chart and the rolling beta chart (/benchmark-comparison). The chart draws
    the rebased levels; each day's return is the change in its level from the date before."""
    if not isinstance(payload, Mapping) or not payload.get("available") or not payload.get("series"):
        return Points()
    benchmark = payload.get("benchmark")
    benchmark_id = str(benchmark.get("benchmark_id") or "") if isinstance(benchmark, Mapping) else ""
    found: dict[str, dict[Key, Value]] = {DAILY_PORTFOLIO: {}, DAILY_BENCHMARK: {}, REBASED: {},
                                          ROLLING_BETA: {}, ROLLING_CORRELATION: {}}
    previous = None
    for point in payload["series"]:
        day = _day(point.get("date"))
        found[REBASED][(day, "portfolio")] = (_number(point.get("portfolio")), None)
        found[REBASED][(day, "benchmark")] = (_number(point.get("benchmark")), None)
        if previous is not None:
            found[DAILY_PORTFOLIO][(day, "")] = (_change(point.get("portfolio"), previous.get("portfolio")), None)
            found[DAILY_BENCHMARK][(day, "")] = (_change(point.get("benchmark"), previous.get("benchmark")), None)
        previous = point
    for point in payload.get("rolling") or []:
        day = _day(point.get("date"))
        found[ROLLING_BETA][(day, "")] = (_number(point.get("beta")), None)
        found[ROLLING_CORRELATION][(day, "")] = (_number(point.get("correlation")), None)
    return Points(_day(payload["series"][-1].get("date")), benchmark_id, found)


def holdings_points(payload: Mapping) -> Points:
    """RSI (14) on the Holdings page, per holding on the holdings date. Composites have none."""
    if not isinstance(payload, Mapping) or "valuation_method" not in payload or not payload.get("snapshot_date"):
        return Points()
    effective = _day(payload.get("snapshot_date"))
    found = {(effective, str(row.get("conid"))): (_number(row.get("rsi")), rsi_detail(row))
             for row in payload.get("holdings") or [] if isinstance(row, Mapping) and "rsi" in row}
    return Points(effective, "", {RSI: found} if found else {})


def correlation_points(payload: Mapping) -> Points:
    """Risk › Correlation (/holdings-correlation): each pair of the largest holdings once."""
    if not isinstance(payload, Mapping) or not payload.get("available"):
        return Points()
    effective = _day(payload.get("snapshot_date"))
    conids = [str(holding.get("conid")) for holding in payload.get("holdings") or []]
    matrix = payload.get("matrix") or []
    found = {}
    for i, left in enumerate(conids):
        for j, right in enumerate(conids):
            if left < right:
                cell = matrix[i][j]
                found[(effective, f"{left}|{right}")] = (_number(cell.get("correlation")),
                                                         {"observations": cell.get("observations")})
    return Points(effective, "", {HOLDINGS_CORRELATION: found} if found else {})


# The website endpoints that draw chart figures: how to read them and which figures they show.
ENDPOINTS: dict[str, tuple[Callable[[Mapping], Points], tuple[str, ...]]] = {
    "analytics": (analytics_points, (ROLLING_VOLATILITY, EPISODES, TRAILING)),
    "benchmark-comparison": (benchmark_points, (DAILY_PORTFOLIO, DAILY_BENCHMARK, ROLLING_BETA,
                                                ROLLING_CORRELATION, REBASED)),
    "holdings": (holdings_points, (RSI,)),
    "holdings-correlation": (correlation_points, (HOLDINGS_CORRELATION,)),
}


# --- The build's readers (certified_build.page_rows calls them for the page "charts") ----------


VOLATILITY_REASON = "Fewer than 63 daily returns on this date."
ROLLING_REASON = "Fewer than 126 common-date returns with the benchmark on this date."
FIRST_RETURN_REASON = "The first common date has no return before it."
RSI_REASONS = {"no_current_mark": "No mark price on the holdings date.",
               "insufficient_history": "Fewer than 15 usable daily marks."}
CORRELATION_REASON = "Fewer than 40 shared daily returns, or a price that did not move."

RECENT_COMPARISON_SQL = """
SELECT observation_date, portfolio_value, benchmark_level, portfolio_return, benchmark_return
FROM performance_comparison_daily
WHERE tenant_id = %s AND subject_id = %s AND benchmark_id = %s AND calculation_version = %s
  AND reporting_currency = %s AND quality_status IN ('pass', 'warn') AND observation_date <= %s
ORDER BY observation_date DESC
LIMIT %s
"""

# The marks a holding's RSI or a correlation pair is worked out from, fingerprinted where they are.
MARKS_SHA_SQL = """
SELECT encode(sha256(convert_to(COALESCE(string_agg(conid::text || '|' || snapshot_date::text || '|'
           || COALESCE(mark_price_text, ''), ';' ORDER BY conid::text, snapshot_date::text), ''), 'UTF8')), 'hex')
       AS digest, count(*) AS marks
FROM subject_position_series
WHERE tenant_id = %s AND subject_id = %s AND conid::text = ANY(%s) AND snapshot_date BETWEEN %s AND %s
"""


def _marks_sha(pages, conids: list[str], start: str, end: str) -> str:
    with pages.web._database() as connection:
        found = connection.execute(MARKS_SHA_SQL, (pages.web.TENANT_ID, pages.subject.id, conids, start, end)).fetchone()
    return build.sha256({"conids": conids, "from": start, "to": end, "marks": found["digest"],
                         "count": found["marks"]})


def correlation_payload(pages) -> dict:
    """The Correlation page's answer, called once for the subject and date."""
    return pages._get("holdings_correlation", lambda: build._call(pages.web.holdings_correlation_view,
                                                                  pages.subject.id, as_of=pages.as_of))


def analytics_rows(pages) -> list:
    subject, as_of = pages.subject, pages.as_of
    payload = pages.analytics()
    found = analytics_points(payload)
    if not found.figures or found.effective != as_of:
        return []
    common = {"inputs": pages.nav_inputs(), "source_table": "silver.subject_nav_series",
              "source_key": build._nav_key(subject, as_of), "source_version": payload.get("calculation_version")}
    volatility, _ = found.figures[ROLLING_VOLATILITY].get((as_of, ""), (None, None))
    rows = [build._row(subject, ROLLING_VOLATILITY, as_of, volatility, reason=VOLATILITY_REASON, **common)]
    for (_, peak), (depth, detail) in found.figures[EPISODES].items():
        rows.append(build._row(subject, EPISODES, as_of, depth, item=peak, detail=detail, **common))
    return rows


def benchmark_rows(pages) -> list:
    subject, as_of = pages.subject, pages.as_of
    payload = pages.comparison()
    found = benchmark_points(payload)
    if not found.figures or found.effective != as_of:
        return []  # a day the benchmark's market was shut has no point on the chart
    version = str(payload.get("calculation_version"))
    with pages.web._database() as connection:
        recent = connection.execute(RECENT_COMPARISON_SQL, (
            pages.web.TENANT_ID, subject.id, found.benchmark_id, version, payload.get("reporting_currency"), as_of,
            ROLLING_BENCHMARK_OBSERVATIONS + 1)).fetchall()
    if not recent or _day(recent[0]["observation_date"]) != as_of:
        return []
    common = {"source_table": "gold.performance_comparison_daily", "source_version": version,
              "benchmark_id": found.benchmark_id,
              "source_key": {"subject_id": subject.id, "benchmark_id": found.benchmark_id, "observation_date": as_of,
                             "calculation_version": version}}
    levels = build.sha256([version, [[_day(row["observation_date"]), str(row["portfolio_value"]),
                                      str(row["benchmark_level"])] for row in recent[:2]]])
    returns = build.sha256([version, [[_day(row["observation_date"]), str(row["portfolio_return"]),
                                       str(row["benchmark_return"])] for row in recent
                                      if row["portfolio_return"] is not None and row["benchmark_return"] is not None]
                            [:ROLLING_BENCHMARK_OBSERVATIONS]])
    basis = {"return_basis": payload.get("benchmark_return_convention")}
    beta, _ = found.figures[ROLLING_BETA].get((as_of, ""), (None, None))
    correlation, _ = found.figures[ROLLING_CORRELATION].get((as_of, ""), (None, None))
    return [
        build._row(subject, DAILY_PORTFOLIO, as_of, recent[0]["portfolio_return"], inputs=levels,
                   reason=FIRST_RETURN_REASON, **common),
        build._row(subject, DAILY_BENCHMARK, as_of, recent[0]["benchmark_return"], inputs=levels,
                   reason=FIRST_RETURN_REASON, detail=basis, **common),
        build._row(subject, ROLLING_BETA, as_of, beta, inputs=returns, reason=ROLLING_REASON, **common),
        build._row(subject, ROLLING_CORRELATION, as_of, correlation, inputs=returns, reason=ROLLING_REASON,
                   **common),
    ]


def rsi_rows(pages) -> list:
    subject, as_of = pages.subject, pages.as_of
    found = holdings_points(pages.holdings())
    if not found.figures or found.effective != as_of:
        return []
    points = found.figures[RSI]
    start = (date.fromisoformat(as_of) - timedelta(days=RSI_LOOKBACK_DAYS)).isoformat()
    inputs = _marks_sha(pages, sorted(item for _, item in points), start, as_of)
    key = {"subject_id": subject.id, "snapshot_date": as_of}
    return [build._row(subject, RSI, as_of, value, item=item, detail=detail, observations=detail["observations"],
                       reason=RSI_REASONS.get(detail["reason"]) or "No RSI on this date.", inputs=inputs,
                       source_table="silver.subject_position_series", source_key={**key, "conid": item})
            for (_, item), (value, detail) in sorted(points.items())]


def correlation_rows(pages) -> list:
    subject, as_of = pages.subject, pages.as_of
    if subject.kind == "composite_portfolio":
        return []  # no security-level prices; the page says so
    payload = correlation_payload(pages)
    found = correlation_points(payload)
    if not found.figures or found.effective != as_of:
        return []
    conids = [str(holding["conid"]) for holding in payload["holdings"]]
    start = (date.fromisoformat(as_of) - timedelta(days=CORRELATION_LOOKBACK_DAYS)).isoformat()
    inputs = _marks_sha(pages, conids, start, as_of)
    key = {"subject_id": subject.id, "snapshot_date": as_of}
    return [build._row(subject, HOLDINGS_CORRELATION, as_of, value, item=item, detail=detail,
                       observations=detail["observations"], reason=CORRELATION_REASON, inputs=inputs,
                       source_table="silver.subject_position_series", source_key={**key, "pair": item})
            for (_, item), (value, detail) in sorted(found.figures[HOLDINGS_CORRELATION].items())]


READERS: dict[str, tuple[Callable, ...]] = {PAGE: (analytics_rows, benchmark_rows, rsi_rows, correlation_rows)}


# --- The build's mode -------------------------------------------------------------------------


def run_pages(named: Iterable[str] | None, readers: Mapping[str, Any],
              environ: Mapping[str, str] | None = None) -> tuple[list[str], str | None]:
    """The pages a run reads, and the charts' mode when it reads them (None otherwise).

    The charts are read when CERTIFIED_FIGURES_CHARTS is fallback or on, or when --page charts
    names them (then fallback if the setting is off). With the setting off and no --page charts,
    the list is exactly the one the build read before."""
    mode = build_mode(environ)
    named = list(named or [])
    if PAGE in named and mode == "off":
        mode = "fallback"
    pages = [page for page in (named or cf.pages()) if page in readers or (page == PAGE and mode != "off")]
    return pages, (mode if PAGE in pages else None)


def settle(mode: str, rows: list, result: dict, disagreements: dict, notes: dict, errors: list) -> dict:
    """After the build's compare(): what its charts mode does with chart problems, and the counts.

    fallback: a chart row that breaks a contract rule, changed with the same inputs and version,
    or read differently the second time (that figure, for that subject, in this run) is left
    out, so readers keep the last published point and the website shows today's; reader errors
    are counted. Nothing about the charts holds the build. on: the build's own checks decide
    (reader errors become read errors)."""
    reader_errors: dict = notes.pop("chart_errors", {})
    counts = {"mode": mode, "rows_read": sum(row.figure_id in FIGURES for row in rows),
              "reader_errors": sum(sum(kinds.values()) for kinds in reader_errors.values())}
    if mode == "on":
        for reader, kinds in sorted(reader_errors.items()):
            for kind, count in sorted(kinds.items()):
                errors.append(f"charts {reader}: {kind} x{count}")
        counts["written"] = sum(row.figure_id in FIGURES for row in result["new"] + result["changed"])
        return counts
    disagreed: dict[str, set[str]] = {}
    for who in list(disagreements):
        chart_ids = {figure_id for figure_id in disagreements[who] if figure_id in FIGURES}
        if chart_ids:
            disagreed[who] = chart_ids
            disagreements[who] = [figure_id for figure_id in disagreements[who] if figure_id not in chart_ids]
            if not disagreements[who]:
                del disagreements[who]
    violations = {id(row) for row in result["violations"] if row.figure_id in FIGURES}
    left_out, figures = Counter(), set()

    def keep(row) -> bool:
        if row.figure_id not in FIGURES:
            return True
        why = ("rule_failures" if build.rule_failures([row]) else
               "same_inputs_new_answer" if id(row) in violations else
               "page_disagreements" if row.figure_id in disagreed.get(build.mask(row.subject_id), ()) else None)
        if why:
            left_out[why] += 1
            figures.add(row.figure_id)
        return why is None

    result["new"] = [row for row in result["new"] if keep(row)]
    result["changed"] = [row for row in result["changed"] if keep(row)]
    result["violations"] = [row for row in result["violations"] if row.figure_id not in FIGURES]
    counts.update(written=sum(row.figure_id in FIGURES for row in result["new"] + result["changed"]),
                  left_out=dict(sorted(left_out.items())), left_out_figures=sorted(figures),
                  disagreeing_subjects=len(disagreed))
    return counts


def summary_line(counts: Mapping) -> str:
    left = counts.get("left_out") or {}
    return (f"Charts ({counts['mode']}): {counts['rows_read']} rows read, {counts.get('written', 0)} to write"
            + (f", {sum(left.values())} left out ({', '.join(f'{k} {v}' for k, v in left.items())})" if left else "")
            + f", {counts['reader_errors']} reader errors.")


# --- The website: today's charts, compared with or shown from the certified figures -------------


# One subject's rows of these figures between the chart's first and as-of dates: the table is
# indexed by (tenant, subject, as-of date) and by its key (tenant, subject, figure, ...).
READ_SQL = f"""
SELECT figure_id, item, benchmark_id, as_of_date, value, unit, status, detail, build_id, published_at
FROM {certified_pages.VIEW}
WHERE tenant_id = %(tenant)s AND subject_id = %(subject)s AND figure_id = ANY(%(figures)s)
  AND benchmark_id = ANY(%(benchmarks)s) AND as_of_date BETWEEN %(first)s::date AND %(effective)s::date
  AND (figure_id = ANY(%(series)s) OR as_of_date = %(effective)s::date)
"""


def read_rows(connection, *, tenant_id: str, subject_id: str, figure_ids: Iterable[str], first: str,
              effective: str, benchmark_id: str) -> list[dict]:
    """The certified rows a chart answer is compared with: series points from its first date to its
    as-of date, tables on its as-of date."""
    figure_ids = list(figure_ids)
    return connection.execute(READ_SQL, {
        "tenant": tenant_id, "subject": subject_id, "first": first, "effective": effective,
        "benchmarks": ["", benchmark_id] if benchmark_id else [""], "figures": figure_ids,
        "series": [figure_id for figure_id in figure_ids if figure_id in ONE_POINT_A_DATE],
    }).fetchall()


@contextmanager
def _connect(tenant_id: str):
    """A read-only connection of its own, with a short statement timeout (as certified_pages)."""
    import psycopg
    from psycopg.rows import dict_row

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("The website database connection is not configured")
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=certified_pages.CONNECT_TIMEOUT_S,
                         options=f"-c statement_timeout={READ_TIMEOUT_MS} -c lock_timeout={READ_TIMEOUT_MS}") as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        yield connection


def certified_points(rows: Iterable[Mapping], benchmark_id: str) -> tuple[dict[str, dict[Key, Value]], dict]:
    """{figure: {(date, item): (value, detail)}} from certified rows, and the newest build among them."""
    found: dict[str, dict[Key, Value]] = {}
    newest: dict = {}
    for row in rows:
        figure = cf.figure(row["figure_id"])
        if (row["benchmark_id"] or "") != (benchmark_id if figure.per_benchmark else ""):
            continue
        value = None if row["status"] != "ok" or row["value"] is None else float(row["value"])
        found.setdefault(figure.id, {})[(_day(row["as_of_date"]), row["item"] or "")] = (value, row.get("detail"))
        published = row.get("published_at")
        if published is not None and (not newest or published > newest["published_at"]):
            newest = {"build_id": str(row["build_id"]), "published_at": published}
    return found, newest


def _canonical(detail: Any) -> str:
    return json.dumps(detail or {}, sort_keys=True, default=str, separators=(",", ":"))


def compare_figure(figure_id: str, today: Mapping[Key, Value], certified: Mapping[Key, Value]) -> dict:
    """Counts of today's points that differ from or have no certified point, and certified table
    rows the page does not show."""
    unit = cf.figure(figure_id).unit
    differ = missing = 0
    for key, (value, detail) in today.items():
        if key not in certified:
            missing += 1
            continue
        certified_value, certified_detail = certified[key]
        if not certified_pages._same(value, certified_value, unit) or (
                figure_id in DETAILED and _canonical(detail) != _canonical(certified_detail)):
            differ += 1
    extra = 0 if figure_id in ONE_POINT_A_DATE else sum(key not in today for key in certified)
    return {"compared": len(today), "differ": differ, "missing": missing, "extra": extra}


def agrees(counts: Mapping) -> bool:
    return bool(counts["compared"]) and not (counts["differ"] or counts["missing"] or counts["extra"])


@dataclass
class Check:
    """One answer's chart figures against the certified ones."""

    today: Points
    figure_ids: list[str]
    results: dict[str, dict]
    certified: dict[str, dict[Key, Value]]
    newest: dict
    read_ms: float
    rows: int = 0


def check(endpoint: str, payload: Mapping, connect: Callable, *, tenant_id: str, subject_id: str,
          figure_ids: Iterable[str] | None = None) -> Check | None:
    """Today's chart figures on this answer against the certified ones; None when it shows none."""
    extract, shown = ENDPOINTS[endpoint]
    today = extract(payload)
    wanted = [figure_id for figure_id in (shown if figure_ids is None else figure_ids)
              if figure_id in shown and today.figures.get(figure_id)]
    if not wanted or not today.effective:
        return None
    started = time.perf_counter()
    with connect() as connection:
        rows = read_rows(connection, tenant_id=tenant_id, subject_id=subject_id, figure_ids=wanted,
                         first=today.first(wanted), effective=today.effective, benchmark_id=today.benchmark_id)
    read_ms = round((time.perf_counter() - started) * 1000, 1)
    certified, newest = certified_points(rows, today.benchmark_id)
    results = {figure_id: compare_figure(figure_id, today.figures[figure_id], certified.get(figure_id, {}))
               for figure_id in wanted}
    return Check(today, wanted, results, certified, newest, read_ms, len(rows))


def _log(endpoint: str, mode: str, status: str, subject_id: str, **details: Any) -> None:
    certified_pages._log(endpoint, PAGE, mode, status, subject=build.mask(subject_id), **details)


def _counts(results: Mapping[str, Mapping], figure_ids: Iterable[str]) -> dict:
    figure_ids = list(figure_ids)
    return {"compared": sum(results[f]["compared"] for f in figure_ids),
            **{kind: {f: results[f][kind] for f in figure_ids if results[f][kind]}
               for kind in ("differ", "missing", "extra")}}


def serve(endpoint: str, payload: Any, *, tenant_id: str, subject_id: str,
          environ: Mapping[str, str] | None = None) -> Any:
    """The answer with its charts as today, compared with (shadow) or shown from (on) the
    certified figures. With CERTIFIED_FIGURES_PAGES unset or charts off, ``payload`` itself,
    untouched, with no query and no log line. Call only after the page's access check."""
    if endpoint not in ENDPOINTS or not isinstance(payload, dict):
        return payload
    environ = os.environ if environ is None else environ
    if not str(environ.get(cf.PAGES_SETTING, "")).strip():
        return payload
    modes = certified_pages.page_modes(environ)
    mode = modes.get(PAGE, "off")
    if mode == "off":
        return payload
    try:
        return _serve(endpoint, payload, mode, modes, tenant_id=tenant_id, subject_id=subject_id)
    except Exception as error:  # the certified layer must never take a chart down
        _log(endpoint, mode, "error", subject_id, error=type(error).__name__)
        return payload


def _serve(endpoint: str, payload: dict, mode: str, modes: Mapping[str, str], *, tenant_id: str,
           subject_id: str) -> dict:
    _, shown = ENDPOINTS[endpoint]
    # A borrowed series is shown from the certified figures only once its own page is on.
    wanted = [figure_id for figure_id in shown
              if mode == "shadow" or figure_id not in BORROWED or modes.get(BORROWED[figure_id]) == "on"]
    found = check(endpoint, payload, lambda: _connect(tenant_id), tenant_id=tenant_id, subject_id=subject_id,
                  figure_ids=wanted)
    if found is None:
        return payload
    results = found.results
    if mode == "shadow":
        differs = not all(agrees(results[f]) for f in found.figure_ids)
        _log(endpoint, mode, "none" if not found.rows else "differ" if differs else "match", subject_id,
             read_ms=found.read_ms, **_counts(results, found.figure_ids))
        return payload
    used = [f for f in found.figure_ids if agrees(results[f])]
    fallen = [f for f in found.figure_ids if f not in used]
    if fallen:  # today's for these, with one line saying so
        _log(endpoint, mode, "fallback", subject_id, read_ms=found.read_ms, used=used, today=fallen,
             **_counts(results, fallen))
    if not used:
        return payload
    _overlay(endpoint, payload, found, used)
    effective = found.today.effective
    payload["certified_charts"] = {
        "as_of": effective,
        "label": f"Certified · as of {certified_pages._label(effective)}",
        "note": None,
        "freshness": cf.freshness(effective, effective),
        "pages": [PAGE],
        "figure_ids": used,
        "points": sum(results[f]["compared"] for f in used),
        "build_id": found.newest.get("build_id"),
        "published_at": (found.newest["published_at"].isoformat()
                         if hasattr(found.newest.get("published_at"), "isoformat") else found.newest.get("published_at")),
        "link": certified_pages.GUIDE_LINK,
    }
    return payload


def _overlay(endpoint: str, payload: dict, found: Check, used: list[str]) -> None:
    """Write the certified points into the fields the page's script draws; each equals today's
    within the tolerances. The daily returns have no field of their own: the chart draws them
    compounded, as the rebased levels."""
    certified, effective = found.certified, found.today.effective
    planned: list[tuple[dict, str, Any]] = []

    def plan(figure_id: str, holder: dict, name: str, key: Key) -> None:
        if figure_id in used:
            planned.append((holder, name, certified[figure_id][key][0]))

    if endpoint == "analytics":
        for point in payload.get("rolling") or []:
            plan(ROLLING_VOLATILITY, point, "volatility", (_day(point.get("date")), ""))
            plan(TRAILING, point, "trailing_return", (_day(point.get("date")), ""))
        for episode in (payload.get("drawdown") or {}).get("episodes") or []:
            plan(EPISODES, episode, "depth", (effective, _day(episode.get("peak_date"))))
    elif endpoint == "benchmark-comparison":
        for point in payload.get("series") or []:
            plan(REBASED, point, "portfolio", (_day(point.get("date")), "portfolio"))
            plan(REBASED, point, "benchmark", (_day(point.get("date")), "benchmark"))
        for point in payload.get("rolling") or []:
            plan(ROLLING_BETA, point, "beta", (_day(point.get("date")), ""))
            plan(ROLLING_CORRELATION, point, "correlation", (_day(point.get("date")), ""))
    elif endpoint == "holdings":
        for row in payload.get("holdings") or []:
            if "rsi" in row:
                plan(RSI, row, "rsi", (effective, str(row.get("conid"))))
    elif endpoint == "holdings-correlation":
        conids = [str(holding.get("conid")) for holding in payload.get("holdings") or []]
        for i, left in enumerate(conids):
            for j, right in enumerate(conids):
                if left != right:
                    plan(HOLDINGS_CORRELATION, payload["matrix"][i][j], "correlation",
                         (effective, "|".join(sorted((left, right)))))
    for holder, name, value in planned:  # every lookup has worked before anything is written
        holder[name] = value


# --- The rehearsal on a restored copy (read-only) -----------------------------------------------


def rehearse(database_url: str, *, web=None, only_subjects: Iterable[str] = (), dates: int = build.RECHECK_DATES,
             minutes: float | None = None, out: Callable[[str], None] = print,
             clock: Callable[[], float] = time.monotonic) -> dict:
    """The build's chart readers on a copy, writing nothing, and what the website would show.

    For each subject, its newest ``dates`` NAV dates (every date with 0), newest first, until the
    time limit: the rows the readers give, by figure; the contract rules; against the published
    figures (new, changed, unchanged, same inputs with a new answer); the second read on the
    website's own connections for the newest date (page disagreements); reader errors; and, for
    the newest date, the website's own comparison (charts=on shows certified or today's). Ends
    with the rows the whole history needs. Prints counts and figure ids only; subjects masked.
    Run with CERTIFIED_FIGURES_PAGES empty, so the pages answer the old way."""
    import psycopg

    limit = build.time_limit(minutes)
    begun = clock()
    web = web or build.load_web()
    counts: dict[str, Any] = {"by_figure": {}, "rule_failures": {}, "page_disagreements": {}, "reader_errors": {},
                              "website": {}, "subject_dates_read": 0, "subject_dates_planned": 0}
    by_figure: dict[str, Counter] = {}
    rows: list = []
    notes: dict = {}
    per_date: list[int] = []
    with build.shared_web_database(web, database_url):
        found, skipped = build.subjects(web, only_subjects)
        plan = [(subject, list(reversed(subject.dates))[:dates or None]) for subject in found]
        counts["subject_dates_planned"] = sum(len(days) for _, days in plan)
        full_history = sum(len(subject.dates) for subject in found)
        with psycopg.connect(database_url, options=f"-c app.tenant_id={web.TENANT_ID} "
                                                   "-c default_transaction_read_only=on") as connection:
            published = build.published_rows(connection, [subject.id for subject in found])
            connection.rollback()
        stopped = False
        for subject, days in plan:
            for day in days:
                if clock() - begun >= limit * 60:
                    stopped = True
                    break
                read = build.page_rows(web, subject, day, [PAGE], notes=notes)
                counts["subject_dates_read"] += 1
                per_date.append(len(read))
                rows += read
                for row in read:
                    by_figure.setdefault(row.figure_id, Counter())[row.status] += 1
                if day == subject.dates[-1]:
                    who = build.mask(subject.id)
                    with build.own_connections(web):
                        again = build.page_rows(web, subject, day, [PAGE], notes={})
                    first = {row.key: row.compared() for row in read}
                    second = {row.key: row.compared() for row in again}
                    differing = sorted({key[1] for key in set(first) | set(second) if first.get(key) != second.get(key)})
                    if differing:
                        counts["page_disagreements"][who] = differing
                    counts["website"][who] = _website_check(web, subject, day)
            if stopped:
                break
    result = build.compare(rows, published)
    failures = build.rule_failures(rows)
    counts["rule_failures"] = dict(Counter(failures))
    counts["by_figure"] = {figure: dict(found_counts) for figure, found_counts in sorted(by_figure.items())}
    counts.update(rows=len(rows), new=len(result["new"]), changed=len(result["changed"]),
                  unchanged=result["unchanged"], same_inputs_new_answer=len(result["violations"]),
                  same_inputs_new_answer_figures=sorted({row.figure_id for row in result["violations"]}),
                  page_unavailable=notes.get("page_unavailable", {}),
                  reader_errors=notes.get("chart_errors", {}), subjects=len(found), subjects_skipped=len(skipped),
                  published_chart_rows=sum(key[1] in FIGURES for key in published),
                  rows_per_date=round(sum(per_date) / len(per_date), 1) if per_date else 0.0,
                  full_history_dates=full_history)
    counts["full_history_rows"] = round(counts["rows_per_date"] * full_history)
    for line in report_lines(counts, stopped=stopped, minutes=limit):
        out(line)
    return counts


def _website_check(web, subject, day: str) -> dict:
    """What charts=on would show for each chart figure on the subject's newest date: counts only."""
    pages = build.Pages(web, subject, day)
    answers = {"analytics": pages.analytics, "benchmark-comparison": pages.comparison, "holdings": pages.holdings,
               "holdings-correlation": lambda: correlation_payload(pages)}
    shown: dict[str, dict] = {}
    for endpoint, answer in answers.items():
        if endpoint == "holdings-correlation" and subject.kind == "composite_portfolio":
            continue
        try:
            payload = answer()
            found = check(endpoint, payload, web._database, tenant_id=web.TENANT_ID, subject_id=subject.id)
        except build.PageUnavailable:
            continue
        except Exception as error:  # noqa: BLE001 - counted, as the website logs it
            shown[endpoint] = {"error": type(error).__name__}
            continue
        if found is None:
            continue
        for figure_id, result in found.results.items():
            shown[figure_id] = {**result, "shows": "certified" if agrees(result) else "today's",
                                "read_ms": found.read_ms}
    return shown


def report_lines(counts: Mapping, *, stopped: bool, minutes: float) -> list[str]:
    lines = [f"Charts rehearsal (read-only): {counts['subjects']} subjects ({counts['subjects_skipped']} skipped), "
             f"{counts['subject_dates_read']} of {counts['subject_dates_planned']} subject dates read"
             + (f", stopped after {minutes:g} minutes." if stopped else "."),
             f"Rows read: {counts['rows']} ({counts['rows_per_date']} a subject date)."]
    for figure, statuses in counts["by_figure"].items():
        lines.append(f"  {figure}: " + ", ".join(f"{status} {count}" for status, count in sorted(statuses.items())))
    lines.append(f"Against the published figures: {counts['new']} new, {counts['changed']} changed, "
                 f"{counts['unchanged']} unchanged, {counts['same_inputs_new_answer']} changed with the same inputs"
                 + (f" ({', '.join(counts['same_inputs_new_answer_figures'])})" if counts['same_inputs_new_answer']
                    else "") + ".")
    failures = counts["rule_failures"]
    lines.append(f"Rule failures: {sum(failures.values())}"
                 + (" (" + ", ".join(f"{k} {v}" for k, v in sorted(failures.items())) + ")" if failures else "") + ".")
    lines.append(f"Page disagreements (second read, the website's own connections): "
                 f"{len(counts['page_disagreements'])} subjects.")
    for who, figure_ids in sorted(counts["page_disagreements"].items()):
        lines.append(f"  {who}: {', '.join(figure_ids)}")
    errors = counts["reader_errors"]
    lines.append(f"Reader errors: {sum(sum(kinds.values()) for kinds in errors.values())}"
                 + (" (" + "; ".join(f"{reader}: " + ", ".join(f"{kind} {n}" for kind, n in sorted(kinds.items()))
                                     for reader, kinds in sorted(errors.items())) + ")" if errors else "") + ".")
    if counts["page_unavailable"]:
        lines.append("Pages that answered with an error (as the website shows): "
                     + ", ".join(f"{page} {n}" for page, n in sorted(counts["page_unavailable"].items())) + ".")
    lines.append("The website on each subject's newest date (what charts=on would show):")
    for who, figures in sorted(counts["website"].items()):
        for figure, result in sorted(figures.items()):
            if "error" in result:
                lines.append(f"  {who} {figure}: error {result['error']}")
            else:
                lines.append(f"  {who} {figure}: {result['compared']} compared, {result['differ']} differ, "
                             f"{result['missing']} missing, {result['extra']} extra: shows {result['shows']} "
                             f"({result['read_ms']:g} ms)")
    lines.append(f"Chart rows already published: {counts['published_chart_rows']}. The whole history needs about "
                 f"{counts['full_history_rows']} rows ({counts['full_history_dates']} subject dates).")
    return lines
