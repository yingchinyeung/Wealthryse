"""Full-page raster and independent typography/geometry acceptance of Azure HKD batch."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.alpha_world_ms_pdf_validation import validate_benchmark_typography
from ibkr_etl.monthly_artifacts import _validate_alpha_world_ms_allocation
from PIL import Image,ImageChops,ImageDraw
from pypdf import PdfReader
import pdfplumber


def main():
    installed=len(sys.argv)>1 and sys.argv[1]=='installed'
    output=ROOT/('output/pdf/AlphaWorld_MS_Azure_Installed_HKD_HIBOR_v14r11' if installed else 'output/pdf/AlphaWorld_MS_Azure_HKD_HIBOR_v14r11')
    gold=ROOT/'tmp/pdfs/alpha-ms-header-history-local-v14r10'
    work=ROOT/('tmp/pdfs/alpha-ms-hkd-installed-v14r11' if installed else 'tmp/pdfs/alpha-ms-hkd-cutover-v14r11')
    work.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((output/'manifest.json').read_bytes())
    results=[];pages=[]
    for item in manifest['artifacts']:
        period=item['period_end'];path=output/('AlphaWorld_MS_Factsheet_'+period+'.pdf')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Downloaded PDF differs')
        reader=PdfReader(path)
        if len(reader.pages)!=2:raise ValueError('Unexpected page count')
        font=validate_benchmark_typography(reader.pages[0])
        _validate_alpha_world_ms_allocation(reader.pages[1])
        with pdfplumber.open(path) as doc:
            words=doc.pages[0].extract_words(extra_attrs=['fontname','size'])
            benchmark=[w for w in words if 296<=w['x0']<411 and 108<=w['top']<=120]
            risk=[w for w in words if 430<=w['x0']<500 and 108<=w['top']<=120]
            if [w['text'] for w in benchmark]!=['HIBOR','+','Spread'] or [w['text'] for w in risk]!=['Multi-Tranche']:raise ValueError('Header content differs')
            if any(w['fontname']!=risk[0]['fontname'] or abs(w['size']-risk[0]['size'])>1e-9 or abs(w['top']-risk[0]['top'])>1e-9 for w in benchmark+risk):raise ValueError('Header typography or alignment differs')
            text='\n'.join(p.extract_text() for p in doc.pages)
            if 'USD' in text or '1M HIBOR, period average' not in text:raise ValueError('Currency or risk-free convention differs')
        subprocess.run(['pdftoppm','-scale-to','1000','-png',str(path),str(work/period)],capture_output=True,check=True)
        for page in (1,2):
            before=Image.open(gold/(period+'-after-'+str(page)+'.png')).convert('RGB')
            after=Image.open(work/(period+'-'+str(page)+'.png')).convert('RGB')
            if before.size!=after.size or ImageChops.difference(before,after).getbbox():raise ValueError('Approved visual differs: '+period+'/'+str(page))
            pages.append((period,page,after.copy()))
        results.append({'period_end':period,'sha256':item['sha256'],'full_page_pixels_identical':True,
            'typography':font,'independent_embedded_font_and_baseline':'pass','equal_box_geometry':'pass',
            'currency':'HKD','risk_free_basis':'1M HIBOR, period average'})
        print(json.dumps({'period':period,'full_page_parity':'pass'}),flush=True)
    sheets=[]
    for start in range(0,len(pages),6):
        sheet=Image.new('RGB',(1530,1460),'#dddddd');draw=ImageDraw.Draw(sheet)
        for offset,(period,page,picture) in enumerate(pages[start:start+6]):
            x=offset%3*510;y=offset//3*730
            draw.text((x+8,y+5),period+' page '+str(page),fill='black')
            picture.thumbnail((500,705));sheet.paste(picture,(x+5,y+22))
        name=work/('contact-'+str(start//6+1)+'.png');sheet.save(name);sheets.append(str(name))
    proof={'status':'pass','months':len(results),'pages':len(pages),'full_page_pixels_identical':True,
        'checks':results,'image_digest':manifest['image_digest'],'manifest_sha256':hashlib.sha256((output/'manifest.json').read_bytes()).hexdigest(),
        'contact_sheets':sheets}
    (work/'verification.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
    print(json.dumps({'status':'pass','months':len(results),'pages':len(pages),'proof':str(work/'verification.json')}))


if __name__=='__main__':main()
