from __future__ import annotations
import json
import os
import re
from pathlib import Path
from typing import List, Dict

from dotenv import load_dotenv
from groq import AsyncGroq

load_dotenv(Path(__file__).parent.parent / ".env")

_client: AsyncGroq | None = None


def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        key = os.environ.get("GROQ_API_KEY")
        if not key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Add it to quick-compare/.env or the environment."
            )
        _client = AsyncGroq(api_key=key)
    return _client

_SYSTEM_PROMPT = """You are a grocery shopping assistant. Your job is to help users find and compare grocery prices.

When a user wants to buy something, extract the product and quantity and respond with ONLY a JSON object:
{"intent": "search", "product": "<product name>", "quantity": "<quantity or null>"}

When the user is just chatting, asking for help, or the message is not a purchase request, respond with:
{"intent": "chat", "reply": "<your friendly response>"}

Keep context across the conversation — if the user says "what about zepto?" after a search, they mean the same product.
Never include anything outside the JSON in your response."""


async def chat(
    user_message: str,
    history: List[Dict[str, str]] | None = None,
) -> Dict:
    """
    Send a message with optional chat history.
    Returns a dict with:
      - intent="search" → keys: product, quantity, search_query
      - intent="chat"   → key: reply
    history format: [{"role": "user"|"assistant", "content": "..."}]
    """
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    response = await _get_client().chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=messages,
        temperature=0.1,
        max_tokens=200,
    )
    raw = response.choices[0].message.content.strip()

    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    # Try to extract JSON even if LLM added extra text around it
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not json_match:
        # LLM returned plain text — treat as a chat reply
        return {"intent": "chat", "reply": raw}

    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError:
        return {"intent": "chat", "reply": raw}

    if data.get("intent") == "search":
        product = (data.get("product") or "").strip()
        quantity = (data.get("quantity") or "").strip()
        data["search_query"] = f"{product} {quantity}".strip() if quantity else product

    return data


async def parse_query(natural_language: str, history: List[Dict[str, str]] | None = None) -> str:
    """Convenience wrapper used by /smart-search — returns just the search string.
    Falls back to the raw query if Groq is unavailable, so browser search always works."""
    if not os.environ.get("GROQ_API_KEY"):
        return natural_language.strip()
    try:
        result = await chat(natural_language, history)
        if result.get("intent") == "search":
            return result["search_query"]
    except Exception:
        pass  # Groq down or rate-limited — fall through to raw query
    return natural_language.strip()
