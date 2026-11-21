"""Turn Markdown inline markup into plain text for stored document pieces.

A standard CommonMark parser (``markdown-it-py``) decides what is markup, so
only characters the parser reads as formatting are removed. Literal characters
that only look like markup stay, for example the asterisk in a stop codon
(``p.Tyr318*``), a run of significance stars (``****``) or a tilde meaning
"about" (``~5 kb``).

What is removed or rewritten:

- ``<u>``, ``<sup>`` and ``<sub>`` tag pairs (the tags only, before the
  emphasis parse, so ``Ca**<sup>2+</sup>**`` reads as ``Ca2+``). Characters
  that were inside such a pair are never read as markup, so
  ``val<sup>*</sup>sig<sup>*</sup>`` keeps both asterisks;
- emphasis and strong emphasis with ``*`` or ``_``, and ``~~`` strikethrough
  (single-tilde strikethrough is off). Underscores inside a name are never
  touched (``snake_case``); a name with an underscore at both ends that
  starts at a word edge or after punctuation loses those two underscores
  (``__main__`` gives ``main``, ``${X}_R1_`` gives ``${X}R1``);
- code span backticks (the code text is kept exactly);
- links and images (the link text or image description is kept);
- backslash escapes are undone (``\\*`` gives ``*``), except an escaped
  backslash, which stays as two backslashes everywhere because in a table
  cell undoing it would turn the next pipe into part of the cell;
- character references are decoded (``&amp;`` gives ``&``, ``&#0;`` and NUL
  give U+FFFD), and the parser's output is normalized with NFKC, so a
  character code ends the same as the literal character (``&nbsp;`` gives a
  space, ``&micro;`` gives ``μ``, ``&#178;`` gives ``2``);
- line endings: a carriage return becomes a line feed.

Kept exactly as written: text in angle brackets (``<UAS:GFP>``,
``Tg(<lexAop:GFP>)``, ``<https://example.org>``, e-mail addresses), because
autolinks and raw HTML are both off; other HTML tags; a single ``~``.
Block structure (headings, list markers, table pipes) is handled by the
caller, which hands this module one element's text at a time.

The clean-up must run exactly once per text: a second run would read an
asterisk that was escaped in the source (``\\*x\\*``) as emphasis and delete
it. Every step is linear in the length of the text.
"""

from __future__ import annotations

import re
import unicodedata

from markdown_it import MarkdownIt
from markdown_it.token import Token

# One opening or closing tag. Fixed alternatives, so matching cannot backtrack.
_INLINE_TAG = re.compile(r"<(/?)(u|sup|sub)>", re.IGNORECASE)
# An existing backslash escape, or a markup character that must be escaped.
_ESCAPE_OR_MARKER = re.compile(r"(\\.)|([*_~`])", re.DOTALL)

_MARKUP_ONLY_TOKENS = frozenset(
    {
        "em_open",
        "em_close",
        "strong_open",
        "strong_close",
        "s_open",
        "s_close",
        "link_open",
        "link_close",
    }
)

# CommonMark with raw HTML and autolinks off (text in angle brackets stays as
# written) and ``~~`` strikethrough on. markdown-it's strikethrough rule needs
# two tildes, so a single "~" is always literal.
_PARSER = (
    MarkdownIt("commonmark", {"html": False})
    .enable("strikethrough")
    .disable("autolink")
)

_ESCAPED_BACKSLASH = "\\\\"
# Escapes that hold a row's cell structure: an escaped pipe, and an escaped
# backslash (undoing it would leave a backslash that escapes the next pipe).
_STRUCTURAL_CELL_ESCAPES = frozenset({"\\|", _ESCAPED_BACKSLASH})
_PROSE_KEPT_ESCAPES = frozenset({_ESCAPED_BACKSLASH})


def _escape_markers(segment: str) -> str:
    """Backslash-escape markup characters that are not escaped already."""

    return _ESCAPE_OR_MARKER.sub(
        lambda match: match.group(1) or "\\" + match.group(2),
        segment,
    )


def _strip_inline_tags(text: str) -> str:
    """Remove ``<u>``/``<sup>``/``<sub>`` pairs in one left-to-right pass.

    A closing tag pairs with the nearest open tag of the same name on the same
    line; a pair must hold at least one character. Unpaired tags stay as
    literal text. Markup characters inside a pair are escaped, so they stay
    literal after the emphasis parse instead of pairing with text outside.
    """

    tags = list(_INLINE_TAG.finditer(text))
    if not tags:
        return text
    open_tags: dict[str, list[int]] = {}
    paired = [False] * len(tags)
    previous_end = 0
    for position, tag in enumerate(tags):
        if "\n" in text[previous_end : tag.start()]:
            open_tags = {}
        previous_end = tag.end()
        name = tag.group(2).lower()
        if not tag.group(1):
            open_tags.setdefault(name, []).append(position)
            continue
        openers = open_tags.get(name)
        if not openers:
            continue
        opener = openers.pop()
        if tags[opener].end() < tag.start():
            paired[opener] = paired[position] = True

    parts: list[str] = []
    depth = 0
    cursor = 0
    for position, tag in enumerate(tags):
        if not paired[position]:
            continue
        segment = text[cursor : tag.start()]
        parts.append(_escape_markers(segment) if depth else segment)
        depth += -1 if tag.group(1) else 1
        cursor = tag.end()
    parts.append(text[cursor:])
    return "".join(parts)


def _split_on_kept_escapes(text: str, kept: frozenset[str]) -> list[str]:
    """Split text into runs of text and the kept two-character escapes."""

    pieces: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(text):
        escape = text[index : index + 2]
        if escape in kept:
            pieces.append("".join(current))
            pieces.append(escape)
            current = []
            index += 2
        elif text[index] == "\\" and index + 1 < len(text):
            current.append(escape)
            index += 2
        else:
            current.append(text[index])
            index += 1
    pieces.append("".join(current))
    return pieces


def _render_inline_tokens(tokens: list[Token]) -> str:
    parts: list[str] = []
    for token in tokens:
        if token.type in ("text", "code_inline"):
            parts.append(token.content)
        elif token.type in ("softbreak", "hardbreak"):
            parts.append("\n")
        elif token.type == "image":
            parts.append(_render_inline_tokens(token.children or []))
        elif token.type in _MARKUP_ONLY_TOKENS:
            continue
        else:
            raise ValueError(f"Unexpected Markdown inline token type: {token.type}")
    return "".join(parts)


def _parse_inline(text: str) -> str:
    if not text:
        return ""
    rendered: list[str] = []
    for block_token in _PARSER.parseInline(text):
        rendered.append(_render_inline_tokens(block_token.children or []))
    # NFKC is idempotent, so normalizing the output too keeps the run-once
    # rule while making a decoded character code match its literal form.
    return unicodedata.normalize("NFKC", "".join(rendered))


def _keep_cell_boundaries(cleaned: str) -> str:
    """Re-escape a parsed cell piece so it cannot move the row's cell boundaries.

    Decoding can produce a pipe (``&#124;``) or a backslash (``&#92;``). The
    row splitter treats a pipe after an odd number of backslashes as part of
    the cell, so a pipe after an even run gets one more backslash, and a piece
    that ends in an odd run gets one more so it cannot escape the next pipe.
    """

    parts: list[str] = []
    run = 0
    for character in cleaned:
        if character == "|" and run % 2 == 0:
            parts.append("\\")
        parts.append(character)
        run = run + 1 if character == "\\" else 0
    if run % 2:
        parts.append("\\")
    return "".join(parts)


def _clean_inline(text: str, kept_escapes: frozenset[str], *, in_cell: bool = False) -> str:
    pieces = _split_on_kept_escapes(_strip_inline_tags(text), kept_escapes)
    cleaned: list[str] = []
    for piece in pieces:
        if piece in kept_escapes:
            cleaned.append(piece)
        elif in_cell:
            cleaned.append(_keep_cell_boundaries(_parse_inline(piece)))
        else:
            cleaned.append(_parse_inline(piece))
    return "".join(cleaned)


def markdown_inline_to_plain_text(text: str) -> str:
    """Return ``text`` with its Markdown inline markup removed."""

    if not text:
        return ""
    return _clean_inline(text, _PROSE_KEPT_ESCAPES)


def split_table_row_cells(row: str) -> list[str]:
    """Split one Markdown table row on its unescaped pipes.

    A backslash escapes the character after it, so a pipe after an odd number
    of backslashes stays inside its cell. The result keeps the text before the
    first pipe and after the last pipe, so ``"|".join(cells)`` gives back
    ``row`` exactly.
    """

    cells: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(row):
        character = row[index]
        if character == "\\" and index + 1 < len(row):
            current.append(row[index : index + 2])
            index += 2
        elif character == "|":
            cells.append("".join(current))
            current = []
            index += 1
        else:
            current.append(character)
            index += 1
    cells.append("".join(current))
    return cells


def _clean_table_cell(cell: str) -> str:
    content = cell.strip()
    if not content:
        return cell
    leading = cell[: len(cell) - len(cell.lstrip())]
    trailing = cell[len(cell.rstrip()) :]
    return f"{leading}{_clean_inline(content, _STRUCTURAL_CELL_ESCAPES, in_cell=True)}{trailing}"


def markdown_table_to_plain_text(table: str) -> str:
    """Clean a Markdown table cell by cell.

    Each line that starts with ``|`` is a row: its pipes stay where they are
    and each cell's text is cleaned on its own, so markup never pairs across
    cells. A pipe or backslash produced by decoding is re-escaped, so the row
    keeps exactly its cells. Any other line is cleaned as inline text.
    """

    cleaned_lines: list[str] = []
    for line in table.split("\n"):
        if line.lstrip().startswith("|"):
            cleaned_lines.append(
                "|".join(_clean_table_cell(cell) for cell in split_table_row_cells(line))
            )
        else:
            cleaned_lines.append(markdown_inline_to_plain_text(line))
    return "\n".join(cleaned_lines)
