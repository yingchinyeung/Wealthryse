"""Retain verified specialist deployment/trial receipts in protected storage."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from azure.core.exceptions import ResourceExistsError
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from ibkr_etl.specialist_audit_service import validate_archive_properties
from deploy_metadata_monitor import request, BASE

ROOT=Path(__file__).resolve().parents[1]
ACCOUNT='ibkrv2stgauhecwc4itaoo'
CONTAINER='specialist-audit-checkpoints'


def verify_observation_reader_receipt(record):
    expected=dict(status='passed',column_permissions_verified=41,write_privileges=0,
        raw_statement_access=False,credential_access=False,forced_rls_tables=8,
        allowed_relation_checks=9,denied_column_checks=4,financial_rows_read=0,
        source_rows_written=0,trial_role_rolled_back=True,service_login_created=False,agents_enabled=0)
    if (any(type(record.get(k)) is not type(v) or record.get(k)!=v for k,v in expected.items())
        or not re.fullmatch('[0-9a-f]{64}',record.get('nav_view_sha256',''))):
        raise ValueError('Exact successful observation reader rehearsal required')


def verify_interrupted_predecessor(dispatch,result,prior_dispatch,prior):
    if (dispatch.get('recovery_rehearsal') is not True or prior_dispatch.get('recovery_rehearsal') is not True
        or dispatch['execution']==prior_dispatch['execution']
        or any(dispatch[k]!=prior_dispatch[k] for k in ('operation_id','payload_sha256','source_manifest_sha256','job_settings_sha256'))
        or prior.get('execution')!=prior_dispatch['execution'] or prior.get('status')!='Failed'
        or prior.get('configuration_verified') is not True or prior.get('job_settings_unchanged') is not True
        or prior.get('payload_sha256')!=prior_dispatch['payload_sha256'] or len(prior.get('records',[]))!=1):
        raise ValueError('Exact interrupted predecessor required')
    stopped=prior['records'][0];finished=result['records'][0]
    if (stopped.get('status')!='interrupted' or stopped.get('expected_interruption') is not True
        or stopped.get('operation_id')!=dispatch['operation_id'] or stopped.get('database_committed') is not True
        or stopped.get('protected_archive_verified') is not False
        or finished.get('resumed_committed_intent') is not True or finished.get('operation_event_count')!=2
        or finished['phases'][0]['sequence']!=stopped.get('sequence')
        or finished['phases'][0]['event_sha256']!=stopped.get('event_sha256')):
        raise ValueError('Recovery did not preserve interrupted intent')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--kind',choices=['report-builder','metadata-install','report-worker','report-preview','audit-worker','audit-capture','capture-reader','authority','authority-install','authority-worker','observation-reader','observation-worker'],required=True)
    parser.add_argument('--execution',required=True)
    parser.add_argument('--interrupted-execution',help='Include verified failed predecessor in a recovery acceptance receipt')
    args=parser.parse_args()
    pattern='ibkrv2stg-agent-report(?:-provision)?-[a-z0-9]{7}' if args.kind in ('report-worker','report-preview') else 'ibkrv2stg-bootstrap-[a-z0-9]{7}'
    if args.kind=='audit-worker': pattern='ibkrv2stg-agent-audit(?:-provision)?-[a-z0-9]{7}'
    if args.kind=='audit-capture': pattern='ibkrv2stg-agent-audit-[a-z0-9]{7}'
    if args.kind=='capture-reader': pattern='ibkrv2stg-agent-report-[a-z0-9]{7}'
    if args.kind=='authority-worker': pattern='ibkrv2stg-authority(?:-provision)?-[a-z0-9]{7}'
    if args.kind=='observation-worker': pattern='ibkrv2stg-observe-(?:reader|prov)-[a-z0-9]{7}'
    if not re.fullmatch(pattern,args.execution): raise ValueError('Exact execution required')
    folder=ROOT/f'.work/specialist-{args.kind}-20261002'
    result=json.loads((folder/(args.execution+'.json')).read_text())
    worker=args.kind in ('report-worker','audit-worker','authority-worker','observation-worker')
    dispatch=None if worker else json.loads((folder/(args.execution+'-dispatch.json')).read_text())
    deployment_verified=(result.get('configuration_verified') is True and re.fullmatch('[0-9a-f]{64}',result.get('payload_sha256','')))
    if args.kind in ('authority','authority-install','observation-reader') and (result.get('configuration_verified') is not True or result.get('source_sha256')!=dispatch.get('source_sha256')):
        raise ValueError('Verified isolated authority payload required')
    if args.kind in ('report-preview','audit-capture','capture-reader') and (not deployment_verified or dispatch['payload_sha256']!=result['payload_sha256']):
        raise ValueError('Verified report preview payload required')
    if result['status']!='Succeeded' or (not deployment_verified if worker else result['job_settings_unchanged'] is not True) or len(result['records'])!=1:
        raise ValueError('Verified completed execution required')
    record=result['records'][0]
    if args.kind=='observation-reader': verify_observation_reader_receipt(record)
    if not (record.get('status')=='passed' or record.get('state') in ('installed_dormant','already_installed')):
        raise ValueError('Successful acceptance or installation required')
    evidence={'dispatch':dispatch,'result':result}
    if args.interrupted_execution:
        if args.kind!='audit-capture' or not re.fullmatch(pattern,args.interrupted_execution):
            raise ValueError('Exact audit recovery predecessor required')
        prior_dispatch=json.loads((folder/(args.interrupted_execution+'-dispatch.json')).read_text())
        prior=json.loads((folder/(args.interrupted_execution+'.json')).read_text())
        verify_interrupted_predecessor(dispatch,result,prior_dispatch,prior)
        evidence['interrupted_predecessor']={'dispatch':prior_dispatch,'result':prior}
    payload=json.dumps(evidence,sort_keys=True,separators=(',',':')).encode()
    digest=hashlib.sha256(payload).hexdigest()
    name=f'acceptance/{args.execution}-{digest}.json'
    arm=BASE+f'Microsoft.Storage/storageAccounts/{ACCOUNT}/blobServices/default/containers/{CONTAINER}?api-version=2023-05-01'
    validate_archive_properties(request('GET',arm))
    service=BlobServiceClient(f'https://{ACCOUNT}.blob.core.windows.net',credential=AzureCliCredential())
    blob=service.get_blob_client(CONTAINER,name)
    try: blob.upload_blob(payload,overwrite=False,content_settings=ContentSettings(content_type='application/json'))
    except ResourceExistsError: pass
    properties=blob.get_blob_properties()
    pinned=service.get_blob_client(CONTAINER,name,version_id=properties.version_id)
    if properties.size!=len(payload) or pinned.download_blob(offset=0,length=len(payload)+1).readall()!=payload:
        raise ValueError('Protected receipt readback mismatch')
    validate_archive_properties(request('GET',arm))
    print(json.dumps(dict(container=CONTAINER,blob=name,sha256=digest,version_id=properties.version_id,
                         protected_readback_verified=True)))


if __name__=='__main__': main()
