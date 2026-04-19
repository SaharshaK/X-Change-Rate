from urllib.parse import quote
from .base import BaseScraper, Product

# Swiggy Instamart uses AWS WAF bot protection — headless browsers are blocked.
# This scraper attempts the request but will return a clean error via safe_search().
# Workaround: use your real Chrome session via the persistent context (Chrome must be closed).

JS = """
() => {
    const results = [];
    const prices = Array.from(document.querySelectorAll('*')).filter(el =>
        el.children.length === 0 && /^\u20b9\s*\d+/.test((el.textContent || '').trim())
    );
    prices.forEach(priceEl => {
        let card = priceEl;
        for (let i = 0; i < 8; i++) {
            card = card.parentElement;
            if (!card) return;
            if (card.offsetHeight > 150 && card.offsetWidth > 80) break;
        }
        if (!card) return;

        const priceText = (priceEl.textContent || '').trim();
        const price = parseFloat(priceText.replace(/[^\d.]/g, ''));

        const nameEl = Array.from(card.querySelectorAll('*')).find(el =>
            el.children.length === 0 && (el.textContent || '').trim().length > 5 &&
            !/^\u20b9/.test((el.textContent || '').trim())
        );
        const name = nameEl ? (nameEl.textContent || '').trim() : '';

        const qtyEls = Array.from(card.querySelectorAll('*')).filter(el =>
            el.children.length === 0 &&
            /\d+\s*(g|kg|ml|L|ltr|pcs|pack)/i.test((el.textContent || '').trim())
        );
        const qty = qtyEls[0] ? qtyEls[0].textContent.trim() : '';

        const img = card.querySelector('img');
        const outOfStock = Array.from(card.querySelectorAll('button'))
            .some(b => b.disabled || (b.textContent || '').toLowerCase().includes('notify'));

        if (name && price) {
            results.push({
                name, price, mrp: null, quantity: qty,
                image_url: img ? img.src : null, in_stock: !outOfStock
            });
        }
    });

    // Deduplicate by name
    const seen = new Set();
    return results.filter(r => {
        if (seen.has(r.name)) return false;
        seen.add(r.name);
        return true;
    });
}
"""


class InstamartScraper(BaseScraper):
    platform = "instamart"
    cookie_domains = ["swiggy.com"]
    BASE_URL = "https://www.swiggy.com"

    async def search(self, query: str) -> list:
        page = await self.new_page()
        try:
            url = f"{self.BASE_URL}/instamart/search?query={quote(query)}"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            body_text = await page.evaluate("() => document.body.innerText.substring(0, 200)")
            if "something went wrong" in body_text.lower():
                raise RuntimeError(
                    "Instamart blocked the request (AWS WAF). "
                    "Close Chrome and re-run so Playwright can use your real Chrome session."
                )

            await page.wait_for_selector(
                "[class*='ItemWidget'], div[data-testid*='item'], div[class*='product']",
                timeout=10000
            )

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
