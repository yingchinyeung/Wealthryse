"""Preserve the rejected alert-v2 output shape; no alert or notification write."""
import hashlib
import alert_secret_metadata_monitor_20261010 as a


def main():
    folder=a.c.OUT/'alert-v2';prior=a.c.protected(folder/'qualification.json')
    rejected=a.query().replace('FindingCount=tolong(E.finding_count)','FindingCount=toint(E.finding_count)')
    if hashlib.sha256(rejected.encode()).hexdigest()!=prior['query_sha256']:raise ValueError('Rejected query identity differs')
    rows=a.c.az('monitor','log-analytics','query','-w',a.WORKSPACE,'--analytics-query',rejected)
    if len(rows)!=1 or 'FindingCount' in rows[0] or not {'FindingCount_int','FindingCount_long'}<=set(rows[0]):raise ValueError('Original projection mismatch no longer reproduced')
    a.c.save(folder,'projection-review',{'accepted_for_deployment':False,'row_count_cases_passed':26,
        'query_sha256':prior['query_sha256'],'observed_projection':rows,
        'issue':'KQL union split the integer and long FindingCount columns. Pre-deployment inventory matching rejected the query; no alert was created from it.',
        'correction':'Alert-v3 uses a long projection on both union branches and additionally validates output fields in all fault tests.',
        'alert_created_from_rejected_query':False,'additional_test_notification_sent':False})
    print('Rejected output schema preserved; original tests were not relabeled as deployment acceptance')


if __name__=='__main__':main()
