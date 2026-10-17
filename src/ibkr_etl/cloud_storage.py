from __future__ import annotations

import mimetypes
import os
from pathlib import Path


_CONTENT_TYPE_OVERRIDES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".sqlite": "application/vnd.sqlite3",
    ".xml": "application/xml",
    ".zip": "application/zip",
}


def content_type_for_path(path: Path) -> str:
    """Return a stable MIME type for artifacts published to Azure Blob Storage."""
    return _CONTENT_TYPE_OVERRIDES.get(
        path.suffix.lower(),
        mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    )


def _service(account_url: str, credential=None, **client_options):
    if not account_url:
        return None
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
    except ImportError as error:
        raise RuntimeError(
            "Azure Blob mode requires azure-identity and azure-storage-blob."
        ) from error
    if credential is None:
        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return BlobServiceClient(account_url=account_url, credential=credential, **client_options)


def best_effort_credential(*, connection_timeout: float, read_timeout: float):
    """Managed identity only (AZURE_CLIENT_ID picks the user-assigned one): one token
    request with short timeouts and no retries, so a hung identity endpoint fails in
    seconds instead of walking DefaultAzureCredential's chain."""
    from azure.identity import ManagedIdentityCredential

    return ManagedIdentityCredential(
        client_id=os.environ.get("AZURE_CLIENT_ID", "").strip() or None,
        connection_timeout=connection_timeout,
        read_timeout=read_timeout,
        retry_total=0,
    )


def container_client(account_url: str, container: str, *, credential=None, **client_options):
    """One reusable container client. Best-effort writers pass best_effort_credential,
    short timeouts and retry_total=0 so a storage problem cannot hold up their job."""
    service = _service(account_url, credential, **client_options)
    return None if service is None else service.get_container_client(container)


def upload_bytes(
    account_url: str,
    container: str,
    blob_name: str,
    content: bytes,
    *,
    overwrite: bool = False,
    content_type: str | None = None,
    metadata: dict[str, str] | None = None,
) -> str | None:
    service = _service(account_url)
    if service is None:
        return None
    client = service.get_blob_client(container=container, blob=blob_name)
    return upload_with_client(
        client, content, overwrite=overwrite, content_type=content_type, metadata=metadata
    )


def upload_with_client(
    client,
    content: bytes,
    *,
    overwrite: bool = False,
    content_type: str | None = None,
    metadata: dict[str, str] | None = None,
) -> str:
    """Upload through an existing blob client (see container_client)."""
    upload_options = {"overwrite": overwrite}
    if metadata is not None:
        upload_options["metadata"] = metadata
    if content_type:
        from azure.storage.blob import ContentSettings

        upload_options["content_settings"] = ContentSettings(content_type=content_type)
    client.upload_blob(content, **upload_options)
    return client.url


def upload_file(
    account_url: str,
    container: str,
    blob_name: str,
    path: Path,
    *,
    overwrite: bool = True,
    content_type: str | None = None,
) -> str | None:
    return upload_bytes(
        account_url,
        container,
        blob_name,
        path.read_bytes(),
        overwrite=overwrite,
        content_type=content_type or content_type_for_path(path),
    )


def download_file_if_exists(
    account_url: str,
    container: str,
    blob_name: str,
    destination: Path,
) -> bool:
    service = _service(account_url)
    if service is None:
        return False
    client = service.get_blob_client(container=container, blob=blob_name)
    if not client.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(client.download_blob().readall())
    return True


def list_blobs(account_url: str, container: str, prefix: str) -> list[dict]:
    """Names and sizes of the blobs under prefix."""
    service = _service(account_url)
    if service is None:
        return []
    client = service.get_container_client(container)
    return [{"name": blob.name, "size": int(blob.size or 0)} for blob in client.list_blobs(name_starts_with=prefix)]


def newest_blob_time(client, prefix: str, *, limit: int = 500):
    """Latest last-modified time among the first `limit` blobs under prefix, or None.

    `client` is a container client (see container_client). Read-only."""
    newest = None
    for index, blob in enumerate(client.list_blobs(name_starts_with=prefix, results_per_page=limit)):
        if index >= limit:
            break
        modified = getattr(blob, "last_modified", None)
        if modified is not None and (newest is None or modified > newest):
            newest = modified
    return newest


def blob_exists(account_url: str, container: str, blob_name: str) -> bool:
    service = _service(account_url)
    if service is None:
        return False
    return bool(service.get_blob_client(container=container, blob=blob_name).exists())


def download_blob_bytes(
    account_url: str,
    container: str,
    blob_name: str,
    *,
    maximum_bytes: int = 25 * 1024 * 1024,
) -> bytes:
    """Download one private artifact with an explicit in-memory size limit."""

    service = _service(account_url)
    if service is None:
        raise RuntimeError("Azure Blob storage is not configured")
    client = service.get_blob_client(container=container, blob=blob_name)
    properties = client.get_blob_properties()
    if int(properties.size) > maximum_bytes:
        raise RuntimeError("The requested artifact exceeds the download size limit")
    content = bytes(client.download_blob(max_concurrency=1).readall())
    if len(content) != int(properties.size):
        raise RuntimeError("The downloaded artifact size does not match Blob Storage")
    return content


def download_blob_snapshot(account_url: str, container: str, blob_name: str, *,
                           maximum_bytes: int = 8 * 1024 * 1024):
    """Read one version/ETag-bound artifact; only a real 404 means unavailable.

    A concurrent overwrite, denial or oversized object is an error, never a
    reason to fall back to a different source. Does not mutate Blob Storage.
    """
    import hashlib
    from azure.core import MatchConditions
    from azure.core.exceptions import ResourceNotFoundError

    if type(maximum_bytes) is not int or maximum_bytes <= 0:
        raise ValueError("Positive snapshot size limit required")
    service = _service(account_url)
    if service is None:
        raise RuntimeError("Azure Blob storage is not configured")
    client = service.get_blob_client(container=container, blob=blob_name)
    try:
        properties = client.get_blob_properties()
    except ResourceNotFoundError:
        return None
    size = int(properties.size)
    if not 0 < size <= maximum_bytes:
        raise ValueError("Invalid snapshot size")
    etag = properties.etag
    version = properties.version_id
    if type(etag) is not str or not etag:
        raise ValueError("Snapshot ETag missing")
    if version is not None:
        if type(version) is not str or not version:
            raise ValueError("Snapshot version invalid")
        client = service.get_blob_client(container=container, blob=blob_name, version_id=version)
    content = bytes(client.download_blob(etag=etag,
        match_condition=MatchConditions.IfNotModified, max_concurrency=1).readall())
    if len(content) != size:
        raise ValueError("Snapshot size changed")
    return content, {"container": container, "blob_name": blob_name,
                     "version_id": version, "etag": etag, "bytes": size,
                     "content_sha256": hashlib.sha256(content).hexdigest(),
                     "storage_snapshot_verified": True}
