"""Bind the actual September Azure PPTX inspected in desktop PowerPoint."""
import hashlib,json
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-candidate-reports-20261009-v4'
def main():
 proof=json.loads((FOLDER/'download/verification.json').read_bytes())
 item=next(i for i in proof['items'] if i['period_end']=='2026-09-30')
 deck=FOLDER/'download/AlphaWorld_T_Presentation_2026-09-30.pptx'
 if hashlib.sha256(deck.read_bytes()).hexdigest()!=item['presentation']['sha256']:raise ValueError('Reviewed Azure deck changed')
 result={'reviewed_at':datetime.now(timezone.utc).isoformat(),'period_end':'2026-09-30',
  'deck_sha256':item['presentation']['sha256'],'image':proof['image'],
  'application':'Microsoft PowerPoint','window_id':4395284,'repair_prompt_observed':False,
  'slides_individually_inspected':list(range(1,21)),
  'inspection_method':'Native desktop window, each slide observed; transient chart placeholders re-observed after rendering.',
  'visible_clipping_or_overlaps_found':False,'six_financial_charts_displayed':True,
  'allocation_table_footnote_clear':True,'internal_only_and_provisional_crisis_disclosures_visible':True,
  'benchmark_unavailability_disclosed':True,'all_month_native_review_complete':False,
  'report_approval_changed':False,'source_deck_edited':False}
 with (FOLDER/'native-review-2026-09-30.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
 print(json.dumps({'status':'September_native_review_passed','slides':20,'all_month_native_review_complete':False}))
if __name__=='__main__':main()
