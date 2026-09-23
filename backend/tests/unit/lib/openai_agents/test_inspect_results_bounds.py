"""ALL-1287: inspect_results pages, searches and reads results inside the budget."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import pytest

import src.lib.openai_agents.tool_result_bounds as tool_result_bounds
from src.lib.openai_agents import inspect_results as inspect_results_module
from src.lib.openai_agents.tool_result_bounds import canonical_json, content_sha256
from src.schemas.curation_workspace import CurationExtractionSourceKind
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackMetadata,
    DomainPackObjectDefinition,
)

BUDGET = 8192
RESULT_ID = "77777777-7777-7777-7777-777777777777"
RESULT_REF = f"extraction-result:{RESULT_ID}"
UNICODE = "β-catenin 表达 im Flügel 😀 "


def _field(path: str, label: str | None = None) -> DomainPackFieldDefinition:
    return DomainPackFieldDefinition(
        field_path=path,
        field_type=DomainPackFieldType.STRING,
        display_name=label,
    )


def _definition(object_type: str) -> DomainPackObjectDefinition:
    return DomainPackObjectDefinition(
        object_type=object_type,
        display_name=object_type,
        metadata={
            "object_role": "curatable_unit",
            "supervisor_manifest": {
                "primary_label_field": "label",
                "secondary_label_field": "symbol",
                "summary_fields": ["curie", "taxon", "note"],
            },
        },
        fields=[
            _field("label", "Label"),
            _field("symbol", "Symbol"),
            _field("curie", "Validated CURIE"),
            _field("taxon", "Taxon"),
            _field("note", "Note"),
            _field("verified_quote"),
        ],
    )


def _metadata() -> DomainPackMetadata:
    return DomainPackMetadata(
        pack_id="fixture.bounds",
        display_name="Fixture Bounds Pack",
        version="0.1.0",
        metadata_api_version="1.0.0",
        object_definitions=[_definition("Assertion"), _definition("Condition")],
    )


def _object(index: int, *, object_type: str = "Assertion", status: str = "validated",
            note: str | None = None) -> dict[str, Any]:
    return {
        "object_type": object_type,
        "object_role": "curatable_unit",
        "pending_ref_id": f"obj-{index}",
        "status": status,
        "payload": {
            "label": f"Object {index} {UNICODE}",
            "symbol": f"SYM{index}",
            "curie": f"TEST:{index:04d}",
            "taxon": "NCBITaxon:9606",
            "note": note if note is not None else (UNICODE * 14)[:400],
        },
        "evidence_record_ids": [f"evidence-{index}"],
    }


def _finding(index: int, *, severity: str = "warning", status: str = "open",
             field_path: str | None = "curie", **extra: Any) -> dict[str, Any]:
    target = {"pending_ref_id": f"obj-{index}", "object_type": "Assertion"}
    finding: dict[str, Any] = {
        "severity": severity,
        "status": status,
        "message": f"Finding for object {index}.",
        **extra,
    }
    if field_path:
        finding["field_ref"] = {"object_ref": target, "field_path": field_path}
    else:
        finding["object_ref"] = target
    return finding


def _payload(object_count: int = 60) -> dict[str, Any]:
    return {
        "envelope_id": "env-bounds",
        "domain_pack_id": "fixture.bounds",
        "status": "validated",
        "extracted_objects": [_object(index) for index in range(object_count)],
        "validation_findings": [],
        "metadata": {
            "evidence_records": [
                {
                    "evidence_record_id": f"evidence-{index}",
                    "verified_quote": f"Quote {index}: {UNICODE}",
                    "page": 3,
                    "section": "Results",
                }
                for index in range(object_count)
            ]
        },
    }


class _Record:
    def __init__(self, payload: dict[str, Any], **overrides: Any) -> None:
        values = {
            "extraction_result_id": RESULT_ID,
            "document_id": "doc-1",
            "adapter_key": "fixture.bounds",
            "agent_key": "fixture_agent",
            "source_kind": CurationExtractionSourceKind.CHAT,
            "origin_session_id": "session-bounds",
            "trace_id": "trace-1",
            "flow_run_id": None,
            "user_id": "user-bounds",
            "payload_json": payload,
            "created_at": datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc),
        }
        values.update(overrides)
        for key, value in values.items():
            setattr(self, key, value)


@pytest.fixture(autouse=True)
def active_chat(monkeypatch):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", str(BUDGET))
    monkeypatch.setattr(inspect_results_module, "get_current_session_id", lambda: "session-bounds")
    monkeypatch.setattr(inspect_results_module, "get_current_user_id", lambda: "user-bounds")
    monkeypatch.setattr(
        inspect_results_module.document_state,
        "get_document",
        lambda _user_id: {"id": "doc-1"},
    )
    monkeypatch.setattr(
        "src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id",
        lambda domain_pack_id: _metadata() if domain_pack_id == "fixture.bounds" else None,
    )


@pytest.fixture
def reported(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tool_result_bounds,
        "report_payload_contract_violation",
        lambda violation, **kwargs: calls.append((violation, kwargs)) or True,
    )
    return calls


def _install(monkeypatch, *records: _Record) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _list(**kwargs):
        calls.append(kwargs)
        return [
            record
            for record in records
            if kwargs.get("origin_session_id") in (None, record.origin_session_id)
            and kwargs.get("user_id") == record.user_id
        ]

    monkeypatch.setattr(inspect_results_module, "list_extraction_results", _list)
    return calls


async def _call(**kwargs: Any) -> dict[str, Any]:
    raw = await inspect_results_module.inspect_results(**kwargs)
    assert len(raw.encode("utf-8")) <= BUDGET, kwargs
    assert tool_result_bounds.serialized_size(json.loads(raw)) <= BUDGET, kwargs
    return json.loads(raw)


async def _follow(first: dict[str, Any], rows_key: str) -> list[dict[str, Any]]:
    rows = list(first[rows_key])
    page = first
    while page.get("next_call"):
        page = await _call(**page["next_call"])
        assert page["status"] == "ok", page
        rows.extend(page[rows_key])
    return rows


async def _read_all(first: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Reassemble an exact chunked value by following next_call."""

    page = first
    content = page["detail"]["content"]
    while page.get("next_call"):
        page = await _call(**page["next_call"])
        content += page["detail"]["content"]
    assert page["detail"]["complete"] is True
    return content, page


@pytest.mark.asyncio
async def test_default_object_page_is_smaller_and_fits_the_budget(monkeypatch):
    _install(monkeypatch, _Record(_payload(60)))

    page = await _call(action="objects", result_ref=RESULT_REF)

    assert page["status"] == "ok"
    assert page["object_count"] == 60
    assert 0 < page["returned_count"] <= 20
    assert page["next_call"]["result_ref"] == RESULT_REF
    assert page["next_call"]["result_sha256"] == page["result_sha256"]


@pytest.mark.asyncio
async def test_object_pages_are_complete_in_order_without_duplicates(monkeypatch):
    _install(monkeypatch, _Record(_payload(60)))

    first = await _call(action="objects", result_ref=RESULT_REF, limit=100)
    rows = await _follow(first, "objects")

    assert [row["object_ref"] for row in rows] == [f"obj-{index}" for index in range(60)]
    assert first["page_ended_by"] == "size_budget"
    # Values inside rows are exact, never shortened with an ellipsis.
    assert rows[0]["fields"]["note"] == (UNICODE * 14)[:400]
    assert rows[0]["display_label"] == f"Object 0 {UNICODE}"


@pytest.mark.asyncio
async def test_summary_is_a_compact_inventory_of_counts(monkeypatch):
    payload = _payload(300)
    payload["extracted_objects"][0]["object_type"] = "Condition"
    payload["extracted_objects"][1]["status"] = "needs_review"
    payload["validation_findings"] = [
        _finding(1, severity="error"),
        _finding(2, severity="warning", status="resolved"),
        _finding(2, severity="info", status="resolved", field_path=None),
    ]
    _install(monkeypatch, _Record(payload))

    summary = await _call(action="summary", result_ref=RESULT_REF)

    assert summary["status"] == "ok"
    assert "objects" not in summary
    inventory = summary["inventory"]
    assert inventory["object_count"] == 300
    assert inventory["objects_by_type"]["Condition"]["count"] == 1
    assert inventory["objects_by_type"]["Assertion"]["count"] == 299
    assert inventory["objects_by_status"] == {"needs_review": 1, "validated": 299}
    assert inventory["objects_by_validation_state"] == {"open": 1, "resolved": 1, "none": 298}
    assert inventory["findings"]["by_severity"] == {"error": 1, "info": 1, "warning": 1}
    assert inventory["findings"]["open"] == 1
    assert summary["filterable_fields"]["Assertion"] == ["label", "symbol", "curie", "taxon", "note"]


@pytest.mark.asyncio
async def test_filters_by_type_status_validation_and_field_text(monkeypatch):
    payload = _payload(30)
    payload["extracted_objects"][3]["object_type"] = "Condition"
    payload["extracted_objects"][4]["status"] = "needs_review"
    payload["extracted_objects"][5]["payload"]["taxon"] = "NCBITaxon:7955"
    payload["validation_findings"] = [
        _finding(6, severity="error"),
        _finding(7, severity="warning", status="resolved"),
    ]
    _install(monkeypatch, _Record(payload))

    async def refs(**filters):
        page = await _call(action="objects", result_ref=RESULT_REF, **filters)
        assert page["status"] == "ok", page
        return [row["object_ref"] for row in await _follow(page, "objects")]

    assert await refs(object_type="Condition") == ["obj-3"]
    assert await refs(status="needs_review") == ["obj-4"]
    assert await refs(query="7955") == ["obj-5"]
    assert await refs(field_path="taxon", query="7955") == ["obj-5"]
    assert await refs(field_path="curie", query="7955") == []
    assert await refs(validation_state="open") == ["obj-6"]
    assert await refs(validation_state="resolved") == ["obj-7"]
    assert await refs(severity="error") == ["obj-6"]
    assert len(await refs(validation_state="none")) == 28

    selected = await _call(action="objects", result_ref=RESULT_REF, fields=["curie"], limit=2)
    assert [set(row["fields"]) for row in selected["objects"]] == [{"curie"}, {"curie"}]
    assert selected["field_labels"] == {"curie": "Validated CURIE"}
    assert selected["next_call"]["fields"] == ["curie"]


@pytest.mark.asyncio
async def test_unknown_or_hidden_fields_and_bad_filters_are_rejected(monkeypatch):
    _install(monkeypatch, _Record(_payload(3)))

    hidden = await _call(action="objects", result_ref=RESULT_REF, fields=["verified_quote"])
    unknown = await _call(action="objects", result_ref=RESULT_REF, field_path="nope", query="x")
    bad_state = await _call(action="objects", result_ref=RESULT_REF, validation_state="maybe")
    long_query = await _call(action="objects", result_ref=RESULT_REF, query="x" * 5000)

    ignored = await _call(action="validation", result_ref=RESULT_REF, status="open")

    assert ignored["error_code"] == "invalid_request"
    assert "validation_state" in ignored["supported_arguments"]
    assert hidden["error_code"] == "field_not_supervisor_visible"
    assert unknown["error_code"] == "field_not_supervisor_visible"
    assert bad_state["error_code"] == "invalid_request"
    assert long_query["error_code"] == "invalid_request"


@pytest.mark.asyncio
async def test_oversized_unicode_field_is_withheld_and_read_exactly(monkeypatch):
    huge = (UNICODE * 3000) + "END"
    payload = _payload(3)
    payload["extracted_objects"][1]["payload"]["note"] = huge
    _install(monkeypatch, _Record(payload))

    page = await _call(action="objects", result_ref=RESULT_REF)
    row = next(row for row in page["objects"] if row["object_ref"] == "obj-1")
    descriptor = row["fields"]["note"]
    assert descriptor["withheld"] is True
    assert descriptor["total_chars"] == len(huge)
    assert descriptor["value_sha256"] == content_sha256(huge)
    assert "..." not in json.dumps(page, ensure_ascii=False)

    first = await _call(**descriptor["read"])
    content, last = await _read_all(first)
    assert content == huge
    assert last["detail"]["value_sha256"] == content_sha256(huge)

    whole = await _call(action="object", result_ref=RESULT_REF, object_ref="obj-1")
    assert whole["object"]["fields"]["note"]["withheld"] is True
    assert whole["object"]["fields"]["curie"] == "TEST:0001"

    small = await _call(action="field", result_ref=RESULT_REF, object_ref="obj-1", field_path="curie")
    assert small["value"] == "TEST:0001"
    assert small["complete"] is True


@pytest.mark.asyncio
async def test_findings_and_validator_results_page_and_read_exactly(monkeypatch):
    big_details = {"candidates": [f"{UNICODE}{index}" for index in range(900)]}
    payload = _payload(4)
    payload["validation_findings"] = [
        _finding(0, finding_id="finding-big", details=big_details),
        _finding(1, severity="error"),
        _finding(
            2,
            severity="info",
            status="resolved",
            details={
                "validation_result": {
                    "status": "resolved",
                    "request_id": "req-2",
                    "resolved_values": {"curie": "TEST:9999", "names": [UNICODE] * 400},
                    "target": {"object_type": "Assertion", "object_id": "obj-2", "field_path": "curie"},
                }
            },
        ),
    ]
    _install(monkeypatch, _Record(payload))

    findings = await _call(action="validation", result_ref=RESULT_REF)
    assert findings["finding_count"] == 3
    rows = await _follow(findings, "validation_findings")
    assert [row["finding_ref"] for row in rows] == ["finding-big", "finding-index:1", "finding-index:2"]
    assert rows[0]["details"]["withheld"] is True

    only_open = await _call(action="validation", result_ref=RESULT_REF, validation_state="open")
    assert only_open["finding_count"] == 2
    errors = await _call(action="validation", result_ref=RESULT_REF, severity="error")
    assert [row["finding_ref"] for row in errors["validation_findings"]] == ["finding-index:1"]

    one = await _call(action="validation", result_ref=RESULT_REF, finding_ref="finding-big")
    assert one["finding"]["finding_ref"] == "finding-big"
    assert one["finding"]["details"]["withheld"] is True
    content, _ = await _read_all(await _call(**one["finding"]["details"]["read"]))
    assert json.loads(content) == big_details
    assert content == canonical_json(big_details)

    results = await _call(action="validator_results", result_ref=RESULT_REF)
    assert results["total_count"] == 1
    entry = results["validator_results"][0]
    assert entry["validator_result_key"] == "request:req-2"
    assert entry["status"] == "resolved"
    assert entry["resolved_values"]["withheld"] is True
    detail = await _call(**entry["resolved_values"]["read"])
    content, _ = await _read_all(detail)
    assert json.loads(content)["curie"] == "TEST:9999"


@pytest.mark.asyncio
async def test_evidence_pages_and_oversized_quote_reads(monkeypatch):
    quote = (UNICODE * 2000) + "END"
    payload = _payload(2)
    payload["metadata"]["evidence_records"][0]["verified_quote"] = quote
    _install(monkeypatch, _Record(payload))

    page = await _call(action="evidence", result_ref=RESULT_REF, object_ref="obj-0")
    record = page["evidence"][0]
    assert record["withheld"] is True
    content, _ = await _read_all(await _call(**record["reads"][0]))
    assert content == quote

    other = await _call(action="evidence", result_ref=RESULT_REF, object_ref="obj-1")
    assert other["evidence"][0]["verified_quote"] == f"Quote 1: {UNICODE}"


@pytest.mark.asyncio
async def test_other_session_results_are_rejected_for_every_read(monkeypatch):
    _install(monkeypatch, _Record(_payload(3), origin_session_id="someone-elses-session"))

    for kwargs in (
        {"action": "summary"},
        {"action": "objects"},
        {"action": "object", "object_ref": "obj-0"},
        {"action": "field", "object_ref": "obj-0", "field_path": "note"},
        {"action": "validation", "finding_ref": "finding-index:0"},
        {"action": "validator_results"},
        {"action": "evidence", "object_ref": "obj-0", "detail_path": "0.verified_quote"},
    ):
        response = await _call(result_ref=RESULT_REF, **kwargs)
        assert response["status"] == "no_context", kwargs
        assert "Object 0" not in json.dumps(response, ensure_ascii=False)


@pytest.mark.asyncio
async def test_changed_result_and_bad_cursors_are_explicit(monkeypatch):
    record = _Record(_payload(60))
    _install(monkeypatch, record)

    first = await _call(action="objects", result_ref=RESULT_REF)
    changed = deepcopy(record.payload_json)
    changed["extracted_objects"].pop(0)
    record.payload_json = changed

    stale = await _call(**first["next_call"])
    malformed = await _call(action="objects", result_ref=RESULT_REF, cursor="abc")
    past_end = await _call(action="objects", result_ref=RESULT_REF, cursor="999")
    negative_limit = await _call(action="objects", result_ref=RESULT_REF, limit=-1)

    assert stale["error_code"] == "stale_result_cursor"
    assert malformed["error_code"] == "invalid_result_cursor"
    assert past_end["error_code"] == "invalid_result_cursor"
    assert negative_limit["error_code"] == "invalid_request"


@pytest.mark.asyncio
async def test_unmeetable_budget_returns_compact_failure_and_reports_once(monkeypatch, reported):
    _install(monkeypatch, _Record(_payload(3)))
    monkeypatch.setattr(inspect_results_module, "tool_result_budget", lambda: 200)

    response = json.loads(
        await inspect_results_module.inspect_results(action="summary", result_ref=RESULT_REF)
    )

    assert response["error_code"] == "tool_result_budget_unmet"
    assert response["tool_name"] == "inspect_results"
    assert len(reported) == 1
    assert reported[0][0].category == "tool_result_budget_escape"


@pytest.mark.asyncio
async def test_list_and_search_pages_fit_and_continue(monkeypatch):
    records = [
        _Record(
            _payload(40),
            extraction_result_id=f"8888888{index}-8888-8888-8888-888888888888",
            created_at=datetime(2026, 9, 22, 12, index, tzinfo=timezone.utc),
        )
        for index in range(3)
    ]
    _install(monkeypatch, *records)

    listing = await _call(action="list", target="this_chat")
    assert listing["total_count"] == 3

    first = await _call(action="search", target="this_chat", query="catenin")
    matches = await _follow(first, "matches")
    assert len(matches) == first["total_count"]
    keys = [(m["result_ref"], m["object_ref"], m["match_type"], m.get("field_path"),
             m.get("evidence_record_id")) for m in matches]
    assert len(keys) == len(set(keys))
