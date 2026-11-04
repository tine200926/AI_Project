<#
.SYNOPSIS
    Registers (or removes) the Windows Scheduled Task that runs the monitor.
.EXAMPLE
    .\install-task.ps1                      # every 5 minutes
.EXAMPLE
    .\install-task.ps1 -IntervalMinutes 2   # every 2 minutes
.EXAMPLE
    .\install-task.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'WeverseRestockMonitor',
    [ValidateRange(1, 1440)][int]$IntervalMinutes = 5,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'." -ForegroundColor Yellow
    }
    else {
        Write-Host "No scheduled task named '$TaskName'." -ForegroundColor Gray
    }
    return
}

$script = Join-Path $PSScriptRoot 'weverse-restock.ps1'
if (-not (Test-Path $script)) { throw "Cannot find $script" }
if (-not (Test-Path (Join-Path $PSScriptRoot 'config.json'))) {
    throw 'config.json is missing - copy config.example.json to config.json and fill it in first.'
}

$pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue)?.Source
if (-not $pwsh) { $pwsh = (Get-Command powershell).Source }

$action = New-ScheduledTaskAction -Execute $pwsh `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory $PSScriptRoot

# Repeat forever on the chosen interval. Omitting -RepetitionDuration is what
# Task Scheduler reads as "indefinitely"; passing [TimeSpan]::MaxValue or Zero
# is rejected outright ("Duration:P99999999DT23H59M59S" / "PT0S").
$repeating = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

# A bare -AtLogOn trigger covers every user and so needs admin rights; scoping
# it to the current account registers fine unelevated.
$me = "$env:USERDOMAIN\$env:USERNAME"
$triggers = @($repeating, (New-ScheduledTaskTrigger -AtLogOn -User $me))

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
        -Settings $settings -Description 'Weverse Shop restock monitor (LINE notify)' `
        -Force -ErrorAction Stop | Out-Null
}
catch {
    # Some policies still refuse the logon trigger. The repeating trigger plus
    # -StartWhenAvailable is enough on its own, so fall back rather than fail.
    Write-Host "Logon trigger refused ($($_.Exception.Message.Trim())); registering the repeating trigger only." -ForegroundColor Yellow
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $repeating `
        -Settings $settings -Description 'Weverse Shop restock monitor (LINE notify)' `
        -Force | Out-Null
}

Write-Host "Registered '$TaskName': every $IntervalMinutes minute(s)." -ForegroundColor Green
Write-Host 'Starting one run now...' -ForegroundColor Gray
Start-ScheduledTask -TaskName $TaskName
