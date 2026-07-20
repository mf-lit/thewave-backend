"""Scrape ticket/price information from The Wave's ticketing site.

The public ticketing site (https://ticketing.thewave.com) is a client-rendered
Next.js app backed by a SecuTix/Vivaticket B2C API at
https://ticketing-api.thewave.com. Prices are not in the HTML; they are loaded
by an authenticated XHR (``performanceProducts``) that requires a session
bootstrapped by the front-end (CSRF token + session cookies).

This module bootstraps that session once with a headless browser, then replays
the price API directly for any performance, so a single browser launch can
price many performances.
"""

from .collector import CollectSummary, collect
from .scraper import Performance, Product, WaveScraper, scrape_performance
from .storage import PriceStore, RecordResult

__all__ = [
    "WaveScraper",
    "Performance",
    "Product",
    "scrape_performance",
    "PriceStore",
    "RecordResult",
    "collect",
    "CollectSummary",
]
