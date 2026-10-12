"""Owner-confirmed corrections to two fixed texts in the deck template.

The slide 19 disclaimer heading reads "llustrative examples only", with
"llustrative" as its own run inside the disclaimer textbox, and the slide 20
contact email reads "inquiry@www.sprout-asset.com". Only the text of those
runs changes, so the template's fonts, colours and layout are kept. The
disclaimer wording itself is untouched.

Usage: python -m ibkr_etl.presentation_text_fixes in.pptx out.pptx
Prints a JSON receipt with the number of runs changed.
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

SLIDE = re.compile(r'ppt/slides/slide\d+\.xml')
EMAIL = ('<a:t>inquiry@www.sprout-asset.com</a:t>', '<a:t>inquiry@sprout-asset.com</a:t>')
# The heading run, followed (possibly after the next run's properties) by " examples only".
HEADING = re.compile(r'<a:t>llustrative(?= examples only</a:t>|</a:t></a:r><a:r>(?:<a:rPr[^>]*/>|<a:rPr[^>]*>.*?</a:rPr>)?<a:t> examples only</a:t>)')


def fix_xml(xml: str) -> tuple[str, int, int]:
    xml, heading = HEADING.subn('<a:t>Illustrative', xml)
    email = xml.count(EMAIL[0])
    return xml.replace(*EMAIL), heading, email


def fix_pptx(source: Path, target: Path) -> dict:
    heading = email = 0
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(target, 'w') as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if SLIDE.fullmatch(info.filename):
                xml, h, e = fix_xml(data.decode('utf-8'))
                if h or e:
                    data = xml.encode('utf-8')
                    heading += h
                    email += e
            zout.writestr(info, data)
    return {'status': 'passed', 'heading_runs_fixed': heading, 'email_runs_fixed': email}


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        raise SystemExit('usage: python -m ibkr_etl.presentation_text_fixes in.pptx out.pptx')
    print(json.dumps(fix_pptx(Path(args[0]), Path(args[1]))))


if __name__ == '__main__':
    main()
