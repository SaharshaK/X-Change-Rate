from urllib.parse import quote
from .base import BaseScraper, Product

JS = """
() => {
    const results = [];
    // Zepto uses data-is-out-of-stock attribute on product cards
    const cards = document.querySelectorAll("div[data-is-out-of-stock]");
    cards.forEach(card => {
        const img = card.querySelector('img');
        const name = img ? img.alt : '';

        let price = null, mrp = null;
        Array.from(card.querySelectorAll('*')).forEach(el => {
            const t = (el.textContent || '').trim();
            if (/^\u20b9\s*\d+$/.test(t) && el.children.length === 0) {
                if (!price) price = parseFloat(t.replace(/[^\d.]/g, ''));
                else if (!mrp && parseFloat(t.replace(/[^\d.]/g, '')) > price) {
                    mrp = parseFloat(t.replace(/[^\d.]/g, ''));
                }
            }
        });

        const qtyEls = Array.from(card.querySelectorAll('*')).filter(el =>
            el.children.length === 0 &&
            /\d+\s*(g|kg|ml|L|ltr|pcs|pack|piece)/i.test((el.textContent || '').trim())
        );
        const qty = qtyEls[0] ? qtyEls[0].textContent.trim() : '';

        const outOfStock = card.getAttribute('data-is-out-of-stock') === 'true';

        if (name && price) {
            results.push({
                name,
                price,
                mrp,
                quantity: qty,
                image_url: img ? img.src : null,
                in_stock: !outOfStock,
            });
        }
    });
    return results;
}
"""


class ZeptoScraper(BaseScraper):
    platform = "zepto"
    cookie_domains = ["zeptonow.com"]
    BASE_URL = "https://www.zeptonow.com"

    async def search(self, query: str) -> list:
        page = await self.new_page()
        try:
            url = f"{self.BASE_URL}/search?query={quote(query)}"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3500)
            await page.wait_for_selector("div[data-is-out-of-stock]", timeout=15000)

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
