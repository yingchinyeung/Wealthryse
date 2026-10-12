"""Private all-month rendered report qualification, never Azure mutation."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor,as_completed
from PIL import Image,ImageChops
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
REVIEW=ROOT/'.work/alpha-ms-crisis-release-20261008'
RUNTIME=REVIEW/'candidate-v4/local-runtime'
NODE=Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
MODULES=Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')
PYTHON=Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe')
SKILL=Path('C:/Users/antho/.codex/plugins/cache/openai-primary-runtime/presentations/26.1007.11041/skills/presentations')
POPPLER=Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe')
PPTX=ROOT/'output/pptx/AlphaWorld_MS_Crisis_Review_v1'
PDF=ROOT/'output/pdf/AlphaWorld_MS_Crisis_Review_v1'
TEMPLATE=RUNTIME/'templates/U16088571/approved-august-2026-20-slide-v24.pptx'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def environment():
    return {**os.environ,'PYTHONPATH':os.pathsep.join((str(RUNTIME/'src'),str(RUNTIME/'scripts'),str(ROOT/'scripts'))),
        'PYTHONUTF8':'1','RUNTIME_PYTHON':str(PYTHON),'RUNTIME_NODE_MODULES':str(MODULES),
        'SKILL_DIR':str(SKILL),'PRESENTATION_STRATEGY_NAME':'iStrategy: Alpha World MS','MS_LOCAL_TEMPLATE':str(TEMPLATE)}

def pdf():
    from ibkr_etl.monthly_artifacts import render_factsheet
    from ibkr_etl.alpha_world_ms_pdf_validation import validate
    PDF.mkdir(parents=True);qa=REVIEW/'pdf-previews-v1';qa.mkdir()
    reference=ROOT/'.work/alpha-ms-headers-v14r15-20261008-r2/worker/candidate-runtime-v1/outputs'
    authority=ROOT/'.work/alpha-ms-factsheets-v13-20261007'
    rows=[]
    for source in sorted((REVIEW/'qualified-inputs-v1').glob('20*.json')):
        p=json.loads(source.read_bytes());period=p['period_end'];name='AlphaWorld_MS_Factsheet_'+period+'.pdf'
        target=PDF/name
        render_factsheet(p,template_path=authority/'template.pdf',design_model_path=authority/'completed_example.pdf',output_path=target)
        validate(target,p)
        old,new=PdfReader(reference/name),PdfReader(target)
        for key in ('/MSMetrics','/MSCalculationEvidence','/MSCopyVersion','/MSCopySHA256'):
            if old.metadata[key]!=new.metadata[key]:raise ValueError('Factsheet financial/copy metadata changed')
        folder=qa/period;folder.mkdir()
        for label,path in (('before',reference/name),('after',target)):
            subprocess.run([str(POPPLER),'-r','100','-png',str(path),str(folder/label)],check=True,capture_output=True)
        for page in (1,2):
            a=Image.open(folder/f'before-{page}.png').convert('RGB');b=Image.open(folder/f'after-{page}.png').convert('RGB')
            if ImageChops.difference(a,b).getbbox():raise ValueError('Factsheet visual changed: '+period)
        rows.append({'period_end':period,'sha256':sha(target),'both_pages_pixel_identical':True,'financial_metadata_unchanged':True})
        print(json.dumps({'pdf_passed':period}),flush=True)
    with (qa/'verification.json').open('x',encoding='utf-8') as stream:json.dump({'status':'passed','count':21,'rows':rows},stream,indent=2)

def decks():
    env=environment();PPTX.mkdir(parents=True,exist_ok=True);qa=REVIEW/'all-deck-previews-v1';qa.mkdir()
    def one(source):
        p=json.loads(source.read_bytes());period=p['period_end'];target=PPTX/('AlphaWorld_MS_Presentation_'+period+'.pptx')
        if not target.exists():
            r=subprocess.run([str(NODE),'--import',(ROOT/'scripts/ms_local_table_adapter.mjs').as_uri(),
                str(ROOT/'scripts/render_monthly_presentation.mjs'),str(TEMPLATE),str(source),str(target)],
                cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=180)
            with (qa/(period+'-render.log')).open('x',encoding='utf-8') as stream:stream.write(r.stdout+r.stderr)
            if r.returncode:raise ValueError(period+' render failed: '+r.stderr[-1200:])
        r=subprocess.run([str(PYTHON),str(ROOT/'scripts/audit_alpha_ms_presentation.py'),str(target),str(source)],
            env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
        if r.returncode:raise ValueError(period+' financial audit failed: '+r.stderr[-1200:])
        validation=json.loads(r.stdout)
        if not validation['qualified_crisis_verified']:raise ValueError('Qualified crisis verification missing')
        r=subprocess.run([str(NODE),str(ROOT/'scripts/inspect_alpha_ms_presentation.mjs'),str(target),str(qa/period)],
            env=env,capture_output=True,text=True,encoding='utf-8',timeout=180)
        if r.returncode:raise ValueError(period+' preview failed: '+r.stderr[-1200:])
        old=ROOT/'.work/alpha-ms-number-formats-20261008-v2/all-month-previews'/period
        comparisons=[{'slide':i,'pixel_identical':sha(qa/period/f'slide-{i:02d}.png')==sha(old/f'slide-{i:02d}.png')} for i in range(1,21)]
        return {'period_end':period,'sha256':sha(target),'validation':validation,'slides':comparisons,
                'changed_slides':[r['slide'] for r in comparisons if not r['pixel_identical']]}
    rows=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        work=[pool.submit(one,p) for p in sorted((REVIEW/'qualified-inputs-v1').glob('20*.json'))]
        for done in as_completed(work):
            row=done.result();rows.append(row);print(json.dumps({'pptx_passed':row['period_end'],'changed_slides':row['changed_slides']}),flush=True)
    with (qa/'verification.json').open('x',encoding='utf-8') as stream:json.dump({'status':'rendered_and_numerically_qualified_visual_review_pending','count':21,'rows':sorted(rows,key=lambda r:r['period_end'])},stream,indent=2)

if __name__=='__main__':
    {'pdf':pdf,'decks':decks}[sys.argv[1]]()
