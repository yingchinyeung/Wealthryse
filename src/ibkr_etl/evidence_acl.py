"""Keep unactivated evidence objects owner-only after broad bootstrap grants.

This policy deliberately grants no runtime reader/writer authority. A later
reviewed reader cutover must replace this quarantine explicitly. Database
owners/administrators are outside this application-role boundary.
"""
import re

TABLES = ("workflow_stage_evidence", "workflow_shadow_decisions", "fx_evidence_runs",
          "fx_evidence_versions", "fx_evidence_observations", "fx_evidence_legacy_aliases", "fx_evidence_batches")
FUNCTIONS = ("reject_shadow_workflow_mutation", "reject_fx_evidence_mutation")

ACL_QUERY = """SELECT 'TABLE' AS kind,c.relname AS name,a.grantee,
    CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS recipient
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
    LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
    WHERE n.nspname=%s AND c.relname=ANY(%s) AND a.grantee<>c.relowner
    UNION
    SELECT 'FUNCTION',p.proname,a.grantee,
    CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace,
    LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
    WHERE n.nspname=%s AND p.proname=ANY(%s) AND p.pronargs=0 AND a.grantee<>p.proowner
    ORDER BY kind,name,grantee"""


def quarantine_evidence_objects(connection, *, schema="public"):
    from psycopg import sql
    from psycopg.pq import TransactionStatus
    from psycopg.rows import dict_row
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise ValueError("Invalid evidence ACL schema")
    if connection.info.transaction_status != TransactionStatus.INTRANS:
        raise ValueError("Evidence ACL requires a caller-owned transaction")
    parameters = (schema, list(TABLES), schema, list(FUNCTIONS))
    with connection.transaction():
        with connection.cursor(row_factory=dict_row) as c:
            rows = c.execute(ACL_QUERY, parameters).fetchall()
            for row in rows:
                kind, name = row["kind"], row["name"]
                if (kind == "TABLE" and name in TABLES):
                    target = sql.SQL("TABLE {}").format(sql.Identifier(schema, name))
                elif kind == "FUNCTION" and name in FUNCTIONS:
                    target = sql.SQL("FUNCTION {}()").format(sql.Identifier(schema, name))
                else:
                    raise ValueError("Unexpected protected evidence object")
                recipient = sql.SQL("PUBLIC") if row["grantee"] == 0 else sql.Identifier(row["recipient"])
                c.execute(sql.SQL("REVOKE ALL ON {} FROM {} CASCADE").format(target, recipient))
            if c.execute(ACL_QUERY, parameters).fetchall():
                raise ValueError("Evidence ACL readback retains non-owner grants")
    return {"revoked_object_grantees": len(rows), "runtime_access_authorized": False}
