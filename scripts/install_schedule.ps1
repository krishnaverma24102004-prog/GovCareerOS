<#
Registers a Windows Task Scheduler task that runs scripts/daily_job.py every day (default 07:00 local time)
for the current user. Re-running it replaces the task. Remove it with scripts/uninstall_schedule.ps1.

    powershell -ExecutionPolicy Bypass -File C:\Users\krishna\govcareeros\scripts\install_schedule.ps1
    powershell -ExecutionPolicy Bypass -File C:\Users\krishna\govcareeros\scripts\install_schedule.ps1 -At 06:30 -Python C:\Python312\python.exe

- Working directory and PYTHONPATH are the folder that CONTAINS govcareeros (code imports "govcareeros.backend...").
- The job reads DATABASE_URL etc. from govcareeros\.env, as when run by hand.
- Console output goes to storage\logs\daily_job.out.log; the structured run log is storage\logs\daily_job.jsonl.
- Runs only while the user is logged on (no password is stored). A run missed while the PC was off starts
  as soon as possible afterwards. The job's own lock prevents overlapping runs.
#>
param(
    [string]$TaskName = "GovCareerOS Daily Job",
    [string]$At = "07:00",
    [string]$Python = ""
)
$ErrorActionPreference = "Stop"

$Project = Split-Path -Parent $PSScriptRoot          # ...\govcareeros
$Parent = Split-Path -Parent $Project                 # ...\ (goes on PYTHONPATH)
$Script = Join-Path $PSScriptRoot "daily_job.py"
$LogDir = Join-Path $Project "storage\logs"
$OutLog = Join-Path $LogDir "daily_job.out.log"

if (-not $Python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $cmd) { throw "python not found on PATH; pass -Python C:\path\to\python.exe" }
    $Python = $cmd.Source
}
if ($Python -like "*\WindowsApps\*") {
    Write-Warning "$Python is the Microsoft Store alias; Task Scheduler may not be able to start it. Pass -Python with the real python.exe."
}
if (-not (Test-Path $Script)) { throw "not found: $Script" }
New-Item -ItemType Directory -Force $LogDir | Out-Null

# Task Scheduler cannot set environment variables, so cmd.exe sets PYTHONPATH and redirects output
$cmdArgs = "/d /c set `"PYTHONPATH=$Parent`" && `"$Python`" `"$Script`" >> `"$OutLog`" 2>&1"
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" -Argument $cmdArgs -WorkingDirectory $Parent
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 5) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "GovCareerOS: discover official sources, UPSC pipeline, LLM drafts, eligibility radar, digest ($Script)" -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "Registered '$($task.TaskName)' ($($task.State)); next run: $($info.NextRunTime)"
Write-Host "  cmd.exe $cmdArgs"
Write-Host "  working dir: $Parent"
Write-Host "Run it now with:  Start-ScheduledTask -TaskName '$TaskName'"
