"""Owner-approved read-only grant, scoped exclusively to the new QA container."""
import hashlib,json,sys,uuid
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request,SUB,OWNER
from audit_azure_runtime import az
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_g04_fresh_20261010 import CONTAINER,STORAGE_API

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261010-stage29/qa-reader-v1'
ROLE='/subscriptions/'+SUB+'/providers/Microsoft.Authorization/roleDefinitions/2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
NAME=str(uuid.uuid5(uuid.NAMESPACE_URL,CONTAINER.lower()+'|'+OWNER+'|'+ROLE.rsplit('/',1)[1]))
TARGET=CONTAINER+'/providers/Microsoft.Authorization/roleAssignments/'+NAME
API='?api-version=2022-04-01'


def main(stage):
    if stage not in ('apply','verify'):raise ValueError('Unknown operation')
    role=request('GET',ROLE+API)['properties']
    if role['roleName']!='Storage Blob Data Reader' or len(role['permissions'])!=1 or set(role['permissions'][0]['dataActions'])!={'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read'}:
        raise ValueError('Exact built-in read-only role required')
    if az('ad','signed-in-user','show','--query','id')!=OWNER:raise ValueError('Current owner identity differs')
    storage=request('GET',CONTAINER+STORAGE_API)
    if storage['properties']['publicAccess']!='None' or storage['properties']['metadata'].get('purpose')!='isolated-upgrade-g04':raise ValueError('QA storage boundary differs')
    body={'properties':{'roleDefinitionId':ROLE,'principalId':OWNER,'principalType':'User',
        'description':'Owner-approved QA report visual inspection only; no production-report or viewer entitlement changes.'}}
    if stage=='apply':
        if OUT.exists() or request('GET',TARGET+API,missing=True) is not None:raise ValueError('Reconcile prior assignment')
        save(OUT,'intent',{'target':TARGET,'scope':CONTAINER,'body':body,'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'owner_approval':'Approve read-only access to the QA container','approved_date':'2026-10-10',
            'role_definition':role,'production_scope_grants':0,'viewer_permissions_changed':False})
        request('PUT',TARGET+API,data=body)
    intent=protected(OUT/'intent.json')
    if intent['body']!=body or intent['source_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise ValueError('Grant source drift')
    result=request('GET',TARGET+API)['properties']
    if result['scope'].lower()!=CONTAINER.lower() or result['principalId']!=OWNER or result['principalType']!='User' or result['roleDefinitionId'].lower()!=ROLE.lower():raise ValueError('Read-only grant differs')
    if not (OUT/'verified.json').exists():save(OUT,'verified',{'passed':True,'target':TARGET,'scope':CONTAINER,'principal':OWNER,
        'role':'Storage Blob Data Reader','production_scope_grants':0,'viewer_permissions_changed':False})
    print('Exact owner-only QA-container read grant verified')


if __name__=='__main__':main(*sys.argv[1:])
