"""Seal completed cutover evidence; no database or report mutations."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import control_ms_website_exact_cutover as control
import deploy_ms_website_cutover as deployment

p = control.p
FOLDER = control.FOLDER
REPORT_FOLDER = p.ROOT / '.work/alpha-ms-format-worker-candidate-20261008-r4/registered-batch-v1'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def verify_downloads(downloads, accepted_items, root):
    expected = {item['generation_run_id']: item for item in accepted_items}
    require(len(expected) == len(accepted_items) == len(downloads) == 42, 'Exact 42-file inventory required')
    require(len({item['period_end'] for item in expected.values()}) == 21, 'Exact 21-month inventory required')
    result = []
    seen = set()
    root = root.resolve()
    for row in downloads:
        route = re.fullmatch(r'/api/onboarding/alpha-world-ms/historical-reports/(\d+)/download', row['href'])
        require(route is not None, 'Observed route outside MS private batch')
        run = int(route.group(1))
        require(run in expected and run not in seen, 'Unexpected or duplicate report route')
        seen.add(run)
        item = expected[run]
        require(item['status'] == 'validated' and item['batch_id'] == 'a46bea9b-ed50-4942-9dda-bc7e28d05b07',
                'Accepted file scope differs')
        path = Path(row['path']).resolve()
        require(path.parent == root and path.is_file(), 'Download outside explicit download directory')
        registered = Path(item['blob_name']).name
        original = Path(registered)
        require(path.name == registered or re.fullmatch(re.escape(original.stem) + r' \([1-9]\d*\)' +
                re.escape(original.suffix), path.name), 'Filename differs beyond a Chrome duplicate suffix')
        require(row['label'].startswith(item['period_end'] + ' · ' + item['report_type'].upper()),
                'Visible period/report label differs')
        digest = p.sha(path)
        require(digest == item['artifact_sha256'] and path.stat().st_size == item['byte_count'],
                'Downloaded bytes differ from accepted artifact')
        result.append({'generation_run_id': run, 'period_end': item['period_end'], 'report_type': item['report_type'],
                       'sha256': digest, 'byte_count': path.stat().st_size, 'route': row['href']})
    require(seen == set(expected) and sum(x['report_type'] == 'factsheet' for x in result) == 21 and
            sum(x['report_type'] == 'presentation' for x in result) == 21, 'Report pair inventory differs')
    return sorted(result, key=lambda x: x['generation_run_id'])


def main():
    require(not (FOLDER / 'completion.json').exists(), 'Preserve original completion evidence')
    _, image = control.gate()
    current = deployment.current_images(require_candidate=True)
    records = {}
    for mode in ('qualify', 'apply', 'verify'):
        record = p.release.read(control.stage_folder(mode), 'qualified')
        p.verify_protected(record)
        require(record['image'] == image and record['binding_sha256'] == p.sha(FOLDER / 'binding.json'),
                'Qualified stage binding differs')
        require(record['proof_sha256'] == p.sha(control.stage_folder(mode) / 'proof.json'), 'Stage proof bytes differ')
        records[mode] = record
    require(records['qualify']['status'] == 'exact_writer_and_rollback_qualified' and
            records['apply']['status'] == 'live_apply_verified' and records['verify']['status'] == 'live_verify_verified',
            'Every qualified release stage must pass')
    verification = p.release.read(control.stage_folder('verify'), 'proof')
    require(verification['persistent_database_writes'] is False and verification['public_before'] == verification['public_after'],
            'Separate verification must be entirely read-only')
    require(verification['preview_validation']['ready_for_owner_preview_approval'] is True and
            all(verification[k] is False for k in ('factsheets_changed', 'ibkr_source_changed', 'reports_released',
                                                  'approvals_written', 'onboarding_activated')), 'Protected state differs')
    accepted = p.release.read(REPORT_FOLDER / 'registration-verify-v2', 'qualified')
    p.verify_protected(accepted)
    report_proof = accepted['proof']
    require(report_proof['batch_id'] == 'a46bea9b-ed50-4942-9dda-bc7e28d05b07' and
            report_proof['batch']['manifest_sha256'] == 'b64c4a187ebf772da7ae81ed9a9b19b88a418b2c5e97453f8d58516dc2b22e24',
            'Protected report batch differs')
    downloads = json.loads((FOLDER / 'browser-report-downloads.json').read_bytes())
    items = verify_downloads(downloads, report_proof['items'], Path('C:/Users/antho/Downloads'))
    screenshots = {name: p.sha(FOLDER / name) for name in
                   ('ms-preview-ready.jpg', 'ms-performance-gspc.jpg', 'ms-performance-hsi.jpg')}
    for kind in ('website', 'benchmark'):
        record = p.release.read(FOLDER, kind + '-qualified')
        p.verify_protected(record)
        require(record['image'] == image and record['settings_preserved'] and record['authentication_preserved'],
                'Coordinated image evidence differs')
    record = {'status': 'live_ms_website_cutover_verified_private_preview_ready',
              'verified_at_utc': datetime.now(timezone.utc).isoformat(), 'image': image,
              'website_revision': current['website']['properties']['latestReadyRevisionName'],
              'benchmark_primary': 'GSPC', 'benchmark_secondary': 'HSI', 'reporting_currency': 'HKD',
              'comparison_rows_verified': 853, 'separate_read_only_verification': True,
              'stage_execution_ids': {mode: evidence['execution'] for mode, evidence in records.items()},
              'stage_proof_sha256': {mode: evidence['proof_sha256'] for mode, evidence in records.items()},
              'binding_sha256': p.sha(FOLDER / 'binding.json'), 'checker_sha256': p.sha(Path(__file__)),
              'report_downloads': items, 'website_downloads_verified': 42,
              'accepted_report_qualification_sha256': p.sha(REPORT_FOLDER / 'registration-verify-v2/qualified.json'),
              'browser_download_observation_sha256': p.sha(FOLDER / 'browser-report-downloads.json'),
              'screenshot_sha256': screenshots,
              'preview_evidence_sha256': verification['preview_validation']['evidence_sha256'],
              'preview_approval_written': False, 'onboarding_activated': False, 'reports_changed': False,
              'ibkr_sources_changed': False, 'old_comparison_history_preserved': True,
              'authentication_credentials_schedules_preserved': True,
              'full_live_rollback_exercised': False,
              'next_gate': 'Owner review and private-preview approval; final activation remains a separate action'}
    p.release.build.save(FOLDER, 'completion',
                         {**record, 'protected_receipt': p.retain(record, 'ms-website-live-cutover-completion-v1')})
    print(json.dumps({'status': record['status'], 'comparison_rows': 853, 'downloads_verified': 42,
                      'preview_approval_written': False, 'onboarding_activated': False}), flush=True)


if __name__ == '__main__':
    main()
