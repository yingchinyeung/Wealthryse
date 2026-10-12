"""Read-only exact-image scan coverage for all current RG application templates."""
import json,subprocess,collections
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlsplit
from deploy_viewer_private_admin_20261008 import request
from deploy_metadata_monitor import BASE
from rehearse_ms_worker_image_rollback import restore_protected
from run_specialist_gateway_read_verification import retain
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261009-stage11/image-coverage-v1.json'
def inventory():
    rows=[]
    for kind in ('containerApps','jobs'):
        path=BASE+'Microsoft.App/'+kind;url=path+'?api-version=2024-03-01'
        for _ in range(20):
            data=request('GET',url)
            for row in data['value']:
                for c in row['properties']['template']['containers']:
                    rows.append({'name':row['name'],'kind':kind,'container':c['name'],'image':c['image'],'trigger':row['properties'].get('configuration',{}).get('triggerType'),'provisioning_state':row['properties'].get('provisioningState')})
            link=data.get('nextLink')
            if not link:break
            p=urlsplit(link)
            if p.scheme!='https' or p.netloc!='management.azure.com' or p.path.lower()!=path.lower():raise ValueError('Unexpected inventory pagination')
            url=p.path+'?'+p.query
        else:raise ValueError('Inventory exceeds bounded pagination')
    return sorted(rows,key=lambda r:(r['kind'],r['name'],r['container']))
def main():
    if OUT.exists():raise ValueError('Preserve existing coverage evidence')
    rows=inventory();images={r['image'] for r in rows};found=collections.defaultdict(list)
    paths=subprocess.run(['rg','--files','--hidden','.work','-g','scan-completed.json','-g','!node_modules'],cwd=ROOT,text=True,capture_output=True,check=True).stdout.splitlines()
    for name in paths:
        p=ROOT/name;record=json.loads(p.read_bytes())
        if record.get('image') in images:found[record['image']].append((p,record))
    checks=[]
    for image in sorted(images):
        candidates=sorted(found.get(image,[]),key=lambda pair:pair[0].stat().st_mtime,reverse=True);item={'image':image,'templates':[r['name'] for r in rows if r['image']==image],'protected_scan_verified':False}
        for path,record in candidates:
            receipt=path.with_name('scan-protected-receipt.json')
            if not receipt.exists():continue
            remote,_=restore_protected(json.loads(receipt.read_bytes()))
            if remote!=record:raise ValueError('Scan receipt differs for '+image)
            assessment=record['assessment']
            item.update(protected_scan_verified=True,scan_path=str(path.relative_to(ROOT)),status=assessment['status'],counts=assessment['counts'],scanner_version=assessment.get('scanner_version'),scan_receipt=json.loads(receipt.read_bytes()));break
        checks.append(item);print(json.dumps({'image':image,'protected_scan_verified':item['protected_scan_verified'],'templates':len(item['templates'])}),flush=True)
    if inventory()!=rows:raise ValueError('Runtime inventory changed during scan audit')
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'definitions':len(rows),'unique_images':len(images),'templates':rows,'images':checks,'missing_protected_scan_images':sum(not i['protected_scan_verified'] for i in checks),'scope':'Current application/job template image coverage, including manual jobs; not a fresh rescan, registry contents audit or acceptance of unscanned images.'}
    with OUT.open('x',encoding='utf-8') as f:json.dump({**result,'protected_receipt':retain(result,'upgrade-all-runtime-scan-coverage')},f,indent=2)
    print(json.dumps({k:result[k] for k in ('definitions','unique_images','missing_protected_scan_images')}))
if __name__=='__main__':main()
