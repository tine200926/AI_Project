"""執行中的程序：可疑位置、偽裝成系統程式、惡意指令列、Office 啟動指令殼。"""
from __future__ import annotations

import os
import re
from typing import List

from ..context import ScanContext, make_finding
from ..model import Finding, Scored
from ..scoring import check_masquerade, score_command
from ..util import IS_LINUX, IS_WINDOWS, as_list, powershell_json
from ..util import run as run_cmd

OFFICE_APPS = {"winword.exe", "excel.exe", "powerpnt.exe", "outlook.exe", "msaccess.exe", "mspub.exe",
               "onenote.exe", "visio.exe", "acrord32.exe", "acrobat.exe", "foxitreader.exe", "foxitpdfreader.exe"}
SHELLS = {"cmd.exe", "powershell.exe", "pwsh.exe", "wscript.exe", "cscript.exe", "mshta.exe", "rundll32.exe",
          "regsvr32.exe", "certutil.exe", "bitsadmin.exe", "schtasks.exe", "msiexec.exe", "wmic.exe"}


def collect_processes() -> List[dict]:
    """回傳 [{pid, ppid, name, exe, cmdline}]，優先用 psutil，其次用系統內建方式。"""
    try:
        import psutil  # type: ignore
        procs = []
        for p in psutil.process_iter(["pid", "ppid", "name", "exe", "cmdline"]):
            info = p.info
            procs.append({
                "pid": info.get("pid"),
                "ppid": info.get("ppid"),
                "name": info.get("name") or "",
                "exe": info.get("exe") or "",
                "cmdline": " ".join(info.get("cmdline") or []),
            })
        return procs
    except ImportError:
        pass
    except Exception:
        pass

    if IS_WINDOWS:
        data = powershell_json(
            "Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine"
            " | ConvertTo-Json -Compress"
        )
        return [
            {"pid": p.get("ProcessId"), "ppid": p.get("ParentProcessId"), "name": p.get("Name") or "",
             "exe": p.get("ExecutablePath") or "", "cmdline": p.get("CommandLine") or ""}
            for p in as_list(data) if isinstance(p, dict)
        ]
    if IS_LINUX and os.path.isdir("/proc"):
        procs = []
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            base = "/proc/" + d
            try:
                with open(base + "/comm", encoding="utf-8", errors="replace") as fh:
                    name = fh.read().strip()
                with open(base + "/cmdline", "rb") as fh:
                    cmdline = fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
                ppid = 0
                with open(base + "/status", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if line.startswith("PPid:"):
                            ppid = int(line.split()[1])
                            break
            except (OSError, ValueError):
                continue
            try:
                exe = os.readlink(base + "/exe")
            except OSError:
                exe = ""
            procs.append({"pid": int(d), "ppid": ppid, "name": name, "exe": exe, "cmdline": cmdline})
        return procs
    out = run_cmd(["ps", "-axww", "-o", "pid=,ppid=,comm=,args="]) or ""
    procs = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 3 and parts[0].isdigit():
            comm = parts[2]
            procs.append({"pid": int(parts[0]), "ppid": int(parts[1]), "name": os.path.basename(comm),
                          "exe": comm if comm.startswith("/") else "", "cmdline": parts[3] if len(parts) > 3 else comm})
    return procs


def score_process(p: dict, by_pid: dict, ctx: ScanContext) -> Scored:
    s = Scored()
    name = p.get("name") or ""
    exe = p.get("exe") or ""
    cmd = p.get("cmdline") or ""
    lname = name.lower()

    s.extend(score_command(cmd or exe, ctx.locations))
    if exe and not any("位於" in r for r in s.reasons):
        # 指令列裡沒寫完整路徑時，用實際執行檔位置再算一次
        w, label = ctx.locations.classify(exe)
        if w:
            s.add(w, "執行檔位於%s：%s" % (label, exe))

    if IS_WINDOWS:
        s.extend(check_masquerade(name, exe))
        parent = by_pid.get(p.get("ppid"))
        if parent and (parent.get("name") or "").lower() in OFFICE_APPS and lname in SHELLS:
            s.add(5, "由 %s 啟動了 %s（惡意巨集／文件的典型行為）" % (parent.get("name"), name))
    if IS_LINUX:
        if exe.endswith(" (deleted)"):
            s.add(3, "執行檔已從磁碟刪除但程序仍在執行（入侵後常見的湮滅手法）")
        if exe and re.match(r"^\[?(kworker|kthreadd|ksoftirqd|migration|rcu_\w+|watchdog)", cmd or name):
            s.add(5, "名稱偽裝成核心執行緒，但其實是一般程式：%s" % exe)
    return s


def run_check(ctx: ScanContext) -> List[Finding]:
    procs = ctx.processes()
    if not procs:
        ctx.note("無法取得程序清單")
        return []
    me = os.getpid()
    by_pid = {p.get("pid"): p for p in procs}

    def is_self(p: dict) -> bool:
        # 掃描器自己、以及它開的 PowerShell 子程序
        pid, ppid = p.get("pid"), p.get("ppid")
        return pid == me or ppid == me or (by_pid.get(ppid) or {}).get("ppid") == me

    scored = []
    for p in procs:
        if is_self(p):
            continue
        s = score_process(p, by_pid, ctx)
        if s.score >= 1:
            scored.append((p, s))

    ctx.prefetch_signatures(p.get("exe") for p, s in scored)
    findings = []
    for p, s in scored:
        exe = p.get("exe") or ""
        if exe and not ctx.locations.is_system(exe):
            s.extend(ctx.signature(exe))
        if s.score < 2 and not s.forced:
            continue
        title = "可疑程序：%s" % (p.get("name") or exe or "?")
        target = "PID %s  %s" % (p.get("pid"), exe or p.get("name"))
        findings.append(make_finding("processes", title, target, s, pid=p.get("pid"), ppid=p.get("ppid"),
                                     executable=exe, command=p.get("cmdline")))
    return findings


run = run_check
