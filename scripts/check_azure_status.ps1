[CmdletBinding()]
param(
    [string]$ResourceGroup = 'rg-ibkr-database-v11',
    [string]$JobName = 'ibkr-v11-daily',
    [int]$ExecutionCount = 10
)

$ErrorActionPreference = 'Stop'
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw 'Azure CLI is not installed.'
}

Write-Host 'Job configuration'
$Job = & az containerapp job show --name $JobName --resource-group $ResourceGroup `
    --query '{name:name,trigger:properties.configuration.triggerType,cron:properties.configuration.scheduleTriggerConfig.cronExpression,image:properties.template.containers[0].image}' `
    --output json | ConvertFrom-Json

$Job | Format-List name, trigger, cron, image

Write-Host 'Recent executions'
& az containerapp job execution list --name $JobName --resource-group $ResourceGroup `
    --query "sort_by([], &properties.startTime) | reverse(@) | [0:$ExecutionCount].{name:name,status:properties.status,start:properties.startTime,end:properties.endTime}" `
    --output table

if ($Job.trigger -eq 'Schedule' -and $Job.cron -match '^0\s+(\d{1,2})\s+\*\s+\*\s+\*$') {
    $UtcHour = [int]$Matches[1]
    $HktHour = ($UtcHour + 8) % 24
    Write-Host ("The live schedule is {0:D2}:00 HKT daily ({1})." -f $HktHour, $Job.cron)
}
else {
    Write-Warning "The job is not using the expected daily schedule trigger. Trigger=$($Job.trigger) Cron=$($Job.cron)"
}
