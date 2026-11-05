"""網路連線：連到已知惡意 IP、常見木馬／挖礦／Tor 連接埠、可疑位置的程式對外連線。"""
from __future__ import annotations

import ipaddress
import os
import socket
import struct
from typing import Dict, List

from ..context import ScanContext, make_finding
from ..model import Finding, Scored, Severity
from ..util import IS_LINUX, IS_WINDOWS, as_list, powershell_json

# 連接埠 → 說明。這些埠本身不代表一定有問題，只是常見於惡意用途。
SUSPICIOUS_PORTS = {
    4444: "Metasploit 預設連接埠",
    1337: "常見後門連接埠",
    31337: "常見後門連接埠（Back Orifice）",
    6666: "IRC 殭屍網路",
    6667: "IRC 殭屍網路",
    6697: "IRC 殭屍網路",
    3333: "挖礦礦池",
    5555: "挖礦礦池／ADB 遠端除錯",
    7777: "挖礦礦池",
    14444: "門羅幣礦池",
    45700: "門羅幣礦池",
    9001: "Tor 中繼節點",
    9030: "Tor 目錄伺服器",
    9050: "Tor SOCKS 代理",
    9150: "Tor 瀏覽器代理",
    5900: "VNC 遠端桌面",
    1080: "SOCKS 代理",
}


def _is_external(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_unspecified or a.is_reserved)


# ---------------------------------------------------------------- 取得連線

_TCP_STATES = {"01": "ESTABLISHED", "02": "SYN_SENT", "0A": "LISTEN", "06": "TIME_WAIT", "08": "CLOSE_WAIT"}


def _parse_proc_addr(text: str, v6: bool):
    host, port = text.split(":")
    raw = bytes.fromhex(host)
    if v6:
        # /proc 以 4 個 32 位元小端序字組存放
        raw = b"".join(struct.pack("<I", struct.unpack(">I", raw[i:i + 4])[0]) for i in range(0, 16, 4))
        ip = socket.inet_ntop(socket.AF_INET6, raw)
    else:
        ip = socket.inet_ntoa(struct.pack("<I", int(host, 16)))
    return ip, int(port, 16)


def _linux_connections() -> List[dict]:
    inode_pid: Dict[str, int] = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            for fd in os.listdir("/proc/%s/fd" % d):
                try:
                    link = os.readlink("/proc/%s/fd/%s" % (d, fd))
                except OSError:
                    continue
                if link.startswith("socket:["):
                    inode_pid[link[8:-1]] = int(d)
        except OSError:
            continue
    conns = []
    for proto, path, v6 in (("tcp", "/proc/net/tcp", False), ("tcp6", "/proc/net/tcp6", True),
                            ("udp", "/proc/net/udp", False), ("udp6", "/proc/net/udp6", True)):
        try:
            with open(path) as fh:
                lines = fh.read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 10:
                continue
            try:
                lip, lport = _parse_proc_addr(parts[1], v6)
                rip, rport = _parse_proc_addr(parts[2], v6)
            except (ValueError, OSError):
                continue
            state = _TCP_STATES.get(parts[3], parts[3]) if proto.startswith("tcp") else ("LISTEN" if rport == 0 else "ESTABLISHED")
            conns.append({"proto": proto, "laddr": lip, "lport": lport, "raddr": rip, "rport": rport,
                          "state": state, "pid": inode_pid.get(parts[9])})
    return conns


def collect_connections() -> List[dict]:
    try:
        import psutil  # type: ignore
        out = []
        for c in psutil.net_connections(kind="inet"):
            out.append({
                "proto": "tcp" if c.type == socket.SOCK_STREAM else "udp",
                "laddr": c.laddr.ip if c.laddr else "", "lport": c.laddr.port if c.laddr else 0,
                "raddr": c.raddr.ip if c.raddr else "", "rport": c.raddr.port if c.raddr else 0,
                "state": "LISTEN" if c.status == "LISTEN" else c.status, "pid": c.pid,
            })
        return out
    except ImportError:
        pass
    except Exception:
        pass
    if IS_WINDOWS:
        data = powershell_json(
            "Get-NetTCPConnection | Select-Object LocalAddress, LocalPort, RemoteAddress, RemotePort,"
            " @{n='State';e={[string]$_.State}}, OwningProcess | ConvertTo-Json -Compress"
        )
        out = []
        for c in as_list(data):
            if not isinstance(c, dict):
                continue
            state = (c.get("State") or "").upper()
            out.append({"proto": "tcp", "laddr": c.get("LocalAddress") or "", "lport": c.get("LocalPort") or 0,
                        "raddr": c.get("RemoteAddress") or "", "rport": c.get("RemotePort") or 0,
                        "state": "LISTEN" if state == "LISTEN" else state, "pid": c.get("OwningProcess")})
        return out
    if IS_LINUX:
        return _linux_connections()
    return []


# ---------------------------------------------------------------- 評分

def run(ctx: ScanContext) -> List[Finding]:
    conns = collect_connections()
    if not conns:
        ctx.note("無法取得網路連線清單（可能需要系統管理員權限，或安裝 psutil）")
        return []
    procs = {p.get("pid"): p for p in ctx.processes()}

    # 依程序彙整
    per_proc: Dict[object, Dict[str, object]] = {}
    for c in conns:
        pid = c.get("pid")
        rec = per_proc.setdefault(pid, {"s": Scored(), "remote": [], "listen": []})
        s: Scored = rec["s"]  # type: ignore[assignment]
        raddr, rport, lport = c.get("raddr") or "", c.get("rport") or 0, c.get("lport") or 0
        listening = c.get("state") == "LISTEN" or (not raddr or raddr in ("0.0.0.0", "::", "*"))
        if listening:
            rec["listen"].append("%s:%s" % (c.get("laddr"), lport))  # type: ignore[union-attr]
            if lport in SUSPICIOUS_PORTS and c.get("proto", "").startswith("tcp"):
                s.add(3, "在 %d 埠等待連入（%s）" % (lport, SUSPICIOUS_PORTS[lport]))
            continue
        plain = raddr.split("%")[0]
        if plain.startswith("::ffff:"):
            plain = plain[7:]
        if plain in ctx.bad_ips:
            rec["remote"].append("%s:%s" % (raddr, rport))  # type: ignore[union-attr]
            s.add(10, "連線到已知惡意 IP：%s" % plain)
            s.force(Severity.CRITICAL)
            continue
        if not _is_external(raddr):
            continue
        rec["remote"].append("%s:%s" % (raddr, rport))  # type: ignore[union-attr]
        if rport in SUSPICIOUS_PORTS:
            s.add(3, "連線到外部 %s:%d（%s）" % (raddr, rport, SUSPICIOUS_PORTS[rport]))

    ctx.prefetch_signatures((procs.get(pid) or {}).get("exe") for pid in per_proc)
    findings = []
    for pid, rec in per_proc.items():
        s = rec["s"]  # type: ignore[assignment]
        p = procs.get(pid) or {}
        exe = p.get("exe") or ""
        remote = rec["remote"]
        if exe and (remote or rec["listen"]):
            w, label = ctx.locations.classify(exe)
            if w:
                s.add(w + 1, "位於%s的程式有網路連線：%s" % (label, exe))
                s.extend(ctx.signature(exe))  # type: ignore[union-attr]
        if s.score < 2 and not s.forced:  # type: ignore[union-attr]
            continue
        name = p.get("name") or ("PID %s" % pid if pid else "未知程序")
        findings.append(make_finding(
            "network", "可疑網路活動：%s" % name, "PID %s  %s" % (pid, exe or name), s,  # type: ignore[arg-type]
            pid=pid, executable=exe, remote=sorted(set(remote))[:20], listen=sorted(set(rec["listen"]))[:20],  # type: ignore[arg-type]
        ))
    return findings
