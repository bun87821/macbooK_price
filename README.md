# Apple 整修品監控 🔔

任何人都可以透過 Telegram 訂閱自己想要的 Mac 整修品條件，指令即時回覆，商品每 30 分鐘檢查一次，新上架就通知。

Bot：[@Macbook_price_track_bot](https://t.me/Macbook_price_track_bot)

## 怎麼用（任何人都可以）

跟 Bot 傳訊息即可，不需要有這個 repo 的權限：

- `/watch 關鍵字1,關鍵字2,... [min=容量GB]` — 新增一組監控條件，商品標題要同時包含所有關鍵字才算符合
  例：`/watch 13 吋,MacBook Air,M5 min=512`
- `/list` — 查看目前訂閱的條件
- `/unwatch 編號` — 移除一組條件（編號看 `/list`）
- `/help` — 顯示說明

每人最多可設定 5 組條件。關鍵字比對忽略空格和大小寫（打「13吋」也能對到官網的「13 吋」）。新上架符合條件的商品會主動推送給你；商品售罄下架後會重新納入偵測，之後再上架會重新通知。

## 架構

分成兩塊，資料交會點是 Cloudflare KV：

1. **Cloudflare Worker（`worker/`）— 即時指令**
   Telegram 透過 webhook 把每則訊息即時推給 Worker，Worker 解析 `/watch`、`/list`、`/unwatch` 等指令、立刻回覆，並把每個人的訂閱條件存進 KV（key 是 `chat:<chat_id>`）。
2. **GitHub Actions（`.github/workflows/check.yml`）— 每 30 分鐘的商品檢查**
   從 KV 讀出所有訂閱 → Playwright 渲染 [整修品頁面](https://www.apple.com/tw/shop/refurbished/mac) → 對每個人的條件比對標題、必要時抓商品頁確認容量 → 新上架的發 Telegram 通知 → 把「已通知過」記錄存回 `seen.json`。

KV 讀不到時（還沒設定、暫時故障），Actions 會退回使用 repo 裡的 `subscriptions.json` 快照，通知功能不中斷；KV 是空的（剛部署）時會自動把快照裡的舊訂閱者搬進 KV。

## 設定步驟（架設自己的一份）

### 1. 建立 Telegram Bot

1. 在 Telegram 找 [@BotFather](https://t.me/BotFather)，傳 `/newbot`，照指示取名
2. 拿到 Bot Token（長得像 `123456789:ABCdef...`）

### 2. 部署 Cloudflare Worker（即時回覆）

需要一個免費的 [Cloudflare](https://dash.cloudflare.com/sign-up) 帳號和 Node.js：

```bash
npm install -g wrangler
wrangler login
cd worker
wrangler kv namespace create SUBS      # 把輸出的 id 填進 wrangler.toml
wrangler secret put BOT_TOKEN          # 貼上 Telegram Bot Token
wrangler secret put TG_SECRET          # 自己想一串隨機英數字（webhook 驗證用）
wrangler deploy                        # 記下部署出來的網址
```

然後在瀏覽器開這個網址，跟 Telegram 註冊 webhook（把三個占位換成自己的）：

```
https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=https://<你的worker網址>/webhook&secret_token=<TG_SECRET>
```

看到 `{"ok":true,...}` 就完成了，這時候跟 Bot 傳訊息應該會秒回。

### 3. 設定 GitHub Repo 密鑰（商品檢查）

Repo 頁面 → Settings → Secrets and variables → Actions → New repository secret，共四個：

| Secret | 內容 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `CF_ACCOUNT_ID` | Cloudflare 帳號 ID（dashboard 右側或網址列可見） |
| `CF_KV_NAMESPACE_ID` | 剛剛建立的 KV namespace id（跟 wrangler.toml 裡同一個） |
| `CF_API_TOKEN` | Cloudflare API Token，權限給 Account → Workers KV Storage → Edit（[建立頁面](https://dash.cloudflare.com/profile/api-tokens)） |

### 4. 測試

Repo 頁面 → Actions → 「Apple 整修品監控」→ Run workflow 手動跑一次。
Log 應該顯示訂閱人數和抓到的商品數；跟 Bot `/watch` 訂一個條件後再跑一次，確認 KV 讀得到。

## 資料存放

- **Cloudflare KV**：每個使用者的訂閱條件（Worker 即時寫入，Actions 讀取）
- `subscriptions.json`：KV 的快照，備援用，每次 Actions 執行後更新
- `seen.json`：每個使用者已經通知過的商品網址，避免重複通知

後兩個檔案會被 workflow 自動 commit 回這個 **public** repo，代表訂閱者的 Telegram Chat ID 會留在 Git 歷史紀錄裡（公開可查），但不會包含姓名、電話等其他個資。

## 注意事項

- **GitHub 會自動停用 60 天沒動靜的 repo 排程**，收到「scheduled workflow disabled」通知信時去 Actions 頁按一下重新啟用就好
- 排程用的是 GitHub 的免費額度，public repo 完全免費；Cloudflare Workers/KV 免費額度也遠超這個用量
- 如果哪天腳本抓到 0 項商品（Apple 改版），workflow 會失敗，GitHub 會寄信通知你
- 如果 Playwright 下載 Chromium 卡住導致 workflow 超時取消，通常是暫時性的網路問題，等下一次排程通常會自動恢復
