"""All-month local MS subtitle qualification; no Azure or source writes."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

from PIL import Image, ImageChops, ImageDraw
from pypdf import PdfReader
from ibkr_etl.monthly_artifacts import render_factsheet, factsheet_layout_version
from ibkr_etl.alpha_world_ms_subtitle import validate, FONT_SHA256, FONT_PROGRAM_SHA256

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'output/pdf/AlphaWorld_MS_Subtitle_v14r14'
QA=ROOT/'tmp/pdfs/alpha-ms-subtitle-v14r14'
BASE=ROOT/'output/pdf/AlphaWorld_MS_Azure_Distribution_Ticks_v14r13'


def sha(raw):return hashlib.sha256(raw).hexdigest()


def archive(output_path=None):
    record=json.loads((OUTPUT/'manifest.json').read_bytes())
    assert record['status']=='local-qualified-not-deployed' and record['count']==21
    for row in record['checks']:
        target=OUTPUT/('AlphaWorld_MS_Factsheet_'+row['period_end']+'.pdf')
        assert sha(target.read_bytes())==row['pdf_sha256']
        validate(PdfReader(target).pages[0])
    path=Path(output_path) if output_path is not None else OUTPUT.with_suffix('.zip')
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED) as bundle:
        for file in sorted(OUTPUT.glob('*.pdf')):bundle.write(file,file.name)
        bundle.write(OUTPUT/'manifest.json','manifest.json')
    return path


def main(*, changed_regions=None, extra_validation=None):
    OUTPUT.mkdir(parents=True,exist_ok=True)
    QA.mkdir(parents=True,exist_ok=False)
    capture=ROOT/'.work/alpha-ms-factsheets-v13-20261007'
    assets=json.loads((capture/'capture.json').read_bytes())['assets']
    for asset in assets:
        assert sha((capture/(asset['asset_role']+'.pdf')).read_bytes())==asset['sha256']
    inputs=sorted((ROOT/'.work/alpha-ms-hkd-hibor-20261007/hkd-inputs').glob('*.json'))
    assert len(inputs)==21
    rows=[];crops=[]
    for path in inputs:
        raw=path.read_bytes();snapshot=json.loads(raw)
        period=snapshot['period_end'];name='AlphaWorld_MS_Factsheet_'+period+'.pdf'
        target=OUTPUT/name
        # The separately inspected September preview is already qualified.
        if not target.exists():
            result=render_factsheet(snapshot,template_path=capture/'template.pdf',
                design_model_path=capture/'completed_example.pdf',output_path=target)
            assert result['pdf_accuracy_validation']['status']=='pass'
        from ibkr_etl.alpha_world_ms_pdf_validation import validate as accuracy
        accuracy(target,snapshot)
        old,new=PdfReader(BASE/name),PdfReader(target)
        validate(new.pages[0])
        extra=extra_validation(new,period) if extra_validation is not None else {}
        for key in ('/MSMetrics','/MSCalculationEvidence','/MSCopyVersion','/MSCopySHA256'):
            assert old.metadata[key]==new.metadata[key],(period,key)
        tile=QA/period;tile.mkdir()
        for label,pdf in (('before',BASE/name),('after',target)):
            subprocess.run(['pdftoppm','-r','100','-png',str(pdf),str(tile/label)],check=True,capture_output=True)
        for page in (1,2):
            before=Image.open(tile/f'before-{page}.png').convert('RGB')
            after=Image.open(tile/f'after-{page}.png').convert('RGB')
            diff=ImageChops.difference(before,after)
            if page==1 or changed_regions is not None:
                sx,sy=after.width/float(new.pages[0].mediabox.width),after.height/float(new.pages[0].mediabox.height)
                left,bottom,right,top=(changed_regions[page] if changed_regions is not None else (21,764,275,778))
                region=(int(left*sx),int((float(new.pages[page-1].mediabox.height)-top)*sy),
                        int(right*sx)+1,int((float(new.pages[page-1].mediabox.height)-bottom)*sy)+1)
                assert diff.getbbox() is not None
                diff.paste((0,0,0),region)
                if page==1:crops.append((period,after.crop((0,0,after.width,int(98*sy)))))
            assert diff.getbbox() is None,(period,page,diff.getbbox())
        assert path.read_bytes()==raw
        rows.append({'period_end':period,'pdf_sha256':sha(target.read_bytes()),'input_sha256':sha(raw),
                     'subtitle':validate(new.pages[0]),'outside_subtitle_pixel_identical':changed_regions is None,
                     'page_two_pixel_identical':changed_regions is None,'financial_metadata_unchanged':True,
                     'changed_regions':changed_regions,'extra_validation':extra,
                     'outside_requested_regions_pixel_identical':True})
        print(period+' passed',flush=True)
    for asset in assets:
        assert sha((capture/(asset['asset_role']+'.pdf')).read_bytes())==asset['sha256']
    w=crops[0][1].width;h=crops[0][1].height+22
    contact=Image.new('RGB',(w*3,h*7),'white');draw=ImageDraw.Draw(contact)
    for i,(period,crop) in enumerate(crops):
        x,y=i%3*w,i//3*h;draw.text((x+5,y+3),period,fill='black');contact.paste(crop,(x,y+22))
    contact.save(QA/'all-21-titles.png')
    manifest={'status':'local-qualified-not-deployed','count':len(rows),
        'layout_version':factsheet_layout_version('alpha-world-ms'),'font_sha256':FONT_SHA256,
        'font_program_sha256':FONT_PROGRAM_SHA256,
        'azure_changed':False,'source_data_changed':False,'checks':rows}
    with (OUTPUT/'manifest.json').open('x',encoding='utf-8') as stream:
        stream.write(json.dumps(manifest,indent=2)+'\n')


if __name__=='__main__':main()
