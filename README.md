# Apple 整修品監控 🔔

每 30 分鐘檢查 Apple 台灣官網整修品區，目標商品上架時透過 Telegram 通知。

目前監控目標：**13 吋 MacBook Air M5 銀色 512GB**

## 運作方式

1. GitHub Actions 排程啟動無頭瀏覽器（Playwright），渲染 [整修品頁面](https://www.apple.com/tw/shop/refurbished/mac)
2. 比對商品標題關鍵字（`13 吋`、`MacBook Air`、`M5`、`銀色`）
3. 符合的商品再抓詳細頁確認是 `512GB`
4. 新上架（不在 `seen.json` 裡）才發 Telegram 通知，避免重複轟炸
5. 商品售罄下架後會從 `seen.json` 移除，之後再上架會重新通知

## 設定步驟

### 1. 建立 Telegram Bot

1. 在 Telegram 找 [@BotFather](https://t.me/BotFather)，傳 `/newbot`，照指示取名
2. 拿到 Bot Token（長得像 `123456789:ABCdef...`）
3. 跟你的新 Bot 傳一句話（任何內容都行）
4. 瀏覽器開 `https://api.telegram.org/bot<你的Token>/getUpdates`
   在回傳的 JSON 裡找 `"chat":{"id":一串數字}`，那就是你的 Chat ID

### 2. 建立 GitHub Repo 並設定密鑰

1. 把這三個檔案推到一個新的 repo（private 即可）
2. Repo 頁面 → Settings → Secrets and variables → Actions → New repository secret
   - `TELEGRAM_BOT_TOKEN`：Bot Token
   - `TELEGRAM_CHAT_ID`：Chat ID

### 3. 測試

Repo 頁面 → Actions → 「Apple 整修品監控」→ Run workflow 手動跑一次。
看 log 有沒有正常抓到商品數（目前約 21 項）。

## 修改監控目標

改 `check_refurb.py` 開頭的 `TARGETS` 清單即可，可以設定多組，符合任一組就會通知：

```python
TARGETS = [
    {"keywords": ["13 吋", "MacBook Air", "M5"], "min_gb": 512},
    {"keywords": ["15 吋", "MacBook Air", "M4"], "min_gb": 256},
]
```

- `keywords`：標題必須同時包含清單裡所有關鍵字
- `min_gb`：進一步到商品頁確認的最低儲存容量（GB），符合這個容量以上就算通過，不需要限制就設成 `0`

## 注意事項

- **GitHub 會自動停用 60 天沒動靜的 repo 排程**，收到「scheduled workflow disabled」通知信時去 Actions 頁按一下重新啟用就好（有商品變動時 bot 會自動 commit，通常不會閒置這麼久）
- 排程用的是 GitHub 的免費額度，public repo 完全免費，private repo 每月 2,000 分鐘也綽綽有餘（每次跑約 2 分鐘 × 每天 48 次 ≈ 每月 2,880 分鐘，**private repo 會超過**，建議用 public repo，或把頻率降到每小時一次）
- 如果哪天腳本抓到 0 項商品（Apple 改版），workflow 會失敗，GitHub 會寄信通知你
