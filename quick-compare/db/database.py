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
        await db.commit()


async def clear_cache():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM price_cache")
        await db.commit()
