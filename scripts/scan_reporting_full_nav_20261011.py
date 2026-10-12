"""Same exact image scan, comparing ARM and CLI with reviewed default fields."""
import copy,json,sys
import scan_ms_presentation_worker_candidate as scan
import release_reporting_full_nav_20261011 as release
from control_upgrade_readers_20261010 import save
def canonical_cli(job):
    value=copy.deepcopy(job)
    config=value['properties']['configuration']
    if config.get('identitySettings')!=[]:raise ValueError('Nonempty or missing CLI identity settings')
    del config['identitySettings']
    for container in value['properties']['template']['containers']:
        if container.get('imageType')!='ContainerImage':raise ValueError('Unexpected CLI image type')
        del container['imageType']
    return value
def jobs_unchanged(folder):
    if folder.resolve()!=release.OUT.resolve():raise ValueError('Exact v4 candidate only')
    expected=release.r.read(folder/'build-intent.json')['before_jobs']
    if set(expected)!={'ibkrv2stg-month-end-factsheet'}:raise ValueError('Exact reporting job required')
    for name,binding in expected.items():
        arm=release.r.request('GET',release.r.BASE+'Microsoft.App/jobs/'+name+'?api-version=2024-03-01')
        cli=scan.build.az('containerapp','job','show','--subscription',scan.build.SUB,'-g',scan.build.GROUP,'-n',name)
        normalized=canonical_cli(cli)
        for job in (arm,normalized):
            if job['properties']['template']['containers'][0]['image']!=binding['image'] or scan.build.invariant(job)!=binding['settings_invariant_sha256']:raise ValueError('Actual reporting worker changed')
    path=folder/'binding-shape-acceptance.json'
    if not path.exists():save(folder,'binding-shape-acceptance',{'passed':True,'matching_api_version':'2024-03-01','normalized_cli_defaults':{'identitySettings':[],'imageType':'ContainerImage'},'arm_and_normalized_cli_match_frozen_build':True,'job_changes':False,'nondefault_changes_rejected':True})
if __name__=='__main__':
    if sys.argv[1:]!=['--folder',str(release.OUT.relative_to(release.ROOT))] and sys.argv[1:]!=['--folder',str(release.OUT.relative_to(release.ROOT)).replace('\\','/')]:raise ValueError('Exact v4 scan folder required')
    scan.jobs_unchanged=jobs_unchanged
    scan.main()
