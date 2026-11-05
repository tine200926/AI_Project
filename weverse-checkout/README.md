# Weverse Shop 快速結帳（搶購）

把多個商品（含選項、數量）一次組成結帳網址，直接在瀏覽器開啟 Weverse Shop 結帳頁。
原理與官網購物車「結帳」按鈕相同：`/order/checkout?saleItems=[{saleStockId, quantity}, ...]`

## 檔案

| 檔案 | 用途 |
|---|---|
| `weverse_checkout.py` | 主程式（需要 Python 3，Windows 用 `py` 執行） |
| `items.txt` | 商品清單 |
| `list.bat` | **先跑這個**：列出清單中每個商品的所有選項（編號 / 名稱 / saleStockId） |
| `run.bat` | 立即開啟結帳頁 |
| `開搶.bat` | 等到指定時間，每秒檢查，開賣就開啟結帳頁（在檔案內設定 `AT`、`REF`） |

## 單選、下拉選單、按鈕：要怎麼設定？

**三種的設定方式完全一樣。** 商品頁上的選項不論長成單選、下拉選單還是按鈕，
每一個可購買的組合最後都對應到一個 `saleStockId`，結帳網址只需要它。程式會把商品頁裡所有
選項（包含多層的按鈕分組，例如「顏色」按鈕 × 「尺寸」按鈕）攤平成一張清單，例如：

```
     1. BLACK / M    [@201]
     2. BLACK / L    [@202]
     3. WHITE / M    [@203]
     4. WHITE / L    (售完)  [@204]
```

`items.txt` 每行一個商品：`網址 | 選項:數量 | 選項:數量`，選項可以這樣寫：

| 寫法 | 意思 |
|---|---|
| `#2:2` 或 `2:2` | 第 2 個選項買 2 個（編號依 `list.bat` 的順序；`2:2` 只有在沒有選項剛好叫「2」時才當編號） |
| `A ver.:1` | 選項名稱（完全相同優先，否則唯一的部分符合） |
| `BLACK+M:1` | **多組按鈕/下拉**：每個關鍵字都要出現在選項名稱裡（`+` 前後可有空白） |
| `@201:1` | 直接指定 saleStockId —— 最準確，選項名稱怪或重複時用這個 |
| `*` | 執行時再互動選擇 |
| `網址` / `網址 \| 2` | 無選項商品，數量 1 / 2 |

範例：

```
https://shop.weverse.io/zh-tw/shop/KRW/artists/3/sales/71109 | 2:2 | 3:1 | 4:1
https://shop.weverse.io/zh-tw/shop/KRW/artists/3/sales/00000 | BLACK+M:1 | WHITE+L:1
https://shop.weverse.io/zh-tw/shop/KRW/artists/3/sales/00000 | @201:1
```

如果某個關鍵字符合多個選項（例如只寫 `M`，BLACK / M 和 WHITE / M 都符合），程式會列出來並略過該項，
請多加關鍵字或改用編號 / `@saleStockId`。

### 小技巧：開賣前頁面還看不到選項

開賣前商品頁可能讀不到選項。若你事先知道 saleStockId（例如之前補貨時用 `list.bat` 查過），
用 `@saleStockId:數量` 寫在清單裡，即使頁面讀不到也能直接組出結帳網址。

## 指令列用法

```bash
py weverse_checkout.py items.txt --list                 # 列出所有選項
py weverse_checkout.py items.txt                        # 立即開啟結帳頁
py weverse_checkout.py items.txt --at 20:00 --refresh 1 # 20:00 開始每秒檢查，直到可購買
py weverse_checkout.py items.txt --refresh 3 --partial  # 任一項可購買就先結帳
py weverse_checkout.py items.txt --no-open              # 只印出網址
```

> 預設瀏覽器要先登入 Weverse 帳號。同一張結帳單的商品必須是同一個幣別/商店（網址中的 `KRW` 等）。
