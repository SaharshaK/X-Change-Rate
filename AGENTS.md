# AGENTS.md — Quick Compare

## What this project is

A hackathon price-comparison tool that scrapes **Blinkit**, **Zepto**, and **Instamart** and exposes a REST API. Teammates are building a dashboard and a Telegram bot on top of the API.

## Repo layout

```
quick-compare/       ← the entire backend lives here
├── api/main.py      ← FastAPI app, all routes
├── scrapers/        ← one file per platform
├── db/database.py   ← SQLite cache (30-min TTL)
├── run.py           ← start the server
└── requirements.txt
```

## How to run

```bash
cd quick-compare
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python run.py          # → http://localhost:8000
```

Chrome must be **closed** before starting — the scraper reads your Chrome cookies to reuse login sessions for Blinkit and Zepto.

## Key API endpoints

| Endpoint | What it does |
|---|---|
| `GET /compare?q=<query>` | Compare all platforms, returns cheapest too |
| `GET /cheapest?q=<query>` | Just the cheapest product |
| `GET /search/{platform}?q=<query>` | Single platform search |
| `DELETE /cache` | Bust the 30-min price cache |
| `GET /health` | Health check |
| `GET /docs` | Auto-generated Swagger UI |

Full response schema is in `quick-compare/README.md`.

## Platform status

- **Blinkit** — working, uses Chrome cookie injection
- **Zepto** — working, no login required
- **Instamart** — blocked by Swiggy's AWS WAF; returns `status: "error"` with a clear message. Will work if Chrome is closed (persistent context mode).

## How the Chrome cookie trick works

`scrapers/base.py` tries two modes on startup:
1. **Persistent context** (Chrome closed): Playwright launches Chrome directly with your profile — full session reuse.
2. **Cookie injection** (Chrome open): `scrapers/cookie_extractor.py` reads Chrome's SQLite cookie DB, decrypts the AES-128-CBC values using the macOS Keychain key, and injects them into a fresh Playwright browser.

## Caching

SQLite at `db/cache.db`. Results are cached per `(query, platform)` for 30 minutes. Hit `DELETE /cache` to clear manually.

## Adding a new platform

1. Create `scrapers/<platform>.py`, subclass `BaseScraper`
2. Set `platform = "name"` and `cookie_domains = ["domain.com"]`
3. Implement `async def search(self, query: str) -> List[Product]`
4. Register in `scrapers/__init__.py` and `SCRAPER_MAP` in `api/main.py`

## Common tasks

**Clear cache and re-scrape:**
```bash
curl -X DELETE http://localhost:8000/cache
```

**Test a single platform:**
```bash
curl "http://localhost:8000/search/blinkit?q=tata+salt"
```

**Expose to teammates via ngrok:**
```bash
ngrok http 8000
```

## Dependencies

- `fastapi` + `uvicorn` — API server
- `playwright` — browser automation
- `pycryptodome` — Chrome cookie decryption
- `aiosqlite` — async SQLite cache
- Python 3.9+, Google Chrome installed on the machine
