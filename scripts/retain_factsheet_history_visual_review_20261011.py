"""Retain the six first/latest two-page PDFs actually inspected this turn."""
import hashlib,json
from pathlib import Path
from pypdf import PdfReader
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261011-stage34/factsheet-history-review-v1'
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    downloads=protected(OUT/'downloads.json');rows=downloads['artifacts']
    if not downloads['passed'] or len(rows)!=6: raise ValueError('Exact reviewed downloads required')
    images={}
    for row in rows:
        path=Path(row['file'])
        if path.parent!=OUT or sha(path)!=row['sha256'] or len(PdfReader(path).pages)!=2: raise ValueError('Reviewed artifact changed')
        for page in (1,2):
            png=path.with_name(path.stem+'-'+str(page)+'.png')
            if not png.exists(): raise ValueError('Reviewed page render missing')
            images[png.name]=sha(png)
    january=OUT/'U16088571_2025-01-31.pdf'
    text=' '.join(PdfReader(january).pages[0].extract_text().split())
    if '2025-01-31 99.82 -0.18% -0.18%' not in text: raise ValueError('Corrected January row missing')
    save(OUT,'visual-review',{'passed':True,'scope':'First and latest generic factsheets only: six files and all twelve rendered pages inspected in this turn using the PDF workflow.',
        'files':{Path(r['file']).name:r['sha256'] for r in rows},'page_png_sha256':images,
        'checks':{'corrected_january_return_visible':True,'longest_history_tables_fit':True,'no_table_overlap_or_clipping':True,'charts_and_disclaimers_present':True},
        'renderer':'Poppler pdftoppm','render_warnings':'Missing display-font notices for Symbol and ArialUnicode retained; no missing visible text identified in the inspected pages.',
        'sampled_visual_review':True,'all_63_factsheets_visual_review':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED','deployed':False})
    print(json.dumps({'sampled_visual_review_passed':True,'files':6,'pages':12,'all_historical_visual_review':False}))
if __name__=='__main__': main()
