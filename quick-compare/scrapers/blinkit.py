from urllib.parse import quote
from .base import BaseScraper, Product

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
