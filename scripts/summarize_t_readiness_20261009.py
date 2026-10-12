"""Record current Azure observations and local qualification without release claims."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import html
import json
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.work/alpha-t-readiness-20261009-v1'
OUT = ROOT / 'output/audit/AlphaWorld_T_Onboarding_20261009'
live = json.loads((WORK / 'readiness-result.json').read_bytes())
core = json.loads((ROOT / '.work/alpha-t-core-cache-qualification-20261009-v1/summary.json').read_bytes())
access = json.loads((WORK / 'wiki-access.json').read_bytes())
suite = ET.parse(WORK / 'regression.xml').getroot().find('testsuite')
assert live['status'] == 'Succeeded' and live['job_settings_unchanged']
assert live['records'][0]['read_only'] and not live['records'][0]['production_data_written']
assert core['months'] == 21 and all(r['status'] == 'passed' for r in core['results'])
assert suite is not None and suite.get('errors') == suite.get('failures') == '0'
paths = [WORK / 'readiness-result.json', WORK / 'regression.xml', WORK / 'wiki-access.json',
    ROOT / '.work/alpha-t-core-cache-qualification-20261009-v1/summary.json',
    ROOT / 'src/ibkr_etl/alpha_world_t_cache.py', ROOT / 'src/ibkr_etl/risk_snapshots.py',
    ROOT / 'tests/test_alpha_world_t_cache.py']
result = {'generated_at': datetime.now(timezone.utc).isoformat(),
    'status': 'not_onboarded; local core-cache qualification advanced',
    'azure_probe': live, 'private_core_cache_months': core['months'],
    'regression': {'tests_passed':109, 'subtests_passed':78, 'junit_cases':int(suite.get('tests'))},
    'local_fix': 'Reject T legacy/preloaded-input calculation, cache reads and writes until production qualification; private source-bound HKD/HIBOR cache replay added.',
    'deployed': False, 'viewer_access_changed': False, 'factsheet_wording_changed': False,
    'new_external_access_evidence': access,
    'next_external_requirement': 'A permitted independent historical-price source. Nasdaq WIKI is a candidate but requires an API key; dataset coverage, provenance and allowed use still require verification. No account created and no purchase made.',
    'remaining_internal_work': ['T production risk and benchmark integration',
        'Final risk-enriched monthly presentations and full visual acceptance',
        'Azure release, security and rollback qualification', 'Final report approval and viewer activation'],
    'evidence': [{'file':str(p.relative_to(ROOT)), 'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]}
with (OUT / 'Checkpoint_v3.json').open('x', encoding='utf-8') as stream:
    json.dump(result, stream, indent=2)
page = '''<!doctype html><html lang="en"><meta charset="utf-8"><title>Alpha World T checkpoint v3</title>
<style>body{font:16px/1.6 Arial;max-width:940px;margin:40px auto;padding:0 24px;color:#243247}h1{font-size:28px}.notice{background:#fff3d8;padding:18px;border-left:5px solid #b77914}li{margin:10px 0}</style>
<h1>Alpha World T: onboarding checkpoint v3</h1><p>9 October 2026 · Private verification record</p>
<p class="notice"><b>Not onboarded.</b> A fresh, read-only Azure probe confirms there is no T onboarding case, registered report, reporting snapshot or risk cache. The active reporting worker does not contain the T analytics modules. No deployment, production data write or viewer-access change was made.</p>
<h2>Progress in this work</h2><ul><li>All 21 private monthly core-cache candidates pass deterministic replay using source-bound HKD NAV and inception-to-valuation HIBOR evidence.</li>
<li>Closed local alternate-entry gaps that could accept unqualified T legacy calculations or caches. Production guards remain in force.</li>
<li>109 tests and 78 subtests passed, including MS regression checks. The Azure diagnostic succeeded with saved job settings unchanged.</li></ul>
<h2>External blocker</h2><p>Independent crisis-price verification is incomplete. A public request to the documented Nasdaq WIKI table returned HTTP 403 with code QEPx04 and explicitly required an API key. This is a possible additional source, not verified coverage or permission to use its data. No account, subscription or purchase was created.</p>
<h2>Remaining internal work</h2><ul>''' + ''.join('<li>'+html.escape(x)+'</li>' for x in result['remaining_internal_work']) + '''</ul>
<p>Factsheet wording and actual source data remain unchanged.</p><p><a href="Checkpoint_v3.json">Evidence and hashes</a> · <a href="Checkpoint_v2.html">Previous checkpoint</a></p></html>'''
with (OUT / 'Checkpoint_v3.html').open('x', encoding='utf-8') as stream:
    stream.write(page)
print(json.dumps({'report':str(OUT / 'Checkpoint_v3.html'), 'status':result['status']}))
