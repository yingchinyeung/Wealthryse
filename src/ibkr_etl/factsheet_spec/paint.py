"""What a PDF page actually paints, independent of how its content stream was written.

Two pages that paint the same glyphs, paths and images in the same places, colours,
fonts and line styles are the same page to a reader, even when one stream sets its
font twice or saves the graphics state more often. ``paint_ops`` replays a page's
content (including form XObjects) and returns only what is painted, each with the
state in effect at that moment, so two renders can be compared fairly; ``text_runs``
returns the shown strings with their origin, font and size, which the generated
checks, the template reader and the builder's comparison all read.

Nothing here renders or rasterises; it is pure content-stream arithmetic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from pypdf import PdfReader
from pypdf.generic import ContentStream
from reportlab.pdfbase.pdfmetrics import standardFonts, stringWidth

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
MAX_FORM_DEPTH = 20
PAINT_TOLERANCE = 0.02


def multiply(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    """The PDF matrix product a x b (apply a, then b)."""
    return (
        a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5],
    )


def apply(m: tuple[float, ...], x: float, y: float) -> tuple[float, float]:
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


@dataclass
class _State:
    ctm: tuple[float, ...] = IDENTITY
    fill: tuple[str, tuple[float, ...]] = ("g", (0.0,))
    stroke: tuple[str, tuple[float, ...]] = ("g", (0.0,))
    line_width: float = 1.0
    dash: tuple[tuple[float, ...], float] = ((), 0.0)
    cap: int = 0
    join: int = 0
    font: str | None = None
    font_size: float = 0.0
    leading: float = 0.0
    char_spacing: float = 0.0
    word_spacing: float = 0.0
    horizontal_scale: float = 100.0
    rise: float = 0.0
    render_mode: int = 0
    ext: tuple[str, ...] = ()
    clip: tuple[Any, ...] = ()

    def copy(self) -> "_State":
        return _State(**self.__dict__)


@dataclass(frozen=True)
class TextRun:
    """One shown string: page-space origin, the font's BaseFont name and the effective size."""

    text: str
    x: float
    y: float
    size: float
    font: str | None
    fill: tuple[str, tuple[float, ...]]
    angle: float = 0.0
    width: float = 0.0  # page-space advance of the string, from the font's widths (estimated for composite fonts)


@dataclass
class PageScan:
    ops: list[tuple[Any, ...]] = field(default_factory=list)
    runs: list[TextRun] = field(default_factory=list)


def _round(value: float) -> float:
    return round(float(value), 2)


def _font_name(resources: Any, name: Any) -> str | None:
    try:
        font = resources["/Font"][name].get_object()
    except (KeyError, TypeError):
        return None
    return str(font.get("/BaseFont", "")).lstrip("/") or None


def _font_object(resources: Any, name: Any) -> Any:
    try:
        return resources["/Font"][name].get_object()
    except (KeyError, TypeError):
        return None


_TOKENS = re.compile(rb"<([0-9A-Fa-f\s]*)>|\[|\]|(begincodespacerange|endcodespacerange|beginbfchar|endbfchar|"
                     rb"beginbfrange|endbfrange)")


def _hex(value: bytes) -> bytes:
    digits = re.sub(rb"\s", b"", value)
    return bytes.fromhex((digits + b"0" * (len(digits) % 2)).decode("ascii"))


def _utf16(value: bytes) -> str:
    try:
        return value.decode("utf-16-be")
    except UnicodeDecodeError:
        return ""


def _raw_bytes(raw: Any) -> bytes:
    """The bytes a content stream showed, before pypdf decoded them as text."""
    if isinstance(raw, str):
        original = getattr(raw, "original_bytes", None)
        return bytes(original) if original is not None else raw.encode("latin-1", "replace")
    return bytes(raw)


class _FontInfo:
    """How one font turns shown bytes into text and widths: its ToUnicode map, code length and glyph widths."""

    def __init__(self, font: Any) -> None:
        self.font = font
        self.composite = font is not None and font.get("/Subtype") == "/Type0"
        self.code_length = 2 if self.composite else 1
        self.unicode: dict[bytes, str] = {}
        self.widths: dict[int, float] = {}
        self.default_width = 1000.0 if self.composite else 500.0
        self.standard: str | None = None
        self.base_font = str(font.get("/BaseFont", "")).lstrip("/") if font is not None else ""
        if font is None:
            return
        try:
            stream = font.get("/ToUnicode")
            if stream is not None:
                self._read_cmap(stream.get_object().get_data())
        except Exception:  # a broken map leaves the text as hex, as before
            self.unicode = {}
        try:
            self._read_widths(font)
        except Exception:  # unreadable widths fall back to the default advance
            self.widths = {}

    def _read_widths(self, font: Any) -> None:
        if self.composite:
            descendant = font["/DescendantFonts"][0].get_object()
            self.default_width = float(descendant.get("/DW", 1000))
            w = descendant.get("/W")
            items = [item.get_object() if hasattr(item, "get_object") else item
                     for item in (w.get_object() if w is not None else [])]
            i = 0
            while i + 1 < len(items):
                first = int(items[i])
                if isinstance(items[i + 1], list):
                    for offset, width in enumerate(items[i + 1]):
                        self.widths[first + offset] = float(width)
                    i += 2
                else:
                    for code in range(first, int(items[i + 1]) + 1):
                        self.widths[code] = float(items[i + 2])
                    i += 3
            return
        widths = font.get("/Widths")
        if widths is not None:
            first = int(font.get("/FirstChar", 0))
            for offset, width in enumerate(widths.get_object()):
                self.widths[first + offset] = float(width)
        elif self.base_font in standardFonts:
            self.standard = self.base_font

    def _read_cmap(self, data: bytes) -> None:
        section, pending, array = None, [], None
        for match in _TOKENS.finditer(data):
            keyword = match.group(2)
            if keyword:
                section = keyword[5:] if keyword.startswith(b"begin") else None
                pending = []
                continue
            token = match.group(0)
            if token == b"[":
                array = []
                continue
            if token == b"]":
                pending.append(array)
                array = None
            else:
                value = _hex(match.group(1))
                if array is not None:
                    array.append(value)
                    continue
                pending.append(value)
            if section == b"codespacerange" and len(pending) == 2:
                self.code_length = max(1, len(pending[0]))
                pending = []
            elif section == b"bfchar" and len(pending) == 2:
                self.unicode[pending[0]] = _utf16(pending[1])
                pending = []
            elif section == b"bfrange" and len(pending) == 3:
                low, high, target = pending
                start, stop = int.from_bytes(low, "big"), int.from_bytes(high, "big")
                for index, code in enumerate(range(start, min(stop, start + 65535) + 1)):
                    key = code.to_bytes(len(low), "big")
                    if isinstance(target, list):
                        if index < len(target):
                            self.unicode[key] = _utf16(target[index])
                    else:
                        number = int.from_bytes(target, "big") + index
                        self.unicode[key] = _utf16(number.to_bytes(len(target), "big"))
                pending = []

    def codes(self, data: bytes) -> list[bytes]:
        n = self.code_length
        return [data[i:i + n] for i in range(0, len(data), n)]

    def decode(self, raw: Any) -> str:
        data = _raw_bytes(raw)
        if self.unicode:
            return "".join(self.unicode.get(code, "�") for code in self.codes(data))
        if self.composite:
            return "<" + data.hex() + ">"
        return data.decode("latin-1")

    def width(self, raw: Any) -> float:
        """The string's advance in 1/1000 em."""
        data = _raw_bytes(raw)
        if self.standard:
            return stringWidth(data.decode("latin-1"), self.standard, 1000.0)
        return sum(self.widths.get(int.from_bytes(code, "big"), self.default_width) for code in self.codes(data))


_FONT_CACHE: dict[int, _FontInfo] = {}


def _font_info(font: Any) -> _FontInfo:
    info = _FONT_CACHE.get(id(font))
    if info is None or info.font is not font:
        if len(_FONT_CACHE) > 512:
            _FONT_CACHE.clear()
        info = _FONT_CACHE[id(font)] = _FontInfo(font)
    return info


def _decode(raw: Any, font: Any) -> str:
    """The shown string's text, through the font's ToUnicode map when it has one."""
    return _font_info(font).decode(raw)


def _shown_strings(op: bytes, args: list[Any]) -> list[Any]:
    if op in (b"Tj", b"'"):
        return [args[0]]
    if op == b'"':
        return [args[2]]
    if op == b"TJ":
        return [item for item in args[0] if not isinstance(item, (int, float)) and not hasattr(item, "as_numeric")]
    return []


def scan_page(page: Any) -> PageScan:
    """Replay a page and collect its painting operations and text runs."""
    result = PageScan()
    pdf = page.pdf if hasattr(page, "pdf") else None
    contents = page.get_contents()
    if contents is None:
        return result

    def walk(stream: Any, resources: Any, base: _State, depth: int) -> None:
        if depth > MAX_FORM_DEPTH:
            raise ValueError("Form XObjects nest too deeply")
        content = ContentStream(stream, pdf)
        state = base.copy()
        stack: list[_State] = []
        tm = tlm = IDENTITY
        path: list[tuple[Any, ...]] = []
        pending_clip = False
        for args, op in content.operations:
            if op == b"q":
                stack.append(state.copy())
            elif op == b"Q":
                if stack:
                    state = stack.pop()
            elif op == b"cm":
                state.ctm = multiply(tuple(map(float, args)), state.ctm)
            elif op in (b"g", b"rg", b"k", b"sc", b"scn"):
                state.fill = (op.decode(), tuple(_round(a) for a in args if not hasattr(a, "startswith")))
            elif op in (b"G", b"RG", b"K", b"SC", b"SCN"):
                state.stroke = (op.decode(), tuple(_round(a) for a in args if not hasattr(a, "startswith")))
            elif op == b"cs":
                state.fill = (str(args[0]), ())
            elif op == b"CS":
                state.stroke = (str(args[0]), ())
            elif op == b"w":
                state.line_width = float(args[0])
            elif op == b"d":
                state.dash = (tuple(_round(a) for a in args[0]), _round(args[1]))
            elif op == b"J":
                state.cap = int(args[0])
            elif op == b"j":
                state.join = int(args[0])
            elif op == b"gs":
                state.ext = state.ext + (str(args[0]),)
            elif op == b"BT":
                tm = tlm = IDENTITY
            elif op == b"Tf":
                state.font = str(args[0])
                state.font_size = float(args[1])
            elif op == b"TL":
                state.leading = float(args[0])
            elif op == b"Tc":
                state.char_spacing = float(args[0])
            elif op == b"Tw":
                state.word_spacing = float(args[0])
            elif op == b"Tz":
                state.horizontal_scale = float(args[0])
            elif op == b"Ts":
                state.rise = float(args[0])
            elif op == b"Tr":
                state.render_mode = int(args[0])
            elif op == b"Tm":
                tm = tlm = tuple(map(float, args))
            elif op in (b"Td", b"TD"):
                if op == b"TD":
                    state.leading = -float(args[1])
                tm = tlm = multiply((1.0, 0.0, 0.0, 1.0, float(args[0]), float(args[1])), tlm)
            elif op == b"T*":
                tm = tlm = multiply((1.0, 0.0, 0.0, 1.0, 0.0, -state.leading), tlm)
            elif op in (b"Tj", b"TJ", b"'", b'"'):
                if op in (b"'", b'"'):
                    if op == b'"':
                        state.word_spacing, state.char_spacing = float(args[0]), float(args[1])
                    tm = tlm = multiply((1.0, 0.0, 0.0, 1.0, 0.0, -state.leading), tlm)
                font_obj = _font_object(resources, state.font)
                base_font = _font_name(resources, state.font)
                matrix = multiply(tm, state.ctm)
                x, y = matrix[4], matrix[5]
                scale = (matrix[2] ** 2 + matrix[3] ** 2) ** 0.5
                size = state.font_size * scale
                angle = 0.0
                if abs(matrix[1]) > 1e-9 or matrix[0] < 0:
                    import math
                    angle = round(math.degrees(math.atan2(matrix[1], matrix[0])), 1)
                shown = "".join(_decode(s, font_obj) for s in _shown_strings(op, args))
                if op == b"TJ":
                    shown_key = tuple(
                        _round(item) if isinstance(item, (int, float)) or hasattr(item, "as_numeric")
                        else _decode(item, font_obj) for item in args[0]
                    )
                else:
                    shown_key = (shown,)
                result.ops.append((
                    "text", shown_key, _round(x), _round(y), _round(size), base_font, angle,
                    state.fill, state.stroke if state.render_mode in (1, 2, 5, 6) else None,
                    state.render_mode, _round(state.char_spacing), _round(state.word_spacing),
                    _round(state.horizontal_scale), _round(state.rise), state.ext, state.clip,
                ))
                if shown.strip():
                    advance = sum(_font_info(font_obj).width(item) for item in _shown_strings(op, args)) / 1000.0
                    if op == b"TJ":
                        advance -= sum(float(item) for item in args[0]
                                       if isinstance(item, (int, float)) or hasattr(item, "as_numeric")) / 1000.0
                    width = advance * size * state.horizontal_scale / 100.0
                    result.runs.append(TextRun(shown.strip(), _round(x), _round(y), _round(size), base_font,
                                               state.fill, angle, _round(width)))
                # Advance is not tracked: every renderer here positions each string itself.
            elif op == b"m":
                path.append(("m",) + apply(state.ctm, float(args[0]), float(args[1])))
            elif op == b"l":
                path.append(("l",) + apply(state.ctm, float(args[0]), float(args[1])))
            elif op == b"c":
                points = [apply(state.ctm, float(args[i]), float(args[i + 1])) for i in (0, 2, 4)]
                path.append(("c",) + tuple(v for p in points for v in p))
            elif op in (b"v", b"y"):
                points = [apply(state.ctm, float(args[i]), float(args[i + 1])) for i in (0, 2)]
                path.append((op.decode(),) + tuple(v for p in points for v in p))
            elif op == b"h":
                path.append(("h",))
            elif op == b"re":
                x, y, w, h = map(float, args)
                corners = [apply(state.ctm, px, py) for px, py in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))]
                path.append(("re",) + tuple(v for p in corners for v in p))
            elif op in (b"W", b"W*"):
                pending_clip = True
            elif op in (b"f", b"F", b"f*", b"S", b"s", b"B", b"B*", b"b", b"b*", b"n"):
                geometry = tuple(tuple(_round(v) if not isinstance(v, str) else v for v in segment) for segment in path)
                if pending_clip:
                    state.clip = state.clip + (geometry,)
                    pending_clip = False
                if op != b"n":
                    fills = op in (b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*")
                    strokes = op in (b"S", b"s", b"B", b"B*", b"b", b"b*")
                    scale = (abs(state.ctm[0] * state.ctm[3] - state.ctm[1] * state.ctm[2])) ** 0.5
                    result.ops.append((
                        "path", op.decode(), geometry,
                        state.fill if fills else None,
                        state.stroke if strokes else None,
                        _round(state.line_width * scale) if strokes else None,
                        state.dash if strokes else None,
                        state.cap if strokes else None, state.join if strokes else None,
                        state.ext, state.clip,
                    ))
                path = []
            elif op == b"Do":
                xobject = resources.get("/XObject", {}).get(args[0]) if resources else None
                if xobject is None:
                    continue
                xobject = xobject.get_object()
                subtype = xobject.get("/Subtype")
                if subtype == "/Form":
                    child = state.copy()
                    child.ctm = multiply(tuple(map(float, xobject.get("/Matrix", IDENTITY))), state.ctm)
                    walk(xobject, xobject.get("/Resources", resources), child, depth + 1)
                else:
                    import hashlib
                    digest = hashlib.sha256(xobject.get_data()).hexdigest()[:16]
                    result.ops.append(("image", digest, tuple(_round(v) for v in state.ctm), state.ext, state.clip))
            elif op == b"BI" or op == b"INLINE IMAGE":
                result.ops.append(("inline-image", tuple(_round(v) for v in state.ctm), state.clip))
            elif op == b"sh":
                result.ops.append(("shading", str(args[0]), tuple(_round(v) for v in state.ctm), state.clip))

    walk(contents, page.get("/Resources", {}), _State(), 0)
    return result


def paint_ops(page: Any) -> list[tuple[Any, ...]]:
    return scan_page(page).ops


def text_runs(page: Any) -> list[TextRun]:
    return scan_page(page).runs


def document_scans(path_or_reader: Any) -> list[PageScan]:
    reader = path_or_reader if isinstance(path_or_reader, PdfReader) else PdfReader(str(path_or_reader))
    return [scan_page(page) for page in reader.pages]


def _close(a: Any, b: Any, tolerance: float) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) <= tolerance
        except (TypeError, ValueError):
            return False
    if isinstance(a, tuple) and isinstance(b, tuple):
        return len(a) == len(b) and all(_close(x, y, tolerance) for x, y in zip(a, b))
    return a == b


def _visual(op: tuple[Any, ...]) -> tuple[Any, ...]:
    """The parts of an op a reader can see: drop ExtGState and clip names, which renders rename freely."""
    if op[0] == "text":
        return op[:14]
    if op[0] == "path":
        return op[:9]
    return op[:3]


def first_difference(left: Iterable[tuple[Any, ...]], right: Iterable[tuple[Any, ...]],
                     tolerance: float = PAINT_TOLERANCE) -> tuple[int, Any, Any] | None:
    """The first painted operation that differs between two pages, or None when they paint the same."""
    a, b = [_visual(op) for op in left], [_visual(op) for op in right]
    for index, (x, y) in enumerate(zip(a, b)):
        if not _close(x, y, tolerance):
            return index, x, y
    if len(a) != len(b):
        index = min(len(a), len(b))
        return index, a[index] if index < len(a) else None, b[index] if index < len(b) else None
    return None
