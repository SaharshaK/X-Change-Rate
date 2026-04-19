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
DEFAULT_LATITUDE = float(os.getenv("DEFAULT_LATITUDE", "12.9716"))
DEFAULT_LONGITUDE = float(os.getenv("DEFAULT_LONGITUDE", "77.5946"))
DEFAULT_ADDRESS_LABEL = os.getenv("DEFAULT_ADDRESS_LABEL", "").strip()

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
                geolocation={"latitude": DEFAULT_LATITUDE, "longitude": DEFAULT_LONGITUDE},
                permissions=["geolocation"],
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
            ),
            geolocation={"latitude": DEFAULT_LATITUDE, "longitude": DEFAULT_LONGITUDE},
            permissions=["geolocation"],
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
            f"""
            Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
            const defaultPosition = {{
              coords: {{
                latitude: {DEFAULT_LATITUDE},
                longitude: {DEFAULT_LONGITUDE},
                accuracy: 25,
              }},
              timestamp: Date.now(),
            }};
            navigator.geolocation.getCurrentPosition = (success) => success(defaultPosition);
            navigator.geolocation.watchPosition = (success) => {{
              success(defaultPosition);
              return 1;
            }};
            navigator.geolocation.clearWatch = () => {{}};
            """
        )
        return page

    async def ensure_default_location(self, page: Page) -> None:
        await page.wait_for_timeout(1200)

        for _ in range(3):
            try:
                body = (await page.inner_text("body")).lower()
            except Exception:
                return

            if not any(
                phrase in body
                for phrase in (
                    "select location",
                    "choose location",
                    "enter your location",
                    "delivery location",
                    "use current location",
                )
            ):
                return

            try:
                await page.evaluate(
                    """(addressLabel) => {
                        const normalize = (s) => (s || '').trim().toLowerCase().replace(/\\s+/g, ' ');
                        const clickByText = (texts) => {
                            const targets = Array.from(document.querySelectorAll('button, a, div[role="button"]'));
                            for (const el of targets) {
                                const text = normalize(el.textContent);
                                if (!text) continue;
                                if (texts.some(t => text.includes(t))) {
                                    el.click();
                                    return true;
                                }
                            }
                            return false;
                        };

                        clickByText([
                            'use current location',
                            'current location',
                            'allow location',
                            'detect my location',
                            'share location',
                            'use my location',
                            'confirm location',
                            'set location',
                        ]);

                        if (!addressLabel) return;

                        const input = Array.from(document.querySelectorAll('input')).find((el) => {
                            const hint = normalize((el.getAttribute('placeholder') || '') + ' ' + (el.getAttribute('aria-label') || ''));
                            return (
                                hint.includes('search') ||
                                hint.includes('location') ||
                                hint.includes('address') ||
                                hint.includes('area') ||
                                hint.includes('pincode')
                            );
                        });

                        if (!input) return;
                        input.focus();
                        input.value = addressLabel;
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    DEFAULT_ADDRESS_LABEL,
                )
            except Exception:
                return

            await page.wait_for_timeout(1800)

            if DEFAULT_ADDRESS_LABEL:
                try:
                    await page.evaluate(
                        """(addressLabel) => {
                            const normalize = (s) => (s || '').trim().toLowerCase().replace(/\\s+/g, ' ');
                            const target = normalize(addressLabel);
                            const options = Array.from(document.querySelectorAll('button, li, [role="option"], a, div'));
                            for (const el of options) {
                                const text = normalize(el.textContent);
                                if (!text || text.length < 3) continue;
                                if (text.includes(target) || target.includes(text)) {
                                    el.click();
                                    return true;
                                }
                            }
                            return false;
                        }""",
                        DEFAULT_ADDRESS_LABEL,
                    )
                except Exception:
                    return

                await page.wait_for_timeout(1500)

    @abstractmethod
    async def search(self, query: str) -> List[Product]:
        pass

    async def safe_search(self, query: str) -> Tuple[List[Product], Optional[str]]:
        try:
            products = await self.search(query)
            return products, None
        except Exception as e:
            return [], str(e)

    async def add_to_cart(
        self, query: str, product_name: str, price: Optional[float] = None
    ) -> bool:
        raise NotImplementedError(f"{self.platform} does not support add-to-cart")

    async def safe_add_to_cart(
        self, query: str, product_name: str, price: Optional[float] = None
    ) -> Tuple[bool, Optional[str]]:
        try:
            success = await self.add_to_cart(query, product_name, price)
            return success, None
        except NotImplementedError as e:
            return False, str(e)
        except Exception as e:
            return False, str(e)
