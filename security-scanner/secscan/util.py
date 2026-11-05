"""跨平台小工具：執行外部指令、PowerShell、雜湊、熵值、路徑處理。"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Optional

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# Windows 下隱藏子程序的主控台視窗
_CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def is_admin() -> bool:
    try:
        if IS_WINDOWS:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return False


def norm(path: str) -> str:
    """正規化路徑，供比對用（Windows 不分大小寫）。"""
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))
    except Exception:
        return path


def is_under(path: str, prefix: str) -> bool:
    """path 是否等於 prefix 或位於其下（兩者都需先 norm）。"""
    if not prefix:
        return False
    if path == prefix:
        return True
    p = prefix if prefix.endswith(os.sep) else prefix + os.sep
    return path.startswith(p)


_PCT_VAR = re.compile(r"%([^%\s]+)%")


def expand_env(text: str) -> str:
    """展開 %VAR%（Windows 風格）、$VAR 與 ~。"""
    if not text:
        return text

    def repl(m: "re.Match[str]") -> str:
        return os.environ.get(m.group(1), os.environ.get(m.group(1).upper(), m.group(0)))

    text = _PCT_VAR.sub(repl, text)
    text = os.path.expandvars(text)
    if text.startswith("~"):
        text = os.path.expanduser(text)
    return text


def run(cmd: List[str], timeout: int = 60) -> Optional[str]:
    """執行指令並回傳 stdout；失敗回傳 None。"""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = proc.stdout
    for enc in ("utf-8", "mbcs" if IS_WINDOWS else "latin-1"):
        try:
            return out.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return out.decode("utf-8", "replace")


def powershell_exe() -> Optional[str]:
    if not IS_WINDOWS:
        return None
    # Windows PowerShell 5.1 一定有，且內建 Defender / ScheduledTasks 模組
    for name in ("powershell.exe", "pwsh.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def powershell_json(script: str, timeout: int = 180) -> Any:
    """執行 PowerShell 腳本（腳本需自行 ConvertTo-Json），回傳解析後的 JSON；失敗回傳 None。"""
    exe = powershell_exe()
    if not exe:
        return None
    prelude = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$ProgressPreference='SilentlyContinue';"
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    )
    out = run(
        [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", prelude + script],
        timeout=timeout,
    )
    if not out:
        return None
    out = out.strip().lstrip("﻿")
    if not out:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def as_list(value: Any) -> List[Any]:
    """PowerShell 只有一筆資料時 ConvertTo-Json 會輸出物件而不是陣列。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [data.count(i) for i in range(256)]  # bytes.count 在 C 裡跑，比逐位元組迴圈快很多
    total = len(data)
    ent = 0.0
    for c in counts:
        if c:
            p = c / total
            ent -= p * math.log2(p)
    return ent


def file_hashes(path: str, chunk: int = 1 << 20) -> Dict[str, str]:
    md5, sha1, sha256 = hashlib.md5(), hashlib.sha1(), hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            md5.update(block)
            sha1.update(block)
            sha256.update(block)
    return {"md5": md5.hexdigest(), "sha1": sha1.hexdigest(), "sha256": sha256.hexdigest()}


def read_head(path: str, size: int) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(size)
    except OSError:
        return b""


def decode_text(data: bytes) -> str:
    """盡量把腳本內容解成文字（UTF-8 / UTF-16 / 退回 latin-1）。"""
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    # 沒有 BOM 的 UTF-16LE：每隔一個位元組是 0
    if len(data) > 8 and data[1:40:2].count(0) > 15:
        try:
            return data.decode("utf-16-le", "replace")
        except Exception:
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


_ASCII_RUN = re.compile(rb"[\x20-\x7e]{4,}")
_UTF16_RUN = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")


def extract_strings(data: bytes) -> str:
    """從二進位檔（例如 .lnk 捷徑）抽出可讀字串，類似 strings 指令。"""
    parts = [m.group().decode("ascii") for m in _ASCII_RUN.finditer(data)]
    parts += [m.group().decode("utf-16-le") for m in _UTF16_RUN.finditer(data)]
    return "\n".join(parts)


def authenticode(paths: Iterable[str], timeout: int = 600) -> Dict[str, Dict[str, str]]:
    """批次查詢 Windows 數位簽章。回傳 {norm(path): {"status":…, "signer":…}}。"""
    paths = [p for p in dict.fromkeys(paths) if p]
    if not IS_WINDOWS or not paths:
        return {}
    fd, listfile = tempfile.mkstemp(suffix=".txt", prefix="secscan-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(paths))
        script = (
            "Get-Content -LiteralPath '%s' -Encoding UTF8 | Where-Object { $_ } | ForEach-Object {"
            " $s = Get-AuthenticodeSignature -LiteralPath $_;"
            " $sub = ''; if ($s.SignerCertificate) { $sub = $s.SignerCertificate.Subject };"
            " [pscustomobject]@{ Path = $_; Status = [string]$s.Status; Signer = $sub } }"
            " | ConvertTo-Json -Compress" % listfile.replace("'", "''")
        )
        data = powershell_json(script, timeout=timeout)
    finally:
        try:
            os.remove(listfile)
        except OSError:
            pass
    result: Dict[str, Dict[str, str]] = {}
    for item in as_list(data):
        if isinstance(item, dict) and item.get("Path"):
            result[norm(item["Path"])] = {
                "status": item.get("Status") or "",
                "signer": item.get("Signer") or "",
            }
    return result


def short_signer(subject: str) -> str:
    m = re.search(r"CN=(\"[^\"]+\"|[^,]+)", subject or "")
    return m.group(1).strip('"') if m else (subject or "")


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def enable_ansi() -> bool:
    """讓 Windows 10+ 的主控台支援 ANSI 顏色。"""
    if not sys.stdout.isatty():
        return False
    if IS_WINDOWS:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
                return True
            return False
        except Exception:
            return False
    return True
