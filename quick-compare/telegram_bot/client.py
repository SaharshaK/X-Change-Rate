from __future__ import annotations

from typing import Any, Dict

import httpx


class QuickCompareApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=90.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def compare(
        self,
        query: str,
        *,
        platforms: str,
        headless: bool = True,
        smart: bool = False,
    ) -> Dict[str, Any]:
        endpoint = "/smart-search" if smart else "/compare"
        response = await self._client.get(
            endpoint,
            params={
                "q": query,
                "platforms": platforms,
                "headless": str(headless).lower(),
            },
        )
        response.raise_for_status()
        return response.json()

    async def cheapest(
        self,
        query: str,
        *,
        platforms: str,
        headless: bool = True,
    ) -> Dict[str, Any] | None:
        response = await self._client.get(
            "/cheapest",
            params={
                "q": query,
                "platforms": platforms,
                "headless": str(headless).lower(),
            },
        )
        response.raise_for_status()
        return response.json()

    async def suggest(self, query: str) -> Dict[str, Any]:
        response = await self._client.get("/suggest", params={"q": query})
        response.raise_for_status()
        return response.json()
