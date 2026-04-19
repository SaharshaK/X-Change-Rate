from __future__ import annotations
import aiosqlite
import json
import time
from pathlib import Path
from typing import List, Optional

DB_PATH = Path(__file__).parent / "cache.db"
CACHE_TTL = 1800  # 30 minutes


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS price_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                platform TEXT NOT NULL,
                results TEXT NOT NULL,
                fetched_at INTEGER NOT NULL,
                UNIQUE(query, platform)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS product_names (
                name TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        await db.commit()


async def get_cached(query: str, platform: str) -> Optional[List[dict]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT results, fetched_at FROM price_cache WHERE query = ? AND platform = ?",
            (query.lower().strip(), platform)
        ) as cursor:
            row = await cursor.fetchone()
            if row and (time.time() - row[1]) < CACHE_TTL:
                return json.loads(row[0])
    return None


async def set_cached(query: str, platform: str, results: List[dict]):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO price_cache (query, platform, results, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(query, platform) DO UPDATE SET
                results = excluded.results,
                fetched_at = excluded.fetched_at
        """, (query.lower().strip(), platform, json.dumps(results), int(time.time())))

        # Index product names for autocomplete
        now = int(time.time())
        for r in results:
            if r.get("name"):
                await db.execute("""
                    INSERT INTO product_names (name, platform, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET updated_at = excluded.updated_at
                """, (r["name"], platform, now))

        await db.commit()


async def suggest_names(q: str, limit: int = 8) -> List[str]:
    """Substring match against previously seen product names — instant, no scraping."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT name FROM product_names WHERE name LIKE ? ORDER BY updated_at DESC LIMIT ?",
            (f"%{q}%", limit)
        ) as cursor:
            rows = await cursor.fetchall()
    return [r[0] for r in rows]


async def clear_cache():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM price_cache")
        await db.execute("DELETE FROM product_names")
        await db.commit()
