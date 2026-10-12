"""Bind an explicit existing native run after missing CLI acknowledgement."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re

import build_ms_presentation_worker_candidate as build
from rehearse_ms_worker_image_rollback import restore_protected


def main():
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,required=True)
    p.add_argument('--run-id',required=True);args=p.parse_args()
    folder=args.folder.resolve()
    if folder.parent!=build.ROOT/'.work' or not folder.name.startswith('alpha-ms-website-benchmark-candidate-'):
        raise ValueError('Explicit scoped candidate folder required')
    source=json.loads((folder/'source-intent.json').read_bytes())
    intent=json.loads((folder/'build-intent.json').read_bytes())
    for record in (source,intent):
        restored,_=restore_protected(record['protected_receipt'])
        if restored!={k:v for k,v in record.items() if k!='protected_receipt'}:
            raise ValueError('Protected record differs')
    if intent['source_intent_sha256']!=build.sha(folder/'source-intent.json'):
        raise ValueError('Frozen intent changed')
    if {p.relative_to(folder/'context').as_posix():build.sha(p) for p in (folder/'context').rglob('*') if p.is_file()}!=source['files_sha256']:
        raise ValueError('Frozen context changed')
    if not re.fullmatch(r'[a-z0-9]{4,12}',args.run_id):raise ValueError('Explicit native run required')
    run=build.az('acr','task','show-run','--subscription',build.SUB,'-r',build.REGISTRY,'--run-id',args.run_id)
    elapsed=(datetime.fromisoformat(run['createTime'].replace('Z','+00:00'))-datetime.fromisoformat(intent['prepared_at'])).total_seconds()
    output=run.get('outputImages') or []
    if (not 0<=elapsed<=120 or run['runId']!=args.run_id or run['status']!='Succeeded'
            or len(output)!=1 or output[0]['registry']!=build.REGISTRY+'.azurecr.io'
            or output[0]['repository']!='ibkr-platform' or output[0]['tag']!=source['tag'].split(':',1)[1]
            or not re.fullmatch(r'sha256:[0-9a-f]{64}',output[0]['digest'])):
        raise ValueError('Succeeded native run does not uniquely match frozen tag/time/registry')
    build.save(folder,'build-ack',{'run':args.run_id,'tag':source['tag'],
        'reconciled_after_missing_cli_ack':True,'output_images':output,'redispatched':False})
    print(json.dumps({'run':args.run_id,'redispatched':False,'image':output[0]['digest']}),flush=True)


if __name__=='__main__':main()
