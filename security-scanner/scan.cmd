@echo off
rem Double-click to run a quick security scan and open the HTML report.
rem Any arguments are passed through, e.g.
rem   scan.cmd --full
rem   scan.cmd --path D:\Downloads
rem   scan.cmd --checks protection
rem Without arguments it asks for administrator rights (needed to read
rem Defender exclusions and every process); with arguments it runs as-is.
setlocal
pushd "%~dp0"

if "%~1"=="" (
  net session >nul 2>&1
  if errorlevel 1 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
    if not errorlevel 1 (popd & exit /b 0)
    echo Could not elevate - continuing without administrator rights.
  )
)

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (where python >nul 2>&1 && set "PY=python")
if not defined PY (
  echo.
  echo Python 3 not found. Install it with:  winget install --id Python.Python.3.12
  echo.
  pause
  popd & exit /b 1
)

for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "TS=%%T"
if not exist "%~dp0reports" mkdir "%~dp0reports"
set "REPORT=%~dp0reports\scan-%TS%.html"

%PY% -m secscan --html "%REPORT%" %*
set "RC=%ERRORLEVEL%"
if exist "%REPORT%" start "" "%REPORT%"
echo.
pause
popd
exit /b %RC%
