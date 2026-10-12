"""Recheck affected disposable risk caches in the qualified risk worker.

Uses its installed fingerprint/readiness and per-date locking implementation.
No broker writes, frozen-snapshot changes, approvals or onboarding activation.
"""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, '/app/src')
from ibkr_etl.config import load_settings
from ibkr_etl.db import connect
from ibkr_etl.risk_snapshots import build_risk_snapshot, RiskInputsNotReady
from ibkr_etl.risk_refresh import risk_date_lock, mark_cache_check


def main():
    settings = load_settings()
    c = connect(settings.database_path, settings.database_url, settings.tenant_id)
    rebuilt = []
    unchanged = []
    blocked = []
    try:
        for subject in ('U16088571','U16088606','hong-kong-china','alpha-world-ms'):
            dates = [str(r[0]) for r in c.execute('''SELECT DISTINCT snapshot_date FROM subject_nav_series
                WHERE tenant_id=? AND subject_id=? AND snapshot_date >= '2026-08-27' ORDER BY snapshot_date''',
                (settings.tenant_id, subject))]
            if len(dates) > 45:
                raise ValueError('Risk correction scope unexpectedly exceeds approved recovery range')
            for day in dates:
                scope = {'tenant_id':settings.tenant_id,'subject_id':subject,'as_of':day}
                c.commit()
                with risk_date_lock(c, **scope) as acquired:
                    if not acquired:
                        blocked.append({'subject_id':subject,'as_of':day,'reason':'date_busy'})
                        continue
                    c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                    try:
                        result = build_risk_snapshot(c, **scope, only_if_changed=True)
                    except RiskInputsNotReady as error:
                        c.rollback()
                        mark_cache_check(c, **scope, status='blocked', reason=str(error), invalidate=True)
                        blocked.append({'subject_id':subject,'as_of':day,'reason':str(error)})
                        continue
                    mark_cache_check(c, **scope, status='current')
                (unchanged if result['status']=='unchanged' else rebuilt).append(result)
                print('ALPHA_MS_RECOVERY_RISK_DATE '+json.dumps(result,sort_keys=True),flush=True)
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'risk_correction_complete' if not blocked else 'risk_correction_blocked',
            'rebuilt':len(rebuilt),'unchanged':len(unchanged),'blocked':blocked,
            'frozen_reports_changed':False,'broker_facts_changed':False,'onboarding_activated':False}),flush=True)
        return 0 if not blocked else 1
    finally:
        c.close()


if __name__=='__main__':
    raise SystemExit(main())
