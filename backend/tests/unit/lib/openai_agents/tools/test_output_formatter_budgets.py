"""ALL-1275: bounded output-formatter tools, exact detail reads and chat delivery."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from src.lib.flows import output_projection
from src.lib.flows.chat_output_delivery import (
    chat_output_delivery_scope,
    deliver_projected_chat_output,
)
from src.lib.flows.output_projection import (
    FlowOutputArtifact,
    FlowOutputArtifactBundle,
    FlowOutputField,
    FlowOutputProjectionPlan,
    apply_projection_plan,
)
from src.lib.openai_agents.tools import output_formatter_tools
from src.lib.openai_agents.tools.output_formatter_tools import build_output_formatter_tools
from tests.unit.lib.flows.test_profile_projection import _selected_plan, profile_step  # noqa: F401
from tests.fixtures.flows.semantic_chat_output import (
    EM_DASH,
    EXPECTED_ROWS,
    SEMANTIC_CHAT_PLAN,
    build_semantic_output_bundle,
)


@pytest.fixture(scope="module")
def semantic_bundle() -> FlowOutputArtifactBundle:
    return build_semantic_output_bundle()


@pytest.fixture
def reports(monkeypatch) -> list[tuple[Any, dict[str, Any]]]:
    captured: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(
        output_formatter_tools,
        "report_payload_contract_violation",
        lambda violation, **kwargs: captured.append((violation, kwargs)),
    )
    return captured


def _tool(tools, name: str):
    return next(tool for tool in tools if tool.name == name)


async def _call(tool, payload: dict | None = None) -> tuple[dict, str]:
    raw = await tool.on_invoke_tool(
        SimpleNamespace(tool_name=tool.name), json.dumps(payload or {})
    )
    return json.loads(raw), raw


def _chat_tools(bundle, *, configured_plan=None, deliver=deliver_projected_chat_output):
    return build_output_formatter_tools(
        bundle=bundle,
        output_format="chat",
        formatter_agent_id="chat_output_formatter",
        configured_plan=configured_plan,
        deliver_chat_output=deliver,
    )


def _wide_bundle(*, field_count: int = 160, row_count: int = 3) -> FlowOutputArtifactBundle:
    rows = []
    for index in range(row_count):
        row = {
            "artifact.extraction_result_id": "wide-result",
            "object.object_id": f"wide-{index}",
        }
        for field in range(field_count):
            row[f"object.attribute.field_{field:03d}"] = f"value {field} for row {index} " * 4
        rows.append(row)
    catalog = [
        FlowOutputField(
            ref=f"object.attribute.field_{field:03d}",
            label=f"Field {field:03d}",
            value_type="string",
            row_source="object",
            non_empty_count=row_count,
            examples=[rows[0][f"object.attribute.field_{field:03d}"]],
        )
        for field in range(field_count)
    ] + [
        FlowOutputField(ref="object.object_id", label="Object Id", value_type="string", row_source="object"),
        FlowOutputField(
            ref="artifact.extraction_result_id", label="Result", value_type="string", row_source="object"
        ),
    ]
    return FlowOutputArtifactBundle(
        flow_name="Wide flow",
        artifacts=[FlowOutputArtifact(source_key="wide", rows_by_source={"object": rows})],
        field_catalog=catalog,
        default_row_source="object",
    )


def _rows_bundle(row_count: int) -> FlowOutputArtifactBundle:
    rows = [
        {"object.object_id": f"row-{index}", "object.attribute.name": f"name é {index}"}
        for index in range(row_count)
    ]
    return FlowOutputArtifactBundle(
        flow_name="Rows flow",
        artifacts=[FlowOutputArtifact(source_key="rows", rows_by_source={"object": rows})],
        field_catalog=[
            FlowOutputField(ref="object.attribute.name", label="Name", value_type="string", row_source="object"),
            FlowOutputField(ref="object.object_id", label="Object Id", value_type="string", row_source="object"),
        ],
        default_row_source="object",
    )


_NAME_PLAN = {
    "format": "chat",
    "row_source": "object",
    "columns": [{"key": "name", "header": "Name", "field_ref": "object.attribute.name"}],
}


@pytest.mark.asyncio
async def test_inventory_pages_and_searches_wide_catalog_within_budget(monkeypatch):
    monkeypatch.setenv("OUTPUT_TOOL_MAX_RESPONSE_CHARS", "6000")
    bundle = _wide_bundle()
    inspect = _tool(_chat_tools(bundle), "inspect_output_artifacts")

    seen: list[str] = []
    cursor = ""
    pages = 0
    while True:
        payload, raw = await _call(inspect, {"cursor": cursor, "row_source": "object"})
        assert payload["status"] == "ok", payload
        assert len(raw) <= 6000
        catalog = payload["inventory"]["field_catalog"]
        seen.extend(entry["ref"] for entry in catalog["entries"])
        pages += 1
        cursor = catalog["next_cursor"]
        if not cursor:
            break
    assert pages > 1
    assert seen == [field.ref for field in bundle.field_catalog]

    searched, _ = await _call(inspect, {"catalog_query": "Field 15"})
    refs = [entry["ref"] for entry in searched["inventory"]["field_catalog"]["entries"]]
    assert refs == [f"object.attribute.field_{n:03d}" for n in range(150, 160)]

    invalid, _ = await _call(inspect, {"cursor": "9999"})
    assert invalid["status"] == "invalid"
    assert "beyond" in invalid["errors"][0]


@pytest.mark.asyncio
async def test_inventory_on_semantic_fixture_is_bounded_and_excludes_diagnostics(semantic_bundle):
    inspect = _tool(_chat_tools(semantic_bundle), "inspect_output_artifacts")
    payload, raw = await _call(inspect)
    assert payload["status"] == "ok"
    assert len(raw) <= output_formatter_tools.get_output_tool_max_response_chars()
    inventory = payload["inventory"]
    assert inventory["row_sources"]["object"]["row_count"] == 7
    assert inventory["row_sources"]["validation_finding"]["row_count"] > 7
    # Catalog examples are short previews; full diagnostics need read_output_value.
    assert raw.count("lexical match") <= len(inventory["field_catalog"]["entries"])
    for entry in inventory["field_catalog"]["entries"]:
        for example in entry.get("examples", []):
            assert len(example) <= output_formatter_tools._MAX_TEXT_CHARS + 80


@pytest.mark.asyncio
async def test_read_output_value_reconstructs_giant_non_ascii_value_exactly(monkeypatch):
    monkeypatch.setenv("OUTPUT_TOOL_VALUE_READ_CHARS", "700")
    giant = ("é— expression in body wall muscle ≥ L3; " * 4000).strip()
    bundle = _rows_bundle(2)
    bundle.artifacts[0].rows_by_source["object"][1]["object.attribute.name"] = giant
    read = _tool(_chat_tools(bundle), "read_output_value")

    pieces: list[str] = []
    offset: int | None = 0
    calls = 0
    while offset is not None:
        payload, raw = await _call(
            read, {"row_ref": "object#2", "field_ref": "object.attribute.name", "offset": offset}
        )
        assert payload["status"] == "ok"
        assert payload["encoding"] == "text"
        assert len(raw) <= output_formatter_tools.get_output_tool_max_response_chars()
        pieces.append(payload["value_slice"])
        offset = payload["next_offset"]
        calls += 1
    assert "".join(pieces) == giant
    assert calls == -(-len(giant) // 700)

    for bad in (
        {"row_ref": "object#3", "field_ref": "object.attribute.name"},
        {"row_ref": "validation_finding#1", "field_ref": "object.attribute.name"},
        {"row_ref": "object:1", "field_ref": "object.attribute.name"},
        {"row_ref": "object#1", "field_ref": "object.attribute.unknown"},
        {"row_ref": "object#1", "field_ref": "object.attribute.name", "offset": 10_000},
    ):
        payload, _ = await _call(read, bad)
        assert payload["status"] == "invalid", bad


@pytest.mark.asyncio
async def test_read_output_value_returns_structured_diagnostics_as_json(semantic_bundle):
    read = _tool(_chat_tools(semantic_bundle), "read_output_value")
    row = semantic_bundle.rows_for_source("validation_finding")[0]
    expected = json.dumps(row["validation.candidate_matches"], ensure_ascii=False, sort_keys=True)
    pieces = []
    offset = 0
    while offset is not None:
        payload, _ = await _call(
            read,
            {"row_ref": "validation_finding#1", "field_ref": "validation.candidate_matches", "offset": offset},
        )
        assert payload["encoding"] == "json"
        pieces.append(payload["value_slice"])
        offset = payload["next_offset"]
    assert json.loads("".join(pieces)) == row["validation.candidate_matches"]
    assert "".join(pieces) == expected


@pytest.mark.asyncio
async def test_row_and_value_pages_obey_budget_with_continuation(monkeypatch, semantic_bundle):
    monkeypatch.setenv("OUTPUT_TOOL_MAX_RESPONSE_CHARS", "8000")
    tools = _chat_tools(semantic_bundle)
    rows_tool = _tool(tools, "inspect_output_rows")
    total = len(semantic_bundle.rows_for_source("validation_finding"))
    seen_refs: list[str] = []
    cursor = ""
    while True:
        payload, raw = await _call(
            rows_tool, {"row_source": "validation_finding", "limit": 1000, "cursor": cursor}
        )
        assert payload["status"] == "ok"
        assert len(raw) <= 8000
        seen_refs.extend(payload["row_refs"])
        cursor = payload["next_cursor"]
        if not cursor:
            break
    assert seen_refs == [f"validation_finding#{index}" for index in range(1, total + 1)]

    values_tool = _tool(tools, "inspect_field_values")
    payload, raw = await _call(
        values_tool,
        {"row_source": "validation_finding", "field_ref": "validation.message", "limit": 1000},
    )
    assert len(raw) <= 8000
    assert payload["distinct_count"] > len(payload["values"]) or not payload["next_cursor"]

    invalid, _ = await _call(rows_tool, {"row_source": "object", "cursor": "-1"})
    assert invalid["status"] == "invalid"


@pytest.mark.asyncio
async def test_budget_escape_is_reported_once_with_compact_error(monkeypatch, reports):
    monkeypatch.setenv("OUTPUT_TOOL_MAX_RESPONSE_CHARS", "2000")
    explain = _tool(_chat_tools(_rows_bundle(2)), "explain_formatter_capabilities")
    payload, raw = await _call(explain)
    assert payload["code"] == "response_over_budget"
    assert len(raw) <= 2000
    assert len(reports) == 1
    violation, kwargs = reports[0]
    assert violation.category == "tool_result_budget_escape"
    assert violation.setting == "OUTPUT_TOOL_MAX_RESPONSE_CHARS"
    assert kwargs["tool_name"] == "explain_formatter_capabilities"


@pytest.mark.asyncio
async def test_chat_finalize_renders_semantic_rows_and_returns_receipt(semantic_bundle):
    finalize = _tool(_chat_tools(semantic_bundle), "finalize_chat_output")
    with chat_output_delivery_scope() as delivery:
        receipt, raw = await _call(finalize, {"plan_json": json.dumps(SEMANTIC_CHAT_PLAN)})
        duplicate, _ = await _call(finalize, {"plan_json": json.dumps(SEMANTIC_CHAT_PLAN)})

    assert receipt["delivered"] is True
    assert receipt["projection_summary"]["row_count"] == len(EXPECTED_ROWS)
    assert "WBbt" not in raw
    assert duplicate["code"] == "already_finalized"
    assert duplicate["finalized_output"]["chat_output_id"] == receipt["chat_output_id"]
    lines = (delivery.output or "").split("\n")
    assert len(lines) == len(EXPECTED_ROWS) + 2
    assert lines[2].split(" | ")[3] == EM_DASH


@pytest.mark.asyncio
async def test_chat_output_renders_every_row_beyond_inspection_page_size():
    bundle = _rows_bundle(137)
    finalize = _tool(_chat_tools(bundle), "finalize_chat_output")
    with chat_output_delivery_scope() as delivery:
        receipt, raw = await _call(finalize, {"plan_json": json.dumps(_NAME_PLAN)})
    assert receipt["projection_summary"]["row_count"] == 137
    lines = (delivery.output or "").split("\n")
    assert len(lines) == 139
    assert lines[-1] == "| name é 136 |"
    assert "Showing" not in (delivery.output or "")
    assert len(raw) < 2000


@pytest.mark.asyncio
async def test_operational_row_ceiling_fails_explicitly_but_curator_limit_is_honored(monkeypatch, reports):
    monkeypatch.setattr(output_projection, "MAX_PROJECTION_ROWS", 5)
    bundle = _rows_bundle(7)
    finalize = _tool(_chat_tools(bundle), "finalize_chat_output")
    with chat_output_delivery_scope() as delivery:
        failed, _ = await _call(finalize, {"plan_json": json.dumps(_NAME_PLAN)})
    assert failed["status"] == "failed"
    assert failed["code"] == "operational_ceiling_exceeded"
    assert failed["delivered"] is False
    assert delivery.output is None
    assert len(reports) == 1
    violation, _ = reports[0]
    assert violation.category == "output_delivery_failure"
    assert (violation.measured, violation.limit, violation.unit) == (7, 5, "rows")
    assert violation.setting == "FLOW_PROJECTION_MAX_ROWS"

    limited_plan = {**_NAME_PLAN, "max_rows": 3}
    finalize = _tool(_chat_tools(bundle), "finalize_chat_output")
    with chat_output_delivery_scope() as delivery:
        receipt, _ = await _call(finalize, {"plan_json": json.dumps(limited_plan)})
    assert receipt["delivered"] is True
    assert receipt["projection_summary"]["limited_by_max_rows"] is True
    assert receipt["projection_summary"]["row_count"] == 3
    assert receipt["projection_summary"]["total_count"] == 7
    assert "Showing 3 of 7 projected rows." in (delivery.output or "")
    assert len(reports) == 1


@pytest.mark.asyncio
async def test_file_finalize_fails_explicitly_above_operational_row_ceiling(monkeypatch, reports):
    monkeypatch.setattr(output_projection, "MAX_PROJECTION_ROWS", 5)
    saved: list[Any] = []

    async def save(*args):
        saved.append(args)
        return {"file_id": "f", "filename": "f.csv", "download_url": "/f"}

    tools = build_output_formatter_tools(
        bundle=_rows_bundle(7),
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=save,
    )
    plan = {**_NAME_PLAN, "format": "csv"}
    failed, _ = await _call(_tool(tools, "finalize_and_save"), {"plan_json": json.dumps(plan)})
    assert failed["code"] == "operational_ceiling_exceeded"
    assert failed["saved_file"] is False
    assert saved == []
    assert len(reports) == 1


@pytest.mark.asyncio
async def test_chat_content_ceiling_and_delivery_failure_are_reported(monkeypatch, reports):
    monkeypatch.setenv("FLOW_OUTPUT_CHAT_MAX_CHARS", "1000")
    delivered: list[str] = []

    async def deliver(content, receipt):
        delivered.append(content)
        return receipt

    finalize = _tool(_chat_tools(_rows_bundle(200), deliver=deliver), "finalize_chat_output")
    failed, _ = await _call(finalize, {"plan_json": json.dumps(_NAME_PLAN)})
    assert failed["code"] == "operational_ceiling_exceeded"
    assert delivered == []
    assert reports[-1][0].setting == "FLOW_OUTPUT_CHAT_MAX_CHARS"

    monkeypatch.delenv("FLOW_OUTPUT_CHAT_MAX_CHARS")

    async def broken(content, receipt):
        raise RuntimeError("stream closed")

    finalize = _tool(_chat_tools(_rows_bundle(2), deliver=broken), "finalize_chat_output")
    failed, _ = await _call(finalize, {"plan_json": json.dumps(_NAME_PLAN)})
    assert failed["code"] == "delivery_failed"
    assert len(reports) == 2
    assert reports[-1][0].category == "output_delivery_failure"


@pytest.mark.asyncio
async def test_chat_notes_are_bounded_and_cannot_carry_table_rows(monkeypatch):
    monkeypatch.setenv("FLOW_OUTPUT_CHAT_NOTES_MAX_CHARS", "40")
    finalize = _tool(_chat_tools(_rows_bundle(2)), "finalize_chat_output")
    with chat_output_delivery_scope() as delivery:
        too_long, _ = await _call(finalize, {"notes": "x" * 41, "plan_json": json.dumps(_NAME_PLAN)})
        table, _ = await _call(finalize, {"notes": "| a |\n| b |", "plan_json": json.dumps(_NAME_PLAN)})
    assert too_long["status"] == "invalid"
    assert table["status"] == "invalid"
    assert delivery.output is None


@pytest.mark.asyncio
async def test_overrides_edit_derived_cells_and_rows_without_touching_saved_data():
    bundle = _rows_bundle(4)
    original = deepcopy(bundle.rows_for_source("object"))
    plan = {
        **_NAME_PLAN,
        "overrides": [
            {"row_ref": "object#2", "column_key": "name", "value": "curator label"},
            {"row_ref": "object#4", "exclude": True},
        ],
    }
    tools = _chat_tools(bundle)
    preview, _ = await _call(_tool(tools, "preview_output_projection"), {"plan_json": json.dumps(plan)})
    assert preview["preview"]["row_refs"] == ["object#1", "object#2", "object#3"]
    with chat_output_delivery_scope() as delivery:
        receipt, _ = await _call(_tool(tools, "finalize_chat_output"), {"plan_json": json.dumps(plan)})
    assert receipt["projection_summary"]["overrides_applied"] == 1
    assert receipt["projection_summary"]["rows_excluded"] == 1
    assert (delivery.output or "").split("\n")[2:] == [
        "| name é 0 |",
        "| curator label |",
        "| name é 2 |",
    ]
    assert bundle.rows_for_source("object") == original

    for overrides in (
        [{"row_ref": "object#9", "column_key": "name", "value": "x"}],
        [{"row_ref": "object#1", "column_key": "missing", "value": "x"}],
        [{"row_ref": "object#1", "column_key": "name", "value": {"rows": [1]}}],
        [{"row_ref": "object#1", "exclude": True, "value": "x"}],
        [{"row_ref": "object#1", "column_key": "name", "value": "x", "rows": []}],
    ):
        invalid, _ = await _call(
            _tool(tools, "validate_output_projection"),
            {"plan_json": json.dumps({**_NAME_PLAN, "overrides": overrides})},
        )
        assert invalid["status"] == "invalid", overrides


@pytest.mark.asyncio
async def test_selected_field_lock_rejects_model_overrides(profile_step):  # noqa: F811
    step, _, profile = profile_step
    step["node_id"] = "stocks"
    bundle = output_projection.build_flow_output_artifact_bundle(
        completed_steps=[step], flow_name="Stock", profile_resolver=lambda _: profile
    )
    locked = _selected_plan(bundle, "csv")

    async def save(*_args):
        return {"file_id": "f", "filename": "f.csv", "download_url": "/f"}

    tools = build_output_formatter_tools(
        bundle=bundle, output_format="csv", formatter_agent_id="csv_formatter",
        configured_plan=locked.model_dump(mode="json"), save_projected_output=save,
    )
    changed = locked.model_dump(mode="json")
    changed["overrides"] = [
        {"row_ref": "object#1", "column_key": locked.columns[0].key, "value": "x"}
    ]
    payload, _ = await _call(_tool(tools, "validate_output_projection"), {"plan_json": json.dumps(changed)})
    assert payload["status"] == "invalid"
    assert "fixed output fields" in payload["errors"][0]
    unchanged, _ = await _call(
        _tool(tools, "validate_output_projection"),
        {"plan_json": json.dumps(locked.model_dump(mode="json"))},
    )
    assert unchanged["status"] == "ok"


def test_format_elements_rejects_misaligned_lists_and_bad_placeholders():
    bundle = _rows_bundle(1)
    row = bundle.rows_for_source("object")[0]
    row["object.attribute.labels"] = ["a", "b"]
    row["object.attribute.ids"] = ["A:1"]
    bundle.field_catalog.extend(
        FlowOutputField(ref=ref, label=ref, value_type="list", row_source="object")
        for ref in ("object.attribute.labels", "object.attribute.ids")
    )
    misaligned = FlowOutputProjectionPlan.model_validate({
        "format": "chat", "row_source": "object",
        "columns": [{"key": "x", "transform": {
            "type": "format_elements",
            "field_refs": ["object.attribute.labels", "object.attribute.ids"],
            "default": "{1} ({2})",
        }}],
    })
    errors, _, _ = output_projection.validate_projection_plan(bundle, misaligned)
    assert any("incompatible lengths" in error for error in errors)
    bad_placeholder = misaligned.model_copy(deep=True)
    bad_placeholder.columns[0].transform.default = "{3}"
    errors, _, _ = output_projection.validate_projection_plan(bundle, bad_placeholder)
    assert any("placeholder {3}" in error for error in errors)


def test_projection_row_refs_follow_filters_and_sorts():
    bundle = _rows_bundle(3)
    plan = FlowOutputProjectionPlan.model_validate({
        **_NAME_PLAN,
        "sort": [{"field_ref": "object.object_id", "direction": "desc"}],
        "filters": [{"field_ref": "object.object_id", "op": "ne", "value": "row-1"}],
    })
    result = apply_projection_plan(bundle, plan)
    assert result.row_refs == ["object#3", "object#1"]
