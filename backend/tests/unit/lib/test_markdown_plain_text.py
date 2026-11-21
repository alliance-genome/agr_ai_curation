"""Markdown clean-up keeps literal characters and removes only real markup.

All examples are made up. Each "before" value is what the clean-up used until
0.12.0 (``agr_abc_document_parsers.strip_markdown_formatting`` 1.5.1, regex
based) produced for the same input, checked against that version when this
file was written. It is kept next to the new value so each change in stored
text is visible; the tests assert the new value.
"""

import random
import time

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
        # Characters inside a stripped tag are never read as markup.
        ("val<sup>*</sup>sig<sup>*</sup>", "val</sup>sig<sup>", "val*sig*"),
        ("Smith<sup>*</sup>, Jones<sup>*</sup>", "Smith</sup>, Jones<sup>", "Smith*, Jones*"),
        ("<sup>*a*</sup> note", "a note", "*a* note"),
        ("<sup><u>x</u></sup>", "x", "x"),
        # An empty pair, or a pair split across lines, stays as written.
        ("<sup></sup>", "<sup></sup>", "<sup></sup>"),
        ("<sup>a\nb</sup>", "<sup>a\nb</sup>", "<sup>a\nb</sup>"),
        # A single tilde is literal.
        ("~5 kb and ~10 kb", "~5 kb and ~10 kb", "~5 kb and ~10 kb"),
        # Other HTML stays literal text.
        ("a<br>b <i>x</i>", "a<br>b <i>x</i>", "a<br>b <i>x</i>"),
        # Text in angle brackets stays exactly as written (autolinks are off).
        ("<UAS:GFP> line", "<UAS:GFP> line", "<UAS:GFP> line"),
        ("Tg(<lexAop:GFP>) fish", "Tg(<lexAop:GFP>) fish", "Tg(<lexAop:GFP>) fish"),
        ("Pax6<Sey> mice", "Pax6<Sey> mice", "Pax6<Sey> mice"),
        ("<Df(3L)> and p<0.05 and a<b>c", "<Df(3L)> and p<0.05 and a<b>c", "<Df(3L)> and p<0.05 and a<b>c"),
        ("<https://example.org>", "<https://example.org>", "<https://example.org>"),
        ("<ab@cd.ef>", "<ab@cd.ef>", "<ab@cd.ef>"),
        # Underscores inside a word are never emphasis.
        ("snake_case_name w_1118_ my_gene_1", "snake_case_name w_1118_ my_gene_1", "snake_case_name w_1118_ my_gene_1"),
        # An escaped backslash stays as two backslashes, as in table cells.
        ("a\\\\b", "a\\\\b", "a\\\\b"),
    ],
)
def test_inline_clean_up_before_and_after(raw, before, after):
    assert markdown_inline_to_plain_text(raw) == after


@pytest.mark.parametrize(
    ("raw", "before", "after"),
    [
        # Release-note side effects of reading Markdown with a standard parser.
        # Underscore emphasis: a pair that opens at the start of a word and
        # closes at the end of one, also next to punctuation.
        ("_italic_ and snake_case_name", "_italic_ and snake_case_name", "italic and snake_case_name"),
        ("__init__ method", "__init__ method", "init method"),
        ("UAS-_GAL4_ driver", "UAS-_GAL4_ driver", "UAS-GAL4 driver"),
        # Backslash escapes are undone.
        ("\\_ \\# \\[x\\]", "\\_ \\# \\[x\\]", "_ # [x]"),
        # Character references are decoded.
        ("AT&amp;T &lt;5 &#42;", "AT&amp;T &lt;5 &#42;", "AT&T <5 *"),
        # Decoded characters are normalized with NFKC, like literal ones.
        ("5&nbsp;mM", "5&nbsp;mM", "5 mM"),
        ("&micro;M and x&#178;", "&micro;M and x&#178;", "\u03bcM and x2"),
        ("a&#0;b", "a&#0;b", "a\ufffdb"),
        ("a\x00b", "a\x00b", "a\ufffdb"),
        # An image keeps its description.
        ("![alt text](img.png)", "!alt text", "alt text"),
        # Line endings: a backslash hard break loses its backslash, CR becomes LF.
        ("x\\\ny", "x\\\ny", "x\ny"),
        ("a\rb", "a\rb", "a\nb"),
        # Stray marks from Word italics that end in punctuation.
        ("S. *cerevisiae***.**", "S. cerevisiae.", "S. cerevisiae**.**"),
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
        ("| a\\\\b | c |", "| a\\\\b | c |", "| a\\\\b | c |"),
        ("| val<sup>*</sup>x<sup>*</sup> | <UAS:GFP> |", "| val</sup>x<sup> | <UAS:GFP> |", "| val*x* | <UAS:GFP> |"),
        ("|  padded  |   |", "|  padded  |   |", "|  padded  |   |"),
    ],
)
def test_table_clean_up_is_cell_by_cell(raw, before, after):
    assert markdown_table_to_plain_text(raw) == after


@pytest.mark.parametrize(
    ("literal", "encoded"),
    [
        ("5\u00a0mM", "5&nbsp;mM"),
        ("\u00b5M", "&micro;M"),
        ("x\u00b2", "x&#178;"),
        ("a\u2009b", "a&#8201;b"),
    ],
)
def test_a_character_code_ends_the_same_as_its_literal_character(literal, encoded):
    assert markdown_inline_to_plain_text(encoded) == markdown_inline_to_plain_text(literal)
    assert markdown_table_to_plain_text(f"| {encoded} |") == markdown_table_to_plain_text(f"| {literal} |")


@pytest.mark.parametrize(
    ("raw", "after"),
    [
        # Decoding must never add, remove or move a cell boundary.
        ("| a&#124;b | c |", "| a\\|b | c |"),
        ("| x | &vert; |", "| x | \\| |"),
        ("| a&#92;| b |", "| a\\\\| b |"),
        ("| a&#92;&#92;| b |", "| a\\\\| b |"),
        ("| &#65372; | b |", "| \\| | b |"),
        # A decoded backslash followed by a space cannot escape the pipe.
        ("| &#92; |", "| \\ |"),
        ("| a&#92; | b |", "| a\\ | b |"),
    ],
)
def test_decoded_characters_never_move_a_cell_boundary(raw, after):
    cleaned = markdown_table_to_plain_text(raw)

    assert cleaned == after
    assert len(split_table_row_cells(cleaned)) == len(split_table_row_cells(raw))


@pytest.mark.parametrize(
    "row",
    ["| C:\\Users\\data\\ | x |", "| path C:\\ |", "| a | b\\"],
)
def test_a_literal_trailing_backslash_before_a_space_or_the_row_end_is_kept(row):
    assert markdown_table_to_plain_text(row) == row
    assert len(split_table_row_cells(markdown_table_to_plain_text(row))) == len(split_table_row_cells(row))


def test_random_rows_keep_their_cells_and_plain_cells_stay_unchanged():
    rng = random.Random(1460)
    for _ in range(2000):
        plain = "|" + "".join(rng.choice(["a", "b", " ", "\\", "|"]) for _ in range(rng.randint(1, 16)))
        assert markdown_table_to_plain_text(plain) == plain
        coded = "|" + "".join(
            rng.choice(["a", " ", "\\", "|", "&#92;", "&#124;", "&vert;", "*"])
            for _ in range(rng.randint(1, 12))
        )
        assert len(split_table_row_cells(markdown_table_to_plain_text(coded))) == len(split_table_row_cells(coded))


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


_HOSTILE_UNITS = ["<sup>", "<u>", "<sub>", "<sup>*", "&", "<", "*", "_", "~", "`", "![", "[", "\\\\"]


def _repeat_to(unit: str, size: int) -> str:
    return (unit * (size // len(unit) + 1))[:size]


def _clean_both_ways(text: str) -> None:
    markdown_inline_to_plain_text(text)
    markdown_table_to_plain_text(f"| {text} |")


def _best_seconds(text: str, runs: int = 2) -> float:
    best = float("inf")
    for _ in range(runs):
        started = time.perf_counter()
        _clean_both_ways(text)
        best = min(best, time.perf_counter() - started)
    return best


@pytest.mark.parametrize("unit", _HOSTILE_UNITS)
def test_hostile_input_cost_grows_linearly(unit):
    # Eight times the input costs about eight times the time on a linear path
    # and about sixty-four times on a quadratic one; 24 leaves room for a busy
    # machine either way. A ratio does not depend on how fast the machine is.
    small = _best_seconds(_repeat_to(unit, 16_000))
    large = _best_seconds(_repeat_to(unit, 128_000))

    assert large < 24 * max(small, 0.002)


# The tag pre-strip and escape handling are this module's own code; 1 MB of
# each (the converted-file cap) must finish. The old regexes took over 700 s
# on 1 MB of "<sup>"; these took 8 to 16 s on the CI runner.
_ONE_MEGABYTE = 1_000_000
_ONE_MEGABYTE_SECONDS = 120


@pytest.mark.parametrize("unit", ["<sup>", "<u>", "<sub>", "<sup>*", "\\\\"])
def test_one_megabyte_of_unclosed_tags_finishes(unit):
    started = time.perf_counter()
    _clean_both_ways(_repeat_to(unit, _ONE_MEGABYTE))

    assert time.perf_counter() - started < _ONE_MEGABYTE_SECONDS


def _run_fresh_import(script: str) -> str:
    """Run ``script`` in a new interpreter, so the parser module is imported anew."""
    import subprocess
    import sys
    from pathlib import Path

    backend_dir = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=backend_dir, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def test_the_parser_rule_caches_are_built_at_import():
    # The text clean-up runs off the event loop; markdown-it builds rule caches
    # lazily, which is unsafe for concurrent first use, so import builds them.
    output = _run_fresh_import(
        "import src.lib.markdown_plain_text as m\n"
        "p = m._PARSER\n"
        "rulers = (p.core.ruler, p.block.ruler, p.inline.ruler, p.inline.ruler2)\n"
        "print(all(r.__cache__ is not None for r in rulers))\n"
    )
    assert output == "True"


def test_threads_cleaning_at_once_right_after_import_all_get_correct_text():
    """Eight threads make their first parse at the same moment, in 30 fresh processes.

    A near-zero switch interval makes the lazy-cache race show: without the
    import-time warm-up about one process in five returns wrong text, so 30
    processes fail this test reliably.
    """
    script = (
        "import sys, threading\n"
        "sys.setswitchinterval(1e-6)\n"
        "import src.lib.markdown_plain_text as m\n"
        "text = 'Signal in **B cells**, p.Tyr318*/c.954C>G and ~~old~~ [link](x)'\n"
        "barrier = threading.Barrier(8)\n"
        "results = []\n"
        "def clean():\n"
        "    barrier.wait()\n"
        "    results.append(m.markdown_inline_to_plain_text(text))\n"
        "threads = [threading.Thread(target=clean) for _ in range(8)]\n"
        "[t.start() for t in threads]\n"
        "[t.join() for t in threads]\n"
        "print(sorted(set(results)))\n"
    )
    expected = repr(["Signal in B cells, p.Tyr318*/c.954C>G and old link"])
    outputs = [_run_fresh_import(script) for _ in range(30)]
    assert outputs == [expected] * 30
