"""掃描結果的資料結構。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List


class Severity(IntEnum):
    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @property
    def label(self) -> str:
        return SEVERITY_LABELS[self]

    @classmethod
    def parse(cls, text: str) -> "Severity":
        key = text.strip().upper()
        for sev in cls:
            if sev.name == key or SEVERITY_LABELS[sev] == text.strip():
                return sev
        raise ValueError("未知的嚴重度：%s（可用 info / low / medium / high / critical）" % text)


SEVERITY_LABELS = {
    Severity.INFO: "資訊",
    Severity.LOW: "低",
    Severity.MEDIUM: "中",
    Severity.HIGH: "高",
    Severity.CRITICAL: "嚴重",
}

# 分數 → 嚴重度。分數由各項可疑跡象的權重加總而來（見 rules.py / scoring.py）。
SCORE_THRESHOLDS = (
    (9, Severity.CRITICAL),
    (6, Severity.HIGH),
    (4, Severity.MEDIUM),
    (2, Severity.LOW),
)


def severity_from_score(score: int) -> Severity:
    for threshold, sev in SCORE_THRESHOLDS:
        if score >= threshold:
            return sev
    return Severity.INFO


# 執行順序：快的先跑，檔案掃描最後
CHECK_LABELS = {
    "protection": "防護狀態",
    "persistence": "開機自動執行",
    "processes": "執行中程序",
    "network": "網路連線",
    "files": "檔案",
}


@dataclass
class Finding:
    check: str                 # files / persistence / processes / network / protection
    severity: Severity
    title: str                 # 一句話說明是什麼
    target: str                # 檔案路徑、登錄機碼、PID…
    reasons: List[str] = field(default_factory=list)
    score: int = 0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check": self.check,
            "check_label": CHECK_LABELS.get(self.check, self.check),
            "severity": self.severity.name.lower(),
            "severity_label": self.severity.label,
            "score": self.score,
            "title": self.title,
            "target": self.target,
            "reasons": list(self.reasons),
            "details": self.details,
        }


class Scored:
    """累加可疑跡象的小工具：每個跡象有權重與中文說明。"""

    def __init__(self) -> None:
        self.score = 0
        self.reasons: List[str] = []
        self.evidence: List[str] = []
        self.rule_ids: List[str] = []
        self.forced: Severity | None = None

    def add(self, weight: int, reason: str, evidence: str | None = None, rule_id: str | None = None) -> None:
        self.score += weight
        if reason and reason not in self.reasons:
            self.reasons.append(reason)
        if evidence:
            self.evidence.append(evidence)
        if rule_id:
            self.rule_ids.append(rule_id)

    def note(self, reason: str) -> None:
        """不加分、只附註的說明（例如「有效數位簽章」）。"""
        if reason not in self.reasons:
            self.reasons.append(reason)

    def force(self, sev: Severity) -> None:
        if self.forced is None or sev > self.forced:
            self.forced = sev

    def extend(self, other: "Scored") -> None:
        self.score += other.score
        for r in other.reasons:
            if r not in self.reasons:
                self.reasons.append(r)
        self.evidence.extend(other.evidence)
        self.rule_ids.extend(other.rule_ids)
        if other.forced is not None:
            self.force(other.forced)

    @property
    def severity(self) -> Severity:
        sev = severity_from_score(self.score)
        if self.forced is not None and self.forced > sev:
            return self.forced
        return sev
