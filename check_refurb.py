# -*- coding: utf-8 -*-
"""
Apple 台灣整修品監控
可設定多組監控目標，有新上架符合任一組條件的商品時，透過 Telegram Bot 通知。
"""
import asyncio
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

URL = "https://www.apple.com/tw/shop/refurbished/mac"
SEEN_FILE = Path("seen.json")

# 監控目標清單，可以設定多組。每組的 keywords 必須同時出現在標題裡才算符合
# （Apple 標題會夾雜 \xa0 不斷行空格，比對前會先正規化），min_gb 是進一步到商品頁
# 確認的最低儲存容量（GB），符合這個容量以上就算通過，不需要限制就設成 0
TARGETS = [
    {"keywords": ["13 吋", "MacBook Air", "M5"], "min_gb": 512},
    {"keywords": ["13 吋", "MacBook Air", "M4"], "min_gb": 512},
]

# 商品頁上會出現的儲存容量標示，由大到小比對，比對到第一個出現的就當作該商品容量
CAPACITY_SIZES_GB = [
    ("8TB", 8192), ("4TB", 4096), ("2TB", 2048), ("1TB", 1024),
    ("512GB", 512), ("256GB", 256), ("128GB", 128), ("64GB", 64),
]

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def norm(s: str) -> str:
    """把不斷行空格換成一般空格，方便關鍵字比對"""
    return s.replace("\xa0", " ").replace("\u2009", " ")


def send_telegram(text: str):
    if not TG_TOKEN or not TG_CHAT:
        print("[warn] 未設定 Telegram 密鑰，略過通知")
        return
    api = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TG_CHAT,
        "text": text,
        "disable_web_page_preview": False,
    }).encode()
    req = urllib.request.Request(
        api, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        print("[telegram]", r.status)


def check_min_capacity(url: str, min_gb: int) -> bool:
    """抓商品詳細頁純 HTML，確認儲存容量是否達到 min_gb"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", "ignore").lower()
        for label, gb in CAPACITY_SIZES_GB:
            if label.lower() in html:
                return gb >= min_gb
        return True  # 抓不到容量標示，寧可通知，別漏掉
    except Exception as e:
        print(f"[warn] 規格確認失敗 {url}: {e}")
        return True  # 確認不了就寧可通知，別漏掉


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
    products = asyncio.run(fetch_products())
    print(f"共取得 {len(products)} 項商品")

    if len(products) == 0:
        print("[error] 沒抓到任何商品，頁面結構可能改了")
        sys.exit(1)  # 讓 workflow 變紅，GitHub 會寄失敗通知

    seen = set()
    if SEEN_FILE.exists():
        seen = set(json.loads(SEEN_FILE.read_text()))

    matched_urls = set()
    new_hits = []
    for target in TARGETS:
        keywords, min_gb = target["keywords"], target.get("min_gb", 0)
        matches = [p for p in products
                   if all(k in norm(p["title"]) for k in keywords)]
        print(f"符合關鍵字 {keywords}：{len(matches)} 項")
        matched_urls.update(m["url"] for m in matches)

        for m in matches:
            if m["url"] in seen or m["url"] in [h["url"] for h in new_hits]:
                continue
            if min_gb and not check_min_capacity(m["url"], min_gb):
                print(f"[skip] 容量不足 {min_gb}GB：{norm(m['title'])}")
                continue
            new_hits.append(m)

    for m in new_hits:
        text = (f"🎯 Apple 整修品有貨了！\n\n"
                f"{norm(m['title'])}\n"
                f"價格：{m['price'] or '未知'}\n\n"
                f"{m['url']}\n\n"
                f"手刀下單，整修品通常很快售罄！")
        send_telegram(text)
        print("[notify]", norm(m["title"]))

    # 更新 seen：只保留「目前還在架上」的，這樣售罄後再上架會重新通知
    SEEN_FILE.write_text(json.dumps(sorted(matched_urls), ensure_ascii=False, indent=2))

    if not new_hits:
        print("這次沒有新上架的目標商品")


if __name__ == "__main__":
    main()
