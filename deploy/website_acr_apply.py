"""Swap the reviewed src/ibkr_etl files into the live image. Runs as root inside an ACR Tasks build.

`deploy/website_release.py acr-build` copies this script, release.json and the reviewed files
into the build context; the Dockerfile starts FROM the exact live website image. Each file the
change touches must be in the live image exactly as the deployed commit has it (or already as
reviewed); otherwise nothing is built, so a live-only edit is never overwritten. Standard library only.
"""
import hashlib, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path('/app/src/ibkr_etl')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def apply(release, src=SRC, files=HERE / 'files'):
    owner = src.stat().st_uid
    if str(owner) != release['runtime_user'].partition(':')[0]:
        raise SystemExit(f"The live image's source belongs to uid {owner}, not the expected runtime user {release['runtime_user']}")
    drifted = []
    for name, want in sorted(release['touched'].items()):
        # Some live files were saved with Windows line endings; Python reads both.
        live = src / name
        if name in release['imported'] and not live.is_file():
            continue  # imported first and missing from the live image: added whole below (see first_imported)
        unix = hashlib.sha256(live.read_bytes().replace(b'\r\n', b'\n')).hexdigest() if live.is_file() else None
        if not {sha(live), unix} & {want['live'], want['new']}:
            drifted.append(name)
    if drifted:
        raise SystemExit('Live website files differ from the deployed commit; nothing built: ' + ', '.join(drifted))
    written, removed = [], []
    for name, want in sorted(release['touched'].items()):
        target = src / name
        if want['new'] is None:
            if target.exists():
                target.unlink()
                removed.append(name)
        elif sha(target) != want['new']:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((files / name).read_bytes())
            written.append(name)
    # Files the reviewed website imports and the live one did not (see first_imported).
    for name, digest in sorted(release['imported'].items()):
        if sha(src / name) != digest:
            (src / name).parent.mkdir(parents=True, exist_ok=True)
            (src / name).write_bytes((files / name).read_bytes())
            written.append(name)
    for name, want in list(release['touched'].items()) + [(n, {'new': d}) for n, d in release['imported'].items()]:
        if sha(src / name) != want['new']:
            raise SystemExit(name + ' does not match the reviewed commit after the swap')
    print(json.dumps({'written': written, 'removed': removed}, sort_keys=True))


if __name__ == '__main__':
    apply(json.loads((HERE / 'release.json').read_text()))
