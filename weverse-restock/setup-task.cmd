@echo off
rem Registers the Scheduled Task that runs the monitor. Examples:
rem   setup-task.cmd
rem   setup-task.cmd -IntervalMinutes 2
rem   setup-task.cmd -Uninstall
pushd "%~dp0"
call "%~dp0_pwsh.cmd" || (popd & exit /b 1)
"%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-task.ps1" %*
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
