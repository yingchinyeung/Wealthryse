"""Compare actual cloud-rendered PDFs to the installed v14r12 artifacts."""
import json
from PIL import Image, ImageChops, ImageDraw
from pypdf import PdfReader
from reportlab.pdfbase.pdfmetrics import stringWidth

import deploy_alpha_ms_distribution_ticks as release
from qualify_alpha_ms_distribution_ticks import raster, sha
from ibkr_etl.alpha_world_ms_pdf_validation import _elements
from ibkr_etl.alpha_world_ms_copy import validate_copy


def main():
    proof = release.previous.read('candidate-probe-qualified')['proof']
    assert proof['status'] == 'passed' and proof['count'] == 21
    baseline = release.ROOT/'output/pdf/AlphaWorld_MS_Azure_Copy_v14r12'
    before_manifest = json.loads((baseline/'manifest.json').read_bytes())
    before_rows = {r['period_end']:r for r in before_manifest['artifacts']}
    qa = release.ROOT/'tmp/pdfs/alpha-ms-azure-distribution-v14r13'
    qa.mkdir(parents=True, exist_ok=False)
    checks, crops = [], []
    for row in proof['artifacts']:
        period = row['period_end']
        name = 'AlphaWorld_MS_Factsheet_'+period+'.pdf'
        original = baseline/name
        target = release.FOLDER/'candidate-pdfs'/name
        assert sha(original.read_bytes()) == before_rows[period]['sha256']
        assert sha(target.read_bytes()) == row['sha256']
        assert row['data_sha256'] == before_rows[period]['data_sha256']
        assert row['snapshot_id'] == before_rows[period]['snapshot_id']
        old, new = PdfReader(original), PdfReader(target)
        for key in ('/MSMetrics','/MSCalculationEvidence'):
            assert old.metadata[key] == new.metadata[key]
        assert new.metadata['/ChartStyleVersion'] == release.LAYOUT
        validate_copy(target)
        _, _, text = _elements(new.pages[0], new)
        ticks = [r for r in text if r[3]==5.5 and abs(r[2]-99.)<1e-5 and r[0].endswith('%')]
        assert len(ticks) >= 2
        for previous, current in zip(ticks, ticks[1:]):
            assert float(current[0][:-1])-float(previous[0][:-1]) == 25.
            assert current[1]-(previous[1]+stringWidth(previous[0],'Helvetica',5.5)) > 3.5
        folder = qa/period;folder.mkdir()
        raster(original, folder/'before');raster(target, folder/'after')
        for page in (1,2):
            before = Image.open(folder/f'before-{page}.png').convert('RGB')
            after = Image.open(folder/f'after-{page}.png').convert('RGB')
            diff = ImageChops.difference(before,after)
            if page == 1:
                sx, sy = after.width/float(new.pages[0].mediabox.width), after.height/float(new.pages[0].mediabox.height)
                assert diff.getbbox() is not None
                diff.paste((0,0,0),(int(95*sx),int((841.89-232)*sy),int(545*sx)+1,int((841.89-96)*sy)+1))
                crops.append((period,after.crop((int(90*sx),int((841.89-244)*sy),int(545*sx),int((841.89-89)*sy)))))
            assert diff.getbbox() is None,(period,page,diff.getbbox())
        checks.append({'period_end':period,'pdf_sha256':row['sha256'],'financial_metadata_unchanged':True,
                       'frozen_payload_identity_unchanged':True,'x_ticks':[r[0] for r in ticks],
                       'outside_requested_chart_region_pixel_identical':True,'page_two_pixel_identical':True})
        print(period+' cloud PDF passed',flush=True)
    tile_w=max(image.width for _,image in crops);tile_h=max(image.height for _,image in crops)+25
    sheet=Image.new('RGB',(tile_w*3,tile_h*7),'white');draw=ImageDraw.Draw(sheet)
    for i,(label,crop) in enumerate(crops):
        x,y=(i%3)*tile_w,(i//3)*tile_h
        draw.text((x+5,y+4),label,fill='black');sheet.paste(crop,(x,y+25))
    sheet.save(qa/'all-21-distributions.png')
    result={'status':'pass','count':len(checks),'image':release.previous.image(),'checks':checks,
            'scope':'X-axis labels and faint guides only; all 42 pages compared',
            'baseline_image':release.BASE_IMAGE,'financial_calculations_unchanged':True}
    with (release.FOLDER/'pdf-page-checks.json').open('x',encoding='utf-8') as handle:
        json.dump(result,handle,indent=2)


if __name__=='__main__':main()
