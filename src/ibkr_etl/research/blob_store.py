"""Where the research job keeps its files: Azure Blob Storage, or a local folder for dry runs.

In Azure the job signs in as the research identity (a managed identity; AZURE_CLIENT_ID picks
it). Its roles are on single containers only (infra/research.bicep): read on ibkr-raw,
bronze-market-data and bronze-vendor-data, write on research-features and
research-experiments. Nothing else in the platform reads or writes the research containers.

* :class:`AppendBlobStore` – the experiment log's store (:class:`.experiment_log.Store`): one
  append blob in research-experiments, one block per trial, each block one JSON line. Blocks
  are only ever added, so a recorded trial is never rewritten in place.
* :class:`BlobFiles` – plain files in one container (feature exports, reports), with
  :meth:`BlobFiles.write` refusing to replace a file unless asked.
* :class:`BlobBronzeSource` – the bronze captures the feature export replays: raw IBKR
  statements from ibkr-raw and market-data responses from bronze-market-data, each counted
  from the time it was stored.
* :class:`DirectoryFiles` and :class:`FileLineStore` – the same interfaces on a local folder.

Every class takes a container client, so tests pass fakes and importing this module never
reaches Azure.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from . import experiment_log
from .feature_store import Capture

FEATURES_CONTAINER = "research-features"
EXPERIMENTS_CONTAINER = experiment_log.CONTAINER
RAW_STATEMENTS_CONTAINER = "ibkr-raw"
MARKET_DATA_CONTAINER = "bronze-market-data"
VENDOR_DATA_CONTAINER = "bronze-vendor-data"
READ_ONLY_CONTAINERS = (RAW_STATEMENTS_CONTAINER, MARKET_DATA_CONTAINER, VENDOR_DATA_CONTAINER)
WRITE_CONTAINERS = (FEATURES_CONTAINER, EXPERIMENTS_CONTAINER)
ACCOUNT_URL_ENV = "AZURE_STORAGE_ACCOUNT_URL"
# An append blob holds at most 50,000 blocks; the log stops with a clear message before that.
MAX_APPEND_BLOCKS = 50_000
MAX_CAPTURE_BYTES = 64 * 1024 * 1024


class StorageError(RuntimeError):
    """A research file could not be read or written as expected."""


def container(name: str, account_url: str | None = None):
    """A container client signed in as the job's managed identity."""
    from ..cloud_storage import container_client

    url = (account_url or os.environ.get(ACCOUNT_URL_ENV, "")).strip()
    if not url:
        raise StorageError(f"{ACCOUNT_URL_ENV} is not set")
    if name not in READ_ONLY_CONTAINERS + WRITE_CONTAINERS:
        raise StorageError(f"The research job does not use the container {name!r}")
    return container_client(url, name)


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _missing(error: BaseException) -> bool:
    from azure.core.exceptions import ResourceNotFoundError

    return isinstance(error, ResourceNotFoundError)


def _exists(error: BaseException) -> bool:
    from azure.core.exceptions import ResourceExistsError, ResourceModifiedError

    return isinstance(error, (ResourceExistsError, ResourceModifiedError))


class AppendBlobStore:
    """The experiment log on one append blob (default ``experiments/log.jsonl``)."""

    def __init__(self, container_client: Any, blob_name: str = experiment_log.LOG_PATH) -> None:
        self._blob = container_client.get_blob_client(blob_name)
        self._ready = False

    def _create_once(self) -> None:
        if self._ready:
            return
        from azure.core import MatchConditions

        try:
            # If-None-Match: * so an existing log is never replaced by an empty one.
            self._blob.create_append_blob(match_condition=MatchConditions.IfMissing)
        except Exception as error:  # noqa: BLE001 - only "already there" is expected
            if not _exists(error):
                raise
        self._ready = True

    def append(self, line: str) -> None:
        if not line or "\n" in line or "\r" in line:
            raise ValueError("An experiment log entry is one non-empty line")
        self._create_once()
        try:
            self._blob.append_block((line + "\n").encode("utf-8"))
        except Exception as error:  # noqa: BLE001
            if "BlockCountExceedsLimit" in str(error):
                raise StorageError(f"The experiment log has reached {MAX_APPEND_BLOCKS} trials; start a new "
                                   "log blob (blob_name) and keep the old one") from error
            raise

    def lines(self) -> list[str]:
        try:
            content = self._blob.download_blob().readall()
        except Exception as error:  # noqa: BLE001
            if _missing(error):
                return []
            raise
        text = bytes(content).decode("utf-8")
        if text and not text.endswith("\n"):
            raise StorageError("The experiment log ends part-way through a line; it needs checking before more trials")
        return [line for line in text.splitlines() if line]


class BlobFiles:
    """Files in one container, for :class:`.feature_store.FeatureSink` and the reports."""

    def __init__(self, container_client: Any, *, maximum_bytes: int = 512 * 1024 * 1024) -> None:
        self._container = container_client
        self._maximum = maximum_bytes

    def read(self, path: str) -> bytes | None:
        client = self._container.get_blob_client(path)
        try:
            data = bytes(client.download_blob().readall())
        except Exception as error:  # noqa: BLE001
            if _missing(error):
                return None
            raise
        if len(data) > self._maximum:
            raise StorageError(f"{path} is larger than {self._maximum} bytes")
        return data

    def write(self, path: str, data: bytes, *, content_type: str, overwrite: bool) -> None:
        from azure.storage.blob import ContentSettings

        client = self._container.get_blob_client(path)
        try:
            client.upload_blob(bytes(data), overwrite=overwrite, content_settings=ContentSettings(content_type=content_type))
        except Exception as error:  # noqa: BLE001
            if not overwrite and _exists(error):
                raise FileExistsError(path) from error
            raise


class BlobBronzeSource:
    """Bronze captures for the replays, each counted from the time the platform stored it.

    Statements are the raw Flex files under ``<tenant id>/`` in ibkr-raw (v2's own pulls).
    The v1.1 archive copies under ``v11-archive/`` were stored on 17 Oct 2026, so they count
    only from then; they are left out unless ``include_v11_archive`` is set. Market-data
    captures are under ``<feed>/`` in bronze-market-data; the later of the blob's creation time
    and its ``received_at`` metadata is used.
    """

    def __init__(self, raw_statements: Any, market_data: Any, *, tenant_id: str, include_v11_archive: bool = False,
                 maximum_bytes: int = MAX_CAPTURE_BYTES) -> None:
        self._raw = raw_statements
        self._market = market_data
        self._prefixes = [f"{tenant_id}/"] + (["v11-archive/"] if include_v11_archive else [])
        self._maximum = maximum_bytes

    def _download(self, client: Any, name: str, size: int) -> bytes:
        if size > self._maximum:
            raise StorageError(f"{name} is larger than {self._maximum} bytes")
        data = bytes(client.get_blob_client(name).download_blob().readall())
        if len(data) != size:
            raise StorageError(f"{name} changed size while it was read")
        return data

    def statements(self, *, known_by: datetime) -> Iterator[Capture]:
        for prefix in self._prefixes:
            for blob in self._raw.list_blobs(name_starts_with=prefix):
                created = getattr(blob, "creation_time", None)
                if not blob.name.lower().endswith(".xml") or created is None:
                    continue
                stored = _utc(created)
                if stored <= known_by:
                    yield Capture(RAW_STATEMENTS_CONTAINER, blob.name, stored,
                                  self._download(self._raw, blob.name, int(blob.size or 0)))

    def market_captures(self, feed: str, *, known_by: datetime) -> Iterator[Capture]:
        for blob in self._market.list_blobs(name_starts_with=f"{feed}/", include=["metadata"]):
            created = getattr(blob, "creation_time", None)
            if created is None:
                continue
            stored = _utc(created)
            received = (getattr(blob, "metadata", None) or {}).get("received_at")
            if received:
                try:
                    stored = max(stored, _utc(datetime.fromisoformat(received)))
                except ValueError:
                    pass
            if stored <= known_by:
                yield Capture(MARKET_DATA_CONTAINER, blob.name, stored,
                              self._download(self._market, blob.name, int(blob.size or 0)))


def _safe(root: Path, path: str) -> Path:
    relative = PurePosixPath(path)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise StorageError(f"Unexpected file name {path!r}")
    return root.joinpath(*relative.parts)


class DirectoryFiles:
    """The :class:`BlobFiles` interface on a local folder."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def read(self, path: str) -> bytes | None:
        target = _safe(self.root, path)
        return target.read_bytes() if target.is_file() else None

    def write(self, path: str, data: bytes, *, content_type: str, overwrite: bool) -> None:
        target = _safe(self.root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb" if overwrite else "xb") as handle:
            handle.write(bytes(data))

    def names(self, prefix: str = "") -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")
                      if p.is_file() and p.relative_to(self.root).as_posix().startswith(prefix))


class FileLineStore:
    """The experiment log's store on a local file, appended a line at a time."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, line: str) -> None:
        if not line or "\n" in line or "\r" in line:
            raise ValueError("An experiment log entry is one non-empty line")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def lines(self) -> list[str]:
        if not self.path.is_file():
            return []
        return [line for line in self.path.read_text(encoding="utf-8").splitlines() if line]
