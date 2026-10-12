"""Seal stage evidence; does not mutate application resources or portfolio rows."""
import json
from datetime import datetime, timezone
import release_extended_crisis_identity_20261009 as candidate
from run_specialist_gateway_read_verification import retain

root = candidate.STAGE
read = candidate.release.read
target = root / 'stage-closeout.json'
if target.exists():
    raise ValueError('Preserve existing closeout')
def result(folder):
    value = read(root / folder / 'result.json')
    if value['status'] != 'Succeeded':
        raise ValueError('Successful execution required')
    payload = json.loads(value['records'][0]['Result'])
    if not payload['passed']:
        raise ValueError('Passing evidence required')
    return {'execution': value['execution'], 'result': payload}

deployments = {kind: read(root / (kind + '-deployment-v2') / 'verified.json')
               for kind in ('app', 'risk', 'benchmark')}
if not all(v['passed'] and v['protected_receipt']['protected_readback_verified']
           for v in deployments.values()):
    raise ValueError('Protected deployment verification required')
postflight = result('history-postflight-v1')
if postflight['result']['migrated_rows_verified'] != 446:
    raise ValueError('Exact historical scope required')
value = {
    'stage': 'Approved U16088571 and MS hypothetical crisis identity policy',
    'recorded_at': datetime.now(timezone.utc).isoformat(),
    'policy_deployed': True,
    'ms_historical_migration_verified': True,
    'physical_account_stored_crisis_refresh_complete': False,
    'full_upgrade_complete': False,
    'deployments': deployments,
    'temporary_sql_rollback': result('history-sql-rehearsal-v1'),
    'migration': result('history-cache-apply-v1'),
    'independent_postflight': postflight,
    'owner_browser': {'as_of': '2026-10-07', 'hypothetical_crisis_drawdown_display': '-33.54%',
                      'runtime': 'alpha-world-ms-hkd-risk-runtime-v2',
                      'screenshot_sha256': candidate.release.sha(root / 'ms-live-proxy-risk.png')},
    'actual_returns_changed': False,
    'report_wording_or_released_files_changed': False,
    'viewer_acceptance': 'Explicitly unverified at owner request; no viewer available',
    'remaining': ['Physical-account controlled crisis refresh and dependency-ordered catch-up',
                  'New-month and governed regeneration acceptance',
                  'Private DNS, authenticated callers, build/push and cold-pull qualification',
                  'East Asia measured recovery and legacy HA',
                  'Other image-family scans, alert delivery and all-page responsive checks',
                  'Genuine daily-cycle and burn-in evidence'],
}
receipt = retain(value, 'extended-crisis-stage-closeout')
candidate.release.save(target, {**value, 'protected_receipt': receipt})
print(json.dumps({'sealed': True, 'full_upgrade_complete': False, 'receipt': receipt}))
