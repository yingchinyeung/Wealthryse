"""Accept the observed execution-only job-scoped secret alias, not job changes."""
import copy,sys
import control_upgrade_private_canaries_v2_20261011 as previous

original=previous.original

def same(actual,wanted):
    actual=copy.deepcopy(actual)
    if isinstance(wanted,dict) and set(wanted)=={'containers'}:
        _,definitions=original.definitions()
        matches=[name for name,body in definitions.items() if body['properties']['template']==wanted]
        if len(matches)!=1:raise ValueError('Unknown execution template')
        containers=actual.get('containers',[])
        if len(containers)!=1:raise ValueError('Unexpected execution containers')
        env=containers[0].get('env',[])
        expected=wanted['containers'][0]['env']
        if len(env)!=len(expected):raise ValueError('Unexpected execution environment')
        for observed,reference in zip(env,expected):
            if observed.get('value') or observed.get('name')!=reference['name']:
                raise ValueError('Unexpected execution environment value or name')
            if observed.get('secretRef')=='cappjob-'+matches[0]:
                observed['secretRef']=reference['secretRef']
    previous.same(actual,wanted)

def main(*args):
    original.same=same
    original.main(*args)

if __name__=='__main__':main(*sys.argv[1:])
