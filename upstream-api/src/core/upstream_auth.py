"""
Upstream API authentication using WASM-based CSRF token generation.

Auth flow:
1. GET /auth/token to get a raw token + session cookie (JSESSIONID)
2. Process the token through a WASM module (gen function) to produce the CSRF token
3. Call the API with the session cookie, X-CSRF-Token header, and X-API-KEY header
"""

import logging
import time
from pathlib import Path

import requests
import wasmtime
import yaml

logger = logging.getLogger(__name__)

BE_BASE_URL = "https://ticketing-api.thewave.com"
API_KEY = "42"
TENANT = "twb-prod*base"
SITE = "b2c"

WASM_URL = "https://ticketing.thewave.com/static/wasm/a.wasm"
WASM_PATH = Path(__file__).parent.parent.parent / "data" / "a.wasm"

# Module-level cached session
_session: requests.Session | None = None
_session_ip: str | None = None

# Proxy config loaded from config.yaml
_proxies: dict | None = None

# Cached external IP
_cached_ip: str | None = None
_cached_ip_time: float = 0
_IP_CACHE_TTL = 600  # 10 minutes


def _load_proxy_config() -> dict | None:
    """Load proxy URL from config.yaml and return a requests-compatible proxies dict."""
    global _proxies
    if _proxies is not None:
        return _proxies if _proxies else None

    config_file = Path(__file__).parent.parent.parent / "config" / "config.yaml"
    if not config_file.exists():
        _proxies = {}
        return None

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        proxy_url = config.get("proxy", "").strip() if isinstance(config, dict) else ""
    except Exception as e:
        logger.warning(f"Failed to load proxy config: {e}")
        _proxies = {}
        return None

    if proxy_url:
        _proxies = {"http": proxy_url, "https": proxy_url}
        logger.info(f"Using HTTP proxy: {proxy_url}")
        return _proxies

    _proxies = {}
    return None


def _download_wasm() -> None:
    """Download the WASM module if not already cached locally."""
    if WASM_PATH.exists():
        return
    logger.info(f"Downloading WASM module from {WASM_URL}")
    WASM_PATH.parent.mkdir(parents=True, exist_ok=True)
    proxies = _load_proxy_config()
    resp = requests.get(WASM_URL, timeout=10, proxies=proxies)
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
    proxies = _load_proxy_config()
    if proxies:
        session.proxies.update(proxies)
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


def get_external_ip() -> str | None:
    """Fetch external IP from canhazip.com, cached for 10 minutes."""
    global _cached_ip, _cached_ip_time
    now = time.monotonic()
    if _cached_ip is not None and (now - _cached_ip_time) < _IP_CACHE_TTL:
        return _cached_ip
    try:
        proxies = _load_proxy_config()
        resp = requests.get("https://canhazip.com", timeout=5, proxies=proxies)
        resp.raise_for_status()
        _cached_ip = resp.text.strip()
        _cached_ip_time = now
        logger.info(f"External IP: {_cached_ip}")
        return _cached_ip
    except Exception as e:
        logger.warning(f"Failed to fetch external IP: {e}")
        return _cached_ip


def get_authenticated_session(force_refresh: bool = False) -> requests.Session:
    """Return a cached authenticated session, creating one if needed.

    Re-authenticates automatically if the external IP has changed.
    """
    global _session, _session_ip
    current_ip = get_external_ip()
    if _session is not None and not force_refresh:
        if current_ip is not None and current_ip != _session_ip:
            logger.info(f"External IP changed from {_session_ip} to {current_ip}, re-authenticating")
            force_refresh = True
    if _session is None or force_refresh:
        logger.info("Creating new authenticated upstream session")
        _session = _create_session()
        _session_ip = current_ip
    return _session


def reset_session() -> None:
    """Clear the cached session, forcing re-authentication on next use."""
    global _session, _session_ip
    _session = None
    _session_ip = None
