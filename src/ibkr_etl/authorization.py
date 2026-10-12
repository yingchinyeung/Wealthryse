from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


ROLE_PRECEDENCE = (
    "owner",
    "platform_admin",
    "final_approver",
    "compliance_reviewer",
    "portfolio_analyst_reviewer",
    "onboarding_operator",
    "viewer",
)
SUBJECT_ACCESS = frozenset({"read", "report", "analyze", "onboard", "approve", "admin"})


@dataclass(frozen=True)
class AuthorizedIdentity:
    object_id: str
    email: str
    display_name: str | None
    roles: tuple[str, ...]

    @property
    def primary_role(self) -> str:
        for role in ROLE_PRECEDENCE:
            if role in self.roles:
                return role
        raise RuntimeError("Authorized identity has no recognized role")


def resolve_authorized_identity(
    connection, *, tenant_id: str, object_id: str, email: str
) -> AuthorizedIdentity | None:
    normalized_object_id = object_id.strip().lower()
    normalized_email = email.strip().lower()
    if not normalized_object_id or not normalized_email:
        return None
    user = connection.execute(
        """
        SELECT entra_object_id, email, display_name, status
        FROM app_users
        WHERE tenant_id = ? AND lower(entra_object_id) = ? AND lower(email) = ?
        """,
        (tenant_id, normalized_object_id, normalized_email),
    ).fetchone()
    if user is None or str(user["status"]) != "active":
        return None
    rows = connection.execute(
        """
        SELECT role_name
        FROM app_user_roles
        WHERE tenant_id = ? AND lower(entra_object_id) = ?
          AND role_status = 'active' AND effective_from <= now()
          AND (effective_to IS NULL OR effective_to >= now())
        ORDER BY role_name
        """,
        (tenant_id, normalized_object_id),
    ).fetchall()
    roles = tuple(sorted({str(row["role_name"]) for row in rows}))
    if not roles or any(role not in ROLE_PRECEDENCE for role in roles):
        return None
    return AuthorizedIdentity(
        object_id=normalized_object_id,
        email=normalized_email,
        display_name=(str(user["display_name"]) if user["display_name"] else None),
        roles=roles,
    )


def subject_access_levels(
    connection, *, tenant_id: str, object_id: str, subject_id: str
) -> frozenset[str]:
    rows = connection.execute(
        """
        SELECT access_level
        FROM user_account_access
        WHERE tenant_id = ? AND lower(entra_object_id) = ? AND subject_id = ?
        """,
        (tenant_id, object_id.strip().lower(), subject_id),
    ).fetchall()
    levels = frozenset(str(row["access_level"]) for row in rows)
    if not levels.issubset(SUBJECT_ACCESS):
        raise RuntimeError("Subject access registry contains an unsupported permission")
    return levels


def may_view_subject(
    *, roles: Iterable[str], access_levels: Iterable[str], lifecycle_status: str
) -> bool:
    role_set = frozenset(roles)
    access_set = frozenset(access_levels)
    if role_set & {"owner", "platform_admin"}:
        return True
    if lifecycle_status in {"draft", "onboarding", "preview"}:
        return bool(role_set & {"onboarding_operator", "final_approver"}) and bool(
            access_set & {"onboard", "approve", "admin"}
        )
    if lifecycle_status != "active":
        return False
    return bool(access_set & {"read", "report", "analyze", "approve", "admin"})
