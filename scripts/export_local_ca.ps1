[CmdletBinding()]
param(
    [string]$SubjectPattern = 'CN=Norton Web/Mail Shield Root*',
    [string]$OutputPath = (Join-Path (Split-Path -Parent $PSScriptRoot) '.local-ca.crt')
)

$ErrorActionPreference = 'Stop'
$certificate = Get-ChildItem Cert:\CurrentUser\Root, Cert:\LocalMachine\Root |
    Where-Object { $_.Subject -like $SubjectPattern } |
    Select-Object -First 1

if (-not $certificate) {
    throw "No trusted Windows root matches: $SubjectPattern"
}

$base64 = [Convert]::ToBase64String($certificate.RawData)
$lines = for ($offset = 0; $offset -lt $base64.Length; $offset += 64) {
    $length = [Math]::Min(64, $base64.Length - $offset)
    $base64.Substring($offset, $length)
}
$pem = @('-----BEGIN CERTIFICATE-----') + $lines + @('-----END CERTIFICATE-----')
[IO.File]::WriteAllLines($OutputPath, $pem, [Text.UTF8Encoding]::new($false))
Write-Host "Exported the trusted local root to $OutputPath"
Write-Host 'The file is ignored by Git. Do not send or copy it to a different computer.'
