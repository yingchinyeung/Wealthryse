"""Close the 13-reference scan investigation before any image deployment."""
from datetime import datetime,timezone
import hashlib,json
from pathlib import Path
from audit_upgrade_image_coverage_20261009 import inventory
from control_upgrade_legacy_recovery_probe_20261009 import protected
from rehearse_ms_worker_image_rollback import restore_protected
from run_specialist_gateway_read_verification import retain
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261010-stage19'


def main():
    if (OUT/'remaining-scan-closeout.json').exists():raise ValueError('Preserve sealed scans')
    intent=protected(OUT/'intent.json');before=inventory()
    if before!=intent['templates']:raise ValueError('Runtime changed before scan closure')
    checkpoint=protected(ROOT/'.work/platform-upgrade-20261010-stage18/image-coverage-v1.json')
    found={}
    for folder in [*sorted((OUT/'remaining-security-v1').glob('image-scan-*')), *sorted(OUT.glob('reference-*-v1'))]:
        path=folder/'verification.json'
        if not path.exists():path=folder/'unqualified-verification.json'
        value=json.loads(path.read_bytes())
        receipt=value['protected_receipt'];remote,_=restore_protected(receipt)
        record=value['record'] if 'record' in value else {k:v for k,v in value.items() if k!='protected_receipt'}
        if remote!=record:raise ValueError('Protected image scan differs')
        declared=record.get('declared_reference',record['image'])
        if declared in found:raise ValueError('Duplicate scan family')
        found[declared]={'declared_reference':declared,'resolved_image':record['image'],
            'assessment_status':record['assessment']['status'],'counts':record['assessment']['counts'],
            'mutable_reference':record.get('mutable_reference',False),'receipt':receipt,'path':str(path.relative_to(ROOT))}
    expected={i['image'] for i in checkpoint['images'] if not i['protected_scan_verified']}
    if set(found)!=expected or len(found)!=13:raise ValueError('Incomplete remaining-reference evidence')
    for i in checkpoint['images']:
        if i['image'] in found:found[i['image']]['jobs']=i['templates']
    if inventory()!=before:raise ValueError('Inventory changed during closure')
    record={'sealed_at':datetime.now(timezone.utc).isoformat(),'references_reviewed':13,
        'jobs_covered':sum(len(i['jobs']) for i in found.values()),'images':list(found.values()),
        'blocked_complete_assessments':sum(i['assessment_status']=='blocked' for i in found.values()),
        'unqualified_coverage_assessments':sum(i['assessment_status']=='unqualified' for i in found.values()),
        'passing_assessments':sum(i['assessment_status']=='pass' for i in found.values()),
        'templates':before,'image_bindings_unchanged':True,'jobs_started':0,'no_runtime_changes':True,
        'full_upgrade_complete':False,'limitations':['Coverage failures remain unqualified; OS/Python findings are retained without waiving required coverage.',
            'Mutable tags were resolved again during reconciliation, not converted into immutable job declarations.',
            'These scans identify package vulnerabilities; they are not evidence of exploitation.']}
    receipt=retain(record,'upgrade-stage19-remaining-scan-closeout')
    with (OUT/'remaining-scan-closeout.json').open('x',encoding='utf-8') as f:json.dump({**record,'protected_receipt':receipt},f,indent=2)
    print(json.dumps({k:v for k,v in record.items() if k not in ('images','templates')}))


if __name__=='__main__':main()
