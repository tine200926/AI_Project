#!/bin/sh
# Linux / macOS：快速掃描並輸出 HTML 報告。參數會直接傳給 secscan，例如 ./scan.sh --full
# 建議用 sudo 執行，才能看到所有程序與網路連線。
cd "$(dirname "$0")" || exit 1
mkdir -p reports
report="reports/scan-$(date +%Y%m%d-%H%M%S).html"
python3 -m secscan --html "$report" "$@"
rc=$?
if command -v xdg-open >/dev/null 2>&1; then xdg-open "$report" >/dev/null 2>&1 &
elif command -v open >/dev/null 2>&1; then open "$report"; fi
exit $rc
