"""Read a bounded archive sample using existing user RBAC; never call Flex."""
import argparse
import json
import sqlite3
from pathlib import Path

from azure.core.exceptions import HttpResponseError
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient

from ibkr_etl.fx_source_evidence import rate_status
from ibkr_etl.snapshot_preflight import archive_blob_location, inspect_archived_xml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    accounts = {"U16088571", "U16088606"}
    with sqlite3.connect(args.source.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        invalid = {}
        runs = set()
        for row in db.execute("SELECT account_id,rate_date,row_sha256,rate_text,last_seen_run_id FROM fx_rates WHERE account_id IN (?,?)", tuple(sorted(accounts))):
            if rate_status(row["rate_text"]) != "positive":
                invalid[(row["account_id"], row["rate_date"], row["row_sha256"])] = row["rate_text"]
                runs.add(row["last_seen_run_id"])
        raw = [dict(r) for r in db.execute("SELECT run_id,content_sha256,byte_count,file_path FROM raw_reports ORDER BY received_at")]
    affected = [r for r in raw if r["run_id"] in runs]
    selected = raw[-2:]
    if affected:
        selected += [affected[0], affected[len(affected)//2], affected[-1]]
    selected = list({r["content_sha256"]: r for r in selected}.values())
    service = BlobServiceClient("https://ibkrv115rcc62xxqblay.blob.core.windows.net", credential=AzureCliCredential())
    results = []
    for row in selected:
        try:
            container, blob = archive_blob_location(row["file_path"], expected_host="ibkrv115rcc62xxqblay.blob.core.windows.net")
            client = service.get_blob_client(container, blob)
            props = client.get_blob_properties()
            if props.size != row["byte_count"] or props.size > 64 * 1024 * 1024:
                raise ValueError("Archive size mismatch")
            result = inspect_archived_xml(client.download_blob().readall(), expected_sha256=row["content_sha256"],
                accounts=accounts, invalid_identities=invalid)
            results.append({"source_run_id": row["run_id"], "archive_sha256": row["content_sha256"], **result})
        except HttpResponseError as error:
            results.append({"source_run_id": row["run_id"], "status": "unavailable", "http_status": error.status_code})
    evidence = {"mode": "read_only", "sample_only": True, "source_completeness": "unverified",
        "permissions_changed": False, "invalid_normalized_observations": len(invalid), "records": results}
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()
