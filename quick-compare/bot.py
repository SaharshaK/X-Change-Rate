from __future__ import annotations
import asyncio
import os
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv(Path(__file__).parent / ".env")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API_BASE  = os.getenv("API_BASE", "http://localhost:8000")

# Per-user chat history for LLM context
_histories: Dict[int, List[Dict]] = defaultdict(list)

PLATFORM_EMOJI = {"blinkit": "🟡", "zepto": "🟣"}


def _format_results(data: dict) -> str:
    query    = data["query"]
    cheapest = data.get("cheapest")
    results  = data.get("results", {})

    lines = [f"🔍 *{query}*\n"]

    for platform, result in results.items():
        emoji = PLATFORM_EMOJI.get(platform, "🛒")
        label = platform.capitalize()

        if result["status"] == "error":
            lines.append(f"{emoji} *{label}*: unavailable")
            continue

        products = result["products"]
        if not products:
            lines.append(f"{emoji} *{label}*: no results")
            continue

        best = min((p for p in products if p["in_stock"]), key=lambda p: p["price"], default=None)
        if best:
            tag = " ✅ CHEAPEST" if cheapest and best["name"] == cheapest["name"] and best["platform"] == cheapest["platform"] else ""
            lines.append(f"{emoji} *{label}*: ₹{best['price']} — {best['name']}{tag}")
        else:
            lines.append(f"{emoji} *{label}*: out of stock")

    if cheapest:
        lines.append(f"\n💚 Best deal: *{cheapest['name']}* at ₹{cheapest['price']} on {cheapest['platform'].capitalize()}")

    return "\n".join(lines)


def _cart_keyboard(data: dict) -> InlineKeyboardMarkup | None:
    """One 'Add to cart' button per platform that has results."""
    query    = data["query"]
    cheapest = data.get("cheapest")
    results  = data.get("results", {})

    buttons = []
    for platform, result in results.items():
        if result["status"] == "error" or not result["products"]:
            continue
        best = min(
            (p for p in result["products"] if p["in_stock"]),
            key=lambda p: p["price"],
            default=None,
        )
        if best:
            emoji = PLATFORM_EMOJI.get(platform, "🛒")
            label = f"{emoji} Add to {platform.capitalize()} cart (₹{best['price']})"
            cb    = f"cart|{platform}|{query}|{best['name']}"
            buttons.append([InlineKeyboardButton(label, callback_data=cb[:64])])

    return InlineKeyboardMarkup(buttons) if buttons else None


async def _call_smart_search(query: str, user_id: int) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            f"{API_BASE}/compare",
            params={"q": query, "platforms": "blinkit,zepto"},
        )
        resp.raise_for_status()
        return resp.json()


async def _add_to_cart(platform: str, query: str, product_name: str) -> tuple[bool, str]:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{API_BASE}/add-to-cart",
            json={"platform": platform, "query": query, "product_name": product_name},
        )
        if resp.status_code == 200:
            return True, ""
        try:
            return False, resp.json().get("detail", "Failed")
        except Exception:
            return False, "Failed"


# ---------- handlers ----------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi! I'm your grocery price bot.\n\n"
        "Just tell me what you want to buy — in plain English:\n"
        "• _I want to buy tomato_\n"
        "• _Get me 2 kg onions_\n"
        "• _Amul butter 500g_\n\n"
        "I'll compare prices on Blinkit and Zepto and help you add to cart! 🛒",
        parse_mode="Markdown",
    )


async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _histories[update.effective_user.id].clear()
    await update.message.reply_text("🧹 Conversation history cleared.")


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text    = update.message.text.strip()
    history = _histories[user_id]

    # First ask the LLM what the intent is
    thinking = await update.message.reply_text("⏳ Thinking...")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{API_BASE}/nlp/chat",
                json={"message": text, "history": history},
            )
            resp.raise_for_status()
            result = resp.json()
    except Exception as e:
        await thinking.edit_text(f"❌ Error: {e}")
        return

    # Update history
    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": str(result)})
    # Keep last 20 messages to avoid token bloat
    _histories[user_id] = history[-20:]

    intent = result.get("intent")

    if intent == "chat":
        await thinking.edit_text(result.get("reply", "🤔 I'm not sure what you mean. Try asking for a product!"))
        return

    # intent == "search"
    search_query = result.get("search_query", text)
    await thinking.edit_text(f"🔍 Searching for *{search_query}*...", parse_mode="Markdown")

    try:
        data     = await _call_smart_search(search_query, user_id)
        message  = _format_results(data)
        keyboard = _cart_keyboard(data)
        await thinking.edit_text(message, parse_mode="Markdown", reply_markup=keyboard)
    except Exception as e:
        await thinking.edit_text(f"❌ Search failed: {e}")


async def handle_cart_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("|", 3)
    if len(parts) != 4:
        return

    _, platform, search_query, product_name = parts
    await query.edit_message_reply_markup(None)
    msg = await query.message.reply_text(f"🛒 Adding to {platform.capitalize()} cart...")

    ok, err = await _add_to_cart(platform, search_query, product_name)
    if ok:
        checkout_urls = {
            "blinkit": "https://blinkit.com/?cart=open",
            "zepto":   "https://www.zeptonow.com/?cart=open",
        }
        await msg.edit_text(
            f"✅ Added to {platform.capitalize()} cart!\n\n"
            f"[👉 Open {platform.capitalize()} to checkout]({checkout_urls.get(platform, '#')})",
            parse_mode="Markdown",
        )
    else:
        await msg.edit_text(f"❌ Failed to add to cart: {err}")


# ---------- NLP endpoint (called by bot, served by FastAPI) ----------
# We add a lightweight /nlp/chat route to main.py so the bot can call it.
# This keeps all LLM logic server-side.

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CallbackQueryHandler(handle_cart_callback, pattern=r"^cart\|"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Bot is running... Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
