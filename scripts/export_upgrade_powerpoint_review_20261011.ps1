param(
    [string]$Subject = 'alpha-world-t',
    [string]$Period = '2026-09-30',
    [switch]$All
)
$ErrorActionPreference = 'Stop'
$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$review = Join-Path $workspace '.work/platform-upgrade-20261011-stage40/historical-review-v1'
$sourceRoot = [IO.Path]::GetFullPath((Join-Path $review 'files'))
$exportRoot = Join-Path $workspace '.work/platform-upgrade-20261011-stage41/native-powerpoint-v1'
$manifest = Get-Content -LiteralPath (Join-Path $review 'result.json') -Raw | ConvertFrom-Json
if (-not $manifest.passed -or $manifest.files -ne 210) { throw 'Completed verified QA batch required' }
$targets = @($manifest.artifacts | Where-Object { $_.report_type -eq 'presentation' -and ($All -or ($_.subject -eq $Subject -and $_.period_end -eq $Period)) })
if (($All -and $targets.Count -ne 105) -or (-not $All -and $targets.Count -ne 1)) { throw 'Exact requested review scope required' }
foreach ($row in $targets) {
    $source = [IO.Path]::GetFullPath($row.file)
    if (-not $source.StartsWith($sourceRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Source outside approved local review folder' }
    if ((Get-Item -LiteralPath $source).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked review file denied' }
    if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $row.sha256) { throw 'Original QA file hash differs' }
}
New-Item -ItemType Directory -Path $exportRoot -Force | Out-Null
$powerpoint = $null
$deck = $null
try {
    # Document-object automation approved by the owner. No SendKeys, UI input,
    # app security setting changes, normal-file saves, or Quit call is used.
    $powerpoint = New-Object -ComObject PowerPoint.Application
    $originalNames = @()
    for ($i=1; $i -le $powerpoint.Presentations.Count; $i++) { $originalNames += $powerpoint.Presentations.Item($i).FullName }
    foreach ($row in $targets) {
        $source = [IO.Path]::GetFullPath($row.file)
        $folder = Join-Path $exportRoot ($row.subject + '/' + $row.period_end)
        $destination = Join-Path $folder 'native-review.pdf'
        $receipt = Join-Path $folder 'export.json'
        if (Test-Path -LiteralPath $receipt) {
            $existing = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
            if ($existing.source_sha256 -ne $row.sha256 -or -not $existing.read_only -or -not $existing.source_unchanged -or (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $existing.pdf_sha256) { throw 'Existing native export changed; preserve it' }
            continue
        }
        if (Test-Path -LiteralPath $destination) { throw 'Unfinished export exists; reconcile before retrying' }
        if ($originalNames -contains $source) { throw 'Preserve a QA presentation already open by the user' }
        New-Item -ItemType Directory -Path $folder -Force | Out-Null
        try {
            $deck = $powerpoint.Presentations.Open($source, -1, 0, 0)
            if ($deck.ReadOnly -ne -1 -or $deck.Slides.Count -ne 20) { throw 'Read-only twenty-slide deck required' }
            # PowerShell 7 cannot bind this installation's optional
            # ExportAsFixedFormat COM signature. Native PDF SaveAs (format 32)
            # writes only the new destination; the source is open read-only.
            $deck.SaveAs($destination, 32)
            if (-not (Test-Path -LiteralPath $destination) -or (Get-Item -LiteralPath $destination).Length -eq 0) { throw 'Native PDF export missing' }
            $deck.Close()
            [void][Runtime.InteropServices.Marshal]::ReleaseComObject($deck)
            $deck = $null
            if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $row.sha256) { throw 'Source changed during read-only export' }
            $record = [ordered]@{ subject=$row.subject; period_end=$row.period_end; source=$source; source_sha256=$row.sha256; pdf=$destination; pdf_sha256=(Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant(); slides=20; read_only=$true; source_unchanged=$true; native_export_complete=$true; visual_review='UNVERIFIED'; cleanup_required=$true }
            # Generated runtime evidence, not an edit of application source.
            [IO.File]::WriteAllText($receipt, ($record | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
            [pscustomobject]@{subject=$row.subject;period=$row.period_end;native_export='passed';visual_review='UNVERIFIED'} | ConvertTo-Json -Compress
        } finally {
            if ($null -ne $deck) {
                $deck.Close()
                [void][Runtime.InteropServices.Marshal]::ReleaseComObject($deck)
                $deck = $null
            }
        }
    }
    $remaining = @()
    for ($i=1; $i -le $powerpoint.Presentations.Count; $i++) { $remaining += $powerpoint.Presentations.Item($i).FullName }
    if (@(Compare-Object $originalNames $remaining).Count -ne 0) { throw 'Existing presentation set changed; do not close unrelated documents' }
    Write-Output 'Existing PowerPoint presentations preserved; no application Quit issued.'
} finally {
    if ($null -ne $powerpoint) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($powerpoint) }
}
