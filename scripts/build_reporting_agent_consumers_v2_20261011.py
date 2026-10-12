"""Preserve failed v1 build evidence; repair only test source-path wiring."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
import build_reporting_agent_consumers_20261011 as old


def prepare(kind):
    before = old.STAGE / ('consumer-agent-' + kind + '-v1')
    after = old.STAGE / ('consumer-agent-' + kind + '-v2')
    if after.exists():
        raise ValueError('Preserve previous retry')
    ack = old.h.read(before / 'build-ack.json')
    run = subprocess.run([old.h.build.AZ, '-m', 'azure.cli', 'acr', 'task', 'logs', '-r', old.h.build.REGISTRY,
                          '--run-id', ack['run']], capture_output=True, text=True, encoding='utf-8', timeout=120)
    log = run.stdout + run.stderr
    expected = "FileNotFoundError: [Errno 2] No such file or directory: '/qualification/reporting-agent/src/ibkr_etl/agent_tools.py'"
    if log.count(expected) != 2 or 'Ran 33 tests' not in log or 'FAILED (errors=2)' not in log or 'REPORTING_AGENT_NATIVE ' in log:
        raise ValueError('Unexpected prior failure; stop for diagnosis')
    with (before / 'native-failure.log').open('x', encoding='utf-8') as stream:
        stream.write(log)
    old.h.save(before / 'native-failure.json', {'run': ack['run'], 'passed': False,
        'reason': 'Two source-inspection tests lacked the relative source link; other 31 tests passed.',
        'log_sha256': old.h.sha(before / 'native-failure.log'), 'deployed': False})
    shutil.copytree(before / 'payload', after / 'payload')
    shutil.copyfile(before / 'prepared.json', after / 'prepared.json')


if __name__ == '__main__':
    stage, kind = sys.argv[1:]
    if kind not in ('web', 'assurance'):
        raise ValueError('Exact two consumers only')
    if stage == 'prepare':
        prepare(kind)
    source = Path(old.__file__).read_text()
    for before, after in (("('consumer-agent-' + kind + '-v1')", "('consumer-agent-' + kind + '-v2')"),
                          ("kind + '-20261011-v1'", "kind + '-20261011-v2'")):
        if source.count(before) != 1:
            raise ValueError('Exact fresh candidate anchor required')
        source = source.replace(before, after)
    exec(compile(source, 'scoped-agent-consumer-v2', 'exec'))
