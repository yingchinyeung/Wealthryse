# Gives the rebalancing site an approved way to build its image inside our
# private network: a small ACR Tasks agent pool in its own new subnet of
# ibkrv2stg-vnet. Builds run there and push to the private registry over its
# private endpoint, so the registry stays closed to the internet.
#
# Changes (all additive): one new /27 subnet "acr-build" in ibkrv2stg-vnet,
# and one agent pool "rebalance" (tier S1) on ibkrv2stgauhecwc4itaoo.
# Nothing else in the network, registry, website or jobs is changed.
# Safe to re-run.

param(
    [string]$ResourceGroup = 'rg-ibkr-database-v11',
    [string]$Subscription = '3956e821-cee4-403f-8708-e84e2ed490eb',
    [string]$Vnet = 'ibkrv2stg-vnet',
    [string]$SubnetName = 'acr-build',
    [string]$Registry = 'ibkrv2stgauhecwc4itaoo',
    [string]$PoolName = 'rebalance',
    [string]$Tier = 'S1'
)

$ErrorActionPreference = 'Stop'
$azExe = (Get-Command az -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
function AzRun {
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    $out = @(& $azExe @args 2>&1)
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "az $($args -join ' ') failed (exit $code): $(($out | ForEach-Object { "$_" }) -join "`n")" }
    return ($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_" }) -join "`n"
}
function ToNumber([string]$ip) { $b = ([System.Net.IPAddress]::Parse($ip)).GetAddressBytes(); return ([uint32]$b[0] -shl 24) + ([uint32]$b[1] -shl 16) + ([uint32]$b[2] -shl 8) + [uint32]$b[3] }
function ToAddress([uint32]$n) { return '{0}.{1}.{2}.{3}' -f (($n -shr 24) -band 255), (($n -shr 16) -band 255), (($n -shr 8) -band 255), ($n -band 255) }
function Range([string]$cidr) {
    $parts = $cidr.Split('/'); $size = [uint32][math]::Pow(2, 32 - [int]$parts[1])
    $start = ToNumber $parts[0]; return @($start, ($start + $size - 1))
}

if ((AzRun account show --query id -o tsv) -ne $Subscription) { AzRun account set --subscription $Subscription | Out-Null }

Write-Host "== Finding room for a small build subnet in $Vnet"
$subnetId = AzRun network vnet subnet list -g $ResourceGroup --vnet-name $Vnet --query "[?name=='$SubnetName'].id | [0]" -o tsv
if (-not $subnetId) {
    [string[]]$spaces = (AzRun network vnet show -g $ResourceGroup -n $Vnet --query 'addressSpace.addressPrefixes' -o tsv) -split "`r?`n" | Where-Object { $_ }
    [string[]]$used = @((AzRun network vnet subnet list -g $ResourceGroup --vnet-name $Vnet --query '[].addressPrefix' -o tsv) -split "`r?`n") +
                     @((AzRun network vnet subnet list -g $ResourceGroup --vnet-name $Vnet --query '[].addressPrefixes[]' -o tsv) -split "`r?`n") | Where-Object { $_ -match '/' }
    $usedRanges = $used | ForEach-Object { ,(Range $_) }
    $prefix = $null
    foreach ($space in $spaces) {
        $r = Range $space
        for ($start = $r[0]; $start + 31 -le $r[1] -and -not $prefix; $start += 32) {
            $end = $start + 31
            $clash = $usedRanges | Where-Object { -not ($end -lt $_[0] -or $start -gt $_[1]) }
            if (-not $clash) { $prefix = "$(ToAddress $start)/27" }
        }
        if ($prefix) { break }
    }
    if (-not $prefix) { throw "No free /27 left in $Vnet; nothing was changed." }
    Write-Host "== Creating subnet $SubnetName ($prefix)"
    # Builds need to download base images and packages, so the subnet keeps
    # ordinary outbound internet access; nothing can reach into it.
    $subnetId = AzRun network vnet subnet create -g $ResourceGroup --vnet-name $Vnet -n $SubnetName --address-prefixes $prefix --default-outbound-access true --query id -o tsv
}

Write-Host "== Creating build pool $PoolName (takes a few minutes)"
$existing = AzRun acr agentpool list -r $Registry --query "[?name=='$PoolName'].name | [0]" -o tsv
if (-not $existing) {
    AzRun acr agentpool create -r $Registry -n $PoolName --tier $Tier --subnet-id $subnetId | Out-Null
}
$poolId = AzRun acr agentpool show -r $Registry -n $PoolName --query id -o tsv
# Same labels as the registry it belongs to, so it shows up with the rest of the foundation.
AzRun resource tag --ids $poolId --tags layer=foundation component=registry owner=ying -i | Out-Null
$state = AzRun acr agentpool show -r $Registry -n $PoolName --query provisioningState -o tsv
$count = AzRun acr agentpool show -r $Registry -n $PoolName --query count -o tsv
Write-Host ''
Write-Host "DONE  pool=$PoolName  state=$state  instances=$count  subnet=$subnetId"
