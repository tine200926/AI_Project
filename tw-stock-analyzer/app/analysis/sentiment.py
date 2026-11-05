"""新聞輿情：過濾投顧灌水／重複／不相關標題，再以財經詞典量化多空。

流程：
1. 灌水偵測：報明牌、加 LINE、保證獲利、飆股名單等話術 → 直接排除並列出原因。
2. 去重：標題字元 bigram 的 Jaccard 相似度 > 0.6、數字相同且開頭主詞相同，視為同一則（轉載），只計一次，
   但記錄「轉載家數」作為熱度。
3. 加權：來源可信度 × 與個股相關度 × 時間衰減（半衰期 3 天）。
4. 計分：詞典比對（含否定詞翻轉），每則 -1~+1，加權平均後換算成 -100~+100。
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

PROMO_PATTERNS = [
    (r"報.?明?牌|明牌", "報明牌"),
    (r"加\s*(入)?\s*LINE|LINE\s*(群|好友|@)|賴群", "導流 LINE 群組"),
    (r"(免費|限時|立即).{0,4}(領取|加入|諮詢|名額|試用)", "限時／免費招攬"),
    (r"保證(獲利|賺|翻倍)|穩賺|包賺|必賺|零風險", "保證獲利話術"),
    (r"必(漲|噴|飆)|明天.{0,3}(漲停|大漲)", "保證上漲話術"),
    (r"飆股(名單|清單)?|下一檔.{0,6}(飆股|翻倍)|翻倍股", "飆股推銷"),
    (r"老師|分析師.{0,4}(帶單|帶你)|帶單|跟單", "帶單／老師"),
    (r"會員|VIP|訂閱.{0,4}(方案|解鎖)", "會員招攬"),
    (r"主力(進場|大買|鎖碼)|內線|神準", "主力／內線話術"),
    (r"錯過.{0,4}(再等|後悔)|最後機會|倒數", "急迫性話術"),
]
PROMO_SOURCES = ("投顧", "部落格", "達人", "群組", "論壇", "PTT", "Dcard", "粉專", "YouTube")

SOURCE_WEIGHT = [
    (("公開資訊觀測站", "證交所", "櫃買中心", "金管會", "中央社", "Reuters", "路透", "Bloomberg", "彭博"), 1.0),
    (("經濟日報", "工商時報", "鉅亨", "MoneyDJ", "財訊", "今周刊", "商業周刊", "天下", "數位時代", "CMoney"), 0.95),
    (("自由", "聯合", "中時", "ETtoday", "Yahoo", "TVBS", "三立", "東森", "風傳媒", "新頭殼", "Nownews"), 0.75),
]
UNKNOWN_SOURCE_WEIGHT = 0.55

POSITIVE = {
    "創新高": 2, "新高": 1.5, "成長": 1, "年增": 1, "月增": 0.5, "增加": 0.5, "看好": 1, "樂觀": 1,
    "調升": 1.5, "上修": 1.5, "利多": 1.5, "暢旺": 1.5, "強勁": 1, "擴產": 1, "接單": 1, "訂單": 0.5,
    "轉盈": 2, "獲利": 0.5, "買超": 1, "加碼": 1, "漲價": 1, "大漲": 1, "漲停": 1, "回升": 1, "反彈": 0.5,
    "優於預期": 2, "超乎預期": 2, "配息": 0.5, "殖利率": 0.3, "受惠": 1, "突破": 0.8, "合作": 0.5,
    "認證": 0.5, "庫藏股": 1, "復甦": 1, "攀升": 1, "旺季": 0.8, "領漲": 1, "續增": 1,
}
NEGATIVE = {
    "下修": 1.5, "調降": 1.5, "衰退": 1.5, "年減": 1, "月減": 0.5, "減少": 0.5, "虧損": 2, "轉虧": 2,
    "利空": 1.5, "砍單": 2, "疑慮": 1, "承壓": 1, "賣超": 1, "減碼": 1, "大跌": 1, "跌停": 1.5,
    "重挫": 1.5, "跌破": 1, "低於預期": 2, "不如預期": 2, "延後": 1, "停工": 1.5, "違約": 2.5,
    "掏空": 3, "裁員": 1.5, "罰款": 1.5, "處置": 1.5, "下滑": 1, "疲弱": 1, "淡季": 0.8, "警示": 1.5,
    "減資": 1, "訴訟": 1.5, "調查": 1, "跳票": 3, "示警": 1, "崩": 1.5, "殺盤": 1.5, "保守": 0.8,
}
NEGATIONS = ("不", "未", "沒", "無", "非", "免", "否認")
EVENT_TAGS = {
    "營收": "營收", "法說": "法說會", "目標價": "目標價", "財測": "財測", "除息": "除權息", "配息": "股利",
    "擴產": "產能", "資本支出": "資本支出", "外資": "法人動向", "投信": "法人動向", "併購": "併購",
    "處置": "處置／注意", "增資": "籌資", "減資": "減資", "庫藏股": "庫藏股", "訴訟": "法律", "AI": "AI",
}


def promo_reasons(title: str, source: str = "") -> list[str]:
    reasons = [why for pat, why in PROMO_PATTERNS if re.search(pat, title, re.IGNORECASE)]
    if any(s.lower() in (source or "").lower() for s in PROMO_SOURCES):
        reasons.append(f"來源類型（{source}）")
    if title.count("!") + title.count("！") >= 2:
        reasons.append("標題過度煽動")
    return reasons


def source_weight(source: str) -> float:
    for names, w in SOURCE_WEIGHT:
        if any(n.lower() in (source or "").lower() for n in names):
            return w
    return UNKNOWN_SOURCE_WEIGHT


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"[\s\W_]+", "", text.lower())
    return {t[i:i + 2] for i in range(len(t) - 1)} or {t}


def similarity(a: str, b: str) -> float:
    x, y = _bigrams(a), _bigrams(b)
    return len(x & y) / len(x | y) if x | y else 0.0


def is_duplicate(a: str, b: str) -> bool:
    """轉載判定：內容相似、數字相同、且開頭主詞相同（避免把不同公司的同型標題當成重複）。"""
    if similarity(a, b) <= 0.6:
        return False
    if re.findall(r"\d+(?:\.\d+)?", a) != re.findall(r"\d+(?:\.\d+)?", b):
        return False
    lead_a = _bigrams(re.sub(r"^[〈【\[(（].{1,8}?[〉】\])）]", "", a)[:4])
    lead_b = _bigrams(re.sub(r"^[〈【\[(（].{1,8}?[〉】\])）]", "", b)[:4])
    return bool(lead_a & lead_b)


def lexicon_score(text: str) -> tuple[float, list[str]]:
    hits, total, mag = [], 0.0, 0.0
    for lex, sign in ((POSITIVE, 1), (NEGATIVE, -1)):
        for word, w in lex.items():
            for m in re.finditer(re.escape(word), text):
                before = text[max(0, m.start() - 2): m.start()]
                s = -sign if any(n in before for n in NEGATIONS) else sign
                total += s * w
                mag += w
                hits.append(("+" if s > 0 else "−") + word)
    if mag == 0:
        return 0.0, hits
    return max(-1.0, min(1.0, total / max(mag, 2.0))), hits


def _age_days(published: str, now: datetime) -> float | None:
    if not published:
        return None
    try:
        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(published[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    return max(0.0, (now - dt).total_seconds() / 86400)


def analyze(items: list[dict], keywords: list[str] | None = None, now: datetime | None = None,
            half_life_days: float = 3.0) -> dict:
    now = now or datetime.now(timezone.utc)
    keywords = [k for k in (keywords or []) if k]
    kept: list[dict] = []
    filtered: list[dict] = []

    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        reasons = promo_reasons(title, it.get("source", ""))
        if reasons:
            filtered.append({**it, "reason": "疑似投顧灌水／招攬：" + "、".join(reasons)})
            continue
        dup = next((k for k in kept if is_duplicate(k["title"], title)), None)
        if dup is not None:
            dup["coverage"] += 1
            dup.setdefault("also_from", []).append(it.get("source", ""))
            filtered.append({**it, "reason": f"重複報導（同「{dup['title'][:18]}…」）"})
            continue
        kept.append({**it, "title": title, "coverage": 1})

    scored = []
    for it in kept:
        s, hits = lexicon_score(it["title"] + " " + re.sub(r"<[^>]+>", "", it.get("summary", ""))[:200])
        relevance = 1.0 if not keywords or any(k in it["title"] for k in keywords) else 0.5
        age = _age_days(it.get("published", ""), now)
        decay = 0.5 ** (age / half_life_days) if age is not None else 0.5
        weight = source_weight(it.get("source", "")) * relevance * decay * (1 + 0.15 * math.log(it["coverage"]))
        tags = sorted({tag for kw, tag in EVENT_TAGS.items() if kw in it["title"]})
        scored.append({
            "title": it["title"], "link": it.get("link", ""), "source": it.get("source", ""),
            "published": it.get("published", ""), "score": round(s, 3), "weight": round(weight, 3),
            "hits": hits, "tags": tags, "coverage": it["coverage"],
            "tone": "pos" if s > 0.15 else "neg" if s < -0.15 else "neu",
        })

    informative = [x for x in scored if x["hits"]]
    wsum = sum(x["weight"] for x in informative)
    score = round(sum(x["score"] * x["weight"] for x in informative) / wsum * 100, 1) if wsum else 0.0
    pos = sum(1 for x in scored if x["tone"] == "pos")
    neg = sum(1 for x in scored if x["tone"] == "neg")
    confidence = "低" if len(informative) < 3 else "中" if len(informative) < 8 else "高"
    label = "偏多" if score >= 25 else "偏空" if score <= -25 else "中性"

    tag_counts: dict[str, int] = {}
    for x in scored:
        for t in x["tags"]:
            tag_counts[t] = tag_counts.get(t, 0) + 1
    scored.sort(key=lambda x: (-abs(x["score"]) * x["weight"], x["title"]))

    return {
        "score": score, "label": label, "confidence": confidence,
        "counts": {"total": len(items), "kept": len(scored), "filtered": len(filtered),
                   "positive": pos, "negative": neg, "neutral": len(scored) - pos - neg},
        "top_tags": sorted(tag_counts.items(), key=lambda kv: -kv[1])[:6],
        "articles": scored,
        "filtered": filtered[:50],
        "digest": digest(scored, score, label),
    }


def digest(scored: list[dict], score: float, label: str) -> list[str]:
    """規則式「快速解讀」：只引用標題原文，不加入任何標題沒有的數字。"""
    if not scored:
        return ["過濾後沒有可用的新聞。"]
    lines = [f"整體輿情 {label}（{score:+.0f}），有效新聞 {len(scored)} 則。"]
    pos = [x for x in scored if x["tone"] == "pos"][:3]
    neg = [x for x in scored if x["tone"] == "neg"][:3]
    if pos:
        lines.append("利多焦點：" + "；".join(f"{x['title']}（{x['source']}）" for x in pos))
    if neg:
        lines.append("利空焦點：" + "；".join(f"{x['title']}（{x['source']}）" for x in neg))
    hot = [x for x in scored if x["coverage"] >= 3]
    if hot:
        lines.append("多家媒體報導：" + "；".join(x["title"] for x in hot[:3]))
    return lines
