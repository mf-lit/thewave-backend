"""Core scraper: bootstrap a session, then read prices from the B2C API."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse, parse_qs, urlencode

from playwright.sync_api import sync_playwright

FRONTEND = "https://ticketing.thewave.com"
API_HOST = "https://ticketing-api.thewave.com"
# The API key the front-end sends. It is a non-secret constant baked into the
# public JS bundle, not a credential.
API_KEY = "42"
DEFAULT_LOCALE = "en-GB"
# The front-end excludes the "COURSES" offer from the single-performance view.
DEFAULT_EXCLUDED_OFFERS = ("COURSES",)
# Event category codes the front-end uses to list all bookable performances.
# These come from /config/eventView and are overridable in case they change.
DEFAULT_EVENT_CATEGORIES = ("TWBB2C", "ALL2")
# The /events/calendar endpoint accepts at most this many days per request, so
# longer ranges are fetched as successive windows.
MAX_CALENDAR_DAYS = 7


@dataclass
class Product:
    """A purchasable product (a ticket type) for a performance."""

    product_ak: str
    title: str
    full_title: str
    price: float | None
    currency: str | None
    product_type: str | None
    sort_order: int
    capacity_code: str | None = None
    available: int | None = None  # remaining availability for this product, if known

    def __str__(self) -> str:
        price = f"{self.price:.2f} {self.currency}" if self.price is not None else "no price"
        avail = "" if self.available is None else f"  ({self.available} left)"
        return f"{self.title}: {price}{avail}"


@dataclass
class Performance:
    """A performance (a dated session) and its priced products."""

    performance_ak: str
    event_ak: str | None
    title: str | None
    date: str | None
    time: str | None
    available: int | None
    products: list[Product] = field(default_factory=list)

    @property
    def price_min(self) -> float | None:
        prices = [p.price for p in self.products if p.price is not None]
        return min(prices) if prices else None

    @property
    def price_max(self) -> float | None:
        prices = [p.price for p in self.products if p.price is not None]
        return max(prices) if prices else None


def performance_ak_from_url(url: str) -> str:
    """Extract a performanceAk from a ticketing URL, or return the input unchanged.

    Accepts either a bare AK (``TWB.EVN17.PRF1687``) or a full performance URL.
    """
    if "://" not in url:
        return url
    qs = parse_qs(urlparse(url).query)
    aks = qs.get("performanceAk") or qs.get("performanceAK")
    if aks:
        return aks[0]
    raise ValueError(f"No performanceAk found in URL: {url}")


class WaveScraper:
    """Scrapes prices from The Wave's ticketing site.

    Use as a context manager so the browser is cleaned up::

        with WaveScraper() as scraper:
            perf = scraper.fetch("TWB.EVN17.PRF1687")
            print(perf.price_min, perf.price_max)

    The first :meth:`fetch` launches a headless browser to bootstrap a session;
    subsequent fetches reuse it and hit the API directly.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        locale: str = DEFAULT_LOCALE,
        excluded_offer_codes: tuple[str, ...] = DEFAULT_EXCLUDED_OFFERS,
        proxy: str | None = None,
        timeout_ms: int = 60_000,
    ) -> None:
        self.headless = headless
        self.locale = locale
        self.excluded_offer_codes = list(excluded_offer_codes)
        self.proxy = proxy
        self.timeout_ms = timeout_ms

        self._pw = None
        self._browser = None
        self._page = None
        self._csrf_token: str | None = None
        self._api_base: str | None = None  # e.g. https://ticketing-api.thewave.com/api/twb-prod*base/b2c/v1

    # -- lifecycle -------------------------------------------------------

    def __enter__(self) -> "WaveScraper":
        self._pw = sync_playwright().start()
        launch_kwargs: dict = {"headless": self.headless}
        if self.proxy:
            # Applies to page navigation and page.request (the API replay) alike.
            launch_kwargs["proxy"] = {"server": self.proxy}
        self._browser = self._pw.chromium.launch(**launch_kwargs)
        self._page = self._browser.new_page()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

    # -- session bootstrap ----------------------------------------------

    def _bootstrap(self, url: str) -> None:
        """Load a front-end page so it establishes a session, then capture creds.

        Captures the CSRF token and the tenant-specific API base path from the
        observed network traffic, so neither is hard-coded. Any front-end page
        works — it issues the same bootstrap API calls (config/account) that
        carry the CSRF token.
        """
        captured: dict[str, str] = {}

        def on_request(req):
            if "x-csrf-token" in req.headers and "csrf" not in captured:
                captured["csrf"] = req.headers["x-csrf-token"]
            if "api_base" not in captured:
                m = re.match(rf"{re.escape(API_HOST)}/api/[^/]+/b2c/v1", req.url)
                if m:
                    captured["api_base"] = m.group(0)

        self._page.on("request", on_request)
        self._page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
        self._page.remove_listener("request", on_request)

        if "csrf" not in captured or "api_base" not in captured:
            raise RuntimeError(
                "Failed to bootstrap session (no CSRF token / API base observed). "
                "The site structure may have changed."
            )
        self._csrf_token = captured["csrf"]
        self._api_base = captured["api_base"]

    def _require_page(self) -> None:
        if self._page is None:
            raise RuntimeError("WaveScraper must be used as a context manager.")

    def _ensure_session(self, performance_ak: str) -> None:
        self._require_page()
        if self._csrf_token is None:
            self._bootstrap(
                f"{FRONTEND}/b2c/ticketSale/performance?performanceAk={performance_ak}"
            )

    def _ensure_session_anon(self) -> None:
        """Bootstrap a session without needing a specific performance AK."""
        self._require_page()
        if self._csrf_token is None:
            self._bootstrap(f"{FRONTEND}/")

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": API_KEY,
            "x-csrf-token": self._csrf_token,
            "content-type": "application/json",
            "accept": "application/json, text/plain, */*",
            "referer": f"{FRONTEND}/",
        }

    def _get_json(self, path: str) -> dict:
        resp = self._page.request.get(f"{self._api_base}{path}", headers=self._headers())
        if not resp.ok:
            raise RuntimeError(f"GET {path} failed: HTTP {resp.status}")
        return resp.json()

    def _post_json(self, path: str, data: dict) -> dict:
        resp = self._page.request.post(
            f"{self._api_base}{path}", headers=self._headers(), data=data
        )
        if not resp.ok:
            raise RuntimeError(f"POST {path} failed: HTTP {resp.status}")
        return resp.json()

    # -- public API ------------------------------------------------------

    def fetch(self, performance_or_url: str) -> Performance:
        """Return a :class:`Performance` with all priced products.

        Makes two API calls (performance info + products). For bulk price
        collection where the date/event are already known, prefer
        :meth:`price_range`, which makes a single call.
        """
        performance_ak = performance_ak_from_url(performance_or_url)
        self._ensure_session(performance_ak)

        info = self._get_json(f"/performances/{performance_ak}?locale={self.locale}")
        products_resp = self._products(performance_ak)
        return self._build_performance(performance_ak, info, products_resp)

    def price_range(self, performance_or_url: str) -> tuple[float | None, float | None]:
        """Return ``(price_min, price_max)`` across a performance's products.

        Single API call (``POST /performanceProducts``) — lighter than
        :meth:`fetch`. Returns ``(None, None)`` if no priced products are found.
        """
        performance_ak = performance_ak_from_url(performance_or_url)
        self._ensure_session(performance_ak)
        products_resp = self._products(performance_ak)
        prices = [
            (item.get("price") or {}).get("value")
            for item in products_resp.get("products") or []
        ]
        prices = [p for p in prices if p is not None]
        if not prices:
            return None, None
        return min(prices), max(prices)

    def list_calendar(
        self,
        start_date: dt.date,
        num_days: int,
        event_category_codes: tuple[str, ...] = DEFAULT_EVENT_CATEGORIES,
    ) -> list[dict]:
        """Enumerate non-past performances for ``num_days`` from ``start_date``.

        Returns a list of ``{"performance_ak", "event_ak", "date", "time"}``.
        Each underlying ``/events/calendar`` request covers at most
        :data:`MAX_CALENDAR_DAYS` days, so longer ranges are fetched as
        successive windows (e.g. 19 days -> 7 + 7 + 5) and concatenated.
        """
        self._ensure_session_anon()

        seen: set[str] = set()
        out: list[dict] = []
        offset = 0
        while offset < num_days:
            window = min(MAX_CALENDAR_DAYS, num_days - offset)
            window_start = start_date + dt.timedelta(days=offset)
            data = self._fetch_calendar_window(window_start, window, event_category_codes)
            for day in data.get("days") or []:
                for perf in day.get("performances") or []:
                    if perf.get("isPast"):
                        continue
                    ak = perf.get("performanceAK")
                    if not ak or ak in seen:
                        continue
                    seen.add(ak)
                    out.append(
                        {
                            "performance_ak": ak,
                            "event_ak": perf.get("eventAk"),
                            "date": perf.get("date"),
                            "time": perf.get("time"),
                        }
                    )
            offset += window
        return out

    def _fetch_calendar_window(
        self, start_date: dt.date, num_days: int, event_category_codes: tuple[str, ...]
    ) -> dict:
        params = [("locale", self.locale)]
        params += [("eventCategoryCode[]", code) for code in event_category_codes]
        params += [("dateFrom", start_date.isoformat()), ("numberOfDays", str(num_days))]
        query = urlencode(params)
        return self._get_json(f"/events/calendar?{query}")

    def _products(self, performance_ak: str) -> dict:
        return self._post_json(
            "/performanceProducts",
            {
                "locale": self.locale,
                "performanceAks": [performance_ak],
                "components": None,
                "excludedOfferCodes": self.excluded_offer_codes,
                "extraRedeem": True,
            },
        )

    # -- parsing ---------------------------------------------------------

    @staticmethod
    def _build_performance(performance_ak: str, info: dict, products_resp: dict) -> Performance:
        perf = (info or {}).get("performance") or {}
        fields = perf.get("fields") or {}
        availability = perf.get("availability") or {}

        # remaining availability per product, keyed by capacity code
        avail_by_code: dict[str, int] = {}
        for entry in perf.get("availabilityPerProduct") or []:
            code = entry.get("code")
            a = (entry.get("availability") or {}).get("available")
            if code is not None:
                avail_by_code[code] = a

        products: list[Product] = []
        for item in products_resp.get("products") or []:
            price = item.get("price") or {}
            f = item.get("fields") or {}
            cap = item.get("capacityCode")
            products.append(
                Product(
                    product_ak=item.get("productAk"),
                    title=f.get("title") or f.get("fullTitle"),
                    full_title=f.get("fullTitle"),
                    price=price.get("value"),
                    currency=price.get("currency"),
                    product_type=item.get("productType"),
                    sort_order=item.get("sortOrder", 0),
                    capacity_code=cap,
                    available=avail_by_code.get(cap),
                )
            )
        products.sort(key=lambda p: p.sort_order)

        return Performance(
            performance_ak=performance_ak,
            event_ak=perf.get("eventAk"),
            title=fields.get("title"),
            date=perf.get("date"),
            time=perf.get("time"),
            available=availability.get("available"),
            products=products,
        )


def scrape_performance(performance_or_url: str, **kwargs) -> Performance:
    """Convenience one-shot: launch a browser, fetch one performance, clean up.

    For multiple performances, prefer :class:`WaveScraper` as a context manager
    to reuse a single session.
    """
    with WaveScraper(**kwargs) as scraper:
        return scraper.fetch(performance_or_url)
