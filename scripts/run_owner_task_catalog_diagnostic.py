"""One private-host catalog observation. No installer, model or source query."""
from __future__ import annotations
import base64
import hashlib
import json
import lzma
from psycopg.rows import dict_row
from ibkr_etl import owner_codex_catalog_snapshot as snapshot
from ibkr_etl import owner_codex_isolated_target as host
import run_owner_task_isolated_provider_observation as credentials

OPERATION='bacadfc0-e0dd-483e-a074-f2dc0d2a9d79'
JOB='ibkrv2stg-owncatd-20261005'
JOB_ID=host.BASE+'Microsoft.App/jobs/'+JOB
PATH='scripts/run_owner_task_catalog_diagnostic.py'
PREFIX='OWNER_TASK_CATALOG_DIAGNOSTIC_V1 '
WIRE_PREFIX='OWNER_TASK_CATALOG_DIAGNOSTIC_WIRE_V1 '
MAX_RAW=2*1024*1024
MAX_PACKED=128*1024
OPTIONS='-c default_transaction_read_only=on -c search_path=pg_catalog -c statement_timeout=30000 -c lock_timeout=3000 -c idle_in_transaction_session_timeout=30000'
canonical=snapshot.canonical


def require(value,code='fence_catalog_diagnostic_unverified'):
    if not value:raise ValueError(code)


def frames(record):
    data=canonical(record)
    require(0<len(data)<=MAX_RAW)
    packed=lzma.compress(data,preset=6)
    require(len(packed)<=MAX_PACKED)
    encoded=base64.b64encode(packed).decode('ascii')
    parts=[encoded[i:i+700] for i in range(0,len(encoded),700)]
    require(0<len(parts)<=250)
    binding=dict(operation_id=OPERATION,record_sha256=hashlib.sha256(data).hexdigest(),
        compressed_sha256=hashlib.sha256(packed).hexdigest(),raw_bytes=len(data),
        compressed_bytes=len(packed),frame_count=len(parts))
    return [dict(binding,index=i,data=part) for i,part in enumerate(parts)]


def reconstruct(values):
    require(type(values) is list and 0<len(values)<=250)
    binding={k:v for k,v in values[0].items() if k not in ('index','data')}
    require(set(binding)=={'operation_id','record_sha256','compressed_sha256','raw_bytes','compressed_bytes','frame_count'}
        and binding['operation_id']==OPERATION and type(binding['frame_count']) is int
        and len(values)==binding['frame_count'] and type(binding['raw_bytes']) is int
        and 0<binding['raw_bytes']<=MAX_RAW and type(binding['compressed_bytes']) is int
        and 0<binding['compressed_bytes']<=MAX_PACKED)
    parts={}
    for item in values:
        require(type(item) is dict and set(item)==set(binding)|{'index','data'}
            and {k:item[k] for k in binding}==binding and type(item['index']) is int
            and 0<=item['index']<len(values) and item['index'] not in parts
            and type(item['data']) is str and 0<len(item['data'])<=700)
        parts[item['index']]=item['data']
    packed=base64.b64decode(''.join(parts[i] for i in range(len(values))),validate=True)
    require(len(packed)==binding['compressed_bytes'] and hashlib.sha256(packed).hexdigest()==binding['compressed_sha256'])
    dec=lzma.LZMADecompressor(memlimit=64*1024*1024)
    data=dec.decompress(packed,max_length=MAX_RAW+1)
    require(dec.eof and not dec.unused_data and len(data)==binding['raw_bytes']
        and hashlib.sha256(data).hexdigest()==binding['record_sha256'])
    record=json.loads(data)
    require(canonical(record)==data)
    return record,binding


def main(secret,*,connect,emit=print):
    result=dict(status='unaccepted',operation_id=OPERATION,job_resource_id=JOB_ID,
        source_rows_read=0,source_rows_written=0,model_calls=0,agents_activated=0,
        production_changed=False,sql_mutations=0,automatic_retry=False,step_3_complete=False,step_4_started=False)
    try:
        parameters=credentials.validate_admin_secret(secret);credentials.require_ca_bundle()
        observations=[]
        for _ in range(3):
            with connect(**parameters,autocommit=True,row_factory=dict_row,connect_timeout=20,
                options=OPTIONS,application_name='owner-catalog-diagnostic') as c:
                observations.append(snapshot.read_snapshot(c))
        rows=observations[0].rows();groups={}
        for member in rows['memberships']:
            groups.setdefault((member['role_oid'],member['member_oid']),[]).append(member)
        ties=[dict(role_oid=key[0],member_oid=key[1],grantor_oids=[m['grantor_oid'] for m in members],
            option_variants=len({(m['admin_option'],m['inherit_option'],m['set_option']) for m in members}))
            for key,members in sorted(groups.items()) if len(members)>1]
        record=dict(operation_id=OPERATION,job_resource_id=JOB_ID,catalog=rows,
            snapshot_sha256=[v.sha256 for v in observations],
            observed_differences=[snapshot.differences(observations[0],v) for v in observations[1:]],
            fresh_connections=3,tied_memberships=ties,sql_mutations=0,
            historical_drift_cause_proven=False,source_rows_read=0,source_rows_written=0,
            step_3_complete=False,step_4_started=False)
        wire=frames(record)
        for frame in wire:emit(WIRE_PREFIX+canonical(frame).decode('ascii'),flush=True)
        result.update(status='catalog_diagnostic_observed',record_sha256=wire[0]['record_sha256'],
            frame_count=len(wire),fresh_connections=3,catalog_stable=all(v.sha256==observations[0].sha256 for v in observations),
            historical_drift_cause_proven=False)
    except Exception:
        result['code']='fence_catalog_diagnostic_unverified'
    emit(PREFIX+canonical(result).decode('ascii'),flush=True)
    return result
