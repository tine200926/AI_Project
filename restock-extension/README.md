# 補貨通知 for Weverse Shop（Chrome 擴充功能）

`weverse-restock`（PowerShell + LINE）的 Chrome 擴充功能版。不需要 LINE、不需要伺服器、不需要工作排程：
Chrome 開著就會定時檢查，補貨時跳出桌面通知，點通知直接打開商品頁。

判斷邏輯與 PowerShell 版相同：

- 從商品頁 HTML 裡的 `__NEXT_DATA__` 讀每個選項的 `isSoldOut`，不需登入。
- **商品層級的狀態優先**：`out.of.stock` / `end` / `closed` 等狀態下，所有選項都視為售完（選項層級的旗標會過期）。
- 選項名稱比對忽略大小寫與標點，依「完全相同 → 開頭相符 → 包含」嘗試，所以 `o.c.l` 對得到 `O.C.L.`。
- 依網址幣別自動帶地區 cookie（`JPY`→`JP`、`KRW`→`KR`、`USD`→`US`、`TWD`→`TW`），可以逐項覆寫國家。
- 只在「上次售完 → 這次有貨」時通知，同一選項有冷卻時間（預設 180 分鐘）。

## 免費安裝給自己用（不用上架）

1. 下載或 `git clone` 這個資料夾。
2. Chrome 打開 `chrome://extensions`，右上角打開「開發人員模式」。
3. 按「載入未封裝項目」，選 `restock-extension` 資料夾。
4. 會自動打開設定頁：貼上商品網址 → 讀取選項 → 勾選 → 加入監測。
5. 按「測試通知」確認看得到通知。Windows 沒跳出來的話，到「設定 → 系統 → 通知」確認 Google Chrome 是開啟的。

> 從 PowerShell 版搬過來：設定頁的「匯入／匯出」可以直接貼上 `config.json`，只會讀 `targets`（LINE token 會被忽略）。

## 檔案

| 檔案 | 用途 |
|---|---|
| `manifest.json` | Manifest V3 設定 |
| `background.js` | Service worker：排程、抓頁面、判斷補貨、通知、圖示徽章 |
| `lib/core.js` | 解析頁面、比對選項、判斷狀態（不依賴 Chrome API，可用 node 測試） |
| `lib/store.js` | `chrome.storage.local` 的讀寫與預設值 |
| `popup.html/js` | 工具列小視窗：每個商品的庫存一覽、立即檢查 |
| `options.html/js` | 設定頁：新增／編輯／暫停／移除商品、設定、匯入匯出、紀錄 |
| `tests/` | `core.test.mjs` 單元測試、`e2e.mjs` 用 Playwright 載入擴充功能跑完整流程 |
| `tools/` | 產生圖示、商店圖片，以及打包上架用的 zip |
| `store/` | 商店截圖與宣傳圖 |
| `PRIVACY.md` / `STORE_LISTING.md` | 隱私權政策、上架要填的文字 |

## 限制

- **Chrome 要開著**。電腦睡眠、關機或 Chrome 完全關閉時不會檢查；需要 24 小時監測的話，PowerShell 版加工作排程比較適合。
- 檢查間隔最短 1 分鐘。熱門商品不建議設太短，以免被網站限制。

## 測試

```bash
node --test tests/core.test.mjs
```

```bash
node tests/e2e.mjs
```

`e2e.mjs` 會起一個本機的假商品頁伺服器，把真正的擴充功能載進 Chromium，驗證：讀取選項、第一次不通知、地區 cookie、商品層級狀態優先、補貨通知、徽章數字、冷卻時間、錯誤通知只發一次、暫停、排程間隔、設定頁渲染、匯入 PowerShell 版 `config.json`。

## 發佈到 Chrome Web Store

**費用：一次性 US$5 開發者註冊費**，之後上架、更新都免費。其餘（主機、隱私權政策網頁）都可以用免費方案。

1. **打包**
   ```bash
   python3 tools/pack.py
   ```
   產生 `dist/restock-extension-<版本>.zip`（`manifest.json` 在 zip 最上層，只包執行需要的檔案）。

2. **註冊開發者帳號**：到 [Chrome Web Store 開發人員資訊主頁](https://chrome.google.com/webstore/devconsole)，用 Google 帳號登入（需開啟兩步驟驗證），同意條款並付 US$5。建議填一個公開用的聯絡信箱並完成驗證。

3. **公開隱私權政策（免費）**，擇一：
   - repo 是公開的：直接用 GitHub 上 `PRIVACY.md` 的網址。
   - repo 是私人的：把 `PRIVACY.md` 內容貼到一份 Google 文件，「共用 → 知道連結的任何人都能檢視」，用那個網址。

4. **新增項目**：資訊主頁按「新增項目」，上傳 zip。

5. **填資料**：照 `STORE_LISTING.md` 複製貼上「商店資訊」「隱私權做法」「發佈」三個分頁；圖片用 `icons/icon128.png` 和 `store/` 裡的三張。

6. **送審**：可見度建議先選「不公開」，只有拿到連結的人能安裝。審查通常幾天，有時一到兩週。

7. **之後更新**：改 `manifest.json` 的 `version`（例如 `1.0.1`）→ 重新 `pack.py` → 在資訊主頁「套件 → 上傳新套件」→ 送審。

**容易被退件的地方**（已經處理過，修改時注意別破壞）：
- 權限只要必要的；主機權限只限 `https://shop.weverse.io/*`。
- 不用 Weverse 的 logo，名稱寫「for Weverse Shop」並在說明註明非官方，避免被當成冒用。
- 不載入遠端程式碼（所有 JS 都在套件內）。
- 截圖要能看出實際功能。

要重新產生圖片：

```bash
python3 tools/make_icons.py
```

```bash
node tests/e2e.mjs --screenshots store/raw && python3 tools/make_store_assets.py
```
