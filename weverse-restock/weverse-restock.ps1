<#
.SYNOPSIS
    Weverse Shop restock monitor with LINE push notification.
.DESCRIPTION
    Polls every sale page listed in config.json, reads the per-option sold-out
    flags embedded in each page's __NEXT_DATA__ payload, and sends a LINE push
    when a watched option flips from sold out to available. All hits found in
    one pass are batched into a single message, because the LINE free plan
    allows only 200 pushes per month.
.EXAMPLE
    .\weverse-restock.ps1              # one check (use this from Task Scheduler)
.EXAMPLE
    .\weverse-restock.ps1 -Loop        # keep running, check every intervalSeconds
.EXAMPLE
    .\weverse-restock.ps1 -Status      # print current stock, change nothing
.EXAMPLE
    .\weverse-restock.ps1 -TestNotify  # verify the LINE credentials
#>
[CmdletBinding()]
param(
    [string]$Config = "$PSScriptRoot\config.json",
    [switch]$Loop,
    [switch]$Status,
    [switch]$TestNotify
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'lib.ps1')

function Format-Notification {
    # Groups hits by product so one message can cover several pages.
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][array]$Restocked,
        [Parameter(Mandatory)][AllowEmptyCollection()][array]$SoldOut
    )
    $lines = @()

    if ($Restocked.Count) {
        $lines += '🎉 補貨通知'
        foreach ($g in ($Restocked | Group-Object { $_.Target.id })) {
            $t = $g.Group[0].Target
            $lines += ''
            $lines += "■ $($t.label)"
            foreach ($h in $g.Group) { $lines += "・$($h.Name)" }
            $lines += $t.url
        }
    }

    if ($SoldOut.Count) {
        if ($lines.Count) { $lines += ''; $lines += '---' }
        $lines += '😢 又賣完了'
        foreach ($g in ($SoldOut | Group-Object { $_.Target.id })) {
            $t = $g.Group[0].Target
            $lines += ''
            $lines += "■ $($t.label)"
            foreach ($h in $g.Group) { $lines += "・$($h.Name)" }
        }
    }

    return ($lines -join "`n")
}

function Invoke-Check {
    param([Parameter(Mandatory)]$Cfg)

    $state     = Get-State
    $now       = Get-Date
    $restocked = @()
    $wentOut   = @()
    $failed    = @()
    $targets   = @($Cfg.resolvedTargets)

    for ($i = 0; $i -lt $targets.Count; $i++) {
        $t = $targets[$i]

        # Be a polite scraper: space out requests when watching several pages.
        if ($i -gt 0 -and $Cfg.politeDelayMs -gt 0) {
            Start-Sleep -Milliseconds $Cfg.politeDelayMs
        }

        $saleStatus = ''
        try {
            $options = Get-SaleOptions -Url $t.url -Country $t.country `
                -TimeoutSec $Cfg.requestTimeoutSec -MaxRetries $Cfg.maxRetries `
                -SaleStatus ([ref]$saleStatus)
        }
        catch {
            # One dead page must not stop the others from being checked.
            Write-Log "[$($t.label)] $($_.Exception.Message)" ERROR
            $failed += [pscustomobject]@{ Target = $t; Reason = $_.Exception.Message }
            continue
        }

        $watched = Resolve-Watched -Watch @($t.watch) -Options $options -TargetLabel $t.label

        # Counters for the one-line per-target summary written after this loop.
        $nAvailable = 0
        $nNew       = 0

        foreach ($name in $watched.Keys) {
            $soldOut   = $options[$name]
            $available = -not $soldOut
            $key       = "option::$($t.id)::$name"

            $prevSoldOut    = $null
            $lastNotifiedAt = $null
            if ($state.ContainsKey($key)) {
                $entry = $state[$key]
                if ($entry.PSObject.Properties['soldOut'] -and $null -ne $entry.soldOut) {
                    $prevSoldOut = [bool]$entry.soldOut
                }
                if ($entry.PSObject.Properties['notifiedAt'] -and $entry.notifiedAt) {
                    $lastNotifiedAt = [datetime]$entry.notifiedAt
                }
            }

            $firstSighting = ($null -eq $prevSoldOut)
            $isRestock = $available -and
                         ($firstSighting ? [bool]$Cfg.notifyOnFirstRun : $prevSoldOut)
            $isSellOut = $soldOut -and (-not $firstSighting) -and (-not $prevSoldOut)

            if ($available) { $nAvailable++ }
            if ($firstSighting) { $nNew++ }

            $cooledDown = $true
            if ($lastNotifiedAt) {
                $cooledDown = ($now - $lastNotifiedAt).TotalMinutes -ge $Cfg.cooldownMinutes
            }

            if ($isRestock -and $cooledDown) {
                Write-Log "[$($t.label)] RESTOCK: '$name' is available" HIT
                # State for these is written after the push, so a failed send
                # stays retryable instead of silently swallowing the restock.
                $restocked += [pscustomobject]@{
                    Target      = $t
                    Name        = $name
                    Key         = $key
                    PrevSoldOut = $prevSoldOut
                    NotifiedAt  = $lastNotifiedAt
                }
                continue
            }

            if ($isRestock) {
                $mins = [int](($now - $lastNotifiedAt).TotalMinutes)
                Write-Log "[$($t.label)] '$name' still available; inside cooldown (last notified ${mins}m ago)"
            }
            elseif ($isSellOut) {
                Write-Log "[$($t.label)] '$name' went back to sold out"
                if ($Cfg.notifyOnSoldOut) {
                    $wentOut += [pscustomobject]@{ Target = $t; Name = $name }
                }
            }
            elseif ($firstSighting) {
                # Worth one line: it shows what just started being tracked.
                Write-Log ("[{0}] now tracking '{1}': {2}" -f $t.label, $name, ($soldOut ? 'sold out' : 'available'))
            }
            elseif ($Cfg.logEveryOption) {
                # Opt-in firehose for debugging. Off by default, because one
                # line per option per run buries the events that matter.
                Write-Log ("[{0}] '{1}': {2}" -f $t.label, $name, ($soldOut ? 'sold out' : 'available'))
            }

            $state[$key] = [pscustomobject]@{
                soldOut    = $soldOut
                checkedAt  = $now.ToString('o')
                notifiedAt = if ($lastNotifiedAt) { $lastNotifiedAt.ToString('o') } else { $null }
            }
        }

        # One summary line per target per run, so the log proves the monitor
        # ran without one line per option drowning the actual events.
        $summary = "[{0}] status={1} | {2} watched | {3} available" -f `
            $t.label, $saleStatus, $watched.Count, $nAvailable
        if ($nNew) { $summary += " | $nNew new" }
        Write-Log $summary
    }

    # One push for everything found this pass - the free plan allows only 200
    # pushes a month, so a 10-product watch list must not burn 10 of them.
    $sent = $false
    if ($restocked.Count -or $wentOut.Count) {
        $text = Format-Notification -Restocked $restocked -SoldOut $wentOut
        $sent = Send-LineMessage -Cfg $Cfg -Text $text
    }

    foreach ($h in $restocked) {
        if (-not $sent) {
            Write-Log "[$($h.Target.label)] notification for '$($h.Name)' did not send; will retry next check" WARN
        }
        $state[$h.Key] = [pscustomobject]@{
            # Keep the old sold-out value on failure so the next pass sees the
            # same transition and tries again.
            soldOut    = if ($sent) { $false }
                         elseif ($null -ne $h.PrevSoldOut) { $h.PrevSoldOut }
                         else { $true }
            checkedAt  = $now.ToString('o')
            notifiedAt = if ($sent) { $now.ToString('o') }
                         elseif ($h.NotifiedAt) { $h.NotifiedAt.ToString('o') }
                         else { $null }
        }
    }

    # Tell the user if the monitor itself is broken, once per cooldown window,
    # and clear the flag once every page is readable again.
    if ($failed.Count -and $Cfg.notifyOnError) {
        $last = $null
        if ($state.ContainsKey('error::notifiedAt') -and $state['error::notifiedAt']) {
            $last = [datetime]$state['error::notifiedAt']
        }
        if (-not $last -or ($now - $last).TotalMinutes -ge $Cfg.cooldownMinutes) {
            $lines = @('⚠️ 補貨監測異常', '')
            foreach ($f in $failed) {
                $lines += "■ $($f.Target.label)"
                $lines += $f.Reason
                $lines += $f.Target.url
                $lines += ''
            }
            Send-LineMessage -Cfg $Cfg -Text ($lines -join "`n") | Out-Null
            $state['error::notifiedAt'] = $now.ToString('o')
        }
    }
    elseif (-not $failed.Count -and $state.ContainsKey('error::notifiedAt')) {
        $state.Remove('error::notifiedAt')
    }

    Save-State -State $state
}

function Invoke-CheckSafely {
    param([Parameter(Mandatory)]$Cfg)
    try {
        Invoke-Check -Cfg $Cfg
    }
    catch {
        # Per-target failures are handled inside Invoke-Check; reaching here
        # means something structural broke (unreadable state, bad config).
        Write-Log $_.Exception.Message ERROR
    }
}

# ------------------------------------------------------------------- main ----
$cfg = Get-Config -Path $Config

if ($TestNotify) {
    $lines = @('✅ Weverse 補貨監測：測試訊息', '')
    foreach ($t in @($cfg.resolvedTargets)) {
        $lines += "■ $($t.label)"
        $lines += "・$(@($t.watch) -join ', ')"
    }
    $ok = Send-LineMessage -Cfg $cfg -Text ($lines -join "`n")
    exit ($ok ? 0 : 1)
}

if ($Status) {
    foreach ($t in @($cfg.resolvedTargets)) {
        Write-Host ''
        Write-Host "■ $($t.label)  [id: $($t.id)]" -ForegroundColor Cyan
        Write-Host "  $($t.url)" -ForegroundColor DarkGray
        try {
            $saleStatus = ''
            $options = Get-SaleOptions -Url $t.url -Country $t.country `
                -TimeoutSec $cfg.requestTimeoutSec -MaxRetries $cfg.maxRetries `
                -SaleStatus ([ref]$saleStatus)
            $statusColor = if ($saleStatus -eq 'sale') { 'Green' } else { 'DarkYellow' }
            Write-Host "  sale status: $saleStatus" -ForegroundColor $statusColor
            $watched = Resolve-Watched -Watch @($t.watch) -Options $options -TargetLabel $t.label
            $options.GetEnumerator() | ForEach-Object {
                [pscustomobject]@{
                    Option    = $_.Key
                    Status    = if ($_.Value) { 'SOLD OUT' } else { 'AVAILABLE' }
                    Monitored = if ($watched.Contains($_.Key)) { 'yes' } else { '' }
                }
            } | Format-Table -AutoSize | Out-String | Write-Host
        }
        catch {
            Write-Host "  ERROR: $($_.Exception.Message)" -ForegroundColor Red
        }
    }
    exit 0
}

if ($Loop) {
    Write-Log "loop mode started; every $($cfg.intervalSeconds)s; $(@($cfg.resolvedTargets).Count) target(s)"
    while ($true) {
        Invoke-CheckSafely -Cfg $cfg
        Start-Sleep -Seconds $cfg.intervalSeconds
        # Re-read config so targets added over LINE are picked up without a restart.
        try { $cfg = Get-Config -Path $Config } catch { Write-Log $_.Exception.Message ERROR }
    }
}

Invoke-CheckSafely -Cfg $cfg
