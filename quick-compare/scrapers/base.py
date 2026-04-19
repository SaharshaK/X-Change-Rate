from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Tuple, List

from playwright.async_api import async_playwright, BrowserContext, Page
import os

CHROME_USER_DATA = os.getenv(
    "CHROME_USER_DATA",
    os.path.expanduser("~/Library/Application Support/Google/Chrome"),
)
CHROME_PROFILE = os.getenv("CHROME_PROFILE", "Default")

LAUNCH_ARGS = [
    f"--profile-directory={CHROME_PROFILE}",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
]


@dataclass
class Product:
    name: str
    price: float
    mrp: Optional[float]
    quantity: str
    image_url: Optional[str]
    platform: str
    in_stock: bool
    url: Optional[str] = None

    def discount_percent(self) -> Optional[float]:
        if self.mrp and self.mrp > self.price:
            return round((self.mrp - self.price) / self.mrp * 100, 1)
        return None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "price": self.price,
            "mrp": self.mrp,
            "quantity": self.quantity,
            "image_url": self.image_url,
            "platform": self.platform,
            "in_stock": self.in_stock,
            "url": self.url,
            "discount_percent": self.discount_percent(),
        }


class BaseScraper(ABC):
    platform: str = ""
    cookie_domains: List[str] = []  # override in subclass

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._context: Optional[BrowserContext] = None
        self._browser = None
        self._playwright = None

    async def __aenter__(self):
        self._playwright = await async_playwright().start()

        # --- attempt 1: reuse Chrome profile (Chrome must be closed) ---
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=CHROME_USER_DATA,
                channel="chrome",
                headless=self.headless,
                args=LAUNCH_ARGS,
                ignore_default_args=["--enable-automation"],
            )
            return self
        except Exception:
            pass  # Chrome is open — fall through to cookie injection

        # --- attempt 2: fresh browser + injected Chrome cookies ---
        from .cookie_extractor import extract_cookies

        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )

        if self.cookie_domains:
            cookies = extract_cookies(self.cookie_domains)
            if cookies:
                await self._context.add_cookies(cookies)

        return self

    async def __aexit__(self, *args):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def new_page(self) -> Page:
        page = await self._context.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        return page

    @abstractmethod
    async def search(self, query: str) -> List[Product]:
        pass

    async def safe_search(self, query: str) -> Tuple[List[Product], Optional[str]]:
        try:
            products = await self.search(query)
            return products, None
        except Exception as e:
            return [], str(e)
