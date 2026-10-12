"""Append a source-bound private checkpoint; never overwrite prior evidence."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import html
import json
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/audit/AlphaWorld_T_Onboarding_20261009'
WORK = ROOT / '.work/alpha-t-onboarding-completion-20261009'


def read(path):
    return json.loads(path.read_bytes())


prior = read(OUT / 'Checkpoint.json')
market_path = ROOT / '.work/alpha-t-market-reference-candidates-20261009/qualified-closes.json'
deck_path = ROOT / '.work/alpha-t-presentations-qualification-v5/summary-all.json'
risk_path = WORK / 'extended-risk-summary-v3-proxy.json'
market, decks, risk = map(read, [market_path, deck_path, risk_path])
suite = ET.parse(WORK / 'regression-v2.xml').getroot().find('testsuite')
assert suite is not None and suite.get('failures') == suite.get('errors') == '0'
assert market['counts'] == {'hang-seng-price-index': 419, 'spy-settlement': 427}
assert not market['mismatches_or_missing']
assert decks['months'] == 21 and decks['slides'] == 420 and not decks['errors']
assert all(item['financial']['status'] == 'passed' for item in decks['items'])
assert len(risk['months']) == 21 and all(not m['crisis_issues'] for m in risk['months'])

remaining = [
    'Independently verify the consumed 2008–2009 split- and dividend-adjusted crisis price histories. Public Nasdaq returned no observations; direct Stooq returned browser verification and normal browser access did not reach the history. No paid provider is available from the owner.',
    'Integrate and qualify the T-specific HKD/HIBOR risk-cache and runtime contract without disabling current safeguards.',
    'Rebuild presentations with final qualified risk inputs and finish visual review of all monthly slides. The private batch withholds unqualified crisis and benchmark analytics.',
    'Qualify Azure security, runtime, rollback and website downloads; obtain final report approval and activate viewer access only after the applicable checks pass.',
]
evidence_paths = [OUT / 'Checkpoint.json', market_path, deck_path, risk_path,
                  WORK / 'regression-v2.xml', ROOT / 'src/ibkr_etl/alpha_world_t_crisis_policy.py']
summary = {
    'status': 'private qualification advanced; onboarding not complete',
    'generated_at': datetime.now(timezone.utc).isoformat(),
    'scope': 'Incremental local qualification; not a fresh Azure-state assertion',
    'production_writes_this_work': 0, 'production_deployments_this_work': 0,
    'viewer_access_changed_this_work': False, 'factsheet_wording_changed': False,
    'cash_detail_ids_reconciled': prior['cash_detail_ids_recovered'],
    'cash_statement_totals_exact': prior['cash_statement_totals_exact'],
    'hibor_fixings_matched': prior['hibor_owner_fixings_matched'],
    'actual_history_hedge_closes_verified': market['counts'],
    'market_provider_exceptions': market['provider_exceptions'],
    'presentation_months_computationally_checked': 21,
    'presentation_slides_exported': 420,
    'native_charts_checked': sum(x['financial']['financial_charts_verified'] for x in decks['items']),
    'risk_fields_checked': sum(x['financial']['risk_metrics_verified'] for x in decks['items']),
    'all_month_visual_acceptance_complete': False,
    'crisis_proxy': {'owner_approved': True, 'scope': 'T only; PSKY/SKYD instrument 804144296 uses SPY for pre-listing hypothetical history',
                    'private_replay_months': 21, 'affected_periods': 'May–September 2026',
                    'actual_nav_and_core_kpis_changed': False,
                    'independent_crisis_price_verification_complete': False},
    'regression': {'tests_passed': 60, 'subtests_passed': 39, 'junit_cases': int(suite.get('tests'))},
    'owner_has_separate_historical_market_provider': False,
    'subscriptions_or_purchases_made': False,
    'cash_source_policy': 'Original records preserved. Recovered detail IDs are audit evidence; no shared cash-table migration is required merely to calculate T from whole-account NAV.',
    'remaining': remaining,
    'evidence': [{'file': str(p.relative_to(ROOT)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in evidence_paths],
}
with (OUT / 'Checkpoint_v2.json').open('x', encoding='utf-8') as stream:
    json.dump(summary, stream, indent=2, ensure_ascii=False)
page = '''<!doctype html><html lang="en"><meta charset="utf-8"><title>Alpha World T qualification checkpoint v2</title>
<style>body{font:16px/1.55 Arial;max-width:1000px;margin:40px auto;padding:0 24px;color:#243247}h1{font-size:29px}.notice{background:#fff4da;padding:18px;border-left:5px solid #b77914}li{margin:10px 0}small{color:#526174}</style>
<h1>Alpha World T: qualification checkpoint v2</h1><small>9 October 2026 · Private · Not a release approval</small>
<p class="notice"><b>Onboarding is not complete.</b> This work did not deploy changes, modify production records or activate viewer access. Factsheet wording and actual NAV calculations remain unchanged.</p>
<h2>Evidence completed</h2><ul>
<li>1,718 detailed cash entries reconciled and 16 statement totals matched. Original records remain preserved.</li>
<li>420 published one-month HIBOR fixings matched.</li>
<li>419 Hang Seng and 427 SPY actual-history closes checked. The 20 April 2026 Nasdaq flat OHLC/no-volume row was excluded and independently corroborated against ChartExchange; stored data was not changed.</li>
<li>21 monthly presentations exported: 420 slides, 126 native charts and 252 risk fields passed automated financial checks. This is not full visual or PowerPoint-application acceptance.</li>
<li>The owner-approved SPY identity proxy is applied privately to T's hypothetical PSKY/SKYD crisis exposure. All 21 monthly arithmetic replays pass; five months change. Actual NAV and core KPIs do not change.</li>
<li>60 regression tests and 39 subtests passed.</li></ul>
<h2>Remaining</h2><ol>''' + ''.join('<li>' + html.escape(x) + '</li>' for x in remaining) + '''</ol>
<p>No data subscription or purchase was made. A missing independent source is not treated as a successful validation. Existing release safeguards remain in force.</p>
<p><a href="Checkpoint_v2.json">Machine-readable evidence and hashes</a> · <a href="Checkpoint.html">Previous checkpoint (superseded)</a></p></html>'''
with (OUT / 'Checkpoint_v2.html').open('x', encoding='utf-8') as stream:
    stream.write(page)
print(json.dumps({'checkpoint': str(OUT / 'Checkpoint_v2.html'), 'status': summary['status']}))
