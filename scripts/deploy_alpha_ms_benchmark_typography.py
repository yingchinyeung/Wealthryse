"""Evidence-gated header-only Azure rollout on the installed v14r9 image."""
import ast
import json
from pathlib import Path
import sys

import deploy_alpha_ms_allocation_geometry as gate

ROOT=gate.ROOT
BASE_FOLDER=ROOT/'.work/alpha-ms-azure-geometry-v14r9-20261007-r2'
gate.FOLDER=ROOT/'.work/alpha-ms-azure-header-v14r10-20261007'
gate.BASE_CONTEXT=BASE_FOLDER/'context'
gate.BASE_IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:fbd1253223195f1a5ee5f2d83c4cefa8b9948ce660b3c5efa13e9877c17f6441'
gate.BASE_LAYOUT='subject-completed-factsheet-model-v14r9-alpha-world-ms-allocation-geometry'
gate.LAYOUT='subject-completed-factsheet-model-v14r10-alpha-world-ms-benchmark-typography'
gate.PREFIX='review/alpha-world-ms/azure-header-v14r10-20261007/'
gate.OUTPUT_FOLDER=ROOT/'output/pdf/AlphaWorld_MS_Azure_Header_Typography_v14r10'
gate.QA_FOLDER=ROOT/'tmp/pdfs/alpha-ms-header-history-azure-v14r10'
gate.PROBE_PREFIX='qualification/alpha-world-ms/header-v14r10-20261007/'
gate.RUN_MARKER='PASS: header-only Azure historical and future MS entrypoints v14r10'
gate.MIN_LINUX_TESTS=54
gate.RELEASE_STATUS='header-only-azure-renderer-deployed-and-qualified'
gate.previous.FOLDER=gate.FOLDER
gate.previous.LAYOUT=gate.LAYOUT
gate.previous.PREFIX=gate.PREFIX
gate.previous.TAG='ibkr-platform:alpha-ms-header-v14r10-20261007'
gate.previous.build.FOLDER=gate.FOLDER
gate.previous.build.TAG=gate.previous.TAG

OLD_HEADER='''        # Correct inherited factual fields without changing the source design.
        c.setFillColor(white)
        c.rect(296,718,115,16.3,fill=1,stroke=0)
        c.setFillColor(black)
        c.setFont("Helvetica",8.0)
        # Owner-approved factsheet label. The stored comparison series is a
        # separate analytical input; do not relabel or replace source data.
        c.drawString(299,724.8,"HIBOR + Spread")'''


def prepare():
    if gate.FOLDER.exists():raise ValueError('Frozen attempt exists; inspect instead')
    baseline=json.loads((BASE_FOLDER/'source-intent.json').read_bytes())['record']
    for name,digest in baseline['files_sha256'].items():
        if gate.previous.sha((gate.BASE_CONTEXT/name).read_bytes())!=digest:raise ValueError('Installed frozen context changed')
    before={n:gate.previous.get_job(n) for n in gate.previous.JOBS}
    if any(j['properties']['template']['containers'][0]['image']!=gate.BASE_IMAGE for j in before.values()):
        raise ValueError('Installed baseline drift; do not overwrite')
    context=gate.FOLDER/'context';context.mkdir(parents=True)
    module='src/ibkr_etl/monthly_artifacts.py'
    source=(gate.BASE_CONTEXT/module).read_text(encoding='utf-8')
    if source.count(OLD_HEADER)!=1:raise ValueError('Exact original overlay required')
    patched=source.replace(OLD_HEADER,'        # Preserve the approved embedded Arial benchmark; match Multi-Tranche exactly.')
    patched=gate.replace_symbol(patched,'ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION',
        gate.node_source((ROOT/module).read_text(encoding='utf-8'),'ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION')[1])
    def unchanged(text):
        return [ast.dump(n,include_attributes=False) for n in ast.parse(text).body
            if getattr(n,'name',None)!='_draw_alpha_world_page_one'
            and not (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name)
                and t.id=='ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION' for t in n.targets))]
    if unchanged(source)!=unchanged(patched):raise ValueError('Other renderer logic changed')
    validator_name='src/ibkr_etl/alpha_world_ms_pdf_validation.py'
    validator=(gate.BASE_CONTEXT/validator_name).read_text(encoding='utf-8')
    old="""    benchmark = [r[0] for r in text if abs(r[1]-299)<.001 and abs(r[2]-724.8)<.001 and r[3]==8.]
    _require(benchmark == ['HIBOR + Spread'], 'owner-approved factsheet benchmark label differs')"""
    if validator.count(old)!=1:raise ValueError('Exact caption validation required')
    validator=validator.replace(old,'    validate_benchmark_typography(reader.pages[0])')
    validator+='\n\n'+gate.node_source((ROOT/validator_name).read_text(encoding='utf-8'),'validate_benchmark_typography')[1]+'\n'
    release=json.loads((gate.BASE_CONTEXT/'ALPHA_MS_RENDER_RELEASE.json').read_bytes())
    old_prefix=b'review/alpha-world-ms/azure-geometry-v14r9-20261007-r2/'
    for name in release['installed_files_sha256']:
        content=(gate.BASE_CONTEXT/name).read_bytes()
        if name==module:content=patched.encode()
        elif name==validator_name:content=validator.encode()
        elif name=='scripts/render_alpha_world_ms_azure_batch.py':
            if content.count(old_prefix)!=1:raise ValueError('Exact private output prefix required')
            content=content.replace(old_prefix,gate.PREFIX.encode())
        target=context/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
        release['installed_files_sha256'][name]=gate.previous.sha(content)
    for name in ('src/ibkr_etl/alpha_world_ms_analytics.py','src/ibkr_etl/market_calendar.py','scripts/generate_month_end_package.py'):
        if (context/name).read_bytes()!=(gate.BASE_CONTEXT/name).read_bytes():raise ValueError('Financial or scheduling module changed')
    release.update(layout_version=gate.LAYOUT,base_image=gate.BASE_IMAGE,header_typography_only=True,
        financial_calculation_code_unchanged=True,hkd_cutover=False,geometry_only=False,report_release_authorized=False)
    (context/'ALPHA_MS_RENDER_RELEASE.json').write_text(json.dumps(release,indent=2),encoding='utf-8')
    for original in (gate.BASE_CONTEXT/'tests').glob('*.py'):
        target=context/'tests'/original.name;target.parent.mkdir(exist_ok=True);target.write_bytes(original.read_bytes())
    fixtures=context/'tests/test_monthly_artifacts.py'
    fixtures.write_text(gate.replace_symbol(fixtures.read_text(encoding='utf-8'),'_write_alpha_world_source',
        gate.node_source((ROOT/'tests/test_monthly_artifacts.py').read_text(encoding='utf-8'),'_write_alpha_world_source')[1]),encoding='utf-8')
    tests=context/'tests/test_alpha_world_ms_pdf_accuracy.py'
    current=(ROOT/'tests/test_alpha_world_ms_pdf_accuracy.py').read_text(encoding='utf-8')
    cls=next(n for n in ast.parse(current).body if isinstance(n,ast.ClassDef) and n.name=='MSPDFAccuracyTests')
    names={'test_rejects_benchmark_font_different_from_risk_profile','test_rejects_smaller_benchmark_font','test_rejects_shifted_benchmark_baseline'}
    methods=['\n'.join(current.splitlines()[n.lineno-1:n.end_lineno]) for n in cls.body if getattr(n,'name',None) in names]
    if len(methods)!=3:raise ValueError('All typography regressions required')
    tests.write_text(gate.replace_symbol(tests.read_text(encoding='utf-8'),'MSPDFAccuracyTests',
        gate.node_source(tests.read_text(encoding='utf-8'),'MSPDFAccuracyTests')[1]+'\n\n'+'\n\n'.join(methods)),encoding='utf-8')
    docker=f'FROM {gate.BASE_IMAGE}\nENV PYTHONPATH=/app/src:/app:/app/scripts\n'
    docker+='COPY --chown=10001:10001 src/ibkr_etl/ /app/src/ibkr_etl/\nCOPY --chown=10001:10001 scripts/ /app/scripts/\n'
    docker+='COPY --chown=10001:10001 ALPHA_MS_RENDER_RELEASE.json /app/ALPHA_MS_RENDER_RELEASE.json\nCOPY --chown=10001:10001 tests/ /tmp/alpha-ms-tests/\n'
    docker+='RUN ["python","scripts/check_report_runtime.py"]\nRUN ["python","-m","unittest","discover","-s","/tmp/alpha-ms-tests","-v"]\n'
    docker+='RUN ["python","-c","import run_registry_month_end_packages,run_self_service_onboarding_reports; import ibkr_etl.monthly_artifacts as m; assert m.factsheet_layout_version(\'alpha-world-ms\') == \''+gate.LAYOUT+'\'; print(\''+gate.RUN_MARKER+'\')"]\n'
    docker+='CMD ["python","scripts/run_registry_month_end_packages.py"]\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    gate.previous.build.save('source-intent',{'tag':gate.previous.TAG,'layout_version':gate.LAYOUT,
        'files_sha256':{p.relative_to(context).as_posix():gate.previous.sha(p.read_bytes()) for p in context.rglob('*') if p.is_file()},
        'before_jobs':before,'before_invariants':{n:gate.previous.invariant(j) for n,j in before.items()},
        'base_image':gate.BASE_IMAGE,'scope':'MS benchmark header typography only',
        'financial_calculation_code_unchanged':True,'hkd_cutover':False,'source_data_changed':False,'production_promotion':False})
    print(json.dumps({'prepared':True,'scope':'header-only','financial_modules_unchanged':True}))


if __name__=='__main__':
    if len(sys.argv)==2 and sys.argv[1]=='prepare':prepare()
    elif len(sys.argv)==2 and sys.argv[1]=='build':
        try:gate.previous.build.main()
        except ValueError as error:
            if str(error)!='Unknown dispatch outcome; reconcile native runs, do not redispatch':raise
            gate.previous.reconcile_build()
    else:gate.main()
