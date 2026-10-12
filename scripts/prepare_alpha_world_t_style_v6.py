"""Restore only the omitted native MS KPI blue divider to frozen T v5 style."""
import hashlib
import json
from pathlib import Path
from pypdf import PdfReader, PdfWriter
from prepare_alpha_world_t_ms_style import retain

ROOT=Path(__file__).resolve().parents[1]

def main():
    source=ROOT/'output/pdf/AlphaWorld_MS_Azure_Disclaimer_v14r16/AlphaWorld_MS_Factsheet_2026-09-30.pdf'
    previous=ROOT/'templates/alpha-world-t/ms_style_sections_v5.pdf'
    target=ROOT/'templates/alpha-world-t/ms_style_sections_v6.pdf'
    expected=next(r['sha256'] for r in json.loads((source.parent/'verification.json').read_bytes())['rows']
                  if r['period_end']=='2026-09-30')
    assert hashlib.sha256(source.read_bytes()).hexdigest()==expected
    assert hashlib.sha256(previous.read_bytes()).hexdigest()=='a284badf39281008228e5579a5277bec14188014998c55deac7c45537d64ed18'
    writer=PdfWriter(clone_from=PdfReader(previous))
    native=PdfWriter(clone_from=PdfReader(source))
    # The v5 clipping union stopped at y=665 below the title; the divider is
    # y=666..668. Keep the original embedded colors, vectors and masks intact.
    stripe=(389.,665.,175.,4.)
    retain(native.pages[0],(),(stripe,),(stripe,))
    writer.pages[0].merge_page(native.pages[0])
    writer.add_metadata({'/Title':'Alpha World T MS v14r16 style with restored KPI divider',
                         '/KPIDividerSourceSHA256':expected})
    with target.open('xb') as stream: writer.write(stream)
    print(hashlib.sha256(target.read_bytes()).hexdigest())

if __name__=='__main__': main()
