"""Reviewed dispatch filename correction; frozen worker/plan remain unchanged.

The original source preflight is retained. Its job phase stopped on the older
trial's consumed marker before any intent/reservation/write. This extension
allows only the two named source additions, never a changed predecessor.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import re
from pathlib import Path
import deploy_owner_task_catalog_diagnostic as base

EXTRAS=frozenset(('scripts/owner_task_catalog_dispatch_scope.py','tests/test_owner_task_catalog_dispatch_scope.py'))
PREFLIGHT_SHA='69f6d0f49eaea6a2826eff6397a2880437dcbecd75adf0bd3c1cd2d6c5e1f847'
BASE_SHA='9e32fd1de374a07e3ca2158f0b1e4b0bdee53202ee4b197a66429b0add20869b'
REVIEW_NAME='serviceaud-step3-catalog-live-dispatch-scope-review.json'
require=base.require


def extension(before,after):
    require(type(before) is dict and len(before)==834 and type(after) is dict
        and set(after)==set(before)|EXTRAS and not set(before)&EXTRAS
        and all(after[k]==v for k,v in before.items())
        and all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in after.values()))


def source_chain(before,after):
    if len(after)==834:return base.source_extension(before,after)
    original={k:v for k,v in after.items() if k not in EXTRAS}
    base.source_extension(before,original);extension(original,after)


def predecessor_context():
    require(hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest()==BASE_SHA)
    body=ast.get_source_segment(Path(base.__file__).read_text(),next(n for n in ast.parse(Path(base.__file__).read_text()).body
        if isinstance(n,ast.FunctionDef) and n.name=='old_context'))
    namespace=dict(vars(base),source_extension=source_chain)
    exec(compile(body,__file__,'exec'),namespace)
    return namespace['old_context']()


def corrected_dispatch(namespace):
    source=Path(base.generic.__file__).read_text()
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='dispatch')
    body=ast.get_source_segment(source,node)
    require(body.count("'serviceaud-step3-fence-'")==1)
    body=body.replace("'serviceaud-step3-fence-'",repr(base.NAME_PREFIX))
    exec(compile(body,__file__,'exec'),namespace)
    return namespace['dispatch']


def control():
    n=base.control();old,guard=predecessor_context();p=n['p']
    def guards(pf):
        pr,registered=n['load']('live-preflight')
        require(pr['protected_receipt']['sha256']==PREFLIGHT_SHA and registered==pf)
        rr,review=p.old.load(REVIEW_NAME)
        current=p.old.prior.host.sources();extension(pf['sources'],current)
        require(review['status']=='step_3_catalog_dispatch_scope_reviewed'
            and review['preflight_receipt']==pr['protected_receipt'] and review['sources']==current
            and review['worker_plan_unchanged'] is True and review['step_3_complete'] is False and review['step_4_started'] is False)
        identity,native=base.predecessor(old,guard)
        require(identity==pf['sql_identity'] and native==pf['native_predecessor'])
        return identity
    n['guards']=guards;corrected_dispatch(n)
    return n


def review():
    n=base.control();p=n['p'];old,guard=predecessor_context()
    require(not (p.old.FOLDER/REVIEW_NAME).exists())
    pr,pf=n['load']('live-preflight')
    require(pr['protected_receipt']['sha256']==PREFLIGHT_SHA and len(pf['sources'])==834)
    sources=p.old.prior.host.sources();extension(pf['sources'],sources)
    require(not any((p.old.FOLDER/(base.NAME_PREFIX+'job-'+k+'.json')).exists() for k in ('intent','ack','accepted')))
    client=n['Transport']();require(client.call('GET',base.worker.JOB_ID,missing=True) is None)
    identity,before=base.predecessor(old,guard)
    require(identity==pf['sql_identity'] and before==pf['native_predecessor'])
    checks=p.old.prior.parent.reviewed.run_local_checks()
    identity2,after=base.predecessor(old,guard)
    require(before==after and identity==identity2 and p.old.prior.host.sources()==sources
        and client.call('GET',base.worker.JOB_ID,missing=True) is None)
    return p.old.save(dict(status='step_3_catalog_dispatch_scope_reviewed',checked_at_utc=p.old.now(),
        preflight_receipt=pr['protected_receipt'],sources=sources,local_checks=checks,
        native_before=before,native_after=after,all_834_predecessor_sources_preserved=True,
        worker_plan_unchanged=True,original_controller_dispatch_blocked_before_intent=True,
        diagnostic_job_created=False,sql_mutations=0,source_rows_read=0,source_rows_written=0,
        production_changed=False,step_3_complete=False,step_4_started=False),REVIEW_NAME)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=('review','job','accept-job','start','observe','collect'))
    command=parser.parse_args().command
    try:
        if command=='review':r=review()
        else:
            n=control();client=n['Transport']()
            r=n['dispatch'](client,command) if command in ('job','start') else n['accept_job'](client) if command=='accept-job' else n['observe'](client) if command=='observe' else n['collect'](client)
        print(json.dumps({k:r[k] for k in ('status','protected_receipt','execution','native_status') if k in r}),flush=True)
    except Exception as error:
        print(json.dumps(dict(status='step_3_catalog_scope_unaccepted_inspect_only',code=type(error).__name__,automatic_retry=False)),flush=True)
        raise SystemExit(1) from None
