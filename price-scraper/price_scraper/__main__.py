"""CLI for the Wave price scraper.

Two subcommands:

    # Show prices for one or more performances (text or JSON)
    uv run python -m price_scraper show TWB.EVN17.PRF1687
    uv run python -m price_scraper show --json <url> <url> ...

    # Collect N days of prices into a SQLite DB, throttled to a target duration
    uv run python -m price_scraper collect --days 7 --target-time 1h --db prices.db
"""

import argparse
import json
import re
import sys
from dataclasses import asdict

from .collector import collect
from .scraper import DEFAULT_EVENT_CATEGORIES, WaveScraper

_DURATION_RE = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$", re.IGNORECASE)


def parse_duration(text: str) -> float:
    """Parse a duration like ``1h``, ``30m``, ``90s``, ``1h30m``, or bare seconds."""
    text = text.strip()
    if text.isdigit():
        return float(text)
    m = _DURATION_RE.fullmatch(text)
    if not m or not any(m.groups()):
        raise argparse.ArgumentTypeError(
            f"invalid duration {text!r} (use e.g. 1h, 30m, 90s, 1h30m, or seconds)"
        )
    hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    return float(hours * 3600 + minutes * 60 + seconds)


def _cmd_show(args) -> int:
    results = []
    with WaveScraper(headless=not args.show_browser, locale=args.locale) as scraper:
        for target in args.performances:
            try:
                results.append(scraper.fetch(target))
            except Exception as e:  # keep going across multiple targets
                print(f"error: {target}: {e}", file=sys.stderr)

    if args.json:
        print(json.dumps([asdict(p) for p in results], indent=2))
    else:
        for perf in results:
            print(f"\n{perf.title or perf.performance_ak}  [{perf.performance_ak}]")
            print(f"  {perf.date} {perf.time}  |  {perf.available} available")
            for product in perf.products:
                print(f"    - {product}")
            if perf.price_min is not None:
                print(f"  price range: {perf.price_min:.2f}-{perf.price_max:.2f}")

    return 0 if results else 1


def _cmd_collect(args) -> int:
    def on_progress(index, total, ak, status):
        print(f"[{index}/{total}] {ak}: {status}", file=sys.stderr)

    summary = collect(
        days=args.days,
        target_seconds=parse_duration(args.target_time),
        db_path=args.db,
        proxy=args.proxy,
        locale=args.locale,
        categories=tuple(c.strip() for c in args.categories.split(",") if c.strip()),
        on_progress=on_progress,
    )
    print(summary, file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape ticket prices from The Wave.")
    parser.add_argument("--locale", default="en-GB", help="locale (default: en-GB)")
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="print prices for one or more performances")
    show.add_argument(
        "performances",
        nargs="+",
        metavar="AK_OR_URL",
        help="performanceAk (e.g. TWB.EVN17.PRF1687) or a full performance URL",
    )
    show.add_argument("--json", action="store_true", help="emit JSON instead of text")
    show.add_argument(
        "--show-browser", action="store_true", help="run the browser headed (for debugging)"
    )
    show.set_defaults(func=_cmd_show)

    coll = sub.add_parser("collect", help="scrape N days of prices into a SQLite DB")
    coll.add_argument("--days", type=int, default=7, help="days from today to scrape (default: 7)")
    coll.add_argument(
        "--target-time",
        default="1h",
        help="target wall-clock for the whole pass: 1h, 30m, 90s, or seconds (default: 1h)",
    )
    coll.add_argument("--db", default="prices.db", help="SQLite DB path (default: prices.db)")
    coll.add_argument("--proxy", default=None, help="HTTP proxy URL (default: direct)")
    coll.add_argument(
        "--categories",
        default=",".join(DEFAULT_EVENT_CATEGORIES),
        help=f"comma-separated event category codes (default: {','.join(DEFAULT_EVENT_CATEGORIES)})",
    )
    coll.set_defaults(func=_cmd_collect)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
