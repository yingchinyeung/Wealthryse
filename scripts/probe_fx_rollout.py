"""Read-only catalog preflight. No financial rows, secrets, DDL or grants."""
import json
import os
import psycopg
from psycopg.rows import dict_row


def main():
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row,
                         options="-c default_transaction_read_only=on -c statement_timeout=30000") as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        migrations = c.execute("SELECT version,sha256 FROM public.schema_migrations ORDER BY version").fetchall()
        defaults = c.execute("""SELECT pg_get_userbyid(d.defaclrole) AS owner,
            coalesce(n.nspname,'GLOBAL') AS schema, d.defaclobjtype AS kind,
            CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS grantee,
            a.privilege_type, a.is_grantable
            FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
            CROSS JOIN LATERAL aclexplode(d.defaclacl) a
            WHERE d.defaclrole=(SELECT oid FROM pg_roles WHERE rolname=current_user)
            AND (d.defaclnamespace=0 OR n.nspname='public')
            ORDER BY schema,kind,grantee,privilege_type""").fetchall()
        objects = c.execute("""SELECT n.nspname AS schema,c.relname AS name FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
            AND (c.relname LIKE 'fx_evidence_%' OR c.relname LIKE 'workflow_stage_evidence'
                 OR c.relname LIKE 'workflow_shadow_decisions') ORDER BY c.relname""").fetchall()
        context = c.execute("SELECT current_user AS role,current_setting('transaction_read_only') AS read_only").fetchone()
    print("FX_EVIDENCE_SCHEMA_TRIAL "+json.dumps({"mode":"read_only_rollout_preflight",
        "context":context,"migration_count":len(migrations),"last_migration":migrations[-1] if migrations else None,
        "default_privileges":defaults,"existing_evidence_objects":objects,"execution_authorized":False}),flush=True)


if __name__ == "__main__":
    main()
