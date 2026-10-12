"""All-month numerical, embedded-font and pixel acceptance for a header-only change."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.alpha_world_ms_pdf_validation import validate_benchmark_typography
from ibkr_etl.monthly_artifacts import _validate_alpha_world_ms_allocation
import pdfplumber
from PIL import Image,ImageChops,ImageDraw
from pypdf import PdfReader


def verify(old_folder,new_folder,work):
    work.mkdir(parents=True,exist_ok=False)
    names=sorted(p.name for p in new_folder.glob('AlphaWorld_MS_Factsheet_*.pdf'))
    if len(names)!=21:raise ValueError('All 21 historical months required')
    results=[];crops=[]
    for name in names:
        old=old_folder/name;new=new_folder/name
        readers=[PdfReader(old),PdfReader(new)]
        if any(len(r.pages)!=2 for r in readers):raise ValueError('Unexpected page count')
        for key in ('/MSMetrics','/MSCalculationVersion','/MSCalculationEvidence'):
            if readers[0].metadata[key]!=readers[1].metadata[key]:raise ValueError('Financial evidence changed: '+name+' '+key)
        typography=validate_benchmark_typography(readers[1].pages[0])
        _validate_alpha_world_ms_allocation(readers[1].pages[1])
        with pdfplumber.open(new) as doc:
            words=doc.pages[0].extract_words(extra_attrs=['fontname','size'])
            benchmark=[w for w in words if 296<=w['x0']<411 and 108<=w['top']<=120]
            risk=[w for w in words if 430<=w['x0']<500 and 108<=w['top']<=120]
            if [w['text'] for w in benchmark]!=['HIBOR','+','Spread'] or [w['text'] for w in risk]!=['Multi-Tranche']:
                raise ValueError('Extra or missing header values')
            if any(w['fontname']!=risk[0]['fontname'] or abs(w['size']-risk[0]['size'])>1e-9
                    or abs(w['top']-risk[0]['top'])>1e-9 for w in benchmark+risk):raise ValueError('Independent typography mismatch')
        day=name.removeprefix('AlphaWorld_MS_Factsheet_').removesuffix('.pdf')
        for label,pdf in [('before',old),('after',new)]:
            subprocess.run(['pdftoppm','-scale-to','1000','-png',str(pdf),str(work/(day+'-'+label))],capture_output=True,check=True)
        bounds=[]
        for page in (1,2):
            before=Image.open(work/(day+'-before-'+str(page)+'.png')).convert('RGB')
            after=Image.open(work/(day+'-after-'+str(page)+'.png')).convert('RGB')
            if before.size!=after.size:raise ValueError('Page dimensions changed')
            bbox=ImageChops.difference(before,after).getbbox();bounds.append(bbox)
            if page==2 and bbox:raise ValueError('Page two changed')
            if page==1:
                width=float(readers[1].pages[0].mediabox.width);height=float(readers[1].pages[0].mediabox.height)
                sx,sy=after.width/width,after.height/height
                allowed=(int(296*sx)-2,int((height-734.3)*sy)-2,int(411*sx)+2,int((height-718)*sy)+2)
                if not bbox or any((bbox[0]<allowed[0],bbox[1]<allowed[1],bbox[2]>allowed[2],bbox[3]>allowed[3])):
                    raise ValueError('Change outside benchmark value')
                crop=after.crop((int(285*sx),int(95*sy),int(505*sx),int(125*sy)))
                crops.append((day,crop))
        results.append({'period_end':day,'pdf_sha256':hashlib.sha256(new.read_bytes()).hexdigest(),
            'metrics_and_financial_evidence_unchanged':True,'typography':typography,
            'independent_typography_passed':True,'allocation_geometry_passed':True,
            'page_two_pixel_identical':True,'page_one_change_bounds':bounds[0],
            'changes_only_in_benchmark_value':True})
        print(json.dumps({'period':day,'header_and_raster':'passed'}),flush=True)
    sheet=Image.new('RGB',(1560,840),'white');draw=ImageDraw.Draw(sheet)
    for i,(day,crop) in enumerate(crops):
        crop=crop.resize((500,70));x=(i%3)*520;y=(i//3)*120
        draw.text((x+10,y+5),day,fill='black');sheet.paste(crop,(x+10,y+30))
    sheet.save(work/'header-contact-sheet.png')
    proof={'status':'pass','count':21,'scope':'benchmark header typography only','checks':results,
        'old_folder':str(old_folder),'new_folder':str(new_folder),'contact_sheet':str(work/'header-contact-sheet.png')}
    (work/'verification.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
    print(json.dumps({'status':'pass','count':21,'proof':str(work/'verification.json')}))


if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='local':
        verify(ROOT/'output/pdf/AlphaWorld_MS_Allocation_Layout_v14r9',
            ROOT/'output/pdf/AlphaWorld_MS_Header_Typography_History_v14r10',ROOT/'tmp/pdfs/alpha-ms-header-history-local-v14r10')
    elif mode=='azure':
        verify(ROOT/'output/pdf/AlphaWorld_MS_Azure_Allocation_Geometry_v14r9',
            ROOT/'output/pdf/AlphaWorld_MS_Azure_Header_Typography_v14r10',ROOT/'tmp/pdfs/alpha-ms-header-history-azure-v14r10')
    else:raise ValueError('Unknown batch')
