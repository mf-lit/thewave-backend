"""The markdown subset a message body may use.

The grammar is closed. The server parses every body on write and rejects
anything outside it, which is what lets the Flutter parser be strict and
small — it never has to guess, degrade, or fall back to rendering raw text.

    Blocks   split on one or more blank lines.
             A block whose every line matches `- ` is a bullet list.
             Otherwise it is a paragraph, and its lines join with one space.
    Inline   `**bold**`, `*italic*`, `[label](https://…)`, in paragraphs and
             list items alike. No nesting: a link label and the inside of an
             emphasis run are plain text.
    Escapes  `\\\\`, `\\*`, `\\[` and nothing else.
    URLs     must start with `https://`.

Two consequences of "closed" worth stating, because they are what an author
trips over and what the client guide has to spell out:

* A bare ``*`` or ``[`` is an error, not literal text. ``2 \\* 3`` is how you
  write a literal asterisk. There is no lenient reading in which an unmatched
  delimiter is "probably just text".
* A list needs a blank line before it. ``Intro:\\n- one\\n- two`` is one block
  whose lines do not *all* start with ``- ``, so it is a paragraph reading
  "Intro: - one - two". The dashboard's live preview is there to make that
  visible before it ships.

The server only needs the boolean — did it parse — but building the real tree
is what lets the client guide state the grammar as fact rather than as a
description of some Python.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .validation import ValidationError

TEXT = "text"
BOLD = "bold"
ITALIC = "italic"
LINK = "link"

PARAGRAPH = "paragraph"
BULLETS = "bullets"

BULLET_PREFIX = "- "
URL_SCHEME = "https://"

# The complete escape set. `(`, `)` and `]` are not in it: they are only ever
# special *inside* a link, where the opening `[` has already been committed to,
# so there is no ambiguity to resolve outside one.
ESCAPABLE = "\\*["

BODY_NOT_TEXT_ERROR = "Invalid body. Expected text"
TRAILING_BACKSLASH_ERROR = "Invalid body: a trailing '\\' escapes nothing"
LINK_LABEL = "a link label"

_CONSTRUCT_NAMES = {
    BOLD: "bold",
    ITALIC: "italic",
    LINK: LINK_LABEL,
}

_OPENERS = {
    BOLD: "**",
    ITALIC: "*",
    LINK: "[",
}

_CLOSERS = {
    BOLD: "**",
    ITALIC: "*",
    LINK: "]",
}


def invalid_escape_error(char: str) -> str:
    return (
        f"Invalid escape '\\{char}' in body. Only '\\\\', '\\*' and '\\[' "
        "can be escaped"
    )


def unclosed_error(construct: str) -> str:
    return (
        f"Unclosed {_CONSTRUCT_NAMES[construct]} in body: "
        f"'{_OPENERS[construct]}' with no matching '{_CLOSERS[construct]}'"
    )


def empty_error(construct: str) -> str:
    return f"Empty {_CONSTRUCT_NAMES[construct]} in body"


def unescaped_error(char: str, construct: str) -> str:
    return (
        f"Unescaped '{char}' inside {_CONSTRUCT_NAMES[construct]} in body. "
        f"Write '\\{char}' for a literal '{char}'"
    )


MISSING_LINK_URL_ERROR = "Invalid link in body: '[label]' must be followed by '(url)'"
UNCLOSED_LINK_URL_ERROR = "Unclosed link URL in body: '(' with no matching ')'"
EMPTY_LINK_URL_ERROR = "Empty link URL in body"
LINK_SCHEME_ERROR = f"Invalid link in body. URLs must start with '{URL_SCHEME}'"


@dataclass(frozen=True)
class Span:
    """A run of inline content. ``url`` is set only when ``kind`` is ``link``."""

    kind: str
    text: str
    url: Optional[str] = None


@dataclass(frozen=True)
class Block:
    """A paragraph or a bullet list.

    ``items`` holds one sequence of spans per line of content: exactly one for
    a paragraph, one per bullet for a list. Keeping the shape uniform means a
    renderer walks both the same way and only branches on ``kind`` to decide
    whether to draw a bullet.
    """

    kind: str
    items: Tuple[Tuple[Span, ...], ...]

    @property
    def spans(self) -> Tuple[Span, ...]:
        """The single item of a paragraph."""
        return self.items[0]


def parse(body: str) -> Tuple[Block, ...]:
    """Parse a message body, or raise ``ValidationError`` saying what is wrong.

    An empty or all-whitespace body parses to no blocks rather than failing —
    "a body is required" is ``models``' rule to state, not this module's.
    """
    if not isinstance(body, str):
        raise ValidationError(BODY_NOT_TEXT_ERROR)

    blocks: List[Block] = []
    for chunk in _chunks(body):
        if all(line.startswith(BULLET_PREFIX) for line in chunk):
            items = tuple(
                _inline(line[len(BULLET_PREFIX):].strip()) for line in chunk
            )
            blocks.append(Block(BULLETS, items))
        else:
            blocks.append(Block(PARAGRAPH, (_inline(" ".join(chunk)),)))
    return tuple(blocks)


def to_json(blocks: Tuple[Block, ...]) -> List[dict]:
    """The parse tree as plain JSON, for the dashboard's live preview.

    The preview renders *this* — the tree the server built — rather than
    re-parsing the body in JavaScript. A second implementation of the grammar
    is a second thing that can disagree with it, and the whole point of
    validating on write is that there is one answer to what a body means.
    """
    return [
        {
            "kind": block.kind,
            "items": [
                [
                    {"kind": span.kind, "text": span.text, "url": span.url}
                    for span in item
                ]
                for item in block.items
            ],
        }
        for block in blocks
    ]


def _chunks(body: str) -> List[List[str]]:
    """Split into blocks on blank lines, with each line stripped.

    Lines are stripped before anything else looks at them, so indentation
    carries no meaning — which it must not, given that a paragraph's lines are
    about to be joined with a single space anyway.
    """
    normalised = body.replace("\r\n", "\n").replace("\r", "\n")
    chunks: List[List[str]] = []
    current: List[str] = []
    for raw in normalised.split("\n"):
        line = raw.strip()
        if line:
            current.append(line)
        elif current:
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks


def _inline(text: str) -> Tuple[Span, ...]:
    """Parse one line of inline content into spans."""
    spans: List[Span] = []
    buffer: List[str] = []
    index, end = 0, len(text)

    def flush() -> None:
        if buffer:
            spans.append(Span(TEXT, "".join(buffer)))
            buffer.clear()

    while index < end:
        char = text[index]
        if char == "\\":
            literal, index = _escape(text, index)
            buffer.append(literal)
        elif text.startswith("**", index):
            flush()
            content, index = _run(text, index + 2, BOLD)
            spans.append(Span(BOLD, content))
        elif char == "*":
            flush()
            content, index = _run(text, index + 1, ITALIC)
            spans.append(Span(ITALIC, content))
        elif char == "[":
            flush()
            span, index = _link(text, index + 1)
            spans.append(span)
        else:
            buffer.append(char)
            index += 1

    flush()
    return tuple(spans)


def _escape(text: str, index: int) -> Tuple[str, int]:
    """Read the escape sequence at ``index``; return the literal and the next index."""
    if index + 1 >= len(text):
        raise ValidationError(TRAILING_BACKSLASH_ERROR)
    char = text[index + 1]
    if char not in ESCAPABLE:
        raise ValidationError(invalid_escape_error(char))
    return char, index + 2


def _run(text: str, start: int, construct: str) -> Tuple[str, int]:
    """Read plain text up to ``construct``'s closing delimiter.

    Nothing nests: inside a run the only markup left is an escape, and an
    unescaped ``*`` or ``[`` is an error rather than a second opener. That is
    what makes an unmatched delimiter impossible to mistake for literal text.
    """
    closer = _CLOSERS[construct]
    buffer: List[str] = []
    index, end = start, len(text)

    while index < end:
        if text.startswith(closer, index):
            content = "".join(buffer)
            if not content:
                raise ValidationError(empty_error(construct))
            return content, index + len(closer)

        char = text[index]
        if char == "\\":
            literal, index = _escape(text, index)
            buffer.append(literal)
            continue
        # A backslash never reaches here — it was consumed above — so this is
        # exactly the unescaped `*` or `[` case.
        if char in ESCAPABLE:
            raise ValidationError(unescaped_error(char, construct))
        buffer.append(char)
        index += 1

    raise ValidationError(unclosed_error(construct))


def _link(text: str, start: int) -> Tuple[Span, int]:
    """Read ``label](url)`` from ``start``, which sits just past the ``[``."""
    label, index = _run(text, start, LINK)

    if index >= len(text) or text[index] != "(":
        raise ValidationError(MISSING_LINK_URL_ERROR)

    closing = text.find(")", index + 1)
    if closing == -1:
        raise ValidationError(UNCLOSED_LINK_URL_ERROR)

    # No escape processing inside a URL: it ends at the first ')', full stop.
    # A URL that needs a literal ')' is one percent-encoding away from working,
    # and the alternative is an escape set that differs between two halves of
    # the same construct.
    url = text[index + 1:closing].strip()
    if not url:
        raise ValidationError(EMPTY_LINK_URL_ERROR)
    if not url.startswith(URL_SCHEME):
        raise ValidationError(LINK_SCHEME_ERROR)

    return Span(LINK, label, url), closing + 1
