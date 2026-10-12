"""Retain bounded review evidence and an exact owner-authorized cleanup list."""
import hashlib,json
from pathlib import Path
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage41'
NATIVE=OUT/'native-powerpoint-v1'
SOURCE=ROOT/'.work/platform-upgrade-20261011-stage40/historical-review-v1'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    source=protected(SOURCE/'result.json')
    text=json.loads((NATIVE/'text-screening.json').read_bytes())
    geometry=json.loads((NATIVE/'geometry-screening.json').read_bytes())
    if not text['text_screen_passed'] or not geometry['passed']:raise ValueError('Screening unresolved')
    if text['native_exported_decks']!=105 or geometry['pages']!=2100:raise ValueError('Incomplete screening')
    exports=[json.loads(p.read_bytes()) for p in sorted(NATIVE.glob('*/*/export.json'))]
    if len(exports)!=105 or len(source['artifacts'])!=210:raise ValueError('Exact batch required')
    cleanup=[]
    for row in source['artifacts']:
        path=Path(row['file']).resolve()
        if not path.is_relative_to((SOURCE/'files').resolve()) or sha(path)!=row['sha256']:
            raise ValueError('Source scope or hash changed')
        cleanup.append({'path':str(path),'sha256':row['sha256'],'kind':'downloaded_qa_file'})
    for row in exports:
        path=Path(row['pdf']).resolve()
        if path!=NATIVE.resolve()/row['subject']/row['period_end']/'native-review.pdf' or sha(path)!=row['pdf_sha256']:
            raise ValueError('Export scope or hash changed')
        if not row['source_unchanged'] or not row['read_only']:raise ValueError('Read-only evidence required')
        cleanup.append({'path':str(path),'sha256':row['pdf_sha256'],'kind':'native_review_export'})
    visual=[]
    selected=[('alpha-world-t','2026-09-30',range(1,21)),('U16088606','2026-08-31',range(9,12)),('U16088571','2025-01-31',range(9,11))]
    for subject,period,pages in selected:
        for slide in pages:
            path=NATIVE/subject/period/'renders'/f'slide-{slide:02}.png'
            visual.append({'subject':subject,'period_end':period,'slide':slide,'png_sha256':sha(path),
                'individual_visual_layout_inspected':True,'unexpected_clipping_or_overlap_identified':False})
    images=list((SOURCE/'renders').glob('*.png'))+list(NATIVE.glob('*/*/renders/slide-*.png'))
    if len(images)!=31 or len(visual)!=25:raise ValueError('Unexpected review-render scope')
    cleanup.extend({'path':str(p.resolve()),'sha256':sha(p),'kind':'temporary_page_render'} for p in images)
    if len(cleanup)!=346 or len({r['path'] for r in cleanup})!=346:raise ValueError('Unexpected cleanup count')
    for row in cleanup:
        path=Path(row['path'])
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('Reparse path rejected')
    save(OUT,'native-review-evidence',{'passed_bounded_checks':True,'native_exports':exports,
        'text_screen':text,'geometry_screen':geometry,'individual_visual_reviews':visual,
        'full_historical_native_visual_review':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED',
        'real_viewer_sign_in':'UNVERIFIED','production_reports_or_approvals_changed':False,
        'source_files_unchanged':True,'full_upgrade_complete':False})
    save(OUT,'local-cleanup-plan',{'authorized':'Owner requested deletion of downloaded QA files and review exports after use.',
        'scope':'This completed local screening and sampled visual review only; further historical visual acceptance remains unverified.',
        'files':cleanup,'delete_count':346,'recursive_delete':False,'azure_originals_preserved':True,
        'audit_metadata_preserved':True})
    print(json.dumps({'native_decks':105,'native_pages':2100,'visually_inspected_slides':25,'cleanup_files':346}))

if __name__=='__main__':main()
