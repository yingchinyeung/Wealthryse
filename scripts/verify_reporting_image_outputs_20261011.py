"""Exact private downloads and full-deck comparison to native-reviewed reports."""
import hashlib,json,re,zipfile
from pathlib import Path
from xml.etree import ElementTree as E
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261011-stage34/image-review-v1'
A='{http://schemas.openxmlformats.org/drawingml/2006/main}'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def require(ok,message):
    if not ok:raise ValueError(message)
def main():
    require(not OUT.exists(),'Preserve earlier review')
    trial=protected(OUT.parent/'image-qa-v1/result.json');require(trial['passed'],'Passing installed-image QA required')
    old=protected(ROOT/'.work/platform-upgrade-20261011-stage33/artifact-review-v2/downloads.json')
    ms=protected(ROOT/'.work/platform-upgrade-20261011-stage33/artifact-review-ms-note-v1/downloads.json')
    refs={(r['subject'],r['report_type']):r for r in old['artifacts']}
    refs.update({(r['subject'],r['report_type']):r for r in ms['artifacts']})
    for r in refs.values():require(sha(Path(r['file']))==r['sha256'],'Reviewed source changed')
    save(OUT,'intent',{'trial':trial['protected_receipt'],'image':trial['image'],'scope':'Read-only private ten-file download, compare native-reviewed content; no release or approval'})
    outputs=[]
    with BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()) as service:
        for subject in trial['proof']['subjects']:
            sid=subject['subject']
            for artifact in subject['artifacts']:
                kind=artifact['report_type'];container,name=artifact['output_reference'].split('/',1)
                ext='pdf' if kind=='factsheet' else 'pptx'
                require(container=='upgrade-g04-reports-20261010' and name=='candidate-stage34-image-qa-v1/'+sid+'/'+('factsheet.pdf' if kind=='factsheet' else 'presentation.pptx'),'Outside exact QA outputs')
                blob=service.get_blob_client(container,name);require(blob.get_blob_properties().size<64*1024*1024,'Oversized QA file')
                content=blob.download_blob().readall();require(hashlib.sha256(content).hexdigest()==artifact['sha256'],'Download hash mismatch')
                path=OUT/(sid+'.'+ext)
                with path.open('xb') as f:f.write(content)
                ref=refs[(sid,kind)]
                if kind=='factsheet':require(sha(path)==ref['sha256'],'Factsheet differs from reviewed output: '+sid)
                else:
                    with zipfile.ZipFile(ref['file']) as left,zipfile.ZipFile(path) as right:
                        charts=lambda z:sorted(n for n in z.namelist() if re.fullmatch(r'ppt/(?:slides/)?charts/chart\d+\.xml',n))
                        require(charts(left)==charts(right) and len(charts(left))==6,'Chart inventory differs')
                        for name in charts(left):require(E.tostring(E.fromstring(left.read(name)))==E.tostring(E.fromstring(right.read(name))),'Chart differs: '+sid)
                        media=lambda z:{n:hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.startswith('ppt/media/')}
                        require(media(left)==media(right),'Visual media changed')
                        for i in range(1,21):
                            trees=[E.fromstring(z.read(f'ppt/slides/slide{i}.xml')) for z in (left,right)]
                            for tag in ('t','tbl','xfrm','pPr','rPr','endParaRPr','solidFill','ln','latin'):
                                require([E.tostring(x) for x in trees[0].iter(A+tag)]==[E.tostring(x) for x in trees[1].iter(A+tag)],f'{sid} slide {i} {tag} differs')
                outputs.append({'subject':sid,'report_type':kind,'file':str(path),'sha256':artifact['sha256'],'reviewed_reference_sha256':ref['sha256'],'content_matches_native_review':True})
    require(len(outputs)==10,'Incomplete output comparison')
    save(OUT,'result',{'passed':True,'image':trial['image'],'artifacts':outputs,'factsheets_byte_identical':5,'slides_compared':100,'charts_compared':30,
        'native_review_carried_forward_by_content_comparison':True,'deployed':False,'full_upgrade_complete':False,'real_viewer_sign_in':'UNVERIFIED','observation_14_day':'UNVERIFIED'})
    print(json.dumps({'passed':True,'factsheets_identical':5,'slides_compared':100,'charts_compared':30,'deployed':False}))
if __name__=='__main__':main()
