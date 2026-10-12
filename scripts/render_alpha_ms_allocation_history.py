"""Create-only HKD historical layout replacements; never release or edit inputs."""
import hashlib
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.monthly_artifacts import render_factsheet, factsheet_layout_version
from pypdf import PdfReader


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--header-typography',action='store_true')
    args=parser.parse_args()
    original=ROOT/('output/pdf/AlphaWorld_MS_Allocation_Layout_v14r9' if args.header_typography
        else 'output/pdf/AlphaWorld_MS_HKD_HIBOR_Validation_v14r8')
    inputs=ROOT/'.work/alpha-ms-hkd-hibor-20261007/hkd-inputs'
    capture=ROOT/'.work/alpha-ms-factsheets-v13-20261007'
    prior=json.loads((original/'manifest.json').read_bytes())
    authority=json.loads((capture/'capture.json').read_bytes())
    if len(prior['artifacts'])!=21:raise ValueError('Unexpected historical scope')
    for asset in authority['assets']:
        if hashlib.sha256((capture/(asset['asset_role']+'.pdf')).read_bytes()).hexdigest()!=asset['sha256']:
            raise ValueError('Design authority changed')
    output=ROOT/('output/pdf/AlphaWorld_MS_Header_Typography_History_v14r10' if args.header_typography
        else 'output/pdf/AlphaWorld_MS_Allocation_Layout_v14r9')
    output.mkdir(exist_ok=False)
    results=[]
    for row in prior['artifacts']:
        day=row['period_end'];raw=(inputs/(day+'.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['data_sha256']:raise ValueError('Pinned input changed')
        snapshot=json.loads(raw);before=json.dumps(snapshot,sort_keys=True,allow_nan=False)
        target=output/('AlphaWorld_MS_Factsheet_'+day+'.pdf')
        rendered=render_factsheet(snapshot,template_path=capture/'template.pdf',
            design_model_path=capture/'completed_example.pdf',output_path=target)
        if before!=json.dumps(snapshot,sort_keys=True,allow_nan=False):raise ValueError('Input mutated')
        old=PdfReader(original/target.name);new=PdfReader(target)
        if json.loads(old.metadata['/MSMetrics'])!=json.loads(new.metadata['/MSMetrics']):
            raise ValueError('Financial metrics changed')
        if args.header_typography and any(old.metadata[key]!=new.metadata[key]
                for key in ('/MSCalculationEvidence','/MSCalculationVersion')):
            raise ValueError('Financial methodology evidence changed')
        results.append({'period_end':day,'data_sha256':row['data_sha256'],'pdf':str(target),
            'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'render':rendered})
        print(json.dumps({'period':day,'status':'private_layout_rendered'}),flush=True)
    manifest={'count':len(results),'layout_version':factsheet_layout_version('alpha-world-ms'),
        'first_month':'2025-01-31','last_month':'2026-09-30','artifacts':results,
        'calculations_and_wording_changed':False,'reports_released':False,'source_data_changed':False,
        'status':'private-historical-layout-batch-not-published'}
    with (output/'manifest.json').open('x',encoding='utf-8') as f:json.dump(manifest,f,indent=2)
    print(json.dumps({'status':manifest['status'],'count':len(results),'folder':str(output)}))


if __name__=='__main__':main()
