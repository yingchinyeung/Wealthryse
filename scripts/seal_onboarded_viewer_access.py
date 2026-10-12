"""Retain final narrow viewer-grant evidence; performs no new permission changes."""
import hashlib
import json
from datetime import datetime, timezone

import verify_onboarded_viewer_access_v2 as verification
from run_onboarded_viewer_access import SUBJECTS, canonical

p = verification.p
FOLDER = verification.original.FOLDER


def main():
    if (FOLDER / 'completion.json').exists():
        raise ValueError('Preserve original completion evidence')
    evidence = {}
    for phase, folder in (('probe', FOLDER / 'probe'), ('apply', FOLDER / 'apply'), ('verify', verification.STAGE)):
        qualified = p.release.read(folder, 'qualified')
        p.verify_protected(qualified)
        if qualified['proof_sha256'] != p.sha(folder / 'proof.json'):
            raise ValueError('Protected phase proof changed')
        evidence[phase] = (qualified, p.release.read(folder, 'proof'))
    baseline = evidence['probe'][1]
    applied = evidence['apply'][1]
    checked = evidence['verify'][1]
    if (evidence['verify'][0]['status'] != 'four_viewers_all_four_accounts_verified' or checked['missing'] or
            checked['viewer_count'] != 4 or len(checked['runtime_checks']) != 16 or
            evidence['apply'][0]['inserted_read_grants'] != 4 or checked['database_writes'] is not False):
        raise ValueError('Verified exact four-viewer/four-account scope required')
    if canonical(applied['state']) != canonical(checked['state']):
        raise ValueError('Authorization state changed after the scoped grant transaction')
    for key in baseline['state']:
        if key != 'access' and canonical(baseline['state'][key]) != canonical(checked['state'][key]):
            raise ValueError('Identity, role, lifecycle or feature state changed')
    old = baseline['state']['access']
    new = checked['state']['access']
    added = [row for row in new if row not in old]
    if (len(new) != len(old) + 4 or not all(row in new for row in old) or len(added) != 4 or
            any(row['subject_id'] != 'alpha-world-ms' or row['access_level'] != 'read' for row in added)):
        raise ValueError('Existing grants changed or scope exceeded four MS read grants')
    record = {'status': 'all_registered_viewers_all_four_onboarded_accounts_verified',
              'verified_at_utc': datetime.now(timezone.utc).isoformat(), 'viewers': 4, 'accounts': list(SUBJECTS),
              'new_read_grants': 4, 'new_grant_subject': 'alpha-world-ms', 'viewer_account_checks': 16,
              'missing_grants': 0, 'existing_entitlements_preserved': True, 'no_higher_permissions_added': True,
              'identities_roles_activation_reports_sources_unchanged': True,
              'owner_pages_denied': True, 'anonymous_denied': True, 'persistent_job_settings_unchanged': True,
              'real_viewer_browser_sign_in_tested': False, 'identity_claims_simulated_only_inside_private_job': True,
              'failed_v1_read_only_check_preserved': True,
              'phase_execution_ids': {phase: value[0]['execution'] for phase, value in evidence.items()},
              'phase_proof_sha256': {phase: value[0]['proof_sha256'] for phase, value in evidence.items()},
              'viewer_identity_sha256': sorted(hashlib.sha256(row['entra_object_id'].encode()).hexdigest() for row in added)}
    p.release.build.save(FOLDER, 'completion',
                         {**record, 'protected_receipt': p.retain(record, 'four-account-viewer-completion-v1')})
    print(json.dumps({'status': record['status'], 'viewers': 4, 'accounts': 4, 'new_read_grants': 4,
                      'viewer_account_checks': 16, 'higher_permissions_added': False}), flush=True)


if __name__ == '__main__':
    main()
