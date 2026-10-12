"""Prepare immutable T runtime candidates from verified installed image layers.

This never deploys, changes database state, or approves a report. Shared Python
files are patched by an explicit symbol allowlist; unrelated local web work is
excluded. Every installed/candidate byte is bound in the release manifest.
"""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import sys

import release_alpha_ms_disclaimer_v14r16 as prior
from deploy_alpha_ms_allocation_geometry import node_source, replace_symbol
from release_alpha_ms_headers_v14r15 import unchanged

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-runtime-release-20261009-v7'
PARENT=ROOT/'.work/alpha-ms-disclaimer-v14r16-20261009-r2'
IMAGES={
    'web':'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:fdc6e0c0606a10b2be11cad67bb24a7a0664a7c9f6f1ef10f19165759dadc56a',
    'worker':'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:c8865ffd07b1c353a16820422190a01573a642190fd7bc8946c33e0f8aa66f0a',
}
SYMBOLS={
    'subject_onboarding':('approved_reference_definitions',),
    'alpha_world_ms_analytics':('analyze','simulate','_analyze','_simulate'),
    'derived_portfolio':('refresh_alpha_world_t_portfolio','refresh_fixed_unit_composite'),
    'risk_snapshots':('compute_risk_snapshot','load_risk_inputs','risk_input_fingerprint',
        'snapshot_read_status','store_risk_snapshot','validate_risk_input_readiness'),
    'agent_tools':('_benchmark','_freshness','_risk','execute_tool'),
    'reporting_snapshot':('_collect_tool_results','assemble_month_end_payload'),
    'monthly_artifacts':('_alpha_world_return_distribution','_draw_alpha_world_distribution',
        '_draw_alpha_world_line_chart','factsheet_layout_version','render_factsheet','validate_alpha_world_factsheet_layout'),
    'presentation_design':('presentation_content_contract_version','presentation_content_requirements'),
    'historical_reporting':('run_historical_month_end_batch',),
}
SCRIPT_SYMBOLS={
    'generate_month_end_package.py':('main',),
    'render_onboarding_historical_reports.py':('_render_presentation',),
    'validate_subject_presentation.py':('validate_subject_presentation',),
    'run_self_service_derived_refresh.py':('_refresh_analytics_if_approved',),
    'run_self_service_onboarding_analytics.py':('main',),
}


def sha(raw):return hashlib.sha256(raw).hexdigest()


def save(path,value):
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,indent=2)


def patch_symbols(original,current,symbols):
    result=original
    for name in symbols:
        source=node_source(current,name)[1]
        try:node_source(result,name)
        except (ValueError,StopIteration):result+='\n\n'+source+'\n'
        else:result=replace_symbol(result,name,source)
    if unchanged(original,set(symbols))!=unchanged(result,set(symbols)):
        raise ValueError('Non-allowlisted AST changed')
    ast.parse(result)
    return result.encode()


def prepare():
    if FOLDER.exists():raise ValueError('Frozen attempt exists; preserve it')
    FOLDER.mkdir()
    prior.IMAGES=IMAGES;prior.PARENTS={k:PARENT/k for k in IMAGES}
    inputs=ROOT/'.work/alpha-t-reporting-batch-20261009-v2/inputs'
    periods=sorted(p.stem for p in inputs.glob('*.json'))
    if len(periods)!=21:raise ValueError('21 qualified monthly inputs required')
    for kind in IMAGES:
        folder=FOLDER/kind;runtime=folder/'local-runtime';context=folder/'context'
        baseline=prior.extract_parent(kind,runtime)
        patch={}
        for module,symbols in SYMBOLS.items():
            name='src/ibkr_etl/'+module+'.py'
            patch[name]=patch_symbols(baseline[name].decode('utf-8'),(ROOT/name).read_text(encoding='utf-8'),symbols)
        for source in sorted((ROOT/'src/ibkr_etl').glob('alpha_world_t_*.py')):
            name=source.relative_to(ROOT).as_posix()
            if name in baseline:raise ValueError('Unexpected installed T module; inspect baseline')
            patch[name]=source.read_bytes()
        for name in ('src/ibkr_etl/website_benchmarks.py',):
            patch[name]=(ROOT/name).read_bytes()
        # The reporting baseline lacks the deployed website's HKD comparison
        # support. Use its already-deployed implementation as the comparator
        # basis, then add only the T allowlisted branch changes.
        name='src/ibkr_etl/provisional_benchmark.py'
        comparison_base=(PARENT/'web/local-runtime'/name).read_text(encoding='utf-8')
        current=(ROOT/name).read_text(encoding='utf-8')
        patch[name]=patch_symbols(comparison_base,current,('refresh_and_materialize','_refresh_and_materialize'))
        for script,symbols in SCRIPT_SYMBOLS.items():
            name='scripts/'+script
            patch[name]=patch_symbols(baseline[name].decode('utf-8'),(ROOT/name).read_text(encoding='utf-8'),symbols)
        for name in ('scripts/render_alpha_t_private_presentation.mjs','scripts/render_alpha_t_presentation.mjs',
                     'scripts/alpha_t_presentation_runtime.mjs',
                     'scripts/check_t_report_smoke.py',
                     'scripts/audit_alpha_t_private_presentation.py',
                     'templates/alpha-world-t/ms_style_sections_v6.pdf','templates/alpha-world-t/owner_template_20261008.pdf'):
            patch[name]=(ROOT/name).read_bytes()
        if sha(baseline['scripts/render_monthly_presentation.mjs'])!='03faeb260623d6f39125b82e838b25f26fb984652b337629fd8df3b3928aea0f':
            raise ValueError('T adapter reference renderer differs')
        name='src/ibkr_etl/web.py';original=baseline[name].decode('utf-8')
        raw=patch_symbols(original,(ROOT/name).read_text(encoding='utf-8'),
            ('_onboarding_preview_payload','risk_analytics','risk_analytics_details','benchmark_comparison')).decode('utf-8')
        raw+='\nfrom .website_benchmarks import website_comparison_version, MS_HKD_COMPARISON_VERSION, T_HKD_COMPARISON_VERSION\n'
        patch[name]=raw.encode()
        for name,data in patch.items():
            for parent in (runtime,context):
                target=parent/name;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(data)
        shutil.copytree(PARENT/kind/'context/tests',context/'tests',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        # Replace only the obsolete USD T test with the reviewed fail-closed
        # test; preserve all parent MS/physical-account coverage.
        test=context/'tests/test_monthly_artifacts.py'
        old_text=test.read_text(encoding='utf-8')
        new_text=(ROOT/'tests/test_monthly_artifacts.py').read_text(encoding='utf-8')
        old_node=next(n for n in ast.walk(ast.parse(old_text)) if isinstance(n,ast.FunctionDef) and n.name=='test_alpha_world_t_uses_alpha_world_family_and_correct_primary_sleeve')
        new_node=next(n for n in ast.walk(ast.parse(new_text)) if isinstance(n,ast.FunctionDef) and n.name=='test_alpha_world_t_rejects_legacy_usd_ms_template_path')
        lines=old_text.splitlines(keepends=True)
        lines[old_node.lineno-1:old_node.end_lineno]=new_text.splitlines(keepends=True)[new_node.lineno-1:new_node.end_lineno]
        test.write_text(''.join(lines),encoding='utf-8')
        shutil.copyfile(ROOT/'scripts/ms_website_runtime_environment.py',context/'tests/ms_website_runtime_environment.py')
        for name in ('test_alpha_world_t_analytics.py','test_alpha_world_t_cache.py','test_alpha_world_t_crisis.py',
                     'test_alpha_world_t_crisis_policy.py','test_alpha_world_t_reporting.py',
                     'test_alpha_world_t_consumer_paths.py','test_alpha_world_t_onboarding.py','test_ms_website_comparisons.py','test_t_runtime_release.py','test_t_presentation_runtime.py'):
            shutil.copyfile(ROOT/'tests'/name,context/'tests'/name)
        fixture=context/'tests/t-release-fixtures';fixture.mkdir()
        shutil.copytree(inputs,fixture/'inputs')
        # Supply the installed scripts needed by source-inspection tests, but
        # do not copy unrelated local scripts into the image.
        name='scripts/release_internal_reports.py';p=context/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(baseline[name])
        manifest={'subject_id':'alpha-world-t','base_image':IMAGES[kind],
            'contract_sha256':'99b9d84e8118632b59dfd73fa64c08c05af409bb05a3daa34dd2fe78173f0209',
            'baseline_files_sha256':{n:sha(baseline[n]) for n in patch if n in baseline},
            'installed_files_sha256':{n:sha(data) for n,data in patch.items()},
            'periods':periods,'scope':'T HKD runtime and report dispatch; no unrelated web UI features',
            'source_data_changed':False,'report_approvals_changed':False,'viewer_activation_changed':False}
        save(context/'ALPHA_T_RUNTIME_RELEASE.json',manifest)
        before='import hashlib,pathlib; e='+repr(manifest['baseline_files_sha256'])+"; assert all(hashlib.sha256((pathlib.Path('/app')/n).read_bytes()).hexdigest()==h for n,h in e.items())"
        after='import hashlib,pathlib; e='+repr(manifest['installed_files_sha256'])+"; assert all(hashlib.sha256((pathlib.Path('/app')/n).read_bytes()).hexdigest()==h for n,h in e.items()); print('PASS: qualified T HKD runtime candidate')"
        docker=f'FROM {IMAGES[kind]}\nENV PYTHONPATH=/app/src:/app/scripts:/app/t-runtime-tests PYTHONDONTWRITEBYTECODE=1\n'
        docker+='RUN '+json.dumps(['python','-c',before])+'\n'
        for section in ('src','scripts','templates'):docker+=f'COPY --chown=10001:10001 {section}/ /app/{section}/\n'
        docker+='COPY --chown=10001:10001 tests/ /app/t-runtime-tests/\nCOPY --chown=10001:10001 ALPHA_T_RUNTIME_RELEASE.json /app/\n'
        docker+='RUN ["python","scripts/check_report_runtime.py"]\nRUN ["python","-m","unittest","discover","-s","/app/t-runtime-tests","-v"]\n'
        docker+='RUN ["python","scripts/check_t_report_smoke.py"]\n'
        docker+='RUN '+json.dumps(['python','-c',after])+'\n'
        (context/'Dockerfile').write_text(docker,encoding='utf-8')
        save(folder/'source-intent.json',{'tag':f'ibkr-platform:alpha-t-hkd-{kind}-20261009-v7',
            'base_image':IMAGES[kind],'files_sha256':{p.relative_to(context).as_posix():sha(p.read_bytes()) for p in context.rglob('*') if p.is_file()}})
    save(FOLDER/'prepared.json',{'candidates':2,'deployed':False})
    print(json.dumps({'prepared':True,'deployed':False,'folder':str(FOLDER)}))


if __name__=='__main__':prepare()
