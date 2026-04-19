from __future__ import annotations

from html import escape
from typing import Any, Dict, List


PLATFORM_LABELS = {
    "blinkit": "Blinkit",
    "zepto": "Zepto",
    "instamart": "Instamart",
}


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform.title())


def format_price(value: float | None) -> str:
    if value is None:
        return "NA"
    if float(value).is_integer():
        return f"Rs {int(value)}"
    return f"Rs {value:.2f}"


def summarize_product(product: Dict[str, Any]) -> str:
    parts = [
        f"<b>{escape(product['name'])}</b>",
        f"{format_price(product.get('price'))}",
    ]

    quantity = product.get("quantity")
    if quantity:
        parts.append(escape(quantity))

    if product.get("discount_percent") is not None:
        parts.append(f"{product['discount_percent']}% off")

    stock = "In stock" if product.get("in_stock") else "Out of stock"
    parts.append(stock)
    return " | ".join(parts)


def format_compare_response(
    response: Dict[str, Any],
    *,
    max_products_per_platform: int,
) -> str:
    query = escape(response["query"])
    lines: List[str] = [f"<b>Results for:</b> {query}"]

    cheapest = response.get("cheapest")
    if cheapest:
        lines.append(
            "<b>Cheapest:</b> "
            f"{escape(cheapest['name'])} on {platform_label(cheapest['platform'])} "
            f"for {format_price(cheapest.get('price'))}"
        )
    else:
        lines.append("<b>Cheapest:</b> No in-stock match found yet.")

    for platform, result in response.get("results", {}).items():
        lines.append("")
        lines.append(f"<b>{platform_label(platform)}</b> [{escape(result['status'])}]")
        if result.get("error"):
            lines.append(f"Error: {escape(result['error'])}")
            continue

        products = result.get("products", [])[:max_products_per_platform]
        if not products:
            lines.append("No matching products found.")
            continue

        for product in products:
            lines.append(summarize_product(product))

    return "\n".join(lines)


def format_cheapest_response(product: Dict[str, Any] | None, query: str) -> str:
    if not product:
        return f"No in-stock result found for <b>{escape(query)}</b>."

    return (
        f"<b>Cheapest for:</b> {escape(query)}\n"
        f"{summarize_product(product)}\n"
        f"Platform: <b>{platform_label(product['platform'])}</b>"
    )


def format_suggestions_response(query: str, suggestions: List[str]) -> str:
    if not suggestions:
        return f"No suggestions found for <b>{escape(query)}</b>."

    lines = [f"<b>Suggestions for:</b> {escape(query)}"]
    for suggestion in suggestions[:10]:
        lines.append(f"• {escape(suggestion)}")
    return "\n".join(lines)
