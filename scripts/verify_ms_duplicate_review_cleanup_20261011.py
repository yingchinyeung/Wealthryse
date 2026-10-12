"""Record removal of only the four reviewed temporary page images."""
import json
from pathlib import Path
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261011-stage42/duplicate-pdf-review-v1'
if __name__=='__main__':
    review=protected(OUT/'review.json')
    if len(review['files'])!=4 or any(Path(row['path']).exists() for row in review['files']):raise ValueError('Temporary review cleanup incomplete')
    save(OUT,'cleanup',{'completed':True,'deleted_temporary_page_images':4,'source_pdfs_saved':False,'azure_originals_preserved':True,'review_receipt':review['protected_receipt']})
    print(json.dumps({'cleanup_complete':True,'temporary_page_images_deleted':4}))
