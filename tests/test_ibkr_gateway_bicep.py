import base64
import datetime
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BICEP = ROOT / "infra" / "staging-ibkr-gateway.bicep"
DEPLOY = ROOT / "deploy" / "ibkr-gateway"
CLOUD_INIT = DEPLOY / "cloud-init.yaml"
COMPOSE = DEPLOY / "docker-compose.yml"
FETCH_SECRETS = DEPLOY / "fetch-secrets.sh"
RELOGIN_CHECK = DEPLOY / "relogin-check.sh"
SYSTEMD = DEPLOY / "systemd"

STAGING_TAGS = {
    "application": "ibkr-portfolio-platform",
    "environment": "staging",
    "owner": "portfolio-platform",
    "costCenter": "pilot",
    "version": "2.0",
    "dataClassification": "confidential-financial",
}
SECRETS = {"ibkr-username", "ibkr-password", "database-live-url"}


def _read(path):
    return path.read_text(encoding="utf-8")


def _deploy_files():
    return sorted(path for path in DEPLOY.rglob("*") if path.is_file())


def _param_default(text, name):
    match = re.search(rf"^param {name} \w+ = (.+)$", text, re.M)
    return match.group(1).strip("'") if match else None


def _security_rules(text):
    """{name: fields} for the NSG rules; 'ports' lists quoted ports plus parameter names."""
    start = text.index("securityRules: [")
    body = text[start:text.index("\n    ]\n", start)]
    rules = {}
    for chunk in re.split(r"\n\s*name: ", body)[1:]:
        name = re.match(r"'([^']+)'", chunk).group(1)
        fields = {key: value.strip("'") for key, value in
                  re.findall(r"\n\s*(\w+): ('[^']*'|[\w.]+)[ \t]*(?=\n)", chunk)}
        port_text = re.search(r"destinationPortRanges?: (.*?)\n\s*\}", chunk, re.S).group(1)
        fields["ports"] = re.findall(r"'([^']*)'", port_text) + (["ibkrPorts"] if "ibkrPorts" in port_text else [])
        rules[name] = fields
    return rules


def _role_assignments(text):
    pattern = r"^resource (\w+) 'Microsoft\.Authorization/roleAssignments@[^']+' = (if \([^\n]*\) )?\{\n(.*?)\n\}"
    return {name: {"condition": condition, "body": body}
            for name, condition, body in re.findall(pattern, text, re.M | re.S)}


def _compose_block(text, name, indent="  "):
    match = re.search(rf"^{indent}{re.escape(name)}:\n(.*?)(?=^{indent}\S|^\S|\Z)", text, re.M | re.S)
    return match.group(1)


def _compose_list(block, key):
    match = re.search(rf"^    {key}:\n((?:      - .*\n)+)", block, re.M)
    return [item.strip() for item in re.findall(r"- (.*)", match.group(1))] if match else []


def _compose_env(block):
    env = _compose_block(block, "environment", "    ")
    return {key: value.strip().strip('"') for key, value in re.findall(r"^      ([A-Z0-9_]+): (.*)$", env, re.M)}


def _unit_values(path, key):
    return re.findall(rf"^{key}=(.*)$", _read(path), re.M)


class IbkrGatewayNetworkTest(unittest.TestCase):
    def test_no_public_ip_anywhere(self):
        for path in [BICEP, *_deploy_files()]:
            self.assertNotIn("publicipaddress", _read(path).lower(), path)

    def test_firewall_allows_only_the_planned_traffic(self):
        text = _read(BICEP)
        rules = _security_rules(text)
        allows = {name: rule for name, rule in rules.items() if rule["access"] == "Allow"}
        expected = {
            "AllowBastionSshInbound": ("Inbound", "bastionSubnetPrefix", "*", ["22"]),
            "AllowPostgresOutbound": ("Outbound", "*", "postgresSubnetPrefix", ["5432"]),
            "AllowPrivateEndpointsOutbound": ("Outbound", "*", "privateEndpointPrefix", ["443"]),
            "AllowAzureDnsOutbound": ("Outbound", "*", "168.63.129.16", ["53"]),
            "AllowAzureHostAgentOutbound": ("Outbound", "*", "168.63.129.16", ["80", "32526"]),
            "AllowInstanceMetadataOutbound": ("Outbound", "*", "169.254.169.254", ["80"]),
            "AllowInternetOutbound": ("Outbound", "*", "Internet", ["80", "443", "ibkrPorts"]),
        }
        self.assertEqual(set(allows), set(expected))
        for name, (direction, source, destination, ports) in expected.items():
            rule = allows[name]
            self.assertEqual(
                (rule["direction"], rule["sourceAddressPrefix"], rule["destinationAddressPrefix"], rule["ports"]),
                (direction, source, destination, ports), name)
        self.assertEqual(_param_default(text, "bastionSubnetPrefix"), "10.50.8.0/26")
        self.assertEqual(_param_default(text, "postgresSubnetPrefix"), "10.50.4.0/28")
        self.assertEqual(_param_default(text, "privateEndpointPrefix"), "10.50.10.0/24")
        self.assertRegex(text, r"param ibkrPorts array = \[\s*'4000-4002'\s*\]")
        self.assertNotIn("AzureCloud", text)

    def test_firewall_denies_everything_else_both_ways(self):
        rules = _security_rules(_read(BICEP))
        for direction in ("Inbound", "Outbound"):
            deny = rules[f"DenyAll{direction}"]
            self.assertEqual(deny["direction"], direction)
            self.assertEqual(deny["access"], "Deny")
            for key in ("protocol", "sourceAddressPrefix", "destinationAddressPrefix"):
                self.assertEqual(deny[key], "*")
            self.assertEqual(deny["ports"], ["*"])
            for name, rule in rules.items():
                if rule["direction"] == direction and rule["access"] == "Allow":
                    self.assertLess(int(rule["priority"]), int(deny["priority"]), name)

    def test_nic_has_the_firewall_and_no_forwarding(self):
        text = _read(BICEP)
        nic = re.search(r"resource nic '[^']+' = \{(.*?)\n\}", text, re.S).group(1)
        self.assertIn("networkSecurityGroup: {\n      id: nsg.id", nic)
        self.assertIn("enableIPForwarding: false", nic)
        self.assertIn("id: subnetId", nic)
        self.assertRegex(text, r"(?m)^param subnetId string$")
        self.assertNotIn("Microsoft.Network/virtualNetworks", text)


class IbkrGatewayVmTest(unittest.TestCase):
    def test_names_size_and_tags(self):
        text = _read(BICEP)
        self.assertEqual(_param_default(text, "vmName"), "ibkrv2stg-ibkr-gateway")
        self.assertEqual(_param_default(text, "vmSize"), "Standard_D2as_v5")
        self.assertEqual(_param_default(text, "location"), "resourceGroup().location")
        for var, suffix in (("identityName", "identity"), ("nsgName", "nsg"), ("patchConfigurationName", "patch")):
            self.assertIn(f"var {var} = '${{vmName}}-{suffix}'", text)
        tags = dict(re.findall(r"^  (\w+): '([^']*)'$", re.search(r"var tags = \{\n(.*?)\n\}", text, re.S).group(1), re.M))
        self.assertEqual(tags, STAGING_TAGS)

    def test_trusted_launch_ubuntu_on_premium_ssd(self):
        text = _read(BICEP)
        for expected in (
            "securityType: 'TrustedLaunch'",
            "secureBootEnabled: true",
            "vTpmEnabled: true",
            "offer: 'ubuntu-24_04-lts'",
            "sku: 'server'",
            "diskSizeGB: 32",
            "storageAccountType: 'Premium_LRS'",
        ):
            self.assertIn(expected, text)

    def test_entra_ssh_only(self):
        text = _read(BICEP)
        self.assertIn("disablePasswordAuthentication: true", text)
        self.assertIn("type: 'AADSSHLoginForLinux'", text)
        self.assertIn("publisher: 'Microsoft.Azure.ActiveDirectory'", text)
        self.assertNotRegex(text, r"adminPassword")
        self.assertNotRegex(text, r"ssh-(rsa|ed25519) AAAA")
        admin = _role_assignments(text)["adminLogin"]
        self.assertEqual(admin["condition"], "if (!empty(adminGroupObjectId)) ")
        self.assertIn("scope: vm\n", admin["body"])
        self.assertIn("name: '1c0163c0-47e6-4577-8991-ea5c82e286e4'", text)

    def test_user_assigned_identity_is_attached(self):
        text = _read(BICEP)
        self.assertIn("resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@", text)
        vm = re.search(r"resource vm '[^']+' = \{(.*?)\n\}", text, re.S).group(1)
        self.assertIn("type: 'UserAssigned'", vm)
        self.assertIn("'${identity.id}': {}", vm)

    def test_secret_scoped_readers_and_registry_pull(self):
        text = _read(BICEP)
        for name in SECRETS:
            self.assertRegex(text, rf"resource \w+ 'secrets' existing = \{{\n    name: '{name}'\n")
        assignments = _role_assignments(text)
        scopes = {}
        for name, assignment in assignments.items():
            body = assignment["body"]
            self.assertTrue(body.lstrip().startswith("name: guid("), name)
            scope = re.search(r"scope: (\S+)", body).group(1)
            role = re.search(r"roleDefinitionId: (\S+)", body).group(1)
            scopes.setdefault(role, set()).add(scope)
            self.assertNotEqual(scope, "vault", f"{name} must not be vault-wide")
        self.assertEqual(scopes["keyVaultSecretsUserRole.id"],
                         {"vault::ibkrUsername", "vault::ibkrPassword", "vault::databaseLiveUrl"})
        self.assertEqual(scopes["acrPullRole.id"], {"registry"})
        self.assertEqual(set(scopes), {"keyVaultSecretsUserRole.id", "acrPullRole.id", "vmAdministratorLoginRole.id"})
        self.assertIn("name: '4633458b-17de-408a-b874-0445c86b69e6'", text)
        self.assertIn("name: '7f951dda-4ed3-4680-a7ca-43fe172d538d'", text)

    def test_weekly_patch_window_monday_2030_hong_kong(self):
        text = _read(BICEP)
        self.assertIn("maintenanceScope: 'InGuestPatch'", text)
        self.assertIn("recurEvery: '1Week Monday'", text)
        self.assertIn("startDateTime: '${patchWindowStartDate} 20:30'", text)
        # Windows time zone ID for UTC+8 Beijing, Chongqing, Hong Kong, Urumqi.
        self.assertIn("timeZone: 'China Standard Time'", text)
        start = datetime.date.fromisoformat(_param_default(text, "patchWindowStartDate"))
        self.assertEqual(start.strftime("%A"), "Monday")
        assignment = re.search(r"resource \w+ 'Microsoft\.Maintenance/configurationAssignments@[^']+' = \{(.*?)\n\}",
                               text, re.S).group(1)
        self.assertIn("scope: vm\n", assignment)
        self.assertIn("maintenanceConfigurationId: patchWindow.id", assignment)
        self.assertIn("patchMode: 'AutomaticByPlatform'", text)
        self.assertIn("bypassPlatformSafetyChecksOnUserSchedule: true", text)

    def test_outputs_and_header(self):
        text = _read(BICEP)
        for output in ("vmName", "identityClientId", "identityPrincipalId"):
            self.assertRegex(text, rf"(?m)^output {output} string = ")
        header = text[:text.index("targetScope")]
        self.assertIn("Not deployed by CI", header)
        self.assertIn("network thread", header)
        self.assertIn("Rollback: delete the VM, its OS disk and its NIC", header)


class IbkrGatewayCustomDataTest(unittest.TestCase):
    def _values(self):
        text = _read(BICEP)
        block = re.search(r"var cloudInitValues = \{\n(.*?)\n\}", text, re.S).group(1)
        return dict(re.findall(r"^  (__[A-Z0-9_]+__): (.+)$", block, re.M))

    def test_custom_data_is_cloud_init_with_every_placeholder_replaced(self):
        text = _read(BICEP)
        self.assertIn("customData: base64(cloudInit)", text)
        self.assertIn("loadTextContent('../deploy/ibkr-gateway/cloud-init.yaml')", text)
        placeholders = set(re.findall(r"__[A-Z0-9_]+__", _read(CLOUD_INIT)))
        self.assertEqual(placeholders, set(self._values()))
        self.assertTrue(_read(CLOUD_INIT).startswith("#cloud-config\n"))

    def test_custom_data_carries_no_secret_values(self):
        text = _read(BICEP)
        allowed = {
            "vault.properties.vaultUri", "identity.properties.clientId", "registry.properties.loginServer",
            "gatewayImage", "collectorImage", "tradingMode", "gatewayRelayPort", "liveAccountIds", "tenantId",
            "string(liveMarketDataType)",
        }
        for key, value in self._values().items():
            if key.endswith("_B64__"):
                self.assertRegex(value, r"^loadFileAsBase64\('\.\./deploy/ibkr-gateway/[\w./-]+'\)$", key)
            else:
                self.assertIn(value, allowed, key)
        for forbidden in ("@secure", "getSecret(", "listKeys(", "listCredentials(", "secretUri"):
            self.assertNotIn(forbidden, text)

    def test_every_deploy_file_is_delivered_to_the_right_path(self):
        values = self._values()
        loaded = {re.search(r"'\.\./deploy/ibkr-gateway/([^']+)'", value).group(1): key
                  for key, value in values.items() if key.endswith("_B64__")}
        expected = {str(path.relative_to(DEPLOY)) for path in _deploy_files()} - {"cloud-init.yaml"}
        self.assertEqual(set(loaded), expected)
        cloud_init = _read(CLOUD_INIT)
        renamed = {"systemd/docker-imds-guard.conf": "/etc/systemd/system/docker.service.d/10-ibkr-imds-guard.conf"}
        for source, key in loaded.items():
            path = re.search(rf"- path: (\S+)\n(?:    .*\n)*?    content: {key}\n", cloud_init).group(1)
            self.assertEqual(path, renamed.get(source, path))
            self.assertEqual(Path(path).name, Path(renamed.get(source, source)).name)
            self.assertIn("    encoding: b64\n    content: " + key, cloud_init)

    def test_rendered_custom_data_fits_azure_limit(self):
        text = _read(CLOUD_INIT)
        for key, value in self._values().items():
            if key.endswith("_B64__"):
                source = re.search(r"'\.\./([^']+)'", value).group(1)
                text = text.replace(key, base64.b64encode((ROOT / source).read_bytes()).decode())
            else:
                text = text.replace(key, "x" * 120)
        self.assertLess(len(base64.b64encode(text.encode())), 87380)


class IbkrGatewayComposeTest(unittest.TestCase):
    def test_no_ports_are_published(self):
        text = _read(COMPOSE)
        for key in ("ports", "expose", "network_mode", "privileged", "pid"):
            self.assertNotRegex(text, rf"(?m)^\s*{key}\s*:")

    def test_gateway_is_read_only_and_steps_aside(self):
        env = _compose_env(_compose_block(_read(COMPOSE), "ib-gateway"))
        self.assertEqual(env["READ_ONLY_API"], "yes")
        self.assertEqual(env["EXISTING_SESSION_DETECTED_ACTION"], "secondary")
        self.assertEqual(env["TWOFA_TIMEOUT_ACTION"], "restart")
        # A missed push never turns into an all-night login loop.
        self.assertEqual(env["RELOGIN_AFTER_TWOFA_TIMEOUT"], "no")
        self.assertEqual(env["AUTO_RESTART_TIME"], "05:30 AM")
        self.assertEqual(env["TIME_ZONE"], "Asia/Hong_Kong")
        self.assertTrue(env["TRADING_MODE"].startswith("${TRADING_MODE:?"))
        self.assertEqual(env["TWS_PASSWORD_FILE"], "/run/secrets/ibkr-password")
        for key in ("TWS_USERID", "TWS_PASSWORD", "VNC_SERVER_PASSWORD", "VNC_SERVER_PASSWORD_FILE"):
            self.assertNotIn(key, env)

    def test_gateway_config_with_password_stays_on_tmpfs(self):
        block = _compose_block(_read(COMPOSE), "ib-gateway")
        ini = _compose_env(block)["IBC_INI"]
        mounts = [entry.split(":")[0] for entry in _compose_list(block, "tmpfs")]
        self.assertIn(str(Path(ini).parent), mounts)

    def test_gateway_never_restarts_itself(self):
        text = _read(COMPOSE)
        gateway = re.search(r"^    restart: (.*)$", _compose_block(text, "ib-gateway"), re.M).group(1).strip("'\"")
        self.assertTrue(gateway == "no" or re.fullmatch(r"on-failure:[1-3]", gateway), gateway)
        collector = re.search(r"^    restart: (.*)$", _compose_block(text, "collector"), re.M).group(1)
        self.assertEqual(collector, "unless-stopped")

    def test_each_container_mounts_only_its_own_secrets(self):
        text = _read(COMPOSE)
        self.assertEqual(set(_compose_list(_compose_block(text, "ib-gateway"), "secrets")),
                         {"ibkr-username", "ibkr-password"})
        self.assertEqual(_compose_list(_compose_block(text, "collector"), "secrets"), ["database-live-url"])
        files = dict(re.findall(r"^  ([\w-]+):\n    file: (\S+)$", text[text.index("\nsecrets:\n"):], re.M))
        self.assertEqual(files, {
            "ibkr-username": "/run/ibkr-secrets/gateway/ibkr-username",
            "ibkr-password": "/run/ibkr-secrets/gateway/ibkr-password",
            "database-live-url": "/run/ibkr-secrets/collector/database-live-url",
        })
        # The username is read from its secret file inside the container only.
        entrypoint = _compose_block(_compose_block(text, "ib-gateway"), "entrypoint", "    ")
        self.assertIn('TWS_USERID="$$(cat /run/secrets/ibkr-username)"', entrypoint)
        for line in text.splitlines():
            if "TWS_USERID" in line and not line.lstrip().startswith("#"):
                self.assertIn(line, entrypoint)

    def test_collector_settings(self):
        block = _compose_block(_read(COMPOSE), "collector")
        env = _compose_env(block)
        self.assertIn('command: ["python", "-m", "ibkr_etl.live_collector"]', block)
        self.assertEqual(env["IB_GATEWAY_HOST"], "ib-gateway")
        self.assertEqual(env["IB_CLIENT_ID"], "17")
        self.assertEqual(env["LIVE_DATABASE_URL_FILE"], "/run/secrets/database-live-url")
        self.assertEqual(env["LIVE_STATUS_FILE"], "/run/ibkr-live/status.json")
        for key in ("IB_GATEWAY_PORT", "LIVE_ACCOUNT_IDS", "PORTFOLIO_TENANT_ID", "LIVE_MARKET_DATA_TYPE"):
            self.assertTrue(env[key].startswith("${" + key), key)
        self.assertNotIn("DATABASE_URL", env)
        self.assertIn("source: /run/ibkr-live\n        target: /run/ibkr-live", block)

    def test_relay_port_follows_trading_mode(self):
        text = _read(BICEP)
        self.assertEqual(_param_default(text, "tradingMode"), "paper")
        self.assertRegex(text, r"@allowed\(\[\n  'paper'\n  'live'\n\]\)\n(@description\([^\n]*\)\n)?param tradingMode")
        self.assertIn("var gatewayRelayPort = tradingMode == 'live' ? '4003' : '4004'", text)
        self.assertIn("IB_GATEWAY_PORT=__IB_GATEWAY_PORT__", _read(CLOUD_INIT))
        self.assertEqual(_param_default(text, "liveAccountIds"), "U16088571,U16088606")
        self.assertEqual(_param_default(text, "tenantId"), "00000000-0000-0000-0000-000000000001")
        self.assertEqual(_param_default(text, "liveMarketDataType"), "3")


class IbkrGatewayHostTest(unittest.TestCase):
    def test_containers_cannot_reach_instance_metadata(self):
        cloud_init = _read(CLOUD_INIT)
        self.assertIn("rule=(DOCKER-USER -i \"$bridge\" -d 169.254.169.254/32 -j DROP)", cloud_init)
        self.assertIn("for bridge in 'docker+' 'br-+'; do", cloud_init)
        drop_in = _read(SYSTEMD / "docker-imds-guard.conf")
        self.assertIn("Requires=ibkr-imds-guard.service", drop_in)
        self.assertIn("After=ibkr-imds-guard.service", drop_in)
        self.assertIn("Before=docker.service", _read(SYSTEMD / "ibkr-imds-guard.service"))

    def test_service_fetches_secrets_then_guards_then_starts_compose(self):
        unit = _read(SYSTEMD / "ibkr-live.service")
        steps = [line for line in unit.splitlines() if line.startswith(("ExecStartPre=", "ExecStart="))]
        order = [next(i for i, line in enumerate(steps) if marker in line)
                 for marker in ("fetch-secrets.sh", "imds-guard.sh", "docker compose")]
        self.assertEqual(order, sorted(order))
        self.assertIn("up --detach", steps[order[2]])
        self.assertIn("After=network-online.target docker.service", unit)
        self.assertIn("Requires=docker.service", unit)
        self.assertIn("ExecStopPost=/usr/bin/rm -rf /run/ibkr-secrets", unit)

    def test_relogin_timers_in_hong_kong_time(self):
        self.assertEqual(_unit_values(SYSTEMD / "ibkr-gateway-weekly-relogin.timer", "OnCalendar"),
                         ["Mon *-*-* 21:00:00 Asia/Hong_Kong"])
        self.assertEqual(_unit_values(SYSTEMD / "ibkr-gateway-relogin-check.timer", "OnCalendar"),
                         ["*-*-* 08..22:00,30:00 Asia/Hong_Kong", "*-*-* 23:00:00 Asia/Hong_Kong"])
        self.assertEqual(_unit_values(SYSTEMD / "ibkr-gateway-weekly-relogin.service", "ExecStart"),
                         ["/opt/ibkr-live/relogin-check.sh weekly"])
        self.assertEqual(_unit_values(SYSTEMD / "ibkr-gateway-relogin-check.service", "ExecStart"),
                         ["/opt/ibkr-live/relogin-check.sh check"])
        script = _read(RELOGIN_CHECK)
        for setting in ("STALE_AFTER_SECONDS=600", "WINDOW_TIME_ZONE=Asia/Hong_Kong",
                        "WINDOW_START=0800", "WINDOW_END=2300"):
            self.assertRegex(script, rf"(?m)^{setting}\b")
        cloud_init = _read(CLOUD_INIT)
        self.assertIn("timezone: Asia/Hong_Kong", cloud_init)
        self.assertIn("ibkr-gateway-weekly-relogin.timer, ibkr-gateway-relogin-check.timer]", cloud_init)

    def test_secrets_are_fetched_with_the_vm_identity_to_tmpfs(self):
        script = _read(FETCH_SECRETS)
        self.assertIn('--data-urlencode "client_id=${IDENTITY_CLIENT_ID}"', script)
        self.assertIn("SECRETS_DIR=/run/ibkr-secrets", script)
        self.assertIn("stat -f -c %T /run", script)
        self.assertIn("--password-stdin", script)
        for name in SECRETS:
            self.assertIn(f"write_secret {name} ", script)
        # Tokens reach curl on stdin, never as arguments.
        self.assertIn("-H @-", script)
        self.assertIn("--data-binary @-", script)
        self.assertNotRegex(script, r"-H [\"']Authorization")


class IbkrGatewaySecretHygieneTest(unittest.TestCase):
    def test_shell_scripts_are_strict_and_quiet(self):
        scripts = [FETCH_SECRETS, RELOGIN_CHECK]
        guard = re.search(r"path: /opt/ibkr-live/imds-guard\.sh\n.*?content: \|\n((?:      .*\n|\n)+)",
                          _read(CLOUD_INIT), re.S).group(1)
        for name, text in [(path.name, _read(path)) for path in scripts] + [("imds-guard.sh", guard)]:
            self.assertIn("set -euo pipefail", text, name)
            self.assertNotRegex(text, r"set -[a-z]*x", name)
            self.assertNotRegex(text, r"(?m)^\s*echo .*\$\{?(vault_token|registry_token)", name)
        # Every printf except log() feeds a pipe; log() lines never name a token.
        fetch = _read(FETCH_SECRETS).replace("\\\n", " ")
        for line in fetch.splitlines():
            if "printf" in line and not line.lstrip().startswith("log()"):
                self.assertTrue(line.rstrip().endswith("|"), line)
            if re.match(r"\s*log ", line):
                self.assertNotRegex(line, r"token|\$value")

    def test_no_literal_credentials(self):
        patterns = (
            r"TWS_PASSWORD(?!_FILE)\s*[:=]",
            r"(?m)^\s*VNC_SERVER_PASSWORD(_FILE)?\s*:",
            r"IbPassword",
            r"postgres(ql)?://",
            r"-----BEGIN",
            r"(?i)password[ \t]*[:=][ \t]*['\"]?[A-Za-z0-9]",
            r"AccountKey=",
        )
        for path in [BICEP, *_deploy_files()]:
            text = _read(path)
            for pattern in patterns:
                self.assertNotRegex(text, pattern, f"{path.name}: {pattern}")


if __name__ == "__main__":
    unittest.main()
