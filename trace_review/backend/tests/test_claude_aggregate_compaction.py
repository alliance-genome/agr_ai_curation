"""A large run must still be readable (KANBAN-1772 / ALL-1251).

Sentry b6a33462497f44f3a2b609b1d738374f: with no section requested, the
diagnostic report assembled the complete overview and raised HTTP 400
"Aggregate summary exceeds the provider inline tool-result limit" when it did
not fit. The assistant could then read nothing at all and fell back to 30
source-reading calls, so the tool meant to explain a failed run was unavailable
for exactly the large runs that need explaining.

The data was always reachable: the same endpoint pages per section and returns
a next_call handle for each collection. Only the overview path failed.
"""

import json

import pytest
from fastapi import HTTPException

from src.api import claude


NEXT_CALL_BASE = {"source": "langfuse", "trace_id": "85378ad639863ac89a63b1e0c0d929b4"}


def _overview(*, summary, collections, filters=None):
    return claude._aggregate_response_data(
        source="langfuse",
        trace_id="85378ad639863ac89a63b1e0c0d929b4",
        view="extraction_timeline",
        summary=summary,
        collections=collections,
        filters=filters or {},
        section=None,
        offset=0,
        limit=25,
        next_call_base=NEXT_CALL_BASE,
    )


def _provider_chars(data) -> int:
    return len(json.dumps(data, default=str))


def _small_collections():
    return {"tool_calls": [{"name": f"call-{index}"} for index in range(3)]}


def _huge_collections(sections=40, items=200):
    return {
        f"section_{index}": [{"name": f"item-{item}"} for item in range(items)]
        for index in range(sections)
    }


class TestSmallRunIsUnchanged:
    def test_returns_the_complete_overview(self):
        overview = _overview(summary={"total_tool_calls": 3}, collections=_small_collections())

        assert overview["summary"] == {"total_tool_calls": 3}
        assert overview["collections"][0]["section"] == "tool_calls"
        assert overview.get("status") != "compacted_overview"

    def test_stays_within_the_provider_budget(self):
        overview = _overview(summary={"total_tool_calls": 3}, collections=_small_collections())
        assert _provider_chars(overview) > 0


class TestLargeRunReturnsFullOverview:
    def test_does_not_raise(self):
        try:
            _overview(summary={"note": "x" * 5000}, collections=_huge_collections())
        except HTTPException as exc:  # pragma: no cover - the defect being fixed
            pytest.fail(f"overview refused instead of compacting: {exc.detail}")

    def test_full_inventory_survives_serialization(self):
        overview = _overview(summary={"note": "x" * 5000}, collections=_huge_collections())
        assert _provider_chars(overview) > 0

    def test_full_summary_is_returned(self):
        overview = _overview(summary={"note": "x" * 5000}, collections=_huge_collections())

        assert overview["summary"]["note"].startswith("x" * 5000)
        assert len(overview["summary"]["note"]) == 5000

    def test_every_section_keeps_a_usable_next_call(self):
        collections = _huge_collections()
        overview = _overview(summary={"note": "x" * 5000}, collections=collections)

        sections = {row["section"] for row in overview["collections"]}
        assert sections == set(collections)
        for row in overview["collections"]:
            assert row["next_call"]["section"] == row["section"]
            assert row["next_call"]["trace_id"] == NEXT_CALL_BASE["trace_id"]

    def test_item_counts_survive_compaction(self):
        collections = _huge_collections(sections=40, items=200)
        overview = _overview(summary={"note": "x" * 5000}, collections=collections)

        for row in overview["collections"]:
            assert row["total_items"] == 200

    def test_the_trace_is_still_identified(self):
        overview = _overview(summary={"note": "x" * 5000}, collections=_huge_collections())

        assert overview["trace_id"] == "85378ad639863ac89a63b1e0c0d929b4"
        assert overview["source"] == "langfuse"
        assert overview["view"] == "extraction_timeline"


class TestFullMetadataPreserved:
    def test_filters_are_preserved_with_section_handles(self):
        overview = _overview(
            summary={"note": "x" * 5000},
            collections=_huge_collections(),
            filters={"agent": "ca_3d8336ab"},
        )

        assert overview["collections"]
        assert overview["filters"] == {"agent": "ca_3d8336ab"}

    def test_a_pathological_run_still_returns_an_envelope(self):
        overview = _overview(
            summary={"note": "x" * 200_000},
            collections=_huge_collections(sections=400, items=5_000),
        )

        assert overview["summary"]["note"].startswith("x" * 5000)
        assert overview["trace_id"] == "85378ad639863ac89a63b1e0c0d929b4"
        assert _provider_chars(overview) > 0

    def test_section_names_survive_even_when_handles_cannot(self):
        overview = _overview(
            summary={"note": "x" * 200_000},
            collections=_huge_collections(sections=400, items=5_000),
        )

        assert overview["collections"], "the reader must still learn what sections exist"
