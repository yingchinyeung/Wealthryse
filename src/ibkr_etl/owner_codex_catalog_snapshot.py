"""Inert private-host catalog diagnostics; never installs or repairs objects.

Queries only catalogs, with hashes instead of role settings/comments/ACL text.
Full membership keys include the grantor. Unknown fields, truncation and duplicate
keys fail closed. Row ordering is not treated as a permission change.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from . import owner_codex_isolated_target as host

CA_PATH='/etc/ssl/certs/ca-certificates.crt'
HASH="pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to({value},'UTF8')),'hex')"
QUERIES={
    'databases':"SELECT d.oid::bigint AS oid,d.datname AS name,d.datdba::bigint AS owner_oid,d.datallowconn AS allow_connections,d.datconnlimit AS connection_limit,"+HASH.format(value='d.datacl::text')+" AS acl_sha256,"+HASH.format(value="pg_catalog.shobj_description(d.oid,'pg_database')")+" AS marker_sha256 FROM pg_catalog.pg_database d ORDER BY d.oid LIMIT 129",
    'roles':"SELECT r.oid::bigint AS oid,r.rolname AS name,r.rolsuper AS superuser,r.rolinherit AS inherit,r.rolcreaterole AS create_role,r.rolcreatedb AS create_database,r.rolcanlogin AS can_login,r.rolreplication AS replication,r.rolbypassrls AS bypass_rls,r.rolconnlimit AS connection_limit,r.rolvaliduntil::text AS valid_until,"+HASH.format(value='r.rolconfig::text')+" AS configuration_sha256,"+HASH.format(value="pg_catalog.shobj_description(r.oid,'pg_authid')")+" AS marker_sha256 FROM pg_catalog.pg_roles r ORDER BY r.oid LIMIT 513",
    'memberships':"SELECT m.roleid::bigint AS role_oid,m.member::bigint AS member_oid,m.grantor::bigint AS grantor_oid,m.admin_option,m.inherit_option,m.set_option FROM pg_catalog.pg_auth_members m ORDER BY m.roleid,m.member,m.grantor LIMIT 4097",
}
KEYS={'databases':('oid',),'roles':('oid',),'memberships':('role_oid','member_oid','grantor_oid')}
FIELDS={
    'databases':dict(oid='oid',name='name',owner_oid='oid',allow_connections='bool',connection_limit='limit',acl_sha256='sha',marker_sha256='sha'),
    'roles':dict(oid='oid',name='name',superuser='bool',inherit='bool',create_role='bool',create_database='bool',can_login='bool',replication='bool',bypass_rls='bool',connection_limit='limit',valid_until='text',configuration_sha256='sha',marker_sha256='sha'),
    'memberships':dict(role_oid='oid',member_oid='oid',grantor_oid='oid',admin_option='bool',inherit_option='bool',set_option='bool'),
}
LIMITS={'databases':128,'roles':512,'memberships':4096}


class CatalogSnapshotError(ValueError):
    def __init__(self):
        super().__init__('owner_catalog_snapshot_unverified')


def require(value):
    if not value:
        raise CatalogSnapshotError()


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()


def valid(value,kind):
    if kind=='bool':return type(value) is bool
    if kind=='oid':return type(value) is int and 0<value<2**32
    if kind=='limit':return type(value) is int and -1<=value<2**31
    if kind=='sha':return value is None or (type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None)
    if kind=='name':return type(value) is str and 0<len(value.encode('utf-8'))<=63 and '\x00' not in value
    if kind=='text':return value is None or (type(value) is str and len(value)<=128 and '\x00' not in value)
    return False


def normalized(value):
    require(type(value) is dict and set(value)==set(FIELDS))
    result={}
    for name,fields in FIELDS.items():
        rows=value[name]
        require(type(rows) is list and len(rows)<=LIMITS[name]
            and all(type(row) is dict and set(row)==set(fields)
                and all(valid(row[key],kind) for key,kind in fields.items()) for row in rows))
        keys=[tuple(row[k] for k in KEYS[name]) for row in rows]
        require(len(keys)==len(set(keys)))
        result[name]=sorted((dict(row) for row in rows),key=lambda row:tuple(row[k] for k in KEYS[name]))
    roles={r['oid'] for r in result['roles']}
    require(roles and result['databases'] and all(r['owner_oid'] in roles for r in result['databases'])
        and all(all(r[k] in roles for k in KEYS['memberships']) for r in result['memberships']))
    return result


@dataclass(frozen=True)
class CatalogSnapshot:
    payload:bytes

    def __post_init__(self):
        try:
            require(type(self.payload) is bytes and 0<len(self.payload)<=2*1024*1024)
            require(canonical(normalized(json.loads(self.payload)))==self.payload)
        except Exception:
            raise CatalogSnapshotError() from None

    @classmethod
    def from_rows(cls,rows):
        try:
            return cls(canonical(normalized(rows)))
        except Exception:
            raise CatalogSnapshotError() from None

    @property
    def sha256(self):
        return hashlib.sha256(self.payload).hexdigest()

    def rows(self):
        return json.loads(self.payload)


def differences(before,after):
    require(type(before) is CatalogSnapshot and type(after) is CatalogSnapshot)
    left,right=before.rows(),after.rows()
    result={}
    for name in FIELDS:
        l={tuple(r[k] for k in KEYS[name]):r for r in left[name]}
        r={tuple(v[k] for k in KEYS[name]):v for v in right[name]}
        changed=[]
        for key in sorted(set(l)|set(r)):
            if l.get(key)!=r.get(key):
                changed.append(dict(key=list(key),before_sha256=hashlib.sha256(canonical(l[key])).hexdigest() if key in l else None,
                    after_sha256=hashlib.sha256(canonical(r[key])).hexdigest() if key in r else None,
                    changed_fields=sorted(k for k in FIELDS[name] if l.get(key,{}).get(k)!=r.get(key,{}).get(k))))
        if changed:result[name]=changed
    return result


def read_snapshot(connection):
    """Trusted fresh admin connection to the registered private postgres DB.

    A read-only transaction is not a claim of an atomic cluster-wide inventory.
    Live composition must independently check stability and approved differences.
    No credentials are acquired and no source data or SQL mutator is provided.
    """
    try:
        require(connection.autocommit is True and connection.info.transaction_status==0
            and connection.info.host==host.HOST and connection.info.dbname=='postgres'
            and connection.info.user==host.ADMIN and connection.pgconn.ssl_in_use is True
            and connection.info.get_parameters().get('sslmode')=='verify-full'
            and connection.info.get_parameters().get('sslrootcert')==CA_PATH)
        with connection.transaction():
            connection.execute('SET TRANSACTION READ ONLY')
            identity=connection.execute("SELECT session_user::text AS login,current_user::text AS role,current_database() AS database,current_setting('transaction_read_only') AS readonly").fetchone()
            require(identity==dict(login=host.ADMIN,role=host.ADMIN,database='postgres',readonly='on'))
            rows={name:connection.execute(query).fetchall() for name,query in QUERIES.items()}
            snapshot=CatalogSnapshot.from_rows(rows)
        require(connection.info.transaction_status==0)
        return snapshot
    except Exception:
        raise CatalogSnapshotError() from None
