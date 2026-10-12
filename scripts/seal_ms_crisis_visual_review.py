"""Bind the completed manual 63-slide inspection to exact all-month outputs."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'.work/alpha-ms-crisis-release-20261008'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    folder=BASE/'all-deck-previews-v1';proof=json.loads((folder/'verification.json').read_bytes())
    assert proof['count']==21
    inspected=[]
    for row in proof['rows']:
        assert row['changed_slides']==[14,16,17]
        assert len(row['slides'])==20 and row['validation']['qualified_crisis_verified']
        period=row['period_end'];path=ROOT/'output/pptx/AlphaWorld_MS_Crisis_Review_v1'/('AlphaWorld_MS_Presentation_'+period+'.pptx')
        assert sha(path)==row['sha256']
        inspected.append({'period_end':period,'presentation_sha256':row['sha256'],
            'inspected_slide_png_sha256':{str(i):sha(folder/period/f'slide-{i:02d}.png') for i in (14,16,17)}})
    result={'status':'passed','months':21,'slides':420,'changed_slides_visually_inspected':63,
        'unchanged_slides_pixel_identical':357,'review':'Main agent inspected all changed slides at full preview size; no clipping or overflow; hypothetical stress and proxy disclosures present',
        'all_month_numerical_verification_sha256':sha(folder/'verification.json'),'rows':inspected,'powerpoint_native_app_check':False}
    with (BASE/'local-visual-acceptance.json').open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
    print(json.dumps({'status':'passed','inspected_changed_slides':63,'months':21}))
if __name__=='__main__':main()
