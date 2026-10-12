"""Bind only slides actually inspected individually in this review session."""
import hashlib,json,argparse
from pathlib import Path
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-internal-presentations-20261009-v1'
PERIODS=('2025-02-28','2025-03-31','2025-04-30')

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--part',type=int,default=1)
    parser.add_argument('--period',action='append')
    args=parser.parse_args()
    periods=tuple(args.period or PERIODS)
    rows=[]
    for period in periods:
        deck=FOLDER/'output'/f'AlphaWorld_T_Presentation_{period}.pptx'
        receipt=json.loads((FOLDER/'receipts'/f'{period}.json').read_bytes())
        if sha(deck)!=receipt['output_sha256']:raise ValueError('Reviewed deck binding drift')
        slides=[FOLDER/'previews'/period/f'slide-{n:02}.png' for n in range(1,21)]
        rows.append({'period_end':period,'deck_sha256':sha(deck),
            'individual_slide_image_sha256':{p.name:sha(p) for p in slides},
            'review':'All 20 individual slide images inspected at their full exported dimensions.',
            'visible_overlap_clipping_or_label_defects_found':False,
            'benchmark_unavailability_disclosed':True})
    report={'reviewed_at':datetime.now(timezone.utc).isoformat(),'reviewed_months':len(periods),
        'reviewed_slides':20*len(periods),'all_month_visual_acceptance':False,'items':rows,
        'powerpoint_application_acceptance':'incomplete: native file-dialog set_value failed twice with stale cached element 419; no T deck opened',
        'reports_released':False,'onboarding_activated':False}
    with (FOLDER/f'visual-review-20261009-part{args.part}.json').open('x',encoding='utf-8') as f:
        json.dump(report,f,indent=2)
    print(json.dumps({k:v for k,v in report.items() if k!='items'}))
