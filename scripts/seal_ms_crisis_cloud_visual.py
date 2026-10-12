"""Bind manually reviewed native conclusions to full all-month pixel audits."""
import json
import release_ms_crisis as candidate
import build_ms_presentation_worker_candidate as build
from run_specialist_gateway_read_verification import retain

def main():
    root=candidate.FOLDER.parent;folder=root/'candidate-v6'
    if (folder/'cloud-acceptance.json').exists():raise ValueError('Acceptance already frozen')
    pending_path=folder/'cloud-offline-qa-v1/pending-visual.json'
    preview_path=folder/'cloud-slide-previews-v1/verification.json'
    pending=json.loads(pending_path.read_bytes());preview=json.loads(preview_path.read_bytes())
    assert len(pending['rows'])==42 and preview['decks']==42 and preview['slides']==840
    native={(r['kind'],r['period_end']):r for r in preview['rows']}
    periods=sorted({r['period_end'] for r in pending['rows']});assert len(periods)==21
    manual=[]
    for period in periods:
        a=native['worker',period];b=native['web',period]
        # The 21 worker conclusions were inspected individually, not inferred
        # from a contact sheet. All web slide pixels must match those images.
        assert a['preview_sha256']==b['preview_sha256']
        manual.append({'period_end':period,'slide':17,'worker_and_web_png_sha256':a['preview_sha256']['17'],
                       'inspection':'Readable, no overlap or clipping; qualified hypothetical HKD stress label and audited value visible'})
    for row in pending['rows']:
        n=native[row['kind'],row['period_end']]
        assert n['different_slides']==[17] and n['pixel_identical_slides']==[i for i in range(1,21) if i!=17]
        assert n['presentation_sha256']==row['presentation_sha256']
        assert row['financial_metrics_unchanged'] and row['pdf_pages_pixel_identical']==2
        assert row['presentation_audit']['ms_financial_audit']['qualified_crisis_verified']
        for i,h in n['preview_sha256'].items():
            assert build.sha(folder/'cloud-slide-previews-v1'/row['kind']/row['period_end']/f'slide-{int(i):02d}.png')==h
    for period in periods:
        rows=[r for r in pending['rows'] if r['period_end']==period]
        assert len(rows)==2 and rows[0]['data_sha256']==rows[1]['data_sha256']
    record={'status':'passed','months':21,'financial_metrics_unchanged':True,
        'source_data_changed':False,'approvals_changed':False,'reports_released':False,
        'native_pdf_pages_pixel_verified':84,'native_presentation_slides_compared':840,
        'native_slides_pixel_identical_to_locally_accepted':798,
        'native_worker_conclusions_individually_inspected':21,
        'web_conclusions_pixel_identical_to_inspected_worker':21,
        'powerpoint_native_app_check':False,'manual_reviews':manual,'rows':pending['rows'],
        'pending_numerical_audit_sha256':build.sha(pending_path),'native_preview_audit_sha256':build.sha(preview_path),
        'local_visual_acceptance_sha256':build.sha(root/'local-visual-acceptance.json')}
    build.save(folder,'cloud-acceptance',{**record,'protected_receipt':retain(record,'ms-crisis-v6-all-month-cloud-acceptance')})
    print(json.dumps({'status':'passed','months':21,'native_report_pairs':42}))

if __name__=='__main__':main()
