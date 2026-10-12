"""Independent PDF text geometry and raster checks for the header correction."""
import json
from pathlib import Path
import subprocess

import pdfplumber
from PIL import Image,ImageChops

ROOT=Path(__file__).resolve().parents[1]


def main():
    old=ROOT/'output/pdf/AlphaWorld_MS_Allocation_Layout_v14r9/AlphaWorld_MS_Factsheet_2026-09-30.pdf'
    new=ROOT/'output/pdf/AlphaWorld_MS_Header_Typography_v14r10/AlphaWorld_MS_Factsheet_2026-09-30.pdf'
    work=ROOT/'tmp/pdfs/alpha-ms-header-typography-v14r10';work.mkdir(parents=True,exist_ok=False)
    with pdfplumber.open(new) as document:
        page=document.pages[0]
        words=page.extract_words(extra_attrs=['fontname','size'])
        benchmark=[w for w in words if 296<=w['x0']<411 and 108<=w['top']<=120]
        risk=[w for w in words if 430<=w['x0']<500 and 108<=w['top']<=120]
        if [w['text'] for w in benchmark]!=['HIBOR','+','Spread'] or [w['text'] for w in risk]!=['Multi-Tranche']:
            raise ValueError('Extra or missing header values')
        for word in benchmark+risk:
            if word['fontname']!=risk[0]['fontname'] or abs(word['size']-risk[0]['size'])>1e-9 or abs(word['top']-risk[0]['top'])>1e-9:
                raise ValueError('Independent header font/size/alignment mismatch')
        width,height=page.width,page.height
    for label,path in [('before',old),('after',new)]:
        subprocess.run(['pdftoppm','-scale-to','1500','-png',str(path),str(work/label)],capture_output=True,check=True)
    changes=[]
    for number in (1,2):
        before=Image.open(work/('before-'+str(number)+'.png')).convert('RGB')
        after=Image.open(work/('after-'+str(number)+'.png')).convert('RGB')
        if before.size!=after.size:raise ValueError('Page dimensions changed')
        bounds=ImageChops.difference(before,after).getbbox();changes.append(bounds)
        if number==2 and bounds:raise ValueError('Allocation/disclaimer page changed')
        if number==1:
            sx,sy=after.width/width,after.height/height
            permitted=(int(296*sx)-2,int((height-734.3)*sy)-2,int(411*sx)+2,int((height-718)*sy)+2)
            if not bounds or any((bounds[0]<permitted[0],bounds[1]<permitted[1],bounds[2]>permitted[2],bounds[3]>permitted[3])):
                raise ValueError('Change outside benchmark value')
            after.crop((int(285*sx),int(95*sy),int(505*sx),int(125*sy))).resize((1100,150)).save(work/'header-close-up.png')
    proof={'status':'pass','benchmark_words':benchmark,'risk_profile_words':risk,
        'page_one_change_bounds':changes[0],'changes_only_in_benchmark_value':True,'page_two_pixel_identical':True}
    (work/'verification.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
    print(json.dumps({'status':'pass','page_one_change_bounds':changes[0],'page_two_pixel_identical':True}))


if __name__=='__main__':main()
