"""Independent frozen-candidate financial/workbook audit of downloaded outputs."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess

import deploy_ms_presentation_worker_release as release


def main():
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,required=True)
    args=p.parse_args();folder=args.folder.resolve();stage=folder/'registered-batch-v1'
    proof=release.read(stage,'render-qualified');frozen=folder/'local-runtime'
    manifest=release.read(folder/'context','MS_PRESENTATION_WORKER_RELEASE')
    for name in ('scripts/validate_subject_presentation.py','scripts/audit_alpha_ms_presentation.py',
            'src/ibkr_etl/ms_chart_workbooks.py','src/ibkr_etl/alpha_world_ms_presentation.py',
            'src/ibkr_etl/presentation_design.py'):
        if release.build.sha(frozen/name)!=manifest['installed_files_sha256'][name]:raise ValueError('Frozen auditor changed')
    output=stage/'independent-audit-v1';output.mkdir(exist_ok=False)
    env={**os.environ,'PYTHONPATH':'src;scripts','PYTHONUTF8':'1'}
    source="import json,sys;from pathlib import Path;from validate_subject_presentation import validate_subject_presentation as v;print(json.dumps(v(Path(sys.argv[1]),Path(sys.argv[2]),subject_id='alpha-world-ms',period_end=sys.argv[3],expected_strategy='iStrategy: Alpha World MS')))"

    def check(row):
        period=row['period_end'];deck=stage/'outputs'/Path(row['presentation_blob']).name
        snapshot=release.build.ROOT/'.work/alpha-ms-presentation-hkd-20261007/inputs'/(period+'.json')
        if release.build.sha(deck)!=row['presentation_sha256']:raise ValueError('Downloaded deck changed')
        command=[str((release.build.ROOT/'.work/venv-test/Scripts/python.exe').resolve()),'-c',source,str(deck),str(snapshot),period]
        result=subprocess.run(command,cwd=frozen,env=env,capture_output=True,text=True,encoding='utf-8',timeout=180)
        if result.returncode:raise ValueError('Independent financial audit failed: '+period)
        audit=json.loads(result.stdout)
        if (audit['output_sha256']!=row['presentation_sha256'] or audit['snapshot_sha256']!=row['data_sha256']
            or audit['ms_financial_audit']['financial_charts_verified']!=6
            or audit['ms_financial_audit']['risk_metrics_verified']!=7
            or audit['ms_chart_workbook_audit']['embedded_workbooks_verified']!=6):
            raise ValueError('Independent output-bound coverage differs: '+period)
        release.build.save(output,period,{'period_end':period,'validation':audit})
        return {'period_end':period,'presentation_sha256':row['presentation_sha256'],'snapshot_sha256':row['data_sha256'],
                'financial_charts':6,'risk_metrics':7,'editable_workbooks':6}

    items=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in as_completed([pool.submit(check,r) for r in proof['proof']['artifacts']]):
            item=result.result();items.append(item);print(json.dumps({'period':item['period_end'],'independent_audit':'passed'}),flush=True)
    if len(items)!=21:raise ValueError('Incomplete audit')
    record={'status':'passed','months':21,'financial_charts':126,'risk_metrics':147,'editable_workbooks':126,
            'render_qualified_sha256':release.build.sha(stage/'render-qualified.json'),'items':sorted(items,key=lambda r:r['period_end']),
            'source_data_changed':False,'database_writes':0,'native_microsoft_powerpoint_tested':False}
    release.build.save(output,'receipt',record)
    print(json.dumps({k:record[k] for k in ('status','months','financial_charts','risk_metrics','editable_workbooks')}),flush=True)


if __name__=='__main__':main()
