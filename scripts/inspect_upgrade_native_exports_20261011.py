"""Native PDF completeness screen; individual visual acceptance remains separate."""
from concurrent.futures import ProcessPoolExecutor
import hashlib,json,re,unicodedata,zipfile
from pathlib import Path
from xml.etree import ElementTree as E
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage41/native-powerpoint-v1'
SOURCE=ROOT/'.work/platform-upgrade-20261011-stage40/historical-review-v1'
A='{http://schemas.openxmlformats.org/drawingml/2006/main}'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def normalized(value):
    text=unicodedata.normalize('NFKC',value).replace('\u00ad','').replace('\u200b','')
    return ''.join(c.lower() for c in text if c.isalnum() or c in '%$')

def inspect(row):
    source=Path(row['file']).resolve();folder=OUT/row['subject']/row['period_end']
    receipt=json.loads((folder/'export.json').read_bytes());pdf=Path(receipt['pdf']).resolve()
    if not source.is_relative_to((SOURCE/'files').resolve()) or pdf!=folder.resolve()/'native-review.pdf':raise ValueError('Export outside approved review scope')
    if sha(source)!=row['sha256'] or receipt['source_sha256']!=row['sha256'] or sha(pdf)!=receipt['pdf_sha256']:
        raise ValueError('Native source/export hash differs')
    if not all(receipt[k] for k in ('read_only','source_unchanged','native_export_complete')):raise ValueError('Read-only export required')
    document=PdfReader(pdf)
    if len(document.pages)!=20:raise ValueError('Twenty native PDF pages required')
    results=[]
    with zipfile.ZipFile(source) as package:
        for index,page in enumerate(document.pages,1):
            text=page.extract_text() or '';visible=normalized(text)
            slide=E.fromstring(package.read('ppt/slides/slide'+str(index)+'.xml'))
            expected=[''.join(n.text or '' for n in paragraph.findall('.//'+A+'t')) for paragraph in slide.findall('.//'+A+'p')]
            missing=[i for i,s in enumerate(expected) if len(normalized(s))>=3 and normalized(s) not in visible]
            results.append({'slide':index,'visible_text_characters':len(text),'paragraphs_checked':sum(len(normalized(s))>=3 for s in expected),
                            'missing_paragraph_indexes':missing,'nonempty':len(text.strip())>20})
    return {'subject':row['subject'],'period_end':row['period_end'],'source_sha256':row['sha256'],'pdf_sha256':receipt['pdf_sha256'],
            'slides':results,'text_screen_passed':all(r['nonempty'] and not r['missing_paragraph_indexes'] for r in results),
            'visual_review':'UNVERIFIED'}

def main():
    output=OUT/'text-screening.json'
    if output.exists():raise ValueError('Preserve earlier native inspection')
    manifest=json.loads((SOURCE/'result.json').read_bytes())
    rows=[r for r in manifest['artifacts'] if r['report_type']=='presentation']
    if len(rows)!=105 or len(list(OUT.glob('*/*/export.json')))!=105:raise ValueError('All 105 native exports required')
    results=[]
    with ProcessPoolExecutor(max_workers=3) as workers:
        for index,result in enumerate(workers.map(inspect,rows),1):
            results.append(result)
            if index%10==0:print(json.dumps({'decks_screened':index,'total':105}),flush=True)
    record={'native_exported_decks':105,'native_pdf_pages':2100,'source_files_unchanged':True,'results':results,
            'text_screen_passed':all(r['text_screen_passed'] for r in results),
            'visual_review':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED',
            'cleanup_required':True,'production_changes':False,'full_upgrade_complete':False}
    with output.open('x',encoding='utf-8') as stream:json.dump(record,stream,indent=2)
    print(json.dumps({'native_exported_decks':105,'native_pdf_pages':2100,
        'decks_with_text_flags':sum(not r['text_screen_passed'] for r in results),'visual_review':'UNVERIFIED'}))

if __name__=='__main__':main()
