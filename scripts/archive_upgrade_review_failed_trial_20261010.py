"""Retain the original failed installed-feature test; never rerun or relabel it."""
import hashlib, json, os, subprocess
from datetime import datetime
from pathlib import Path
from audit_azure_runtime import az, AZ
from control_upgrade_readers_20261010 import save

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261010-stage18/review-security-v1'


def main():
    if (OUT / 'failed-build.json').exists(): raise ValueError('Preserve failure archive')
    intent = json.loads((OUT / 'build-intent.json').read_bytes())
    run = az('acr', 'task', 'show-run', '-r', 'ibkrv2stgauhecwc4itaoo', '--run-id', 'ckc4')
    elapsed = (datetime.fromisoformat(run['createTime'].replace('Z', '+00:00')) - datetime.fromisoformat(intent['prepared_at'])).total_seconds()
    if run['status'] != 'Failed' or not 0 <= elapsed <= 120 or run.get('outputImages'):
        raise ValueError('Failure binding differs')
    result = subprocess.run([AZ, '-m', 'azure.cli', 'acr', 'task', 'logs', '-r', 'ibkrv2stgauhecwc4itaoo', '--run-id', 'ckc4'],
        capture_output=True, text=True, encoding='utf-8', timeout=120, env={**os.environ, 'PYTHONUTF8': '1'})
    log = result.stdout + result.stderr
    if "No module named 'ibkr_etl.admin_chat'" not in log or 'FAILED (errors=1)' not in log:
        raise ValueError('Original failed diagnostic differs')
    save(OUT, 'failed-build', {'run': 'ckc4', 'status': 'Failed', 'build_intent_sha256': hashlib.sha256((OUT / 'build-intent.json').read_bytes()).hexdigest(),
        'native_log': log, 'native_log_sha256': hashlib.sha256(log.encode()).hexdigest(),
        'reason': 'A newer admin_chat test was supplied to an older installed review image lacking that module. The application was not changed; v2 tests only its installed features.',
        'production_changed': False, 'failure_waived_as_pass': False})
    print('Original failed review build archived; not relabeled as passing')


if __name__ == '__main__': main()
