"""Exercise the real PowerShell preflight with synthetic commands, never sign in."""
import base64
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which('pwsh')


@unittest.skipUnless(SHELL, 'PowerShell 7 unavailable')
class MailPreflightTests(unittest.TestCase):
    def run_case(self, case):
        with tempfile.TemporaryDirectory() as folder:
            source = (ROOT/'scripts/connect_specialist_mail_admin.ps1').read_text(encoding='utf-8')
            old_root = '$mailRoot = Split-Path -Parent $PSScriptRoot'
            old_folder = "$mailFolder = Join-Path $mailRoot '.work\\specialist-mail-20261003'"
            self.assertIn(old_root, source)
            self.assertIn(old_folder, source)
            fixture_path = folder.replace("'", "''")
            source = source.replace(old_root, f"$mailRoot = '{fixture_path}'").replace(old_folder, '$mailFolder = $mailRoot')
            fixtures = r'''
$mailTestCase = '__CASE__'
function Get-AuthenticodeSignature {
    [CmdletBinding()]param($LiteralPath)
    $signatureState = if ($mailTestCase -eq 'bad_signature') { 'NotTrusted' } else { 'Valid' }
    [pscustomobject]@{Status=$signatureState; SignerCertificate=[pscustomobject]@{Subject='O=Microsoft Corporation'}}
}
function Import-Module { [CmdletBinding()]param($Name) }
function Connect-ExchangeOnline { [CmdletBinding()]param([switch]$Device,[bool]$ShowBanner,[switch]$SkipLoadingFormatData) }
function Disconnect-ExchangeOnline { [CmdletBinding()]param([bool]$Confirm) }
function Get-ConnectionInformation {
    [CmdletBinding()]param()
    $fixtureTenant = if ($mailTestCase -eq 'wrong_tenant') { '11111111-1111-1111-1111-111111111111' } else { '0578a50b-a7a1-40a4-905b-8b20dba84124' }
    [pscustomobject]@{State='Connected'; TenantID=$fixtureTenant}
}
function Get-Command {
    [CmdletBinding()]param($Name)
    if ($mailTestCase -ne 'no_admin' -or $Name -notlike 'New-*') { [pscustomobject]@{Name=$Name} }
}
function Get-Recipient {
    [CmdletBinding()]param($Filter,$ResultSize)
    if ($Filter -ne "ExternalDirectoryObjectId -eq '8617fd1c-2280-40f2-92dd-45686e9d686c'" -or $ResultSize -ne 2) { throw 'Unbounded recipient lookup' }
    $fixtureAddress = if ($mailTestCase -eq 'wrong_address') { 'other@example.com' } else { 'anthony.ying@wealthryse.ai' }
    $fixtureType = if ($mailTestCase -eq 'not_mailbox') { 'MailUser' } else { 'UserMailbox' }
    $fixtureMailbox = [pscustomobject]@{PrimarySmtpAddress=$fixtureAddress;RecipientTypeDetails=$fixtureType}
    $fixtureMailbox
    if ($mailTestCase -eq 'duplicate') { $fixtureMailbox }
}
'''.replace('__CASE__', case)
            command = fixtures + '\n' + source
            encoded = base64.b64encode(command.encode('utf-16le')).decode()
            result = subprocess.run([SHELL, '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                    capture_output=True, text=True, timeout=30)
            receipt = json.loads((Path(folder)/'exchange-preflight.json').read_text())
            self.assertEqual(receipt['mailbox_permissions_changed'], 0)
            self.assertEqual(receipt['messages_sent'], 0)
            self.assertEqual(receipt['source_rows_changed'], 0)
            self.assertFalse(receipt['schedule_enabled'])
            return result, receipt

    def test_exact_tenant_and_existing_mailbox_pass_read_only(self):
        result, receipt = self.run_case('passed')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(receipt['status'], 'passed')
        self.assertEqual(receipt['matched_mailboxes'], 1)

    def test_wrong_tenant_blocks_before_mailbox(self):
        result, receipt = self.run_case('wrong_tenant')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(receipt['phase'], 'tenant_verification')

    def test_sign_in_does_not_imply_administration(self):
        result, receipt = self.run_case('no_admin')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(receipt['phase'], 'administration_capability')

    def test_multiple_recipients_are_not_accepted(self):
        result, receipt = self.run_case('duplicate')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(receipt['phase'], 'mailbox_verification')

    def test_wrong_address_is_not_accepted(self):
        result, receipt = self.run_case('wrong_address')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(receipt['phase'], 'mailbox_verification')

    def test_external_mail_user_is_not_an_exchange_mailbox(self):
        result, receipt = self.run_case('not_mailbox')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(receipt['phase'], 'mailbox_verification')

    def test_untrusted_module_stops_before_sign_in(self):
        result, receipt = self.run_case('bad_signature')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(receipt['phase'], 'module_verification')
