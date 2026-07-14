# -*- coding: utf-8 -*-
"""
Apple 台灣整修品監控
任何人都可以透過 Telegram 傳指令給 Bot，訂閱自己想要的監控條件。
"""
import asyncio
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

URL = "https://www.apple.com/tw/shop/refurbished/mac"
SEEN_FILE = Path("seen.json")
SUBS_FILE = Path("subscriptions.json")
OFFSET_FILE = Path("tg_offset.json")

MAX_TARGETS_PER_CHAT = 5

# 商品頁上會出現的儲存容量標示，由大到小比對，比對到第一個出現的就當作該商品容量
CAPACITY_SIZES_GB = [
    ("8TB", 8192), ("4TB", 4096), ("2TB", 2048), ("1TB", 1024),
    ("512GB", 512), ("256GB", 256), ("128GB", 128), ("64GB", 64),
]

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")

HELP_TEXT = (
    "🍎 Apple 整修品監控 Bot\n\n"
    "指令：\n"
    "/watch 關鍵字1,關鍵字2,... [min=容量GB]\n"
    "  新增一組監控條件，商品標題要同時包含所有關鍵字才算符合\n"
    "  例：/watch 13 吋,MacBook Air,M5 min=512\n\n"
    "/list — 查看目前訂閱的條件\n"
    "/unwatch 編號 — 移除一組條件（編號看 /list）\n"
    "/help — 顯示這個說明\n\n"
    f"每人最多可設定 {MAX_TARGETS_PER_CHAT} 組條件。"
)


def norm(s: str) -> str:
    """把不斷行空格換成一般空格，方便關鍵字比對"""
    return s.replace("\xa0", " ").replace(" ", " ")


def send_telegram(chat_id: str, text: str):
    if not TG_TOKEN or not chat_id:
        print("[warn] 未設定 Telegram Bot Token 或 chat_id，略過通知")
        return
    api = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": False,
    }).encode()
    req = urllib.request.Request(
        api, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f"[telegram] chat={chat_id} status={r.status}")
    except Exception as e:
        print(f"[warn] 發送給 {chat_id} 失敗: {e}")


def get_updates(offset: int):
    """拉取使用者傳給 Bot 的新訊息"""
    if not TG_TOKEN:
        return []
    params = urllib.parse.urlencode({"offset": offset, "timeout": 0})
    api = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?{params}"
    try:
        with urllib.request.urlopen(api, timeout=20) as r:
            data = json.loads(r.read().decode())
        return data.get("result", [])
    except Exception as e:
        print(f"[warn] 取得 Telegram 訊息失敗: {e}")
        return []


def parse_watch_args(text: str):
    """解析 /watch 指令參數，回傳 (keywords, min_gb)，格式錯誤回傳 None"""
    m = re.search(r"min\s*[:=]\s*(\d+)", text, re.I)
    min_gb = int(m.group(1)) if m else 0
    if m:
        text = text[:m.start()] + text[m.end():]
    keywords = [kw.strip() for kw in text.split(",") if kw.strip()]
    if not keywords:
        return None
    return keywords, min_gb


def process_commands(subs: dict) -> bool:
    """處理使用者傳來的指令，原地更新 subs。回傳 subs 是否有變更。"""
    offset = 0
    if OFFSET_FILE.exists():
        offset = json.loads(OFFSET_FILE.read_text()).get("offset", 0)

    updates = get_updates(offset)
    if not updates:
        return False

    changed = False
    for u in updates:
        offset = max(offset, u["update_id"] + 1)
        msg = u.get("message") or {}
        chat_id = str(msg.get("chat", {}).get("id", "") or "")
        text = (msg.get("text") or "").strip()
        if not chat_id or not text:
            continue

        if text in ("/start", "/help"):
            send_telegram(chat_id, HELP_TEXT)

        elif text.startswith("/watch"):
            parsed = parse_watch_args(text[len("/watch"):].strip())
            if not parsed:
                send_telegram(chat_id, "格式錯誤，範例：\n/watch 13 吋,MacBook Air,M5 min=512")
                continue
            keywords, min_gb = parsed
            targets = subs.setdefault(chat_id, [])
            if len(targets) >= MAX_TARGETS_PER_CHAT:
                send_telegram(chat_id, f"已達上限（{MAX_TARGETS_PER_CHAT} 組），請先用 /unwatch 移除一些")
                continue
            targets.append({"keywords": keywords, "min_gb": min_gb})
            changed = True
            suffix = f"（{min_gb}GB 以上）" if min_gb else ""
            send_telegram(chat_id, f"✅ 已新增監控：{' + '.join(keywords)}{suffix}")

        elif text.startswith("/unwatch"):
            arg = text[len("/unwatch"):].strip()
            targets = subs.get(chat_id, [])
            if not arg.isdigit() or not (1 <= int(arg) <= len(targets)):
                send_telegram(chat_id, "請提供正確編號，用 /list 查看")
                continue
            removed = targets.pop(int(arg) - 1)
            changed = True
            send_telegram(chat_id, f"🗑 已移除：{' + '.join(removed['keywords'])}")

        elif text == "/list":
            targets = subs.get(chat_id, [])
            if not targets:
                send_telegram(chat_id, "目前沒有訂閱任何條件，用 /watch 新增")
            else:
                lines = []
                for i, t in enumerate(targets):
                    suffix = f"（{t['min_gb']}GB 以上）" if t.get("min_gb") else ""
                    lines.append(f"{i + 1}. {' + '.join(t['keywords'])}{suffix}")
                send_telegram(chat_id, "目前訂閱的條件：\n" + "\n".join(lines))

        else:
            send_telegram(chat_id, "看不懂這個指令，傳 /help 看說明")

    OFFSET_FILE.write_text(json.dumps({"offset": offset}))
    return changed


def get_capacity_gb(url: str, cache: dict):
    """抓商品詳細頁純 HTML，取得儲存容量（GB），同一次執行內用 cache 避免重複下載"""
    if url in cache:
        return cache[url]
    gb = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", "ignore").lower()
        for label, size in CAPACITY_SIZES_GB:
            if label.lower() in html:
                gb = size
                break
    except Exception as e:
        print(f"[warn] 規格確認失敗 {url}: {e}")
    cache[url] = gb
    return gb


async def fetch_products():
    """渲染整修品頁面，回傳 [{url, title, price}]"""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(user_agent=UA)
        await page.goto(URL, wait_until="networkidle", timeout=90000)
        await page.wait_for_timeout(3000)

        # 防禦性處理：滾到底、點掉可能出現的「顯示更多」
        for _ in range(10):
            await page.mouse.wheel(0, 4000)
            await page.wait_for_timeout(500)
            btn = page.locator('button:has-text("顯示更多")').first
            try:
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    await page.wait_for_timeout(1500)
            except Exception:
                pass

        products = await page.evaluate("""() => {
          const seen = new Map();
          document.querySelectorAll('a[href*="/shop/product/"]').forEach(a => {
            const title = a.textContent.trim();
            if (!title) return;
            const key = a.href.split('?')[0];
            // 從卡片區塊找價格文字
            let price = '';
            let node = a;
            for (let i = 0; i < 6 && node; i++) {
              const m = node.textContent.match(/NT\\$[\\d,]+/);
              if (m) { price = m[0]; break; }
              node = node.parentElement;
            }
            seen.set(key, { url: key, title, price });
          });
          return [...seen.values()];
        }""")
        await browser.close()
        return products


def main():
    subs = {}
    if SUBS_FILE.exists():
        subs = json.loads(SUBS_FILE.read_text())

    if process_commands(subs):
        SUBS_FILE.write_text(json.dumps(subs, ensure_ascii=False, indent=2))

    products = asyncio.run(fetch_products())
    print(f"共取得 {len(products)} 項商品")

    if len(products) == 0:
        print("[error] 沒抓到任何商品，頁面結構可能改了")
        sys.exit(1)  # 讓 workflow 變紅，GitHub 會寄失敗通知

    seen_all = {}
    if SEEN_FILE.exists():
        seen_all = json.loads(SEEN_FILE.read_text())

    capacity_cache = {}
    any_notified = False

    for chat_id, targets in subs.items():
        seen = set(seen_all.get(chat_id, []))
        matched_urls = set()
        new_hits = []

        for target in targets:
            keywords, min_gb = target["keywords"], target.get("min_gb", 0)
            matches = [p for p in products
                       if all(k in norm(p["title"]) for k in keywords)]
            matched_urls.update(m["url"] for m in matches)

            for m in matches:
                if m["url"] in seen or m["url"] in [h["url"] for h in new_hits]:
                    continue
                if min_gb:
                    gb = get_capacity_gb(m["url"], capacity_cache)
                    if gb is not None and gb < min_gb:
                        continue
                new_hits.append(m)

        print(f"chat={chat_id} 符合 {len(matched_urls)} 項，新上架 {len(new_hits)} 項")

        for m in new_hits:
            text = (f"🎯 Apple 整修品有貨了！\n\n"
                    f"{norm(m['title'])}\n"
                    f"價格：{m['price'] or '未知'}\n\n"
                    f"{m['url']}\n\n"
                    f"手刀下單，整修品通常很快售罄！")
            send_telegram(chat_id, text)
            print(f"[notify] chat={chat_id} {norm(m['title'])}")
            any_notified = True

        seen_all[chat_id] = sorted(matched_urls)

    SEEN_FILE.write_text(json.dumps(seen_all, ensure_ascii=False, indent=2))

    if not any_notified:
        print("這次沒有新上架的目標商品")


if __name__ == "__main__":
    main()
