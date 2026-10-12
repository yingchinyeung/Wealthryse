"""Remove only the accepted temporary inbox reader-grant job, not its grants.

Its exact definition and execution evidence remain recoverable in protected
storage. No shared identity, secret, registry, database object or source is removed.
"""
import argparse
import json
from deploy_specialist_report_worker import ROOT
import deploy_agent_inbox_access as access
from run_specialist_gateway_read_verification import retain

FOLDER=ROOT/'.work/specialist-inbox-access-20261003-r2'


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=('start','verify'));args=p.parse_args()
    if access.az('account','show','--query','id')!=access.SUB: raise ValueError('Unexpected subscription')
    access.JOBS={key:value+'-r2' for key,value in access.JOBS.items()}
    path=access.job_path('grant')+'?api-version=2024-03-01'
    intent_path=FOLDER/'cleanup-intent.json'
    current=access.request('GET',path,missing=True)
    if args.stage=='verify':
        saved=json.loads(intent_path.read_text())
        if current is not None: raise ValueError('Exact provisioner deletion not yet confirmed')
        result=dict(status='passed',removed=access.JOBS['grant'],recovery_definition=saved['definition'],
            read_grants_preserved=True,shared_identities_preserved=True,shared_secrets_preserved=True,
            source_rows_changed=0,emails_sent=0)
        result['protected_receipt']=retain(result,'inbox-provisioner-cleanup-completed')
        (FOLDER/'cleanup-acceptance.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k!='recovery_definition'}),flush=True);return
    if intent_path.exists(): raise ValueError('Cleanup already requested; verify instead of retrying')
    for stage in ('grant','test'):
        value=json.loads((FOLDER/(stage+'-acceptance.json')).read_text())
        if value['status']!='passed' or not value['protected_receipt']['protected_readback_verified']:
            raise ValueError('Protected grant and independent fresh-login acceptance required')
    definition=access.definition('grant');access.validate_job(current,definition)
    if current.get('tags',{}).get('purpose')!='specialist-inbox-acceptance': raise ValueError('Unregistered cleanup purpose')
    runs=access.request('GET',access.job_path('grant')+'/executions?api-version=2024-03-01').get('value',[])
    dispatch=json.loads((FOLDER/'grant-dispatch.json').read_text())
    if len(runs)!=1 or runs[0]['name']!=dispatch['execution'] or runs[0]['properties']['status']!='Succeeded':
        raise ValueError('One exact completed grant execution required')
    intent=dict(target=access.JOBS['grant'],definition=definition,execution=dispatch['execution'],
        scope='temporary manual provisioner only',grants_preserved=True,source_rows_changed=0,emails_sent=0)
    receipt=retain(intent,'inbox-provisioner-cleanup-intent')
    intent_path.write_text(json.dumps(dict(intent,protected_receipt=receipt),indent=2),encoding='utf-8')
    access.request('DELETE',path)
    print(json.dumps(dict(deletion_requested=access.JOBS['grant'],protected_intent=receipt,recovery_definition_retained=True)),flush=True)


if __name__=='__main__': main()
