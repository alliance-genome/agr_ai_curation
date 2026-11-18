"""Keep TraceReview's decoder tied to the application's real numbered view.

TraceReview reads a section read from Langfuse as the model received it: the
SDK's repr of the model view, whose passages show numbered lines. Its decoder
(trace_review/backend/src/analyzers/document_read_results.py) must reverse the
renderer in document_result_view exactly. This test renders a golden fixture
with the real span builder and the real view, and fails when the fixture no
longer matches. TraceReview's test_document_read_results.py decodes the same
fixture and expects the original sentences back.

To regenerate after an intended renderer change, run this module as a script in
the backend test environment, for example in the backend-unit-tests container
(where trace_review is mounted read-only, so write elsewhere and copy it over):
    PYTHONPATH=/app/backend:/app/backend/src:/app/packages/alliance/python/src \\
        python tests/unit/lib/packages/alliance/test_numbered_view_fixture.py OUT.json
"""

from __future__ import annotations

import json
from pathlib import Path

import agr_ai_curation_alliance.tools.weaviate_search as weaviate_search  # pyright: ignore[reportMissingImports]
from src.lib.openai_agents.document_result_view import document_result_view

FIXTURE = (
    Path(__file__).resolve().parents[6]
    / "trace_review" / "backend" / "tests" / "fixtures" / "numbered_document_view.json"
)

# Stored passage text, as the reading tools receive it from the store.
PASSAGES = [
    # A sentence whose own later lines start with "[n]" (a primer list).
    (4, "Flies were raised at 25 C.\nPrimers\n[1] GAL4-F\n[2] GAL4-R"),
    # A sentence that itself starts with a bracketed number (a reference).
    (5, "[2] Brown A. Wingless signalling in the wing disc."),
    # Windows line endings inside and between sentences.
    (6, "Larvae were staged at 96 h.\r\nDiscs were dissected in PBS\r\nand fixed."),
    # A passage with no sentences at all: it is shown as empty text.
    (7, "   "),
]


def render_fixture() -> dict:
    passages = []
    for chunk_index, text in PASSAGES:
        chunk_id = f"00000000-0000-0000-0000-{chunk_index:012d}"
        passages.append(weaviate_search.NumberedPassage(
            chunk_id=chunk_id, chunk_index=chunk_index, page_number=3, section="Methods",
            content=text, evidence_spans=weaviate_search._evidence_spans(chunk_id, text, 3, "Methods"),
        ))
    result = weaviate_search.SectionReadResult(
        summary="Read 4 passages from Methods.",
        section=weaviate_search.NumberedSection(
            requested_section="Methods", matched_sections=["Methods"], page_numbers=[3],
            total_chunk_count=len(passages), passages=passages,
        ),
    )
    view, _events = document_result_view("read_section", result)
    return {
        "about": "Written by backend/tests/unit/lib/packages/alliance/test_numbered_view_fixture.py; do not edit by hand.",
        "sentences": [[span.text for span in passage.evidence_spans] for passage in passages],
        # What Langfuse records: the Agents SDK sends a returned dictionary as its repr.
        "model_output": str(view),
    }


def test_the_trace_review_round_trip_fixture_matches_the_current_renderer():
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == render_fixture(), (
        "The numbered view changed. Regenerate the fixture (see this module's docstring) "
        "and check TraceReview's decoder still reverses it."
    )


def test_the_fixture_covers_the_hard_cases():
    fixture = render_fixture()
    sentences = fixture["sentences"]
    assert any("\n[1] GAL4-F" in sentence for sentence in sentences[0])
    assert sentences[1][0].startswith("[2] ")
    assert any("\r\n" in sentence for sentence in sentences[2])
    assert sentences[3] == []


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURE
    target.write_text(json.dumps(render_fixture(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {target}")
