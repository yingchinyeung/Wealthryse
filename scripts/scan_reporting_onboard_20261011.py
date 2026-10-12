"""Exact onboarding-render candidate scan with strict ARM and CLI binding checks."""
import sys
import release_reporting_onboard_20261011 as release
import scan_ms_presentation_worker_candidate as scan
from scan_reporting_full_nav_20261011 import canonical_cli
from control_upgrade_readers_20261010 import save

def jobs_unchanged(folder):
    if folder.resolve()!=release.OUT.resolve():raise ValueError('Exact onboarding candidate only')
    expected=release.r.read(folder/'build-intent.json')['before_jobs']
    if set(expected)!={'ibkrv2stg-onboard-render'}:raise ValueError('Exact onboarding job required')
    for name,binding in expected.items():
        arm=release.r.request('GET',release.r.BASE+'Microsoft.App/jobs/'+name+'?api-version=2024-03-01')
        cli=scan.build.az('containerapp','job','show','--subscription',scan.build.SUB,'-g',scan.build.GROUP,'-n',name)
        for job in (arm,canonical_cli(cli)):
            if job['properties']['template']['containers'][0]['image']!=binding['image'] or scan.build.invariant(job)!=binding['settings_invariant_sha256']:raise ValueError('Actual onboarding worker changed')
    if not (folder/'binding-shape-acceptance.json').exists():save(folder,'binding-shape-acceptance',{'passed':True,'matching_api_version':'2024-03-01','normalized_cli_defaults':{'identitySettings':[],'imageType':'ContainerImage'},'arm_and_normalized_cli_match_frozen_build':True,'job_changes':False,'nondefault_changes_rejected':True})

if __name__=='__main__':
    if len(sys.argv)!=1:raise ValueError('Exact candidate only')
    sys.argv=[sys.argv[0],'--folder',str(release.OUT)]
    scan.jobs_unchanged=jobs_unchanged
    scan.main()
