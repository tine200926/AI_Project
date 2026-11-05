"""檔案掃描：病毒碼雜湊、偽裝副檔名、可疑位置的執行檔、惡意腳本內容。"""
from __future__ import annotations

import os
import re
import stat
import time
import zipfile
from typing import Iterator, List, Optional, Tuple

from ..context import ScanContext, make_finding
from ..model import Finding, Scored, Severity
from ..rules import contains_eicar, match_rules
from ..scoring import check_masquerade, startup_folders
from ..util import IS_LINUX, IS_MAC, IS_WINDOWS, decode_text, extract_strings, file_hashes, read_head, shannon_entropy

EXEC_EXT = {".exe", ".dll", ".scr", ".com", ".pif", ".cpl", ".sys", ".ocx", ".msi", ".jar", ".appx", ".msix"}
SCRIPT_EXT = {".ps1", ".psm1", ".psd1", ".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
              ".hta", ".sh", ".bash", ".py", ".pyw", ".reg", ".scf", ".inf", ".url", ".command", ".applescript"}
SHORTCUT_EXT = {".lnk"}
SOURCE_EXT = {".py", ".pyw", ".js", ".rb", ".pl", ".php", ".ts", ".java", ".go", ".cs"}
# 放在可疑位置就值得注意的腳本類型（Windows Script Host／批次檔），一般的 .py / .sh 不算
LOCATION_SCRIPT_EXT = {".ps1", ".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".scf", ".lnk"}
MACRO_EXT = {".docm", ".xlsm", ".pptm", ".dotm", ".xltm", ".xlam", ".ppam", ".potm"}
OLE_EXT = {".doc", ".xls", ".ppt"}
ARCHIVE_EXT = {".zip"}
CONTAINER_EXT = {".iso", ".img", ".vhd", ".vhdx"}     # 常被用來繞過「來自網路」警告
NOTE_EXT = {".txt", ".html", ".htm", ".hta"}
# 正常情況下內容絕不會是執行檔的類型
DISGUISE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico", ".pdf", ".txt", ".doc", ".docx", ".xls",
                ".xlsx", ".ppt", ".pptx", ".mp3", ".mp4", ".avi", ".mkv", ".wav", ".log", ".csv", ".rtf", ".svg"}
SOFT_DISGUISE_EXT = {".tmp", ".dat", ".bin", ""}

_DOC_EXT = r"pdf|docx?|xlsx?|pptx?|jpe?g|png|gif|bmp|txt|rtf|mp3|mp4|avi|mov|zip|rar|7z|csv|html?"
_RUN_EXT = r"exe|scr|com|pif|bat|cmd|vbs|vbe|js|jse|wsf|hta|lnk|ps1|cpl|msi|jar"
DOUBLE_EXT = re.compile(r"\.(?:%s)[\s_]*\.(?:%s)$" % (_DOC_EXT, _RUN_EXT), re.IGNORECASE)
PADDED_EXT = re.compile(r"\s{4,}\.(?:%s)$" % _RUN_EXT, re.IGNORECASE)
RTLO = "\u202e"

SKIP_DIRS = {".git", ".svn", ".hg", "node_modules", "__pycache__", "winsxs", "$windows.~bt", "$windows.~ws",
             "cache", "cache_data", "code cache", "gpucache", "shadercache", "service worker", "indexeddb",
             "cache2", "webcache", "inetcache", "crashpad", "site-packages", ".npm", ".nuget", ".gradle", ".m2"}

# FILE_ATTRIBUTE_OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS
_CLOUD_ONLY = 0x1000 | 0x40000 | 0x400000

MAX_CONTENT_BYTES = 2 * 1024 * 1024
MAX_BINARY_READ = 1024 * 1024
MAX_DEEP_BYTES = 64 * 1024 * 1024
MAX_NOTE_BYTES = 256 * 1024
MAX_HASH_BYTES = 200 * 1024 * 1024


# ---------------------------------------------------------------- 掃描範圍

def quick_scan_roots() -> List[str]:
    home = os.path.expanduser("~")
    env = os.environ.get
    roots: List[Optional[str]] = []
    if IS_WINDOWS:
        windir = env("WINDIR") or r"C:\Windows"
        sysdrive = (env("SystemDrive") or "C:") + "\\"
        roots += [env("TEMP"), os.path.join(windir, "Temp"), os.path.join(home, "Downloads"),
                  os.path.join(home, "Desktop"), os.path.join(home, "Documents"), env("APPDATA"), env("LOCALAPPDATA"),
                  env("PUBLIC"), env("ProgramData"), os.path.join(sysdrive, "PerfLogs"),
                  os.path.join(sysdrive, "$Recycle.Bin")]
        roots += startup_folders()
    else:
        roots += ["/tmp", "/var/tmp", "/dev/shm", os.path.join(home, "Downloads"), os.path.join(home, "Desktop"),
                  os.path.join(home, ".local", "bin"), os.path.join(home, ".config", "autostart"),
                  os.path.join(home, ".config", "systemd")]
        if IS_MAC:
            roots += ["/Users/Shared", os.path.join(home, "Library", "LaunchAgents"),
                      "/Library/LaunchAgents", "/Library/LaunchDaemons"]
    out = []
    seen = set()
    for r in roots:
        if r and os.path.isdir(r):
            key = os.path.normcase(os.path.abspath(r))
            if key not in seen:
                seen.add(key)
                out.append(r)
    # 若某個根目錄已包含在另一個裡面就略過（例如 TEMP 在 LOCALAPPDATA 底下）
    result = []
    for r in out:
        nr = os.path.normcase(os.path.abspath(r))
        if not any(nr != os.path.normcase(os.path.abspath(o)) and nr.startswith(os.path.normcase(os.path.abspath(o)) + os.sep) for o in out):
            result.append(r)
    return result


def full_scan_roots() -> List[str]:
    if IS_WINDOWS:
        import string
        drives = []
        try:
            import ctypes
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            for i, letter in enumerate(string.ascii_uppercase):
                if mask & (1 << i):
                    root = letter + ":\\"
                    # 只掃本機固定磁碟（DRIVE_FIXED = 3）
                    if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:
                        drives.append(root)
        except Exception:
            drives = [(os.environ.get("SystemDrive") or "C:") + "\\"]
        return drives
    return ["/"]


def walk(roots: List[str], ctx: ScanContext, max_depth: Optional[int]) -> Iterator[Tuple[str, os.stat_result]]:
    seen_dirs = set()
    for root in roots:
        root = os.path.abspath(root)
        base_depth = root.rstrip(os.sep).count(os.sep)
        stack = [root]
        while stack:
            d = stack.pop()
            if ctx.is_self(d):
                continue
            try:
                st = os.stat(d)
                key = (st.st_dev, st.st_ino)
                if key in seen_dirs and st.st_ino:
                    continue
                seen_dirs.add(key)
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name.lower() in SKIP_DIRS:
                                continue
                            if not IS_WINDOWS and d == "/" and entry.name in ("proc", "sys", "dev", "run", "snap"):
                                continue
                            if max_depth is not None and entry.path.count(os.sep) - base_depth > max_depth:
                                continue
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            st = entry.stat(follow_symlinks=False)
                            # OneDrive 等雲端「僅線上」檔案：讀取會觸發下載，略過
                            if getattr(st, "st_file_attributes", 0) & _CLOUD_ONLY:
                                continue
                            yield entry.path, st
                    except OSError:
                        continue


# ---------------------------------------------------------------- 單一檔案分析

def _file_type(head: bytes) -> str:
    if head[:2] == b"MZ":
        return "pe"
    if head[:4] == b"\x7fELF":
        return "elf"
    if head[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
        return "macho"
    if head[:2] == b"#!":
        return "script"
    if head[:4] == b"PK\x03\x04":
        return "zip"
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "ole"
    return ""


def _is_hidden(path: str, st: os.stat_result) -> bool:
    if IS_WINDOWS:
        return bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_HIDDEN)
    return os.path.basename(path).startswith(".")


def _inspect_zip(path: str, s: Scored) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()[:2000]
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError, NotImplementedError):
        return
    names = [i.filename for i in infos]
    lower = [n.lower() for n in names]
    if any(n.endswith("vbaproject.bin") for n in lower):
        s.add(2, "Office 文件內含 VBA 巨集")
    runnable = [n for n in names if os.path.splitext(n.lower())[1] in EXEC_EXT | SCRIPT_EXT | SHORTCUT_EXT]
    encrypted = any(i.flag_bits & 0x1 for i in infos)
    doubled = [n for n in names if DOUBLE_EXT.search(n)]
    if doubled:
        s.add(4, "壓縮檔內有雙重副檔名的檔案：%s" % doubled[0])
    if runnable and encrypted:
        s.add(2, "有密碼保護的壓縮檔內含可執行檔（常用來躲避防毒掃描）")
    if runnable and len(infos) <= 3:
        s.add(1, "壓縮檔只包含可執行檔／腳本：%s" % runnable[0])


def analyze_file(path: str, st: os.stat_result, ctx: ScanContext) -> Optional[Tuple[Scored, dict]]:
    """回傳 (評分, 細節)；不值得細看的檔案回傳 None。"""
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    size = st.st_size
    s = Scored()
    details = {"path": path, "size": size}

    interesting = ext in EXEC_EXT | SCRIPT_EXT | SHORTCUT_EXT | MACRO_EXT | OLE_EXT | ARCHIVE_EXT | CONTAINER_EXT
    is_note = ext in NOTE_EXT and size <= MAX_NOTE_BYTES
    check_magic = ext in DISGUISE_EXT or ext in SOFT_DISGUISE_EXT
    unix_exec = not IS_WINDOWS and bool(st.st_mode & 0o111)

    # 檔名本身的詭計（不需要讀檔）
    if RTLO in name:
        s.add(5, "檔名含有「從右到左」隱形字元，用來把 exe 偽裝成其他副檔名")
    if DOUBLE_EXT.search(name):
        s.add(4, "雙重副檔名（例如 發票.pdf.exe），偽裝成文件的執行檔")
    elif PADDED_EXT.search(name):
        s.add(3, "副檔名前塞了大量空白，企圖藏起真正的副檔名")

    if not (interesting or is_note or check_magic or unix_exec or s.score):
        return None
    if size == 0:
        return None

    head = read_head(path, 4096)
    if not head:
        return None
    ftype = _file_type(head)
    if contains_eicar(head):
        s.add(10, "EICAR 防毒測試檔（無害，但代表防毒軟體沒有攔下它）")
        s.force(Severity.CRITICAL)
        details["eicar"] = True

    is_binary_exec = ftype in ("pe", "elf", "macho")
    if is_binary_exec and ext in DISGUISE_EXT:
        s.add(5, "副檔名是 %s，但內容其實是執行檔（偽裝）" % ext)
    elif ftype == "pe" and ext in SOFT_DISGUISE_EXT:
        s.add(1, "沒有正常副檔名的 Windows 執行檔")
    elif not interesting and not is_note and not s.score and not (unix_exec and is_binary_exec) and not (unix_exec and ftype == "script"):
        return None

    loc_w, loc_label = ctx.locations.classify(path)
    runnable = is_binary_exec or ext in EXEC_EXT or ext in SCRIPT_EXT or ext in SHORTCUT_EXT or (unix_exec and ftype == "script")
    location_matters = is_binary_exec or ext in EXEC_EXT or ext in LOCATION_SCRIPT_EXT or (unix_exec and ftype == "script")
    if location_matters and loc_w:
        s.add(loc_w, "可執行檔位於%s" % loc_label)
        details["location"] = loc_label
    if runnable and _is_hidden(path, st):
        s.add(1, "隱藏檔案")
    if IS_LINUX and is_binary_exec and path.startswith("/dev/shm"):
        s.add(2, "執行檔放在記憶體檔案系統，重開機就消失（常見於入侵工具）")

    # 系統資料夾裡的大量正常執行檔不做內容分析（太慢且沒意義）
    if is_binary_exec and (loc_w or s.score or not ctx.locations.is_system(path)) and size <= MAX_DEEP_BYTES:
        masq = check_masquerade(name, path) if IS_WINDOWS else Scored()
        s.extend(masq)
        body = read_head(path, MAX_BINARY_READ) if size > len(head) else head
        ent = shannon_entropy(body)
        details["entropy"] = round(ent, 2)
        if ent > 7.4 and size > 64 * 1024:
            s.add(1, "內容熵值很高（%.2f），可能被加殼或加密" % ent)
        bin_weight = 0
        for rule, snippet in match_rules(extract_strings(body)):
            if rule.id in ("crypto-miner", "cred-dump", "amsi-bypass", "shadow-delete", "reverse-shell"):
                s.add(rule.weight, rule.desc, snippet, rule.id)
                bin_weight += rule.weight
        if bin_weight:
            details["_bin_weight"] = bin_weight

    if ext in SCRIPT_EXT or ftype == "script" or ext in SHORTCUT_EXT or ext == ".hta":
        data = read_head(path, MAX_CONTENT_BYTES)
        text = extract_strings(data) if ext in SHORTCUT_EXT else decode_text(data)
        hits = match_rules(text)
        # 程式庫原始碼（例如語法高亮器、安裝程式說明）常以字串形式提到這些指令；
        # 不在可疑位置的原始碼檔，只有一個非重大特徵時不計分
        if ext in SOURCE_EXT and not loc_w and len(hits) == 1 and hits[0][0].weight < 5:
            hits = []
        for rule, snippet in hits:
            s.add(rule.weight, rule.desc, snippet, rule.id)
        if ext in SHORTCUT_EXT and re.search(r"\b(?:powershell|pwsh|cmd|mshta|wscript|cscript|rundll32|regsvr32)(?:\.exe)?\b", text, re.I):
            if re.search(r"(?:^|\s)[-/](?:c|k|e[a-z]*|w(?:indowstyle)?|command)\b", text, re.I):
                s.add(2, "捷徑實際上是執行系統指令（捷徑木馬常見手法）")

    if is_note:
        data = read_head(path, MAX_NOTE_BYTES)
        for rule, snippet in match_rules(decode_text(data), scope="note"):
            s.add(rule.weight, rule.desc, snippet, rule.id)

    if ext in MACRO_EXT or (ext in OLE_EXT and ftype == "ole"):
        if ftype == "ole":
            data = read_head(path, MAX_CONTENT_BYTES)
            if "_VBA_PROJECT".encode("utf-16-le") in data or b"_VBA_PROJECT" in data:
                s.add(2, "Office 文件內含 VBA 巨集")
            if re.search(rb"(?i)auto_?open|document_?open|workbook_?open", data):
                s.add(2, "巨集會在開啟文件時自動執行")
        if loc_w:
            s.add(1, "位於%s" % loc_label)

    if ftype == "zip" or ext in ARCHIVE_EXT:
        before = s.score
        _inspect_zip(path, s)
        if s.score > before and loc_w and ext not in MACRO_EXT:
            s.add(1, "位於%s" % loc_label)
    if ext in CONTAINER_EXT and loc_w:
        s.add(1, "磁碟映像檔（%s）位於%s，常被用來夾帶惡意程式" % (ext, loc_label))

    # 病毒碼雜湊比對
    if ctx.hashes and size <= MAX_HASH_BYTES and (runnable or interesting or s.score):
        try:
            h = file_hashes(path)
            details.update(h)
            if h["sha256"] in ctx.hashes or h["md5"] in ctx.hashes or h["sha1"] in ctx.hashes:
                s.add(10, "檔案雜湊值符合已知惡意程式清單")
                s.force(Severity.CRITICAL)
        except OSError:
            pass

    details["type"] = ftype or ext.lstrip(".")
    return s, details


def _title(s: Scored, details: dict) -> str:
    if details.get("eicar"):
        return "EICAR 防毒測試檔"
    if s.forced == Severity.CRITICAL:
        return "已知惡意程式"
    rules = set(s.rule_ids)
    if "ransom-note" in rules:
        return "疑似勒索訊息"
    if rules & {"cred-dump", "amsi-bypass", "shadow-delete", "reverse-shell"}:
        return "含有攻擊工具特徵的檔案"
    if "crypto-miner" in rules:
        return "疑似挖礦程式"
    if rules:
        return "內容可疑的腳本"
    for r in s.reasons:
        if "偽裝" in r or "副檔名" in r or "從右到左" in r:
            return "偽裝的執行檔"
    if "巨集" in " ".join(s.reasons):
        return "含巨集的 Office 文件"
    return "可疑位置的執行檔"


def run(ctx: ScanContext, roots: List[str], max_depth: Optional[int], min_score: int = 2) -> List[Finding]:
    results: List[Tuple[Scored, dict]] = []
    count = 0
    started = time.time()
    last = started
    for path, st in walk(roots, ctx, max_depth):
        count += 1
        now = time.time()
        if now - last > 2:
            last = now
            ctx.log("已檢查 %d 個檔案… %s" % (count, path[:80]))
        try:
            res = analyze_file(path, st, ctx)
        except (OSError, MemoryError):
            continue
        if res and res[0].score >= 1:
            results.append(res)
    ctx.log("檔案掃描：共 %d 個檔案，耗時 %.0f 秒" % (count, time.time() - started))

    # Windows：替候選執行檔批次查數位簽章，有效簽章會降低分數
    if ctx.check_signatures and IS_WINDOWS:
        ctx.prefetch_signatures(d["path"] for s, d in results)
        for s, d in results:
            if s.forced is None:
                s.extend(ctx.signature(d["path"]))
                info = ctx.signature_info(d["path"])
                if info:
                    d["signature"] = info.get("status")
                    if info.get("status") == "Valid" and d.get("_bin_weight"):
                        # 有效簽章的程式（常見是資安軟體本身）內含攻擊工具字串，不當成惡意
                        s.score -= d["_bin_weight"]
                        s.note("上述字串出現在有效簽章的程式中，可能是資安軟體本身")

    findings = []
    for s, d in results:
        if s.score < min_score and s.forced is None:
            continue
        d.pop("_bin_weight", None)
        findings.append(make_finding("files", _title(s, d), d["path"], s, **d))
    return findings
