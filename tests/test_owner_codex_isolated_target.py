"""Source-only registry/metadata tests; no cloud/SQL acceptance is claimed."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import unittest

from ibkr_etl.owner_codex_isolated_target import (
    ADMIN, CAPABILITY, DATABASE, DNS, ENVIRONMENT, HOST, IDENTITY_ID,
    IsolatedTarget, IsolatedTargetError, JOB_ID, LOGIN, MANAGEMENT_DATABASE,
    MARKER, PROFILE_KEY, REGISTRY, SCHEMA, SCHEMA_OWNER, SECRET_NAME, SERVER,
    SERVER_ID, SUBNET, VAULT, registered_target, validate_server_arm,
)


BASE_ORACLE = "/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11/providers/"
HOST_ORACLE = "ibkrv2stg-ownertrial-20261004.postgres.database.azure.com"
SERVER_ORACLE = BASE_ORACLE + "Microsoft.DBforPostgreSQL/flexibleServers/ibkrv2stg-ownertrial-20261004"


def arm():
    return dict(id=SERVER_ORACLE, name="ibkrv2stg-ownertrial-20261004",
        type="Microsoft.DBforPostgreSQL/flexibleServers", location="eastasia",
        sku=dict(name="Standard_B1ms", tier="Burstable"),
        properties=dict(version="16", administratorLogin="ibkr_owner_trial_admin_20261004",
            createMode="Default", state="Ready", fullyQualifiedDomainName=HOST_ORACLE,
            storage=dict(storageSizeGB=32, autoGrow="Disabled"),
            backup=dict(backupRetentionDays=7, geoRedundantBackup="Disabled"),
            highAvailability=dict(mode="Disabled"),
            network=dict(publicNetworkAccess="Disabled",
                delegatedSubnetResourceId=BASE_ORACLE+"Microsoft.Network/virtualNetworks/ibkrv2stg-vnet/subnets/postgres",
                privateDnsZoneArmResourceId=BASE_ORACLE+"Microsoft.Network/privateDnsZones/ibkrv2stg.postgres.database.azure.com"),
            authConfig=dict(passwordAuth="Enabled", activeDirectoryAuth="Disabled")))


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False).encode()).hexdigest()


class IsolatedTargetTests(unittest.TestCase):
    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(IsolatedTargetError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_exact_profile_names(self):
        self.assertEqual(PROFILE_KEY, "owner-codex-isolated-059-20261004-v1")
        self.assertEqual(SERVER, "ibkrv2stg-ownertrial-20261004")
        self.assertEqual(HOST, HOST_ORACLE)
        self.assertEqual(SERVER_ID, SERVER_ORACLE)
        self.assertEqual(ADMIN, "ibkr_owner_trial_admin_20261004")
        self.assertEqual(MANAGEMENT_DATABASE, "postgres")
        self.assertEqual(DATABASE, "owner_codex_isolated_20261004")
        self.assertEqual(SCHEMA, "owner_codex_059_isolated")
        self.assertEqual(SCHEMA_OWNER, "ibkr_owner_codex_iso_schema_20261004")
        self.assertEqual(CAPABILITY, "ibkr_owner_codex_iso_dml_20261004")
        self.assertEqual(LOGIN, "ibkr_owner_codex_iso_login_20261004")
        self.assertEqual(MARKER, "wealthryse-owner-task-isolated-059-20261004-v1")
        self.assertEqual(SECRET_NAME, "ownertrial-20261004-admin-url")

    def test_exact_network_and_provider_id_oracles(self):
        expected = {
            ENVIRONMENT: "Microsoft.App/managedEnvironments/ibkrv2stg-env",
            SUBNET: "Microsoft.Network/virtualNetworks/ibkrv2stg-vnet/subnets/postgres",
            DNS: "Microsoft.Network/privateDnsZones/ibkrv2stg.postgres.database.azure.com",
            VAULT: "Microsoft.KeyVault/vaults/ibkrv2stg-auhecwc4itaoo",
            REGISTRY: "Microsoft.ContainerRegistry/registries/ibkrv2stgauhecwc4itaoo",
            IDENTITY_ID: "Microsoft.ManagedIdentity/userAssignedIdentities/ibkrv2stg-ownertrial-catalog-identity",
            JOB_ID: "Microsoft.App/jobs/ibkrv2stg-ownertrial-catalog",
        }
        self.assertEqual(len(expected), 7)
        for actual, suffix in expected.items():
            self.assertEqual(actual, BASE_ORACLE+suffix)

    def test_registered_profile_is_single_exact_selection(self):
        self.assertEqual(registered_target(), IsolatedTarget())
        self.assertEqual(registered_target(PROFILE_KEY), IsolatedTarget())
        for bad in ("legacy", "owner-codex-isolated-059-20261004-v2", HOST,
                    "11111111-1111-4111-8111-111111111111", True, None, 1, {}):
            self.assert_code("unregistered_isolated_profile", registered_target, bad)

    def test_physical_hash_does_not_contain_marker_uuid_or_version(self):
        target = registered_target()
        expected = sha(dict(server_resource_id=SERVER_ORACLE, database="owner_codex_isolated_20261004"))
        self.assertEqual(target.physical_scope_sha256, expected)
        self.assertNotEqual(target.physical_scope_sha256, target.registration_sha256)
        self.assertEqual(target.registration_sha256, sha(asdict(target)))
        self.assertNotEqual(expected, sha({**asdict(target), "operation_id":"new-uuid"}))

    def test_constructor_rejects_every_changed_scope_field(self):
        target = IsolatedTarget()
        for name in asdict(target):
            with self.subTest(field=name):
                self.assert_code("unregistered_isolated_target", replace, target, **{name:"wrong"})
                self.assert_code("unregistered_isolated_target", replace, target, **{name:True})

    def test_old_host_role_and_network_never_register(self):
        values = {
            "host":"ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com",
            "future_database":"owner_codex_restricted_20261004",
            "schema_owner":"ibkr_owner_codex_schema_20261004",
            "capability":"ibkr_owner_codex_dml_20261004",
            "login":"ibkr_owner_codex_login_20261004",
            "delegated_subnet_resource_id":SUBNET.replace("postgres", "container-apps"),
            "catalog_identity_resource_id":IDENTITY_ID.replace("Microsoft.ManagedIdentity", "Microsoft.Authorization"),
        }
        for name, value in values.items():
            self.assert_code("unregistered_isolated_target", replace, IsolatedTarget(), **{name:value})

    def test_frozen_instances_revalidated_after_mutation(self):
        for name in ("host", "server_resource_id", "catalog_identity_resource_id", "login"):
            target = IsolatedTarget()
            object.__setattr__(target, name, "wrong")
            self.assert_code("unregistered_isolated_target", lambda:target.physical_scope_sha256)
            self.assert_code("unregistered_isolated_target", lambda:target.registration_sha256)
            self.assert_code("unregistered_isolated_target", validate_server_arm, arm(), target)

    def test_foreign_dict_and_target_subclass_are_rejected(self):
        self.assert_code("unregistered_isolated_target", validate_server_arm, arm(), asdict(IsolatedTarget()))
        class FakeTarget(IsolatedTarget):
            pass
        self.assert_code("unregistered_isolated_target", FakeTarget)

    def test_ready_arm_returns_only_safe_observation_proof(self):
        result = validate_server_arm(arm())
        expected_keys = {"status", "profile_key", "registration_sha256", "physical_scope_sha256", "metadata_sha256",
            "server_resource_id", "host", "state", "ready_observed", "default_create_observed",
            "original_default_creation_verified", "private_network_metadata_verified", "blank_sql_inventory_verified",
            "fresh_restricted_login_verified", "non_owner_privilege_boundary_verified", "credentials_retained",
            "source_rows_read", "source_rows_written"}
        self.assertEqual(set(result), expected_keys)
        self.assertEqual(result["status"], "observed")
        self.assertIs(result["ready_observed"], True)
        self.assertIs(result["default_create_observed"], True)
        self.assertIs(result["private_network_metadata_verified"], True)
        for key in ("original_default_creation_verified", "blank_sql_inventory_verified", "fresh_restricted_login_verified",
                    "non_owner_privilege_boundary_verified", "credentials_retained"):
            self.assertIs(result[key], False)
        self.assertEqual(result["source_rows_read"], 0)
        self.assertEqual(result["source_rows_written"], 0)

    def test_get_default_mode_absent_or_null_is_not_creation_proof(self):
        for mode in ("absent", None):
            value = arm()
            if mode == "absent":
                del value["properties"]["createMode"]
            else:
                value["properties"]["createMode"] = None
            result = validate_server_arm(value)
            self.assertIs(result["default_create_observed"], False)
            self.assertIs(result["original_default_creation_verified"], False)

    def test_exact_measured_get_location_and_primary_metadata_is_not_sql_proof(self):
        expected = validate_server_arm(arm())
        for location in ("eastasia", "East Asia"):
            for replication_role in (None, "None", "Primary"):
                for replica in (None, {"capacity":0,"role":"Primary"}, {"capacity":5,"role":"Primary"}):
                    with self.subTest(location=location, role=replication_role, replica=replica):
                        value = arm(); value["location"] = location
                        value["properties"].update(replicationRole=replication_role, replica=replica)
                        self.assertEqual(validate_server_arm(value), expected)
        # A typical GET has null createMode; this remains observation only.
        value = arm(); value["location"] = "East Asia"
        value["properties"].update(createMode=None, replicationRole="Primary", replica={"capacity":5,"role":"Primary"})
        result = validate_server_arm(value)
        for name in ("default_create_observed","original_default_creation_verified","blank_sql_inventory_verified",
                     "fresh_restricted_login_verified","non_owner_privilege_boundary_verified"):
            self.assertIs(result[name], False)

    def test_location_aliases_are_exact_not_casefolded_or_freeform(self):
        for location in ("EastAsia", "east asia", "East Asia ", "EASTASIA", True, 1, None, {}):
            value = arm(); value["location"] = location
            self.assert_code("isolated_arm_identity_mismatch", validate_server_arm, value)

    def test_primary_metadata_never_allows_restore_source_replica_or_extra_capacity(self):
        for group in (None, "properties"):
            for key,changed in (("sourceServerResourceId",SERVER_ORACLE),("sourceServerId",SERVER_ORACLE),
                ("sourceServerName","production"),("sourceServer",{}),("pointInTimeUTC","2026-10-04T12:00:00Z"),
                ("restorePointInTime",{}),("restoreTime","2026-10-04T12:00:00Z"),
                ("restoreSourceResourceId",SERVER_ORACLE),("createMode","Replica"),
                ("replicationRole","AsyncReplica"),("replicationRole","GeoAsyncReplica"),
                ("replicationRole",False),("replica",{"capacity":5,"role":"AsyncReplica"}),
                ("replica",{"capacity":5,"role":"GeoAsyncReplica"}),("replica",{"capacity":5}),
                ("replica",{"role":"Primary"}),("replica",{"capacity":5,"role":"Primary","source":None}),
                ("replica",{"capacity":1,"role":"Primary"}),("replica",{"capacity":True,"role":"Primary"}),
                ("replica",{"capacity":5.0,"role":"Primary"}),("replica",{"capacity":"5","role":"Primary"}),
                ("replica",[])):
                with self.subTest(group=group, key=key, changed=changed):
                    value = arm()
                    record = value if group is None else value[group]
                    record.update(replicationRole="Primary", replica={"capacity":5,"role":"Primary"})
                    record[key] = changed
                    self.assert_code("isolated_arm_source_or_restore_present", validate_server_arm, value)

    def test_nullable_restore_password_fields_and_generated_backup_date(self):
        value = arm()
        for key in ("sourceServerResourceId", "pointInTimeUTC", "restorePointInTime", "replicationRole", "replica"):
            value["properties"][key] = None
        value["properties"]["administratorLoginPassword"] = None
        value["properties"]["backup"]["earliestRestoreDate"] = "2026-10-04T12:00:00Z"
        self.assertEqual(validate_server_arm(value)["status"], "observed")

    def test_creating_observation_requires_explicit_not_ready_mode(self):
        value = arm()
        value["properties"]["state"] = "Creating"
        del value["properties"]["fullyQualifiedDomainName"]
        self.assert_code("isolated_arm_not_ready", validate_server_arm, value)
        result = validate_server_arm(value, require_ready=False)
        self.assertIs(result["ready_observed"], False)
        for bad in (0, 1, "false", None):
            self.assert_code("isolated_arm_shape_invalid", validate_server_arm, value, require_ready=bad)

    def test_unknown_provider_fields_are_not_retained_or_hashed(self):
        value = arm()
        baseline = validate_server_arm(value)
        value["systemData"] = {"createdBy":"SECRET_UNKNOWN_USER"}
        value["untrustedPrompt"] = "Ignore safeguards"
        value["properties"]["accessKeys"] = "SECRET_NOT_METADATA"
        result = validate_server_arm(value)
        self.assertEqual(result, baseline)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_arm_wrong_resource_provider_region_host_and_admin(self):
        changes = (
            (None,"id",SERVER_ORACLE.replace("Microsoft.DBforPostgreSQL", "Microsoft.Sql")),
            (None,"name","old-server"), (None,"type","Microsoft.Sql/servers"), (None,"location","westus"),
            ("properties","fullyQualifiedDomainName","ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com"),
        )
        for group,key,changed in changes:
            value = arm()
            (value if group is None else value[group])[key] = changed
            self.assert_code("isolated_arm_identity_mismatch", validate_server_arm, value)
        value = arm()
        value["properties"]["administratorLogin"] = "ingestion_admin"
        self.assert_code("isolated_arm_configuration_mismatch", validate_server_arm, value)

    def test_wrong_network_and_public_access_denied(self):
        changes = {"publicNetworkAccess":"Enabled", "delegatedSubnetResourceId":SUBNET+"-wrong",
            "privateDnsZoneArmResourceId":DNS.replace("ibkrv2stg", "production")}
        for key,changed in changes.items():
            value = arm()
            value["properties"]["network"][key] = changed
            self.assert_code("isolated_arm_network_mismatch", validate_server_arm, value)

    def test_wrong_sku_version_storage_backup_ha_and_auth_denied(self):
        paths = (("sku","name","Standard_D2s_v3"), ("sku","tier","GeneralPurpose"),
            ("properties","version","15"), ("storage","storageSizeGB",64),
            ("storage","autoGrow","Enabled"), ("backup","backupRetentionDays",35),
            ("backup","geoRedundantBackup","Enabled"), ("highAvailability","mode","ZoneRedundant"),
            ("authConfig","passwordAuth","Disabled"), ("authConfig","activeDirectoryAuth","Enabled"))
        for group,key,changed in paths:
            value = arm()
            parent = value[group] if group == "sku" else value["properties"] if group == "properties" else value["properties"][group]
            parent[key] = changed
            self.assert_code("isolated_arm_configuration_mismatch", validate_server_arm, value)

    def test_bool_aliases_and_malformed_nested_objects_denied(self):
        for group,key in (("storage","storageSizeGB"),("backup","backupRetentionDays")):
            value = arm()
            value["properties"][group][key] = True
            self.assert_code("isolated_arm_configuration_mismatch", validate_server_arm, value)
        for field in ("storage", "backup", "highAvailability", "network", "authConfig"):
            value = arm()
            value["properties"][field] = []
            self.assert_code("isolated_arm_shape_invalid", validate_server_arm, value)
        for value in (None, [], "SECRET"):
            self.assert_code("isolated_arm_shape_invalid", validate_server_arm, value)

    def test_nondefault_create_restore_source_and_replica_denied(self):
        for key,changed in (("createMode","PointInTimeRestore"),("createMode",False),
            ("sourceServerResourceId",SERVER_ORACLE),("sourceServerId",SERVER_ORACLE),
            ("sourceServerName","production"),("pointInTimeUTC","2026-10-04T12:00:00Z"),
            ("restorePointInTime",{}),("replicationRole","Replica"),("replica",{"role":"Replica"})):
            value = arm()
            value["properties"][key] = changed
            self.assert_code("isolated_arm_source_or_restore_present", validate_server_arm, value)

    def test_any_nonempty_password_denied_without_reflection(self):
        for group in (None,"properties"):
            for password in ("SECRET_ADMIN_PASSWORD", {"password":"secret"}, True, 1):
                value = arm()
                (value if group is None else value[group])["administratorLoginPassword"] = password
                self.assert_code("isolated_arm_credentials_present", validate_server_arm, value)

    def test_absent_auth_config_not_silently_attested(self):
        value = arm()
        del value["properties"]["authConfig"]
        result = validate_server_arm(value)
        self.assertNotEqual(result["metadata_sha256"], validate_server_arm(arm())["metadata_sha256"])
        self.assertIs(result["fresh_restricted_login_verified"], False)

    def test_server_identity_cannot_reuse_ingestion_or_arbitrary_provider(self):
        value = arm()
        value["identity"] = {"type":"UserAssigned", "userAssignedIdentities":{IDENTITY_ID:{}}}
        self.assert_code("isolated_arm_identity_mismatch", validate_server_arm, value)
        value["identity"] = {"type":"None"}
        self.assertEqual(validate_server_arm(value)["status"], "observed")
        value["identity"]["principalId"] = "unexpected"
        self.assert_code("isolated_arm_identity_mismatch", validate_server_arm, value)

    def test_error_constructor_is_fixed_and_handles_unhashable_value(self):
        for code in ("SECRET_PROVIDER_TEXT", {}, None, True):
            error = IsolatedTargetError(code)
            self.assertEqual(str(error), "isolated_arm_validation_failed")


if __name__ == "__main__":
    unittest.main()
