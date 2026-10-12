"""Render and visually qualify every MS month; never alter source snapshots."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from PIL import Image, ImageChops, ImageDraw
from pypdf import PdfReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from ibkr_etl.monthly_artifacts import render_factsheet, factsheet_layout_version
from ibkr_etl.alpha_world_ms_pdf_validation import _elements
from ibkr_etl.alpha_world_ms_copy import validate_copy

OUTPUT = ROOT/'output/pdf/AlphaWorld_MS_Distribution_Ticks_v14r13'
QA = ROOT/'tmp/pdfs/alpha-ms-distribution-ticks-v14r13'
BASELINE = ROOT/'tmp/pdfs/alpha-ms-copy-v14r12/shared-renderer'
POPPER = Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def raster(path, prefix):
    subprocess.run([str(POPPER), '-scale-to', '1400', '-png', str(path), str(prefix)], check=True,
                   capture_output=True)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=False)
    QA.mkdir(parents=True, exist_ok=False)
    capture = ROOT/'.work/alpha-ms-factsheets-v13-20261007'
    assets = json.loads((capture/'capture.json').read_bytes())['assets']
    for asset in assets:
        assert sha((capture/(asset['asset_role']+'.pdf')).read_bytes()) == asset['sha256']
    paths = sorted((ROOT/'.work/alpha-ms-hkd-hibor-20261007/hkd-inputs').glob('*.json'))
    assert len(paths) == 21
    checks, crops = [], []
    for path in paths:
        raw = path.read_bytes()
        snapshot = json.loads(raw)
        immutable = json.dumps(snapshot, sort_keys=True)
        period = snapshot['period_end']
        target = OUTPUT/('AlphaWorld_MS_Factsheet_'+period+'.pdf')
        result = render_factsheet(snapshot, template_path=capture/'template.pdf',
                                 design_model_path=capture/'completed_example.pdf', output_path=target)
        assert path.read_bytes() == raw and json.dumps(snapshot, sort_keys=True) == immutable
        assert result['pdf_accuracy_validation']['status'] == 'pass'
        validate_copy(target)
        original = BASELINE/(path.stem+'.pdf')
        old, new = PdfReader(original), PdfReader(target)
        for key in ('/MSCalculationEvidence', '/MSMetrics'):
            assert key in old.metadata and old.metadata[key] == new.metadata[key], key
        _, _, runs = _elements(new.pages[0], new)
        ticks = [r for r in runs if r[3] == 5.5 and abs(r[2]-99.) < 1e-5 and r[0].endswith('%')]
        assert len(ticks) >= 2
        for previous, current in zip(ticks, ticks[1:]):
            assert float(current[0][:-1])-float(previous[0][:-1]) == 25.
            assert current[1] - (previous[1]+stringWidth(previous[0], 'Helvetica', previous[3])) > 3.5
        folder = QA/period
        folder.mkdir()
        raster(original, folder/'before')
        raster(target, folder/'after')
        for page in (1, 2):
            before = Image.open(folder/f'before-{page}.png').convert('RGB')
            after = Image.open(folder/f'after-{page}.png').convert('RGB')
            difference = ImageChops.difference(before, after)
            if page == 1:
                sx, sy = after.width/float(new.pages[0].mediabox.width), after.height/float(new.pages[0].mediabox.height)
                # Only chart guides and return ticks may change, not headings,
                # numerical metrics, disclosure, allocation or the NAV charts.
                region = (int(95*sx), int((841.89-232)*sy), int(545*sx)+1, int((841.89-96)*sy)+1)
                assert difference.getbbox() is not None
                difference.paste((0,0,0), region)
                crop = after.crop((int(90*sx), int((841.89-244)*sy), int(545*sx), int((841.89-89)*sy)))
                crops.append((period, crop))
            assert difference.getbbox() is None, (period, page, difference.getbbox())
        checks.append({'period_end':period, 'pdf_sha256':sha(target.read_bytes()),
                       'input_sha256':sha(raw), 'source_unchanged':True,
                       'calculation_metadata_unchanged':True, 'accuracy':'pass',
                       'tick_labels':[r[0] for r in ticks], 'label_spacing':'pass',
                       'outside_tick_and_guide_region_pixel_identical':True,
                       'page_two_pixel_identical':True})
        print(period+' passed: '+', '.join(r[0] for r in ticks), flush=True)
    tile_w = max(image.width for _,image in crops)
    tile_h = max(image.height for _,image in crops)+25
    sheet = Image.new('RGB',(tile_w*3,tile_h*7),'white')
    draw = ImageDraw.Draw(sheet)
    for i,(label,crop) in enumerate(crops):
        x,y=(i%3)*tile_w,(i//3)*tile_h
        draw.text((x+5,y+4),label,fill='black')
        sheet.paste(crop,(x,y+25))
    sheet.save(QA/'all-21-distributions.png')
    for asset in assets:
        assert sha((capture/(asset['asset_role']+'.pdf')).read_bytes()) == asset['sha256']
    record={'status':'local-qualified-not-deployed','layout_version':factsheet_layout_version('alpha-world-ms'),
            'count':len(checks),'production_changed':False,'financial_calculations_changed':False,
            'ibkr_source_changed':False,'checks':checks}
    (OUTPUT/'manifest.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__':
    main()
