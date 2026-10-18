"""Lineage records: what each job run read and wrote, in the shape of OpenLineage run events.

Off unless LINEAGE_RECORDING=true. A job wraps its work in run():

    with lineage.run("ibkrv2stg-benchmark-daily", connection=connection) as record:
        ...
        record.output("silver.benchmark_prices", rows=n)

Each recorded run leaves, in its own short transactions on its own connection:
- a START row in lineage_events when it begins, and one COMPLETE, FAIL or ABORT row when it
  ends, with the job, the code version and the container job execution;
- a lineage_io row for each dataset it read or wrote: tables as postgres://<schema>.<table>
  with a row count where the job knows it, files as blob://<container>/<name> with their
  sha256. Raw market-data files stored through raw_capture are added as inputs by themselves.

With connection given, the job's database session also carries the run id (the setting
lineage.run_id), and migration 071's trigger stamps it on every gold row that session writes
(lineage_run_id). trace() walks back from such a row to the runs and raw files behind it.

Recording never fails or slows the job:
- every public function and method swallows its own errors, and run() never changes the
  job's exception or result;
- each write runs in a daemon thread that the job waits on for at most WRITE_WAIT_SECONDS,
  and all waits in one process share LINEAGE_BUDGET_SECONDS (10 by default);
- the first error or timeout logs one warning and switches recording off for the rest of
  the process.
The code version is LINEAGE_CODE_VERSION when set (for example the image digest), otherwise
a sha256 of the ibkr_etl package files, which source_fingerprint() can recompute from a checkout.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import unquote, urlsplit

from . import raw_capture

LOGGER = logging.getLogger(__name__)

SWITCH_ENV = "LINEAGE_RECORDING"
CODE_VERSION_ENV = "LINEAGE_CODE_VERSION"
BUDGET_ENV = "LINEAGE_BUDGET_SECONDS"
DATABASE_ENV = "DATABASE_URL"
TENANT_ENV = "PORTFOLIO_TENANT_ID"
EXECUTION_ENV = "CONTAINER_APP_JOB_EXECUTION_NAME"
JOB_ENV = "CONTAINER_APP_JOB_NAME"
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_BUDGET_SECONDS = 10.0
WRITE_WAIT_SECONDS = 5.0
CONNECT_TIMEOUT_SECONDS = 3
STATEMENT_TIMEOUT_MS = 3000
FINGERPRINT_SECONDS = 2.0
NAMESPACE = "wealthryse"
PRODUCER = "ibkr_etl.lineage/1"
SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"
SESSION_SETTING = "lineage.run_id"
MAX_DATASETS = 5000
MAX_FACET_TEXT = 200
MAX_LIST_FACET = 500
PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("IBKR_PROJECT_ROOT", PACKAGE_ROOT.parents[1])).resolve()

_TABLE = re.compile(r"(bronze|silver|gold|public)\.[a-z_][a-z0-9_]{0,62}")
_BLOB = re.compile(r"[a-z0-9][a-z0-9-]{1,62}/[^\s\x00]{1,1024}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    """True only when LINEAGE_RECORDING is exactly true (any case)."""
    try:
        environ = os.environ if environ is None else environ
        return str(environ.get(SWITCH_ENV, "")).strip().lower() == "true"
    except Exception:  # noqa: BLE001
        return False


def dataset(name: str) -> tuple[str, str] | None:
    """(namespace, name) for 'silver.trades', 'postgres://silver.trades' or
    'blob://<container>/<path>'; None for anything else."""
    text = str(name).strip()
    if text.startswith("postgres://"):
        text = text[len("postgres://"):]
    elif text.startswith("blob://"):
        path = text[len("blob://"):]
        return ("blob", path) if _BLOB.fullmatch(path) else None
    return ("postgres", text) if _TABLE.fullmatch(text) else None


def uri(namespace: str, name: str) -> str:
    return f"{namespace}://{name}"


def blob_dataset(location: Any) -> str | None:
    """'blob://<container>/<path>' for an Azure blob URL (as upload_bytes returns it);
    None for a local file path or anything else."""
    try:
        parts = urlsplit(str(location or ""))
        if parts.scheme != "https" or not parts.netloc.endswith(".blob.core.windows.net"):
            return None
        path = unquote(parts.path).lstrip("/")
        return "blob://" + path if _BLOB.fullmatch(path) else None
    except Exception:  # noqa: BLE001
        return None


# Process-wide time budget and switch ---------------------------------------------------

class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.spent = 0.0
        self.reserved = 0.0
        self.disabled: str | None = None
        self.budget: float | None = None
        self.warned: set[str] = set()


_STATE = _State()


def reset() -> None:
    """Forget the budget and any earlier failure (tests, or a new job in the same process)."""
    global _STATE
    _STATE = _State()


def _budget() -> float:
    if _STATE.budget is None:
        try:
            _STATE.budget = max(0.0, float(os.environ.get(BUDGET_ENV) or DEFAULT_BUDGET_SECONDS))
        except ValueError:
            _STATE.budget = DEFAULT_BUDGET_SECONDS
    return _STATE.budget


def _warn_once(key: str, message: str, *args: Any) -> None:
    try:
        with _STATE.lock:
            first = key not in _STATE.warned
            _STATE.warned.add(key)
        if first:
            LOGGER.warning(message, *args)
    except Exception:  # noqa: BLE001
        pass


def _disable(reason: str) -> None:
    with _STATE.lock:
        if _STATE.disabled is None:
            _STATE.disabled = reason
            first = True
        else:
            first = False
    if first:
        LOGGER.warning("Lineage recording is off for the rest of this run: %s", reason)


def disabled_reason() -> str | None:
    return _STATE.disabled


def _bounded(work: Callable[[], None]) -> bool:
    """Run work in a daemon thread and wait for it within the budget. False on any error,
    timeout or spent budget; the first of those switches recording off."""
    with _STATE.lock:
        if _STATE.disabled is not None:
            return False
        left = _budget() - _STATE.spent - _STATE.reserved
        claim = min(WRITE_WAIT_SECONDS, left)
        if claim <= 0:
            exhausted = True
        else:
            exhausted = False
            _STATE.reserved += claim
    if exhausted:
        _disable("time budget of %g seconds used" % _budget())
        return False
    outcome: dict[str, BaseException] = {}
    done = threading.Event()

    def target() -> None:
        try:
            work()
        except BaseException as error:  # noqa: BLE001 - handed back to the caller
            outcome["error"] = error
        finally:
            done.set()

    started = time.monotonic()
    try:
        threading.Thread(target=target, name="lineage-write", daemon=True).start()
        finished = done.wait(claim)
    finally:
        with _STATE.lock:
            _STATE.reserved = max(0.0, _STATE.reserved - claim)
            _STATE.spent += time.monotonic() - started
    if not finished:
        _disable("the database did not answer within %.1f seconds" % claim)
        return False
    if "error" in outcome:
        error = outcome["error"]
        _disable(f"{type(error).__name__}: {' '.join(str(error).split())[:MAX_FACET_TEXT]}")
        return False
    return True


# Code version -------------------------------------------------------------------------

_FINGERPRINT: dict[str, str | None] = {}


def source_fingerprint(root: Path | None = None) -> str | None:
    """sha256 over the package's files (path and content), skipping caches. None if it
    takes longer than FINGERPRINT_SECONDS or fails."""
    root = Path(root or PACKAGE_ROOT)
    key = str(root)
    if key in _FINGERPRINT:
        return _FINGERPRINT[key]
    value: str | None = None
    try:
        deadline = time.monotonic() + FINGERPRINT_SECONDS
        digest = hashlib.sha256()
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            relative = path.relative_to(root).as_posix()
            if "__pycache__" in relative.split("/") or path.suffix in {".pyc", ".pyo"}:
                continue
            digest.update(relative.encode("utf-8") + b"\0" + hashlib.sha256(path.read_bytes()).digest())
            if time.monotonic() > deadline:
                raise TimeoutError("fingerprint took too long")
        value = digest.hexdigest()
    except Exception:  # noqa: BLE001
        value = None
    _FINGERPRINT[key] = value
    return value


def _entry_script() -> dict[str, str]:
    try:
        path = Path(sys.argv[0]).resolve()
        if not path.is_file():
            return {}
        try:
            name = path.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            name = path.name
        return {"entry_script": name, "entry_script_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    except Exception:  # noqa: BLE001
        return {}


def code_facets(environ: Mapping[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """(code version, details). LINEAGE_CODE_VERSION wins; otherwise source-sha256:<fingerprint>."""
    environ = os.environ if environ is None else environ
    fingerprint = source_fingerprint()
    details = {**({"source_sha256": fingerprint} if fingerprint else {}), **_entry_script()}
    explicit = str(environ.get(CODE_VERSION_ENV, "")).strip()[:MAX_FACET_TEXT]
    version = explicit or (f"source-sha256:{fingerprint}" if fingerprint else "unknown")
    return version, details


# Database writes ----------------------------------------------------------------------

_EVENT_SQL = """
    INSERT INTO public.lineage_events(tenant_id, run_id, event_type, event_time, job_namespace,
        job_name, code_version, execution, producer, facets)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
"""
_IO_SQL = """
    INSERT INTO public.lineage_io(tenant_id, run_id, direction, namespace, name, row_count, sha256, facets)
    SELECT %s, %s, d.direction, d.namespace, d.name, d.row_count, d.sha256, COALESCE(d.facets, '{}'::jsonb)
    FROM jsonb_to_recordset(%s::jsonb)
        AS d(direction text, namespace text, name text, row_count bigint, sha256 text, facets jsonb)
"""


def _execute(database_url: str, tenant_id: str, statements: list[tuple[str, tuple]]) -> None:
    """One short transaction on a new connection; committed only if every statement succeeds."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(database_url)
    params["options"] = " ".join(filter(None, [
        str(params.get("options") or "").strip(),
        f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
        "-c lock_timeout=1000",
        "-c idle_in_transaction_session_timeout=10000",
        f"-c app.tenant_id={tenant_id}",
    ]))
    params["connect_timeout"] = str(CONNECT_TIMEOUT_SECONDS)
    params.setdefault("application_name", "lineage-recorder")
    with psycopg.connect(**params) as connection:
        for sql, args in statements:
            connection.execute(sql, args)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Runs ---------------------------------------------------------------------------------

class Run:
    """One job run. Inactive (every method a no-op, id None) unless recording is on and the
    START event was stored."""

    def __init__(self, job_name: str, *, namespace: str = NAMESPACE, database_url: str = "",
                 tenant_id: str = DEFAULT_TENANT_ID, facets: Mapping[str, Any] | None = None) -> None:
        self.job_name = str(job_name)[:MAX_FACET_TEXT]
        self.namespace = str(namespace)[:MAX_FACET_TEXT]
        self.run_id = str(uuid.uuid4())
        self.started_at = _now()
        self.database_url = database_url
        self.tenant_id = tenant_id
        self.status: str | None = None
        self.active = False
        self._facets: dict[str, Any] = dict(facets or {})
        self._datasets: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._dropped = 0
        self._lock = threading.Lock()
        self._remove_observer: Callable[[], None] | None = None

    @property
    def id(self) -> str | None:
        """The run id, only while the run is recorded (its START event is stored)."""
        return self.run_id if self.active else None

    # What the job reads and writes ----------------------------------------------------

    def input(self, name: str, *, rows: int | None = None, sha256: str | None = None, **facets: Any) -> None:
        self._add("input", name, rows, sha256, facets)

    def output(self, name: str, *, rows: int | None = None, sha256: str | None = None, **facets: Any) -> None:
        self._add("output", name, rows, sha256, facets)

    def facet(self, **values: Any) -> None:
        """Extra run facts for the terminal event, such as ingestion_run_ids."""
        if not self.active:
            return
        try:
            with self._lock:
                self._facets.update(values)
        except Exception:  # noqa: BLE001
            pass

    def append(self, key: str, value: Any) -> None:
        """Add one value to a list facet (at most MAX_LIST_FACET values are kept)."""
        if not self.active:
            return
        try:
            with self._lock:
                values = self._facets.setdefault(str(key)[:60], [])
                if isinstance(values, list) and len(values) < MAX_LIST_FACET and value not in values:
                    values.append(value)
        except Exception:  # noqa: BLE001
            pass

    def exit_code(self, code: Any) -> None:
        """A job's own result: anything but 0 or None makes the run FAIL."""
        if not self.active:
            return
        try:
            with self._lock:
                self._facets["exit_code"] = code
                if code not in (0, None) and self.status is None:
                    self.status = "FAIL"
        except Exception:  # noqa: BLE001
            pass

    def bind(self, connection: Any) -> bool:
        """Put the run id on the job's PostgreSQL session, so the gold rows it writes carry it.

        Only an idle session is changed, and the setting is committed at once, so the job's
        own transactions never include it. False if not recorded, not PostgreSQL, or busy."""
        if not self.active:
            return False
        try:
            raw = getattr(connection, "_connection", connection)
            info = getattr(raw, "info", None)
            if info is None or not hasattr(raw, "execute"):
                return False
            from psycopg import pq

            if info.transaction_status != pq.TransactionStatus.IDLE:
                _warn_once("bind-busy", "Lineage did not mark a database session that was in a transaction")
                return False
            raw.execute("SELECT set_config(%s, %s, false)", (SESSION_SETTING, self.run_id))
            if not raw.autocommit:
                raw.commit()
            with self._lock:
                self._facets["bound_sessions"] = int(self._facets.get("bound_sessions", 0)) + 1
            return True
        except Exception as error:  # noqa: BLE001
            _warn_once("bind", "Lineage could not mark the job's database session: %s", type(error).__name__)
            return False

    # Internals ------------------------------------------------------------------------

    def _add(self, direction: str, name: str, rows: Any, sha256: Any, facets: Mapping[str, Any]) -> None:
        if not self.active:
            return
        try:
            parsed = dataset(name)
            if parsed is None:
                _warn_once("dataset:" + str(name)[:80], "Lineage ignored a dataset name it does not know: %s",
                           str(name)[:80])
                return
            count = None if rows is None or isinstance(rows, bool) else max(0, int(rows))
            digest = str(sha256).lower() if sha256 and _SHA256.fullmatch(str(sha256).lower()) else None
            extra = {str(k)[:60]: (v if isinstance(v, (int, float, bool)) or v is None else str(v)[:MAX_FACET_TEXT])
                     for k, v in facets.items()}
            key = (direction, *parsed)
            with self._lock:
                entry = self._datasets.get(key)
                if entry is None:
                    if len(self._datasets) >= MAX_DATASETS:
                        self._dropped += 1
                        return
                    self._datasets[key] = {"row_count": count, "sha256": digest, "facets": extra}
                    return
                if count is not None:
                    entry["row_count"] = count if entry["row_count"] is None else entry["row_count"] + count
                entry["sha256"] = entry["sha256"] or digest
                entry["facets"].update(extra)
        except Exception:  # noqa: BLE001
            pass

    def _captured(self, record: Mapping[str, Any]) -> None:
        container, blob = record.get("container"), record.get("blob")
        if container and blob:
            self.input(f"blob://{container}/{blob}", sha256=record.get("sha256"), bytes=record.get("bytes"),
                       feed=record.get("feed"), provider=record.get("provider"),
                       request_key=record.get("request_key"), outcome=record.get("outcome"))

    def _start(self) -> None:
        version, details = code_facets()
        execution = os.environ.get(EXECUTION_ENV, "").strip()[:MAX_FACET_TEXT] or None
        facets = {"code": details, "schema_url": SCHEMA_URL,
                  **({"container_app_job": os.environ[JOB_ENV][:MAX_FACET_TEXT]} if os.environ.get(JOB_ENV) else {})}
        self._code_version = version
        self._execution = execution
        args = (self.tenant_id, self.run_id, "START", self.started_at, self.namespace, self.job_name,
                version, execution, PRODUCER, _json(facets))
        if _bounded(lambda: _execute(self.database_url, self.tenant_id, [(_EVENT_SQL, args)])):
            self.active = True
            self._remove_observer = raw_capture.add_observer(self._captured)

    def _finish(self, error: BaseException | None) -> None:
        if not self.active:
            return
        try:
            if self._remove_observer is not None:
                self._remove_observer()
            with self._lock:
                if error is not None:
                    if isinstance(error, SystemExit) and error.code in (0, None):
                        status = self.status or "COMPLETE"
                    elif isinstance(error, (KeyboardInterrupt, GeneratorExit)):
                        status = "ABORT"
                    else:
                        status = "FAIL"
                    self._facets["error"] = {"type": type(error).__name__}
                else:
                    status = self.status or "COMPLETE"
                self.status = status
                if self._dropped:
                    self._facets["datasets_not_recorded"] = self._dropped
                facets = dict(self._facets)
                datasets = [{"direction": d, "namespace": ns, "name": n, **entry}
                            for (d, ns, n), entry in sorted(self._datasets.items())]
            event = (self.tenant_id, self.run_id, status, _now(), self.namespace, self.job_name,
                     self._code_version, self._execution, PRODUCER, _json(facets))
            statements = [(_EVENT_SQL, event)]
            if datasets:
                statements.append((_IO_SQL, (self.tenant_id, self.run_id, _json(datasets))))
            _bounded(lambda: _execute(self.database_url, self.tenant_id, statements))
        except Exception as failure:  # noqa: BLE001
            _disable(f"{type(failure).__name__} while finishing a run")
        finally:
            self.active = False


# The tables each wired job reads, recorded as inputs of its runs. Raw files are added as
# they are read, and outputs by the job from what it wrote. Names are catalog job names.
JOB_INPUTS: dict[str, tuple[str, ...]] = {
    "ibkrv2stg-institutional-daily": (),
    "ibkrv2stg-benchmark-daily": (
        "silver.nav_snapshots", "silver.derived_portfolio_nav", "bronze.flex_rows", "silver.fx_rates",
        "silver.derived_portfolio_cash_ledger", "silver.position_snapshots", "silver.derived_portfolio_positions",
        "silver.instruments"),
    "ibkrv2stg-sector-classification": (
        "silver.position_snapshots", "silver.derived_portfolio_positions", "silver.instruments"),
    "ibkrv2stg-risk-free-daily": (),
    "ibkrv2stg-crisis-scenarios-daily": (
        "silver.nav_snapshots", "silver.derived_portfolio_nav", "silver.position_snapshots",
        "silver.derived_portfolio_positions", "silver.synthetic_portfolio_components", "silver.instruments"),
    "ibkrv2stg-derived-daily": (
        "silver.accounts", "silver.instruments", "silver.trades", "silver.cash_transactions",
        "silver.corporate_actions", "silver.position_snapshots", "silver.cash_snapshots", "silver.nav_snapshots",
        "silver.fx_rates", "silver.fx_transactions", "silver.instrument_classifications", "silver.risk_free_rates"),
    "ibkrv2stg-risk-snapshots": (
        "gold.performance_comparison_daily", "gold.crisis_replay_daily", "gold.crisis_replay_components",
        "silver.nav_snapshots", "silver.derived_portfolio_nav", "silver.position_snapshots",
        "silver.derived_portfolio_positions", "silver.instruments", "silver.instrument_classifications",
        "silver.risk_free_rates", "silver.reference_fx_rates", "silver.benchmark_prices"),
}


def _new_run(job_name: str, *, namespace: str, database_url: str | None, tenant_id: str | None,
             facets: Mapping[str, Any] | None) -> Run:
    try:
        url = database_url if database_url is not None else os.environ.get(DATABASE_ENV, "")
        tenant = str(uuid.UUID(str(tenant_id or os.environ.get(TENANT_ENV) or DEFAULT_TENANT_ID)))
        record = Run(job_name, namespace=namespace, database_url=url, tenant_id=tenant, facets=facets)
        if enabled() and url.startswith(("postgres://", "postgresql://")) and _STATE.disabled is None:
            record._start()
            for name in JOB_INPUTS.get(record.job_name, ()):
                record.input(name)
        return record
    except Exception as error:  # noqa: BLE001
        _warn_once("start", "Lineage could not start a run record: %s", type(error).__name__)
        return Run(str(job_name))


_CURRENT: list[Run] = []


def current() -> Run:
    """The innermost run() block in progress, or an inactive run whose methods do nothing."""
    try:
        return _CURRENT[-1]
    except IndexError:
        return Run("none")


@contextlib.contextmanager
def run(job_name: str, *, connection: Any = None, namespace: str = NAMESPACE, database_url: str | None = None,
        tenant_id: str | None = None, facets: Mapping[str, Any] | None = None) -> Iterator[Run]:
    """Record one job run around the block. Yields a Run whose methods never raise.

    The job's own exception passes through unchanged and makes the run FAIL (ABORT for an
    interrupt). With connection, the job's PostgreSQL session is marked (see Run.bind)."""
    record = _new_run(job_name, namespace=namespace, database_url=database_url, tenant_id=tenant_id,
                      facets=facets)
    if connection is not None:
        record.bind(connection)
    _CURRENT.append(record)
    try:
        yield record
    except BaseException as error:
        record._finish(error)
        raise
    else:
        record._finish(None)
    finally:
        with contextlib.suppress(ValueError):
            _CURRENT.remove(record)


def run_main(job_name: str, main: Callable[[], Any]) -> Any:
    """Run a job's main() inside run(job_name) and return its result unchanged; a non-zero
    result makes the run FAIL. Inside, current() is this run, so main can bind its
    connection and record outputs."""
    with run(job_name) as record:
        code = main()
        record.exit_code(code)
    return code


# Reading lineage back -----------------------------------------------------------------

# Tables trace() starts from, with the key columns that pick one row (the tenant comes
# from the session) and the column that references their lineage.
TRACEABLE = {
    "gold.risk_analytics_snapshots": (("subject_id", "as_of_date", "analytics_version"), "lineage_run_id"),
    "gold.performance_comparison_daily": (
        ("subject_id", "benchmark_id", "observation_date", "calculation_version"), "lineage_run_id"),
    "gold.crisis_scenario_results": (
        ("subject_id", "holdings_as_of", "scenario_id", "calculation_version"), "lineage_run_id"),
    "silver.benchmark_prices": (("benchmark_id", "observation_date", "observation_status"), "raw_sha256"),
}


def _raw(connection: Any) -> Any:
    return getattr(connection, "_connection", connection)


def _rows(connection: Any, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    cursor = _raw(connection).execute(sql, args)
    names = [column.name for column in cursor.description or ()]
    # Rows may be tuples, dicts or the jobs' HybridRow (a mapping that iterates its names).
    return [{name: row[name] for name in names} if isinstance(row, Mapping) else dict(zip(names, tuple(row)))
            for row in cursor.fetchall()]


def trace(connection: Any, table: str, key: Mapping[str, Any], *, max_depth: int = 6) -> dict[str, Any]:
    """Walk back from one gold row (or a benchmark price) to the runs and raw files behind it.

    The first run comes from the row itself: its lineage_run_id, or for benchmark_prices the
    run that read the raw file named by raw_sha256 (basis 'row'). Each run's table inputs
    lead to the latest run that wrote that table and finished before it started (basis
    'dataset-time', an inference). Blob inputs are the raw files; IBKR runs also list the
    ingestion runs, whose raw_sha256 and raw_path name the statement. Read-only."""
    name = table if "." in table else next((t for t in TRACEABLE if t.split(".", 1)[1] == table), table)
    if name not in TRACEABLE:
        raise ValueError(f"trace starts from one of {sorted(TRACEABLE)}")
    columns, reference = TRACEABLE[name]
    missing = [c for c in columns if c not in key]
    if missing:
        raise ValueError(f"{name} needs {', '.join(missing)}")
    schema, relation = name.split(".", 1)
    where = " AND ".join(f"{c}::text = %s" for c in columns)
    found = _rows(connection, f"SELECT {reference} AS reference FROM {schema}.{relation} WHERE {where}",
                  tuple(str(key[c]) for c in columns))
    result: dict[str, Any] = {"table": name, "key": {c: str(key[c]) for c in columns}, "found": bool(found),
                              "runs": [], "raw_files": [], "ibkr_statements": []}
    if len(found) != 1:
        result["note"] = "no such row" if not found else "the key matches more than one row"
        return result
    reference_value = found[0]["reference"]
    result["reference"] = None if reference_value is None else str(reference_value)
    queue: list[tuple[str, int, str]] = []
    if reference == "lineage_run_id" and reference_value is not None:
        queue.append((str(reference_value), 0, "row"))
    elif reference == "raw_sha256" and reference_value:
        readers = _rows(connection, """
            SELECT io.run_id::text AS run_id FROM public.lineage_io io
            JOIN public.lineage_io o ON o.tenant_id = io.tenant_id AND o.run_id = io.run_id
                AND o.direction = 'output' AND o.namespace = 'postgres' AND o.name = %s
            JOIN public.lineage_runs r ON r.tenant_id = io.tenant_id AND r.run_id = io.run_id
            WHERE io.direction = 'input' AND io.namespace = 'blob' AND io.sha256 = %s
            ORDER BY r.ended_at DESC NULLS LAST LIMIT 1""", (name, str(reference_value)))
        queue.extend((row["run_id"], 0, "row") for row in readers)
    if not queue:
        result["note"] = "the row was written by a run that was not recorded"
        return result
    seen: set[str] = set()
    ingestion_ids: set[int] = set()
    while queue:
        run_id, depth, basis = queue.pop(0)
        if run_id in seen or depth > max_depth:
            continue
        seen.add(run_id)
        runs = _rows(connection, """
            SELECT run_id::text AS run_id, job_namespace, job_name, status, started_at, ended_at,
                   code_version, execution, facets
            FROM public.lineage_runs WHERE run_id = %s""", (run_id,))
        if not runs:
            result["runs"].append({"run_id": run_id, "depth": depth, "basis": basis, "recorded": False})
            continue
        step = {**runs[0], "depth": depth, "basis": basis, "recorded": True}
        io = _rows(connection, """
            SELECT direction, namespace, name, row_count, sha256, facets FROM public.lineage_io
            WHERE run_id = %s ORDER BY direction, namespace, name""", (run_id,))
        step["inputs"] = [dict(item, dataset=uri(item["namespace"], item["name"])) for item in io
                          if item["direction"] == "input"]
        step["outputs"] = [dict(item, dataset=uri(item["namespace"], item["name"])) for item in io
                           if item["direction"] == "output"]
        result["runs"].append(step)
        for value in (step.get("facets") or {}).get("ingestion_run_ids") or []:
            with contextlib.suppress(TypeError, ValueError):
                ingestion_ids.add(int(value))
        for item in step["inputs"]:
            if item["namespace"] == "blob":
                result["raw_files"].append({"dataset": item["dataset"], "sha256": item["sha256"],
                                            "read_by_run": run_id, "facets": item["facets"]})
                continue
            upstream = _rows(connection, """
                SELECT r.run_id::text AS run_id FROM public.lineage_io o
                JOIN public.lineage_runs r ON r.tenant_id = o.tenant_id AND r.run_id = o.run_id
                WHERE o.direction = 'output' AND o.namespace = %s AND o.name = %s
                  AND r.ended_at IS NOT NULL AND r.ended_at <= %s AND r.run_id <> %s
                ORDER BY r.ended_at DESC LIMIT 1""",
                (item["namespace"], item["name"], step["started_at"], run_id))
            for row in upstream:
                queue.append((row["run_id"], depth + 1, "dataset-time"))
    if ingestion_ids:
        result["ibkr_statements"] = _rows(connection, """
            SELECT run_id, run_type, status, requested_from, requested_to, raw_sha256, raw_path
            FROM ingestion_runs WHERE run_id = ANY(%s) ORDER BY run_id""", (sorted(ingestion_ids),))
    return result


def main(argv: list[str] | None = None) -> int:
    """python -m ibkr_etl.lineage trace <table> column=value ... (reads DATABASE_URL)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] != "trace":
        print("usage: python -m ibkr_etl.lineage trace <table> column=value ...", file=sys.stderr)
        return 2
    import psycopg

    key = dict(part.split("=", 1) for part in args[2:] if "=" in part)
    tenant = str(uuid.UUID(os.environ.get(TENANT_ENV) or DEFAULT_TENANT_ID))
    with psycopg.connect(os.environ[DATABASE_ENV], options=f"-c app.tenant_id={tenant}") as connection:
        print(json.dumps(trace(connection, args[1], key), indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
