"""Build-only full Python/Node replacement, retaining installed app and packages."""
import hashlib,json,shutil,sys
from pathlib import Path
import release_upgrade_workflow_security_20261010 as release
import release_platform_access_correction_20261009 as helper

ROOT=release.ROOT;OUT=ROOT/'.work/platform-upgrade-20261010-stage25'
TARGETS={'historical':{'job':'ibkrv2stg-historical-reports','digest':'8f3a771c67ce4dc5f98aa425f5e6bf4f1b77981447538ed2d165df25b774aa64',
    'entry':'generate_historical_month_end_batch.py','tests':20,'test_files':['scripts/test_upgrade_historical_contract_20261010.py'],
    'missing_environment_error':'The historical reporting batch may run only in staging'},
    'legacypresentation':{'job':'ibkrv2stg-presentation-v23-hist','repository':'ibkr-portfolio','digest':'0a88ba6bfb300601ea751a0b595fe4c6d06e45ee7c6e492478b98d80ee5afddb',
    'entry':'upgrade_historical_presentations_v23.py','tests':20,'test_files':['scripts/test_upgrade_legacy_presentation_contract_20261010.py'],
    'missing_environment_error':'Historical v23 upgrade may run only in staging'},
    'inbox':{'job':'ibkrv2stg-inbox-acceptance','jobs':['ibkrv2stg-inbox-acceptance','ibkrv2stg-inbox-acceptance-r2'],
    'digest':'d0614cf32c524117bae1df447478b5f5fd85e253db45e742a9017d2db6084fe4','entry':'inline-inbox-acceptance','tests':20,
    'test_files':['scripts/test_upgrade_inbox_contract_20261010.py'],'missing_environment_error':'Exact tenant and database-only authorization required'}}
INLINE_SHA={'ibkrv2stg-inbox-acceptance':'977ed558013e3f4e7e8072c57f062252fd51725cc779c01243b9f4d0ad71921e',
    'ibkrv2stg-inbox-acceptance-r2':'c48813544690b487c41fba8df5660c2988a061239776663a44d1a42705dad0c1'}
ORIGINAL_SNAPSHOT=release.snapshot


def inbox_sources():
    from inspect_upgrade_owner_payloads_20261010 import decode
    result={};before={}
    for name in TARGETS['inbox']['jobs']:
        live=release.request('GET',release.rid('Microsoft.App/jobs',name)+release.API);c=live['properties']['template']['containers'][0]
        argv=c.get('command',[])+c.get('args',[])
        if live['properties']['configuration']['triggerType']!='Manual' or c['image']!=source_image('inbox') or '-c' not in argv:raise ValueError('Inbox definition changed')
        texts=decode(argv[-1])
        if len(texts)!=2 or hashlib.sha256(texts[-1].encode()).hexdigest()!=INLINE_SHA[name]:raise ValueError('Inbox payload changed')
        result[name]=texts[-1];before[name]={'image':c['image'],'settings_invariant_sha256':release.settings_invariant(live)}
    return result,before


def snapshot(target):return inbox_sources()[1] if target=='inbox' else ORIGINAL_SNAPSHOT(target)


def source_image(target):return 'ibkrv2stgauhecwc4itaoo.azurecr.io/'+TARGETS[target].get('repository','ibkr-platform')+'@sha256:'+TARGETS[target]['digest']


def prepare(target):
    spec=TARGETS[target];folder=OUT/(target+'-security-v1');context=folder/'context'
    if folder.exists():raise ValueError('Preserve existing build evidence')
    before=release.snapshot(target);(context/'tests').mkdir(parents=True)
    for name in spec['test_files']:shutil.copyfile(ROOT/name,context/'tests'/Path(name).name)
    inline={}
    if target=='inbox':
        inline,confirmed=inbox_sources()
        if confirmed!=before:raise ValueError('Inbox changed during capture')
        path=context/'tests/test_upgrade_inbox_contract_20261010.py';text=path.read_text(encoding='utf-8')
        marker='PAYLOADS = {}  # Injected verbatim approved live source by the isolated preparer.'
        if text.count(marker)!=1:raise ValueError('Test source changed')
        path.write_text(text.replace(marker,'PAYLOADS = '+repr(inline)),encoding='utf-8')
    (context/'support/ibkr_etl').mkdir(parents=True)
    (context/'support/ibkr_etl/__init__.py').touch()
    shutil.copyfile(ROOT/'src/ibkr_etl/minimal_runtime.py',context/'support/ibkr_etl/minimal_runtime.py')
    shutil.copyfile(ROOT/'scripts/assemble_minimal_report_runtime.py',context/'support/assemble.py')
    assets=ROOT/'.work/system-improvements-20261006/report-security-runtime-v1/context'
    for name in ('nodejs-keyring.kbx','SHASUMS256.txt.asc','node-v24.21.0-linux-x64.tar.xz'):shutil.copyfile(assets/name,context/name)
    qualifier=(ROOT/'scripts/qualify_upgrade_workflow_runtime_20261010.py').read_text(encoding='utf-8')
    marker="    before=manifest();baseline=Path('/qualification/tests/baseline-source.json')"
    if qualifier.count(marker)!=1:raise ValueError('Qualifier boundary changed')
    node="""    import subprocess
    node=subprocess.run(['node','--input-type=module','-e',"import {createRequire} from 'node:module';const r=createRequire('/app/scripts/render_monthly_presentation.mjs');const p=r.resolve('@oai/artifact-tool');const a=await import(p);if(!a.Presentation||!a.PresentationFile)throw Error('Renderer API missing');console.log(process.version)"],cwd='/app',capture_output=True,text=True,timeout=120)
    if node.returncode:raise ValueError('Native Node renderer import failed: '+node.stderr[-2000:])
    print('LEGACY_NODE_PROOF '+json.dumps({'stage':stage,'node':node.stdout.strip(),'renderer_imported':True,'report_generated':False}),flush=True)
"""
    qualifier=qualifier.replace(marker,node+marker)
    if target=='inbox':
        line="        module=runpy.run_path('/app/scripts/'+config['entry'],run_name='qualification_import')"
        if qualifier.count(line)!=1:raise ValueError('Entry import boundary changed')
        qualifier=qualifier.replace(line,"        module={'__name__':'qualification_import'};exec(compile(config['inline_source'],'<preserved-inline>','exec'),module)")
        qualifier=qualifier.replace('except RuntimeError as exc:','except (RuntimeError,ValueError) as exc:')
    (context/'qualify.py').write_text(qualifier,encoding='utf-8')
    config={k:spec[k] for k in ('entry','tests','missing_environment_error')}
    if inline:config['inline_source']=inline[spec['job']]
    helper.save(context/'config.json',config)
    docker='''FROM python:3.12.15-slim-trixie@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe AS verified-node
RUN apt-get update && apt-get install --yes --no-install-recommends gpgv xz-utils
COPY nodejs-keyring.kbx SHASUMS256.txt.asc node-v24.21.0-linux-x64.tar.xz /tmp/
RUN gpgv --keyring /tmp/nodejs-keyring.kbx --output /tmp/verified-shasums /tmp/SHASUMS256.txt.asc && cd /tmp && sha256sum --check --ignore-missing verified-shasums && mkdir /tmp/node-bin && tar -xJf node-v24.21.0-linux-x64.tar.xz --strip-components=2 -C /tmp/node-bin node-v24.21.0-linux-x64/bin/node

FROM {base} AS installed
COPY --chown=10001:10001 tests/ /qualification/tests/
COPY --chown=10001:10001 qualify.py config.json /qualification/
USER 0:0
RUN ["python","-c","from pathlib import Path;Path('/qualification/src').symlink_to('/app/src',target_is_directory=True);Path('/qualification/migrations').symlink_to('/app/migrations',target_is_directory=True);Path('/qualification/scripts').symlink_to('/app/scripts',target_is_directory=True)"]
USER 10001:10001
ENV PYTHONPATH=/app/src:/app/scripts PYTHONDONTWRITEBYTECODE=1
RUN ["python","/qualification/qualify.py","baseline"]

FROM python:3.12.15-slim-trixie@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe AS builder
RUN apt-get update && apt-get dist-upgrade -y && apt-get install --yes --no-install-recommends libstdc++6 && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libsqlite3-0)" ge 3.46.1-7+deb13u2 && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libssl3t64)" ge 3.5.7-1~deb13u3 && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' liblzma5)" ge 5.8.1-1+deb13u2
RUN ["python","-c","from pathlib import Path;import shutil;p=Path('/usr/local/lib/python3.12/site-packages');assert p.resolve()==p and p.is_dir();shutil.rmtree(p)"]
COPY --from=installed /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=installed /usr/local/bin/ibkr-etl /usr/local/bin/ibkr-etl
COPY --from=installed /app/ /app/
COPY --from=verified-node /tmp/node-bin/node /usr/local/bin/node
COPY support/ /build-support/
ENV PYTHONPATH=/build-support PYTHONDONTWRITEBYTECODE=1
RUN ["python","-m","pip","check"]
RUN ["python","/build-support/assemble.py"]
RUN ["python","-c","from pathlib import Path;import ssl;v=ssl.get_default_verify_paths();f=Path(v.openssl_cafile);d=Path(v.openssl_capath);assert str(f) in ('/usr/lib/ssl/cert.pem','/usr/local/ssl/cert.pem') and d==f.parent/'certs';p=Path('/runtime')/f.parent.relative_to('/');p.mkdir(parents=True,exist_ok=True);assert not (p/'cert.pem').exists();(p/'cert.pem').symlink_to('/etc/ssl/certs/ca-certificates.crt');assert not (p/'certs').exists();(p/'certs').symlink_to('/etc/ssl/certs')"]

FROM scratch
COPY --from=builder /runtime/ /
COPY --from=installed --chown=10001:10001 /qualification/ /qualification/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 IBKR_PROJECT_ROOT=/app PYTHONPATH=/app/src:/app/scripts LD_LIBRARY_PATH=/usr/local/lib PATH=/usr/local/bin
WORKDIR /app
RUN ["python","-c","from pathlib import Path;import os;p=Path('/app');os.chown(p,10001,10001);[os.chown(x,10001,10001,follow_symlinks=False) for x in p.rglob('*')]"]
USER 10001:10001
RUN ["python","/qualification/qualify.py","candidate"]
CMD ["python","scripts/{entry}"]
'''.replace('{base}',release.source_image(target)).replace('{entry}',spec['entry'])
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    for p in context.rglob('*.py'):compile(p.read_bytes(),str(p),'exec')
    helper.save(folder/'source-intent.json',{'tag':'ibkr-platform:'+target+'-security-20261010-v1','base_image':release.source_image(target),
        'files_sha256':helper.manifest(context),'before_jobs':before,'scope':'Full Node/Python runtime security only; entire installed application/packages/templates retained; no reports, financial data or approvals changed.'})
    helper.save(folder/'local-qualification.json',{'status':'syntax_only_native_pending','tests_run':0,'source_intent_sha256':helper.sha(folder/'source-intent.json')})
    print('Prepared source-preserving full runtime candidate')


def main(target,stage):
    release.OUT=OUT;release.TARGETS=TARGETS;release.source_image=source_image;release.snapshot=snapshot
    if stage=='prepare':return prepare(target)
    if stage=='scan':
        import scan_ms_presentation_worker_candidate as scanner
        folder=OUT/(target+'-security-v1')
        def unchanged(path):
            if path!=folder.resolve() or release.snapshot(target)!=json.loads((folder/'source-intent.json').read_bytes())['before_jobs']:raise ValueError('Installed binding changed')
            native=json.loads((folder/'native-build-qualified.json').read_bytes())
            log=folder/'native-build.log'
            if native['native_log_sha256']!=helper.sha(log):raise ValueError('Native log differs')
            nodes=[json.loads(line.split('LEGACY_NODE_PROOF ',1)[1]) for line in log.read_text(encoding='utf-8').splitlines() if line.startswith('LEGACY_NODE_PROOF ')]
            if len(nodes)!=2 or {p['stage'] for p in nodes}!={'baseline','candidate'} or any(p.get('renderer_imported') is not True or p.get('report_generated') is not False for p in nodes):raise ValueError('Native renderer import proofs incomplete')
            if next(p for p in nodes if p['stage']=='candidate')['node']!='v24.21.0':raise ValueError('Unqualified Node runtime')
        scanner.jobs_unchanged=unchanged
        sys.argv=[sys.argv[0],'--folder',str(folder)];return scanner.main()
    if stage not in ('build','inspect','logs'):raise ValueError('Build only; deployment prohibited here')
    return release.main(target,stage)


if __name__=='__main__':main(*sys.argv[1:])
