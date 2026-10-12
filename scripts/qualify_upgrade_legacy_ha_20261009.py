"""Clone-only compute/HA rehearsal. Never mutates the legacy source server."""
import copy,json,sys
from datetime import datetime,timezone
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request,rid
from control_upgrade_legacy_recovery_probe_20261009 import protected
from run_specialist_gateway_read_verification import retain
from audit_azure_runtime import az

ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage10'
OUT=ROOT/'legacy-ha-rehearsal-v1'
SOURCE=rid('Microsoft.DBforPostgreSQL/flexibleServers','ibkrv11-pg-5rcc62xxqblay')
TARGET=rid('Microsoft.DBforPostgreSQL/flexibleServers','ibkrv11-upgrade-recovery-1009')
API='?api-version=2024-08-01'
SKU={'name':'Standard_D2ds_v5','tier':'GeneralPurpose'}

def invariant(server):
    value=copy.deepcopy(server)
    # Service status, restore horizon and role/zone change during qualification;
    # credentials, networking, version, storage, backup policy remain invariant.
    props=value['properties']
    for k in ('state','highAvailability','availabilityZone','minorVersion'):
        props.pop(k,None)
    props.get('backup',{}).pop('earliestRestoreDate',None)
    return {'location':value['location'],'identity':value.get('identity'),'tags':value.get('tags'),'properties':props}

def guarded_clone(server):
    p=server['properties']
    # Azure PITR inherits the source tags, despite tags in the restore request.
    # Identity is established by the exact ID, dedicated subnet, protected
    # restore dispatch and independently reconciled table fingerprints.
    if server['id'].lower()!=TARGET.lower() or server['location'].replace(' ','').lower()!='eastasia' or server.get('tags',{}).get('dataClassification')!='confidential-financial':
        raise ValueError('Only isolated East Asia recovery clone is eligible')
    if p['network']['publicNetworkAccess']!='Disabled' or not p['network']['delegatedSubnetResourceId'].endswith('/ibkrv11-vnet/subnets/upgrade-recovery'):
        raise ValueError('Clone private network differs')

def save(name,value):
    record={**value,'protected_receipt':retain(value,'upgrade-legacy-ha-'+name)}
    with (OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump(record,f,indent=2)

def load(name):return protected(OUT/(name+'.json'))

def checked(plan,source,clone):
    guarded_clone(clone)
    if invariant(source)!=plan['source_invariant'] or source['sku']!=plan['source_sku'] or source['properties']['highAvailability']!=plan['source_ha']:
        raise ValueError('Live legacy source changed; reconcile before continuing')
    if invariant(clone)!=plan['clone_invariant']:raise ValueError('Clone non-compute/HA settings changed')

def main(stage):
    source=request('GET',SOURCE+API);clone=request('GET',TARGET+API);guarded_clone(clone)
    if stage=='plan':
        if OUT.exists():raise ValueError('Preserve existing rehearsal')
        proof=protected(ROOT/'legacy-recovery-probe-v1/result.json')
        restore=protected(ROOT/'legacy-recovery-v1/restore-intent.json')
        ack=protected(ROOT/'legacy-recovery-v1/restore-ack.json')
        if restore['target']!=TARGET or (ack.get('id') and ack['id'].lower()!=TARGET.lower()) or restore['body']['properties']['sourceServerResourceId']!=SOURCE or restore['body']['properties']['createMode']!='PointInTimeRestore':raise ValueError('Exact protected restore provenance required')
        if not proof['passed'] or proof['proof']['database_writes']!=0 or proof['proof']['table_differences']:raise ValueError('All-table recovery comparison required')
        if clone['properties']['state']!='Ready' or clone['sku']['tier']!='Burstable' or clone['properties']['highAvailability']['mode']!='Disabled':raise ValueError('Unexpected clone starting configuration')
        capabilities=az('postgres','flexible-server','list-skus','--location','eastasia')
        selected=[s for c in capabilities for e in c['supportedServerEditions'] if e['name']==SKU['tier'] for s in e['supportedServerSkus'] if s['name']==SKU['name']]
        if len(selected)!=1 or 'ZoneRedundant' not in selected[0]['supportedHaMode'] or not {'1','2','3'}<=set(selected[0]['supportedZones']):raise ValueError('Requested HA SKU unavailable in East Asia')
        standby='1' if clone['properties']['availabilityZone']!='1' else '2'
        OUT.mkdir()
        save('plan',{'source_invariant':invariant(source),'source_sku':source['sku'],'source_ha':source['properties']['highAvailability'],'clone_invariant':invariant(clone),'clone_sku':clone['sku'],'initial_zone':clone['properties']['availabilityZone'],'sku':SKU,'standby_zone':standby,'capability':selected[0],'recovery_proof':proof['protected_receipt'],'scope':'Only isolated clone compute and HA; source remains unchanged, no database redirect or credential mutation. Reconcile each unknown response; never repeat mutation.','rollback':'On clone only: disable HA after qualification, verify Ready, restore original SKU, then independently fingerprint all tables. No automatic live fallback or live failover.','created_at':datetime.now(timezone.utc).isoformat()})
        print('Prepared isolated legacy compute and HA rehearsal; source untouched');return
    plan=load('plan');checked(plan,source,clone)
    if stage=='state':
        print(json.dumps({'state':clone['properties']['state'],'sku':clone['sku'],'ha':clone['properties']['highAvailability'],'zone':clone['properties']['availabilityZone'],'source_unchanged':True}));return
    if stage=='enable-explicit-zone':
        failure=load('enable-diagnostic-result')
        if failure['http_status']!=400 or failure['error_code']!='InvalidParameterValue' or clone['properties']['state']!='Ready' or clone['properties']['highAvailability']['mode']!='Disabled' or clone['sku']!=SKU:raise ValueError('Reconciled rejected zone parameters required')
        if (OUT/'enable-explicit-zone-intent.json').exists():raise ValueError('Reconcile existing explicit-zone operation')
        primary=clone['properties']['availabilityZone']
        if primary!=plan['initial_zone'] or primary==plan['standby_zone']:raise ValueError('Never move the existing primary zone')
        body={'properties':{'availabilityZone':primary,'highAvailability':{'mode':'ZoneRedundant','standbyAvailabilityZone':plan['standby_zone']}}}
        save('enable-explicit-zone-intent',{'target':TARGET,'body':body,'started_at':datetime.now(timezone.utc).isoformat(),'primary_zone_preserved':True})
        import requests
        import deploy_viewer_private_admin_20261008 as arm
        response=requests.patch('https://management.azure.com'+TARGET+API,headers={'Authorization':'Bearer '+arm._TOKEN},json=body,timeout=60)
        body=response.json() if response.content else {};error=body.get('error') or {}
        result={'http_status':response.status_code,'accepted':response.ok,'error_code':error.get('code'),'error_message':error.get('message')}
        save('enable-explicit-zone-result',result);print(json.dumps(result));return
    if stage=='reconcile-enable':
        old=load('enable-intent')
        if clone['properties']['state']!='Ready' or clone['sku']!=SKU or clone['properties']['highAvailability']['mode']!='Disabled':raise ValueError('Rejected operation not reconciled')
        if (OUT/'enable-diagnostic-intent.json').exists():raise ValueError('Inspect recorded diagnostic outcome; never retry')
        # The original request returned HTTP 400, and a fresh Ready/Disabled
        # readback establishes that it did not enable HA. One journaled retry
        # retains the service error instead of discarding its diagnostic code.
        save('enable-rejected-reconciliation',{'original_response':400,'state':clone['properties']['state'],'ha':clone['properties']['highAvailability'],'source_unchanged':True,'original_intent':old['protected_receipt']})
        save('enable-diagnostic-intent',{'target':TARGET,'body':old['body'],'started_at':datetime.now(timezone.utc).isoformat()})
        import requests
        import deploy_viewer_private_admin_20261008 as arm
        response=requests.patch('https://management.azure.com'+TARGET+API,headers={'Authorization':'Bearer '+arm._TOKEN},json=old['body'],timeout=60)
        body=response.json() if response.content else {}
        error=body.get('error') or {}
        result={'http_status':response.status_code,'accepted':response.ok,'error_code':error.get('code'),'error_message':error.get('message')}
        save('enable-diagnostic-result',result);print(json.dumps(result));return
    if stage in ('disable-ha','restore-sku','verify-rollback'):
        if not load('verify-failover-result')['passed']:raise ValueError('Observed failover required before rollback')
        if stage=='verify-rollback':
            if clone['properties']['state']!='Ready' or clone['properties']['highAvailability']['mode']!='Disabled' or clone['sku']!=plan['clone_sku']:raise ValueError('Original compute and disabled HA not restored')
            proof=protected(ROOT/'legacy-ha-rollback-read-v1/result.json')
            if not proof['passed'] or proof['proof']['table_differences']:raise ValueError('Full-table rollback preservation required')
            if not (OUT/'verify-rollback-result.json').exists():save('verify-rollback-result',{'passed':True,'source_unchanged':True,'original_sku_restored':clone['sku'],'ha':clone['properties']['highAvailability'],'data_preservation':proof['protected_receipt'],'scope':'Isolated clone failover and compute/HA rollback only; production unchanged'})
            print('Isolated failover rollback and all-table preservation verified');return
        if clone['properties']['state']!='Ready' or (OUT/(stage+'-intent.json')).exists():raise ValueError('Reconcile existing rollback; do not retry')
        if stage=='disable-ha':
            if clone['properties']['highAvailability']['state']!='Healthy' or clone['sku']!=SKU:raise ValueError('Healthy qualified clone required')
            body={'properties':{'highAvailability':{'mode':'Disabled'}}}
        else:
            load('disable-ha-intent')
            if clone['properties']['highAvailability']['mode']!='Disabled' or clone['sku']!=SKU:raise ValueError('Disable HA and verify Ready first')
            body={'sku':plan['clone_sku']}
        save(stage+'-intent',{'target':TARGET,'body':body,'source_unchanged':True,'started_at':datetime.now(timezone.utc).isoformat()})
        response=request('PATCH',TARGET+API,data=body);save(stage+'-ack',{'id':response.get('id'),'state':response.get('properties',{}).get('state')});print('Submitted clone-only rollback step: '+stage);return
    if stage not in ('scale','enable','verify-scale','verify-ha','failover','verify-failover'):raise ValueError('Unknown rehearsal step')
    if stage=='verify-failover':
        operation=load('failover-intent');observer=protected(ROOT/'legacy-failover-probe-v2/result.json')
        if clone['properties']['state']!='Ready' or clone['properties']['highAvailability']['state']!='Healthy':print('Clone failover still recovering');return
        if not observer['passed'] or not observer['proof']['table_fingerprints_unchanged'] or clone['properties']['availabilityZone']!=operation['standby_before'] or clone['properties']['availabilityZone']==operation['primary_before']:raise ValueError('Observed clone failover and data preservation not proven')
        if not (OUT/'verify-failover-result.json').exists():save('verify-failover-result',{'passed':True,'source_unchanged':True,'new_primary_zone':clone['properties']['availabilityZone'],'ha':clone['properties']['highAvailability'],'observer_receipt':observer['protected_receipt'],'observed_maximum_success_sample_gap_seconds':observer['proof']['largest_success_sample_gap_seconds'],'qualification':'Planned clone failover and reconnect; not a service-level guarantee or live outage test.'})
        print('Planned isolated failover, reconnect and full-table preservation passed');return
    if stage.startswith('verify-'):
        if clone['properties']['state']!='Ready':print(json.dumps({'state':clone['properties']['state']}));return
        if clone['sku']!=SKU:raise ValueError('Qualified SKU not installed')
        if stage=='verify-ha' and (clone['properties']['highAvailability']['mode']!='ZoneRedundant' or clone['properties']['highAvailability']['state']!='Healthy' or clone['properties']['highAvailability']['standbyAvailabilityZone']!=plan['standby_zone']):
            print(json.dumps({'ha':clone['properties']['highAvailability']}));return
        name=stage+'-result'
        if not (OUT/(name+'.json')).exists():save(name,{'passed':True,'target':TARGET,'sku':clone['sku'],'ha':clone['properties']['highAvailability'],'verified_at':datetime.now(timezone.utc).isoformat(),'source_unchanged':True})
        print(json.dumps({'passed':True,'step':stage}));return
    if clone['properties']['state']!='Ready':raise ValueError('Clone must be Ready')
    if (OUT/(stage+'-intent.json')).exists():raise ValueError('Existing operation: reconcile, never redispatch')
    if stage=='failover':
        ready=protected(ROOT/'legacy-failover-probe-v2/ready.json')
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(ready['event_at'].replace('Z','+00:00'))).total_seconds()
        run=request('GET',rid('Microsoft.App/jobs','ibkr-v11-daily')+'/executions/'+ready['execution']+'?api-version=2024-03-01')
        if not 0<=age<=180 or run['properties']['status']!='Running' or not load('verify-ha-result')['passed'] or clone['properties']['highAvailability']['state']!='Healthy':raise ValueError('Fresh running clone observer and healthy HA required')
        body={'restartWithFailover':True,'failoverMode':'PlannedFailover'}
        save('failover-intent',{'target':TARGET,'body':body,'primary_before':clone['properties']['availabilityZone'],'standby_before':clone['properties']['highAvailability']['standbyAvailabilityZone'],'started_at':datetime.now(timezone.utc).isoformat(),'observer':ready['execution'],'source_unchanged':True})
        response=request('POST',TARGET+'/restart'+API,data=body)
        save('failover-ack',{'id':response.get('id'),'state':response.get('properties',{}).get('state')})
        print('Submitted planned failover of the isolated recovery clone only');return
    if stage=='scale':
        if clone['sku']!=plan['clone_sku'] or clone['properties']['highAvailability']['mode']!='Disabled':raise ValueError('Initial clone drift')
        body={'sku':SKU}
    else:
        if not load('verify-scale-result')['passed'] or clone['sku']!=SKU or clone['properties']['highAvailability']['mode']!='Disabled':raise ValueError('Scale verification required')
        body={'properties':{'highAvailability':{'mode':'ZoneRedundant','standbyAvailabilityZone':plan['standby_zone']}}}
    save(stage+'-intent',{'target':TARGET,'body':body,'started_at':datetime.now(timezone.utc).isoformat(),'source_unchanged':True})
    response=request('PATCH',TARGET+API,data=body)
    save(stage+'-ack',{'id':response.get('id'),'state':response.get('properties',{}).get('state')})
    print(json.dumps({'submitted':stage,'target':TARGET,'production_changed':False}))

if __name__=='__main__':main(sys.argv[1])
