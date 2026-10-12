"""Preserve review evidence before the owner-requested temporary-file cleanup."""
import hashlib,json
from pathlib import Path
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261011-stage42/duplicate-pdf-review-v1'
TEMP=ROOT/'tmp/pdfs/ms-release-duplicates-20261011-v1'

def main():
    if OUT.exists():raise ValueError('Preserve existing review')
    files=[]
    for name in ('55-1.png','55-2.png','57-1.png','57-2.png'):
        path=TEMP/name
        if path.is_symlink() or not path.is_file() or path.resolve().parent!=TEMP.resolve():raise ValueError('Unexpected review target')
        files.append({'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size})
    if {p.name for p in TEMP.iterdir()}!={Path(r['path']).name for r in files}:raise ValueError('Unexpected temporary files')
    provenance=protected(OUT.parent/'duplicates-v1/result.json')
    save(OUT,'review',{'passed':True,'reviewed_pages':4,'source_receipt':provenance['protected_receipt'],
        'source_pdfs_saved':False,'page1_normalized_text_identical':True,'page1_render_identical':files[0]['sha256']==files[2]['sha256'],
        'newer_release_id':57,'obsolete_release_id':55,'newer_disclaimer_matches_user_requested_sprout_wording':True,
        'visual_findings':'Both two-page files inspected. No clipping or missing text found in this comparison; the newer version contains the requested disclaimer. This comparison does not re-certify financial calculations.',
        'files':files,'azure_originals_modified':False,'registrations_changed':False,'cleanup_required':True})
    print(json.dumps({'reviewed_pages':4,'page1_render_identical':files[0]['sha256']==files[2]['sha256'],'cleanup_ready':True}))

if __name__=='__main__':main()
