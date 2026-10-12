"""Protect exact full preimages before the scoped atomic reference repair."""
import sys,json,hashlib
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from run_specialist_gateway_read_verification import retain
import control_platform_assurance_probe_20261009 as control
ROOT=control.ROOT/'.work/platform-upgrade-20261009-stage8'
control.OUT=ROOT/'report-repair-apply-v1'
proof=json.loads((ROOT/'report-repair-qualification-v1/result.json').read_bytes())
plan=json.loads(proof['records'][0]['Result'])
if proof['status']!='Succeeded' or not plan['passed'] or plan['qualified_rows']!=375 or not plan['clone_rollback_passed']:raise ValueError('Clone recovery qualification required')
source=control.ROOT/'scripts/apply_upgrade_report_repair_20261009.py'
class BoundSource:
    def read_text(self,encoding):return 'PLAN = '+repr(plan)+'\n'+source.read_text(encoding=encoding)
control.SOURCE=BoundSource()
if __name__=='__main__':
    if sys.argv[1]=='start':
        content=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()).get_blob_client('internal-reports',plan['private_blob']).download_blob().readall()
        if hashlib.sha256(content).hexdigest()!=plan['sha256']:raise ValueError('Qualified preimages differ')
        value=json.loads(content)
        from apply_upgrade_report_repair_20261009 import validate_package
        validate_package(value,'00000000-0000-0000-0000-000000000001')
        protected=ROOT/'report-repair-full-preimages.json'
        if protected.exists():
            saved=json.loads(protected.read_bytes())
            if saved['source_sha256']!=plan['sha256']:raise ValueError('Protected scope changed')
        else:
            receipt=retain(value,'upgrade-report-reference-full-preimages')
            with protected.open('x',encoding='utf-8') as f:json.dump({'source_sha256':plan['sha256'],'protected_receipt':receipt},f,indent=2)
        if control.OUT.exists():raise ValueError('Preserve previous mutation dispatch')
        control.idle(control.JOB)
        job=control.request('GET',control.PATH+control.API)
        image=control.request('GET',control.BASE+'Microsoft.App/containerApps/ibkrv2stg-web'+control.API)['properties']['template']['containers'][0]['image']
        expected=json.loads((control.ROOT/'.work/platform-upgrade-20261009-stage7/app-deployment-v2/verified.json').read_bytes())['image']
        if image!=expected:raise ValueError('Qualified website image drift')
        code=control.SOURCE.read_text(encoding='utf-8');template=control.corrected_template(job,code);container=template['containers'][0];container['image']=image
        keep={'DATABASE_URL','PORTFOLIO_TENANT_ID','AZURE_CLIENT_ID'}
        container['env']=[e for e in container.get('env',[]) if e['name'] in keep]+[{'name':'AUTHORIZATION_MODE','value':'database'}]
        control.OUT.mkdir()
        intent={'template':template,'job_invariant':control.settings_invariant(job),'source_sha256':hashlib.sha256(code.encode()).hexdigest(),
                'database_writes':375,'mutation':'output_reference only on qualified awaiting-approval generation rows','full_preimage_receipt':json.loads(protected.read_bytes()),'released_files_or_approvals_changed':False,'model_calls':0}
        intent['protected_receipt']=retain(intent,'upgrade-report-reference-write-intent')
        control.save('intent',intent)
        ack=control.request('POST',control.PATH+'/start'+control.API,data=template)
        control.save('dispatch',{'execution':ack['name']})
        print(json.dumps({'execution':ack['name'],'qualified_reference_repairs':375}))
    else:control.main(sys.argv[1])
