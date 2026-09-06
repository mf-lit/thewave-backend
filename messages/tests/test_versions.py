"""Version parsing and range membership.

The parsing half must stay byte-identical to
``upstream-api/src/core/version_gate.py``; the range half is this service's
own, and its two interesting cases are zero-padding and what an unparseable
client version does.
"""
from __future__ import annotations

import pytest

from src import versions


@pytest.mark.parametrize(
    "value, expected",
    [
        ("1", (1,)),
        ("1.2.3", (1, 2, 3)),
        ("0.0.0", (0, 0, 0)),
        ("1.2.3.4", (1, 2, 3, 4)),
        ("", None),
        (None, None),
        ("1.2.3-beta", None),
        ("1.2.x", None),
        ("v1.2.3", None),
        ("1..2", None),
        ("1.-2", None),
    ],
)
def test_parse_version(value, expected):
    assert versions.parse_version(value) == expected


def test_compare_pads_the_shorter_side():
    assert versions.compare((1, 0), (1, 0, 0)) == 0
    assert versions.compare((1, 0), (1, 0, 1)) == -1
    assert versions.compare((1, 0, 1), (1, 0)) == 1


def test_compare_is_numeric_not_lexicographic():
    """The reason targeting filters in Python: "1.0.9" > "1.0.10" as strings."""
    assert versions.compare((1, 0, 10), (1, 0, 9)) == 1


@pytest.mark.parametrize(
    "version, minimum, maximum, expected",
    [
        ("1.2.3", None, None, True),
        ("1.2.3", "1.0.0", None, True),
        ("1.2.3", "1.3.0", None, False),
        ("1.2.3", None, "2.0.0", True),
        ("1.2.3", None, "1.0.0", False),
        ("1.2.3", "1.2.3", "1.2.3", True),  # both bounds inclusive
        ("1.0", "1.0.0", "1.0.0", True),  # padded, so equal
        ("1.0.10", "1.0.9", None, True),
    ],
)
def test_in_range(version, minimum, maximum, expected):
    assert versions.in_range(version, minimum, maximum) is expected


@pytest.mark.parametrize("version", [None, "", "1.2.3-beta"])
def test_unparseable_version_fails_closed_on_a_bound_open_without_one(version):
    assert versions.in_range(version, None, None) is True
    assert versions.in_range(version, "1.0.0", None) is False
    assert versions.in_range(version, None, "2.0.0") is False
