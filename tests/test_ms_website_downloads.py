import copy
import hashlib

import pytest

from verify_ms_website_downloads import verify


@pytest.fixture
def accepted(tmp_path):
    observation = {'origin': 'https://ibkrv2stg-web.purplerock-8cff24a2.eastasia.azurecontainerapps.io',
                   'owner_visible': True, 'batch_status': 'awaiting_owner_approval',
                   'approval_button_enabled': False, 'manifest_prefix': 'a' * 16, 'downloads': []}
    proof = {'status': 'private_batch_verified', 'validation': {'ready_for_owner_approval': True},
             'batch': {'manifest_sha256': 'a' * 64}, 'batch_id': 'fixture-private-batch', 'items': []}
    for month in range(21):
        period = f'{2025 + month // 12}-{month % 12 + 1:02d}-28'
        for kind, caption, ext in [('factsheet', 'Factsheet', 'pdf'), ('presentation', 'Presentation', 'pptx')]:
            run = 100 + len(proof['items'])
            payload = f'unit-test-byte-identity-{run}'.encode()
            digest = hashlib.sha256(payload).hexdigest()
            name = f'AlphaWorld_MS_{caption}_{period}_{digest}.{ext}'
            (tmp_path / name).write_bytes(payload)
            observation['downloads'].append([run, name])
            proof['items'].append({'generation_run_id': run, 'batch_id': proof['batch_id'],
                                   'status': 'validated', 'period_end': period, 'report_type': kind,
                                   'blob_name': 'review/alpha-world-ms/' + period + '/' + name,
                                   'artifact_sha256': digest, 'byte_count': len(payload)})
    return observation, proof, tmp_path


def test_exact_42_files_pass(accepted):
    rows = verify(*accepted)
    assert len(rows) == 42 and all(r['matches_accepted'] for r in rows)


def test_chrome_duplicate_suffix_requires_explicit_observation_and_exact_bytes(accepted):
    observation, proof, root = accepted
    original = observation['downloads'][0][1]
    expected = root / original
    duplicate = expected.with_name(expected.stem + ' (1)' + expected.suffix)
    duplicate.write_bytes(expected.read_bytes())
    observation['downloads'][0][1] = duplicate.name
    with pytest.raises(ValueError):
        verify(observation, proof, root)
    observation['chrome_duplicate_suffixes_expected'] = True
    assert verify(observation, proof, root)[0]['chrome_duplicate_suffix'] is True
    duplicate.write_bytes(b'changed')
    with pytest.raises(ValueError):
        verify(observation, proof, root)


@pytest.mark.parametrize('suffix', [' (0)', ' (01)', ' (evil)', ' copy', '/../other'])
def test_unrecognized_browser_suffix_is_rejected(accepted, suffix):
    observation, proof, root = accepted
    original = root / observation['downloads'][0][1]
    observation['chrome_duplicate_suffixes_expected'] = True
    observation['downloads'][0][1] = original.stem + suffix + original.suffix
    with pytest.raises(ValueError):
        verify(observation, proof, root)


@pytest.mark.parametrize('field,value', [('owner_visible', False), ('batch_status', 'approved'),
                                       ('approval_button_enabled', True), ('manifest_prefix', 'b' * 16),
                                       ('origin', 'https://unrelated.invalid')])
def test_reject_wrong_owner_or_review_state(accepted, field, value):
    observation, proof, root = accepted
    observation[field] = value
    with pytest.raises(ValueError):
        verify(observation, proof, root)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'wrong_route', 'path', 'hash', 'size', 'batch', 'status', 'pair'])
def test_reject_inventory_and_byte_changes(accepted, change):
    observation, proof, root = copy.deepcopy(accepted)
    if change == 'missing': observation['downloads'].pop()
    if change == 'duplicate': observation['downloads'][1] = observation['downloads'][0]
    if change == 'wrong_route': observation['downloads'][0][0] = 999
    if change == 'path': observation['downloads'][0][1] = '../unrelated.pdf'
    if change == 'hash': proof['items'][0]['artifact_sha256'] = 'b' * 64
    if change == 'size': proof['items'][0]['byte_count'] += 1
    if change == 'batch': proof['items'][0]['batch_id'] = 'other'
    if change == 'status': proof['items'][0]['status'] = 'failed'
    if change == 'pair': proof['items'][0]['report_type'] = 'presentation'
    with pytest.raises(ValueError):
        verify(observation, proof, root)
