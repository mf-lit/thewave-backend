"""The body grammar: every construct accepted, every malformed form rejected.

The client guide states this grammar as fact, so these tests are what make it
true. A change here is a change to a published contract and needs a client
release, not just a server one.
"""
from __future__ import annotations

import pytest

from src import markdown
from src.markdown import BOLD, BULLETS, ITALIC, LINK, PARAGRAPH, TEXT, Span
from src.validation import ValidationError


def spans(body: str):
    """The spans of a single-paragraph body."""
    blocks = markdown.parse(body)
    assert len(blocks) == 1
    return blocks[0].spans


def test_empty_body_parses_to_no_blocks():
    assert markdown.parse("") == ()
    assert markdown.parse("   \n\n  ") == ()


def test_plain_paragraph():
    assert spans("Just words.") == (Span(TEXT, "Just words."),)


def test_paragraph_lines_join_with_one_space():
    assert spans("one\ntwo\nthree") == (Span(TEXT, "one two three"),)


def test_indentation_carries_no_meaning():
    assert spans("  one\n    two") == (Span(TEXT, "one two"),)


def test_blocks_split_on_one_or_more_blank_lines():
    blocks = markdown.parse("first\n\nsecond\n\n\n\nthird")
    assert [block.kind for block in blocks] == [PARAGRAPH] * 3
    assert blocks[2].spans == (Span(TEXT, "third"),)


def test_bullet_list():
    blocks = markdown.parse("- one\n- two")
    assert len(blocks) == 1
    assert blocks[0].kind == BULLETS
    assert blocks[0].items == ((Span(TEXT, "one"),), (Span(TEXT, "two"),))


def test_a_block_is_a_list_only_when_every_line_is_a_bullet():
    """The documented rule, and the trap it sets.

    Without the blank line the lead-in and the bullets are one block, which is
    a paragraph — so the bullets read as literal text. The dashboard's live
    preview exists to make this visible before it ships.
    """
    blocks = markdown.parse("Closures:\n- Tuesday\n- Wednesday")
    assert len(blocks) == 1
    assert blocks[0].kind == PARAGRAPH
    assert blocks[0].spans == (Span(TEXT, "Closures: - Tuesday - Wednesday"),)

    blocks = markdown.parse("Closures:\n\n- Tuesday\n- Wednesday")
    assert [block.kind for block in blocks] == [PARAGRAPH, BULLETS]


def test_bold_italic_and_link():
    assert spans("a **b** c") == (
        Span(TEXT, "a "),
        Span(BOLD, "b"),
        Span(TEXT, " c"),
    )
    assert spans("a *b* c") == (
        Span(TEXT, "a "),
        Span(ITALIC, "b"),
        Span(TEXT, " c"),
    )
    assert spans("see [the site](https://thewave.com) now") == (
        Span(TEXT, "see "),
        Span(LINK, "the site", "https://thewave.com"),
        Span(TEXT, " now"),
    )


def test_inline_markup_works_inside_a_list_item():
    blocks = markdown.parse("- **Tuesday** closed")
    assert blocks[0].items == ((Span(BOLD, "Tuesday"), Span(TEXT, " closed")),)


def test_bold_is_matched_before_italic():
    assert spans("**bold**") == (Span(BOLD, "bold"),)


def test_adjacent_runs():
    assert spans("**a***b*") == (Span(BOLD, "a"), Span(ITALIC, "b"))


@pytest.mark.parametrize(
    "body, expected",
    [
        (r"2 \* 3", "2 * 3"),
        (r"a \\ b", "a \\ b"),
        (r"see \[this]", "see [this]"),
    ],
)
def test_escapes(body, expected):
    assert spans(body) == (Span(TEXT, expected),)


def test_escapes_work_inside_a_run():
    assert spans(r"**2 \* 3**") == (Span(BOLD, "2 * 3"),)
    assert spans(r"[a \[b](https://x.com)") == (Span(LINK, "a [b", "https://x.com"),)


def test_closing_brackets_outside_a_link_are_literal():
    """`]` and `)` are only special inside a link, so they need no escape."""
    assert spans("a ) b ] c") == (Span(TEXT, "a ) b ] c"),)


@pytest.mark.parametrize(
    "body, expected",
    [
        ("**unclosed", markdown.unclosed_error(BOLD)),
        ("*unclosed", markdown.unclosed_error(ITALIC)),
        ("[unclosed", markdown.unclosed_error(LINK)),
        ("****", markdown.empty_error(BOLD)),
        ("**", markdown.unclosed_error(BOLD)),
        ("[](https://x.com)", markdown.empty_error(LINK)),
        ("**a*b**", markdown.unescaped_error("*", BOLD)),
        ("[a*b](https://x.com)", markdown.unescaped_error("*", LINK)),
        ("[a[b](https://x.com)", markdown.unescaped_error("[", LINK)),
        ("[label]", markdown.MISSING_LINK_URL_ERROR),
        ("[label] (https://x.com)", markdown.MISSING_LINK_URL_ERROR),
        ("[label](https://x.com", markdown.UNCLOSED_LINK_URL_ERROR),
        ("[label]()", markdown.EMPTY_LINK_URL_ERROR),
        ("[label](http://x.com)", markdown.LINK_SCHEME_ERROR),
        ("[label](mailto:a@b.com)", markdown.LINK_SCHEME_ERROR),
        (r"a \n b", markdown.invalid_escape_error("n")),
        ("ends with a backslash \\", markdown.TRAILING_BACKSLASH_ERROR),
    ],
)
def test_rejections(body, expected):
    with pytest.raises(ValidationError) as excinfo:
        markdown.parse(body)
    assert str(excinfo.value) == expected


def test_a_bare_asterisk_is_an_error_not_literal_text():
    """The closed-grammar rule: no lenient reading of an unmatched delimiter."""
    with pytest.raises(ValidationError):
        markdown.parse("open 9 * 5")


def test_non_text_body():
    with pytest.raises(ValidationError) as excinfo:
        markdown.parse(None)
    assert str(excinfo.value) == markdown.BODY_NOT_TEXT_ERROR
