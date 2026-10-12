"""Read back the exact private Azure batch; no storage or registry mutations."""
import hashlib,json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-candidate-reports-20261009-v4'
PREFIX='review/alpha-world-t/candidate-reports-20261009-v4/'

def sha(raw):return hashlib.sha256(raw).hexdigest()
def main():
 result=json.loads((FOLDER/'reports-result.json').read_bytes())
 if result['status']!='Succeeded' or len(result['records'])!=1 or not result['job_settings_unchanged']:raise ValueError('Successful private batch required')
 row=result['records'][0]
 if row['status']!='passed' or row['months']!=21:raise ValueError('Incomplete batch')
 service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential())
 def fetch(item):
  if not item['blob'].startswith(PREFIX) or '..' in item['blob']:raise ValueError('Blob outside accepted private batch')
  blob=service.get_blob_client('internal-reports',item['blob']);props=blob.get_blob_properties()
  if props.size>25*1024*1024:raise ValueError('Unexpected artifact size')
  raw=blob.download_blob().readall()
  if sha(raw)!=item['sha256']:raise ValueError('Private artifact hash differs')
  return raw
 proof_raw=fetch(row['proof']);proof=json.loads(proof_raw)
 intent=json.loads((FOLDER/'reports-intent.json').read_bytes())
 if proof['image']!=intent['image'] or proof['months']!=21:raise ValueError('Proof identity differs')
 output=FOLDER/'download';output.mkdir(exist_ok=True)
 if (output/'verification.json').exists():
  if (output/'verification.json').read_bytes()!=proof_raw:raise ValueError('Local proof drift')
 else:
  with (output/'verification.json').open('xb') as stream:stream.write(proof_raw)
 def download(item):
  name=Path(item['blob']).name;path=output/name
  if path.exists():
   if sha(path.read_bytes())!=item['sha256']:raise ValueError('Local file drift')
  else:
   raw=fetch(item)
   with path.open('xb') as stream:stream.write(raw)
  return {'name':name,'sha256':item['sha256']}
 artifacts=[item[kind] for item in proof['items'] for kind in ('factsheet','presentation')]
 with ThreadPoolExecutor(max_workers=4) as executor:files=list(executor.map(download,artifacts))
 record={'status':'passed','files':files,'count':len(files),'image':proof['image'],
  'proof_sha256':sha(proof_raw),'reports_registered':False,'reports_released':False}
 with (FOLDER/'download-verified.json').open('x',encoding='utf-8') as stream:json.dump(record,stream,indent=2)
 print(json.dumps({'status':'passed','downloaded_files':len(files),'remote_writes':0}))

if __name__=='__main__':main()
