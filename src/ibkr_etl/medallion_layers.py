"""Grants for the medallion layer schemas (bronze, silver, gold).

Migrations 068 to 070 move the data tables into the layer schemas and leave a
security_invoker view with the old name in public. Reading or writing through
such a view needs the privilege on the view and on the table behind it.

bootstrap_postgres.py keeps granting on public as before, which now reaches the
views. mirror_privileges then gives each managed login exactly the same rights on
the table behind each view, so every login keeps today's rights. Everything here
does nothing until a layer schema exists.
"""
from __future__ import annotations

LAYERS = ("bronze", "silver", "gold")
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")

PAIRS_QUERY = """
SELECT n.nspname AS layer, c.relname AS name
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_class v ON v.relname = c.relname AND v.relkind = 'v'
   AND v.relnamespace = 'public'::regnamespace
 WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p', 'v')
 ORDER BY 1, 2
"""

DIRECT_QUERY = """
SELECT a.privilege_type
  FROM pg_class c, aclexplode(c.relacl) a
 WHERE c.oid = %s::regclass AND a.grantee = %s::regrole AND a.privilege_type = ANY(%s)
 ORDER BY 1
"""


def present_layers(connection) -> list[str]:
    rows = connection.execute(
        "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s)", (list(LAYERS),)
    ).fetchall()
    found = {row[0] for row in rows}
    return [layer for layer in LAYERS if layer in found]


def compatibility_pairs(connection) -> list[tuple[str, str]]:
    """(layer, name) for each layer object that has a view of the same name in public."""
    return [(row[0], row[1]) for row in connection.execute(PAIRS_QUERY, (list(LAYERS),)).fetchall()]


def _direct(connection, relation: str, role: str) -> list[str]:
    return [row[0] for row in connection.execute(DIRECT_QUERY, (relation, role, list(PRIVILEGES))).fetchall()]


def runtime_search_path(layers: list[str]) -> str:
    """Layer schemas first for runtime logins; the migration login keeps public first."""
    return ", ".join(['"$user"', *layers, "public"])


def mirror_privileges(connection, roles: list[str]) -> dict[str, int]:
    """Give each role the same direct rights on each layer table as on its public view."""
    from psycopg import sql

    pairs = compatibility_pairs(connection)
    changed = 0
    for role in roles:
        needs = set()
        for layer, name in pairs:
            table = sql.Identifier(layer, name)
            wanted = _direct(connection, f'public."{name}"', role)
            if _direct(connection, f'"{layer}"."{name}"', role) == wanted:
                if wanted:
                    needs.add(layer)
                continue
            connection.execute(sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(table, sql.Identifier(role)))
            if wanted:
                connection.execute(sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                    sql.SQL(", ").join(sql.SQL(p) for p in wanted), table, sql.Identifier(role)))
                needs.add(layer)
            changed += 1
        for layer in present_layers(connection):
            verb = "GRANT USAGE ON SCHEMA {} TO {}" if layer in needs else "REVOKE USAGE ON SCHEMA {} FROM {}"
            connection.execute(sql.SQL(verb).format(sql.Identifier(layer), sql.Identifier(role)))
    return {"pairs": len(pairs), "roles": len(roles), "tables_changed": changed}


def mismatches(connection, roles: list[str]) -> list[str]:
    """Each role's direct rights on a layer table must equal its rights on the public view."""
    found = []
    for role in roles:
        for layer, name in compatibility_pairs(connection):
            view = _direct(connection, f'public."{name}"', role)
            table = _direct(connection, f'"{layer}"."{name}"', role)
            if view != table:
                found.append(f"{role}: public.{name} {view} but {layer}.{name} {table}")
    return found
