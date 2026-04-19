from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import DefaultDict, Deque, Dict, List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from api.nlp import chat as nlp_chat

from .client import QuickCompareApiClient
from .config import BotConfig, load_config
from .formatting import (
    format_cheapest_response,
    format_compare_response,
    format_suggestions_response,
)


logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

SUPPORTED_PLATFORMS = ("blinkit", "zepto", "instamart")


def _extract_platforms(text: str, fallback: str) -> str:
    lowered = text.lower()
    matches = [platform for platform in SUPPORTED_PLATFORMS if platform in lowered]
    if matches:
        return ",".join(matches)
    return fallback


def _build_keyboard(response: Dict) -> InlineKeyboardMarkup | None:
    buttons: List[List[InlineKeyboardButton]] = []
    cheapest = response.get("cheapest")
    if cheapest and cheapest.get("url"):
        buttons.append(
            [InlineKeyboardButton(f"Open {cheapest['platform'].title()}", url=cheapest["url"])]
        )

    platform_buttons = []
    for platform, result in response.get("results", {}).items():
        products = result.get("products", [])
        if products and products[0].get("url"):
            platform_buttons.append(
                InlineKeyboardButton(platform.title(), url=products[0]["url"])
            )

    if platform_buttons:
        buttons.append(platform_buttons[:3])

    return InlineKeyboardMarkup(buttons) if buttons else None


class ConversationMemory:
    def __init__(self, max_messages: int = 8):
        self._history: DefaultDict[int, Deque[Dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=max_messages)
        )
        self._last_search: Dict[int, str] = {}

    def history(self, chat_id: int) -> List[Dict[str, str]]:
        return list(self._history[chat_id])

    def append_user(self, chat_id: int, message: str) -> None:
        self._history[chat_id].append({"role": "user", "content": message})

    def append_assistant(self, chat_id: int, message: str) -> None:
        self._history[chat_id].append({"role": "assistant", "content": message})

    def remember_search(self, chat_id: int, query: str) -> None:
        self._last_search[chat_id] = query

    def last_search(self, chat_id: int) -> str | None:
        return self._last_search.get(chat_id)


async def _send_html(
    update: Update,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if update.message:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = (
        "Send me any grocery request like <b>1 dozen bananas</b> or "
        "<b>compare amul butter 500g</b> and I’ll check Blinkit, Zepto, and Instamart.\n\n"
        "Commands:\n"
        "/compare &lt;query&gt;\n"
        "/cheapest &lt;query&gt;\n"
        "/platform &lt;blinkit|zepto|instamart&gt; &lt;query&gt;\n"
        "/suggest &lt;partial query&gt;"
    )
    await _send_html(update, message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_command(update, context)


async def compare_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip()
    if not query:
        await _send_html(update, "Usage: <b>/compare amul butter 500g</b>")
        return

    await run_compare(update, context, query=query, smart=True)


async def cheapest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip()
    if not query:
        await _send_html(update, "Usage: <b>/cheapest amul butter 500g</b>")
        return

    api: QuickCompareApiClient = context.application.bot_data["api_client"]
    config: BotConfig = context.application.bot_data["config"]
    memory: ConversationMemory = context.application.bot_data["memory"]

    product = await api.cheapest(query, platforms=config.default_platforms)
    memory.remember_search(update.effective_chat.id, query)
    reply = format_cheapest_response(product, query)
    memory.append_assistant(update.effective_chat.id, reply)
    await _send_html(update, reply)


async def platform_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await _send_html(update, "Usage: <b>/platform zepto tata salt</b>")
        return

    platform = context.args[0].lower().strip()
    if platform not in SUPPORTED_PLATFORMS:
        supported = ", ".join(SUPPORTED_PLATFORMS)
        await _send_html(update, f"Platform must be one of: <b>{supported}</b>")
        return

    query = " ".join(context.args[1:]).strip()
    await run_compare(update, context, query=query, platforms=platform, smart=True)


async def suggest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip()
    if len(query) < 2:
        await _send_html(update, "Usage: <b>/suggest amul</b>")
        return

    api: QuickCompareApiClient = context.application.bot_data["api_client"]
    response = await api.suggest(query)
    await _send_html(update, format_suggestions_response(query, response.get("suggestions", [])))


async def run_compare(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    query: str,
    platforms: str | None = None,
    smart: bool,
) -> None:
    api: QuickCompareApiClient = context.application.bot_data["api_client"]
    config: BotConfig = context.application.bot_data["config"]
    memory: ConversationMemory = context.application.bot_data["memory"]

    selected_platforms = platforms or _extract_platforms(query, config.default_platforms)
    response = await api.compare(
        query,
        platforms=selected_platforms,
        smart=smart,
    )
    reply = format_compare_response(
        response,
        max_products_per_platform=config.max_products_per_platform,
    )
    memory.remember_search(update.effective_chat.id, response["query"])
    memory.append_assistant(update.effective_chat.id, reply)
    await _send_html(update, reply, reply_markup=_build_keyboard(response))


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if not text:
        return

    memory: ConversationMemory = context.application.bot_data["memory"]
    chat_id = update.effective_chat.id
    history = memory.history(chat_id)
    memory.append_user(chat_id, text)

    last_search = memory.last_search(chat_id)
    only_platform_switch = (
        last_search is not None
        and any(platform in text.lower() for platform in SUPPORTED_PLATFORMS)
        and len(text.split()) <= 5
    )
    if only_platform_switch:
        await run_compare(
            update,
            context,
            query=last_search,
            platforms=_extract_platforms(text, context.application.bot_data["config"].default_platforms),
            smart=False,
        )
        return

    nlp_result = await nlp_chat(text, history=history)
    if nlp_result.get("intent") == "chat":
        reply = nlp_result.get(
            "reply",
            "Send me a grocery item and I’ll compare prices for you.",
        )
        memory.append_assistant(chat_id, reply)
        await _send_html(update, reply)
        return

    query = nlp_result.get("search_query") or text
    await run_compare(
        update,
        context,
        query=query,
        platforms=_extract_platforms(text, context.application.bot_data["config"].default_platforms),
        smart=False,
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Telegram bot error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "Something went wrong while talking to the search service. "
            "Please try again in a moment."
        )


async def post_init(application: Application) -> None:
    config = load_config()
    application.bot_data["config"] = config
    application.bot_data["api_client"] = QuickCompareApiClient(config.api_base_url)
    application.bot_data["memory"] = ConversationMemory()


async def post_shutdown(application: Application) -> None:
    api: QuickCompareApiClient | None = application.bot_data.get("api_client")
    if api:
        await api.close()


def build_application() -> Application:
    config = load_config()
    application = (
        Application.builder()
        .token(config.token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("compare", compare_command))
    application.add_handler(CommandHandler("cheapest", cheapest_command))
    application.add_handler(CommandHandler("platform", platform_command))
    application.add_handler(CommandHandler("suggest", suggest_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    application = build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
