"""命令列入口：python -m secscan [選項]"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from datetime import datetime
from typing import List

from . import __version__
from .checks import files, network, persistence, processes, protection
from .context import HASH_DIR, Allowlist, ScanContext, TOOL_DIR, default_hash_files, load_hashes, load_ips
from .model import CHECK_LABELS, Severity
from .report import collect_meta, print_console, to_html, to_json
from .util import enable_ansi, is_admin

ALL_CHECKS = list(CHECK_LABELS)
MALWAREBAZAAR_RECENT = "https://bazaar.abuse.ch/export/txt/sha256/recent/"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="secscan",
        description="掃描本機電腦，找出可疑的檔案、開機自動執行項目、程序、網路連線與防毒設定。只讀取、不刪除任何東西。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="範例：\n"
               "  python -m secscan                       快速掃描（常見藏毒位置）\n"
               "  python -m secscan --full --html r.html  掃描整顆磁碟並輸出 HTML 報告\n"
               "  python -m secscan --path D:\\下載         只掃指定資料夾\n"
               "  python -m secscan --checks protection   只檢查防毒／防火牆狀態\n"
               "  python -m secscan --update-hashes       下載最新惡意程式雜湊清單\n",
    )
    p.add_argument("--full", action="store_true", help="掃描所有本機磁碟（較慢）；預設只掃常見藏毒位置")
    p.add_argument("--path", action="append", default=[], metavar="資料夾", help="指定要掃描的資料夾（可重複）")
    p.add_argument("--checks", default=",".join(ALL_CHECKS),
                   help="要執行的檢查，逗號分隔：%s（預設全部）" % ",".join(ALL_CHECKS))
    p.add_argument("--max-depth", type=int, default=None, metavar="N", help="資料夾最大深度（快速掃描預設 8）")
    p.add_argument("--hash-db", action="append", default=[], metavar="檔案",
                   help="額外的惡意雜湊清單（每行一個 MD5/SHA1/SHA256）；data/hashes/ 內的檔案會自動載入")
    p.add_argument("--ip-db", action="append", default=[], metavar="檔案", help="惡意 IP 清單（每行一個）")
    p.add_argument("--allowlist", default=os.path.join(TOOL_DIR, "allowlist.txt"), metavar="檔案",
                   help="白名單檔（預設 allowlist.txt）")
    p.add_argument("--min-severity", default="low", metavar="等級",
                   help="只顯示此等級以上：info / low / medium / high / critical（預設 low）")
    p.add_argument("--json", metavar="檔案", help="另存 JSON 報告")
    p.add_argument("--html", metavar="檔案", help="另存 HTML 報告（建議）")
    p.add_argument("--no-signatures", action="store_true", help="不檢查 Windows 數位簽章（較快，但誤報較多）")
    p.add_argument("--no-color", action="store_true", help="終端機不使用顏色")
    p.add_argument("-v", "--verbose", action="store_true", help="顯示指令列、命中片段、雜湊等細節")
    p.add_argument("-q", "--quiet", action="store_true", help="不顯示進度")
    p.add_argument("--update-hashes", action="store_true",
                   help="從 MalwareBazaar 下載近 48 小時的惡意程式 SHA256 清單到 data/hashes/ 後結束")
    p.add_argument("--version", action="version", version="secscan " + __version__)
    return p


def update_hashes(url: str = MALWAREBAZAAR_RECENT) -> int:
    os.makedirs(HASH_DIR, exist_ok=True)
    dest = os.path.join(HASH_DIR, "malwarebazaar-recent.txt")
    print("下載 %s …" % url)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "secscan/" + __version__})
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        print("下載失敗：%s" % exc, file=sys.stderr)
        print("可以手動下載任何雜湊清單（每行一個雜湊），放到 %s" % HASH_DIR, file=sys.stderr)
        return 1
    hashes = load_hashes_from_text(body)
    if not hashes:
        print("下載的內容裡沒有找到雜湊值，未更新。", file=sys.stderr)
        return 1
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write("# MalwareBazaar 近期惡意程式 SHA256（%s 下載）\n" % datetime.now().strftime("%Y-%m-%d %H:%M"))
        fh.write("\n".join(sorted(hashes)) + "\n")
    print("已儲存 %d 筆雜湊到 %s" % (len(hashes), dest))
    return 0


def load_hashes_from_text(text: str) -> List[str]:
    import re
    return sorted({m.lower() for m in re.findall(r"\b[0-9a-fA-F]{64}\b", text)})


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    if args.update_hashes:
        return update_hashes()

    try:
        min_sev = Severity.parse(args.min_severity)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    checks = [c.strip() for c in args.checks.split(",") if c.strip()]
    unknown = [c for c in checks if c not in ALL_CHECKS]
    if unknown:
        print("未知的檢查項目：%s（可用：%s）" % (", ".join(unknown), ", ".join(ALL_CHECKS)), file=sys.stderr)
        return 2

    color = not args.no_color and enable_ansi()

    def log(msg: str) -> None:
        if args.quiet:
            return
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K" + msg[:110] if color else "\r" + msg[:110].ljust(110))
            sys.stderr.flush()
        else:
            print(msg, file=sys.stderr)

    hashes = load_hashes(default_hash_files() + args.hash_db)
    ctx = ScanContext(
        hashes=hashes,
        bad_ips=load_ips(args.ip_db),
        allowlist=Allowlist(args.allowlist),
        check_signatures=not args.no_signatures,
        log=log,
    )
    admin = is_admin()
    started = datetime.now()
    if not args.quiet:
        print("secscan %s — 開始掃描（%s）%s" % (__version__, "、".join(CHECK_LABELS[c] for c in checks),
                                            "" if admin else "　※ 非系統管理員，部分項目會略過"), file=sys.stderr)

    roots: List[str] = []
    if "files" in checks:
        if args.path:
            roots = [p for p in args.path if os.path.isdir(p)]
            for p in args.path:
                if not os.path.isdir(p):
                    print("找不到資料夾：%s" % p, file=sys.stderr)
        elif args.full:
            roots = files.full_scan_roots()
        else:
            roots = files.quick_scan_roots()
    max_depth = args.max_depth if args.max_depth is not None else (None if (args.full or args.path) else 8)

    findings = []
    runners = {
        "protection": lambda: protection.run(ctx),
        "persistence": lambda: persistence.run(ctx),
        "processes": lambda: processes.run(ctx),
        "network": lambda: network.run(ctx),
        "files": lambda: files.run(ctx, roots, max_depth),
    }
    for name in ALL_CHECKS:
        if name not in checks:
            continue
        log("檢查%s…" % CHECK_LABELS[name])
        try:
            findings += runners[name]()
        except KeyboardInterrupt:
            ctx.note("掃描被使用者中斷，結果不完整")
            break
        except Exception as exc:
            ctx.note("「%s」檢查發生錯誤：%s: %s" % (CHECK_LABELS[name], type(exc).__name__, exc))
    if not args.quiet and sys.stderr.isatty():
        sys.stderr.write("\r" + " " * 110 + "\r")

    hidden = [f for f in findings if ctx.allowlist.matches(f)]
    findings = [f for f in findings if not ctx.allowlist.matches(f)]
    if hidden:
        ctx.note("有 %d 個項目因符合白名單而未列出" % len(hidden))
    shown = [f for f in findings if f.severity >= min_sev]

    finished = datetime.now()
    meta = collect_meta(started, finished, checks, roots, ctx.notes, admin, len(hashes))

    for path, content in ((args.json, lambda: to_json(findings, meta)), (args.html, lambda: to_html(findings, meta))):
        if not path:
            continue
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content())
        except OSError as exc:
            print("無法寫入報告 %s：%s" % (path, exc), file=sys.stderr)
    print_console(shown, meta, color, args.verbose)
    for path in (args.json, args.html):
        if path and os.path.isfile(path):
            print("報告：%s" % os.path.abspath(path))

    worst = max((f.severity for f in findings), default=Severity.INFO)
    return 3 if worst >= Severity.HIGH else 0
