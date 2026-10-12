"""Scoped website handover correction, built only on its own installed image."""
import ast,hashlib,json,os,re,shutil,subprocess,sys,tarfile
from pathlib import Path
import release_platform_access_correction_20261009 as h
import capture_reporting_consumers_offline_20261011 as capture
import control_upgrade_main_dns_stage41_v2_20261011 as dns
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage42/handover-image-v3'
BASE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:7015b4dda6d6a2d91e24eff1bfd7d08c865681eb6165cd31c64645f9bf2861aa'
SCAN=ROOT/'.work/platform-upgrade-20261011-stage34/consumer-agent-web-v3/scan-v1'
FILES=('src/ibkr_etl/web.py','src/ibkr_etl/monthly_reports.py','src/ibkr_etl/monthly_release.py')
TESTS=('report_snapshot_handover','monthly_reports','monthly_release','monthly_release_api','internal_release','alpha_ms_download_names','alpha_t_download_names','authorization')

def unchanged():
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage42/resume-v1/result.json')
    if dns.current_boundary_function()()!=baseline['boundary']:raise ValueError('Live boundary drift')

def function(source,name):
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name==name)
    lines=source.splitlines(keepends=True)
    return ''.join(lines[node.lineno-1:node.end_lineno])

def once(source,old,new):
    if source.count(old)!=1:raise ValueError('Expected one scoped patch anchor')
    return source.replace(old,new,1)

def capture_sources():
    digest=BASE.split(':')[-1];raw=(SCAN/digest).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Manifest mismatch')
    manifest=json.loads(raw);sources={}
    with tarfile.open(SCAN/'image.docker.tar','r:') as outer:
        records=json.load(outer.extractfile('manifest.json'))
        if len(records)!=1:raise ValueError('Single image archive required')
        record=records[0];config=outer.extractfile(record['Config']).read()
        if 'sha256:'+hashlib.sha256(config).hexdigest()!=manifest['config']['digest']:raise ValueError('Config mismatch')
        diffs=json.loads(config)['rootfs']['diff_ids']
        if len(diffs)!=len(record['Layers']):raise ValueError('Layer count mismatch')
        for layer_name,diff in zip(record['Layers'],diffs):
            digest=hashlib.sha256()
            with outer.extractfile(layer_name) as stream:
                while chunk:=stream.read(1024*1024):digest.update(chunk)
            if 'sha256:'+digest.hexdigest()!=diff:raise ValueError('Layer integrity failed')
            new={};deleted=set();opaque=set()
            with tarfile.open(fileobj=outer.extractfile(layer_name),mode='r|') as layer:
                for entry in layer:
                    name=capture.member_name(entry.name);parent,_,leaf=name.rpartition('/')
                    if leaf=='.wh..wh..opq':opaque.add(parent)
                    elif leaf.startswith('.wh.'):deleted.add((parent+'/' if parent else '')+leaf[4:])
                    elif (name.startswith('app/src/') or name.startswith('app/scripts/')) and name.endswith('.py'):
                        if not entry.isfile() or not 0<entry.size<2*1024*1024:raise ValueError('Regular bounded source required')
                        new[name]=layer.extractfile(entry).read()
                    elif (entry.issym() or entry.islnk()) and (name in ('app','app/src','app/scripts') or name.startswith(('app/src/','app/scripts/'))):
                        raise ValueError('Unexpected source parent link')
            sources=capture.apply_layer(sources,new,deleted,opaque)
    if not {'app/'+n for n in FILES}<=set(sources):raise ValueError('Missing scoped source')
    return {n.removeprefix('app/'):raw.decode('utf-8') for n,raw in sources.items()}

def prepare():
    if OUT.exists():raise ValueError('Preserve previous build attempt')
    baseline=capture_sources();candidate=dict(baseline)
    runtime=OUT/'local-runtime'
    for name,source in baseline.items():
        path=runtime/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(source,encoding='utf-8',newline='\n')
    for name in ('_monthly_report_catalog','_download_monthly_report'):
        original=function(baseline[FILES[0]],name);modified=original
        indent='          ' if name=='_monthly_report_catalog' else '              '
        modified=once(modified,"AND a.revoked_at IS NULL","AND a.revoked_at IS NULL\n"+indent+"AND a.superseded_by_artifact_release_id IS NULL")
        modified=once(modified,"AND d.status = 'approved'","AND d.status = 'approved'\n"+indent+"AND d.snapshot_id = r.snapshot_id")
        if name=='_download_monthly_report':
            modified=once(modified,"AND s.status IN ('frozen', 'approved')","AND s.status IN ('frozen', 'approved', 'superseded')\n"+indent+"AND s.quality_status <> 'fail'")
        else:
            modified=once(modified,"AND s.subject_id = %s","AND s.subject_id = %s\n"+indent+"AND s.status IN ('frozen', 'approved', 'superseded')\n"+indent+"AND s.quality_status <> 'fail'\n"+indent+"AND r.status = 'released_internal'")
        if ast.dump(ast.parse(modified))!=ast.dump(ast.parse(function((ROOT/FILES[0]).read_text(encoding='utf-8'),name))):
            raise ValueError('Local function has additional unscoped changes')
        candidate[FILES[0]]=once(candidate[FILES[0]],original,modified)
    for prefix in ('const renderReportOptions=','const updateReportMonth='):
        old=next(line for line in candidate[FILES[0]].splitlines() if line.startswith(prefix))
        new=next(line for line in (ROOT/FILES[0]).read_text(encoding='utf-8').splitlines() if line.startswith(prefix))
        candidate[FILES[0]]=once(candidate[FILES[0]],old,new)
    old=function(baseline[FILES[1]],'build_monthly_report_catalog')
    new=function((ROOT/FILES[1]).read_text(encoding='utf-8'),'build_monthly_report_catalog')
    candidate[FILES[1]]=once(candidate[FILES[1]],old,new)
    local_web=(ROOT/FILES[0]).read_text(encoding='utf-8')
    for name in ('_monthly_release_candidate','release_subject_monthly_reports'):
        candidate[FILES[0]]=once(candidate[FILES[0]],function(baseline[FILES[0]],name),function(local_web,name))
    candidate[FILES[0]]=once(candidate[FILES[0]],'def _monthly_release_candidate(',
        function(local_web,'_supersede_prior_monthly_releases')+'\n\n\ndef _monthly_release_candidate(')
    anchor='            artifact["state_revision"] = int(release.get("state_revision") or 1)'
    candidate[FILES[2]]=once(candidate[FILES[2]],anchor,anchor+"\n            artifact['superseded']=release.get('superseded_by_artifact_release_id') is not None")
    if candidate[FILES[2]]!=(ROOT/FILES[2]).read_text(encoding='utf-8'):raise ValueError('Unscoped release package change')
    context=OUT/'context'
    for name in FILES:
        ast.parse(candidate[name])
        path=context/'payload'/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(candidate[name],encoding='utf-8',newline='\n')
        original=OUT/'baseline'/name;original.parent.mkdir(parents=True,exist_ok=True)
        original.write_text(baseline[name],encoding='utf-8',newline='\n')
    (context/'tests').mkdir()
    (context/'scripts').mkdir()
    shutil.copyfile(runtime/'scripts/release_internal_reports.py',context/'scripts/release_internal_reports.py')
    for name in TESTS:shutil.copyfile(ROOT/'tests'/('test_'+name+'.py'),context/'tests'/('test_'+name+'.py'))
    shutil.copyfile(ROOT/'scripts/qualify_report_handover_native_20261011.py',context/'qualify.py')
    # Preliminary tests load the two exact staged modules with local dependencies.
    code="""import importlib.util,sys,unittest,pathlib,ibkr_etl
root=pathlib.Path(sys.argv[1])
for short in ('monthly_reports','monthly_release','web'):
 name='ibkr_etl.'+short;spec=importlib.util.spec_from_file_location(name,root/'payload/src/ibkr_etl'/(short+'.py'))
 module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);setattr(ibkr_etl,short,module)
result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(root/'tests')))
print('HANDOVER_TEST_COUNT',result.testsRun)
sys.exit(not result.wasSuccessful() or bool(result.skipped))
"""
    run=subprocess.run([sys.executable,'-c',code,str(context)],cwd=runtime,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1',
        'PYTHONPATH':os.pathsep.join(map(str,(runtime/'src',runtime/'scripts')))},capture_output=True,text=True,timeout=180)
    (OUT/'local-tests.log').write_text(run.stdout+run.stderr,encoding='utf-8')
    if run.returncode:print(run.stderr[-3000:]);raise ValueError('Exact staged-source tests failed')
    count=int(re.findall(r'HANDOVER_TEST_COUNT (\d+)',run.stdout)[-1])
    plan={'base_image':BASE,'baseline':{n:hashlib.sha256(baseline[n].encode()).hexdigest() for n in FILES},
        'candidate':h.manifest(context/'payload'),'test_count':count,
        'scope':'Previously approved unrevoked reports remain available across calculation-snapshot replacement until explicitly replaced/revoked. New approvals remain separate; scope, quality and integrity guards preserved.'}
    h.save(context/'prepared.json',plan)
    docker='FROM '+BASE+' AS qualification\nCOPY --chown=10001:10001 . /qualification/report-handover/\nRUN ["python","/qualification/report-handover/qualify.py"]\nFROM '+BASE+'\n'
    for name in (*FILES,'ALPHA_T_RUNTIME_RELEASE.json'):docker+='COPY --from=qualification --chown=10001:10001 /app/'+name+' /app/'+name+'\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    h.save(OUT/'source-intent.json',{'tag':'ibkr-platform:report-handover-20261011-v3','base_image':BASE,'candidate_hashes':plan['candidate'],
        'files_sha256':h.manifest(context),'scope':plan['scope'],'source_only':True,'private_records_uploaded':False})
    h.save(OUT/'local-qualification.json',{'status':'isolated_local_candidate_passed','tests_run':count,
        'source_intent_sha256':h.sha(OUT/'source-intent.json'),'log_sha256':h.sha(OUT/'local-tests.log')})
    print(json.dumps({'prepared':True,'local_tests':count,'changed_source_files':len(FILES)}))

def main(stage):
    h.WEB=OUT
    if stage=='prepare':return prepare()
    source=h.read(OUT/'source-intent.json')
    if h.manifest(OUT/'context')!=source['files_sha256']:raise ValueError('Frozen source drift')
    unchanged()
    if stage=='build':
        if (OUT/'build-intent.json').exists():raise ValueError('Reconcile existing native run')
        h.save(OUT/'build-intent.json',{'tag':source['tag'],'before_jobs':{},'source_intent_sha256':h.sha(OUT/'source-intent.json'),'private_records_uploaded':False})
        run=subprocess.run([h.build.AZ,'-m','azure.cli','acr','build','-r',h.build.REGISTRY,'-t',source['tag'],'-f',str(OUT/'context/Dockerfile'),'--no-logs','--no-wait',str(OUT/'context')],capture_output=True,text=True,encoding='utf-8',timeout=180)
        ids=re.findall(r'Queued a build with ID: ([a-z0-9]+)',run.stdout+run.stderr)
        if run.returncode or len(ids)!=1:raise ValueError('Unknown build outcome; reconcile')
        h.save(OUT/'build-ack.json',{'run':ids[0],'tag':source['tag']});print(json.dumps({'run':ids[0]}));return
    if stage=='inspect':return h.inspect()
    if stage=='scan':
        import scan_ms_presentation_worker_candidate as scanner
        scanner.jobs_unchanged=lambda _:unchanged()
        sys.argv=[sys.argv[0],'--folder',str(OUT)];return scanner.main()
    if stage!='logs':raise ValueError('Unknown action')
    ack=h.read(OUT/'build-ack.json')
    run=subprocess.run([h.build.AZ,'-m','azure.cli','acr','task','logs','-r',h.build.REGISTRY,'--run-id',ack['run']],capture_output=True,text=True,encoding='utf-8',timeout=120)
    log=run.stdout+run.stderr
    with (OUT/'native-build.log').open('x',encoding='utf-8') as f:f.write(log)
    proofs=[json.loads(x) for x in re.findall(r'^REPORT_HANDOVER_NATIVE (\{.*\})$',log,re.M)]
    if run.returncode or len(proofs)!=1 or not proofs[0]['passed'] or proofs[0]['candidate_hashes']!=source['candidate_hashes'] or proofs[0]['tests']!=h.read(OUT/'local-qualification.json')['tests_run']:raise ValueError('Native qualification failed')
    h.save(OUT/'native-build-qualified.json',{'qualified':True,'run':ack['run'],'linux_tests':proofs[0]['tests'],'proof':proofs[0],'native_log_sha256':h.sha(OUT/'native-build.log')})
    print(json.dumps({'native_tests':proofs[0]['tests'],'passed':True}))
if __name__=='__main__':main(sys.argv[1])
