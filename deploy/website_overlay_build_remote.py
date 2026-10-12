"""Build a website image on the private build VM. Run only via Azure Run Command.

The deploy-website workflow fills in CONFIG and runs this as root inside a
private mount namespace (`unshare --mount`). It starts from the exact image
the website runs today, replaces only src/ibkr_etl with the reviewed commit
(after checking the live files are the commit the workflow expects), builds
with no network, and pushes one new tag over the registry's private endpoint.
No application starts and no database is touched.
"""
import base64, gzip, hashlib, json, os, shutil, socket, ssl, subprocess, tempfile, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

CONFIG = None  # replaced by the workflow
PREFIX = 'WEBSITE_BUILD_RESULT '
HOSTS = {'ibkrv2stgauhecwc4itaoo.azurecr.io': '10.50.10.5', 'ibkrv2stgauhecwc4itaoo.eastasia.data.azurecr.io': '10.50.10.4'}
REG = 'ibkrv2stgauhecwc4itaoo.azurecr.io'


def run(args, operation, *, timeout=900, input=None, env=None, cwd=None):
    r = subprocess.run(args, input=input, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
    if r.returncode:
        raise RuntimeError(operation + ' failed with exit ' + str(r.returncode) + ': ' + (r.stderr or r.stdout)[-1500:])
    return r.stdout


def request(url, data=None, headers=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    body = None if data is None else urllib.parse.urlencode(data).encode()
    with opener.open(urllib.request.Request(url, data=body, headers=headers or {}), timeout=30) as r:
        return json.load(r)


def tree_hashes(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob('*.py')) if '__pycache__' not in p.parts}


def main():
    config = CONFIG
    base, target = config['base_image'], config['target_image']
    if not base.startswith(REG + '/') or not target.startswith(REG + '/ibkr-platform:web-'):
        raise ValueError('Unexpected image names')
    os.umask(0o077)
    if os.geteuid() != 0 or os.readlink('/proc/self/ns/mnt') == os.readlink('/proc/1/ns/mnt'):
        raise ValueError('A separate root mount namespace is mandatory')
    for tool in ('dockerd', 'docker', 'mount', 'umount', 'patch'):
        if not shutil.which(tool):
            raise ValueError('Required build tool missing: ' + tool)
    if shutil.disk_usage('/tmp').free < 12 * 1024 ** 3:
        raise ValueError('Insufficient disposable build space')
    root = Path(tempfile.mkdtemp(prefix='wealthryse-website-build-')).resolve()
    original = Path('/etc/hosts').read_bytes()
    hosts = root / 'hosts'
    hosts.write_bytes(original + b'\n' + ''.join(ip + ' ' + host + '\n' for host, ip in HOSTS.items()).encode())
    env = {k: v for k, v in os.environ.items() if k.upper() not in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG')}
    env['DOCKER_CONFIG'] = str(root / 'credentials')
    Path(env['DOCKER_CONFIG']).mkdir(mode=0o700)
    process = log = None
    mounted = False
    result = {'base_image': base, 'target_image': target, 'source_commit': config['source_commit']}
    try:
        run(['mount', '--bind', str(hosts), '/etc/hosts'], 'namespace-only hosts bind', timeout=15)
        mounted = True
        for host, expected in HOSTS.items():
            with socket.create_connection((host, 443), timeout=15) as sock:
                with ssl.create_default_context().wrap_socket(sock, server_hostname=host) as secure:
                    if secure.getpeername()[0] != expected:
                        raise ValueError('Registry is not reached over its private address')
        token = request('http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fmanagement.azure.com%2F', headers={'Metadata': 'true'})['access_token']
        payload = token.split('.')[1]
        tenant = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))['tid']
        refresh = request('https://' + REG + '/oauth2/exchange', {'grant_type': 'access_token', 'service': REG, 'tenant': tenant, 'access_token': token})['refresh_token']
        del token
        location = root / 'daemon'
        location.mkdir()
        log = (location / 'daemon.log').open('w')
        sock = str(location / 'docker.sock')
        process = subprocess.Popen(['dockerd', '--host=unix://' + sock, '--data-root=' + str(location / 'data'), '--exec-root=' + str(location / 'exec'),
                                    '--pidfile=' + str(location / 'pid'), '--bridge=none', '--iptables=false', '--ip6tables=false'],
                                   stdout=log, stderr=log, env=env)
        client = ['docker', '--host', 'unix://' + sock]
        for _ in range(60):
            if process.poll() is not None:
                raise RuntimeError('Isolated Docker daemon exited')
            if subprocess.run(client + ['info'], capture_output=True, env=env, timeout=5).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Isolated Docker daemon readiness timeout')
        run(client + ['login', REG, '--username', '00000000-0000-0000-0000-000000000000', '--password-stdin'], 'registry sign-in', input=refresh, timeout=60, env=env)
        del refresh
        run(client + ['pull', base], 'live image pull', env=env)

        context = root / 'context'
        (context / 'src').mkdir(parents=True)
        container = run(client + ['create', base], 'live image unpack', env=env).strip()
        try:
            run(client + ['cp', container + ':/app/src/ibkr_etl', str(context / 'src')], 'live source copy', env=env)
        finally:
            run(client + ['rm', container], 'live image cleanup', env=env)
        package = context / 'src' / 'ibkr_etl'
        live = tree_hashes(package)
        if live != config['live_hashes']:
            expected = config['live_hashes']
            result['live_drift'] = {
                'only_live': sorted(set(live) - set(expected)),
                'only_expected': sorted(set(expected) - set(live)),
                'different': sorted(n for n in set(live) & set(expected) if live[n] != expected[n]),
            }
            raise ValueError('Live website source is not the expected commit; nothing built')
        patch_file = root / 'change.patch'
        patch_file.write_bytes(gzip.decompress(base64.b64decode(config['patch'])))
        run(['patch', '-p1', '--forward', '--no-backup-if-mismatch', '-i', str(patch_file)], 'source patch', cwd=str(context))
        if tree_hashes(package) != config['new_hashes']:
            raise ValueError('Patched source does not match the reviewed commit; nothing built')
        removed = sorted(set(config['live_hashes']) - set(config['new_hashes']))
        lines = ['FROM ' + base, 'USER root',
                 'LABEL ai.wealthryse.source-commit="' + config['source_commit'] + '"',
                 'COPY src/ibkr_etl/ /app/src/ibkr_etl/']
        if removed:
            lines.append('RUN rm -f ' + ' '.join('/app/src/ibkr_etl/' + name for name in removed))
        lines += ['RUN SITE=$(python -c "import ibkr_etl,os;print(os.path.dirname(ibkr_etl.__file__))") && '
                  'if [ "$SITE" != "/app/src/ibkr_etl" ]; then cp -r /app/src/ibkr_etl/. "$SITE"/' +
                  (' && cd "$SITE" && rm -f ' + ' '.join(removed) if removed else '') + '; fi && '
                  'find /app/src/ibkr_etl -name __pycache__ -prune -exec rm -rf {} + && chown -R 10001:10001 /app/src',
                  'USER 10001:10001',
                  'RUN python -c "import ibkr_etl.web" && python scripts/check_report_runtime.py']
        (context / 'Dockerfile').write_text('\n'.join(lines) + '\n')
        run(client + ['build', '--network=none', '--pull=false', '-t', target, str(context)], 'website image build', timeout=1800, env=env)
        run(client + ['push', target], 'website image push', env=env)
        digests = json.loads(run(client + ['image', 'inspect', target, '--format', '{{json .RepoDigests}}'], 'pushed digest', env=env))
        pushed = [d for d in digests if d.startswith(REG + '/ibkr-platform@sha256:')]
        if len(pushed) != 1:
            raise ValueError('Pushed digest missing')
        result.update(passed=True, pushed_image=pushed[0], removed_files=removed)
    except Exception as error:
        result.update(passed=False, error=str(error)[-1500:])
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if log is not None:
            log.close()
        if mounted:
            subprocess.run(['umount', '/etc/hosts'], timeout=15)
        if root.parent == Path('/tmp') and root.name.startswith('wealthryse-website-build-'):
            shutil.rmtree(root, ignore_errors=True)
    print(PREFIX + json.dumps(result), flush=True)
    if not result.get('passed'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
