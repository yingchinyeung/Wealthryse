"""One protected, read-only catalog diagnostic on the existing private bootstrap.

No job definition, role, database, secret, schedule, source data or agent changes.
An exclusive protected intent prevents redispatch after an uncertain response.
This is NOT the setup/run/cleanup dispatcher or a credential-isolation trial.
"""
import argparse
import base64
import copy
import gzip
import hashlib
import json
from pathlib import Path
import re

import deploy_owner_task_postgres_trial as deploy
import inspect_owner_task_restricted_boundary as collector
import resume_owner_task_postgres_trial as resume
import verify_owner_task_source_checkpoint as qa

# The first preflight is retained, unused, after a source-only cleanup hardening
# correction. This new registered preflight never consumes or overwrites it.
FOLDER = deploy.ROOT / ".work/owner-task-restricted-boundary-20261004-r2"
PRIOR = deploy.ROOT / ".work/owner-task-postgres-20261003-r2"
WORKSPACE = "5264a124-10cd-45f6-93a5-6d65c37e9bfb"
HISTORY = (("run-acceptance.json", "reviewed-resume-run-accepted", "ibkrv2stg-bootstrap-ybfle7f",
    "02977b9ed05c3d5a9b678d95dd16835a921d0a9fbe2333b57f27c087fe9cd9d8", "2026-10-03T23:20:34.8070542Z"),
    ("cleanup-acceptance.json", "reviewed-resume-cleanup-accepted", "ibkrv2stg-bootstrap-bwcqbym",
    "f4d9908be269e6dc9f792e8e35a96686c9cb73806dd16e7ba74978618af0b001", "2026-10-03T23:27:58.9018429Z"))


def source_manifest():
    # Freeze tests and all invoked helper code too; document updates do not
    # invalidate this code receipt or retroactively become tested source.
    return {k: v for k, v in qa.source_manifest().items() if k != qa.DOCUMENT}


def worker_source():
    code = Path(collector.__file__).read_text(encoding="utf-8")
    source = "import os,json,sys,psycopg\nfrom psycopg.rows import dict_row\nfrom psycopg.conninfo import conninfo_to_dict\n"
    source += "scope={'__name__':'trusted_boundary_collector'}\nexec(compile(" + repr(code) + ", 'trusted-boundary-collector','exec'),scope)\n"
    source += "try:\n"
    source += " params=conninfo_to_dict(os.environ.pop('DATABASE_ADMIN_URL'))\n"
    source += " if set(params)-{'host','dbname','port','sslmode','user','password'} or params.get('host')!=scope['HOST'] or params.get('dbname')!='ibkr_staging' or params.get('port','5432')!='5432' or params.get('sslmode')!='require' or not params.get('user') or not params.get('password'): raise ValueError('fixed_admin_binding_required')\n"
    source += " with psycopg.connect(**dict(params,dbname='postgres'),autocommit=True,connect_timeout=20,row_factory=dict_row,options='-c statement_timeout=20000 -c lock_timeout=3000 -c default_transaction_read_only=on -c search_path=pg_catalog') as raw:\n"
    source += "  record=scope['inspect_boundary'](raw)\n"
    source += "except Exception:\n record={'status':'failed','reason':'boundary_inspection_failed','automatic_retry':False}\n"
    source += "print(scope['PREFIX']+json.dumps(record,sort_keys=True),flush=True)\n"
    source += "sys.exit(0 if record['status']=='observed' else 1)\n"
    return source


def frozen_template(job):
    deploy.validate_bootstrap(job)
    before = source_manifest()
    source = worker_source()
    encoded = base64.b64encode(gzip.compress(source.encode(), mtime=0)).decode()
    container = copy.deepcopy(job["properties"]["template"]["containers"][0])
    container.update(command=["python"], args=["-c", "import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('" + encoded + "')),'trusted-boundary-worker','exec'))"],
        resources={"cpu": 0.5, "memory": "1Gi"}, env=[{"name": "DATABASE_ADMIN_URL", "secretRef": "database-admin-url"},
            {"name": "OWNER_TASK_TRIAL_STAGE", "value": "boundary-inspection"}])
    container.pop("volumeMounts", None)
    if source_manifest() != before:
        raise ValueError("boundary_source_changed_while_freezing")
    return {"containers": [container]}, {"files": before, "payload_sha256": hashlib.sha256(source.encode()).hexdigest()}


def historical_receipts():
    result = []
    for name, kind, execution, digest, version in HISTORY:
        raw = json.loads((PRIOR / name).read_text(encoding="utf-8"))
        receipt = raw.get("protected_receipt", {})
        if receipt.get("sha256") != digest or receipt.get("version_id") != version:
            raise ValueError("exact_historical_receipt_required")
        value = deploy.protected(raw, kind)
        if value.get("status") != "passed" or value.get("execution") != execution or value.get("baseline_unchanged") is not True:
            raise ValueError("accepted_historical_trial_and_cleanup_required")
        result.append(receipt)
    return result


def log_records(rows):
    if type(rows) is not list or len(rows) > 1:
        raise ValueError("at_most_one_boundary_record_required")
    result = []
    for row in rows:
        if (type(row) is not dict or set(row) not in ({"Log_s"}, {"Log_s", "TableName"})
            or type(row.get("Log_s")) is not str or ("TableName" in row and row["TableName"] != "PrimaryResult")
            or not row["Log_s"].startswith(collector.PREFIX) or len(row["Log_s"]) > 16384):
            raise ValueError("boundary_cli_record_shape_invalid")
        record = json.loads(row["Log_s"][len(collector.PREFIX):])
        if record != {"status": "failed", "reason": "boundary_inspection_failed", "automatic_retry": False}:
            collector.validate_record(record)
        elif record["automatic_retry"] is not False:
            raise ValueError("boundary_failure_record_invalid")
        result.append(record)
    return result


def observed_record(records):
    if len(records) != 1:
        raise ValueError("one_observed_boundary_record_required")
    return collector.validate_record(records[0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("prepare", "start", "inspect", "verify"))
    parser.add_argument("--execution")
    args = parser.parse_args()
    deploy.FOLDER = FOLDER
    if deploy.az("account", "show", "--query", "id") != deploy.SUB:
        raise ValueError("registered_subscription_required")
    FOLDER.mkdir(parents=True, exist_ok=True)
    job = deploy.request("GET", deploy.PATH + "?api-version=2024-03-01")
    wanted, source = frozen_template(job)
    if args.stage == "prepare":
        if (FOLDER / "preflight.json").exists():
            raise ValueError("boundary_preflight_reserved_no_overwrite")
        history = historical_receipts()
        local = qa.run_local_checks()
        if frozen_template(job)[1] != source:
            raise ValueError("boundary_sources_changed_during_qa")
        baseline = deploy.baseline()
        if baseline.get("job_inventory_complete") is not True or len(baseline.get("other_jobs", {})) != 34:
            raise ValueError("complete_existing_job_inventory_required")
        preflight = deploy.save(dict(status="prepared", source=source, template=wanted, local_checks=local,
            baseline=baseline, bootstrap_settings_sha256=deploy.settings(job), historical_receipts=history,
            read_only=True, restricted_setup_authorized=False, automatic_retry=False), "preflight.json", "restricted-boundary-preflight")
        deploy.protected(preflight, "restricted-boundary-preflight")
        if frozen_template(job)[1] != source:
            raise ValueError("boundary_source_changed_during_retention")
        print(json.dumps(dict(status="prepared", tests=local["test_count"], protected_receipt=preflight["protected_receipt"])), flush=True)
        return
    pf_raw = json.loads((FOLDER / "preflight.json").read_text(encoding="utf-8"))
    pf = deploy.protected(pf_raw, "restricted-boundary-preflight")
    if (pf.get("status") != "prepared" or pf["source"] != source or pf["template"] != wanted
        or pf["bootstrap_settings_sha256"] != deploy.settings(job) or pf["historical_receipts"] != historical_receipts()
        or pf.get("read_only") is not True or pf.get("restricted_setup_authorized") is not False):
        raise ValueError("frozen_boundary_preflight_binding_required")
    if args.stage == "start":
        if (FOLDER / "intent.json").exists() or (FOLDER / "dispatch.json").exists():
            raise ValueError("boundary_intent_reserved_no_redispatch")
        if deploy.baseline() != pf["baseline"]:
            raise ValueError("boundary_baseline_changed")
        resume.assert_bootstrap_idle()
        intent = deploy.save(dict(stage="readonly-boundary-inspection", source=source, template=wanted,
            preflight_receipt=pf_raw["protected_receipt"], baseline=pf["baseline"],
            bootstrap_settings_sha256=deploy.settings(job), read_only=True,
            restricted_setup_authorized=False, automatic_retry=False), "intent.json", "restricted-boundary-intent")
        deploy.protected(intent, "restricted-boundary-intent")
        if frozen_template(job)[1] != source or deploy.baseline() != pf["baseline"]:
            raise ValueError("boundary_source_or_baseline_changed_no_retry")
        run = deploy.request("POST", deploy.PATH + "/start?api-version=2024-03-01", data=wanted)
        if type(run.get("name")) is not str or not re.fullmatch(deploy.JOB + r"-[a-z0-9]{7}", run["name"]):
            raise ValueError("boundary_dispatch_response_uncertain_no_retry")
        saved = deploy.save(dict(execution=run["name"], source=source, intent_receipt=intent["protected_receipt"]),
            "dispatch.json", "restricted-boundary-dispatch")
        print(json.dumps(dict(execution=saved["execution"], read_only=True, protected_intent_verified=True)), flush=True)
        return
    intent_raw = json.loads((FOLDER / "intent.json").read_text(encoding="utf-8"))
    intent = deploy.protected(intent_raw, "restricted-boundary-intent")
    dispatch = deploy.protected(json.loads((FOLDER / "dispatch.json").read_text(encoding="utf-8")), "restricted-boundary-dispatch")
    if (args.execution != dispatch["execution"] or not re.fullmatch(deploy.JOB + r"-[a-z0-9]{7}", args.execution or "")
        or intent["source"] != source or dispatch["source"] != source or intent["template"] != wanted
        or dispatch["intent_receipt"] != intent_raw["protected_receipt"] or intent["preflight_receipt"] != pf_raw["protected_receipt"]
        or intent["bootstrap_settings_sha256"] != deploy.settings(job) or intent["baseline"] != pf["baseline"]):
        raise ValueError("exact_boundary_execution_binding_required")
    run = deploy.request("GET", deploy.PATH + "/executions/" + args.execution + "?api-version=2024-03-01")
    actual = run["properties"]["template"]
    if run.get("name") != args.execution or actual.get("volumes") or actual.get("initContainers"):
        raise ValueError("boundary_execution_scope_invalid")
    deploy.verify_containers(actual["containers"], wanted)
    rows = deploy.az("monitor", "log-analytics", "query", "-w", WORKSPACE, "--analytics-query",
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + args.execution +
        "' | where Log_s startswith '" + collector.PREFIX + "' | project Log_s")
    records = log_records(rows)
    if args.stage == "inspect":
        print(json.dumps(dict(execution=args.execution, status=run["properties"]["status"], records=records,
            executed_source_verified=True)), flush=True)
        return
    if run["properties"]["status"] != "Succeeded":
        raise ValueError("successful_boundary_inspection_required_no_retry")
    record = observed_record(records)
    if deploy.baseline() != pf["baseline"] or frozen_template(job)[1] != source:
        raise ValueError("boundary_baseline_or_source_changed")
    value = deploy.save(dict(status="observed", execution=args.execution, source=source, record=record,
        preflight_receipt=pf_raw["protected_receipt"], intent_receipt=intent_raw["protected_receipt"],
        baseline_unchanged=True, existing_jobs_unchanged=34, daily_schedule_hkt="18:00",
        owner_runtime_active=False, six_specialist_runtimes_active=False), "acceptance.json", "restricted-boundary-observed")
    deploy.protected(value, "restricted-boundary-observed")
    if frozen_template(job)[1] != source:
        raise ValueError("boundary_source_changed_after_retention")
    print(json.dumps(dict(status="observed", execution=args.execution,
        database_boundary_eligible=record["database_boundary_eligible"], reason=record["reason"],
        protected_receipt=value["protected_receipt"])), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(status="stopped", error_type=type(error).__name__, automatic_retry=False)), flush=True)
        raise SystemExit(1)
