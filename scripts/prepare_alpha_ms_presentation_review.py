"""Read-only/private input preparation, not deployment or batch registration."""
import base64
import copy
import json
import sys
import align_alpha_ms_web_v14r13 as aligned
p=aligned.p
ROOT=aligned.ROOT
FOLDER=ROOT/'.work/alpha-ms-presentation-hkd-20261007'
PREFIX='review/alpha-world-ms/presentation-hkd-20261007/'

def save(name,data):
    FOLDER.mkdir(exist_ok=True)
    target=FOLDER/(name+'.json')
    if target.exists():raise ValueError('Original operation exists; reconcile rather than redispatch')
    receipt=p.retain(data,'ms-presentation-hkd-'+name+'-20261007')
    with target.open('x',encoding='utf-8') as h:json.dump({'record':data,'protected_receipt':receipt},h,indent=2)
def read(name):return json.loads((FOLDER/(name+'.json')).read_bytes())['record']
def dispatch():
    job=p.get_job(p.JOBS[0])
    if job['properties']['template']['containers'][0]['image']!=aligned.IMAGE:raise ValueError('Exact installed image required')
    runs=p.request('GET',p.job_path(p.JOBS[0])+'/executions?api-version=2024-03-01').get('value',[])
    if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Existing worker busy')
    source=(ROOT/'scripts/probe_alpha_ms_presentation_inputs.py').read_bytes()
    template={k:v for k,v in copy.deepcopy(job['properties']['template']).items() if v is not None}
    c=template['containers'][0];c['command']=['python']
    c['args']=['-c',"import base64;exec(compile(base64.b64decode('"+base64.b64encode(source).decode()+"'),'private-ms-presentation-inputs','exec'))"]
    c['env']=[e for e in c['env'] if e['name']!='MS_PRESENTATION_REVIEW_PREFIX']+[{'name':'MS_PRESENTATION_REVIEW_PREFIX','value':PREFIX}]
    save('input-intent',{'template':template,'source_sha256':p.sha(source),'before_job_invariant':p.invariant(job),'database_writes':0})
    result=p.request('POST',p.job_path(p.JOBS[0])+'/start?api-version=2024-03-01',data=template)
    save('input-dispatch',{'execution':result['name']});print(json.dumps({'execution':result['name']}))
def verify():
    name=read('input-dispatch')['execution']
    execution=p.request('GET',p.job_path(p.JOBS[0])+'/executions/'+name+'?api-version=2024-03-01')
    if execution['properties']['status']!='Succeeded':print(json.dumps({'status':execution['properties']['status']}));return
    actual=execution['properties']['template']['containers'][0];wanted=read('input-intent')['template']['containers'][0]
    if any(actual[k]!=wanted[k] for k in ('image','command','args')):raise ValueError('Executed identity differs')
    if p.invariant(p.get_job(p.JOBS[0]))!=read('input-intent')['before_job_invariant']:raise ValueError('Persistent configuration differs')
    service=p.storage();raw=service.get_blob_client('internal-reports',PREFIX+'input-proof.json').download_blob().readall();proof=json.loads(raw)
    if proof['status']!='passed' or proof['database_writes']!=0 or len(proof['items'])!=21:raise ValueError('Input proof failed')
    inputs=FOLDER/'inputs';inputs.mkdir(exist_ok=True)
    for row in proof['items']:
        data=service.get_blob_client('internal-reports',row['blob_name']).download_blob().readall()
        if p.sha(data)!=row['sha256']:raise ValueError('Input download hash differs')
        target=inputs/(row['period_end']+'.json')
        if target.exists():
            if target.read_bytes()!=data:raise ValueError('Existing private input differs; never overwrite')
        else:
            with target.open('xb') as h:h.write(data)
    old=json.loads((ROOT/'.work/alpha-ms-website-batch-20261007/preflight-r2-qualified.json').read_bytes())['record']['proof']
    template=next(a for a in old['assets'] if a['report_type']=='presentation' and a['asset_role']=='template')
    # Owner intake is not readable under the controller's container-scoped role.
    # Do not widen access for a layout test: production uses its existing worker
    # identity to validate that evidence. The immutable platform source is local.
    layout=ROOT/'output/pptx/U16088571_Monthly_Presentation_2026-08-31_Enhanced_v24.pptx'
    if p.sha(layout.read_bytes())!='4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191':raise ValueError('Platform layout hash differs')
    save('inputs-qualified',{'proof':proof,'template_sha256':template['sha256'],'strategy':old['presentation_strategy'],
        'identity_evidence':'Prior qualified read-only Azure metadata; intake bytes not fetched by controller',
        'production_identity_recheck_required':True,'platform_layout_sha256':p.sha(layout.read_bytes())})
    print(json.dumps({'status':'passed','months':21,'source_data_changed':False}))
if __name__=='__main__':{'dispatch':dispatch,'verify':verify}[sys.argv[1]]()
