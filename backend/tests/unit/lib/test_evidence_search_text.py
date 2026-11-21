"""Read-time PDF search text for table-row evidence fragments (P4 follow-up, PR #1096).

The search text comes from the one shared table-row splitter and is added only when
evidence is served; the stored record and its quote never change. Made-up rows.
"""

import json
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.lib.evidence_search_text import with_fragment_search_texts
from src.lib.pipeline.table_chunking import table_row_search_text


@pytest.mark.parametrize(
    "row,expected",
    [
        ("| XS1 | 3 | 5 | 4.0 |", "XS1 3 5 4.0"),
        # An escaped backslash before a pipe does not escape the pipe: two cells.
        ("|C:\\\\|3|", "C:\\ 3"),
        ("| x | y\\\\|", "x y\\"),
        # An escaped pipe stays inside its cell, as a plain pipe.
        ("| a \\| b |  | c |", "a | b c"),
        # Empty cells are left out.
        ("| XS2 |  |  | 0.5 |", "XS2 0.5"),
        ("| only |", "only"),
    ],
    ids=["ordinary", "even-backslashes", "trailing-even-backslashes", "escaped-pipe", "empty-cells", "one-cell"],
)
def test_table_row_search_text_uses_the_shared_splitter(row, expected):
    assert table_row_search_text(row) == expected


def _record(fragments):
    return {
        "entity": "zeta",
        "verified_quote": "\n\n".join(fragment["text"] for fragment in fragments),
        "page": 3,
        "section": "Results",
        "chunk_id": "chunk-3",
        "source_fragments": fragments,
    }


def test_only_table_row_fragments_get_search_text_and_nothing_is_mutated():
    stored = {
        "type": "evidence_summary",
        "evidence_records": [_record([
            {"text": "Flies were w[1118] | y[1] at 25 C.", "span_type": "sentence"},
            {"text": "| XS1 | 3 | 5 | 4.0 |", "span_type": "table_row"},
        ])],
    }
    before = deepcopy(stored)

    served = with_fragment_search_texts(stored)

    fragments = served["evidence_records"][0]["source_fragments"]
    assert "search_text" not in fragments[0]  # prose, even with a pipe, is never altered
    assert fragments[1]["search_text"] == "XS1 3 5 4.0"
    assert served["evidence_records"][0]["verified_quote"] == before["evidence_records"][0]["verified_quote"]
    assert fragments[1]["text"] == "| XS1 | 3 | 5 | 4.0 |"
    assert stored == before


def test_a_table_row_fragment_without_text_gets_no_search_text():
    served = with_fragment_search_texts({"source_fragments": [{"text": "", "span_type": "table_row"}, {"span_type": "table_row"}]})
    assert all("search_text" not in fragment for fragment in served["source_fragments"])


def test_nested_flow_payloads_are_served_with_the_field():
    payload = {"details": {"flow_step_evidence": {"evidence_records": [_record([
        {"text": "| XS9 | 1 |", "span_type": "table_row"},
    ])]}}}
    served = with_fragment_search_texts(payload)
    fragment = served["details"]["flow_step_evidence"]["evidence_records"][0]["source_fragments"][0]
    assert fragment["search_text"] == "XS9 1"


def test_sse_events_carry_the_field():
    from src.api import chat_common

    line = chat_common._stream_event_sse({"type": "evidence_summary", "evidence_records": [_record([
        {"text": "| XS1 | 3 |", "span_type": "table_row"},
    ])]})

    payload = json.loads(line.removeprefix("data: ").strip())
    assert payload["evidence_records"][0]["source_fragments"][0]["search_text"] == "XS1 3"


def test_chat_history_messages_carry_the_field_and_the_stored_row_is_unchanged():
    from src.api import chat_common
    from src.lib.chat_history_repository import ASSISTANT_CHAT_KIND, ChatMessageRecord

    stored_payload = {"evidence_records": [_record([{"text": "| XS1 | 3 |", "span_type": "table_row"}])]}
    record = ChatMessageRecord(
        message_id=uuid4(),
        session_id="session-1",
        chat_kind=ASSISTANT_CHAT_KIND,
        turn_id="turn-1",
        role="assistant",
        message_type="text",
        content="Answer.",
        payload_json=stored_payload,
        trace_id=None,
        created_at=datetime(2026, 11, 21, tzinfo=timezone.utc),
    )

    served = chat_common._serialize_message(record)

    assert served.payload_json["evidence_records"][0]["source_fragments"][0]["search_text"] == "XS1 3"
    assert "search_text" not in stored_payload["evidence_records"][0]["source_fragments"][0]


def test_review_grid_projections_carry_the_field_on_their_source_record():
    from src.lib.domain_packs.materialization import project_evidence_anchor_projections
    from src.schemas.domain_envelope import DomainEnvelope

    source = {
        "evidence_record_id": "E1",
        "verified_quote": "| XS1 | 3 | 5 | 4.0 |",
        "page": 3,
        "document_id": "D1",
        "source_span_ids": ["s1"],
        "source_fragments": [{"text": "| XS1 | 3 | 5 | 4.0 |", "span_type": "table_row"}],
    }
    envelope = DomainEnvelope(
        envelope_id="envelope-1",
        domain_pack_id="fixture",
        metadata={"evidence_records": [source]},
        extracted_objects=[{"object_type": "Assertion", "object_id": "O1", "evidence_record_ids": ["E1"]}],
    )

    projections = project_evidence_anchor_projections(envelope, envelope_revision=1, document_id="D1")

    assert projections
    fragment = projections[0].metadata["source_record"]["source_fragments"][0]
    assert fragment["search_text"] == "XS1 3 5 4.0"
    assert projections[0].quote == "| XS1 | 3 | 5 | 4.0 |"
    assert "search_text" not in envelope.metadata["evidence_records"][0]["source_fragments"][0]
