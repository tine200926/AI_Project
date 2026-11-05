"""輸出：終端機、JSON、HTML 報告。"""
from __future__ import annotations

import html
import json
import platform
import re
import socket
from collections import Counter
from datetime import datetime
from typing import Dict, List

from .model import CHECK_LABELS, Finding, Severity

COLORS = {
    Severity.CRITICAL: "\033[1;97;41m",
    Severity.HIGH: "\033[1;91m",
    Severity.MEDIUM: "\033[1;93m",
    Severity.LOW: "\033[96m",
    Severity.INFO: "\033[37m",
}
RESET = "\033[0m"
DIM = "\033[2m"


_BIDI = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def visible(text: str) -> str:
    """把隱形的方向控制字元（RTLO 等）顯示成 [U+202E]，避免報告本身也被騙。"""
    return _BIDI.sub(lambda m: "[U+%04X]" % ord(m.group()), str(text))


def sort_findings(findings: List[Finding]) -> List[Finding]:
    order = list(CHECK_LABELS)
    return sorted(findings, key=lambda f: (-int(f.severity), -f.score,
                                           order.index(f.check) if f.check in order else 99, f.target))


def summary(findings: List[Finding]) -> Dict[str, int]:
    c = Counter(f.severity for f in findings)
    return {s.name.lower(): c.get(s, 0) for s in Severity}


def verdict(findings: List[Finding]) -> str:
    worst = max((f.severity for f in findings), default=Severity.INFO)
    if worst >= Severity.CRITICAL:
        return "發現高度可疑或已知惡意的項目，建議立即處理（見下方建議）。"
    if worst >= Severity.HIGH:
        return "發現需要優先確認的可疑項目。"
    if worst >= Severity.MEDIUM:
        return "有一些值得檢查的項目，多數可能是正常軟體，請逐一確認。"
    if worst >= Severity.LOW:
        return "只有低風險項目，通常是正常軟體的特徵，有空再看即可。"
    return "沒有發現可疑項目。"


ADVICE = [
    "先別急著刪除：用右鍵 →「使用 Microsoft Defender 掃描」，或把檔案上傳到 https://www.virustotal.com 交叉確認。",
    "不認得的開機項目可以先在「工作管理員 → 啟動應用程式」停用，觀察幾天沒問題再移除。",
    "確認是誤判的項目，把路徑或 SHA256 加進 allowlist.txt，下次就不會再出現。",
    "若有「嚴重」項目或 Defender 偵測紀錄仍在活動中：先中斷網路，執行 Defender 離線掃描（Windows 安全性 → 病毒與威脅防護 → 掃描選項 → Microsoft Defender 離線掃描），並更改重要帳號密碼。",
]


def print_console(findings: List[Finding], meta: dict, color: bool, verbose: bool) -> None:
    def c(text: str, code: str) -> str:
        return code + text + RESET if color else text

    print()
    print(c("═" * 64, DIM))
    print(" 掃描結果  %s  %s" % (meta["hostname"], meta["finished"]))
    print(c("═" * 64, DIM))
    if not findings:
        print(" " + verdict(findings))
    current = None
    for f in sort_findings(findings):
        if f.severity != current:
            current = f.severity
            print()
            print(c(" ■ %s " % f.severity.label, COLORS[f.severity]))
        tag = CHECK_LABELS.get(f.check, f.check)
        print("  %s %s" % (c("[%s]" % tag, DIM), f.title))
        print("      %s" % visible(f.target))
        for r in f.reasons:
            print("      %s %s" % (c("·", DIM), r))
        if verbose:
            for key in ("command", "evidence", "sha256", "remote", "lines", "resources"):
                val = f.details.get(key)
                if val:
                    if isinstance(val, list):
                        val = " | ".join(str(v) for v in val)
                    print(c("      %s: %s" % (key, str(val)[:300]), DIM))
    print()
    counts = summary(findings)
    parts = ["%s %d" % (s.label, counts[s.name.lower()]) for s in sorted(Severity, reverse=True)]
    print(c("─" * 64, DIM))
    print(" 統計：" + "   ".join(parts))
    print(" 結論：" + verdict(findings))
    for note in meta.get("notes", []):
        print(c(" 註：" + note, DIM))
    print()


def to_json(findings: List[Finding], meta: dict) -> str:
    return json.dumps({
        "meta": meta,
        "summary": summary(findings),
        "verdict": verdict(findings),
        "findings": [f.to_dict() for f in sort_findings(findings)],
    }, ensure_ascii=False, indent=2, default=str)


def collect_meta(started: datetime, finished: datetime, checks: List[str], roots: List[str],
                 notes: List[str], admin: bool, hash_count: int) -> dict:
    return {
        "tool": "secscan",
        "hostname": socket.gethostname(),
        "platform": "%s %s" % (platform.system(), platform.release()),
        "python": platform.python_version(),
        "started": started.strftime("%Y-%m-%d %H:%M:%S"),
        "finished": finished.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_sec": round((finished - started).total_seconds(), 1),
        "checks": checks,
        "scan_roots": roots,
        "admin": admin,
        "known_hashes": hash_count,
        "notes": notes,
    }


_HTML_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--muted:#5d6675;--line:#e3e6eb;--chip:#eef0f3;
--crit:#b3261e;--crit-bg:#fde7e5;--high:#c2410c;--high-bg:#ffedd5;--med:#a16207;--med-bg:#fef9c3;
--low:#0e7490;--low-bg:#cffafe;--info:#4b5563;--info-bg:#e5e7eb;--accent:#2563eb}
@media (prefers-color-scheme:dark){:root{--bg:#111318;--card:#1a1d24;--fg:#e6e8ec;--muted:#9aa3b2;--line:#2a2f3a;
--chip:#252a34;--crit:#ff8a80;--crit-bg:#4a1512;--high:#fdba74;--high-bg:#47230e;--med:#fde047;--med-bg:#3b3410;
--low:#67e8f9;--low-bg:#0f3640;--info:#cbd5e1;--info-bg:#2b313c;--accent:#60a5fa}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 system-ui,-apple-system,"Segoe UI","Microsoft JhengHei","PingFang TC","Noto Sans TC",sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:24px;margin:0 0 4px}.sub{color:var(--muted);font-size:13px}
.verdict{margin:20px 0;padding:16px 18px;border-radius:12px;background:var(--card);border:1px solid var(--line);
border-left:6px solid var(--accent);font-weight:600}
.v-critical{border-left-color:var(--crit)}.v-high{border-left-color:var(--high)}.v-medium{border-left-color:var(--med)}
.v-low{border-left-color:var(--low)}
.tiles{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:16px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;cursor:pointer;user-select:none}
.tile b{display:block;font-size:26px;font-variant-numeric:tabular-nums}.tile span{color:var(--muted);font-size:13px}
.tile.off{opacity:.4}
.t-critical b{color:var(--crit)}.t-high b{color:var(--high)}.t-medium b{color:var(--med)}.t-low b{color:var(--low)}.t-info b{color:var(--info)}
.bar{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0 14px}
.bar input,.bar select{font:inherit;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
.bar input{flex:1;min-width:200px}
.f{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:10px}
.f header{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.sev{font-size:12px;font-weight:700;padding:2px 9px;border-radius:999px;white-space:nowrap}
.s-critical{color:var(--crit);background:var(--crit-bg)}.s-high{color:var(--high);background:var(--high-bg)}
.s-medium{color:var(--med);background:var(--med-bg)}.s-low{color:var(--low);background:var(--low-bg)}
.s-info{color:var(--info);background:var(--info-bg)}
.chk{font-size:12px;color:var(--muted);background:var(--chip);padding:2px 8px;border-radius:6px}
.title{font-weight:600}.target{font-family:ui-monospace,Consolas,monospace;font-size:13px;color:var(--muted);
word-break:break-all;margin:6px 0}
ul{margin:6px 0 0;padding-left:20px}details{margin-top:8px}summary{cursor:pointer;color:var(--accent);font-size:13px}
pre{white-space:pre-wrap;word-break:break-all;background:var(--chip);padding:10px;border-radius:8px;font-size:12px;
font-family:ui-monospace,Consolas,monospace;margin:8px 0 0}
.box{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin-top:24px}
.box h2{font-size:16px;margin:0 0 6px}.muted{color:var(--muted);font-size:13px}.empty{text-align:center;padding:30px;color:var(--muted)}
@media (max-width:640px){.tiles{grid-template-columns:repeat(3,1fr)}}
"""

_HTML_JS = """
const tiles=[...document.querySelectorAll('.tile')],items=[...document.querySelectorAll('.f')];
const q=document.getElementById('q'),chk=document.getElementById('chk'),empty=document.getElementById('empty');
const off=new Set(['info']);
function apply(){const t=q.value.trim().toLowerCase(),c=chk.value;let n=0;
items.forEach(el=>{const ok=!off.has(el.dataset.sev)&&(!c||el.dataset.check===c)&&(!t||el.textContent.toLowerCase().includes(t));
el.hidden=!ok;if(ok)n++});tiles.forEach(x=>x.classList.toggle('off',off.has(x.dataset.sev)));empty.hidden=n>0}
tiles.forEach(x=>x.addEventListener('click',()=>{const s=x.dataset.sev;off.has(s)?off.delete(s):off.add(s);apply()}));
q.addEventListener('input',apply);chk.addEventListener('change',apply);apply();
"""


def to_html(findings: List[Finding], meta: dict) -> str:
    e = html.escape
    counts = summary(findings)
    worst = max((f.severity for f in findings), default=Severity.INFO)
    out = ['<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           "<title>電腦安全掃描報告</title><style>%s</style></head><body><div class='wrap'>" % _HTML_CSS,
           "<h1>電腦安全掃描報告</h1>",
           "<div class='sub'>%s · %s · %s 完成（%s 秒）%s</div>" % (
               e(meta["hostname"]), e(meta["platform"]), e(meta["finished"]), meta["duration_sec"],
               "" if meta.get("admin") else " · 非系統管理員"),
           "<div class='verdict v-%s'>%s</div>" % (worst.name.lower(), e(verdict(findings))),
           "<div class='tiles'>"]
    for s in sorted(Severity, reverse=True):
        out.append("<div class='tile t-%s' data-sev='%s' title='點一下顯示／隱藏'><b>%d</b><span>%s</span></div>" % (
            s.name.lower(), s.name.lower(), counts[s.name.lower()], s.label))
    out.append("</div><div class='bar'><input id='q' placeholder='搜尋路徑、名稱、原因…'>"
               "<select id='chk'><option value=''>全部類別</option>")
    for key, label in CHECK_LABELS.items():
        out.append("<option value='%s'>%s</option>" % (key, label))
    out.append("</select></div>")

    for f in sort_findings(findings):
        sev = f.severity.name.lower()
        out.append("<article class='f' data-sev='%s' data-check='%s'><header>" % (sev, e(f.check)))
        out.append("<span class='sev s-%s'>%s</span><span class='chk'>%s</span><span class='title'>%s</span>" % (
            sev, f.severity.label, e(CHECK_LABELS.get(f.check, f.check)), e(f.title)))
        out.append("</header><div class='target'>%s</div>" % e(visible(f.target)))
        if f.reasons:
            out.append("<ul>%s</ul>" % "".join("<li>%s</li>" % e(r) for r in f.reasons))
        extra = {k: v for k, v in f.details.items() if k not in ("path",)}
        if extra:
            out.append("<details><summary>詳細資料</summary><pre>%s</pre></details>" % e(visible(
                json.dumps(extra, ensure_ascii=False, indent=2, default=str))))
        out.append("</article>")
    out.append("<div id='empty' class='empty' hidden>目前篩選條件下沒有項目（「資訊」預設隱藏，點上方方塊可顯示）。</div>")

    out.append("<div class='box'><h2>接下來怎麼做</h2><ul>%s</ul></div>" % "".join("<li>%s</li>" % e(a) for a in ADVICE))
    out.append("<div class='box'><h2>掃描資訊</h2><div class='muted'>")
    out.append("檢查項目：%s<br>" % e("、".join(CHECK_LABELS.get(c, c) for c in meta["checks"])))
    if meta.get("scan_roots"):
        out.append("掃描資料夾：%s<br>" % e("；".join(meta["scan_roots"])))
    out.append("已知惡意雜湊數量：%d<br>" % meta.get("known_hashes", 0))
    for note in meta.get("notes", []):
        out.append("註：%s<br>" % e(note))
    out.append("本工具以特徵與經驗法則判斷「可疑」，不等於確定是病毒；也無法取代正式防毒軟體。</div></div>")
    out.append("</div><script>%s</script></body></html>" % _HTML_JS)
    return "".join(out)
