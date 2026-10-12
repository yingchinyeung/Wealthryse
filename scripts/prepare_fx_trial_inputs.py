"""Retain two verified, content-addressed FX inputs in existing private storage.

No overwrite, new container, SAS, role grant, raw XML or production DB writes.
"""
import hashlib
import json
from pathlib import Path

from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, HttpResponseError
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from audit_azure_runtime import az
from ibkr_etl.fx_evidence_import import read_bundle
from ibkr_etl.util import utc_now_iso

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / ".work/archive-evidence-20261002/bundles"
TENANT = "00000000-0000-0000-0000-000000000001"
SOURCE = "c181220e93c60a629f70ea402a7e41ee013edbde3496b70882b5311b68f78e9d"
INPUTS = (
    ("U16088571", "004f396fb118d5ef284c20bacc604cc1cbd7be9b67f6476e61ac11a4d70617e6", "2eace02e50c31eee44bb0a12d4692c35ec3c5d9fdde1a16612a8160cb0b6b831"),
    ("U16088606", "f908d513eade699e6a28fa621528eab6b5c0ab2cee45a10cd07a41cf6b53322c", "50470d40fd68233705d2f1da0dceb62c514461350a5da3cb75110d85d85be4c9"),
)


def main():
    if az("account", "show", "--query", "id") != "3956e821-cee4-403f-8708-e84e2ed490eb":
        raise ValueError("Unexpected subscription")
    service = BlobServiceClient("https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net", credential=AzureCliCredential())
    container = service.get_container_client("input-snapshots")
    if container.get_container_properties().get("public_access") is not None:
        raise ValueError("Private input container required")
    records = []
    for account, artifact, bundle_hash in INPUTS:
        path = FOLDER / (account + "-" + artifact + ".json")
        bundle = read_bundle(path, expected_artifact_sha256=artifact, expected_bundle_sha256=bundle_hash,
                             tenant_id=TENANT, account_id=account)
        if bundle["source_snapshot_sha256"] != SOURCE:
            raise ValueError("Unexpected source snapshot")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != artifact:
            raise ValueError("Artifact changed before upload")
        name = f"{TENANT}/fx-evidence-trials/{account}/{artifact}.json"
        blob = container.get_blob_client(name)
        try:
            blob.upload_blob(content, overwrite=False, content_settings=ContentSettings(content_type="application/json"),
                             metadata={"purpose": "rollback-only-fx-trial", "sha256": artifact})
        except ResourceExistsError:
            pass  # Existing bytes must verify; never replace them.
        except HttpResponseError as error:
            if error.status_code == 403:
                raise RuntimeError("Private input upload denied by Azure RBAC; no permission changes or alternate credentials attempted") from None
            raise
        props = blob.get_blob_properties()
        if props.size != len(content):
            raise ValueError("Private artifact byte count differs")
        stored = blob.download_blob(etag=props.etag, match_condition=MatchConditions.IfNotModified).readall()
        if hashlib.sha256(stored).hexdigest() != artifact:
            raise ValueError("Private artifact readback differs")
        record = dict(account_id=account, artifact_sha256=artifact, bundle_sha256=bundle_hash,
                      blob_name=name, etag=props.etag, byte_count=len(content))
        records.append(record)
        print(json.dumps({"account_id": account, "private_artifact_verified": True, "byte_count": len(content)}), flush=True)
    result = dict(tenant_id=TENANT, source_snapshot_sha256=SOURCE, container="input-snapshots", records=records,
                  prepared_at=utc_now_iso(), execution_authorized=False)
    target = ROOT / ".work/fx-evidence-schema-20261002/real-inputs.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(target), "artifacts": len(records), "execution_authorized": False}))


if __name__ == "__main__":
    main()
