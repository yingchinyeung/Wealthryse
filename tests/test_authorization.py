from __future__ import annotations

import unittest

from ibkr_etl.authorization import may_view_subject, resolve_authorized_identity


class _Result:
    def __init__(self, *, one=None, many=None):
        self.one = one
        self.many = many or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class _Connection:
    def __init__(self, results):
        self.results = list(results)

    def execute(self, query, params):
        return self.results.pop(0)


class AuthorizationTests(unittest.TestCase):
    def test_identity_requires_matching_object_email_active_user_and_role(self):
        connection = _Connection(
            [
                _Result(
                    one={
                        "entra_object_id": "object-1",
                        "email": "analyst@example.com",
                        "display_name": "Analyst",
                        "status": "active",
                    }
                ),
                _Result(many=[{"role_name": "viewer"}]),
            ]
        )
        identity = resolve_authorized_identity(
            connection,
            tenant_id="tenant",
            object_id="OBJECT-1",
            email="ANALYST@EXAMPLE.COM",
        )
        self.assertIsNotNone(identity)
        self.assertEqual(identity.primary_role, "viewer")

    def test_identity_without_active_role_fails_closed(self):
        connection = _Connection(
            [
                _Result(
                    one={
                        "entra_object_id": "object-1",
                        "email": "analyst@example.com",
                        "display_name": None,
                        "status": "active",
                    }
                ),
                _Result(many=[]),
            ]
        )
        self.assertIsNone(
            resolve_authorized_identity(
                connection,
                tenant_id="tenant",
                object_id="object-1",
                email="analyst@example.com",
            )
        )

    def test_active_subject_requires_resource_entitlement(self):
        self.assertFalse(
            may_view_subject(roles=["viewer"], access_levels=[], lifecycle_status="active")
        )
        self.assertTrue(
            may_view_subject(
                roles=["viewer"], access_levels=["read"], lifecycle_status="active"
            )
        )
        self.assertTrue(
            may_view_subject(
                roles=["owner"], access_levels=[], lifecycle_status="active"
            )
        )

    def test_preview_requires_operator_or_approver_and_scoped_permission(self):
        self.assertFalse(
            may_view_subject(
                roles=["viewer"], access_levels=["read"], lifecycle_status="preview"
            )
        )
        self.assertTrue(
            may_view_subject(
                roles=["onboarding_operator"],
                access_levels=["onboard"],
                lifecycle_status="onboarding",
            )
        )


if __name__ == "__main__":
    unittest.main()
