#!/usr/bin/env python3
"""Check response_fields.CLIENT_FIELDS still covers the Flutter model.

The whitelist decides which `fields` keys survive into a calendar response.
Adding a field to the Flutter client's PerformanceFields without adding it here
means the client asks for something the API silently strips - a bug that shows
up as a blank UI, with nothing in any log to explain it.

The two live in separate repos, so this cannot be an ordinary unit test. Run it
from the deploy path instead:

    python3 scripts/check_client_fields.py [path/to/performance.dart]

Exits non-zero on drift.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.response_fields import CLIENT_FIELDS  # noqa: E402

DEFAULT_MODEL = Path("/home/marc/waveform/lib/models/performance.dart")

# `final String? priceLabel;` and friends, within the PerformanceFields class.
FIELD_PATTERN = re.compile(r"^\s*final\s+[\w<>?]+\s+(\w+);", re.MULTILINE)


def declared_fields(source: str) -> set[str]:
    """The property names PerformanceFields declares."""
    start = source.index("class PerformanceFields")
    end = source.index("const PerformanceFields", start)
    return set(FIELD_PATTERN.findall(source[start:end]))


def main(argv: list[str]) -> int:
    model = Path(argv[1]) if len(argv) > 1 else DEFAULT_MODEL
    if not model.is_file():
        print(f"SKIP: no Flutter model at {model}")
        return 0

    try:
        declared = declared_fields(model.read_text())
    except ValueError:
        print(f"FAIL: could not find PerformanceFields in {model}")
        return 1

    if not declared:
        print(f"FAIL: parsed no fields out of {model} - has the model changed shape?")
        return 1

    missing = declared - CLIENT_FIELDS
    if missing:
        print("FAIL: the client reads fields the API strips:")
        for name in sorted(missing):
            print(f"  {name}")
        print("\nAdd them to CLIENT_FIELDS in src/core/response_fields.py.")
        return 1

    extra = CLIENT_FIELDS - declared
    note = f" ({len(extra)} kept but no longer read: {', '.join(sorted(extra))})" if extra else ""
    print(f"OK: all {len(declared)} client fields survive the strip{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
