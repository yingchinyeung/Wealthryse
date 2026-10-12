"""One synthetic alert to the existing owner route; never redispatch an intent."""
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
import requests
from azure.identity import AzureCliCredential
from deploy_viewer_private_admin_20261008 import request, rid
from control_upgrade_legacy_recovery_probe_20261009 import protected
from run_specialist_gateway_read_verification import retain

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261010-stage12/notification-test-v1'
GROUP = rid('Microsoft.Insights/actionGroups', 'ibkr-v11-daily-alerts')
RECIPIENT = 'anthony.ying@wealthryse.ai'


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def status_path(location):
    p = urlsplit(location)
    prefix = GROUP + '/notificationStatus/'
    if (p.scheme != 'https' or p.netloc != 'management.azure.com' or p.fragment
            or not p.path.lower().startswith(prefix.lower())
            or not re.fullmatch('[A-Za-z0-9-]{1,128}', p.path[len(prefix):])
            or p.query not in ('api-version=2021-09-01', 'api-version=2022-06-01')):
        raise ValueError('Unexpected status route; inspect accepted intent without resending')
    return p.path + '?' + p.query


def save(name, value):
    receipt = retain(value, 'upgrade-alert-test-' + name)
    with (OUT / (name + '.json')).open('x', encoding='utf-8') as f:
        json.dump({**value, 'protected_receipt': receipt}, f, indent=2)


def provider_completed(value, earliest):
    actions = value.get('actionDetails', [])
    if len(actions) != 1:
        return False
    action = actions[0]
    # The documented properties are strings, not enums. Retain the exact
    # observed wire values; accept only these two explicit successful pairs.
    if (value.get('state'), action.get('Status'), action.get('SubState')) not in {
        ('Completed', 'Completed', 'Default'), ('Complete', 'Succeeded', None)
    } or action.get('MechanismType') != 'Email' or action.get('Name') != 'operations-email' or action.get('Detail'):
        return False
    try:
        stamp = datetime.fromisoformat(action['SendTime'])
        return stamp.utcoffset() is not None and earliest <= stamp <= datetime.now(timezone.utc)
    except (KeyError, TypeError, ValueError):
        return False


def main(stage):
    group = request('GET', GROUP + '?api-version=2023-01-01')['properties']
    receivers = group.get('emailReceivers', [])
    if (group.get('enabled') is not True or len(receivers) != 1
            or receivers[0].get('emailAddress') != RECIPIENT
            or receivers[0].get('name') != 'operations-email'
            or receivers[0].get('status') != 'Enabled'
            or any(v for k, v in group.items() if k.endswith('Receivers') and k != 'emailReceivers')):
        raise ValueError('Exact existing owner-only destination required')
    if stage == 'start':
        if OUT.exists():
            raise ValueError('Reconcile existing dispatch; never automatically resend')
        OUT.mkdir()
        body = {'alertType': 'logalertv2', 'emailReceivers': [{k: receivers[0][k] for k in ('name', 'emailAddress', 'useCommonAlertSchema')}]}
        save('intent', {'checked_at': datetime.now(timezone.utc).isoformat(), 'group_sha256': sha(group),
                        'recipient': RECIPIENT, 'body': body, 'source_data_included': False,
                        'scope': 'One synthetic notification, no rule or recipient configuration changes'})
        token = AzureCliCredential().get_token('https://management.azure.com/.default').token
        try:
            response = requests.post('https://management.azure.com' + GROUP + '/createNotifications?api-version=2021-09-01',
                                     headers={'Authorization': 'Bearer ' + token}, json=body,
                                     timeout=60, allow_redirects=False)
        except requests.RequestException as error:
            raise RuntimeError('Unknown notification outcome; do not resend: ' + type(error).__name__) from None
        finally:
            del token
        # Store response routing before validation to preserve ambiguous outcomes.
        location = response.headers.get('Location', '')
        parsed = urlsplit(location)
        acknowledgement = {'http_status': response.status_code, 'group_sha256': sha(group),
                           'location_shape': {'scheme': parsed.scheme, 'host': parsed.hostname, 'path': parsed.path,
                                              'query': parsed.query if parsed.query in ('api-version=2021-09-01', 'api-version=2022-06-01') else None},
                           'location_sha256': hashlib.sha256(location.encode()).hexdigest()}
        save('acknowledgement', acknowledgement)
        if response.status_code == 202:
            save('dispatch', {'status_path': status_path(location), 'group_sha256': sha(group)})
        else:
            raise ValueError('Unexpected notification response; reconcile without resending')
        print('One synthetic owner-only notification accepted; receipt not yet confirmed')
        return
    if stage == 'reconcile':
        if (OUT / 'dispatch.json').exists():
            raise ValueError('Known dispatch exists; read it')
        ack = protected(OUT / 'acknowledgement.json')
        intent = protected(OUT / 'intent.json')
        shape = ack['location_shape']
        if (ack['http_status'] != 202 or ack['group_sha256'] != sha(group)
                or intent['group_sha256'] != sha(group)
                or shape['scheme'] != 'https' or shape['host'] != 'management.azure.com'):
            raise ValueError('Known accepted dispatch on exact unchanged group required')
        # Official GET status contract supports 2021-09-01 independently of
        # the query Azure happened to return. Use only the retained exact ID.
        route = status_path('https://management.azure.com' + shape['path'] + '?api-version=2021-09-01')
        save('dispatch', {'status_path': route, 'group_sha256': sha(group),
                          'reconciled_from': ack['protected_receipt'], 'resent': False})
        print('Accepted notification route reconciled; no second notification sent')
        return
    if stage != 'read':
        raise ValueError('Unknown stage')
    intent = protected(OUT / 'intent.json')
    dispatch = protected(OUT / 'dispatch.json')
    if sha(group) != intent['group_sha256'] or sha(group) != dispatch['group_sha256']:
        raise ValueError('Action group changed since dispatch')
    route = status_path('https://management.azure.com' + dispatch['status_path'])
    value = request('GET', route)
    actions = [{k: r.get(k) for k in ('MechanismType', 'Name', 'Status', 'SubState', 'SendTime')}
               for r in value.get('actionDetails', [])]
    completed = provider_completed(value, datetime.fromisoformat(intent['checked_at']))
    result = {'provider_completed': completed, 'state': value.get('state'), 'actions': actions,
              'recipient_delivery_confirmed': False, 'group_unchanged': True,
              'synthetic_test_only': True, 'portfolio_data_sent': False, 'full_upgrade_complete': False}
    if completed and not (OUT / 'result.json').exists():
        save('result', result)
    print(json.dumps(result))


if __name__ == '__main__':
    main(sys.argv[1])
