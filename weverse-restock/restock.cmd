@echo off
rem Runs the monitor. Any arguments are passed through, e.g.
rem   restock.cmd -Status
rem   restock.cmd -TestNotify
rem   restock.cmd -Loop
pushd "%~dp0"
call "%~dp0_pwsh.cmd" || (popd & exit /b 1)
"%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0weverse-restock.ps1" %*
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
