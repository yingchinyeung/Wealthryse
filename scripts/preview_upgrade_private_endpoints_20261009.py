"""ARM what-if gate. This does not deploy resources."""
import hashlib,json,subprocess,os
from pathlib import Path
from audit_azure_runtime import AZ,RG
from prepare_upgrade_private_endpoints_20261009 import OUT
from run_specialist_gateway_read_verification import retain
def validate_changes(changes,expected):
    wanted={s.lower() for s in expected};created=set()
    for change in changes:
        kind=change['changeType'];scope=change['resourceId'].lower()
        if kind=='Ignore':continue
        if kind!='Create' or scope not in wanted:raise ValueError('What-if includes an existing or unexpected resource change')
        created.add(scope)
    if created!=wanted:raise ValueError('What-if does not cover the exact additive plan')
def main():
    from rehearse_ms_worker_image_rollback import restore_protected
    plan=json.loads((OUT/'plan.json').read_bytes());remote,_=restore_protected(plan['protected_receipt'])
    if remote!={k:v for k,v in plan.items() if k!='protected_receipt'}:raise ValueError('Protected plan differs')
    if json.loads((OUT/'template.json').read_bytes())!=plan['template']:raise ValueError('Template drift')
    if (OUT/'what-if.json').exists():raise ValueError('Preserve previous preview')
    result=subprocess.run([AZ,'-m','azure.cli','deployment','group','what-if','--resource-group',RG,'--template-file',str(OUT/'template.json'),'--mode','Incremental','--no-pretty-print','-o','json'],capture_output=True,text=True,encoding='utf-8',timeout=240,env={**os.environ,'PYTHONUTF8':'1'})
    if result.returncode:
        with (OUT/'what-if-failure.log').open('x',encoding='utf-8') as f:f.write(result.stderr)
        raise ValueError('What-if failed; no deployment authorized')
    proof=json.loads(result.stdout)
    with (OUT/'what-if.json').open('x',encoding='utf-8') as f:json.dump(proof,f,indent=2)
    if proof.get('status')!='Succeeded':raise ValueError('What-if not successful')
    validate_changes(proof.get('changes',[]),plan['expected_created_resource_ids'])
    record={'passed':True,'resource_changes':'create-only exact 11 resources','plan_sha256':hashlib.sha256((OUT/'plan.json').read_bytes()).hexdigest(),'preview':proof,'live_changes':False}
    with (OUT/'what-if-qualified.json').open('x',encoding='utf-8') as f:json.dump({**record,'protected_receipt':retain(record,'upgrade-private-endpoints-what-if')},f,indent=2)
    print('Exact create-only what-if qualified; nothing deployed')
if __name__=='__main__':main()
