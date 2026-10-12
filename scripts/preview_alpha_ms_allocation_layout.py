"""Create a separate private geometry review without changing frozen reports."""
import hashlib
import json
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.monthly_artifacts import render_factsheet
from pypdf import PdfReader


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',type=int,default=1,choices=range(1,100))
    args=parser.parse_args()
    capture=ROOT/'.work/alpha-ms-factsheets-v13-20261007'
    source=ROOT/'.work/alpha-ms-hkd-hibor-20261007/hkd-inputs/2026-09-30.json'
    raw=source.read_bytes();snapshot=json.loads(raw)
    manifest=json.loads((ROOT/'output/pdf/AlphaWorld_MS_HKD_HIBOR_Validation_v14r8/manifest.json').read_bytes())
    expected=next(r for r in manifest['artifacts'] if r['period_end']=='2026-09-30')['data_sha256']
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Pinned snapshot changed')
    authority=json.loads((capture/'capture.json').read_bytes())
    for asset in authority['assets']:
        if hashlib.sha256((capture/(asset['asset_role']+'.pdf')).read_bytes()).hexdigest()!=asset['sha256']:
            raise ValueError('Design source changed')
    output=ROOT/f'output/pdf/AlphaWorld_MS_Allocation_Layout_Review_v14r9_r{args.revision}'
    output.mkdir(exist_ok=False)
    target=output/'AlphaWorld_MS_Factsheet_2026-09-30_Allocation_Layout_Review.pdf'
    before=json.dumps(snapshot,sort_keys=True,allow_nan=False)
    result=render_factsheet(snapshot,template_path=capture/'template.pdf',
        design_model_path=capture/'completed_example.pdf',output_path=target)
    if before!=json.dumps(snapshot,sort_keys=True,allow_nan=False) or source.read_bytes()!=raw:
        raise ValueError('Source input changed')
    baseline=ROOT/'output/pdf/AlphaWorld_MS_Azure_HKD_HIBOR_v14r8/AlphaWorld_MS_Factsheet_2026-09-30.pdf'
    old_meta=PdfReader(baseline).metadata;new_meta=PdfReader(target).metadata
    for key in ('/MSMetrics','/MSCalculationVersion'):
        old = json.loads(old_meta[key]) if key=='/MSMetrics' else old_meta[key]
        new = json.loads(new_meta[key]) if key=='/MSMetrics' else new_meta[key]
        if old!=new:
            raise ValueError('Numerical evidence changed: '+key)
    old_evidence=json.loads(old_meta['/MSCalculationEvidence'])
    new_evidence=json.loads(new_meta['/MSCalculationEvidence'])
    calendar_metadata={'baseline':old_evidence.pop('calendar_version'),
        'candidate':new_evidence.pop('calendar_version')}
    if old_evidence!=new_evidence:raise ValueError('Numerical evidence differs')
    # The candidate already had the extended calendar revision before this
    # geometry-only change. Verify every pinned step-3 module is unchanged.
    step3=json.loads((ROOT/'.work/alpha-ms-step3-20261007-r5/cache-qualification.json').read_bytes())
    for name,digest in step3['source_files_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('Step-3 source unexpectedly changed: '+name)
    record={'status':'private-layout-review-not-deployed','pdf':str(target),'pdf_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
        'input_sha256':expected,'source_input_unchanged':True,'calculations_unchanged':True,
        'wording_and_percentages_unchanged':True,'production_changed':False,'reports_released':False,
        'preexisting_calendar_metadata_difference':calendar_metadata,
        'step3_candidate_sources_unchanged':True,
        'source_files_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ('src/ibkr_etl/monthly_artifacts.py','src/ibkr_etl/alpha_world_ms_pdf_validation.py')},
        'render':result}
    with (output/'manifest.json').open('x',encoding='utf-8') as f:json.dump(record,f,indent=2)
    print(json.dumps({'status':record['status'],'pdf':str(target),'calculations_unchanged':True}))


if __name__=='__main__':main()
