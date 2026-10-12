"""Offline, fail-fast check for the complete report container (no cloud calls)."""
from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

from pypdf import PdfReader


TEMPLATES = (
    "U16088571/approved-august-2026-20-slide-v24.pptx",
    "U16088571/approved-august-2026-factsheet-v6.pdf",
    "U16088571/approved-august-2026-factsheet-template.pdf",
    "U16088606/intake/monthly factsheet template.pdf",
    "U16088606/intake/monthly presentation deck template.pptx",
    "U16088606/intake/previously completed factsheet.pdf",
    "U16088606/intake/template-intake-manifest.json",
)


def check_templates(root: Path) -> None:
    for name in TEMPLATES:
        path = root / "templates" / name
        if not path.is_file():
            raise RuntimeError(f"Report runtime is incomplete: missing {name}")
        if path.suffix == ".pdf":
            if len(PdfReader(path).pages) < 1:
                raise RuntimeError(f"Empty PDF template: {name}")
        elif path.suffix == ".pptx":
            with zipfile.ZipFile(path) as deck:
                if deck.testzip() or "ppt/presentation.xml" not in deck.namelist():
                    raise RuntimeError(f"Invalid presentation template: {name}")
        else:
            json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    check_templates(root)
    # Execute from scripts so module resolution matches the real renderer.
    # Canvas rasterization exercises its platform-specific native binary too.
    subprocess.run(
        ["node", "--input-type=module", "-e",
         "import {FileBlob, PresentationFile} from '@oai/artifact-tool';"
         "import {Canvas} from 'skia-canvas';"
         "if (!FileBlob || !PresentationFile) throw Error('Missing PPTX API');"
         "const c=new Canvas(16,16); c.getContext('2d').fillRect(0,0,16,16);"
         "if (!(await c.toBuffer('png')).length) throw Error('Canvas failed');"],
        cwd=root / "scripts", check=True, timeout=120,
    )
    print("PASS: report templates, PowerPoint runtime and native chart canvas")


if __name__ == "__main__":
    main()
