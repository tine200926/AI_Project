<#
    Shared helpers for the Weverse restock monitor.
    Dot-sourced by weverse-restock.ps1 and webhook-server.ps1 so both speak to
    the same config, state and LINE plumbing.
#>

$script:ProjectRoot = $PSScriptRoot
$script:StateFile   = Join-Path $PSScriptRoot 'state.json'
$script:LogFile     = Join-Path $PSScriptRoot 'restock.log'

# Rotation policy; Get-Config overrides these from config.json.
$script:LogMaxBytes = 1MB
$script:LogKeep     = 3

# ---------------------------------------------------------------- logging ----
function Write-Log {
    param(
        [Parameter(Mandatory)][string]$Message,
        [ValidateSet('INFO', 'WARN', 'ERROR', 'HIT')][string]$Level = 'INFO',
        [string]$LogFile = $script:LogFile
    )
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message

    $color = switch ($Level) {
        'WARN'  { 'Yellow' }
        'ERROR' { 'Red' }
        'HIT'   { 'Green' }
        default { 'Gray' }
    }
    Write-Host $line -ForegroundColor $color

    # Rotate so an every-5-minutes task cannot fill the disk, keeping a few
    # generations: the cap is logMaxMB * (logKeep + 1).
    if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -gt $script:LogMaxBytes) {
        for ($i = $script:LogKeep; $i -ge 1; $i--) {
            $older = "$LogFile.$i"
            if (Test-Path $older) {
                if ($i -eq $script:LogKeep) { Remove-Item $older -Force }
                else { Move-Item $older "$LogFile.$($i + 1)" -Force }
            }
        }
        Move-Item $LogFile "$LogFile.1" -Force
    }
    Add-Content -LiteralPath $LogFile -Value $line -Encoding utf8
}

# ----------------------------------------------------------------- config ----
function Get-TargetId {
    # Short, stable key for state.json. Weverse URLs end in the sale id
    # (.../sales/42095), which is the natural identifier; fall back to a
    # sanitized URL when the shape is unfamiliar.
    param([string]$Url)

    if ($Url -match '/sales/(\d+)') { return $Matches[1] }
    return ($Url -replace '[^A-Za-z0-9]', '_')
}

function Get-Config {
    param([string]$Path = (Join-Path $script:ProjectRoot 'config.json'))

    if (-not (Test-Path $Path)) {
        throw "Config not found: $Path  (copy config.example.json to config.json and fill it in)"
    }

    # The webhook server can be rewriting this file while the monitor reads it;
    # a torn read is transient, so retry briefly before giving up.
    $cfg = $null
    foreach ($attempt in 1..3) {
        try {
            $cfg = Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json
            break
        }
        catch {
            if ($attempt -eq 3) { throw "config.json is not valid JSON: $($_.Exception.Message)" }
            Start-Sleep -Milliseconds 300
        }
    }

    $defaults = [ordered]@{
        intervalSeconds   = 300
        cooldownMinutes   = 180
        notifyOnFirstRun  = $false
        notifyOnSoldOut   = $false
        notifyOnError     = $true
        requestTimeoutSec = 30
        maxRetries        = 3
        politeDelayMs     = 1500
        webhookPort       = 8787
        logMaxMB          = 1
        logKeep           = 3
        logEveryOption    = $false
    }
    foreach ($k in $defaults.Keys) {
        if (-not $cfg.PSObject.Properties[$k]) {
            $cfg | Add-Member -NotePropertyName $k -NotePropertyValue $defaults[$k]
        }
    }
    # Google Sheet mode: the sheet owns the target list and may override the
    # tunable settings above. LINE credentials always stay in config.json.
    $sheetMode = Test-SheetMode $cfg
    if ($sheetMode) { Merge-SheetConfig -Cfg $cfg -Defaults $defaults }
    $cfg | Add-Member -NotePropertyName sheetMode -NotePropertyValue $sheetMode -Force

    $script:LogMaxBytes = [int]$cfg.logMaxMB * 1MB
    $script:LogKeep     = [int]$cfg.logKeep

    # Accept both shapes: a `targets` array, or a single top-level url/watch
    # pair (the original one-product config stays valid).
    $targets = @()
    if ($sheetMode) {
        # An empty sheet is a legitimate state (everything paused or deleted).
        $targets = @($cfg.targets | Where-Object { $_ })
    }
    elseif ($cfg.PSObject.Properties['targets'] -and $cfg.targets) {
        $targets = @($cfg.targets)
    }
    elseif ($cfg.PSObject.Properties['url'] -and $cfg.url) {
        $single = [pscustomobject]@{
            label = if ($cfg.PSObject.Properties['label'] -and $cfg.label) { $cfg.label } else { $cfg.url }
            url   = $cfg.url
            watch = $cfg.watch
        }
        $targets = @($single)
    }
    else {
        throw "Config needs either a 'targets' array or a top-level 'url'"
    }

    $seen = @{}
    foreach ($t in $targets) {
        foreach ($required in 'url', 'watch') {
            if (-not $t.PSObject.Properties[$required] -or -not $t.$required) {
                throw "Every target needs '$required' (offending entry: $($t | ConvertTo-Json -Compress))"
            }
        }
        if (-not $t.PSObject.Properties['label'] -or -not $t.label) {
            if ($t.PSObject.Properties['label']) { $t.label = $t.url }
            else { $t | Add-Member -NotePropertyName label -NotePropertyValue $t.url }
        }
        # Optional per-target country override; blank means "infer from the URL".
        if (-not $t.PSObject.Properties['country']) {
            $t | Add-Member -NotePropertyName country -NotePropertyValue ''
        }

        # Two targets may legitimately share a sale id, so disambiguate rather
        # than letting their saved state collide.
        $id = Get-TargetId $t.url
        if ($seen.ContainsKey($id)) {
            $seen[$id]++
            $id = "$id-$($seen[$id])"
        }
        else { $seen[$id] = 1 }
        $t | Add-Member -NotePropertyName id -NotePropertyValue $id -Force
    }

    $cfg | Add-Member -NotePropertyName resolvedTargets -NotePropertyValue $targets -Force
    return $cfg
}

function Save-Config {
    # Atomic rewrite: the monitor may read this file at any moment, so never
    # leave a half-written config on disk.
    param(
        [Parameter(Mandatory)]$Cfg,
        [string]$Path = (Join-Path $script:ProjectRoot 'config.json')
    )

    if ($Cfg.PSObject.Properties['sheetMode'] -and $Cfg.sheetMode) {
        # The targets and settings in $Cfg came from the sheet; writing them
        # back would freeze a copy into config.json. Use Add-/Remove-Target.
        throw 'Save-Config is not used in Google Sheet mode'
    }

    $out = $Cfg | Select-Object -Property * -ExcludeProperty resolvedTargets, sheetMode
    foreach ($t in @($out.targets)) {
        if (-not $t) { continue }
        # 'id' is derived, and an inferred country should not be frozen into the
        # file - only an explicit override is worth persisting.
        if ($t.PSObject.Properties['id']) { $t.PSObject.Properties.Remove('id') }
        if ($t.PSObject.Properties['country'] -and -not $t.country) {
            $t.PSObject.Properties.Remove('country')
        }
    }

    $tmp = "$Path.tmp"
    $out | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $tmp -Encoding utf8
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Add-Target {
    # Append a target (or, in sheet mode, add/update the sheet row).
    param(
        [Parameter(Mandatory)]$Cfg,
        [Parameter(Mandatory)]$Entry,
        [string]$Path = (Join-Path $script:ProjectRoot 'config.json')
    )
    if ($Cfg.sheetMode) {
        Invoke-SheetApi -Cfg $Cfg -Action add -Params @{
            url   = $Entry.url
            label = $Entry.label
            watch = (@($Entry.watch) -join ', ')
        } | Out-Null
        return
    }
    if (-not $Cfg.PSObject.Properties['targets'] -or -not $Cfg.targets) {
        $Cfg | Add-Member -NotePropertyName targets -NotePropertyValue @() -Force
    }
    $Cfg.targets = @(@($Cfg.targets) + $Entry)
    Save-Config -Cfg $Cfg -Path $Path
}

function Remove-Target {
    param(
        [Parameter(Mandatory)]$Cfg,
        [Parameter(Mandatory)][string]$Url,
        [string]$Path = (Join-Path $script:ProjectRoot 'config.json')
    )
    if ($Cfg.sheetMode) {
        Invoke-SheetApi -Cfg $Cfg -Action remove -Params @{ url = $Url } | Out-Null
        return
    }
    $Cfg.targets = @(@($Cfg.targets) | Where-Object { ($_.url -split '[?#]')[0] -ne $Url })
    Save-Config -Cfg $Cfg -Path $Path
}

# ----------------------------------------------------------- google sheet ----
# The sheet is served by the Apps Script in google-sheet/Code.gs. Its last good
# response is cached so a Google hiccup does not stop the monitor.
$script:SheetCacheFile = Join-Path $PSScriptRoot 'sheet-cache.json'

# Settings the sheet may override. webhookPort and the log policy are left
# out on purpose: changing them needs a restart, so they live in config.json.
$script:SheetSettingKeys = @(
    'intervalSeconds', 'cooldownMinutes', 'notifyOnFirstRun', 'notifyOnSoldOut',
    'notifyOnError', 'requestTimeoutSec', 'maxRetries', 'politeDelayMs', 'logEveryOption'
)

function Test-SheetMode {
    param($Cfg)
    return [bool]($Cfg.PSObject.Properties['sheet'] -and $Cfg.sheet -and
        $Cfg.sheet.PSObject.Properties['url'] -and $Cfg.sheet.url -and
        $Cfg.sheet.url -notlike 'YOUR_*')
}

function ConvertTo-SheetBool {
    # Sheet cells arrive as real booleans from checkboxes, but people also type
    # TRUE / 是 / 1 by hand.
    param($Value, [bool]$Default)
    if ($Value -is [bool]) { return $Value }
    $s = ([string]$Value).Trim().ToLowerInvariant()
    if (-not $s) { return $Default }
    if ($s -in 'true', '1', 'yes', 'y', 'v', '是', '開', 'on', '✓', '✔') { return $true }
    if ($s -in 'false', '0', 'no', 'n', '否', '關', 'off') { return $false }
    return $Default
}

function Invoke-SheetApi {
    param(
        [Parameter(Mandatory)]$Cfg,
        [string]$Action = 'read',
        [hashtable]$Params = @{}
    )
    if (-not $Cfg.sheet.key -or $Cfg.sheet.key -like 'YOUR_*') {
        throw 'config.json 的 sheet.key 還沒填（要和 Apps Script 裡的 SECRET 相同）'
    }
    $query = [ordered]@{ key = $Cfg.sheet.key; action = $Action }
    foreach ($k in $Params.Keys) { $query[$k] = $Params[$k] }
    $qs = ($query.GetEnumerator() | ForEach-Object {
            '{0}={1}' -f [uri]::EscapeDataString($_.Key), [uri]::EscapeDataString([string]$_.Value)
        }) -join '&'
    $sep = if ($Cfg.sheet.url.Contains('?')) { '&' } else { '?' }
    $timeout = if ($Cfg.PSObject.Properties['requestTimeoutSec']) { [int]$Cfg.requestTimeoutSec } else { 30 }

    # Apps Script answers with a redirect to googleusercontent.com, which
    # Invoke-RestMethod follows on its own.
    $resp = Invoke-RestMethod -Uri "$($Cfg.sheet.url)$sep$qs" -TimeoutSec $timeout
    if ($resp -is [string]) {
        # An HTML page here means a Google sign-in wall: the web app was not
        # deployed with access set to "Anyone".
        throw 'Google 試算表回傳的不是 JSON。請確認 Apps Script 部署成「網頁應用程式」，存取權設為「任何人」。'
    }
    if (-not $resp.ok) { throw "Google 試算表回報錯誤：$($resp.error)" }
    return $resp
}

function Merge-SheetConfig {
    param(
        [Parameter(Mandatory)]$Cfg,
        [Parameter(Mandatory)]$Defaults
    )

    $data = $null
    try {
        $data = Invoke-SheetApi -Cfg $Cfg -Action read
        try {
            $data | ConvertTo-Json -Depth 8 |
                Set-Content -LiteralPath $script:SheetCacheFile -Encoding utf8
        }
        catch { }
    }
    catch {
        if (-not (Test-Path $script:SheetCacheFile)) {
            throw "讀不到 Google 試算表，也沒有之前的快取：$($_.Exception.Message)"
        }
        Write-Log "Google 試算表讀取失敗，先用上次的快取：$($_.Exception.Message)" WARN
        $data = Get-Content -LiteralPath $script:SheetCacheFile -Raw -Encoding utf8 | ConvertFrom-Json
    }

    $list = [System.Collections.Generic.List[object]]::new()
    foreach ($row in @($data.targets)) {
        if (-not $row -or -not $row.url) { continue }
        if ($row.PSObject.Properties['enabled'] -and
            -not (ConvertTo-SheetBool $row.enabled $true)) { continue }

        $watch = @(([string]$row.watch) -split '[,，、]' |
            ForEach-Object { $_.Trim() } | Where-Object { $_ })
        if (-not $watch.Count) { $watch = @('*') }

        $t = [pscustomobject]@{
            label = ([string]$row.label).Trim()
            url   = (([string]$row.url).Trim() -split '[?#]')[0]
            watch = $watch
        }
        if ($row.PSObject.Properties['country'] -and $row.country) {
            $t | Add-Member -NotePropertyName country -NotePropertyValue ([string]$row.country).Trim()
        }
        $list.Add($t)
    }
    $Cfg | Add-Member -NotePropertyName targets -NotePropertyValue $list.ToArray() -Force

    if ($data.PSObject.Properties['settings'] -and $data.settings) {
        foreach ($p in $data.settings.PSObject.Properties) {
            if ($p.Name -notin $script:SheetSettingKeys) {
                Write-Log "試算表 settings 裡的「$($p.Name)」不是可調整的參數，已忽略" WARN
                continue
            }
            $default = $Defaults[$p.Name]
            try {
                $value = if ($default -is [bool]) { ConvertTo-SheetBool $p.Value $Cfg.($p.Name) }
                         else { [int]$p.Value }
            }
            catch {
                Write-Log "試算表 settings 的「$($p.Name)」值「$($p.Value)」不是數字，已忽略" WARN
                continue
            }
            $Cfg | Add-Member -NotePropertyName $p.Name -NotePropertyValue $value -Force
        }
    }
}

# ------------------------------------------------------------------ state ----
function Get-State {
    param([string]$Path = $script:StateFile)

    if (-not (Test-Path $Path)) { return @{} }
    try {
        $raw = Get-Content -LiteralPath $Path -Raw -Encoding utf8
        if ([string]::IsNullOrWhiteSpace($raw)) { return @{} }
        $state = @{}
        foreach ($p in ($raw | ConvertFrom-Json).PSObject.Properties) {
            $state[$p.Name] = $p.Value
        }
        return $state
    }
    catch {
        Write-Log "state.json unreadable, starting fresh: $($_.Exception.Message)" WARN
        return @{}
    }
}

function Save-State {
    param(
        [Parameter(Mandatory)][hashtable]$State,
        [string]$Path = $script:StateFile
    )
    $tmp = "$Path.tmp"
    $State | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $tmp -Encoding utf8
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

# ------------------------------------------------------------------ fetch ----
function Get-ShopCountry {
    # Region-restricted sales (JP-only merch, etc.) render an empty page unless
    # the request claims to be from that country. Infer it from the currency in
    # the URL; an explicit country in the config always wins.
    param(
        [Parameter(Mandatory)][string]$Url,
        [string]$Country = ''
    )
    if ($Country) { return $Country.ToUpperInvariant() }

    $currency = if ($Url -match '/shop/([A-Z]{3})/') { $Matches[1] } else { '' }
    switch ($currency) {
        'JPY'   { return 'JP' }
        'KRW'   { return 'KR' }
        'USD'   { return 'US' }
        'TWD'   { return 'TW' }
        default { return '' }
    }
}

function Get-SaleOptions {
    # Returns an ordered hashtable of option name -> [bool] isSoldOut.
    # Weverse renders the sale payload into the page itself, so one plain GET is
    # enough: no login and no headless browser - only the region cookies below.
    param(
        [Parameter(Mandatory)][string]$Url,
        [int]$TimeoutSec = 30,
        [int]$MaxRetries = 3,
        [string]$Country = '',
        [ref]$SaleName,
        [ref]$SaleStatus
    )

    $headers = @{
        'User-Agent'      = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
        'Accept'          = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        'Accept-Language' = 'zh-TW,zh;q=0.9,en;q=0.8'
        'Cache-Control'   = 'no-cache'
    }

    # Both cookies are required: setting only wes_display_user_country still
    # returns the "cannot be viewed in your country" shell.
    $shopCountry = Get-ShopCountry -Url $Url -Country $Country
    if ($shopCountry) {
        $cookies = @(
            "wes_display_user_country=$shopCountry"
            "wes_order_user_country=$shopCountry"
        )
        if ($Url -match '/shop/([A-Z]{3})/')  { $cookies += "wes_currency=$($Matches[1])" }
        if ($Url -match '/artists/(\d+)/')    { $cookies += "wes_artistId=$($Matches[1])" }
        $headers['Cookie'] = $cookies -join '; '
    }

    $html = $null
    for ($attempt = 1; $attempt -le $MaxRetries; $attempt++) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -Headers $headers `
                -TimeoutSec $TimeoutSec -UseBasicParsing
            $html = $resp.Content
            break
        }
        catch {
            $msg = $_.Exception.Message
            if ($attempt -eq $MaxRetries) { throw "fetch failed after $attempt attempts: $msg" }
            $backoff = [int][Math]::Pow(2, $attempt) * 3   # 6s, 12s, ...
            Write-Log "fetch attempt $attempt failed ($msg); retrying in ${backoff}s" WARN
            Start-Sleep -Seconds $backoff
        }
    }

    $options = [ordered]@{}
    $pattern = '"saleOptionName":"(?<name>(?:[^"\\]|\\.)*)","isSoldOut":(?<sold>true|false)'
    foreach ($m in [regex]::Matches($html, $pattern)) {
        # Option names arrive JSON-escaped; undo the escapes that actually occur.
        # Trim too: single-SKU sales carry one pseudo-option named after the
        # product, often with a trailing space that would leak into messages
        # and into state keys.
        $name = ($m.Groups['name'].Value -replace '\\"', '"' -replace '\\\\', '\').Trim()
        if (-not $name) { $name = '(單一規格)' }
        $options[$name] = [bool]::Parse($m.Groups['sold'].Value)
    }

    # The sale-level status overrides the per-option flags, which go stale: a
    # sale sitting at SOLD_OUT can still carry "isSoldOut":false options that
    # are not actually buyable. "sale.status.*" is a distinctive enough key to
    # match on without anchoring to the sale object.
    $statusCode = ''
    if ($html -match '"statusCode":"sale\.status\.(?<c>[a-z.]+)"') {
        $statusCode = $Matches['c']
    }
    $sellable = switch ($statusCode) {
        'sale'         { $true }
        ''             { $null }
        # Known non-purchasable states.
        'out.of.stock' { $false }
        'end'          { $false }
        'ended'        { $false }
        'closed'       { $false }
        'stop'         { $false }
        'to.be.sold'   { $false }
        default        { $null }   # unrecognised - fall back to the flags
    }

    if ($sellable -eq $false) {
        foreach ($k in @($options.Keys)) { $options[$k] = $true }
    }
    elseif ($null -eq $sellable) {
        # Never go silently blind if Weverse renames a status: keep using the
        # per-option flags, but say so.
        Write-Log "unrecognised sale status '$statusCode' for $Url - relying on per-option isSoldOut flags" WARN
    }

    if ($SaleStatus) { $SaleStatus.Value = $statusCode }

    if ($options.Count -eq 0) {
        # A region-blocked page returns HTTP 200 with the product data stripped
        # out entirely, so "no Product block" is the signal to point at country
        # settings rather than at a markup change.
        if ($html -notmatch '"@type":"Product"') {
            $hint = if ($shopCountry) {
                "tried country '$shopCountry'; if the sale is limited to another region, set 'country' on this target"
            } else {
                "no country could be inferred from the URL - set 'country' on this target (e.g. 'JP')"
            }
            throw "page returned no product data, which usually means the sale is not viewable from the configured country ($hint)"
        }
        throw 'parsed 0 options from the page - Weverse may have changed its markup, or the sale was removed'
    }

    # Product name, used to auto-label targets added over LINE. The page
    # carries a schema.org JSON-LD block whose "name" is the product; plain
    # "name" keys are ambiguous (artists have one too), and <title> is
    # "ARTIST | Product", so the product is the part after the pipe.
    if ($SaleName) {
        $SaleName.Value = ''
        $product = ''
        $artist  = ''

        if ($html -match '"@type":"Product","name":"(?<n>(?:[^"\\]|\\.)*)"') {
            $product = $Matches['n']
        }
        elseif ($html -match '<title>(?<n>[^<]+)</title>') {
            $parts = $Matches['n'] -split '\s*\|\s*', 2
            $product = if ($parts.Count -eq 2) { $parts[1] } else { $parts[0] }
        }
        if ($html -match '"labelArtistInfo":\{[^}]*?"name":"(?<a>(?:[^"\\]|\\.)*)"') {
            $artist = $Matches['a']
        }

        $product = ($product -replace '\\"', '"' -replace '\\/', '/').Trim()
        $artist  = ($artist  -replace '\\"', '"' -replace '\\/', '/').Trim()

        $SaleName.Value = if ($artist -and $product) { "$artist / $product" }
                          elseif ($product) { $product }
                          else { '' }
    }

    return $options
}

# ------------------------------------------------------------- name match ----
function Get-NormalizedName {
    param([string]$Name)
    # The UI shows "O.C.L" but the payload says "O.C.L."; dropping case and
    # punctuation keeps the watch list forgiving to type.
    return (($Name -replace '[^\p{L}\p{N}]', '').ToUpperInvariant())
}

function Resolve-Watched {
    # Maps each watch-list entry to the real option name(s) on the page.
    # A single '*' entry watches every option.
    param(
        [Parameter(Mandatory)][string[]]$Watch,
        [Parameter(Mandatory)]$Options,
        [string]$TargetLabel = '',
        [switch]$Quiet
    )
    $resolved = [ordered]@{}

    if ($Watch -contains '*') {
        foreach ($k in $Options.Keys) { $resolved[$k] = $true }
        return $resolved
    }

    foreach ($wanted in $Watch) {
        $target = Get-NormalizedName $wanted
        if (-not $target) { continue }

        # Try the tightest match first and only widen if nothing hit, so
        # "O.C.L" cannot silently also select some longer unrelated option.
        # The substring pass matters because Weverse option names often carry
        # a bracketed prefix, e.g. "[6/30(月)受け取り]ACRYLIC STAND".
        $hit = $false
        foreach ($mode in 'exact', 'prefix', 'contains') {
            foreach ($actual in $Options.Keys) {
                $candidate = Get-NormalizedName $actual
                $matched = switch ($mode) {
                    'exact'    { $candidate -eq $target }
                    'prefix'   { $candidate.StartsWith($target) }
                    'contains' { $candidate.Contains($target) }
                }
                if ($matched) {
                    $resolved[$actual] = $true
                    $hit = $true
                }
            }
            if ($hit) { break }
        }
        if (-not $hit -and -not $Quiet) {
            Write-Log "[$TargetLabel] watched option '$wanted' is not on the page right now (page has: $($Options.Keys -join ', '))" WARN
        }
    }
    return $resolved
}

# ------------------------------------------------------------------- LINE ----
function Send-LineMessage {
    param(
        [Parameter(Mandatory)]$Cfg,
        [Parameter(Mandatory)][string]$Text
    )
    if (-not $Cfg.PSObject.Properties['line'] -or
        -not $Cfg.line.channelAccessToken -or
        $Cfg.line.channelAccessToken -like 'YOUR_*') {
        Write-Log "LINE is not configured; message not sent: $($Text -replace '\r?\n', ' / ')" WARN
        return $false
    }

    # LINE rejects text over 5000 characters outright.
    if ($Text.Length -gt 4900) { $Text = $Text.Substring(0, 4880) + "`n...(略)" }

    $body = @{
        to       = $Cfg.line.to
        messages = @(@{ type = 'text'; text = $Text })
    } | ConvertTo-Json -Depth 5 -Compress

    try {
        Invoke-RestMethod -Method Post -Uri 'https://api.line.me/v2/bot/message/push' `
            -Headers @{ Authorization = "Bearer $($Cfg.line.channelAccessToken)" } `
            -ContentType 'application/json; charset=utf-8' `
            -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) `
            -TimeoutSec 20 | Out-Null
        Write-Log 'LINE push sent'
        return $true
    }
    catch {
        # LINE answers with a JSON body explaining the rejection; surface it.
        $detail = $_.Exception.Message
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $detail = $_.ErrorDetails.Message }
        Write-Log "LINE push FAILED: $detail" ERROR
        return $false
    }
}

function Send-LineReply {
    # Replying to a webhook event is free and does not count against the
    # monthly push quota, so always prefer it inside the webhook handler.
    param(
        [Parameter(Mandatory)]$Cfg,
        [Parameter(Mandatory)][string]$ReplyToken,
        [Parameter(Mandatory)][string]$Text
    )
    if ($Text.Length -gt 4900) { $Text = $Text.Substring(0, 4880) + "`n...(略)" }

    $body = @{
        replyToken = $ReplyToken
        messages   = @(@{ type = 'text'; text = $Text })
    } | ConvertTo-Json -Depth 5 -Compress

    try {
        Invoke-RestMethod -Method Post -Uri 'https://api.line.me/v2/bot/message/reply' `
            -Headers @{ Authorization = "Bearer $($Cfg.line.channelAccessToken)" } `
            -ContentType 'application/json; charset=utf-8' `
            -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) `
            -TimeoutSec 20 | Out-Null
        return $true
    }
    catch {
        $detail = $_.Exception.Message
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $detail = $_.ErrorDetails.Message }
        Write-Log "LINE reply FAILED: $detail" ERROR
        return $false
    }
}
