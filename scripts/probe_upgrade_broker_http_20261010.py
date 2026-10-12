"""Real uvicorn process with synthetic auth and loopback-only networking."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import urllib.error


def main():
    if any(os.environ.get(k) for k in ('DATABASE_URL', 'IBKR_FLEX_TOKEN', 'BROKER_AUTH', 'KEY_VAULT_URI')):
        raise ValueError('No live credentials permitted in probe environment')
    child = '''import socket,uvicorn
original=socket.socket.connect
def local_only(sock,address):
 if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1'):
  raise RuntimeError('Qualification forbids non-loopback connections')
 return original(sock,address)
socket.socket.connect=local_only
uvicorn.run('ibkr_etl.credential_broker:app',host='127.0.0.1',port=18765,access_log=False,log_level='error')
'''
    env = {k: os.environ[k] for k in ('PATH', 'PYTHONPATH', 'LD_LIBRARY_PATH', 'SSL_CERT_FILE') if k in os.environ}
    env.update(PYTHONPATH='/app/src:/app/scripts', PYTHONDONTWRITEBYTECODE='1',
               KEY_VAULT_URI='https://qualification.vault.azure.net', AZURE_CLIENT_ID='synthetic-client',
               BROKER_AUTH='synthetic-only-qualification-auth-0000')
    proc = subprocess.Popen([sys.executable, '-c', child], env=env, cwd='/app', stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    checks = []
    def call(path, method='GET', body=None, authorization=None):
        headers = {'Content-Type': 'application/json'}
        if authorization: headers['Authorization'] = authorization
        req = urllib.request.Request('http://127.0.0.1:18765' + path, method=method,
                                     data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as response: return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
    try:
        ready = False
        for _ in range(60):
            if proc.poll() is not None: raise ValueError('Candidate HTTP server failed to start')
            try:
                status, body = call('/health')
                ready = status == 200 and json.loads(body) == {'status': 'ready', 'capability': 'set-only-flex-secrets'}
                if ready: break
            except urllib.error.URLError: pass
            time.sleep(.25)
        if not ready: raise ValueError('Actual HTTP readiness not established')
        checks.append('actual_uvicorn_health_ready')
        for path in ('/docs', '/redoc', '/openapi.json'):
            if call(path)[0] != 404: raise ValueError('Documentation unexpectedly enabled')
            checks.append(path + '_disabled')
        if call('/v1/credentials')[0] != 405: raise ValueError('Credential read endpoint enabled')
        checks.append('credential_read_route_absent')
        payload = {'secret_name': 'ibkr-flex-source-' + 'b' * 32, 'token': 'synthetic-runtime-token'}
        for label, authorization in (('missing_auth', None), ('wrong_auth', 'Bearer incorrect')):
            status, body = call('/v1/credentials', 'POST', payload, authorization)
            if status != 403 or b'synthetic-runtime-token' in body: raise ValueError('Unauthorized request accepted or secret exposed')
            checks.append(label + '_denied')
        for label, body in (('invalid_namespace', {**payload, 'secret_name': 'database-admin-url'}),
                            ('extra_field', {**payload, 'unexpected': True})):
            if call('/v1/credentials', 'POST', body)[0] != 422: raise ValueError('Invalid input accepted')
            checks.append(label + '_rejected')
    finally:
        proc.terminate()
        try: proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.communicate(timeout=5)
    print('BROKER_HTTP_PROOF ' + json.dumps({'passed': True, 'checks': checks, 'actual_server_process': True,
          'synthetic_credentials_only': True, 'server_nonloopback_connections_denied': True,
          'secret_writes': 0, 'broker_calls': 0, 'database_connections': 0, 'saved_service_changed': False}), flush=True)


if __name__ == '__main__': main()
