"""Private, resumable 21-month T presentation qualification; no cloud writes."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from prepare_alpha_t_private_presentation import prepare, HISTORY, COMPLETION, ROOT
from audit_alpha_t_private_presentation import audit
from qualify_alpha_ms_presentation_months import matching_finalizer


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', type=Path, required=True)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--period', help='Qualify one month before the full batch')
    parser.add_argument('--qualified-inputs',type=Path,help='Previously verified, source-bound T crisis snapshots')
    parser.add_argument('--internal-renderer',action='store_true',help='Qualify the internal-release renderer, without releasing reports')
    parser.add_argument('--workers', type=int, choices=(1,2), default=2)
    args=parser.parse_args()
    if args.internal_renderer and not args.qualified_inputs:raise ValueError('Internal renderer requires qualified frozen inputs')
    folder=args.folder.resolve()
    folder.relative_to(ROOT/'.work')
    template=ROOT/'output/pptx/U16088571_Monthly_Presentation_2026-08-31_Enhanced_v24.pptx'
    if sha(template)!='4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191':
        raise ValueError('Approved template changed')
    periods=sorted(p.stem for p in (HISTORY/'snapshots').glob('*.json'))
    if len(periods)!=21 or periods[0]!='2025-01-31' or periods[-1]!='2026-09-30':
        raise ValueError('Expected 21 historical months')
    files=[template,
           ROOT/'src/ibkr_etl/alpha_world_t_presentation.py',ROOT/'src/ibkr_etl/alpha_world_t_crisis.py',
           *(ROOT/'scripts'/n for n in ('prepare_alpha_t_private_presentation.py',
             'render_alpha_t_private_presentation.mjs','render_alpha_t_presentation.mjs','alpha_t_presentation_runtime.mjs','render_monthly_presentation.mjs',
             'audit_alpha_t_private_presentation.py','audit_alpha_ms_presentation.py',
             'inspect_alpha_ms_presentation.mjs','qualify_alpha_t_presentation_months.py')),
           *(HISTORY/'snapshots'/f'{p}.json' for p in periods),
           *(COMPLETION/'extended-risk-v2'/f'{p}.json' for p in periods),
           COMPLETION/'hkab-history-verification.json',COMPLETION/'fresh-cash-reconciliation-2.json',
           HISTORY/'qualification.sqlite']
    if args.qualified_inputs:
        args.qualified_inputs=args.qualified_inputs.resolve()
        args.qualified_inputs.relative_to(ROOT/'.work')
        files += [args.qualified_inputs/f'{p}.json' for p in periods]
    binding={str(p.relative_to(ROOT)):sha(p) for p in files}
    folder.mkdir(exist_ok=True)
    for name in ('inputs','output','previews','logs','receipts'):
        (folder/name).mkdir(exist_ok=True)
    binding_path=folder/'source-code-binding.json'
    if binding_path.exists():
        if json.loads(binding_path.read_bytes())!=binding:
            raise ValueError('Batch source changed; use a new revision')
    else:save(binding_path,binding)
    env=dict(os.environ)
    for key in ('SKILL_DIR','RUNTIME_PYTHON','RUNTIME_NODE_MODULES'):
        if not env.get(key):raise ValueError(key+' required')
    env['PRESENTATION_STRATEGY_NAME']='iStrategy: Alpha World T'
    def run(command, log):
        result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,timeout=600)
        save(log,{'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
        if result.returncode:raise ValueError('Command failed; inspect '+str(log))
    def qualify(period):
        source=folder/'inputs'/f'{period}.json'
        suffix='' if args.internal_renderer else '_Private'
        output=folder/'output'/f'AlphaWorld_T_Presentation_{period}{suffix}.pptx'
        receipt=folder/'receipts'/f'{period}.json'
        if receipt.exists():
            value=json.loads(receipt.read_bytes())
            if value['input_sha256']!=sha(source) or value['output_sha256']!=sha(output):
                raise ValueError('Existing qualified output changed')
            return value
        if not source.exists():
            if args.qualified_inputs:
                value=json.loads((args.qualified_inputs/f'{period}.json').read_bytes())
                from ibkr_etl.alpha_world_t_presentation import build_model
                if not build_model(value)['crisis_available']:raise ValueError('Qualified T crisis replay required')
                save(source,value)
            else:prepare(period,source,extended_risk=True)
        if not output.exists():
            renderer='scripts/render_alpha_t_presentation.mjs' if args.internal_renderer else 'scripts/render_alpha_t_private_presentation.mjs'
            run([str(args.node),renderer,str(template),str(source),str(output)],
                folder/'logs'/f'{period}.render.json')
        financial=audit(output,source)
        if args.internal_renderer:
            from validate_subject_presentation import validate_subject_presentation
            shared_gate=validate_subject_presentation(output,source,subject_id='alpha-world-t',period_end=period,
                expected_strategy='iStrategy: Alpha World T')
            if shared_gate['status']!='passed':raise ValueError('Shared T report release validator did not pass')
        finalizer,checks=matching_finalizer(output)
        previews=folder/'previews'/period
        if not previews.exists():
            run([str(args.node),'scripts/inspect_alpha_ms_presentation.mjs',str(output),str(previews)],
                folder/'logs'/f'{period}.preview.json')
        if len(list(previews.glob('slide-*.png')))!=20 or len(list(previews.glob('slide-*.layout.json')))!=20:
            raise ValueError('Incomplete slide previews')
        result={'period_end':period,'input_sha256':sha(source),'output_sha256':sha(output),
                'financial':financial,'finalizer_receipt':str(finalizer),
                'layout_warnings':checks['presentationLayout'].get('warnings',[]),
                'output_path':str(output),'preview_folder':str(previews),'preview_slides':20,
                'visual_acceptance':'pending','powerpoint_application_acceptance':'pending',
                'publication_allowed':False,'azure_changed':False}
        save(receipt,result)
        return result
    selected=[args.period] if args.period else periods
    if any(p not in periods for p in selected):raise ValueError('Unknown period')
    results=[];errors=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs={pool.submit(qualify,p):p for p in selected}
        for job in as_completed(jobs):
            period=jobs[job]
            try:
                results.append(job.result())
                print(json.dumps({'period':period,'status':'financial_package_passed','completed':len(results)}),flush=True)
            except Exception as exc:
                errors.append({'period':period,'error':str(exc)})
                print(json.dumps(errors[-1]),flush=True)
    if any(sha(p)!=binding[str(p.relative_to(ROOT))] for p in files):
        raise ValueError('Source or code changed during qualification')
    summary={'status':'financial_package_passed' if not errors else 'incomplete','months':len(results),
             'slides':20*len(results),'errors':errors,'items':sorted(results,key=lambda r:r['period_end']),
             'publication_allowed':False,'azure_changed':False,'visual_acceptance':'pending'}
    summary_path=folder/('summary-'+(args.period or 'all')+'.json')
    if not summary_path.exists():save(summary_path,summary)
    print(json.dumps({k:v for k,v in summary.items() if k!='items'}),flush=True)
    return int(bool(errors))


if __name__=='__main__':raise SystemExit(main())
