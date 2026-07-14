// Telegram webhook Worker：即時回覆指令，訂閱資料存 Cloudflare KV
// 部署方式見 repo README「即時回覆（Cloudflare Worker）」一節

const MAX_TARGETS = 5;

const HELP_TEXT = `🍎 Apple 整修品監控 Bot

指令：
/watch 關鍵字1,關鍵字2,... [min=容量GB]
  新增一組監控條件，商品標題要同時包含所有關鍵字才算符合
  例：/watch 13 吋,MacBook Air,M5 min=512

/list — 查看目前訂閱的條件
/unwatch 編號 — 移除一組條件（編號看 /list）
/help — 顯示這個說明

每人最多可設定 ${MAX_TARGETS} 組條件。`;

const WELCOME_TEXT = `👋 歡迎使用 Apple 整修品監控 Bot！

這個 Bot 每 30 分鐘檢查一次 Apple 台灣官網的 Mac 整修品區，有符合你條件的商品「新上架」時，會主動傳訊息通知你。

📖 快速上手：
1️⃣ 用 /watch 訂閱條件，關鍵字用逗號分隔，商品標題要同時包含所有關鍵字才算符合：
　　/watch 13 吋,MacBook Air,M5
2️⃣ 想限制最低容量就加上 min=容量GB：
　　/watch 13 吋,MacBook Air,M5 min=512
　　（512GB、1TB、2TB 都會通知，256GB 不會）
3️⃣ 用 /list 查看訂閱、/unwatch 編號 移除訂閱

💡 小提醒：
• 每人最多 ${MAX_TARGETS} 組條件
• 同一台商品只會通知一次，售罄下架後若再上架會重新通知
• 指令會立即回覆，但商品檢查是每 30 分鐘一輪

隨時傳 /help 可以再看一次指令說明。`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/webhook") {
      if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TG_SECRET) {
        return new Response("forbidden", { status: 403 });
      }
      let update;
      try {
        update = await request.json();
      } catch {
        return new Response("ok");
      }
      try {
        await handleUpdate(update, env);
      } catch (e) {
        console.log("handleUpdate error:", e);
      }
      return new Response("ok"); // 一律回 200，避免 Telegram 重送
    }
    // 訂閱清單，給 GitHub Actions 的排程檢查讀取。
    // 內容與 repo 裡公開的 subscriptions.json 快照相同，所以不需要另外驗證。
    if (request.method === "GET" && url.pathname === "/subs") {
      const subs = {};
      let cursor;
      do {
        const page = await env.SUBS.list({ prefix: "chat:", cursor });
        for (const k of page.keys) {
          const chat = await env.SUBS.get(k.name, "json");
          const targets = chat?.targets || [];
          if (targets.length > 0) subs[k.name.slice("chat:".length)] = targets;
        }
        cursor = page.list_complete ? null : page.cursor;
      } while (cursor);
      return new Response(JSON.stringify(subs), {
        headers: { "Content-Type": "application/json; charset=utf-8" },
      });
    }
    return new Response("Apple refurb bot worker");
  },
};

async function handleUpdate(update, env) {
  const msg = update.message;
  if (!msg || !msg.chat || !msg.text) return;
  const chatId = String(msg.chat.id);
  const text = msg.text.trim();

  const key = `chat:${chatId}`;
  const chat = (await env.SUBS.get(key, "json")) || { greeted: false, targets: [] };

  // 第一次互動的人，不管傳什麼都先送一份使用說明書
  const firstContact = !chat.greeted;
  if (firstContact) {
    chat.greeted = true;
    await sendMessage(env, chatId, WELCOME_TEXT);
  }

  let reply = null;
  if (text === "/start" || text === "/help") {
    if (!firstContact) reply = HELP_TEXT; // 剛剛才送過說明書就不重複
  } else if (text.startsWith("/watch")) {
    const parsed = parseWatchArgs(text.slice("/watch".length).trim());
    if (!parsed) {
      reply = "格式錯誤，範例：\n/watch 13 吋,MacBook Air,M5 min=512";
    } else {
      const { keywords, minGb } = parsed;
      const dup = chat.targets.some(
        (t) =>
          (t.min_gb || 0) === minGb &&
          t.keywords.length === keywords.length &&
          t.keywords.every((k, i) => k === keywords[i])
      );
      if (dup) {
        reply = "這組條件已經訂閱過了，用 /list 查看";
      } else if (chat.targets.length >= MAX_TARGETS) {
        reply = `已達上限（${MAX_TARGETS} 組），請先用 /unwatch 移除一些`;
      } else {
        chat.targets.push({ keywords, min_gb: minGb });
        const suffix = minGb ? `（${minGb}GB 以上）` : "";
        reply = `✅ 已新增監控：${keywords.join(" + ")}${suffix}`;
      }
    }
  } else if (text.startsWith("/unwatch")) {
    const arg = text.slice("/unwatch".length).trim();
    const idx = /^\d+$/.test(arg) ? parseInt(arg, 10) : 0;
    if (idx < 1 || idx > chat.targets.length) {
      reply = "請提供正確編號，用 /list 查看";
    } else {
      const [removed] = chat.targets.splice(idx - 1, 1);
      reply = `🗑 已移除：${removed.keywords.join(" + ")}`;
    }
  } else if (text === "/list") {
    reply =
      chat.targets.length === 0
        ? "目前沒有訂閱任何條件，用 /watch 新增"
        : "目前訂閱的條件：\n" +
          chat.targets
            .map((t, i) => {
              const suffix = t.min_gb ? `（${t.min_gb}GB 以上）` : "";
              return `${i + 1}. ${t.keywords.join(" + ")}${suffix}`;
            })
            .join("\n");
  } else if (!firstContact) {
    reply = "看不懂這個指令，傳 /help 看說明";
  }

  await env.SUBS.put(key, JSON.stringify(chat));
  if (reply) await sendMessage(env, chatId, reply);
}

function parseWatchArgs(text) {
  let minGb = 0;
  const m = text.match(/min\s*[:=]\s*(\d+)/i);
  if (m) {
    minGb = parseInt(m[1], 10);
    text = text.replace(m[0], "");
  }
  // 全形逗號也接受，使用者常用中文輸入法
  const keywords = text
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (keywords.length === 0) return null;
  return { keywords, minGb };
}

async function sendMessage(env, chatId, text) {
  await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text,
      disable_web_page_preview: false,
    }),
  });
}
