"""Package verified installed Azure outputs without rewriting the PDFs."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import deploy_alpha_ms_copy as deployment


def main():
    completion=deployment.previous.read('completion')
    proof=deployment.previous.read('installed-probe-qualified')['proof']
    folder=deployment.OUTPUT
    archive=ROOT/'output/pdf/AlphaWorld_MS_Azure_Copy_v14r12_All_21_Months.zip'
    if archive.exists(): raise ValueError('Refusing to replace a prior archive')
    expected={Path(row['blob_name']).name:row['sha256'] for row in proof['artifacts']}
    assert len(expected)==21 and len(list(folder.glob('*.pdf')))==21
    for name,digest in expected.items():
        assert hashlib.sha256((folder/name).read_bytes()).hexdigest()==digest
    record={'status':'azure_rendered_and_installed','completion':completion,
            'cloud_verification':proof,'all_pdf_hashes_verified':True}
    (folder/'deployment-verification.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as output:
        for name in sorted(expected): output.write(folder/name,arcname=name)
        output.write(folder/'manifest.json',arcname='manifest.json')
        output.write(folder/'deployment-verification.json',arcname='deployment-verification.json')
    with zipfile.ZipFile(archive) as output:
        assert output.testzip() is None
        for name,digest in expected.items():assert hashlib.sha256(output.read(name)).hexdigest()==digest
    result={'archive':str(archive),'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
            'monthly_pdfs':21,'pages':42,'matches_installed_cloud_outputs':True}
    with (folder/'archive-verification.json').open('x',encoding='utf-8') as handle:json.dump(result,handle,indent=2)
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
