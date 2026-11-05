"""可疑內容規則：比對腳本內容、指令列、捷徑字串。

每條規則有一個權重，命中的權重加總後換算成嚴重度（見 model.severity_from_score）。

注意：少數字串（EICAR 測試碼、AMSI 繞過、憑證竊取工具名稱）在這裡以 Base64 存放。
這是為了避免「掃描器原始碼本身」被防毒軟體誤判成惡意檔，並非混淆用途。
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import List, Tuple


def _b64(text: str) -> str:
    return base64.b64decode(text).decode("ascii")


# EICAR 標準防毒測試字串（無害，所有防毒軟體都會把它當成病毒，用來測試）
EICAR = _b64("WDVPIVAlQEFQWzRcUFpYNTQoUF4pN0NDKTd9JEVJQ0FSLVNUQU5EQVJELUFOVElWSVJVUy1URVNULUZJTEUhJEgrSCo=")

_AMSI_WORDS = _b64("QW1zaVV0aWxzfGFtc2lJbml0RmFpbGVkfEFtc2lTY2FuQnVmZmVyfEFtc2lPcGVuU2Vzc2lvbg==")
_CRED_TOOLS = _b64("bWltaWthdHp8c2VrdXJsc2E6Onxsc2FkdW1wOjp8a2VyYmVyb3M6OmdvbGRlbnxJbnZva2UtTWltaWthdHo=")


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: "re.Pattern[str]"
    weight: int
    desc: str
    scope: str = "code"   # code：腳本／指令列；note：一般文字檔（勒索信）
    keywords: Tuple[str, ...] = ()  # 預先篩選：文字裡至少要有其中一個（小寫）才跑正規表示式


def _r(rule_id: str, pattern: str, weight: int, desc: str, scope: str = "code") -> Rule:
    return Rule(rule_id, re.compile(pattern, re.IGNORECASE), weight, desc, scope, KEYWORDS.get(rule_id, ()))


_PSK = ("powershell", "pwsh")
KEYWORDS = {
    "ps-encoded": _PSK, "ps-hidden": _PSK, "ps-bypass": _PSK,
    "ps-download-exec": ("iex", "invoke-expression"),
    "b64-exec": ("frombase64string",),
    "reflective-load": ("reflection.assembly",),
    "amsi-bypass": ("amsi",),
    "defender-tamper": ("mppreference", "disableantispyware", "disablerealtimemonitoring"),
    "shadow-delete": ("vssadmin", "shadowcopy", "wbadmin", "bcdedit"),
    "certutil-abuse": ("certutil",),
    "bitsadmin-download": ("bitsadmin",),
    "mshta-remote": ("mshta",),
    "regsvr32-remote": ("regsvr32",),
    "rundll32-script": ("rundll32",),
    "download-then-run": ("curl", "wget"),
    "pipe-to-shell": ("curl", "wget"),
    "reverse-shell": ("/dev/tcp/", "nc", "socket.socket", "tcpclient"),
    "cred-dump": tuple(w.lower() for w in _CRED_TOOLS.split("|")) + ("procdump", "comsvcs"),
    "crypto-miner": ("stratum+", "xmrig", "donate-level", "cryptonight", "minerd", "nicehash"),
    "reg-run-key": ("currentversion\\run",),
    "schtasks-create": ("schtasks",),
    "wscript-exec": ("wscript.shell", "shell.application"),
    "caret-obfuscation": ("^",),
    "char-code-obfuscation": ("[char]", "chr(", "chrw("),
    "disable-firewall": ("netsh", "windefend", "wscsvc", "mpssvc", "securityhealthservice"),
    "clear-logs": ("wevtutil", "clear-eventlog", "history", "histfile"),
    "ld-preload": ("ld_preload", "ld.so.preload"),
    "ransom-note": ("encrypted", "bitcoin", "btc", "monero", "加密"),
}


_PS = r"(?:powershell|pwsh)(?:\.exe)?"

RULES: List[Rule] = [
    _r("ps-encoded",
       _PS + r"\b[^\n]{0,200}?\s[-/]e[a-z]*\s+[A-Za-z0-9+/=]{20,}",
       4, "PowerShell 以 Base64 編碼執行指令（惡意程式常用來隱藏真正的指令）"),
    _r("ps-download-exec",
       r"(?:\bIEX\b|Invoke-Expression)[\s\S]{0,200}?(?:DownloadString|DownloadData|Net\.WebClient|Invoke-WebRequest|Invoke-RestMethod|\biwr\b|\birm\b)"
       r"|(?:DownloadString|DownloadData|Net\.WebClient|Invoke-WebRequest|Invoke-RestMethod|\biwr\b|\birm\b)[\s\S]{0,200}?(?:\bIEX\b|Invoke-Expression)",
       3, "從網路下載後直接在記憶體執行（Download & Execute）"),
    _r("ps-hidden",
       _PS + r"\b[^\n]{0,200}?\s[-/]w(?:in(?:dow(?:style)?)?)?\s+(?:h(?:idden)?|1)\b",
       2, "以隱藏視窗執行 PowerShell"),
    _r("ps-bypass",
       _PS + r"\b[^\n]{0,200}?\s[-/](?:ep|exec(?:utionpolicy)?)\s+(?:bypass|unrestricted)",
       1, "繞過 PowerShell 執行原則"),
    _r("b64-exec",
       r"FromBase64String[\s\S]{0,300}?(?:\bIEX\b|Invoke-Expression|\.Invoke\(|Assembly\]::Load)",
       3, "Base64 解碼後立即執行或載入程式碼"),
    _r("reflective-load",
       r"\[(?:System\.)?Reflection\.Assembly\]::Load(?:File)?\s*\(",
       2, "以反射方式載入 .NET 程式（無檔案攻擊常用手法）"),
    _r("amsi-bypass", _AMSI_WORDS, 4,"嘗試停用 AMSI 防毒掃描介面"),
    _r("defender-tamper",
       r"Set-MpPreference[^\n]{0,200}?-Disable\w+\s+(?:\$true|1)\b|Add-MpPreference[^\n]{0,200}?-Exclusion\w+"
       r"|DisableAntiSpyware|DisableRealtimeMonitoring",
       4, "關閉 Windows Defender 或新增排除項目"),
    _r("shadow-delete",
       r"vssadmin(?:\.exe)?\s+delete\s+shadows|wmic(?:\.exe)?\s+shadowcopy\s+delete"
       r"|wbadmin(?:\.exe)?\s+delete\s+(?:catalog|systemstatebackup)"
       r"|bcdedit(?:\.exe)?\s+/set\s+\S+\s+recoveryenabled\s+no|Win32_ShadowCopy[\s\S]{0,80}?Delete",
       5, "刪除系統還原點／備份（勒索軟體的典型行為）"),
    _r("certutil-abuse",
       r"certutil(?:\.exe)?[^\n]{0,100}?[-/](?:urlcache|verifyctl|decode(?:hex)?)\b",
       3, "濫用 certutil 下載或解碼檔案"),
    _r("bitsadmin-download",
       r"bitsadmin(?:\.exe)?[^\n]{0,60}?/(?:transfer|addfile)\b",
       2, "使用 bitsadmin 背景下載檔案"),
    _r("mshta-remote",
       r"mshta(?:\.exe)?\s+[\"']?(?:https?:|javascript:|vbscript:)",
       4, "mshta 執行遠端或內嵌腳本"),
    _r("regsvr32-remote",
       r"regsvr32(?:\.exe)?[^\n]{0,100}?/i:\s*[\"']?https?:",
       4, "regsvr32 載入遠端腳本（Squiblydoo 手法）"),
    _r("rundll32-script",
       r"rundll32(?:\.exe)?[^\n]{0,80}?(?:javascript:|vbscript:|mshtml,\s*RunHTMLApplication)",
       4, "rundll32 執行腳本"),
    _r("download-then-run",
       r"(?:curl|wget)(?:\.exe)?\s[^\n]{0,200}?\s(?:-o|-O|--output)\s[^\n]{0,200}?(?:&&|;|\|)\s*(?:start\b|cmd\b|powershell\b|chmod\s+\+x|\./)",
       2, "下載檔案後立即執行"),
    _r("pipe-to-shell",
       r"(?:curl|wget)\s[^\n|]{0,200}\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b",
       2, "下載腳本後直接交給 shell 執行"),
    _r("reverse-shell",
       r"/dev/tcp/\d{1,3}(?:\.\d{1,3}){3}/\d+"
       r"|\bnc(?:at)?(?:\.exe)?\s[^\n]{0,60}?\s-[a-z]*e\s+(?:/bin/(?:ba)?sh|cmd(?:\.exe)?)"
       r"|socket\.socket[\s\S]{0,300}?(?:pty\.spawn|os\.dup2)"
       r"|Net\.Sockets\.TCPClient[\s\S]{0,400}?(?:GetStream|\bIEX\b|Invoke-Expression)",
       4, "反向連線 Shell（讓攻擊者遠端操控電腦）"),
    _r("cred-dump",
       _CRED_TOOLS + r"|procdump(?:64)?(?:\.exe)?[^\n]{0,60}?lsass|comsvcs(?:\.dll)?[^\n]{0,40}?MiniDump",
       5, "竊取帳號密碼（憑證傾印工具）"),
    _r("crypto-miner",
       r"stratum\+(?:tcp|ssl|tls)://|\bxmrig\b|--donate-level|\bcryptonight\b|\bminerd\b|\bnicehash\b",
       4, "加密貨幣挖礦程式（挖礦木馬）"),
    _r("reg-run-key",
       r"reg(?:\.exe)?\s+add\s+[\"']?HK(?:CU|LM|EY_CURRENT_USER|EY_LOCAL_MACHINE)\\Software\\Microsoft\\Windows\\CurrentVersion\\Run",
       2, "寫入登錄檔讓程式開機自動執行"),
    _r("schtasks-create",
       r"schtasks(?:\.exe)?\s+/create\b",
       1, "建立排程工作"),
    _r("wscript-exec",
       r"(?:WScript\.Shell|Shell\.Application)[\s\S]{0,300}?\.(?:Run|ShellExecute|Exec)\b",
       1, "以 VBScript／JScript 啟動外部程式"),
    _r("caret-obfuscation",
       r"(?:[a-z.]\^){4,}[a-z]",
       2, "以 ^ 字元混淆指令"),
    _r("char-code-obfuscation",
       r"(?:\[char\]\s*\d{2,3}\s*[+,]\s*){6,}|(?:chrw?\(\d{2,3}\)\s*[&+]\s*){6,}",
       2, "以字元碼拼接隱藏字串"),
    _r("disable-firewall",
       r"netsh(?:\.exe)?\s+(?:adv)?firewall\s+set\s+\S+\s+(?:state\s+off|opmode\s+disable)"
       r"|sc(?:\.exe)?\s+(?:stop|config)\s+(?:WinDefend|wscsvc|MpsSvc|SecurityHealthService)\b",
       3, "關閉防火牆或安全服務"),
    _r("clear-logs",
       r"wevtutil(?:\.exe)?\s+cl\b|Clear-EventLog|\bhistory\s+-c\b|unset\s+HISTFILE|rm\s+-r?f?\s+[^\n]*\.bash_history",
       3, "清除系統紀錄（湮滅證據）"),
    _r("ld-preload",
       r"\bLD_PRELOAD=|/etc/ld\.so\.preload",
       1, "使用 LD_PRELOAD 注入函式庫"),
    _r("ransom-note",
       r"your (?:important )?files (?:have been|are|were) encrypted"
       r"|(?:bitcoin|btc|monero)[\s\S]{0,300}?(?:decrypt|ransom)"
       r"|(?:decrypt|ransom)[\s\S]{0,300}?(?:bitcoin|btc|monero)"
       r"|(?:您|你)的(?:文件|檔案|資料)(?:已經?|都已)被加密",
       5, "疑似勒索軟體留下的勒索訊息", scope="note"),
]


def match_rules(text: str, scope: str = "code") -> List[Tuple[Rule, str]]:
    """回傳 [(規則, 命中片段)]。"""
    hits: List[Tuple[Rule, str]] = []
    if not text:
        return hits
    lower = text.lower()
    for rule in RULES:
        if rule.scope != scope:
            continue
        if rule.keywords and not any(k in lower for k in rule.keywords):
            continue
        m = rule.pattern.search(text)
        if m:
            snippet = " ".join(m.group(0).split())
            if len(snippet) > 160:
                snippet = snippet[:157] + "..."
            hits.append((rule, snippet))
    return hits


def contains_eicar(data: bytes) -> bool:
    return EICAR.encode("ascii") in data
