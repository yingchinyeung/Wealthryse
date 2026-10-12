"""Release the website from GitHub Actions (see .github/workflows/deploy-website.yml).

Steps, each a subcommand so the workflow can show and stop at each one:
  prepare  read the live image, work out what changed, refuse unsupported changes
  build    build the new image on the private build VM through Azure Run Command
  deploy   point the website at the new image
  verify   wait for the new revision, check health and that sign-in is still required
  rollback point the website back at the image recorded by prepare
State passes between steps in release-state.json.
"""
import base64, gzip, hashlib, json, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path

RG = 'rg-ibkr-database-v11'
APP = 'ibkrv2stg-web'
VM = 'ibkrv2stg-private-admin'
REG = 'ibkrv2stgauhecwc4itaoo.azurecr.io'
# Source the website image was built from before releases came from GitHub;
# the build refuses if the live files differ from it.
BASELINE_COMMIT = '59dbd295c32b8503aa9f66be5b9cf0e5b70562b2'
PACKAGE = 'src/ibkr_etl'
# Paths that are not part of the website image.
IGNORED_PREFIXES = ('docs/', 'tests/', '.github/', 'infra/', 'deploy/', '.azuredevops/',
                    # CI-only helper; the website never runs it.
                    'scripts/ci_run_tests.py')
STATE = Path('release-state.json')
ROOT = Path(__file__).resolve().parents[1]


def az(*args, parse=True):
    out = subprocess.run(['az', *args, '-o', 'json'] if parse else ['az', *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if parse and out.strip() else out


def git(*args, binary=False):
    return subprocess.run(['git', *args], check=True, capture_output=True, cwd=ROOT, text=not binary).stdout


def package_hashes(commit):
    files = [f for f in git('ls-tree', '-r', '--name-only', commit, PACKAGE).split() if f.endswith('.py')]
    return {f[len(PACKAGE) + 1:]: hashlib.sha256(git('show', commit + ':' + f, binary=True)).hexdigest() for f in files}


def prepare():
    head = git('rev-parse', 'HEAD').strip()
    app = az('containerapp', 'show', '-n', APP, '-g', RG)
    containers = app['properties']['template']['containers']
    if len(containers) != 1:
        raise SystemExit('Expected one website container')
    live = containers[0]['image']
    tag = live.split(':')[-1] if '@' not in live else ''
    deployed = tag[len('web-'):] if tag.startswith('web-') and len(tag) == 44 else BASELINE_COMMIT
    if subprocess.run(['git', 'merge-base', '--is-ancestor', deployed, head], cwd=ROOT).returncode:
        raise SystemExit(f'Live website commit {deployed} is not in this branch history')
    changed = [p for p in git('diff', '--name-only', deployed, head).split() if p]
    image_changes = [p for p in changed if not p.startswith(IGNORED_PREFIXES) and not p.endswith('.md')]
    unsupported = [p for p in image_changes if not p.startswith(PACKAGE + '/')]
    if unsupported:
        raise SystemExit('This release path only ships src/ibkr_etl. Other image files changed:\n  ' + '\n  '.join(unsupported))
    state = {
        'head': head, 'deployed_commit': deployed, 'live_image': live,
        'fqdn': app['properties']['configuration']['ingress']['fqdn'],
        'target_image': f'{REG}/ibkr-platform:web-{head}',
        'changed_files': [p for p in image_changes],
    }
    STATE.write_text(json.dumps(state, indent=2))
    print(json.dumps(state, indent=2))
    if not image_changes:
        print('No website source changes since the live image; nothing to release.')


def build():
    state = json.loads(STATE.read_text())
    if not state['changed_files']:
        return
    patch = git('diff', '--binary', state['deployed_commit'], state['head'], '--', PACKAGE, binary=True)
    config = {
        'base_image': state['live_image'], 'target_image': state['target_image'], 'source_commit': state['head'],
        'live_hashes': package_hashes(state['deployed_commit']), 'new_hashes': package_hashes(state['head']),
        'patch': base64.b64encode(gzip.compress(patch, 9)).decode(),
    }
    code = (ROOT / 'deploy' / 'website_overlay_build_remote.py').read_text()
    marker = 'CONFIG = None  # replaced by the workflow'
    if code.count(marker) != 1:
        raise SystemExit('Build script placeholder missing')
    code = code.replace(marker, 'CONFIG = json.loads(' + repr(json.dumps(config)) + ')')
    command = "cat > /tmp/wealthryse-website-build.py <<'WEALTHRYSE_EOF'\n" + code + "\nWEALTHRYSE_EOF\n" \
              "unshare --mount --propagation private python3 /tmp/wealthryse-website-build.py; status=$?; rm -f /tmp/wealthryse-website-build.py; exit $status"
    vm = az('vm', 'show', '-g', RG, '-n', VM, '-d')
    started = vm.get('powerState') != 'VM running'
    if started:
        print('Starting the build VM')
        az('vm', 'start', '-g', RG, '-n', VM, parse=False)
    state['vm_started_by_release'] = started
    STATE.write_text(json.dumps(state, indent=2))
    sub = az('account', 'show')['id']
    name = 'website-build-' + state['head'][:12]
    url = f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{RG}/providers/Microsoft.Compute/virtualMachines/{VM}/runCommands/{name}?api-version=2024-07-01'
    body = Path('run-command.json')
    body.write_text(json.dumps({'location': vm['location'], 'properties': {
        'source': {'script': command}, 'asyncExecution': True, 'timeoutInSeconds': 3000, 'treatFailureAsDeploymentFailure': False}}))
    try:
        az('rest', '--method', 'put', '--url', url, '--body', '@' + str(body), parse=False)
        deadline = time.time() + 3300
        while True:
            run = az('rest', '--method', 'get', '--url', url + '&$expand=instanceView')
            view = run['properties'].get('instanceView') or {}
            if view.get('executionState') in ('Succeeded', 'Failed', 'TimedOut', 'Canceled'):
                break
            if time.time() > deadline:
                raise SystemExit('Build did not finish in time')
            time.sleep(20)
        lines = [l[len('WEBSITE_BUILD_RESULT '):] for l in (view.get('output') or '').splitlines() if l.startswith('WEBSITE_BUILD_RESULT ')]
        result = json.loads(lines[-1]) if lines else {'passed': False, 'error': (view.get('error') or '')[-1500:]}
        print(json.dumps(result, indent=2))
        if not result.get('passed') or view.get('exitCode') != 0:
            raise SystemExit('Build failed; the website was not changed')
        state['pushed_image'] = result['pushed_image']
        STATE.write_text(json.dumps(state, indent=2))
    finally:
        subprocess.run(['az', 'rest', '--method', 'delete', '--url', url], capture_output=True)
        if started:
            print('Stopping the build VM again')
            subprocess.run(['az', 'vm', 'deallocate', '-g', RG, '-n', VM, '--no-wait'], capture_output=True)


def deploy():
    state = json.loads(STATE.read_text())
    if not state['changed_files']:
        return
    now = az('containerapp', 'show', '-n', APP, '-g', RG)['properties']['template']['containers'][0]['image']
    if now != state['live_image']:
        raise SystemExit(f'Website image changed during the release ({now}); stopping')
    suffix = 'web-' + state['head'][:7] + '-' + str(int(time.time()))[-6:]
    az('containerapp', 'update', '-n', APP, '-g', RG, '--container-name', 'web', '--image', state['target_image'], '--revision-suffix', suffix, parse=False)
    state['revision'] = APP + '--' + suffix
    STATE.write_text(json.dumps(state, indent=2))
    print('Website switched to', state['target_image'])


def anonymous_status(fqdn, path):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open('https://' + fqdn + path, timeout=20) as r:
            return r.status, ''
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get('Location', '')


def verify():
    state = json.loads(STATE.read_text())
    if not state['changed_files']:
        return
    revision = state['revision']
    for _ in range(60):
        app = az('containerapp', 'show', '-n', APP, '-g', RG)['properties']
        if app.get('latestReadyRevisionName') == revision:
            break
        time.sleep(10)
    else:
        raise SystemExit('New revision did not become ready')
    rev = az('containerapp', 'revision', 'show', '-n', APP, '-g', RG, '--revision', revision)['properties']
    checks = []
    for path in ('/', '/api/portfolio/U16088571/holdings', '/api/holdings/combined', '/api/portfolio/U16088571/performance/periods'):
        code, location = anonymous_status(state['fqdn'], path)
        ok = code == 401 or (code == 302 and '/.auth/login/' in location)
        checks.append({'path': path, 'status': code, 'sign_in_required': ok})
    report = {'revision': revision, 'health': rev.get('healthState'), 'running': rev.get('runningState'), 'checks': checks}
    print(json.dumps(report, indent=2))
    if rev.get('healthState') != 'Healthy' or not all(c['sign_in_required'] for c in checks):
        raise SystemExit('Verification failed')


def rollback():
    state = json.loads(STATE.read_text())
    suffix = 'rollback-' + str(int(time.time()))[-6:]
    az('containerapp', 'update', '-n', APP, '-g', RG, '--container-name', 'web', '--image', state['live_image'], '--revision-suffix', suffix, parse=False)
    print('Website switched back to', state['live_image'])


if __name__ == '__main__':
    {'prepare': prepare, 'build': build, 'deploy': deploy, 'verify': verify, 'rollback': rollback}[sys.argv[1]]()
