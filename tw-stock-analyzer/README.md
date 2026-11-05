# 台股量化分析網站

上市股票（TWSE）的研究網站，包含：依市場主流策略做多因子選股、風險控管標註、基本面與財報摘要、新聞輿情過濾、技術面查詢、策略回測，以及 AI Agent 問答。

**核心原則：所有數字都來自實際抓到的資料，不讓聊天機器人憑記憶編數字。**

- 選股、回測、財報摘要、新聞計分都是 Python 程式算出來的，不經過語言模型。
- 每個區塊都會標出資料來源與資料日期。抓不到的資料會寫「取得失敗」或「缺少」，不會拿推估值補上，也不會偷偷改用模擬資料。
- AI Agent 只能透過工具（選股、個股分析、新聞、回測）拿資料。回答下方附有完整的工具呼叫紀錄，可以逐一核對。

> 本專案僅供量化研究與資訊整理，不構成投資建議。過去績效不代表未來報酬。

## 快速開始

```bash
cd tw-stock-analyzer
pip install -r requirements.txt
cp .env.example .env           # 視需要填入 FINMIND_TOKEN / ANTHROPIC_API_KEY
uvicorn app.main:app --reload
```

打開 http://127.0.0.1:8000

- 沒網路或想先看介面：在 `.env` 設 `TWSA_DATA_MODE=demo`。畫面頂端會出現「模擬資料」橫幅，所有數字都是亂數產生的。
- 第一次產生推薦或跑回測時，要逐檔下載歷史價格，大約 30 秒到 1 分鐘。之後同一天內會使用 `.cache/` 的快取。

## 功能

| 分頁 | 內容 |
|---|---|
| 今日推薦 | 用 0050 對 200 日線判斷多頭、空頭或盤整，自動選出「目前市場主流」策略，對選股池做多因子排名。每檔附綜合分數、入選理由（實際數值與池內排名）、弱項、技術面多空、風險等級與停損價。處置股、注意股與低流動性股會先排除並列出原因。 |
| 個股分析 | K 線、均線、KD；技術訊號拆成逐項的多空分數；基本面（月營收年增與連續成長、毛利率變化、近四季 EPS／ROE、負債比、本益比、殖利率）；風險控管（波動、回撤、Beta、流動性、ATR 停損、2R 目標、依風險預算算出幾張幾股）；新聞輿情；三大法人；綜合判讀。 |
| 新聞輿情 | 大盤新聞的過濾與量化。可勾選 AI 快速解讀，模型只能根據過濾後的標題摘要。 |
| 策略回測 | ① 多因子月調倉回測（與 0050 比較）；② 個股技術策略回測（均線交叉、MACD、RSI 逆勢、海龜通道突破，加 ATR 移動停損）。兩者都附樣本內／樣本外績效。 |
| AI Agent | 用自然語言提問，Claude 會自己呼叫上述工具並整理答案。需要 `ANTHROPIC_API_KEY`。 |

### 選股策略（`app/analysis/factors.py`）

| 策略 | 因子權重 | 何時是主流 |
|---|---|---|
| 動能趨勢 | 6 月動能 35%、12 月動能 15%、距半年線 20%、營收年增 20%、低波動 10% | 0050 在 200 日線之上且均線上揚 |
| 價值成長 | 本益比 25%、淨值比 10%、營收年增 30%、ROE 15%、6 月動能 20% | 盤整 |
| 低波動防禦 | 低波動 45%、殖利率 20%、ROE 15%、距半年線 20% | 0050 在 200 日線之下且均線下彎 |
| 高股息存股 | 殖利率 45%、低波動 25%、淨值比 10%、ROE 20% | 手動選擇 |
| 法人籌碼 | 法人 20 日買超/均量 40%、6 月動能 30%、營收年增 30% | 手動選擇（要逐檔抓法人資料，較慢） |

計分方式：每個因子先在選股池內取百分位，再去掉頭尾 5% 的極端值；虧損股的本益比視為最差；個股缺值給中性分數 0.5；整個池都缺的因子會被剔除，權重重新分配，畫面上會註明。

### 風險控管（`app/analysis/risk.py`）

- 風險旗標：處置股、注意股、60 日年化波動 > 40% / 60%、近一年回撤 > 25% / 40%、20 日均成交值 < 5,000 萬 / 1,000 萬、Beta > 1.5、近四季虧損、非金融業負債比 > 75%、資料缺漏。
- 依旗標加總得到「低／中／高／極高」四個等級。**處置／注意清單取不到時會標示「無法確認」，不會當成沒有警示。**
- 停損價 = 收盤 − N×ATR（預設 N = 2）；移動停損 = 進場後最高收盤 − N×ATR。
- 部位大小：停損時最多虧損「資金 × 單筆風險 %」，且單一部位不超過資金的 10%。結果換算成「張 + 零股」。

### 新聞過濾（`app/analysis/sentiment.py`）

1. **投顧灌水／招攬**：報明牌、加 LINE、保證獲利、必漲、飆股名單、老師帶單、會員招攬、主力內線、急迫性話術，以及部落格或群組類來源，一律排除，並列出命中的規則。
2. **重複轉載**：標題相似（bigram Jaccard > 0.6）、數字相同、開頭主詞相同時只算一次，轉載家數記為熱度。不同公司的同型標題不會被合併。
3. **加權**：來源可信度（中央社、經濟日報、工商時報、鉅亨等權重較高）× 與個股的相關度 × 時間衰減（半衰期 3 天）。
4. **計分**：用財經多空詞典計分，處理否定詞（例如「未成長」算利空）。命中太少時可信度標為「低」，綜合判讀會忽略這一項。

### 回測的真實性設定（`app/analysis/backtest.py`）

- 訊號在 t 日收盤產生：多因子在 t+1 日收盤調倉，個股策略在 t+1 日開盤成交。
- 成本：手續費 0.1425%（可設折扣），賣出證交稅 0.3%（ETF 為 0.1%），單邊滑價 0.1%。
- 月營收要到次月 10 日（法定公布期限）之後才會使用。有測試確認竄改未來資料不會影響當期因子。
- 回測期間取不到歷史值的因子（ROE、法人；本益比可選擇開啟）會被剔除並註明。
- 報告前 70% 與後 30% 的績效；樣本外 Sharpe 遠低於樣本內時，會提示可能過度擬合。
- **已知限制**：選股池是「現在」的權值股，有存活者偏差，績效會偏樂觀；也沒有歷史的處置／注意清單可以排除。

## 資料來源

| 資料 | 主要來源 | 備援 |
|---|---|---|
| 歷史日線 | FinLab（有 token 才用）→ Yahoo Finance（還原權息） | FinMind `TaiwanStockPrice`（未還原） |
| 當日行情、本益比／淨值比／殖利率 | TWSE OpenAPI `STOCK_DAY_ALL`、`BWIBBU_ALL` | — |
| 月營收 | TWSE `t187ap05_L`（最新月）、FinMind `TaiwanStockMonthRevenue`（歷史） | — |
| 季財報 | FinMind `TaiwanStockFinancialStatements` + `TaiwanStockBalanceSheet` | TWSE `t187ap06_L_ci` / `t187ap07_L_ci`（只有最近一季的累計數） |
| 三大法人、本益比歷史 | FinMind `TaiwanStockInstitutionalInvestorsBuySell`、`TaiwanStockPER` | — |
| 處置／注意股票 | TWSE `/announcement/punish`、`/announcement/notice` | — |
| 新聞 | FinMind `TaiwanStockNews`、Google News RSS | — |

TWSE 的中文欄位名偶爾會改版，所以解析時用「欄位名包含」的方式比對（`app/data/base.py:pick`）。上線前建議先實際呼叫一次各端點，確認欄位與 `tests/test_parsers.py` 的樣本相符。處置／注意股的兩個端點路徑尤其需要確認；取不到時畫面會標示「無法確認」。

目前只涵蓋上市股票；上櫃（TPEx）股票的當日資料不在 TWSE 端點中，畫面上會提示。

## AI Agent（`app/agent.py`）

- 使用 Anthropic Python SDK 的 tool runner。預設模型 `claude-opus-5-5`（可用 `TWSA_AI_MODEL` 更換），開啟 adaptive thinking，effort 為 medium。
- 開啟伺服器端 refusal fallback（`fallbacks: "default"`）：模型的安全分類器誤擋時，會自動改用適合的模型重跑。不需要的話，刪掉 `agent._extra()` 即可。
- 系統提示要求：數字只能來自工具結果，並標註來源與日期；查不到就說查無資料；模擬資料必須明說；推薦時要附風險與停損，回測要說明限制。
- 工具：`market_overview`、`screen_stocks`、`analyze_stock`、`market_news_sentiment`、`backtest_factor_strategy`、`backtest_stock_strategy`。

## API

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/overview` | 大盤狀態、可用策略與因子 |
| GET | `/api/screen?preset=auto&top_n=10&universe=2330,2317` | 多因子推薦 |
| GET | `/api/stock/{code}?capital=1000000&risk_pct=1&atr_mult=2` | 個股完整分析 |
| GET | `/api/news/market?days=1&ai=false` | 大盤新聞輿情 |
| POST | `/api/backtest/factor` | `{"preset":"momentum","years":5,"top_n":10}` |
| POST | `/api/backtest/strategy` | `{"code":"2330","strategy":"breakout","stop_atr":2.5}` |
| POST | `/api/agent` | `{"question":"...","history":[...]}` |

## 測試

```bash
python -m pytest -q
```

測試在模擬資料模式下執行，不需要網路。涵蓋：指標正確性、技術訊號、灌水與重複新聞過濾、否定詞、風險等級與部位上限、因子方向與缺值處理、**回測無偷看未來**、營收公布延遲、交易成本、各資料源的解析、所有 API 端點，以及 Agent 的工具迴圈接線。

## 檔案

| 路徑 | 用途 |
|---|---|
| `app/main.py` | FastAPI 路由 |
| `app/service.py` | 把資料與分析組合成各頁的結果 |
| `app/agent.py` | Claude AI Agent 與新聞 AI 解讀 |
| `app/data/` | 各資料源（`twse` / `finmind` / `yahoo` / `news_rss` / `finlab_src` / `demo`）、`provider.py`（備援與快取）、`universe.py`（預設 45 檔選股池） |
| `app/analysis/` | `indicators` 指標、`technical` 技術訊號、`fundamentals` 財報摘要、`risk` 風險控管、`sentiment` 新聞輿情、`factors` 因子與策略、`backtest` 回測 |
| `static/` | 前端（原生 JS；圖表用內附的 TradingView Lightweight Charts v4.2.0，Apache-2.0） |
| `tests/` | pytest |
