# -*- coding: utf-8 -*-
"""
Apple 台灣整修品監控
訂閱指令由 Cloudflare Worker（worker/）即時處理並存進 KV；
這支腳本每 30 分鐘從 KV 讀訂閱清單、爬整修品頁面、發上架通知。
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
SUBS_FILE = Path("subscriptions.json")  # KV 的快照，KV 讀不到時的備援

# 商品頁上會出現的儲存容量標示，由大到小比對，比對到第一個出現的就當作該商品容量
CAPACITY_SIZES_GB = [
    ("8TB", 8192), ("4TB", 4096), ("2TB", 2048), ("1TB", 1024),
    ("512GB", 512), ("256GB", 256), ("128GB", 128), ("64GB", 64),
]

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_KV_NAMESPACE_ID = os.environ.get("CF_KV_NAMESPACE_ID", "")
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def norm(s: str) -> str:
    """把不斷行空格換成一般空格，方便關鍵字比對"""
    return s.replace("\xa0", " ").replace(" ", " ")


def match_key(s: str) -> str:
    """比對用的正規化：去掉所有空白、轉小寫，讓「13吋」也能對到「13 吋」"""
    return re.sub(r"\s+", "", norm(s)).lower()


def title_matches(title: str, keywords: list) -> bool:
    key = match_key(title)
    return all(match_key(k) in key for k in keywords)


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


def kv_request(path: str, method: str = "GET", data: bytes = None) -> str:
    base = (f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
            f"/storage/kv/namespaces/{CF_KV_NAMESPACE_ID}")
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Authorization": f"Bearer {CF_API_TOKEN}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode()


def load_subscriptions() -> dict:
    """從 Cloudflare KV 讀訂閱清單（Worker 即時寫入的），回傳 {chat_id: targets}。
    KV 讀不到就退回 repo 裡的快照；KV 還是空的（剛部署）就用快照把舊訂閱者搬進去。"""
    snapshot = {}
    if SUBS_FILE.exists():
        snapshot = json.loads(SUBS_FILE.read_text())

    if not (CF_ACCOUNT_ID and CF_KV_NAMESPACE_ID and CF_API_TOKEN):
        print("[warn] 未設定 Cloudflare KV 密鑰，改用 repo 快照")
        return snapshot

    try:
        keys = json.loads(kv_request("/keys?prefix=chat:&limit=1000"))["result"]

        if not keys and snapshot:
            print("[info] KV 是空的，把快照裡的舊訂閱者搬進 KV")
            for cid, targets in snapshot.items():
                body = json.dumps(
                    {"greeted": True, "targets": targets},
                    ensure_ascii=False).encode()
                kv_request(f"/values/chat:{cid}", method="PUT", data=body)
            return snapshot

        subs = {}
        for k in keys:
            name = k["name"]
            chat = json.loads(kv_request("/values/" + urllib.parse.quote(name)))
            targets = chat.get("targets", [])
            if targets:
                subs[name.removeprefix("chat:")] = targets

        # 寫回快照：備援用，也方便直接在 repo 看目前有哪些訂閱
        SUBS_FILE.write_text(json.dumps(subs, ensure_ascii=False, indent=2))
        return subs
    except Exception as e:
        print(f"[warn] 讀取 KV 失敗（{e}），改用 repo 快照")
        return snapshot


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
    subs = load_subscriptions()
    print(f"訂閱者共 {len(subs)} 人")

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
                       if title_matches(p["title"], keywords)]
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
