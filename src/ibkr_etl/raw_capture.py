"""Raw copies of market-data provider responses in the bronze-market-data container.

Each fetch hands over the provider response as received, before parsing. Capture is
off unless MARKET_DATA_RAW_CONTAINER and AZURE_STORAGE_ACCOUNT_URL are both set.

It is best effort and never raises into the job:
- storage calls use managed identity only, short timeouts and no retries;
- each upload runs in a daemon thread that the caller waits on for a bounded time;
- every thread draws on one time budget per process (MARKET_DATA_RAW_BUDGET_SECONDS),
  so the total time callers wait on capture never exceeds it;
- the first storage error or timeout, or the budget running out, logs one warning and
  switches capture off for the rest of the process.

Blob names, written with overwrite=False (the container is under a legal hold):
- <feed>/<yyyy>/<mm>/<dd>/<request-key>-<sha256 prefix>.<ext>, dated by the UTC request
  time, for responses that can change from day to day;
- <feed>/history/<request-key>-<sha256 prefix>.<ext> for windows that ended over a week
  ago (see settled), so the same answer is stored once and a repeat is "existing".

Each stored file (written or already there) is passed to the observers registered with
add_observer, which is how lineage.py records it as an input of the run; sha256_of gives
the full sha256 of a file this process stored.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

LOGGER = logging.getLogger(__name__)

CONTAINER_ENV = "MARKET_DATA_RAW_CONTAINER"
ACCOUNT_URL_ENV = "AZURE_STORAGE_ACCOUNT_URL"
BUDGET_ENV = "MARKET_DATA_RAW_BUDGET_SECONDS"
EXECUTION_ENV = "CONTAINER_APP_JOB_EXECUTION_NAME"
DEFAULT_BUDGET_SECONDS = 30.0
CONNECTION_TIMEOUT_SECONDS = 3
READ_TIMEOUT_SECONDS = 5
# The longest one upload may hold up its caller (token plus upload share it).
WRITE_WAIT_SECONDS = float(CONNECTION_TIMEOUT_SECONDS + READ_TIMEOUT_SECONDS)
SETTLED_AFTER_DAYS = 7
SHA_PREFIX = 16
MAX_KEY_LENGTH = 120
MAX_METADATA_VALUE = 1024
MAX_REMEMBERED_DIGESTS = 20000

# Feed folders in bronze-market-data, one per provider dataset.
FEEDS = frozenset({
    "yahoo-benchmark-history",  # benchmark index closes (yfinance history)
    "yahoo-fx-history",  # FX closes used to convert benchmarks and crisis prices
    "yahoo-crisis-history",  # crisis-window adjusted closes (yfinance history)
    "yahoo-sector-search",  # Yahoo search profiles used for sectors (HTTP JSON)
    "yahoo-sector-profile",  # yfinance get_info fallback for sectors
    "yahoo-chart",  # Yahoo chart closes behind market_data_prices (HTTP JSON)
    "treasury-par-yield",  # U.S. Treasury daily par yield curve (HTTP XML)
    "hkma-hibor",  # HKMA HIBOR fixing pages (HTTP JSON)
    "hkab-hibor",  # HKAB dated HIBOR fallback (HTTP JSON)
    "bok-cd-91d",  # Bank of Korea ECOS CD 91-day yield (HTTP JSON)
})

_SECRET_QUERY_KEYS = re.compile(r"(key|token|sig|secret|password|credential|auth|code)", re.I)
_EXTENSIONS = (("xml", "xml"), ("json", "json"), ("csv", "csv"), ("html", "html"), ("text/plain", "txt"))
_CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
    "xml": "application/xml",
    "html": "text/html",
    "txt": "text/plain",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def request_key(*parts: Any) -> str:
    """A short, blob-safe key such as GSPC_2025-01-20_2026-10-17_1d."""
    text = "_".join(str(part) for part in parts if part not in (None, ""))
    cleaned = re.sub(r"[^A-Za-z0-9._=-]+", "_", text).strip("_.")
    return cleaned[:MAX_KEY_LENGTH] or "request"


def settled(end: date | datetime | str, *, now: datetime | None = None) -> bool:
    """True when a requested window ended more than SETTLED_AFTER_DAYS before now. Its
    answer should no longer change, so it is filed once under <feed>/history/."""
    try:
        if isinstance(end, datetime):
            end_day = end.date()
        elif isinstance(end, date):
            end_day = end
        else:
            end_day = date.fromisoformat(str(end).strip()[:10])
        today = (now or utc_now()).astimezone(timezone.utc).date()
        return end_day < today - timedelta(days=SETTLED_AFTER_DAYS)
    except (TypeError, ValueError):
        return False


def new_run_id(job: str) -> str:
    """A run id for jobs without an ingestion run, such as crisis-scenarios-20261016T092000Z-1a2b3c4d."""
    return f"{job}-{utc_now():%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


def safe_url(url: str) -> str:
    """The URL without user info or credential-like query parameters."""
    parts = urlsplit(str(url))
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not _SECRET_QUERY_KEYS.search(k)])
    return urlunsplit((parts.scheme, host, parts.path, query, ""))


def _metadata_value(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    return text.encode("ascii", "backslashreplace").decode("ascii")[:MAX_METADATA_VALUE]


def _extension(content_type: str | None, default: str) -> str:
    lowered = (content_type or "").lower()
    for marker, extension in _EXTENSIONS:
        if marker in lowered:
            return extension
    return default


def _default_uploader(account_url: str, container: str) -> Callable[..., str]:
    from .cloud_storage import best_effort_credential, container_client, upload_with_client

    client = container_client(
        account_url,
        container,
        credential=best_effort_credential(
            connection_timeout=CONNECTION_TIMEOUT_SECONDS, read_timeout=READ_TIMEOUT_SECONDS
        ),
        connection_timeout=CONNECTION_TIMEOUT_SECONDS,
        read_timeout=READ_TIMEOUT_SECONDS,
        retry_total=0,
    )

    def upload(blob_name: str, content: bytes, *, content_type: str, metadata: dict[str, str]) -> str:
        return upload_with_client(
            client.get_blob_client(blob_name),
            content,
            overwrite=False,
            content_type=content_type,
            metadata=metadata,
        )

    return upload


def _already_exists(error: BaseException) -> bool:
    try:
        from azure.core.exceptions import ResourceExistsError
    except ImportError:  # pragma: no cover - the SDK is installed in every image
        return False
    return isinstance(error, ResourceExistsError)


class RawCapture:
    """Writes raw provider responses; every public method swallows its own errors."""

    def __init__(
        self,
        *,
        container: str = "",
        account_url: str = "",
        budget_seconds: float = DEFAULT_BUDGET_SECONDS,
        uploader_factory: Callable[[str, str], Callable[..., str]] = _default_uploader,
        job_execution: str = "",
        write_wait_seconds: float = WRITE_WAIT_SECONDS,
    ) -> None:
        self.container = container.strip()
        self.account_url = account_url.strip()
        self.enabled = bool(self.container and self.account_url)
        self.budget_seconds = budget_seconds
        self.write_wait_seconds = write_wait_seconds
        self.job_execution = job_execution
        self.job_run_id = ""
        self.disabled_reason: str | None = None
        self.stats = {"written": 0, "existing": 0, "failed": 0, "skipped": 0}
        self._uploader_factory = uploader_factory
        self._uploader: Callable[..., str] | None = None
        self._spent = 0.0  # seconds callers have waited on storage, all threads together
        self._reserved = 0.0  # seconds claimed by uploads still in flight
        self._names: set[str] = set()
        self._digests: dict[str, str] = {}  # blob name -> full sha256, for files stored by this process
        self._warned: set[str] = set()
        self._lock = threading.Lock()  # stats and budget; never held while waiting
        self._factory_lock = threading.Lock()  # only taken in upload threads

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "RawCapture":
        environ = os.environ if environ is None else environ
        try:
            budget = float(environ.get(BUDGET_ENV) or DEFAULT_BUDGET_SECONDS)
        except ValueError:
            budget = DEFAULT_BUDGET_SECONDS
        return cls(
            container=environ.get(CONTAINER_ENV, ""),
            account_url=environ.get(ACCOUNT_URL_ENV, ""),
            budget_seconds=max(0.0, budget),
            job_execution=environ.get(EXECUTION_ENV, ""),
        )

    # Public capture calls ------------------------------------------------------------

    def http(self, feed: str, provider: str, *, url: str, body: bytes, key: str,
             status: int | None = None, content_type: str | None = None,
             extension: str | None = None, requested_at: datetime | None = None,
             history: bool = False) -> str | None:
        """An HTTP response body exactly as received, with its status and URL."""
        if not self._open():
            return None
        try:
            if status is None:
                status = getattr(body, "status", None)
            if content_type is None:
                content_type = getattr(body, "content_type", None)
            extension = extension or _extension(content_type, "bin")
            return self._write(
                feed, provider, bytes(body), extension,
                content_type or _CONTENT_TYPES.get(extension, "application/octet-stream"),
                key, requested_at, history,
                {"source_url": safe_url(url), "http_status": "" if status is None else status,
                 "content_format": "http-body"},
            )
        except Exception as error:  # noqa: BLE001 - capture must never fail the fetch
            self._item_failed(feed, error)
            return None

    def frame(self, feed: str, provider: str, *, frame: Any, request: Mapping[str, Any],
              key: str, requested_at: datetime | None = None, history: bool = False) -> str | None:
        """A yfinance DataFrame as CSV with its index, plus the request parameters."""
        if not self._open():
            return None
        try:
            # Python float repr is shortest round-trip, so the CSV is lossless;
            # read it back with pandas.read_csv(..., float_precision="round_trip").
            content = frame.to_csv().encode("utf-8")
            return self._write(
                feed, provider, content, "csv", _CONTENT_TYPES["csv"], key, requested_at, history,
                {"request": json.dumps(dict(request), sort_keys=True, default=str),
                 "content_format": "pandas-csv-with-index"},
            )
        except Exception as error:  # noqa: BLE001
            self._item_failed(feed, error)
            return None

    def document(self, feed: str, provider: str, *, payload: Any, request: Mapping[str, Any],
                 key: str, requested_at: datetime | None = None, history: bool = False) -> str | None:
        """A provider object (for example yfinance get_info) as JSON."""
        if not self._open():
            return None
        try:
            content = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
            return self._write(
                feed, provider, content, "json", _CONTENT_TYPES["json"], key, requested_at, history,
                {"request": json.dumps(dict(request), sort_keys=True, default=str),
                 "content_format": "json"},
            )
        except Exception as error:  # noqa: BLE001
            self._item_failed(feed, error)
            return None

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {"container": self.container, **self.stats,
                    "seconds": round(self._spent, 3), "disabled": self.disabled_reason}

    def digest_of(self, name: str | None) -> str | None:
        """The full sha256 of a file this process stored under name, or None."""
        if not name:
            return None
        with self._lock:
            return self._digests.get(name)

    # Internals -----------------------------------------------------------------------

    def _open(self) -> bool:
        """Cheap check before serialising: on, not switched off, budget left."""
        if not self.enabled:
            return False
        with self._lock:
            if self.disabled_reason is None:
                return True
            self.stats["skipped"] += 1
            return False

    def _reserve(self) -> float:
        """Claim up to one write's wait from the shared budget; 0 means skip this one."""
        with self._lock:
            if self.disabled_reason is not None:
                return 0.0
            left = self.budget_seconds - self._spent
            if left <= 0:
                self._disable("time budget of %g seconds used" % self.budget_seconds)
                return 0.0
            free = left - self._reserved
            if free <= 0:
                return 0.0  # uploads in flight hold the rest of the budget for now
            claim = min(free, self.write_wait_seconds)
            self._reserved += claim
            return claim

    def _release(self, claim: float, elapsed: float) -> None:
        with self._lock:
            self._reserved = max(0.0, self._reserved - claim)
            self._spent += elapsed
            if self._spent >= self.budget_seconds:
                self._disable("time budget of %g seconds used" % self.budget_seconds)

    def _write(self, feed: str, provider: str, content: bytes, extension: str,
               content_type: str, key: str, requested_at: datetime | None, history: bool,
               extra: Mapping[str, Any]) -> str | None:
        if feed not in FEEDS:
            raise ValueError(f"Unknown raw market-data feed {feed!r}")
        claim = self._reserve()
        if claim <= 0:
            with self._lock:
                self.stats["skipped"] += 1
            return None
        started = time.monotonic()
        try:
            requested = (requested_at or utc_now()).astimezone(timezone.utc)
            digest = hashlib.sha256(content).hexdigest()
            folder = "history" if history else f"{requested:%Y/%m/%d}"
            name = f"{feed}/{folder}/{request_key(key)}-{digest[:SHA_PREFIX]}.{extension}"
            stored = dict(name=name, digest=digest, size=len(content), feed=feed, provider=provider, key=key)
            with self._lock:
                seen = name in self._names
                if seen:
                    self.stats["existing"] += 1
            if seen:
                return self._stored(**stored, outcome="existing")
            metadata = {
                "feed": feed,
                "provider": provider,
                "requested_at": requested.isoformat(timespec="seconds"),
                "received_at": utc_now().isoformat(timespec="seconds"),
                "sha256": digest,
                "request_key": request_key(key),
                **({"job_run_id": self.job_run_id} if self.job_run_id else {}),
                **({"job_execution": self.job_execution} if self.job_execution else {}),
                **{k: v for k, v in extra.items() if v not in (None, "")},
            }
            metadata = {k: _metadata_value(v) for k, v in metadata.items()}
            try:
                self._upload_within(claim - (time.monotonic() - started), name, content,
                                    content_type=content_type, metadata=metadata)
            except Exception as error:  # noqa: BLE001
                exists = _already_exists(error)
                with self._lock:
                    if exists:
                        self._names.add(name)
                        self.stats["existing"] += 1
                    else:
                        self.stats["failed"] += 1
                        self._disable(f"{type(error).__name__}: {' '.join(str(error).split())[:200]}")
                if exists:
                    return self._stored(**stored, outcome="existing")
                return None
            with self._lock:
                self._names.add(name)
                self.stats["written"] += 1
            return self._stored(**stored, outcome="written")
        finally:
            self._release(claim, time.monotonic() - started)

    def _stored(self, *, name: str, digest: str, size: int, feed: str, provider: str, key: str,
                outcome: str) -> str:
        """Remember a stored file's sha256 and tell the observers; returns its blob name."""
        with self._lock:
            if name not in self._digests and len(self._digests) < MAX_REMEMBERED_DIGESTS:
                self._digests[name] = digest
        _notify({"container": self.container, "blob": name, "sha256": digest, "bytes": size,
                 "feed": feed, "provider": provider, "request_key": request_key(key), "outcome": outcome})
        return name

    def _upload_within(self, timeout: float, name: str, content: bytes, **options: Any) -> None:
        """Upload in a daemon thread and wait at most `timeout`; a hung call is left behind."""
        outcome: dict[str, BaseException] = {}
        done = threading.Event()

        def work() -> None:
            try:
                self._upload_function()(name, content, **options)
            except BaseException as error:  # noqa: BLE001 - handed back to the caller
                outcome["error"] = error
            finally:
                done.set()

        threading.Thread(target=work, name="raw-market-data-upload", daemon=True).start()
        if not done.wait(max(0.0, timeout)):
            limit = ""
            if timeout < self.write_wait_seconds - 0.01:
                limit = f", the rest of the {self.budget_seconds:g}-second time budget"
            raise TimeoutError(f"storage did not answer within {max(0.0, timeout):.1f} seconds{limit}")
        if "error" in outcome:
            raise outcome["error"]

    def _upload_function(self) -> Callable[..., str]:
        with self._factory_lock:
            if self._uploader is None:
                self._uploader = self._uploader_factory(self.account_url, self.container)
            return self._uploader

    def _disable(self, reason: str) -> None:
        # Called with self._lock held.
        if self.disabled_reason is None:
            self.disabled_reason = reason
            LOGGER.warning("Raw market-data capture is off for the rest of this run: %s", reason)

    def _item_failed(self, feed: str, error: Exception) -> None:
        try:
            with self._lock:
                self.stats["failed"] += 1
                first = feed not in self._warned
                self._warned.add(feed)
            if first:
                LOGGER.warning("Raw market-data capture skipped a %s response: %s",
                               feed, type(error).__name__)
        except Exception:  # noqa: BLE001
            pass


_OBSERVERS: list[Callable[[dict[str, Any]], None]] = []
_OBSERVERS_LOCK = threading.Lock()


def add_observer(callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
    """Call callback with each stored file (container, blob, sha256, bytes, feed, provider,
    request_key, outcome). Returns a function that removes it. An observer's error is ignored."""
    with _OBSERVERS_LOCK:
        _OBSERVERS.append(callback)

    def remove() -> None:
        with _OBSERVERS_LOCK:
            if callback in _OBSERVERS:
                _OBSERVERS.remove(callback)

    return remove


def _notify(record: dict[str, Any]) -> None:
    with _OBSERVERS_LOCK:
        observers = list(_OBSERVERS)
    for observer in observers:
        try:
            observer(dict(record))
        except Exception:  # noqa: BLE001 - an observer never fails a capture
            pass


_DEFAULT: RawCapture | None = None
_DEFAULT_LOCK = threading.Lock()


def default() -> RawCapture:
    global _DEFAULT
    if _DEFAULT is None:
        with _DEFAULT_LOCK:
            if _DEFAULT is None:
                capture = RawCapture.from_environment()
                if capture.enabled:
                    atexit.register(_report_at_exit, capture)
                _DEFAULT = capture
    return _DEFAULT


def reset(capture: RawCapture | None = None) -> None:
    """Replace the process-wide capture (tests) or re-read the environment."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        _DEFAULT = capture


def _report_at_exit(capture: RawCapture) -> None:
    try:
        summary = capture.summary()
        if any(summary[key] for key in capture.stats) or summary["disabled"]:
            sys.stderr.write("MARKET_DATA_RAW_CAPTURE " + json.dumps(summary, sort_keys=True) + "\n")
    except Exception:  # noqa: BLE001
        pass


def set_job_run_id(run_id: Any) -> None:
    try:
        default().job_run_id = "" if run_id is None else str(run_id)
    except Exception:  # noqa: BLE001
        pass


def sha256_of(name: str | None) -> str | None:
    """The full sha256 of a file the process-wide capture stored under name, or None."""
    try:
        return default().digest_of(name)
    except Exception:  # noqa: BLE001
        return None


def capture_http(feed: str, provider: str, **kwargs: Any) -> str | None:
    try:
        return default().http(feed, provider, **kwargs)
    except Exception:  # noqa: BLE001
        return None


def capture_frame(feed: str, provider: str, **kwargs: Any) -> str | None:
    try:
        return default().frame(feed, provider, **kwargs)
    except Exception:  # noqa: BLE001
        return None


def capture_document(feed: str, provider: str, **kwargs: Any) -> str | None:
    try:
        return default().document(feed, provider, **kwargs)
    except Exception:  # noqa: BLE001
        return None
