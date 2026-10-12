"""Raster and vector acceptance for a geometry-only historical PDF batch."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.monthly_artifacts import _validate_alpha_world_ms_allocation
from PIL import Image, ImageChops, ImageDraw
from pypdf import PdfReader


def verify(old_folder,new_folder,work):
    work.mkdir(parents=True,exist_ok=False)
    results=[];crops=[]
    names=sorted(p.name for p in new_folder.glob('AlphaWorld_MS_Factsheet_*.pdf'))
    if len(names)!=21:raise ValueError('Expected all 21 monthly PDFs')
    for name in names:
        old=old_folder/name;new=new_folder/name
        readers=[PdfReader(old),PdfReader(new)]
        if any(len(r.pages)!=2 for r in readers):raise ValueError('Unexpected page count')
        _validate_alpha_world_ms_allocation(readers[1].pages[1])
        if json.loads(readers[0].metadata['/MSMetrics'])!=json.loads(readers[1].metadata['/MSMetrics']):
            raise ValueError('Financial metrics changed: '+name)
        day=name.removeprefix('AlphaWorld_MS_Factsheet_').removesuffix('.pdf')
        for label,pdf in [('before',old),('after',new)]:
            subprocess.run(['pdftoppm','-scale-to','1000','-png',str(pdf),str(work/(day+'-'+label))],
                capture_output=True,check=True)
        bounds=[]
        for page in (1,2):
            before=Image.open(work/(day+'-before-'+str(page)+'.png')).convert('RGB')
            after=Image.open(work/(day+'-after-'+str(page)+'.png')).convert('RGB')
            if before.size!=after.size:raise ValueError('Page dimensions changed')
            diff=ImageChops.difference(before,after);bbox=diff.getbbox();bounds.append(bbox)
            if page==1 and bbox:raise ValueError('Page one changed: '+name)
            if page==2:
                width=float(readers[1].pages[1].mediabox.width);height=float(readers[1].pages[1].mediabox.height)
                sx=after.width/width;sy=after.height/height
                allowed=(int(95*sx)-2,int((height-755)*sy)-2,int(505*sx)+2,int((height-607)*sy)+2)
                if not bbox or any((bbox[0]<allowed[0],bbox[1]<allowed[1],bbox[2]>allowed[2],bbox[3]>allowed[3])):
                    raise ValueError('Changes outside allocation region: '+name+' '+str(bbox))
                crops.append((day,after.crop(allowed)))
        results.append({'period_end':day,'pdf_sha256':hashlib.sha256(new.read_bytes()).hexdigest(),
            'metrics_unchanged':True,'page_one_pixel_identical':True,'page_two_change_bounds':bounds[1],
            'changes_only_in_allocation_region':True,'vector_geometry_passed':True})
        print(json.dumps({'period':day,'geometry_and_raster':'passed'}),flush=True)
    tile_w=510;tile_h=235
    sheet=Image.new('RGB',(tile_w*3,tile_h*7),'white');draw=ImageDraw.Draw(sheet)
    for i,(day,crop) in enumerate(crops):
        crop.thumbnail((tile_w-16,tile_h-35))
        x=(i%3)*tile_w;y=(i//3)*tile_h
        draw.text((x+10,y+5),day,fill='black');sheet.paste(crop,(x+(tile_w-crop.width)//2,y+27))
    sheet.save(work/'allocation-contact-sheet.png')
    proof={'status':'pass','count':21,'scope':'allocation geometry only','checks':results,
        'old_folder':str(old_folder),'new_folder':str(new_folder),'contact_sheet':str(work/'allocation-contact-sheet.png')}
    (work/'verification.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
    print(json.dumps({'status':'pass','count':21,'proof':str(work/'verification.json')}))


if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='local':
        verify(ROOT/'output/pdf/AlphaWorld_MS_HKD_HIBOR_Validation_v14r8',
            ROOT/'output/pdf/AlphaWorld_MS_Allocation_Layout_v14r9',ROOT/'tmp/pdfs/alpha-ms-allocation-history-local-v14r9')
    elif mode=='azure':
        verify(ROOT/'output/pdf/AlphaWorld_MS_Azure_Factsheets_v14r7',
            ROOT/'output/pdf/AlphaWorld_MS_Azure_Allocation_Geometry_v14r9',ROOT/'tmp/pdfs/alpha-ms-allocation-history-azure-v14r9')
    else:raise ValueError('Unknown batch')
