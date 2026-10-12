"""Retain observed real owner UI acceptance; no new publication or notification."""
import hashlib
import json
from run_specialist_gateway_read_verification import retain
from run_specialist_daily_publication import FOLDER,prerequisites


def main():
    prerequisites()
    publication=json.loads((FOLDER/'acceptance.json').read_text())
    if publication.get('status')!='passed': raise ValueError('Publication acceptance required')
    result=dict(status='passed',report_date='2026-10-02',
        report_sha256=publication['record']['report_sha256'],owner_email='anthony.ying@wealthryse.ai',
        real_signed_in_chrome_owner_detail_verified=True,appeared_without_manual_refresh=True,
        reporting_window_verified=True,event_count=0,coverage_limitation_visible=True,
        report_does_not_prove_autonomous_execution=True,financial_release_not_approved=True,
        website_available=True,email_state='pending',recipient_delivery_confirmed=False,
        publication_receipt=publication['protected_receipt'],source_rows_changed=0)
    screenshot=FOLDER/'owner-daily-report.jpg'
    result['screenshot_sha256']=hashlib.sha256(screenshot.read_bytes()).hexdigest()
    result['protected_receipt']=retain(result,'daily-publication-browser-completed')
    (FOLDER/'browser-acceptance.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__': main()
