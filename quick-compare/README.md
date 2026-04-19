# Quick Compare — Price Comparison API

Compares grocery prices across **Blinkit**, **Zepto**, and **Instamart** in real time.  
Built for hackathon use — exposes a clean REST API your dashboard or Telegram bot can call directly.

---

## Platform Status

| Platform | Status | Notes |
|---|---|---|
| Blinkit | ✅ Working | Requires Chrome cookies (see setup) |
| Zepto | ✅ Working | No login needed |
| Instamart | ⚠️ Limited | AWS WAF blocks headless — works only when Chrome is closed |

---

## Quick Start

**Prerequisites:** Python 3.9+, Google Chrome installed

```bash
cd quick-compare

# 1. Create virtualenv and install deps
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Install Playwright's Chromium
playwright install chromium

# 3. Start the API server
python run.py
# → running at http://0.0.0.0:8000
```

> **Important:** Chrome must be closed when you start the server.  
> The scraper reuses your existing Chrome login sessions (Blinkit, Zepto, Swiggy).  
> Once the server is running, you can reopen Chrome.

### Run the Telegram bot

The repo now includes a polling-based Telegram bot that talks to this API and reuses the NLP parsing already wired into the backend branch.

```bash
# 1. Copy env vars
cp .env.example .env

# 2. Fill these in
#    GROQ_API_KEY=...
#    TELEGRAM_BOT_TOKEN=...

# 3. Start the API
python run.py

# 4. In another terminal, start the bot
python run_telegram_bot.py
```

Suggested local setup:
- Keep `QUICK_COMPARE_API_BASE_URL=http://127.0.0.1:8000`
- Talk to the bot in Telegram with messages like `1 dozen bananas` or `what about zepto?`
- Use `ngrok http 8000` only if another service needs to reach your local API

---

## API Reference

Base URL: `http://localhost:8000`  
Interactive docs: `http://localhost:8000/docs`

---

### `GET /compare`

Search all platforms and get a side-by-side comparison.

**Query params**

| Param | Required | Default | Description |
|---|---|---|---|
| `q` | ✅ | — | Product to search, e.g. `amul butter 500g` |
| `platforms` | ❌ | `blinkit,zepto,instamart` | Comma-separated list |
| `headless` | ❌ | `true` | Set `false` to watch the browser |

**Example**
```
GET /compare?q=amul butter
```

**Response**
```json
{
  "query": "amul butter",
  "timestamp": "2026-04-19T05:23:51Z",
  "results": {
    "blinkit": {
      "platform": "blinkit",
      "products": [
        {
          "name": "Amul Salted Butter",
          "price": 60.0,
          "mrp": null,
          "quantity": "100 g",
          "image_url": "https://...",
          "platform": "blinkit",
          "in_stock": true,
          "url": "https://blinkit.com/s/?q=amul+butter",
          "discount_percent": null
        }
      ],
      "status": "success",
      "error": null,
      "search_time_ms": 3200
    },
    "zepto": { "..." },
    "instamart": { "..." }
  },
  "cheapest": {
    "name": "Amul Salted Butter",
    "price": 55.0,
    "platform": "zepto",
    "quantity": "100 g",
    "..."
  },
  "summary": {
    "blinkit": 60.0,
    "zepto": 55.0,
    "instamart": null
  }
}
```

---

### `GET /cheapest`

Returns only the single cheapest in-stock product across all platforms.

```
GET /cheapest?q=amul butter
```

**Response** — a single product object (same shape as items in `products[]` above), or `null` if nothing found.

---

### `GET /search/{platform}`

Search a single platform.

```
GET /search/blinkit?q=amul butter
GET /search/zepto?q=tata salt
GET /search/instamart?q=milk
```

**Response** — same shape as one entry in `results` from `/compare`.

---

### `GET /smart-search`

Natural-language search powered by the NLP layer.

```
GET /smart-search?q=I want 1 dozen bananas
```

**Response** — same shape as `/compare`, after the natural-language query is converted into a search term.

---

### `GET /platforms`

List all supported platforms.

```json
{ "platforms": ["blinkit", "zepto", "instamart"] }
```

---

### `GET /health`

```json
{ "status": "ok", "timestamp": "2026-04-19T05:00:00Z" }
```

---

### `DELETE /cache`

Clears the 30-minute price cache. Call this if you're seeing stale data.

```
DELETE /cache
→ { "message": "Cache cleared" }
```

---

## Product Object

Every product in the API has this shape:

```json
{
  "name": "Amul Salted Butter",
  "price": 60.0,
  "mrp": 65.0,
  "quantity": "100 g",
  "image_url": "https://...",
  "platform": "blinkit",
  "in_stock": true,
  "url": "https://blinkit.com/s/?q=amul+butter",
  "discount_percent": 7.7
}
```

| Field | Type | Description |
|---|---|---|
| `name` | string | Product name |
| `price` | float | Selling price (₹) |
| `mrp` | float \| null | MRP if shown |
| `quantity` | string | Pack size, e.g. `500 g` |
| `image_url` | string \| null | Product image |
| `platform` | string | Source platform |
| `in_stock` | bool | Whether it can be added to cart |
| `url` | string | Search URL used |
| `discount_percent` | float \| null | `(mrp - price) / mrp * 100` |

---

## Caching

Prices are cached in a local SQLite DB (`db/cache.db`) for **30 minutes**.  
Cached responses return instantly with `"status": "cached"` and `"search_time_ms": 0`.

---

## Expose to teammates via ngrok

```bash
# Install ngrok, add your auth token from ngrok.com, then:
ngrok http 8000
# → https://abc123.ngrok.io — share this URL with your team
```

---

## Project Structure

```
quick-compare/
├── api/
│   ├── main.py          # FastAPI app — all endpoints live here
│   └── nlp.py           # NLP parsing for natural-language shopping queries
├── scrapers/
│   ├── base.py          # Base scraper + Chrome cookie reuse logic
│   ├── cookie_extractor.py  # Decrypts Chrome's AES-128-CBC cookies on macOS
│   ├── blinkit.py
│   ├── zepto.py
│   └── instamart.py
├── db/
│   └── database.py      # SQLite cache (aiosqlite)
├── telegram_bot/
│   ├── main.py          # Telegram handlers and conversation memory
│   ├── client.py        # HTTP client for calling the FastAPI API
│   ├── formatting.py    # Telegram-friendly result formatting
│   └── config.py        # Env-based bot configuration
├── run.py               # Entry point
├── run_telegram_bot.py  # Telegram polling entry point
└── requirements.txt
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `CHROME_USER_DATA` | `~/Library/Application Support/Google/Chrome` | Chrome profile dir |
| `CHROME_PROFILE` | `Default` | Chrome profile name |
| `GROQ_API_KEY` | — | Required for natural-language parsing |
| `TELEGRAM_BOT_TOKEN` | — | Required to run the Telegram bot |
| `QUICK_COMPARE_API_BASE_URL` | `http://127.0.0.1:8000` | Backend API URL used by the bot |
| `TELEGRAM_DEFAULT_PLATFORMS` | `blinkit,zepto,instamart` | Platforms queried by default |
| `TELEGRAM_MAX_PRODUCTS_PER_PLATFORM` | `1` | Number of products shown per platform in replies |

Set via a `.env` file or export before running.

---

## Telegram Bot Commands

The bot supports both free-text messages and explicit commands:

- Send plain English like `I want amul butter 500g`
- Follow up with `what about zepto?` after a previous search
- `/compare amul butter 500g`
- `/cheapest tata salt`
- `/platform blinkit milk`
- `/suggest amu`
