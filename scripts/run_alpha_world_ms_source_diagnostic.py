"""One-shot read-only diagnostic dispatch; leaves installed worker unchanged."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/".work/alpha-ms-azure-controller-runtime"))
from deploy_metadata_monitor import request,BASE
FOLDER=ROOT/".work/alpha-ms-v14-20261007"
FOLDER.mkdir(exist_ok=True)
path=BASE+"Microsoft.App/jobs/ibkrv2stg-onboard-render"
if len(sys.argv)==2 and sys.argv[1]=="start":
    intent=FOLDER/"diagnostic-intent.json"
    if intent.exists():raise ValueError("Already dispatched; inspect the existing operation")
    job=request("GET",path+"?api-version=2024-03-01")
    runs=request("GET",path+"/executions?api-version=2024-03-01").get("value",[])
    if any(r["properties"]["status"] in ("Running","Processing") for r in runs):raise ValueError("Worker busy")
    source=(ROOT/"scripts/diagnose_alpha_world_ms_observations.py").read_bytes()
    template=copy.deepcopy(job["properties"]["template"])
    template.pop("volumes",None)
    template["containers"][0]["command"]=["python"]
    template["containers"][0]["args"]=["-c","import base64;exec(compile(base64.b64decode('"+base64.b64encode(source).decode()+"'),'ms-readonly-date-diagnostic','exec'))"]
    with intent.open("x") as f:json.dump({"source_sha256":hashlib.sha256(source).hexdigest(),"job_unchanged":True},f)
    response=request("POST",path+"/start?api-version=2024-03-01",data=template)
    with (FOLDER/"diagnostic-dispatch.json").open("x") as f:json.dump({"execution":response["name"]},f)
    print(json.dumps({"execution":response["name"]}))
elif len(sys.argv)==2 and sys.argv[1]=="read":
    ack=json.loads((FOLDER/"diagnostic-dispatch.json").read_text())
    run=request("GET",path+"/executions/"+ack["execution"]+"?api-version=2024-03-01")
    status=run["properties"]["status"]
    print(json.dumps({"execution":ack["execution"],"status":status}))
    if status=="Succeeded":
        from azure.storage.blob import BlobServiceClient
        from azure.identity import AzureCliCredential
        service=BlobServiceClient("https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net",credential=AzureCliCredential())
        content=service.get_blob_client("internal-reports","review/alpha-world-ms/v14-20261007/source-date-diagnostic.json").download_blob().readall()
        with (FOLDER/"source-date-diagnostic.json").open("xb") as f:f.write(content)
else:raise ValueError("Specify start or read")
