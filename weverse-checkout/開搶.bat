@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem ===== 在這裡設定 =====
rem 開搶時間：例 20:00 或 2026-10-15 20:00（留空 = 立即）
set "AT=2026-11-05 10:00"
rem 刷新間隔秒數（留空 = 不刷新，時間到直接開結帳頁）
set "REF=1"
rem ======================
echo 商品清單: items.txt
echo 開搶時間: %AT%   刷新間隔: %REF% 秒
set "ARGS=items.txt"
if defined AT set ARGS=%ARGS% --at "%AT%"
if defined REF set ARGS=%ARGS% --refresh %REF%
py weverse_checkout.py %ARGS%
pause
