"""Single unauthenticated request to the documented Nasdaq WIKI table API."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import requests

OUT = Path(__file__).resolve().parents[1] / '.work/alpha-t-readiness-20261009-v1'
url = 'https://data.nasdaq.com/api/v3/datatables/WIKI/PRICES.json'
response = requests.get(url, params={'ticker':'AAPL', 'date.gte':'2008-09-15',
                                    'date.lte':'2009-03-09'}, timeout=30)
with (OUT / 'wiki-public-response.bin').open('xb') as stream:
    stream.write(response.content)
try:
    body = response.json()
except requests.exceptions.JSONDecodeError:
    body = {}
table = body.get('datatable', {})
result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'url': response.url,
    'http_status': response.status_code, 'content_type': response.headers.get('Content-Type'),
    'response_sha256': hashlib.sha256(response.content).hexdigest(),
    'rows': len(table.get('data', [])), 'columns': table.get('columns', []),
    'provider_error': body.get('quandl_error'),
    'independent_crisis_prices_verified': False, 'credentials_supplied': False}
with (OUT / 'wiki-access.json').open('x', encoding='utf-8') as stream:
    json.dump(result, stream, indent=2)
print(json.dumps(result))
