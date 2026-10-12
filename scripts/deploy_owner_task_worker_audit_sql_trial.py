"""Explicit one-shot audit SQL trial; never retries a consumed predecessor."""
import argparse
import json
import re

import owner_task_worker_audit_sql_candidate as candidate


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=('prepare','job','accept-job','start','observe','collect'))
    command=parser.parse_args().command
    n=candidate.controller_namespace();client=n['Transport']()
    result=(n['prepare'](client) if command=='prepare' else n['dispatch'](client,command)
        if command in ('job','start') else n['accept_job'](client) if command=='accept-job'
        else n['observe'](client) if command=='observe' else n['collect'](client))
    print(json.dumps({k:result[k] for k in ('status','protected_receipt','execution','native_status') if k in result}),flush=True)


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        code=str(error) if isinstance(error,ValueError) and re.fullmatch('fence_[a-z_]+',str(error)) else type(error).__name__
        print(json.dumps(dict(status='step_3_audit_sql_unaccepted_inspect_only',code=code,automatic_retry=False)),flush=True)
        raise SystemExit(1) from None
