@echo off
rem Locates PowerShell 7 (pwsh). The .ps1 scripts use PS7-only syntax, so
rem Windows PowerShell 5.1 cannot even parse them.
set "PWSH="
for %%P in (pwsh.exe) do if not defined PWSH set "PWSH=%%~$PATH:P"
if not defined PWSH if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" set "PWSH=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not defined PWSH if exist "%ProgramFiles(x86)%\PowerShell\7\pwsh.exe" set "PWSH=%ProgramFiles(x86)%\PowerShell\7\pwsh.exe"
if not defined PWSH if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe" set "PWSH=%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe"
if not defined PWSH (
  echo.
  echo PowerShell 7 ^(pwsh^) not found.
  echo Install it with:  winget install --id Microsoft.PowerShell
  echo.
  exit /b 1
)
exit /b 0
