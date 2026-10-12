from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from .authorization import ROLE_PRECEDENCE, SUBJECT_ACCESS
from .util import canonical_json, utc_now_iso


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
OBJECT_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
LEGACY_ROLE = {
    "owner": "owner",
    "platform_admin": "administrator",
    "final_approver": "administrator",
    "compliance_reviewer": "analyst",
    "portfolio_analyst_reviewer": "analyst",
    "onboarding_operator": "analyst",
    "viewer": "viewer",
}


@dataclass(frozen=True)
class RegistryUser:
    object_id: str
    email: str
    display_name: str | None
    roles: tuple[str, ...]
    entitlements: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class IdentityRegistryManifest:
    mode: str
    users: tuple[RegistryUser, ...]
    sha256: str

    @property
    def role_count(self) -> int:
        return sum(len(user.roles) for user in self.users)

    @property
    def entitlement_count(self) -> int:
        return sum(len(user.entitlements) for user in self.users)


def _primary_legacy_role(roles: tuple[str, ...]) -> str:
    for role in ROLE_PRECEDENCE:
        if role in roles:
            return LEGACY_ROLE[role]
    raise RuntimeError("Identity has no supported role")


def validate_identity_manifest(value: Mapping[str, Any]) -> IdentityRegistryManifest:
    mode = str(value.get("mode", "merge")).strip().lower()
    if mode not in {"merge", "replace"}:
        raise RuntimeError("Identity registry mode must be merge or replace")
    raw_users = value.get("users")
    if not isinstance(raw_users, list) or not raw_users:
        raise RuntimeError("Identity registry requires at least one user")
    users: list[RegistryUser] = []
    object_ids: set[str] = set()
    emails: set[str] = set()
    owner_count = 0
    for raw in raw_users:
        if not isinstance(raw, dict):
            raise RuntimeError("Every identity registry user must be an object")
        object_id = str(raw.get("object_id", "")).strip().lower()
        email = str(raw.get("email", "")).strip().lower()
        if not OBJECT_ID_RE.fullmatch(object_id):
            raise RuntimeError("Identity registry contains an invalid Entra object ID")
        if not EMAIL_RE.fullmatch(email):
            raise RuntimeError("Identity registry contains an invalid email address")
        if object_id in object_ids or email in emails:
            raise RuntimeError("Identity registry contains a duplicate identity")
        object_ids.add(object_id)
        emails.add(email)
        raw_roles = raw.get("roles")
        if not isinstance(raw_roles, list) or not raw_roles:
            raise RuntimeError(f"Identity {email} requires at least one role")
        roles = tuple(sorted({str(role).strip() for role in raw_roles}))
        if any(role not in ROLE_PRECEDENCE for role in roles):
            raise RuntimeError(f"Identity {email} contains an unsupported role")
        owner_count += int("owner" in roles)
        raw_entitlements = raw.get("entitlements", [])
        if not isinstance(raw_entitlements, list):
            raise RuntimeError(f"Identity {email} entitlements must be a list")
        entitlements: list[tuple[str, str]] = []
        seen_subjects: set[str] = set()
        for entitlement in raw_entitlements:
            if not isinstance(entitlement, dict):
                raise RuntimeError(f"Identity {email} has an invalid entitlement")
            subject_id = str(entitlement.get("subject_id", "")).strip()
            # Physical IBKR account identifiers are case-insensitive and use the
            # canonical upper-case form.  Derived portfolio identifiers are
            # governed slugs whose stored case must be preserved (for example,
            # ``hong-kong-china``).  Upper-casing every identifier made it
            # impossible to grant a Viewer access to a derived portfolio.
            if re.fullmatch(r"(?i)u\d+", subject_id):
                subject_id = subject_id.upper()
            access_level = str(entitlement.get("access_level", "")).strip().lower()
            subject_key = subject_id.casefold()
            if not subject_id or len(subject_id) > 80 or subject_key in seen_subjects:
                raise RuntimeError(f"Identity {email} has an invalid subject entitlement")
            if access_level not in SUBJECT_ACCESS:
                raise RuntimeError(f"Identity {email} has an unsupported access level")
            seen_subjects.add(subject_key)
            entitlements.append((subject_id, access_level))
        users.append(
            RegistryUser(
                object_id=object_id,
                email=email,
                display_name=(str(raw["display_name"]).strip() if raw.get("display_name") else None),
                roles=roles,
                entitlements=tuple(sorted(entitlements)),
            )
        )
    if owner_count != 1:
        raise RuntimeError("Identity registry must contain exactly one owner")
    normalized = {
        "mode": mode,
        "users": [
            {
                "object_id": user.object_id,
                "email": user.email,
                "display_name": user.display_name,
                "roles": list(user.roles),
                "entitlements": [
                    {"subject_id": subject_id, "access_level": access_level}
                    for subject_id, access_level in user.entitlements
                ],
            }
            for user in sorted(users, key=lambda item: item.object_id)
        ],
    }
    digest = hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()
    return IdentityRegistryManifest(mode=mode, users=tuple(users), sha256=digest)


def apply_identity_manifest(
    connection,
    *,
    tenant_id: str,
    manifest: IdentityRegistryManifest,
    applied_by_identity_sha256: str,
) -> str:
    actor_digest = applied_by_identity_sha256.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", actor_digest):
        raise RuntimeError("Registry actor digest is invalid")
    now = utc_now_iso()
    revision_id = str(
        uuid.uuid5(uuid.UUID("694d7e9f-1084-41c7-b823-672a8b36d50f"), f"{tenant_id}:{manifest.sha256}")
    )
    existing = connection.execute(
        "SELECT status FROM identity_registry_revisions WHERE tenant_id = ? AND manifest_sha256 = ?",
        (tenant_id, manifest.sha256),
    ).fetchone()
    if existing is not None and str(existing["status"]) == "applied":
        return revision_id

    subject_rows = connection.execute(
        "SELECT subject_id FROM platform_subjects WHERE tenant_id = ?",
        (tenant_id,),
    ).fetchall()
    known_subjects = {str(row["subject_id"]) for row in subject_rows}
    subjects_by_key: dict[str, str] = {}
    for subject_id in known_subjects:
        subject_key = subject_id.casefold()
        if subject_key in subjects_by_key and subjects_by_key[subject_key] != subject_id:
            raise RuntimeError(
                "Platform contains ambiguous case-insensitive subject identifiers"
            )
        subjects_by_key[subject_key] = subject_id
    requested_subjects = {
        subject_id for user in manifest.users for subject_id, _ in user.entitlements
    }
    missing = sorted(
        subject_id
        for subject_id in requested_subjects
        if subject_id.casefold() not in subjects_by_key
    )
    if missing:
        raise RuntimeError("Identity registry references unknown subjects: " + ", ".join(missing))

    connection.execute(
        """
        INSERT INTO identity_registry_revisions(
            revision_id, tenant_id, manifest_sha256, apply_mode, status,
            user_count, role_count, entitlement_count,
            applied_by_identity_sha256, created_at
        ) VALUES (?, ?, ?, ?, 'validated', ?, ?, ?, ?, now())
        ON CONFLICT (tenant_id, manifest_sha256) DO UPDATE
        SET status = 'validated', error_code = NULL, applied_at = NULL
        """,
        (
            revision_id,
            tenant_id,
            manifest.sha256,
            manifest.mode,
            len(manifest.users),
            manifest.role_count,
            manifest.entitlement_count,
            actor_digest,
        ),
    )
    desired_ids = {user.object_id for user in manifest.users}
    for user in manifest.users:
        connection.execute(
            """
            INSERT INTO app_users(
                tenant_id, entra_object_id, email, display_name, role_name,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT (tenant_id, entra_object_id) DO UPDATE
            SET email = excluded.email, display_name = excluded.display_name,
                role_name = excluded.role_name, status = 'active',
                updated_at = excluded.updated_at
            """,
            (
                tenant_id,
                user.object_id,
                user.email,
                user.display_name,
                _primary_legacy_role(user.roles),
                now,
                now,
            ),
        )
        current_roles = connection.execute(
            """
            SELECT role_name FROM app_user_roles
            WHERE tenant_id = ? AND entra_object_id = ?
              AND role_status = 'active' AND effective_to IS NULL
            """,
            (tenant_id, user.object_id),
        ).fetchall()
        current = {str(row["role_name"]) for row in current_roles}
        for removed in sorted(current - set(user.roles)):
            connection.execute(
                """
                UPDATE app_user_roles
                SET role_status = 'revoked', effective_to = now(), updated_at = now()
                WHERE tenant_id = ? AND entra_object_id = ? AND role_name = ?
                  AND role_status = 'active' AND effective_to IS NULL
                """,
                (tenant_id, user.object_id, removed),
            )
        for added in sorted(set(user.roles) - current):
            connection.execute(
                """
                INSERT INTO app_user_roles(
                    tenant_id, entra_object_id, role_name, role_status,
                    effective_from, granted_by_identity_sha256, reason
                ) VALUES (?, ?, ?, 'active', now(), ?, 'Applied from integrity-bound identity registry')
                """,
                (tenant_id, user.object_id, added, actor_digest),
            )
        desired_access = {
            subjects_by_key[subject_id.casefold()]: access_level
            for subject_id, access_level in user.entitlements
        }
        current_access = connection.execute(
            """
            SELECT subject_id, access_level FROM user_account_access
            WHERE tenant_id = ? AND entra_object_id = ?
            """,
            (tenant_id, user.object_id),
        ).fetchall()
        for row in current_access:
            subject_id = str(row["subject_id"])
            if subject_id not in desired_access:
                connection.execute(
                    """
                    DELETE FROM user_account_access
                    WHERE tenant_id = ? AND entra_object_id = ? AND subject_id = ?
                    """,
                    (tenant_id, user.object_id, subject_id),
                )
        for subject_id, access_level in sorted(desired_access.items()):
            connection.execute(
                """
                INSERT INTO user_account_access(
                    tenant_id, entra_object_id, subject_id, access_level,
                    granted_at, granted_by
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (tenant_id, entra_object_id, subject_id) DO UPDATE
                SET access_level = excluded.access_level,
                    granted_at = excluded.granted_at, granted_by = excluded.granted_by
                """,
                (tenant_id, user.object_id, subject_id, access_level, now, actor_digest),
            )
    if manifest.mode == "replace":
        rows = connection.execute(
            "SELECT entra_object_id FROM app_users WHERE tenant_id = ? AND status = 'active'",
            (tenant_id,),
        ).fetchall()
        for row in rows:
            object_id = str(row["entra_object_id"]).lower()
            if object_id in desired_ids:
                continue
            connection.execute(
                "UPDATE app_users SET status = 'disabled', updated_at = ? WHERE tenant_id = ? AND entra_object_id = ?",
                (now, tenant_id, object_id),
            )
            connection.execute(
                """
                UPDATE app_user_roles SET role_status = 'revoked', effective_to = now(), updated_at = now()
                WHERE tenant_id = ? AND entra_object_id = ?
                  AND role_status = 'active' AND effective_to IS NULL
                """,
                (tenant_id, object_id),
            )
            connection.execute(
                "DELETE FROM user_account_access WHERE tenant_id = ? AND entra_object_id = ?",
                (tenant_id, object_id),
            )
    connection.execute(
        """
        UPDATE identity_registry_revisions
        SET status = 'applied', applied_at = now(), error_code = NULL
        WHERE tenant_id = ? AND revision_id = ?
        """,
        (tenant_id, revision_id),
    )
    return revision_id
