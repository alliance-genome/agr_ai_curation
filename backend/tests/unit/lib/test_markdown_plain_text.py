"""Markdown clean-up keeps literal characters and removes only real markup.

All examples are made up. Each "before" value is what the clean-up used until
0.12.0 (``agr_abc_document_parsers.strip_markdown_formatting`` 1.5.1, regex
based) produced for the same input, checked against that version when this
file was written. It is kept next to the new value so each change in stored
text is visible; the tests assert the new value.
"""

import pytest

from src.lib.markdown_plain_text import (
    markdown_inline_to_plain_text,
    markdown_table_to_plain_text,
    split_table_row_cells,
)


@pytest.mark.parametrize(
    ("raw", "before", "after"),
    [
        # Literal asterisks now survive.
        ("p.Tyr318*/c.954C>G and p.Gln12*", "p.Tyr318/c.954C>G and p.Gln12", "p.Tyr318*/c.954C>G and p.Gln12*"),
        ("Significance was ****.", "Significance was **.", "Significance was ****."),
        ("use `a*b*c` here", "use abc here", "use a*b*c here"),
        ("\\*x\\* stays", "\\x\\ stays", "*x* stays"),
        # Real emphasis is still removed.
        ("*drag-1* mutants", "drag-1 mutants", "drag-1 mutants"),
        ("**bold** text and ~~struck~~", "bold text and struck", "bold text and struck"),
        ("[text](https://example.org/a)", "text", "text"),
        # Tags are removed before the emphasis parse, as before.
        ("10<sup>-5</sup>", "10-5", "10-5"),
        ("Ca**<sup>2+</sup>**", "Ca2+", "Ca2+"),
        ("H<sub>2</sub>O and <u>underlined</u>", "H2O and underlined", "H2O and underlined"),
        # A single tilde is literal.
        ("~5 kb and ~10 kb", "~5 kb and ~10 kb", "~5 kb and ~10 kb"),
        # Other HTML stays literal text.
        ("a<br>b <i>x</i>", "a<br>b <i>x</i>", "a<br>b <i>x</i>"),
    ],
)
def test_inline_clean_up_before_and_after(raw, before, after):
    assert markdown_inline_to_plain_text(raw) == after


@pytest.mark.parametrize(
    ("raw", "before", "after"),
    [
        # Release-note side effects of reading Markdown with a standard parser.
        ("_italic_ and snake_case_name", "_italic_ and snake_case_name", "italic and snake_case_name"),
        ("\\_ \\# \\[x\\]", "\\_ \\# \\[x\\]", "_ # [x]"),
        ("AT&amp;T &lt;5 &#42;", "AT&amp;T &lt;5 &#42;", "AT&T <5 *"),
        ("<https://example.org>", "<https://example.org>", "https://example.org"),
        ("S. *cerevisiae***.**", "S. cerevisiae.", "S. cerevisiae**.**"),
        ("a\x00b", "a\x00b", "a�b"),
    ],
)
def test_parser_side_effects_before_and_after(raw, before, after):
    assert markdown_inline_to_plain_text(raw) == after


def test_known_limit_a_pair_of_unmarked_literal_asterisks_reads_as_emphasis():
    # SCRUM-6973: ABC's converted text cannot mark these asterisks as literal,
    # so CommonMark reads the pair as emphasis. One unpaired "w[*]" survives.
    assert markdown_inline_to_plain_text("w[*]; y[*]") == "w[]; y[]"
    assert markdown_inline_to_plain_text("w[*] flies") == "w[*] flies"


@pytest.mark.parametrize(
    ("raw", "before", "after"),
    [
        ("| **** | p.Tyr318* |", "| * | p.Tyr318 |", "| **** | p.Tyr318* |"),
        ("| a* | b | c* |", "| a | b | c |", "| a* | b | c* |"),
        (
            "| **Gene** | *Allele* |\n| --- | :---: |\n| p.Gln12* | **** |",
            "| Gene | Allele |\n| --- | :---: |\n| p.Gln12 | * |",
            "| Gene | Allele |\n| --- | :---: |\n| p.Gln12* | **** |",
        ),
        ("| a \\| *b* | c |", "| a \\| b | c |", "| a \\| b | c |"),
        ("| x \\\\| y |", "| x \\\\| y |", "| x \\\\| y |"),
        ("|  padded  |   |", "|  padded  |   |", "|  padded  |   |"),
    ],
)
def test_table_clean_up_is_cell_by_cell(raw, before, after):
    assert markdown_table_to_plain_text(raw) == after


def test_escaped_pipe_inside_a_cell_stays_escaped_and_in_its_cell():
    cleaned = markdown_table_to_plain_text("| 1 \\| 2 | **3** |")

    assert cleaned == "| 1 \\| 2 | 3 |"
    assert split_table_row_cells(cleaned) == ["", " 1 \\| 2 ", " 3 ", ""]


def test_split_table_row_cells_round_trips_the_row():
    row = "| a | b \\| c | d \\\\| e"

    cells = split_table_row_cells(row)

    assert cells == ["", " a ", " b \\| c ", " d \\\\", " e"]
    assert "|".join(cells) == row


def test_empty_text_stays_empty():
    assert markdown_inline_to_plain_text("") == ""
    assert markdown_table_to_plain_text("") == ""
