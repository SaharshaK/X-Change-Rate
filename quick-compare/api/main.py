from __future__ import annotations
import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

from scrapers import BlinkitScraper, ZeptoScraper, InstamartScraper
from scrapers.base import Product
from db import init_db, get_cached, set_cached, clear_cache


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


async def fetch_platform(query: str, platform: str, headless: bool) -> PlatformResult:
    cached = await get_cached(query, platform)
    if cached is not None:
        return PlatformResult(
            platform=platform,
            products=[ProductOut(**p) for p in cached],
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

    return PlatformResult(
        platform=platform,
        products=[product_to_out(p) for p in products],
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


@app.delete("/cache")
async def bust_cache():
    await clear_cache()
    return {"message": "Cache cleared"}
