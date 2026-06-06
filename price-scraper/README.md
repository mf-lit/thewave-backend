# price-scraper

Scrapes ticket/price information from The Wave's ticketing site
(`https://ticketing.thewave.com`).

## How it works

The ticketing site is a client-rendered Next.js app — prices are **not** in the
page HTML. They are loaded by an authenticated XHR to a SecuTix/Vivaticket B2C
API at `https://ticketing-api.thewave.com`, which requires a session
(CSRF token + cookies) that the front-end establishes on load.

So this scraper:

1. Launches a headless browser (Playwright/Chromium) and loads a performance
   page once, capturing the CSRF token and tenant-specific API base path.
2. Replays the price API (`POST /performanceProducts` and
   `GET /performances/{ak}`) directly for any performance, reusing that session.

A single browser launch can therefore price many performances.

## Setup

```bash
uv sync
uv run playwright install chromium   # one-time: download the browser
```

## Usage

### `show` — prices for specific performances

Accepts a `performanceAk` or a full performance URL:

```bash
uv run python -m price_scraper show TWB.EVN17.PRF1687
uv run python -m price_scraper show --json <url> <url> ...
```

### `collect` — N days of prices into SQLite (throttled)

Enumerates every performance for the next N days and records each one's min/max
price in a SQLite DB, throttling API calls so the whole pass takes about
`--target-time`:

```bash
uv run python -m price_scraper collect --days 7 --target-time 1h --db prices.db
uv run python -m price_scraper collect --days 19 --target-time 30m --proxy http://127.0.0.1:8119
```

- `--days N` — days from today (default 7). Enumeration is chunked into ≤7-day
  windows internally (e.g. 19 → 7 + 7 + 5).
- `--target-time` — `1h`, `30m`, `90s`, `1h30m`, or bare seconds (default `1h`).
  The inter-performance delay is `target-time / number-of-performances`.
- `--db PATH` (default `prices.db`), `--proxy URL` (default direct),
  `--categories` (default `TWBB2C,ALL2`).

Run it repeatedly (e.g. a daily cron) against the same DB — it updates
`date_last_scraped` every run and appends to a per-performance price `history`
only when the min/max price changes.

#### Database schema

Table `performances`:

| column               | meaning                                                       |
|----------------------|---------------------------------------------------------------|
| `performanceAK`      | performance key (primary key)                                 |
| `price_min`          | current minimum product price                                 |
| `price_max`          | current maximum product price                                 |
| `date_first_scraped` | when the performance was first recorded                       |
| `date_last_scraped`  | when it was last seen (updated every run)                     |
| `history`            | JSON list of `{price_min, price_max, date}`, appended on change |

### As a library

```python
from price_scraper import WaveScraper, collect

with WaveScraper() as scraper:
    perf = scraper.fetch("TWB.EVN17.PRF1687")
    print(perf.title, perf.price_min, perf.price_max)

# or a full collection pass:
summary = collect(days=7, target_seconds=3600, db_path="prices.db")
print(summary)
```

For a single lookup, `scrape_performance("TWB.EVN17.PRF1687")` launches and tears
down a browser in one call.

## Notes

- The `x-api-key: 42` header is a non-secret constant baked into the public JS
  bundle, not a credential.
- The API base path (`/api/twb-prod*base/b2c/v1`) is discovered at runtime from
  observed traffic, so a tenant/environment rename won't silently break it.
- The calendar endpoint's own `priceMin`/`priceMax` are unreliable (often `0`),
  so `collect` prices each performance via `POST /performanceProducts`.
