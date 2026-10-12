"""Write an evidence-bound local addendum only after Azure verification succeeds."""
import hashlib,html,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'.work/alpha-t-cash-backfill-20261009-r2'
OUT=ROOT/'output/audit/AlphaWorld_T_Accuracy_20261009'

def main():
    receipts={}
    for stage in ('rehearse','apply','diagnose-release-r2'):
        proof=json.loads((WORK/(stage+'-result.json')).read_bytes())
        content=(WORK/(stage+'-evidence.json')).read_bytes()
        result=json.loads(content)
        row=next(r for r in proof['records'] if r.get('passed') or r.get('status')=='diagnosed')
        assert proof['status']=='Succeeded' and proof['job_settings_unchanged']
        assert hashlib.sha256(content).hexdigest()==row['sha256']
        assert result.get('passed') or result.get('status')=='diagnosed'
        receipts[stage]={'proof':proof,'result':result,'sha256':row['sha256']}
    trial,applied,verified=[receipts[s]['result'] for s in ('rehearse','apply','diagnose-release-r2')]
    assert trial['rollback_verified'] and applied['committed'] and verified['read_only']
    assert applied['inserted']==10 and verified['cash_rows']==10 and verified['source_links_verified']
    assert trial['protected_before']==trial['protected_after']==applied['protected_before']==applied['protected_after']
    assert trial['source_code_sha256']==applied['source_code_sha256']
    baseline=applied['protected_after']
    assert set(verified['current_fingerprints'])==set(baseline)
    differences={k for k in baseline if baseline[k]!=verified['current_fingerprints'][k]}
    assert differences==set(verified['protected_differences'])=={'report_generation_runs','report_artifact_releases'}
    assert verified['reconstructed_baseline']['release_table_without_later_rows']==baseline['report_artifact_releases']
    assert verified['reconstructed_baseline']['prior_generation_status']=='awaiting_approval'
    releases=verified['later_releases']
    assert len(releases)==1 and releases[0]['subject_id']=='alpha-world-ms'
    assert releases[0]['period_end']=='2026-09-30' and releases[0]['status']=='released_internal'
    assert verified['settings']['transaction_read_only']=='on'
    strict_check=json.loads((WORK/'verify-result.json').read_bytes())
    assert strict_check['status']=='Failed' and strict_check['job_settings_unchanged']
    evidence={'status':'cash_snapshot_gap_resolved','cash_rows_inserted':10,'account_date_gaps_resolved':4,
       'new_flex_requests':0,'tests_passed':12,'receipts':receipts,
       'strict_post_apply_check':strict_check,
       'concurrent_release':releases[0],
       'concurrent_release_resolution':'Read-only reconstruction matches the exact pre-release fingerprints; release left untouched.',
       'unchanged':['Existing cash records','NAV','Positions','Trades','Cash transactions','FX and market data',
                    'Derived portfolios','Risk caches','Report snapshots and artifacts','Onboarding','Azure job settings'],
       'remaining_gaps':['Blank transaction IDs/types and possible duplicate economic records',
                         'Broader original-broker and publisher reconciliation','Narrative/model mismatch',
                         'Missing T benchmark/crisis analytics and remaining monthly presentations']}
    with (OUT/'AlphaWorld_T_Cash_Recovery_Evidence.json').open('x',encoding='utf-8') as f:json.dump(evidence,f,indent=2)
    rows=''.join('<tr><td>'+stage+'</td><td>'+html.escape(receipts[stage]['proof']['execution'])+'</td><td>'+receipts[stage]['sha256']+'</td></tr>' for stage in receipts)
    body=f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Alpha World T cash recovery addendum</title>
<style>body{{font:16px/1.6 Arial;max-width:1050px;margin:40px auto;padding:0 24px;color:#193329}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border:1px solid #ccd6d0;padding:10px;overflow-wrap:anywhere;text-align:left}}</style>
<h1>Cash snapshot gap resolved</h1><p>9 October 2026. Addendum to the Alpha World T accuracy review.</p>
<p>The two original Flex reports had already been retrieved and archived on 7 October. The earlier recovery imported 152 missing positions but intentionally left cash tables unchanged. The previous audit's recommendation to obtain those two reports again was unnecessary.</p>
<p>The current Azure database now includes <strong>10 original cash snapshots</strong> across four account/date gaps: U16088571 and U16088606, 27 and 28 August 2026. U16088571 has one base-summary row per day; U16088606 has base summary, HKD, TWD and USD per day.</p>
<p>All four base summaries match existing NAV cash exactly. Greater China's currency balances reconcile to the base summary using existing same-day FX within 0.000001 USD. Missing FX attributes in the original cash rows remain null; no rate was invented. The dated recovery keys retain exact broker attributes and source hashes, with two new ingestion audit records and ten raw-row provenance records.</p>
<h2>Verification</h2><ul><li>12 local safeguard tests passed.</li><li>The exact Azure writer inserted all ten rows, passed a zero-insert replay, then rolled back successfully before the committed import.</li><li>The applied import committed ten rows without overwriting an existing observation.</li><li>A separate read-only Azure diagnostic confirmed all ten stored values, source hashes, original attributes and audit-run links.</li><li>All protected fingerprints matched within the import transaction. The later read-only diagnostic confirmed that financial inputs, NAV, derived portfolios, risk caches, report snapshots and onboarding remained unchanged, apart from the intended cash/provenance additions. Persistent Azure job settings are unchanged.</li><li>No new Flex request, report regeneration or subject activation occurred in this recovery.</li></ul>
<h2>Concurrent report-release event</h2><p>The strict post-import comparison stopped because a separate release of the September 2026 Alpha World MS factsheet occurred at 02:14:09 UTC, after the cash import at 02:13:51 UTC. It added release row 57 and changed existing generation 1047 from awaiting approval to internally released. Read-only reconstruction of the two affected tables matched their exact pre-import fingerprints; all other protected table fingerprints matched directly. No database state was reverted and the release was left untouched. The original failed comparison and successful diagnostic are both retained in the evidence.</p>
<h2>What remains open</h2><p>This resolves only the four missing cash snapshots. Blank cash-transaction identifiers/types, possible duplicates, broader broker/provider verification, the retained narrative mismatch and missing T presentation analytics still need work. The earlier private September presentation is an audit-time snapshot; its missing-cash warning is superseded by this addendum. Its calculated metrics are unchanged.</p>
<h2>Evidence</h2><table><tr><th>Stage</th><th>Azure execution</th><th>Receipt SHA-256</th></tr>{rows}</table>
<p><a href="AlphaWorld_T_Accuracy_Review.html">Original accuracy review</a> · <a href="AlphaWorld_T_Cash_Recovery_Evidence.json">Detailed recovery evidence</a></p></html>'''
    with (OUT/'AlphaWorld_T_Cash_Recovery_Addendum.html').open('x',encoding='utf-8') as f:f.write(body)
    print(json.dumps({'status':evidence['status'],'inserted':10,'output':str(OUT/'AlphaWorld_T_Cash_Recovery_Addendum.html')}))
if __name__=='__main__':main()
