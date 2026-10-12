"""Private all-month render/package audit. Never writes to Azure or source data.

Visual inspection remains a separate gate, explicitly pending in the receipt.
Uses the existing approved renderer and read-only slide-preview exporter.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess

from audit_alpha_ms_presentation import audit

ROOT=Path(__file__).resolve().parents[1]
TEMPLATE_SHA='4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191'
BASELINE_WARNINGS={('object_outside_slide_boundary',slide,identity)
    for slide,identity in ((1,'125'),(1,'2'),(1,'121'),(2,'37'),(20,'5'),(20,'7'),(20,'11'))}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_record(path, value):
    with path.open('x',encoding='utf-8') as handle:
        json.dump(value,handle,indent=2,allow_nan=False)


def matching_finalizer(output):
    digest=sha(output)
    matches=[]
    for folder in (ROOT/'.work/presentation-validation').glob(output.stem+'-*'):
        receipt=folder/(output.name+'.validation.json')
        if receipt.exists():
            value=json.loads(receipt.read_bytes())
            if value.get('finalSha256')==digest:
                matches.append((receipt,value))
    if not matches:
        raise ValueError('Missing exact finalizer receipt: '+output.name)
    receipt,value=sorted(matches,key=lambda pair:str(pair[0]))[-1]
    for gate in ('packageIntegrity','presentationLayout'):
        if value[gate].get('finding_count')!=0 or value[gate].get('exitCode')!=0:
            raise ValueError('Finalizer gate did not pass: '+gate)
    if value['packageIntegrity']['slide_count']!=20:
        raise ValueError('Twenty approved slides are required')
    warnings={(w['kind'],w.get('slide'),w.get('object_id')) for w in value['presentationLayout'].get('warnings',[])}
    if warnings-BASELINE_WARNINGS:
        raise ValueError('New layout warnings require review: '+str(warnings-BASELINE_WARNINGS))
    return receipt,value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node',type=Path,required=True)
    parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--workers',type=int,choices=(1,2),default=2)
    args=parser.parse_args()
    args.folder=args.folder.resolve()
    args.folder.relative_to(ROOT/'.work')
    inputs=ROOT/'.work/alpha-ms-presentation-hkd-20261007/inputs'
    proof=json.loads((inputs.parent/'inputs-qualified.json').read_bytes())['record']['proof']
    items=proof['items']
    if len(items)!=21 or len({item['period_end'] for item in items})!=21 or proof['status']!='passed':
        raise ValueError('The previously qualified 21-month input proof is required')
    template=ROOT/'output/pptx/U16088571_Monthly_Presentation_2026-08-31_Enhanced_v24.pptx'
    if sha(template)!=TEMPLATE_SHA:
        raise ValueError('Approved layout source changed')
    for item in items:
        if sha(inputs/(item['period_end']+'.json'))!=item['sha256']:
            raise ValueError('Frozen input changed: '+item['period_end'])
    binding={'template_sha256':TEMPLATE_SHA,'items':items,
        'renderer_sha256':sha(ROOT/'scripts/render_monthly_presentation.mjs'),
        'model_sha256':sha(ROOT/'src/ibkr_etl/alpha_world_ms_presentation.py'),
        'content_expectations_sha256':sha(ROOT/'src/ibkr_etl/presentation_design.py'),
        'audit_sha256':sha(ROOT/'scripts/audit_alpha_ms_presentation.py')}
    binding['generic_validator_sha256']=sha(ROOT/'scripts/validate_monthly_presentation.py')
    args.folder.mkdir(exist_ok=True)
    for name in ('output','previews','receipts','logs'):
        (args.folder/name).mkdir(exist_ok=True)
    bound=args.folder/'input-and-code-binding.json'
    if bound.exists():
        if json.loads(bound.read_bytes())!=binding:
            raise ValueError('Existing batch binds different code or inputs; use a new revision')
    else:
        create_record(bound,binding)
    environment=dict(os.environ)
    if not all(environment.get(k) for k in ('SKILL_DIR','RUNTIME_PYTHON','RUNTIME_NODE_MODULES')):
        raise ValueError('Configured bundled finalizer runtime is required')
    environment['PRESENTATION_STRATEGY_NAME']='iStrategy: Alpha World MS'

    def qualify(item):
        period=item['period_end'];snapshot=inputs/(period+'.json')
        output=args.folder/'output'/f'AlphaWorld_MS_Presentation_{period}_HKD_Review.pptx'
        receipt=args.folder/'receipts'/(period+'.json')
        if receipt.exists():
            saved=json.loads(receipt.read_bytes())
            if saved['input_sha256']!=sha(snapshot) or saved['output_sha256']!=sha(output):
                raise ValueError('Previously qualified local output changed: '+period)
            return saved
        if not output.exists():
            result=subprocess.run([str(args.node),str(ROOT/'scripts/render_monthly_presentation.mjs'),
                str(template),str(snapshot),str(output)],cwd=ROOT,env=environment,
                timeout=300,capture_output=True,text=True)
            create_record(args.folder/'logs'/(period+'.render.json'),
                {'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
            if result.returncode:
                raise ValueError('Render or finalization failed: '+period)
        financial=audit(output,snapshot)
        finalizer,checks=matching_finalizer(output)
        preview=args.folder/'previews'/period
        if not preview.exists():
            result=subprocess.run([str(args.node),str(ROOT/'scripts/inspect_alpha_ms_presentation.mjs'),
                str(output),str(preview)],cwd=ROOT,env=environment,
                timeout=300,capture_output=True,text=True)
            create_record(args.folder/'logs'/(period+'.preview.json'),
                {'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
            if result.returncode:
                raise ValueError('Final slide preview export failed: '+period)
        if len(list(preview.glob('slide-*.png')))!=20 or len(list(preview.glob('slide-*.layout.json')))!=20:
            raise ValueError('Incomplete final slide previews: '+period)
        if sha(snapshot)!=item['sha256']:
            raise ValueError('Frozen input changed during rendering')
        value={'period_end':period,'input_sha256':sha(snapshot),'output_sha256':sha(output),
            'output_path':str(output),'financial':financial,'finalizer_receipt':str(finalizer),
            'layout_warnings':checks['presentationLayout'].get('warnings',[]),
            'preview_folder':str(preview),'preview_slides':20,'visual_acceptance':'pending',
            'azure_changed':False,'source_data_changed':False,'reports_released':False}
        create_record(receipt,value)
        return value

    results=[];errors=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(qualify,item):item['period_end'] for item in items}
        for future in as_completed(futures):
            period=futures[future]
            try:
                value=future.result();results.append(value)
                print(json.dumps({'period_end':period,'status':'financial_package_passed',
                    'completed':len(results),'visual_acceptance':'pending'}),flush=True)
            except Exception as error:
                errors.append({'period_end':period,'error':str(error)})
                print(json.dumps({'period_end':period,'status':'failed','error':str(error)}),flush=True)
    if sha(template)!=TEMPLATE_SHA or any(sha(inputs/(i['period_end']+'.json'))!=i['sha256'] for i in items):
        raise ValueError('Source/template integrity changed during the batch')
    for field,source in (('renderer_sha256','scripts/render_monthly_presentation.mjs'),
                         ('model_sha256','src/ibkr_etl/alpha_world_ms_presentation.py'),
                         ('content_expectations_sha256','src/ibkr_etl/presentation_design.py'),
                         ('audit_sha256','scripts/audit_alpha_ms_presentation.py'),
                         ('generic_validator_sha256','scripts/validate_monthly_presentation.py')):
        if sha(ROOT/source)!=binding[field]:
            raise ValueError('Qualification code changed during the batch')
    # This deliberately does not certify all-slide visual or Azure runtime acceptance.
    summary={'status':'financial_package_passed' if not errors else 'incomplete',
        'months_verified':len(results),'slides_exported':sum(r['preview_slides'] for r in results),
        'native_charts_verified':sum(r['financial']['financial_charts_verified'] for r in results),
        'risk_metrics_verified':sum(r['financial']['risk_metrics_verified'] for r in results),
        'errors':errors,'items':sorted(results,key=lambda r:r['period_end']),
        'visual_acceptance':'pending','azure_changed':False,'source_data_changed':False,'reports_released':False}
    summary_path=args.folder/'batch-financial-package-receipt.json'
    if summary_path.exists():
        if json.loads(summary_path.read_bytes())!=summary:
            raise ValueError('Existing summary differs; preserve it and use a new receipt filename')
    else:
        create_record(summary_path,summary)
    print(json.dumps({k:v for k,v in summary.items() if k not in ('items',)}),flush=True)
    return int(bool(errors))


if __name__=='__main__':
    raise SystemExit(main())
