"""All-month local MS copy qualification; no Azure, source or approval writes."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

from PIL import Image,ImageChops
from pypdf import PdfReader
from ibkr_etl.monthly_artifacts import render_factsheet,factsheet_layout_version
from ibkr_etl.alpha_world_ms_copy import validate_copy,COPY_SHA256,FONT_SHA256
from ibkr_etl.alpha_world_ms_pdf_validation import validate as validate_metrics

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'output/pdf/AlphaWorld_MS_Disclaimer_v14r16'
BASE=ROOT/'output/pdf/AlphaWorld_MS_Azure_Headers_v14r15'
QA=ROOT/'tmp/pdfs/alpha-ms-disclaimer-v14r16'


def sha(raw):return hashlib.sha256(raw).hexdigest()


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--pdftoppm',required=True);args=parser.parse_args()
    QA.mkdir(parents=True,exist_ok=False)
    OUTPUT.mkdir(parents=True,exist_ok=True)
    capture=ROOT/'.work/alpha-ms-factsheets-v13-20261007'
    assets=json.loads((capture/'capture.json').read_bytes())['assets']
    inputs=sorted((ROOT/'.work/alpha-ms-hkd-hibor-20261007/hkd-inputs').glob('*.json'))
    if len(inputs)!=21:raise ValueError('Expected 21 frozen inputs')
    originals={str(p):sha(p.read_bytes()) for p in inputs}
    for asset in assets:
        p=capture/(asset['asset_role']+'.pdf')
        if sha(p.read_bytes())!=asset['sha256']:raise ValueError('Source authority changed')
        originals[str(p)]=asset['sha256']
    rows=[]
    for path in inputs:
        snapshot=json.loads(path.read_bytes());period=snapshot['period_end']
        name='AlphaWorld_MS_Factsheet_'+period+'.pdf';target=OUTPUT/name
        if not target.exists():
            render_factsheet(snapshot,template_path=capture/'template.pdf',design_model_path=capture/'completed_example.pdf',output_path=target)
        check=validate_copy(target);validate_metrics(target,snapshot)
        old,new=PdfReader(BASE/name),PdfReader(target)
        for key in ('/MSMetrics','/MSCalculationEvidence','/LayoutAuthoritySHA256','/CapitalPathBasis'):
            if new.metadata[key]!=old.metadata[key]:raise ValueError('Unrequested metadata changed: '+key)
        if new.metadata['/ChartStyleVersion']!=factsheet_layout_version('alpha-world-ms'):raise ValueError('Layout version differs')
        tile=QA/period;tile.mkdir()
        for label,pdf in (('before',BASE/name),('after',target)):
            subprocess.run([args.pdftoppm,'-r','100','-png',str(pdf),str(tile/label)],check=True,capture_output=True)
        for page in (1,2):
            before=Image.open(tile/f'before-{page}.png').convert('RGB')
            after=Image.open(tile/f'after-{page}.png').convert('RGB')
            if before.size!=after.size:raise ValueError('Page dimensions changed')
            diff=ImageChops.difference(before,after)
            if page==2:
                if diff.getbbox() is None:raise ValueError('Requested disclaimer did not change')
                scale=100/72.;height=float(new.pages[1].mediabox.height)
                diff.paste((0,0,0),(int(37*scale),int((height-528)*scale),int(566*scale)+1,int((height-95)*scale)+1))
            if diff.getbbox() is not None:raise ValueError(f'Unexpected pixel changes: {period} page {page} {diff.getbbox()}')
        rows.append({'period_end':period,'pdf_sha256':sha(target.read_bytes()),'input_sha256':originals[str(path)],
                     'copy_validation':check,'page_one_pixel_identical':True,'outside_disclaimer_pixel_identical':True,
                     'financial_metadata_unchanged':True})
        print(period+' passed',flush=True)
    if any(sha(Path(p).read_bytes())!=digest for p,digest in originals.items()):raise ValueError('An input was altered')
    manifest={'status':'local-qualified-not-deployed','count':len(rows),'layout_version':factsheet_layout_version('alpha-world-ms'),
        'copy_sha256':COPY_SHA256,'font_sha256':FONT_SHA256,'source_data_changed':False,'azure_changed':False,
        'typography':{'font':'Georgia','size':8.999790908,'leading':12,'paragraph_gap':6},'checks':rows}
    with (OUTPUT/'manifest.json').open('x',encoding='utf-8') as stream:json.dump(manifest,stream,indent=2)
    with zipfile.ZipFile(OUTPUT.with_suffix('.zip'),'x',compression=zipfile.ZIP_DEFLATED) as bundle:
        for p in sorted(OUTPUT.iterdir()):bundle.write(p,p.name)
    print('ALL 21 PASSED; LOCAL ONLY',flush=True)


if __name__=='__main__':main()
