# 隱私權政策 — 補貨通知 for Weverse Shop

最後更新：2026-11-04

本擴充功能**不蒐集、不傳送、不販售**任何個人資料。

## 會儲存什麼

以下資料只存在你自己瀏覽器的 `chrome.storage.local`，不會離開你的電腦：

- 你加入的商品網址、名稱、要監測的選項
- 你的設定（檢查間隔、冷卻時間等）
- 每個選項上次看到的庫存狀態，以及最近的執行紀錄

移除擴充功能時，這些資料會一併刪除。

## 會連到哪裡

只會連到 `https://shop.weverse.io/` 上**你自己加入的商品頁**，用一般的 HTTP GET 讀取公開的庫存資訊。
為了讀取地區限定商品，這些請求會附上國家／幣別 cookie（例如 `wes_display_user_country=JP`）；這個設定只套用在擴充功能自己發出的背景請求，不會影響你平常瀏覽 Weverse Shop。

不會連到任何其他伺服器，沒有分析工具、廣告或追蹤。

## 權限用途

| 權限 | 用途 |
|---|---|
| `alarms` | 定時檢查商品頁 |
| `storage` | 在本機儲存監測清單、設定與庫存狀態 |
| `notifications` | 補貨時顯示桌面通知 |
| `declarativeNetRequestWithHostAccess` | 為背景請求附上地區 cookie，以讀取地區限定商品 |
| `https://shop.weverse.io/*` | 讀取商品頁的庫存資訊 |

## 聯絡

有問題請到專案的 GitHub Issues 回報，或寄信到開發者在 Chrome Web Store 上公開的聯絡信箱。

本擴充功能與 Weverse、HYBE 無任何關係，也未獲其授權或背書。
