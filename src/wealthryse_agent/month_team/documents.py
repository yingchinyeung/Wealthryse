"""Read the package's review files: download, verify, extract text, render pages.

Files are read into memory or a private temporary folder and discarded; the
team stores only what it concluded from them.
"""
from __future__ import annotations

import hashlib
import io
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Callable

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_PAGES = 12
_SLIDE = re.compile(r"ppt/slides/slide(\d+)\.xml\Z")
_PARAGRAPH = re.compile(r"<a:p[ >].*?</a:p>", re.S)
_RUN_TEXT = re.compile(r"<a:t>([^<]*)</a:t>")


class DocumentError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


Downloader = Callable[[str, str, str], bytes]


def default_downloader(account_url: str, container: str, blob_name: str) -> bytes:
    from ibkr_etl.cloud_storage import download_blob_bytes

    return download_blob_bytes(account_url, container, blob_name, maximum_bytes=MAX_FILE_BYTES)


@dataclass(frozen=True)
class ReviewFile:
    report_type: str
    generation_run_id: int
    content: bytes


def fetch(file: dict, *, account_url: str, expected_container: str, download: Downloader) -> ReviewFile:
    """Download one package file and prove it is the file the package recorded."""
    container, _, blob_name = str(file.get("output_reference") or "").partition("/")
    if container != expected_container or not blob_name.startswith("review/"):
        raise DocumentError("unexpected_location", "The package file is not in the private review area.")
    content = download(account_url, container, blob_name)
    if hashlib.sha256(content).hexdigest() != str(file.get("output_sha256") or "").lower():
        raise DocumentError("hash_mismatch", "The stored file does not match the hash recorded when it was built.")
    return ReviewFile(str(file["report_type"]), int(file["generation_run_id"]), content)


def pdf_text(content: bytes, *, max_pages: int = MAX_PAGES, label: str = "factsheet PDF") -> list[str]:
    """Each page's text runs, joined by newlines, in drawing order."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content))
        pages = []
        for page in reader.pages[:max_pages]:
            runs: list[str] = []
            page.extract_text(visitor_text=lambda text, *_: runs.append(text.strip()) if text.strip() else None)
            pages.append("\n".join(runs))
        return pages
    except Exception as error:  # pypdf raises many types for damaged files
        raise DocumentError("unreadable_pdf", f"The {label} could not be read.") from error


def pdf_page_images(content: bytes, *, dpi: int = 110) -> list[bytes]:
    """PNG images of each page, rendered with poppler's pdftoppm."""
    with tempfile.TemporaryDirectory(prefix="month-team-") as directory:
        source = Path(directory) / "file.pdf"
        source.write_bytes(content)
        try:
            subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-l", str(MAX_PAGES), str(source),
                            str(Path(directory) / "page")], check=True, capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as error:
            raise DocumentError("render_failed", "The factsheet pages could not be rendered to images.") from error
        images = sorted(Path(directory).glob("page-*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
        return [image.read_bytes() for image in images]


def pptx_text(content: bytes) -> list[str]:
    """Each slide's text, one paragraph per line, in slide order."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            slides = sorted(((int(m.group(1)), name) for name in archive.namelist() if (m := _SLIDE.match(name))))
            texts = []
            for _, name in slides[:60]:
                xml = archive.read(name).decode("utf-8", errors="replace")
                lines = ["".join(unescape(t) for t in _RUN_TEXT.findall(p)).strip() for p in _PARAGRAPH.findall(xml)]
                texts.append("\n".join(line for line in lines if line))
            return texts
    except (zipfile.BadZipFile, KeyError) as error:
        raise DocumentError("unreadable_pptx", "The presentation could not be read.") from error
