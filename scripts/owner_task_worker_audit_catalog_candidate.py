"""Prospective audit SQL correction; never rearms either failed trial.

Use fully keyed, redacted catalog snapshots. Exclude only the new registered
database/roles and retain every snapshot, including on failure. The historical
failure remains unaccepted; the first snapshot must match the fresh baseline.
"""
from __future__ import annotations
import ast
import hashlib
import types
from pathlib import Path

from ibkr_etl import owner_codex_catalog_snapshot as catalog
import owner_task_worker_audit_sql_revocation_candidate as previous
import run_owner_task_catalog_diagnostic as framing

ROOT=previous.ROOT
BASE_WORKER=previous.BASE_WORKER
PATH='scripts/owner_task_worker_audit_catalog_candidate.py'
CONTROL_PATH='scripts/deploy_owner_task_worker_audit_catalog_trial.py'
TEST_PATH='tests/test_owner_task_worker_audit_catalog_trial.py'
ADDITIONS=frozenset((PATH,CONTROL_PATH,TEST_PATH))
OPERATION='89b88478-11e1-45d5-9ff1-048c4cf25ce9'
JOB='ibkrv2stg-ownaud3-20261005'
DATABASE='owner_codex_audit_sql_20261005_v3'
SCHEMA='owner_codex_audit_sql_trial_v3'
OWNER='ibkr_owner_audit_schema_v3_20261005'
CAPABILITY='ibkr_owner_audit_writer_v3_20261005'
LOGIN='ibkr_owner_audit_wlogin_v3_20261005'
READER_CAP='ibkr_owner_audit_reader_v3_20261005'
READER_LOGIN='ibkr_owner_audit_rlogin_v3_20261005'
ROLES=(OWNER,CAPABILITY,LOGIN,READER_CAP,READER_LOGIN)
MARKER='owner-codex-audit-sql-isolated-20261005-v3'
PREFIX='OWNER_TASK_WORKER_AUDIT_SQL_TRIAL_V3 '
WIRE_PREFIX='OWNER_TASK_WORKER_AUDIT_CATALOG_WIRE_V3 '
BASELINE_SHA='1c1a9f3aa79a016ffdeb25d5b3433a330ccfacd1aad0851782897bb7c707ee3e'
RECIPE_SHA='6a5a62d40c46cabb9cd41d901747e7d989def21b7d8840ca866aa03ac2938157'
FRAMING_SHA='ce1e5a54f76a6865ab89d8008be4dece2efd7553a44458feec312a0493e62e7a'


def project_predecessors(value):
    """Fixed NEW scope only; never suppress an existing grantor's permissions."""
    if type(value) is not catalog.CatalogSnapshot:raise ValueError('fence_audit_catalog_invalid')
    rows=value.rows()
    own={r['oid'] for r in rows['roles'] if r['name'] in ROLES}
    # A new role granting rights between existing principals is NOT own scope.
    if any(m['grantor_oid'] in own and m['role_oid'] not in own and m['member_oid'] not in own
           for m in rows['memberships']):raise ValueError('fence_audit_catalog_grantor_escape')
    scoped=dict(databases=[r for r in rows['databases'] if r['name']!=DATABASE],
        roles=[r for r in rows['roles'] if r['name'] not in ROLES],
        memberships=[m for m in rows['memberships'] if m['role_oid'] not in own and m['member_oid'] not in own])
    return catalog.CatalogSnapshot.from_rows(scoped)


def predecessor_catalog(admin):
    full=catalog.read_snapshot(admin)
    require(len(_catalog_snapshots)<6,'fence_audit_catalog_observation_limit')
    record=dict(full_catalog=full.rows(),full_sha256=full.sha256,
        predecessor_catalog=None,predecessor_sha256=None)
    _catalog_snapshots.append(record)
    scoped=project_predecessors(full)
    record.update(predecessor_catalog=scoped.rows(),predecessor_sha256=scoped.sha256)
    return scoped.rows()


def segment(path,name):
    text=(ROOT/path).read_text()
    return ast.get_source_segment(text,next(n for n in ast.parse(text).body
        if isinstance(n,ast.FunctionDef) and n.name==name))


def worker_source(raw):
    if (hashlib.sha256(Path(previous.__file__).read_bytes()).hexdigest()!=RECIPE_SHA
        or hashlib.sha256(Path(framing.__file__).read_bytes()).hexdigest()!=FRAMING_SHA):
        raise ValueError('fence_audit_catalog_recipe_drift')
    text=previous.worker_source(raw);replace=previous.previous.previous.tls.replace_once
    old=previous.worker_module()
    for name in ('OPERATION','JOB','DATABASE','SCHEMA','OWNER','CAPABILITY','LOGIN',
                 'READER_CAP','READER_LOGIN','MARKER','PATH','PREFIX'):
        text=replace(text,name+' = '+repr(getattr(old,name)),name+' = '+repr(globals()[name]))
    text=replace(text,'import secrets','import secrets\nimport base64\nimport lzma\n'
        'from ibkr_etl import owner_codex_catalog_snapshot as catalog\n'
        '_catalog_snapshots=[]\nBASELINE_SHA='+repr(BASELINE_SHA)+'\n'
        'WIRE_PREFIX='+repr(WIRE_PREFIX)+'\nMAX_RAW=2*1024*1024\nMAX_PACKED=128*1024')
    node=next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name=='predecessor_catalog')
    text=replace(text,ast.get_source_segment(text,node),segment(PATH,'project_predecessors')+'\n\n'+segment(PATH,'predecessor_catalog'))
    text=replace(text,'        before=install(admin,connect,parameters,ddl,passwords)',
        "        require(hashlib.sha256(canonical(predecessor_catalog(admin))).hexdigest()==BASELINE_SHA,'fence_audit_catalog_baseline_drift')\n"
        '        before=install(admin,connect,parameters,ddl,passwords)')
    text=replace(text,'        if admin is not None:\n            admin.close()',
        "        if admin is not None:\n            try:\n                require(hashlib.sha256(canonical(predecessor_catalog(admin))).hexdigest()==BASELINE_SHA,'fence_audit_catalog_final_drift')\n"
        "            except Exception:\n                result.update(status='unaccepted',predecessor_metadata_unchanged=False,"
        "failure_stage='catalog_final_readback',code='fence_audit_catalog_final_readback_failed')\n"
        "            finally:\n                admin.close()")
    # Preserve bounded framing and complete snapshots even when qualification fails.
    text=replace(text,"    emit(PREFIX+canonical(result).decode('ascii'),flush=True)",
        "    record=dict(operation_id=OPERATION,job_resource_id=JOB_ID,catalog_snapshots=_catalog_snapshots,historical_drift_cause_proven=False)\n"
        "    wire=frames(record)\n    for frame in wire:emit(WIRE_PREFIX+canonical(frame).decode('ascii'),flush=True)\n"
        "    result.update(catalog_record_sha256=wire[0]['record_sha256'],catalog_frame_count=len(wire),historical_drift_cause_proven=False)\n"
        "    emit(PREFIX+canonical(result).decode('ascii'),flush=True)")
    text+='\n\n'+segment(framing.PATH,'frames')+'\n\n'+segment(framing.PATH,'reconstruct')+'\n'
    compile(text,PATH,'exec')
    return text


def worker_module():
    module=types.ModuleType('owner_audit_catalog_candidate_worker')
    module.__file__,module.__package__=str(ROOT/PATH),''
    exec(compile(worker_source((ROOT/BASE_WORKER).read_bytes()),PATH,'exec'),module.__dict__)
    return module
