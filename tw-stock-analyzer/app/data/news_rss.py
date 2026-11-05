"""Google News RSS（繁中、台灣版）：用於個股與大盤的每日新聞標題。"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote

from .base import DataUnavailable, Sourced, http_get

URL = "https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"


def parse_rss(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        # Google News 標題結尾會附「 - 媒體名稱」
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].strip()
        published = item.findtext("pubDate") or ""
        try:
            published = parsedate_to_datetime(published).isoformat()
        except (TypeError, ValueError):
            pass
        items.append({
            "title": title,
            "link": (item.findtext("link") or "").strip(),
            "source": source,
            "published": published,
            "summary": (item.findtext("description") or "").strip(),
        })
    return items


def search(query: str, days: int = 7) -> Sourced:
    url = URL.format(q=quote(f"{query} when:{days}d"))
    try:
        items = parse_rss(http_get(url).text)
    except Exception as exc:
        raise DataUnavailable(f"Google News RSS「{query}」取得失敗：{exc}") from exc
    return Sourced(items, "Google News RSS", None, url)
