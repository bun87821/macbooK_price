# -*- coding: utf-8 -*-
"""一次性腳本：把歡迎說明書補發給改版前就傳過訊息的使用者，發完即可刪除。"""
import json
import sys
import types
from pathlib import Path

# check_refurb 頂層 import 了 playwright，這裡只需要它的文案和發送函式，先墊一個假模組
sys.modules["playwright"] = types.ModuleType("playwright")
sys.modules["playwright.async_api"] = types.ModuleType("playwright.async_api")
sys.modules["playwright.async_api"].async_playwright = None

from check_refurb import OFFSET_FILE, WELCOME_TEXT, send_telegram  # noqa: E402

# 2026-07-14 run 29350454320 的 log 裡出現過、但還沒收過說明書的 chat id
CHAT_IDS = [
    "6922810979", "5016776599", "1792230462", "6467904314", "8175495742",
    "973283295", "1043139367", "8491315754", "1583076729", "8284342171",
    "1013864592",  # 上次回 403（可能封鎖了 bot），再試一次無妨
]

state = json.loads(OFFSET_FILE.read_text()) if OFFSET_FILE.exists() else {}
greeted = set(state.get("greeted", []))

for chat_id in CHAT_IDS:
    if chat_id in greeted:
        print(f"[skip] {chat_id} 已打過招呼")
        continue
    send_telegram(chat_id, WELCOME_TEXT)
    greeted.add(chat_id)

state["greeted"] = sorted(greeted)
OFFSET_FILE.write_text(json.dumps(state))
print("完成，greeted 共", len(greeted), "人")
