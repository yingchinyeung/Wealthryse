"""Versioned corrected transient checks; preserves the rejected original request.

Only omit null template fields and empty secret-bound values, and gzip the exact
frozen check source. Never mutate persisted jobs or invoke processing/DB access.
"""
import argparse
import base64
import copy
import gzip
import hashlib
import json
from pathlib import Path
import re

import deploy_ms_presentation_worker_release as release
from deploy_metadata_monitor import request
from rehearse_ms_worker_image_rollback import restore_protected
from run_specialist_gateway_read_verification import retain


def require(ok,message):
    if not ok:raise ValueError(message)


def corrected_template(job,source):
    template={k:copy.deepcopy(v) for k,v in job['properties']['template'].items() if v is not None}
    require(len(template['containers'])==1,'Single installed worker required')
    c=template['containers'][0]
    for env in c.get('env',[]):
        if env.get('secretRef') and env.get('value')=='':env.pop('value')
    encoded=base64.b64encode(gzip.compress(source.encode(),mtime=0)).decode()
    c.update(command=['python'],args=['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'ms-installed-worker-identity-check','exec'))"])
    return template


def no_matching_execution(name,commands):
    target=release.path(name)+'/executions';path=target+release.API
    from urllib.parse import urlsplit
    for _ in range(20):
        data=request('GET',path)
        for run in data.get('value',[]):
            if any(c.get('args') in commands for c in run['properties'].get('template',{}).get('containers',[])):
                raise ValueError('Matching execution exists; inspect it rather than dispatch')
        link=data.get('nextLink')
        if not link:return
        parsed=urlsplit(link)
        require(parsed.scheme=='https' and parsed.netloc=='management.azure.com'
                and parsed.path.casefold()==target.casefold(),'Unexpected execution inventory pagination')
        path=parsed.path+'?'+parsed.query
    raise ValueError('Execution inventory exceeded bounded pages')


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=('prepare','dispatch','inspect'))
    p.add_argument('--folder',type=Path,required=True);p.add_argument('--job',choices=release.build.JOBS,required=True)
    args=p.parse_args();folder=args.folder.resolve();deployment=folder/'deployment-v1';name=args.job
    _,image,_=release.image_evidence(folder);plan=release.read(deployment,'plan')
    require(plan['image']==image and plan['controller_sha256']==release.build.sha(Path(release.__file__))
            and all(release.build.sha(folder/n)==h for n,h in plan['evidence_sha256'].items()),'Frozen release changed')
    archived,_=restore_protected(plan['protected_receipt'])
    require(archived=={k:v for k,v in plan.items() if k!='protected_receipt'},'Protected release plan differs')
    release.all_jobs(deployment,plan)
    job=release.checked_job(deployment,plan,name,{image})
    require(release.read(deployment,name+'-apply-qualified')['status']=='passed','Persistent image verification required')
    stage=deployment/'installed-check-v2'/name
    template=corrected_template(job,plan['check_source'])
    if args.stage=='prepare':
        require(not stage.exists(),'Preserve corrected check attempt')
        require(not (deployment/(name+'-check-qualified.json')).exists(),'Installed check already qualified')
        release.window()
        for n in release.build.JOBS:release.idle(n)
        commands=[template['containers'][0]['args']]
        old_path=deployment/(name+'-check-intent.json')
        old=None
        if old_path.exists():
            require(not (deployment/(name+'-check-dispatch.json')).exists(),'Prior dispatch acknowledged; inspect rather than replace')
            old=json.loads(old_path.read_bytes())
            recovered,_=restore_protected(old['protected_receipt'])
            require(recovered=={k:v for k,v in old.items() if k!='protected_receipt'}
                    and old['source_sha256']==hashlib.sha256(plan['check_source'].encode()).hexdigest(),
                    'Prior protected check intent differs')
            commands.append(old['template']['containers'][0]['args'])
        no_matching_execution(name,commands)
        record={'image':image,'job':name,'release_plan_sha256':release.build.sha(deployment/'plan.json'),
            'controller_sha256':release.build.sha(Path(__file__)),'template':template,
            'source_sha256':hashlib.sha256(plan['check_source'].encode()).hexdigest(),
            'prior_intent_sha256':release.build.sha(old_path) if old else None,
            'prior_result':'HTTP 400 observed by release controller; no matching execution and no dispatch acknowledgement' if old else None,
            'correction':'Omit null template fields and empty secret-bound values; gzip the unchanged frozen check source',
            'normal_processing_invoked':False,'database_connections':0}
        stage.mkdir(parents=True)
        release.build.save(stage,'intent',{**record,'protected_receipt':retain(record,folder.name+'-'+name+'-installed-check-v2')})
        print(json.dumps({'stage':'prepared-corrected-check','job':name,'source_unchanged':True}),flush=True)
        return
    intent=release.read(stage,'intent')
    require(intent['release_plan_sha256']==release.build.sha(deployment/'plan.json')
            and intent['controller_sha256']==release.build.sha(Path(__file__))
            and intent['template']==template,'Corrected check intent changed')
    recovered,_=restore_protected(intent['protected_receipt'])
    require(recovered=={k:v for k,v in intent.items() if k!='protected_receipt'},'Protected corrected intent differs')
    if args.stage=='dispatch':
        require(not (stage/'dispatch-intent.json').exists(),'Dispatch attempted; inspect rather than repeat')
        release.window()
        for n in release.build.JOBS:release.idle(n)
        no_matching_execution(name,[template['containers'][0]['args']])
        release.build.save(stage,'dispatch-intent',{'image':image,'source_sha256':intent['source_sha256']})
        run=request('POST',release.path(name)+'/start'+release.API,data=template)
        require(run.get('name'),'Unknown corrected dispatch; do not repeat')
        release.build.save(stage,'dispatch',{'execution':run['name'],'image':image})
        print(json.dumps({'job':name,'execution':run['name']}),flush=True)
        return
    ack=release.read(stage,'dispatch')
    require(re.fullmatch(re.escape(name)+'-[a-z0-9]{7}',ack['execution']),'Exact installed execution required')
    run=request('GET',release.path(name)+'/executions/'+ack['execution']+release.API)
    state=run['properties']['status'];print(json.dumps({'job':name,'execution':ack['execution'],'status':state}),flush=True)
    if state!='Succeeded':return
    actual=run['properties']['template']['containers'];expected=template['containers']
    require(len(actual)==len(expected)==1 and all(actual[0].get(k)==expected[0].get(k)
            for k in ('image','command','args','name')),'Actual installed source/check/image differs')
    proof={'status':'passed','image':image,'job':name,'execution':ack['execution'],
        'installed_files_verified':len(plan['binding']['installed_files_sha256']),
        'normal_entrypoints_invoked':0,'database_connections':0,'persistent_settings_preserved':True,
        'verification_method':'versioned_corrected_transient_request','corrected_intent_sha256':release.build.sha(stage/'intent.json'),
        'prior_rejected_request_preserved':bool(intent['prior_intent_sha256']),
        'source_sha256':intent['source_sha256']}
    protected=retain(proof,folder.name+'-'+name+'-installed-check-v2-passed')
    record={**proof,'protected_receipt':protected}
    release.build.save(stage,'qualified',record)
    # New worker qualification, not relabelling the original HTTP 400 attempt.
    release.build.save(deployment,name+'-check-qualified',record)
    print(json.dumps({'job':name,'installed_files_verified':proof['installed_files_verified'],'passed':True}),flush=True)


if __name__=='__main__':main()
