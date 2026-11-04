@echo off
rem Starts the LINE webhook server. Leave this window open.
pushd "%~dp0"
call "%~dp0_pwsh.cmd" || (popd & exit /b 1)
"%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0webhook-server.ps1" %*
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
