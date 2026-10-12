"""Owner-requested MS subtitle, using the authority's embedded Arial font.

T's subtitle uses ArialMT 9.025 pt. Retain that embedded subset in a small,
hash-bound resource rather than rely on an incomplete MS subset or host font.
"""
import math
import hashlib
import io
from pathlib import Path

from pypdf import PageObject, PdfReader
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from .alpha_world_ms_copy import _EmbeddedFont, _multiply

TEXT = 'Multi-Tranche Quantitative strategy'
VERSION = 'alpha-world-ms-subtitle-v14r14'
SIZE = 9.025
X = 22.84536
Y = 767.9968152  # Same 14.4-point title/subtitle baseline gap as T.
FONT_SHA256 = 'bfe50ba93225363b632ec7cbe75526819dd7cd692ef77763a8afdb3e54542918'
FONT_PROGRAM_SHA256 = 'abb40c26801f1d70d3b03c3b0682eaa85fa97a084609f5714b742a8bf47a63f6'


class _SubtitleFont(_EmbeddedFont):
    def __init__(self, reference):
        font = reference.get_object()
        self.win_ansi = (font.get('/Subtype') == '/TrueType'
                         and font.get('/Encoding') == '/WinAnsiEncoding')
        if self.win_ansi:
            self.reference = reference
        else:
            super().__init__(reference)

    def encode(self, text):
        return text.encode('cp1252').hex() if self.win_ansi else super().encode(text)


def _font(resources, depth=0):
    if depth > 20:
        raise ValueError('MS subtitle font resource nesting exceeds limit')
    for reference in resources.get('/Font', {}).values():
        font = reference.get_object()
        if str(font.get('/BaseFont', '')).split('+')[-1] == 'ArialMT':
            result = _SubtitleFont(reference)
            try:
                result.encode(TEXT)
            except KeyError:
                continue
            return result
    for child in resources.get('/XObject', {}).values():
        child = child.get_object()
        if child.get('/Subtype') == '/Form':
            result = _font(child.get('/Resources', resources), depth+1)
            if result is not None:
                return result
    return None


def apply(page):
    raw = (Path(__file__).parent/'assets/alpha_world_subtitle_font.pdf').read_bytes()
    if hashlib.sha256(raw).hexdigest() != FONT_SHA256:
        raise ValueError('Owner subtitle font resource identity differs')
    reader = PdfReader(io.BytesIO(raw))
    font = _font(reader.pages[0]['/Resources'])
    if font is None:
        raise ValueError('Owner resource lacks Arial required to match T subtitle')
    overlay = PageObject.create_blank_page(width=float(page.mediabox.width), height=float(page.mediabox.height))
    overlay[NameObject('/Resources')] = DictionaryObject({
        NameObject('/Font'): DictionaryObject({NameObject('/MSSubtitle'): font.reference})})
    stream = DecodedStreamObject()
    stream.set_data((f'q 0 g BT /MSSubtitle {SIZE} Tf 1 0 0 1 {X} {Y} Tm '
                     f'<{font.encode(TEXT)}> Tj ET Q').encode('ascii'))
    overlay[NameObject('/Contents')] = stream
    page.merge_page(overlay)
    validate(page)


def validate(page):
    matches = []
    def visitor(text, cm, tm, font, size):
        matrix = _multiply(tm, cm)
        if text.strip() == TEXT:
            descriptor = font.get('/FontDescriptor', {})
            if hasattr(descriptor, 'get_object'): descriptor = descriptor.get_object()
            program = descriptor.get('/FontFile2')
            digest = hashlib.sha256(program.get_object().get_data()).hexdigest() if program else None
            matches.append((matrix[4], matrix[5], size*math.hypot(matrix[2], matrix[3]),
                            str(font.get('/BaseFont', '')).split('+')[-1], digest))
    page.extract_text(visitor_text=visitor)
    if (len(matches) != 1 or matches[0][3] != 'ArialMT'
            or matches[0][4] != FONT_PROGRAM_SHA256
            or any(abs(a-b) > .0001 for a,b in zip(matches[0][:3], (X,Y,SIZE)))):
        raise ValueError('MS subtitle wording, Arial font, size or placement differs')
    return {'status': 'pass', 'font': 'ArialMT', 'size': SIZE, 'version': VERSION,
            'font_program_sha256': FONT_PROGRAM_SHA256}
