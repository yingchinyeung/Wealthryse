"""Fixed, tool-free CLI test. This is not an owner task entry point."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from ibkr_etl.codex_runtime_probe import (
    VERSION, managed_token, child_environment, ProbeError,
)
from ibkr_etl.codex_exec_probe import ExecSyntheticRelay, exec_probe

CHECKS = ('thread_started_verified', 'turn_started_verified', 'turn_completed',
          'event_stream_observed', 'runtime_output_verified', 'process_exit_verified')


def verify_catalog(directory, environment):
    content = Path('/probe/codex-exec-model-catalog.json').read_bytes()
    descriptor = json.loads(Path('/probe/catalog-source.json').read_text())
    if hashlib.sha256(content).hexdigest() != '0c231c9cce20499aabf693206989a84cb0552d83bb6af80ec1ea471bfeb282bb':
        raise ProbeError('custom_catalog_bytes_differ')
    bundled = subprocess.run(['/usr/local/bin/codex', 'debug', 'models', '--bundled'],
        cwd=directory/'workspace', env=environment, capture_output=True, timeout=20, check=True)
    if len(bundled.stdout) > 4*1024*1024:
        raise ProbeError('bundled_catalog_limit')
    models = json.loads(bundled.stdout).get('models', [])
    selected = [v for v in models if v.get('slug') == 'gpt-5.6-sol']
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    if (len(selected) != 1 or descriptor.get('runtime_version') != VERSION or
            descriptor.get('native_entry_sha256') != '6d1d665f8d03d6dfb18f51d87a510712a0f72fda44423538896b8e1c59427413' or
            hashlib.sha256(canonical(selected[0])).hexdigest() != descriptor['native_entry_sha256']):
        raise ProbeError('native_catalog_metadata_differs')
    alias = dict(selected[0], slug='gpt-5-6-sol-apac', upgrade=None)
    if json.loads(content) != {'models': [alias]}:
        raise ProbeError('custom_alias_metadata_differs')
    return dict(native_catalog_matches_linux=True, custom_alias_verified=True,
                catalog_sha256=descriptor['catalog_sha256'], native_entry_sha256=descriptor['native_entry_sha256'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', required=True, choices=['offline', 'azure'])
    args = parser.parse_args()
    artifact = json.loads(Path('/probe/runtime-artifact.json').read_text())
    result = dict(schema='codex-synthetic-exec-acceptance-v1', status='failed', mode=args.mode,
                  runtime_version=VERSION, artifact=artifact, source_data_sent=False,
                  owner_runtime_active=False, six_specialist_runtimes_active=False,
                  production_accepted=False, interface='cli-exec', interface_maturity='stable')
    try:
        if (artifact['runtime_version'] != VERSION or
                hashlib.sha256(Path('/usr/local/bin/codex').read_bytes()).hexdigest() != artifact['binary_sha256'] or
                hashlib.sha256(Path('/usr/local/bin/codex-code-mode-host').read_bytes()).hexdigest() != artifact['code_mode_host_sha256']):
            raise ProbeError('runtime_binary_differs')
        with tempfile.TemporaryDirectory(prefix='codex-exec-synthetic-') as name:
            directory = Path(name)
            for part in ('home', 'codex-home', 'tmp', 'workspace'):
                (directory/part).mkdir()
            environment = child_environment(directory, 'not-a-model-credential')
            version = subprocess.run(['/usr/local/bin/codex', '--version'], cwd=directory/'workspace',
                env=environment, capture_output=True, timeout=10, check=True)
            if version.stdout.decode().strip() != 'codex-cli '+VERSION:
                raise ProbeError('runtime_version_differs')
            help_result = subprocess.run(['/usr/local/bin/codex', 'exec', '--help'],
                cwd=directory/'workspace', env=environment, capture_output=True, timeout=10, check=True)
            help_bytes = help_result.stdout
            if len(help_bytes) > 65536 or not all(flag.encode() in help_bytes for flag in
                    ('--json', '--skip-git-repo-check', '--sandbox')):
                raise ProbeError('cli_flags_not_supported')
            result.update(cli_flags_verified=True, cli_help_sha256=hashlib.sha256(help_bytes).hexdigest())
            result.update(verify_catalog(directory, environment))
            token = managed_token(os.environ) if args.mode == 'azure' else None
            try:
                with ExecSyntheticRelay(token) as relay:
                    try:
                        result.update(asyncio.run(exec_probe(relay, directory)))
                    finally:
                        result.update(request_count=relay.request_count, accepted_count=relay.accepted_count,
                                      advertised_tool_count=relay.advertised_tool_count,
                                      upstream_tools_enabled=False, response_sha256=relay.response_sha256,
                                      relay_failure=relay.failure)
                    if (all(result.get(k) is True for k in CHECKS)
                            and relay.request_count == relay.accepted_count == 1 and not relay.failure):
                        result['status'] = 'passed'
            finally:
                token = None
    except Exception as error:
        result['reason'] = str(error) if isinstance(error, ProbeError) else 'runtime_trial_failed'
    print('CODEX_EXEC_PROBE '+json.dumps(result, sort_keys=True), flush=True)
    if result['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
