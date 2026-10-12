"""Build a private, explicitly incomplete onboarding checkpoint from audit receipts."""
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone
import json,hashlib,html,sys
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.parser import parse_flex_xml
W=ROOT/'.work/alpha-t-onboarding-completion-20261009'
O=ROOT/'output/audit/AlphaWorld_T_Onboarding_20261009'
def read(name):return json.loads((W/name).read_bytes())
cash=read('fresh-cash-reconciliation-2.json');old=read('supplemental-inputs.json')
broker=read('broker-reconciliation.json');fields=read('broker-field-verification.json')
instruments=read('trade-instrument-verification-v3.json')
hibor=read('hkab-history-verification.json');risk=read('extended-risk-summary-v2.json')
assert cash['fresh_detail_rows']==cash['with_recoverable_detail_id']==1718
assert not cash['duplicate_detail_ids'] and cash['ambiguous']==0
assert len(cash['statement_bridges'])==16 and all(r['matched'] for r in cash['statement_bridges'])
assert instruments['rows']==instruments['verified']==1525
assert hibor['status']=='verified' and len(hibor['rows'])==420
assert len(risk['months'])==21 and all(r['crisis_status']=='private reconstructed arithmetic verified' for r in risk['months'])
assert all(v['rows_with_issues']==0 for k,v in fields['summary'].items() if k!='trades')
missing={r['old_row_sha256'] for r in cash['missing']}
original_lookup={r['row_sha256']:r for r in broker['comparisons']['cash_transactions']['matched']}
classifications=[];cache={}
for r in old['cash_transactions']:
    if r['row_sha256'] not in missing:continue
    if r['effective_date']>'2026-09-30':kind='outside factsheet cutoff; not changed'
    else:
        sha=original_lookup[r['row_sha256']]['source_sha256']
        if sha not in cache:
            raw=(W/'originals'/(sha+'.xml')).read_bytes();assert hashlib.sha256(raw).hexdigest()==sha
            cache[sha]={e.row_sha256:e.attributes for e in parse_flex_xml(raw).rows if e.record_type=='CashTransaction'}
        assert cache[sha][r['row_sha256']].get('accountId')=='-'
        kind='original non-account-specific summary; preserve raw, do not count as extra detail'
    classifications.append({'account':r['account_id'],'row_sha256':r['row_sha256'],'effective_date':r['effective_date'],'classification':kind})
counts=dict(Counter(r['classification'] for r in classifications))
assert sorted(counts.values())==[30,34]
evidence_files=['broker-reconciliation.json','broker-field-verification.json','trade-instrument-verification-v3.json','fresh-cash-reconciliation-2.json','hkab-history-verification.json','extended-risk-summary-v2.json','regression.xml']
summary={'status':'private qualification advanced; onboarding not complete','generated_at':datetime.now(timezone.utc).isoformat(),
 'production_database_writes':0,'production_deployments':0,'viewer_activation':False,'factsheet_wording_changed':False,
 'cash_detail_ids_recovered':1718,'cash_statement_totals_exact':16,'cash_ambiguity_classifications':counts,
 'original_field_comparisons':fields['summary'],'trade_instrument_resolution':{'rows':1525,'verified':1525,'kinds':dict(Counter(r['identity_kind'] for r in instruments['evidence']))},
 'hibor_owner_fixings_matched':420,'private_stress_months_arithmetic_passed':21,'drawdown_simulations':{'months_with_5000_paths':20,'initial_month_insufficient_history':1},
 'regression':{'tests_passed':47,'subtests_passed':34,'initial_environment_failure':'One temporary-directory permission error; all tests passed using a scoped writable test temp directory.'},
 'cash_classifications':classifications,
 'remaining':['Qualify a provenance-preserving detail-cash migration and replay/rollback before any shared source-table changes.',
 'Independently verify HSI and SPY daily closing-price histories and crisis constituent/proxy prices. Owner NAV is not an exchange closing price.',
 'Integrate the qualified T risk contract; keep current production cache safeguards until replacement tests pass.',
 'Render and numerically/visually accept every monthly 20-slide presentation with final qualified inputs.',
 'Qualify Azure runtime, security, rollback and website downloads, obtain final report approval, and then activate T viewer access.'],
 'evidence':[{'file':n,'sha256':hashlib.sha256((W/n).read_bytes()).hexdigest()} for n in evidence_files]}
O.mkdir(parents=True,exist_ok=True)
with (O/'Checkpoint.json').open('x',encoding='utf-8') as f:json.dump(summary,f,indent=2)
rows=''.join('<tr><td>'+html.escape(k)+'</td><td>'+str(v['rows'])+'</td><td>'+('Instrument mapping separately verified' if k=='trades' else 'Exact original-field match')+'</td></tr>' for k,v in fields['summary'].items())
pending=''.join('<li>'+html.escape(t)+'</li>' for t in summary['remaining'])
page='''<!doctype html><html lang="en"><meta charset="utf-8"><title>Alpha World T — onboarding checkpoint</title>
<style>body{font:16px/1.55 Arial,sans-serif;max-width:1000px;margin:40px auto;color:#1b2638;padding:0 24px}h1{font-size:30px}h2{margin-top:32px;font-size:21px}.status{background:#fff4da;border-left:5px solid #bb7900;padding:16px}table{border-collapse:collapse;width:100%}td,th{padding:9px;text-align:left;border-bottom:1px solid #dce2ea}small{color:#586577}li{margin:9px 0}</style>
<h1>Alpha World T: private onboarding checkpoint</h1><small>9 October 2026 · Confidential · Not a release approval</small>
<p class="status"><strong>Onboarding is not complete.</strong> This work made no production database writes, deployments or viewer-access changes. Factsheet wording remains unchanged.</p>
<h2>Broker evidence recovered</h2><p>Two freshly generated XML exports cover both T source accounts from 20 January 2025 through 30 September 2026. All 1,718 detailed cash entries match existing events and now have distinct broker transaction IDs and types. All 16 tested dividend and withholding-tax statement totals reconcile exactly.</p>
<p>The unmatched existing records comprise 34 original non-account-specific summary rows and 30 entries after the September factsheet cutoff. No rows were deleted or imported. A qualified migration must preserve raw summary evidence without treating it as additional cash transactions.</p>
<h2>Database-to-original checks</h2><table><tr><th>Dataset</th><th>Rows</th><th>Result</th></tr>'''+rows+'''</table>
<p>Every trade value and original field checked matches the source. Instrument resolution is separately verified: actual broker instrument metadata or statement-of-funds evidence where supplied; deterministic internal FLEX identifiers where the original broker omitted a Conid. Internal identifiers are not represented as broker transaction IDs.</p>
<h2>Reference rates and private risk calculations</h2><p>All 420 stored one-month HIBOR fixings match freshly retrieved HKAB publisher responses. All 21 private monthly crisis reconstructions pass arithmetic checks. Twenty months have 5,000 independently replayed bootstrap paths; January 2025 remains unavailable because it has fewer than 20 daily returns. No partial-year observation is presented as a complete one-year drawdown.</p>
<p>The crisis reconstruction uses exact broker FX for current holding values and explicitly identifies source receivables retained in the flat residual. Archived crisis market prices and proxies remain independently unverified; these private results are not release-qualified.</p>
<h2>Verification</h2><p>47 targeted regression tests and 34 subtests passed. These are computational and safeguard checks, not visual acceptance of every presentation. This checkpoint does not claim that every report is ready to release.</p>
<h2>Remaining work</h2><ol>'''+pending+'''</ol><p><a href="Checkpoint.json">Machine-readable checkpoint and evidence hashes</a></p></html>'''
with (O/'Checkpoint.html').open('x',encoding='utf-8') as f:f.write(page)
print(json.dumps({'report':str(O/'Checkpoint.html'),'status':summary['status'],'cash_classifications':counts}))
