"""開機自動執行（持久化）項目：惡意程式最常藏身的地方。

Windows：登錄檔 Run 機碼、Winlogon、IFEO 偵錯器劫持、AppInit_DLLs、啟動資料夾、排程工作、服務、WMI 事件訂閱。
Linux：crontab、systemd、shell 啟動檔、/etc/ld.so.preload、autostart。
macOS：LaunchAgents / LaunchDaemons。
"""
from __future__ import annotations

import glob
import os
import plistlib
import re
from typing import Iterator, List, Optional, Tuple

from ..context import ScanContext, make_finding
from ..model import Finding, Scored
from ..rules import match_rules
from ..scoring import extract_executable, resolve_executable, score_command, startup_folders
from ..util import IS_MAC, IS_WINDOWS, as_list, decode_text, extract_strings, powershell_json, read_head
from ..util import run as run_cmd


class Entry:
    """一個開機自動執行項目。"""

    def __init__(self, kind: str, location: str, name: str, command: str, inventory: bool = True,
                 base: Optional[Scored] = None) -> None:
        self.kind = kind            # 例如「登錄檔 Run」「排程工作」
        self.location = location    # 機碼／檔案路徑
        self.name = name
        self.command = command
        self.inventory = inventory  # 沒有可疑跡象時，是否仍以「資訊」列出
        self.base = base or Scored()


# ================================================================ Windows

def _reg_values(hive, path: str, wow64: bool = False) -> Iterator[Tuple[str, object]]:
    import winreg
    flags = winreg.KEY_READ | (winreg.KEY_WOW64_32KEY if wow64 else winreg.KEY_WOW64_64KEY)
    try:
        with winreg.OpenKey(hive, path, 0, flags) as key:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                yield name, value
                i += 1
    except OSError:
        return


def _reg_subkeys(hive, path: str) -> List[str]:
    import winreg
    out = []
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(key, i))
                except OSError:
                    break
                i += 1
    except OSError:
        pass
    return out


def _reg_get(hive, path: str, name: str):
    for n, v in _reg_values(hive, path):
        if n.lower() == name.lower():
            return v
    return None


RUN_KEYS = [
    r"Software\Microsoft\Windows\CurrentVersion\Run",
    r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
    r"Software\Microsoft\Windows\CurrentVersion\RunOnceEx",
    r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run",
    r"Software\Microsoft\Windows NT\CurrentVersion\Windows\Run",
]


def windows_registry_entries(ctx: ScanContext) -> List[Entry]:
    import winreg
    entries: List[Entry] = []
    hives = [(winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")]
    for hive, hname in hives:
        for key in RUN_KEYS:
            for wow in ((False, True) if hname == "HKLM" else (False,)):
                for name, value in _reg_values(hive, key, wow64=wow):
                    if not isinstance(value, str) or not value.strip():
                        continue
                    loc = "%s\\%s%s" % (hname, key, " (32 位元)" if wow else "")
                    entries.append(Entry("登錄檔開機執行", loc, name or "(預設)", value))

    wl = r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon"
    shell = _reg_get(winreg.HKEY_LOCAL_MACHINE, wl, "Shell")
    if isinstance(shell, str) and shell.strip().lower() not in ("explorer.exe", ""):
        b = Scored()
        b.add(6, "Winlogon Shell 被改成 explorer.exe 以外的程式（登入就會執行）")
        entries.append(Entry("Winlogon", "HKLM\\" + wl, "Shell", shell, base=b))
    shell_cu = _reg_get(winreg.HKEY_CURRENT_USER, wl, "Shell")
    if isinstance(shell_cu, str) and shell_cu.strip():
        b = Scored()
        b.add(6, "目前使用者設定了自訂的 Winlogon Shell")
        entries.append(Entry("Winlogon", "HKCU\\" + wl, "Shell", shell_cu, base=b))
    userinit = _reg_get(winreg.HKEY_LOCAL_MACHINE, wl, "Userinit")
    if isinstance(userinit, str):
        parts = [p.strip().strip('"') for p in userinit.split(",") if p.strip()]
        extra = [p for p in parts if not p.lower().endswith("\\userinit.exe") and p.lower() != "userinit.exe"]
        if extra:
            b = Scored()
            b.add(6, "Winlogon Userinit 被附加了其他程式：%s" % ", ".join(extra))
            entries.append(Entry("Winlogon", "HKLM\\" + wl, "Userinit", userinit, base=b))

    ifeo = r"Software\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
    for sub in _reg_subkeys(winreg.HKEY_LOCAL_MACHINE, ifeo):
        dbg = _reg_get(winreg.HKEY_LOCAL_MACHINE, ifeo + "\\" + sub, "Debugger")
        if isinstance(dbg, str) and dbg.strip():
            b = Scored()
            if re.search(r"procexp|vsjitdebugger|windbg|devenv", dbg, re.I):
                b.add(1, "已為 %s 設定偵錯器（常見於開發／系統工具）" % sub)
            else:
                b.add(5, "IFEO 劫持：啟動 %s 時會改執行其他程式" % sub)
            entries.append(Entry("IFEO 偵錯器", "HKLM\\%s\\%s" % (ifeo, sub), "Debugger", dbg, base=b))
    spe = r"Software\Microsoft\Windows NT\CurrentVersion\SilentProcessExit"
    for sub in _reg_subkeys(winreg.HKEY_LOCAL_MACHINE, spe):
        mon = _reg_get(winreg.HKEY_LOCAL_MACHINE, spe + "\\" + sub, "MonitorProcess")
        if isinstance(mon, str) and mon.strip():
            b = Scored()
            b.add(4, "%s 結束時會觸發執行其他程式（SilentProcessExit 持久化）" % sub)
            entries.append(Entry("SilentProcessExit", "HKLM\\%s\\%s" % (spe, sub), "MonitorProcess", mon, base=b))

    win = r"Software\Microsoft\Windows NT\CurrentVersion\Windows"
    dlls = _reg_get(winreg.HKEY_LOCAL_MACHINE, win, "AppInit_DLLs")
    load = _reg_get(winreg.HKEY_LOCAL_MACHINE, win, "LoadAppInit_DLLs")
    if isinstance(dlls, str) and dlls.strip():
        b = Scored()
        b.add(5 if load == 1 else 2, "AppInit_DLLs：每個程式啟動時都會載入這些 DLL")
        entries.append(Entry("AppInit_DLLs", "HKLM\\" + win, "AppInit_DLLs", dlls, base=b))
    return entries


def _lnk_command(path: str) -> str:
    text = extract_strings(read_head(path, 64 * 1024))
    return " ".join(text.split("\n"))


def windows_startup_entries(ctx: ScanContext) -> List[Entry]:
    entries = []
    for folder in startup_folders():
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            path = os.path.join(folder, name)
            if not os.path.isfile(path) or name.lower() == "desktop.ini":
                continue
            ext = os.path.splitext(name)[1].lower()
            b = Scored()
            if ext == ".lnk":
                cmd = _lnk_command(path)
                # 捷徑抽出的字串很雜，只保留規則比對；位置交給下面的 score_command
                m = re.search(r"[A-Za-z]:\\[^\x00\n\"<>|]+?\.(?:exe|bat|cmd|ps1|vbs|js|hta|scr|com)\b", cmd, re.I)
                command = m.group(0) if m else ""
                for rule, snippet in match_rules(cmd):
                    b.add(rule.weight, rule.desc, snippet, rule.id)
                entries.append(Entry("啟動資料夾", folder, name, command or path, base=b))
            else:
                b.add(2, "啟動資料夾內直接放了 %s 檔（一般應該是捷徑）" % (ext or "無副檔名"))
                data = read_head(path, 512 * 1024)
                for rule, snippet in match_rules(decode_text(data)):
                    b.add(rule.weight, rule.desc, snippet, rule.id)
                entries.append(Entry("啟動資料夾", folder, name, path, base=b))
    return entries


def windows_scheduled_tasks(ctx: ScanContext) -> List[Entry]:
    script = (
        "Get-ScheduledTask | ForEach-Object { $t = $_; foreach ($a in $t.Actions) {"
        " if ($a.Execute) { [pscustomobject]@{ Path = ($t.TaskPath + $t.TaskName); Execute = $a.Execute;"
        " Arguments = $a.Arguments; Author = $t.Author; State = [string]$t.State;"
        " Hidden = [bool]$t.Settings.Hidden } } } } | ConvertTo-Json -Compress"
    )
    data = powershell_json(script)
    if data is None:
        ctx.note("無法讀取排程工作清單（PowerShell 執行失敗）")
        return []
    entries = []
    for t in as_list(data):
        if not isinstance(t, dict):
            continue
        path = t.get("Path") or ""
        exe = (t.get("Execute") or "").strip()
        if exe and " " in exe and not exe.startswith('"'):
            exe = '"%s"' % exe
        cmd = (exe + " " + (t.get("Arguments") or "")).strip()
        b = Scored()
        microsoft = path.lower().startswith("\\microsoft\\")
        if t.get("Hidden") and not microsoft:
            b.add(1, "排程工作被設為隱藏")
        if microsoft and t.get("Author") and not re.search(r"microsoft|\$\(@%systemroot%|^\$\(@", str(t.get("Author")), re.I):
            b.add(1, "放在 \\Microsoft\\ 底下，但作者不是 Microsoft")
        entries.append(Entry("排程工作", path, path.rsplit("\\", 1)[-1], cmd, inventory=not microsoft, base=b))
    return entries


def windows_services(ctx: ScanContext) -> List[Entry]:
    script = (
        "Get-CimInstance Win32_Service | Select-Object Name, DisplayName, PathName, StartMode, State"
        " | ConvertTo-Json -Compress"
    )
    data = powershell_json(script)
    if data is None:
        ctx.note("無法讀取服務清單（PowerShell 執行失敗）")
        return []
    entries = []
    windir = (os.environ.get("WINDIR") or r"C:\Windows").lower()
    for svc in as_list(data):
        if not isinstance(svc, dict):
            continue
        path = (svc.get("PathName") or "").strip()
        if not path:
            continue
        b = Scored()
        if not path.startswith('"'):
            m = re.match(r"(.+?\.exe)\b", path, re.I)
            exe = m.group(1) if m else path
            if " " in exe and not exe.lower().startswith(windir) and svc.get("StartMode") == "Auto":
                b.add(2, "服務路徑含空白但沒有加引號（可被用來提權）")
        name = svc.get("Name") or ""
        label = "%s（%s）" % (svc.get("DisplayName") or name, name)
        entries.append(Entry("系統服務", "HKLM\\SYSTEM\\CurrentControlSet\\Services\\" + name, label, path,
                             inventory=False, base=b))
    return entries


def windows_wmi(ctx: ScanContext) -> List[Entry]:
    script = (
        "Get-CimInstance -Namespace root\\subscription -ClassName __EventConsumer | ForEach-Object {"
        " [pscustomobject]@{ Class = $_.CimClass.CimClassName; Name = $_.Name;"
        " Command = $_.CommandLineTemplate; Script = $_.ScriptText } } | ConvertTo-Json -Compress"
    )
    data = powershell_json(script)
    entries = []
    for c in as_list(data):
        if not isinstance(c, dict):
            continue
        cls = c.get("Class") or ""
        if cls not in ("CommandLineEventConsumer", "ActiveScriptEventConsumer"):
            continue
        b = Scored()
        b.add(5, "WMI 事件訂閱會自動執行指令／腳本（無檔案持久化手法，正常軟體很少用）")
        cmd = c.get("Command") or c.get("Script") or ""
        entries.append(Entry("WMI 事件訂閱", "root\\subscription\\" + cls, c.get("Name") or "", cmd, base=b))
    return entries


# ================================================================ Linux / macOS

def _read_lines(path: str) -> List[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


_CRON_ENV = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*=")


def cron_command(line: str, system_format: bool) -> Optional[str]:
    """從 crontab 行取出指令（系統 crontab 多一個使用者欄位）。"""
    line = line.strip()
    if not line or line.startswith("#") or _CRON_ENV.match(line):
        return None
    if line.startswith("@"):
        parts = line.split(None, 2 if system_format else 1)
        return parts[-1] if len(parts) > (2 if system_format else 1) else None
    parts = line.split(None, 6 if system_format else 5)
    need = 7 if system_format else 6
    return parts[-1] if len(parts) >= need else None


def unix_entries(ctx: ScanContext) -> List[Entry]:
    entries: List[Entry] = []
    home = os.path.expanduser("~")

    # crontab
    sys_crons = ["/etc/crontab"] + sorted(glob.glob("/etc/cron.d/*"))
    for path in sys_crons:
        for line in _read_lines(path):
            cmd = cron_command(line, system_format=True)
            if cmd:
                entries.append(Entry("cron 排程", path, line.strip()[:60], cmd))
    user_crons = sorted(glob.glob("/var/spool/cron/*")) + sorted(glob.glob("/var/spool/cron/crontabs/*"))
    for path in user_crons:
        for line in _read_lines(path):
            cmd = cron_command(line, system_format=False)
            if cmd:
                entries.append(Entry("cron 排程", path, line.strip()[:60], cmd))
    if not user_crons:
        out = run_cmd(["crontab", "-l"], timeout=10)
        for line in (out or "").splitlines():
            cmd = cron_command(line, system_format=False)
            if cmd:
                entries.append(Entry("cron 排程", "crontab -l", line.strip()[:60], cmd))
    for d in ("/etc/cron.hourly", "/etc/cron.daily", "/etc/cron.weekly", "/etc/cron.monthly"):
        for path in sorted(glob.glob(d + "/*")):
            b = Scored()
            for rule, snippet in match_rules("\n".join(_read_lines(path))):
                b.add(rule.weight, rule.desc, snippet, rule.id)
            entries.append(Entry("cron 排程", d, os.path.basename(path), path, inventory=False, base=b))

    # systemd
    unit_dirs = ["/etc/systemd/system", os.path.join(home, ".config/systemd/user"), "/etc/systemd/user"]
    for d in unit_dirs:
        for path in sorted(glob.glob(d + "/*.service") + glob.glob(d + "/*/*.service")):
            if os.path.islink(path):
                continue  # 多半是指向 /usr/lib 的套件單元
            for line in _read_lines(path):
                m = re.match(r"\s*Exec(?:Start|StartPre|StartPost|Reload|Stop)\s*=\s*[-@:+!]*(.+)", line)
                if m:
                    entries.append(Entry("systemd 服務", path, os.path.basename(path), m.group(1).strip()))

    # shell 啟動檔
    rc_files = [os.path.join(home, f) for f in (".bashrc", ".bash_profile", ".profile", ".zshrc", ".zprofile", ".bash_login", ".bash_logout")]
    rc_files += ["/etc/profile", "/etc/bash.bashrc", "/etc/zshrc", "/etc/rc.local"] + sorted(glob.glob("/etc/profile.d/*.sh"))
    for path in rc_files:
        text = "\n".join(_read_lines(path))
        if not text:
            continue
        b = Scored()
        for rule, snippet in match_rules(text):
            b.add(rule.weight, rule.desc, snippet, rule.id)
        for m in re.finditer(r"^\s*alias\s+(sudo|ls|ps|ssh|su|passwd|netstat|top|kill)=([^\n]+)", text, re.M):
            if re.search(r"/tmp/|/dev/shm/|curl|wget|base64|\bnc\b", m.group(2)):
                b.add(4, "替系統指令 %s 設了可疑的 alias" % m.group(1), m.group(0).strip())
        if b.score:
            entries.append(Entry("Shell 啟動檔", path, os.path.basename(path), "", inventory=False, base=b))

    # ld.so.preload：每個程式都會被注入
    preload = [l.strip() for l in _read_lines("/etc/ld.so.preload") if l.strip() and not l.strip().startswith("#")]
    for lib in preload:
        b = Scored()
        b.add(6, "/etc/ld.so.preload 會把這個函式庫注入所有程式（Rootkit 常用手法）")
        entries.append(Entry("ld.so.preload", "/etc/ld.so.preload", lib, lib, base=b))

    # XDG autostart
    for d in (os.path.join(home, ".config/autostart"), "/etc/xdg/autostart"):
        for path in sorted(glob.glob(d + "/*.desktop")):
            for line in _read_lines(path):
                if line.startswith("Exec="):
                    entries.append(Entry("桌面自動啟動", path, os.path.basename(path), line[5:].strip(),
                                         inventory=d.startswith(home)))

    # SSH 授權金鑰（攻擊者常加自己的金鑰當後門）
    for path in [os.path.join(home, ".ssh/authorized_keys"), "/root/.ssh/authorized_keys"]:
        for line in _read_lines(path):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            b = Scored()
            if re.match(r"(?:command|environment)=", line):
                b.add(2, "SSH 金鑰附帶強制執行指令")
            comment = line.split()[-1] if len(line.split()) >= 3 else ""
            entries.append(Entry("SSH 授權金鑰", path, comment or "(無註解)", line[:80] + "…", base=b))

    # macOS LaunchAgents / LaunchDaemons
    if IS_MAC:
        for d in (os.path.join(home, "Library/LaunchAgents"), "/Library/LaunchAgents", "/Library/LaunchDaemons"):
            for path in sorted(glob.glob(d + "/*.plist")):
                try:
                    with open(path, "rb") as fh:
                        pl = plistlib.load(fh)
                except Exception:
                    continue
                args = pl.get("ProgramArguments") or ([pl["Program"]] if pl.get("Program") else [])
                cmd = " ".join(str(a) for a in args)
                label = str(pl.get("Label") or os.path.basename(path))
                b = Scored()
                if label.startswith("com.apple.") and not path.startswith("/System"):
                    b.add(3, "使用 com.apple. 名稱但不在系統資料夾（偽裝成 Apple 元件）")
                entries.append(Entry("LaunchAgent/Daemon", path, label, cmd, base=b))
    return entries


# ================================================================ 主流程

def collect_entries(ctx: ScanContext) -> List[Entry]:
    if IS_WINDOWS:
        entries = []
        for fn in (windows_registry_entries, windows_startup_entries, windows_scheduled_tasks, windows_services, windows_wmi):
            ctx.log("讀取%s…" % {
                "windows_registry_entries": "登錄檔開機項目",
                "windows_startup_entries": "啟動資料夾",
                "windows_scheduled_tasks": "排程工作",
                "windows_services": "系統服務",
                "windows_wmi": "WMI 事件訂閱",
            }[fn.__name__])
            try:
                entries += fn(ctx)
            except Exception as exc:  # 單一來源失敗不要中斷整個掃描
                ctx.note("讀取 %s 失敗：%s" % (fn.__name__, exc))
        return entries
    return unix_entries(ctx)


def run(ctx: ScanContext) -> List[Finding]:
    entries = collect_entries(ctx)
    exes = {}
    for e in entries:
        exe = resolve_executable(extract_executable(e.command)) if e.command else None
        if exe:
            exes[id(e)] = exe
    ctx.prefetch_signatures(exes.values())

    findings: List[Finding] = []
    for e in entries:
        s = Scored()
        s.extend(e.base)
        if e.command:
            s.extend(score_command(e.command, ctx.locations))
        exe = exes.get(id(e))
        if exe and s.score >= 1 and not ctx.locations.is_system(exe):
            s.extend(ctx.signature(exe))
        if exe and not os.path.exists(exe) and ("\\" in exe or "/" in exe) and s.score:
            s.note("指向的檔案已不存在：%s" % exe)
        if s.score < 2 and not s.forced:
            if not e.inventory:
                continue
            s.score = max(s.score, 0)
        title = "%s：%s" % (e.kind, e.name) if e.name else e.kind
        target = e.location if not e.name else "%s → %s" % (e.location, e.name)
        f = make_finding("persistence", title, target, s, command=e.command, executable=exe, kind=e.kind)
        findings.append(f)
    return findings
