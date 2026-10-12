# Read-only Exchange sign-in preflight. No mail grant or submission.
# Device authentication is completed by the human, not automated browser input.
$ErrorActionPreference = 'Stop'
$mailRoot = Split-Path -Parent $PSScriptRoot
$mailFolder = Join-Path $mailRoot '.work\specialist-mail-20261003'
$mailModule = Join-Path $mailFolder 'modules\ExchangeOnlineManagement\3.10.1\ExchangeOnlineManagement.psd1'
$mailTenant = '0578a50b-a7a1-40a4-905b-8b20dba84124'
$mailUserId = '8617fd1c-2280-40f2-92dd-45686e9d686c'
$mailUser = 'anthony.ying@wealthryse.ai'
$mailConnected = $false
$mailPhase = 'module_verification'
try {
    foreach ($mailFile in @($mailModule, (Join-Path (Split-Path $mailModule -Parent) 'netCore\ExchangeOnlineManagement.psm1'))) {
        $mailSignature = Get-AuthenticodeSignature -LiteralPath $mailFile
        if ($mailSignature.Status -ne 'Valid' -or $mailSignature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
            throw 'Microsoft module signature check failed'
        }
    }
    Import-Module $mailModule -ErrorAction Stop
    $mailPhase = 'human_device_sign_in'
    Connect-ExchangeOnline -Device -ShowBanner:$false -SkipLoadingFormatData -ErrorAction Stop
    $mailConnected = $true
    $mailPhase = 'tenant_verification'
    $mailConnections = @(Get-ConnectionInformation -ErrorAction Stop | Where-Object { $_.State -eq 'Connected' })
    if ($mailConnections.Count -ne 1 -or [string]$mailConnections[0].TenantID -ne $mailTenant) {
        throw 'Exact approved Exchange tenant required'
    }
    $mailPhase = 'administration_capability'
    $mailRequired = @('Get-Recipient', 'Get-ServicePrincipal', 'Get-ManagementScope',
        'Get-ManagementRoleAssignment', 'New-ServicePrincipal', 'New-ManagementScope',
        'New-ManagementRoleAssignment', 'Test-ServicePrincipalAuthorization')
    $mailMissing = @($mailRequired | Where-Object { -not (Get-Command $_ -ErrorAction SilentlyContinue) })
    if ($mailMissing.Count -gt 0) {
        # A signed-in user is not assumed to be an Exchange administrator.
        throw 'Required Exchange administration commands are not available to this account'
    }
    $mailPhase = 'mailbox_verification'
    $mailRecipients = @(Get-Recipient -Filter "ExternalDirectoryObjectId -eq '$mailUserId'" -ResultSize 2 -ErrorAction Stop)
    if ($mailRecipients.Count -ne 1 -or [string]$mailRecipients[0].PrimarySmtpAddress -ne $mailUser -or
        [string]$mailRecipients[0].RecipientTypeDetails -ne 'UserMailbox') {
        throw 'Exact approved existing user mailbox required'
    }
    $mailResult = [ordered]@{
        status = 'passed'; phase = 'read_only_exchange_preflight'; tenant_id = $mailTenant
        sender = $mailUser; mailbox_directory_id = $mailUserId; matched_mailboxes = 1
        required_administration_commands_available = $true; mailbox_permissions_changed = 0
        messages_sent = 0; source_rows_changed = 0; schedule_enabled = $false
        observed_at = [DateTimeOffset]::UtcNow.ToString('o')
    }
} catch {
    # Never print authentication exception bodies, connection objects or tokens.
    $mailResult = [ordered]@{
        status = 'needs_attention'; phase = $mailPhase
        error_type = $_.Exception.GetType().Name
        mailbox_permissions_changed = 0; messages_sent = 0; source_rows_changed = 0
        schedule_enabled = $false; observed_at = [DateTimeOffset]::UtcNow.ToString('o')
    }
} finally {
    if ($mailConnected) { Disconnect-ExchangeOnline -Confirm:$false -ErrorAction SilentlyContinue }
}
$mailJson = $mailResult | ConvertTo-Json -Depth 3
[System.IO.File]::WriteAllText((Join-Path $mailFolder 'exchange-preflight.json'), $mailJson)
Write-Output ('SPECIALIST_EXCHANGE_PREFLIGHT ' + ($mailResult | ConvertTo-Json -Depth 3 -Compress))
if ($mailResult.status -ne 'passed') { exit 1 }
