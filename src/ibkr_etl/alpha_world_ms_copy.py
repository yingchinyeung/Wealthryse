"""Owner-supplied MS disclaimer using the approved MS typography.

No runtime host fonts, market data or calculation changes.
The original approved PDF asset remains untouched. Old glyphs are removed,
not just hidden, so copied/searchable text also has the corrected spacing.
"""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from pypdf import PageObject
from pypdf.generic import ArrayObject, ContentStream, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

COPY_VERSION = "alpha-world-ms-owner-disclaimer-v14r16"
FONT_SHA256 = 'b93db4ac2607fae9f26fe35142317b180dd540ffe000352deb0241a776c0713c'
BODY_LEADING = 12.
PARAGRAPH_GAP = 6.
FOOTER = (
    "This document is provided for informational purposes only and does not constitute an offer or solicitation to buy or sell any financial instrument. "
    "Past performance is not indicative of future results. Monte Carlo simulations are based on historical return distributions and are subject to model risk. "
    "All figures are unaudited. VaR and CVaR are calculated at the 95% confidence level on daily returns. "
    "The Equity Tranche exhibits leveraged characteristics and may result in significant capital loss. "
    "This document is intended for professional and institutional investors only."
)
PARAGRAPHS = (
    "This fact sheet is provided solely for informational purposes and relates exclusively to the methodology and indicative characteristics of the WealthRyse Enhanced Index. The Enhanced Index is a rules-based construct designed to reflect systematic index calculations. It is not, and should not be interpreted as, a financial product, investment vehicle, or any form of regulated offering.",
    "Enhanced Index is produced through an automated, formula-driven process based on objective data and established parameters. Its design is conceptually similar to widely used equity benchmarks in terms of rules-based calculation methodology. No investment discretion, judgment, or advice is involved in its creation or maintenance. All outputs are technical and intended for reference purposes only.",
    "This fact sheet relates to a rules-based index framework that corresponds to the methodology used in a separate index arrangement under which Sprout Asset Management (Asia) Limited acts as the Index Advisor. WealthRyse provides non-discretionary basket-composition inputs solely to Sprout under a separate agreement. Any parties referenced in the applicable Index Rules are not parties to this fact sheet and do not endorse or assume responsibility for its contents.",
    "This material does not constitute, and should not be construed as, an offer, solicitation, or recommendation regarding any security, financial instrument, or investment strategy. No regulatory approval has been sought or obtained for this material, and its distribution may be subject to legal or regulatory restrictions in certain jurisdictions. Recipients are responsible for ensuring compliance with all applicable laws and regulations.",
    "Index outputs may be shared with an execution manager or other authorized party for implementation purposes. These outputs are strictly formula-based and do not include investment advice, suitability assessments, or trading instructions. The execution manager retains full discretion and authority over all investment decisions, trade execution, and portfolio management. Under no circumstances should the provision of index outputs be construed as delegation of regulated activities or portfolio management.",
    "Recipients should consult directly with the execution manager to understand the execution process, associated risks, and any applicable regulatory or operational requirements. The execution manager is solely responsible for:",
    "The index calculation agent makes no representation or warranty, express or implied, as to its accuracy, completeness, or fitness for any particular purpose. All information is subject to change without notice. It shall have no liability for any direct, indirect, or consequential loss arising from reliance on this material. Recipients should not rely on this fact sheet for investment or other financial decisions and are encouraged to conduct independent analysis. Nothing herein should be regarded as financial, legal, tax, or other professional advice.",
)
BULLETS = (
    "Implementing and executing transactions in accordance with the client mandate and applicable law;",
    "Determining trade timing, sizing, and counterparties;",
    "Ensuring compliance with all licensing, regulatory, and fiduciary obligations;",
    "Managing portfolio risk exposures and maintaining oversight of investment decisions; and",
    "Communicating performance, strategy rationale, and risk assessments directly to clients.",
)
COPY_SHA256 = hashlib.sha256((FOOTER + "\n" + "\n".join(PARAGRAPHS + BULLETS)).encode()).hexdigest()
IDENTITY = (1., 0., 0., 1., 0., 0.)


def _multiply(a, b):
    return (a[0]*b[0]+a[1]*b[2], a[0]*b[1]+a[1]*b[3],
            a[2]*b[0]+a[3]*b[2], a[2]*b[1]+a[3]*b[3],
            a[4]*b[0]+a[5]*b[2]+b[4], a[4]*b[1]+a[5]*b[3]+b[5])


class _EmbeddedFont:
    def __init__(self, reference):
        self.reference = reference
        font = reference.get_object()
        self.standard = str(font.get('/BaseFont', '')).lstrip('/')
        if font.get('/Subtype') == '/Type1' and self.standard in ('Helvetica', 'Times-Roman'):
            return
        self.standard = None
        self.code_bytes = 1 if font.get('/Subtype') == '/TrueType' else 2
        if self.code_bytes == 2 and font.get('/Encoding') != '/Identity-H':
            raise ValueError('MS disclaimer font must have Identity-H encoding')
        cmap = font['/ToUnicode'].get_object().get_data().decode('ascii')
        self.codes = {}
        for block in re.findall(r'beginbfchar(.*?)endbfchar', cmap, re.S):
            for src, dst in re.findall(r'<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>', block):
                self.codes[bytes.fromhex(dst).decode('utf-16-be')] = int(src, 16)
        for block in re.findall(r'beginbfrange(.*?)endbfrange', cmap, re.S):
            for first, last, dst in re.findall(r'<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>', block):
                for code in range(int(first, 16), int(last, 16)+1):
                    self.codes[chr(int(dst, 16)+code-int(first, 16))] = code
        if self.code_bytes == 1:
            self.default_width = 0.
            self.widths = {int(font['/FirstChar'])+i:float(w) for i,w in enumerate(font['/Widths'])}
            return
        descendant = font['/DescendantFonts'][0].get_object()
        self.default_width = float(descendant.get('/DW', 1000))
        self.widths = {}
        widths = descendant.get('/W', [])
        if hasattr(widths, 'get_object'): widths = widths.get_object()
        i = 0
        while i < len(widths):
            start = int(widths[i]); values = widths[i+1]; i += 2
            if hasattr(values, 'get_object'): values = values.get_object()
            if isinstance(values, list):
                self.widths.update({start+j: float(v) for j, v in enumerate(values)})
            else:
                width = float(widths[i]); i += 1
                self.widths.update({j: width for j in range(start, int(values)+1)})

    def width(self, text, size):
        if self.standard:
            from reportlab.pdfbase.pdfmetrics import stringWidth
            return stringWidth(text, self.standard, size)
        return sum(self.widths.get(self.codes[c], self.default_width) for c in text)*size/1000

    def encode(self, text):
        if self.standard: return text.encode('cp1252').hex()
        return b''.join(self.codes[c].to_bytes(self.code_bytes, 'big') for c in text).hex()


def _body_font(original, owner):
    """Add the Georgia L and parentheses missing from the old MS subset."""
    if original.standard: return original  # synthetic test authorities
    path=Path(__file__).resolve().parent/'assets/alpha_world_ms_disclaimer_font.pdf'
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=FONT_SHA256:
        raise ValueError('MS disclaimer font authority differs')
    from pypdf import PdfReader
    import io
    reader=PdfReader(io.BytesIO(raw))
    ref=reader.pages[0]['/Resources']['/Font']['/MSDisclaimerGeorgia']
    font=_EmbeddedFont(ref.clone(owner))
    for char in original.codes:
        if char.isascii() and char.isprintable() and abs(font.width(char,9.)-original.width(char,9.))>.00001:
            raise ValueError('MS disclaimer typeface differs from its authority')
    if not all(char in font.codes for char in ''.join(PARAGRAPHS+BULLETS)):
        raise ValueError('MS disclaimer contains an unembedded glyph')
    return font


def _wrap(text, font, size, width):
    lines = []; line = ''
    for word in text.split():
        candidate = line + ' ' + word if line else word
        if font.width(candidate, size) <= width:
            line = candidate
        else:
            if not line or font.width(word, size) > width:
                raise ValueError('MS disclaimer word cannot fit its text area')
            lines.append(line); line = word
    if line: lines.append(line)
    return lines


def _repair_page(page, page_number):
    """Remove only glyphs in the two annotated regions, preserving vectors."""
    found = {}; removed = {'footer': 0, 'body': 0}
    owner = page.indirect_reference.pdf if page.indirect_reference else page.pdf

    def walk(stream, resources, transform, depth=0):
        if depth > 20: raise ValueError('MS disclaimer form nesting exceeds limit')
        content = ContentStream(stream, owner)
        cm = transform; tm = IDENTITY; stack = []; font_ref = None; size = 0.
        operations = []
        for args, op in content.operations:
            if op == b'q': stack.append((cm, tm, font_ref, size))
            elif op == b'Q': cm, tm, font_ref, size = stack.pop()
            elif op == b'cm': cm = _multiply(tuple(map(float, args)), cm)
            elif op == b'BT': tm = IDENTITY
            elif op == b'Tm': tm = tuple(map(float, args))
            elif op in (b'Td', b'TD'):
                tm = _multiply((1., 0., 0., 1., float(args[0]), float(args[1])), tm)
            elif op == b'Tf': font_ref = resources['/Font'][args[0]]; size = float(args[1])
            elif op == b'Do':
                child = resources.get('/XObject', {}).get(args[0])
                if child is not None:
                    child = child.get_object()
                    if child.get('/Subtype') == '/Form':
                        walk(child, child.get('/Resources', resources),
                             _multiply(tuple(map(float, child.get('/Matrix', IDENTITY))), cm), depth+1)
            elif op in (b'Tj', b'TJ'):
                matrix = _multiply(tm, cm); x, y = matrix[4:]
                region = ('footer' if 20 <= x <= 570 and 15 <= y <= 34 else
                          'body' if page_number == 1 and 38 <= x <= 565 and 104 <= y <= 527 else None)
                if region:
                    base = str(font_ref.get_object().get('/BaseFont'))
                    if (region == 'footer' and (base.endswith('+IBMPlexSans') or base == '/Helvetica') or
                            region == 'body' and (base.endswith('+Georgia') or base == '/Times-Roman')):
                        if region not in found:
                            found[region] = (_EmbeddedFont(font_ref), size*math.hypot(matrix[2], matrix[3]))
                    removed[region] += 1
                    # Preserve all state and relative word-position operators.
                    # Every glyph in these complete text areas is replaced below.
                    continue
            operations.append((args, op))
        content.operations = operations
        if depth == 0:
            page.replace_contents(content)
        else:
            stream.set_data(content.get_data())

    walk(page.get_contents(), page['/Resources'], IDENTITY)
    expected = ('footer', 'body') if page_number == 1 else ('footer',)
    if any(key not in found or not removed[key] for key in expected):
        raise ValueError('MS approved disclaimer font/region not found')
    overlay = PageObject.create_blank_page(width=float(page.mediabox.width), height=float(page.mediabox.height))
    fonts = DictionaryObject(); operations = ['q']
    if page_number == 1:
        # The completed authority also has rasterized list dots inside its
        # disclaimer illustration. Clear only the interior, not its border.
        operations.append('1 g 38 96 527 431 re f')
    operations.append('0 g')

    def line(text, font, size, name, x, y):
        fonts[NameObject(name)] = font.reference
        operations.append(f'BT {name} {size:.9f} Tf 1 0 0 1 {x:.6f} {y:.6f} Tm <{font.encode(text)}> Tj ET')

    font, size = found['footer']
    lines = _wrap(FOOTER, font, size, 533.8)
    if len(lines) != 3: raise ValueError('MS disclaimer footer must fit three lines')
    for i, text in enumerate(lines): line(text, font, size, '/MSCopyFooter', 22.947262, 28.907386-i*5.9856)
    if page_number == 1:
        original, size = found['body']; font = _body_font(original,owner); y = 516.170418
        for i, paragraph in enumerate(PARAGRAPHS):
            for text in _wrap(paragraph, font, size, 523.5):
                line(text, font, size, '/MSCopyBody', 39.7133, y); y -= BODY_LEADING
            y -= PARAGRAPH_GAP
            if i == 5:
                for bullet in BULLETS:
                    # A PDF vector dot avoids adding a host-dependent bullet font.
                    x = 43.2; radius = 1.15; k = radius*0.55228475; cy = y+size*.32
                    operations.extend((f'{x+radius:.6f} {cy:.6f} m',
                        f'{x+radius:.6f} {cy+k:.6f} {x+k:.6f} {cy+radius:.6f} {x:.6f} {cy+radius:.6f} c',
                        f'{x-k:.6f} {cy+radius:.6f} {x-radius:.6f} {cy+k:.6f} {x-radius:.6f} {cy:.6f} c',
                        f'{x-radius:.6f} {cy-k:.6f} {x-k:.6f} {cy-radius:.6f} {x:.6f} {cy-radius:.6f} c',
                        f'{x+k:.6f} {cy-radius:.6f} {x+radius:.6f} {cy-k:.6f} {x+radius:.6f} {cy:.6f} c f'))
                    for text in _wrap(bullet, font, size, 508.):
                        line(text, font, size, '/MSCopyBody', 55., y); y -= BODY_LEADING
                y -= PARAGRAPH_GAP
        if y+BODY_LEADING+PARAGRAPH_GAP < 100.: raise ValueError('MS main disclaimer exceeds its approved area')
    operations.append('Q')
    overlay[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): fonts})
    content = DecodedStreamObject(); content.set_data('\n'.join(operations).encode('ascii'))
    overlay[NameObject('/Contents')] = content
    # Keep disclosure vectors in their own Form, rather than mixing their
    # rectangle/dot paths into the independently validated allocation diagram.
    content.update({NameObject('/Type'): NameObject('/XObject'),
                    NameObject('/Subtype'): NameObject('/Form'),
                    NameObject('/FormType'): NumberObject(1),
                    NameObject('/BBox'): ArrayObject(list(page.mediabox)),
                    NameObject('/Resources'): overlay['/Resources']})
    xobjects = page['/Resources'].setdefault(NameObject('/XObject'), DictionaryObject())
    name = NameObject('/MSDisclaimerCopy')
    if name in xobjects: raise ValueError('MS disclaimer correction already present')
    xobjects[name] = owner._add_object(content)
    root = ContentStream(page.get_contents(), owner)
    root.operations += [([], b'q'), ([name], b'Do'), ([], b'Q')]
    page.replace_contents(root)
    return removed


def repair_disclaimers(pages):
    if len(pages) != 2: raise ValueError('MS factsheet must have two pages')
    return [_repair_page(page, i) for i, page in enumerate(pages)]


def validate_copy(path):
    """Check real searchable PDF copy, not just correction metadata."""
    from pypdf import PdfReader
    reader = PdfReader(path)
    if len(reader.pages) != 2: raise ValueError('MS factsheet must have two pages')
    for i, page in enumerate(reader.pages):
        text = re.sub(r'\s+', ' ', page.extract_text())
        if FOOTER not in text: raise ValueError(f'MS footer copy differs on page {i+1}')
        if i == 1 and any(item not in text for item in PARAGRAPHS + BULLETS):
            raise ValueError('MS disclaimer copy differs')
        if i == 1:
            form=page['/Resources']['/XObject'].get('/MSDisclaimerCopy')
            if form is None: raise ValueError('MS disclaimer copy form missing')
            actual=re.sub(r'\s+',' ',page.extract_xform_text(form.get_object())).strip()
            expected=' '.join((FOOTER,)+PARAGRAPHS[:6]+BULLETS+PARAGRAPHS[6:])
            if actual!=expected: raise ValueError('MS disclaimer copy order or completeness differs')
    return {'status': 'pass', 'version': COPY_VERSION, 'copy_sha256': COPY_SHA256}
