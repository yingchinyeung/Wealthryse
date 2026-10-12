"""Prepare a sanitized inbox image overlay; no build or deployment implicit.

Pins the exact current web image and retains its private renderer assets. The
context contains reviewed source and tests only, never .work, templates, raw
data, reports, Git, environment files or credentials. Source hashes bind both
the preparation and protected build intent; candidate promotion is separate.
"""
import argparse
import hashlib
import json
from pathlib import Path

from deploy_specialist_report_worker import ROOT
from inspect_agent_inbox_release import live_web
from run_specialist_gateway_read_verification import retain

FOLDER=ROOT/'.work/ai-inbox-candidate-20261003'
TAG='ibkr-platform:ai-inbox-candidate-20261003'
FIXTURE='tests/fixtures/sample_flex.xml'
FIXTURE_SHA256='83cfadd3b192a1c0574597ac845dd3ade5db341f57df3394490225d5e2230ecb'


def source_paths(root):
    result=set()
    types={'src':{'.py'},'scripts':{'.py','.ps1','.mjs','.cjs','.js'},'tests':{'.py'},
           'infra':{'.bicep'},'migrations':{'.sql'},'deploy':{'.Dockerfile'}}
    for folder in types:
        for path in (root/folder).rglob('*'):
            relative=path.relative_to(root)
            if any(p.startswith('.') or p in ('__pycache__','node_modules','private-inputs','templates','reports','raw','data') for p in relative.parts):
                continue
            if path.is_file() and path.suffix in types[folder]:
                result.add(relative.as_posix())
    if (root/FIXTURE).is_file():result.add(FIXTURE)
    return sorted(result|{'requirements.lock','Dockerfile','Dockerfile.azure','Dockerfile.risk','pyproject.toml'})


def verify_fixture(root):
    # Reviewed parser example for fictional U1234567; never glob arbitrary XML.
    if hashlib.sha256((root/FIXTURE).read_bytes()).hexdigest()!=FIXTURE_SHA256:
        raise ValueError('Only the reviewed synthetic parser fixture may enter the context')


def main():
    p=argparse.ArgumentParser();p.add_argument('--prepare',action='store_true',required=True)
    p.add_argument('--attempt',type=int,choices=range(1,6),default=1);args=p.parse_args()
    folder=FOLDER if args.attempt==1 else FOLDER.with_name(FOLDER.name+'-r'+str(args.attempt))
    tag=TAG if args.attempt==1 else TAG+'-r'+str(args.attempt)
    inspection=json.loads((ROOT/'.work/specialist-inbox-release-20261003/acceptance.json').read_text())
    if inspection.get('status')!='passed' or inspection.get('protected_receipt',{}).get('protected_readback_verified') is not True:
        raise ValueError('Protected live image inspection required')
    current=live_web()
    if inspection['web']!=current:raise ValueError('Live web changed; review new baseline')
    verify_fixture(ROOT)
    files={name:(ROOT/name).read_bytes() for name in source_paths(ROOT)}
    hashes={name:hashlib.sha256(value).hexdigest() for name,value in sorted(files.items())}
    source_hash=hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    intent=dict(schema='ai-inbox-candidate-v1',source_manifest_sha256=source_hash,files=hashes,
        base=current,tag=tag,build_only=True,production_promotion_authorized_by_this_receipt=False,
        private_data_in_context=False,baseline_assets_retained=True,
        source_rows_changed=0,microsoft365_sender_enabled=False,report_ready_notifications_enabled=False)
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/'intent.json'
    if path.exists():
        prior=json.loads(path.read_text())
        if prior['intent']!=intent:raise ValueError('Different candidate intent exists; do not replace')
        protected=prior['protected_receipt']
    else:
        protected=retain(intent,'inbox-candidate-build-intent')
        path.write_text(json.dumps(dict(intent=intent,protected_receipt=protected),indent=2),encoding='utf-8')
    context=folder/'context'
    if context.exists():
        names={p.relative_to(context).as_posix() for p in context.rglob('*') if p.is_file()}
        if names-set(files):raise ValueError('Unexpected files in isolated build context')
    for name,payload in files.items():
        target=context/name
        if target.exists() and target.read_bytes()!=payload:raise ValueError('Refusing to replace changed candidate bytes')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload)
    print(json.dumps(dict(context=str(context),base_image=current['image'],tag=tag,
        source_manifest_sha256=source_hash,file_count=len(files),protected_intent=protected,
        production_deployed=False)),flush=True)


if __name__=='__main__':main()
