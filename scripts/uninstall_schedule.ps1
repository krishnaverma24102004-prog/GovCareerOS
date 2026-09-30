<#
Removes the Task Scheduler task registered by scripts/install_schedule.ps1. Logs in storage\logs are kept.

    powershell -ExecutionPolicy Bypass -File C:\Users\krishna\govcareeros\scripts\uninstall_schedule.ps1
#>
param([string]$TaskName = "GovCareerOS Daily Job")
$ErrorActionPreference = "Stop"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed '$TaskName'."
} else {
    Write-Host "No task named '$TaskName'."
}
