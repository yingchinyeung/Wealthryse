"""Read-only source-file inventory with the existing reporting identity."""
import json
import os
from collections import Counter
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from ibkr_etl.parser import parse_flex_xml

service = BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'], credential=DefaultAzureCredential())
result = {'mode': 'read_only', 'source_data_changed': False, 'containers': []}
for name in ('approved-reports', 'onboarding-intake'):
    entry = {'container': name, 'status': 'unsearched'}
    try:
        blobs = list(service.get_container_client(name).list_blobs(include=['snapshots', 'versions', 'deleted']))
        entry['count'] = len(blobs)
        entry['extensions'] = dict(Counter(b.name.rsplit('.', 1)[-1].lower() for b in blobs))
        entry['xml_sources'] = []
        entry['non_xml_source_candidates'] = []
        for blob in blobs:
            if blob.name.lower().endswith('.xml'):
                raw = service.get_blob_client(name, blob.name, version_id=blob.get('version_id'),
                    snapshot=blob.get('snapshot')).download_blob().readall()
                report = parse_flex_xml(raw)
                found = [r for r in report.rows if r.account_id in ('U16088571','U16088606') and
                         r.report_date in ('2026-08-27','2026-08-28') and r.record_type.lower() == 'openposition']
                entry['xml_sources'].append({'blob': blob.name, 'target_position_rows': len(found)})
            elif blob.name.lower().endswith(('.sqlite', '.db', '.zip', '.csv', '.gz')):
                entry['non_xml_source_candidates'].append(blob.name)
        entry['status'] = 'listed_and_xml_searched'
    except Exception as e:
        entry['status'] = 'unavailable'
        entry['error'] = type(e).__name__
        entry['http_status'] = getattr(e, 'status_code', None)
    result['containers'].append(entry)
print('MS_DOCUMENT_SOURCE_SEARCH ' + json.dumps(result), flush=True)
