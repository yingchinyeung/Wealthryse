"""Create-only, readback-verified monthly MS review uploads."""
from azure.core.exceptions import ResourceExistsError
from ibkr_etl.cloud_storage import download_blob_bytes, upload_bytes


def upload_ms_review(storage_url, container, blob_name, content, *, content_type):
    try:
        uploaded = upload_bytes(storage_url, container, blob_name, content,
                                overwrite=False, content_type=content_type)
    except ResourceExistsError:
        # An identical immutable artifact is a safe resume, not an overwrite.
        if download_blob_bytes(storage_url, container, blob_name) != content:
            raise RuntimeError('Existing immutable MS report differs')
        uploaded = f'{storage_url.rstrip("/")}/{container}/{blob_name}'
    if not uploaded:
        raise RuntimeError('Private MS report storage is unavailable')
    if download_blob_bytes(storage_url, container, blob_name) != content:
        raise RuntimeError('Immutable MS report readback differs')
    return uploaded
