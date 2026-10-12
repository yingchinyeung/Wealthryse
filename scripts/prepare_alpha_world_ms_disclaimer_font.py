"""Bundle an embeddable Georgia subset with glyph advances matching MS.

One-time authoring utility. Monthly rendering never reads host font files.
"""
import argparse
import hashlib
import io
import struct
from pathlib import Path

from pypdf import PdfReader,PdfWriter
from pypdf.generic import DictionaryObject,NameObject
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from ibkr_etl.alpha_world_ms_copy import _EmbeddedFont


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--font',required=True)
    parser.add_argument('--authority',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    target=Path(args.output)
    if target.exists(): raise ValueError('Existing font authority must not be overwritten')
    raw=Path(args.font).read_bytes()
    tables=struct.unpack('>H',raw[4:6])[0]
    for i in range(tables):
        tag,_,offset,length=struct.unpack('>4sIII',raw[12+i*16:28+i*16])
        if tag==b'OS/2':
            flags=struct.unpack('>H',raw[offset+8:offset+10])[0]
            if flags & (0x2|0x100|0x200): raise ValueError('Font embedding/subsetting is restricted')
    authority=PdfReader(args.authority); fonts=[]
    def walk(resources):
        for ref in resources.get('/Font',{}).values():
            if str(ref.get_object().get('/BaseFont','')).endswith('+Georgia'): fonts.append(_EmbeddedFont(ref))
        for ref in resources.get('/XObject',{}).values():
            obj=ref.get_object()
            if obj.get('/Subtype')=='/Form': walk(obj.get('/Resources',{}))
    for page in authority.pages: walk(page['/Resources'])
    original=max(fonts,key=lambda f:len(f.codes))
    pdfmetrics.registerFont(TTFont('MSDisclaimerGeorgia',args.font))
    for char in original.codes:
        if char.isascii() and char.isprintable() and abs(pdfmetrics.stringWidth(char,'MSDisclaimerGeorgia',9)-original.width(char,9))>.00001:
            raise ValueError('Georgia does not match MS glyph advances')
    buffer=io.BytesIO(); c=canvas.Canvas(buffer)
    c.setFont('MSDisclaimerGeorgia',9);c.drawString(0,0,''.join(chr(i) for i in range(32,127)));c.save()
    source=PdfReader(buffer);writer=PdfWriter();page=writer.add_blank_page(width=595.276,height=841.89)
    matches=[ref for ref in source.pages[0]['/Resources']['/Font'].values()
             if str(ref.get_object().get('/BaseFont','')).endswith('+Georgia')]
    if len(matches)!=1: raise ValueError('Expected one Georgia subset')
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):
        DictionaryObject({NameObject('/MSDisclaimerGeorgia'):matches[0].clone(writer)})})
    writer.add_metadata({'/Title':'MS disclaimer Georgia glyph supplement',
        '/AuthoritySHA256':hashlib.sha256(Path(args.authority).read_bytes()).hexdigest(),
        '/FontSourceSHA256':hashlib.sha256(raw).hexdigest()})
    with target.open('xb') as stream:writer.write(stream)
    print(hashlib.sha256(target.read_bytes()).hexdigest())


if __name__=='__main__':main()
