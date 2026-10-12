"""Build-only, checksum-pinned official runtime; no package lifecycle scripts."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    lock = json.loads(Path('/probe/codex-runtime.lock.json').read_text())
    if (lock['runtime_version'] != '0.160.0'
            or lock['tarball'] != 'https://registry.npmjs.org/@openai/codex/-/codex-0.160.0-linux-x64.tgz'
            or lock['binary_member'] != 'package/vendor/x86_64-unknown-linux-musl/bin/codex'
            or lock['code_mode_host_member'] != 'package/vendor/x86_64-unknown-linux-musl/bin/codex-code-mode-host'):
        raise ValueError('Unreviewed runtime artifact')
    with build_opener(NoRedirect()).open(Request(lock['tarball']), timeout=120) as response:
        content = response.read(200 * 1024 * 1024 + 1)
    if len(content) > 200 * 1024 * 1024:
        raise ValueError('Runtime package exceeds build limit')
    expected = base64.b64decode(lock['integrity'].removeprefix('sha512-'), validate=True)
    if hashlib.sha512(content).digest() != expected:
        raise ValueError('Official package integrity differs')
    manifest = dict(runtime_version=lock['runtime_version'],
                    package_sha256=hashlib.sha256(content).hexdigest(),
                    lock_sha256=hashlib.sha256(Path('/probe/codex-runtime.lock.json').read_bytes()).hexdigest())
    with tarfile.open(fileobj=io.BytesIO(content), mode='r:gz') as archive:
        for key,name,limit in [('binary_member','codex',320*1024*1024),
                               ('code_mode_host_member','codex-code-mode-host',100*1024*1024)]:
            members = [m for m in archive.getmembers() if m.name == lock[key]]
            if len(members) != 1 or not members[0].isfile() or members[0].size > limit:
                raise ValueError('Expected pinned runtime component missing or duplicated')
            # Never extract paths/links; copy only the two exact regular files.
            binary = archive.extractfile(members[0]).read()
            target = Path('/usr/local/bin')/name
            target.write_bytes(binary)
            target.chmod(0o755)
            manifest['binary_sha256' if name=='codex' else 'code_mode_host_sha256'] = hashlib.sha256(binary).hexdigest()
    Path('/probe/runtime-artifact.json').write_text(json.dumps(manifest, sort_keys=True))
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
