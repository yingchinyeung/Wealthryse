"""Independent source FX/metric checks and PNG review sheets; read-only inputs."""
from pathlib import Path
import hashlib
import json
import sys
from PIL import Image, ImageDraw
import pdfplumber
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.alpha_world_ms_hkd import rebuild
from ibkr_etl.alpha_world_ms_pdf_validation import validate


def main():
    folder=ROOT/'.work/alpha-ms-hkd-hibor-20261007'
    source=json.loads((folder/'source-inspection.json').read_bytes())
    actual=rebuild(source)
    saved=json.loads((folder/'hkd-history.json').read_bytes())
    if actual!=saved:raise ValueError('Rebuilt source history differs from rendered history')
    # Direct independent broker-quote selection, without the production FX class.
    preferred=['U16088571']
    preferred+=list(dict.fromkeys(r['account_id'] for r in source['broker_fx'] if r['account_id'] not in preferred))
    def quote(currency,day):
        if currency=='HKD':return 1.,day
        for account in preferred:
            for inverse in (False,True):
                pair=('HKD',currency) if inverse else (currency,'HKD')
                candidates=[r for r in source['broker_fx'] if r['account_id']==account and
                    (r['from_currency'],r['to_currency'])==pair and r['rate_date']<=day]
                if candidates:
                    last=max(candidates,key=lambda r:r['rate_date'])
                    value=float(last['rate_text'])
                    return (1/value if inverse else value),last['rate_date']
        raise ValueError('Missing independent FX quote')
    for row in actual['source_audit']:
        rate,day=quote(row['source_currency'],row['date'])
        initial,_=quote(row['source_currency'],'2025-01-20')
        original=[r for r in source['components'] if r['component_id']==row['component_id'] and r['snapshot_date']=='2025-01-20']
        if len(original)!=1:raise ValueError('Ambiguous original inception component')
        expected_initial=float(original[0]['source_value'])*initial
        if not np.isclose(rate,row['fx_to_hkd'],rtol=0,atol=1e-12) or day!=row['fx_date']:
            raise ValueError('Independent source FX selection differs')
        if not np.isclose(expected_initial,row['normalization_hkd'],rtol=0,atol=1e-10):
            raise ValueError('Independent normalization differs')
    output=ROOT/'output/pdf/AlphaWorld_MS_HKD_HIBOR_Validation_v14r8'
    manifest=json.loads((output/'manifest.json').read_bytes())
    for item in manifest['artifacts']:
        path=Path(item['pdf'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('PDF bytes changed')
        with pdfplumber.open(path) as pdf:
            if len(pdf.pages)!=2:raise ValueError('Wrong page count')
            first,second=(p.extract_text() for p in pdf.pages)
            if 'USD' in first or '1M HIBOR, period average' not in first:raise ValueError('Wrong currency/rate label')
        validate(path,json.loads((folder/'hkd-inputs'/(item['period_end']+'.json')).read_bytes()))
    raster=ROOT/'tmp/pdfs/alpha-ms-hkd-v14r8'
    images=sorted(raster.glob('*-[12].png'))
    if len(images)!=42:raise ValueError('42 Poppler-rendered pages required')
    for offset in range(0,len(images),7):
        canvas=Image.new('RGB',(1260,1890),'#d8d8d8');draw=ImageDraw.Draw(canvas)
        for i,path in enumerate(images[offset:offset+7]):
            thumb=Image.open(path);thumb.thumbnail((410,590))
            x=(i%3)*420;y=(i//3)*630
            canvas.paste(thumb,(x,y+25));draw.text((x+8,y+6),path.stem.replace('AlphaWorld_MS_Factsheet_',''),fill='black')
        canvas.save(raster/('contact-'+str(offset//7+1)+'.png'))
    record={'pdf_count':21,'page_count':42,'original_inputs_changed':False,
        'source_fx_checks':len(actual['source_audit']),'independent_source_fx_checks':'pass',
        'actual_pdf_hash_currency_and_caption_checks':'pass','production_cutover':False,
        'visual_review':'pending'}
    (folder/'private-qa.json').open('x',encoding='utf-8').write(json.dumps(record,indent=2))
    print(json.dumps(record))


if __name__=='__main__':main()
