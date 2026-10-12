"""Screen native export page bounds; this does not establish visual acceptance."""
from concurrent.futures import ProcessPoolExecutor
import hashlib,json,re
from pathlib import Path
import pdfplumber

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage41/native-powerpoint-v1'

def inspect(receipt_path):
    r=json.loads(receipt_path.read_bytes());path=Path(r['pdf'])
    if path.resolve()!=receipt_path.parent.resolve()/'native-review.pdf':raise ValueError('Outside export scope')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=r['pdf_sha256']:raise ValueError('Export changed')
    results=[]
    with pdfplumber.open(path) as doc:
        if len(doc.pages)!=20:raise ValueError('Incorrect native page count')
        for n,page in enumerate(doc.pages,1):
            outside=[c for c in page.chars if c.get('text','').strip() and
                (c['x0'] < -1 or c['x1'] > page.width+1 or c['top'] < -1 or c['bottom'] > page.height+1)]
            text=page.extract_text() or ''
            results.append({'slide':n,'page_size':[page.width,page.height],'characters':len(page.chars),
                'text_outside_page':len(outside),'unexpected_placeholder':bool(re.search(r'#(?:VALUE!|REF!|DIV/0!)|\bNaN\b',text))})
            page.close()
    return {'subject':r['subject'],'period_end':r['period_end'],'source_sha256':r['source_sha256'],'pdf_sha256':r['pdf_sha256'],
        'pages':results,'bounds_screen_passed':all(not x['text_outside_page'] and not x['unexpected_placeholder'] for x in results)}

def main():
    target=OUT/'geometry-screening.json'
    if target.exists():raise ValueError('Preserve completed geometry screen')
    rows=sorted(OUT.glob('*/*/export.json'))
    if len(rows)!=105:raise ValueError('Complete native batch required')
    results=[]
    with ProcessPoolExecutor(max_workers=3) as workers:
        for i,r in enumerate(workers.map(inspect,rows),1):
            results.append(r)
            if i%15==0:print(json.dumps({'decks_geometry_screened':i}),flush=True)
    record={'decks':105,'pages':2100,'results':results,'passed':all(x['bounds_screen_passed'] for x in results),
        'limitations':'Page-bound and placeholder screen only; overlap, chart content and individual visual acceptance are separate.',
        'full_historical_native_visual_review':'UNVERIFIED'}
    with target.open('x',encoding='utf-8') as f:json.dump(record,f,indent=2)
    print(json.dumps({'passed':record['passed'],'flagged_decks':sum(not x['bounds_screen_passed'] for x in results)}))

if __name__=='__main__':main()
