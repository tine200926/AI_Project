@echo off
chcp 65001 >nul
cd /d "%~dp0"
py weverse_checkout.py items.txt
pause
