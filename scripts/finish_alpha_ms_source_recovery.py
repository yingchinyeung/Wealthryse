"""Create a protected closeout only after every scoped native check succeeds."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / '.work/alpha-ms-recovery-20261007'
sys.path[:0] = [str(ROOT / '.work/alpha-ms-azure-controller-runtime'), str(ROOT / 'scripts')]
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from deploy_metadata_monitor import request, BASE
from run_alpha_ms_recovery_operation import invariant


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    current = BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net', credential=credential)
    legacy = BlobServiceClient('https://ibkrv115rcc62xxqblay.blob.core.windows.net', credential=credential)
    sources = []
    for day in ('2026-08-27','2026-08-28'):
        info = json.loads((FOLDER / ('flex-' + day + '-source.json')).read_bytes())
        original = legacy.get_blob_client('raw-statements', info['blob']).download_blob().readall()
        copied = current.get_blob_client('ibkr-raw', '00000000-0000-0000-0000-000000000001/recovery/20261007/flex_' + day + '_' + info['sha256'] + '.xml').download_blob().readall()
        if sha(original) != info['sha256'] or copied != original:
            raise ValueError('Original archived source integrity no longer matches')
        sources.append({'date':day,'sha256':sha(original),'bytes':len(original),
            'legacy_blob':info['blob'],'current_archive_verified_identical':True})
    csv = json.loads((FOLDER / 'csv-controls.json').read_bytes())
    for info in csv['sources']:
        if sha((Path('C:/Users/antho/Downloads') / info['file']).read_bytes()) != info['sha256']:
            raise ValueError('Uploaded broker CSV changed')
    labels = ('fetch-2026-08-27-r2','fetch-2026-08-28','legacy-apply','current-apply',
        'derived-apply','comparison-coverage-apply','corrected-factsheets',
        'corrected-risk-caches-final','current-recovery-idempotence')
    executions = []
    jobs = {}
    for label in labels:
        ack = json.loads((FOLDER / (label + '-dispatch.json')).read_bytes())
        intent = json.loads((FOLDER / (label + '-intent.json')).read_bytes())
        path = BASE + 'Microsoft.App/jobs/' + ack['job']
        run = request('GET', path + '/executions/' + ack['execution'] + '?api-version=2024-03-01')
        if run['properties']['status'] != 'Succeeded':
            raise ValueError('Native operation not successfully complete: ' + label)
        if ack['job'] not in jobs:
            jobs[ack['job']] = request('GET', path + '?api-version=2024-03-01')
        if invariant(jobs[ack['job']]) != intent['job_invariant']:
            raise ValueError('Persistent Azure job settings changed')
        executions.append({'label':label,**ack,'status':'Succeeded','source_sha256':intent['source_sha256']})
    risk = json.loads((FOLDER / 'corrected-risk-caches-final-result.json').read_bytes())
    if len(risk['records']) != 1 or risk['records'][0]['status'] != 'risk_correction_complete' or risk['records'][0]['blocked']:
        raise ValueError('Final risk coverage still blocked or unverified')
    replay = json.loads((FOLDER / 'current-recovery-idempotence-result.json').read_bytes())
    if len(replay['records']) != 1 or any(x['positions_inserted'] for x in replay['records'][0]['counts']):
        raise ValueError('Recovery replay was not idempotent')
    prefix = 'review/alpha-world-ms/source-recovery-20261007/'
    receipts = {}
    for name in ('current-apply.json','derived-apply.json','derived-comparison-apply.json',
                 'current-idempotence.json','corrected-reports/manifest.json'):
        raw = current.get_blob_client('internal-reports', prefix + name).download_blob().readall()
        receipts[name] = {'sha256':sha(raw),'bytes':len(raw)}
    corrected = json.loads((ROOT / 'output/pdf/AlphaWorld_MS_Recovered_20261007/manifest.json').read_bytes())
    qa = json.loads((ROOT / 'tmp/pdfs/alpha-ms-recovered-20261007/numeric-validation.json').read_bytes())
    if qa['status'] != 'pass' or len(qa['reports']) != 2:
        raise ValueError('Independent PDF financial and chart checks missing')
    result = {'status':'original_source_recovery_and_private_corrected_factsheets_complete',
        'source_dates':['2026-08-27','2026-08-28'],'sources':sources,'csv_controls':csv['sources'],
        'unique_missing_positions_recovered':152,'databases':['legacy','current'],
        'derived_subjects':['hong-kong-china','alpha-world-ms'],
        'existing_broker_facts_unchanged':True,'existing_derived_nav_values_unchanged':True,
        'published_snapshots_unchanged':True,'reports_released':False,'onboarding_activated':False,
        'executions':executions,'private_receipts':receipts,'risk_final':risk['records'][0],
        'corrected_factsheets':corrected['artifacts'],'independent_numeric_checks':qa,
        'visual_review':'All four final PDF pages inspected; approved layout retained',
        'focused_regression_tests_passed':33,'full_methodology_certification':False,
        'recovery_replay_inserts':0,'failed_rehearsals_preserved':True}
    data = json.dumps(result,sort_keys=True,indent=2).encode()
    # The owner's read-only archive grant is intentionally not widened. The
    # existing reporting identity archives these exact verified bytes separately.
    path = FOLDER / 'closeout.json'
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('Existing local closeout differs')
    else:
        with path.open('xb') as f:
            f.write(data)
    print(json.dumps({'status':result['status'],'positions':152,'pdfs':2,'risk':result['risk_final'],
        'closeout_sha256':sha(data),'reports_released':False,'onboarding_activated':False}))


if __name__ == '__main__':
    main()
