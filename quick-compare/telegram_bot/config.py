from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).parent.parent / ".env")


@dataclass(frozen=True)
class BotConfig:
    token: str
    api_base_url: str
    default_platforms: str
    max_products_per_platform: int


def load_config() -> BotConfig:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required to run the Telegram bot.")

    api_base_url = os.getenv("QUICK_COMPARE_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    default_platforms = os.getenv("TELEGRAM_DEFAULT_PLATFORMS", "blinkit,zepto,instamart")
    max_products = int(os.getenv("TELEGRAM_MAX_PRODUCTS_PER_PLATFORM", "1"))

    return BotConfig(
        token=token,
        api_base_url=api_base_url,
        default_platforms=default_platforms,
        max_products_per_platform=max(1, max_products),
    )
