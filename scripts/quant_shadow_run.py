"""Shadow run of the gold quant build on a restored copy of the database (Fri 30 Oct 2026).

docs/QUANT_ANALYTICS.md, "Shadow run on a restored copy (Fri 30 Oct)", has the plan. The temporary
job ibkrv2stg-quant-shadow (infra/staging-quant-shadow-job.bicep) runs it, or it is run by hand on
the admin VM. It works on a restored copy only, never the live server and never through the
bootstrap job:

  python scripts/quant_shadow_run.py preflight  --copy-host <copy FQDN> [--copy-subnet CIDR]
  python scripts/quant_shadow_run.py apply-held --copy-host ...
  python scripts/quant_shadow_run.py run        --copy-host ... [--max-minutes 180] [--history-days 365]
  python scripts/quant_shadow_run.py review     --copy-host ... --out DIR
  python scripts/quant_shadow_run.py undo       --copy-host ...

The guard comes first in every command, before any connection. The database URL (DATABASE_ADMIN_URL,
or the variable --url-env names) must name exactly one host. That host must equal --copy-host and
must not be a live server: the two names below, plus any in LIVE_DATABASE_HOSTS. Its server name
must also look like a restored copy (-recovery-, -restore- or -shadow-). With --copy-subnet, every
address the host resolves to must lie in that subnet. Otherwise the command exits 2 and connects to
nothing. It then prints the host and database it will use.

preflight   Read-only. The server version, whether the 076 and 077 objects exist already,
            subjects by type, the latest NAV date of each subject the daily job builds (masked),
            and row counts of the silver and gold inputs the build reads.
apply-held  Applies infra/medallion/drafts/quant_analytics.sql, then desk_live_views.sql, each in
            one transaction, and records each in quant_shadow.draft_events on the copy. It never
            touches schema_migrations, migrations/postgres or the bootstrap job. A draft whose
            objects are all there already is left alone.
run         First the daily job's pass: every subject's latest NAV date, as
            scripts/build_quant_analytics.py runs it (the same subjects, order, lock, transaction
            and QUANT_ANALYTICS_MINUTES budget), timed. Then the past year of NAV dates of each
            subject, newest first. Subject-dates that already have every section are skipped, so
            a second run carries on. --max-minutes bounds the whole command. The latest-date pass
            always runs to its own budget first. Progress lines show masked ids. The build setting
            is forced on in this process only. The publish gate is off, as in the daily job.
review      Writes review.md and review.json to --out and prints both between BEGIN and END
            lines prefixed QUANT_SHADOW_REVIEW, so they can be read back from the job's logs.
            Counts only: rows by section and quality, unavailable reasons (dates and numbers
            replaced), timing against the 09:30 to 09:40 window, the contract rules, the 077
            views' row counts and the errors.
undo        Runs the two undo drafts on the copy and clears the run log, for a second rehearsal.

Subjects appear as u01, u02, ... in every output (sorted ids, so stable on one copy). The mapping
is never written. The run log in the copy's quant_shadow schema holds the real ids; it goes with
the copy.

Exit codes: 0 done, 1 something failed (read the output), 2 refused or could not connect,
3 run stopped at --max-minutes with dates left (run it again to carry on).
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import logging
import math
import os
import re
import socket
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _path in (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import build_quant_analytics as job  # noqa: E402  the daily job's subject and date queries
from ibkr_etl import publish_gate  # noqa: E402
from ibkr_etl import quant_snapshots as qs  # noqa: E402
from ibkr_etl.certified_figures import medallion_dir  # noqa: E402
from ibkr_etl.db import DEFAULT_TENANT_ID, connect  # noqa: E402

LIVE_HOSTS = ("ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com", "ibkrv2stg.postgres.database.azure.com")
LIVE_HOSTS_ENV = "LIVE_DATABASE_HOSTS"
COPY_MARKERS = ("-recovery-", "-restore-", "-shadow-")
URL_ENV = "DATABASE_ADMIN_URL"
SCHEMA = "quant_shadow"
REVIEW_PREFIX = "QUANT_SHADOW_REVIEW"
WINDOW_START, WINDOW_END, LATER_START = "09:30", "09:40", "10:00"
WINDOW_SECONDS = 600  # the daily job must be over by 09:40, before the certified run at 09:45
DEFAULT_MAX_MINUTES = 180.0
HISTORY_DAYS = 365
STATEMENT_TIMEOUT_MS = 120000  # each count or check on the copy
EXIT_OK, EXIT_FAILED, EXIT_REFUSED, EXIT_INCOMPLETE = 0, 1, 2, 3
UNKNOWN_ID = "u??"  # an account-like id in a message that is not one of the copy's subjects
_now = time.monotonic  # budgets and timings (tests replace it)


@dataclass(frozen=True)
class Draft:
    name: str
    file: str
    undo: str
    number: str  # its migration number on its day
    objects: tuple[tuple[str, str], ...]  # (relation, relkind) once applied on a database with the layers


DRAFTS = (
    Draft("quant_analytics", "quant_analytics.sql", "quant_analytics_undo.sql", "076",
          (("gold.quant_analytics_snapshots", "r"), ("public.quant_analytics_snapshots", "v"))),
    Draft("desk_live_views", "desk_live_views.sql", "desk_live_views_undo.sql", "077",
          (("silver.subject_live_executions", "v"), ("public.subject_live_executions", "v"),
           ("silver.benchmark_live_quotes", "v"), ("public.benchmark_live_quotes", "v"))),
)

LEDGER_SQL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
COMMENT ON SCHEMA {SCHEMA} IS
    'Shadow run of the quant build on this restored copy (scripts/quant_shadow_run.py); goes with the copy.';
CREATE TABLE IF NOT EXISTS {SCHEMA}.draft_events (
    event_id BIGSERIAL PRIMARY KEY,
    draft TEXT NOT NULL,
    file TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('apply', 'undo')),
    outcome TEXT NOT NULL CHECK (outcome IN ('applied', 'undone', 'failed')),
    sha256 TEXT NOT NULL,
    seconds DOUBLE PRECISION NOT NULL,
    error TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {SCHEMA}.passes (
    pass_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN ('latest', 'history')),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    seconds DOUBLE PRECISION,
    summary JSONB NOT NULL DEFAULT '{{}}'::jsonb
);
CREATE TABLE IF NOT EXISTS {SCHEMA}.builds (
    pass_id UUID NOT NULL REFERENCES {SCHEMA}.passes(pass_id) ON DELETE CASCADE,
    subject_id TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    status TEXT NOT NULL,
    seconds DOUBLE PRECISION,
    load_ms DOUBLE PRECISION,
    written INTEGER NOT NULL DEFAULT 0,
    sections JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    errors JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pass_id, subject_id, as_of_date)
);
"""

COMPLETE_SQL = """
SELECT subject_id, as_of_date FROM quant_analytics_snapshots
WHERE tenant_id = ? AND analytics_version = ?
GROUP BY subject_id, as_of_date HAVING count(DISTINCT section) = ?
"""


# ---------------------------------------------------------------- the guard (no connection)


class Refused(Exception):
    """The database URL is not the restored copy, so nothing is connected."""


@dataclass(frozen=True)
class Target:
    host: str
    database: str
    url: str = field(repr=False)


def _host_name(value: Any) -> str:
    return str(value or "").strip().rstrip(".").lower()


def live_hosts(environ: Mapping[str, str]) -> set[str]:
    """The live server names: the two this platform has, and any in LIVE_DATABASE_HOSTS."""
    extra = re.split(r"[\s,;]+", str(environ.get(LIVE_HOSTS_ENV, "")))
    return {_host_name(host) for host in (*LIVE_HOSTS, *extra) if _host_name(host)}


def _is_live(host: str, live: set[str]) -> bool:
    """The host, or its server name (its first label, as in <server>.private.postgres...), is live."""
    return host in live or host.split(".")[0] in {name.split(".")[0] for name in live}


def check_target(url: str, copy_host: str | None, *, environ: Mapping[str, str] | None = None,
                 copy_subnet: str | None = None, url_env: str = URL_ENV,
                 resolve: Callable[..., list] | None = None) -> Target:
    """The copy to connect to, or Refused. Reads the URL only; never connects."""
    environ = os.environ if environ is None else environ
    resolve = socket.getaddrinfo if resolve is None else resolve
    if "bootstrap" in str(environ.get("CONTAINER_APP_JOB_NAME", "")).lower():
        raise Refused("this runs in its own job, never through the bootstrap job")
    if not str(url or "").strip():
        raise Refused(f"{url_env} is not set")
    if not str(url).startswith(("postgres://", "postgresql://")):
        raise Refused(f"{url_env} must be a postgresql:// URL")
    from psycopg.conninfo import conninfo_to_dict

    try:
        params = conninfo_to_dict(url)
    except Exception as error:  # noqa: BLE001 - never echo the URL, which holds the password
        raise Refused(f"{url_env} could not be read as a PostgreSQL URL ({type(error).__name__})") from None
    if params.get("hostaddr") or environ.get("PGHOSTADDR"):
        raise Refused("the URL names hostaddr (or PGHOSTADDR is set), which would connect past the host check")
    if params.get("service") or environ.get("PGSERVICE"):
        raise Refused("the URL names a service (or PGSERVICE is set), which could name another host")
    hosts = [_host_name(host) for host in str(params.get("host") or "").split(",")]
    if len(hosts) != 1 or not hosts[0]:
        raise Refused("the URL must name exactly one host")
    host, copy = hosts[0], _host_name(copy_host)
    if not copy:
        raise Refused("--copy-host is required: the restored copy's host name")
    live = live_hosts(environ)
    if _is_live(host, live):
        raise Refused(f"{host} is a live database server (or an alias of one); this runs on a restored copy only")
    if _is_live(copy, live):
        raise Refused(f"--copy-host {copy} names a live database server")
    if host != copy:
        raise Refused(f"the URL's host {host} is not --copy-host {copy}")
    server = host.split(".")[0]
    if not any(marker in server for marker in COPY_MARKERS):
        raise Refused(f"{host} does not look like a restored copy: its server name must contain "
                      + ", ".join(COPY_MARKERS[:-1]) + f" or {COPY_MARKERS[-1]}")
    if copy_subnet:
        try:
            network = ipaddress.ip_network(copy_subnet, strict=False)
        except ValueError:
            raise Refused(f"--copy-subnet {copy_subnet} is not a subnet") from None
        try:
            addresses = sorted({item[4][0] for item in resolve(host, 5432, type=socket.SOCK_STREAM)})
        except OSError as error:
            raise Refused(f"{host} could not be resolved ({type(error).__name__})") from None
        outside = [address for address in addresses if ipaddress.ip_address(address) not in network]
        if not addresses or outside:
            raise Refused(f"{host} resolves to {', '.join(outside) or 'nothing'}, outside --copy-subnet {network}")
    database = str(params.get("dbname") or params.get("user") or "")
    return Target(host=host, database=database, url=str(url))


def _open(target: Target, tenant_id: str):
    """A connection to the copy, as the job opens one (db.connect: ? placeholders, app.tenant_id)."""
    return connect(Path("/nonexistent/quant-shadow.sqlite3"), target.url, tenant_id)


# ---------------------------------------------------------------- masking


# IBKR account numbers (U1234567), and Kiwoom's platform ids (KIWOOM-<account number>).
ACCOUNT_ID = re.compile(r"[A-Z]{1,2}\d{5,}|KIWOOM-[0-9A-Za-z]+")
DATE_IN_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?")
NUMBER_IN_TEXT = re.compile(r"\d+(?:[.,]\d+)*(?:[eE][-+]?\d+)?")


class Masker:
    """u01, u02, ... for the subject ids given, in sorted order, so the labels hold within one
    review and on one copy. Any other account-like id in a text becomes u??."""

    def __init__(self, subject_ids: Iterable[Any] = (), aliases: Mapping[str, Any] | None = None):
        ordered = sorted({str(value) for value in subject_ids if value is not None and str(value)})
        width = max(2, len(str(len(ordered))))
        self.labels = {subject: f"u{index:0{width}d}" for index, subject in enumerate(ordered, 1)}
        # Another id of a subject (a Kiwoom account's bare account number) takes the subject's label.
        for alias, subject in (aliases or {}).items():
            alias, subject = str(alias or "").strip(), str(subject)
            if alias and alias not in self.labels and subject in self.labels:
                self.labels[alias] = self.labels[subject]
        known = [rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])"
                 for name in sorted(self.labels, key=len, reverse=True)]
        self._ids = re.compile("(" + "|".join([*known, ACCOUNT_ID.pattern]) + ")")

    def label(self, subject_id: Any) -> str:
        return self.labels.get(str(subject_id), UNKNOWN_ID)

    def text(self, value: Any) -> str:
        """Ids masked, the rest as it is (log lines)."""
        return self._ids.sub(lambda match: self.label(match.group(0)), str(value))

    def clean(self, value: Any, limit: int = 300) -> str:
        """Ids masked, dates and numbers replaced, plain ASCII (reasons and errors): no figure
        survives, and the same message on different days or subjects groups together."""
        parts = self._ids.split(str(value or ""))
        cleaned = "".join(self.label(part) if index % 2 else
                          NUMBER_IN_TEXT.sub("<n>", DATE_IN_TEXT.sub("<date>", part))
                          for index, part in enumerate(parts))
        cleaned = re.sub(r"\s+", " ", cleaned).strip().replace("|", "/")
        return cleaned.encode("ascii", "replace").decode("ascii")[:limit]


_MASK = [Masker()]  # what the log formatter masks with; the commands set the copy's subjects


class _MaskedFormatter(logging.Formatter):
    """Log records (an engine's traceback names its subject) with the ids masked."""

    def format(self, record: logging.LogRecord) -> str:
        return _MASK[0].text(super().format(record))


def _subjects_masker(connection, tenant_id: str) -> Masker:
    """Every subject id of the copy that any output could name."""
    found = {str(row[0]) for row in connection.execute("SELECT subject_id FROM platform_subjects").fetchall()}
    if _exists(connection, "public.quant_analytics_snapshots"):
        found |= {str(row[0]) for row in connection.execute(
            "SELECT DISTINCT subject_id FROM quant_analytics_snapshots").fetchall()}
    if _exists(connection, f"{SCHEMA}.builds"):
        found |= {str(row[0]) for row in connection.execute(f"SELECT DISTINCT subject_id FROM {SCHEMA}.builds"
                                                             ).fetchall()}
    aliases = {str(row[1]): str(row[0]) for row in connection.execute(
        "SELECT subject_id, source_account_id FROM platform_subjects WHERE source_account_id IS NOT NULL"
    ).fetchall() if str(row[1] or "").strip()}
    connection.rollback()
    _MASK[0] = Masker(found, aliases)
    return _MASK[0]


def _brokers(connection, tenant_id: str) -> dict[str, str]:
    """Each physical account's broker as accounts.account_type has it (kiwoom for Kiwoom)."""
    found = {str(row[0]): str(row[1] or "ibkr").lower() for row in connection.execute(
        "SELECT account_id, account_type FROM accounts WHERE tenant_id = ?", (tenant_id,)).fetchall()}
    connection.rollback()
    return found


# ---------------------------------------------------------------- timing maths (pure)


def percentile(values: Sequence[float], q: float) -> float | None:
    """The q-th quantile by linear interpolation between the closest ranks (inclusive)."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def spread(values: Iterable[float | None], digits: int = 3) -> dict[str, Any]:
    found = [float(value) for value in values if value is not None]
    if not found:
        return {"count": 0, "p50": None, "p95": None, "max": None}
    return {"count": len(found), "p50": round(percentile(found, 0.5), digits),
            "p95": round(percentile(found, 0.95), digits), "max": round(max(found), digits)}


def window_verdict(seconds: float | None, *, subject_dates: int, skipped_for_time: int,
                   minutes: float) -> dict[str, Any]:
    """The latest-date pass against the 09:30 to 09:40 window. It fits when it started every
    subject-date inside its budget and was over within the window's ten minutes; if not, the job
    moves to 10:00 (Medallion review, note 3). The margin is what is left of the ten minutes for
    the container's start, which the pass does not include."""
    if seconds is None or subject_dates == 0:
        return {"measured": False, "window": f"{WINDOW_START} to {WINDOW_END} UTC"}
    ends = datetime(2026, 1, 1, 9, 30) + timedelta(seconds=math.ceil(seconds))
    fits = skipped_for_time == 0 and seconds <= WINDOW_SECONDS
    return {"measured": True, "window": f"{WINDOW_START} to {WINDOW_END} UTC", "window_seconds": WINDOW_SECONDS,
            "seconds": round(seconds, 3), "budget_minutes": minutes, "within_budget": seconds <= minutes * 60,
            "subject_dates": subject_dates, "skipped_for_time": skipped_for_time,
            "would_end_at": ends.strftime("%H:%M:%S"), "margin_seconds": round(WINDOW_SECONDS - seconds, 1),
            "fits_window": fits, "move_to_10_00": not fits}


def _cold(build: Mapping[str, Any]) -> bool:
    """Rebuilt with no section skipped as unchanged: a whole subject-date's work."""
    sections = build.get("sections") or {}
    return (build.get("status") == "rebuilt" and bool(sections)
            and all(section.get("status") != "unchanged" for section in sections.values()))


def timing_summary(builds: Sequence[Mapping[str, Any]], passes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Seconds per subject-date and milliseconds per section over the cold builds (every section
    built), the latest-date pass against the window, and the history passes."""
    cold = [build for build in builds if _cold(build)]
    latest = sorted((item for item in passes if item.get("phase") == "latest" and item.get("finished")),
                    key=lambda item: str(item.get("started_at")))
    chosen = next((item for item in latest if (item.get("summary") or {}).get("cold")), latest[-1] if latest else None)
    sections: dict[str, list[float]] = {name: [] for name in qs.SECTION_NAMES}
    for build in cold:
        for name, section in (build.get("sections") or {}).items():
            if section.get("status") == "rebuilt" and section.get("build_ms") is not None and name in sections:
                sections[name].append(float(section["build_ms"]))
    summary: dict[str, Any] = {
        "latest_pass": {"measured": False, "window": f"{WINDOW_START} to {WINDOW_END} UTC"},
        "latest_passes": len(latest),
        "per_subject_date_seconds": spread(build.get("seconds") for build in cold),
        "load_ms": spread((build.get("load_ms") for build in cold), digits=1),
        "per_section_ms": {name: spread(values, digits=1) for name, values in sections.items()},
        "history": {"passes": sum(1 for item in passes if item.get("phase") == "history"),
                    "seconds": round(sum(float(item.get("seconds") or 0.0) for item in passes
                                         if item.get("phase") == "history"), 3),
                    "rebuilt": sum(1 for build in builds if build.get("phase") == "history"
                                   and build.get("status") == "rebuilt")},
    }
    if chosen is not None:
        info = chosen.get("summary") or {}
        verdict = window_verdict(chosen.get("seconds"), subject_dates=int(info.get("subject_dates") or 0),
                                 skipped_for_time=len(info.get("skipped_for_time") or []),
                                 minutes=float(info.get("minutes") or qs.DEFAULT_MINUTES))
        mine = [build for build in cold if build.get("pass_id") == chosen.get("pass_id")]
        summary["latest_pass"] = {**verdict, "cold": bool(info.get("cold")), "rebuilt": int(info.get("rebuilt") or 0),
                                  "unchanged": int(info.get("unchanged") or 0),
                                  "runner": str(info.get("runner") or ""), "cpu_count": info.get("cpu_count"),
                                  "per_subject_date_seconds": spread(build.get("seconds") for build in mine)}
    return summary


# ---------------------------------------------------------------- small database helpers


def _tuples(rows: Iterable[Any]) -> list[tuple]:
    """Rows as plain tuples (db.HybridRow iterates over its column names, like a mapping)."""
    return [tuple(row[index] for index in range(len(row))) for row in rows]


def _exists(connection, name: str) -> bool:
    return connection.execute("SELECT to_regclass(?) IS NOT NULL AS found", (name,)).fetchone()["found"]


def _relkind(connection, name: str) -> str | None:
    row = connection.execute("SELECT relkind::text AS kind FROM pg_class WHERE oid = to_regclass(?)",
                             (name,)).fetchone()
    return row["kind"] if row else None


def _read_only(connection) -> None:
    connection.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    connection.execute("SELECT set_config('statement_timeout', ?, false)", (str(STATEMENT_TIMEOUT_MS),))
    connection.commit()


def _ensure_ledger(connection) -> None:
    connection.execute(LEDGER_SQL)
    connection.commit()


def _draft_state(connection, draft: Draft) -> tuple[str, dict[str, str | None]]:
    """absent (none of its objects), applied (all, as the draft leaves them) or partial."""
    kinds = {name: _relkind(connection, name) for name, _ in draft.objects}
    if not any(kinds.values()):
        return "absent", kinds
    if all(kinds[name] == kind for name, kind in draft.objects):
        return "applied", kinds
    return "partial", kinds


def _draft_path(draft: Draft, undo: bool = False) -> Path:
    return medallion_dir() / "drafts" / (draft.undo if undo else draft.file)


def _say(item: Mapping[str, Any]) -> None:
    print(json.dumps(item, sort_keys=True, default=str), flush=True)


def _emit(name: str, text: str, out: Callable[[str], None] = print) -> None:
    """One file of the review as numbered log lines between a BEGIN line (its sha256 and line
    count) and an END line, so it can be put back together from the job's logs in order."""
    lines = text[:-1].split("\n") if text.endswith("\n") else text.split("\n")
    out(f"{REVIEW_PREFIX} {name} BEGIN sha256={hashlib.sha256(text.encode('utf-8')).hexdigest()} lines={len(lines)}")
    for number, line in enumerate(lines, 1):
        out(f"{REVIEW_PREFIX} {name} {number:05d} {line}")
    out(f"{REVIEW_PREFIX} {name} END")


# ---------------------------------------------------------------- what is on the copy (read-only)


def _inputs() -> tuple[tuple[str, str, tuple], ...]:
    from ibkr_etl.benchmark_analytics import CALCULATION_VERSION

    good = "quality_status IN ('pass', 'warn')"
    return (
        ("performance_comparison_daily", f"SELECT count(*) FROM performance_comparison_daily "
                                         f"WHERE calculation_version = ? AND {good}", (CALCULATION_VERSION,)),
        ("risk_free_rates", f"SELECT count(*) FROM risk_free_rates WHERE currency = 'USD' AND {good}", ()),
        ("subject_position_series", "SELECT count(*) FROM subject_position_series", ()),
        ("instruments", "SELECT count(*) FROM instruments", ()),
        ("instrument_classifications", f"SELECT count(*) FROM instrument_classifications "
                                       f"WHERE status = 'classified' AND {good}", ()),
        ("trades", "SELECT count(*) FROM trades", ()),
        ("reference_fx_rates", f"SELECT count(*) FROM reference_fx_rates WHERE {good}", ()),
        ("subject_nav_series", "SELECT count(*) FROM subject_nav_series", ()),
        ("subject_cash_activity_series", "SELECT count(*) FROM subject_cash_activity_series", ()),
        ("portfolio_benchmark_mapping", "SELECT count(*) FROM portfolio_benchmark_mapping "
                                        "WHERE mapping_type = 'primary'", ()),
    )


def copy_report(connection, tenant_id: str, masker: Masker) -> dict[str, Any]:
    """Counts, dates and names only: the server, the held objects, the subjects and the inputs."""
    from ibkr_etl.profiled_composite import is_profiled

    version = str(connection.execute("SHOW server_version").fetchone()[0])
    objects = {name: _relkind(connection, name) for draft in DRAFTS for name, _ in draft.objects}
    prerequisites = {
        "bronze_silver_gold_schemas": all(connection.execute("SELECT to_regnamespace(?) IS NOT NULL", (layer,)
                                                             ).fetchone()[0] for layer in ("bronze", "silver", "gold")),
        "lineage_stamp_run": connection.execute("SELECT to_regprocedure('public.lineage_stamp_run()') IS NOT NULL"
                                                ).fetchone()[0],
        "live_tables": all(_exists(connection, f"bronze.{name}")
                           for name in ("live_executions", "live_contracts", "live_quotes")),
    }
    by_type = [{"subject_type": str(row[0]), "lifecycle_status": str(row[1]), "subjects": int(row[2])}
               for row in connection.execute(
                   "SELECT subject_type, lifecycle_status, count(*) FROM platform_subjects GROUP BY 1, 2 ORDER BY 1, 2"
               ).fetchall()]
    eligible = [str(row["subject_id"]) for row in connection.execute(job.SUBJECTS_SQL, (tenant_id,)).fetchall()]
    types = {str(row[0]): str(row[1]) for row in connection.execute(
        "SELECT subject_id, subject_type FROM platform_subjects WHERE tenant_id = ?", (tenant_id,)).fetchall()}
    own = sum(1 for subject in eligible if subject in qs.OWN_PIPELINES or (
        types.get(subject) == "composite_portfolio" and is_profiled(connection, subject, tenant_id=tenant_id)))
    brokers = _brokers(connection, tenant_id)
    latest = []
    for subject in eligible:
        value = connection.execute(job.LATEST_NAV_SQL, (tenant_id, subject)).fetchone()["latest"]
        latest.append({"subject": masker.label(subject), "subject_type": types.get(subject, ""),
                       "broker": _broker_group(brokers.get(subject), types.get(subject, "")),
                       "latest_nav_date": str(value)[:10] if value else None})
    connection.rollback()
    inputs = {}
    for name, sql, params in _inputs():
        try:
            inputs[name] = {"rows": int(connection.execute(sql, params).fetchone()[0])}
        except Exception as error:  # noqa: BLE001 - a count that cannot run is reported, not raised
            inputs[name] = {"error": type(error).__name__}
        connection.rollback()
    return {"server_version": version.split()[0], "server_major": version.split(".")[0], "objects": objects,
            "prerequisites": prerequisites, "subjects": {
                "by_type": by_type, "eligible": len(eligible), "own_pipeline": own,
                "latest_nav": sorted(latest, key=lambda item: item["subject"])},
            "inputs": inputs}


def preflight(connection, target: Target, args, environ) -> int:
    _read_only(connection)
    masker = _subjects_masker(connection, args.tenant)
    report = copy_report(connection, args.tenant, masker)
    events = (connection.execute(f"SELECT count(*) FROM {SCHEMA}.draft_events").fetchone()[0]
              if _exists(connection, f"{SCHEMA}.draft_events") else 0)
    connection.rollback()
    _say({"command": "preflight", "copy": {"host": target.host, "database": target.database}, **report,
          "shadow_ledger_events": int(events)})
    return EXIT_OK


# ---------------------------------------------------------------- the held drafts


def _draft_event(connection, draft: Draft, *, action: str, outcome: str, sha256: str, seconds: float,
                 error: str | None = None) -> None:
    connection.execute(f"INSERT INTO {SCHEMA}.draft_events (draft, file, action, outcome, sha256, seconds, error) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (draft.name, draft.undo if action == "undo" else draft.file, action, outcome, sha256,
                        round(seconds, 3), error))


def _run_draft(connection, draft: Draft, *, undo: bool) -> dict[str, Any]:
    """One draft (or its undo) in one transaction, recorded in the ledger in the same commit."""
    path = _draft_path(draft, undo)
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    action = "undo" if undo else "apply"
    item: dict[str, Any] = {"command": "undo" if undo else "apply-held", "draft": draft.name, "number": draft.number,
                            "file": path.name, "sha256": digest}
    state, kinds = _draft_state(connection, draft)
    connection.rollback()
    if not undo and state == "applied":
        return {**item, "outcome": "already applied", "objects": kinds}
    if not undo and state == "partial":
        return {**item, "outcome": "failed", "objects": kinds,
                "error": "some of its objects exist and some do not; run undo first"}
    if undo and state == "absent":
        return {**item, "outcome": "nothing to undo", "objects": kinds}
    raw = getattr(connection, "_connection", connection)  # the draft text as it is, no ? placeholders
    started = _now()
    try:
        raw.execute(text)
        state, kinds = _draft_state(connection, draft)
        wanted = "absent" if undo else "applied"
        if state != wanted:
            raise RuntimeError(f"the draft left its objects {state}: {kinds}")
        seconds = _now() - started
        _draft_event(connection, draft, action=action, outcome="undone" if undo else "applied", sha256=digest,
                     seconds=seconds)
        connection.commit()
        return {**item, "outcome": "undone" if undo else "applied", "seconds": round(seconds, 3), "objects": kinds}
    except Exception as error:  # noqa: BLE001 - recorded and reported; the transaction rolls back whole
        connection.rollback()
        message = f"{type(error).__name__}: {error}"[:500]
        _draft_event(connection, draft, action=action, outcome="failed", sha256=digest, seconds=_now() - started,
                     error=message)
        connection.commit()
        return {**item, "outcome": "failed", "error": _MASK[0].text(message)}


def apply_held(connection, target: Target, args, environ) -> int:
    _ensure_ledger(connection)
    _subjects_masker(connection, args.tenant)
    results = [_run_draft(connection, draft, undo=False) for draft in DRAFTS]
    for result in results:
        _say(result)
    return EXIT_FAILED if any(result["outcome"] == "failed" for result in results) else EXIT_OK


def undo(connection, target: Target, args, environ) -> int:
    _ensure_ledger(connection)
    _subjects_masker(connection, args.tenant)
    results = [_run_draft(connection, draft, undo=True) for draft in DRAFTS]
    # The rows went with the table: the next run starts a new rehearsal, timed cold again.
    cleared = connection.execute(f"DELETE FROM {SCHEMA}.passes WHERE tenant_id = ?", (args.tenant,)).rowcount
    connection.commit()
    for result in results:
        _say(result)
    _say({"command": "undo", "run_log_passes_cleared": cleared})
    return EXIT_FAILED if any(result["outcome"] == "failed" for result in results) else EXIT_OK


# ---------------------------------------------------------------- the build


def _build_one(connection, tenant_id: str, subject_id: str, as_of: str, environ: Mapping[str, str]) -> dict[str, Any]:
    """One subject-date exactly as scripts/build_quant_analytics.py builds it: its own advisory
    lock, one REPEATABLE READ transaction, the sections written and committed together."""
    scope = {"tenant_id": tenant_id, "subject_id": subject_id, "as_of": as_of}
    item: dict[str, Any] = {"subject_id": subject_id, "as_of": as_of, "written": 0, "sections": {}, "errors": {},
                            "load_ms": None}
    started = _now()
    with qs.quant_date_lock(connection, **scope) as acquired:
        if not acquired:
            return {**item, "status": "blocked", "seconds": None,
                    "errors": {"build": "another quant worker owns this date"}}
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        try:
            result = qs.build_quant_snapshot(connection, **scope, environ=environ)
        except publish_gate.BuildHeldBack as error:
            return {**item, "status": "held_back", "seconds": None, "errors": {"build": str(error)[:500]}}
        except Exception as error:  # noqa: BLE001 - one subject-date never stops the others
            connection.rollback()
            return {**item, "status": "failed", "seconds": None,
                    "errors": {"build": f"{type(error).__name__}: {error}"[:500]}}
    return {**item, "status": result["status"], "seconds": round(_now() - started, 3), "load_ms": result["load_ms"],
            "written": result["written"], "errors": dict(result["errors"]),
            "sections": {name: {key: section.get(key) for key in ("status", "build_ms", "quality_status", "available")
                                if key in section} for name, section in result["sections"].items()}}


def _start_pass(connection, tenant_id: str, phase: str) -> str:
    pass_id = str(uuid.uuid4())
    connection.execute(f"INSERT INTO {SCHEMA}.passes (pass_id, tenant_id, phase, started_at) VALUES (?::uuid, ?, ?, ?)",
                       (pass_id, tenant_id, phase, datetime.now(timezone.utc)))
    connection.commit()
    return pass_id


def _finish_pass(connection, pass_id: str, seconds: float, summary: Mapping[str, Any]) -> None:
    connection.execute(f"UPDATE {SCHEMA}.passes SET finished_at = ?, seconds = ?, summary = ?::jsonb "
                       "WHERE pass_id = ?::uuid",
                       (datetime.now(timezone.utc), round(seconds, 3), json.dumps(summary, sort_keys=True), pass_id))
    connection.commit()


def _record(connection, pass_id: str, item: Mapping[str, Any]) -> None:
    connection.execute(
        f"""
        INSERT INTO {SCHEMA}.builds (pass_id, subject_id, as_of_date, status, seconds, load_ms, written, sections, errors)
        VALUES (?::uuid, ?, ?, ?, ?, ?, ?, ?::jsonb, ?::jsonb)
        ON CONFLICT (pass_id, subject_id, as_of_date) DO UPDATE SET status = excluded.status,
            seconds = excluded.seconds, load_ms = excluded.load_ms, written = excluded.written,
            sections = excluded.sections, errors = excluded.errors, recorded_at = now()
        """,
        (pass_id, item["subject_id"], item["as_of"], item["status"], item.get("seconds"), item.get("load_ms"),
         int(item.get("written") or 0), json.dumps(item.get("sections") or {}, sort_keys=True),
         json.dumps(item.get("errors") or {}, sort_keys=True)))
    connection.commit()


def _progress(phase: str, item: Mapping[str, Any], masker: Masker, **extra: Any) -> None:
    _say({"phase": phase, "subject": masker.label(item["subject_id"]), "as_of": item["as_of"],
          "status": item["status"], "written": item.get("written", 0), "seconds": item.get("seconds"),
          "errors": sorted(item.get("errors") or {}), **extra})


def _complete(connection, tenant_id: str) -> set[tuple[str, str]]:
    """Subject-dates that already hold every section of this analytics version."""
    found = {(str(row[0]), str(row[1])) for row in connection.execute(
        COMPLETE_SQL, (tenant_id, qs.QUANT_ANALYTICS_VERSION, len(qs.SECTION_NAMES))).fetchall()}
    connection.rollback()
    return found


def history_work(connection, tenant_id: str, days: int) -> list[tuple[str, str]]:
    """The NAV dates of the past `days` days of each subject the daily job builds, up to its
    latest, newest first (as the job orders a backfill)."""
    subjects = [str(row["subject_id"]) for row in connection.execute(job.SUBJECTS_SQL, (tenant_id,)).fetchall()]
    pairs = []
    for subject in subjects:
        latest = connection.execute(job.LATEST_NAV_SQL, (tenant_id, subject)).fetchone()["latest"]
        if not latest:
            continue
        since = (date.fromisoformat(str(latest)[:10]) - timedelta(days=days)).isoformat()
        pairs += [(subject, str(row["snapshot_date"])[:10])
                  for row in connection.execute(job.NAV_DATES_SQL, (tenant_id, subject, since)).fetchall()]
    connection.rollback()
    return sorted(set(pairs), key=lambda pair: (pair[1], pair[0]), reverse=True)


def _latest_pass(target: Target, tenant_id: str, environ: Mapping[str, str], masker: Masker,
                 runner: str) -> dict[str, Any]:
    """The daily job's pass, timed as the job runs it: connect, list the subjects, build each
    latest date newest first under the minutes budget, then the contract rules on those dates."""
    minutes = qs.time_limit(None, environ)
    log = _open(target, tenant_id)
    try:
        pass_id = _start_pass(log, tenant_id, "latest")
        started = _now()
        connection = _open(target, tenant_id)
        try:
            subjects = [str(row["subject_id"]) for row in connection.execute(job.SUBJECTS_SQL, (tenant_id,)).fetchall()]
            work = job._work(connection, tenant_id, subjects, None)
            built, skipped = [], []
            for subject_id, as_of in work:
                if _now() - started >= minutes * 60:
                    skipped.append([subject_id, as_of])
                    continue
                item = _build_one(connection, tenant_id, subject_id, as_of, environ)
                built.append(item)
                _record(log, pass_id, item)
                _progress("latest", item, masker)
            dates = sorted({item["as_of"] for item in built if item["status"] in ("rebuilt", "unchanged")})
            rules = qs.check_contract_rules(connection, as_of_dates=dates) if dates else []
        finally:
            connection.close()
        seconds = _now() - started
        counts = {status: sum(1 for item in built if item["status"] == status)
                  for status in ("rebuilt", "unchanged", "failed", "blocked", "held_back")}
        summary = {"minutes": minutes, "subject_dates": len(work), **counts, "skipped_for_time": skipped,
                   "section_errors": sum(1 for item in built if item["status"] in ("rebuilt", "unchanged")
                                         and item["errors"]),
                   "cold": counts["rebuilt"] > 0 and counts["unchanged"] == 0, "contract_rules": rules,
                   "runner": runner, "cpu_count": os.cpu_count()}
        summary["verdict"] = window_verdict(seconds, subject_dates=len(work), skipped_for_time=len(skipped),
                                            minutes=minutes)
        _finish_pass(log, pass_id, seconds, summary)
    finally:
        log.close()
    return summary


def _history_pass(connection, tenant_id: str, environ: Mapping[str, str], masker: Masker, *, days: int,
                  deadline: float, max_minutes: float) -> dict[str, Any]:
    pass_id = _start_pass(connection, tenant_id, "history")
    started = _now()
    work = history_work(connection, tenant_id, days)
    complete = _complete(connection, tenant_id)
    counts = {"already_built": 0, "rebuilt": 0, "unchanged": 0, "failed": 0, "blocked": 0, "held_back": 0, "left": 0}
    section_errors = 0
    for index, (subject_id, as_of) in enumerate(work, 1):
        if (subject_id, as_of) in complete:
            counts["already_built"] += 1
            continue
        if _now() >= deadline:
            counts["left"] += 1
            continue
        item = _build_one(connection, tenant_id, subject_id, as_of, environ)
        counts[item["status"]] += 1
        section_errors += bool(item["status"] in ("rebuilt", "unchanged") and item["errors"])
        _record(connection, pass_id, item)
        _progress("history", item, masker, done=index, of=len(work))
    summary = {"history_days": days, "max_minutes": max_minutes, "subject_dates": len(work), **counts,
               "section_errors": section_errors}
    _finish_pass(connection, pass_id, _now() - started, summary)
    return summary


def run(connection, target: Target, args, environ) -> int:
    if not _exists(connection, "public.quant_analytics_snapshots"):
        connection.rollback()
        print("The quant table is not on the copy: run apply-held first.", file=sys.stderr, flush=True)
        return EXIT_REFUSED
    if args.max_minutes <= 0 or not math.isfinite(args.max_minutes):
        print("--max-minutes must be a positive number", file=sys.stderr, flush=True)
        return EXIT_REFUSED
    started = _now()
    deadline = started + args.max_minutes * 60
    _ensure_ledger(connection)
    masker = _subjects_masker(connection, args.tenant)
    # Forced on here only; the publish gate is off, as the daily job's Bicep leaves it.
    build_environ = {**environ, qs.BUILD_SETTING: "on", publish_gate.MODE_ENV: "off"}
    done = connection.execute(f"SELECT count(*) FROM {SCHEMA}.passes WHERE tenant_id = ? AND phase = 'latest' "
                              "AND finished_at IS NOT NULL", (args.tenant,)).fetchone()[0]
    connection.rollback()
    latest = None
    if not done:
        latest = _latest_pass(target, args.tenant, build_environ, masker, args.runner)
        _say({"phase": "latest", "summary": {key: value for key, value in latest.items()
                                             if key not in ("skipped_for_time", "contract_rules")},
              "skipped_for_time": len(latest["skipped_for_time"]),
              "contract_rules_failed": [rule["rule"] for rule in latest["contract_rules"] if not rule.get("passed")]})
    history = _history_pass(connection, args.tenant, build_environ, masker, days=args.history_days,
                            deadline=deadline, max_minutes=args.max_minutes)
    failed = sum((latest or {}).get(key, 0) + history[key] for key in ("failed", "blocked", "held_back"))
    failed += (latest or {}).get("section_errors", 0) + history["section_errors"]
    status = "failed" if failed else "incomplete" if history["left"] else "complete"
    _say({"command": "run", "status": status, "latest_pass": "run now" if latest else "done before",
          "history": history, "elapsed_seconds": round(_now() - started, 1)})
    return EXIT_FAILED if failed else EXIT_INCOMPLETE if history["left"] else EXIT_OK


# ---------------------------------------------------------------- the review


def _broker_group(account_type: str | None, subject_type: str) -> str:
    """kiwoom, ibkr, or derived (a portfolio or composite built from accounts)."""
    if subject_type and subject_type != "physical_account":
        return "derived"
    return "kiwoom" if str(account_type or "").lower() == "kiwoom" else "ibkr"


def _rows_by_broker(connection, tenant_id: str) -> dict[str, dict[str, dict[str, int]]]:
    """Rows and unavailable rows per section for each broker group, so the Kiwoom accounts' coverage
    reads on its own (docs/QUANT_ANALYTICS.md, Kiwoom accounts)."""
    brokers = _brokers(connection, tenant_id)
    types = {str(row[0]): str(row[1]) for row in connection.execute(
        "SELECT subject_id, subject_type FROM platform_subjects WHERE tenant_id = ?", (tenant_id,)).fetchall()}
    rows = connection.execute(
        """
        SELECT subject_id, section, (payload_json->>'available') = 'true' AS available, count(*)
        FROM quant_analytics_snapshots WHERE tenant_id = ? AND analytics_version = ?
        GROUP BY 1, 2, 3
        """, (tenant_id, qs.QUANT_ANALYTICS_VERSION)).fetchall()
    connection.rollback()
    found: dict[str, dict[str, dict[str, int]]] = {}
    for subject, section, available, count in _tuples(rows):
        group = _broker_group(brokers.get(str(subject)), types.get(str(subject), "physical_account"))
        entry = found.setdefault(group, {}).setdefault(str(section), {"rows": 0, "unavailable": 0})
        entry["rows"] += int(count)
        if not available:
            entry["unavailable"] += int(count)
    return {group: dict(sorted(sections.items())) for group, sections in sorted(found.items())}


def _rows_by_section(connection, tenant_id: str) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT section, quality_status, (payload_json->>'available') = 'true' AS available, count(*)
        FROM quant_analytics_snapshots WHERE tenant_id = ? AND analytics_version = ?
        GROUP BY 1, 2, 3
        """, (tenant_id, qs.QUANT_ANALYTICS_VERSION)).fetchall()
    sections = {name: {"rows": 0, "pass": 0, "warn": 0, "fail": 0, "unavailable": 0} for name in qs.SECTION_NAMES}
    for section, quality, available, count in _tuples(rows):
        entry = sections.setdefault(str(section), {"rows": 0, "pass": 0, "warn": 0, "fail": 0, "unavailable": 0})
        entry["rows"] += int(count)
        entry[str(quality)] = entry.get(str(quality), 0) + int(count)
        if not available:
            entry["unavailable"] += int(count)
    total = {key: sum(entry[key] for entry in sections.values()) for key in ("rows", "pass", "warn", "fail",
                                                                            "unavailable")}
    return {"by_section": sections, "total": total}


def _reasons(connection, tenant_id: str, masker: Masker) -> list[dict[str, Any]]:
    """Why sections, or single engine calls inside them, are unavailable: grouped, with counts."""
    found: dict[tuple[str, str, str], int] = {}
    params = (tenant_id, qs.QUANT_ANALYTICS_VERSION)
    for section, reason, count in _tuples(connection.execute(
            "SELECT section, payload_json->>'reason', count(*) FROM quant_analytics_snapshots "
            "WHERE tenant_id = ? AND analytics_version = ? AND (payload_json->>'available') IS DISTINCT FROM 'true' "
            "GROUP BY 1, 2", params).fetchall()):
        key = (str(section), "(section)", masker.clean(reason or "no reason given"))
        found[key] = found.get(key, 0) + int(count)
    for section, call, reason, count in _tuples(connection.execute(
            "SELECT s.section, r.key, r.value->>'reason', count(*) FROM quant_analytics_snapshots s, "
            "jsonb_each(CASE WHEN jsonb_typeof(s.payload_json->'results') = 'object' THEN s.payload_json->'results' "
            "ELSE '{}'::jsonb END) r WHERE s.tenant_id = ? AND s.analytics_version = ? "
            "AND jsonb_typeof(r.value) = 'object' AND r.value->>'available' = 'false' GROUP BY 1, 2, 3",
            params).fetchall()):
        key = (str(section), str(call), masker.clean(reason or "no reason given"))
        found[key] = found.get(key, 0) + int(count)
    order = {name: index for index, name in enumerate(qs.SECTION_NAMES)}
    return [{"section": section, "call": call, "reason": reason, "rows": count}
            for (section, call, reason), count in sorted(found.items(), key=lambda item: (
                order.get(item[0][0], 99), item[0][1] != "(section)", -item[1], item[0]))]


def _coverage(connection, tenant_id: str, masker: Masker, complete: set[tuple[str, str]]) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT subject_id, count(DISTINCT as_of_date), min(as_of_date), max(as_of_date) "
        "FROM quant_analytics_snapshots WHERE tenant_id = ? AND analytics_version = ? GROUP BY 1",
        (tenant_id, qs.QUANT_ANALYTICS_VERSION)).fetchall()
    found = [{"subject": masker.label(subject), "dates": int(dates), "complete_dates":
              sum(1 for pair in complete if pair[0] == str(subject)), "first": str(first), "last": str(last)}
             for subject, dates, first, last in _tuples(rows)]
    return sorted(found, key=lambda item: item["subject"])


def _desk_views(connection) -> dict[str, Any]:
    if not all(_exists(connection, name) for name, _ in DRAFTS[1].objects):
        return {"present": False}
    found = {"present": True}
    try:
        rows, subjects = _tuples([connection.execute("SELECT count(*), count(DISTINCT subject_id) "
                                                     "FROM public.subject_live_executions").fetchone()])[0]
        found["subject_live_executions"] = {"rows": int(rows), "subjects": int(subjects)}
        found["benchmark_live_quotes"] = {"rows": int(connection.execute(
            "SELECT count(*) FROM public.benchmark_live_quotes").fetchone()[0])}
    except Exception as error:  # noqa: BLE001 - reported
        found["error"] = type(error).__name__
    connection.rollback()
    return found


def _ledger(connection, masker: Masker) -> dict[str, Any]:
    if not _exists(connection, f"{SCHEMA}.draft_events"):
        return {"events": []}
    events = [{"draft": str(row[0]), "file": str(row[1]), "action": str(row[2]), "outcome": str(row[3]),
               "sha256": str(row[4]), "seconds": float(row[5]), "error": masker.clean(row[6]) if row[6] else None}
              for row in connection.execute(
                  f"SELECT draft, file, action, outcome, sha256, seconds, error FROM {SCHEMA}.draft_events "
                  "ORDER BY event_id").fetchall()]
    return {"events": events}


def _run_log(connection, tenant_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not _exists(connection, f"{SCHEMA}.passes"):
        return [], []
    passes = [{"pass_id": str(row[0]), "phase": str(row[1]), "started_at": str(row[2]), "finished": row[3] is not None,
               "seconds": row[4], "summary": qs.decoded_payload(row[5])}
              for row in connection.execute(
                  f"SELECT pass_id, phase, started_at, finished_at, seconds, summary FROM {SCHEMA}.passes "
                  "WHERE tenant_id = ? ORDER BY started_at", (tenant_id,)).fetchall()]
    builds = [{"pass_id": str(row[0]), "phase": str(row[1]), "subject_id": str(row[2]), "as_of": str(row[3]),
               "status": str(row[4]), "seconds": row[5], "load_ms": row[6], "written": int(row[7] or 0),
               "sections": qs.decoded_payload(row[8]), "errors": qs.decoded_payload(row[9])}
              for row in connection.execute(
                  f"SELECT b.pass_id, p.phase, b.subject_id, b.as_of_date, b.status, b.seconds, b.load_ms, b.written, "
                  f"b.sections, b.errors FROM {SCHEMA}.builds b JOIN {SCHEMA}.passes p USING (pass_id) "
                  "WHERE p.tenant_id = ? ORDER BY p.started_at, b.recorded_at", (tenant_id,)).fetchall()]
    connection.rollback()
    return passes, builds


def _errors(builds: Sequence[Mapping[str, Any]], complete: set[tuple[str, str]], masker: Masker) -> list[dict]:
    found = []
    for build in builds:
        for section, message in sorted((build.get("errors") or {}).items()):
            found.append({"phase": build["phase"], "subject": masker.label(build["subject_id"]), "as_of": build["as_of"],
                          "status": build["status"], "section": str(section), "error": masker.clean(message),
                          "built_since": (build["subject_id"], build["as_of"]) in complete})
    return found


def build_review(connection, target: Target, tenant_id: str, *, history_days: int | None = None) -> dict[str, Any]:
    """Everything the review holds, as plain values: counts, dates, names and timings only."""
    masker = _subjects_masker(connection, tenant_id)
    review: dict[str, Any] = {
        "format": "quant-shadow-review", "format_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "analytics_version": qs.QUANT_ANALYTICS_VERSION,
        "masking": "Subjects appear as u01, u02, ... (sorted ids); the mapping is not written anywhere. Reasons and "
                   "errors have dates and numbers replaced. Counts only: no client figures.",
    }
    report = copy_report(connection, tenant_id, masker)
    review["copy"] = {"host": target.host, "database": target.database, "postgresql": report["server_major"],
                      "prerequisites": report["prerequisites"], "subjects": report["subjects"],
                      "inputs": report["inputs"]}
    review["held_migrations"] = {draft.name: {"number": draft.number, "state": _draft_state(connection, draft)[0]}
                                 for draft in DRAFTS}
    connection.rollback()
    ledger = _ledger(connection, masker)
    for draft in DRAFTS:
        mine = [event for event in ledger["events"] if event["draft"] == draft.name]
        review["held_migrations"][draft.name]["events"] = mine
    passes, builds = _run_log(connection, tenant_id)
    table = _exists(connection, "public.quant_analytics_snapshots")
    complete = _complete(connection, tenant_id) if table else set()
    if table:
        review["rows"] = _rows_by_section(connection, tenant_id)
        review["rows"]["by_broker"] = _rows_by_broker(connection, tenant_id)
        dates = sorted({pair[1] for pair in complete})
        review["rows"]["subject_dates_complete"] = len(complete)
        review["rows"]["subjects"] = _coverage(connection, tenant_id, masker, complete)
        review["unavailable_reasons"] = _reasons(connection, tenant_id, masker)
        all_dates = [str(row[0]) for row in connection.execute(
            "SELECT DISTINCT as_of_date FROM quant_analytics_snapshots WHERE tenant_id = ? AND analytics_version = ?",
            (tenant_id, qs.QUANT_ANALYTICS_VERSION)).fetchall()]
        connection.rollback()
        review["rows"]["first_date"], review["rows"]["last_date"] = (dates[0], dates[-1]) if dates else (None, None)
        review["contract_rules"] = qs.check_contract_rules(connection, as_of_dates=all_dates) if all_dates else []
        days = history_days or next((int(item["summary"].get("history_days") or HISTORY_DAYS) for item in reversed(passes)
                                     if item["phase"] == "history" and item["summary"]), HISTORY_DAYS)
        work = history_work(connection, tenant_id, days)
        review["history"] = {"history_days": days, "subject_dates": len(work),
                             "complete": sum(1 for pair in work if pair in complete),
                             "left": sum(1 for pair in work if pair not in complete)}
    else:
        review["rows"] = None
        review["unavailable_reasons"] = []
        review["contract_rules"] = []
        review["history"] = None
    review["timing"] = timing_summary(builds, passes)
    review["desk_views"] = _desk_views(connection)
    review["errors"] = _errors(builds, complete, masker)
    for item in passes:  # what the daily job's pass skipped for time, masked
        skipped = (item.get("summary") or {}).get("skipped_for_time") or []
        if item["phase"] == "latest" and skipped:
            review["timing"].setdefault("latest_skipped_for_time", []).extend(
                {"subject": masker.label(subject), "as_of": as_of} for subject, as_of in skipped)
    return review


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("|", "/")


def _table(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows]
    return lines


def _seconds(value: Any) -> str:
    return "-" if value is None else f"{float(value):.3f}"


def _ms(value: Any) -> str:
    return "-" if value is None else f"{float(value):.1f}"


def _duration(seconds: float) -> str:
    whole = int(math.ceil(seconds))
    return f"{whole // 60} min {whole % 60} s"


def render_markdown(review: Mapping[str, Any]) -> str:
    """The review as Markdown, plain ASCII so it survives the trip through the job's logs."""
    copy, timing, rows = review["copy"], review["timing"], review.get("rows")
    latest = timing["latest_pass"]
    out = ["# Quant analytics shadow run on a restored copy", "",
           f"Generated {review['generated_at']}. Analytics version {review['analytics_version']}. "
           f"Copy: host {copy['host']}, database {copy['database']}, PostgreSQL {copy['postgresql']}.",
           "", review["masking"], "", "## Summary", ""]
    for name, held in review["held_migrations"].items():
        applied = [event for event in held["events"] if event["action"] == "apply"]
        last = applied[-1] if applied else None
        if last and last["outcome"] == "applied":
            text = f"applied on the copy in {_seconds(last['seconds'])} s (sha256 {last['sha256'][:12]})"
        elif last:
            text = f"FAILED on the copy: {last['error']}"
        else:
            text = "not applied by this tool"
        out.append(f"- Held migration {held['number']} {name}: {text}; now {held['state']}.")
    if latest.get("measured"):
        where = (f"Started at {WINDOW_START} it would end at {latest['would_end_at']}, "
                 + (f"inside {WINDOW_START} to {WINDOW_END} with {latest['margin_seconds']:g} s to spare for the "
                    f"container's start: the job can stay at {WINDOW_START}."
                    if latest["fits_window"] else
                    f"so it does not fit {WINDOW_START} to {WINDOW_END}: the job moves to {LATER_START}."))
        out.append(f"- Daily job ({WINDOW_START} UTC): the latest-date pass took {_seconds(latest['seconds'])} s "
                   f"({_duration(latest['seconds'])}) for {latest['subject_dates']} subject-dates, "
                   f"budget {latest['budget_minutes']:g} minutes, {latest['skipped_for_time']} skipped for time. "
                   + where + ("" if latest["cold"] else " (Not a cold build: some dates were built before.)"))
    else:
        out.append("- Daily job timing: not measured (no latest-date pass has finished).")
    history = review.get("history")
    if history:
        out.append(f"- History ({history['history_days']} days): {history['complete']} of {history['subject_dates']} "
                   f"subject-dates complete" + (f", {history['left']} left (run again to carry on)."
                                                if history["left"] else "."))
    if rows:
        total = rows["total"]
        out.append(f"- Rows: {total['rows']} (pass {total['pass']}, warn {total['warn']}, fail {total['fail']}); "
                   f"unavailable sections {total['unavailable']}.")
    rules = review["contract_rules"]
    out.append(f"- Contract rules: {sum(1 for rule in rules if rule.get('passed'))} of {len(rules)} passed.")
    errors = review["errors"]
    out.append(f"- Errors: {len(errors)}" + (f" ({sum(1 for e in errors if not e['built_since'])} not built since)."
                                             if errors else "."))
    out += ["", "## Copy", ""]
    subjects = copy["subjects"]
    out += _table(("Subject type", "Lifecycle", "Subjects"),
                  ((row["subject_type"], row["lifecycle_status"], row["subjects"]) for row in subjects["by_type"]))
    out += ["", f"Built by the daily job: {subjects['eligible']} subjects ({subjects['own_pipeline']} with their own "
            "HKD pipeline, stored as unavailable).", ""]
    coverage = {item["subject"]: item for item in (rows or {}).get("subjects") or []}
    out += _table(("Subject", "Type", "Broker", "Latest NAV date", "Dates built", "Complete", "First", "Last"),
                  ((item["subject"], item["subject_type"], item.get("broker", ""), item["latest_nav_date"],
                    coverage.get(item["subject"], {}).get("dates", 0),
                    coverage.get(item["subject"], {}).get("complete_dates", 0),
                    coverage.get(item["subject"], {}).get("first"), coverage.get(item["subject"], {}).get("last"))
                   for item in subjects["latest_nav"]))
    out += [""]
    out += _table(("Input (silver and gold)", "Rows"),
                  ((name, value.get("rows", value.get("error"))) for name, value in copy["inputs"].items()))
    out += ["", "## Rows by section and quality", ""]
    if rows:
        out += _table(("Section", "Rows", "pass", "warn", "fail", "Unavailable"),
                      ((name, entry["rows"], entry["pass"], entry["warn"], entry["fail"], entry["unavailable"])
                       for name, entry in rows["by_section"].items()))
        out += ["", f"Subject-dates with every section: {rows['subject_dates_complete']}, "
                f"from {_cell(rows['first_date'])} to {_cell(rows['last_date'])}."]
        groups = rows.get("by_broker") or {}
        if groups:
            out += ["", "### Rows by broker (unavailable in brackets)", ""]
            names = sorted({section for sections in groups.values() for section in sections})
            out += _table(("Section", *groups), ((section, *(
                f"{groups[group][section]['rows']} ({groups[group][section]['unavailable']})"
                if section in groups[group] else "0" for group in groups)) for section in names))
    else:
        out.append("The quant table is not on the copy.")
    out += ["", "## Why sections are unavailable", "",
            "Dates appear as <date> and numbers as <n>. Call (section) is the whole section.", ""]
    out += _table(("Section", "Call", "Reason", "Rows"),
                  ((item["section"], item["call"], item["reason"], item["rows"])
                   for item in review["unavailable_reasons"])) if review["unavailable_reasons"] else ["None."]
    out += ["", "## Timing", "",
            f"Measured in-process on {latest.get('runner') or 'an unnamed runner'} "
            f"({_cell(latest.get('cpu_count'))} CPUs); the job's container start is not included. "
            "Cold builds only: subject-dates whose every section was built, none unchanged.", ""]
    if latest.get("measured"):
        out += _table(("Latest-date pass", "Value"), (
            ("Subject-dates", latest["subject_dates"]), ("Rebuilt", latest["rebuilt"]),
            ("Unchanged", latest["unchanged"]), ("Skipped for time", latest["skipped_for_time"]),
            ("Total seconds", _seconds(latest["seconds"])), ("Budget minutes", f"{latest['budget_minutes']:g}"),
            ("Within budget", latest["within_budget"]), ("Would end at (from 09:30:00)", latest["would_end_at"]),
            ("Seconds to spare before 09:40", f"{latest['margin_seconds']:g}"),
            ("Fits 09:30 to 09:40", latest["fits_window"]), ("Move to 10:00", latest["move_to_10_00"]),
            ("Cold", latest["cold"]), ("Passes finished", timing["latest_passes"])))
        out.append("")
    spreads = [("Per subject-date, all phases (s)", timing["per_subject_date_seconds"], _seconds),
               ("Per subject-date, latest-date pass (s)",
                latest.get("per_subject_date_seconds") or spread(()), _seconds),
               ("Load per subject-date (ms)", timing["load_ms"], _ms)]
    spreads += [(f"Section {name} (ms)", value, _ms) for name, value in timing["per_section_ms"].items()]
    out += _table(("Measure", "Count", "p50", "p95", "Max"),
                  ((label, value["count"], show(value["p50"]), show(value["p95"]), show(value["max"]))
                   for label, value, show in spreads))
    out += ["", f"History passes: {timing['history']['passes']}, {_seconds(timing['history']['seconds'])} s, "
            f"{timing['history']['rebuilt']} subject-dates rebuilt."]
    if timing.get("latest_skipped_for_time"):
        out += ["", "Skipped for time in the latest-date pass: "
                + ", ".join(f"{item['subject']} {item['as_of']}" for item in timing["latest_skipped_for_time"]) + "."]
    out += ["", "## Contract rules", ""]
    out += _table(("Rule", "Severity", "Bad rows", "Must be", "Passed"),
                  ((rule["rule"], rule["severity"], rule.get("count", rule.get("error")), rule.get("must_be"),
                    rule.get("passed")) for rule in rules)) if rules else ["Not run: no rows."]
    out += ["", "## Desk views (077)", ""]
    desk = review["desk_views"]
    if desk.get("present") and "error" not in desk:
        out += _table(("View", "Rows", "Subjects"), (
            ("subject_live_executions", desk["subject_live_executions"]["rows"],
             desk["subject_live_executions"]["subjects"]),
            ("benchmark_live_quotes", desk["benchmark_live_quotes"]["rows"], "-")))
    else:
        out.append("Not on the copy." if not desk.get("present") else f"Could not be read: {desk['error']}.")
    out += ["", "## Errors", ""]
    out += _table(("Phase", "Subject", "Date", "Status", "Section", "Error", "Built since"),
                  ((e["phase"], e["subject"], e["as_of"], e["status"], e["section"], e["error"], e["built_since"])
                   for e in errors)) if errors else ["None."]
    text = "\n".join(out) + "\n"
    return text.encode("ascii", "replace").decode("ascii")


def _write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)


def review(connection, target: Target, args, environ) -> int:
    _read_only(connection)
    found = build_review(connection, target, args.tenant)
    markdown = render_markdown(found)
    text = json.dumps(found, indent=1, sort_keys=True, ensure_ascii=True) + "\n"
    out = Path(args.out)
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_private(out / "review.md", markdown)
    _write_private(out / "review.json", text)
    _emit("md", markdown)
    _emit("json", text)
    latest = found["timing"]["latest_pass"]
    _say({"command": "review", "out": str(out), "files": ["review.md", "review.json"],
          "fits_window": latest.get("fits_window"), "move_to_10_00": latest.get("move_to_10_00"),
          "errors": len(found["errors"])})
    return EXIT_OK


# ---------------------------------------------------------------- command line


COMMANDS = {"preflight": preflight, "apply-held": apply_held, "run": run, "review": review, "undo": undo}


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=tuple(COMMANDS))
    parser.add_argument("--copy-host", required=True, help="the restored copy's host name; any other is refused")
    parser.add_argument("--copy-subnet", default=None,
                        help="the copy's subnet (10.50.11.0/27, upgrade-recovery): the host must resolve inside it")
    parser.add_argument("--url-env", default=URL_ENV, help=f"variable holding the copy's admin URL (default {URL_ENV})")
    parser.add_argument("--tenant", default=None, help="default PORTFOLIO_TENANT_ID, else the platform's tenant")
    parser.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                        help=f"run: stop starting subject-dates after this many minutes (default {DEFAULT_MAX_MINUTES:g})")
    parser.add_argument("--history-days", type=int, default=HISTORY_DAYS,
                        help=f"run: build the NAV dates of this many days before each latest date (default {HISTORY_DAYS})")
    parser.add_argument("--runner", default="local", help="run: where it runs, for the review (job, admin-vm)")
    parser.add_argument("--out", default=None, help="review: the folder for review.md and review.json")
    args = parser.parse_args(argv)
    if args.command == "review" and not args.out:
        parser.error("review needs --out DIR")
    return args


def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None) -> int:
    environ = os.environ if environ is None else environ
    args = _arguments(argv)
    args.tenant = args.tenant or environ.get("PORTFOLIO_TENANT_ID") or DEFAULT_TENANT_ID
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_MaskedFormatter("%(levelname)s %(name)s: %(message)s"))
    handler.setLevel(logging.WARNING)
    root = logging.getLogger()
    root.addHandler(handler)
    _MASK[0] = Masker()
    try:
        try:
            target = check_target(environ.get(args.url_env, ""), args.copy_host, environ=environ,
                                  copy_subnet=args.copy_subnet, url_env=args.url_env)
        except Refused as error:
            print(f"Refused: {error}. Nothing was connected.", file=sys.stderr, flush=True)
            _say({"command": args.command, "status": "refused", "reason": str(error)})
            return EXIT_REFUSED
        print(f"Restored copy: host {target.host}, database {target.database} ({args.command})", flush=True)
        try:
            connection = _open(target, args.tenant)
        except Exception as error:  # noqa: BLE001 - the copy cannot be reached from here
            print(f"Could not connect to {target.host}: {type(error).__name__}: {_MASK[0].text(error)}",
                  file=sys.stderr, flush=True)
            return EXIT_REFUSED
        try:
            return COMMANDS[args.command](connection, target, args, environ)
        except Exception:  # noqa: BLE001 - logged through the masked handler: a message could name a subject
            logging.getLogger("quant_shadow_run").exception("%s failed on the copy", args.command)
            return EXIT_FAILED
        finally:
            connection.close()
    finally:
        root.removeHandler(handler)


if __name__ == "__main__":
    raise SystemExit(main())
