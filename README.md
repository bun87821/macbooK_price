# Apple 整修品監控 🔔

每 30 分鐘檢查 Apple 台灣官網整修品區，任何人都可以透過 Telegram 訂閱自己想要的商品條件，上架時會收到通知。

Bot：[@Macbook_price_track_bot](https://t.me/Macbook_price_track_bot)

## 怎麼用（任何人都可以）

跟 Bot 傳訊息即可，不需要有這個 repo 的權限：

- `/watch 關鍵字1,關鍵字2,... [min=容量GB]` — 新增一組監控條件，商品標題要同時包含所有關鍵字才算符合
  例：`/watch 13 吋,MacBook Air,M5 min=512`
- `/list` — 查看目前訂閱的條件
- `/unwatch 編號` — 移除一組條件（編號看 `/list`）
- `/help` — 顯示說明

每人最多可設定 5 組條件。新上架符合條件的商品會主動推送給你；商品售罄下架後會重新納入偵測，之後再上架會重新通知。

## 運作方式

1. GitHub Actions 每 30 分鐘啟動無頭瀏覽器（Playwright），渲染 [整修品頁面](https://www.apple.com/tw/shop/refurbished/mac)
2. 先拉取使用者傳給 Bot 的新訊息（`/watch`、`/list`、`/unwatch` 等指令），更新訂閱清單 `subscriptions.json`
3. 對每個訂閱者的每組條件比對商品標題關鍵字，符合的再抓詳細頁確認容量是否達標
4. 新上架（該使用者還沒被通知過的）才發 Telegram 通知，避免重複轟炸
5. 商品售罄下架後會從該使用者的已通知清單移除，之後再上架會重新通知

## 設定步驟（架設自己的一份）

### 1. 建立 Telegram Bot

1. 在 Telegram 找 [@BotFather](https://t.me/BotFather)，傳 `/newbot`，照指示取名
2. 拿到 Bot Token（長得像 `123456789:ABCdef...`）

### 2. 建立 GitHub Repo 並設定密鑰

1. 把這個 repo 推到你自己的 GitHub（public，才能免費跑排程）
2. Repo 頁面 → Settings → Secrets and variables → Actions → New repository secret
   - `TELEGRAM_BOT_TOKEN`：Bot Token

### 3. 測試

Repo 頁面 → Actions → 「Apple 整修品監控」→ Run workflow 手動跑一次。
看 log 有沒有正常抓到商品數（目前約 21 項），再跟你的 Bot 傳 `/watch` 指令試試。

## 資料存放

- `subscriptions.json`：每個使用者（用 Telegram Chat ID 識別）訂閱的條件，透過 `/watch`、`/unwatch` 指令更新
- `seen.json`：每個使用者已經通知過的商品網址，避免重複通知
- `tg_offset.json`：記錄 Telegram 訊息讀取進度，避免重複處理同一則指令

這些檔案會被 workflow 自動 commit 回這個 **public** repo，代表訂閱者的 Chat ID 會留在 Git 歷史紀錄裡（公開可查），但不會包含姓名、電話等其他個資。

## 注意事項

- **GitHub 會自動停用 60 天沒動靜的 repo 排程**，收到「scheduled workflow disabled」通知信時去 Actions 頁按一下重新啟用就好
- 排程用的是 GitHub 的免費額度，public repo 完全免費
- 如果哪天腳本抓到 0 項商品（Apple 改版），workflow 會失敗，GitHub 會寄信通知你
- 如果 Playwright 下載 Chromium 卡住導致 workflow 超時取消，通常是暫時性的網路問題，等下一次排程通常會自動恢復
