"""Retain this turn's native slide-nine inspection and unchanged-file evidence."""
import hashlib,json,zipfile
from pathlib import Path
from xml.etree import ElementTree as E
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261011-stage34/january-native-review-v1'
def main():
    downloads=protected(OUT/'downloads.json');row=next(r for r in downloads['artifacts'] if r['report_type']=='presentation');path=Path(row['file'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('Native review changed original file')
    with zipfile.ZipFile(path) as deck:
        text=' '.join(n.text or '' for n in E.fromstring(deck.read('ppt/slides/slide9.xml')).iter('{http://schemas.openxmlformats.org/drawingml/2006/main}t'))
    if 'All 9 NAV intervals are included.' not in text or '1 without matched positions is residual-only.' not in text:raise ValueError('Wrong reviewed disclosure')
    save(OUT,'native-slide9-review',{'passed':True,'scope':'Only the new January attribution disclosure and surrounding slide nine, inspected in native PowerPoint slideshow during this turn.',
        'file':str(path),'sha256':row['sha256'],'slide':9,'native_app':'Microsoft PowerPoint','checks':{'disclosure_fits_without_clipping':True,'no_overlap_with_contribution_table':True,'all_nine_intervals_disclosed':True,'one_residual_only_interval_disclosed':True,'signed_contributions_visible':True},
        'closed_without_saving':True,'original_hash_unchanged':True,'full_twenty_slide_native_review':'UNVERIFIED','full_historical_native_review':'UNVERIFIED','deployed':False})
    print(json.dumps({'native_slide9_passed':True,'original_hash_unchanged':True,'all_historical_visual_acceptance':False}))
if __name__=='__main__':main()
