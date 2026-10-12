"""Protect the sanitized live Exchange preflight and exact reviewed source.

Records observation, not permission approval or a claim of mail delivery.
"""
import hashlib
import json
from datetime import datetime

from deploy_specialist_report_worker import ROOT
from run_specialist_gateway_read_verification import retain

FOLDER = ROOT / '.work/specialist-mail-20261003'


def main():
    record = json.loads((FOLDER/'exchange-preflight.json').read_text(encoding='utf-8'))
    allowed = {'status','phase','error_type','mailbox_permissions_changed','messages_sent',
               'source_rows_changed','schedule_enabled','observed_at','tenant_id','sender',
               'mailbox_directory_id','matched_mailboxes','required_administration_commands_available'}
    if set(record)-allowed or any(record.get(k) != 0 or type(record[k]) is not int for k in
                                ('mailbox_permissions_changed','messages_sent','source_rows_changed')):
        raise ValueError('Exact sanitized read-only preflight required')
    if record.get('schedule_enabled') is not False or datetime.fromisoformat(record['observed_at']).utcoffset() is None:
        raise ValueError('Aware observation time and disabled schedule required')
    # This checkpoint actually stopped at administration capability discovery.
    if (record.get('status') != 'needs_attention' or record.get('phase') != 'administration_capability'
            or record.get('error_type') != 'RuntimeException'):
        raise ValueError('Expected actual administration-capability stop required')
    artifact = FOLDER/'ExchangeOnlineManagement.3.10.1.nupkg'
    package_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if package_sha != '545fb0fdf65b96abed37f4f5b5d7db661276ddc7de48a742bb6e7199ab9414a3':
        raise ValueError('Reviewed Microsoft module package differs')
    value = dict(observation=record, module_version='3.10.1', package_sha256=package_sha,
                 source_sha256=hashlib.sha256((ROOT/'scripts/connect_specialist_mail_admin.ps1').read_bytes()).hexdigest(),
                 sign_in_and_tenant_checks_preceded_capability_check=True,
                 exchange_grant_accepted=False, delivery_accepted=False)
    protected = retain(value, 'mail-exchange-preflight')
    (FOLDER/'exchange-preflight-protected.json').write_text(json.dumps(dict(observation=value, protected=protected), indent=2))
    print(json.dumps(dict(status='retained_needs_attention', protected=protected, messages_sent=0)))


if __name__ == '__main__':
    main()
