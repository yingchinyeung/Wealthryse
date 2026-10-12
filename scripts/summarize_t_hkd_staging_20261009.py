"""Publish an evidence-bound checkpoint without claiming completed onboarding."""
import hashlib
import html
import json
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/audit/AlphaWorld_T_Onboarding_20261009'
CORRECTION=ROOT/'.work/alpha-t-hkd-correction-20261009-v1'


def read(path):return json.loads(path.read_bytes())


def receipt(path,status):
    data=read(path)
    if data['status']!='Succeeded' or not data['job_settings_unchanged'] or len(data['records'])!=1:
        raise ValueError('Successful exact receipt required: '+str(path))
    row=data['records'][0]
    if row['status']!=status:raise ValueError('Receipt status differs')
    return row


def main():
    apply=receipt(CORRECTION/'apply-result.json','hkd_staged_pending_approval')
    verify=receipt(CORRECTION/'verify-result.json','verified_hkd_pending_approval')
    rehearsal=receipt(CORRECTION/'rehearsal-result.json','passed')
    database_path=ROOT/'.work/alpha-t-postgres-qualification-20261009-v4/postgres-result.json'
    database=receipt(database_path,'passed')
    crisis_path=ROOT/'.work/alpha-t-internal-crisis-cloud-20261009-v3/crisis-result.json'
    crisis=receipt(crisis_path,'passed')
    pdf_path=ROOT/'.work/alpha-t-reporting-batch-20261009-v2/verification.json'
    deck_path=ROOT/'.work/alpha-t-internal-presentations-20261009-v1/summary-all.json'
    pdf=read(pdf_path);decks=read(deck_path)
    suite=ET.parse(CORRECTION/'regression.xml').getroot().find('testsuite')
    if suite is None or int(suite.get('errors','0')) or int(suite.get('failures','0')):
        raise ValueError('Passing regression suite required')
    if apply['plan_sha256']!=verify['plan_sha256'] or apply['plan_sha256']!=rehearsal['plan_sha256']:
        raise ValueError('Correction receipts differ')
    if database['nav_observations']!=443 or database['months']!=21 or crisis['months']!=21:
        raise ValueError('Incomplete database qualification')
    if pdf['months']!=21 or pdf['pages_pixel_identical']!=42 or decks['months']!=21 or decks['errors']:
        raise ValueError('Incomplete private artifact qualification')
    remaining=[
        'Qualify and deploy scoped HKD runtime images, including website comparisons, scheduled workers, security scans and runtime rollback.',
        'Record the new HKD calculation approval against its exact definition; do not reuse the revoked USD scope.',
        'Finish all-month visual and native PowerPoint acceptance; January and September internal decks were visually inspected, not all 420 slides.',
        'Register and verify the 21 factsheets and 21 presentations through the live report workflow, then complete report approval, private website acceptance and viewer activation.',
    ]
    paths=[CORRECTION/n for n in ('plan.json','rehearsal-result.json','apply-result.json','verify-result.json','regression.xml')]
    paths += [database_path,crisis_path,pdf_path,deck_path]
    result={
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'status':'HKD configuration staged on Azure; onboarding not complete',
        'supersedes_status_in':'Checkpoint_v3.json; older evidence preserved',
        'live_configuration':{'base_currency':'HKD','reporting_currency':'HKD','lifecycle':'onboarding',
            'new_definition_status':'validated_pending_approval','usd_definition':'superseded_preserved',
            'usd_calculation_approval':'revoked_preserved','website_primary':'HSI','website_secondary':'GSPC',
            'new_contract_sha256':read(CORRECTION/'plan.json')['new_definition_sha256'],
            'changed_configuration_rows':apply['changed_configuration_rows'],
            'independent_readback':'passed','rollback_rehearsal':'passed','backup':apply['backup']},
        'database_rehearsal':{'daily_navs':443,'monthly_cache_and_reporting_models':21,
            'cache_replay':'unchanged','tenant_isolation':'passed','persistent_writes':0,
            'isolation':'session-local PostgreSQL tables, not a separate server',
            'candidate_code_sha256':database['candidate_code_sha256'],
            'later_change':'T website benchmark-role guard corrected to preserve recorded HSI primary/GSPC secondary; not yet cloud-qualified'},
        'crisis_replay':{'months':21,'qualification':'internal_only','independent_provider_price_certification':False,
            'proxies':'documented; owner-approved T-only SPY identity fallback',
            'additional_provider_subscription_required_for_internal_onboarding':False},
        'private_factsheets':{'months':21,'pages_pixel_identical_to_accepted_layout':42,'wording_changed':False},
        'private_presentations':{'months':21,'slides':420,'financial_package_errors':0,
            'charts_per_deck_verified':6,'risk_values_per_deck_verified':13,
            'all_month_visual_acceptance':'pending','native_powerpoint_acceptance':'pending'},
        'regression_junit_cases':int(suite.get('tests')),
        'earlier_scoped_repair':'Three missing archived Flex OpenPosition receivable evidence rows restored with protected original-source hashes; normalized financial source rows unchanged.',
        'deployed_new_runtime':False,'reports_released':False,'viewer_access_activated':False,
        'remaining':remaining,
        'evidence':[{'file':str(p.relative_to(ROOT)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths],
    }
    with (OUT/'Checkpoint_v4.json').open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><title>Alpha World T onboarding checkpoint v4</title>
<style>body{font:16px/1.6 Arial;color:#243247;max-width:950px;margin:40px auto;padding:0 24px}h1{font-size:28px}.notice{background:#fff3d8;border-left:5px solid #b77914;padding:18px}li{margin:9px 0}</style>
<h1>Alpha World T: HKD staging verified</h1><p>9 October 2026 · Internal progress and evidence record</p>
<p class="notice"><b>Onboarding is not complete.</b> The live configuration is now HKD, with the new definition awaiting its own approval. USD configuration and approval history remain preserved. Reports are not released, and viewer access is not activated.</p>
<h2>Verified</h2><ul><li>The nine-row metadata correction passed a PostgreSQL temporary-table rehearsal, rollback check and separate post-commit readback. A protected rollback backup was verified. Other subjects and broker source data were not changed by this correction.</li>
<li>443 daily NAV observations and all 21 monthly cache/reporting models passed the isolated Azure database rehearsal. Replaying each cache was unchanged. These were temporary-table tests, not a deployed runtime.</li>
<li>All 21 internal crisis replays passed source-bound checks using provisional prices and documented proxies. They are not independently certified market prices.</li>
<li>21 private factsheets match the accepted layout pixel-for-pixel across 42 pages. All 21 private presentations passed financial/package checks, covering 420 slides. Complete visual/native PowerPoint acceptance remains pending.</li>
<li>T's recorded HSI primary and S&amp;P 500 secondary website roles are preserved; the factsheet retains HIBOR + Spread. Factsheet wording remains unchanged.</li></ul>
<h2>Remaining</h2><ol>'''+''.join('<li>'+html.escape(x)+'</li>' for x in remaining)+'''</ol>
<p>This checkpoint replaces the current-status claims in earlier checkpoints. Prior evidence is retained; an additional historical-price subscription is not being treated as an internal-onboarding prerequisite.</p>
<p><a href="Checkpoint_v4.json">Evidence and hashes</a></p></html>'''
    with (OUT/'Checkpoint_v4.html').open('x',encoding='utf-8') as f:f.write(page)
    print(json.dumps({'status':result['status'],'report':str(OUT/'Checkpoint_v4.html')}))


if __name__=='__main__':main()
