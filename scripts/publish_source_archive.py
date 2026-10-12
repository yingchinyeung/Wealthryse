"""Create-only source backup + read-back verification; never deploys or approves.

Uses the signed-in Azure CLI identity, not account keys or SAS URLs. Existing
objects are read/verified, never overwritten. Each receipt pins blob versions.
"""
import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from audit_azure_runtime import az
from ibkr_etl.source_archive import canonical, sha256, verify_archive

ACCOUNT = "ibkrv2stgauhecwc4itaoo"
SUBSCRIPTION = "3956e821-cee4-403f-8708-e84e2ed490eb"
TENANT = "0578a50b-a7a1-40a4-905b-8b20dba84124"
ROOT = Path(__file__).resolve().parents[1]


def publish_file(container: str, blob: str, path: Path, expected: str) -> dict:
    if container not in {"release-sources", "release-evidence"}:
        raise ValueError("Unexpected release container")
    if sha256(path.read_bytes()) != expected:
        raise ValueError("Local artifact changed before upload")
    args = ["--account-name", ACCOUNT, "--auth-mode", "login", "--container-name", container,
            "--name", blob]
    properties = az("storage", "container", "show", "--account-name", ACCOUNT,
                    "--auth-mode", "login", "--name", container)
    if properties["properties"].get("publicAccess") not in (None, "None", "none"):
        raise RuntimeError("Release container must be private")
    if not az("storage", "blob", "exists", *args)["exists"]:
        # A concurrent writer or unknown response fails closed. Re-running is
        # safe: it verifies the existing bytes rather than replacing them.
        az("storage", "blob", "upload", *args, "--file", str(path),
           "--overwrite", "false", "--if-none-match", "*", "--type", "block",
           "--content-type", {".zip": "application/zip", ".json": "application/json"}.get(path.suffix, "application/octet-stream"),
           "--metadata", "sha256=" + expected)
    properties = az("storage", "blob", "show", *args)
    version = properties.get("versionId")
    if not version:
        raise RuntimeError("A pinned blob version is required")
    with tempfile.TemporaryDirectory(prefix="release-readback-") as folder:
        downloaded = Path(folder) / "artifact"
        az("storage", "blob", "download", *args, "--version-id", version,
           "--file", str(downloaded), "--no-progress")
        if sha256(downloaded.read_bytes()) != expected:
            raise RuntimeError("Azure read-back hash mismatch; artifact not verified")
    return {"account": ACCOUNT, "container": container, "blob": blob,
            "version_id": version, "sha256": expected,
            "etag": properties["properties"]["etag"], "readback_verified": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive_directory", type=Path)
    args = parser.parse_args()
    folder = args.archive_directory.resolve(strict=True)
    if not folder.is_relative_to((ROOT / ".work" / "azure-native-release").resolve()):
        raise RuntimeError("Only a locally prepared release directory may be published")
    archive_path, record_path = folder / "source.zip", folder / "source-record.json"
    archive = archive_path.read_bytes()
    digest = sha256(archive)
    manifest = verify_archive(archive, digest)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if (folder.name != digest or record.get("source_archive_sha256") != digest
            or record.get("source_tree_sha256") != manifest["tree_sha256"]
            or record.get("schema") != "wealthryse-source-record-v1"
            or record.get("state") != "prepared_not_approved"
            or record.get("deployment_authorized") is not False
            or record.get("build_ready") is not False):
        raise RuntimeError("Source record does not match unapproved source archive")
    identity = az("account", "show", "--query", "{id:id,tenantId:tenantId}")
    if identity != {"id": SUBSCRIPTION, "tenantId": TENANT}:
        raise RuntimeError("Unexpected Azure subscription or tenant")
    policy = az("storage", "account", "blob-service-properties", "show",
                "-g", "rg-ibkr-database-v11", "-n", ACCOUNT,
                "--query", "{versioning:isVersioningEnabled}")
    if policy.get("versioning") is not True:
        raise RuntimeError("Blob versioning is required")
    source = publish_file("release-sources", f"sha256/{digest}/source.zip", archive_path, digest)
    record_digest = sha256(record_path.read_bytes())
    evidence = publish_file("release-evidence", f"sha256/{digest}/{record_digest}/source-record.json",
                            record_path, record_digest)
    receipt = {"schema": "wealthryse-archive-receipt-v1", "state": "archived_not_approved",
               "verified_at": datetime.now(timezone.utc).isoformat(), "source": source,
               "record": evidence, "deployment_authorized": False}
    receipt_path = folder / "archive-receipt.json"
    receipt_path.write_bytes(canonical(receipt))
    receipt_digest = sha256(receipt_path.read_bytes())
    receipt_blob = publish_file("release-evidence",
        f"sha256/{digest}/{receipt_digest}/archive-receipt.json", receipt_path, receipt_digest)
    print(json.dumps({"state": receipt["state"], "source_sha256": digest,
                      "source_files": len(manifest["files"]), "receipt": receipt_blob}))


if __name__ == "__main__":
    main()
