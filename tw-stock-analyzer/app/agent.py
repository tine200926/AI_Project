"""AI Agent：讓 Claude 透過工具查資料、跑選股與回測，再整理成回答。

避免「憑記憶編造數字」的設計：
1. 模型手上沒有任何市場數字，只能呼叫下列工具取得（資料來源與日期一併回傳）。
2. 系統提示要求每個數字都要能在工具結果中找到，查不到就說查無資料。
3. 回傳給前端的結果附上完整的工具呼叫紀錄，使用者可以逐一核對。
"""
from __future__ import annotations

import json

from .config import settings
from .data.base import DataUnavailable

SYSTEM_PROMPT = """你是台股量化研究助理，替使用者查資料、做多因子選股與回測，並用繁體中文回答。

資料規則（最重要）：
- 你對市場行情、財報、新聞一律「不知道」，任何股價、本益比、營收、報酬率、指標數值都必須來自本次對話中的工具結果。不可以用訓練資料或記憶補數字，也不可以自行推估後當成事實。
- 引用數字時標明資料來源與資料日期（工具結果中的 source / as_of）。
- 工具回傳錯誤或缺漏時，直接說明「查無資料」與原因，不要猜。
- 工具結果中 is_demo 為 true 時，開頭必須明說「目前為模擬資料，數字不代表真實市場」。

分析規則：
- 推薦或評論個股時，一併列出風險等級、風險旗標與停損參考價（來自工具結果）。
- 提到回測績效時，同時說明樣本外表現、交易成本假設與存活者偏差等限制；回測好不代表未來會賺。
- 新聞面只採用工具過濾後的新聞；被過濾的投顧灌水內容不要當成依據。
- 不保證獲利、不喊單、不提供任何未公開資訊。結尾附一句「以上為量化研究資訊，非投資建議」。

回答方式：先給結論，再列重點依據（條列、精簡），需要時用表格比較。"""

FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


def _dump(obj, limit: int = 12000) -> str:
    text = json.dumps(obj, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…（結果過長已截斷）"


def _safe(fn):
    try:
        return _dump(fn())
    except (DataUnavailable, ValueError, KeyError) as exc:
        return _dump({"error": str(exc)})


def _tools():
    from anthropic import beta_tool

    from . import service

    @beta_tool
    def market_overview() -> str:
        """取得大盤狀態：以 0050 判斷多頭／空頭／盤整，以及目前建議的主流選股策略與 0050 技術面。"""
        def run():
            o = service.overview()
            return {"regime": o["regime"], "benchmark": o.get("benchmark"), "presets": o["presets"]}
        return _safe(run)

    @beta_tool
    def screen_stocks(preset: str = "auto", top_n: int = 10, codes: str = "") -> str:
        """多因子選股排名，已排除處置／注意股與低流動性股，每檔附風險等級與停損參考。

        Args:
            preset: auto（依大盤狀態自動選）、momentum、value_growth、dividend、defensive、chips。
            top_n: 回傳前幾名（1-30）。
            codes: 自訂選股池，逗號分隔的股票代號；空字串代表預設的權值股池。
        """
        from .data.universe import parse_universe
        return _safe(lambda: service.screen(preset, parse_universe(codes) if codes else None,
                                            max(1, min(int(top_n), 30))))

    @beta_tool
    def analyze_stock(code: str) -> str:
        """個股完整分析：技術面訊號、基本面財報摘要、風險控管（停損、部位）、新聞輿情、法人買賣超。

        Args:
            code: 股票代號，例如 2330。
        """
        def run():
            r = service.stock(code)
            r.pop("chart", None)
            senti = r.get("sentiment") or {}
            if "articles" in senti:
                senti["articles"] = senti["articles"][:8]
                senti["filtered"] = [{"title": f.get("title"), "reason": f.get("reason")} for f in senti.get("filtered", [])[:5]]
            rev = (r.get("fundamentals") or {}).get("revenue") or {}
            rev.pop("series", None)
            return r
        return _safe(run)

    @beta_tool
    def market_news_sentiment() -> str:
        """今日台股財經新聞：過濾投顧灌水與重複報導後的多空分數、利多／利空焦點與被排除的新聞。"""
        def run():
            r = service.market_news()
            r["articles"] = r["articles"][:15]
            r["filtered"] = [{"title": f.get("title"), "reason": f.get("reason")} for f in r["filtered"][:8]]
            return r
        return _safe(run)

    @beta_tool
    def backtest_factor_strategy(preset: str = "momentum", years: float = 5, top_n: int = 10) -> str:
        """多因子策略歷史回測（月調倉、等權、含手續費與證交稅），回傳績效、與 0050 比較、樣本內外績效。

        Args:
            preset: momentum、value_growth、dividend、defensive、chips。
            years: 回測年數（1-8）。
            top_n: 每月持有檔數（3-20）。
        """
        def run():
            r = service.backtest_factor(preset, years=max(1.0, min(float(years), 8.0)),
                                        top_n=max(3, min(int(top_n), 20)))
            r.pop("curve", None)
            r["holdings"] = r["holdings"][-3:]
            return r
        return _safe(run)

    @beta_tool
    def backtest_stock_strategy(code: str, strategy: str = "ma_cross", years: float = 5, stop_atr: float = 2.5) -> str:
        """單一個股技術策略回測（次日開盤成交、含交易成本與 ATR 移動停損），並與買進持有比較。

        Args:
            code: 股票代號。
            strategy: ma_cross（均線交叉）、macd、rsi（逆勢）、breakout（通道突破）。
            years: 回測年數（1-8）。
            stop_atr: ATR 移動停損倍數，0 代表不設停損。
        """
        def run():
            r = service.backtest_strategy(code, strategy, years=max(1.0, min(float(years), 8.0)),
                                          stop_atr=float(stop_atr) or None)
            r.pop("curve", None)
            r["trades"] = r["trades"][-8:]
            return r
        return _safe(run)

    return [market_overview, screen_stocks, analyze_stock, market_news_sentiment,
            backtest_factor_strategy, backtest_stock_strategy]


def _client():
    import anthropic
    return anthropic.Anthropic()


def _extra(model: str) -> dict:
    if model in FALLBACK_MODELS:
        # 安全分類器拒答時，由伺服器依拒答類別自動改用適合的模型重跑
        return {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    return {}


def _error_text(exc: Exception) -> str:
    import anthropic
    if isinstance(exc, anthropic.AuthenticationError):
        return "Anthropic API 金鑰無效，請檢查 ANTHROPIC_API_KEY。"
    if isinstance(exc, anthropic.RateLimitError):
        return "Anthropic API 呼叫太頻繁，請稍後再試。"
    if isinstance(exc, anthropic.APIStatusError):
        return f"Anthropic API 錯誤（HTTP {exc.status_code}）：{exc.message}"
    if isinstance(exc, anthropic.APIConnectionError):
        return "無法連線到 Anthropic API。"
    raise exc


def ask(question: str, history: list[dict] | None = None) -> dict:
    if not settings.ai_enabled:
        return {"error": "尚未設定 ANTHROPIC_API_KEY，AI Agent 未啟用。其他功能不受影響。"}
    messages = [{"role": m["role"], "content": str(m["content"])}
                for m in (history or [])[-10:] if m.get("role") in ("user", "assistant") and m.get("content")]
    messages.append({"role": "user", "content": question})
    try:
        runner = _client().beta.messages.tool_runner(
            model=settings.ai_model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=_tools(),
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            max_iterations=10,
            **_extra(settings.ai_model),
        )
        calls, final = [], None
        for message in runner:
            final = message
            for block in message.content:
                if block.type == "tool_use":
                    calls.append({"tool": block.name, "input": block.input})
    except Exception as exc:  # noqa: BLE001 — 轉成使用者看得懂的錯誤訊息
        return {"error": _error_text(exc)}
    if final is None:
        return {"error": "模型沒有回應"}
    if final.stop_reason == "refusal":
        return {"error": "模型拒絕回答此問題。", "tool_calls": calls}
    text = "\n".join(b.text for b in final.content if b.type == "text").strip()
    return {"answer": text, "tool_calls": calls, "model": final.model, "stop_reason": final.stop_reason}


DIGEST_PROMPT = """以下是已經過濾投顧灌水與重複轉載後的台股新聞標題（含來源、規則式多空分數）。
請用繁體中文寫「今日財經新聞快速解讀」：
1. 三到五點重點（每點一句），標註對應的新聞來源。
2. 整體多空傾向與主要驅動因素。
3. 需要留意的風險事件。
限制：只能使用下列標題中出現的資訊與數字，不得加入標題沒有的數字、公司或事件；標題資訊不足就直說。"""


def news_digest(analysis: dict) -> dict:
    if not settings.ai_enabled:
        return {"error": "尚未設定 ANTHROPIC_API_KEY"}
    lines = [f"- [{a['source']}] {a['title']}（規則分數 {a['score']:+.2f}，轉載 {a['coverage']} 家）"
             for a in analysis.get("articles", [])[:40]]
    if not lines:
        return {"error": "沒有可解讀的新聞"}
    try:
        resp = _client().beta.messages.create(
            model=settings.ai_model,
            max_tokens=4000,
            system=DIGEST_PROMPT,
            messages=[{"role": "user", "content": "\n".join(lines)}],
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
            **_extra(settings.ai_model),
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": _error_text(exc)}
    if resp.stop_reason == "refusal":
        return {"error": "模型拒絕回答。"}
    return {"digest": "\n".join(b.text for b in resp.content if b.type == "text").strip(), "model": resp.model}
