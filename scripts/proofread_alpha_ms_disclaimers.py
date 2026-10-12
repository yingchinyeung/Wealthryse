"""Create one separate, text-only correction of the installed September PDF."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from pypdf import PdfReader, PdfWriter
from ibkr_etl.alpha_world_ms_copy import COPY_SHA256, COPY_VERSION, repair_disclaimers, validate_copy


def main():
    source = ROOT/'output/pdf/AlphaWorld_MS_Azure_Installed_HKD_HIBOR_v14r11/AlphaWorld_MS_Factsheet_2026-09-30.pdf'
    output = ROOT/'output/pdf/AlphaWorld_MS_Text_Corrections_v14r12'
    output.mkdir(exist_ok=True)
    target = output/source.name
    if target.exists(): raise ValueError('Refusing to overwrite a prior review PDF')
    original = source.read_bytes()
    reader = PdfReader(source); metadata = dict(reader.metadata)
    writer = PdfWriter(); writer.clone_document_from_reader(reader)
    removed = repair_disclaimers(writer.pages)
    writer.add_metadata({'/MSCopyVersion': COPY_VERSION, '/MSCopySHA256': COPY_SHA256})
    with target.open('wb') as stream: writer.write(stream)
    actual = dict(PdfReader(target).metadata)
    if any(actual.get(k) != v for k, v in metadata.items()):
        raise ValueError('Existing PDF metadata changed')
    if source.read_bytes() != original: raise ValueError('Original PDF changed')
    copy_check = validate_copy(target)
    record = {'status': 'local-review-not-deployed', 'source_sha256': hashlib.sha256(original).hexdigest(),
              'pdf_sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'source_unchanged': True,
              'all_original_metadata_unchanged': True, 'copy_validation': copy_check,
              'removed_text_operations': removed, 'pdf': str(target)}
    (output/'manifest.json').write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(record, indent=2))


if __name__ == '__main__': main()
