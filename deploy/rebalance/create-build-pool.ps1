# Gives the rebalancing site an approved way to build its image inside our
# private network: a small ACR Tasks agent pool in its own new subnet of
# ibkrv2stg-vnet. Builds run there and push to the private registry over its
# private endpoint, so the registry stays closed to the internet.
#
# Changes (all additive): one new subnet "acr-build" (10.50.14.0/27) in
# ibkrv2stg-vnet and one agent pool "rebalance" (tier S1) on
# ibkrv2stgauhecwc4itaoo. Nothing else in the network, registry, website or
# jobs is changed. Before changing anything it checks that the range is free
# and that the private-endpoint subnet's rules let HTTPS in from the new
# subnet; if either check fails it stops with nothing changed. Safe to re-run.

param(
    [string]$ResourceGroup = 'rg-ibkr-database-v11',
    [string]$Subscription = '3956e821-cee4-403f-8708-e84e2ed490eb',
    [string]$Vnet = 'ibkrv2stg-vnet',
    [string]$SubnetName = 'acr-build',
    # Pinned with the network work: clear of 10.50.10.0/24 (endpoints) and 10.50.12.0/24 (planned gateway).
    [string]$Prefix = '10.50.14.0/27',
    [string]$EndpointNsg = 'ibkr-upgrade-pe-main-nsg',
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
function Range([string]$cidr) {
    $parts = $cidr.Split('/'); $size = [uint32][math]::Pow(2, 32 - [int]$parts[1])
    $start = ToNumber $parts[0]; return @($start, ($start + $size - 1))
}

if ((AzRun account show --query id -o tsv) -ne $Subscription) { AzRun account set --subscription $Subscription | Out-Null }

Write-Host "== Checking the build subnet would reach the registry's private address"
# Read-only. Walk the endpoint subnet's inbound rules in priority order and find
# the first that applies to HTTPS from the build range. Azure's default rule lets
# traffic from inside the network through, so no match means it is allowed.
function Covers([string]$src) {
    if ($src -in @('*', 'Any', 'VirtualNetwork')) { return $true }
    if ($src -notmatch '/') { if ($src -match '^\d+\.\d+\.\d+\.\d+$') { $src = "$src/32" } else { return $false } }
    $r = Range $src; $w = Range $Prefix; return ($w[0] -ge $r[0] -and $w[1] -le $r[1])
}
function Has443([string]$port) {
    if ($port -eq '*') { return $true }
    if ($port -match '^(\d+)-(\d+)$') { return ([int]$Matches[1] -le 443 -and [int]$Matches[2] -ge 443) }
    return ($port -eq '443')
}
$rows = (AzRun network nsg rule list -g $ResourceGroup --nsg-name $EndpointNsg --query "sort_by([?direction=='Inbound'], &priority)[].[priority, access, protocol, sourceAddressPrefix, join(',', sourceAddressPrefixes), destinationPortRange, join(',', destinationPortRanges), name]" -o tsv) -split "`r?`n" | Where-Object { $_ }
$firstMatch = $null
foreach ($row in $rows) {
    $f = $row -split "`t"
    if ($f[2] -notin @('*', 'Tcp')) { continue }
    $sources = @($f[3]) + ($f[4] -split ',') | Where-Object { $_ -and $_ -ne 'None' }
    $ports = @($f[5]) + ($f[6] -split ',') | Where-Object { $_ -and $_ -ne 'None' }
    if (($sources | Where-Object { Covers $_ }) -and ($ports | Where-Object { Has443 $_ })) { $firstMatch = $f; break }
}
if ($firstMatch -and $firstMatch[1] -eq 'Deny') {
    throw "STOPPED, nothing changed: rule '$($firstMatch[7])' (priority $($firstMatch[0])) on $EndpointNsg would block HTTPS from $Prefix to the registry. Paste this line to Claude."
}
Write-Host "   allowed ($(if ($firstMatch) { "rule '$($firstMatch[7])'" } else { 'network default' }))"

Write-Host "== Checking $Prefix is free in $Vnet"
$subnetId = AzRun network vnet subnet list -g $ResourceGroup --vnet-name $Vnet --query "[?name=='$SubnetName'].id | [0]" -o tsv
if (-not $subnetId) {
    [string[]]$spaces = (AzRun network vnet show -g $ResourceGroup -n $Vnet --query 'addressSpace.addressPrefixes' -o tsv) -split "`r?`n" | Where-Object { $_ }
    [string[]]$used = @((AzRun network vnet subnet list -g $ResourceGroup --vnet-name $Vnet --query '[].addressPrefix' -o tsv) -split "`r?`n") +
                     @((AzRun network vnet subnet list -g $ResourceGroup --vnet-name $Vnet --query '[].addressPrefixes[]' -o tsv) -split "`r?`n") | Where-Object { $_ -match '/' }
    $want = Range $Prefix
    $inside = $spaces | Where-Object { $r = Range $_; $want[0] -ge $r[0] -and $want[1] -le $r[1] }
    if (-not $inside) { throw "STOPPED, nothing changed: $Prefix is outside $Vnet's address space." }
    foreach ($u in $used) { $r = Range $u; if (-not ($want[1] -lt $r[0] -or $want[0] -gt $r[1])) { throw "STOPPED, nothing changed: $Prefix overlaps existing subnet $u." } }
    Write-Host "== Creating subnet $SubnetName ($Prefix)"
    # Builds need to download base images and packages, so the subnet keeps
    # ordinary outbound internet access; nothing can reach into it.
    $subnetId = AzRun network vnet subnet create -g $ResourceGroup --vnet-name $Vnet -n $SubnetName --address-prefixes $Prefix --default-outbound-access true --query id -o tsv
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
