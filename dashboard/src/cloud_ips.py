"""Classify an IP address as belonging to Google (or Apple) infrastructure.

Many "clients" are actually Google systems (Global Cache nodes, proxies,
crawlers) rather than real app users. ``is_cloud_ip`` returns 1 for such
addresses so callers can filter them out.

Detection is primarily by **reverse DNS**: Google Global Cache nodes are
embedded in ISP IP space worldwide (no published CIDR covers them), but they
all resolve to a Google domain (``*.google.com`` / ``*.1e100.net``). A small
CIDR list is kept as a fast-path (avoids a DNS lookup for known ranges) and as
a fallback when DNS is unavailable. Apple is matched by CIDR only (17.0.0.0/8).
"""

import concurrent.futures
import ipaddress
import socket

# Apple owns the whole 17.0.0.0/8.
APPLE_RANGES = ["17.0.0.0/8"]

# Known Google ranges — fast-path so these skip the DNS lookup (and a fallback
# if reverse DNS is down). Reverse DNS catches everything else, incl. GGC.
GOOGLE_RANGES = [
    "8.8.4.0/24", "8.8.8.0/24", "8.34.208.0/20", "8.35.192.0/20",
    "23.236.48.0/20", "23.251.128.0/19",
    "34.64.0.0/10",
    "35.184.0.0/13", "35.192.0.0/14", "35.196.0.0/15", "35.198.0.0/16",
    "35.199.0.0/17", "35.200.0.0/13", "35.208.0.0/12", "35.224.0.0/12",
    "35.240.0.0/13",
    "64.233.160.0/19", "66.102.0.0/20", "66.249.64.0/19",
    "70.32.128.0/19", "72.14.192.0/18", "74.125.0.0/16",
    "104.154.0.0/15", "104.196.0.0/14", "104.237.160.0/19",
    "107.167.160.0/19", "107.178.192.0/18",
    "108.59.80.0/20", "108.170.192.0/18", "108.177.0.0/17",
    "130.211.0.0/16", "136.112.0.0/12",
    "142.250.0.0/15", "142.251.0.0/16", "146.148.0.0/17",
    "162.216.148.0/22", "162.222.176.0/21",
    "172.110.32.0/21", "172.217.0.0/16", "172.253.0.0/16",
    "173.194.0.0/16", "173.255.112.0/20", "192.158.28.0/22",
    "199.36.154.0/23", "199.36.156.0/24", "199.192.112.0/22", "199.223.232.0/21",
    "207.223.160.0/20",
    "208.65.152.0/22", "208.68.108.0/22", "208.81.188.0/22", "208.117.224.0/19",
    "209.85.128.0/17",
    "216.58.192.0/19", "216.73.80.0/20", "216.239.32.0/19",
    # Google Global Cache nodes seen embedded in ISP space.
    "62.122.200.0/24", "93.191.8.0/22", "190.208.14.0/24",
]

_NETWORKS = [ipaddress.ip_network(c) for c in (APPLE_RANGES + GOOGLE_RANGES)]

# Reverse-DNS suffixes that indicate Google infrastructure (dotted-boundary
# match, so "evilgoogle.com" does NOT match ".google.com").
_GOOGLE_PTR_SUFFIXES = ("google.com", "1e100.net", "googleusercontent.com")

# Reverse lookups run on a thread pool with a per-batch deadline; results cached.
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=32, thread_name_prefix="ptr")
_PTR_DEADLINE = 4.0  # seconds, total, for a prewarm batch
_ptr_cache: dict[str, str] = {}


def _lookup(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0].lower()
    except Exception:
        return ""


def _reverse_dns(ip: str) -> str:
    """Cached reverse-DNS hostname for ``ip`` (lowercased), or '' on failure."""
    if ip not in _ptr_cache:
        _ptr_cache[ip] = _lookup(ip)
    return _ptr_cache[ip]


def prewarm(ips) -> None:
    """Resolve a batch of IPs concurrently into the cache, bounded by a deadline.

    Call this once before classifying many rows so per-row ``is_cloud_ip`` hits
    the cache instead of doing serial DNS lookups (which would be slow for IPs
    with no PTR record). IPs that don't resolve within the deadline are cached
    as '' (treated as not-cloud).
    """
    pending = {ip for ip in ips if ip and ip not in _ptr_cache}
    if not pending:
        return
    futures = {_pool.submit(_lookup, ip): ip for ip in pending}
    done, not_done = concurrent.futures.wait(futures, timeout=_PTR_DEADLINE)
    for fut in done:
        _ptr_cache[futures[fut]] = fut.result()
    for fut in not_done:
        _ptr_cache.setdefault(futures[fut], "")


def _ptr_is_google(host: str) -> bool:
    return any(host == s or host.endswith("." + s) for s in _GOOGLE_PTR_SUFFIXES)


def is_cloud_ip(ip):
    """Return 1 if ``ip`` is Google/Apple infrastructure, else 0.

    Fast-path on the CIDR list, then fall back to reverse DNS (Google domains).
    Returns 0 for NULL/empty/unparseable values. Int return so it works as a
    SQLite scalar function.
    """
    if not ip:
        return 0
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return 0
    if any(addr in net for net in _NETWORKS):
        return 1
    return 1 if _ptr_is_google(_reverse_dns(ip)) else 0
