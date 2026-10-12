"""Retain the direct owner's receipt of the exact real report-ready notice."""
import hashlib
import json
from deploy_specialist_report_worker import ROOT
from run_specialist_gateway_read_verification import retain
from enable_specialist_ready_notification import FOLDER


def main():
    activation=json.loads((FOLDER/'acceptance.json').read_text())
    observed=json.loads((FOLDER/'alert-observation.json').read_text())
    publication=json.loads((ROOT/'.work/specialist-daily-publication-20261003/acceptance.json').read_text())
    if (activation.get('status')!='passed' or activation.get('notification_rule_enabled') is not True or
            len(observed.get('alerts',[]))!=1 or observed['alerts'][0].get('condition')!='Fired' or
            observed['alerts'][0].get('action_status',{}).get('isSuppressed') is not False or
            publication.get('status')!='passed'):
        raise ValueError('Exact accepted activation, real alert and published report required')
    for value in (activation,observed,publication):
        if value.get('protected_receipt',{}).get('protected_readback_verified') is not True:
            raise ValueError('Protected acceptance required')
    result=dict(status='passed',recipient='anthony.ying@wealthryse.ai',report_date='2026-10-02',
        report_sha256=publication['record']['report_sha256'],owner_reported_received=True,
        confirmation_source='Direct human reply to the separate real report-ready notice question: Yes, received the report-ready notice',
        alert_id=observed['alerts'][0]['alert_id'],full_report_emailed=False,
        daily_schedule_enabled=False,source_rows_changed=0,
        receipts=[v['protected_receipt'] for v in (activation,observed,publication)])
    result['protected_receipt']=retain(result,'ready-notice-owner-receipt')
    (FOLDER/'owner-receipt.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
