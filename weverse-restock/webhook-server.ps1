<#
.SYNOPSIS
    LINE webhook server: add, remove and query monitored products by chat.
.DESCRIPTION
    Listens on http://localhost:<webhookPort>/ and expects to be reached through
    a Cloudflare Tunnel. Every request is authenticated with the X-Line-Signature
    HMAC, so a leaked tunnel URL cannot be used to rewrite your config.

    Commands (Chinese or English, case-insensitive):
      加 <url> [選項1,選項2]   add <url> [opt1,opt2]   新增監測（省略選項 = 全部）
      列表                     list                    列出目前監測的商品
      刪 <id>                  del <id>                移除某個商品
      狀態 [id]                status [id]             現在的庫存狀況
      說明                     help                    指令一覽
.EXAMPLE
    .\webhook-server.ps1
.EXAMPLE
    .\webhook-server.ps1 -Port 9000
#>
[CmdletBinding()]
param(
    [string]$Config = "$PSScriptRoot\config.json",
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'lib.ps1')

$script:LogFile = Join-Path $PSScriptRoot 'webhook.log'

# ------------------------------------------------------------- signature ----
function Test-LineSignature {
    # LINE signs the raw request body with the channel secret. Without this
    # check, anyone who learns the tunnel URL could edit the watch list.
    param(
        [Parameter(Mandatory)][byte[]]$BodyBytes,
        [string]$Signature,
        [Parameter(Mandatory)][string]$ChannelSecret
    )
    if ([string]::IsNullOrWhiteSpace($Signature)) { return $false }

    $hmac = [System.Security.Cryptography.HMACSHA256]::new(
        [System.Text.Encoding]::UTF8.GetBytes($ChannelSecret))
    try {
        $expected = [Convert]::ToBase64String($hmac.ComputeHash($BodyBytes))
    }
    finally { $hmac.Dispose() }

    # Constant-time comparison.
    $a = [System.Text.Encoding]::ASCII.GetBytes($expected)
    $b = [System.Text.Encoding]::ASCII.GetBytes($Signature)
    if ($a.Length -ne $b.Length) { return $false }
    $diff = 0
    for ($i = 0; $i -lt $a.Length; $i++) { $diff = $diff -bor ($a[$i] -bxor $b[$i]) }
    return ($diff -eq 0)
}

# -------------------------------------------------------------- commands ----
function Format-TargetList {
    param([Parameter(Mandatory)]$Cfg)

    $targets = @($Cfg.resolvedTargets)
    if (-not $targets.Count) { return '目前沒有監測任何商品。用「加 <網址>」新增。' }

    $lines = @("📋 監測中（$($targets.Count) 項）")
    foreach ($t in $targets) {
        $lines += ''
        $lines += "[$($t.id)] $($t.label)"
        $lines += "・選項：$(@($t.watch) -join ', ')"
    }
    $lines += ''
    $lines += '移除：刪 <id>'
    return ($lines -join "`n")
}

function Invoke-AddCommand {
    param(
        [Parameter(Mandatory)]$Cfg,
        [Parameter(Mandatory)][string]$Arguments
    )

    # First whitespace-separated token is the URL; the rest is the option list.
    $parts = $Arguments.Trim() -split '\s+', 2
    $url   = $parts[0]
    $watchRaw = if ($parts.Count -gt 1) { $parts[1] } else { '' }

    if ($url -notmatch '^https?://shop\.weverse\.io/') {
        return "網址看起來不對。請貼 Weverse Shop 的商品頁網址，例如`nhttps://shop.weverse.io/zh-tw/shop/KRW/artists/7/sales/42095"
    }
    # Strip tracking query strings so the same product does not get added twice.
    $url = ($url -split '[?#]')[0]

    foreach ($t in @($Cfg.resolvedTargets)) {
        if ($t.url -eq $url) {
            return "這個商品已經在監測清單裡了：`n[$($t.id)] $($t.label)`n・選項：$(@($t.watch) -join ', ')`n`n要改選項請先「刪 $($t.id)」再重新加。"
        }
    }

    $saleName = ''
    try {
        $options = Get-SaleOptions -Url $url -TimeoutSec $Cfg.requestTimeoutSec `
            -MaxRetries 2 -SaleName ([ref]$saleName)
    }
    catch {
        return "抓不到這個頁面：`n$($_.Exception.Message)"
    }

    $watch = @()
    if ($watchRaw) {
        # Accept commas, full-width commas, or Japanese/Chinese enumeration commas.
        $watch = @($watchRaw -split '[,，、]' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    }
    if (-not $watch.Count) { $watch = @('*') }

    $resolved = Resolve-Watched -Watch $watch -Options $options -Quiet

    # A single-SKU sale (quantity only, no chooser) still carries one
    # pseudo-option named after the product. There is nothing to pick, so an
    # unmatched option name should not block the add.
    if (-not $resolved.Count -and $options.Count -eq 1) {
        $watch = @('*')
        $resolved = Resolve-Watched -Watch $watch -Options $options -Quiet
    }

    if (-not $resolved.Count) {
        return (@(
            "指定的選項在這個頁面找不到。"
            ''
            '這頁有的選項：'
        ) + ($options.Keys | ForEach-Object { "・$_" }) + @(
            ''
            '想監測全部選項的話，不要帶選項名稱，直接貼網址就好。'
        )) -join "`n"
    }

    $label = if ($saleName) { $saleName } else { $url }
    $entry = [pscustomobject]@{ label = $label; url = $url; watch = $watch }

    if (-not $Cfg.PSObject.Properties['targets'] -or -not $Cfg.targets) {
        $Cfg | Add-Member -NotePropertyName targets -NotePropertyValue @() -Force
    }
    $Cfg.targets = @(@($Cfg.targets) + $entry)
    Save-Config -Cfg $Cfg -Path $Config

    $statusLines = foreach ($n in $resolved.Keys) {
        "・$n`t$(if ($options[$n]) { '售完' } else { '有貨' })"
    }
    Write-Log "added target: $label ($url) watch=$($watch -join ',')"
    return (@(
        '✅ 已加入監測'
        ''
        "■ $label"
        "・監測選項：$(@($watch) -join ', ')"
        ''
        '目前狀態：'
    ) + $statusLines) -join "`n"
}

function Invoke-DeleteCommand {
    param(
        [Parameter(Mandatory)]$Cfg,
        [Parameter(Mandatory)][string]$Arguments
    )
    $id = $Arguments.Trim()
    if (-not $id) { return '用法：刪 <id>。先用「列表」查 id。' }

    $targets = @($Cfg.resolvedTargets)
    $match = $targets | Where-Object { $_.id -eq $id -or $_.label -eq $id }
    if (-not $match) {
        return "找不到 id 或名稱為「$id」的商品。用「列表」看看目前有哪些。"
    }
    $match = @($match)[0]

    $Cfg.targets = @(@($Cfg.targets) | Where-Object { ($_.url -split '[?#]')[0] -ne $match.url })
    Save-Config -Cfg $Cfg -Path $Config

    # Drop that product's saved stock state so a re-add starts clean.
    $state = Get-State
    foreach ($k in @($state.Keys | Where-Object { $_ -like "option::$($match.id)::*" })) {
        $state.Remove($k)
    }
    Save-State -State $state

    Write-Log "removed target: $($match.label) ($($match.url))"
    return "🗑 已移除`n`n■ $($match.label)"
}

function Invoke-StatusCommand {
    param(
        [Parameter(Mandatory)]$Cfg,
        [string]$Arguments = ''
    )
    $targets = @($Cfg.resolvedTargets)
    $filter  = $Arguments.Trim()
    if ($filter) {
        $targets = @($targets | Where-Object { $_.id -eq $filter -or $_.label -like "*$filter*" })
        if (-not $targets.Count) { return "找不到「$filter」。用「列表」看看目前有哪些。" }
    }
    if (-not $targets.Count) { return '目前沒有監測任何商品。' }

    $lines = @()
    foreach ($t in $targets) {
        $lines += "■ $($t.label)"
        try {
            $options = Get-SaleOptions -Url $t.url -Country $t.country `
                -TimeoutSec $Cfg.requestTimeoutSec -MaxRetries 2
            $watched = Resolve-Watched -Watch @($t.watch) -Options $options -Quiet
            if (-not $watched.Count) { $lines += '・(監測的選項目前不在頁面上)' }
            foreach ($n in $watched.Keys) {
                $lines += "$(if ($options[$n]) { '  ✗' } else { '  ✓' }) $n　$(if ($options[$n]) { '售完' } else { '有貨' })"
            }
        }
        catch {
            $lines += "・查詢失敗：$($_.Exception.Message)"
        }
        $lines += ''
    }
    return ($lines -join "`n").TrimEnd()
}

function Get-HelpText {
    return @(
        '🤖 Weverse 補貨監測'
        ''
        '加 <網址> [選項1,選項2]'
        '　新增監測。省略選項＝監測全部選項。'
        '　例：加 https://shop.weverse.io/.../sales/42095 O.C.L'
        ''
        '列表　　目前監測的商品與 id'
        '刪 <id>　移除某個商品'
        '狀態 [id]　現在的庫存狀況'
        '說明　　這份指令一覽'
    ) -join "`n"
}

function Invoke-Command {
    param(
        [Parameter(Mandatory)]$Cfg,
        [Parameter(Mandatory)][string]$Text
    )
    $text = $Text.Trim()
    if (-not $text) { return Get-HelpText }

    $parts = $text -split '\s+', 2
    $verb  = $parts[0].ToLowerInvariant()
    $rest  = if ($parts.Count -gt 1) { $parts[1] } else { '' }

    # A bare URL with no verb is the common case: treat it as "add".
    if ($verb -match '^https?://') { return Invoke-AddCommand -Cfg $Cfg -Arguments $text }

    switch -Regex ($verb) {
        '^(加|新增|add)$'            { return Invoke-AddCommand    -Cfg $Cfg -Arguments $rest }
        '^(刪|刪除|移除|del|delete|remove|rm)$' { return Invoke-DeleteCommand -Cfg $Cfg -Arguments $rest }
        '^(列表|清單|list|ls)$'      { return Format-TargetList    -Cfg $Cfg }
        '^(狀態|庫存|status)$'       { return Invoke-StatusCommand -Cfg $Cfg -Arguments $rest }
        '^(說明|help|\?|？)$'        { return Get-HelpText }
        default {
            return "看不懂「$($parts[0])」。`n`n$(Get-HelpText)"
        }
    }
}

# ---------------------------------------------------------------- handler ----
function Invoke-WebhookEvents {
    param([Parameter(Mandatory)]$Payload)

    if (-not $Payload.PSObject.Properties['events']) { return }

    foreach ($ev in @($Payload.events)) {
        if ($ev.type -ne 'message' -or $ev.message.type -ne 'text') { continue }

        # Re-read the config per event: the monitor and earlier commands may
        # both have changed it since this process started.
        $cfg = Get-Config -Path $Config

        $senderId = if ($ev.source.PSObject.Properties['userId']) { $ev.source.userId } else { '' }
        $allowed  = $cfg.line.to
        if ($allowed -and $senderId -and $senderId -ne $allowed) {
            # Someone else found the bot; answer nothing useful and log it.
            Write-Log "ignoring message from unauthorized sender $senderId" WARN
            Send-LineReply -Cfg $cfg -ReplyToken $ev.replyToken -Text '這是私人用的機器人。' | Out-Null
            continue
        }

        $text = $ev.message.text
        Write-Log "command: $text"
        try {
            $reply = Invoke-Command -Cfg $cfg -Text $text
        }
        catch {
            Write-Log "command failed: $($_.Exception.Message)" ERROR
            $reply = "指令執行失敗：`n$($_.Exception.Message)"
        }
        Send-LineReply -Cfg $cfg -ReplyToken $ev.replyToken -Text $reply | Out-Null
    }
}

# ------------------------------------------------------------------- main ----
$cfg = Get-Config -Path $Config

if (-not $cfg.line.PSObject.Properties['channelSecret'] -or
    -not $cfg.line.channelSecret -or
    $cfg.line.channelSecret -like 'YOUR_*') {
    throw 'config.json needs line.channelSecret (LINE console -> Basic settings -> Channel secret) before the webhook can verify requests.'
}

if (-not $Port) { $Port = $cfg.webhookPort }
$prefix = "http://localhost:$Port/"

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add($prefix)
try {
    $listener.Start()
}
catch {
    throw "Could not listen on $prefix - is another copy already running, or the port in use? ($($_.Exception.Message))"
}

Write-Log "webhook server listening on $prefix"
Write-Host ''
Write-Host "Point the Cloudflare Tunnel at $prefix" -ForegroundColor Cyan
Write-Host 'Then set the LINE webhook URL to  https://<your-tunnel-host>/webhook' -ForegroundColor Cyan
Write-Host 'Ctrl+C to stop.' -ForegroundColor DarkGray
Write-Host ''

try {
    while ($listener.IsListening) {
        $ctx = $listener.GetContext()
        $req = $ctx.Request
        $res = $ctx.Response
        $status = 200
        $replyBody = 'OK'
        $pending = $null

        try {
            if ($req.HttpMethod -eq 'GET') {
                # Handy for confirming the tunnel is up from a browser.
                $replyBody = 'weverse-restock webhook is alive'
            }
            elseif ($req.HttpMethod -ne 'POST') {
                $status = 405; $replyBody = 'Method Not Allowed'
            }
            else {
                $ms = [System.IO.MemoryStream]::new()
                $req.InputStream.CopyTo($ms)
                $bodyBytes = $ms.ToArray()
                $ms.Dispose()

                $sig = $req.Headers['X-Line-Signature']
                if (-not (Test-LineSignature -BodyBytes $bodyBytes -Signature $sig -ChannelSecret $cfg.line.channelSecret)) {
                    Write-Log "rejected request with bad signature from $($req.RemoteEndPoint)" WARN
                    $status = 403; $replyBody = 'Forbidden'
                }
                else {
                    $json = [System.Text.Encoding]::UTF8.GetString($bodyBytes)
                    $payload = $json | ConvertFrom-Json
                    # LINE retries on anything but a prompt 200, so acknowledge
                    # first and let the handler run after the response is sent.
                    $pending = $payload
                }
            }
        }
        catch {
            Write-Log "request handling error: $($_.Exception.Message)" ERROR
            $status = 500; $replyBody = 'Error'
        }

        try {
            $buf = [System.Text.Encoding]::UTF8.GetBytes($replyBody)
            $res.StatusCode = $status
            $res.ContentType = 'text/plain; charset=utf-8'
            $res.ContentLength64 = $buf.Length
            $res.OutputStream.Write($buf, 0, $buf.Length)
            $res.OutputStream.Close()
        }
        catch { Write-Log "failed to write response: $($_.Exception.Message)" WARN }

        if ($pending) {
            Invoke-WebhookEvents -Payload $pending
            $pending = $null
        }
    }
}
finally {
    $listener.Stop()
    $listener.Close()
    Write-Log 'webhook server stopped'
}
