"""The research job: point-in-time feature exports, backtests and the research server's start and stop.

Runs as the Container Apps job ibkrv2stg-research (infra/research.bicep), signed in as the
research identity, or locally with --local-dir for a dry run. docs/RESEARCH_ZONE.md has the
whole picture. Subcommands:

  export-features --as-known-on YYYY-MM-DD --subject U1234567 [--subject ...] [--dataset ...]
      Reads silver as known at the end of that day (silver.<table>_as_known for the five
      history tables) through the live server's read-only research_reader login, rebuilds
      positions, instruments and market prices by replaying bronze in the order it was
      stored, and writes Parquet with manifests to research-features (feature_store).
      Never reads the live server between 07:50 and 09:25 UTC, the daily chain's window.
  backtest --spec path.json
      Runs each parameter set of a signal (research.signals) through the backtester on
      exported prices, records every trial in the experiment log (an append blob in
      research-experiments), runs walk-forward splits and writes a report (markdown and
      JSON) with the deflated Sharpe ratio from the family's trial statistics.
  start-server / stop-server [--if-idle]
      Starts or stops the research database server (it is stopped when idle). With
      --if-idle (the nightly ibkrv2stg-research-idle-stop job) a server that another run
      is still using is left on.
  check
      Read-mostly check of the job's settings, logins and container access, for switch-on.

export-features, backtest and check start the research server first when
RESEARCH_SERVER_RESOURCE_ID is set and always stop it at the end, also when they fail
(--keep-server-running leaves it on for a run that follows straight away). If another run
recorded in research.job_runs is still going, the server is left on for it and that run
stops it when it ends.

Environment (set by infra/research.bicep):
  RESEARCH_LIVE_READER_URL     live server, research_reader login (Key Vault research-live-reader-url)
  RESEARCH_DATABASE_URL        research server, research_job login (Key Vault research-db-url)
  RESEARCH_SERVER_RESOURCE_ID  the research server, for start and stop
  AZURE_STORAGE_ACCOUNT_URL    the storage account; AZURE_CLIENT_ID the research identity
  PORTFOLIO_TENANT_ID          the tenant whose rows the logins see (row security)
  RESEARCH_CODE_VERSION        the image's source commit, recorded with every trial
  RESEARCH_MAX_RUN_SECONDS     a run older than this no longer keeps the server on (the job's time limit)
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import logging
import math
import os
import re
import signal as signals_module
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time as clock_time, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ibkr_etl.quant import skill_statistics  # noqa: E402
from ibkr_etl.research import backtester, blob_store, experiment_log, feature_store, signals, validation  # noqa: E402

LOGGER = logging.getLogger("research_job")
DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"
READER_LOGIN = "research_reader"
JOB_LOGIN = "research_job"
DAILY_WINDOW = (clock_time(7, 50), clock_time(9, 25))
SERVER_ID = re.compile(r"^/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[\w.()-]+/providers/"
                       r"Microsoft\.DBforPostgreSQL/flexibleServers/[a-z0-9-]{3,63}$")
ARM = "https://management.azure.com"
ARM_API_VERSION = "2024-08-01"
# The job's replica time limit (infra/research.bicep). A run still marked running after this
# was cut off, so it no longer keeps the server on.
MAX_RUN_SECONDS = 4 * 3600
REPORT_DISCLAIMER = ("Research only. Nothing here is shown to clients or used by the website or the agents. A result "
                     "reaches people only as a gold certified figure, through the publish gate, with its model card, "
                     "validation and the owner's approval.")
# Live objects the reader must never read; checked by the check subcommand where they exist.
FORBIDDEN_LIVE = ("gold.reporting_snapshots", "gold.certified_figures", "bronze.flex_rows", "bronze.raw_reports",
                  "public.audit_events", "public.ibkr_flex_credentials", "public.broker_connections")


class JobError(RuntimeError):
    """The research job cannot do what was asked; nothing was changed."""


class DailyWindowError(JobError):
    """The live server is not read during the daily chain's window."""


def in_daily_window(moment: datetime) -> bool:
    now = moment.astimezone(timezone.utc).time()
    return DAILY_WINDOW[0] <= now <= DAILY_WINDOW[1]


def refuse_daily_window(moment: datetime | None = None) -> None:
    moment = moment or datetime.now(timezone.utc)
    if in_daily_window(moment):
        raise DailyWindowError("The live server is not read between 07:50 and 09:25 UTC (the daily chain); "
                               "run again after 09:25 UTC")


def new_run_id(now: datetime | None = None) -> str:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"research-{moment:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


# --- the live server, read only ----------------------------------------------------------

class PostgresSilverReader:
    """silver.<table>_as_known(at) through the research_reader login, in one read-only snapshot."""

    def __init__(self, url: str, tenant_id: str, *, require_login: str | None = READER_LOGIN) -> None:
        login = unquote(urlsplit(url).username or "")
        if require_login and login != require_login:
            raise JobError(f"RESEARCH_LIVE_READER_URL must sign in as {require_login}, never a broader login")
        self._url = url
        self._tenant = tenant_id
        self._connection = None

    def _connect(self):
        if self._connection is None:
            import psycopg
            from psycopg.rows import dict_row

            self._connection = psycopg.connect(
                self._url, connect_timeout=20, row_factory=dict_row,
                options=f"-c app.tenant_id={self._tenant} -c default_transaction_read_only=on "
                        "-c statement_timeout=300000")
            # One snapshot for every table, so the datasets agree with each other.
            self._connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        return self._connection

    def as_known(self, table: str, at: datetime, accounts: Sequence[str]) -> list[Mapping[str, Any]]:
        from psycopg import sql

        if table not in feature_store.HISTORY_TABLES:
            raise JobError(f"{table} has no as-known history")
        connection = self._connect()
        function = f"silver.{table}_as_known(timestamptz)"
        if connection.execute("SELECT to_regprocedure(%s)", (function,)).fetchone()["to_regprocedure"] is None:
            raise JobError(f"{function} is not on the live server yet (U8 history); export the replayed datasets only")
        query = sql.SQL("SELECT * FROM silver.{}(%s) AS known WHERE known.account_id = ANY(%s)").format(
            sql.Identifier(f"{table}_as_known"))
        return [dict(row) for row in connection.execute(query, (at, list(accounts))).fetchall()]

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


# --- the research server: start, stop and the run register ---------------------------------

def _arm_request(method: str, url: str, token: str) -> tuple[int, bytes]:
    request = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


class ServerControl:
    """Start and stop the research server through Azure Resource Manager as the research identity.

    The identity's custom role on that one server allows read, start and stop only
    (infra/research.bicep). The server's own state is polled; nothing else is read.
    """

    STABLE = {"Ready", "Stopped"}

    def __init__(self, resource_id: str, *, token: Callable[[], str] | None = None,
                 request: Callable[[str, str, str], tuple[int, bytes]] = _arm_request,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 poll_seconds: float = 15.0, timeout_seconds: float = 1200.0) -> None:
        if not SERVER_ID.match(resource_id or ""):
            raise JobError("RESEARCH_SERVER_RESOURCE_ID is not a PostgreSQL flexible server resource id")
        self.resource_id = resource_id
        self._token = token or self._managed_identity_token
        self._request = request
        self._sleep, self._clock = sleep, clock
        self._poll, self._timeout = poll_seconds, timeout_seconds
        self.actions: list[str] = []

    @staticmethod
    def _managed_identity_token() -> str:
        from azure.identity import ManagedIdentityCredential

        credential = ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID", "").strip() or None)
        return credential.get_token(f"{ARM}/.default").token

    def _call(self, method: str, suffix: str = "") -> dict[str, Any]:
        url = f"{ARM}{self.resource_id}{suffix}?api-version={ARM_API_VERSION}"
        status, body = self._request(method, url, self._token())
        if status >= 400:
            raise JobError(f"Azure answered {status} to {method} {suffix or 'server'}: {body[:300]!r}")
        return json.loads(body.decode("utf-8")) if body.strip() else {}

    def state(self) -> str:
        return str(self._call("GET").get("properties", {}).get("state") or "Unknown")

    def _wait(self, wanted: set[str]) -> str:
        deadline = self._clock() + self._timeout
        while True:
            state = self.state()
            if state in wanted or state in {"Disabled", "Dropping"}:
                return state
            if self._clock() > deadline:
                raise JobError(f"The research server stayed {state} for {self._timeout:.0f} seconds")
            self._sleep(self._poll)

    def start(self) -> str:
        state = self._wait(self.STABLE)
        if state == "Stopped":
            self._call("POST", "/start")
            self.actions.append("start")
            state = self._wait({"Ready"})
        if state != "Ready":
            raise JobError(f"The research server is {state}, not Ready")
        return state

    def stop(self) -> str:
        state = self._wait(self.STABLE)
        if state == "Ready":
            self._call("POST", "/stop")
            self.actions.append("stop")
            state = self._wait({"Stopped"})
        if state != "Stopped":
            raise JobError(f"The research server is {state}, not Stopped")
        return state


class NullRegister:
    """No research database configured (a local run): nothing is recorded there."""

    enabled = False

    def started(self, run_id: str, command: str, code_version: str, detail: Mapping[str, Any]) -> None: ...
    def finished(self, run_id: str, status: str, detail: Mapping[str, Any]) -> None: ...
    def export_recorded(self, run_id: str, export: Mapping[str, Any]) -> None: ...

    def running(self, *, other_than: str | None, within_seconds: int) -> int:
        return 0

    def check(self) -> dict[str, Any]:
        return {"configured": False}


class DatabaseRegister:
    """research.job_runs and research.feature_exports on the research server, as research_job."""

    enabled = True

    def __init__(self, url: str, tenant_id: str) -> None:
        if unquote(urlsplit(url).username or "") != JOB_LOGIN:
            raise JobError(f"RESEARCH_DATABASE_URL must sign in as {JOB_LOGIN}")
        self._url, self._tenant = url, tenant_id

    def _run(self, statement: str, params: Sequence[Any] = ()) -> list[tuple]:
        import psycopg

        with psycopg.connect(self._url, connect_timeout=30, options=f"-c app.tenant_id={self._tenant}") as connection:
            cursor = connection.execute(statement, params)
            return cursor.fetchall() if cursor.description else []

    def started(self, run_id: str, command: str, code_version: str, detail: Mapping[str, Any]) -> None:
        self._run("INSERT INTO research.job_runs (run_id, tenant_id, command, started_at, status, code_version, detail) "
                  "VALUES (%s, %s, %s, now(), 'running', %s, %s::jsonb)",
                  (run_id, self._tenant, command, code_version, json.dumps(feature_store.plain(detail))))

    def finished(self, run_id: str, status: str, detail: Mapping[str, Any]) -> None:
        self._run("UPDATE research.job_runs SET finished_at = now(), status = %s, detail = detail || %s::jsonb "
                  "WHERE run_id = %s", (status, json.dumps(feature_store.plain(detail)), run_id))

    def export_recorded(self, run_id: str, export: Mapping[str, Any]) -> None:
        for dataset, item in export["datasets"].items():
            self._run("INSERT INTO research.feature_exports (tenant_id, dataset, as_known_on, subjects, manifest_path, "
                      "rows_sha256, row_count, run_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                      "ON CONFLICT DO NOTHING",
                      (self._tenant, dataset, export["as_known_on"], feature_store.subjects_key(export["subjects"]),
                       item["manifest"], item["rows_sha256"], item["rows"], run_id))

    def running(self, *, other_than: str | None, within_seconds: int) -> int:
        """How many other runs of this tenant are still going (started within the time limit)."""
        rows = self._run("SELECT count(*) FROM research.job_runs WHERE status = 'running' AND run_id <> %s "
                         "AND started_at > now() - make_interval(secs => %s)", (other_than or "", int(within_seconds)))
        return int(rows[0][0])

    def check(self) -> dict[str, Any]:
        row = self._run("SELECT current_user, has_schema_privilege(current_user, 'research', 'USAGE'), "
                        "has_schema_privilege(current_user, 'research', 'CREATE'), "
                        "has_database_privilege(current_user, current_database(), 'CREATE')")[0]
        return {"configured": True, "login": row[0], "research_usage": row[1], "research_create": row[2],
                "database_create": row[3], "ok": row[0] == JOB_LOGIN and row[1] and not row[2] and not row[3]}


# --- where files go ------------------------------------------------------------------------

@dataclass
class Context:
    """Settings and storage for one run. Tests replace the sources with in-memory ones."""

    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    local_dir: Path | None = None
    run_id: str = field(default_factory=new_run_id)
    silver: Any = None
    bronze: Any = None
    register: Any = None
    server: Any = None
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    @property
    def tenant_id(self) -> str:
        return self.environ.get("PORTFOLIO_TENANT_ID", "").strip() or DEFAULT_TENANT

    @property
    def code_version(self) -> str:
        return self.environ.get("RESEARCH_CODE_VERSION", "").strip() or "unknown"

    @property
    def max_run_seconds(self) -> int:
        value = self.environ.get("RESEARCH_MAX_RUN_SECONDS", "").strip()
        return int(value) if value.isdigit() and int(value) > 0 else MAX_RUN_SECONDS

    def _container(self, name: str):
        return blob_store.container(name, self.environ.get(blob_store.ACCOUNT_URL_ENV, ""))

    def features(self):
        if self.local_dir is not None:
            return blob_store.DirectoryFiles(self.local_dir / "features")
        return blob_store.BlobFiles(self._container(blob_store.FEATURES_CONTAINER))

    def experiments(self):
        if self.local_dir is not None:
            return blob_store.DirectoryFiles(self.local_dir / "experiments")
        return blob_store.BlobFiles(self._container(blob_store.EXPERIMENTS_CONTAINER))

    def experiment_store(self):
        if self.local_dir is not None:
            return blob_store.FileLineStore(self.local_dir / "experiments" / experiment_log.LOG_PATH)
        return blob_store.AppendBlobStore(self._container(blob_store.EXPERIMENTS_CONTAINER))

    def silver_reader(self):
        if self.silver is None:
            url = self.environ.get("RESEARCH_LIVE_READER_URL", "").strip()
            if not url:
                raise JobError("RESEARCH_LIVE_READER_URL is not set, so silver cannot be read")
            self.silver = PostgresSilverReader(url, self.tenant_id)
        return self.silver

    def bronze_source(self, *, include_v11_archive: bool = False):
        if self.bronze is None:
            self.bronze = blob_store.BlobBronzeSource(
                self._container(blob_store.RAW_STATEMENTS_CONTAINER), self._container(blob_store.MARKET_DATA_CONTAINER),
                tenant_id=self.tenant_id, include_v11_archive=include_v11_archive)
        return self.bronze

    def run_register(self):
        if self.register is None:
            url = self.environ.get("RESEARCH_DATABASE_URL", "").strip()
            self.register = DatabaseRegister(url, self.tenant_id) if url and self.local_dir is None else NullRegister()
        return self.register

    def server_control(self):
        if self.server is None:
            resource = self.environ.get("RESEARCH_SERVER_RESOURCE_ID", "").strip()
            self.server = ServerControl(resource) if resource else False
        return self.server or None


# --- export-features -----------------------------------------------------------------------

def command_export(args: argparse.Namespace, context: Context) -> dict[str, Any]:
    datasets = tuple(args.dataset or feature_store.DATASETS)
    reads_live = any(name in feature_store.HISTORY_TABLES for name in datasets)
    if reads_live:
        refuse_daily_window(context.now())
    request = feature_store.ExportRequest(
        as_known_on=args.as_known_on, subjects=tuple(args.subject or ()), tenant_id=context.tenant_id,
        run_id=context.run_id, code_version=context.code_version, datasets=datasets)
    silver = context.silver_reader() if reads_live else feature_store.MemorySilverReader()
    bronze = context.bronze_source(include_v11_archive=args.include_v11_archive) \
        if set(datasets) & set(feature_store.REPLAYED) else feature_store.MemoryBronzeSource()

    def before_read(dataset: str) -> None:
        if dataset in feature_store.HISTORY_TABLES:
            refuse_daily_window(context.now())

    # In Azure the files are the point, so a missing pyarrow fails the run. A local dry run
    # without pyarrow writes the manifests only.
    write_parquet = context.local_dir is None or feature_store.parquet_available()
    try:
        export = feature_store.export_features(request, silver=silver, bronze=bronze, sink=context.features(),
                                               write_parquet=write_parquet, now=context.now(), before_read=before_read)
    finally:
        if hasattr(silver, "close"):
            silver.close()
    context.run_register().export_recorded(context.run_id, export)
    return {"command": "export-features", "manifest": export["manifest"], "outcome": export["outcome"],
            "fingerprint": export["fingerprint"], "parquet_written": write_parquet,
            "datasets": {name: {"rows": item["rows"], "outcome": item["outcome"]} for name, item in export["datasets"].items()}}


# --- backtest ------------------------------------------------------------------------------

SPEC_KEYS = {"family", "hypothesis", "signal", "parameters", "grid", "prices", "symbols", "backtest", "walk_forward",
             "report_name"}
BACKTEST_SETTINGS = {"start", "end", "rebalance", "execution_lag", "commission_bps", "spread_bps", "cash_rate_daily",
                     "allow_short"}


def load_spec(path: Path) -> dict[str, Any]:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    unknown = sorted(set(spec) - SPEC_KEYS)
    if unknown:
        raise JobError(f"The spec has unknown keys: {', '.join(unknown)}")
    for key in ("family", "hypothesis", "signal", "prices"):
        if not spec.get(key):
            raise JobError(f"The spec needs {key!r}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", spec["family"]):
        raise JobError("family is lower-case letters, digits and hyphens (it names a folder)")
    if spec["signal"] not in signals.SIGNALS:
        raise JobError(f"Unknown signal {spec['signal']!r}; choose one of {', '.join(sorted(signals.SIGNALS))}")
    unknown = sorted(set(spec.get("backtest") or {}) - BACKTEST_SETTINGS)
    if unknown:
        raise JobError(f"Unknown backtest settings: {', '.join(unknown)}")
    grid = spec.get("grid") or {}
    if not isinstance(grid, dict) or any(not isinstance(values, list) or not values for values in grid.values()):
        raise JobError("grid maps each setting to a non-empty list of values")
    prices = spec["prices"]
    if not isinstance(prices, dict) or len({"export_manifest", "path"} & set(prices)) != 1:
        raise JobError("prices names either an export_manifest in research-features or a local JSON path")
    walk = spec.get("walk_forward")
    if walk is not None:
        unknown = sorted(set(walk) - {"train", "test", "step", "gap", "expanding"})
        if unknown or "train" not in walk or "test" not in walk:
            raise JobError("walk_forward takes train and test (sessions), and optionally step, gap and expanding")
        if int(walk.get("step") or walk["test"]) < int(walk["test"]):
            raise JobError("walk_forward step must be at least test, so the test windows never overlap")
    return spec


def load_prices(spec: Mapping[str, Any], context: Context, spec_path: Path) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    """{symbol: {date: close}} and where it came from (with the fingerprint the trials record)."""
    source = spec["prices"]
    if "export_manifest" in source:
        export, rows = feature_store.load_dataset(context.features(), source["export_manifest"], "market_prices")
        described = {"export_manifest": source["export_manifest"], "as_known_on": export["as_known_on"],
                     "fingerprint": export["fingerprint"]}
    else:
        path = Path(source["path"])
        path = path if path.is_absolute() else spec_path.parent / path
        raw = path.read_bytes()
        loaded = json.loads(raw.decode("utf-8"))
        rows = [{"symbol": symbol, "price_date": day, "close": close}
                for symbol, series in loaded.items() for day, close in series.items()]
        described = {"path": str(source["path"]),
                     "fingerprint": experiment_log.data_fingerprint("prices-file", hashlib.sha256(raw).hexdigest())}
    prices: dict[str, dict[str, float]] = {}
    wanted = set(spec.get("symbols") or [])
    for row in rows:
        if wanted and row["symbol"] not in wanted:
            continue
        close = row.get("close")
        if close is None or not math.isfinite(float(close)) or float(close) <= 0:
            continue
        prices.setdefault(str(row["symbol"]), {})[str(row["price_date"])] = float(close)
    missing = sorted(wanted - set(prices))
    if missing:
        raise JobError(f"No prices for {', '.join(missing)}")
    if not prices:
        raise JobError("No prices to backtest")
    described["symbols"] = sorted(prices)
    return prices, described


def parameter_sets(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The signal settings of every trial: ``parameters`` with each combination of ``grid``."""
    base = dict(spec.get("parameters") or {})
    grid = spec.get("grid") or {}
    names = sorted(grid)
    if not names:
        return [base]
    return [{**base, **dict(zip(names, combination))} for combination in itertools.product(*(grid[n] for n in names))]


def _metrics(result: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("sharpe_ratio", "annualised_return", "annualised_volatility", "maximum_drawdown", "turnover_per_year",
            "cost_drag_per_year", "total_cost", "final_nav", "observations", "period_start", "period_end")
    return {key: result.get(key) for key in keys}


def _sharpe(returns: Sequence[float]) -> float | None:
    return backtester.summary_statistics(list(returns))["sharpe_ratio"] if len(returns) > 1 else None


def walk_forward(trials: Sequence[Mapping[str, Any]], settings: Mapping[str, Any]) -> dict[str, Any]:
    """Pick the best trial on each training window by Sharpe ratio, then keep its returns on the next
    test window. The kept returns are out of sample: no test day was used to pick the trial."""
    count = min(len(trial["returns"]) for trial in trials)
    splits = validation.walk_forward_splits(count, train=int(settings["train"]), test=int(settings["test"]),
                                            step=int(settings.get("step") or settings["test"]),
                                            gap=int(settings.get("gap") or 0), expanding=bool(settings.get("expanding")))
    if not splits:
        return {"available": False, "reason": f"{count} return days are not enough for one train and test window"}
    kept: list[float] = []
    chosen = []
    for train, test in splits:
        scores = [(_sharpe([trial["returns"][i] for i in train]), index) for index, trial in enumerate(trials)]
        best = max(scores, key=lambda item: (-math.inf if item[0] is None else item[0], -item[1]))[1]
        kept.extend(trials[best]["returns"][i] for i in test)
        chosen.append({"train": [train[0], train[-1]], "test": [test[0], test[-1]],
                       "trial_id": trials[best]["trial_id"], "settings": trials[best]["settings"]})
    summary = backtester.summary_statistics(kept)
    result = {"available": True, "splits": chosen, "out_of_sample_days": len(kept), **summary}
    try:
        result["probabilistic_sharpe_ratio"] = skill_statistics.probabilistic_sharpe_ratio(kept)["probabilistic_sharpe_ratio"]
    except Exception as error:  # noqa: BLE001 - too short a record is reported, not fatal
        result["probabilistic_sharpe_ratio"] = None
        result["probabilistic_sharpe_note"] = str(error)
    return result


def command_backtest(args: argparse.Namespace, context: Context) -> dict[str, Any]:
    spec_path = Path(args.spec)
    spec = load_spec(spec_path)
    prices, data = load_prices(spec, context, spec_path)
    settings = dict(spec.get("backtest") or {})
    log = experiment_log.ExperimentLog(context.experiment_store())

    trials = []
    for chosen in parameter_sets(spec):
        signal = signals.build(spec["signal"], chosen, assets=sorted(prices))
        result = backtester.run_backtest(prices, signal, **settings)
        trial = experiment_log.Trial(
            family=spec["family"], hypothesis=spec["hypothesis"],
            parameters={"signal": spec["signal"], "settings": chosen, "backtest": settings, "symbols": sorted(prices)},
            data_fingerprint=data["fingerprint"], code_version=context.code_version,
            metrics={**_metrics(result), "signals_version": signals.MODEL_VERSION,
                     "backtester_version": backtester.MODEL_VERSION})
        trial_id = log.record(trial)
        trials.append({"trial_id": trial_id, "settings": chosen, "metrics": _metrics(result),
                       "returns": result["returns"], "method": result["method"]})

    family = log.family_statistics(spec["family"])
    selected = max(trials, key=lambda item: -math.inf if item["metrics"]["sharpe_ratio"] is None
                   else item["metrics"]["sharpe_ratio"])
    try:
        deflated = skill_statistics.deflated_sharpe_ratio(
            selected["returns"], trials=max(1, family["trials"]), trial_sharpe_variance=family["variance"] or 0.0)
        deflated = {key: deflated[key] for key in ("deflated_sharpe_ratio", "expected_maximum_sharpe_annual",
                                                    "sharpe_ratio_annual", "trials", "trial_sharpe_variance_annual",
                                                    "observations", "method")}
    except Exception as error:  # noqa: BLE001 - a short record is reported, not fatal
        deflated = {"deflated_sharpe_ratio": None, "note": str(error)}

    report = {
        "schema": "research-backtest-report-v1", "run_id": context.run_id, "family": spec["family"],
        "hypothesis": spec["hypothesis"], "signal": spec["signal"], "signals_version": signals.MODEL_VERSION,
        "backtester_version": backtester.MODEL_VERSION, "code_version": context.code_version, "data": data,
        "backtest_settings": settings, "method": trials[0]["method"],
        "trials": [{key: item[key] for key in ("trial_id", "settings", "metrics")} for item in trials],
        "family_statistics": family, "selected_trial": selected["trial_id"], "deflated_sharpe": deflated,
        "walk_forward": walk_forward(trials, spec["walk_forward"]) if spec.get("walk_forward") else None,
        "disclaimer": REPORT_DISCLAIMER,
    }
    name = spec.get("report_name") or context.run_id
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name):
        raise JobError("report_name is letters, digits, dots, hyphens and underscores")
    folder = f"reports/{spec['family']}/{name}"
    files = context.experiments()
    files.write(f"{folder}/report.json", (json.dumps(feature_store.plain(report), indent=1, sort_keys=True) + "\n").encode(),
                content_type="application/json", overwrite=False)
    files.write(f"{folder}/report.md", render_report(report).encode("utf-8"), content_type="text/markdown",
                overwrite=False)
    return {"command": "backtest", "report": f"{folder}/report.md", "trials": len(trials),
            "family_trials": family["trials"], "deflated_sharpe_ratio": deflated.get("deflated_sharpe_ratio")}


def _number(value: Any, *, percent: bool = False, places: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.{places}f}%" if percent else f"{value:.{places}f}"


def render_report(report: Mapping[str, Any]) -> str:
    deflated = report["deflated_sharpe"]
    family = report["family_statistics"]
    lines = [
        f"# Backtest: {report['family']} ({report['signal']})", "",
        f"**Hypothesis.** {report['hypothesis']}", "",
        f"Run `{report['run_id']}`, code `{report['code_version']}`, {report['signals_version']}, "
        f"{report['backtester_version']}.", "",
        "## Data", "",
        *(f"- {key.replace('_', ' ')}: `{value}`" for key, value in report["data"].items()), "",
        "## Trials", "",
        "| Trial | Settings | Sharpe | Return a year | Volatility | Worst drawdown | Turnover a year | Cost drag a year |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for trial in report["trials"]:
        m = trial["metrics"]
        lines.append(f"| `{trial['trial_id'][:12]}` | `{json.dumps(trial['settings'], sort_keys=True)}` | "
                     f"{_number(m['sharpe_ratio'])} | {_number(m['annualised_return'], percent=True)} | "
                     f"{_number(m['annualised_volatility'], percent=True)} | {_number(m['maximum_drawdown'], percent=True)} | "
                     f"{_number(m['turnover_per_year'])} | {_number(m['cost_drag_per_year'], percent=True)} |")
    lines += [
        "", "## Is the best trial more than luck?", "",
        f"The family **{report['family']}** has {family['trials']} recorded trials (every attempt, including earlier "
        f"runs), with a variance of trial Sharpe ratios of {_number(family['variance'], places=4)}.", "",
        f"- Selected trial: `{report['selected_trial'][:12]}`",
        f"- Deflated Sharpe ratio: **{_number(deflated.get('deflated_sharpe_ratio'), places=3)}** (the probability that "
        "its true Sharpe ratio beats the best that this many skill-less trials would show by luck)",
    ]
    if deflated.get("expected_maximum_sharpe_annual") is not None:
        lines.append(f"- Best Sharpe ratio expected by luck: {_number(deflated['expected_maximum_sharpe_annual'])}")
    if deflated.get("note"):
        lines.append(f"- Not available: {deflated['note']}")
    walk = report.get("walk_forward")
    if walk:
        lines += ["", "## Walk-forward (out of sample)", ""]
        if walk.get("available"):
            lines += [f"- {len(walk['splits'])} windows, {walk['out_of_sample_days']} test days",
                      f"- Sharpe {_number(walk['sharpe_ratio'])}, return a year {_number(walk['annualised_return'], percent=True)}, "
                      f"worst drawdown {_number(walk['maximum_drawdown'], percent=True)}",
                      f"- Probabilistic Sharpe ratio: {_number(walk.get('probabilistic_sharpe_ratio'), places=3)}"]
        else:
            lines.append(f"- Not available: {walk['reason']}")
    lines += ["", "## Method", "", report["method"], "", "---", "", report["disclaimer"], ""]
    return "\n".join(lines)


# --- check ---------------------------------------------------------------------------------

def command_check(args: argparse.Namespace, context: Context) -> dict[str, Any]:
    results: dict[str, Any] = {"run_id": context.run_id, "tenant_id": context.tenant_id,
                               "code_version": context.code_version}
    results["research_database"] = context.run_register().check()
    url = context.environ.get("RESEARCH_LIVE_READER_URL", "").strip()
    if url and not in_daily_window(context.now()):
        import psycopg

        with psycopg.connect(url, connect_timeout=20, options=f"-c app.tenant_id={context.tenant_id}") as connection:
            login = connection.execute("SELECT current_user").fetchone()[0]
            forbidden = {}
            for name in FORBIDDEN_LIVE:
                if connection.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0] is not None:
                    forbidden[name] = connection.execute("SELECT has_table_privilege(current_user, %s, 'SELECT')",
                                                         (name,)).fetchone()[0]
            history = {table: connection.execute("SELECT to_regprocedure(%s) IS NOT NULL",
                                                 (f"silver.{table}_as_known(timestamptz)",)).fetchone()[0]
                       for table in feature_store.HISTORY_TABLES}
            reads_silver = connection.execute("SELECT has_table_privilege(current_user, 'silver.accounts', 'SELECT')"
                                              ).fetchone()[0]
        results["live_reader"] = {"login": login, "reads_silver": reads_silver, "forbidden_readable": forbidden,
                                  "as_known_functions": history,
                                  "ok": login == READER_LOGIN and reads_silver and not any(forbidden.values())}
    else:
        results["live_reader"] = {"skipped": "not configured" if not url else "daily window"}
    storage = {}
    if context.local_dir is None and context.environ.get(blob_store.ACCOUNT_URL_ENV):
        for name in blob_store.READ_ONLY_CONTAINERS:
            client = context._container(name)
            try:
                next(iter(client.list_blobs(results_per_page=1)), None)
                storage[name] = "read"
            except Exception as error:  # noqa: BLE001 - reported per container
                storage[name] = f"failed: {type(error).__name__}"
        probe = (json.dumps({"run_id": context.run_id, "checked_at": context.now().isoformat()}) + "\n").encode()
        for name in blob_store.WRITE_CONTAINERS:
            files = blob_store.BlobFiles(context._container(name))
            try:
                files.write(f"checks/{context.run_id}.json", probe, content_type="application/json", overwrite=False)
                storage[name] = "written" if files.read(f"checks/{context.run_id}.json") == probe else "mismatch"
            except Exception as error:  # noqa: BLE001
                storage[name] = f"failed: {type(error).__name__}"
    results["storage"] = storage or {"skipped": "local run or no storage account"}
    results["ok"] = (results["research_database"].get("ok", True)
                     and results["live_reader"].get("ok", True)
                     and all(value in ("read", "written") for value in storage.values()))
    return results


# --- the command line ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_research_job.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser, *, server: bool = True) -> None:
        command.add_argument("--local-dir", type=Path,
                             help="write features, the experiment log and reports to this folder instead of storage")
        if server:
            command.add_argument("--keep-server-running", action="store_true",
                                 help="leave the research server on afterwards (another run follows straight away)")

    export = sub.add_parser("export-features", help="export bronze and silver as known on a date")
    export.add_argument("--as-known-on", required=True, help="yyyy-mm-dd; a day that has ended (UTC)")
    export.add_argument("--subject", action="append", required=True, help="an account id; repeat for several")
    export.add_argument("--dataset", action="append", choices=feature_store.DATASETS,
                        help="only these datasets (default: all eight)")
    export.add_argument("--include-v11-archive", action="store_true",
                        help="also replay the v1.1 statements under v11-archive/ (counted from 17 Oct 2026)")
    common(export)

    backtest = sub.add_parser("backtest", help="run a signal's trials, walk forward and write a report")
    backtest.add_argument("--spec", required=True, type=Path, help="the backtest spec (JSON)")
    common(backtest)

    check = sub.add_parser("check", help="check settings, logins and container access")
    common(check)
    sub.add_parser("start-server", help="start the research server and leave it running")
    stop = sub.add_parser("stop-server", help="stop the research server")
    stop.add_argument("--if-idle", action="store_true",
                      help="leave it on while another run recorded in research.job_runs is still going")
    return parser


COMMANDS: dict[str, Callable[[argparse.Namespace, Context], dict[str, Any]]] = {
    "export-features": command_export,
    "backtest": command_backtest,
    "check": command_check,
}


def _on_terminate(signum, frame):  # noqa: ARG001 - signal handler signature
    raise SystemExit(128 + signum)


def _finish(register: Any, run_id: str, status: str, detail: Mapping[str, Any]) -> None:
    try:
        register.finished(run_id, status, detail)
    except Exception as error:  # noqa: BLE001 - the run's own outcome matters more than its record
        LOGGER.error("The run could not be recorded as %s: %s", status, error)


def _others_running(context: Context, register: Any, run_id: str | None) -> int:
    """Other runs still using the server. If that cannot be read, none: the server is stopped."""
    try:
        return int(register.running(other_than=run_id, within_seconds=context.max_run_seconds))
    except Exception as error:  # noqa: BLE001 - stopping is the safe default
        LOGGER.warning("Other runs could not be checked (%s); stopping the server anyway", error)
        return 0


def _stop(control: ServerControl, context: Context, register: Any, run_id: str | None) -> dict[str, Any]:
    others = _others_running(context, register, run_id) if register is not None else 0
    if others:
        LOGGER.info("Leaving the research server on: %s other run(s) still going; the last one stops it", others)
        return {"state": control.state(), "left_on_for_other_runs": others}
    return {"state": control.stop()}


def _stop_quietly(control: ServerControl, context: Context, register: Any, run_id: str | None) -> None:
    """Stop the server after a failure without hiding the failure itself."""
    try:
        _stop(control, context, register, run_id)
    except Exception as error:  # noqa: BLE001
        LOGGER.error("The research server could not be stopped after the failure: %s", error)


def main(argv: Sequence[str] | None = None, *, context: Context | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    context = context or Context()
    if getattr(args, "local_dir", None) is not None:
        context.local_dir = args.local_dir
    try:
        # A job stopped at its time limit gets SIGTERM: make it an exit, so the server is still stopped.
        signals_module.signal(signals_module.SIGTERM, _on_terminate)
    except ValueError:  # not the main thread (an embedded run); nothing to install
        pass

    if args.command in ("start-server", "stop-server"):
        control = context.server_control()
        if control is None:
            raise JobError("RESEARCH_SERVER_RESOURCE_ID is not set")
        if args.command == "start-server":
            outcome = {"state": control.start()}
        elif args.if_idle and control.state() not in ("Stopped", "Disabled"):
            control.start()  # lets a server that is starting or stopping settle first
            outcome = _stop(control, context, context.run_register(), None)
        else:
            outcome = {"state": control.stop()}
        print(json.dumps({"command": args.command, **outcome, "actions": control.actions}))
        return 0

    control = context.server_control()
    keep = getattr(args, "keep_server_running", False)
    status, result, register = "failed", {}, None
    try:
        if control is not None:
            control.start()
        register = context.run_register()
        register.started(context.run_id, args.command, context.code_version,
                         {"argv": list(sys.argv[1:] if argv is None else argv)})
        try:
            result = COMMANDS[args.command](args, context)
        except BaseException as error:
            _finish(register, context.run_id, "failed", {"error": type(error).__name__, "message": str(error)[:500]})
            raise
        status = "passed" if result.get("ok", True) else "failed"
        _finish(register, context.run_id, status, {"result": result})
    except BaseException:
        if control is not None and not keep:
            _stop_quietly(control, context, register, context.run_id)
        raise
    if control is not None and not keep:
        _stop(control, context, register, context.run_id)
    print(json.dumps(feature_store.plain({"run_id": context.run_id, "status": status, **result}), sort_keys=True))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except JobError as error:
        print(json.dumps({"status": "failed", "error": type(error).__name__, "message": str(error)}), flush=True)
        raise SystemExit(2) from None
