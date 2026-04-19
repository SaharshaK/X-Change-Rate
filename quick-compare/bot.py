from __future__ import annotations
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

PLATFORM_EMOJI = {"blinkit": "🟡", "zepto": "🟣"}
TOP_N = 3

CHECKOUT_URLS = {
    "blinkit": "https://blinkit.com/?cart=open",
    "zepto":   "https://www.zeptonow.com/?cart=open",
}

# Per-user state — all in bot memory, web app is never touched
_histories: Dict[int, List[Dict]]  = defaultdict(list)   # LLM chat history
_carts:     Dict[int, List[Dict]]  = defaultdict(list)   # [{platform, product_name, qty, price}]
_pending:   Dict[int, dict]        = {}                  # awaiting qty selection


# ---------- cart helpers ----------

def _cart_total(cart: List[Dict], platform: str) -> float:
    return sum(i["price"] * i["qty"] for i in cart if i["platform"] == platform)


def _cart_summary(cart: List[Dict]) -> str:
    if not cart:
        return "🛒 Your cart is empty."

    lines = ["🛒 *Your cart*\n"]
    for platform in ("blinkit", "zepto"):
        items = [i for i in cart if i["platform"] == platform]
        if not items:
            continue
        emoji = PLATFORM_EMOJI[platform]
        lines.append(f"{emoji} *{platform.capitalize()}*")
        for it in items:
            lines.append(f"  • {it['product_name']} × {it['qty']} — ₹{it['price'] * it['qty']:.0f}")
        lines.append(f"  Total: *₹{_cart_total(cart, platform):.0f}*\n")
    return "\n".join(lines)


def _checkout_keyboard(cart: List[Dict]) -> InlineKeyboardMarkup:
    buttons = []
    for platform in ("blinkit", "zepto"):
        if any(i["platform"] == platform for i in cart):
            emoji = PLATFORM_EMOJI[platform]
            total = _cart_total(cart, platform)
            buttons.append([InlineKeyboardButton(
                f"{emoji} Checkout on {platform.capitalize()} (₹{total:.0f})",
                url=CHECKOUT_URLS[platform],
            )])
    buttons.append([InlineKeyboardButton("🗑 Clear cart", callback_data="cart|clear")])
    return InlineKeyboardMarkup(buttons)


# ---------- search helpers ----------

async def _search(query: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            f"{API_BASE}/compare",
            params={"q": query, "platforms": "blinkit,zepto"},
        )
        resp.raise_for_status()
        return resp.json()


def _format_results(data: dict) -> str:
    query    = data["query"]
    cheapest = data.get("cheapest")
    results  = data.get("results", {})
    lines    = [f"🔍 *{query}*\n"]

    for platform, result in results.items():
        emoji = PLATFORM_EMOJI.get(platform, "🛒")
        lines.append(f"{emoji} *{platform.capitalize()}*")

        if result["status"] == "error":
            lines.append("  ⚠️ unavailable\n")
            continue

        in_stock = [p for p in result["products"] if p["in_stock"]]
        if not in_stock:
            lines.append("  No results\n")
            continue

        top = sorted(in_stock, key=lambda p: p["price"])[:TOP_N]
        for p in top:
            tag = " ✅" if (cheapest
                            and p["name"]     == cheapest["name"]
                            and p["platform"] == cheapest["platform"]) else ""
            lines.append(f"  • {p['name']} — *₹{p['price']}* ({p['quantity']}){tag}")
        lines.append("")

    if cheapest:
        lines.append(f"💚 Best deal: *{cheapest['name']}* ₹{cheapest['price']} on {cheapest['platform'].capitalize()}")

    return "\n".join(lines)


def _product_buttons(data: dict) -> InlineKeyboardMarkup | None:
    query    = data["query"]
    results  = data.get("results", {})
    cheapest = data.get("cheapest")
    buttons  = []

    for platform, result in results.items():
        if result["status"] == "error" or not result["products"]:
            continue
        emoji    = PLATFORM_EMOJI.get(platform, "🛒")
        in_stock = [p for p in result["products"] if p["in_stock"]]
        top      = sorted(in_stock, key=lambda p: p["price"])[:TOP_N]

        for p in top:
            tag   = " ✅" if (cheapest
                              and p["name"]     == cheapest["name"]
                              and p["platform"] == cheapest["platform"]) else ""
            label = f"{emoji} ₹{p['price']} — {p['name'][:22]}{tag}"
            # encode: sel|platform|price|product_name  (≤64 chars total)
            cb = f"sel|{platform}|{p['price']}|{p['name'][:25]}"
            buttons.append([InlineKeyboardButton(label, callback_data=cb[:64])])

    return InlineKeyboardMarkup(buttons) if buttons else None


# ---------- command handlers ----------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi! I'm your grocery price bot.\n\n"
        "Tell me what you want in plain English:\n"
        "• _I want to buy tomato_\n"
        "• _Get me 2 kg onions_\n"
        "• _Amul butter 500g_\n\n"
        "Commands:\n"
        "/cart — view your cart\n"
        "/clear — reset conversation\n",
        parse_mode="Markdown",
    )


async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _histories[update.effective_user.id].clear()
    await update.message.reply_text("🧹 Conversation history cleared.")


async def cmd_cart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cart    = _carts[user_id]
    text    = _cart_summary(cart)
    if cart:
        await update.message.reply_text(text, parse_mode="Markdown",
                                        reply_markup=_checkout_keyboard(cart))
    else:
        await update.message.reply_text(text, parse_mode="Markdown")


# ---------- message handler ----------

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text    = update.message.text.strip()
    history = _histories[user_id]

    thinking = await update.message.reply_text("⏳ Thinking...")

    # Ask LLM for intent
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{API_BASE}/nlp/chat",
                json={"message": text, "history": history},
            )
            resp.raise_for_status()
            result = resp.json()
    except Exception as e:
        await thinking.edit_text(f"❌ Error contacting server: {e}")
        return

    # Update history
    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content":
                    result.get("reply") or result.get("search_query") or str(result)})
    _histories[user_id] = history[-20:]

    intent = result.get("intent")

    if intent == "chat":
        await thinking.edit_text(
            result.get("reply", "🤔 Try asking for a product!"))
        return

    # intent == "search"
    product = result.get("product", text).strip()
    await thinking.edit_text(f"🔍 Searching for *{product}*...", parse_mode="Markdown")

    try:
        data     = await _search(product)
        msg      = _format_results(data)
        keyboard = _product_buttons(data)
        await thinking.edit_text(msg, parse_mode="Markdown", reply_markup=keyboard)
    except Exception as e:
        await thinking.edit_text(f"❌ Search failed: {e}")


# ---------- callback handlers ----------

async def handle_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Product button tapped → show quantity picker."""
    cb = update.callback_query
    await cb.answer()

    _, platform, price_str, product_name = cb.data.split("|", 3)
    user_id = cb.from_user.id
    _pending[user_id] = {
        "platform":     platform,
        "product_name": product_name,
        "price":        float(price_str),
    }

    qty_buttons = [
        [InlineKeyboardButton(str(q), callback_data=f"qty|{q}") for q in (1, 2, 3)],
        [InlineKeyboardButton(str(q), callback_data=f"qty|{q}") for q in (4, 5, 6)],
        [InlineKeyboardButton("❌ Cancel", callback_data="qty|cancel")],
    ]
    await cb.message.reply_text(
        f"🛒 *{product_name}*\n{PLATFORM_EMOJI.get(platform,'')} {platform.capitalize()} — ₹{price_str}\n\nHow many units?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(qty_buttons),
    )


async def handle_qty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Quantity chosen → add to in-memory cart."""
    cb = update.callback_query
    await cb.answer()
    await cb.edit_message_reply_markup(None)

    user_id = cb.from_user.id
    qty_str = cb.data.split("|", 1)[1]

    if qty_str == "cancel":
        _pending.pop(user_id, None)
        await cb.message.reply_text("❌ Cancelled.")
        return

    pending = _pending.pop(user_id, None)
    if not pending:
        await cb.message.reply_text("⚠️ Session expired, search again.")
        return

    qty          = int(qty_str)
    platform     = pending["platform"]
    product_name = pending["product_name"]
    price        = pending["price"]

    # Check if already in cart → update qty instead of duplicate
    cart = _carts[user_id]
    for item in cart:
        if item["platform"] == platform and item["product_name"] == product_name:
            item["qty"] += qty
            await cb.message.reply_text(
                f"✅ Updated: *{product_name}* × {item['qty']} in {platform.capitalize()} cart.\n\n"
                f"Use /cart to view or checkout.",
                parse_mode="Markdown",
            )
            return

    cart.append({"platform": platform, "product_name": product_name,
                 "price": price, "qty": qty})
    await cb.message.reply_text(
        f"✅ Added *{qty}× {product_name}* to {PLATFORM_EMOJI.get(platform,'')} {platform.capitalize()} cart.\n\n"
        f"Use /cart to view or checkout.",
        parse_mode="Markdown",
    )


async def handle_cart_action(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Clear cart button."""
    cb = update.callback_query
    await cb.answer()

    if cb.data == "cart|clear":
        _carts[cb.from_user.id].clear()
        await cb.edit_message_text("🗑 Cart cleared.")


# ---------- main ----------

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("clear",   cmd_clear))
    app.add_handler(CommandHandler("cart",    cmd_cart))
    app.add_handler(CallbackQueryHandler(handle_select,     pattern=r"^sel\|"))
    app.add_handler(CallbackQueryHandler(handle_qty,        pattern=r"^qty\|"))
    app.add_handler(CallbackQueryHandler(handle_cart_action, pattern=r"^cart\|"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Bot running — Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
