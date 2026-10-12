"""All-month T typography-only replay, exact pixels and unchanged financials."""
import hashlib
import json
from pathlib import Path
import subprocess
from zipfile import ZipFile,ZIP_DEFLATED
from PIL import Image,ImageChops
from pypdf import PdfReader,PdfWriter
from pypdf.generic import RectangleObject
from ibkr_etl.monthly_artifacts import render_factsheet
from ibkr_etl.alpha_world_t_factsheet import LAYOUT_VERSION

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'output/pdf/AlphaWorld_T_Factsheets_Layout_v5'
QA=ROOT/'tmp/pdfs/alpha-world-t-layout-v5'
POPPLER=Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe')


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def raster(pdf,prefix):
    subprocess.run([str(POPPLER),'-png','-r','100',str(pdf),str(prefix)],capture_output=True,check=True)


def box(coords,height):
    left,bottom,right,top=coords;scale=100/72.
    return (int(left*scale),int((height-top)*scale),int(right*scale)+1,int((height-bottom)*scale)+1)


def main():
    audit=ROOT/'.work/alpha-world-t-onboarding-20261008/history-v1'
    previous=ROOT/'output/pdf/AlphaWorld_T_Factsheets_Layout_v4'
    template=ROOT/'templates/alpha-world-t/owner_template_20261008.pdf'
    ms=ROOT/'output/pdf/AlphaWorld_MS_Azure_Disclaimer_v14r16/AlphaWorld_MS_Factsheet_2026-09-30.pdf'
    files=sorted((audit/'snapshots').glob('*.json'));assert len(files)==21
    before={str(p):sha(p) for p in files+[template,ms]}
    prior=json.loads((audit/'factsheet-layout-v4-results.json').read_bytes())
    assert all(sha(Path(p))==h for p,h in prior['source_hashes_unchanged'].items())
    expected={r['period_end']:r['pdf_sha256'] for r in prior['results']}
    assert not (OUTPUT/'verification.json').exists(), 'Completed qualification must not be overwritten'
    OUTPUT.mkdir(parents=True,exist_ok=True);QA.mkdir(parents=True,exist_ok=True)
    # Different page heights otherwise shift all raster text by half a pixel.
    # Normalize only the QA reference's page box, never font size/coordinates.
    normalized=QA/'ms-reference-t-page-box.pdf'
    if not normalized.exists():
        reference=PdfWriter(clone_from=PdfReader(ms));t_page=PdfReader(template).pages[1]
        for page in reference.pages:
            page.mediabox=RectangleObject(t_page.mediabox);page.cropbox=RectangleObject(t_page.cropbox)
        with normalized.open('xb') as stream:reference.write(stream)
    raster(normalized,QA/'ms');ms_image=Image.open(QA/'ms-2.png').convert('RGB')
    results=[]
    for p in files:
        snapshot=json.loads(p.read_bytes());period=snapshot['period_end']
        name=f'AlphaWorld_T_Factsheet_{period}.pdf';target=OUTPUT/name
        assert sha(previous/name)==expected[period]
        if not target.exists():render_factsheet(snapshot,template_path=template,output_path=target)
        from ibkr_etl.alpha_world_t_pdf_validation import validate
        validation=validate(target,snapshot,template_path=template)
        old,new=PdfReader(previous/name),PdfReader(target)
        for key in ('/TMetrics','/TSnapshotSHA256','/TCalculationEvidence','/TMethodologyIdentity','/PublicationAllowed','/SyntheticData'):
            assert old.metadata[key]==new.metadata[key],(period,key)
        tile=QA/period;tile.mkdir(exist_ok=True)
        raster(previous/name,tile/'old');raster(target,tile/'new')
        for page in (1,2):
            a=Image.open(tile/f'old-{page}.png').convert('RGB');b=Image.open(tile/f'new-{page}.png').convert('RGB')
            assert a.size==b.size
            diff=ImageChops.difference(a,b);assert diff.getbbox() is not None,(period,page,'requested region unchanged')
            height=float(new.pages[page-1].mediabox.height)
            region=(19,706,579,750) if page==1 else (17,77,584,589)
            diff.paste((0,0,0),box(region,height))
            assert diff.getbbox() is None,(period,page,'outside requested regions',diff.getbbox())
            if page==2:
                area=box((20,78,578,588),height)
                assert ImageChops.difference(b.crop(area),ms_image.crop(area)).getbbox() is None,(period,'MS disclaimer pixels differ')
        results.append({'period_end':period,'pdf_sha256':sha(target),'input_sha256':before[str(p)],
            'validation':validation,'financial_metadata_unchanged':True,
            'outside_requested_regions_pixel_identical':True,'disclaimer_pixels_match_ms_v14r16':True})
        print(period+' PASS',flush=True)
    assert all(sha(Path(p))==h for p,h in before.items())
    record={'status':'local-qualified-not-deployed','layout_version':LAYOUT_VERSION,'months':21,
        'disclaimer_words_preserved':True,'t_specific_header_values_preserved':True,
        'ms_pixel_reference_page_box_normalized_only':True,
        'source_hashes_unchanged':before,'azure_changed':False,'results':results}
    with (OUTPUT/'verification.json').open('x',encoding='utf-8') as stream:json.dump(record,stream,indent=2)
    with ZipFile(OUTPUT.with_suffix('.zip'),'x',compression=ZIP_DEFLATED) as archive:
        for p in sorted(OUTPUT.iterdir()):archive.write(p,p.name)
    print('ALL 21 PASS; LOCAL ONLY',flush=True)


if __name__=='__main__':main()
