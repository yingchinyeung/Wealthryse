"""Check all 42 cloud pages: wording, numbers, fonts and unchanged outside pixels."""
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFont
import pdfplumber
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'src')]
import deploy_alpha_ms_copy as deployment
from ibkr_etl.alpha_world_ms_copy import FOOTER,PARAGRAPHS,BULLETS,validate_copy


def main():
    cloud=deployment.previous.read('candidate-probe-qualified')
    folder=deployment.FOLDER/'candidate-pdfs'
    before=ROOT/'output/pdf/AlphaWorld_MS_Azure_Installed_HKD_HIBOR_v14r11'
    scratch=ROOT/'tmp/pdfs/alpha-ms-cloud-copy-v14r12';scratch.mkdir(parents=True,exist_ok=False)
    rows=[];images=[]
    for entry in cloud['proof']['artifacts']:
        period=entry['period_end'];name='AlphaWorld_MS_Factsheet_'+period+'.pdf'
        old=before/name;new=folder/name;readers=[PdfReader(old),PdfReader(new)]
        assert hashlib.sha256(new.read_bytes()).hexdigest()==entry['sha256']
        oldmeta=dict(readers[0].metadata);newmeta=dict(readers[1].metadata)
        allowed={'/ChartStyleVersion','/MSCopyVersion','/MSCopySHA256'}
        assert {k:v for k,v in oldmeta.items() if k not in allowed}=={k:v for k,v in newmeta.items() if k not in allowed}
        validate_copy(new)
        for label,path in (('old',old),('new',new)):
            subprocess.run(['pdftoppm','-scale-to','1400','-png',str(path),str(scratch/(period+'-'+label))],check=True,capture_output=True)
        with pdfplumber.open(old) as p0,pdfplumber.open(new) as p1:
            for page in range(2):
                original=Image.open(scratch/f'{period}-old-{page+1}.png').convert('RGB')
                corrected=Image.open(scratch/f'{period}-new-{page+1}.png').convert('RGB')
                assert original.size==corrected.size
                sx=corrected.width/float(readers[1].pages[page].mediabox.width)
                sy=corrected.height/float(readers[1].pages[page].mediabox.height)
                diff=ImageChops.difference(original,corrected);draw=ImageDraw.Draw(diff)
                areas=[(20,807,558,828)]+([(38,315,565,739)] if page==1 else [])
                for x0,y0,x1,y1 in areas:
                    draw.rectangle((math.floor(x0*sx)-1,math.floor(y0*sy)-1,math.ceil(x1*sx)+1,math.ceil(y1*sy)+1),fill='black')
                assert diff.getbbox() is None,period+' unrequested pixels changed'
                footer=(20,807,570,829)
                expected=FOOTER
                for rect,text in [(footer,expected)]+([((38,315,565,739),' '.join(PARAGRAPHS[:5]+BULLETS+PARAGRAPHS[5:]))] if page==1 else []):
                    actual0=p0.pages[page].crop(rect).extract_text();actual1=p1.pages[page].crop(rect).extract_text()
                    assert re.sub(r'\s+','',actual0)==re.sub(r'\s+','',text)
                    assert ' '.join(actual1.split())==text
                    font,size=('IBMPlexSans',4.912386) if rect==footer else ('Georgia',8.999791)
                    assert all(font in c['fontname'] and abs(c['size']-size)<1e-5 for c in p1.pages[page].crop(rect).chars)
                images.append((period,page+1,corrected.copy()))
        rows.append({'period_end':period,'pdf_sha256':entry['sha256'],'pages':2,'original_financial_metadata_identical':True,
                     'outside_disclaimer_pixels_identical':True,'wording_unchanged_except_whitespace':True,'copy_and_fonts':'pass'})
        print(period+' verified',flush=True)
    assert len(rows)==21
    contacts=[]
    for offset in range(0,len(images),6):
        sheet=Image.new('RGB',(1200,1215),'#dddddd');draw=ImageDraw.Draw(sheet)
        for j,(period,page,img) in enumerate(images[offset:offset+6]):
            img.thumbnail((380,570));x=(j%3)*400+10;y=(j//3)*607+25
            sheet.paste(img,(x,y));draw.text((x,y-18),period+' page '+str(page),fill='black')
        path=scratch/f'contact-{offset//6+1}.png';sheet.save(path);contacts.append(str(path))
    proof={'status':'pass','count':21,'pages':42,'image':deployment.previous.image(),'checks':rows,'contact_sheets':contacts}
    target=deployment.FOLDER/'pdf-page-checks.json'
    with target.open('x',encoding='utf-8') as handle:json.dump(proof,handle,indent=2)
    print(json.dumps({'status':'pass','months':21,'pages':42,'contact_sheets':contacts}))


if __name__=='__main__':main()
