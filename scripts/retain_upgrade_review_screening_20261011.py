"""Retain exact local screening and limited visual evidence, not release approval."""
import hashlib
import json
from pathlib import Path
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage40/historical-review-v1'
GENERIC={'U16088571','U16088606','hong-kong-china'}

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def adjudicate(record):
    results=record['results']
    if len(results)!=210:raise ValueError('Exactly 210 screening results required')
    exceptions=[]
    for row in results:
        if row['kind']=='pptx':
            if not row['screen_passed'] or row['slides']!=20:raise ValueError('Presentation screening failed')
        elif row['kind']=='pdf':
            if len(row['pages'])!=2:raise ValueError('PDF page count differs')
            for page in row['pages']:
                if not page['nonempty'] or page['text_outside_page'] or page['unexpected_placeholder']:
                    raise ValueError('PDF content screening failed')
                if not page['page_label_present']:
                    if row['subject'] not in GENERIC:raise ValueError('Alpha World pagination missing')
                    exceptions.append((row['subject'],row['period_end'],page['page']))
        else:raise ValueError('Unknown screening kind')
    if len(exceptions)!=126 or len(set(exceptions))!=126:
        raise ValueError('Generic-template pagination exception scope differs')
    return {'generic_pages_without_alpha_world_footer':126,
            'reason':'The three approved generic templates do not use the Alpha World Page n of 2 footer. This is a screening-rule mismatch, not permission to add a footer.',
            'other_screening_failures':0}

def main():
    downloaded=protected(OUT/'result.json')
    screen=json.loads((OUT/'local-screening.json').read_bytes())
    if not downloaded['passed'] or not screen['sources_unchanged']:raise ValueError('Unchanged completed download required')
    wanted={(r['subject'],r['period_end'],r['sha256']) for r in downloaded['artifacts']}
    if wanted!={(r['subject'],r['period_end'],r['sha256']) for r in screen['results']}:
        raise ValueError('Screening and downloaded scope differ')
    exceptions=adjudicate(screen)
    for row in downloaded['artifacts']:
        path=Path(row['file']).resolve()
        if not path.is_relative_to((OUT/'files').resolve()) or sha(path)!=row['sha256']:
            raise ValueError('Downloaded source changed')
    images={}
    for prefix in ('T-2026-09','MS-2026-09','U571-2026-09'):
        for page in (1,2):
            path=OUT/'renders'/(prefix+'-'+str(page)+'.png')
            images[path.name]=sha(path)
    save(OUT,'screening-qualified',{'passed':True,'files':210,'factsheet_pages_screened':210,
        'presentation_slides_package_screened':2100,'sources_unchanged':True,
        'screening_sha256':sha(OUT/'local-screening.json'),'adjudication':exceptions,
        'download_receipt':downloaded['protected_receipt'],
        'sample_visual_review':{'files':3,'pages':6,'period_end':'2026-09-30',
            'subjects':['alpha-world-t','alpha-world-ms','U16088571'],
            'page_png_sha256':images,'unexpected_clipping_or_missing_sections_observed':False,
            'renderer':'Poppler 135 DPI Alpha World and 120 DPI U16088571',
            'warnings':'Poppler reported unavailable Symbol and ArialUnicode display fonts; no missing visible text identified in these six inspected pages.'},
        'full_visual_review':'UNVERIFIED','native_powerpoint_review':'UNVERIFIED',
        'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED',
        'cleanup_required':True,'production_changes':False,'full_upgrade_complete':False})
    print(json.dumps({'screening_qualified':True,'files':210,'visual_pages_reviewed':6,'full_visual_review':'UNVERIFIED'}))

if __name__=='__main__':main()
