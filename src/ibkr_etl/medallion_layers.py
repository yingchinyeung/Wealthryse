"""Grants for the medallion layer schemas (bronze, silver, gold).

Migrations 068 to 070 move the data tables into the layer schemas and leave a
security_invoker view with the old name in public. Reading or writing through
such a view needs the privilege on the view and on the table behind it.

bootstrap_postgres.py keeps granting on public as before, which now reaches the
views. mirror_privileges then gives each managed login exactly the same rights on
the table behind each view, column grants included, so every login keeps today's
rights. Everything here does nothing until a layer schema exists.

The U8 history tables (silver.<table>_history) and their setting
(public.silver_history_settings) are written only by their owner, through the
history trigger. The schema-wide grants reach them too, so keep_history_read_only
takes the write rights back. It does nothing until the U8 migration has run.
"""
from __future__ import annotations

LAYERS = ("bronze", "silver", "gold")
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")
COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE")

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

COLUMN_QUERY = """
SELECT p.privilege_type, a.attname
  FROM pg_attribute a, aclexplode(a.attacl) p
 WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
   AND p.grantee = %s::regrole AND p.privilege_type = ANY(%s)
 ORDER BY 1, 2
"""


HISTORY_QUERY = """
SELECT c.relname
  FROM pg_class c
  JOIN pg_trigger t ON t.tgrelid = c.oid AND t.tgname = 'history_append_only' AND NOT t.tgisinternal
 WHERE c.relnamespace = 'silver'::regnamespace AND c.relkind IN ('r', 'p')
 ORDER BY 1
"""
WRITES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")


def present_layers(connection) -> list[str]:
    rows = connection.execute(
        "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s)", (list(LAYERS),)
    ).fetchall()
    found = {row[0] for row in rows}
    return [layer for layer in LAYERS if layer in found]


def compatibility_pairs(connection) -> list[tuple[str, str]]:
    """(layer, name) for each layer object that has a view of the same name in public."""
    return [(row[0], row[1]) for row in connection.execute(PAIRS_QUERY, (list(LAYERS),)).fetchall()]


def _direct(connection, relation: str, role: str) -> tuple[list[str], list[tuple[str, str]]]:
    """The role's own grants on a relation: table privileges, and (privilege, column) pairs."""
    table = [row[0] for row in connection.execute(DIRECT_QUERY, (relation, role, list(PRIVILEGES))).fetchall()]
    columns = [(row[0], row[1]) for row in connection.execute(
        COLUMN_QUERY, (relation, role, list(COLUMN_PRIVILEGES))).fetchall()]
    return table, columns


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
            if wanted[0] or wanted[1]:
                needs.add(layer)  # a column grant needs the schema too
            if _direct(connection, f'"{layer}"."{name}"', role) == wanted:
                continue
            # REVOKE ALL on a table also removes its column grants.
            connection.execute(sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(table, sql.Identifier(role)))
            if wanted[0]:
                connection.execute(sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                    sql.SQL(", ").join(sql.SQL(p) for p in wanted[0]), table, sql.Identifier(role)))
            for privilege in sorted({p for p, _ in wanted[1]}):
                connection.execute(sql.SQL("GRANT {} ({}) ON TABLE {} TO {}").format(
                    sql.SQL(privilege), sql.SQL(", ").join(sql.Identifier(c) for p, c in wanted[1] if p == privilege),
                    table, sql.Identifier(role)))
            changed += 1
        for layer in present_layers(connection):
            verb = "GRANT USAGE ON SCHEMA {} TO {}" if layer in needs else "REVOKE USAGE ON SCHEMA {} FROM {}"
            connection.execute(sql.SQL(verb).format(sql.Identifier(layer), sql.Identifier(role)))
    return {"pairs": len(pairs), "roles": len(roles), "tables_changed": changed}


def mismatches(connection, roles: list[str]) -> list[str]:
    """Each role's direct rights on a layer table, column grants included, must equal those on the public view."""
    found = []
    for role in roles:
        for layer, name in compatibility_pairs(connection):
            view = _direct(connection, f'public."{name}"', role)
            table = _direct(connection, f'"{layer}"."{name}"', role)
            if view[0] != table[0]:
                found.append(f"{role}: public.{name} {view[0]} but {layer}.{name} {table[0]}")
            if view[1] != table[1]:
                found.append(f"{role}: public.{name} columns {view[1]} but {layer}.{name} columns {table[1]}")
    return found


def history_objects(connection) -> list[tuple[str, str]]:
    """(schema, name) of the U8 history tables and their setting; empty until that migration has run."""
    if "silver" not in present_layers(connection):
        return []
    found = [("silver", row[0]) for row in connection.execute(HISTORY_QUERY).fetchall()]
    if connection.execute("SELECT to_regclass('public.silver_history_settings')").fetchone()[0]:
        found.append(("public", "silver_history_settings"))
    return found


def keep_history_read_only(connection, roles: list[str]) -> int:
    """Take back the write rights the schema-wide grants give on the history tables and their setting."""
    from psycopg import sql

    objects = history_objects(connection)
    for schema, name in objects:
        for role in roles:
            connection.execute(sql.SQL("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE {} FROM {}").format(
                sql.Identifier(schema, name), sql.Identifier(role)))
    return len(objects)


def history_write_rights(connection, roles: list[str]) -> list[str]:
    """Any write right a role still has on a history table or the setting, PUBLIC and memberships included."""
    found = []
    for schema, name in history_objects(connection):
        for role in roles:
            for privilege in WRITES:
                if connection.execute("SELECT has_table_privilege(%s, %s, %s)",
                                      (role, f'"{schema}"."{name}"', privilege)).fetchone()[0]:
                    found.append(f"{role}: {privilege} on {schema}.{name}")
    return found
