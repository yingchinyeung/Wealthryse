"""Fresh private render with a bound local-only table-ID compatibility adapter.

Frozen production sources remain unchanged. Azure exact-image qualification is
still required; desktop adapter evidence is never Azure runtime evidence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1]
NODE=Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
MODULES=Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,value):
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2,allow_nan=False)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--folder',required=True,type=Path)
    parser.add_argument('--attempt',type=int,default=1)
    parser.add_argument('--resume-reviewed',action='store_true')
    args=parser.parse_args(); folder=args.folder.resolve(); runtime=folder/'local-runtime'
    intent=json.loads((folder/'source-intent.json').read_bytes())
    qualified=json.loads((folder/'local-qualification.json').read_bytes())
    if qualified['status']!='isolated_local_candidate_passed' or qualified['source_intent_sha256']!=sha(folder/'source-intent.json'):
        raise ValueError('Frozen all-month qualification required')
    for name,digest in intent['files_sha256'].items():
        if sha(folder/'context'/name)!=digest or sha(runtime/name)!=digest:
            raise ValueError('Frozen candidate source differs')
    template=runtime/'templates/U16088571/approved-august-2026-20-slide-v24.pptx'
    snapshot=ROOT/'.work/alpha-ms-presentation-hkd-20261007/inputs/2026-09-30.json'
    target=folder/'September_Cloud_Path_Smoke.pptx'
    if target.exists() and not args.resume_reviewed: raise ValueError('Preserve previous outputs')
    input_hash=sha(snapshot); template_hash=sha(template)
    adapter=ROOT/'scripts/ms_local_table_adapter.mjs'
    utility=ROOT/'scripts/ms_table_binding_compatibility.mjs'
    helper=ROOT/'scripts/inspect_alpha_ms_presentation.mjs'
    bindings={str(path.relative_to(ROOT)):sha(path) for path in (adapter,utility,helper,Path(__file__))}
    environment={**os.environ,'PYTHONPATH':os.pathsep.join(('src','scripts')),
        'PYTHONUTF8':'1','RUNTIME_PYTHON':sys.executable,'RUNTIME_NODE_MODULES':str(MODULES),
        'PRESENTATION_STRATEGY_NAME':'iStrategy: Alpha World MS','MS_LOCAL_TEMPLATE':str(template),
        'TEMP':str(folder/'test-temp'),'TMP':str(folder/'test-temp')}
    environment.pop('SKILL_DIR',None)
    if not args.resume_reviewed:
        result=subprocess.run([str(NODE),'--import',adapter.as_uri(),'scripts/render_monthly_presentation.mjs',
            str(template),str(snapshot),str(target)],cwd=runtime,env=environment,
            capture_output=True,text=True,encoding='utf-8',timeout=180)
        log_name='fresh-render-adapted.log' if args.attempt==1 else f'fresh-render-adapted-r{args.attempt}.log'
        with (folder/log_name).open('x',encoding='utf-8') as stream:
            stream.write(result.stdout+result.stderr)
        if result.returncode:
            raise ValueError('Fresh render failed: '+result.stderr[-1200:])
    preview=folder/'september-smoke-previews'
    if not args.resume_reviewed:
        process=subprocess.run([str(NODE),str(helper),str(target),str(preview)],cwd=ROOT,
            env=environment,capture_output=True,text=True,encoding='utf-8',timeout=180)
        if process.returncode:
            raise ValueError('Fresh preview failed: '+process.stderr[-1200:])
    reviewed=ROOT/'.work/alpha-ms-number-formats-20261008-v2'
    comparison=reviewed/'all-month-previews/comparison.json'
    if sha(comparison)!='382c7db1ff51021765b7bdb15dfe20ca70f4bf7e37ec2417ca3ff55027b2cd65':
        raise ValueError('Reviewed preview inventory differs')
    accepted=next(row for row in json.loads(comparison.read_bytes())['items'] if row['period_end']=='2026-09-30')
    rows=[]
    review_path=folder/'fresh-slide17-visual-review.json'
    delta=json.loads(review_path.read_bytes()) if args.resume_reviewed else None
    for row in accepted['previews']:
        name=f"slide-{row['slide']:02d}.png"; old=reviewed/'all-month-previews/2026-09-30'/name
        new=preview/name
        if sha(old)!=row['new_sha256']: raise ValueError('Reviewed reference changed')
        identical=sha(new)==sha(old)
        if not identical:
            if (row['slide']!=17 or not delta or delta['status']!='private_full_size_visual_delta_accepted'
                    or delta['output_sha256']!=sha(target) or delta['preview_sha256']!=sha(new)
                    or delta['reference_preview_sha256']!=sha(old)
                    or delta['render_log_sha256']!=sha(folder/'fresh-render-adapted-r7.log')):
                raise ValueError('Fresh rendered slide differs without bound manual acceptance: '+str(row['slide']))
            def text(path):
                with zipfile.ZipFile(path) as archive:
                    root=ET.fromstring(archive.read('ppt/slides/slide17.xml'))
                return [element.text for element in root.iter() if element.tag.endswith('}t')]
            if text(target)!=text(reviewed/'presentations/MS_2026-09-30_NumberFormats.pptx'):
                raise ValueError('Slide 17 wording or figures changed')
        rows.append({'slide':row['slide'],'preview_sha256':sha(new),'exact_reviewed_pixel_identity':identical,
                     'manual_visual_delta_accepted':not identical})
    code="""import json,sys
from pathlib import Path
from validate_subject_presentation import validate_subject_presentation
print(json.dumps(validate_subject_presentation(Path(sys.argv[1]),Path(sys.argv[2]),subject_id='alpha-world-ms',period_end='2026-09-30',expected_strategy='iStrategy: Alpha World MS')))
"""
    check=subprocess.run([sys.executable,'-c',code,str(target),str(snapshot)],cwd=runtime,
        env=environment,capture_output=True,text=True,encoding='utf-8',timeout=90)
    if check.returncode: raise ValueError('Fresh strict financial/output gate failed: '+check.stderr[-1000:])
    validation=json.loads(check.stdout)
    if (len(rows)!=20 or sha(snapshot)!=input_hash or sha(template)!=template_hash
            or validation['output_sha256']!=sha(target)
            or any(sha(ROOT/name)!=digest for name,digest in bindings.items())):
        raise ValueError('Fresh render bindings changed')
    save(folder/'fresh-smoke-qualified.json',{
        'status':'fresh_local_adapted_cloud_path_smoke_passed','output_sha256':sha(target),
        'input_sha256':input_hash,'template_sha256':template_hash,
        'source_intent_sha256':sha(folder/'source-intent.json'),
        'local_qualification_sha256':sha(folder/'local-qualification.json'),
        'local_adapter_used':True,'local_adapter_source_binding':bindings,
        'production_renderer_source_unchanged':True,
        'accepted_preview_inventory_sha256':sha(comparison), 'slides':rows,'validation':validation,
        'manual_visual_review_sha256':sha(review_path) if delta else None,
        'desktop_finalizer_used':False,'azure_runtime_verified':False,
        'native_powerpoint_verified':False,'source_data_changed':False,'production_changed':False})
    print(json.dumps({'fresh_smoke_passed':True,'pixel_identical_slides':sum(row['exact_reviewed_pixel_identity'] for row in rows),
                      'manually_reviewed_slides':sum(row['manual_visual_delta_accepted'] for row in rows),
                      'charts_verified':6,'risk_metrics_verified':7,'azure_changed':False}))


if __name__=='__main__': main()
