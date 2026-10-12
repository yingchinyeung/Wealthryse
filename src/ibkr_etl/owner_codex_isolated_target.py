"""Inert exact registration for ONE newly approved blank PostgreSQL host.

Names select reviewed scope, not credentials, approval or provisioning. No IO,
environment access, SDK, connector, SQL, CLI, journal reset or live-execution flag
is present. The old shared-host target and consumed journals are not retargeted.
Observed ARM metadata does not prove original Default creation, an empty SQL
database, fresh restricted-login authentication or a non-owner privilege boundary.
Those require separately authenticated, protected evidence and behavioral tests.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
from types import MappingProxyType


PROFILE_KEY = "owner-codex-isolated-059-20261004-v1"
SUBSCRIPTION = "3956e821-cee4-403f-8708-e84e2ed490eb"
RESOURCE_GROUP = "rg-ibkr-database-v11"
LOCATION = "eastasia"
BASE = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}/providers/"
SERVER = "ibkrv2stg-ownertrial-20261004"
HOST = SERVER + ".postgres.database.azure.com"
SERVER_ID = BASE + "Microsoft.DBforPostgreSQL/flexibleServers/" + SERVER
ADMIN = "ibkr_owner_trial_admin_20261004"
MANAGEMENT_DATABASE = "postgres"
DATABASE = "owner_codex_isolated_20261004"
SCHEMA = "owner_codex_059_isolated"
SCHEMA_OWNER = "ibkr_owner_codex_iso_schema_20261004"
CAPABILITY = "ibkr_owner_codex_iso_dml_20261004"
LOGIN = "ibkr_owner_codex_iso_login_20261004"
MARKER = "wealthryse-owner-task-isolated-059-20261004-v1"
IDENTITY = "ibkrv2stg-ownertrial-catalog-identity"
IDENTITY_ID = BASE + "Microsoft.ManagedIdentity/userAssignedIdentities/" + IDENTITY
JOB = "ibkrv2stg-ownertrial-catalog"
JOB_ID = BASE + "Microsoft.App/jobs/" + JOB
SECRET_NAME = "ownertrial-20261004-admin-url"
ENVIRONMENT = BASE + "Microsoft.App/managedEnvironments/ibkrv2stg-env"
SUBNET = BASE + "Microsoft.Network/virtualNetworks/ibkrv2stg-vnet/subnets/postgres"
DNS = BASE + "Microsoft.Network/privateDnsZones/ibkrv2stg.postgres.database.azure.com"
VAULT = BASE + "Microsoft.KeyVault/vaults/ibkrv2stg-auhecwc4itaoo"
REGISTRY = BASE + "Microsoft.ContainerRegistry/registries/ibkrv2stgauhecwc4itaoo"


class IsolatedTargetError(ValueError):
    """Fixed metadata codes; no provider exceptions or credential reflection."""
    def __init__(self, code):
        self.code = code if type(code) is str and code in {
            "unregistered_isolated_target", "unregistered_isolated_profile", "isolated_arm_shape_invalid",
            "isolated_arm_identity_mismatch", "isolated_arm_configuration_mismatch",
            "isolated_arm_network_mismatch", "isolated_arm_source_or_restore_present",
            "isolated_arm_credentials_present", "isolated_arm_not_ready", "isolated_arm_validation_failed",
        } else "isolated_arm_validation_failed"
        super().__init__(self.code)


def _require(condition, code):
    if not condition:
        raise IsolatedTargetError(code)


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False).encode()).hexdigest()


_EXPECTED = MappingProxyType(dict(profile_key=PROFILE_KEY, subscription_id=SUBSCRIPTION,
    resource_group=RESOURCE_GROUP, location=LOCATION, server_name=SERVER, host=HOST,
    server_resource_id=SERVER_ID, administrator_login=ADMIN, management_database=MANAGEMENT_DATABASE,
    future_database=DATABASE, schema=SCHEMA, schema_owner=SCHEMA_OWNER, capability=CAPABILITY,
    login=LOGIN, marker=MARKER, catalog_identity_name=IDENTITY, catalog_identity_resource_id=IDENTITY_ID,
    catalog_job_name=JOB, catalog_job_resource_id=JOB_ID, admin_secret_name=SECRET_NAME,
    environment_resource_id=ENVIRONMENT, delegated_subnet_resource_id=SUBNET,
    private_dns_zone_resource_id=DNS, vault_resource_id=VAULT, registry_resource_id=REGISTRY))


@dataclass(frozen=True)
class IsolatedTarget:
    profile_key: str = PROFILE_KEY
    subscription_id: str = SUBSCRIPTION
    resource_group: str = RESOURCE_GROUP
    location: str = LOCATION
    server_name: str = SERVER
    host: str = HOST
    server_resource_id: str = SERVER_ID
    administrator_login: str = ADMIN
    management_database: str = MANAGEMENT_DATABASE
    future_database: str = DATABASE
    schema: str = SCHEMA
    schema_owner: str = SCHEMA_OWNER
    capability: str = CAPABILITY
    login: str = LOGIN
    marker: str = MARKER
    catalog_identity_name: str = IDENTITY
    catalog_identity_resource_id: str = IDENTITY_ID
    catalog_job_name: str = JOB
    catalog_job_resource_id: str = JOB_ID
    admin_secret_name: str = SECRET_NAME
    environment_resource_id: str = ENVIRONMENT
    delegated_subnet_resource_id: str = SUBNET
    private_dns_zone_resource_id: str = DNS
    vault_resource_id: str = VAULT
    registry_resource_id: str = REGISTRY

    def __post_init__(self):
        actual = asdict(self)
        _require(type(self) is IsolatedTarget and actual == dict(_EXPECTED)
            and all(type(value) is str for value in actual.values()), "unregistered_isolated_target")

    @property
    def physical_scope_sha256(self):
        self.__post_init__()
        # Neither marker/profile revision nor operation UUID/mode/source version
        # may make the same physical target look like a fresh execution scope.
        return _sha(dict(server_resource_id=self.server_resource_id, database=self.future_database))

    @property
    def registration_sha256(self):
        self.__post_init__()
        return _sha(asdict(self))


def registered_target(profile_key=PROFILE_KEY):
    _require(type(profile_key) is str and profile_key == PROFILE_KEY, "unregistered_isolated_profile")
    return IsolatedTarget()


def _target(value):
    _require(type(value) is IsolatedTarget, "unregistered_isolated_target")
    value.__post_init__()
    return IsolatedTarget()  # Never retain a caller's potentially mutated object.


def _object(value):
    _require(type(value) is dict, "isolated_arm_shape_invalid")
    return value


def _no_source(value):
    # These are restoration/replication inputs, not provider-generated dates
    # such as backup.earliestRestoreDate. Absent/null GET fields are legitimate.
    for key in ("sourceServerResourceId", "sourceServerId", "sourceServerName", "sourceServer",
                "pointInTimeUTC", "restorePointInTime", "restoreTime", "restoreSourceResourceId"):
        _require(key not in value or value[key] is None, "isolated_arm_source_or_restore_present")
    _require(value.get("createMode") in (None, "Default")
        and type(value.get("createMode")) in (str, type(None)), "isolated_arm_source_or_restore_present")
    _require(value.get("replicationRole") in (None, "None", "Primary")
        and type(value.get("replicationRole")) in (str, type(None)), "isolated_arm_source_or_restore_present")
    # Azure's GET metadata can describe a Primary with the default replica
    # capacity (5). Capacity is a provider limit, not an observed replica count.
    replica = value.get("replica")
    _require(replica is None or type(replica) is dict and set(replica) == {"capacity", "role"}
        and replica["role"] == "Primary" and type(replica["capacity"]) is int
        and replica["capacity"] in (0, 5), "isolated_arm_source_or_restore_present")


def _validate_server_arm(value, target, require_ready):
    target = _target(target)
    _require(type(require_ready) is bool, "isolated_arm_shape_invalid")
    value = _object(value)
    _require(value.get("id") == target.server_resource_id and value.get("name") == target.server_name
        and value.get("type") == "Microsoft.DBforPostgreSQL/flexibleServers"
        and type(value.get("location")) is str and value["location"] in (target.location, "East Asia"),
        "isolated_arm_identity_mismatch")
    properties, sku = _object(value.get("properties")), _object(value.get("sku"))
    _require(sku.get("name") == "Standard_B1ms" and sku.get("tier") == "Burstable"
        and properties.get("version") == "16" and properties.get("administratorLogin") == target.administrator_login,
        "isolated_arm_configuration_mismatch")
    for record in (value, properties):
        _no_source(record)
        _require(record.get("administratorLoginPassword") in (None, "")
            and type(record.get("administratorLoginPassword")) in (str, type(None)), "isolated_arm_credentials_present")
    identity = value.get("identity")
    _require(identity is None or type(identity) is dict and set(identity) <= {"type", "principalId", "tenantId", "userAssignedIdentities"}
        and identity.get("type") == "None" and identity.get("principalId") is None and identity.get("tenantId") is None
        and identity.get("userAssignedIdentities") is None, "isolated_arm_identity_mismatch")
    storage, network = _object(properties.get("storage")), _object(properties.get("network"))
    backup, ha = _object(properties.get("backup")), _object(properties.get("highAvailability"))
    _require(type(storage.get("storageSizeGB")) is int and storage["storageSizeGB"] == 32
        and storage.get("autoGrow") == "Disabled" and type(backup.get("backupRetentionDays")) is int
        and backup["backupRetentionDays"] == 7 and backup.get("geoRedundantBackup") == "Disabled"
        and ha.get("mode") == "Disabled", "isolated_arm_configuration_mismatch")
    _require(network.get("publicNetworkAccess") == "Disabled"
        and network.get("delegatedSubnetResourceId") == target.delegated_subnet_resource_id
        and network.get("privateDnsZoneArmResourceId") == target.private_dns_zone_resource_id,
        "isolated_arm_network_mismatch")
    auth = properties.get("authConfig")
    if auth is not None:
        auth = _object(auth)
        _require(auth.get("passwordAuth") == "Enabled" and auth.get("activeDirectoryAuth") == "Disabled",
            "isolated_arm_configuration_mismatch")
    state = properties.get("state")
    _require(type(state) is str and state in ("Ready", "Creating", "Updating"), "isolated_arm_not_ready")
    fqdn = properties.get("fullyQualifiedDomainName")
    _require(fqdn is None or type(fqdn) is str and fqdn == target.host, "isolated_arm_identity_mismatch")
    _require(not require_ready or state == "Ready" and fqdn == target.host, "isolated_arm_not_ready")
    # Only this compiler-selected safe metadata reaches the proof or its hash.
    public = dict(server_resource_id=target.server_resource_id, host=target.host, location=target.location,
        administrator_login=target.administrator_login, version="16", sku_name="Standard_B1ms", sku_tier="Burstable",
        storage_gib=32, storage_autogrow="Disabled", backup_retention_days=7, geo_backup="Disabled",
        ha_mode="Disabled", public_network_access="Disabled", delegated_subnet_resource_id=target.delegated_subnet_resource_id,
        private_dns_zone_resource_id=target.private_dns_zone_resource_id, auth_config_observed=auth is not None,
        password_auth_observed=None if auth is None else "Enabled",
        active_directory_auth_observed=None if auth is None else "Disabled", state=state,
        fqdn_observed=fqdn is not None, default_create_observed=properties.get("createMode") == "Default")
    return dict(status="observed", profile_key=target.profile_key,
        registration_sha256=target.registration_sha256, physical_scope_sha256=target.physical_scope_sha256,
        metadata_sha256=_sha(public), server_resource_id=target.server_resource_id, host=target.host,
        state=state, ready_observed=state == "Ready" and fqdn == target.host,
        default_create_observed=public["default_create_observed"], original_default_creation_verified=False,
        private_network_metadata_verified=True, blank_sql_inventory_verified=False,
        fresh_restricted_login_verified=False, non_owner_privilege_boundary_verified=False,
        credentials_retained=False, source_rows_read=0, source_rows_written=0)


def validate_server_arm(value, target=None, *, require_ready=True):
    """Validate observed GET metadata, not request provenance or SQL acceptance.

    A Default createMode may be absent/null on GET. Its presence is recorded but
    never certifies original creation; trusted control must bind its own exact
    public request definition and protected source separately. No raw response,
    password, secret value, URL or exception text is returned or retained.
    """
    try:
        return _validate_server_arm(value, IsolatedTarget() if target is None else target, require_ready)
    except IsolatedTargetError:
        raise
    except Exception:
        raise IsolatedTargetError("isolated_arm_validation_failed") from None
