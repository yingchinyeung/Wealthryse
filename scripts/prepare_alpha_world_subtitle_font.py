"""Extract the owner's existing subtitle Arial subset; no host-font substitution."""
from pathlib import Path
import hashlib
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, DictionaryObject
from ibkr_etl.alpha_world_ms_subtitle import _font
from ibkr_etl.alpha_world_t_factsheet import AUTHORITY_SHA256

ROOT=Path(__file__).resolve().parents[1]


def main():
    source=ROOT/'templates/alpha-world-t/owner_template_20261008.pdf'
    raw=source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=AUTHORITY_SHA256:
        raise ValueError('Owner font source identity differs')
    reader=PdfReader(source); font=_font(reader.pages[0]['/Resources'])
    writer=PdfWriter(); page=writer.add_blank_page(width=595,height=842)
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):
        DictionaryObject({NameObject('/SubtitleArial'):font.reference.clone(writer)})})
    writer.add_metadata({'/Title':'Owner Alpha World subtitle font resource',
                         '/SourceAuthoritySHA256':AUTHORITY_SHA256})
    target=ROOT/'src/ibkr_etl/assets/alpha_world_subtitle_font.pdf'
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('xb') as stream: writer.write(stream)
    print(hashlib.sha256(target.read_bytes()).hexdigest())


if __name__=='__main__': main()
