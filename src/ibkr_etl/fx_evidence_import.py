"""Atomic additive evidence staging; never activates analytics or commits.

Bundles are internal trusted-worker inputs, not a user-facing upload API.
Their externally recorded digest must come from verified archive assembly.
"""
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from .fx_evidence_contract import validate_version
from .util import canonical_json, sha256_text

KEYS = {
    "runs": ("source_run_id",),
    "versions": ("canonical_key","row_sha256"),
    "observations": ("source_run_id","canonical_key"),
    "aliases": ("legacy_section","legacy_record_type","legacy_key","row_sha256","source_snapshot_sha256"),
}
COLUMNS = {
    "runs": ("source_run_id","archive_sha256","run_status","source_completed_at"),
    "versions": ("canonical_key","row_sha256","section","record_type","report_date","data_json"),
    "observations": ("source_run_id","canonical_key","row_sha256"),
    "aliases": ("legacy_section","legacy_record_type","legacy_key","row_sha256","canonical_key",
                "source_run_id","legacy_first_seen_run_id","source_snapshot_sha256"),
}
TABLES = {"runs":"fx_evidence_runs","versions":"fx_evidence_versions",
          "observations":"fx_evidence_observations","aliases":"fx_evidence_legacy_aliases"}


def fingerprint(bundle):
    return sha256_text(canonical_json({k:v for k,v in bundle.items() if k!="bundle_sha256"}))


def digest(value):
    if not isinstance(value,str) or not re.fullmatch(r"[0-9a-f]{64}",value):
        raise ValueError("Expected SHA-256 digest")


def read_bundle(path, *, expected_artifact_sha256, expected_bundle_sha256, tenant_id, account_id):
    """Read once and pin both the file bytes and the governed bundle contents."""
    digest(expected_artifact_sha256)
    path=Path(path)
    limit=128*1024*1024
    if path.stat().st_size>limit:
        raise ValueError("Evidence artifact size limit")
    content=path.read_bytes()
    if len(content)>limit or hashlib.sha256(content).hexdigest()!=expected_artifact_sha256:
        raise ValueError("Evidence artifact digest mismatch")
    def unique_pairs(items):
        result={}
        for key,value in items:
            if key in result:
                raise ValueError("Duplicate JSON key in evidence artifact")
            result[key]=value
        return result
    bundle=json.loads(content,object_pairs_hook=unique_pairs)
    validate_bundle(bundle,expected_sha256=expected_bundle_sha256)
    if bundle["tenant_id"]!=tenant_id or bundle["account_id"]!=account_id:
        raise ValueError("Evidence artifact scope mismatch")
    return bundle


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z","+00:00")) if isinstance(value,str) else value
    if not isinstance(parsed,datetime) or parsed.tzinfo is None:
        raise ValueError("Timezone-aware source completion required")
    return parsed.astimezone(timezone.utc).isoformat()


def validate_bundle(bundle, *, expected_sha256):
    digest(expected_sha256)
    if set(bundle)!={"contract_version","tenant_id","account_id","source_namespace","source_snapshot_sha256",*KEYS,"bundle_sha256"}:
        raise ValueError("Unexpected evidence bundle fields")
    if bundle["contract_version"]!=1 or bundle["bundle_sha256"]!=expected_sha256 or fingerprint(bundle)!=expected_sha256:
        raise ValueError("Evidence bundle digest/version mismatch")
    if str(UUID(bundle["tenant_id"]))!=bundle["tenant_id"]:
        raise ValueError("Canonical tenant UUID required")
    if not isinstance(bundle["account_id"],str) or not 1<=len(bundle["account_id"])<=80:
        raise ValueError("Account scope required")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,79}",bundle["source_namespace"]):
        raise ValueError("Governed source namespace required")
    digest(bundle["source_snapshot_sha256"])
    indexed = {}
    for group,keys in KEYS.items():
        rows=bundle[group]
        if not isinstance(rows,list):
            raise ValueError("Expected evidence row list")
        index={}
        for row in rows:
            if set(row)!=set(COLUMNS[group]):
                raise ValueError("Unexpected evidence row fields")
            key=tuple(row[k] for k in keys)
            if key in index:
                raise ValueError("Duplicate evidence identity")
            index[key]=row
        indexed[group]=index
    if not indexed["versions"] or not indexed["observations"]:
        raise ValueError("Empty evidence batch")
    for row in bundle["runs"]:
        if type(row["source_run_id"]) is not int or row["source_run_id"]<=0 or row["run_status"]!="success":
            raise ValueError("Successful source run required")
        digest(row["archive_sha256"])
        if timestamp(row["source_completed_at"])!=row["source_completed_at"]:
            raise ValueError("Canonical UTC completion required")
    for row in bundle["versions"]:
        validate_version({"account_id":bundle["account_id"],**row},account_id=bundle["account_id"])
    observed_versions=set()
    observed_runs=set()
    for row in bundle["observations"]:
        if type(row["source_run_id"]) is not int or (row["source_run_id"],) not in indexed["runs"] or (row["canonical_key"],row["row_sha256"]) not in indexed["versions"]:
            raise ValueError("Observation lacks verified source/version")
        observed_runs.add((row["source_run_id"],))
        observed_versions.add((row["canonical_key"],row["row_sha256"]))
    if observed_runs!=set(indexed["runs"]) or observed_versions!=set(indexed["versions"]):
        raise ValueError("Unreferenced evidence run/version")
    for row in bundle["aliases"]:
        observation=indexed["observations"].get((row["source_run_id"],row["canonical_key"]))
        version=indexed["versions"].get((row["canonical_key"],row["row_sha256"]))
        if (not observation or observation["row_sha256"]!=row["row_sha256"] or not version
                or version["section"]!=row["legacy_section"] or version["record_type"]!=row["legacy_record_type"]
                or row["source_snapshot_sha256"]!=bundle["source_snapshot_sha256"]
                or not isinstance(row["legacy_key"],str) or not row["legacy_key"]
                or type(row["legacy_first_seen_run_id"]) is not int or row["legacy_first_seen_run_id"]<=0):
            raise ValueError("Unverified legacy alias")


def _stage_group(c, *, table, scope, columns, keys, rows, group, prior, batch_size):
    """Bounded SQL batches with complete value readback, never count-only checks."""
    from psycopg import sql
    from psycopg.types.json import Jsonb
    readbacks=[]
    inserted=0
    queries=0
    ordered=sorted(rows,key=canonical_json)
    for start in range(0,len(ordered),batch_size):
        chunk=ordered[start:start+batch_size]
        scope_predicate=sql.SQL(" AND ").join(sql.SQL("{}=%s").format(sql.Identifier(k)) for k in scope)
        key_tuple=sql.SQL("({})").format(sql.SQL(",").join(map(sql.Identifier,keys)))
        placeholders=sql.SQL("({})").format(sql.SQL(",").join(sql.Placeholder() for _ in keys))
        select=sql.SQL("SELECT {} FROM {} WHERE {} AND {} IN ({})").format(
            sql.SQL(",").join(map(sql.Identifier,columns)),table,scope_predicate,key_tuple,
            sql.SQL(",").join(placeholders for _ in chunk))
        parameters=(*scope.values(),*(row[k] for row in chunk for k in keys))
        def fetch():
            nonlocal queries
            queries+=1
            fetched=c.execute(select,parameters).fetchall()
            indexed={tuple(row[k] for k in keys):row for row in fetched}
            if len(indexed)!=len(fetched):
                raise ValueError("Duplicate stored evidence identity")
            return indexed
        existing=fetch()
        missing=[row for row in chunk if tuple(row[k] for k in keys) not in existing]
        if missing:
            if prior:
                raise ValueError("Previously staged evidence missing; no automatic repair")
            values_placeholder=sql.SQL("({})").format(sql.SQL(",").join(sql.Placeholder() for _ in columns))
            inserts=[]
            for row in missing:
                values={**scope,**row}
                inserts.extend(Jsonb(values[k]) if k=="data_json" else values[k] for k in columns)
            c.execute(sql.SQL("INSERT INTO {} ({}) VALUES {} ON CONFLICT DO NOTHING").format(
                table,sql.SQL(",").join(map(sql.Identifier,columns)),
                sql.SQL(",").join(values_placeholder for _ in missing)),inserts)
            queries+=1
            inserted+=c.rowcount
            existing=fetch()
        for row in chunk:
            stored=existing.get(tuple(row[k] for k in keys))
            if stored is None:
                raise ValueError("Evidence insertion not visible")
            if group=="versions":
                stored["report_date"]=stored["report_date"].isoformat()
            if group=="runs":
                stored["source_completed_at"]=timestamp(stored["source_completed_at"])
            stored["tenant_id"]=str(stored["tenant_id"])
            if stored!={**scope,**row}:
                raise ValueError("Evidence conflict; no overwrite permitted")
            readbacks.append(stored)
    return readbacks,inserted,queries


def stage_evidence(connection,bundle,*,expected_sha256,schema="public",batch_size=500):
    """Caller-owned transaction + savepoint. Read back every stored value."""
    from psycopg import sql
    from psycopg.pq import TransactionStatus
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    if type(batch_size) is not int or not 1<=batch_size<=2000:
        raise ValueError("Evidence batch size must be between 1 and 2000")
    # Detach from mutable caller state before checking the externally pinned digest.
    bundle=json.loads(canonical_json(bundle))
    validate_bundle(bundle,expected_sha256=expected_sha256)
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}",schema):
        raise ValueError("Invalid evidence schema")
    if connection.info.transaction_status!=TransactionStatus.INTRANS:
        raise ValueError("Existing caller transaction required")
    tenant,account=bundle["tenant_id"],bundle["account_id"]
    with connection.transaction():
        with connection.cursor(row_factory=dict_row) as c:
            if c.execute("SELECT current_setting('app.tenant_id',true) AS tenant").fetchone()["tenant"]!=tenant:
                raise ValueError("Tenant session mismatch")
            lock=int.from_bytes(hashlib.sha256(canonical_json([tenant,account]).encode()).digest()[:8],"big",signed=True)
            c.execute("SELECT pg_advisory_xact_lock(%s)",(lock,))
            if not c.execute(sql.SQL("SELECT 1 FROM {} WHERE tenant_id=%s AND account_id=%s FOR UPDATE").format(sql.Identifier(schema,"accounts")),(tenant,account)).fetchone():
                raise ValueError("Account ownership mismatch")
            batches=sql.Identifier(schema,"fx_evidence_batches")
            prior=c.execute(sql.SQL("SELECT * FROM {} WHERE tenant_id=%s AND account_id=%s AND bundle_sha256=%s").format(batches),(tenant,account,expected_sha256)).fetchone()
            readbacks={}
            inserted={}
            timings={}
            group_queries={}
            for group,keys in KEYS.items():
                table=sql.Identifier(schema,TABLES[group])
                scope={"tenant_id":tenant,"account_id":account}
                if group!="versions":
                    scope["source_namespace"]=bundle["source_namespace"]
                columns=(*scope,*COLUMNS[group])
                started=time.perf_counter()
                readbacks[group],inserted[group],group_queries[group]=_stage_group(c,table=table,scope=scope,
                    columns=columns,keys=keys,rows=bundle[group],group=group,prior=prior,batch_size=batch_size)
                timings[group]=round(time.perf_counter()-started,4)
            receipt={"contract_version":1,"bundle_sha256":expected_sha256,"tenant_id":tenant,"account_id":account,
                "source_snapshot_sha256":bundle["source_snapshot_sha256"],
                "counts":{k:len(v) for k,v in readbacks.items()},
                "readback_sha256":sha256_text(canonical_json(readbacks)),"execution_authorized":False}
            if prior:
                if (prior["receipt_json"]!=receipt or prior["source_snapshot_sha256"]!=bundle["source_snapshot_sha256"]
                        or prior["execution_authorized"]):
                    raise ValueError("Prior evidence receipt differs")
            else:
                c.execute(sql.SQL("INSERT INTO {} (tenant_id,account_id,bundle_sha256,source_snapshot_sha256,receipt_json) VALUES (%s,%s,%s,%s,%s)").format(batches),
                    (tenant,account,expected_sha256,bundle["source_snapshot_sha256"],Jsonb(receipt)))
                if c.execute(sql.SQL("SELECT receipt_json FROM {} WHERE tenant_id=%s AND account_id=%s AND bundle_sha256=%s").format(batches),
                    (tenant,account,expected_sha256)).fetchone()["receipt_json"]!=receipt:
                    raise ValueError("Receipt readback mismatch")
            return {"receipt":receipt,"inserted":inserted,"group_seconds":timings,"group_queries":group_queries}
