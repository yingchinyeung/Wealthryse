"""Freeze requested MS style sections, excluding the T main disclaimer.

One-time local asset preparation, not part of rendering or deployment.
"""
import argparse
import hashlib
import io
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, FloatObject, NameObject
from ibkr_etl.alpha_world_ms_copy import IDENTITY, _multiply


def inside(x,y,boxes):
    return any(a<=x<=a+w and b<=y<=b+h for a,b,w,h in boxes)


def retain(page,text_boxes,image_boxes,clip_boxes):
    def walk(stream,resources,transform,root=False):
        content=ContentStream(stream,page.pdf)
        cm,tm,stack,out=transform,IDENTITY,[],[]
        for args,op in content.operations:
            if op==b'q': stack.append((cm,tm))
            elif op==b'Q': cm,tm=stack.pop()
            elif op==b'cm': cm=_multiply(tuple(map(float,args)),cm)
            elif op==b'BT': tm=IDENTITY
            elif op==b'Tm': tm=tuple(map(float,args))
            elif op in (b'Td',b'TD'): tm=_multiply((1.,0.,0.,1.,float(args[0]),float(args[1])),tm)
            elif op in (b'Tj',b'TJ'):
                m=_multiply(tm,cm)
                if not inside(m[4],m[5],text_boxes): continue
            elif op==b'Do':
                obj=resources['/XObject'][args[0]].get_object()
                if obj.get('/Subtype')=='/Form':
                    walk(obj,obj.get('/Resources',resources),_multiply(tuple(map(float,obj.get('/Matrix',IDENTITY))),cm))
                elif obj.get('/Subtype')=='/Image':
                    if not inside(cm[4]+.5*cm[0]+.5*cm[2],cm[5]+.5*cm[1]+.5*cm[3],image_boxes): continue
            out.append((args,op))
        if root:
            clip=[([],b'q')]+[([FloatObject(v) for v in box],b're') for box in clip_boxes]+[([],b'W'),([],b'n')]
            out=clip+out+[([],b'Q')]
        content.operations=out
        if root: page.replace_contents(content)
        else: stream.set_data(content.get_data())
    walk(page.get_contents(),page['/Resources'],IDENTITY,True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--strategy-font',required=True,help='Arial Bold used once to embed the T title glyph subset')
    args=parser.parse_args()
    target=Path(args.output)
    if target.exists(): raise ValueError('Do not overwrite an existing style authority')
    writer=PdfWriter(clone_from=PdfReader(args.source))
    about=(20.,520.,357.,181.)
    heading=(386.,681.,196.,18.)
    footer=(0.,0.,595.6,58.)
    table=(387.,528.,177.,137.)
    retain(writer.pages[0],(about,heading,footer),(table,footer),(about,heading,table,footer))
    retain(writer.pages[1],(footer,),(footer,),(footer,))
    # Existing MS/T subsets omit the capital T in the new T-specific title.
    # Include a portable Arial Bold subset, with MS-matching glyph advances.
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    from ibkr_etl.alpha_world_t_sections import find_font
    ms_font=find_font(PdfReader(args.source),'Arial-BoldMT')
    pdfmetrics.registerFont(TTFont('TStrategyArial',args.strategy_font))
    for char in 'Alpha World Enhanced Index':
        if abs(pdfmetrics.stringWidth(char,'TStrategyArial',7)-ms_font.width(char,7))>.004:
            raise ValueError('Strategy typeface does not match MS Arial Bold')
    buffer=io.BytesIO(); c=canvas.Canvas(buffer)
    c.setFont('TStrategyArial',7);c.drawString(0,0,'Alpha World T Enhanced Index');c.save()
    subset=PdfReader(buffer)
    for ref in subset.pages[0]['/Resources']['/Font'].values():
        if str(ref.get_object().get('/BaseFont','')).endswith('+Arial-BoldMT'):
            writer.pages[0]['/Resources']['/Font'][NameObject('/TStrategyArial')]=ref.clone(writer)
    writer.metadata.clear()
    writer.add_metadata({'/Title':'Alpha World T static MS-style sections (no main disclaimer)',
                         '/SourceSHA256':hashlib.sha256(Path(args.source).read_bytes()).hexdigest()})
    with target.open('xb') as stream: writer.write(stream)
    print(hashlib.sha256(target.read_bytes()).hexdigest())


if __name__=='__main__': main()
