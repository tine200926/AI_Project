# Weverse Shop 補貨監測 + LINE 通知

監測多個 Weverse Shop 商品頁的任意選項，當選項從「售完」變成「有庫存」時用 LINE 推訊息給你。
也可以**直接在 LINE 裡傳訊息新增／移除監測商品**（需要 Cloudflare Tunnel，見下）。

## 運作方式

Weverse 的商品頁是 Next.js，整頁庫存資料直接寫在 HTML 的 `__NEXT_DATA__` 裡：

```json
{"saleStockId":81279,"saleOptionName":"O.C.L.","isSoldOut":true, ...}
```

所以一次普通的 HTTP GET 就能拿到每個選項的庫存，**不需要登入、不需要無頭瀏覽器**。狀態記在 `state.json`，只在「上次售完 → 這次有貨」的那一刻才發通知。

### ⚠️ 兩層狀態：為什麼不能只看 `isSoldOut`

選項層級的 `isSoldOut` **會過期**。實際遇過的例子：

```
CXM Jacket:  商品 status = SOLD_OUT    但選項 isSoldOut = false
Plush Toy:   商品 status = SOLD_OUT    但 13 個選項有 8 個 isSoldOut = false
```

這兩個商品實際上都買不到。所以判斷邏輯是**商品層級的 `statusCode` 優先**：

| `sale.status.*` | 處理 |
|---|---|
| `sale` | 可購買 → 依選項的 `isSoldOut` 判斷 |
| `out.of.stock` / `end` / `closed` / `stop` / `to.be.sold` | 全部選項視為無貨 |
| 其他（未知） | 退回選項旗標，並在 log 留 `WARN` |

未知狀態刻意**不**當成無貨 —— 萬一 Weverse 改了狀態詞彙，監測應該繼續運作並發出警告，而不是永久靜默。

另外 `goodsOrderLimit.availableQuantity` **不是庫存**，那是單筆訂單的購買上限，別拿來判斷。

## ⚠️ 要用 PowerShell 7，不是 5.1

腳本用到 PowerShell 7 的語法（三元運算子 `? :` 等），**Windows PowerShell 5.1 連解析都會失敗**，會噴一堆 `Unexpected token '?'`。

Windows 預設的「Windows PowerShell」是 5.1。最簡單的辦法是用本資料夾裡的 `.cmd` 啟動器 —— 它們會自動找到 pwsh 7 並切換到正確目錄，所以**從任何 shell、任何目錄執行都可以**，雙擊也行：

```
restock.cmd -Status        等同  pwsh .\weverse-restock.ps1 -Status
restock.cmd -TestNotify
restock.cmd                        跑一次檢查
webhook.cmd                        啟動 LINE webhook server
tunnel.cmd                         開 Cloudflare 隧道
setup-task.cmd                     註冊工作排程
setup-task.cmd -Uninstall
```

要直接跑 `.ps1` 的話，記得用 `pwsh` 開頭，不要用 `powershell`：

```powershell
pwsh -File .\weverse-restock.ps1 -Status
```

沒有 pwsh 的話：`winget install --id Microsoft.PowerShell`

## 檔案

| 檔案 | 用途 |
|---|---|
| `restock.cmd` / `webhook.cmd` / `tunnel.cmd` / `setup-task.cmd` | 啟動器，自動用 pwsh 7 並切到正確目錄 |
| `weverse-restock.ps1` | 監測主程式 |
| `webhook-server.ps1` | LINE webhook：用聊天指令管理監測清單 |
| `lib.ps1` | 兩者共用的抓取／設定／LINE 邏輯 |
| `install-task.ps1` | 註冊／移除工作排程 |
| `install-tunnel.ps1` | 安裝 cloudflared 並開隧道 |
| `google-sheet/Code.gs` | 貼到 Google 試算表的 Apps Script，讓試算表管理監測清單（選用） |
| `config.json` | 你的設定（含 token，不要外流） |
| `state.json` | 上次看到的庫存與通知時間（自動產生） |
| `sheet-cache.json` | 試算表模式下最後一次成功讀到的內容，Google 連不上時備用（自動產生） |
| `restock.log` / `webhook.log` | 執行紀錄，自動輪替（見下方「log 會不會太大」） |

---

# 一、監測設定

## 多個商品、多個選項

`config.json` 的 `targets` 是陣列，一個排程任務會掃完全部：

```json
{
  "targets": [
    {
      "label": "Face Plush Keyring",
      "url": "https://shop.weverse.io/zh-tw/shop/KRW/artists/7/sales/42095",
      "watch": ["O.C.L", "TAMTAM"]
    },
    {
      "label": "ACRYLIC STAND",
      "url": "https://shop.weverse.io/en/shop/KRW/artists/7/sales/41000",
      "watch": ["*"]
    }
  ],
  "line": { "channelAccessToken": "...", "channelSecret": "...", "to": "U..." },
  "cooldownMinutes": 180
}
```

- `watch` 填 `["*"]` = 監測該頁所有選項。
- **地區限定商品**（日本限定等）會視請求來源國家而決定要不要給資料。程式會從網址的幣別推斷國家（`JPY`→`JP`、`KRW`→`KR`、`USD`→`US`、`TWD`→`TW`）並送出對應 cookie，所以一般不用特別設定。推斷不出來或商品限定其他地區時，在該 target 加 `"country": "JP"` 覆寫。
- 選項名稱比對會忽略大小寫、空白、標點，並依「完全相同 → 開頭相符 → 包含」的順序嘗試。所以 `"O.C.L"` 對得到 `O.C.L.`，`"SUNGHO"` 對得到 `012 SUNGHO`。
- 同一個頁面可以列兩次（監測不同選項），`state.json` 會自動用 `42095`、`42095-2` 區分。

## 常用指令

```
restock.cmd -Status        # 列出每個商品所有選項的庫存，不改任何東西
restock.cmd                # 跑一次檢查
restock.cmd -TestNotify    # 測 LINE 通不通
restock.cmd -Loop          # 開著終端機持續跑
```

## 讓它固定跑

```
setup-task.cmd                     # 每 5 分鐘
setup-task.cmd -IntervalMinutes 2  # 熱門商品調密一點
setup-task.cmd -Uninstall          # 移除
```

## 設定欄位

| 欄位 | 說明 |
|---|---|
| `targets[].url` | 商品頁網址 |
| `targets[].label` | 通知訊息裡顯示的名稱 |
| `targets[].watch` | 要監測的選項名稱陣列，或 `["*"]` |
| `targets[].country` | 選填。地區限定商品的國家代碼（如 `"JP"`）。留空＝從網址幣別推斷 |
| `sheet.url` / `sheet.key` | 選填。填了就改用 Google 試算表管理 `targets` 與部分參數，見下一節 |
| `line.channelAccessToken` | Messaging API 分頁的 long-lived token（很長，170+ 字元） |
| `line.channelSecret` | Basic settings 分頁的 Channel secret（32 字元）。**只有 webhook 需要** |
| `line.to` | 收通知的 User ID。同時也是 webhook 的寄件者白名單 |
| `intervalSeconds` | `-Loop` 模式的間隔，預設 300 |
| `cooldownMinutes` | 同一選項發過通知後多久內不重複發，預設 180 |
| `notifyOnFirstRun` | 第一次就看到有貨要不要通知，預設 `false` |
| `notifyOnSoldOut` | 又賣完時要不要也通知，預設 `false` |
| `notifyOnError` | 監測本身壞掉時要不要通知，預設 `true` |
| `politeDelayMs` | 多商品之間的請求間隔，預設 1500 |
| `webhookPort` | webhook 監聽的本機埠號，預設 8787 |
| `logMaxMB` | 單一 log 檔上限（MB），預設 1。超過就輪替 |
| `logKeep` | 保留幾個舊檔（`.1`～`.N`），預設 3。總上限＝`logMaxMB × (logKeep+1)` |
| `logEveryOption` | 預設 `false`。設 `true` 會每輪記錄每個選項（除錯用，很吵） |

## 用 Google 試算表管理監測清單（選用）

不想改 JSON 的話，可以把監測清單和參數放在 Google 試算表，在手機或電腦上直接改。**每次檢查都會重新讀取，改完不用重開任何東西。**

- `config.json` 填了 `sheet.url` 後，`targets` 一律以試算表為準（`config.json` 裡的 `targets` 會被忽略）。
- LINE 的「加」「刪」指令會直接寫進試算表。
- LINE token / secret **不會**放到試算表，仍然只在本機的 `config.json`。
- Google 暫時連不上時，會用上次成功讀到的內容（`sheet-cache.json`）繼續監測，並在 log 留 `WARN`。

### 試算表長這樣

**`targets` 工作表**（第一列是標題，欄位順序可以調；標題也可以寫成 `啟用`／`名稱`／`網址`／`選項`／`國家`）

| enabled | label | url | watch | country | note |
|---|---|---|---|---|---|
| ☑ | Face Plush Keyring | https://shop.weverse.io/zh-tw/shop/KRW/artists/7/sales/42095 | O.C.L, TAMTAM | | |
| ☐ | ACRYLIC STAND | https://shop.weverse.io/en/shop/KRW/artists/7/sales/41000 | * | | 先暫停 |

- `enabled`：取消勾選就暫停監測，不用刪列。
- `watch`：多個選項用逗號分隔（`,`、`，`、`、` 都可以），留空或 `*` = 全部選項。
- `country`、`label`、`note` 可留空。

**`settings` 工作表**（`key` / `value` 兩欄，沒列出的就用 `config.json` 或預設值）

可調整：`cooldownMinutes`、`notifyOnFirstRun`、`notifyOnSoldOut`、`notifyOnError`、`intervalSeconds`、`requestTimeoutSec`、`maxRetries`、`politeDelayMs`、`logEveryOption`。
`webhookPort`、`logMaxMB`、`logKeep` 要重開程式才生效，所以只能在 `config.json` 設定。

> 排程模式（`setup-task.cmd`）多久跑一次是工作排程器決定的，要改請重跑 `setup-task.cmd -IntervalMinutes N`；試算表的 `intervalSeconds` 只影響 `-Loop` 模式。

### 設定步驟

1. 開一個新的 Google 試算表。
2. 選單「擴充功能 → Apps Script」，把預設內容全部刪掉，貼上 `google-sheet/Code.gs` 的內容。
3. 把最上面的 `SECRET` 改成一串夠長的亂數（例如在 PowerShell 跑 `[guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')`），存檔。
4. 函式選單選 `setup` → 執行，第一次會要求授權（選自己的帳號 → 進階 → 前往專案 → 允許）。回到試算表會看到 `targets` 和 `settings` 兩個工作表。
5. 「部署 → 新增部署作業」→ 類型選「網頁應用程式」：
   - 執行身分：**我**
   - 存取權：**任何人**
   - 按「部署」，複製網址（`https://script.google.com/macros/s/…/exec`）。
6. 在 `config.json` 加上：
   ```json
   "sheet": {
     "url": "https://script.google.com/macros/s/…/exec",
     "key": "跟第 3 步一樣的 SECRET"
   }
   ```
7. 跑 `restock.cmd -Status` 確認讀得到試算表裡的商品。

**之後改了 `Code.gs`**，要「部署 → 管理部署作業 → 編輯 → 版本選『新版本』」，網址才會用到新程式碼（網址不變）。只改試算表內容不用重新部署。

**安全性**：「任何人」存取的意思是知道網址的人都能呼叫，但沒有正確的 `key` 只會拿到錯誤訊息。`key` 跟 LINE token 一樣不要外流；萬一外流，改 `SECRET` 再部署新版本即可。

---

# 二、用 LINE 訊息管理監測清單

## 為什麼需要隧道

LINE Messaging API **沒有「拉取訊息」的 API**。Bot 要收到你說的話，只能靠 LINE 主動把事件 POST 到一個**公開的 HTTPS 網址**。你的電腦在 NAT 後面沒有公開網址，所以需要 Cloudflare Tunnel 把本機的 `http://localhost:8787` 開一個對外網址。

**代價**：電腦關機或隧道中斷，就收不到指令（但**排程的補貨監測不受影響**，那個不需要隧道）。

## 設定步驟

### 1. 補上 channel secret

webhook 會用它驗證每個請求的簽章，沒有它不會啟動。
LINE console → 你的 channel → **Basic settings** → **Channel secret**（32 字元），填進 `config.json` 的 `line.channelSecret`。

### 2. 啟動 webhook server

```
webhook.cmd
```

### 3. 另開一個視窗啟動隧道

```
tunnel.cmd
```

第一次會自動安裝 cloudflared（優先用 winget，失敗則直接下載 exe 到本資料夾）。接著會印出一行像這樣的網址：

```
https://random-words-here.trycloudflare.com
```

### 4. 把 webhook URL 填進 LINE

LINE console → **Messaging API** 分頁 → **Webhook URL** 填：

```
https://random-words-here.trycloudflare.com/webhook
```

按 **Verify** 應該回成功，然後把 **Use webhook** 打開。

> ⚠️ 快速隧道的網址**每次重啟都會變**，得重新貼一次。要固定網址就得有 Cloudflare 帳號 + 自己的網域：
> ```powershell
> cloudflared tunnel login
> .\install-tunnel.ps1 -Named weverse -Hostname weverse.你的網域.com
> ```
> 這種具名隧道可以安裝成 Windows 服務，開機自動起來。
> （`tunnel.cmd -Named weverse -Hostname weverse.你的網域.com` 也可以）

## 聊天指令

| 指令 | 說明 |
|---|---|
| `加 <網址> [選項1,選項2]` | 新增監測。省略選項＝監測全部。也可以**直接貼網址**不打「加」 |
| `列表` | 列出目前監測的商品與 id |
| `刪 <id>` | 移除某個商品（id 從「列表」查） |
| `狀態 [id]` | 現在的庫存狀況 |
| `說明` | 指令一覽 |

英文別名：`add` / `list` / `del` / `status` / `help`。選項分隔可用 `,`、`，`、`、`。

範例：

```
加 https://shop.weverse.io/en/shop/KRW/artists/7/sales/41000 SUNGHO,TAESAN
```

Bot 會自動抓商品名稱當 label，並立刻回報那些選項現在的庫存。

## 安全性

- **每個請求都驗證 `X-Line-Signature`**（HMAC-SHA256 + channel secret，常數時間比對）。所以隧道網址外洩也無法改你的設定。
- **只接受 `line.to` 這個 User ID 的訊息**，別人找到 Bot 也沒用。
- server 只綁 `localhost`，不直接對外；對外只有隧道那一條路。

---

# 三、行為細節

- **只在狀態翻轉時通知。** 一直有貨不會每 5 分鐘吵你一次。
- **一次掃描的所有補貨合併成一則訊息。** LINE 免費方案每月只有 **200 則 push**，10 個商品分開發很快就燒完。webhook 的回覆用的是 reply API，**不計入** push 額度。
- **推送失敗會自動重試。** 該發通知但 LINE 推失敗（token 過期、斷網）時，狀態不會被更新成「已有貨」，下次檢查會再試，不會因一次失敗就錯過補貨。
- **單一頁面掛掉不影響其他頁面。** 某個商品下架或連不上時，其餘商品照常檢查，異常只通知一次（受 `cooldownMinutes` 限制）。
- **選項消失只警告、不當錯誤。** `watch` 的名字找不到時，log 會列出該頁目前所有選項名稱，方便對照修正。
- **`-Loop` 模式每輪重讀 config**，所以用 LINE 新增的商品不必重啟就會生效。排程模式本來每次都是新進程，自然也會讀到。

## log 會不會太大

不會，有硬上限，**不需要自己刪**。

輪替規則：`restock.log` 超過 `logMaxMB` 就改名成 `.1`，舊的依序往後推到 `.N`，最舊的刪掉。所以磁碟用量上限是 `logMaxMB × (logKeep + 1)`，預設 **4 MB**。

寫入量（實測 4 商品 / 41 個選項、每 5 分鐘一次）：

| 記錄方式 | 每輪行數 | 每天 | 4 MB 可存 |
|---|---|---|---|
| 每個選項都記（舊行為） | 41 | 0.8 MB | 約 5 天 |
| **只記變化 + 每輪摘要**（現行） | 5 | 0.1 MB | **約 40 天** |

現行 log 長這樣 —— 一行一個商品，證明它跑過了：

```
[Face Plush Keyring] status=sale | 13 watched | 2 available
[Plush Toy] status=out.of.stock | 13 watched | 0 available
```

逐項紀錄只在**狀態改變**時出現（補貨、賣完、新增監測），所以重要事件不會被淹沒。要看完整逐項輸出就把 `logEveryOption` 設成 `true`，或直接跑 `restock.cmd -Status`。

商品加很多時記得調整：10 個商品約每天 0.25 MB，4 MB 仍可存 2 週以上；真的想留更久就加大 `logMaxMB` 或 `logKeep`。

# 常見問題

**`無法辨識 '.\install-task.ps1' 詞彙...`**
兩個可能同時發生：(1) 目前目錄不在本資料夾 —— `.\` 是相對路徑；(2) 你用的是 Windows PowerShell 5.1。用 `setup-task.cmd` 取代即可，它兩個問題都繞過。辨別方法：5.1 的錯誤訊息寫「無法辨識 ... 詞彙」，PowerShell 7 寫「無法將 ... 詞彙辨識為」。

**`Unexpected token '?' in expression or statement`**
用 5.1 跑了 `.ps1`。改用 `.cmd` 啟動器，或 `pwsh -File .\xxx.ps1`。

**LINE 回 `Authentication failed`**
`channelAccessToken` 填錯了。它在 **Messaging API** 分頁最下方，長度 170+ 字元；**Basic settings** 分頁那個 32 字元的是 Channel secret，是 webhook 要用的另一個值，兩者不能互換。

**LINE 回 403 / `Invalid to`**
`line.to` 不對，或你還沒用 Messaging API 分頁的 QR code 把 Bot 加為好友。

**webhook Verify 失敗**
依序確認：`webhook-server.ps1` 還在跑、隧道視窗沒關、URL 結尾有加 `/webhook`、`channelSecret` 填的是 Basic settings 那個 32 字元的值。瀏覽器直接開隧道網址應該看到 `weverse-restock webhook is alive`。

**`page returned no product data ... not viewable from the configured country`**
地區限定商品，而推斷出的國家不對。Weverse 對這類商品會回 HTTP 200 但把商品資料整個拿掉。在該 target 加上正確的 `"country"`（例如日本限定就填 `"JP"`）。

實作細節：`wes_display_user_country` 和 `wes_order_user_country` **兩個 cookie 都必須設**，只設前者仍會回「無法以您設定的國家／地區查看」的空殼頁。

**檢查頻率要多密？**
Weverse 沒有公開的頻率限制，但別太誇張。2–5 分鐘對補貨來說足夠；監測很多商品時記得 `politeDelayMs` 會讓一輪變久。
