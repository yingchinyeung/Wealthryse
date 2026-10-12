"""Read-only API/CLI shape comparison; never print environment values."""
import hashlib,json
import release_reporting_full_nav_20261011 as c
from control_upgrade_readers_20261010 import save
def main():
    r=c.r;intent=r.read(c.OUT/'build-intent.json');name=c.c.JOB
    arm=r.request('GET',r.BASE+'Microsoft.App/jobs/'+name+'?api-version=2024-03-01')
    cli=r.build.az('containerapp','job','show','--subscription',r.build.SUB,'-g',r.build.GROUP,'-n',name)
    def section(job):
        p=job['properties']
        return {'template':p['template'],'identity':job.get('identity'),'configuration':p['configuration'],'environmentId':p['environmentId']}
    differences=[]
    def compare(a,b,path):
        if isinstance(a,dict) and isinstance(b,dict):
            for k in sorted(set(a)|set(b)):
                if k not in a or k not in b:
                    v=a.get(k) if k in a else b.get(k)
                    differences.append({'path':path+'/'+k,'kind':'missing_key','present_in':'arm' if k in a else 'cli','null':v is None,'type':type(v).__name__})
                else:compare(a[k],b[k],path+'/'+k)
        elif isinstance(a,list) and isinstance(b,list):
            if len(a)!=len(b):differences.append({'path':path,'kind':'list_size'})
            else:
                for i,(x,y) in enumerate(zip(a,b)):compare(x,y,path+'/'+str(i))
        elif a!=b:differences.append({'path':path,'kind':'value','arm_type':type(a).__name__,'cli_type':type(b).__name__,'arm_null':a is None,'cli_null':b is None,'arm_empty':a=='','cli_empty':b==''})
    compare(section(arm),section(cli),'')
    result={'arm_matches_build':r.build.invariant(arm)==intent['before_jobs'][name]['settings_invariant_sha256'],
            'arm_invariant':r.build.invariant(arm),'cli_invariant':r.build.invariant(cli),'image_unchanged':arm['properties']['template']['containers'][0]['image']==cli['properties']['template']['containers'][0]['image']==intent['before_jobs'][name]['image'],
            'differences':differences,'last_modified':arm.get('systemData',{}).get('lastModifiedAt'),'changes_made':False}
    save(c.OUT,'binding-shape-diagnostic',result)
    print(json.dumps(result))
if __name__=='__main__':main()
