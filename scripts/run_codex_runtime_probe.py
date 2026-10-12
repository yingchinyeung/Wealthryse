"""One fixed synthetic test; never an arbitrary task execution entry point."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from ibkr_etl.codex_runtime_probe import (VERSION, SyntheticRelay, protocol_probe, managed_token,
                                         child_environment, ProbeError)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', required=True, choices=['offline', 'azure'])
    args = parser.parse_args()
    artifact = json.loads(Path('/probe/runtime-artifact.json').read_text())
    result = dict(schema='codex-synthetic-runtime-acceptance-v1', status='failed', mode=args.mode,
                  runtime_version=VERSION, artifact=artifact, source_data_sent=False,
                  owner_runtime_active=False, six_specialist_runtimes_active=False,
                  production_compatible=False, protocol_stability='experimental')
    try:
        if (artifact['runtime_version'] != VERSION or
                hashlib.sha256(Path('/usr/local/bin/codex').read_bytes()).hexdigest() != artifact['binary_sha256'] or
                hashlib.sha256(Path('/usr/local/bin/codex-code-mode-host').read_bytes()).hexdigest() != artifact['code_mode_host_sha256']):
            raise ProbeError('runtime_binary_differs')
        with tempfile.TemporaryDirectory(prefix='codex-synthetic-') as name:
            directory = Path(name)
            for part in ('home', 'codex-home', 'tmp', 'workspace'):
                (directory/part).mkdir()
            version = subprocess.run(['/usr/local/bin/codex', '--version'], cwd=directory/'workspace',
                env=child_environment(directory, 'not-a-model-credential'), capture_output=True, timeout=10, check=True)
            if version.stdout.decode().strip() != 'codex-cli '+VERSION:
                raise ProbeError('runtime_version_differs')
            # Verify the exact runtime's wire enum instead of trusting examples
            # from rolling documentation or the installed desktop version.
            schema_dir = directory/'protocol-schema'
            subprocess.run(['/usr/local/bin/codex','app-server','generate-json-schema','--out',str(schema_dir)],
                cwd=directory/'workspace',env=child_environment(directory,'not-a-model-credential'),
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20,check=True)
            schema_hashes = {}
            sandbox_enums = []
            for path in schema_dir.rglob('*.json'):
                raw = path.read_bytes()
                if len(raw)>10*1024*1024:
                    raise ProbeError('generated_schema_limit')
                schema_hashes[str(path.relative_to(schema_dir))] = hashlib.sha256(raw).hexdigest()
                value = json.loads(raw)
                definitions = value.get('definitions',{}) | value.get('$defs',{})
                if path.name == 'SandboxMode.json':definitions['SandboxMode'] = value
                if 'SandboxMode' in definitions:
                    sandbox_enums.append(definitions['SandboxMode'].get('enum',[]))
            if not sandbox_enums or not all('read-only' in values for values in sandbox_enums):
                raise ProbeError('generated_sandbox_wire_enum_differs')
            result['protocol_schema_sha256'] = hashlib.sha256(json.dumps(schema_hashes,sort_keys=True).encode()).hexdigest()
            result['sandbox_wire_enum_verified'] = True
            token = managed_token(os.environ) if args.mode == 'azure' else None
            try:
                with SyntheticRelay(token) as relay:
                    try:
                        result.update(asyncio.run(protocol_probe(relay, directory)))
                    finally:
                        result.update(request_count=relay.request_count, accepted_count=relay.accepted_count,
                                      advertised_tool_count=relay.advertised_tool_count,
                                      upstream_tools_enabled=False, response_sha256=relay.response_sha256,
                                      relay_failure=relay.failure)
                    if (all(result.get(k) is True for k in ('handshake_verified','turn_completed',
                            'streamed_delta_observed','runtime_output_verified'))
                            and relay.request_count == relay.accepted_count == 1 and not relay.failure):
                        result['status'] = 'passed'
            finally:
                token = None
    except Exception as error:
        result['reason'] = str(error) if isinstance(error, ProbeError) else 'runtime_trial_failed'
    print('CODEX_RUNTIME_PROBE '+json.dumps(result, sort_keys=True), flush=True)
    if result['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
