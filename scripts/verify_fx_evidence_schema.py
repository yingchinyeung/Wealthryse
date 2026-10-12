"""Rehearse migration 055 in an isolated schema; forcibly roll back all objects."""
import json
import os
import copy
import time
import tempfile
from pathlib import Path
from datetime import date,timedelta

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from ibkr_etl.util import canonical_json, sha256_text
from ibkr_etl.parser import _source_key
from ibkr_etl.fx_evidence_import import fingerprint, stage_evidence, read_bundle
from ibkr_etl.fx_import_lifecycle import import_contract, stage_import_run, CONTRACT_KEY, RECEIPT_KEY, RUN_TYPE
from ibkr_etl.evidence_acl import quarantine_evidence_objects, TABLES as PROTECTED_TABLES, FUNCTIONS as PROTECTED_FUNCTIONS

SCHEMA = "ibkr_fx_evidence_schema_trial"
ROLE = "ibkr_fx_evidence_schema_trial_role"
TABLES = ("fx_evidence_runs", "fx_evidence_versions", "fx_evidence_observations",
          "fx_evidence_legacy_aliases", "fx_evidence_batches")


def main():
    tenant = os.environ["PORTFOLIO_TENANT_ID"]
    real_bundles = load_real_inputs(tenant)
    other = "00000000-0000-0000-0000-000000000002"
    checks = []
    def check(name, condition):
        if not condition:
            raise ValueError("Rehearsal failed: " + name)
        checks.append(name)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row,
        connect_timeout=25, options="-c statement_timeout=60000 -c lock_timeout=5000") as c:
        def absent():
            return (not c.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (SCHEMA,)).fetchone()
                and not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (ROLE,)).fetchone())
        check("no_preexisting_trial_objects", absent())
        with c.transaction(force_rollback=True):
            c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(SCHEMA)))
            c.execute(sql.SQL("SET LOCAL search_path TO {},pg_catalog").format(sql.Identifier(SCHEMA)))
            c.execute("CREATE TABLE tenants (tenant_id UUID PRIMARY KEY)")
            c.execute("CREATE TABLE accounts (account_id TEXT PRIMARY KEY,tenant_id UUID REFERENCES tenants)")
            c.execute("CREATE TABLE ingestion_runs (LIKE public.ingestion_runs INCLUDING CONSTRAINTS INCLUDING INDEXES)")
            c.execute("ALTER TABLE ingestion_runs ENABLE ROW LEVEL SECURITY")
            c.execute("ALTER TABLE ingestion_runs FORCE ROW LEVEL SECURITY")
            c.execute("CREATE POLICY trial_run_tenant ON ingestion_runs USING (tenant_id::text=current_setting('app.tenant_id',true)) WITH CHECK (tenant_id::text=current_setting('app.tenant_id',true))")
            c.execute("INSERT INTO tenants VALUES (%s),(%s)", (tenant,other))
            c.execute("INSERT INTO accounts VALUES ('trial-A',%s),('trial-B',%s)", (tenant,other))
            c.execute("""CREATE FUNCTION app_tenant_id() RETURNS uuid LANGUAGE sql STABLE AS
                $$ SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid $$""")
            c.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS").format(sql.Identifier(ROLE)))
            c.execute(sql.SQL("GRANT {} TO CURRENT_USER").format(sql.Identifier(ROLE)))
            c.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(SCHEMA),sql.Identifier(ROLE)))
            # Reproduce named default ACLs observed on the real public schema.
            c.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO {}")
                      .format(sql.Identifier(SCHEMA),sql.Identifier(ROLE)))
            c.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT EXECUTE ON FUNCTIONS TO {}")
                      .format(sql.Identifier(SCHEMA),sql.Identifier(ROLE)))
            c.execute("CREATE TABLE acl_control_probe (value integer)")
            c.execute("CREATE FUNCTION acl_control_probe() RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$")
            check("named_default_acl_reproduced",c.execute("SELECT has_table_privilege(%s,%s,'INSERT') AS allowed",
                (ROLE,SCHEMA+'.acl_control_probe')).fetchone()["allowed"])
            c.execute(WORKFLOW_MIGRATION_SQL)
            c.execute(MIGRATION_SQL)
            def protected_denied():
                return all(not c.execute("SELECT has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS allowed",
                    (ROLE,SCHEMA+'.'+name)).fetchone()["allowed"] for name in PROTECTED_TABLES)
            def functions_denied():
                return all(not c.execute("SELECT has_function_privilege(%s,%s,'EXECUTE') AS allowed",
                    (ROLE,SCHEMA+'.'+name+'()')).fetchone()["allowed"] for name in PROTECTED_FUNCTIONS)
            check("both_migrations_remove_named_default_table_grants",protected_denied())
            check("both_migrations_remove_named_default_function_grants",functions_denied())
            c.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {} TO {}")
                      .format(sql.Identifier(SCHEMA),sql.Identifier(ROLE)))
            c.execute(sql.SQL("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {} TO {}")
                      .format(sql.Identifier(SCHEMA),sql.Identifier(ROLE)))
            acl=quarantine_evidence_objects(c,schema=SCHEMA)
            check("bootstrap_quarantine_removes_only_protected_grants",acl["revoked_object_grantees"]==9 and protected_denied() and functions_denied())
            check("bootstrap_quarantine_preserves_other_table_access",c.execute("SELECT has_table_privilege(%s,%s,'INSERT') AS allowed",
                (ROLE,SCHEMA+'.acl_control_probe')).fetchone()["allowed"])
            check("bootstrap_quarantine_preserves_other_function_access",c.execute("SELECT has_function_privilege(%s,%s,'EXECUTE') AS allowed",
                (ROLE,SCHEMA+'.acl_control_probe()')).fetchone()["allowed"])
            check("bootstrap_quarantine_idempotent",quarantine_evidence_objects(c,schema=SCHEMA)["revoked_object_grantees"]==0)
            check("five_tables_created", c.execute("SELECT count(*) AS n FROM pg_tables WHERE schemaname=%s AND tablename=ANY(%s)", (SCHEMA,list(TABLES))).fetchone()["n"] == 5)
            flags = c.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class p JOIN pg_namespace n ON n.oid=p.relnamespace WHERE n.nspname=%s AND p.relname=ANY(%s)", (SCHEMA,list(TABLES))).fetchall()
            check("all_tables_force_rls", len(flags)==5 and all(r["relrowsecurity"] and r["relforcerowsecurity"] for r in flags))
            c.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE,TRUNCATE ON ALL TABLES IN SCHEMA {} TO {}").format(sql.Identifier(SCHEMA),sql.Identifier(ROLE)))
            c.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(ROLE)))
            c.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
            def rejected(name, state, action):
                try:
                    with c.transaction():
                        action()
                except psycopg.Error as error:
                    check(name, error.sqlstate == state)
                else:
                    raise ValueError("Expected rejection: " + name)
            def run(run_id, sha):
                c.execute("INSERT INTO fx_evidence_runs VALUES (%s,'trial-A','v11-trial',%s,%s,'success','2025-01-21T00:00:00Z')", (tenant,run_id,sha))
            key = "fx-rate:v2:" + "c"*64
            attrs1 = canonical_json({"fromCurrency":"HKD","toCurrency":"USD","rate":"0.128"})
            attrs2 = canonical_json({"fromCurrency":"HKD","toCurrency":"USD","rate":"0.129"})
            hash1,hash2 = sha256_text(attrs1),sha256_text(attrs2)
            def version(h, attrs, account="trial-A"):
                c.execute("INSERT INTO fx_evidence_versions (tenant_id,account_id,canonical_key,row_sha256,section,record_type,report_date,data_json) VALUES (%s,%s,%s,%s,'ConversionRates','ConversionRate','2025-01-20',%s::jsonb)", (tenant,account,key,h,attrs))
            def observation(run_id, h):
                c.execute("INSERT INTO fx_evidence_observations VALUES (%s,'trial-A','v11-trial',%s,%s,%s)", (tenant,run_id,key,h))
            run(101,"a"*64)
            version(hash1,attrs1)
            observation(101,hash1)
            check("initial_evidence_persisted_in_trial", c.execute("SELECT count(*) AS n FROM fx_evidence_observations").fetchone()["n"]==1)
            rejected("unknown_source_run_rejected","23503",lambda: observation(999,hash1))
            run(102,"b"*64)
            rejected("unknown_version_rejected","23503",lambda: observation(102,"e"*64))
            version(hash2,attrs2)
            observation(102,hash2)
            check("correction_retains_both_versions", c.execute("SELECT count(*) AS n FROM fx_evidence_versions").fetchone()["n"]==2)
            rejected("one_value_per_key_per_run","23505",lambda: observation(102,hash1))
            run(103,"a"*64)
            observation(103,hash1)
            check("repeat_run_does_not_duplicate_version", c.execute("SELECT count(*) AS n FROM fx_evidence_versions").fetchone()["n"]==2)
            c.execute("INSERT INTO fx_evidence_legacy_aliases VALUES (%s,'trial-A','v11-trial','ConversionRates','ConversionRate','legacy-key',%s,%s,101,90,%s)", (tenant,hash1,key,"d"*64))
            check("alias_preserves_first_seen_metadata", c.execute("SELECT legacy_first_seen_run_id FROM fx_evidence_legacy_aliases").fetchone()["legacy_first_seen_run_id"]==90)
            c.execute("INSERT INTO fx_evidence_batches (tenant_id,account_id,bundle_sha256,source_snapshot_sha256,receipt_json) VALUES (%s,'trial-A',%s,%s,'{}')", (tenant,"b"*64,"d"*64))
            rejected("batch_cannot_authorize_execution","23514",lambda: c.execute("INSERT INTO fx_evidence_batches (tenant_id,account_id,bundle_sha256,source_snapshot_sha256,receipt_json,execution_authorized) VALUES (%s,'trial-A',%s,%s,'{}',true)", (tenant,"f"*64,"d"*64)))
            rejected("cross_account_tenant_insert_denied","42501",lambda: version("f"*64,attrs1,"trial-B"))
            for table in TABLES:
                rejected(table+"_update_denied","P0001",lambda t=table: c.execute(sql.SQL("UPDATE {} SET account_id=account_id").format(sql.Identifier(t))))
                rejected(table+"_delete_denied","P0001",lambda t=table: c.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(t))))
                rejected(table+"_truncate_denied","P0001",lambda t=table: c.execute(sql.SQL("TRUNCATE {} CASCADE").format(sql.Identifier(t))))
            c.execute("SELECT set_config('app.tenant_id',%s,true)", (other,))
            check("other_tenant_sees_no_evidence", all(c.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(t))).fetchone()["n"]==0 for t in TABLES))
            c.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
            try:
                with c.transaction():
                    run(104,"f"*64)
                    observation(104,hash1)
                    raise RuntimeError("synthetic late failure")
            except RuntimeError:
                pass
            check("late_failure_rolls_back_run_and_observation", not c.execute("SELECT 1 FROM fx_evidence_runs WHERE source_run_id=104").fetchone() and not c.execute("SELECT 1 FROM fx_evidence_observations WHERE source_run_id=104").fetchone())
            exercise_importer(c,tenant,check)
            exercise_lifecycle(c,tenant,other,check)
            real_results=exercise_real_bundles(c,tenant,check,real_bundles)
            scale_results=exercise_full_size(c,tenant,check) if globals().get("RUN_FULL_SIZE", True) else []
            c.execute("RESET ROLE")
        check("trial_schema_and_role_rolled_back", absent())
    print("FX_EVIDENCE_SCHEMA_TRIAL " + json.dumps({"passed":True,"checks":checks,"check_count":len(checks),
        "mode":"isolated_schema_force_rollback","live_migration_applied":False,
        "full_size_exercised":bool(scale_results),"full_size_results":scale_results,
        "real_bundle_results":real_results}), flush=True)


def exercise_importer(c,tenant,check):
    attrs={"accountId":"trial-A","fromCurrency":"HKD","toCurrency":"USD","rate":"0.128"}
    key=_source_key("trial-A","ConversionRates","ConversionRate",attrs,effective_report_date="2025-01-20")
    row_hash=sha256_text(canonical_json(attrs))
    b={"contract_version":1,"tenant_id":tenant,"account_id":"trial-A","source_namespace":"component-trial",
       "source_snapshot_sha256":"a"*64,
       "runs":[{"source_run_id":201,"archive_sha256":"b"*64,"run_status":"success","source_completed_at":"2025-01-21T00:00:00+00:00"}],
       "versions":[{"canonical_key":key,"row_sha256":row_hash,"section":"ConversionRates","record_type":"ConversionRate","report_date":"2025-01-20","data_json":attrs}],
       "observations":[{"source_run_id":201,"canonical_key":key,"row_sha256":row_hash}],
       "aliases":[{"legacy_section":"ConversionRates","legacy_record_type":"ConversionRate","legacy_key":"legacy-component",
                   "row_sha256":row_hash,"canonical_key":key,"source_run_id":201,"legacy_first_seen_run_id":190,"source_snapshot_sha256":"a"*64}]}
    def seal(bundle):
        bundle["bundle_sha256"]=fingerprint(bundle)
        return bundle
    seal(b)
    def stage(bundle=b):
        return stage_evidence(c,bundle,expected_sha256=bundle["bundle_sha256"],schema=SCHEMA)
    first=stage()
    check("component_inserts_all_four_evidence_groups",sum(first["inserted"].values())==4)
    check("component_receipt_does_not_authorize_execution",first["receipt"]["execution_authorized"] is False)
    again=stage()
    check("component_repeat_is_idempotent",sum(again["inserted"].values())==0 and first["receipt"]==again["receipt"])
    single=stage_evidence(c,b,expected_sha256=b["bundle_sha256"],schema=SCHEMA,batch_size=1)
    check("batch_size_does_not_change_receipt",single["receipt"]==first["receipt"])
    newer=copy.deepcopy(b)
    newer["source_snapshot_sha256"]="c"*64
    newer["aliases"][0]["source_snapshot_sha256"]="c"*64
    seal(newer)
    next_result=stage(newer)
    check("new_snapshot_retains_prior_alias",next_result["inserted"]["aliases"]==1 and next_result["inserted"]["versions"]==0)
    check("both_alias_snapshot_origins_retained",c.execute("SELECT count(*) AS n FROM fx_evidence_legacy_aliases WHERE legacy_key='legacy-component'").fetchone()["n"]==2)
    bad=copy.deepcopy(b)
    bad["runs"].append({**b["runs"][0],"source_run_id":202})
    bad["observations"].append({**b["observations"][0],"source_run_id":202})
    bad["aliases"][0]["legacy_first_seen_run_id"]=191
    seal(bad)
    try:
        stage(bad)
    except ValueError as error:
        check("late_alias_conflict_rejected",str(error)=="Evidence conflict; no overwrite permitted")
    else:
        raise ValueError("Component accepted conflicting alias")
    check("component_late_failure_rolls_back_new_run",not c.execute("SELECT 1 FROM fx_evidence_runs WHERE source_namespace='component-trial' AND source_run_id=202").fetchone())
    check("component_late_failure_rolls_back_observation",not c.execute("SELECT 1 FROM fx_evidence_observations WHERE source_namespace='component-trial' AND source_run_id=202").fetchone())
    check("component_late_failure_records_no_receipt",not c.execute("SELECT 1 FROM fx_evidence_batches WHERE bundle_sha256=%s",(bad["bundle_sha256"],)).fetchone())
    check("prior_receipt_unchanged_after_failure",stage()["receipt"]==first["receipt"])
    # Only the isolated fixture owner bypasses append-only triggers to simulate
    # out-of-band corruption. Never do this against production evidence.
    c.execute("RESET ROLE")
    c.execute("ALTER TABLE fx_evidence_versions DISABLE TRIGGER fx_evidence_append_only")
    c.execute("UPDATE fx_evidence_versions SET data_json='{}' WHERE canonical_key=%s",(key,))
    c.execute("ALTER TABLE fx_evidence_versions ENABLE TRIGGER fx_evidence_append_only")
    c.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(ROLE)))
    try:
        stage()
    except ValueError as error:
        check("same_count_content_corruption_detected",str(error)=="Evidence conflict; no overwrite permitted")
    else:
        raise ValueError("Component accepted corrupted content")
    c.execute("RESET ROLE")
    c.execute("ALTER TABLE fx_evidence_versions DISABLE TRIGGER fx_evidence_append_only")
    c.execute("UPDATE fx_evidence_versions SET data_json=%s::jsonb WHERE canonical_key=%s",(canonical_json(attrs),key))
    c.execute("ALTER TABLE fx_evidence_versions ENABLE TRIGGER fx_evidence_append_only")
    c.execute("ALTER TABLE fx_evidence_legacy_aliases DISABLE TRIGGER fx_evidence_append_only")
    c.execute("DELETE FROM fx_evidence_legacy_aliases WHERE legacy_key='legacy-component' AND source_snapshot_sha256=%s",("a"*64,))
    c.execute("ALTER TABLE fx_evidence_legacy_aliases ENABLE TRIGGER fx_evidence_append_only")
    c.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(ROLE)))
    try:
        stage()
    except ValueError as error:
        check("missing_previously_staged_alias_not_repaired",str(error)=="Previously staged evidence missing; no automatic repair")
    else:
        raise ValueError("Component silently repaired missing history")


def exercise_lifecycle(c,tenant,other,check):
    account="trial-lifecycle"
    c.execute("INSERT INTO accounts VALUES (%s,%s)",(account,tenant))
    attrs={"fromCurrency":"HKD","toCurrency":"USD","rate":"0.128","reportDate":"20250120"}
    key=_source_key(account,"ConversionRates","ConversionRate",attrs,effective_report_date="2025-01-20")
    h=sha256_text(canonical_json(attrs))
    b={"contract_version":1,"tenant_id":tenant,"account_id":account,"source_namespace":"lifecycle",
       "source_snapshot_sha256":"d"*64,
       "runs":[{"source_run_id":301,"archive_sha256":"b"*64,"run_status":"success","source_completed_at":"2025-01-21T00:00:00+00:00"}],
       "versions":[{"canonical_key":key,"row_sha256":h,"section":"ConversionRates","record_type":"ConversionRate","report_date":"2025-01-20","data_json":attrs}],
       "observations":[{"source_run_id":301,"canonical_key":key,"row_sha256":h}],"aliases":[]}
    b["bundle_sha256"]=fingerprint(b)
    artifact=sha256_text(canonical_json(b))
    contract=import_contract(b,expected_bundle_sha256=b["bundle_sha256"],artifact_sha256=artifact)
    def prepare(run_id, state, artifact_hash=artifact, status="running",scope=tenant):
        c.execute("INSERT INTO ingestion_runs (run_id,run_type,requested_from,requested_to,started_at,status,raw_sha256,tenant_id,reconciliation_json) VALUES (%s,%s,'2025-01-20','2025-01-20','trial',%s,%s,%s,%s)",
                  (run_id,RUN_TYPE,status,artifact_hash,scope,canonical_json(state)))
    def stage(run_id=601,value=b,artifact_hash=artifact):
        return stage_import_run(c,value,expected_bundle_sha256=value["bundle_sha256"],artifact_sha256=artifact_hash,run_id=run_id,schema=SCHEMA)
    prepare(601,{CONTRACT_KEY:contract,"unrelated":"retained"})
    first=stage()
    check("lifecycle_stages_exact_evidence",sum(first["inserted"].values())==3)
    run=c.execute("SELECT * FROM ingestion_runs WHERE run_id=601").fetchone()
    state=json.loads(run["reconciliation_json"])
    check("lifecycle_receipt_bound_to_run",state[RECEIPT_KEY]["run_id"]==601 and state[RECEIPT_KEY]==first["receipt"])
    check("lifecycle_preserves_unrelated_state",state["unrelated"]=="retained" and state[CONTRACT_KEY]==contract)
    check("lifecycle_does_not_authorize_or_mark_success",run["status"]=="running" and not first["receipt"]["execution_authorized"] and not first["receipt"]["reconciliation_authorized"])
    again=stage()
    check("lifecycle_repeat_no_duplicates",sum(again["inserted"].values())==0 and again["receipt"]==first["receipt"])
    def rejected(name, action):
        try:
            action()
        except ValueError:
            check(name,True)
        else:
            raise ValueError("Expected lifecycle rejection: "+name)
    prepare(602,{CONTRACT_KEY:{**contract,"account_id":"another-account"}})
    rejected("lifecycle_wrong_account_contract_rejected",lambda:stage(602))
    prepare(603,{CONTRACT_KEY:contract},artifact_hash="f"*64)
    rejected("lifecycle_wrong_artifact_rejected",lambda:stage(603))
    prepare(604,{CONTRACT_KEY:contract},status="success")
    rejected("lifecycle_completed_run_rejected",lambda:stage(604))
    c.execute("SELECT set_config('app.tenant_id',%s,true)",(other,))
    prepare(605,{CONTRACT_KEY:contract},scope=other)
    c.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
    rejected("lifecycle_foreign_tenant_run_rejected",lambda:stage(605))
    late=copy.deepcopy(b)
    late["source_namespace"]="lifecycle-late"
    late["bundle_sha256"]=fingerprint(late)
    late_artifact=sha256_text(canonical_json(late))
    late_contract=import_contract(late,expected_bundle_sha256=late["bundle_sha256"],artifact_sha256=late_artifact)
    late_state={CONTRACT_KEY:late_contract,RECEIPT_KEY:{"conflicting_receipt":True}}
    prepare(606,late_state,artifact_hash=late_artifact)
    rejected("lifecycle_late_receipt_conflict_rejected",lambda:stage(606,late,late_artifact))
    check("lifecycle_late_failure_rolls_back_evidence",not c.execute("SELECT 1 FROM fx_evidence_runs WHERE source_namespace='lifecycle-late'").fetchone()
          and not c.execute("SELECT 1 FROM fx_evidence_observations WHERE source_namespace='lifecycle-late'").fetchone())
    check("lifecycle_late_failure_rolls_back_batch_receipt",not c.execute("SELECT 1 FROM fx_evidence_batches WHERE bundle_sha256=%s",(late["bundle_sha256"],)).fetchone())
    check("lifecycle_late_failure_preserves_run_state",json.loads(c.execute("SELECT reconciliation_json FROM ingestion_runs WHERE run_id=606").fetchone()["reconciliation_json"])==late_state)
    check("lifecycle_earlier_receipt_still_valid",stage()["receipt"]==first["receipt"])


def load_real_inputs(tenant):
    manifest=globals().get("REAL_INPUT_MANIFEST")
    if not manifest:
        return []
    from azure.core import MatchConditions
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient
    if (manifest["tenant_id"]!=tenant or manifest["container"]!="input-snapshots"
            or manifest["execution_authorized"] is not False
            or len(manifest["records"])!=2
            or {r["account_id"] for r in manifest["records"]}!={"U16088571","U16088606"}):
        raise ValueError("Unexpected real-trial input scope")
    service=BlobServiceClient("https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net",
        credential=ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"]))
    container=service.get_container_client("input-snapshots")
    if container.get_container_properties().get("public_access") is not None:
        raise ValueError("Private input container required")
    results=[]
    for record in manifest["records"]:
        expected_name=f"{tenant}/fx-evidence-trials/{record['account_id']}/{record['artifact_sha256']}.json"
        if record["blob_name"]!=expected_name or not 0<record["byte_count"]<=128*1024*1024:
            raise ValueError("Unexpected private trial artifact")
        client=container.get_blob_client(expected_name)
        props=client.get_blob_properties()
        if props.etag!=record["etag"] or props.size!=record["byte_count"]:
            raise ValueError("Private trial artifact changed")
        content=client.download_blob(etag=record["etag"],match_condition=MatchConditions.IfNotModified).readall()
        with tempfile.TemporaryDirectory(prefix="fx-evidence-trial-") as folder:
            path=Path(folder)/"bundle.json"
            path.write_bytes(content)
            bundle=read_bundle(path,expected_artifact_sha256=record["artifact_sha256"],
                expected_bundle_sha256=record["bundle_sha256"],tenant_id=tenant,account_id=record["account_id"])
        if bundle["source_snapshot_sha256"]!=manifest["source_snapshot_sha256"]:
            raise ValueError("Real trial source snapshot differs")
        results.append((record,bundle))
    return results


def exercise_real_bundles(c,tenant,check,inputs):
    results=[]
    for index,(record,bundle) in enumerate(inputs):
        account=bundle["account_id"]
        c.execute("INSERT INTO accounts VALUES (%s,%s)",(account,tenant))
        contract=import_contract(bundle,expected_bundle_sha256=record["bundle_sha256"],artifact_sha256=record["artifact_sha256"])
        run_id=7100+index
        c.execute("INSERT INTO ingestion_runs (run_id,run_type,requested_from,requested_to,started_at,status,raw_sha256,tenant_id,reconciliation_json) VALUES (%s,%s,%s,%s,'trial','running',%s,%s,%s)",
            (run_id,RUN_TYPE,contract["requested_from"],contract["requested_to"],record["artifact_sha256"],tenant,canonical_json({CONTRACT_KEY:contract})))
        def stage():
            return stage_import_run(c,bundle,expected_bundle_sha256=record["bundle_sha256"],artifact_sha256=record["artifact_sha256"],run_id=run_id,schema=SCHEMA)
        started=time.perf_counter()
        first=stage()
        first_seconds=round(time.perf_counter()-started,3)
        check(account+"_real_all_evidence_inserted",first["inserted"]==contract["counts"])
        check(account+"_real_no_execution_authority",first["receipt"]["execution_authorized"] is False and first["receipt"]["reconciliation_authorized"] is False)
        print("FX_EVIDENCE_SCALE_PROGRESS "+json.dumps({"account":account,"phase":"real_lifecycle_staged","seconds":first_seconds}),flush=True)
        started=time.perf_counter()
        again=stage()
        repeat_seconds=round(time.perf_counter()-started,3)
        check(account+"_real_repeat_no_duplicates",sum(again["inserted"].values())==0 and first["receipt"]==again["receipt"])
        run=c.execute("SELECT * FROM ingestion_runs WHERE tenant_id=%s AND run_id=%s",(tenant,run_id)).fetchone()
        check(account+"_real_run_not_marked_success",run["status"]=="running")
        check(account+"_real_receipt_bound_and_retained",json.loads(run["reconciliation_json"])[RECEIPT_KEY]==first["receipt"])
        check(account+"_real_account_scoped_count",c.execute("SELECT count(*) AS n FROM fx_evidence_versions WHERE tenant_id=%s AND account_id=%s",(tenant,account)).fetchone()["n"]==contract["counts"]["versions"])
        results.append({"account_id":account,"synthetic":False,"counts":contract["counts"],
            "artifact_sha256":record["artifact_sha256"],"bundle_sha256":record["bundle_sha256"],
            "source_snapshot_sha256":contract["source_snapshot_sha256"],"first_seconds":first_seconds,
            "repeat_seconds":repeat_seconds,"receipt_sha256":sha256_text(canonical_json(first["receipt"])),
            "source_completeness":"unverified","execution_authorized":False})
    return results


def full_size_bundle(tenant,account):
    runs=[{"source_run_id":1000+i,"archive_sha256":sha256_text("synthetic-archive-"+str(i)),
           "run_status":"success","source_completed_at":"2026-10-01T00:00:00+00:00"} for i in range(454)]
    versions=[]
    observations=[]
    aliases=[]
    for i in range(20319):
        day=(date(2025,1,20)+timedelta(days=i//39)).isoformat()
        attrs={"fromCurrency":"C"+str(i%39).zfill(2),"toCurrency":"USD","rate":"0.128","reportDate":day.replace("-","")}
        key=_source_key(account,"ConversionRates","ConversionRate",attrs,effective_report_date=day)
        h=sha256_text(canonical_json(attrs))
        versions.append({"canonical_key":key,"row_sha256":h,"section":"ConversionRates","record_type":"ConversionRate","report_date":day,"data_json":attrs})
        for offset in range(3 if i<2652 else 2):
            observations.append({"source_run_id":1000+(i+offset)%454,"canonical_key":key,"row_sha256":h})
        if i<521:
            aliases.append({"legacy_section":"ConversionRates","legacy_record_type":"ConversionRate","legacy_key":"synthetic-legacy-"+str(i),
                "row_sha256":h,"canonical_key":key,"source_run_id":1000+i%454,"legacy_first_seen_run_id":1000+i%454,"source_snapshot_sha256":"d"*64})
    bundle={"contract_version":1,"tenant_id":tenant,"account_id":account,"source_namespace":"full-size-synthetic",
        "source_snapshot_sha256":"d"*64,"runs":runs,"versions":versions,"observations":observations,"aliases":aliases}
    bundle["bundle_sha256"]=fingerprint(bundle)
    return bundle


def exercise_full_size(c,tenant,check):
    results=[]
    for account in ("trial-scale-A","trial-scale-B"):
        c.execute("INSERT INTO accounts VALUES (%s,%s)",(account,tenant))
        bundle=full_size_bundle(tenant,account)
        check(account+"_matches_real_cardinalities",{g:len(bundle[g]) for g in ("runs","versions","observations","aliases")}==
              {"runs":454,"versions":20319,"observations":43290,"aliases":521})
        started=time.perf_counter()
        first=stage_evidence(c,bundle,expected_sha256=bundle["bundle_sha256"],schema=SCHEMA)
        first_seconds=round(time.perf_counter()-started,3)
        check(account+"_all_rows_inserted",first["inserted"]==first["receipt"]["counts"])
        check(account+"_bounded_query_count",sum(first["group_queries"].values())==393)
        print("FX_EVIDENCE_SCALE_PROGRESS "+json.dumps({"account":account,"phase":"first_insert_validated","seconds":first_seconds}),flush=True)
        started=time.perf_counter()
        again=stage_evidence(c,bundle,expected_sha256=bundle["bundle_sha256"],schema=SCHEMA)
        repeat_seconds=round(time.perf_counter()-started,3)
        check(account+"_repeat_no_duplicates",sum(again["inserted"].values())==0 and again["receipt"]==first["receipt"])
        check(account+"_repeat_bounded_queries",sum(again["group_queries"].values())==131)
        results.append({"account":account,"synthetic":True,"counts":first["receipt"]["counts"],
            "first_seconds":first_seconds,"repeat_seconds":repeat_seconds,
            "first_group_seconds":first["group_seconds"],"repeat_group_seconds":again["group_seconds"],
            "first_group_queries":first["group_queries"],"repeat_group_queries":again["group_queries"],
            "receipt_sha256":first["receipt"]["readback_sha256"]})
    return results


if __name__ == "__main__":
    main()
