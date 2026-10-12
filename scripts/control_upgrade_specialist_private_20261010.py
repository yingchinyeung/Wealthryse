"""Private endpoint checks for three existing specialist service identities."""
import json
import sys
import control_upgrade_private_identities_20261010 as probe

probe.OUT = probe.ROOT / '.work/platform-upgrade-20261010-stage13/private-specialists-v1'
probe.JOBS = {'specialist-audit': 'ibkrv2stg-agent-audit',
              'specialist-authority': 'ibkrv2stg-authority',
              'specialist-observation': 'ibkrv2stg-observe-reader',
              'owner-archive-reader': 'ibkrv2stg-ownar2-20261006',
              'owner-archive-writer': 'ibkrv2stg-ownaw2-20261006',
              'owner-catalog': 'ibkrv2stg-ownertrial-catalog',
              'owner-model': 'ibkrv2stg-ownrmeta-20261006',
              'owner-runtime': 'ibkrv2stg-ownerruntime-20261004',
              'owner-sql': 'ibkrv2stg-ownaud3-20261005',
              'log-canary': 'ibkrv2stg-ownertrial-log-canary'}

def main(stage, group):
    if group not in probe.JOBS: raise ValueError('Unknown existing identity group')
    job = probe.request('GET', probe.rid('Microsoft.App/jobs', probe.JOBS[group]) + probe.API)
    # The older API omits identity lifecycle restrictions. Never widen them
    # merely to make an in-container token-acquisition probe pass.
    modern = probe.request('GET', probe.rid('Microsoft.App/jobs', probe.JOBS[group]) + '?api-version=2026-07-01')
    settings = modern['properties']['configuration'].get('identitySettings') or []
    ids = {i.lower() for i in modern['identity'].get('userAssignedIdentities', {})}
    disabled = {s['identity'].lower() for s in settings if s.get('lifecycle') == 'None'}
    if ids and ids <= disabled:
        folder = probe.OUT / group
        if not folder.exists(): folder.mkdir(parents=True)
        if not (folder / 'runtime-identity-restriction.json').exists():
            value = {'job': probe.JOBS[group], 'identity_settings': settings,
                'job_invariant': probe.settings_invariant(job), 'runtime_token_access_intentionally_disabled': True,
                'runtime_private_secret_access_verified': False, 'restriction_changed': False,
                'platform_secret_resolution_and_private_registry_pull': 'still_required',
                'reference': 'https://learn.microsoft.com/en-us/azure/container-apps/managed-identity#control-managed-identity-availability'}
            if (folder / 'dispatch.json').exists():
                intent = probe.protected(folder / 'intent.json')
                if intent['invariant'] != probe.settings_invariant(job): raise ValueError('Saved job drift')
                execution = probe.protected(folder / 'dispatch.json')['execution']
                run = probe.request('GET', probe.rid('Microsoft.App/jobs', probe.JOBS[group]) + '/executions/' + execution + probe.API)
                if run['properties']['status'] != 'Failed': raise ValueError('Unexpected probe outcome')
                q = "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + execution + "-' | where Log_s startswith 'azure.identity._exceptions.CredentialUnavailableError:' | where Log_s contains 'No User Assigned or Delegated Managed Identity found' | summarize Matches=count()"
                rows = probe.az('monitor', 'log-analytics', 'query', '-w', '5264a124-10cd-45f6-93a5-6d65c37e9bfb', '--analytics-query', q)
                if len(rows) != 1 or int(rows[0]['Matches']) != 1: raise ValueError('Exact restricted-token diagnostic missing')
                value.update(prior_probe_execution=execution, prior_probe_failed_at_token_acquisition=True,
                             expected_denial_verified=True, private_service_request_not_completed=True)
            probe.save(folder, 'runtime-identity-restriction', value)
        print(json.dumps({'group': group, 'runtime_tokens_intentionally_disabled': True, 'restriction_changed': False}))
        return
    if not (job['properties']['configuration'].get('secrets') or []):
        folder = probe.OUT / group
        if not folder.exists(): folder.mkdir(parents=True)
        if not (folder / 'no-secret-references.json').exists():
            probe.save(folder, 'no-secret-references', {'job': probe.JOBS[group],
                'configured_secret_references': 0, 'secret_test_applicable': False,
                'job_invariant': probe.settings_invariant(job), 'execution_dispatched': False,
                'private_registry_and_other_endpoint_acceptance': 'still_required'})
        print(json.dumps({'group': group, 'secret_test_applicable': False, 'execution_dispatched': False}))
        return
    probe.main(stage, group)


if __name__ == '__main__': main(*sys.argv[1:])
