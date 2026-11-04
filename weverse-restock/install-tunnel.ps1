<#
.SYNOPSIS
    Installs cloudflared and starts a tunnel to the local webhook server.
.DESCRIPTION
    Two modes:

    -Quick (default)  Runs a TryCloudflare tunnel: no Cloudflare account, no
                      domain, nothing to configure. It prints a random
                      https://<words>.trycloudflare.com URL which you paste into
                      the LINE console. The catch: that URL changes every
                      restart, so you must re-paste it each time.

    -Named <name>     Uses a named tunnel tied to your Cloudflare account and a
                      domain you control. The URL is stable, and it can be
                      installed as a Windows service that survives reboots.
                      Requires `cloudflared tunnel login` once.
.EXAMPLE
    .\install-tunnel.ps1
.EXAMPLE
    .\install-tunnel.ps1 -Named weverse -Hostname weverse.example.com
#>
[CmdletBinding()]
param(
    [switch]$Quick,
    [string]$Named,
    [string]$Hostname,
    [int]$Port = 0,
    [switch]$InstallOnly
)

$ErrorActionPreference = 'Stop'

function Get-Cloudflared {
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $local = Join-Path $PSScriptRoot 'cloudflared.exe'
    if (Test-Path $local) { return $local }

    Write-Host 'cloudflared not found; installing...' -ForegroundColor Cyan

    # Prefer winget so updates are managed; fall back to a direct download.
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            # Out-Null matters: a native command's stdout would otherwise become
            # part of this function's return value, and the caller would get
            # winget's log text where it expects a path.
            winget install --id Cloudflare.cloudflared `
                --accept-source-agreements --accept-package-agreements `
                --disable-interactivity 2>&1 | Out-Null

            # The PATH of an already-running shell does not pick winget's
            # changes up, so probe the install locations directly. Note the
            # braces: "$env:ProgramFiles(x86)" would expand to
            # "C:\Program Files(x86)" and never match.
            $candidates = @(
                "$env:ProgramFiles\cloudflared\cloudflared.exe"
                "${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe"
                "$env:LOCALAPPDATA\Microsoft\WinGet\Links\cloudflared.exe"
                "$env:LOCALAPPDATA\Microsoft\WindowsApps\cloudflared.exe"
            )
            foreach ($p in $candidates) {
                if ($p -and (Test-Path $p)) { return $p }
            }
            $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
            if ($cmd) { return $cmd.Source }
        }
        catch { Write-Host "winget install failed ($($_.Exception.Message)); falling back to direct download." -ForegroundColor Yellow }
    }

    $url = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
    Write-Host "Downloading $url" -ForegroundColor DarkGray
    Invoke-WebRequest -Uri $url -OutFile $local -UseBasicParsing
    Unblock-File $local
    return $local
}

$exe = Get-Cloudflared
Write-Host "cloudflared: $exe" -ForegroundColor Green
& $exe --version

if ($InstallOnly) { return }

if (-not $Port) {
    $cfgPath = Join-Path $PSScriptRoot 'config.json'
    if (Test-Path $cfgPath) {
        $cfg = Get-Content $cfgPath -Raw -Encoding utf8 | ConvertFrom-Json
        $Port = if ($cfg.PSObject.Properties['webhookPort']) { $cfg.webhookPort } else { 8787 }
    }
    else { $Port = 8787 }
}
$local = "http://localhost:$Port"

if ($Named) {
    if (-not $Hostname) { throw 'A named tunnel needs -Hostname (a DNS name on a domain in your Cloudflare account).' }

    Write-Host ''
    Write-Host 'Named tunnel setup. If you have not logged in yet, run this first:' -ForegroundColor Cyan
    Write-Host "  $exe tunnel login" -ForegroundColor White
    Write-Host ''

    & $exe tunnel create $Named
    & $exe tunnel route dns $Named $Hostname

    Write-Host ''
    Write-Host 'Install as a service so it comes back after a reboot:' -ForegroundColor Cyan
    Write-Host "  $exe --config <config.yml> service install" -ForegroundColor White
    Write-Host ''
    Write-Host "LINE webhook URL:  https://$Hostname/webhook" -ForegroundColor Green
    Write-Host ''
    # --http-host-header is required: the webhook listens on a localhost-only
    # HttpListener prefix, which rejects any request whose Host header is the
    # tunnel's own hostname with a bare 400.
    & $exe tunnel run --url $local --http-host-header localhost $Named
    return
}

Write-Host ''
Write-Host "Starting a quick tunnel to $local" -ForegroundColor Cyan
Write-Host 'Watch for the https://<random>.trycloudflare.com line below, then set' -ForegroundColor Cyan
Write-Host 'the LINE webhook URL to that host with /webhook on the end.' -ForegroundColor Cyan
Write-Host 'This URL changes every restart. Leave this window open.' -ForegroundColor Yellow
Write-Host ''

# --http-host-header is required: the webhook listens on a localhost-only
# HttpListener prefix, which rejects any request whose Host header is the
# tunnel's own hostname with a bare 400.
& $exe tunnel --url $local --http-host-header localhost
