"""掃描時共用的狀態：設定、病毒碼雜湊庫、白名單、簽章快取、程序清單。"""
from __future__ import annotations

import fnmatch
import ipaddress
import os
import re
import sys
from typing import Callable, Dict, Iterable, List, Optional, Set

from .model import Finding, Scored, Severity
from .scoring import SIGNABLE_EXT, Locations, signature_score
from .util import authenticode, norm

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
TOOL_DIR = os.path.dirname(PACKAGE_DIR)
DATA_DIR = os.path.join(TOOL_DIR, "data")
HASH_DIR = os.path.join(DATA_DIR, "hashes")

_HEX = re.compile(r"\b([0-9a-fA-F]{64}|[0-9a-fA-F]{40}|[0-9a-fA-F]{32})\b")


def load_hashes(paths: Iterable[str]) -> Set[str]:
    """讀取雜湊清單：每行一個 MD5／SHA1／SHA256，# 開頭為註解；也接受 CSV。"""
    result: Set[str] = set()
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    for m in _HEX.finditer(line):
                        result.add(m.group(1).lower())
        except OSError as exc:
            print("無法讀取雜湊清單 %s：%s" % (path, exc), file=sys.stderr)
    return result


def default_hash_files() -> List[str]:
    if not os.path.isdir(HASH_DIR):
        return []
    return sorted(
        os.path.join(HASH_DIR, f) for f in os.listdir(HASH_DIR)
        if f.lower().endswith((".txt", ".csv"))
    )


def load_ips(paths: Iterable[str]) -> Set[str]:
    result: Set[str] = set()
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    token = line.split("#", 1)[0].strip().split(",")[0].strip()
                    if not token:
                        continue
                    try:
                        result.add(str(ipaddress.ip_address(token)))
                    except ValueError:
                        continue
        except OSError as exc:
            print("無法讀取 IP 清單 %s：%s" % (path, exc), file=sys.stderr)
    return result


class Allowlist:
    """白名單：每行一個路徑萬用字元（* ?）或檔案 SHA256。符合的項目不會出現在報告中。"""

    def __init__(self, path: Optional[str] = None) -> None:
        self.globs: List[str] = []
        self.hashes: Set[str] = set()
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if re.fullmatch(r"[0-9a-fA-F]{64}", line):
                        self.hashes.add(line.lower())
                    else:
                        self.globs.append(os.path.normcase(line))

    def matches(self, finding: Finding) -> bool:
        sha = str(finding.details.get("sha256", "")).lower()
        if sha and sha in self.hashes:
            return True
        candidates = [finding.target, finding.details.get("path", ""), finding.details.get("executable", "")]
        for cand in candidates:
            if not cand:
                continue
            c = os.path.normcase(str(cand))
            for g in self.globs:
                if fnmatch.fnmatchcase(c, g):
                    return True
        return False


class ScanContext:
    def __init__(
        self,
        hashes: Optional[Set[str]] = None,
        bad_ips: Optional[Set[str]] = None,
        allowlist: Optional[Allowlist] = None,
        check_signatures: bool = True,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.locations = Locations()
        self.hashes = hashes or set()
        self.bad_ips = bad_ips or set()
        self.allowlist = allowlist or Allowlist()
        self.check_signatures = check_signatures
        self.log = log or (lambda msg: None)
        self.notes: List[str] = []          # 掃描過程的提醒（例如需要系統管理員權限）
        self._sig_cache: Dict[str, dict] = {}
        self._processes: Optional[List[dict]] = None
        self.self_dirs = [norm(TOOL_DIR)]

    # -------------------------------------------------- 數位簽章
    def prefetch_signatures(self, paths: Iterable[str]) -> None:
        if not self.check_signatures:
            return
        todo = []
        for p in paths:
            if not p or os.path.splitext(p)[1].lower() not in SIGNABLE_EXT:
                continue
            n = norm(p)
            if n in self._sig_cache or not os.path.isfile(p):
                continue
            if self.locations.is_system(p):
                continue
            todo.append(p)
            self._sig_cache[n] = {}
        if not todo:
            return
        self.log("檢查 %d 個檔案的數位簽章…" % len(todo))
        self._sig_cache.update(authenticode(todo))

    def signature(self, path: str) -> Scored:
        if not self.check_signatures or not path:
            return Scored()
        return signature_score(self._sig_cache.get(norm(path)))

    def signature_info(self, path: str) -> Optional[dict]:
        return self._sig_cache.get(norm(path)) or None

    # -------------------------------------------------- 程序清單（processes / network 共用）
    def processes(self) -> List[dict]:
        if self._processes is None:
            from .checks.processes import collect_processes
            self._processes = collect_processes()
        return self._processes

    def is_self(self, path: str) -> bool:
        n = norm(path)
        return any(n == d or n.startswith(d + os.sep) for d in self.self_dirs)

    def note(self, msg: str) -> None:
        if msg not in self.notes:
            self.notes.append(msg)


def make_finding(check: str, title: str, target: str, scored: Scored, **details) -> Finding:
    d = {k: v for k, v in details.items() if v not in (None, "", [], {})}
    if scored.evidence:
        d.setdefault("evidence", list(dict.fromkeys(scored.evidence))[:5])
    if scored.rule_ids:
        d.setdefault("rules", list(dict.fromkeys(scored.rule_ids)))
    return Finding(
        check=check,
        severity=scored.severity,
        title=title,
        target=target,
        reasons=list(scored.reasons),
        score=scored.score,
        details=d,
    )


def simple_finding(check: str, severity: Severity, title: str, target: str, reasons: List[str], score: int = 0, **details) -> Finding:
    d = {k: v for k, v in details.items() if v not in (None, "", [], {})}
    return Finding(check=check, severity=severity, title=title, target=target, reasons=reasons, score=score, details=d)
