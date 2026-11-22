"""Display blocks for a text file, built from its processed elements (ALL-1460, P9a).

A quote is an exact slice of a stored piece. The blocks must hold the text exactly as
it was cut into pieces, so the text view can find a quote by exact substring (prose)
or exact row-line equality (table rows). The fixture is cut by the real chunker and
compared. Made-up content only.
"""

import asyncio

import pytest

from src.lib.pdf_viewer.display_blocks import (
    UnsupportedDisplayElementError,
    build_display_blocks,
)
from src.lib.pipeline.chunk import chunk_parsed_document
from src.lib.pipeline.pdfx_parser import markdown_to_pipeline_elements
from src.lib.pipeline.table_chunking import table_row_display_cells, table_row_search_text
from src.models.strategy import ChunkingStrategy


_MARKDOWN = """# Supplementary Table S1

Strains used in this made-up study. They were kept at **two** temperatures.

## Sheet 1

| Strain | Genotype | Temp |
|---|---|---|
| XS0401 | zeta401 \\| b | 25 C |
| XS0402 |  | 20 C |

- first list item
- second item

```
x = 1
```

## Sheet 2

| Probe | Value |
|---|---|
| P1 | 0.5 |
"""


def _elements():
    return markdown_to_pipeline_elements(_MARKDOWN)


def _footer_element(index: int) -> dict:
    return {
        "index": index,
        "type": "NarrativeText",
        "text": "Page 3 of 9",
        "metadata": {"doc_item_label": "page_footer", "section_path": []},
    }


def test_blocks_keep_kinds_levels_and_section_paths_in_reading_order():
    blocks = build_display_blocks(_elements())

    assert [block.kind for block in blocks] == [
        "heading",
        "paragraph",
        "heading",
        "table",
        "list_item",
        "list_item",
        "code",
        "heading",
        "table",
    ]
    headings = [block for block in blocks if block.kind == "heading"]
    assert [(block.text, block.level) for block in headings] == [
        ("Supplementary Table S1", 1),
        ("Sheet 1", 2),
        ("Sheet 2", 2),
    ]
    assert blocks[3].section_path == ["Supplementary Table S1", "Sheet 1"]
    assert all(block.level is None for block in blocks if block.kind != "heading")
    assert all(block.rows is None for block in blocks if block.kind != "table")
    assert all(block.text is None for block in blocks if block.kind == "table")


def test_table_rows_hold_the_exact_stored_line_cells_and_header_flag():
    blocks = build_display_blocks(_elements())
    table = blocks[3]

    assert [row.line for row in table.rows] == [
        "| Strain | Genotype | Temp |",
        "| XS0401 | zeta401 \\| b | 25 C |",
        "| XS0402 | | 20 C |",
    ]
    # The separator line is not a row; the Markdown header row is a header.
    assert [row.is_header for row in table.rows] == [True, False, False]
    # Escapes are undone per cell; empty cells stay so columns line up.
    assert table.rows[1].cells == ["XS0401", "zeta401 | b", "25 C"]
    assert table.rows[2].cells == ["XS0402", "", "20 C"]


def test_cell_unescape_is_the_one_used_for_pdf_search_text():
    row = "| a \\| b |  | C:\\\\|"

    assert table_row_display_cells(row) == ["a | b", "", "C:\\"]
    assert table_row_search_text(row) == " ".join(
        cell for cell in table_row_display_cells(row) if cell
    )


def test_page_footers_are_left_out_as_the_chunker_does():
    elements = _elements()
    elements.insert(2, _footer_element(len(elements)))

    blocks = build_display_blocks(elements)

    assert all(block.text != "Page 3 of 9" for block in blocks)
    assert len(blocks) == 9


def test_an_unknown_element_type_is_a_named_error():
    elements = _elements()
    elements.append({"index": 99, "type": "Image", "text": "fig", "metadata": {}})

    with pytest.raises(UnsupportedDisplayElementError):
        build_display_blocks(elements)


def test_every_stored_piece_is_made_of_block_texts_and_row_lines_exactly():
    elements = _elements()
    elements.insert(2, _footer_element(len(elements)))
    chunks = asyncio.run(
        chunk_parsed_document(
            elements, ChunkingStrategy.get_research_strategy(), "made-up-document"
        )
    )
    blocks = build_display_blocks(elements)
    prose = {block.text for block in blocks if block.kind != "table"}
    row_lines = {row.line for block in blocks if block.kind == "table" for row in block.rows}

    assert chunks
    for chunk in chunks:
        for part in chunk.content.split("\n\n"):
            if part in prose:
                continue
            lines = part.split("\n")
            # A table part: every line is a row line, or the table's separator line.
            assert all(line in row_lines or set(line) <= set("|-: ") for line in lines), part
    # And every block appears in the pieces.
    contents = "\n\n".join(chunk.content for chunk in chunks)
    assert all(text in contents for text in prose)
    assert all(line in contents.split("\n") for line in row_lines)
