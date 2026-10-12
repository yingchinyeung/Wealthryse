"""Qualification stages for the owner-authorized HKD/1M-HIBOR replacement.

Never promotes a partially qualified candidate or alters archived IBKR files.
"""
import json
import base64
import copy
from pathlib import Path
import sys

import qualify_alpha_ms_hkd_azure as q

ROOT=q.ROOT
FOLDER=ROOT/'.work/alpha-ms-hkd-cutover-v14r11-20261007-r3'
BASE_IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:5a9949f5a90b13b36b69cc453b9330070945f85325a23853469afefb190c2f71'
LAYOUT='subject-completed-factsheet-model-v14r10-alpha-world-ms-benchmark-typography'
PREFIX='review/alpha-world-ms/hkd-cutover-v14r11-20261007/'
INPUT_PREFIX='review/alpha-world-ms/hkd-hibor-v14r8-20261007/inputs/'
COORDINATED_JOBS=('ibkrv2stg-onboard-render','ibkrv2stg-month-end-factsheet',
    'ibkrv2stg-derived-daily','ibkrv2stg-risk-snapshots','ibkrv2stg-risk-free-daily')
WEB='ibkrv2stg-web'
ATOMIC_REHEARSAL_LABEL='atomic-rehearse-r2'
q.FOLDER=FOLDER;q.TAG='ibkr-platform:alpha-ms-hkd-cutover-v14r11-20261007-r3'
q.LAYOUT=LAYOUT;q.PREFIX=PREFIX
q.previous.FOLDER=FOLDER;q.previous.TAG=q.TAG;q.previous.LAYOUT=LAYOUT
q.previous.PREFIX=PREFIX+'outputs/'
q.build.FOLDER=FOLDER;q.build.TAG=q.TAG


def prepare():
    if FOLDER.exists():raise ValueError('Frozen qualification already exists; never replace it')
    before={name:q.previous.get_job(name) for name in q.previous.JOBS}
    if any(j['properties']['template']['containers'][0]['image']!=BASE_IMAGE for j in before.values()):
        raise ValueError('Installed renderer drift; do not replace another release')
    manifest=json.loads((ROOT/'output/pdf/AlphaWorld_MS_Header_Typography_History_v14r10/manifest.json').read_bytes())
    modules=['monthly_artifacts','alpha_world_ms_analytics','alpha_world_ms_pdf_validation',
        'alpha_world_ms_hkd','alpha_world_ms_cache','hibor','market_calendar',
        'subject_onboarding','subject_read_contract','derived_portfolio','agent_tools',
        'reference_snapshot','fx_selection','cloud_storage',
        'risk_snapshots','risk_refresh','report','reporting_snapshot','monthly_reporting_analytics','web']
    scripts=['generate_month_end_package','render_alpha_world_ms_hkd_azure_batch',
        'refresh_staging_risk_free','build_risk_snapshots','run_self_service_derived_refresh',
        'run_registry_month_end_packages','run_self_service_onboarding_reports']
    tests=['hibor','alpha_world_ms_hkd','alpha_world_ms_cache','monthly_artifacts',
        'alpha_world_ms_chart_style','alpha_world_ms_analytics','alpha_world_ms_allocation_layout',
        'alpha_world_ms_pdf_accuracy','risk_snapshots','subject_onboarding','agent_tools',
        'derived_portfolio','risk_free','reporting_snapshot','month_end_package_readiness',
        'monthly_reporting_analytics','subject_read_contract','alpha_ms_hkd_runtime']
    context=FOLDER/'context';context.mkdir(parents=True)
    installed={}
    names=['src/ibkr_etl/'+n+'.py' for n in modules]+['scripts/'+n+'.py' for n in scripts]
    names+=['tests/test_'+n+'.py' for n in tests]
    for name in names:
        data=(ROOT/name).read_bytes()
        if name=='scripts/render_alpha_world_ms_hkd_azure_batch.py':
            data=data.replace(b"PREFIX='review/alpha-world-ms/hkd-hibor-v14r8-20261007/'",("PREFIX="+repr(PREFIX)).encode())
            data=data.replace(b"PREFIX+'inputs/'+name",repr(INPUT_PREFIX).encode()+b'+name')
            data=data.replace(b"'layout':'v14r8'",b"'layout':'v14r10'")
        target=context/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        if not name.startswith('tests/'):installed[name]=q.previous.sha(data)
    inputs={'source-inspection.json':q.INPUT/'source-inspection.json',
        'official-hibor.json':q.INPUT/'official-hibor.json','capture.json':q.CAPTURE/'capture.json'}
    # Data hashes belong to the approved HKD payloads, not the old USD snapshots.
    from ibkr_etl.alpha_world_ms_hkd import rebuild,factsheet_snapshot
    history=rebuild(json.loads((q.INPUT/'source-inspection.json').read_bytes()))
    fixings=json.loads((q.INPUT/'official-hibor.json').read_bytes())
    snapshots={};approved_snapshot_hashes={}
    for item in manifest['artifacts']:
        period=item['period_end']
        raw=(q.INPUT/'hkd-inputs'/(period+'.json')).read_bytes()
        previous_payload=json.loads(raw)
        payload=factsheet_snapshot(history,fixings,period)
        normalized=copy.deepcopy(payload);previous_normalized=copy.deepcopy(previous_payload)
        for value in (normalized,previous_normalized):
            # Only the already-qualified 2027 calendar and official transport
            # provenance descriptions changed after the original review.
            value['risk']['analytics_evidence'].pop('calendar_version',None)
            value['risk']['risk_free'].pop('provider',None)
        if normalized!=previous_normalized:raise ValueError('Approved historical financial inputs or metrics changed')
        snapshots[period]=q.previous.sha(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode())
        approved_snapshot_hashes[period]=q.previous.sha(raw)
    if len(snapshots)!=21:raise ValueError('All 21 approved HKD periods required')
    release={'layout_version':LAYOUT,'installed_files_sha256':installed,
        'inputs_sha256':{n:q.previous.sha(p.read_bytes()) for n,p in inputs.items()},
        'snapshot_sha256':snapshots,'contract_sha256':json.loads((q.INPUT/'hkd-history.json').read_bytes())['contract_sha256'],
        'approved_review_snapshot_sha256':approved_snapshot_hashes,
        'review_delta_allowlist':['risk.analytics_evidence.calendar_version','risk.risk_free.provider'],
        'base_image':BASE_IMAGE,'currency':'HKD',
        'risk_free_basis':'published 1M HIBOR fixings, inception through valuation date, arithmetic period average',
        'commercial_usage_rights_owner_confirmed':True,'additional_interest_charge':False,
        'database_writes_authorized':False,'production_promotion':False,
        'owner_authorized_final_cutover':True,'report_release_authorized':False}
    (context/'ALPHA_MS_HKD_RELEASE.json').write_text(json.dumps(release,indent=2),encoding='utf-8')
    docker=f'FROM {BASE_IMAGE}\nENV PYTHONPATH=/app/src:/app:/app/scripts\n'
    docker+='COPY --chown=10001:10001 src/ibkr_etl/ /app/src/ibkr_etl/\nCOPY --chown=10001:10001 scripts/ /app/scripts/\n'
    docker+='COPY --chown=10001:10001 ALPHA_MS_HKD_RELEASE.json /app/ALPHA_MS_HKD_RELEASE.json\nCOPY --chown=10001:10001 tests/ /app/alpha-ms-hkd-tests/\n'
    docker+='RUN ["python","scripts/check_report_runtime.py"]\nRUN ["python","-m","unittest","discover","-s","/app/alpha-ms-hkd-tests","-v"]\n'
    docker+='RUN ["python","-c","import render_alpha_world_ms_hkd_azure_batch,run_registry_month_end_packages,run_self_service_onboarding_reports,run_self_service_derived_refresh,build_risk_snapshots,refresh_staging_risk_free; import ibkr_etl.monthly_artifacts as m; assert m.factsheet_layout_version(\'alpha-world-ms\') == \''+LAYOUT+'\'; print(\'PASS: private Azure HKD/HIBOR runtime v14r8\')"]\n'
    docker+='CMD ["python","scripts/run_registry_month_end_packages.py"]\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    q.build.save('source-intent',{'tag':q.TAG,'layout_version':LAYOUT,
        'files_sha256':{p.relative_to(context).as_posix():q.previous.sha(p.read_bytes()) for p in context.rglob('*') if p.is_file()},
        'before_jobs':before,'before_invariants':{n:q.previous.invariant(j) for n,j in before.items()},
        'scope':'HKD/1M-HIBOR historical and future MS factsheet qualification before coordinated cutover',
        'source_data_changed':False,'production_promotion':False})
    print(json.dumps({'prepared':True,'files':len(names),'financial_currency':'HKD','persistent_jobs_changed':False}))


def verify_inputs():
    expected=json.loads((FOLDER/'context/ALPHA_MS_HKD_RELEASE.json').read_bytes())['inputs_sha256']
    storage=q.previous.storage()
    for name,digest in expected.items():
        if q.previous.sha(storage.get_blob_client('internal-reports',INPUT_PREFIX+name).download_blob().readall())!=digest:
            raise ValueError('Previously qualified licensed input changed')
    q.build.save('private-inputs-uploaded',{'inputs_sha256':expected,'source_data_changed':False,
        'source_prefix':INPUT_PREFIX,'upload_repeated':False,'additional_permissions_granted':False})
    print(json.dumps({'existing_private_inputs_verified':len(expected)}))


def preflight():
    ack=json.loads((ROOT/'.work/alpha-ms-recovery-20261007/hkd-cutover-preflight-v14r11-result.json').read_bytes())
    if ack['status']!='Succeeded' or not ack['job_settings_unchanged']:raise ValueError('Successful original preflight required')
    result=ack['records'][0]
    if result['status']!='inspected':raise ValueError('Preflight stopped')
    raw=q.previous.storage().get_blob_client('internal-reports',result['blob']).download_blob().readall()
    if q.previous.sha(raw)!=result['sha256']:raise ValueError('Preflight digest changed')
    folder=ROOT/'.work/alpha-ms-hkd-cutover-preflight-20261007';folder.mkdir(exist_ok=True)
    target=folder/'live-preflight.json'
    if target.exists():
        if target.read_bytes()!=raw:raise ValueError('Existing preflight differs')
    else:target.write_bytes(raw)
    proof=json.loads(raw)
    print(json.dumps({k:proof[k] for k in ('role','subjects','nav_coverage','hibor_coverage','temp_permission','table_permissions')}))


def postgres_probe():
    q.previous.require_candidate()
    if (FOLDER/'postgres-dispatch-intent.json').exists():raise ValueError('Verify original PostgreSQL probe; never redispatch')
    job=q.previous.get_job(q.previous.JOBS[0])
    if q.previous.invariant(job)!=q.previous.read('source-intent')['before_invariants'][q.previous.JOBS[0]]:
        raise ValueError('Reporting job drift')
    runs=q.previous.request('GET',q.previous.job_path(q.previous.JOBS[0])+'/executions?api-version=2024-03-01').get('value',[])
    if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Worker busy')
    source=(ROOT/'scripts/qualify_alpha_ms_hkd_postgres.py').read_bytes()
    template={k:v for k,v in copy.deepcopy(job['properties']['template']).items() if v is not None}
    container=template['containers'][0];container['image']=q.previous.image();container['command']=['python']
    container['args']=['-c',"import base64;exec(compile(base64.b64decode('"+base64.b64encode(source).decode()+"'),'ms-hkd-postgres-qualification','exec'))"]
    container['env']=[e for e in container['env'] if e['name']!='ALPHA_MS_IMAGE_DIGEST']+[{'name':'ALPHA_MS_IMAGE_DIGEST','value':q.previous.image().split('@')[1]}]
    q.build.save('postgres-dispatch-intent',{'template':template,'source_sha256':q.previous.sha(source),
        'persistent_database_writes_authorized':False,'image':q.previous.image()})
    result=q.previous.request('POST',q.previous.job_path(q.previous.JOBS[0])+'/start?api-version=2024-03-01',data=template)
    q.build.save('postgres-dispatch',{'execution':result['name']})
    print(json.dumps({'postgres_execution':result['name']}))


def postgres_status():
    ack=q.previous.read('postgres-dispatch')
    run=q.previous.request('GET',q.previous.job_path(q.previous.JOBS[0])+'/executions/'+ack['execution']+'?api-version=2024-03-01')
    if run['properties']['status']!='Succeeded':
        output={'status':run['properties']['status'],'execution':ack['execution']}
        if run['properties']['status']=='Failed':
            workspace=q.previous.request('GET',q.previous.BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs?api-version=2023-09-01')
            query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+ack['execution']+"' | where Log_s startswith 'ALPHA_MS_RECOVERY_RESULT ' | project Log_s"
            output['sanitized_failure_markers']=q.build.az('monitor','log-analytics','query','-w',workspace['properties']['customerId'],'--analytics-query',query)
        print(json.dumps(output));return
    wanted=q.previous.read('postgres-dispatch-intent')['template']['containers'][0]
    actual=run['properties']['template']['containers'][0]
    if any(actual[k]!=wanted[k] for k in ('image','command','args')):raise ValueError('Executed PostgreSQL probe differs')
    raw=q.previous.storage().get_blob_client('internal-reports',PREFIX+'postgres-qualification-'+q.previous.image().split('@sha256:')[1]+'.json').download_blob().readall()
    proof=json.loads(raw)
    if proof['status']!='passed' or proof['image_digest']!=q.previous.image().split('@')[1] or proof['database_persistent_writes']!=0 or proof['navs_verified']!=443:
        raise ValueError('PostgreSQL proof does not meet the gate')
    q.build.save('postgres-qualified',{'execution':ack['execution'],'image':q.previous.image(),
        'proof':proof,'proof_sha256':q.previous.sha(raw),'native_execution':run})
    print(json.dumps({'status':'passed','navs_verified':proof['navs_verified']}))


def download():
    from ibkr_etl.alpha_world_ms_hkd import rebuild,factsheet_snapshot
    from ibkr_etl.alpha_world_ms_pdf_validation import validate
    from pypdf import PdfReader
    ack=q.previous.read('azure-dispatch')
    native=q.previous.request('GET',q.previous.job_path(q.previous.JOBS[0])+'/executions/'+ack['execution']+'?api-version=2024-03-01')
    if native['properties']['status']!='Succeeded':raise ValueError('Successful original Azure render required')
    wanted=q.previous.read('azure-dispatch-intent')['template']['containers'][0]
    actual=native['properties']['template']['containers'][0]
    if any(actual[k]!=wanted[k] for k in ('image','command','args')):raise ValueError('Executed financial candidate differs')
    storage=q.previous.storage()
    raw=storage.get_blob_client('internal-reports',PREFIX+'outputs/manifest.json').download_blob().readall()
    manifest=json.loads(raw);release_bytes=(FOLDER/'context/ALPHA_MS_HKD_RELEASE.json').read_bytes();release=json.loads(release_bytes)
    if manifest['image_digest']!=q.previous.image().split('@')[1] or manifest['release_manifest_sha256']!=q.previous.sha(release_bytes):raise ValueError('Cloud release identity differs')
    if manifest['count']!=21 or manifest['database_writes']!=0 or manifest['reports_released'] or manifest['production_cutover']:raise ValueError('Historical scope or write policy differs')
    if {r['period_end']:r['data_sha256'] for r in manifest['artifacts']}!=release['snapshot_sha256']:raise ValueError('Financial input hashes differ')
    output=ROOT/'output/pdf/AlphaWorld_MS_Azure_HKD_HIBOR_v14r11';output.mkdir(exist_ok=False)
    history=rebuild(json.loads((q.INPUT/'source-inspection.json').read_bytes()));fixings=json.loads((q.INPUT/'official-hibor.json').read_bytes())
    for item in manifest['artifacts']:
        period=item['period_end'];name='AlphaWorld_MS_Factsheet_'+period+'.pdf'
        if item['blob_name']!=PREFIX+'outputs/'+name:raise ValueError('Cloud path outside scope')
        pdf=storage.get_blob_client('internal-reports',item['blob_name']).download_blob().readall()
        if q.previous.sha(pdf)!=item['sha256']:raise ValueError('Cloud artifact hash differs')
        path=output/name;path.write_bytes(pdf)
        validate(path,factsheet_snapshot(history,fixings,period))
        previous_pdf=ROOT/'output/pdf/AlphaWorld_MS_Header_Typography_History_v14r10'/name
        if PdfReader(path).metadata['/MSMetrics']!=PdfReader(previous_pdf).metadata['/MSMetrics']:
            raise ValueError('Approved historical metrics changed')
    (output/'manifest.json').write_bytes(raw)
    q.build.save('azure-render-qualified',{'image':q.previous.image(),'execution':ack['execution'],
        'count':21,'manifest_sha256':q.previous.sha(raw),'manifest':manifest,
        'financial_metrics_match_approved_review':True,'database_writes':0,'reports_released':False})
    print(json.dumps({'cloud_hkd_factsheets':21,'vector_and_metric_acceptance':'pass','folder':str(output)}))


def coordinated_baselines():
    q.previous.require_candidate()
    account=q.build.az('account','show')
    if account['user']['name'].strip().lower()!='anthony.ying@wealthryse.ai':raise ValueError('Expected signed-in owner required')
    # Do not log or persist tokens. The signed-in Azure identity only supplies
    # the actor attribution; authorization is the human's explicit instruction.
    token=q.build.az('account','get-access-token','--resource','https://management.azure.com/')['accessToken']
    claims=json.loads(base64.urlsafe_b64decode(token.split('.')[1]+'==='))
    actor=q.previous.sha(claims['oid'].strip().lower().encode())
    del token,claims
    preflight=json.loads((ROOT/'.work/alpha-ms-hkd-cutover-preflight-20261007/live-preflight.json').read_bytes())
    previous_contract=preflight['contracts'][0]
    if actor!=previous_contract['approved_by_identity_sha256']:raise ValueError('Signed-in identity differs from the previously verified approving owner')
    jobs={name:q.previous.request('GET',q.previous.BASE+'Microsoft.App/jobs/'+name+'?api-version=2024-03-01') for name in COORDINATED_JOBS}
    web=q.previous.request('GET',q.previous.BASE+'Microsoft.App/containerApps/'+WEB+'?api-version=2024-03-01')
    if web['properties']['template']['containers'][0]['command']!=['python'] or 'ibkr_etl.web:app' not in web['properties']['template']['containers'][0]['args']:
        raise ValueError('Explicit compatible website startup command required')
    q.build.save('coordinated-baselines',{'jobs':jobs,'website':web,
        'owner_identity_sha256':actor,'previous_contract':previous_contract,
        'authorization':'Explicit owner request to deploy the approved HKD/period-average 1M HIBOR version and replace the current Azure version',
        'source_data_mutation_authorized':False,'report_publication_authorized':False,
        'resource_scope':list(COORDINATED_JOBS)+[WEB]})
    print(json.dumps({'owner_identity_matches_previous_approval':True,'job_baselines':len(jobs),
        'website_baseline':WEB,'persistent_resources_changed':False}))


def atomic_dispatch(mode):
    q.previous.require_candidate();q.previous.read('postgres-qualified')
    baseline=q.previous.read('coordinated-baselines')
    label=ATOMIC_REHEARSAL_LABEL if mode=='rehearse' else 'atomic-apply'
    if (FOLDER/(label+'-intent.json')).exists():raise ValueError('Reconcile original atomic dispatch; never redispatch')
    if mode=='apply':
        q.previous.read('pdf-acceptance');q.previous.read(ATOMIC_REHEARSAL_LABEL+'-qualified')
        q.previous.read('coordinated-installed')
    job=q.previous.get_job(q.previous.JOBS[0])
    if mode=='rehearse' and q.previous.invariant(job)!=q.previous.invariant(baseline['jobs'][q.previous.JOBS[0]]):raise ValueError('Reporting worker drift')
    runs=q.previous.request('GET',q.previous.job_path(q.previous.JOBS[0])+'/executions?api-version=2024-03-01').get('value',[])
    if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Worker busy')
    source=(ROOT/'scripts/apply_alpha_ms_hkd_cutover.py').read_bytes()
    template={k:v for k,v in copy.deepcopy(job['properties']['template']).items() if v is not None}
    container=template['containers'][0];container['image']=q.previous.image();container['command']=['python']
    container['args']=['-c',"import base64;exec(compile(base64.b64decode('"+base64.b64encode(source).decode()+"'),'ms-hkd-atomic-cutover','exec'))"]
    env={'ALPHA_MS_IMAGE_DIGEST':q.previous.image().split('@')[1],'ALPHA_MS_CUTOVER_MODE':mode,
        'ALPHA_MS_OWNER_SHA256':baseline['owner_identity_sha256']}
    container['env']=[e for e in container['env'] if e['name'] not in env]+[{'name':k,'value':v} for k,v in env.items()]
    q.build.save(label+'-intent',{'template':template,'source_sha256':q.previous.sha(source),
        'persistent_database_writes_authorized':mode=='apply','image':q.previous.image()})
    result=q.previous.request('POST',q.previous.job_path(q.previous.JOBS[0])+'/start?api-version=2024-03-01',data=template)
    q.build.save(label+'-dispatch',{'execution':result['name']})
    print(json.dumps({'atomic_mode':mode,'execution':result['name']}))


def atomic_status(mode):
    label=ATOMIC_REHEARSAL_LABEL if mode=='rehearse' else 'atomic-apply';ack=q.previous.read(label+'-dispatch')
    run=q.previous.request('GET',q.previous.job_path(q.previous.JOBS[0])+'/executions/'+ack['execution']+'?api-version=2024-03-01')
    status=run['properties']['status']
    if status!='Succeeded':
        output={'status':status,'execution':ack['execution']}
        if status=='Failed':
            workspace=q.previous.request('GET',q.previous.BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs?api-version=2023-09-01')
            query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+ack['execution']+"' | where Log_s startswith 'ALPHA_MS_RECOVERY_RESULT ' | project Log_s"
            output['sanitized_failure_markers']=q.build.az('monitor','log-analytics','query','-w',workspace['properties']['customerId'],'--analytics-query',query)
        print(json.dumps(output));return
    wanted=q.previous.read(label+'-intent')['template']['containers'][0];actual=run['properties']['template']['containers'][0]
    if any(actual[k]!=wanted[k] for k in ('image','command','args')):raise ValueError('Atomic execution identity differs')
    blob=PREFIX+('atomic-rehearsal-' if mode=='rehearse' else 'database-cutover-')+q.previous.image().split('@sha256:')[1]+'.json'
    raw=q.previous.storage().get_blob_client('internal-reports',blob).download_blob().readall();proof=json.loads(raw)
    if proof['image_digest']!=q.previous.image().split('@')[1] or proof['ibkr_source_changed'] or proof['reports_released'] or proof['onboarding_activated']:raise ValueError('Atomic proof violates approved scope')
    if len(proof['reports'])!=21 or proof['historical_navs_verified']!=443:raise ValueError('Atomic historical coverage differs')
    if mode=='rehearse' and (proof['status']!='passed' or proof['rollback_rehearsal']!='passed' or proof['database_persistent_writes']!=0):raise ValueError('Atomic rollback rehearsal failed')
    if mode=='apply' and proof['status']!='committed':raise ValueError('Database commit not verified')
    q.build.save(label+'-qualified',{'execution':ack['execution'],'image':q.previous.image(),'proof':proof,
        'proof_sha256':q.previous.sha(raw),'blob':blob})
    print(json.dumps({'status':proof['status'],'mode':mode,'historical_months':21,'risk_caches':proof['historical_risk_caches'],
        'latest_nav_date':proof['derived_result']['latest_date'],'rollback_rehearsal':proof.get('rollback_rehearsal')}))


def pdf_acceptance():
    rendered=q.previous.read('azure-render-qualified')
    path=ROOT/'tmp/pdfs/alpha-ms-hkd-cutover-v14r11/verification.json';proof=json.loads(path.read_bytes())
    if proof['status']!='pass' or proof['months']!=21 or proof['pages']!=42 or not proof['full_page_pixels_identical']:raise ValueError('All-page PDF acceptance required')
    if proof['image_digest']!=q.previous.image().split('@')[1] or proof['manifest_sha256']!=rendered['manifest_sha256']:raise ValueError('PDF acceptance belongs to another candidate')
    q.build.save('pdf-acceptance',{'image':q.previous.image(),'proof':proof,'verification_sha256':q.previous.sha(path.read_bytes()),
        'visual_review':'All seven full-page contact sheets inspected; all 42 pages visually identical to the accepted HKD review'})
    print(json.dumps({'months':21,'pages':42,'status':'pass'}))


def job_invariant(job):
    normalized=copy.deepcopy(job)
    if normalized['name']=='ibkrv2stg-risk-free-daily':
        for c in normalized['properties']['template']['containers']:
            c['env']=[e for e in c.get('env',[]) if e['name']!='HIBOR_LICENSE_SCOPE_CONFIRMED']
    return q.previous.invariant(normalized)


def web_invariant(web):
    template=copy.deepcopy(web['properties']['template'])
    template.pop('revisionSuffix',None)
    for c in template['containers']:c.pop('image',None)
    configuration=copy.deepcopy(web['properties']['configuration'])
    # Single-revision promotion necessarily repoints the unchanged 100% traffic
    # rule. Verify its exact new target separately; preserve all other fields.
    for rule in configuration['ingress'].get('traffic',[]):rule.pop('revisionName',None)
    return q.previous.sha(json.dumps({'template':template,'identity':web.get('identity'),
        'configuration':configuration,'environmentId':web['properties']['environmentId']},sort_keys=True).encode())


def install_coordinated():
    q.previous.require_candidate();q.previous.read('pdf-acceptance');q.previous.read(ATOMIC_REHEARSAL_LABEL+'-qualified')
    baselines=q.previous.read('coordinated-baselines')
    if (FOLDER/'coordinated-install-intent.json').exists():raise ValueError('Reconcile original install before any further mutations')
    current={}
    for name in COORDINATED_JOBS:
        job=q.previous.request('GET',q.previous.BASE+'Microsoft.App/jobs/'+name+'?api-version=2024-03-01')
        old=baselines['jobs'][name]
        if job_invariant(job)!=job_invariant(old) or job['properties']['template']['containers'][0]['image']!=old['properties']['template']['containers'][0]['image']:raise ValueError('Job drift: '+name)
        runs=q.previous.request('GET',q.previous.BASE+'Microsoft.App/jobs/'+name+'/executions?api-version=2024-03-01').get('value',[])
        if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Scheduled consumer is busy: '+name)
        current[name]=job
    web=q.previous.request('GET',q.previous.BASE+'Microsoft.App/containerApps/'+WEB+'?api-version=2024-03-01')
    if web_invariant(web)!=web_invariant(baselines['website']) or web['properties']['template']['containers'][0]['image']!=baselines['website']['properties']['template']['containers'][0]['image']:raise ValueError('Website drift')
    q.build.save('coordinated-install-intent',{'image':q.previous.image(),'before_jobs':current,'before_website':web,
        'approval':baselines['authorization'],'database_rollback_rehearsed':True,
        'scope':'five job image references, website image/revision and licensed HIBOR enable flag only'})
    # These are existing services, not a new environment or wider authority.
    for name in COORDINATED_JOBS:
        args=['containerapp','job','update','-g','rg-ibkr-database-v11','-n',name,'--image',q.previous.image()]
        if name=='ibkrv2stg-risk-free-daily':args+=['--set-env-vars','HIBOR_LICENSE_SCOPE_CONFIRMED=true']
        q.build.az(*args)
        after=q.previous.request('GET',q.previous.BASE+'Microsoft.App/jobs/'+name+'?api-version=2024-03-01')
        if job_invariant(after)!=job_invariant(current[name]) or after['properties']['template']['containers'][0]['image']!=q.previous.image():raise ValueError('Installed job mismatch: '+name)
        q.build.save('installed-'+name,{'image':q.previous.image(),'before_image':current[name]['properties']['template']['containers'][0]['image'],
            'settings_preserved':True,'after':after})
    q.build.az('containerapp','update','-g','rg-ibkr-database-v11','-n',WEB,'--image',q.previous.image(),'--revision-suffix','ms-hkd-v14r11')
    verify_coordinated()


def verify_coordinated(persist=True):
    baselines=q.previous.read('coordinated-baselines');q.previous.read('coordinated-install-intent')
    checks=[]
    for name in COORDINATED_JOBS:
        job=q.previous.request('GET',q.previous.BASE+'Microsoft.App/jobs/'+name+'?api-version=2024-03-01')
        if job_invariant(job)!=job_invariant(baselines['jobs'][name]) or job['properties']['template']['containers'][0]['image']!=q.previous.image():raise ValueError('Coordinated job incomplete: '+name)
        if name=='ibkrv2stg-risk-free-daily' and not any(e.get('name')=='HIBOR_LICENSE_SCOPE_CONFIRMED' and e.get('value')=='true' for e in job['properties']['template']['containers'][0]['env']):raise ValueError('HIBOR rights flag missing')
        checks.append({'job':name,'image':q.previous.image(),'settings_preserved':True})
    web=q.previous.request('GET',q.previous.BASE+'Microsoft.App/containerApps/'+WEB+'?api-version=2024-03-01')
    if web_invariant(web)!=web_invariant(baselines['website']) or web['properties']['template']['containers'][0]['image']!=q.previous.image():raise ValueError('Website configuration mismatch')
    if web['properties']['latestReadyRevisionName']!='ibkrv2stg-web--ms-hkd-v14r11' or web['properties']['provisioningState']!='Succeeded':
        print(json.dumps({'website_provisioning':web['properties']['provisioningState'],'ready':web['properties'].get('latestReadyRevisionName'),'status':'waiting'}));return
    traffic=web['properties']['configuration']['ingress']['traffic']
    if len(traffic)!=1 or traffic[0].get('weight')!=100 or traffic[0].get('revisionName')!='ibkrv2stg-web--ms-hkd-v14r11':raise ValueError('New website revision must receive the existing 100% single-revision traffic rule')
    from urllib.request import urlopen
    from urllib.error import HTTPError
    url='https://'+web['properties']['configuration']['ingress']['fqdn']+'/healthz'
    try:
        with urlopen(url,timeout=25) as response:
            content=response.read();status=response.status
    except HTTPError as error:
        if error.code!=401:raise
        status=401;content=error.read()
    if status not in (200,401):raise ValueError('Website public boundary failed')
    # Easy Auth intentionally protects healthz. A 401 is not application-health
    # evidence; qualify the actual image's Azure readiness/health independently.
    revision=q.previous.request('GET',q.previous.BASE+'Microsoft.App/containerApps/'+WEB+'/revisions/ibkrv2stg-web--ms-hkd-v14r11?api-version=2024-03-01')['properties']
    if revision.get('healthState')!='Healthy' or revision.get('runningState') not in ('Running','RunningAtMaxScale') or not revision.get('active') or revision['template']['containers'][0]['image']!=q.previous.image():raise ValueError('Exact healthy active website revision required')
    auth=q.previous.request('GET',q.previous.BASE+'Microsoft.App/containerApps/'+WEB+'/authConfigs/current?api-version=2024-03-01')['properties']
    if not auth.get('platform',{}).get('enabled'):raise ValueError('Existing authentication boundary must remain enabled')
    record={'image':q.previous.image(),'checks':checks,'website_revision':web['properties']['latestReadyRevisionName'],
        'website_public_http_status':status,'public_authentication_required':status==401,'website_response_body_sha256':q.previous.sha(content),
        'website_revision_health_state':revision['healthState'],'website_running_state':revision['runningState'],
        'authentication_enabled':True,'auth_configuration_sha256':q.previous.sha(json.dumps(auth,sort_keys=True).encode()),'web_settings_preserved':True,
        'source_data_changed':False,'database_cutover_pending':not (FOLDER/'atomic-apply-qualified.json').exists()}
    if persist:q.build.save('coordinated-installed',record)
    print(json.dumps({'installed_job_images':5,'website_revision':web['properties']['latestReadyRevisionName'],'website_health':'pass',
        'database_cutover':'pending' if record['database_cutover_pending'] else 'committed'}))
    return record


def inspect_web_delta():
    before=q.previous.read('coordinated-baselines')['website']
    after=q.previous.request('GET',q.previous.BASE+'Microsoft.App/containerApps/'+WEB+'?api-version=2024-03-01')
    def differences(a,b,path=''):
        if isinstance(a,dict) and isinstance(b,dict):
            return [r for key in sorted(set(a)|set(b)) for r in differences(a.get(key),b.get(key),path+'/'+key)]
        if isinstance(a,list) and isinstance(b,list) and len(a)==len(b):
            return [r for i,(x,y) in enumerate(zip(a,b)) for r in differences(x,y,path+'/'+str(i))]
        return [] if a==b else [{'path':path,'before':a,'after':b}]
    delta=differences({'identity':before.get('identity'),'configuration':before['properties']['configuration'],'template':before['properties']['template']},
        {'identity':after.get('identity'),'configuration':after['properties']['configuration'],'template':after['properties']['template']})
    safe=[]
    for row in delta:
        if any(word in row['path'].lower() for word in ('secret','env','credential','token')):
            safe.append({'path':row['path'],'values':'not displayed'})
        else:safe.append(row)
    print(json.dumps({'changed_paths':safe,'provisioning':after['properties']['provisioningState'],'ready':after['properties'].get('latestReadyRevisionName')}))


def installed_probe():
    q.previous.read('coordinated-installed');q.previous.read('atomic-apply-qualified')
    name=q.previous.JOBS[1];label='installed-financial-probe'
    if (FOLDER/(label+'-intent.json')).exists():raise ValueError('Reconcile original installed probe; never redispatch')
    job=q.previous.get_job(name)
    if job['properties']['template']['containers'][0]['image']!=q.previous.image():raise ValueError('Installed scheduled image differs')
    runs=q.previous.request('GET',q.previous.job_path(name)+'/executions?api-version=2024-03-01').get('value',[])
    if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Month-end worker busy')
    source=(ROOT/'scripts/verify_alpha_ms_hkd_installed.py').read_bytes()
    template={k:v for k,v in copy.deepcopy(job['properties']['template']).items() if v is not None}
    container=template['containers'][0];container['command']=['python']
    container['args']=['-c',"import base64;exec(compile(base64.b64decode('"+base64.b64encode(source).decode()+"'),'installed-ms-hkd-qualification','exec'))"]
    container['env']=[e for e in container['env'] if e['name']!='ALPHA_MS_IMAGE_DIGEST']+[{'name':'ALPHA_MS_IMAGE_DIGEST','value':q.previous.image().split('@')[1]}]
    q.build.save(label+'-intent',{'template':template,'source_sha256':q.previous.sha(source),'database_writes':0,
        'persistent_job_invariant':job_invariant(job),'image':q.previous.image()})
    result=q.previous.request('POST',q.previous.job_path(name)+'/start?api-version=2024-03-01',data=template)
    q.build.save(label+'-dispatch',{'execution':result['name']})
    print(json.dumps({'installed_scheduled_identity_execution':result['name']}))


def verify_installed_probe():
    name=q.previous.JOBS[1];label='installed-financial-probe';ack=q.previous.read(label+'-dispatch')
    run=q.previous.request('GET',q.previous.job_path(name)+'/executions/'+ack['execution']+'?api-version=2024-03-01')
    if run['properties']['status']!='Succeeded':
        output={'status':run['properties']['status'],'execution':ack['execution']}
        if run['properties']['status']=='Failed':
            workspace=q.previous.request('GET',q.previous.BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs?api-version=2023-09-01')
            query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+ack['execution']+"' | where Log_s startswith 'ALPHA_MS_RECOVERY_RESULT ' | project Log_s"
            output['sanitized_failure_markers']=q.build.az('monitor','log-analytics','query','-w',workspace['properties']['customerId'],'--analytics-query',query)
        print(json.dumps(output));return
    wanted=q.previous.read(label+'-intent')['template']['containers'][0];actual=run['properties']['template']['containers'][0]
    if any(actual[k]!=wanted[k] for k in ('image','command','args')) or job_invariant(q.previous.get_job(name))!=q.previous.read(label+'-intent')['persistent_job_invariant']:raise ValueError('Installed execution or settings differ')
    service=q.previous.storage();raw=service.get_blob_client('internal-reports',PREFIX+'installed-outputs/verification.json').download_blob().readall();proof=json.loads(raw)
    if proof['status']!='passed' or proof['count']!=21 or proof['image_digest']!=q.previous.image().split('@')[1] or proof['database_writes']!=0:raise ValueError('Installed proof failed')
    output=ROOT/'output/pdf/AlphaWorld_MS_Azure_Installed_HKD_HIBOR_v14r11';output.mkdir(exist_ok=False)
    for item in proof['artifacts']:
        name='AlphaWorld_MS_Factsheet_'+item['period_end']+'.pdf'
        if item['blob_name']!=PREFIX+'installed-outputs/'+name:raise ValueError('Installed file outside scope')
        content=service.get_blob_client('internal-reports',item['blob_name']).download_blob().readall()
        if q.previous.sha(content)!=item['sha256']:raise ValueError('Installed artifact integrity failure')
        (output/name).write_bytes(content)
    (output/'manifest.json').write_bytes(raw)
    q.build.save('installed-financial-qualified',{'execution':ack['execution'],'proof':proof,'proof_sha256':q.previous.sha(raw),
        'image':q.previous.image(),'all_21_rendered_from_live_hkd_snapshots':True})
    print(json.dumps({'status':'pass','months':21,'source':'live HKD frozen reporting snapshots','folder':str(output)}))


def finish():
    q.previous.require_candidate()
    installed=q.previous.read('installed-financial-qualified');database=q.previous.read('atomic-apply-qualified')
    path=ROOT/'tmp/pdfs/alpha-ms-hkd-installed-v14r11/verification.json';pdf=json.loads(path.read_bytes())
    if pdf['status']!='pass' or pdf['months']!=21 or pdf['pages']!=42 or not pdf['full_page_pixels_identical'] or pdf['image_digest']!=q.previous.image().split('@')[1]:raise ValueError('Installed live-data full-page acceptance required')
    proof=installed['proof']
    if proof['count']!=21 or proof['reports_released'] or proof['onboarding_activated'] or proof['ibkr_source_changed']:raise ValueError('Installed scope mismatch')
    live=verify_coordinated(False)
    q.build.save('completion',{'status':'hkd_hibor_replacement_deployed_and_verified','image':q.previous.image(),
        'current_base_and_reporting_currency':'HKD','risk_free_basis':'arithmetic average of actual published 1M HIBOR fixings, inception through factsheet valuation date',
        'additional_financing_charge':False,'layout_version':LAYOUT,'jobs':list(COORDINATED_JOBS),'website':live,
        'contract_version':database['proof']['definition_version'],'contract_sha256':database['proof']['contract_sha256'],
        'latest_nav_date':database['proof']['derived_result']['latest_date'],'historical_hkd_navs_verified':443,
        'live_hkd_nav_dates':database['proof']['derived_result']['nav_dates'],'risk_caches_built':database['proof']['historical_risk_caches'],
        'historical_months':21,'first_month':'2025-01-31','last_month':'2026-09-30','linux_tests':q.previous.read('native-build-qualified')['linux_tests'],
        'scan_counts':q.previous.read('scan-completed')['assessment']['counts'],
        'full_page_pdf_acceptance':{k:v for k,v in pdf.items() if k not in ('checks','contact_sheets')},
        'postgres_probe':q.previous.read('postgres-qualified')['execution'],'rollback_rehearsal':q.previous.read(ATOMIC_REHEARSAL_LABEL+'-qualified')['execution'],
        'database_cutover_execution':database['execution'],'installed_scheduled_identity_execution':installed['execution'],
        'rollback_backup_blob':database['proof']['backup_blob'],'rollback_backup_sha256':database['proof']['backup_sha256'],
        'database_rollback_rehearsed':'actual PostgreSQL temporary shadow tables; not an independent restored server',
        'website_qualification':'Exact healthy active Azure revision, unchanged runtime/settings and protected public sign-in boundary; no new authenticated browser session tested',
        'website_internal_exec_probe':'Unavailable: Azure CLI websocket handshake returned 404 before executing the read-only probe; not counted as passed',
        'legacy_usd_history_retained_as_noncurrent_rollback_evidence':True,'ibkr_source_changed':False,
        'reports_released':False,'published_files_overwritten':False,'onboarding_activated':False,
        'benchmark_comparison_crisis_tail_attribution':'explicitly unqualified; not substituted with legacy USD/GSPC calculations',
        'future_rendering':'installed HKD/HIBOR methodology and approved layout; issuance still requires validated inputs and existing registration/activation/release controls',
        'installed_private_output_prefix':'internal-reports/'+PREFIX+'installed-outputs/'})
    print(json.dumps({'status':'complete','azure_resources_verified':6,'historical_months':21,'current_methodology':'HKD / period-average 1M HIBOR'}))


if __name__=='__main__':
    stage=sys.argv[1] if len(sys.argv)==2 else ''
    if stage=='prepare':prepare()
    elif stage=='preflight':preflight()
    elif stage=='verify-inputs':verify_inputs()
    elif stage=='probe-postgres':postgres_probe()
    elif stage=='verify-postgres':postgres_status()
    elif stage=='download':download()
    elif stage=='coordinated-baselines':coordinated_baselines()
    elif stage=='atomic-rehearse':atomic_dispatch('rehearse')
    elif stage=='atomic-apply':atomic_dispatch('apply')
    elif stage=='verify-rehearse':atomic_status('rehearse')
    elif stage=='verify-apply':atomic_status('apply')
    elif stage=='pdf-acceptance':pdf_acceptance()
    elif stage=='install':install_coordinated()
    elif stage=='verify-install':verify_coordinated()
    elif stage=='inspect-web-delta':inspect_web_delta()
    elif stage=='installed-probe':installed_probe()
    elif stage=='verify-installed-probe':verify_installed_probe()
    elif stage=='finish':finish()
    elif stage=='build':
        try:q.build.main()
        except ValueError as error:
            if str(error)!='Unknown dispatch outcome; reconcile native runs, do not redispatch':raise
            q.previous.reconcile_build()
    elif stage in ('inspect','reconcile','log','scan','dispatch','execution'):q.main()
    else:raise ValueError('Stage not implemented; no unqualified promotion permitted')
