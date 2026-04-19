from typing import Optional
from urllib.parse import quote
from .base import BaseScraper, Product

ADD_TO_CART_JS = """
async (args) => {
    const productName = (args && args.productName) || '';
    const wantPrice = args && args.price != null && !isNaN(Number(args.price)) ? Number(args.price) : null;
    const norm = (s) => (s || '').trim().toLowerCase().replace(/\\s+/g, ' ');
    const wantName = norm(productName);

    const cards = document.querySelectorAll("div[data-is-out-of-stock]");
    const candidates = [];
    cards.forEach(card => {
        const img = card.querySelector('img');
        const name = (img ? img.alt : '').trim();
        if (!name) return;
        let price = null;
        Array.from(card.querySelectorAll('*')).forEach(el => {
            const t = (el.textContent || '').trim();
            if (/^\\u20b9\\s*\\d+$/.test(t) && el.children.length === 0) {
                const v = parseFloat(t.replace(/[^\\d.]/g, ''));
                if (price == null) price = v;
            }
        });
        if (price == null) return;
        candidates.push({ card, name, price, n: norm(name) });
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
    const btns = Array.from(chosen.card.querySelectorAll('button'));
    const addBtn = btns.find(b => {
        const t = (b.textContent || '').trim().toUpperCase();
        return t === 'ADD' || t === '+' || t === 'ADD TO CART';
    }) || btns[btns.length - 1];
    if (!addBtn) return { success: false, error: 'ADD button not found' };
    addBtn.click();
    await new Promise(r => setTimeout(r, 800));
    return { success: true, name: chosen.name };
}
"""

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

    async def add_to_cart(
        self, query: str, product_name: str, price: Optional[float] = None
    ) -> bool:
        page = await self.new_page()
        try:
            url = f"{self.BASE_URL}/search?query={quote(query)}"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            await page.wait_for_selector("div[data-is-out-of-stock]", timeout=15000)

            result = await page.evaluate(
                ADD_TO_CART_JS, {"productName": product_name, "price": price}
            )
            if not result.get("success"):
                raise RuntimeError(result.get("error", "Could not click ADD button"))

            await page.wait_for_timeout(1000)
            return True
        finally:
            await page.close()
