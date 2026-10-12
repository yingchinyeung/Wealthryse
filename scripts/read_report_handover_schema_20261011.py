"""Read the frozen schema trial, accepting its one exact public tenant variable."""
import control_report_handover_schema_20261011 as c
from control_upgrade_readers_20261010 import execution_env

def verify(job,actual,wanted):
    other=lambda rows:[r for r in rows if r.get('name')!='DATABASE_URL']
    expected=[{'name':'PORTFOLIO_TENANT_ID','value':'00000000-0000-0000-0000-000000000001'}]
    if other(wanted)!=expected or other(actual)!=expected:raise ValueError('Tenant environment drift')
    return execution_env(job,[r for r in actual if r.get('name')=='DATABASE_URL'],[r for r in wanted if r.get('name')=='DATABASE_URL'])

if __name__=='__main__':
    c.execution_env=verify
    c.main('read')
