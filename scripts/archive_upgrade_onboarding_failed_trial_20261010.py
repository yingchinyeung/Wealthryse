"""Preserve the rejected test-scope attempt and verified route explanation."""
from datetime import datetime
import hashlib,json
from pathlib import Path
from audit_azure_runtime import az
from control_upgrade_readers_20261010 import save
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261010-stage19/onboarding-security-v1'


def main():
    if (OUT/'failed-build.json').exists():raise ValueError('Preserve prior failure')
    intent=json.loads((OUT/'build-intent.json').read_bytes())
    run=az('acr','task','show-run','-r','ibkrv2stgauhecwc4itaoo','--run-id','ckc6')
    elapsed=(datetime.fromisoformat(run['createTime'].replace('Z','+00:00'))-datetime.fromisoformat(intent['prepared_at'])).total_seconds()
    log=(OUT/'native-build.log').read_text(encoding='utf-8')
    if run['status']!='Failed' or run.get('outputImages') or not 0<=elapsed<=120 or 'FAILED (errors=3)' not in log:
        raise ValueError('Unexpected failed attempt')
    web=ROOT/'.work/platform-upgrade-20261009-stage7/runtime/src/ibkr_etl/web.py';text=web.read_text(encoding='utf-8')
    expected='DERIVED_REFRESH_JOB_NAME\n            if str(row["subject_type"]) in {"filtered_portfolio", "composite_portfolio"}\n            else ONBOARDING_ANALYTICS_JOB_NAME'
    if expected not in text:raise ValueError('Reviewed website routing differs')
    save(OUT,'failed-build',{'run':'ckc6','status':'Failed','native_log':log,
        'native_log_sha256':hashlib.sha256(log.encode()).hexdigest(),
        'build_intent_sha256':hashlib.sha256((OUT/'build-intent.json').read_bytes()).hexdigest(),
        'reason':'T-only tests were incorrectly supplied to the physical-account onboarding image. The qualified website routes composite and filtered portfolios through DERIVED_REFRESH_JOB_NAME. The separate v2 trial uses physical-account entrypoint tests and does not add T features.',
        'reviewed_website_source_sha256':hashlib.sha256(web.read_bytes()).hexdigest(),
        'production_changed':False,'failure_waived_as_pass':False})
    print('Rejected test-scope attempt preserved; no application feature changes')


if __name__=='__main__':main()
