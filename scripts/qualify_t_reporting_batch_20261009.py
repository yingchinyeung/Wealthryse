"""Private all-month consumer assembly and unchanged-pixel factsheet replay."""
import hashlib,json,sqlite3,subprocess
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image,ImageChops
from pypdf import PdfReader
from ibkr_etl.alpha_world_t_reporting import enrich
from ibkr_etl.alpha_world_t_presentation import build_model
from ibkr_etl.reporting_snapshot import _collect_tool_results,assemble_month_end_payload
from ibkr_etl.monthly_artifacts import render_factsheet
from qualify_alpha_world_t_layout_v5 import raster

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-reporting-batch-20261009-v2'
HISTORY=ROOT/'.work/alpha-world-t-onboarding-20261008/history-v1'
QUALIFIED=ROOT/'.work/alpha-t-internal-crisis-20261009-v1'
PRIOR=ROOT/'output/pdf/AlphaWorld_T_Factsheets_Layout_v7'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,indent=2,allow_nan=False)

def main():
    FOLDER.mkdir(exist_ok=False)
    for name in ('inputs','pdf','raster','contacts'):(FOLDER/name).mkdir()
    with sqlite3.connect((HISTORY/'qualification.sqlite').as_uri()+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row
        components=[dict(r) for r in c.execute("SELECT * FROM synthetic_portfolio_components WHERE portfolio_id='alpha-world-t' ORDER BY snapshot_date,component_id")]
    periods=sorted(p.stem for p in (HISTORY/'snapshots').glob('*.json'))
    if len(periods)!=21:raise ValueError('Expected 21 months')
    results=[]
    for period in periods:
        core=json.loads((HISTORY/'snapshots'/f'{period}.json').read_bytes())
        old=json.loads((QUALIFIED/f'{period}.json').read_bytes())
        enriched=enrich(core,[r for r in components if r['snapshot_date']<=core['data_as_of']],old['risk']['crisis_replay'])
        with patch('ibkr_etl.alpha_world_t_reporting.load',return_value=enriched):
            tools=_collect_tool_results(Mock(),tenant_id='tenant',subject_id='alpha-world-t',period_end=period,reporting_currency='HKD')
        snapshot,quality,allowed,blockers=assemble_month_end_payload(subject_id='alpha-world-t',
            period_start='2025-01-20',period_end=period,reporting_currency='HKD',tool_results=tools)
        if quality!='warn' or allowed or 'risk:crisis_replay_unavailable' in blockers:
            raise ValueError('Internal release boundary differs')
        if build_model(snapshot)!=build_model(old):raise ValueError('Presentation model differs '+period)
        source=FOLDER/'inputs'/f'{period}.json';save(source,snapshot)
        name=f'AlphaWorld_T_Factsheet_{period}.pdf';target=FOLDER/'pdf'/name
        result=render_factsheet(snapshot,template_path=ROOT/'templates/alpha-world-t/owner_template_20261008.pdf',output_path=target)
        before,after=PdfReader(PRIOR/name),PdfReader(target)
        for key in ('/TMetrics','/TCalculationEvidence','/TMethodologyIdentity','/PublicationAllowed','/SyntheticData'):
            if before.metadata[key]!=after.metadata[key]:raise ValueError('Financial metadata differs '+key)
        tile=FOLDER/'raster'/period;tile.mkdir()
        raster(PRIOR/name,tile/'old');raster(target,tile/'new')
        for page in (1,2):
            with Image.open(tile/f'old-{page}.png') as a,Image.open(tile/f'new-{page}.png') as b:
                if a.size!=b.size or ImageChops.difference(a.convert('RGB'),b.convert('RGB')).getbbox():
                    raise ValueError(f'Visible factsheet changed {period} page {page}')
        results.append({'period_end':period,'snapshot_sha256':sha(source),'pdf_sha256':sha(target),
            'approved_pdf_sha256':sha(PRIOR/name),'pages_pixel_identical':2,
            'presentation_model_identical':True,'validation':result['pdf_accuracy_validation']})
        print(period+' PASS',flush=True)
    save(FOLDER/'verification.json',{'status':'private_qualified','months':21,'pages_pixel_identical':42,
        'actual_sql_collection_tested':False,'assembled_reporting_models_verified':21,
        'production_deployed':False,'viewer_access_activated':False,'results':results})
    print('ALL 21 PASS; PRIVATE EVIDENCE ONLY',flush=True)

if __name__=='__main__':main()
