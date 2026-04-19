#!/usr/bin/env python3
"""Entry point — starts FastAPI server + Telegram bot together."""
import asyncio
import os
import threading
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def run_server():
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,  # reload=True is incompatible with threading
    )


async def run_bot():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("⚠️  TELEGRAM_BOT_TOKEN not set — bot will not start.")
        return

    # Import here so missing token doesn't crash the server
    from telegram.ext import (
        Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters,
    )
    from bot import (
        cmd_start, cmd_clear, cmd_cart,
        handle_message, handle_select, handle_qty, handle_cart_action,
    )

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("clear",  cmd_clear))
    app.add_handler(CommandHandler("cart",   cmd_cart))
    app.add_handler(CallbackQueryHandler(handle_select,      pattern=r"^sel\|"))
    app.add_handler(CallbackQueryHandler(handle_qty,         pattern=r"^qty\|"))
    app.add_handler(CallbackQueryHandler(handle_cart_action, pattern=r"^cart\|"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Telegram bot running.")
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        # Run forever until cancelled
        await asyncio.Event().wait()


async def main():
    # Run server in a background thread (uvicorn is not async-native)
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    print("🚀 API server running on http://0.0.0.0:8000")

    await run_bot()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Shutting down.")
