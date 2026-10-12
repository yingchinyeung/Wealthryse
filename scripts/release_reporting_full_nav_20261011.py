"""Qualify the full-NAV correction on the exact installed reporting baseline."""
import ast,json,os,re,shutil,subprocess,sys
from pathlib import Path
import release_reporting_corrections_20261011 as c
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=c.ROOT;r=c.r;PRIOR=c.OUT
c.OUT=PRIOR.parent/'reporting-image-v4';OUT=c.OUT
CHANGED=('scripts/report_content_labels.mjs','scripts/render_monthly_presentation.mjs',
         'scripts/render_alpha_t_private_presentation.mjs','src/ibkr_etl/presentation_design.py',
         'src/ibkr_etl/monthly_reporting_analytics.py')

def prepare():
    if OUT.exists():raise ValueError('Preserve existing candidate')
    old=r.read(PRIOR/'source-intent.json')
    if r.manifest(PRIOR/'context')!=old['files_sha256']:raise ValueError('Prior source changed')
    accepted=r.read(PRIOR/'native-build-qualified.json')
    if not accepted['qualified'] or accepted['native_log_sha256']!=r.sha(PRIOR/'native-build.log'):raise ValueError('Prior native evidence required')
    proof=protected(PRIOR.parent/'legacy-monthly-plan-v3/result.json')
    captured=PRIOR.parent/'legacy-monthly-plan-v3/installed_monthly_reporting_analytics.py'
    if not proof['passed'] or proof['proof']['months']!=20 or proof['proof']['native_tests']!=32 or r.sha(captured)!=proof['proof']['installed_monthly_sha256']:raise ValueError('Exact twenty-month reconciliation required')
    # Only the physical attribution routine and its new version constant may differ.
    def other(source):
        tree=ast.parse(source)
        return [ast.dump(n,include_attributes=False) for n in tree.body
                if not (isinstance(n,ast.FunctionDef) and n.name=='build_monthly_reporting_analytics')
                and not (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PHYSICAL_METHOD_VERSION' for t in n.targets))]
    if other(captured.read_text())!=other((ROOT/CHANGED[-1]).read_text()):raise ValueError('Unrelated monthly source changed')
    context=OUT/'context'
    shutil.copytree(PRIOR/'context',context)
    for name in CHANGED:shutil.copyfile(ROOT/name,context/'payload'/name)
    shutil.copyfile(ROOT/'tests/test_monthly_reporting_analytics.py',context/'tests/test_monthly_reporting_analytics.py')
    shutil.copyfile(ROOT/'tests/test_presentation_design.py',context/'tests/test_presentation_design.py')
    shutil.copyfile(ROOT/'scripts/test_report_content_labels.mjs',context/'node-tests/test_report_content_labels.mjs')
    native=(ROOT/'scripts/qualify_reporting_image_native_20261011.py').read_text()
    replacements={'result.testsRun!=72':'result.testsRun!=81',"'# pass 13\\n'":"'# pass 18\\n'","'python_tests':72,'node_tests':13":"'python_tests':81,'node_tests':18",
                  "Qualified reporting currency, source-labelled benchmarks, complete cash notes, bounded conclusions, MS note spacing and T exact shared-renderer integration. No template, NAV, approval or source-data change.":"Qualified reporting currency and full-NAV monthly attribution with explicit residual-only coverage, complete cash notes and exact T integration. Frozen NAV, templates, approvals and broker sources unchanged."}
    for before,after in replacements.items():
        if native.count(before)!=1:raise ValueError('Native qualifier anchor changed')
        native=native.replace(before,after)
    (context/'qualify.py').write_text(native,encoding='utf-8',newline='\n')
    manifest=r.read(context/'patch-manifest.json')
    manifest['baseline'][CHANGED[-1]]=r.sha(captured)
    manifest['candidate']=r.manifest(context/'payload')
    manifest['new_files']=sorted(set(manifest['candidate'])-set(manifest['baseline']))
    (context/'patch-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    r.save(OUT/'source-intent.json',{'tag':'ibkr-platform:reporting-material-corrections-20261011-v4','base_image':c.BASE_IMAGE,
        'files_sha256':r.manifest(context),'candidate_hashes':manifest['candidate'],'scope':'Eleven reporting source files including full-NAV physical monthly attribution. No live deployment, frozen NAV change, broker source change or approval change.',
        'source_only':True,'portfolio_fixture_uploaded':False,'database_fixture_uploaded':False,
        'prior_source_intent_sha256':r.sha(PRIOR/'source-intent.json'),'monthly_reconciliation_sha256':r.sha(PRIOR.parent/'legacy-monthly-plan-v3/result.json')})
    print(json.dumps({'prepared':True,'source_files':len(manifest['candidate']),'context_bytes':sum(p.stat().st_size for p in context.rglob('*') if p.is_file())}))

def qualify():
    source=r.read(OUT/'source-intent.json')
    if r.manifest(OUT/'context')!=source['files_sha256']:raise ValueError('Frozen source drift')
    env={**os.environ,'PYTHONPATH':os.pathsep.join(('src','scripts')),'PYTHONDONTWRITEBYTECODE':'1',
         'TEMP':str(ROOT/'.work/platform-upgrade-20261011-stage33/test-temp'),'TMP':str(ROOT/'.work/platform-upgrade-20261011-stage33/test-temp')}
    tests=[*c.TESTS,'monthly_reporting_analytics']
    py=subprocess.run([sys.executable,'-m','unittest',*['tests.test_'+n for n in tests]],cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
    node=subprocess.run([str(Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')),'--test','--test-reporter=tap',*['scripts/'+n for n in c.NODE]],cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
    log=py.stdout+py.stderr+node.stdout+node.stderr
    with (OUT/'local-tests.log').open('x',encoding='utf-8') as f:f.write(log)
    if py.returncode or node.returncode or 'Ran 81 tests' not in log or '# pass 18\n' not in log or '# fail 0\n' not in log:raise ValueError('Exact local regression suite required: '+log[-2000:])
    r.save(OUT/'local-qualification.json',{'status':'isolated_local_candidate_passed','tests_run':99,'source_intent_sha256':r.sha(OUT/'source-intent.json'),'log_sha256':r.sha(OUT/'local-tests.log'),'native_tests_required':{'python':81,'node':18}})
    print(json.dumps({'local_tests':99,'passed':True,'deployed':False}))

def logs():
    c.verify();ack=r.read(OUT/'build-ack.json');completed=r.read(OUT/'build-completed.json')
    run=subprocess.run([r.build.AZ,'-m','azure.cli','acr','task','logs','-r',r.build.REGISTRY,'--run-id',ack['run']],capture_output=True,text=True,encoding='utf-8',timeout=120,env={**os.environ,'PYTHONUTF8':'1'})
    log=run.stdout+run.stderr;proofs=[json.loads(m) for m in re.findall(r'^REPORTING_IMAGE_NATIVE (\{.*\})$',log,re.M)]
    if run.returncode or len(proofs)!=1 or not proofs[0]['passed'] or proofs[0]['python_tests']!=81 or proofs[0]['node_tests']!=18 or proofs[0]['candidate_hashes']!=r.read(OUT/'source-intent.json')['candidate_hashes'] or '\nFAILED (' in log:raise ValueError('Exact native qualification required')
    with (OUT/'native-build.log').open('x',encoding='utf-8') as f:f.write(log)
    r.save(OUT/'native-build-qualified.json',{'qualified':True,'run':ack['run'],'linux_tests':99,'native_log_sha256':r.sha(OUT/'native-build.log'),'proof':proofs[0]})
    print(json.dumps({'qualified':True,'native_tests':99,'deployed':False}))

if __name__=='__main__':{'prepare':prepare,'qualify':qualify,'build':c.dispatch,'inspect':c.inspect,'reconcile':c.reconcile,'logs':logs}[sys.argv[1]]()
