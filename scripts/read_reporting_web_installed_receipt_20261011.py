"""Read the existing trial; validate its database alias and explicit tenant separately."""
import control_reporting_web_installed_20261011 as c
from control_upgrade_readers_20261010 import execution_env

def validate_env(job, actual, wanted):
    if len(actual)!=2 or len(wanted)!=2:
        raise ValueError('Exactly database and tenant variables required')
    def parts(rows):
        if {r['name'] for r in rows}!={'DATABASE_URL','PORTFOLIO_TENANT_ID'}:
            raise ValueError('Unexpected environment name')
        tenant=[r for r in rows if r['name']=='PORTFOLIO_TENANT_ID'][0]
        if tenant!={'name':'PORTFOLIO_TENANT_ID','value':'00000000-0000-0000-0000-000000000001'}:
            raise ValueError('Tenant changed')
        return [r for r in rows if r['name']=='DATABASE_URL']
    return execution_env(job,parts(actual),parts(wanted))

if __name__=='__main__':
    c.execution_env=validate_env
    c.main('read')
