"""Assemble account-scoped evidence from verified raw bytes; no persistence."""
import json
from .fx_evidence_import import fingerprint, timestamp, validate_bundle
from .parser import _source_key, parse_flex_xml
from .util import canonical_json, sha256_text


def build_bundle(*, tenant_id, account_id, source_namespace, source_snapshot_sha256, archives, legacy_rows):
    from .snapshot_preflight import inspect_archived_xml
    runs,versions,observations,aliases={},{},{},{}
    for metadata,content in archives:
        if metadata["tenant_id"]!=tenant_id or metadata["status"]!="success" or not metadata["completed_at"]:
            raise ValueError("Unverified archive run")
        inspect_archived_xml(content,expected_sha256=metadata["raw_sha256"],accounts={account_id})
        run_id=metadata["run_id"]
        run={"source_run_id":run_id,"archive_sha256":metadata["raw_sha256"],"run_status":"success",
             "source_completed_at":timestamp(metadata["completed_at"])}
        if run_id in runs and runs[run_id]!=run:
            raise ValueError("Conflicting archive source run")
        for row in parse_flex_xml(content).rows:
            if row.account_id!=account_id or row.record_type.lower() not in {"conversionrate","fxrate","currencyconversionrate"}:
                continue
            runs[run_id]=run
            version={"canonical_key":row.source_key,"row_sha256":row.row_sha256,"section":row.section,
                "record_type":row.record_type,"report_date":row.report_date,"data_json":row.attributes}
            identity=(row.source_key,row.row_sha256)
            if identity in versions and versions[identity]!=version:
                raise ValueError("Conflicting content version")
            versions[identity]=version
            observation={"source_run_id":run_id,"canonical_key":row.source_key,"row_sha256":row.row_sha256}
            key=(run_id,row.source_key)
            if key in observations and observations[key]!=observation:
                raise ValueError("Multiple FX values for the same source run/key")
            observations[key]=observation
    for row in legacy_rows:
        if row["account_id"]!=account_id or row["tenant_id"]!=tenant_id:
            raise ValueError("Legacy evidence account/tenant mismatch")
        attrs=json.loads(row["data_json"])
        if sha256_text(canonical_json(attrs))!=row["row_sha256"]:
            raise ValueError("Legacy content hash mismatch")
        key=_source_key(account_id,row["section"],row["record_type"],attrs,effective_report_date=row["report_date"])
        alias={"legacy_section":row["section"],"legacy_record_type":row["record_type"],"legacy_key":row["source_key"],
            "row_sha256":row["row_sha256"],"canonical_key":key,"source_run_id":row["last_seen_run_id"],
            "legacy_first_seen_run_id":row["first_seen_run_id"],"source_snapshot_sha256":source_snapshot_sha256}
        identity=(row["section"],row["record_type"],row["source_key"],row["row_sha256"])
        if identity in aliases:
            raise ValueError("Duplicate legacy row")
        aliases[identity]=alias
    result={"contract_version":1,"tenant_id":tenant_id,"account_id":account_id,"source_namespace":source_namespace,
        "source_snapshot_sha256":source_snapshot_sha256,"runs":sorted(runs.values(),key=canonical_json),
        "versions":sorted(versions.values(),key=canonical_json),"observations":sorted(observations.values(),key=canonical_json),
        "aliases":sorted(aliases.values(),key=canonical_json)}
    result["bundle_sha256"]=fingerprint(result)
    validate_bundle(result,expected_sha256=result["bundle_sha256"])
    return result
