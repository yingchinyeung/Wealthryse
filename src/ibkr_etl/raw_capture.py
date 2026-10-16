"""Raw copies of market-data provider responses in the bronze-market-data container.

Each fetch hands over the provider response as received, before parsing. Capture is
off unless MARKET_DATA_RAW_CONTAINER and AZURE_STORAGE_ACCOUNT_URL are both set.

It is best effort and never raises into the job:
- uploads use short timeouts and no retries;
- the first storage error logs one warning and switches capture off for the process;
- the total time a process may spend capturing is capped (MARKET_DATA_RAW_BUDGET_SECONDS).

Blob names: <feed>/<yyyy>/<mm>/<dd>/<request-key>-<sha256 prefix>.<ext>, dated by the UTC
request time, written with overwrite=False (the container is under a legal hold).
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
from datetime import datetime, timezone
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
SHA_PREFIX = 16
MAX_KEY_LENGTH = 120
MAX_METADATA_VALUE = 1024

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
    from .cloud_storage import container_client, upload_with_client

    client = container_client(
        account_url,
        container,
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


def _already_exists(error: Exception) -> bool:
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
        clock: Callable[[], float] = time.monotonic,
        job_execution: str = "",
    ) -> None:
        self.container = container.strip()
        self.account_url = account_url.strip()
        self.enabled = bool(self.container and self.account_url)
        self.budget_seconds = budget_seconds
        self.job_execution = job_execution
        self.job_run_id = ""
        self.disabled_reason: str | None = None
        self.stats = {"written": 0, "existing": 0, "failed": 0, "skipped": 0}
        self._uploader_factory = uploader_factory
        self._uploader: Callable[..., str] | None = None
        self._clock = clock
        self._spent = 0.0
        self._names: set[str] = set()
        self._warned: set[str] = set()
        self._lock = threading.Lock()

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
             extension: str | None = None, requested_at: datetime | None = None) -> str | None:
        """An HTTP response body exactly as received, with its status and URL."""
        if not self.enabled:
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
                key, requested_at,
                {"source_url": safe_url(url), "http_status": "" if status is None else status,
                 "content_format": "http-body"},
            )
        except Exception as error:  # noqa: BLE001 - capture must never fail the fetch
            self._item_failed(feed, error)
            return None

    def frame(self, feed: str, provider: str, *, frame: Any, request: Mapping[str, Any],
              key: str, requested_at: datetime | None = None) -> str | None:
        """A yfinance DataFrame as CSV with its index, plus the request parameters."""
        if not self.enabled:
            return None
        try:
            # Python float repr is shortest round-trip, so the CSV is lossless;
            # read it back with pandas.read_csv(..., float_precision="round_trip").
            content = frame.to_csv().encode("utf-8")
            return self._write(
                feed, provider, content, "csv", _CONTENT_TYPES["csv"], key, requested_at,
                {"request": json.dumps(dict(request), sort_keys=True, default=str),
                 "content_format": "pandas-csv-with-index"},
            )
        except Exception as error:  # noqa: BLE001
            self._item_failed(feed, error)
            return None

    def document(self, feed: str, provider: str, *, payload: Any, request: Mapping[str, Any],
                 key: str, requested_at: datetime | None = None) -> str | None:
        """A provider object (for example yfinance get_info) as JSON."""
        if not self.enabled:
            return None
        try:
            content = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
            return self._write(
                feed, provider, content, "json", _CONTENT_TYPES["json"], key, requested_at,
                {"request": json.dumps(dict(request), sort_keys=True, default=str),
                 "content_format": "json"},
            )
        except Exception as error:  # noqa: BLE001
            self._item_failed(feed, error)
            return None

    def summary(self) -> dict[str, Any]:
        return {"container": self.container, **self.stats,
                "seconds": round(self._spent, 3), "disabled": self.disabled_reason}

    # Internals -----------------------------------------------------------------------

    def _write(self, feed: str, provider: str, content: bytes, extension: str,
               content_type: str, key: str, requested_at: datetime | None,
               extra: Mapping[str, Any]) -> str | None:
        if feed not in FEEDS:
            raise ValueError(f"Unknown raw market-data feed {feed!r}")
        with self._lock:
            if self.disabled_reason is not None:
                self.stats["skipped"] += 1
                return None
            if self._spent >= self.budget_seconds:
                self._disable("time budget of %.0f seconds used" % self.budget_seconds)
                self.stats["skipped"] += 1
                return None
        started = self._clock()
        try:
            requested = (requested_at or utc_now()).astimezone(timezone.utc)
            digest = hashlib.sha256(content).hexdigest()
            name = (f"{feed}/{requested:%Y/%m/%d}/{request_key(key)}"
                    f"-{digest[:SHA_PREFIX]}.{extension}")
            with self._lock:
                if name in self._names:
                    self.stats["existing"] += 1
                    return name
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
                upload = self._upload_function()
                upload(name, content, content_type=content_type, metadata=metadata)
            except Exception as error:  # noqa: BLE001
                if _already_exists(error):
                    with self._lock:
                        self._names.add(name)
                        self.stats["existing"] += 1
                    return name
                with self._lock:
                    self.stats["failed"] += 1
                    self._disable(f"{type(error).__name__}: {' '.join(str(error).split())[:200]}")
                return None
            with self._lock:
                self._names.add(name)
                self.stats["written"] += 1
            return name
        finally:
            elapsed = self._clock() - started
            with self._lock:
                self._spent += elapsed

    def _upload_function(self) -> Callable[..., str]:
        with self._lock:
            if self._uploader is None:
                self._uploader = self._uploader_factory(self.account_url, self.container)
            return self._uploader

    def _disable(self, reason: str) -> None:
        # Called with the lock held.
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
        if any(capture.stats.values()) or capture.disabled_reason:
            sys.stderr.write("MARKET_DATA_RAW_CAPTURE " + json.dumps(capture.summary(), sort_keys=True) + "\n")
    except Exception:  # noqa: BLE001
        pass


def set_job_run_id(run_id: Any) -> None:
    try:
        default().job_run_id = "" if run_id is None else str(run_id)
    except Exception:  # noqa: BLE001
        pass


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
