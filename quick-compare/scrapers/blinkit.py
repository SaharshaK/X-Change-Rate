from typing import Optional
from urllib.parse import quote
from .base import BaseScraper, Product

ADD_TO_CART_JS = """
async (args) => {
    const productName = (args && args.productName) || '';
    const wantPrice = args && args.price != null && !isNaN(Number(args.price)) ? Number(args.price) : null;
    const norm = (s) => (s || '').trim().toLowerCase().replace(/\\s+/g, ' ');
    const wantName = norm(productName);

    const nameEls = document.querySelectorAll("div[class*='tw-line-clamp-2']");
    const candidates = [];
    nameEls.forEach(nameEl => {
        const text = (nameEl.textContent || '').trim();
        if (!text) return;
        const card = nameEl.closest("div[class*='tw-px-3']") || nameEl.parentElement?.parentElement?.parentElement?.parentElement;
        if (!card) return;
        let price = null;
        Array.from(card.querySelectorAll('*')).forEach(el => {
            const t = (el.textContent || '').trim();
            if (/^\\u20b9\\s*\\d+$/.test(t) && el.children.length === 0) {
                if (price == null) price = parseFloat(t.replace(/[^\\d.]/g, ''));
            }
        });
        if (price == null) return;
        candidates.push({ card, name: text, price, n: norm(text) });
    });

    let picks = candidates.filter(c => c.n === wantName);
    if (picks.length > 1 && wantPrice != null) {
        const byP = picks.filter(c => Math.abs(c.price - wantPrice) < 0.51);
        if (byP.length >= 1) picks = byP;
    }
    if (picks.length === 0) {
        picks = candidates.filter(c => c.n.includes(wantName) || wantName.includes(c.n));
    }
    if (picks.length > 1 && wantPrice != null) {
        const byP = picks.filter(c => Math.abs(c.price - wantPrice) < 0.51);
        if (byP.length >= 1) picks = byP;
    }
    if (picks.length > 1) {
        picks.sort((a, b) => Math.abs(a.n.length - wantName.length) - Math.abs(b.n.length - wantName.length));
        picks = [picks[0]];
    }
    if (picks.length === 0) {
        return { success: false, error: 'Product not found or ADD button not visible' };
    }
    const chosen = picks[0];
    const addBtn = Array.from(chosen.card.querySelectorAll('button')).find(b => {
        const t = (b.textContent || '').trim().toUpperCase();
        return t === 'ADD' || t === '+';
    });
    if (!addBtn) return { success: false, error: 'ADD button not found' };
    addBtn.click();
    await new Promise(r => setTimeout(r, 800));
    return { success: true, name: chosen.name };
}
"""

JS = """
() => {
    const results = [];
    const nameEls = document.querySelectorAll("div[class*='tw-line-clamp-2']");
    nameEls.forEach(nameEl => {
        const card = nameEl.closest("div[class*='tw-px-3']") || nameEl.parentElement.parentElement.parentElement.parentElement;
        if (!card) return;

        let price = null, mrp = null;
        Array.from(card.querySelectorAll('*')).forEach(el => {
            const t = (el.textContent || '').trim();
            if (/^\u20b9\s*\d+$/.test(t) && el.children.length === 0) {
                if (!price) price = parseFloat(t.replace(/[^\\d.]/g, ''));
                else if (!mrp) mrp = parseFloat(t.replace(/[^\\d.]/g, ''));
            }
        });

        const qtyEl = card.querySelector("[class*='tw-text-base-green'], [class*='tw-text-200']");
        const qty = qtyEl ? (qtyEl.textContent || '').trim().split("\\n")[0] : '';

        // Product image: prefer the one with cdn.grofers.com/app-assets (not icons)
        let imgSrc = null;
        card.querySelectorAll('img').forEach(img => {
            if (!imgSrc && img.src && img.src.includes('app-assets')) imgSrc = img.src;
        });
        if (!imgSrc) {
            const img = card.querySelector('img');
            if (img && !img.src.includes('eta-icons')) imgSrc = img.src;
        }

        const outOfStock = (card.textContent || '').toLowerCase().includes('out of stock');
        const name = (nameEl.textContent || '').trim();
        if (name && price) {
            results.push({ name, price, mrp, quantity: qty, image_url: imgSrc, in_stock: !outOfStock });
        }
    });
    return results;
}
"""


class BlinkitScraper(BaseScraper):
    platform = "blinkit"
    cookie_domains = ["blinkit.com"]
    BASE_URL = "https://blinkit.com"

    async def search(self, query: str) -> list:
        page = await self.new_page()
        try:
            url = f"{self.BASE_URL}/s/?q={quote(query)}"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3500)

            # Detect login wall — Blinkit redirects to /login when session expires
            if "/login" in page.url or "/signin" in page.url:
                raise RuntimeError(
                    "Not logged in to Blinkit. Open Chrome, log in at blinkit.com, then restart the server."
                )

            # Detect location prompt (user not set a delivery address)
            body = await page.inner_text("body")
            if "enter your location" in body.lower() or "select location" in body.lower():
                raise RuntimeError(
                    "Blinkit needs a delivery location. Open Chrome, set your address on blinkit.com, then retry."
                )

            await page.wait_for_selector("div[class*='tw-line-clamp-2']", timeout=15000)

            products = await page.evaluate(JS)

            return [
                Product(
                    name=p["name"],
                    price=p["price"],
                    mrp=p.get("mrp"),
                    quantity=p.get("quantity", ""),
                    image_url=p.get("image_url"),
                    platform=self.platform,
                    in_stock=p.get("in_stock", True),
                    url=url,
                )
                for p in products
                if p.get("price", 0) > 0
            ]
        finally:
            await page.close()

    async def add_to_cart(
        self, query: str, product_name: str, price: Optional[float] = None
    ) -> bool:
        page = await self.new_page()
        try:
            url = f"{self.BASE_URL}/s/?q={quote(query)}"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            if "/login" in page.url or "/signin" in page.url:
                raise RuntimeError("Not logged in to Blinkit. Open Chrome, log in, then restart the server.")

            await page.wait_for_selector("div[class*='tw-line-clamp-2']", timeout=15000)

            result = await page.evaluate(
                ADD_TO_CART_JS, {"productName": product_name, "price": price}
            )
            if not result.get("success"):
                raise RuntimeError(result.get("error", "Could not click ADD button"))

            await page.wait_for_timeout(1000)
            return True
        finally:
            await page.close()
