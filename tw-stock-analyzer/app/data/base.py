"""資料來源的共用型別。

每一筆送到前端或 AI Agent 的資料都附帶來源、資料日期與是否為模擬資料，
讓使用者（與模型）能分辨「查到的數字」與「推估／缺漏」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

from ..config import settings

USER_AGENT = "tw-stock-analyzer/1.0 (+https://github.com/)"


class DataUnavailable(RuntimeError):
    """所有資料源都取不到資料。訊息會原樣顯示給使用者，不會用猜的數字補上。"""


@dataclass
class Sourced:
    data: Any
    source: str
    as_of: str | None = None
    url: str | None = None
    is_demo: bool = False
    notes: list[str] = field(default_factory=list)

    def meta(self) -> dict:
        return {
            "source": self.source,
            "as_of": self.as_of,
            "url": self.url,
            "is_demo": self.is_demo,
            "notes": self.notes,
        }


def http_get(url: str, params: dict | None = None, **kw) -> requests.Response:
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    headers.update(kw.pop("headers", {}))
    resp = requests.get(url, params=params, headers=headers, timeout=settings.http_timeout, **kw)
    resp.raise_for_status()
    return resp


def to_float(value: Any) -> float | None:
    """把 TWSE 常見的字串數字（含千分位、'--'、'X0.00'、空字串）轉成 float。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in {"", "-", "--", "---", "N/A", "null", "None"}:
        return None
    if text[:1] in {"X", "+"}:
        text = text[1:]
    try:
        return float(text)
    except ValueError:
        return None


def pick(record: dict, *candidates: str, exclude: tuple[str, ...] = ()) -> Any:
    """依候選欄位名取值：先完全相符，再找「包含」該字串的欄位。

    TWSE OpenAPI 的中文欄位名偶有全形／半形括號差異或改版，用包含比對比較耐用。
    """
    for name in candidates:
        if name in record:
            return record[name]
    for name in candidates:
        for key, value in record.items():
            if name in key and not any(ex in key for ex in exclude):
                return value
    return None
