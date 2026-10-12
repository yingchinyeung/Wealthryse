"""Versioned harness correction: installed worker is in /app/scripts, not /app/worker."""
import hashlib,json,sys
import control_upgrade_g04_worker_20261010 as base
from read_upgrade_g04_worker_20261010 import exact_environment
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected

OLD=base.OUT
OUT=base.ROOT/'.work/platform-upgrade-20261010-stage29/worker-preflight-v2'
original_sources=base.sources
original_template=base.p.corrected_template


def corrected(job,code):
    old="Path('/app/worker/generate_month_end_package.py')"
    if code.count(old)!=1:raise ValueError('Exact path correction only')
    return original_template(job,code.replace(old,"Path('/app/scripts/generate_month_end_package.py')"))


def sources():
    return {**original_sources(),**{n:hashlib.sha256((base.ROOT/'scripts'/n).read_bytes()).hexdigest() for n in
        ('control_upgrade_g04_worker_v2_20261010.py','read_upgrade_g04_worker_20261010.py')}}


def main(stage):
    prior=protected(OLD/'intent.json')
    if prior['sources']!=original_sources():raise ValueError('Original evidence changed')
    if stage=='start':
        name=protected(OLD/'dispatch.json')['execution']
        run=base.p.request('GET',base.PATH+'/executions/'+name+base.p.API)
        if run['properties']['status']!='Failed':raise ValueError('Prior attempt not terminal')
        prefix='UPGRADE_G04_WORKER_ERROR '
        rows=base.p.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
        if len(rows)!=1 or json.loads(rows[0]['Result']).get('type')!='FileNotFoundError':raise ValueError('Prior failure differs')
        if not (OLD/'failure-reconciled.json').exists():save(OLD,'failure-reconciled',{'execution':name,'error':json.loads(rows[0]['Result']),
            'cause':'Worker is installed in /app/scripts as declared by the pinned image Dockerfile; failure before DB URL redirection or DB access',
            'correction':'One exact source path correction in a separately versioned preflight harness'})
    base.OUT=OUT;base.sources=sources;base.p.corrected_template=corrected;base.execution_env=exact_environment
    base.main(stage)


if __name__=='__main__':main(*sys.argv[1:])
