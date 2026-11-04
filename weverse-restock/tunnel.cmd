@echo off
rem Installs cloudflared if needed and opens a tunnel to the webhook server.
rem Leave this window open; the public URL is printed below.
pushd "%~dp0"
call "%~dp0_pwsh.cmd" || (popd & exit /b 1)
"%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-tunnel.ps1" %*
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
