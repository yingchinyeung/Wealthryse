"""Local, non-mutating PDF checks and legible chart contact sheets."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.alpha_world_ms_pdf_validation import validate

FOLDER = ROOT/'output/pdf/AlphaWorld_MS_Monthly_Factsheets_v14r2'
QA = ROOT/'tmp/pdfs/alpha-ms-v14r2-local-review'
QA.mkdir(parents=True,exist_ok=True)
manifest = json.loads((FOLDER/'manifest.json').read_bytes())

def check(entry):
    period = entry['period_end']
    raw = (ROOT/'.work/alpha-ms-factsheets-v13-20261007/snapshots'/f'{period}.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry['data_sha256']: raise ValueError('Frozen source differs')
    target = Path(entry['factsheet'])
    if hashlib.sha256(target.read_bytes()).hexdigest() != entry['sha256']: raise ValueError('PDF differs')
    result = validate(target, json.loads(raw))
    subprocess.run(['pdftoppm','-scale-to','1400','-png',str(target),str(QA/period)],check=True,capture_output=True,timeout=60)
    return {'period_end':period, **result}

with ThreadPoolExecutor(max_workers=4) as pool:
    checks = list(pool.map(check,manifest['artifacts']))
periods = [c['period_end'] for c in checks]
for graph, box in (('performance',(45,588,960,887)),('distribution',(120,960,950,1310)),('allocation',(30,85,950,400))):
    for start in range(0,len(periods),4):
        sheet = Image.new('RGB',(1900,860),'#eeeeee')
        draw = ImageDraw.Draw(sheet)
        for i, period in enumerate(periods[start:start+4]):
            page = 2 if graph=='allocation' else 1
            crop = Image.open(QA/f'{period}-{page}.png').convert('RGB').crop(box)
            crop.thumbnail((925,390))
            x,y = i%2*950+10, i//2*430+25
            sheet.paste(crop,(x,y)); draw.text((x,y-18),period,fill='black')
        sheet.save(QA/f'{graph}-contact-{start//4+1}.png')
record = {'pdfs':len(checks),'pages_rendered':len(checks)*2,'source_data_changed':False,'accuracy':checks}
with (ROOT/'.work/alpha-ms-azure-v14r2-20261007/local-pdf-review.json').open('x',encoding='utf-8') as handle:
    json.dump(record,handle,indent=2)
print(json.dumps({'pdfs':len(checks),'pages_rendered':42,'contact_sheets':18}))
