"""Retain a truthful stage closeout, never declaring the complete upgrade passed."""
import json
from release_ms_agent_tenant_20261009 import BUNDLE,BASE,release
from run_specialist_gateway_read_verification import retain
def result(path):
    record=release.read(path)
    if record['status']!='Succeeded' or len(record['records'])!=1:raise ValueError('Successful exact execution required')
    return json.loads(record['records'][0]['Result'])
def main():
    candidate=result(BUNDLE/'candidate-probe-v1/result.json');live=result(BUNDLE/'live-postflight-v1/result.json');history=result(BUNDLE/'history-postflight-v1/result.json')
    if not candidate['passed'] or not live['passed'] or not history['passed'] or candidate['report_and_approval_state']!=live['report_and_approval_state'] or not history['report_and_approval_state_unchanged']:raise ValueError('Preservation acceptance differs')
    if history['migrated_rows_verified']!=445 or not history['full_row_hash_verified']:raise ValueError('History verification incomplete')
    record={'stage':'MS risk extension and verified assistant tenant','passed':True,'full_upgrade_complete':False,
        'website':release.read(BUNDLE/'web-deployment-v1/verified.json'),'worker':release.read(BASE/'worker-deployment-v1/verified.json'),
        'candidate':candidate,'installed':live,'historical':history,'viewer_acceptance':'Explicitly unverified at owner request; no viewer available',
        'remaining':['physical-account current-date crisis and dependency-ordered daily cycle','new-month and governed regeneration integration','private networking and cold pulls','East Asia recovery and legacy HA','additional image-family security and alerts','all-page responsive checks','authentic burn-in window'],
        'source_or_report_mutations':False}
    release.save(BUNDLE/'stage-closeout.json',{**record,'protected_receipt':retain(record,'ms-upgrade-stage-closeout')})
    print('MS stage sealed; full upgrade remains open')
if __name__=='__main__':main()
