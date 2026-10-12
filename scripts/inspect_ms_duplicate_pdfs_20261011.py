"""Temporary read-only PDF comparison; no source PDFs saved or modified."""
import difflib,hashlib,io,json,subprocess
from pathlib import Path
from pypdf import PdfReader
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'tmp/pdfs/ms-release-duplicates-20261011-v1'
POPPLER=Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe')

def main():
    if OUT.exists():raise ValueError('Preserve prior temporary review')
    rows=protected(ROOT/'.work/platform-upgrade-20261011-stage42/duplicates-v1/result.json')['proof']['duplicate_registrations']
    if [r['artifact_release_id'] for r in rows]!=[55,57]:raise ValueError('Exact reviewed pair required')
    OUT.mkdir(parents=True);texts=[]
    service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential())
    for row in rows:
        client=service.get_blob_client(row['container_name'],row['blob_name']);prop=client.get_blob_properties()
        if prop.size>10*1024*1024:raise ValueError('Bounded file required')
        content=client.download_blob().readall()
        if hashlib.sha256(content).hexdigest()!=row['artifact_sha256'] or client.get_blob_properties().etag!=prop.etag:raise ValueError('File drift')
        pdf=PdfReader(io.BytesIO(content))
        if len(pdf.pages)!=2:raise ValueError('Two-page factsheet expected')
        texts.append([' '.join(page.extract_text().split()) for page in pdf.pages])
        subprocess.run([str(POPPLER),'-f','1','-l','2','-r','110','-png','-',str(OUT/str(row['artifact_release_id']))],input=content,check=True,capture_output=True,timeout=120)
        del content
    for i in range(2):
        a,b=texts[0][i].split(),texts[1][i].split();changes=[]
        for op,x,y,j,k in difflib.SequenceMatcher(a=a,b=b,autojunk=False).get_opcodes():
            if op!='equal':changes.append({'operation':op,'older':' '.join(a[x:y]),'newer':' '.join(b[j:k])})
        print(json.dumps({'page':i+1,'normalized_text_equal':a==b,'changes':changes}))
    print(json.dumps({'temporary_renders':[str(p) for p in sorted(OUT.glob('*.png'))],'source_pdfs_saved':False}))

if __name__=='__main__':main()
