"""Read registered archives with existing RBAC; rehearse keys only in memory."""
import argparse
import hashlib
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from azure.core import MatchConditions
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient

from ibkr_etl.archive_evidence_rehearsal import EvidenceRehearsal, reused_archive_runs
from ibkr_etl.snapshot_preflight import archive_blob_location


class StorageReadCredential:
    """Cache the SDK token in memory; serialize CLI refresh across read threads."""
    def __init__(self):
        self.delegate = AzureCliCredential(process_timeout=60)
        self.token = None
        self.lock = threading.Lock()

    def get_token(self, *scopes, **kwargs):
        if scopes != ("https://storage.azure.com/.default",):
            raise ValueError("Diagnostic credential is storage-scoped")
        with self.lock:
            if self.token is None or self.token.expires_on <= time.time() + 300:
                self.token = self.delegate.get_token(*scopes, **kwargs)
            return self.token


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--bundle-dir", type=Path)
    args = parser.parse_args()
    if args.evidence.exists():
        raise ValueError("Do not overwrite previous evidence")
    source = args.source.resolve()
    if hashlib.sha256(source.read_bytes()).hexdigest() != args.source_sha256:
        raise ValueError("Source export digest mismatch")
    accounts = ("U16088571", "U16088606")
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        raw = [dict(r) for r in db.execute("""SELECT a.*,r.status AS run_status,r.raw_sha256 AS run_sha256,
            r.tenant_id AS run_tenant,r.completed_at AS run_completed_at
            FROM raw_reports a LEFT JOIN ingestion_runs r ON a.run_id=r.run_id ORDER BY a.received_at,a.raw_report_id""")]
        normalized = [dict(r) for r in db.execute("SELECT * FROM fx_rates WHERE account_id IN (?,?)", accounts)]
        legacy = [dict(r) for r in db.execute("""SELECT * FROM flex_rows WHERE account_id IN (?,?)
            AND lower(record_type) IN ('conversionrate','fxrate','currencyconversionrate')""", accounts)]
        runs = [dict(r) for r in db.execute("SELECT run_id,tenant_id,status,completed_at,raw_sha256 FROM ingestion_runs")]
    tenant = "00000000-0000-0000-0000-000000000001"
    if (not raw or len(raw) > 600 or sum(r["byte_count"] for r in raw) > 256*1024*1024
            or any(r["tenant_id"] != tenant or r["run_status"] != "success"
                   or r["run_sha256"] != r["content_sha256"] or r["run_tenant"] != tenant
                   or not r["run_completed_at"] for r in raw)
            or any(r["tenant_id"] != tenant for r in legacy)):
        raise ValueError("Unreviewed archive inventory or lineage")
    rehearsal = EvidenceRehearsal(accounts=accounts, normalized=normalized, legacy=legacy)
    reuse = reused_archive_runs(archive_rows=raw, source_runs=runs,
        required_runs={r["last_seen_run_id"] for r in normalized + legacy}, tenant_id=tenant)
    credential = StorageReadCredential()
    credential.get_token("https://storage.azure.com/.default")
    service = BlobServiceClient("https://ibkrv115rcc62xxqblay.blob.core.windows.net",
        credential=credential, retry_total=0)

    def fetch(row):
        container, blob = archive_blob_location(row["file_path"], expected_host="ibkrv115rcc62xxqblay.blob.core.windows.net")
        if not 0 < row["byte_count"] <= 64*1024*1024:
            raise ValueError("Archive size limit")
        client = service.get_blob_client(container, blob)
        props = client.get_blob_properties()
        if props.size != row["byte_count"]:
            raise ValueError("Archive byte count mismatch")
        content = client.download_blob(max_concurrency=1, etag=props.etag,
            match_condition=MatchConditions.IfNotModified).readall()
        if len(content) != row["byte_count"]:
            raise ValueError("Downloaded byte count mismatch")
        return content

    failures = []
    verified_archives = []
    bundle_archives = []
    run_lookup = {r["run_id"]:r for r in runs}
    idempotence_checks = 0
    started = datetime.now(timezone.utc).isoformat()
    with ThreadPoolExecutor(max_workers=4) as pool:
        for offset in range(0, len(raw), 8):
            batch = raw[offset:offset+8]
            tasks = [pool.submit(fetch, row) for row in batch]
            for row, task in zip(batch, tasks):
                try:
                    content = task.result()
                    rehearsal.add_archive(content, expected_sha256=row["content_sha256"], run_id=row["run_id"])
                    for repeated_run in reuse.get(row["content_sha256"], []):
                        rehearsal.add_archive(content, expected_sha256=row["content_sha256"], run_id=repeated_run)
                    if args.bundle_dir:
                        bundle_archives.append((run_lookup[row["run_id"]],content))
                        bundle_archives.extend((run_lookup[r],content) for r in reuse.get(row["content_sha256"], []))
                    if idempotence_checks < 2:
                        before = rehearsal.summary()
                        if rehearsal.add_archive(content, expected_sha256=row["content_sha256"], run_id=row["run_id"]) or before != rehearsal.summary():
                            raise ValueError("Repeat replay changed evidence")
                        idempotence_checks += 1
                    verified_archives.append({"run_id": row["run_id"], "sha256": row["content_sha256"]})
                except Exception as error:
                    failures.append({"run_id": row["run_id"], "error_type": type(error).__name__,
                                     "http_status": getattr(error, "status_code", None)})
            print(json.dumps({"checked": min(offset+8,len(raw)), "total": len(raw), "failures": len(failures)}), flush=True)
    unchanged = hashlib.sha256(source.read_bytes()).hexdigest() == args.source_sha256
    result = {"started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": args.source_sha256, "source_file_unchanged": unchanged,
        "registered_archives": len(raw), "verified_archives": verified_archives,
        "reused_content_run_links": reuse,
        "failures": failures, "idempotence_checks": idempotence_checks, **rehearsal.summary()}
    result["rehearsal_passed"] = (unchanged and not failures and len(verified_archives) == len(raw)
        and not result["normalized_fx_unmatched"] and not result["legacy_aliases_unmatched"])
    if args.bundle_dir:
        if not result["rehearsal_passed"]:
            raise ValueError("Do not assemble bundles from incomplete verification")
        from ibkr_etl.fx_evidence_bundle import build_bundle
        from ibkr_etl.util import canonical_json
        destination=args.bundle_dir.resolve()
        private_root=Path(__file__).resolve().parents[1]/".work"
        if not destination.is_relative_to(private_root.resolve()):
            raise ValueError("Evidence bundles must stay in private .work storage")
        destination.mkdir(parents=True,exist_ok=True)
        result["bundles"]=[]
        for account in accounts:
            print(json.dumps({"phase":"assembling_bundle","account_id":account}),flush=True)
            bundle=build_bundle(tenant_id=tenant,account_id=account,source_namespace="ibkr-v11",
                source_snapshot_sha256=args.source_sha256,archives=bundle_archives,
                legacy_rows=[r for r in legacy if r["account_id"]==account])
            artifact=canonical_json(bundle).encode()
            artifact_hash=hashlib.sha256(artifact).hexdigest()
            target=destination/(account+"-"+artifact_hash+".json")
            if target.exists() and target.read_bytes()!=artifact:
                raise ValueError("Existing evidence artifact differs")
            if not target.exists():
                target.write_bytes(artifact)
            result["bundles"].append({"account_id":account,"bundle_sha256":bundle["bundle_sha256"],
                "artifact_sha256":artifact_hash,"counts":{g:len(bundle[g]) for g in ("runs","versions","observations","aliases")},
                "path":str(target),"imported":False})
            print(json.dumps({"phase":"bundle_ready","account_id":account,"artifact_sha256":artifact_hash}),flush=True)
    result["archive_verification_completed_at"]=result.pop("completed_at")
    result["completed_at"]=datetime.now(timezone.utc).isoformat()
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k != "verified_archives"}))


if __name__ == "__main__":
    main()
