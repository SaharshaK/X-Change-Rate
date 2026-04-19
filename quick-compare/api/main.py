from __future__ import annotations
import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scrapers import BlinkitScraper, ZeptoScraper, InstamartScraper
from scrapers.base import Product
from db import init_db, get_cached, set_cached, clear_cache, suggest_names


# ---------- models ----------

class ProductOut(BaseModel):
    name: str
    price: float
    mrp: Optional[float]
    quantity: str
    image_url: Optional[str]
    platform: str
    in_stock: bool
    url: Optional[str]
    discount_percent: Optional[float]


class PlatformResult(BaseModel):
    platform: str
    products: List[ProductOut]
    status: str           # "success" | "error" | "cached"
    error: Optional[str]
    search_time_ms: int


class CompareResponse(BaseModel):
    query: str
    timestamp: str
    results: Dict[str, PlatformResult]
    cheapest: Optional[ProductOut]
    summary: Dict[str, Optional[float]]   # platform -> lowest price


# ---------- helpers ----------

SCRAPER_MAP = {
    "blinkit": BlinkitScraper,
    "zepto": ZeptoScraper,
    "instamart": InstamartScraper,
}


def product_to_out(p: Product) -> ProductOut:
    return ProductOut(**p.to_dict())


def is_relevant(product_name: str, query: str) -> bool:
    """
    Require every word in the query to appear as a whole word in the product name.
    Prevents 'butter' from matching 'buttermilk', 'butterscotch', etc.
    """
    import re
    name = product_name.lower()
    for word in query.lower().split():
        if not re.search(rf"\b{re.escape(word)}\b", name):
            return False
    return True


async def fetch_platform(query: str, platform: str, headless: bool) -> PlatformResult:
    cached = await get_cached(query, platform)
    if cached is not None:
        filtered = [ProductOut(**p) for p in cached if is_relevant(p["name"], query)]
        return PlatformResult(
            platform=platform,
            products=filtered,
            status="cached",
            error=None,
            search_time_ms=0,
        )

    scraper_cls = SCRAPER_MAP[platform]
    start = time.time()
    async with scraper_cls(headless=headless) as scraper:
        products, error = await scraper.safe_search(query)

    elapsed = int((time.time() - start) * 1000)

    if not error:
        await set_cached(query, platform, [p.to_dict() for p in products])

    filtered = [product_to_out(p) for p in products if is_relevant(p.name, query)]

    return PlatformResult(
        platform=platform,
        products=filtered,
        status="error" if error else "success",
        error=error,
        search_time_ms=elapsed,
    )


def find_cheapest(results: Dict[str, PlatformResult]) -> Optional[ProductOut]:
    best: Optional[ProductOut] = None
    for result in results.values():
        for p in result.products:
            if p.in_stock and (best is None or p.price < best.price):
                best = p
    return best


def build_summary(results: Dict[str, PlatformResult]) -> Dict[str, Optional[float]]:
    summary = {}
    for platform, result in results.items():
        in_stock = [p.price for p in result.products if p.in_stock]
        summary[platform] = min(in_stock) if in_stock else None
    return summary


# ---------- app ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Quick Compare API",
    description="Price comparison across Blinkit, Zepto, and Instamart",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- routes ----------

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/platforms")
async def list_platforms():
    return {"platforms": list(SCRAPER_MAP.keys())}


@app.get("/compare", response_model=CompareResponse)
async def compare(
    q: str = Query(..., description="Product to search, e.g. 'amul butter 500g'"),
    platforms: str = Query("blinkit,zepto,instamart", description="Comma-separated platforms"),
    headless: bool = Query(True, description="Run browser headless"),
):
    requested = [p.strip().lower() for p in platforms.split(",")]
    invalid = [p for p in requested if p not in SCRAPER_MAP]
    if invalid:
        raise HTTPException(400, f"Unknown platforms: {invalid}. Valid: {list(SCRAPER_MAP.keys())}")

    # Fetch all platforms concurrently
    tasks = [fetch_platform(q, platform, headless) for platform in requested]
    platform_results = await asyncio.gather(*tasks)

    results = {r.platform: r for r in platform_results}
    cheapest = find_cheapest(results)
    summary = build_summary(results)

    return CompareResponse(
        query=q,
        timestamp=datetime.now(timezone.utc).isoformat(),
        results=results,
        cheapest=cheapest,
        summary=summary,
    )


@app.get("/cheapest", response_model=Optional[ProductOut])
async def cheapest_only(
    q: str = Query(..., description="Product to search"),
    platforms: str = Query("blinkit,zepto,instamart"),
    headless: bool = Query(True),
):
    resp = await compare(q=q, platforms=platforms, headless=headless)
    return resp.cheapest


@app.get("/search/{platform}", response_model=PlatformResult)
async def search_platform(
    platform: str,
    q: str = Query(..., description="Product to search"),
    headless: bool = Query(True),
):
    if platform not in SCRAPER_MAP:
        raise HTTPException(404, f"Platform '{platform}' not found. Valid: {list(SCRAPER_MAP.keys())}")
    return await fetch_platform(q, platform, headless)


@app.get("/suggest")
async def suggest(q: str = Query(..., min_length=2)):
    names = await suggest_names(q)
    return {"suggestions": names}


@app.delete("/cache")
async def bust_cache():
    await clear_cache()
    return {"message": "Cache cleared"}


class AddToCartBody(BaseModel):
    platform: str
    query: str
    product_name: str
    price: Optional[float] = None


@app.post("/add-to-cart")
async def add_to_cart_endpoint(
    body: AddToCartBody,
    headless: bool = Query(True, description="Run browser headless"),
):
    """
    Add a single product to the real app cart (Playwright + session cookies).
    Call once per line item; then open the platform cart in the browser.
    """
    platform = body.platform.strip().lower()
    if platform not in SCRAPER_MAP:
        raise HTTPException(
            400,
            f"Unknown platform '{body.platform}'. Valid: {list(SCRAPER_MAP.keys())}",
        )

    scraper_cls = SCRAPER_MAP[platform]
    async with scraper_cls(headless=headless) as scraper:
        ok, err = await scraper.safe_add_to_cart(
            body.query.strip(),
            body.product_name.strip(),
            body.price,
        )

    if not ok:
        raise HTTPException(502, detail=err or "Add to cart failed")

    return {"ok": True, "platform": platform}


# ---------- serve frontend ----------

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    async def serve_frontend():
        return FileResponse(str(FRONTEND_DIR / "index.html"))
