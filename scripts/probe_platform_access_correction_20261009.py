"""Read-only installed-runtime authorization tests with synthetic fixtures."""
import hashlib
import io
import json
import os
from pathlib import Path
import unittest

os.environ['PORTAL_TEST_RUNTIME'] = '/app'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
suite = unittest.defaultTestLoader.discover(
    '/app/platform-correction-tests', pattern='test_snapshot_access_correction.py')
stream = io.StringIO()
result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
files = ['src/ibkr_etl/web.py', 'src/ibkr_etl/metrics_guide.py']
record = {
    'read_only': True, 'synthetic_fixtures_only': True,
    'tests_run': result.testsRun, 'failures': len(result.failures),
    'errors': len(result.errors), 'skipped': len(result.skipped),
    'successful': result.wasSuccessful(), 'database_writes': 0,
    'source_sha256': {name: hashlib.sha256((Path('/app') / name).read_bytes()).hexdigest() for name in files},
}
print('PLATFORM_CORRECTION_PROBE ' + json.dumps(record), flush=True)
if not result.wasSuccessful() or result.testsRun != 14:
    raise SystemExit('Installed-runtime authorization verification failed')
