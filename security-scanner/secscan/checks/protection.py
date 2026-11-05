"""防護狀態：防毒軟體是否啟用、Defender 排除清單、偵測紀錄、防火牆、UAC、hosts 檔。"""
from __future__ import annotations

import os
import re
import shutil
import socket
from typing import List, Tuple

from ..context import ScanContext, simple_finding
from ..model import Finding, Severity, severity_from_score
from ..util import IS_MAC, IS_WINDOWS, as_list, is_admin, powershell_json

CHECK = "protection"


def decode_product_state(state: int) -> Tuple[bool, bool]:
    """Windows 資訊安全中心 productState → (已啟用, 病毒碼為最新)。

    例：397568 = 0x061100 → 啟用、最新；393472 = 0x060100 → 停用；397584 = 0x061110 → 啟用、過期。
    """
    enabled = ((state >> 8) & 0xFF) in (0x10, 0x11)
    up_to_date = (state & 0xFF) == 0x00
    return enabled, up_to_date


# 劫持這些網域可讓防毒軟體或 Windows Update 無法更新
SECURITY_DOMAINS = re.compile(
    r"(?:^|\.)(?:microsoft\.com|windowsupdate\.com|windows\.com|update\.microsoft\.com|wdcp\.microsoft\.com"
    r"|kaspersky(?:-labs)?\.com|avast\.com|avg\.com|eset\.com|symantec\.com|norton\.com|mcafee\.com"
    r"|bitdefender\.(?:com|net)|malwarebytes\.(?:com|org)|mbamupdates\.com|sophos\.com|trendmicro\.com"
    r"|f-secure\.com|virustotal\.com|clamav\.net|avira\.com|drweb\.com|ahnlab\.com|gdatasoftware\.com"
    r"|emsisoft\.com|zonealarm\.com|pandasecurity\.com|webroot\.com|eicar\.org)$",
    re.IGNORECASE,
)
# 這些網域被導到「外部 IP」通常是釣魚／DNS 劫持
HIJACK_TARGETS = re.compile(
    r"(?:^|\.)(?:google\.com|facebook\.com|paypal\.com|apple\.com|icloud\.com|live\.com|outlook\.com"
    r"|office\.com|github\.com|line\.me|instagram\.com|binance\.com|coinbase\.com|metamask\.io|steamcommunity\.com)$",
    re.IGNORECASE,
)
_NULL_IPS = {"0.0.0.0", "127.0.0.1", "::1", "::", "0:0:0:0:0:0:0:0", "255.255.255.255", "fe80::1%lo0"}
_DEFAULT_NAMES = {"localhost", "localhost.localdomain", "broadcasthost", "ip6-localhost", "ip6-loopback",
                  "ip6-localnet", "ip6-mcastprefix", "ip6-allnodes", "ip6-allrouters", "ip6-allhosts"}


def hosts_path() -> str:
    if IS_WINDOWS:
        return os.path.join(os.environ.get("WINDIR") or r"C:\Windows", "System32", "drivers", "etc", "hosts")
    return "/etc/hosts"


def analyze_hosts(text: str) -> List[Tuple[int, str, List[str]]]:
    """回傳 [(分數, 說明, 相關行)]。"""
    blocked_sec, hijacked, custom = [], [], []
    own = socket.gethostname().lower()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        ip, names = parts[0], [n.lower() for n in parts[1:]]
        names = [n for n in names if n not in _DEFAULT_NAMES and n != own and not n.startswith(own + ".")]
        if not names:
            continue
        if any(SECURITY_DOMAINS.search(n) for n in names):
            blocked_sec.append(raw.strip())
        elif ip not in _NULL_IPS and not ip.startswith("127.") and any(HIJACK_TARGETS.search(n) for n in names):
            hijacked.append(raw.strip())
        else:
            custom.append(raw.strip())
    out = []
    if blocked_sec:
        out.append((7, "hosts 檔改寫了防毒軟體／系統更新網站（讓防護無法更新，惡意程式常做）", blocked_sec))
    if hijacked:
        out.append((5, "hosts 檔把常用網站導向其他 IP（可能是釣魚或 DNS 劫持）", hijacked))
    if custom:
        out.append((0, "hosts 檔有 %d 筆自訂項目（多半是廣告封鎖或開發用途）" % len(custom), custom[:20]))
    return out


def _check_hosts(ctx: ScanContext) -> List[Finding]:
    path = hosts_path()
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    findings = []
    for score, reason, lines in analyze_hosts(text):
        findings.append(simple_finding(CHECK, severity_from_score(score), "hosts 檔", path, [reason], score, lines=lines))
    return findings


# ---------------------------------------------------------------- Windows

def _exclusion_score(kind: str, value: str) -> Tuple[int, str]:
    v = value.strip().lower()
    if kind == "path":
        if re.fullmatch(r"[a-z]:\\?\*?", v) or v in ("*", "\\", "c:\\*"):
            return 7, "整顆磁碟都被排除，Defender 等於沒在掃描"
        if re.search(r"\\(?:temp|tmp|appdata|downloads|users\\public|programdata|desktop)(?:\\|$)|%temp%|%appdata%", v):
            return 6, "惡意程式常用的資料夾被排除在掃描之外"
        if "*" in v:
            return 5, "使用萬用字元的大範圍排除"
        return 4, "此路徑不會被 Defender 掃描"
    if kind == "extension":
        if v.lstrip(".*") in ("exe", "dll", "ps1", "bat", "cmd", "vbs", "js", "scr", "hta", "msi", "lnk", "com"):
            return 7, "可執行檔類型被排除，所有這類檔案都不會被掃描"
        return 4, "此副檔名不會被 Defender 掃描"
    if kind == "process":
        if v.split("\\")[-1] in ("powershell.exe", "pwsh.exe", "cmd.exe", "rundll32.exe", "regsvr32.exe", "mshta.exe",
                                "wscript.exe", "cscript.exe", "explorer.exe", "svchost.exe"):
            return 7, "系統指令程式被排除，經由它執行的惡意行為都不會被攔截"
        return 4, "此程式開啟的檔案不會被 Defender 掃描"
    return 4, ""


def _windows_defender(ctx: ScanContext, other_av_enabled: bool) -> List[Finding]:
    script = (
        "$s = Get-MpComputerStatus; $p = Get-MpPreference;"
        " [pscustomobject]@{ Ok = [bool]$s; AMServiceEnabled = $s.AMServiceEnabled; AntivirusEnabled = $s.AntivirusEnabled;"
        " RealTimeProtectionEnabled = $s.RealTimeProtectionEnabled; BehaviorMonitorEnabled = $s.BehaviorMonitorEnabled;"
        " IoavProtectionEnabled = $s.IoavProtectionEnabled; IsTamperProtected = $s.IsTamperProtected;"
        " AntivirusSignatureAge = $s.AntivirusSignatureAge; QuickScanAge = $s.QuickScanAge;"
        " AMRunningMode = [string]$s.AMRunningMode; PUAProtection = $p.PUAProtection;"
        " ExclusionPath = @($p.ExclusionPath); ExclusionExtension = @($p.ExclusionExtension);"
        " ExclusionProcess = @($p.ExclusionProcess) } | ConvertTo-Json -Compress -Depth 3"
    )
    st = powershell_json(script)
    findings: List[Finding] = []
    target = "Microsoft Defender"
    if not isinstance(st, dict) or not st.get("Ok"):
        if not other_av_enabled:
            findings.append(simple_finding(CHECK, Severity.HIGH, "無法取得 Microsoft Defender 狀態", target,
                                           ["Defender 可能已被停用或移除，且沒有偵測到其他啟用中的防毒軟體"], 6))
        return findings

    passive = str(st.get("AMRunningMode") or "").lower() in ("passive mode", "edr block mode", "sxs passive mode")
    if not other_av_enabled and not passive:
        if st.get("RealTimeProtectionEnabled") is False:
            findings.append(simple_finding(CHECK, Severity.HIGH, "Defender 即時保護已關閉", target,
                                           ["即時保護關閉時，新下載或執行的檔案都不會被檢查", "惡意程式經常會先關掉它"], 7))
        if st.get("BehaviorMonitorEnabled") is False:
            findings.append(simple_finding(CHECK, Severity.MEDIUM, "Defender 行為監控已關閉", target,
                                           ["無法偵測程式執行中的惡意行為"], 4))
        if st.get("IoavProtectionEnabled") is False:
            findings.append(simple_finding(CHECK, Severity.MEDIUM, "Defender 下載檔案掃描已關閉", target,
                                           ["從網路下載的檔案與附件不會被掃描"], 4))
        age = st.get("AntivirusSignatureAge")
        if isinstance(age, int) and 7 < age < 65535:
            findings.append(simple_finding(CHECK, Severity.MEDIUM, "Defender 病毒碼已 %d 天沒更新" % age, target,
                                           ["病毒碼過舊會認不出新的惡意程式", "也可能是更新被惡意程式阻擋"], 4))
        if st.get("IsTamperProtected") is False:
            findings.append(simple_finding(CHECK, Severity.LOW, "Defender 竄改防護未開啟", target,
                                           ["開啟後可防止惡意程式修改 Defender 設定（Windows 安全性 → 病毒與威脅防護設定）"], 2))
        if st.get("PUAProtection") == 0:
            findings.append(simple_finding(CHECK, Severity.INFO, "未封鎖可能不需要的應用程式（PUA）", target,
                                           ["可在 Windows 安全性 → 應用程式與瀏覽器控制 → 信譽型保護 開啟"], 0))

    for kind, key, label in (("path", "ExclusionPath", "路徑"), ("extension", "ExclusionExtension", "副檔名"),
                             ("process", "ExclusionProcess", "程式")):
        for value in as_list(st.get(key)):
            if not isinstance(value, str) or not value.strip():
                continue
            if value.startswith("N/A"):
                ctx.note("讀取 Defender 排除清單需要系統管理員權限（請用 scan.cmd 或以系統管理員身分執行）")
                break
            score, why = _exclusion_score(kind, value)
            findings.append(simple_finding(
                CHECK, severity_from_score(score), "Defender 排除%s：%s" % (label, value),
                "Defender 排除清單", [why, "若不是你自己或公司 IT 加的，惡意程式常會把自己加進排除清單"],
                score, exclusion=value, exclusion_type=kind))

    threats = powershell_json(
        "Get-MpThreat | Select-Object ThreatName, SeverityID, IsActive, DidThreatExecute, Resources"
        " | ConvertTo-Json -Compress -Depth 3"
    )
    for t in as_list(threats):
        if not isinstance(t, dict):
            continue
        name = t.get("ThreatName") or "未知威脅"
        res = [r for r in as_list(t.get("Resources")) if isinstance(r, str)][:10]
        if t.get("IsActive"):
            score, sev, why = 8, Severity.HIGH, "Defender 偵測到、但目前仍處於活動狀態（尚未清除）"
        else:
            score, sev, why = 2, Severity.LOW, "Defender 過去偵測並已處理"
        reasons = [why]
        if t.get("DidThreatExecute"):
            reasons.append("這個威脅曾經被執行過")
            score += 2
            sev = max(sev, Severity.MEDIUM)
        findings.append(simple_finding(CHECK, sev, "Defender 偵測紀錄：%s" % name, "Microsoft Defender", reasons,
                                       score, resources=res))
    return findings


def _windows_av_products(ctx: ScanContext) -> Tuple[List[Finding], bool, bool]:
    """回傳 (findings, 有啟用中的第三方防毒, 有資料)。"""
    data = powershell_json(
        "Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct"
        " | Select-Object displayName, productState, pathToSignedProductExe | ConvertTo-Json -Compress"
    )
    products = [p for p in as_list(data) if isinstance(p, dict)]
    if not products:
        return [], False, False  # Windows Server 沒有資訊安全中心
    findings = []
    any_enabled = False
    other_enabled = False
    for p in products:
        name = p.get("displayName") or "?"
        try:
            enabled, fresh = decode_product_state(int(p.get("productState") or 0))
        except (TypeError, ValueError):
            continue
        any_enabled |= enabled
        if enabled and "defender" not in name.lower():
            other_enabled = True
        status = "啟用中" if enabled else "已停用"
        status += "、病毒碼最新" if fresh else "、病毒碼過期"
        sev = Severity.INFO if enabled and fresh else (Severity.MEDIUM if enabled else Severity.INFO)
        findings.append(simple_finding(CHECK, sev, "已安裝的防毒軟體：%s" % name, name, [status],
                                       0 if sev == Severity.INFO else 4, product_state=p.get("productState"),
                                       path=p.get("pathToSignedProductExe")))
    if not any_enabled:
        findings.append(simple_finding(CHECK, Severity.HIGH, "沒有任何啟用中的防毒軟體", "Windows 資訊安全中心",
                                       ["所有已註冊的防毒軟體都是停用狀態"], 7))
    return findings, other_enabled, True


def _windows_firewall(ctx: ScanContext) -> List[Finding]:
    data = powershell_json(
        "Get-NetFirewallProfile | Select-Object Name, @{n='Enabled';e={[string]$_.Enabled}} | ConvertTo-Json -Compress"
    )
    findings = []
    for p in as_list(data):
        if isinstance(p, dict) and str(p.get("Enabled")).lower() in ("false", "0"):
            findings.append(simple_finding(CHECK, Severity.MEDIUM, "Windows 防火牆已關閉：%s 設定檔" % p.get("Name"),
                                           "Windows 防火牆", ["防火牆關閉時，任何程式都能接收外部連線"], 4))
    return findings


def _windows_uac(ctx: ScanContext) -> List[Finding]:
    import winreg
    findings = []
    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            def get(name):
                try:
                    return winreg.QueryValueEx(k, name)[0]
                except OSError:
                    return None
            lua, consent = get("EnableLUA"), get("ConsentPromptBehaviorAdmin")
    except OSError:
        return findings
    if lua == 0:
        findings.append(simple_finding(CHECK, Severity.HIGH, "使用者帳戶控制（UAC）已完全關閉", "HKLM\\" + key,
                                       ["任何程式都能直接取得系統管理員權限"], 6))
    elif consent == 0:
        findings.append(simple_finding(CHECK, Severity.MEDIUM, "UAC 設為「不通知」", "HKLM\\" + key,
                                       ["程式提升權限時不會跳出確認視窗"], 4))
    return findings


# ---------------------------------------------------------------- Linux / macOS

def _unix_checks(ctx: ScanContext) -> List[Finding]:
    findings = []
    av = [n for n in ("clamscan", "clamdscan", "freshclam", "sophos-av", "mdatp", "falcon-sensor", "rkhunter", "chkrootkit")
          if shutil.which(n)]
    if av:
        findings.append(simple_finding(CHECK, Severity.INFO, "已安裝的防毒／檢測工具", "PATH", [", ".join(av)]))
    else:
        findings.append(simple_finding(CHECK, Severity.INFO, "沒有找到防毒軟體", "PATH",
                                       ["可考慮安裝 ClamAV（sudo apt install clamav）或 rkhunter 做進一步檢查"]))
    try:
        with open("/etc/ssh/sshd_config", encoding="utf-8", errors="replace") as fh:
            cfg = fh.read()
        if re.search(r"^\s*PermitRootLogin\s+yes\b", cfg, re.M | re.I):
            findings.append(simple_finding(CHECK, Severity.LOW, "SSH 允許 root 直接登入", "/etc/ssh/sshd_config",
                                           ["建議改為 PermitRootLogin no 或 prohibit-password"], 2))
        if re.search(r"^\s*PermitEmptyPasswords\s+yes\b", cfg, re.M | re.I):
            findings.append(simple_finding(CHECK, Severity.HIGH, "SSH 允許空白密碼登入", "/etc/ssh/sshd_config",
                                           ["任何人都可能不用密碼登入"], 7))
    except OSError:
        pass
    if IS_MAC:
        from ..util import run as run_cmd
        gk = run_cmd(["spctl", "--status"], timeout=10) or ""
        if "disabled" in gk:
            findings.append(simple_finding(CHECK, Severity.MEDIUM, "Gatekeeper 已關閉", "spctl --status",
                                           ["未經簽署的 App 可以直接執行"], 4))
        sip = run_cmd(["csrutil", "status"], timeout=10) or ""
        if "disabled" in sip.lower():
            findings.append(simple_finding(CHECK, Severity.MEDIUM, "系統完整性保護（SIP）已關閉", "csrutil status",
                                           ["系統檔案可被任意修改"], 4))
    return findings


def run(ctx: ScanContext) -> List[Finding]:
    findings: List[Finding] = []
    if IS_WINDOWS:
        if not is_admin():
            ctx.note("目前不是系統管理員：部分項目（Defender 排除清單、其他使用者的程序）無法完整檢查")
        av_findings, other_enabled, _ = _windows_av_products(ctx)
        findings += av_findings
        findings += _windows_defender(ctx, other_enabled)
        findings += _windows_firewall(ctx)
        findings += _windows_uac(ctx)
    else:
        findings += _unix_checks(ctx)
    findings += _check_hosts(ctx)
    return findings
