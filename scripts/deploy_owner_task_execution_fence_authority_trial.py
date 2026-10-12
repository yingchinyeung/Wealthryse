"""Explicit new one-shot trial; no automatic retry of either predecessor."""
import argparse
import json
import re

import owner_task_execution_fence_authority_candidate as candidate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('prepare', 'job', 'accept-job', 'start', 'observe', 'collect'))
    command = parser.parse_args().command
    namespace = candidate.controller_namespace()
    client = namespace['Transport']()
    result = (namespace['prepare'](client) if command == 'prepare'
        else namespace['dispatch'](client, command) if command in ('job', 'start')
        else namespace['accept_job'](client) if command == 'accept-job'
        else namespace['observe'](client) if command == 'observe' else namespace['collect'](client))
    print(json.dumps({key: result[key] for key in ('status', 'protected_receipt', 'execution', 'native_status') if key in result}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(status='step_3_authority_candidate_unaccepted_inspect_only',
            code=str(error) if isinstance(error, ValueError) and re.fullmatch('fence_[a-z_]+', str(error)) else type(error).__name__,
            automatic_retry=False)), flush=True)
        raise SystemExit(1) from None
