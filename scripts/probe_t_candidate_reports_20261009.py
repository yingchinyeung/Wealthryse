"""Render frozen, previously reconciled T inputs; private output only, no DB."""
import hashlib,json,os,sys,tempfile
from pathlib import Path

sys.path[:0]=['/app/src','/app/scripts']
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient,ContentSettings
from ibkr_etl.monthly_artifacts import render_factsheet
from ibkr_etl.alpha_world_t_presentation import build_model
from render_onboarding_historical_reports import _render_presentation


def sha(raw):return hashlib.sha256(raw).hexdigest()


def main():
    binding=json.loads(os.environ['T_REPORT_PROBE_BINDING'])
    for name,digest in binding['installed_files_sha256'].items():
        if sha((Path('/app')/name).read_bytes())!=digest:raise ValueError('Candidate source drift: '+name)
    fixtures=Path('/app/t-runtime-tests/t-release-fixtures/inputs')
    paths=sorted(fixtures.glob('*.json'))
    if [p.stem for p in paths]!=binding['periods']:raise ValueError('Frozen month coverage differs')
    template=Path('/app/templates/U16088571/approved-august-2026-20-slide-v24.pptx')
    if sha(template.read_bytes())!='4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191':
        raise ValueError('20-slide template differs')
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    def put(name,data,mime):
        blob=binding['prefix']+name
        client=service.get_blob_client('internal-reports',blob)
        client.upload_blob(data,overwrite=False,content_settings=ContentSettings(content_type=mime),
            metadata={'sha256':sha(data),'subject_id':'alpha-world-t'})
        if client.download_blob().readall()!=data:raise ValueError('Private artifact readback differs')
        return {'blob':blob,'sha256':sha(data)}
    work=Path('/app/.work');work.mkdir(exist_ok=True)
    items=[]
    with tempfile.TemporaryDirectory(prefix='t-candidate-reports-',dir=work) as directory:
        folder=Path(directory)
        for path in paths:
            period=path.stem;snapshot=json.loads(path.read_bytes())
            if sha(path.read_bytes())!=binding['input_sha256'][period]:raise ValueError('Input hash differs')
            model=build_model(snapshot)
            pdf=folder/f'AlphaWorld_T_Factsheet_{period}.pdf'
            pptx=folder/f'AlphaWorld_T_Presentation_{period}.pptx'
            pdf_check=render_factsheet(snapshot,template_path=Path('/app/templates/alpha-world-t/owner_template_20261008.pdf'),output_path=pdf)
            ppt_check=_render_presentation(template=template,snapshot=path,output=pptx,
                period_end=period,subject_id='alpha-world-t',expected_strategy='iStrategy: Alpha World T')
            items.append({'period_end':period,'input_sha256':sha(path.read_bytes()),
                'factsheet':put('outputs/'+pdf.name,pdf.read_bytes(),'application/pdf'),
                'presentation':put('outputs/'+pptx.name,pptx.read_bytes(),
                    'application/vnd.openxmlformats-officedocument.presentationml.presentation'),
                'pdf_validation':pdf_check['pdf_accuracy_validation'],
                'presentation_validation':ppt_check,'risk_metrics':model['risk']})
            print('T_CANDIDATE_REPORT_MONTH '+json.dumps({'period_end':period,'status':'passed'}),flush=True)
    proof={'status':'passed','image':binding['image'],'months':len(items),'items':items,
        'database_connected':False,'source_data_changed':False,'report_registration_changed':False,
        'reports_released':False,'viewer_access_changed':False,'visual_acceptance':'pending',
        'scope':'Private rendering of source-bound frozen T inputs, not registered production snapshots'}
    receipt=put('verification.json',json.dumps(proof,sort_keys=True,allow_nan=False).encode(),'application/json')
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'passed','months':len(items),'proof':receipt,
        'database_writes':0,'reports_released':False}),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        # Renderer errors contain local paths; never print environment or token-bearing service errors.
        message=str(error)[:400] if isinstance(error,(ValueError,RuntimeError)) else 'Service details redacted'
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'failed','error_class':type(error).__name__,
            'message':message,'database_writes':0,'reports_released':False}),flush=True)
        raise SystemExit(1)
