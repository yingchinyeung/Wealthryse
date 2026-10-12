"""Create only a dedicated, unprivileged daily-mail identity and retain evidence.

No Exchange grants, mailbox access, app consent, worker attachment or email.
An uncertain creation outcome must be inspected, not automatically retried.
"""
import argparse
import json
from datetime import datetime, timezone

from audit_azure_runtime import az, RG
from deploy_metadata_monitor import SUB
from deploy_specialist_report_worker import ROOT
from run_specialist_gateway_read_verification import retain

NAME = 'ibkrv2stg-specialist-mail-identity'
SENDER = 'anthony.ying@wealthryse.ai'
FOLDER = ROOT / '.work/specialist-mail-20261003'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['create', 'verify'])
    args = parser.parse_args()
    if az('account', 'show', '--query', 'id') != SUB:
        raise ValueError('Unexpected subscription')
    target = f'/subscriptions/{SUB}/resourceGroups/{RG}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{NAME}'
    matches = az('identity', 'list', '--resource-group', RG, '--query', f"[?name=='{NAME}']")
    intent_path = FOLDER / 'identity-intent.json'
    if args.stage == 'create':
        if matches or intent_path.exists():
            raise ValueError('Existing identity or prior intent: inspect before any further mutation')
        intent = dict(action='create_unprivileged_mail_identity', resource_id=target,
                      sender=SENDER, recipient=SENDER, occurred_at=datetime.now(timezone.utc).isoformat(),
                      exchange_permissions_requested=['Application Mail.Send scoped to sender only'],
                      exchange_permissions_granted=False, source_access=False, messages_sent=0)
        protected = retain(intent, 'mail-identity-intent')
        FOLDER.mkdir(parents=True, exist_ok=True)
        intent_path.write_text(json.dumps(dict(intent=intent, protected=protected), indent=2))
        # Fixed target only. No secrets or role assignments are added here.
        az('identity', 'create', '--name', NAME, '--resource-group', RG, '--location', 'eastasia',
           '--tags', 'purpose=specialist-daily-mail', 'scope=single-owner-mailbox')
        matches = az('identity', 'list', '--resource-group', RG, '--query', f"[?name=='{NAME}']")
    if not intent_path.exists() or len(matches) != 1:
        raise ValueError('Recorded intent and exact identity required')
    identity = matches[0]
    if (identity['id'].lower() != target.lower() or identity['location'].lower() != 'eastasia'
            or identity.get('tags', {}).get('purpose') != 'specialist-daily-mail'):
        raise ValueError('Unexpected identity configuration')
    principal, client = identity['principalId'], identity['clientId']
    assignments = az('rest', '--method', 'GET', '--url',
                     f'https://graph.microsoft.com/v1.0/servicePrincipals/{principal}/appRoleAssignments')
    roles = az('role', 'assignment', 'list', '--assignee-object-id', principal,
               '--include-inherited', '--all')
    if assignments.get('@odata.nextLink') or assignments.get('value') != [] or roles != []:
        raise ValueError('New mail identity must have no Graph application or Azure role grants')
    receipt = dict(status='passed', resource_id=target, client_id=client, principal_id=principal,
                   sender=SENDER, recipient=SENDER, graph_app_role_grants=0, azure_role_grants=0,
                   worker_attached=False, exchange_permission_verified=False,
                   source_rows_changed=0, messages_sent=0, schedule_enabled=False)
    protected = retain(receipt, 'mail-identity-completed')
    FOLDER.mkdir(parents=True, exist_ok=True)
    (FOLDER / 'identity-acceptance.json').write_text(json.dumps(dict(receipt=receipt, protected=protected), indent=2))
    print(json.dumps(dict(receipt=receipt, protected=protected)))


if __name__ == '__main__':
    main()
