# 三大法人連續買賣超追蹤

追蹤上市個股外資、投信、自營商**連續買超／賣超 3、5、10 日以上**的靜態網站。
資料來源為臺灣證券交易所買賣超彙總表（TWT38U 外資及陸資、TWT44U 投信、TWT43U 自營商），由 GitHub Actions 每天 18:30（台北時間）自動更新並發佈到 GitHub Pages。

## 檔案結構

```
scripts/update.py              抓資料、算連續天數（只用標準函式庫）
docs/index.html                網站（篩選＋表格）
docs/data/streaks.json         前端讀的資料（自動產生）
data/raw/YYYYMMDD.json         每日原始買賣超（自動產生，保留 20 個交易日）
data/closed_days.json          已確認的休市日，避免重複查詢
.github/workflows/update.yml   每天 18:30 排程
```

## 部署（約 5 分鐘）

1. 在 GitHub 建一個新 repo，把整個資料夾推上去。
2. repo 的 **Settings → Pages → Build and deployment → Source** 選 **GitHub Actions**。
3. **Actions** 分頁 → 「更新三大法人資料」→ **Run workflow** 手動跑第一次。
   第一次會補抓最近 20 個交易日，約 3–5 分鐘。
4. 完成後網址是 `https://<你的帳號>.github.io/<repo 名稱>/`。

之後每天自動更新；沒有新資料（假日）時網頁的「更新於」不會變。

## 準時在 18:30 更新（外部排程）

GitHub 內建排程是「盡量」執行，實測常延遲 3～6 小時。要準時，改由 [cron-job.org](https://cron-job.org)（免費）
每天 18:30 呼叫 GitHub API 觸發更新，觸發後約 1 分鐘網站就更新。GitHub 排程保留當備援。

1. **建立 GitHub token**：GitHub → Settings → Developer settings → Personal access tokens →
   **Fine-grained tokens** → Generate new token
   - Repository access：Only select repositories → `twse-inst-streak`
   - Permissions → Repository permissions → **Actions：Read and write**（其他都不用）
   - Expiration 設長一點；到期前 GitHub 會寄信提醒，到時換新 token 貼回 cron-job.org 即可
2. **在 cron-job.org 建立排程**（Create cronjob）
   - URL：`https://api.github.com/repos/YenIzel/twse-inst-streak/actions/workflows/update.yml/dispatches`
   - Execution schedule：Every day，18:30，Time zone 選 **Asia/Taipei**
   - Advanced → Request method：**POST**
   - Advanced → Headers：
     - `Authorization: Bearer <剛才的 token>`
     - `Accept: application/vnd.github+json`
     - `Content-Type: application/json`
   - Advanced → Request body：`{"ref":"main"}`
3. 按 **Test run**，回應 `204 No Content` 就成功了，GitHub Actions 頁面會出現一筆 workflow_dispatch 的執行。

## 本機執行

```bash
python scripts/update.py                 # 需要 Python 3.9+
python -m http.server 8000 -d docs       # 開 http://localhost:8000
```

直接雙擊 `index.html` 讀不到 JSON，要用網頁伺服器開。

## 計算規則

- **連續買超 N 日**：從最新交易日往回，該法人每天買賣超股數都 > 0，且連續天數 ≥ N。賣超同理（< 0）。
  當天沒出現在證交所表上視為 0，會中斷連續。
- 排除權證與牛熊證（03～08 開頭的 6 碼代號），保留股票、ETF、TDR。
- **多選法人**：預設「同時符合」（例如外資＋投信同步連買），可切換「任一符合」。
- **外資**取「外資及陸資」合計欄（含外資自營商）；**自營商**取「自行買賣＋避險」合計欄。
- **累計張數**＝最近 N 日買賣超股數加總 ÷ 1000，四捨五入。
- 連續天數最多算到保留的 20 個交易日，顯示為「≥20 日」。要更長就改 `update.py` 的 `HISTORY`。
- 某天抓取失敗時，只用失敗日之後的連續資料計算（避免連續天數算錯），下次執行自動補抓。
- 買賣超表回傳「無資料」時，先用證交所每月成交資訊（FMTQIK）確認是否真的休市才記為休市日，
  避免證交所偶發的空回應讓某個交易日被永久跳過。

網址會記住篩選條件，例如 `?side=sell&inst=foreign,trust&days=5`，可以直接加書籤。

## 注意事項

- 證交所有流量限制，腳本每次請求間隔約 3 秒，請不要調太快，否則 IP 會被暫時封鎖。
- 若證交所擋掉 GitHub 的伺服器（抓取一直失敗），可改在自己電腦用排程（Windows 工作排程器／cron）
  每天 18:30 跑 `update.py` 再 `git push`。
