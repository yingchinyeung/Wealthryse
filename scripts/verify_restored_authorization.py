"""Exercise revocation against ONLY the disposable restore, always rolled back."""
import json
import os
import psycopg

from ibkr_etl.authorization import resolve_authorized_identity, subject_access_levels
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory


def main():
    host = os.environ.get("RECOVERY_HOST", "")
    if host != "ibkrv2stg-recovery-20261001.postgres.database.azure.com":
        raise RuntimeError("Revocation tests may only run on the named recovery server")
    tenant = os.environ["PORTFOLIO_TENANT_ID"]
    raw = psycopg.connect(os.environ["DATABASE_URL"], host=host,
        connect_timeout=30, options="-c statement_timeout=30000", row_factory=_hybrid_row_factory)
    connection = PostgresConnection(raw)
    try:
        connection.execute("SELECT set_config('app.tenant_id', ?, true)", (tenant,))
        users = connection.execute("SELECT entra_object_id,email FROM app_users WHERE tenant_id = ? AND status='active'", (tenant,)).fetchall()
        if len(users) != 5:
            raise RuntimeError("Expected the reviewed five-user registry; stop for reconciliation")
        checks = 0
        for user in users:
            kwargs = {"tenant_id": tenant, "object_id": user["entra_object_id"], "email": user["email"]}
            if resolve_authorized_identity(connection, **kwargs) is None:
                raise RuntimeError("An active registered identity has no valid effective role")
            connection.execute("SAVEPOINT revocation_probe")
            connection.execute("UPDATE app_users SET status='disabled' WHERE tenant_id = ? AND entra_object_id = ?",
                               (tenant, user["entra_object_id"]))
            if resolve_authorized_identity(connection, **kwargs) is not None:
                raise RuntimeError("Disabled identity incorrectly authorized")
            connection.execute("ROLLBACK TO SAVEPOINT revocation_probe")
            connection.execute("DELETE FROM app_user_roles WHERE tenant_id = ? AND entra_object_id = ?",
                               (tenant, user["entra_object_id"]))
            if resolve_authorized_identity(connection, **kwargs) is not None:
                raise RuntimeError("Role-revoked identity incorrectly authorized")
            connection.execute("ROLLBACK TO SAVEPOINT revocation_probe")
            grants = connection.execute("SELECT DISTINCT subject_id FROM user_account_access WHERE tenant_id = ? AND entra_object_id = ?",
                                        (tenant, user["entra_object_id"])).fetchall()
            connection.execute("DELETE FROM user_account_access WHERE tenant_id = ? AND entra_object_id = ?",
                               (tenant, user["entra_object_id"]))
            for grant in grants:
                if subject_access_levels(connection, tenant_id=tenant, object_id=user["entra_object_id"], subject_id=grant["subject_id"]):
                    raise RuntimeError("Deleted account entitlement remains visible")
            connection.execute("ROLLBACK TO SAVEPOINT revocation_probe")
            connection.execute("RELEASE SAVEPOINT revocation_probe")
            checks += 3 + len(grants)
        connection.rollback()
        print(json.dumps({"status": "pass", "users": len(users), "checks": checks,
                          "production_modified": False, "clone_changes": "rolled_back"}), flush=True)
    finally:
        connection.rollback()
        connection.close()


if __name__ == "__main__":
    main()
