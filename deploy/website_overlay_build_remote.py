"""Build a website image on the private build VM. Run only via Azure Run Command.

The deploy-website workflow fills in CONFIG and runs this as root inside a
private mount namespace (`unshare --mount`). It starts from the exact image
the website runs today, applies only the reviewed change to src/ibkr_etl on top
of the live files (every hunk must apply exactly), builds
with no network, and pushes one new tag over the registry's private endpoint.
No application starts and no database is touched.
"""
import base64, gzip, hashlib, io, json, os, re, shutil, tarfile, socket, ssl, subprocess, tempfile, time, urllib.error, urllib.parse, urllib.request
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


# Runs inside the new image as root: mirror the source into the installed package
# if it lives elsewhere, drop removed files and caches, and hand /app/src back to
# the image's runtime user.
INSTALL_SCRIPT = """
import importlib.util, os, pwd, grp, shutil
src = '/app/src/ibkr_etl'
removed = REMOVED
site = os.path.dirname(importlib.util.find_spec('ibkr_etl').origin)
for name in removed:
    if os.path.exists(os.path.join(src, name)):
        os.remove(os.path.join(src, name))
if os.path.realpath(site) != os.path.realpath(src):
    shutil.copytree(src, site, dirs_exist_ok=True)
    for name in removed:
        if os.path.exists(os.path.join(site, name)):
            os.remove(os.path.join(site, name))
for top in {src, site}:
    for root, dirs, files in os.walk(top):
        if '__pycache__' in dirs:
            shutil.rmtree(os.path.join(root, '__pycache__'))
            dirs.remove('__pycache__')
spec = RUNTIME_USER
user, _, group = spec.partition(':')
uid = int(user) if user.isdigit() else pwd.getpwnam(user).pw_uid
gid = int(group) if group.isdigit() else (grp.getgrnam(group).gr_gid if group else (pwd.getpwuid(uid).pw_gid if not user.isdigit() else uid))
for root, dirs, files in os.walk('/app/src'):
    for name in [root] + [os.path.join(root, f) for f in dirs + files]:
        os.chown(name, uid, gid)
"""


def find_python(client, base, env):
    """Absolute path of the live image's Python, read from the image's files without running it."""
    config = json.loads(run(client + ['image', 'inspect', base, '--format', '{{json .Config}}'], 'live image config', env=env))
    path = next((e.split('=', 1)[1] for e in config.get('Env') or [] if e.startswith('PATH=')), '')
    container = run(client + ['create', base], 'inspect container', env=env).strip()
    entries = {}
    try:
        export = subprocess.Popen(client + ['export', container], stdout=subprocess.PIPE, env=env)
        with tarfile.open(fileobj=export.stdout, mode='r|') as archive:
            for member in archive:
                name = '/' + member.name.lstrip('./')
                if os.path.dirname(name).endswith('/bin') and (member.isfile() or member.issym()):
                    entries[name] = member.linkname if member.issym() else None
        export.wait(timeout=600)
    finally:
        subprocess.run(client + ['rm', '-f', container], capture_output=True, timeout=120, env=env)

    def resolve(name, depth=0):
        if name not in entries or depth > 8:
            return None
        link = entries[name]
        if link is None:
            return name
        return resolve(os.path.normpath(os.path.join(os.path.dirname(name), link)), depth + 1)

    dirs = [d for d in path.split(':') if d] + ['/usr/local/bin', '/usr/bin', '/opt/venv/bin', '/app/.venv/bin', '/venv/bin']
    names = ['python3', 'python'] + sorted({os.path.basename(n) for n in entries if re.fullmatch(r'python3\.\d+', os.path.basename(n))}, reverse=True)
    for folder in dict.fromkeys(dirs):
        for name in names:
            if resolve(folder + '/' + name):
                return folder + '/' + name
    raise ValueError('No Python found in the live image: ' + json.dumps({
        'found': sorted(n for n in entries if 'python' in os.path.basename(n))[:20], 'path': path}))


class Captured(Exception):
    """Capture finished; skip the build."""


def tree_hashes(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob('*.py')) if '__pycache__' not in p.parts}


def rejected_context(package):
    """For each hunk patch rejected, the live lines it was aimed at."""
    out = {}
    for rej in sorted(package.rglob('*.rej')):
        name = rej.relative_to(package).as_posix()[:-len('.rej')]
        lines = (package / name).read_text(errors='replace').splitlines()
        parts = []
        for header in re.findall(r'^@@ -(\d+)(?:,(\d+))? ', rej.read_text(errors='replace'), re.M):
            start, count = int(header[0]), int(header[1] or 1)
            lo, hi = max(start - 25, 1), min(start + count + 25, len(lines))
            parts.append('--- live lines %d-%d\n' % (lo, hi) + '\n'.join(lines[lo - 1:hi]))
        out[name] = '\n'.join(parts)
    return out

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
    detail = ''
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
        if config.get('capture'):
            # Read-only: hand back exact copies of the named live files; nothing is built.
            names = config['capture']
            missing = [n for n in names if n not in live]
            if missing:
                raise ValueError('Not in the live image: ' + ', '.join(missing))
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode='w:xz', preset=9) as tar:
                for name in names:
                    tar.add(str(package / name), arcname=name)
            detail = base64.b64encode(buffer.getvalue()).decode()
            result.update(passed=True, captured={n: live[n] for n in names})
            raise Captured()
        expected, wanted = config['live_hashes'], config['new_hashes']
        # Counts only: the full lists were reported by earlier runs and the
        # Run Command output is small.
        result['live_drift'] = {
            'only_live': len(set(live) - set(expected)),
            'only_expected': len(set(expected) - set(live)),
            'different': len([n for n in set(live) & set(expected) if live[n] != expected[n]]),
        }
        # The live source is not exactly the commit the change was made on, so the
        # change is applied on top of whatever the live files are. Each file the
        # change touches must be where the change expects it, and every hunk must
        # apply exactly (no fuzz); otherwise nothing is built.
        touched = sorted(n for n in set(expected) | set(wanted) if expected.get(n) != wanted.get(n))
        for name in touched:
            if (name in expected) != (name in live):
                raise ValueError('Live website file ' + name + ' is not where the change expects it; nothing built')
        # Some live files were saved with Windows line endings; the change uses
        # Unix ones. Python reads both, so touched files are normalised first.
        normalised = []
        for name in touched:
            path = package / name
            if path.exists() and b'\r\n' in path.read_bytes():
                path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
                normalised.append(name)
        result['line_endings_normalised'] = normalised
        live = {**live, **{n: h for n, h in tree_hashes(package).items() if n in normalised}}
        patch_file = root / 'change.patch'
        patch_file.write_bytes(gzip.decompress(base64.b64decode(config['patch'])))
        applied = subprocess.run(['patch', '-p1', '--forward', '--fuzz=0', '--no-backup-if-mismatch', '-i', str(patch_file)],
                                 capture_output=True, text=True, timeout=300, cwd=str(context))
        if applied.returncode:
            detail = base64.b64encode(gzip.compress(json.dumps(rejected_context(package)).encode(), 9)).decode()
            raise ValueError('Some of the change does not apply to the live files; nothing built')
        patched = tree_hashes(package)
        for name in touched:
            if live.get(name) == expected.get(name) and patched.get(name) != wanted.get(name):
                raise ValueError('Patched ' + name + ' does not match the reviewed commit; nothing built')
        if any(patched.get(n) != live[n] for n in live if n not in touched):
            raise ValueError('The change altered files it should not have; nothing built')
        result['touched_files'] = touched
        removed = sorted(n for n in set(expected) - set(wanted) if n in live)
        # The live image has no "root" passwd entry and may have no shell, so
        # the build uses numeric ids and exec-form Python steps only, and runs
        # the checks as the image's own user.
        user = run(client + ['image', 'inspect', base, '--format', '{{.Config.User}}'], 'live image user', env=env).strip() or '0'
        python = find_python(client, base, env)
        install = (INSTALL_SCRIPT.replace('REMOVED', repr(removed)).replace('RUNTIME_USER', repr(user)))
        lines = ['FROM ' + base, 'USER 0:0',
                 'LABEL ai.wealthryse.source-commit="' + config['source_commit'] + '"',
                 'COPY src/ibkr_etl/ /app/src/ibkr_etl/',
                 'RUN ' + json.dumps([python, '-c', install]),
                 'USER ' + user,
                 'RUN ' + json.dumps([python, '-c', 'import ibkr_etl.web']),
                 'RUN ' + json.dumps([python, 'scripts/check_report_runtime.py'])]
        result.update(runtime_user=user, python=python)
        (context / 'Dockerfile').write_text('\n'.join(lines) + '\n')
        run(client + ['build', '--network=none', '--pull=false', '-t', target, str(context)], 'website image build', timeout=1800, env=env)
        run(client + ['push', target], 'website image push', env=env)
        digests = json.loads(run(client + ['image', 'inspect', target, '--format', '{{json .RepoDigests}}'], 'pushed digest', env=env))
        pushed = [d for d in digests if d.startswith(REG + '/ibkr-platform@sha256:')]
        if len(pushed) != 1:
            raise ValueError('Pushed digest missing')
        result.update(passed=True, pushed_image=pushed[0], removed_files=removed)
    except Captured:
        pass
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
    # Run Command returns only 4 KB of output, so larger detail (conflicts or
    # captured files) is left in a root-only file the workflow reads in pieces
    # and then removes.
    if detail:
        path = '/var/tmp/wealthryse-build-detail-' + config['source_commit'][:12] + '.txt'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(detail)
        result.update(detail_file=path, detail_size=len(detail))
    print(PREFIX + json.dumps(result), flush=True)
    if not result.get('passed'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
