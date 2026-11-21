"""Whole-row table pieces (KANBAN-1976 / ALL-1460, P3).

All tables here are made up. They mimic the shapes found in converted
spreadsheets and Word files: a title row promoted to the Markdown header,
multi-row headers, cells ending in a full stop, very long rows.
"""

import json
from pathlib import Path

import pytest

from src.lib.markdown_plain_text import markdown_table_to_plain_text
from src.lib.openai_agents.document_result_view import table_rows_label
from src.lib.pipeline.chunk import _chunk_by_title, chunk_parsed_document
from src.lib.pipeline.table_chunking import (
    TableHeaderRule,
    count_table_header_rows,
    current_table_header_rule,
    is_table_separator_row,
    layout_table,
    normalize_table_row,
    split_table_cells,
)
from src.models.chunk import ChunkMetadata
from src.models.strategy import ChunkingMethod, ChunkingStrategy, StrategyName

FIXTURES = Path(__file__).parent / "fixtures"
RULE = TableHeaderRule(max_rows=5, min_filled_fraction=0.5)


def _strategy(max_chars: int = 1500) -> ChunkingStrategy:
    return ChunkingStrategy(
        strategy_name=StrategyName.RESEARCH,
        chunking_method=ChunkingMethod.BY_TITLE,
        max_characters=max_chars,
        overlap_characters=200,
        include_metadata=True,
        exclude_element_types=[],
    )


def _table(header_lines, data_rows, columns=4):
    separator = "| " + " | ".join(["---"] * columns) + " |"
    return "\n".join([header_lines[0], separator, *header_lines[1:], *data_rows])


def _data_rows(count, columns=4, note="Kept at 20 C. Fed daily."):
    return [
        "| " + " | ".join([f"XS{i:04d}", f"zeta{i} made-up line.", note, *["v"] * (columns - 3)]) + " |"
        for i in range(1, count + 1)
    ]


def _table_element(text, section="Sheet1"):
    return {"type": "Table", "text": text, "metadata": {"section_title": section, "page_number": 1}}


def _title(text, path=None):
    """A heading as the Markdown parser emits it: its section path ends with itself."""
    return {
        "type": "Title",
        "text": text,
        "metadata": {"section_title": text, "section_path": path or [text], "page_number": 1},
    }


def _table_lines(chunk):
    """The table rows a piece holds, located through its own metadata offsets."""
    lines = []
    for segment in chunk["table_segments"]:
        lines.extend(chunk["content"][segment["char_start"]:segment["char_end"]].split("\n"))
    return lines


# --- Structure helpers ------------------------------------------------------


def test_cells_split_on_unescaped_pipes_only():
    assert split_table_cells(r"| a \| b | c |") == [r" a \| b ", " c "]
    assert split_table_cells("| a |  | c |") == [" a ", "  ", " c "]


# An escaped backslash before a pipe (``\\|``) does not escape the pipe. The
# stored-text clean-up keeps ``\\`` as written, so every reader of table rows
# must split it the same way as the clean-up does.
ESCAPED_BACKSLASH_TITLE_ROW = r"| Made-up path C:\\| | |"


def test_an_escaped_backslash_before_a_pipe_does_not_escape_it():
    assert split_table_cells(r"| a \\| b |") == [" a \\\\", " b "]
    assert split_table_cells(r"| a | b \\|") == [" a ", " b \\\\"]
    assert split_table_cells(r"| a \\\| b |") == [" a \\\\\\| b "]


def test_the_clean_up_and_the_table_reader_agree_on_cells():
    for row in (r"| a \\| b |", r"| a \| b | c |", "| a&#124;b | c |", "| a&#92;| b |"):
        assert len(split_table_cells(markdown_table_to_plain_text(row))) == len(split_table_cells(row))


def test_header_rule_counts_cells_split_by_an_escaped_backslash():
    rows = [ESCAPED_BACKSLASH_TITLE_ROW, "| Line | Value | Notes |", "| XS0001 | 1.2 | ok |"]

    assert split_table_cells(ESCAPED_BACKSLASH_TITLE_ROW) == [r" Made-up path C:\\", " ", " "]
    assert count_table_header_rows(rows, RULE) == 2


def test_pieces_and_column_label_read_cells_split_by_an_escaped_backslash():
    names_row = "| Line | Value | Notes |"
    text = "\n".join([ESCAPED_BACKSLASH_TITLE_ROW, "| --- | --- | --- |", names_row, "| XS0001 | 1.2 | ok |"])

    (chunk,) = _chunk_by_title([_table_element(text)], _strategy())
    (segment,) = chunk["table_segments"]

    assert segment["header_row_count"] == 2
    assert segment["header_text"] == f"{ESCAPED_BACKSLASH_TITLE_ROW}\n{names_row}"
    assert table_rows_label(segment).endswith(f"{ESCAPED_BACKSLASH_TITLE_ROW}\n    {names_row}")


def test_separator_row_is_recognised_by_markdown_syntax():
    assert is_table_separator_row("| --- | :-: | ---: |")
    assert not is_table_separator_row("| - value | --- |")


def test_line_break_tags_become_spaces_and_space_runs_collapse():
    assert normalize_table_row("| a<br>b | c<BR/>d |   e    f |") == "| a b | c d | e f |"


# --- The header rule ----------------------------------------------------------


def test_simple_table_header_is_the_markdown_header_row():
    rows = ["| Strain | Gene | Value | Source |", *_data_rows(3)]
    assert count_table_header_rows(rows, RULE) == 1


def test_title_row_sheet_records_two_header_rows():
    rows = ["| Table S1. Made-up strains used here | | | |", "| Strain | Gene | Value | Source |", *_data_rows(3)]
    assert count_table_header_rows(rows, RULE) == 2


def test_three_row_header_records_three():
    rows = [
        "| Figure 1B made-up counts | | | | | |",
        "| | replicates | | | control | |",
        "| Line | Rep 1 | Rep 2 | Day 1 | Day 2 | Day 3 |",
        *_data_rows(3, columns=6),
    ]
    assert count_table_header_rows(rows, RULE) == 3


def test_header_never_takes_more_than_the_row_limit():
    sparse = "| x | | | | | |"
    rows = [sparse] * 8 + _data_rows(3, columns=6)
    assert count_table_header_rows(rows, RULE) == 5
    assert count_table_header_rows(rows, TableHeaderRule(max_rows=3, min_filled_fraction=0.5)) == 3


def test_known_weak_spot_a_sparse_column_row_pulls_the_first_data_row_into_the_header():
    """Today's behaviour, pinned: when the real column-name row is itself less
    than half filled, the rule keeps going and takes the first data row as the
    last header row. Chris sees the full-set count of these before the rule is
    fixed (plan section 5)."""

    rows = [
        "| Table S2. Made-up measurements | | | | | |",
        "| Line | | | | | Notes |",
        "| XS0001 | 1.2 | 3.4 | 5.6 | 7.8 | ok |",
        "| XS0002 | 2.2 | 4.4 | 6.6 | 8.8 | ok |",
    ]
    assert count_table_header_rows(rows, RULE) == 3
    layout = layout_table(_table([rows[0], rows[1]], rows[2:], columns=6), RULE)
    assert layout.header_row_count == 3
    assert "XS0001" in layout.header_text
    assert layout.total_data_rows == 1


def test_rule_reads_its_parameters_from_settings(monkeypatch):
    monkeypatch.setenv("DOCUMENT_TABLE_HEADER_MAX_ROWS", "2")
    monkeypatch.setenv("DOCUMENT_TABLE_HEADER_MIN_FILLED_FRACTION", "0.75")
    assert current_table_header_rule() == TableHeaderRule(max_rows=2, min_filled_fraction=0.75)
    monkeypatch.delenv("DOCUMENT_TABLE_HEADER_MAX_ROWS")
    monkeypatch.delenv("DOCUMENT_TABLE_HEADER_MIN_FILLED_FRACTION")
    assert current_table_header_rule() == TableHeaderRule(max_rows=5, min_filled_fraction=0.5)


@pytest.mark.parametrize("raw", ["0", "1.5", "-0.1", "half", "nan"])
def test_rule_refuses_an_invalid_filled_fraction(monkeypatch, raw):
    monkeypatch.setenv("DOCUMENT_TABLE_HEADER_MIN_FILLED_FRACTION", raw)
    with pytest.raises(ValueError, match="DOCUMENT_TABLE_HEADER_MIN_FILLED_FRACTION"):
        current_table_header_rule()


@pytest.mark.parametrize("raw", ["0", "-2", "2.5", "five", ""])
def test_rule_refuses_an_invalid_row_limit(monkeypatch, raw):
    monkeypatch.setenv("DOCUMENT_TABLE_HEADER_MAX_ROWS", raw)
    with pytest.raises(ValueError, match="DOCUMENT_TABLE_HEADER_MAX_ROWS"):
        current_table_header_rule()


# --- Whole-row pieces -------------------------------------------------------


def test_row_whose_cells_end_in_full_stops_stays_whole():
    text = _table(["| Strain | Description | Notes | Source |"], _data_rows(80))
    source_rows = set(text.split("\n"))
    chunks = _chunk_by_title([_table_element(text)], _strategy())

    assert len(chunks) > 1
    for chunk in chunks:
        assert set(_table_lines(chunk)) <= source_rows
        assert chunk["content"] == "\n".join(_table_lines(chunk))


def test_no_table_piece_starts_or_ends_inside_a_row():
    text = _table(["| Strain | Description | Notes | Source |"], _data_rows(120))
    chunks = _chunk_by_title([_table_element(text)], _strategy())

    for chunk in chunks:
        assert chunk["content"].startswith("| ")
        assert chunk["content"].endswith(" |")
        assert all(line in text.split("\n") for line in chunk["content"].split("\n"))


def test_row_ranges_are_unbroken_and_cover_each_row_once():
    text = _table(["| Strain | Description | Notes | Source |"], _data_rows(120))
    chunks = _chunk_by_title([_table_element(text)], _strategy())

    covered = []
    for chunk in chunks:
        (segment,) = chunk["table_segments"]
        assert segment["total_data_rows"] == 120
        covered.extend(range(segment["first_data_row"], segment["last_data_row"] + 1))
    assert covered == list(range(1, 121))
    rebuilt = "\n".join(chunk["content"] for chunk in chunks)
    assert rebuilt == text


def test_no_overlap_between_table_pieces():
    text = _table(["| Strain | Description | Notes | Source |"], _data_rows(120))
    chunks = _chunk_by_title([_table_element(text)], _strategy())

    lines = [line for chunk in chunks for line in chunk["content"].split("\n")]
    assert len(lines) == len(set(lines)) == len(text.split("\n"))


def test_row_of_nine_thousand_characters_is_one_whole_piece():
    long_row = "| XS9999 | " + ("ACGT" * 2250) + " | long | Lab A |"
    assert len(long_row) > 9000
    text = _table(["| Strain | Sequence | Notes | Source |"], [*_data_rows(3), long_row, *_data_rows(2)])
    chunks = _chunk_by_title([_table_element(text)], _strategy())

    holding = [chunk for chunk in chunks if long_row in chunk["content"]]
    assert len(holding) == 1
    assert holding[0]["content"] == long_row
    (segment,) = holding[0]["table_segments"]
    assert segment["first_data_row"] == segment["last_data_row"] == 4


def test_header_rows_appear_once_in_stored_text_and_in_every_piece_metadata():
    title_row = "| Table S1. Made-up strains used here | | | |"
    names_row = "| Strain | Description | Notes | Source |"
    text = _table([title_row, names_row], _data_rows(120))
    chunks = _chunk_by_title([_table_element(text)], _strategy())

    stored = "\n".join(chunk["content"] for chunk in chunks)
    assert stored.count(title_row) == 1
    assert stored.count(names_row) == 1
    assert chunks[0]["content"].startswith(f"{title_row}\n| --- | --- | --- | --- |\n{names_row}\n")
    for chunk in chunks:
        (segment,) = chunk["table_segments"]
        assert segment["header_row_count"] == 2
        assert segment["header_text"] == f"{title_row}\n{names_row}"
    assert chunks[0]["table_segments"][0]["first_data_row"] == 1


def test_known_weak_spot_a_header_longer_than_a_piece_is_cut_like_rows():
    """Today's behaviour, pinned: header rows are ordinary rows of the first
    pieces, so a header longer than a piece spills over several pieces, and
    every piece's metadata still carries the whole header text, which is then
    longer than the piece itself. Chris sees how often this happens before the
    rule is fixed (plan section 5)."""

    title_row = "| Table S3. Made-up wide sheet |" + " |" * 29
    names_row = "| " + " | ".join(f"made-up measurement column name number {c} (units)" for c in range(30)) + " |"
    assert len(names_row) > 1500
    text = _table([title_row, names_row], _data_rows(5, columns=30), columns=30)
    chunks = _chunk_by_title([_table_element(text)], _strategy())

    header_text = chunks[0]["table_segments"][0]["header_text"]
    assert len(header_text) > 1500
    assert chunks[0]["table_segments"][0]["first_data_row"] is None
    for chunk in chunks:
        assert chunk["table_segments"][0]["header_text"] == header_text
    stored = "\n".join(chunk["content"] for chunk in chunks)
    assert stored == text


def test_line_break_tags_inside_cells_are_flattened_in_stored_pieces():
    text = _table(["| Strain | Notes |"], ["| XS1 | first<br>second |", "| XS2 |   padded    cell |"], columns=2)
    (chunk,) = _chunk_by_title([_table_element(text)], _strategy())
    assert "| XS1 | first second |" in chunk["content"]
    assert "| XS2 | padded cell |" in chunk["content"]
    assert "<br>" not in chunk["content"]


def test_table_numbers_count_tables_within_the_file():
    first = _table(["| A | B |"], ["| 1 | 2 |"], columns=2)
    second = _table(["| C | D |"], ["| 3 | 4 |"], columns=2)
    chunks = _chunk_by_title(
        [_title("Sheet1"), _table_element(first), _title("Sheet2"), _table_element(second, "Sheet2")],
        _strategy(),
    )
    assert [chunk["table_segments"][0]["table_index"] for chunk in chunks] == [1, 2]


def test_small_table_offsets_locate_its_rows_inside_a_mixed_piece():
    table = _table(["| Strain | Genotype |"], ["| XS1 | zeta1 mutant. |"], columns=2)
    elements = [
        {"type": "NarrativeText", "text": "Some made-up prose before.", "metadata": {}},
        _table_element(table),
        {"type": "NarrativeText", "text": "Some made-up prose after.", "metadata": {}},
    ]
    (chunk,) = _chunk_by_title(elements, _strategy())
    (segment,) = chunk["table_segments"]
    assert chunk["content"] == f"Some made-up prose before.\n\n{table}\n\nSome made-up prose after."
    assert chunk["content"][segment["char_start"]:segment["char_end"]] == table
    assert (segment["first_data_row"], segment["last_data_row"], segment["total_data_rows"]) == (1, 1, 1)


def test_only_the_markdown_separator_line_is_recorded_as_the_separator():
    """An all-dash data row (a common "none" value) is a data row like any other."""
    text = _table(["| Score | Control |"], ["| 3 | 1 |", "| - | - |", "| 5 | -- |"], columns=2)
    (chunk,) = _chunk_by_title([_table_element(text)], _strategy())
    (segment,) = chunk["table_segments"]
    start = segment["separator_char_start"]
    assert chunk["content"][start:].split("\n", 1)[0] == "| --- | --- |"
    assert segment["total_data_rows"] == 3


def test_later_pieces_hold_no_separator():
    text = _table(["| Strain | Description | Notes | Source |"], _data_rows(120))
    chunks = _chunk_by_title([_table_element(text)], _strategy())
    assert chunks[0]["table_segments"][0]["separator_char_start"] == len("| Strain | Description | Notes | Source |") + 1
    assert all(chunk["table_segments"][0]["separator_char_start"] is None for chunk in chunks[1:])


# --- Headings -----------------------------------------------------------------


def test_no_heading_only_pieces():
    text = _table(["| Strain | Description | Notes | Source |"], _data_rows(60))
    elements = [
        _title("Made-up workbook"),
        _title("Sheet1", ["Made-up workbook", "Sheet1"]),
        _table_element(text),
        _title("Results"),
        {"type": "NarrativeText", "text": "x " * 760, "metadata": {"section_title": "Results"}},
    ]
    chunks = _chunk_by_title(elements, _strategy())

    for chunk in chunks:
        assert not all(element["type"] == "Title" for element in chunk["elements"])
    assert chunks[0]["content"].startswith("Made-up workbook\n\nSheet1\n\n| Strain |")
    assert chunks[0]["metadata"]["section_title"] == "Sheet1"
    segment = chunks[0]["table_segments"][0]
    assert chunks[0]["content"][segment["char_start"]:].startswith("| Strain |")
    assert any(chunk["content"].startswith("Results\n\nx x") for chunk in chunks)


def test_workbook_title_and_sheet_heading_join_the_sheet_first_piece():
    table = _table(["| Strain | Genotype |"], ["| XS1 | zeta1. |"], columns=2)
    elements = [_title("Made-up workbook"), _title("Sheet1", ["Made-up workbook", "Sheet1"]), _table_element(table)]
    (chunk,) = _chunk_by_title(elements, _strategy())
    assert chunk["content"] == f"Made-up workbook\n\nSheet1\n\n{table}"
    assert chunk["metadata"]["section_title"] == "Sheet1"
    assert chunk["metadata"]["section_path"] == ["Made-up workbook", "Sheet1"]


def test_sibling_headings_keep_their_own_pieces_and_section_metadata():
    """A heading followed by a sibling (an empty section) is not merged away."""
    elements = [_title(f"Heading {i}") for i in range(300)]
    elements.append({"type": "NarrativeText", "text": "Made-up text.", "metadata": {"section_title": "Heading 299"}})
    chunks = _chunk_by_title(elements, _strategy())
    assert [chunk["metadata"]["section_title"] for chunk in chunks] == [f"Heading {i}" for i in range(300)]
    assert chunks[-1]["content"] == "Heading 299\n\nMade-up text."


def test_headings_in_different_parent_sections_are_not_merged():
    first = _title("Made-up workbook")
    first["metadata"]["parent_section"] = "Made-up workbook"
    second = _title("Sheet1", ["Made-up workbook", "Sheet1"])
    second["metadata"]["parent_section"] = "Sheet1"
    body = {"type": "NarrativeText", "text": "Made-up text.", "metadata": {"section_title": "Sheet1"}}
    chunks = _chunk_by_title([first, second, body], _strategy())
    assert [chunk["content"] for chunk in chunks] == ["Made-up workbook", "Sheet1\n\nMade-up text."]
    assert chunks[0]["metadata"]["parent_section"] == "Made-up workbook"


def test_a_stack_of_nested_headings_stops_at_the_piece_size():
    elements, path = [], []
    for depth in range(60):
        name = f"Made-up nested heading number {depth:02d} of a long outline"
        path = [*path, name]
        elements.append(_title(name, list(path)))
    elements.append({"type": "NarrativeText", "text": "Made-up text.", "metadata": {}})
    chunks = _chunk_by_title(elements, _strategy())

    assert len(chunks) > 1
    assert all(len(chunk["content"]) <= 1500 for chunk in chunks[:-1])
    stored = "\n\n".join(chunk["content"] for chunk in chunks)
    assert all(element["text"] in stored for element in elements)
    for chunk in chunks:
        # The innermost heading's path still names every heading stacked above it.
        stacked = [element["text"] for element in chunk["elements"] if element["type"] == "Title"]
        assert chunk["metadata"]["section_path"][-len(stacked):] == stacked


# --- Stored metadata ----------------------------------------------------------


@pytest.mark.asyncio
async def test_table_segments_reach_the_stored_chunk_metadata():
    text = _table(["| Table S1. Made-up | | | |", "| Strain | Description | Notes | Source |"], _data_rows(40))
    chunks = await chunk_parsed_document([_table_element(text)], _strategy(), "doc-tables")

    assert len(chunks) > 1
    for chunk in chunks:
        assert isinstance(chunk.metadata, ChunkMetadata)
        dumped = chunk.metadata.model_dump()
        (segment,) = dumped["table_segments"]
        assert segment["table_index"] == 1
        assert segment["header_row_count"] == 2
        assert segment["total_data_rows"] == 40
        assert chunk.content[segment["char_start"]:segment["char_end"]] == chunk.content
    assert json.loads(json.dumps(chunks[0].metadata.model_dump(), default=str))["table_segments"]


@pytest.mark.asyncio
async def test_prose_chunks_carry_no_table_segments():
    chunks = await chunk_parsed_document(
        [{"type": "NarrativeText", "text": "Made-up prose only.", "metadata": {}}], _strategy(), "doc-prose"
    )
    assert chunks[0].metadata.table_segments == []


# --- Main papers ----------------------------------------------------------------


def test_main_paper_table_that_fits_one_piece_is_unchanged():
    fixture = json.loads((FIXTURES / "main_paper_table_chunking.json").read_text())
    small_table_piece = next(piece for piece in fixture["pieces_before"] if piece.startswith("| Strain | Genotype |"))
    after = [chunk["content"] for chunk in _chunk_by_title(fixture["elements"], _strategy())]
    assert small_table_piece in after


def test_main_paper_fixture_pins_exactly_which_pieces_change():
    """Decision 8: main papers change too. This synthetic paper pins which
    pieces differ from the chunking on main before this change: the heading-only
    pieces are gone (their headings carried into the next piece) and the long
    table is cut between rows instead of inside them."""

    fixture = json.loads((FIXTURES / "main_paper_table_chunking.json").read_text())
    after = [chunk["content"] for chunk in _chunk_by_title(fixture["elements"], _strategy())]

    assert after == fixture["pieces_after"]
    before = fixture["pieces_before"]
    unchanged = [piece for piece in after if piece in before]
    assert len(unchanged) == 2
    assert unchanged[0].startswith("| Strain | Genotype |")
    assert unchanged[1].startswith("Discussion\n\n")
    heading_only = ["A synthetic study of made-up genes", "Results", "Expression of zeta1", "Strains"]
    assert all(piece in before for piece in heading_only)
    assert not any(piece in heading_only for piece in after)
    assert len(before) == 12 and len(after) == 8
