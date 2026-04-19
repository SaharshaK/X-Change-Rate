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
TOP_N = 3  # products shown per platform

# Temporary store for pending cart adds awaiting qty confirmation
# key: user_id, value: {platform, query, product_name}
_pending_cart: Dict[int, dict] = {}


def _format_results(data: dict) -> str:
    query    = data["query"]
    cheapest = data.get("cheapest")
    results  = data.get("results", {})

    lines = [f"🔍 *{query}*\n"]

    for platform, result in results.items():
        emoji = PLATFORM_EMOJI.get(platform, "🛒")
        label = platform.capitalize()
        lines.append(f"{emoji} *{label}*")

        if result["status"] == "error":
            lines.append("  ⚠️ unavailable\n")
            continue

        in_stock = [p for p in result["products"] if p["in_stock"]]
        if not in_stock:
            lines.append("  No results\n")
            continue

        top = sorted(in_stock, key=lambda p: p["price"])[:TOP_N]
        for p in top:
            tag = " ✅" if cheapest and p["name"] == cheapest["name"] and p["platform"] == cheapest["platform"] else ""
            lines.append(f"  • {p['name']} — *₹{p['price']}* ({p['quantity']}){tag}")
        lines.append("")

    if cheapest:
        lines.append(f"💚 Best deal: *{cheapest['name']}* at ₹{cheapest['price']} on {cheapest['platform'].capitalize()}")

    return "\n".join(lines)


def _cart_keyboard(data: dict) -> InlineKeyboardMarkup | None:
    """One row per product (top N per platform), each with an Add button."""
    query   = data["query"]
    results = data.get("results", {})
    cheapest = data.get("cheapest")

    buttons = []
    for platform, result in results.items():
        if result["status"] == "error" or not result["products"]:
            continue
        emoji = PLATFORM_EMOJI.get(platform, "🛒")
        in_stock = [p for p in result["products"] if p["in_stock"]]
        top = sorted(in_stock, key=lambda p: p["price"])[:TOP_N]
        for p in top:
            tag = " ✅" if cheapest and p["name"] == cheapest["name"] and p["platform"] == cheapest["platform"] else ""
            btn_label = f"{emoji} ₹{p['price']} {p['name'][:25]}{tag}"
            # cb data: select|platform|query|product_name  (truncated to 64 chars)
            cb = f"sel|{platform}|{query[:15]}|{p['name'][:20]}"
            buttons.append([InlineKeyboardButton(btn_label, callback_data=cb)])

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
    assistant_content = result.get("reply") or result.get("search_query") or str(result)
    history.append({"role": "assistant", "content": assistant_content})
    # Keep last 20 messages to avoid token bloat
    _histories[user_id] = history[-20:]

    intent = result.get("intent")

    if intent == "chat":
        await thinking.edit_text(result.get("reply", "🤔 I'm not sure what you mean. Try asking for a product!"))
        return

    # intent == "search" — use only the product name, not quantity, for scraper search
    search_query = result.get("product", result.get("search_query", text)).strip()
    await thinking.edit_text(f"🔍 Searching for *{search_query}*...", parse_mode="Markdown")

    try:
        data     = await _call_smart_search(search_query, user_id)
        message  = _format_results(data)
        keyboard = _cart_keyboard(data)
        await thinking.edit_text(message, parse_mode="Markdown", reply_markup=keyboard)
    except Exception as e:
        import traceback; traceback.print_exc()
        await thinking.edit_text(f"❌ Search failed: {e}")


CHECKOUT_URLS = {
    "blinkit": "https://blinkit.com/?cart=open",
    "zepto":   "https://www.zeptonow.com/?cart=open",
}


async def handle_select_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User tapped a product — show quantity picker."""
    cb = update.callback_query
    await cb.answer()

    parts = cb.data.split("|", 3)
    if len(parts) != 4:
        return
    _, platform, query, product_name = parts

    user_id = cb.from_user.id
    _pending_cart[user_id] = {"platform": platform, "query": query, "product_name": product_name}

    qty_buttons = [
        [
            InlineKeyboardButton("1", callback_data="qty|1"),
            InlineKeyboardButton("2", callback_data="qty|2"),
            InlineKeyboardButton("3", callback_data="qty|3"),
        ],
        [
            InlineKeyboardButton("4", callback_data="qty|4"),
            InlineKeyboardButton("5", callback_data="qty|5"),
            InlineKeyboardButton("6", callback_data="qty|6"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="qty|cancel")],
    ]
    await cb.message.reply_text(
        f"🛒 *{product_name}* on {platform.capitalize()}\n\nHow many units?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(qty_buttons),
    )


async def handle_qty_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User picked a quantity — add to cart N times."""
    cb = update.callback_query
    await cb.answer()

    user_id = cb.from_user.id
    qty_str = cb.data.split("|", 1)[1]

    await cb.edit_message_reply_markup(None)

    if qty_str == "cancel":
        await cb.message.reply_text("❌ Cancelled.")
        _pending_cart.pop(user_id, None)
        return

    pending = _pending_cart.pop(user_id, None)
    if not pending:
        await cb.message.reply_text("⚠️ Session expired, please search again.")
        return

    qty = int(qty_str)
    platform     = pending["platform"]
    query        = pending["query"]
    product_name = pending["product_name"]

    msg = await cb.message.reply_text(f"🛒 Adding {qty}x *{product_name}* to {platform.capitalize()}...", parse_mode="Markdown")

    for _ in range(qty):
        ok, err = await _add_to_cart(platform, query, product_name)
        if not ok:
            await msg.edit_text(f"❌ Failed: {err}")
            return

    await msg.edit_text(
        f"✅ Added {qty}x *{product_name}* to {platform.capitalize()}!\n\n"
        f"[👉 Open {platform.capitalize()} to checkout]({CHECKOUT_URLS.get(platform, '#')})",
        parse_mode="Markdown",
    )


# ---------- NLP endpoint (called by bot, served by FastAPI) ----------
# We add a lightweight /nlp/chat route to main.py so the bot can call it.
# This keeps all LLM logic server-side.

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CallbackQueryHandler(handle_select_callback, pattern=r"^sel\|"))
    app.add_handler(CallbackQueryHandler(handle_qty_callback,    pattern=r"^qty\|"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Bot is running... Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
