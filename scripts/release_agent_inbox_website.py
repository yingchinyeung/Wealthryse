"""One reviewed website-image change; no worker or notification activation.

Requires real-login private candidate acceptance. Retains protected rollback
intent before update and checks that every non-image setting and Easy Auth
configuration is unchanged. Unknown outcomes must be inspected, not retried.
"""
import argparse
import hashlib
import json
import requests

from audit_azure_runtime import az,RG
from deploy_metadata_monitor import request,BASE,SUB
from deploy_specialist_report_worker import ROOT
from deploy_agent_inbox_access import IMAGE
from release_verified_image import invariant
from run_specialist_gateway_read_verification import retain

APP='ibkrv2stg-web'
PATH=BASE+'Microsoft.App/containerApps/'+APP
FOLDER=ROOT/'.work/specialist-inbox-web-release-20261003'
OLD_IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:8f3a771c67ce4dc5f98aa425f5e6bf4f1b77981447538ed2d165df25b774aa64'
OLD_REVISION='ibkrv2stg-web--0000111'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def auth_hash():
    value=request('GET',PATH+'/authConfigs/current?api-version=2024-03-01')
    if not value['properties'].get('platform',{}).get('enabled'):
        raise ValueError('Enabled website authentication required')
    return fingerprint(value['properties'])


def public_probe(resource):
    fqdn=resource['properties']['configuration']['ingress']['fqdn']
    if fqdn!='ibkrv2stg-web.purplerock-8cff24a2.eastasia.azurecontainerapps.io':
        raise ValueError('Unregistered website hostname')
    response=requests.get('https://'+fqdn+'/healthz',allow_redirects=False,timeout=30)
    # Existing Easy Auth protects healthz too. A 401 proves the public auth
    # boundary, not backend health. Revision health and signed-in browser
    # acceptance are independently required; never exempt healthz from auth.
    if response.status_code==401:
        return dict(status_code=401,sign_in_required=True,application_health_confirmed=False)
    if response.status_code==200 and response.json()==dict(status='ok'):
        return dict(status_code=200,body=dict(status='ok'),application_health_confirmed=True)
    raise ValueError('Public website boundary probe failed')


def revision_health(revision,image):
    state=az('containerapp','revision','show','-g',RG,'-n',APP,'--revision',revision)['properties']
    if state.get('healthState')!='Healthy' or state.get('runningState') not in ('Running','RunningAtMaxScale') or not state.get('active'):
        raise ValueError('Healthy active website revision required')
    if state['template']['containers'][0]['image']!=image: raise ValueError('Ready revision image differs')
    return dict(health_state=state['healthState'],running_state=state['runningState'],active=True)


def prerequisites():
    candidate=json.loads((ROOT/'.work/ai-inbox-candidate-20261003-r2/acceptance.json').read_text())
    probe=json.loads((ROOT/'.work/specialist-inbox-access-20261003-r2/test-acceptance.json').read_text())
    if candidate.get('status')!='passed' or candidate.get('image')!=IMAGE or probe.get('status')!='passed' or probe.get('image')!=IMAGE:
        raise ValueError('Candidate build and private real-login acceptance required')
    record=probe['record']
    if record.get('tenant_isolation_verified') is not True or record.get('login')!='ibkr_stg_web':
        raise ValueError('Real website login and tenant isolation acceptance required')
    wanted={('owner',p):200 for p in ('/ai-operations','/api/ai-operations/daily-reports')}
    wanted.update({(label,p):403 for label in ('viewer','anonymous') for p in
        ('/ai-operations','/api/ai-operations/daily-reports','/api/ai-operations/daily-reports/2026-10-02')})
    actual={(v['identity'],v['path']):v['status'] for v in record.get('http_checks',[])}
    if any(actual.get(k)!=v for k,v in wanted.items()): raise ValueError('Owner and denied-user route acceptance required')
    for value in (candidate,probe):
        if not value.get('protected_receipt',{}).get('protected_readback_verified'): raise ValueError('Protected prerequisites required')
    registry=az('acr','manifest','show-metadata','-r','ibkrv2stgauhecwc4itaoo','-n','ibkr-platform@'+IMAGE.split('@')[1])
    if registry.get('digest')!=IMAGE.split('@')[1]: raise ValueError('Candidate registry digest differs')
    return dict(candidate_sha256=fingerprint(candidate),private_acceptance_sha256=fingerprint(probe))


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=('start','inspect','verify'));args=p.parse_args()
    if az('account','show','--query','id')!=SUB: raise ValueError('Unexpected subscription')
    FOLDER.mkdir(parents=True,exist_ok=True)
    intent_path=FOLDER/'intent.json'
    resource=request('GET',PATH+'?api-version=2024-03-01')
    properties=resource['properties']; containers=properties['template']['containers']
    if len(containers)!=1: raise ValueError('One registered web container required')
    if args.stage=='start':
        if intent_path.exists(): raise ValueError('Release intent exists; inspect instead of retrying')
        acceptance=prerequisites()
        if containers[0]['image']!=OLD_IMAGE or properties['latestReadyRevisionName']!=OLD_REVISION:
            raise ValueError('Verified rollback website changed')
        if properties['configuration'].get('activeRevisionsMode')!='Single' or properties['configuration']['ingress']['traffic']!=[dict(revisionName=OLD_REVISION,weight=100)]:
            raise ValueError('Registered single-revision website traffic required')
        env={v['name']:v for v in containers[0]['env']}
        if env.get('AUTHORIZATION_MODE')!=dict(name='AUTHORIZATION_MODE',value='database'):
            raise ValueError('Database-only authorization required')
        intent=dict(previous_image=OLD_IMAGE,previous_revision=OLD_REVISION,candidate_image=IMAGE,
            invariant_sha256=invariant(resource,'app'),auth_configuration_sha256=auth_hash(),
            previous_public_probe=public_probe(resource),previous_revision_health=revision_health(OLD_REVISION,OLD_IMAGE),
            prerequisites=acceptance,scope='one website image only',
            notifications_enabled=False,schedules_changed=False,source_rows_changed=0)
        receipt=retain(intent,'inbox-web-release-intent')
        intent_path.write_text(json.dumps(dict(intent=intent,protected_receipt=receipt),indent=2),encoding='utf-8')
        # Explicit owner approval of the rollout is already recorded; no other
        # application, job, role, environment or traffic setting is updated.
        az('containerapp','update','-g',RG,'-n',APP,'--container-name',containers[0]['name'],'--image',IMAGE)
        print(json.dumps(dict(update_requested=True,image=IMAGE,protected_intent=receipt)),flush=True);return
    saved=json.loads(intent_path.read_text());intent=saved['intent']
    if invariant(resource,'app')!=intent['invariant_sha256'] or auth_hash()!=intent['auth_configuration_sha256']:
        raise ValueError('Non-image or authentication configuration changed; stop and inspect')
    status=dict(image=containers[0]['image'],latest_revision=properties.get('latestRevisionName'),
        latest_ready_revision=properties.get('latestReadyRevisionName'),provisioning_state=properties.get('provisioningState'),
        traffic=properties['configuration']['ingress'].get('traffic'),non_image_settings_unchanged=True,
        authentication_settings_unchanged=True,notifications_enabled=False,source_rows_changed=0)
    if args.stage=='inspect': print(json.dumps(status),flush=True);return
    revision=properties['latestReadyRevisionName']
    if containers[0]['image']!=IMAGE or properties.get('latestRevisionName')!=revision or revision==OLD_REVISION or properties.get('provisioningState')!='Succeeded':
        raise ValueError('New website revision not ready')
    if properties['configuration']['ingress'].get('traffic')!=[dict(revisionName=revision,weight=100)]:
        raise ValueError('All website traffic must target the accepted revision')
    status.update(status='passed',public_probe=public_probe(resource),revision_health=revision_health(revision,IMAGE),
        browser_acceptance_complete=False,rollback_image=OLD_IMAGE,rollback_revision=OLD_REVISION)
    status['protected_receipt']=retain(dict(result=status,intent=saved),'inbox-web-release-completed')
    (FOLDER/'acceptance.json').write_text(json.dumps(status,indent=2),encoding='utf-8');print(json.dumps(status),flush=True)


if __name__=='__main__': main()
