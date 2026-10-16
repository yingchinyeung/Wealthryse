"""Dispatch the one-time owner-role grant job and read its result from the job's console logs.

Storage is reachable only from the private network, so nothing here reads or
writes blobs. Only Azure management calls and a Log Analytics query are used,
and the job's persisted configuration is never changed.
"""
import argparse
import base64
import gzip
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from azure.identity import AzureCliCredential

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / '.work/owner-access-chloe-20261016-v2'
SUB = '3956e821-cee4-403f-8708-e84e2ed490eb'
BASE = f'/subscriptions/{SUB}/resourceGroups/rg-ibkr-database-v11/providers/Microsoft.App/'
JOB = 'ibkrv2stg-onboard-render'
WEB = 'ibkrv2stg-web'
API = '?api-version=2024-03-01'
WORKSPACE = '5264a124-10cd-45f6-93a5-6d65c37e9bfb'
TERMINAL = {'Succeeded', 'Failed', 'Stopped', 'Canceled', 'Cancelled'}
CREDENTIAL = AzureCliCredential()


def arm(method, path, data=None):
    token = CREDENTIAL.get_token('https://management.azure.com/.default').token
    response = requests.request(method, 'https://management.azure.com' + path + API,
                                headers={'Authorization': 'Bearer ' + token}, json=data, timeout=60)
    if not response.ok:
        # Never print response bodies; they can echo configuration.
        raise RuntimeError(f'Azure {method} failed HTTP {response.status_code}')
    return response.json() if response.content else {}


def console_lines(execution):
    token = CREDENTIAL.get_token('https://api.loganalytics.io/.default').token
    query = ('union isfuzzy=true ContainerAppConsoleLogs_CL, ContainerAppConsoleLogs '
             '| extend group=coalesce(column_ifexists("ContainerGroupName_s",""), column_ifexists("ContainerGroupName","")), '
             'line=coalesce(column_ifexists("Log_s",""), column_ifexists("Log","")) '
             f'| where group startswith "{execution}" and line has "OWNER_GRANT" '
             '| project TimeGenerated, line | order by TimeGenerated asc')
    response = requests.post(f'https://api.loganalytics.io/v1/workspaces/{WORKSPACE}/query',
                             headers={'Authorization': 'Bearer ' + token}, json={'query': query, 'timespan': 'PT6H'},
                             timeout=60)
    if not response.ok:
        raise RuntimeError(f'Log Analytics query failed HTTP {response.status_code}')
    rows = response.json()['tables'][0]['rows']
    return [row[1] for row in rows]


def require(ok, message):
    if not ok:
        raise ValueError(message)


def template(job, image, mode):
    source = ("import sys\nsys.path[:0]=['/app/src','/app/scripts']\nCONFIG=" + repr({'mode': mode}) + '\n'
              + (ROOT / 'scripts/run_owner_access_grant.py').read_text(encoding='utf-8'))
    encoded = base64.b64encode(gzip.compress(source.encode(), mtime=0)).decode()
    value = {k: v for k, v in job['properties']['template'].items() if v is not None}
    require(len(value['containers']) == 1, 'Single installed worker required')
    container = value['containers'][0]
    for env in container.get('env', []):
        if env.get('secretRef') and env.get('value') == '':
            env.pop('value')
    require(any(e['name'] == 'DATABASE_URL' and e.get('secretRef') for e in container.get('env', [])),
            'Job has no database credential reference')
    container['env'] = [e for e in container.get('env', []) if e['name'] != 'AUTHORIZATION_MODE']
    container['env'].append({'name': 'AUTHORIZATION_MODE', 'value': 'database'})
    container.update(image=image, command=['python'], args=[
        '-c', "import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('" + encoded + "')),'owner-access-grant','exec'))"])
    return value


def log(message):
    FOLDER.mkdir(parents=True, exist_ok=True)
    line = datetime.now(timezone.utc).isoformat() + ' ' + message
    with (FOLDER / 'commands.log').open('a', encoding='utf-8') as stream:
        stream.write(line + '\n')
    print(line, flush=True)


def run(mode):
    now = datetime.now(timezone.utc)
    require(not 8 * 60 <= now.hour * 60 + now.minute < 9 * 60 + 30, 'Withheld around the 08:00-09:30 UTC daily cycle')
    marker = FOLDER / (mode + '-dispatch.json')
    if marker.exists():
        execution = json.loads(marker.read_text(encoding='utf-8'))['execution']
        log('Resuming inspection of ' + execution)
    else:
        job = arm('GET', BASE + 'jobs/' + JOB)
        website = arm('GET', BASE + 'containerApps/' + WEB)
        container = website['properties']['template']['containers'][0]
        mode_env = [e for e in container['env'] if e['name'] == 'AUTHORIZATION_MODE']
        require(len(mode_env) == 1 and mode_env[0].get('value') == 'database', 'Website is not in database-only authorization')
        runs = arm('GET', BASE + 'jobs/' + JOB + '/executions').get('value', [])
        require(all(r['properties']['status'] in TERMINAL for r in runs), 'Another execution of the job is running; try later')
        body = template(job, container['image'], mode)
        ack = arm('POST', BASE + 'jobs/' + JOB + '/start', data=body)
        execution = ack.get('name')
        require(bool(execution), 'Unknown dispatch outcome; do not repeat')
        FOLDER.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({'execution': execution, 'image': container['image']}), encoding='utf-8')
        log(f'{mode} started {execution} on {container["image"]}')
    require(re.fullmatch(re.escape(JOB) + '-[a-z0-9]{7}', execution), 'Unexpected execution name')
    for _ in range(60):
        status = arm('GET', BASE + 'jobs/' + JOB + '/executions/' + execution)['properties']['status']
        if status in TERMINAL:
            break
        time.sleep(20)
    else:
        raise ValueError('Execution still running after 20 minutes; run the same command again to keep inspecting')
    log(f'{execution} status={status}')
    lines = []
    for _ in range(30):
        # Console logs reach Log Analytics a few minutes after the execution ends.
        lines = console_lines(execution)
        final_kind = 'VERIFY' if mode == 'grant' else 'PROBE'
        if any(('OWNER_GRANT ' + final_kind + ' ') in l or 'probe_blocked' in l or 'OWNER_GRANT FAIL ' in l for l in lines):
            break
        time.sleep(20)
    for line in lines:
        log('job log: ' + line)
    (FOLDER / (mode + '-result.json')).write_text(json.dumps({'execution': execution, 'status': status, 'lines': lines}),
                                                  encoding='utf-8')
    require(status == 'Succeeded', 'Job execution ' + status + '; nothing beyond the logged steps happened')
    require(not any('probe_blocked' in l for l in lines), 'Probe blocked the grant; nothing changed (see the log lines above)')
    require(not any('OWNER_GRANT FAIL ' in l for l in lines), 'Job stopped with an error; see the log lines above')
    done = 'VERIFY' if mode == 'grant' else 'PROBE'
    final = [l for l in lines if ('OWNER_GRANT ' + done + ' ') in l]
    require(bool(final), 'Result line not in the logs yet; run the same command again to re-read them')
    result = json.loads(final[-1].split('OWNER_GRANT ' + done + ' ', 1)[1])
    require(result['status'] == done.lower() + '_passed', done.lower() + ' did not pass; see the log lines above')
    log('OWNER ACCESS GRANT COMPLETE' if mode == 'grant' else 'OWNER ACCESS PROBE PASSED (nothing changed)')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('probe', 'grant'), nargs='?', default='grant')
    run(parser.parse_args().mode)


if __name__ == '__main__':
    main()
