"""Verify browser-returned files against the accepted private batch; never approve."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(observation, proof, downloads_root):
    require(observation['origin'] == 'https://ibkrv2stg-web.purplerock-8cff24a2.eastasia.azurecontainerapps.io'
            and observation['owner_visible'] is True
            and observation['batch_status'] == 'awaiting_owner_approval'
            and observation['approval_button_enabled'] is False, 'Owner/private review state differs')
    require(observation['manifest_prefix'] == proof['batch']['manifest_sha256'][:16], 'Displayed manifest differs')
    require(proof['status'] == 'private_batch_verified' and proof['validation']['ready_for_owner_approval'],
            'Accepted registration required')
    items = {i['generation_run_id']: i for i in proof['items']}
    rows = observation['downloads']
    require(len(items) == len(proof['items']) == len(rows) == 42
            and len({r[0] for r in rows}) == 42 and {r[0] for r in rows} == set(items), 'Exact 42-route inventory required')
    require(len({i['period_end'] for i in items.values()}) == 21
            and len({(i['period_end'], i['report_type']) for i in items.values()}) == 42
            and sum(i['report_type'] == 'factsheet' for i in items.values()) == 21
            and sum(i['report_type'] == 'presentation' for i in items.values()) == 21, '21 monthly pairs required')
    root = downloads_root.resolve()
    result = []
    for run, name in rows:
        item = items[run]
        require(item['batch_id'] == proof['batch_id'] and item['status'] == 'validated', 'Accepted item scope differs')
        expected_name = Path(item['blob_name']).name
        require(re.fullmatch(r'AlphaWorld_MS_(Factsheet|Presentation)_\d{4}-\d{2}-\d{2}_[0-9a-f]{64}\.(pdf|pptx)', expected_name),
                'Registered filename differs')
        expected_path = Path(expected_name)
        duplicate_pattern = re.escape(expected_path.stem) + r' \([1-9]\d*\)' + re.escape(expected_path.suffix)
        require(name == expected_name or
                (observation.get('chrome_duplicate_suffixes_expected') is True and re.fullmatch(duplicate_pattern, name)),
                'Browser filename differs')
        path = (root / name).resolve()
        require(path.parent == root and path.is_file(), 'Downloaded file absent or outside exact download folder')
        digest, size = sha(path), path.stat().st_size
        require(digest == item['artifact_sha256'] and size == item['byte_count'], 'Downloaded bytes differ')
        result.append({'generation_run_id': run, 'period_end': item['period_end'],
                       'report_type': item['report_type'], 'filename': name, 'registered_filename': expected_name,
                       'chrome_duplicate_suffix': name != expected_name,
                       'download_route': f'/api/onboarding/alpha-world-ms/historical-reports/{run}/download',
                       'sha256': digest, 'byte_count': size, 'matches_accepted': True})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--downloads', type=Path, required=True)
    parser.add_argument('--protect', action='store_true')
    args = parser.parse_args()
    root = args.folder.resolve()
    stage = root / 'website-downloads-v1'
    target = stage / 'acceptance.json'
    require(not target.exists(), 'Preserve existing acceptance')
    completion_path = root / 'completion.json'
    completion = json.loads(completion_path.read_bytes())
    if root.parent.name.startswith('alpha-ms-format-worker-candidate-'):
        from qualify_ms_presentation_worker_runtime import runtime_prefix
        runtime_prefix(root.parent)
        require(args.protect, 'New format batch verification requires exact protected readback')
        from run_ms_installed_private_batch import protected
        protected(completion)
        deployed = json.loads((root.parent / 'deployment-v1/completion.json').read_bytes())
        protected(deployed)
        require(completion['status'] == 'installed_private_historical_batch_registered_and_verified'
                and completion['image'] == deployed['image']
                and completion['batch_status'] == 'awaiting_owner_approval'
                and completion['months'] == 21 and completion['factsheets'] == 21
                and completion['presentations'] == 21 and not completion['approvals_changed']
                and not completion['reports_released'] and not completion['onboarding_activated'],
                'Exact protected current format batch required')
    else:
        require(sha(completion_path) == '34b8036a2647858ac8f2463378c498333511e61e527f980ae76020c886cadcc0', 'Prior completion differs')
    qualified_path = root / 'registration-verify-v2/qualified.json'
    require(sha(qualified_path) == completion['qualification_files_sha256']['registration-verify-v2/qualified.json'], 'Registration evidence differs')
    qualified = json.loads(qualified_path.read_bytes())
    observation_path = stage / 'browser-observation.json'
    observation = json.loads(observation_path.read_bytes())
    items = verify(observation, qualified['proof'], args.downloads)
    record = {'status': 'authenticated_website_download_identity_passed',
              'verified_at_utc': datetime.now(timezone.utc).isoformat(),
              'batch_id': completion['batch_id'], 'manifest_sha256': completion['manifest_sha256'],
              'months': 21, 'factsheets': 21, 'presentations': 21, 'downloads_verified': 42,
              'browser': 'Chrome', 'method': 'documented_browser_link_downloadMedia_in_owner_session',
              'browser_observation_sha256': sha(observation_path),
              'registered_qualification_sha256': sha(qualified_path),
              'previous_completion_sha256': sha(completion_path), 'checker_sha256': sha(Path(__file__)),
              'all_file_bytes_and_sizes_equal': True, 'all_route_ids_equal': True,
              'all_filenames_equal': all(not i['chrome_duplicate_suffix'] for i in items),
              'all_browser_names_match_registered_or_explicit_chrome_duplicate_suffix': True,
              'chrome_duplicate_suffix_count': sum(i['chrome_duplicate_suffix'] for i in items),
              'website_authenticated_downloads_verified': True, 'items': items,
              'response_headers_independently_captured': False, 'authorization_negative_tests_run': False,
              'native_microsoft_powerpoint_tested': False, 'approval_button_enabled': False,
              'reports_approved': False, 'reports_released': False, 'onboarding_activated': False,
              'source_data_written': False, 'cookies_or_tokens_retained': False,
              'next_gate': 'Native Microsoft PowerPoint interaction check, then separate owner report approval'}
    if args.protect:
        from run_ms_installed_private_batch import protected
        from run_specialist_gateway_read_verification import retain
        protected(completion)
        protected(qualified)
        record['protected_receipt'] = retain(record, 'ms-website-download-acceptance-v1')
    with target.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(record, indent=2) + '\n')
    print(json.dumps({k: record[k] for k in ('status', 'downloads_verified', 'reports_approved', 'onboarding_activated')}))


if __name__ == '__main__':
    main()
