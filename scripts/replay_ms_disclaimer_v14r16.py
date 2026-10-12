"""Replay all 21 months using the isolated candidate source, compare full pixels."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from PIL import Image,ImageChops
from pypdf import PdfReader
from ibkr_etl.monthly_artifacts import render_factsheet
from ibkr_etl.alpha_world_ms_copy import validate_copy
from ibkr_etl.alpha_world_ms_pdf_validation import validate

ROOT=Path(__file__).resolve().parents[1]
POPPLER=Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    capture=ROOT/'.work/alpha-ms-factsheets-v13-20261007';rows=[]
    accepted=ROOT/'output/pdf/AlphaWorld_MS_Disclaimer_v14r16'
    manifest=json.loads((accepted/'manifest.json').read_bytes())
    for row in manifest['checks']:
        period=row['period_end'];name='AlphaWorld_MS_Factsheet_'+period+'.pdf'
        source=ROOT/'.work/alpha-ms-hkd-hibor-20261007/hkd-inputs'/(period+'.json')
        assert hashlib.sha256(source.read_bytes()).hexdigest()==row['input_sha256']
        assert hashlib.sha256((accepted/name).read_bytes()).hexdigest()==row['pdf_sha256']
        payload=json.loads(source.read_bytes());target=args.output/name
        render_factsheet(payload,template_path=capture/'template.pdf',design_model_path=capture/'completed_example.pdf',output_path=target)
        validate(target,payload);validate_copy(target)
        old,new=PdfReader(accepted/name),PdfReader(target)
        for key in ('/MSMetrics','/MSCalculationEvidence','/LayoutAuthoritySHA256','/CapitalPathBasis','/ChartStyleVersion'):
            assert old.metadata[key]==new.metadata[key],key
        tile=args.output/period;tile.mkdir()
        for label,pdf in (('accepted',accepted/name),('candidate',target)):
            subprocess.run([str(POPPLER),'-r','100','-png',str(pdf),str(tile/label)],check=True,capture_output=True)
        for page in (1,2):
            a=Image.open(tile/f'accepted-{page}.png').convert('RGB');b=Image.open(tile/f'candidate-{page}.png').convert('RGB')
            assert a.size==b.size and ImageChops.difference(a,b).getbbox() is None,(period,page)
        rows.append({'period_end':period,'pdf_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'all_pages_pixel_identical_to_accepted':True})
        print(period+' isolated replay passed',flush=True)
    (args.output/'verification.json').write_text(json.dumps({'status':'passed','count':len(rows),'rows':rows},indent=2),encoding='utf-8')


if __name__=='__main__':main()
