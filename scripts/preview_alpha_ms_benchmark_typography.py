"""Private header-typography preview, preserving inputs and financial evidence."""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.monthly_artifacts import render_factsheet
from ibkr_etl.alpha_world_ms_pdf_validation import validate_benchmark_typography
from pypdf import PdfReader


def main():
    source=ROOT/'.work/alpha-ms-hkd-hibor-20261007/hkd-inputs/2026-09-30.json'
    raw=source.read_bytes();snapshot=json.loads(raw)
    original=ROOT/'output/pdf/AlphaWorld_MS_Allocation_Layout_v14r9'
    prior=json.loads((original/'manifest.json').read_bytes())
    expected=next(r['data_sha256'] for r in prior['artifacts'] if r['period_end']=='2026-09-30')
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Pinned financial input changed')
    capture=ROOT/'.work/alpha-ms-factsheets-v13-20261007'
    authority=json.loads((capture/'capture.json').read_bytes())
    for asset in authority['assets']:
        if hashlib.sha256((capture/(asset['asset_role']+'.pdf')).read_bytes()).hexdigest()!=asset['sha256']:
            raise ValueError('Approved design source changed')
    folder=ROOT/'output/pdf/AlphaWorld_MS_Header_Typography_v14r10'
    folder.mkdir(exist_ok=False)
    target=folder/'AlphaWorld_MS_Factsheet_2026-09-30.pdf'
    before=json.dumps(snapshot,sort_keys=True,allow_nan=False)
    result=render_factsheet(snapshot,template_path=capture/'template.pdf',
        design_model_path=capture/'completed_example.pdf',output_path=target)
    if source.read_bytes()!=raw or json.dumps(snapshot,sort_keys=True,allow_nan=False)!=before:
        raise ValueError('Financial source mutated')
    old=PdfReader(original/target.name);new=PdfReader(target)
    for key in ('/MSMetrics','/MSCalculationVersion','/MSCalculationEvidence'):
        if old.metadata[key]!=new.metadata[key]:raise ValueError('Financial metadata changed: '+key)
    typography=validate_benchmark_typography(new.pages[0])
    record={'status':'private-header-review-not-deployed','pdf':str(target),
        'pdf_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
        'source_sha256':expected,'source_unchanged':True,'calculations_unchanged':True,
        'typography':typography,'render':result,'reports_released':False,'azure_changed':False}
    (folder/'manifest.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps({'status':record['status'],'typography':typography,'pdf':str(target)}))


if __name__=='__main__':main()
