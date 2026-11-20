"""Turn Markdown inline markup into plain text for stored document pieces.

A standard CommonMark parser (``markdown-it-py``) decides what is markup, so
only characters the parser reads as formatting are removed. Literal characters
that only look like markup stay, for example the asterisk in a stop codon
(``p.Tyr318*``), a run of significance stars (``****``) or a tilde meaning
"about" (``~5 kb``).

What is removed or rewritten:

- ``<u>``, ``<sup>`` and ``<sub>`` tag pairs (the tags only, before the
  emphasis parse, so ``Ca**<sup>2+</sup>**`` reads as ``Ca2+``);
- emphasis and strong emphasis with ``*`` or ``_``, and ``~~`` strikethrough
  (single-tilde strikethrough is off);
- code span backticks (the code text is kept exactly);
- links and autolinks (the link text is kept);
- backslash escapes are undone and character references are decoded
  (``\\*`` gives ``*``, ``&amp;`` gives ``&``).

Other HTML is kept as literal text. Block structure (headings, list markers,
table pipes) is handled by the caller, which hands this module one element's
text at a time.

The clean-up must run exactly once per text: a second run would read an
asterisk that was escaped in the source (``\\*x\\*``) as emphasis and delete
it.
"""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.token import Token

_INLINE_TAG_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<u>(.+?)</u>", re.IGNORECASE),
    re.compile(r"<sup>(.+?)</sup>", re.IGNORECASE),
    re.compile(r"<sub>(.+?)</sub>", re.IGNORECASE),
)

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

# CommonMark with raw HTML off (HTML stays literal text) and ``~~``
# strikethrough on. markdown-it's strikethrough rule needs two tildes, so a
# single "~" is always literal.
_PARSER = MarkdownIt("commonmark", {"html": False}).enable("strikethrough")


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


def markdown_inline_to_plain_text(text: str) -> str:
    """Return ``text`` with its Markdown inline markup removed."""

    if not text:
        return ""
    for pattern in _INLINE_TAG_PATTERNS:
        text = pattern.sub(r"\1", text)
    rendered: list[str] = []
    for block_token in _PARSER.parseInline(text):
        rendered.append(_render_inline_tokens(block_token.children or []))
    return "".join(rendered)


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


# Escapes that hold a row's cell structure: an escaped pipe, and an escaped
# backslash (undoing it would leave a backslash that escapes the next pipe).
_STRUCTURAL_CELL_ESCAPES = frozenset({"\\|", "\\\\"})


def _split_cell_on_structural_escapes(content: str) -> list[str]:
    """Split cell text into runs of text and structural escapes kept apart."""

    pieces: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(content):
        escape = content[index : index + 2]
        if escape in _STRUCTURAL_CELL_ESCAPES:
            pieces.append("".join(current))
            pieces.append(escape)
            current = []
            index += 2
        elif content[index] == "\\" and index + 1 < len(content):
            current.append(escape)
            index += 2
        else:
            current.append(content[index])
            index += 1
    pieces.append("".join(current))
    return pieces


def _clean_table_cell(cell: str) -> str:
    content = cell.strip()
    if not content:
        return cell
    leading = cell[: len(cell) - len(cell.lstrip())]
    trailing = cell[len(cell.rstrip()) :]
    cleaned = "".join(
        piece if piece in _STRUCTURAL_CELL_ESCAPES else markdown_inline_to_plain_text(piece)
        for piece in _split_cell_on_structural_escapes(content)
    )
    return f"{leading}{cleaned}{trailing}"


def markdown_table_to_plain_text(table: str) -> str:
    """Clean a Markdown table cell by cell.

    Each line that starts with ``|`` is a row: its pipes stay where they are
    and each cell's text is cleaned on its own, so markup never pairs across
    cells. Any other line is cleaned as inline text.
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
