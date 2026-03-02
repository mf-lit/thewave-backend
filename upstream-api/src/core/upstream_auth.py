"""
Upstream API authentication using WASM-based CSRF token generation.

Auth flow:
1. GET /auth/token to get a raw token + session cookie (JSESSIONID)
2. Process the token through a WASM module (gen function) to produce the CSRF token
3. Call the API with the session cookie, X-CSRF-Token header, and X-API-KEY header
"""

import logging
from pathlib import Path

import requests
import wasmtime

logger = logging.getLogger(__name__)

BE_BASE_URL = "https://ticketing-api.thewave.com"
API_KEY = "42"
TENANT = "twb-prod*base"
SITE = "b2c"

WASM_URL = "https://ticketing.thewave.com/static/wasm/a.wasm"
WASM_PATH = Path(__file__).parent.parent.parent / "data" / "a.wasm"

# Module-level cached session
_session: requests.Session | None = None


def _download_wasm() -> None:
    """Download the WASM module if not already cached locally."""
    if WASM_PATH.exists():
        return
    logger.info(f"Downloading WASM module from {WASM_URL}")
    WASM_PATH.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(WASM_URL, timeout=10)
    resp.raise_for_status()
    WASM_PATH.write_bytes(resp.content)
    logger.info(f"WASM module saved to {WASM_PATH}")


def _make_csrf_processor():
    """Load the WASM module and return a function that processes tokens."""
    _download_wasm()
    engine = wasmtime.Engine()
    store = wasmtime.Store(engine)
    module = wasmtime.Module.from_file(engine, str(WASM_PATH))
    instance = wasmtime.Instance(store, module, [])

    exports = instance.exports(store)
    memory = exports["memory"]
    malloc_fn = exports["malloc"]
    free_fn = exports["free"]
    gen_fn = exports["gen"]

    def process(token_str: str) -> str:
        token_bytes = token_str.encode("utf-8")
        input_ptr = malloc_fn(store, len(token_bytes))
        output_ptr = malloc_fn(store, 32)
        buf = memory.data_ptr(store)
        for i, b in enumerate(token_bytes):
            buf[input_ptr + i] = b
        gen_fn(store, input_ptr, len(token_bytes), output_ptr)
        result = bytes(buf[output_ptr : output_ptr + 32])
        free_fn(store, input_ptr)
        free_fn(store, output_ptr)
        return "".join(f"{b:02x}" for b in result)

    return process


def _create_session() -> requests.Session:
    """Create a requests session with valid CSRF token and cookies."""
    process_token = _make_csrf_processor()

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Origin": "https://ticketing.thewave.com",
        "Referer": "https://ticketing.thewave.com/b2c/ticketSale/eventsCalendar",
    })

    auth_token_url = f"{BE_BASE_URL}/auth/token?tenant={TENANT}&site={SITE}"
    logger.info(f"Fetching auth token from {auth_token_url}")
    resp = session.get(auth_token_url)
    resp.raise_for_status()
    raw_token = resp.json()["token"]

    csrf_token = process_token(raw_token)
    logger.info("CSRF token generated successfully")

    session.headers.update({
        "X-CSRF-Token": csrf_token,
        "X-API-KEY": API_KEY,
    })

    return session


def get_authenticated_session(force_refresh: bool = False) -> requests.Session:
    """Return a cached authenticated session, creating one if needed."""
    global _session
    if _session is None or force_refresh:
        logger.info("Creating new authenticated upstream session")
        _session = _create_session()
    return _session


def reset_session() -> None:
    """Clear the cached session, forcing re-authentication on next use."""
    global _session
    _session = None
