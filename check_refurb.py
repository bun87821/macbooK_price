# -*- coding: utf-8 -*-
"""
Apple 台灣整修品監控
目標：13 吋 MacBook Air M5 銀色（512GB）
有新上架符合條件的商品時，透過 Telegram Bot 通知。
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

# 標題必須同時包含這些關鍵字（Apple 標題會夾雜 \xa0 不斷行空格，比對前會先正規化）
KEYWORDS = ["13 吋", "MacBook Air", "M5", "銀色"]
# 進一步到商品頁確認的規格關鍵字
SPEC_KEYWORD = "512GB"

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


def check_spec(url: str, keyword: str) -> bool:
    """抓商品詳細頁純 HTML，確認規格（例如 512GB）"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", "ignore")
        return keyword.lower() in html.lower()
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
    if os.environ.get("TEST_NOTIFY") == "true":
        send_telegram("✅ 測試訊息：Telegram 通知設定成功！")
        print("[test] 已發送測試訊息")
        return

    products = asyncio.run(fetch_products())
    print(f"共取得 {len(products)} 項商品")

    if len(products) == 0:
        print("[error] 沒抓到任何商品，頁面結構可能改了")
        sys.exit(1)  # 讓 workflow 變紅，GitHub 會寄失敗通知

    seen = set()
    if SEEN_FILE.exists():
        seen = set(json.loads(SEEN_FILE.read_text()))

    matches = [p for p in products
               if all(k in norm(p["title"]) for k in KEYWORDS)]
    print(f"符合關鍵字：{len(matches)} 項")

    new_hits = []
    for m in matches:
        if m["url"] in seen:
            continue
        if SPEC_KEYWORD and not check_spec(m["url"], SPEC_KEYWORD):
            print(f"[skip] 非 {SPEC_KEYWORD}：{norm(m['title'])}")
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
    current_match_urls = [m["url"] for m in matches]
    SEEN_FILE.write_text(json.dumps(current_match_urls, ensure_ascii=False, indent=2))

    if not new_hits:
        print("這次沒有新上架的目標商品")


if __name__ == "__main__":
    main()
