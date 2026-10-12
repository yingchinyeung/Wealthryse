"""Require the exact MS divider and no other pixel changes in all 21 T PDFs."""
import json
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
from PIL import Image,ImageChops
from pypdf import PdfReader
from ibkr_etl.monthly_artifacts import render_factsheet
from ibkr_etl.alpha_world_t_factsheet import LAYOUT_VERSION
from qualify_alpha_world_t_layout_v5 import ROOT,sha,raster,box

OUTPUT=ROOT/'output/pdf/AlphaWorld_T_Factsheets_Layout_v6'
QA=ROOT/'tmp/pdfs/alpha-world-t-layout-v6'

def main():
    previous=ROOT/'output/pdf/AlphaWorld_T_Factsheets_Layout_v5'
    prior=json.loads((previous/'verification.json').read_bytes())
    before=prior['source_hashes_unchanged']
    assert all(sha(Path(p))==h for p,h in before.items())
    expected={r['period_end']:r['pdf_sha256'] for r in prior['results']}
    template=ROOT/'templates/alpha-world-t/owner_template_20261008.pdf'
    snapshots=ROOT/'.work/alpha-world-t-onboarding-20261008/history-v1/snapshots'
    assert not (OUTPUT/'verification.json').exists()
    OUTPUT.mkdir(parents=True,exist_ok=True);QA.mkdir(parents=True,exist_ok=True)
    # This accepted QA reference changes only MS's page box to T dimensions.
    reference=ROOT/'tmp/pdfs/alpha-world-t-layout-v5/ms-reference-t-page-box.pdf'
    raster(reference,QA/'ms')
    ms_image=Image.open(QA/'ms-1.png').convert('RGB')
    results=[]
    for period in sorted(expected):
        name=f'AlphaWorld_T_Factsheet_{period}.pdf';target=OUTPUT/name
        assert sha(previous/name)==expected[period]
        snapshot=json.loads((snapshots/f'{period}.json').read_bytes())
        if not target.exists(): render_factsheet(snapshot,template_path=template,output_path=target)
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
            diff=ImageChops.difference(a,b)
            if page==1:
                assert diff.getbbox() is not None,(period,'missing divider')
                area=box((389,665,564,669),float(new.pages[0].mediabox.height))
                assert ImageChops.difference(b.crop(area),ms_image.crop(area)).getbbox() is None,(period,'divider differs from MS')
                assert sum(1 for r,g,blue in b.crop(area).getdata() if blue>r+30 and blue>g+15)>100,(period,'no blue pixels')
                diff.paste((0,0,0),area)
            assert diff.getbbox() is None,(period,page,'unrequested change',diff.getbbox())
        results.append({'period_end':period,'pdf_sha256':sha(target),'validation':validation,
                        'blue_divider_matches_ms':True,'all_other_pixels_unchanged':True,'financial_metadata_unchanged':True})
        print(period+' PASS',flush=True)
    assert len(results)==21
    assert all(sha(Path(p))==h for p,h in before.items())
    record={'status':'local-qualified-not-deployed','layout_version':LAYOUT_VERSION,'months':21,
            'azure_changed':False,'source_hashes_unchanged':before,'results':results}
    with (OUTPUT/'verification.json').open('x',encoding='utf-8') as stream: json.dump(record,stream,indent=2)
    with ZipFile(OUTPUT.with_suffix('.zip'),'x',compression=ZIP_DEFLATED) as archive:
        for path in sorted(OUTPUT.iterdir()): archive.write(path,path.name)
    print('ALL 21 PASS; LOCAL ONLY',flush=True)

if __name__=='__main__': main()
