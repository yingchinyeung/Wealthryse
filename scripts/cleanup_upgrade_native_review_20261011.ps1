$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$stage = Join-Path $repo '.work/platform-upgrade-20261011-stage41'
$planPath = Join-Path $stage 'local-cleanup-plan.json'
$resultPath = Join-Path $stage 'local-cleanup-completed.json'
if (Test-Path -LiteralPath $resultPath) { throw 'Preserve completed cleanup receipt' }
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ($plan.files.Count -ne 346 -or $plan.delete_count -ne 346 -or $plan.recursive_delete -or -not $plan.protected_receipt.protected_readback_verified) { throw 'Exact protected cleanup plan required' }
$allowed = @(
    (Join-Path $repo '.work/platform-upgrade-20261011-stage40/historical-review-v1/files'),
    (Join-Path $repo '.work/platform-upgrade-20261011-stage40/historical-review-v1/renders'),
    (Join-Path $stage 'native-powerpoint-v1')
) | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\') + '\' }
$targets = @()
foreach ($row in $plan.files) {
    $path = [IO.Path]::GetFullPath($row.path)
    if (@($allowed | Where-Object { $path.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) }).Count -ne 1) { throw 'Cleanup target outside exact review directories' }
    $item = Get-Item -LiteralPath $path
    if ($item.PSIsContainer -or $item.Extension -notin @('.pdf','.pptx','.png')) { throw 'Only enumerated review files may be removed' }
    $ancestor = $item
    while ($null -ne $ancestor -and $ancestor.FullName.Length -ge $repo.Length) {
        if (($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse path rejected' }
        $ancestor = if ($ancestor -is [IO.FileInfo]) { $ancestor.Directory } else { $ancestor.Parent }
    }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $row.sha256) { throw 'Review file changed; do not delete' }
    $targets += $path
}
if (@($targets | Select-Object -Unique).Count -ne 346) { throw 'Duplicate cleanup target' }
foreach ($path in $targets) { Remove-Item -LiteralPath $path -Force }
if (@($targets | Where-Object { Test-Path -LiteralPath $_ }).Count -ne 0) { throw 'Cleanup incomplete' }
$record = [ordered]@{ completed=$true; deleted_files=346; downloaded_qa_files=210; native_pdf_exports=105; temporary_page_renders=31; azure_originals_preserved=$true; audit_metadata_preserved=$true; recursive_delete=$false; plan_sha256=(Get-FileHash -LiteralPath $planPath -Algorithm SHA256).Hash.ToLowerInvariant(); completed_at=[DateTime]::UtcNow.ToString('o') }
[IO.File]::WriteAllText($resultPath,($record | ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
$record | ConvertTo-Json -Compress
