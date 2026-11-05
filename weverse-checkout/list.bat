@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 列出 items.txt 裡每個商品的所有選項（編號 / 名稱 / saleStockId），用來確認清單要怎麼寫
py weverse_checkout.py items.txt --list
pause
