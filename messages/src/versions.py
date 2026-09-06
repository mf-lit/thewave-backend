"""Client version parsing and comparison.

A deliberate copy of the padded-tuple logic in
``upstream-api/src/core/version_gate.py`` — ``_parse_version`` and the padding
inside ``is_upgrade_required``. The two services are separate images with
separate dependency sets, so it cannot be imported; it is duplicated instead,
and the behaviour is kept byte-identical on purpose.

That includes the failure mode: anything with a non-numeric segment parses as
None rather than raising or being coerced. A build suffix like ``1.2.3-beta``
is not a version this system can order, and pretending otherwise would put a
client in or out of a range by accident. Callers decide what None means —
``targeting`` excludes such a client from any message that sets a version
bound, and includes it in messages that set neither.

If the gate's rules ever change, change them here too.
"""
from __future__ import annotations

from typing import Optional, Tuple


def parse_version(version: Optional[str]) -> Optional[Tuple[int, ...]]:
    """Parse a dot-separated numeric version, e.g. ``"2.3.0"`` -> ``(2, 3, 0)``.

    Returns None if the string is empty or any segment isn't a plain
    non-negative integer.
    """
    if not version:
        return None
    segments = version.split(".")
    parsed = []
    for segment in segments:
        if not segment.isdigit():
            return None
        parsed.append(int(segment))
    return tuple(parsed)


def compare(left: Tuple[int, ...], right: Tuple[int, ...]) -> int:
    """Order two parsed versions: -1, 0 or 1.

    The shorter tuple is zero-padded first, so ``1.0`` and ``1.0.0`` compare
    equal. Without that, plain tuple comparison makes ``1.0`` the *lesser* of
    the two and a client on ``1.0`` falls outside ``min_version: "1.0.0"``.
    """
    length = max(len(left), len(right))
    padded_left = left + (0,) * (length - len(left))
    padded_right = right + (0,) * (length - len(right))
    if padded_left < padded_right:
        return -1
    return 1 if padded_left > padded_right else 0


def in_range(
    version: Optional[str], minimum: Optional[str], maximum: Optional[str]
) -> bool:
    """Whether ``version`` sits within an inclusive ``[minimum, maximum]``.

    A bound left as None is no constraint on that end. When neither bound is
    set every client passes, including one whose version is missing or
    unparseable — the message simply is not targeted by version.

    When either bound *is* set, an unparseable client version fails: the rule
    is a constraint the client cannot be shown to satisfy. Fails closed on the
    constraint, open without one.
    """
    if minimum is None and maximum is None:
        return True

    parsed = parse_version(version)
    if parsed is None:
        return False

    low = parse_version(minimum)
    if low is not None and compare(parsed, low) < 0:
        return False

    high = parse_version(maximum)
    if high is not None and compare(parsed, high) > 0:
        return False

    return True
