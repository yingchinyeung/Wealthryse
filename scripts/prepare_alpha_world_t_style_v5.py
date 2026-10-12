"""Freeze the accepted MS v14r16 typography; preserve the owner's T words."""
import hashlib
from pathlib import Path
from pypdf import PdfReader,PdfWriter
from pypdf.generic import NameObject
from prepare_alpha_world_t_ms_style import retain
from ibkr_etl.alpha_world_t_pdf_validation import _disclaimer,_copy_identity

ROOT=Path(__file__).resolve().parents[1]


def main():
    source=ROOT/'output/pdf/AlphaWorld_MS_Azure_Disclaimer_v14r16/AlphaWorld_MS_Factsheet_2026-09-30.pdf'
    old=ROOT/'templates/alpha-world-t/ms_style_sections_v4.pdf'
    target=ROOT/'templates/alpha-world-t/ms_style_sections_v5.pdf'
    t=PdfReader(ROOT/'templates/alpha-world-t/owner_template_20261008.pdf');ms=PdfReader(source)
    from ibkr_etl.alpha_world_ms_copy import PARAGRAPHS,BULLETS,validate_copy
    validate_copy(source)
    assert _copy_identity(PARAGRAPHS[:6]+BULLETS+PARAGRAPHS[6:])==_copy_identity([p.replace('\u2022','') for p in _disclaimer(t.pages[1])])
    # Bind to the accepted, website-downloaded MS artifact, not arbitrary PDFs.
    import json
    evidence=json.loads((source.parent/'verification.json').read_bytes())
    expected=next(r['sha256'] for r in evidence['rows'] if r['period_end']=='2026-09-30')
    assert hashlib.sha256(source.read_bytes()).hexdigest()==expected
    writer=PdfWriter(clone_from=ms)
    about=(20.,520.,357.,181.);heading=(386.,681.,196.,18.)
    footer=(0.,0.,595.6,58.);table=(387.,528.,177.,137.)
    labels=(20.,732.,557.,14.);header=(20.,719.,557.,30.)
    disclaimer=(20.,78.,558.,510.)
    retain(writer.pages[0],(about,heading,footer,labels),(table,footer),
           (about,heading,table,footer,header))
    retain(writer.pages[1],(footer,disclaimer),(footer,disclaimer),(footer,disclaimer))
    # Reuse the existing portable T strategy-title subset unchanged.
    previous=PdfReader(old)
    writer.pages[0]['/Resources']['/Font'][NameObject('/TStrategyArial')]=previous.pages[0]['/Resources']['/Font']['/TStrategyArial'].clone(writer)
    writer.metadata.clear()
    writer.add_metadata({'/Title':'Alpha World T MS v14r16 disclaimer and header style',
                         '/SourceSHA256':expected,'/TDisclaimerWordsPreserved':'true'})
    with target.open('xb') as stream:writer.write(stream)
    print(hashlib.sha256(target.read_bytes()).hexdigest())


if __name__=='__main__':main()
