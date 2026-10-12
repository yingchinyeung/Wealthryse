"""Read-only local artifact screening. Never equate screening with visual QA."""
import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as E
import pdfplumber

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage40/historical-review-v1'
NS={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def inspect_pdf(path):
    pages=[]
    with pdfplumber.open(path) as document:
        if len(document.pages)!=2:raise ValueError('Expected two-page factsheet')
        for number,page in enumerate(document.pages,1):
            text=page.extract_text() or ''
            clipped=[c for c in page.chars if c.get('text','').strip() and
                     (c['x0'] < -1 or c['x1'] > page.width+1 or c['top'] < -1 or c['bottom'] > page.height+1)]
            pages.append({'page':number,'characters':len(page.chars),'text_outside_page':len(clipped),
                          'page_label_present':bool(re.search(r'Page\s+'+str(number)+r'\s+of\s+2',text)),
                          'unexpected_placeholder':bool(re.search(r'#(?:VALUE!|REF!|DIV/0!)|\bNaN\b',text)),
                          'fonts':sorted({c['fontname'] for c in page.chars}),
                          'nonempty':len(text.strip())>100})
    return {'kind':'pdf','pages':pages,'screen_passed':all(p['nonempty'] and not p['text_outside_page'] and not p['unexpected_placeholder'] and p['page_label_present'] for p in pages)}

def inspect_ppt(path):
    with zipfile.ZipFile(path) as package:
        if package.testzip() is not None:raise ValueError('Presentation package CRC failure')
        names=package.namelist()
        slides=sorted(n for n in names if re.fullmatch(r'ppt/slides/slide\d+\.xml',n))
        if len(slides)!=20:raise ValueError('Expected twenty-slide presentation')
        issues=[]
        for name in slides:
            text=' '.join(n.text or '' for n in E.fromstring(package.read(name)).findall('.//a:t',NS))
            if re.search(r'#(?:VALUE!|REF!|DIV/0!)|\bNaN\b',text):issues.append({'slide':name,'kind':'placeholder'})
        external=[]
        for name in names:
            if not name.endswith('.rels'):continue
            for relation in E.fromstring(package.read(name)):
                if relation.get('TargetMode')=='External' and not relation.get('Type','').endswith('/hyperlink'):
                    external.append({'part':name,'type':relation.get('Type','').rsplit('/',1)[-1]})
    return {'kind':'pptx','slides':20,'issues':issues,'external_non_hyperlink_dependencies':external,
            'screen_passed':not issues and not external,'native_visual_review':'UNVERIFIED'}

def main():
    destination=OUT/'local-screening.json'
    if destination.exists():raise ValueError('Preserve completed screening')
    receipt=json.loads((OUT/'result.json').read_bytes())
    if not receipt['passed'] or receipt['files']!=210:raise ValueError('Completed hash-bound download required')
    results=[]
    for index,row in enumerate(receipt['artifacts'],1):
        path=Path(row['file']).resolve()
        if not path.is_relative_to((OUT/'files').resolve()) or path.is_symlink() or sha(path)!=row['sha256']:
            raise ValueError('Local artifact differs or escapes private review folder')
        result=inspect_pdf(path) if row['report_type']=='factsheet' else inspect_ppt(path)
        if sha(path)!=row['sha256']:raise ValueError('Screening changed source artifact')
        results.append({'subject':row['subject'],'period_end':row['period_end'],'sha256':row['sha256'],**result})
        if index%20==0:print(json.dumps({'screened':index,'total':210}),flush=True)
    record={'checked_at':datetime.now(timezone.utc).isoformat(),'files':210,'results':results,
            'screen_passed':all(r['screen_passed'] for r in results),'sources_unchanged':True,
            'full_visual_review':'UNVERIFIED','native_powerpoint_review':'UNVERIFIED',
            'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED',
            'cleanup_required':True,'production_changes':False,'full_upgrade_complete':False}
    with destination.open('x',encoding='utf-8') as stream:json.dump(record,stream,indent=2)
    print(json.dumps({'screen_passed':record['screen_passed'],'files':210,
                      'flagged':[{'subject':r['subject'],'period_end':r['period_end'],'kind':r['kind']} for r in results if not r['screen_passed']],
                      'full_visual_review':'UNVERIFIED'}))

if __name__=='__main__':main()
