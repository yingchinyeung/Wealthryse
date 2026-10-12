"""Server-owned observation authority, freshly loaded on every broker check.

This reader does not authenticate tokens, enroll identities, write grants or
enable agents. Caller identity must come from the trusted gateway. A dedicated
read-only database connection and narrow grants require separate deployment.
"""
import uuid
from datetime import datetime

from .agent_governance import ObservationGrant,POLICY_VERSION,REGISTRY,resource_id
from .agent_observation import AuthorityState,_guard_database


def _items(value,limit):
    if (not isinstance(value,list) or len(value)>limit
        or any(not isinstance(item,str) or not item or item!=item.strip() or len(item)>2048
               or '*' in item or any(ord(c)<32 for c in item) for item in value)
        or len(value)!=len(set(value))):
        raise ValueError('Invalid registered authority scope')
    return frozenset(value)


def decode_authority(row,*,tenant_id,principal_id):
    if row is None: return AuthorityState()
    if row.get('tenant_id')!=tenant_id or row.get('principal_id')!=principal_id:
        raise ValueError('Authority identity differs from authenticated caller')
    for key in ('kill_switch','enabled','background_allowed'):
        if type(row.get(key)) is not bool: raise ValueError('Explicit authority controls required')
    if (row['kill_switch'] or not row['enabled'] or row.get('control_policy_version')!=POLICY_VERSION
        or row.get('policy_version')!=POLICY_VERSION): return AuthorityState()
    for key in ('control_revision','revision'):
        if type(row.get(key)) is not int or row[key]<1: raise ValueError('Versioned authority required')
    for key in ('expires_at','observed_at'):
        if not isinstance(row.get(key),datetime) or row[key].utcoffset() is None:
            raise ValueError('Database authority timestamps required')
    if row['expires_at']<=row['observed_at']: return AuthorityState()
    definition=REGISTRY.get(row.get('specialist_role'))
    if definition is None: raise ValueError('Unknown specialist role')
    subjects=_items(row.get('subject_ids'),500)
    if any(len(s)>120 for s in subjects): raise ValueError('Invalid registered account')
    resources=frozenset(resource_id(r) for r in _items(row.get('resource_ids'),500))
    if len(resources)!=len(row['resource_ids']): raise ValueError('Duplicate canonical resource scope')
    tools=_items(row.get('tool_names'),50)
    if not tools or not tools <= definition.subject_tools|definition.resource_tools:
        raise ValueError('Tools exceed registered specialist role')
    if not subjects and not resources: raise ValueError('Empty authority scope')
    return AuthorityState(ObservationGrant(tenant_id,principal_id,definition.role,subjects,resources,tools,
        row['expires_at'],enabled=True,background_allowed=row['background_allowed']),False)


class PostgresAuthoritySource:
    def __init__(self,connection_factory): self._connections=connection_factory
    def load_current(self,*,tenant_id,principal_id):
        if str(uuid.UUID(tenant_id))!=tenant_id or str(uuid.UUID(principal_id))!=principal_id:
            raise ValueError('Authenticated canonical authority identifiers required')
        # One SELECT supplies a coherent control+grant snapshot. A new connection
        # per invocation prevents stale repeatable-read authority or local caches.
        with self._connections(tenant_id) as c:
            _guard_database(c,tenant_id)
            row=c.execute('''SELECT g.tenant_id::text AS tenant_id,g.principal_id::text AS principal_id,
                g.specialist_role,g.subject_ids,g.resource_ids,g.tool_names,g.enabled,g.background_allowed,
                g.expires_at,g.policy_version,g.revision,p.kill_switch,
                p.policy_version AS control_policy_version,p.revision AS control_revision,
                clock_timestamp() AS observed_at
                FROM specialist_observation_grants g
                JOIN specialist_observation_controls p ON p.tenant_id=g.tenant_id
                WHERE g.tenant_id=? AND g.principal_id=?''',(tenant_id,principal_id)).fetchone()
            return decode_authority(row,tenant_id=tenant_id,principal_id=principal_id)
