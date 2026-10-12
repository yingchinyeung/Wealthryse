"""Independent text/font/pixel checks for the three corrected September areas."""
import json
import math
from pathlib import Path
import re
import sys

from PIL import Image, ImageChops, ImageDraw
import pdfplumber
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from ibkr_etl.alpha_world_ms_copy import FOOTER, PARAGRAPHS, BULLETS, validate_copy


def tight(text): return re.sub(r'\s+', '', text)


def main():
    folder = ROOT/'output/pdf/AlphaWorld_MS_Text_Corrections_v14r12'
    source = ROOT/'output/pdf/AlphaWorld_MS_Azure_Installed_HKD_HIBOR_v14r11/AlphaWorld_MS_Factsheet_2026-09-30.pdf'
    target = folder/source.name
    scratch = ROOT/'tmp/pdfs/alpha-ms-copy-v14r12'
    before = PdfReader(source); after = PdfReader(target)
    assert all(after.metadata[k] == v for k, v in before.metadata.items())
    rows = []
    with pdfplumber.open(source) as original, pdfplumber.open(target) as corrected:
        for i in range(2):
            old_image = Image.open(scratch/f'original-{i+1}.png').convert('RGB')
            new_image = Image.open(scratch/f'final-{i+1}.png').convert('RGB')
            assert old_image.size == new_image.size
            difference = ImageChops.difference(old_image, new_image)
            draw = ImageDraw.Draw(difference)
            regions = [(20, 807, 558, 828)]
            if i == 1: regions.append((38, 315, 565, 739))
            sx = new_image.width/float(after.pages[i].mediabox.width)
            sy = new_image.height/float(after.pages[i].mediabox.height)
            for x0, y0, x1, y1 in regions:
                draw.rectangle((math.floor(x0*sx)-1, math.floor(y0*sy)-1,
                                math.ceil(x1*sx)+1, math.ceil(y1*sy)+1), fill='black')
            assert difference.getbbox() is None, f'Unrequested pixels changed on page {i+1}'
            footer_rect = (20, 807, 570, 829)
            old_footer = original.pages[i].crop(footer_rect)
            new_footer = corrected.pages[i].crop(footer_rect)
            assert tight(old_footer.extract_text()) == tight(FOOTER)
            assert ' '.join(new_footer.extract_text().split()) == FOOTER
            assert all('IBMPlexSans' in c['fontname'] for c in new_footer.chars)
            assert all(abs(c['size']-4.912386) < 1e-5 for c in new_footer.chars)
            new_image.crop((int(20*sx), int(803*sy), int(570*sx), int(830*sy))).save(scratch/f'footer-{i+1}.png')
            if i == 1:
                rect = (38, 315, 565, 739)
                expected = ' '.join(PARAGRAPHS[:5]+BULLETS+PARAGRAPHS[5:])
                body_old = original.pages[i].crop(rect)
                body_new = corrected.pages[i].crop(rect)
                assert tight(body_old.extract_text()) == tight(expected)
                assert ' '.join(body_new.extract_text().split()) == expected
                assert all('Georgia' in c['fontname'] and abs(c['size']-8.999791)<1e-5 for c in body_new.chars)
                assert all(c['x0'] >= 39.7 and c['x1'] <= 563.3 and c['bottom'] <= 736 for c in body_new.chars)
                new_image.crop((int(20*sx), int(298*sy), int(574*sx), int(763*sy))).save(scratch/'disclaimer.png')
            rows.append({'page': i+1, 'outside_requested_areas_pixel_identical': True,
                         'wording_without_whitespace_identical': True, 'font_and_size_preserved': True})
    result = {'status': 'pass', 'copy': validate_copy(target), 'pages': rows,
              'original_metadata_unchanged': True, 'production_changed': False}
    manifest = json.loads((folder/'manifest.json').read_text())
    manifest['independent_validation'] = result
    (folder/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
