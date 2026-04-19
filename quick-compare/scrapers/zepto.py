from typing import Optional
from urllib.parse import quote
from .base import BaseScraper, Product

ADD_TO_CART_JS = """
async (args) => {
    const productName = (args && args.productName) || '';
    const wantPrice = args && args.price != null && !isNaN(Number(args.price)) ? Number(args.price) : null;
    const norm = (s) => (s || '')
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, ' ')
        .replace(/\\s+/g, ' ')
        .trim();
    const wantName = norm(productName);
    const wantTokens = wantName.split(' ').filter(Boolean);
    const overlapScore = (candidate) => {
        const tokens = new Set(norm(candidate).split(' ').filter(Boolean));
        if (wantTokens.length === 0 || tokens.size === 0) return 0;
        let matches = 0;
        wantTokens.forEach(token => {
            if (tokens.has(token)) matches += 1;
        });
        return matches / wantTokens.length;
    };
    const buttonText = (btn) => (btn?.textContent || '').trim().toUpperCase().replace(/\\s+/g, ' ');
    const isAddButton = (btn) => {
        const t = buttonText(btn);
        return t === 'ADD' || t === '+' || t === 'ADD TO CART' || t.startsWith('ADD ');
    };

    const cards = document.querySelectorAll("div[data-is-out-of-stock]");
    const candidates = [];
    cards.forEach(card => {
        const img = card.querySelector('img');
        const name = (img ? img.alt : '').trim();
        if (!name) return;
        if (card.getAttribute('data-is-out-of-stock') === 'true') return;
        let price = null;
        Array.from(card.querySelectorAll('*')).forEach(el => {
            const t = (el.textContent || '').trim();
            if (/^\\u20b9\\s*\\d+$/.test(t) && el.children.length === 0) {
                const v = parseFloat(t.replace(/[^\\d.]/g, ''));
                if (price == null) price = v;
            }
        });
        if (price == null) return;
        const buttons = Array.from(card.querySelectorAll('button')).filter(btn => !btn.disabled);
        const addBtn = buttons.find(isAddButton);
        candidates.push({
            card,
            name,
            price,
            n: norm(name),
            overlap: overlapScore(name),
            addBtn,
        });
    });

    if (candidates.length === 0) {
        return { success: false, error: 'No Zepto product cards were detected on the search page' };
    }

    const ranked = candidates
        .map(c => {
            const exact = c.n === wantName ? 1 : 0;
            const contains = c.n.includes(wantName) || wantName.includes(c.n) ? 1 : 0;
            const priceScore = wantPrice != null ? Math.max(0, 1 - Math.min(Math.abs(c.price - wantPrice), 25) / 25) : 0;
            return {
                ...c,
                score: exact * 100 + contains * 20 + c.overlap * 50 + priceScore * 10 + (c.addBtn ? 5 : -1000),
            };
        })
        .sort((a, b) => b.score - a.score);

    const chosen = ranked[0];
    if (!chosen || chosen.score < 10) {
        return { success: false, error: 'Could not confidently match the requested Zepto product' };
    }
    if (!chosen.addBtn) {
        return { success: false, error: 'Matched Zepto product does not have a clickable ADD button' };
    }

    chosen.card.scrollIntoView({ block: 'center', behavior: 'instant' });
    chosen.addBtn.click();
    await new Promise(r => setTimeout(r, 1200));

    const clickedText = buttonText(chosen.addBtn);
    const quantityButton = Array.from(chosen.card.querySelectorAll('button')).find(b => {
        const t = buttonText(b);
        return t === '+' || t === '-' || /^\\d+$/.test(t);
    });

    if (quantityButton || (clickedText && clickedText !== 'ADD' && clickedText !== 'ADD TO CART')) {
        return { success: true, name: chosen.name };
    }

    return { success: false, error: 'Zepto did not confirm the add-to-cart action after clicking ADD' };
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
            body = await page.inner_text("body")
            if "select location" in body.lower() or "choose location" in body.lower():
                raise RuntimeError(
                    "Zepto needs a delivery location before search can work. Open Chrome, set the address on Zepto, then retry."
                )
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
            body = await page.inner_text("body")
            if "select location" in body.lower() or "choose location" in body.lower():
                raise RuntimeError(
                    "Zepto needs a delivery location before add-to-cart can work. Set the address in Chrome and retry."
                )
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
