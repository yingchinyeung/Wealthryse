"""Exact approved MS v14r16 sections, preserving T's disclosure wording."""
import hashlib
from pathlib import Path

from pypdf import PdfReader, PageObject
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, ArrayObject, ContentStream

from .alpha_world_ms_copy import _EmbeddedFont

STYLE_SHA256='c135be0c7208a3ceddc0dee7f8c64f38f2546a7d9e5ae701cd510c4ce1bb2278'
STYLE_PATH=Path(__file__).resolve().parents[2]/'templates/alpha-world-t/ms_style_sections_v6.pdf'
KPI_TITLE_COLOR=(0.3216,0.3373,0.3686)
KPI_COLOR_PROFILE_SHA256='2b3aa1645779a9e634744faf9b01e9102b0c9b88fd6deced7934df86b949af7e'


def strategy_color_space(style):
    """Use the source MS ICC profile, not an approximate device RGB gray."""
    visited=set()
    def walk(resources):
        for ref in resources.get('/ColorSpace',{}).values():
            space=ref.get_object()
            if isinstance(space,ArrayObject) and str(space[0])=='/ICCBased':
                if hashlib.sha256(space[1].get_object().get_data()).hexdigest()==KPI_COLOR_PROFILE_SHA256:
                    return ref
        for ref in resources.get('/XObject',{}).values():
            obj=ref.get_object()
            if id(obj) in visited: continue
            visited.add(id(obj))
            if obj.get('/Subtype')=='/Form':
                found=walk(obj.get('/Resources',{}))
                if found is not None: return found
        return None
    for page in style.pages:
        found=walk(page['/Resources'])
        if found is not None: return found
    raise ValueError('Missing approved MS KPI title color profile')


def load_style():
    if hashlib.sha256(STYLE_PATH.read_bytes()).hexdigest()!=STYLE_SHA256:
        raise ValueError('T MS-style authority hash differs')
    return PdfReader(STYLE_PATH)


def merge_static_sections(page,style,page_number):
    # Isolate static authority vectors from the root, independently checked
    # T chart and allocation vectors, just as the MS disclosure renderer does.
    source=style.pages[page_number]; owner=page.pdf
    form=DecodedStreamObject(); form.set_data(source.get_contents().get_data())
    form.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Form'),
                 NameObject('/BBox'):ArrayObject(list(source.mediabox)),
                 NameObject('/Resources'):source['/Resources'].clone(owner)})
    name=NameObject('/TStaticMSStyle')
    page['/Resources'].setdefault(NameObject('/XObject'),DictionaryObject())[name]=owner._add_object(form)
    stream=ContentStream(page.get_contents(),owner)
    stream.operations += [([],b'q'),([name],b'Do'),([],b'Q')]
    page.replace_contents(stream)


def find_font(reader, family):
    found=[]; visited=set()
    def walk(resources):
        for ref in resources.get('/Font',{}).values():
            font=ref.get_object()
            if str(font.get('/BaseFont','')).endswith('+'+family):
                found.append(_AnsiFont(ref) if font.get('/Subtype')=='/TrueType' else _EmbeddedFont(ref))
        for ref in resources.get('/XObject',{}).values():
            obj=ref.get_object()
            if id(obj) in visited: continue
            visited.add(id(obj))
            if obj.get('/Subtype')=='/Form': walk(obj.get('/Resources',{}))
    for page in reader.pages: walk(page['/Resources'])
    if not found: raise ValueError('Missing MS embedded font: '+family)
    return max(found,key=lambda font:len(font.codes))


class _AnsiFont:
    def __init__(self,ref):
        self.reference=ref
        font=ref.get_object()
        self.codes={chr(i):i for i in range(int(font['/FirstChar']),int(font['/LastChar'])+1)
                    if float(font['/Widths'][i-int(font['/FirstChar'])])>0}
    def encode(self,text):
        return text.encode('cp1252').hex()


def add_native_text(page, style, page_number):
    if page_number!=0: return
    # Preserve T identity with the exact embedded MS Arial Bold strategy font.
    font=find_font(style,'Arial-BoldMT')
    if not all(c in font.codes for c in 'Alpha World T Enhanced Index'):
        raise ValueError('T strategy title contains an unembedded glyph')
    overlay=PageObject.create_blank_page(width=float(page.mediabox.width),height=float(page.mediabox.height))
    overlay[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):
        DictionaryObject({NameObject('/TStrategy'):font.reference}),NameObject('/ColorSpace'):
        DictionaryObject({NameObject('/TStrategyColor'):strategy_color_space(style)})})
    content=DecodedStreamObject()
    color=' '.join(format(v,'.4f') for v in KPI_TITLE_COLOR)
    content.set_data(('q /TStrategyColor cs '+color+' scn BT /TStrategy 6.937338929 Tf '
        '1 0 0 1 390.779 672.223 Tm <'+font.encode('Alpha World T Enhanced Index')+'> Tj ET Q').encode('ascii'))
    overlay[NameObject('/Contents')]=content
    page.merge_page(overlay)
