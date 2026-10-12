"""Retain the isolated gateway's accepted deployment, build and registration.

Create-only evidence in the protected checkpoint container. Tokens excluded.
"""
import hashlib
import json
from pathlib import Path
from azure.core.exceptions import ResourceExistsError
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient,ContentSettings
from ibkr_etl.specialist_audit_service import validate_archive_properties
from ibkr_etl.specialist_archive_reader import ACCOUNT,CONTAINER,SCOPE
from audit_azure_runtime import az
from deploy_metadata_monitor import request
from register_specialist_gateway import find,verify,API,CLIENT

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/specialist-gateway-20261002'


def main():
    acceptance=json.loads((FOLDER/'acceptance.json').read_text())
    definition=json.loads((FOLDER/'definition.json').read_text())
    manifest=json.loads((FOLDER/'context/gateway-build.json').read_text())
    if (acceptance.get('status')!='passed' or acceptance.get('configuration_verified') is not True
        or acceptance.get('fresh_login_readiness_verified') is not True or acceptance.get('unauthenticated_statuses')!=[401]*3
        or acceptance.get('background_agents_enabled') is not False or acceptance.get('authority_rows_written')!=0):
        raise ValueError('Verified gateway acceptance required')
    build=az('acr','task','show-run','-r',ACCOUNT,'--run-id','ck7r')
    expected=acceptance['image'].split('@',1)[1]
    images=build.get('outputImages',[])
    if (build.get('status')!='Succeeded' or len(images)!=1 or images[0].get('digest')!=expected
        or images[0].get('repository')!='specialist-gateway'):
        raise ValueError('Accepted gateway ACR build required')
    registration=verify(find(API),find(CLIENT))
    evidence=dict(schema='specialist-gateway-deployment-acceptance-v1',acceptance=acceptance,
        definition=definition,source_manifest=manifest,registration=registration,
        build=dict(run_id=build['runId'],status=build['status'],image_digest=expected,
            started_at=build['startTime'],completed_at=build['finishTime']),
        financial_data_included=False,tokens_included=False,authority_enrollment_completed=False)
    owner=FOLDER/'owner-authentication-acceptance.json'
    if owner.exists(): evidence['owner_authentication']=json.loads(owner.read_text())
    payload=json.dumps(evidence,sort_keys=True,separators=(',',':')).encode();digest=hashlib.sha256(payload).hexdigest()
    arm=SCOPE+'?api-version=2023-05-01';validate_archive_properties(request('GET',arm))
    service=BlobServiceClient('https://'+ACCOUNT+'.blob.core.windows.net',credential=AzureCliCredential())
    name='acceptance/specialist-gateway-'+digest+'.json';blob=service.get_blob_client(CONTAINER,name)
    try: blob.upload_blob(payload,overwrite=False,content_settings=ContentSettings(content_type='application/json'))
    except ResourceExistsError:pass
    properties=blob.get_blob_properties();version=properties.version_id
    if (not version or properties.size!=len(payload)
        or service.get_blob_client(CONTAINER,name,version_id=version).download_blob(offset=0,length=len(payload)+1).readall()!=payload):
        raise ValueError('Protected gateway receipt readback failed')
    validate_archive_properties(request('GET',arm))
    receipt=dict(container=CONTAINER,blob=name,sha256=digest,version_id=version,protected_readback_verified=True)
    (FOLDER/'protected-acceptance.json').write_text(json.dumps(receipt,sort_keys=True,indent=2),encoding='utf-8')
    print(json.dumps(receipt))


if __name__=='__main__':main()
