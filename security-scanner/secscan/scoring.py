"""共用的評分邏輯：檔案位置、指令列、偽裝成系統程式、數位簽章。"""
from __future__ import annotations

import ntpath
import os
import re
import shutil
from typing import List, Optional, Tuple

from .model import Scored
from .rules import match_rules
from .util import IS_LINUX, IS_MAC, IS_WINDOWS, expand_env, is_under, levenshtein, norm


# ---------------------------------------------------------------- 檔案位置

class Locations:
    """「正常程式不太會放在這裡」的資料夾，以及各自的權重。"""

    def __init__(self) -> None:
        self.risky: List[Tuple[str, int, str]] = []   # (norm 前綴, 權重, 中文名稱)
        self.system: List[str] = []                   # 系統資料夾（不查簽章、不加分）
        env = os.environ.get
        home = os.path.expanduser("~")

        def add(path: Optional[str], weight: int, label: str) -> None:
            if path:
                self.risky.append((norm(path), weight, label))

        if IS_WINDOWS:
            windir = env("WINDIR") or env("SystemRoot") or r"C:\Windows"
            sysdrive = (env("SystemDrive") or "C:") + "\\"
            appdata = env("APPDATA")
            local = env("LOCALAPPDATA")
            add(env("TEMP"), 2, "暫存資料夾")
            add(env("TMP"), 2, "暫存資料夾")
            add(local and os.path.join(local, "Temp"), 2, "暫存資料夾")
            add(os.path.join(windir, "Temp"), 2, "Windows 暫存資料夾")
            add(os.path.join(home, "Downloads"), 1, "下載資料夾")
            add(appdata, 1, "AppData\\Roaming")
            add(env("PUBLIC") or os.path.join(sysdrive, "Users", "Public"), 2, "公用使用者資料夾")
            add(env("ProgramData") or os.path.join(sysdrive, "ProgramData"), 1, "ProgramData")
            add(os.path.join(sysdrive, "$Recycle.Bin"), 3, "資源回收筒")
            add(os.path.join(sysdrive, "PerfLogs"), 2, "PerfLogs")
            add(os.path.join(windir, "Tasks"), 2, "Windows\\Tasks")
            add(os.path.join(windir, "Fonts"), 3, "字型資料夾")
            for folder in startup_folders():
                add(folder, 2, "啟動資料夾")
            self.system = [norm(windir)]
        else:
            add("/tmp", 2, "暫存資料夾 /tmp")
            add("/var/tmp", 2, "暫存資料夾 /var/tmp")
            add("/dev/shm", 3, "記憶體檔案系統 /dev/shm")
            add(os.path.join(home, "Downloads"), 1, "下載資料夾")
            if IS_MAC:
                add("/private/tmp", 2, "暫存資料夾 /tmp")
                add("/Users/Shared", 2, "/Users/Shared")
                add(os.path.join(home, ".Trash"), 2, "垃圾桶")
            if IS_LINUX:
                add("/run/user", 1, "/run/user")
            self.system = [norm(p) for p in ("/usr/bin", "/usr/sbin", "/bin", "/sbin", "/usr/lib", "/lib", "/usr/libexec", "/System")]
        # 最長前綴優先
        self.risky.sort(key=lambda t: len(t[0]), reverse=True)

    def classify(self, path: str) -> Tuple[int, str]:
        # 只看絕對路徑：「powershell」這種裸名稱若用目前資料夾去補，
        # 從下載資料夾執行掃描時會把所有指令都誤判成在下載資料夾
        if not path or not os.path.isabs(path):
            return 0, ""
        p = norm(path)
        for prefix, weight, label in self.risky:
            if is_under(p, prefix):
                return weight, label
        return 0, ""

    def is_system(self, path: str) -> bool:
        """位於系統資料夾、且不在其中的高風險子資料夾（例如 C:\\Windows\\Temp）。"""
        p = norm(path)
        return any(is_under(p, s) for s in self.system) and not self.classify(path)[0]


def startup_folders() -> List[str]:
    if not IS_WINDOWS:
        return []
    out = []
    appdata = os.environ.get("APPDATA")
    progdata = os.environ.get("ProgramData")
    if appdata:
        out.append(os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup"))
    if progdata:
        out.append(os.path.join(progdata, r"Microsoft\Windows\Start Menu\Programs\StartUp"))
    return out


# ---------------------------------------------------------------- 指令列

_EXEC_EXT = r"(?:exe|com|bat|cmd|vbs|vbe|js|jse|wsf|hta|ps1|scr|dll|cpl|msi|py|sh|jar|lnk|ocx)"
_FIRST_EXE = re.compile(r"(.+?\." + _EXEC_EXT + r")(?=[\s,\"']|$)", re.IGNORECASE)
_WIN_PATH = re.compile(r"[A-Za-z]:\\[^\"'<>|\r\n*?]+?\." + _EXEC_EXT + r"\b", re.IGNORECASE)
_UNC_PATH = re.compile(r"\\\\[^\\\s\"']+\\[^\"'<>|\r\n*?]+?\." + _EXEC_EXT + r"\b", re.IGNORECASE)
_NIX_PATH = re.compile(r"(?<![\w.])/(?:tmp|var/tmp|dev/shm|home|Users|root|opt|private/tmp|run/user)/[^\s\"';|&<>]+")


def extract_executable(cmd: str) -> Optional[str]:
    """從指令列取出主要執行檔路徑。"""
    cmd = expand_env((cmd or "").strip())
    if not cmd:
        return None
    if cmd[0] in "\"'":
        end = cmd.find(cmd[0], 1)
        return cmd[1:end] if end > 0 else cmd[1:]
    m = _FIRST_EXE.match(cmd)
    if m:
        return m.group(1).strip()
    return cmd.split()[0]


def referenced_paths(cmd: str) -> List[str]:
    """指令列中提到的所有檔案路徑（包含參數裡的 DLL、腳本）。"""
    cmd = expand_env(cmd or "")
    found = []
    exe = extract_executable(cmd)
    if exe:
        found.append(exe)
    found += [m.group(0) for m in _WIN_PATH.finditer(cmd)]
    found += [m.group(0) for m in _UNC_PATH.finditer(cmd)]
    if not IS_WINDOWS:
        found += [m.group(0) for m in _NIX_PATH.finditer(cmd)]
    return list(dict.fromkeys(p.strip() for p in found if p.strip()))


def resolve_executable(exe: Optional[str]) -> Optional[str]:
    """沒有目錄的執行檔名（例如 rundll32.exe）用 PATH 找出完整路徑。"""
    if not exe:
        return None
    if os.path.isabs(exe) or ntpath.isabs(exe):
        return exe
    return shutil.which(exe) or exe


_SCRIPT_SUFFIX = (".sh", ".bash", ".py", ".pl", ".rb", ".php", ".js", ".elf", ".bin", ".run", ".appimage")


def _looks_runnable(path: str) -> bool:
    """參數裡的路徑是否像是會被執行的檔案。Windows 路徑在擷取時已限定執行檔副檔名。"""
    if IS_WINDOWS or re.match(r"[A-Za-z]:\\|\\\\", path):
        return True
    if path.lower().endswith(_SCRIPT_SUFFIX):
        return True
    return os.path.isfile(path) and os.access(path, os.X_OK)


_SCRIPT_HOSTS = {"wscript.exe", "cscript.exe", "mshta.exe", "rundll32.exe", "regsvr32.exe"}


def score_command(cmd: str, locations: Locations) -> Scored:
    """評估一條指令列（開機項目、排程、服務、程序）。"""
    s = Scored()
    if not cmd:
        return s
    for rule, snippet in match_rules(cmd):
        s.add(rule.weight, rule.desc, snippet, rule.id)

    best_w, best_label, best_path = 0, "", ""
    main_exe = extract_executable(cmd) or ""
    for path in referenced_paths(cmd):
        if path != main_exe and not _looks_runnable(path):
            continue  # 參數裡提到的一般資料檔（例如輸出到 /tmp 的紀錄檔）不算
        w, label = locations.classify(path)
        if w > best_w:
            best_w, best_label, best_path = w, label, path
    if best_w:
        s.add(best_w, "執行的檔案位於%s：%s" % (best_label, best_path))
        exe = extract_executable(cmd) or ""
        host = ntpath.basename(exe).lower()
        if host in _SCRIPT_HOSTS and norm(best_path) != norm(exe):
            s.add(1, "透過系統程式 %s 執行可疑位置的檔案" % host)
    return s


# ---------------------------------------------------------------- 偽裝成系統程式

def _win_dirs(*subs: str) -> List[str]:
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
    return [ntpath.normcase(ntpath.join(windir, s)) if s else ntpath.normcase(windir) for s in subs]


# 系統程式名稱 → 合法的所在資料夾（相對 %WINDIR%）
SYSTEM_BINARIES = {
    "svchost.exe": ("System32", "SysWOW64"),
    "lsass.exe": ("System32",),
    "csrss.exe": ("System32",),
    "smss.exe": ("System32",),
    "wininit.exe": ("System32",),
    "winlogon.exe": ("System32",),
    "services.exe": ("System32",),
    "spoolsv.exe": ("System32",),
    "taskhostw.exe": ("System32",),
    "dllhost.exe": ("System32", "SysWOW64"),
    "conhost.exe": ("System32",),
    "rundll32.exe": ("System32", "SysWOW64"),
    "explorer.exe": ("", "SysWOW64"),
    "dwm.exe": ("System32",),
    "sihost.exe": ("System32",),
    "ctfmon.exe": ("System32", "SysWOW64"),
    "runtimebroker.exe": ("System32",),
    "searchindexer.exe": ("System32",),
    "lsm.exe": ("System32",),
    "wmiprvse.exe": (r"System32\wbem", r"SysWOW64\wbem"),
    "audiodg.exe": ("System32",),
    "fontdrvhost.exe": ("System32",),
}
# 名字很像系統程式、但本身是合法程式的
_TYPO_LEGIT = {"iexplore.exe", "services.msc", "svchost.mui"}


def check_masquerade(name: str, exe_path: Optional[str]) -> Scored:
    """偵測冒用系統程式名稱（放錯資料夾）或故意拼錯（svch0st.exe）。"""
    s = Scored()
    lname = (name or "").lower()
    if not lname:
        return s
    if lname in SYSTEM_BINARIES and exe_path:
        allowed = _win_dirs(*SYSTEM_BINARIES[lname])
        parent = ntpath.normcase(ntpath.dirname(exe_path))
        if parent not in allowed:
            s.add(6, "使用系統程式名稱 %s，但不在系統資料夾（偽裝成系統程式）" % name)
        return s
    if lname in _TYPO_LEGIT or len(lname) < 9:
        return s
    deobf = lname.replace("0", "o").replace("1", "l")
    for real in SYSTEM_BINARIES:
        if len(real) < 9:
            continue
        if deobf == real or levenshtein(lname, real) == 1:
            s.add(5, "程式名稱 %s 與系統程式 %s 只差一點（故意拼錯的偽裝手法）" % (name, real))
            break
    return s


# ---------------------------------------------------------------- 數位簽章

SIGNABLE_EXT = {".exe", ".dll", ".sys", ".scr", ".cpl", ".ocx", ".msi", ".ps1", ".psm1", ".com"}


def signature_score(info: Optional[dict]) -> Scored:
    from .util import short_signer
    s = Scored()
    if not info:
        return s
    status = info.get("status", "")
    if status == "Valid":
        s.add(-2, "")
        s.note("有效數位簽章：%s" % (short_signer(info.get("signer", "")) or "（未知發行者）"))
    elif status == "NotSigned":
        s.add(1, "沒有數位簽章")
    elif status == "HashMismatch":
        s.add(4, "數位簽章與檔案內容不符（檔案可能被竄改）")
    elif status == "NotTrusted":
        s.add(2, "數位簽章的憑證不受信任")
    return s
