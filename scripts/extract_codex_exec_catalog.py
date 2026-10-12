"""Read bundled metadata from the pinned installed binary without model refresh.

Mechanically vendors public bundled metadata into the build source. Never changes desktop configuration,
uses its credentials or invokes inference. The Linux image must independently
match the same native metadata before accepting the deployment alias.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BINARY = Path('C:/Users/antho/AppData/Local/OpenAI/Codex/bin/23f7d7f110b19ac3/codex.exe')
BINARY_SHA256 = '7d4588265a55adb1403f85d2e058b11dc971459842de84876c86ff02fb7771f2'
FOLDER = ROOT/'deploy'
NATIVE_MODEL = 'gpt-5.6-sol'
ALIAS = 'gpt-5-6-sol-apac'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def alias_catalog(bundled):
    models = bundled.get('models') if isinstance(bundled, dict) else None
    if not isinstance(models, list):
        raise ValueError('Bundled model catalog shape differs')
    selected = [m for m in models if isinstance(m, dict) and m.get('slug') == NATIVE_MODEL]
    if len(selected) != 1:
        raise ValueError('Exactly one native model entry required')
    native = selected[0]
    alias = copy.deepcopy(native)
    alias.update(slug=ALIAS, upgrade=None)
    return {'models': [alias]}, hashlib.sha256(canonical(native)).hexdigest()


def main():
    if hashlib.sha256(BINARY.read_bytes()).hexdigest() != BINARY_SHA256:
        raise ValueError('Pinned local binary differs')
    with tempfile.TemporaryDirectory(prefix='codex-bundled-metadata-', dir=ROOT/'.work') as name:
        directory = Path(name)
        environment = {k: os.environ[k] for k in ('SystemRoot', 'WINDIR', 'PATH') if k in os.environ}
        environment.update(HOME=name, USERPROFILE=name, CODEX_HOME=str(directory/'codex-home'),
                           TEMP=name, TMP=name)
        version = subprocess.run([str(BINARY), '--version'], cwd=directory, env=environment,
                                 capture_output=True, timeout=10, check=True)
        if version.stdout.decode().strip() != 'codex-cli 0.160.0':
            raise ValueError('Pinned local runtime version differs')
        result = subprocess.run([str(BINARY), 'debug', 'models', '--bundled'], cwd=directory,
                                env=environment, capture_output=True, timeout=20, check=True)
        if len(result.stdout) > 4*1024*1024:
            raise ValueError('Bundled catalog exceeds limit')
        value, native_hash = alias_catalog(json.loads(result.stdout))
    content = canonical(value)
    FOLDER.mkdir(parents=True, exist_ok=True)
    path = FOLDER/'codex-exec-model-catalog.json'
    if path.exists() and path.read_bytes() != content:
        raise ValueError('Existing catalog artifact differs; do not overwrite')
    path.write_bytes(content)
    record = dict(schema='codex-exec-bundled-catalog-v1', runtime_version='0.160.0',
                  local_binary_sha256=BINARY_SHA256, native_model=NATIVE_MODEL, deployment_alias=ALIAS,
                  native_entry_sha256=native_hash, catalog_sha256=hashlib.sha256(content).hexdigest(),
                  edits=['slug', 'upgrade'], credentials_included=False, inference_called=False)
    receipt = FOLDER/'codex-exec-catalog-source.json'
    if receipt.exists() and json.loads(receipt.read_text()) != record:
        raise ValueError('Existing catalog descriptor differs')
    receipt.write_text(json.dumps(record, sort_keys=True, indent=2))
    print(json.dumps(record, sort_keys=True))


if __name__ == '__main__': main()
