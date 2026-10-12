"""Fixed staging composition for one delegated, deterministic account-read tool.

Three distinct credentials preserve reader/authority/audit database boundaries.
No grant administration, model calls, background workers or source mutations.
"""
import json
import os
from contextlib import contextmanager
from .agent_gateway import GatewayBinding,ObservationGateway,DatabaseUserDirectory,GatewayDenied,create_gateway_app
from .agent_gateway_limits import GatewayAdmission
from .agent_gateway_auth import TokenPolicy,EntraAccessTokens
from .agent_observation import ObservationBroker
from .agent_authority import PostgresAuthoritySource
from .agent_observation_audit import ProtectedObservationAudit
from .agent_observation_evidence import ObservationEvidenceBinding,AGENT_PRINCIPAL
from .specialist_observation_runtime import observation_database,TENANT
from .specialist_authority_runtime import authority_database
from .specialist_audit_runtime import validate_database_url,bind_audit_session,verify_audit_access
from .specialist_capture import managed_capture_archive

ENTRA='0578a50b-a7a1-40a4-905b-8b20dba84124'
AUDIENCE='e02b72e6-66f8-4c71-ab5c-c91f13213b18'
CLIENT='b55f80e2-ef32-41a8-a741-fe33488e171a'


def compose(source_manifest_sha256):
    import psycopg
    from psycopg.rows import dict_row
    import jwt
    if jwt.__version__!='2.15.1': raise ValueError('Verified gateway token library required')
    reader_url=os.environ['SPECIALIST_OBSERVATION_DATABASE_URL']
    authority_url=os.environ['SPECIALIST_AUTHORITY_DATABASE_URL']
    audit_url=os.environ['SPECIALIST_AUDIT_DATABASE_URL']
    validate_database_url(audit_url)
    reader=lambda tenant:observation_database(reader_url,tenant)
    authority=lambda tenant:authority_database(authority_url,tenant)
    @contextmanager
    def audit_connection():
        with psycopg.connect(audit_url,autocommit=True,connect_timeout=10,row_factory=dict_row) as c: yield c
    archive=managed_capture_archive()
    # Real fresh-login and protected-storage preflight before binding ingress.
    with reader(TENANT): pass
    with authority(TENANT): pass
    with audit_connection() as c:
        bind_audit_session(c);verify_audit_access(c)
    if archive.check_ready().get('protected_archive_verified') is not True:
        raise ValueError('Protected observation archive unavailable')
    adapter=ProtectedObservationAudit(audit_connection,archive,
        producer=ObservationEvidenceBinding(source_manifest_sha256))
    broker=ObservationBroker(reader,authority=PostgresAuthoritySource(authority),audit=adapter,enabled=True)
    gateway=ObservationGateway(binding=GatewayBinding(TENANT,AGENT_PRINCIPAL),
        authenticator=EntraAccessTokens(TokenPolicy(ENTRA,AUDIENCE,frozenset({CLIENT}))),
        directory=DatabaseUserDirectory(reader),broker=broker,admission=GatewayAdmission(),enabled=True,
        supported_tools=frozenset({'get_performance_series'}))
    app=create_gateway_app(gateway)
    @app.get('/health')
    def health(): return {'status':'ready','mode':'delegated-observation','background_agents_enabled':False}
    print('SPECIALIST_GATEWAY_READY '+json.dumps(dict(status='passed',fresh_logins_verified=3,
        protected_archive_verified=True,source_mutation_authority=False,
        authority_enrolled_by_startup=False,background_agents_enabled=False,messages_sent=0)),flush=True)
    return app
