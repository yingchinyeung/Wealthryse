"""Read-only resource-group archive search for two exact IBKR position dates.

Writes new local diagnostic evidence only. Does not request new Flex data,
change Azure resources, replay ingestion, or modify any retained source.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile
import gzip
import csv
import sqlite3
import threading
import time
import base64
import copy
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.work/alpha-ms-azure-controller-runtime'))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'src'))
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from deploy_metadata_monitor import request, BASE
from ibkr_etl.parser import parse_flex_xml

FOLDER = ROOT / '.work/alpha-ms-source-search-20261007'
ACCOUNTS = ('U16088571', 'U16088606')
DATES = ('2026-08-27', '2026-08-28')
class CachedReadCredential:
    """Avoid one CLI subprocess per archive object; never persist tokens."""
    def __init__(self):
        self.credential = AzureCliCredential(process_timeout=60)
        self.tokens = {}
        self.lock = threading.Lock()

    def get_token(self, *scopes, **kwargs):
        with self.lock:
            token = self.tokens.get(scopes)
            if token is None or token.expires_on < time.time() + 120:
                token = self.credential.get_token(*scopes, **kwargs)
                self.tokens[scopes] = token
            return token


CREDENTIAL = CachedReadCredential()


def save(name, data):
    FOLDER.mkdir(exist_ok=True)
    with (FOLDER / (name + '.json')).open('x', encoding='utf-8') as f:
        json.dump(data, f, default=str, indent=2, sort_keys=True)


def read(name):
    return json.loads((FOLDER / (name + '.json')).read_text(encoding='utf-8'))


def service(account):
    return BlobServiceClient('https://' + account + '.blob.core.windows.net', credential=CREDENTIAL,
                             retry_total=1, connection_timeout=25, read_timeout=60)


def inventory():
    sub = BASE.split('/resourceGroups/')[0]
    result = request('GET', sub + '/resourceGroups/rg-ibkr-database-v11/resources?api-version=2021-04-01')
    resources = result.get('value', [])
    while result.get('nextLink'):
        result = request('GET', result['nextLink'])
        resources.extend(result.get('value', []))
    save('resource-inventory', [{'name': r['name'], 'type': r['type'], 'id': r['id'],
                                'location': r.get('location')} for r in resources])
    storage = [r['name'] for r in resources if r['type'].lower() == 'microsoft.storage/storageaccounts']
    containers, errors = [], []
    for account in storage:
        try:
            containers.extend((account, c['name']) for c in service(account).list_containers())
        except Exception as e:
            errors.append({'account': account, 'phase': 'containers', 'error': type(e).__name__,
                           'status': getattr(e, 'status_code', None)})

    def inspect(target):
        account, container = target
        blobs = []
        try:
            for b in service(account).get_container_client(container).list_blobs(
                    include=['metadata', 'snapshots', 'versions', 'deleted']):
                blobs.append({'account': account, 'container': container, 'name': b.name,
                              'size': b.size, 'etag': b.etag, 'last_modified': str(b.last_modified),
                              'version_id': b.get('version_id'), 'snapshot': b.get('snapshot'),
                              'deleted': b.get('deleted', False), 'metadata': b.get('metadata', {}),
                              'content_type': b.content_settings.content_type})
            return {'account': account, 'container': container, 'blobs': blobs, 'status': 'listed'}
        except Exception as e:
            return {'account': account, 'container': container, 'blobs': blobs, 'status': 'incomplete',
                    'error': type(e).__name__, 'http_status': getattr(e, 'status_code', None)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        listings = list(pool.map(inspect, containers))
    save('blob-inventory', {'read_only': True, 'utc': datetime.now(timezone.utc).isoformat(),
                          'accounts': storage, 'errors': errors, 'containers': listings})
    print(json.dumps({'resources': len(resources), 'storage_accounts': storage, 'errors': errors,
                      'containers': [{'account': c['account'], 'container': c['container'],
                                      'count': len(c['blobs']), 'status': c['status'],
                                      'source_candidates': sum(candidate(b) for b in c['blobs'])}
                                     for c in listings]}), flush=True)


def candidate(blob):
    name = blob['name'].lower()
    # Enumerate every container; parse all XML regardless of upload date/name.
    # Compressed Flex archives and delimited position exports are included too.
    return (name.endswith(('.xml', '.xml.gz', '.gz', '.zip', '.csv', '.sqlite', '.db', '.bak'))
            or blob['container'] in ('raw-statements', 'ibkr-raw')
            or 'xml' in str(blob.get('content_type', '')).lower())


def inspect_xml(raw):
    parsed = parse_flex_xml(raw)
    rows = [r for r in parsed.rows if r.account_id in ACCOUNTS]
    positions = [r for r in rows if r.record_type.lower() == 'openposition']
    target = [r for r in positions if r.report_date in DATES]
    nav = [r for r in rows if r.report_date in DATES and r.record_type.lower() in
           ('equitysummarybyreportdateinbase', 'netassetvaluebyassetclass')]
    root = ET.fromstring(raw)
    statements = [{'account': x.attrib.get('accountId'), 'from': x.attrib.get('fromDate'),
                   'to': x.attrib.get('toDate')} for x in root.iter()
                  if x.tag.rsplit('}', 1)[-1] == 'FlexStatement' and x.attrib.get('accountId') in ACCOUNTS]
    dates = sorted({(r.account_id, r.report_date) for r in positions if r.report_date})
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'statement_ranges': statements,
            'position_dates': dates, 'target_position_count': len(target),
            'target_nav_count': len(nav),
            # Complete exact target rows retained privately as evidence if found.
            'target_positions': [{'account': r.account_id, 'date': r.report_date,
                                  'source_key': r.source_key, 'attributes': r.attributes} for r in target]}


def inspect_sqlite(raw):
    # Deserialize a local memory copy, then enforce read-only SQL. The archive
    # bytes and database on Azure are never changed.
    with sqlite3.connect(':memory:') as db:
        db.deserialize(raw)
        db.execute('PRAGMA query_only=ON')
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        queries = {
            'position_snapshots': "SELECT * FROM position_snapshots WHERE account_id IN (?,?) AND snapshot_date IN (?,?)",
            'flex_rows': "SELECT * FROM flex_rows WHERE account_id IN (?,?) AND report_date IN (?,?) AND lower(record_type)='openposition'",
            'nav_snapshots': "SELECT account_id,snapshot_date,count(*) AS rows FROM nav_snapshots WHERE account_id IN (?,?) AND snapshot_date IN (?,?) GROUP BY account_id,snapshot_date",
        }
        found = {}
        errors = {}
        for table, query in queries.items():
            if table in tables:
                try:
                    found[table] = [dict(r) for r in db.execute(query, ACCOUNTS + DATES)]
                except sqlite3.Error as e:
                    errors[table] = type(e).__name__
        return {'sha256': hashlib.sha256(raw).hexdigest(), 'tables_checked': list(found),
                'target_position_count': len(found.get('position_snapshots', [])) + len(found.get('flex_rows', [])),
                'target_nav_count': sum(r['rows'] for r in found.get('nav_snapshots', [])),
                'target_positions': found, 'schema_errors': errors}


def content_search():
    inventory_data = read('blob-inventory')
    blobs = [b for c in inventory_data['containers'] for b in c['blobs'] if candidate(b)]

    def inspect(blob):
        result = {'blob': blob, 'status': 'not_parsed', 'members': []}
        try:
            if blob.get('deleted'):
                result['status'] = 'deleted_not_readable_without_mutation'
                return result
            # Prevent pathological non-source code/build archives from expanding.
            if blob['size'] > 250_000_000:
                result['status'] = 'oversize_requires_targeted_review'
                return result
            client = service(blob['account']).get_blob_client(blob['container'], blob['name'],
                        version_id=blob.get('version_id'), snapshot=blob.get('snapshot'))
            raw = client.download_blob(max_concurrency=2).readall()
            result['sha256'] = hashlib.sha256(raw).hexdigest()
            name = blob['name'].lower()
            if name.endswith('.zip'):
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    members = [m for m in archive.infolist() if m.filename.lower().endswith('.xml')]
                    if sum(m.file_size for m in members) > 250_000_000:
                        result['status'] = 'oversize_archive_requires_targeted_review'
                        return result
                    for m in members:
                        result['members'].append({'name': m.filename, **inspect_xml(archive.read(m))})
                result['status'] = 'xml_archive_parsed' if members else 'no_xml_members'
            elif name.endswith('.gz'):
                unpacked = gzip.decompress(raw)
                result['members'] = [{'name': 'gzip-content', **inspect_xml(unpacked)}]
                result['status'] = 'parsed'
            elif raw.startswith(b'SQLite format 3\x00'):
                result['members'] = [{'name': 'sqlite-memory-copy', **inspect_sqlite(raw)}]
                result['status'] = 'sqlite_parsed'
            elif name.endswith('.csv'):
                records = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
                matched = [r for r in records if any(a in str(r) for a in ACCOUNTS) and
                           any(d.replace('-', '') in str(r).replace('-', '') for d in DATES)]
                result['status'] = 'csv_parsed'
                result['csv_target_rows'] = matched
            elif raw.lstrip().startswith(b'<'):
                result['members'] = [{'name': 'blob-content', **inspect_xml(raw)}]
                result['status'] = 'parsed'
            else:
                # Binary databases and CSVs need their own schema/date analysis.
                result['status'] = 'non_xml_requires_targeted_review'
                result['signature'] = raw[:16].hex()
        except Exception as e:
            result['status'] = 'error'
            result['error'] = type(e).__name__
            result['http_status'] = getattr(e, 'status_code', None)
        return result

    results = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for index, result in enumerate(pool.map(inspect, blobs), 1):
            results.append(result)
            if index % 100 == 0:
                print(json.dumps({'searched': index, 'candidates': len(blobs)}), flush=True)
    save('archive-content-search', {'read_only': True, 'accounts': ACCOUNTS, 'dates': DATES,
                                  'results': results})
    matches = [r for r in results if any(m['target_position_count'] for m in r['members'])]
    from collections import Counter
    print(json.dumps({'candidates': len(blobs), 'status_counts': dict(Counter(r['status'] for r in results)),
                      'matching_position_blobs': [r['blob']['account'] + '/' + r['blob']['container'] + '/' +
                                                 r['blob']['name'] for r in matches]}), flush=True)


def database_plan():
    resources = read('resource-inventory')
    servers = [r for r in resources if r['type'].lower() == 'microsoft.dbforpostgresql/flexibleservers']
    details = []
    for server in servers:
        info = request('GET', server['id'] + '?api-version=2024-08-01')
        from audit_azure_runtime import az
        try:
            databases = az('postgres', 'flexible-server', 'db', 'list', '-g', 'rg-ibkr-database-v11',
                           '-s', server['name'], '--query', '[].name')
        except Exception as e:
            databases = {'inspection_error': type(e).__name__}
        details.append({'server': server['name'], 'state': info['properties'].get('state'),
                        'databases': databases})
    jobs = []
    for name in ('ibkr-v11-daily', 'ibkrv2stg-bootstrap',
                 'ibkrv2stg-ownsql-root-20261005', 'ibkrv2stg-ownrtree2-20261006'):
        j = request('GET', BASE + 'Microsoft.App/jobs/' + name + '?api-version=2024-03-01')
        jobs.append({'job': name,
                     'env': [{'name': e['name'], 'secretRef': e.get('secretRef')} for e in
                             j['properties']['template']['containers'][0].get('env', [])],
                     'secret_refs': [{'name': s['name'], 'keyVaultUrl': s.get('keyVaultUrl')}
                                     for s in (j['properties']['configuration'].get('secrets') or [])]})
    save('database-plan', {'servers': details, 'jobs': jobs})
    print(json.dumps({'servers': details, 'jobs': jobs}), flush=True)


def inspect_csv(raw, filename):
    matched = []
    count = 0
    with io.StringIO(raw.decode('utf-8-sig')) as text:
        for row in csv.DictReader(text):
            count += 1
            account = row.get('account_id') or row.get('accountId')
            day = (row.get('snapshot_date') or row.get('report_date') or row.get('reportDate') or '').replace('-', '')[:8]
            if account in ACCOUNTS and day in {d.replace('-', '') for d in DATES}:
                if filename.lower().endswith('flex_rows.csv') and row.get('record_type', '').lower() != 'openposition':
                    continue
                matched.append(row)
    return {'name': filename, 'sha256': hashlib.sha256(raw).hexdigest(), 'row_count': count,
            'target_positions': matched, 'target_position_count': len(matched)}


def inspect_json(data, account=None, day=None):
    matches = []
    if isinstance(data, dict):
        account = data.get('account_id', data.get('accountId', data.get('subject_id', account)))
        day = next((data[k] for k in ('snapshot_date', 'report_date', 'reportDate', 'date', 'as_of_date',
                                      'data_as_of_date') if data.get(k)), day)
        normalized = str(day).replace('-', '')[:8]
        if account in ACCOUNTS and normalized in {d.replace('-', '') for d in DATES} and any(
                k in data for k in ('quantity', 'quantity_text', 'position', 'positionValue')):
            matches.append({'account': account, 'date': day, 'record': data})
        for value in data.values():
            if isinstance(value, (dict, list)):
                matches.extend(inspect_json(value, account, day))
    elif isinstance(data, list):
        for value in data:
            matches.extend(inspect_json(value, account, day))
    return matches


def supplementary_search():
    blobs = [b for c in read('blob-inventory')['containers'] for b in c['blobs']
             if b['name'].lower().endswith(('.json', '.zip', '.recovery'))]

    def inspect(blob):
        result = {'blob': blob, 'status': 'unsearched', 'members': []}
        try:
            if blob.get('deleted'):
                result['status'] = 'deleted_not_readable_without_mutation'
                return result
            if blob['size'] > 250_000_000:
                result['status'] = 'oversize_requires_targeted_review'
                return result
            raw = service(blob['account']).get_blob_client(blob['container'], blob['name'],
                version_id=blob.get('version_id'), snapshot=blob.get('snapshot')).download_blob().readall()
            result['sha256'] = hashlib.sha256(raw).hexdigest()
            if raw.startswith(b'PK\x03\x04'):
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    members = [m for m in z.infolist() if m.filename.lower().endswith(
                        ('position_snapshots.csv', 'flex_rows.csv'))]
                    if sum(m.file_size for m in members) > 250_000_000:
                        result['status'] = 'oversize_archive_requires_targeted_review'
                        return result
                    result['members'] = [inspect_csv(z.read(m), m.filename) for m in members]
                    result['status'] = 'csv_archive_searched' if members else 'no_source_csv_members'
            elif raw.startswith(b'SQLite format 3\x00'):
                result['members'] = [{'name': 'recovery-sqlite', **inspect_sqlite(raw)}]
                result['status'] = 'recovery_database_searched'
            elif raw.lstrip().startswith((b'{', b'[')):
                matches = inspect_json(json.loads(raw))
                result['members'] = [{'name': 'json-records', 'target_position_count': len(matches),
                                      'target_positions': matches}]
                result['status'] = 'json_searched'
            elif raw.lstrip().startswith(b'<'):
                result['members'] = [{'name': 'recovery-xml', **inspect_xml(raw)}]
                result['status'] = 'recovery_xml_searched'
            else:
                result['status'] = 'non_source_document_or_probe'
        except Exception as e:
            result['status'] = 'error'
            result['error'] = type(e).__name__
            result['http_status'] = getattr(e, 'status_code', None)
        return result

    results = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for index, result in enumerate(pool.map(inspect, blobs), 1):
            results.append(result)
            if index % 200 == 0:
                print(json.dumps({'supplementary_searched': index, 'candidates': len(blobs)}), flush=True)
    save('supplementary-content-search', {'read_only': True, 'results': results})
    from collections import Counter
    print(json.dumps({'candidates': len(blobs), 'status_counts': dict(Counter(r['status'] for r in results)),
                      'matching_blobs': sum(any(m['target_position_count'] for m in r['members']) for r in results)}))


def file_share_inventory():
    accounts = read('blob-inventory')['accounts']
    records = []
    for account in accounts:
        try:
            value = request('GET', BASE + 'Microsoft.Storage/storageAccounts/' + account +
                            '/fileServices/default/shares?api-version=2023-05-01')
            records.append({'account': account, 'status': 'listed',
                            'shares': [x['name'] for x in value.get('value', [])]})
        except Exception as e:
            records.append({'account': account, 'status': 'unavailable', 'error': type(e).__name__})
    save('file-share-inventory', records)
    print(json.dumps(records))


def summarize():
    from collections import Counter
    primary = read('archive-content-search')['results']
    secondary = read('supplementary-content-search')['results']
    raw = [r for r in primary if r['blob']['container'] == 'raw-statements']
    legacy = read('db-legacy-r2-results')
    refs = [row for r in legacy['records'] if r.get('query') == 'raw_archive_references'
            for row in r.get('rows', [])]
    hashes = {r.get('sha256') for r in raw}
    unmatched = [r for r in refs if r['content_sha256'] not in hashes]
    database = {}
    for kind in ('legacy-r2', 'current', 'recovery'):
        record = read('db-' + kind + '-results')
        summary = next(r['summary'] for r in record['records'] if 'summary' in r)
        database[kind] = {'summary': summary, 'job_unchanged': record['job_settings_unchanged'],
                          'target_original_positions': sum(len(r.get('rows', [])) for r in record['records']
                                                           if r.get('query') == 'exact_original_positions'),
                          'position_date_coverage': [row for r in record['records']
                              if r.get('query') == 'normalized_positions' for row in r.get('rows', [])]}
    matches = [{'account': r['blob']['account'], 'container': r['blob']['container'],
                'blob': r['blob']['name'], 'positions': sum(m['target_position_count'] for m in r['members'])}
               for r in primary + secondary if any(m['target_position_count'] for m in r['members'])]
    documents = read('document-search-results')
    blockers = [{'kind': 'stopped_database', 'resource': 'ibkrv2stg-ownertrial-20261004',
                 'reason': 'Not restarted; cannot inspect its live tables in a read-only search'},
                {'kind': 'soft_deleted_objects', 'count': len({(r['blob']['account'], r['blob']['container'],
                         r['blob']['name'], r['blob'].get('version_id'), r['blob'].get('snapshot'))
                         for r in primary + secondary if r['status'] == 'deleted_not_readable_without_mutation'}),
                 'reason': 'Read-only investigation does not undelete protected historical copies'}]
    result = {'status': 'searched_available_rg_sources_no_target_positions_found' if not matches else 'target_candidates_found',
              'resource_group': 'rg-ibkr-database-v11', 'accounts': ACCOUNTS, 'dates': DATES,
              'resource_inventory_count': len(read('resource-inventory')),
              'storage_accounts': read('blob-inventory')['accounts'],
              'container_count': len(read('blob-inventory')['containers']),
              'original_flex_objects_parsed': len(raw), 'unique_flex_payload_hashes': len(hashes),
              'legacy_raw_database_references': len(refs), 'unmatched_legacy_raw_hashes': len(unmatched),
              'sqlite_export_versions_searched': sum(r['status'] == 'sqlite_parsed' for r in primary),
              'primary_status_counts': dict(Counter(r['status'] for r in primary)),
              'supplementary_status_counts': dict(Counter(r['status'] for r in secondary)),
              'document_containers': documents, 'file_shares': read('file-share-inventory'),
              'databases': database, 'matches': matches, 'coverage_limits': blockers,
              'observed_explanation': 'Later multi-day Flex reports contain historical NAV for the requested dates but OpenPosition records only for their final observation date; no position observations were synthesized.',
              'next_recovery_option': 'Retrieve original exact one-day Flex statements for 2026-08-27 and 2026-08-28 under existing credential/token circuit breakers, archive append-only, reconcile, and generate new derived/report versions. No new Flex queries were sent in this search.',
              'source_data_changed': False, 'source_data_imputed': False, 'database_writes': 0,
              'new_flex_queries': 0, 'permissions_changed': False,
              'utc': datetime.now(timezone.utc).isoformat()}
    if matches or unmatched or any(d['target_original_positions'] for d in database.values()):
        result['status'] = 'reconciliation_required'
    errors = [r for r in primary + secondary if r['status'] in ('error', 'oversize_requires_targeted_review',
              'oversize_archive_requires_targeted_review')]
    result['additional_unresolved_objects'] = [{'blob': r['blob']['name'], 'status': r['status'],
                                               'error': r.get('error')} for r in errors]
    evidence = {}
    for path in sorted(FOLDER.glob('*.json')):
        evidence[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    result['evidence_sha256'] = evidence
    save('search-summary', result)
    print(json.dumps({k: result[k] for k in ('status', 'container_count', 'original_flex_objects_parsed',
                                           'sqlite_export_versions_searched', 'unmatched_legacy_raw_hashes',
                                           'matches', 'additional_unresolved_objects')}))


def retain_summary():
    path = FOLDER / 'search-summary.json'
    content = path.read_bytes()
    blob_name = 'review/alpha-world-ms/v14-20261007/source-archive-search-summary.json'
    client = service('ibkrv2stgauhecwc4itaoo').get_blob_client('internal-reports', blob_name)
    client.upload_blob(content, overwrite=False)
    retained = client.download_blob().readall()
    if retained != content:
        raise ValueError('Private diagnostic evidence integrity failure')
    save('summary-cloud-receipt', {'container': 'internal-reports', 'blob': blob_name,
                                  'sha256': hashlib.sha256(content).hexdigest(), 'create_only': True,
                                  'readback_verified': True, 'source_data_changed': False})
    print(json.dumps({'status': 'private_summary_retained', 'source_data_changed': False}))


def retain_via_reporting_identity():
    if (FOLDER / 'summary-retention-intent.json').exists():
        raise ValueError('Already dispatched; inspect the existing retention operation')
    path = BASE + 'Microsoft.App/jobs/ibkrv2stg-onboard-render'
    job = request('GET', path + '?api-version=2024-03-01')
    runs = request('GET', path + '/executions?api-version=2024-03-01').get('value', [])
    if any(r['properties']['status'] in ('Running', 'Processing') for r in runs):
        raise ValueError('Existing reporting job busy')
    content = (FOLDER / 'search-summary.json').read_bytes()
    encoded = base64.b64encode(content).decode()
    blob_name = 'review/alpha-world-ms/v14-20261007/source-archive-search-summary.json'
    code = ("import os,base64,hashlib,json;from azure.identity import DefaultAzureCredential;"
            "from azure.storage.blob import BlobServiceClient;"
            "b=base64.b64decode(" + repr(encoded) + ");"
            "s=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential());"
            "c=s.get_blob_client('internal-reports'," + repr(blob_name) + ");"
            "c.upload_blob(b,overwrite=False);assert c.download_blob().readall()==b;"
            "print('MS_SOURCE_SUMMARY_RETAINED '+json.dumps({'sha256':hashlib.sha256(b).hexdigest(),"
            "'source_data_changed':False,'create_only':True}),flush=True)")
    template = copy.deepcopy(job['properties']['template'])
    template.pop('volumes', None)
    container = template['containers'][0]
    container['command'], container['args'] = ['python'], ['-c', code]
    container['env'] = [e for e in container['env'] if e['name'] in ('AZURE_STORAGE_ACCOUNT_URL', 'AZURE_CLIENT_ID')]
    from run_alpha_world_ms_database_search import invariant
    save('summary-retention-intent', {'sha256': hashlib.sha256(content).hexdigest(),
                                     'job_invariant': invariant(job), 'container': 'internal-reports',
                                     'blob': blob_name, 'source_data_changed': False})
    run = request('POST', path + '/start?api-version=2024-03-01', data=template)
    save('summary-retention-dispatch', {'execution': run['name']})
    print(json.dumps({'execution': run['name'], 'persistent_job_unchanged': True}))


def verify_retained_summary():
    path = BASE + 'Microsoft.App/jobs/ibkrv2stg-onboard-render'
    execution = read('summary-retention-dispatch')['execution']
    run = request('GET', path + '/executions/' + execution + '?api-version=2024-03-01')
    if run['properties']['status'] != 'Succeeded':
        print(json.dumps({'status': run['properties']['status'], 'execution': execution}))
        return
    intent = read('summary-retention-intent')
    content = service('ibkrv2stgauhecwc4itaoo').get_blob_client(intent['container'], intent['blob']).download_blob().readall()
    from run_alpha_world_ms_database_search import invariant
    if hashlib.sha256(content).hexdigest() != intent['sha256'] or invariant(
            request('GET', path + '?api-version=2024-03-01')) != intent['job_invariant']:
        raise ValueError('Retained evidence or unchanged worker verification failed')
    save('summary-cloud-receipt', {'execution': execution, 'sha256': intent['sha256'],
                                  'container': intent['container'], 'blob': intent['blob'],
                                  'create_only': True, 'readback_verified': True, 'job_unchanged': True})
    print(json.dumps({'status': 'private_evidence_verified', 'source_data_changed': False}))


if __name__ == '__main__':
    {'inventory': inventory, 'search': content_search, 'database-plan': database_plan,
     'supplementary': supplementary_search, 'file-shares': file_share_inventory,
     'summary': summarize, 'retain-summary': retain_summary, 'retain-via-job': retain_via_reporting_identity,
     'verify-retained': verify_retained_summary}[sys.argv[1]]()
