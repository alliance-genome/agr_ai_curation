"""One quotable line per table row; the header shown as a label (KANBAN-1976 / ALL-1460, P4).

Table pieces come from the real chunker (P3), so the spanizer reads the same
structural metadata a stored piece carries. All tables are made up.
"""

import json
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

import pytest

import agr_ai_curation_alliance.tools.weaviate_search as weaviate_search  # pyright: ignore[reportMissingImports]
from tests.readable_files_support import single_file
import src.lib.openai_agents.tools.record_evidence as record_evidence
from src.lib.openai_agents.document_result_view import (
    TABLE_LABEL_PREFIX,
    document_result_view,
    ordinal,
    table_rows_label,
)
from src.lib.openai_agents.evidence_spans import (
    EVIDENCE_SPANIZER_VERSION,
    EvidenceSpanResolutionError,
    build_evidence_spans,
    chunk_table_segments,
    resolve_evidence_span_id,
)
from src.lib.packages.package_runner_entrypoint import _build_tool_context
from src.lib.pipeline.chunk import chunk_parsed_document
from src.models.strategy import ChunkingMethod, ChunkingStrategy, StrategyName

FIXTURES = Path(__file__).parents[2] / "openai_agents" / "fixtures"
TITLE_ROW = "| Table S1. Made-up strains | | | |"
NAMES_ROW = "| Strain | Genotype | Temp | Source |"


def _strategy() -> ChunkingStrategy:
    return ChunkingStrategy(
        strategy_name=StrategyName.RESEARCH,
        chunking_method=ChunkingMethod.BY_TITLE,
        max_characters=1500,
        overlap_characters=200,
        include_metadata=True,
        exclude_element_types=[],
    )


def _rows(count: int) -> list[str]:
    return [f"| XS{i:04d} | zeta{i:03d}. | 25 C | Lab {chr(65 + i % 5)} |" for i in range(1, count + 1)]


def _table_text(count: int) -> str:
    return "\n".join([TITLE_ROW, "| --- | --- | --- | --- |", NAMES_ROW, *_rows(count)])


async def _table_pieces(count: int = 200):
    elements = [
        {"type": "Title", "text": "Sheet1", "metadata": {"section_title": "Sheet1"}},
        {"type": "Table", "text": _table_text(count), "metadata": {"section_title": "Sheet1"}},
    ]
    chunks = await chunk_parsed_document(elements, _strategy(), "doc-table")
    return [
        {
            "id": str(uuid5(NAMESPACE_URL, f"table-piece-{chunk.chunk_index}")),
            "chunk_index": chunk.chunk_index,
            "text": chunk.content,
            "page_number": None,
            "section_title": "Sheet1",
            "metadata": json.loads(json.dumps(chunk.metadata.model_dump(), default=str)),
        }
        for chunk in chunks
    ]


def _spans(piece: dict):
    return build_evidence_spans(
        chunk_id=piece["id"],
        chunk_text=piece["text"],
        section_title="Sheet1",
        table_segments=chunk_table_segments(piece["metadata"]),
    )


# --- Spanizer ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_forty_row_table_piece_gives_forty_row_spans():
    pieces = await _table_pieces()
    later = pieces[1]
    (segment,) = later["metadata"]["table_segments"]
    rows_held = segment["last_data_row"] - segment["first_data_row"] + 1
    assert rows_held == 40

    spans = _spans(later)

    assert len(spans) == 40
    assert {span.span_type for span in spans} == {"table_row"}
    assert [span.text for span in spans] == later["text"].split("\n")
    assert [span.span_index for span in spans] == list(range(40))


@pytest.mark.asyncio
async def test_first_piece_keeps_its_heading_as_a_sentence_and_its_header_rows_as_rows():
    first = (await _table_pieces())[0]
    spans = _spans(first)

    assert spans[0].text == "Sheet1" and spans[0].span_type == "sentence"
    assert [span.text for span in spans[1:3]] == [TITLE_ROW, NAMES_ROW]
    assert all(span.span_type == "table_row" for span in spans[1:])
    assert not any(set(span.text) <= set("|-: ") for span in spans), "the separator line is no span"


@pytest.mark.asyncio
async def test_every_row_span_resolves_as_a_table_row_and_stays_a_sentence_without_metadata():
    piece = (await _table_pieces())[2]
    segments = chunk_table_segments(piece["metadata"])
    for span in _spans(piece):
        resolved = resolve_evidence_span_id(
            span_id=span.span_id, chunk_text=piece["text"], expected_chunk_id=piece["id"], table_segments=segments
        )
        assert resolved.text == span.text
        assert resolved.span_type == "table_row"
        # Resolution reads the same structural flag; without it nothing claims a row.
        assert resolve_evidence_span_id(span_id=span.span_id, chunk_text=piece["text"]).span_type == "sentence"


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["middle", "last"])
async def test_an_all_dash_data_row_is_a_row_the_ai_sees_and_can_quote(where):
    """Only the separator line chunking recorded is skipped; "| - | - |" is data."""
    rows = ["| 3 | 1 |", "| 5 | -- |"]
    rows.insert(1 if where == "middle" else 2, "| - | - |")
    table = "\n".join(["| Score | Control |", "| --- | --- |", *rows])
    (chunk,) = await chunk_parsed_document(
        [{"type": "Table", "text": table, "metadata": {"section_title": "Sheet1"}}], _strategy(), "doc-dash"
    )
    piece = {
        "id": "dash-piece", "chunk_index": 0, "text": chunk.content,
        "metadata": json.loads(json.dumps(chunk.metadata.model_dump(), default=str)),
    }
    spans = _spans(piece)
    assert [span.text for span in spans] == ["| Score | Control |", *rows]
    assert "| --- | --- |" not in [span.text for span in spans]

    view, _events = document_result_view("read_chunk", weaviate_search.ChunkReadResult(
        summary="Read chunk 0.",
        chunk=weaviate_search.ChunkReadContent(
            chunk_id=piece["id"], chunk_index=0, content=piece["text"],
            evidence_spans=[weaviate_search.EvidenceSpanResult(**span.to_dict()) for span in spans],
            tables=weaviate_search._table_rows(chunk_table_segments(piece["metadata"])),
        ),
    ))
    lines = view["chunk"]["text"].split("\n")
    assert "this passage holds rows 1 to 3 of 3" in lines[0]
    assert sum(line.startswith("[") for line in lines) - 1 == 3, "every data row the label counts is shown"
    assert any(line.endswith("] | - | - |") for line in lines)


def test_hash_salt_is_unchanged():
    assert EVIDENCE_SPANIZER_VERSION == "pdf_sentence_v1"


def test_prose_spans_are_byte_identical_to_the_spanizer_before_table_rows():
    """Spans of pieces without table metadata, including table text stored
    before whole-row pieces existed, match the spanizer on main before this
    change exactly, and every span ID minted then still resolves."""

    fixture = json.loads((FIXTURES / "spans_before_table_rows.json").read_text())
    assert len(fixture) == 16
    for piece in fixture:
        current = build_evidence_spans(
            chunk_id=piece["chunk_id"], chunk_text=piece["text"], page_number=4, section_title="Results"
        )
        assert [span.to_dict() for span in current] == piece["spans"]
        for stored in piece["spans"]:
            resolved = resolve_evidence_span_id(
                span_id=stored["span_id"], chunk_text=piece["text"], expected_chunk_id=piece["chunk_id"],
                page_number=4, section_title="Results",
            )
            assert resolved.to_dict() == stored


def test_a_table_segment_outside_the_text_is_refused():
    with pytest.raises(EvidenceSpanResolutionError, match="table segment"):
        build_evidence_spans(chunk_id="c", chunk_text="| a |", table_segments=[{"char_start": 0, "char_end": 9}])


def test_metadata_without_table_segments_gives_none():
    assert chunk_table_segments(None) == []
    assert chunk_table_segments("") == []
    assert chunk_table_segments('{"doc_items": []}') == []


# --- Model view: the label -----------------------------------------------------


def test_label_names_the_table_rows_and_columns():
    assert table_rows_label({
        "table_index": 2, "first_data_row": 401, "last_data_row": 520, "total_data_rows": 2033,
        "header_text": f"{TITLE_ROW}\n{NAMES_ROW}",
    }) == (
        f"Column names of the 2nd table in this file's text (this passage holds rows 401 to 520 of 2,033): "
        f"{TITLE_ROW}\n    {NAMES_ROW}"
    )
    assert table_rows_label({
        "table_index": 1, "first_data_row": None, "last_data_row": None, "total_data_rows": 7,
        "header_text": NAMES_ROW,
    }) == (
        "Column names of the 1st table in this file's text "
        f"(this passage holds only its header rows; the table has 7 rows): {NAMES_ROW}"
    )
    assert "(this passage holds row 3 of 9)" in table_rows_label({
        "table_index": 1, "first_data_row": 3, "last_data_row": 3, "total_data_rows": 9, "header_text": NAMES_ROW,
    })


@pytest.mark.parametrize("number,expected", [
    (1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (10, "10th"), (11, "11th"), (12, "12th"), (13, "13th"),
    (14, "14th"), (21, "21st"), (22, "22nd"), (23, "23rd"), (24, "24th"), (100, "100th"), (101, "101st"),
    (102, "102nd"), (103, "103rd"), (111, "111th"), (112, "112th"), (113, "113th"), (121, "121st"),
    (1001, "1,001st"), (1011, "1,011th"),
])
def test_ordinals_are_correct_for_every_number(number, expected):
    assert ordinal(number) == expected


@pytest.mark.parametrize("bad", [0, -1, 1.0, True])
def test_ordinal_refuses_anything_but_a_positive_whole_number(bad):
    with pytest.raises(ValueError):
        ordinal(bad)


@pytest.fixture
def section_tool(monkeypatch):
    from unittest.mock import AsyncMock
    from agr_ai_curation_runtime.function_tools import function_tool

    monkeypatch.setattr(weaviate_search, "function_tool", function_tool)
    monkeypatch.setattr(weaviate_search, "scoped_document_tool", lambda tool, **kwargs: tool)
    monkeypatch.setattr(weaviate_search, "current_readable_files", lambda: single_file("doc-12345678"))
    retain = AsyncMock()
    monkeypatch.setattr(weaviate_search, "retain_issued_spans", retain)
    monkeypatch.setattr(weaviate_search, "record_files_shown", AsyncMock())
    return retain


async def _invoke(tool, payload):
    return await tool.on_invoke_tool(_build_tool_context(tool.name, payload), json.dumps(payload))


@pytest.mark.asyncio
async def test_read_section_shows_the_label_above_rows_without_a_number(monkeypatch, section_tool):
    pieces = await _table_pieces()

    async def _section(**_kwargs):
        return [{**piece, "parent_section": "Sheet1"} for piece in pieces]

    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _section)
    tool = weaviate_search.create_read_section_tool("doc-table", "user-1")

    canonical = await _invoke(tool, {"section_name": "Sheet1"})
    view, _events = document_result_view("read_section", canonical)

    for passage, canonical_passage in zip(view["section"]["passages"], canonical.section.passages):
        lines = passage["text"].split("\n")
        labels = [line for line in lines if line.startswith(TABLE_LABEL_PREFIX)]
        assert len(labels) == 1
        assert labels[0].endswith(f": {TITLE_ROW}")
        assert f"    {NAMES_ROW}" in lines
        label_at = lines.index(labels[0])
        assert lines[label_at + 2].startswith("[")
        numbered = [line for line in lines if line.startswith("[")]
        assert numbered == [f"[{span.span_index}] {span.text}" for span in canonical_passage.evidence_spans]
        assert "tables" not in passage
    second = view["section"]["passages"][1]["text"]
    segment = pieces[1]["metadata"]["table_segments"][0]
    assert (
        f"Column names of the 1st table in this file's text (this passage holds rows {segment['first_data_row']} to "
        f"{segment['last_data_row']} of 200): {TITLE_ROW}"
    ) in second
    # Every span shown was issued; the label never was.
    (reads,), _ = section_tool.await_args
    issued_texts = [span["text"] for _chunk, spans, _issued in reads for span in spans]
    assert not any(text.startswith(TABLE_LABEL_PREFIX) for text in issued_texts)


@pytest.mark.asyncio
async def test_read_chunk_window_starting_mid_table_still_shows_the_label(monkeypatch, section_tool):
    piece = (await _table_pieces())[1]

    async def _get(**_kwargs):
        return piece

    async def _neighbors(**_kwargs):
        return {"previous_chunk_id": None, "next_chunk_id": None}

    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", _get)
    monkeypatch.setattr(weaviate_search, "get_chunk_neighbor_ids", _neighbors)
    tool = weaviate_search.create_read_chunk_tool("doc-table", "user-1")

    canonical = await _invoke(tool, {"chunk_index": piece["chunk_index"], "span_offset": 10})
    view, _events = document_result_view("read_chunk", canonical)

    lines = view["chunk"]["text"].split("\n")
    assert lines[0].startswith(TABLE_LABEL_PREFIX)
    assert lines[1].startswith("    ")
    assert lines[2].startswith("[10] ")
    assert {span.span_type for span in canonical.chunk.evidence_spans} == {"table_row"}


@pytest.mark.asyncio
async def test_search_hit_lists_table_labels_beside_its_unchanged_content(monkeypatch, section_tool):
    piece = (await _table_pieces())[1]

    async def _search(**_kwargs):
        return [{**piece, "score": 0.9}]

    monkeypatch.setattr(weaviate_search, "hybrid_search_chunks", _search)
    tool = weaviate_search.create_search_tool("doc-table", "user-1")

    canonical = await _invoke(tool, {"query": "XS0050"})
    view, _events = document_result_view("search_document", canonical)

    (hit,) = view["hits"]
    assert hit["content"] == piece["text"]
    assert [label.split(" (")[0] for label in hit["table_labels"]] == ["Column names of the 1st table in this file's text"]
    assert "tables" not in hit


@pytest.mark.asyncio
async def test_prose_passage_view_is_unchanged(monkeypatch, section_tool):
    async def _section(**_kwargs):
        return [{"id": "prose-1", "chunk_index": 0, "text": "One sentence. Two sentence.", "parent_section": "Results",
                 "metadata": {}}]

    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _section)
    tool = weaviate_search.create_read_section_tool("doc-prose", "user-1")
    view, _events = document_result_view("read_section", await _invoke(tool, {"section_name": "Results"}))
    assert view["section"]["passages"][0]["text"] == "[0] One sentence.\n[1] Two sentence."


# --- record_evidence -----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_row_quoted_from_a_later_piece_is_an_exact_source_row_without_header_text(monkeypatch):
    monkeypatch.setattr(record_evidence, "function_tool", lambda fn: fn)
    pieces = await _table_pieces()
    later = pieces[2]
    span = _spans(later)[5]

    async def _get_chunk_by_id(**kwargs):
        assert kwargs["chunk_id"] == later["id"]
        return {**later, "parent_section": "Sheet1", "subsection": None, "doc_items": []}

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _get_chunk_by_id)
    monkeypatch.setattr(record_evidence, "current_readable_files", lambda: single_file("doc-table"))
    tool = record_evidence._create_canonical_evidence_recorder("doc-table", "user-1", tracker=cast(Any, None))

    result = await tool(entity="zeta", span_ids=[span.span_id])

    assert result["status"] == "verified"
    quote = result["verified_quote"]
    assert quote in _table_text(200).split("\n")
    assert TITLE_ROW not in quote and NAMES_ROW not in quote and "Strain" not in quote
    assert result["source_fragments"][0]["span_type"] == "table_row"
    assert "page" not in result
