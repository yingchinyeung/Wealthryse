"""Deploy/verify one isolated staging gateway, preserving all existing workloads.

No authority enrollment, consent, role grants, schedules or production releases.
Admission is process-local, so replicas/workers are fixed at one.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import requests
from audit_azure_runtime import az
from deploy_metadata_monitor import request,BASE,SUB,VAULT
from register_specialist_gateway import find,verify,API,CLIENT

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/specialist-gateway-20261002'
NAME='ibkrv2stg-agent-gateway'
PATH=BASE+'Microsoft.App/containerApps/'+NAME
REGISTRY='ibkrv2stgauhecwc4itaoo.azurecr.io'
IDENTITIES={kind:BASE+'Microsoft.ManagedIdentity/userAssignedIdentities/ibkrv2stg-specialist-'+kind+'-identity'
    for kind in ('observation','authority','audit')}


def definition(image):
    if not re.fullmatch(re.escape(REGISTRY)+r'/specialist-gateway@sha256:[0-9a-f]{64}',image):
        raise ValueError('Exact isolated gateway image digest required')
    manifest=json.loads((FOLDER/'context/gateway-build.json').read_text())
    from ibkr_etl.specialist_evidence import OBSERVATION_MANIFESTS
    if manifest['source_manifest_sha256'] not in OBSERVATION_MANIFESTS: raise ValueError('Reviewed source enrollment required')
    refs=[];env=[]
    for kind,identity in IDENTITIES.items():
        refs.append(dict(name=kind+'-url',keyVaultUrl=f'https://{VAULT}.vault.azure.net/secrets/database-specialist-{kind}-url',identity=identity))
        env.append(dict(name='SPECIALIST_'+kind.upper()+'_DATABASE_URL',secretRef=kind+'-url'))
    return dict(location='eastasia',identity=dict(type='UserAssigned',userAssignedIdentities={i:{} for i in IDENTITIES.values()}),
        tags=dict(purpose='isolated-specialist-observation-gateway',sourceManifestSha256=manifest['source_manifest_sha256']),
        properties=dict(environmentId=BASE+'Microsoft.App/managedEnvironments/ibkrv2stg-env',
            configuration=dict(activeRevisionsMode='Single',secrets=refs,
                registries=[dict(server=REGISTRY,identity=IDENTITIES['observation'])],
                ingress=dict(external=True,targetPort=8000,transport='http',allowInsecure=False)),
            template=dict(containers=[dict(name='gateway',image=image,command=['python'],args=['scripts/serve_specialist_gateway.py'],
                env=env,resources=dict(cpu=0.5,memory='1Gi'),probes=[
                    dict(type='Startup',httpGet=dict(path='/health',port=8000),periodSeconds=10,timeoutSeconds=5,failureThreshold=30),
                    dict(type='Readiness',httpGet=dict(path='/health',port=8000),periodSeconds=30,timeoutSeconds=5,failureThreshold=3)])],
                scale=dict(minReplicas=1,maxReplicas=1))))


def validate(current,wanted):
    if (current.get('identity',{}).get('type')!='UserAssigned' or
        {i.lower() for i in current['identity'].get('userAssignedIdentities',{})}!={i.lower() for i in IDENTITIES.values()}):
        raise ValueError('Gateway identities differ')
    for section in ('environmentId',):
        if current['properties'].get(section,'').lower()!=wanted['properties'][section].lower(): raise ValueError('Gateway environment differs')
    cfg=current['properties']['configuration'];wcfg=wanted['properties']['configuration']
    if cfg.get('activeRevisionsMode')!='Single': raise ValueError('Single-revision gateway required')
    ingress=cfg.get('ingress',{})
    if (ingress.get('transport','').lower()!='http'
        or any(ingress.get(k)!=v for k,v in wcfg['ingress'].items() if k!='transport')
        or ingress.get('additionalPortMappings') or ingress.get('customDomains') or ingress.get('corsPolicy')):
        raise ValueError('Gateway ingress differs')
    def bindings(values): return sorted((v.get('name'),v.get('keyVaultUrl'),v.get('identity','').lower()) for v in values)
    if bindings(cfg.get('secrets',[]))!=bindings(wcfg['secrets']): raise ValueError('Gateway credential scopes differ')
    registries=cfg.get('registries',[])
    if (len(registries)!=1 or any(registries[0].get(k)!=v for k,v in wcfg['registries'][0].items())
        or registries[0].get('username') or registries[0].get('passwordSecretRef')):
        raise ValueError('Gateway registry binding differs')
    template=current['properties']['template'];target=wanted['properties']['template']
    scale=template.get('scale',{})
    if scale.get('minReplicas')!=1 or scale.get('maxReplicas')!=1 or scale.get('rules'): raise ValueError('Gateway single-replica admission differs')
    if template.get('initContainers') or template.get('volumes'): raise ValueError('Unregistered gateway resources')
    containers=template.get('containers',[])
    if len(containers)!=1 or any(containers[0].get(k)!=target['containers'][0][k] for k in ('name','image','command','args','env')):
        raise ValueError('Gateway reviewed runtime differs')
    resources=containers[0].get('resources',{})
    if (any(resources.get(k)!=v for k,v in target['containers'][0]['resources'].items())
        or set(resources)-{'cpu','memory','ephemeralStorage'} or resources.get('ephemeralStorage') not in (None,'2Gi')):
        raise ValueError('Gateway capacity differs')
    if sorted(containers[0].get('probes',[]),key=lambda p:p['type'])!=sorted(target['containers'][0]['probes'],key=lambda p:p['type']):
        raise ValueError('Gateway health probes differ')


def check_identity_bindings():
    expected={'observation':('bbe76059-bdd6-4c52-a644-877460a24f7f',2),
        'authority':('15cc67b9-ba8d-473e-b537-20c0eaace8c1',2),
        'audit':('60313d53-6387-46d4-9bb8-dc5f8ab925a5',4)}
    for kind,path in IDENTITIES.items():
        row=request('GET',path+'?api-version=2023-01-31')['properties'];principal=expected[kind][0]
        if row['principalId']!=principal: raise ValueError('Gateway trusted identity changed')
        grants=az('role','assignment','list','--all','--include-inherited','--query',
            "[?principalId=='"+principal+"'].{scope:scope,role:roleDefinitionId}")
        from ibkr_etl.specialist_archive_reader import SCOPE
        scopes={(BASE+'Microsoft.ContainerRegistry/registries/ibkrv2stgauhecwc4itaoo','7f951dda-4ed3-4680-a7ca-43fe172d538d'),
            (BASE+f'Microsoft.KeyVault/vaults/{VAULT}/secrets/database-specialist-{kind}-url','4633458b-17de-408a-b874-0445c86b69e6')}
        if kind=='audit': scopes|={(SCOPE,'ba92f5b4-2d11-453d-a403-e96b0029c9fe'),(SCOPE,'acdd72a7-3385-48ef-bd42-f606fba81ae7')}
        if len(grants)!=expected[kind][1] or {(g['scope'].lower(),g['role'].rsplit('/',1)[-1]) for g in grants}!={(s.lower(),r) for s,r in scopes}:
            raise ValueError('Gateway identity role scopes differ')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['install','verify']);parser.add_argument('--image',required=True);args=parser.parse_args()
    if az('account','show','--query','id')!=SUB: raise ValueError('Unexpected subscription')
    verify(find(API),find(CLIENT));check_identity_bindings()
    wanted=definition(args.image);current=request('GET',PATH+'?api-version=2024-03-01',missing=True)
    if args.stage=='install':
        if current is not None: raise ValueError('Gateway exists; inspect instead of replacing')
        FOLDER.mkdir(parents=True,exist_ok=True)
        (FOLDER/'definition.json').write_text(json.dumps(wanted,indent=2),encoding='utf-8')
        request('PUT',PATH+'?api-version=2024-03-01',data=wanted)
        print(json.dumps(dict(installed=NAME,authority_rows_written=0,existing_workloads_changed=0)));return
    if current is None: raise ValueError('Gateway is absent')
    validate(current,wanted)
    p=current['properties']
    if p.get('provisioningState')!='Succeeded' or p.get('runningStatus')!='Running': raise ValueError('Gateway deployment not ready')
    url='https://'+p['configuration']['ingress']['fqdn']
    health=requests.get(url+'/health',timeout=30,allow_redirects=False)
    if health.status_code!=200 or health.json()!=dict(status='ready',mode='delegated-observation',background_agents_enabled=False):
        raise ValueError('Gateway live fresh-login readiness unverified')
    responses=[]
    for headers in ({},{'x-ms-client-principal-id':'forged'},{'authorization':'Bearer invalid'}):
        response=requests.post(url+'/v1/observations',json=dict(tool_name='get_performance_series',arguments=dict(subject_id='U16088571')),
            headers=headers,timeout=30,allow_redirects=False)
        if response.status_code!=401: raise ValueError('Unauthenticated observation not denied')
        responses.append(response.status_code)
    docs=requests.get(url+'/docs',timeout=20,allow_redirects=False)
    if docs.status_code!=404: raise ValueError('Unexpected gateway documentation exposure')
    result=dict(status='passed',url=url,image=args.image,configuration_verified=True,identity_bindings_verified=True,
        fresh_login_readiness_verified=True,unauthenticated_statuses=responses,docs_status=docs.status_code,
        authority_rows_written=0,background_agents_enabled=False,existing_workloads_changed=0)
    (FOLDER/'acceptance.json').write_text(json.dumps(result,sort_keys=True,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
