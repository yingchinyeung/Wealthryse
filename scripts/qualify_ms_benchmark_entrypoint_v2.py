"""Preserve the rejected v1; omit only Azure's null template defaults."""
from pathlib import Path

import qualify_ms_benchmark_entrypoint as original

STAGE=original.r.FOLDER/'benchmark-entrypoint-v2'


def main():
    p=original.p
    previous=p.release.read(original.STAGE,'intent');p.verify_protected(previous)
    if previous['controller_sha256']!=p.sha(Path(original.__file__)):
        raise ValueError('Frozen original entrypoint controller changed')
    continuation=original.r.FOLDER/'entrypoint-null-default-continuation.json'
    if not continuation.exists():
        if (original.STAGE/'dispatch.json').exists():raise ValueError('Reconcile previous dispatch')
        executions=p.request('GET',original.r.path('benchmark')+'/executions'+p.release.API).get('value',[])
        if any(c.get('args')==previous['template']['containers'][0]['args'] for e in executions
               for c in e['properties'].get('template',{}).get('containers',[])):
            raise ValueError('Previous payload executed; reconcile before new qualification')
        record={'controller_sha256':p.sha(Path(__file__)),
                'previous_intent_sha256':p.sha(original.STAGE/'intent.json'),
                'previous_dispatch_rejected_http':400,'matching_execution_found':False,
                'change':'Omit null template fields from transient start payload only',
                'live_mutations':0,'persistent_database_connections':0}
        p.release.build.save(original.r.FOLDER,continuation.stem,
            {**record,'protected_receipt':p.retain(record,'ms-entrypoint-null-default-continuation-v2')})
    proof=p.release.read(original.r.FOLDER,continuation.stem);p.verify_protected(proof)
    if proof['controller_sha256']!=p.sha(Path(__file__)):
        raise ValueError('Frozen continuation changed')
    original_live=original.r.live
    original.r.live=lambda kind:original.r.old.clean(original_live(kind))
    original.STAGE=STAGE
    original.main()


if __name__=='__main__':main()
