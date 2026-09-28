"""Program-rendered reports: models provide plans, never table rows."""
from copy import deepcopy
import json

import pytest

from src.lib.flows.chat_output_delivery import chat_output_delivery_scope
from src.lib.openai_agents.tools import output_formatter_tools as formatter
from tests.unit.lib.openai_agents.tools.test_output_formatter_budgets import (
    _call, _chat_tools, _rows_bundle, _tool,
)


def report():
    return {"sections": [
        {"heading": "Genes", "plan": {"format": "chat", "row_source": "object", "columns": [
            {"key": "name", "header": "Gene", "field_ref": "object.attribute.name"},
        ]}},
        {"heading": "Evidence", "plan": {"format": "chat", "row_source": "object", "columns": [
            {"key": "id", "header": "Record", "field_ref": "object.object_id"},
            {"key": "name", "header": "Paper name", "field_ref": "object.attribute.name"},
        ]}},
    ]}


@pytest.mark.asyncio
async def test_independent_tables_validate_and_deliver_once_without_model_rows():
    tools = _chat_tools(_rows_bundle(2))
    payload = {"report_json": json.dumps(report())}
    with chat_output_delivery_scope() as delivery:
        checked, _ = await _call(_tool(tools, "validate_output_projection"), payload)
        assert checked["status"] == "ok"
        assert delivery.output is None
        receipt, raw = await _call(_tool(tools, "finalize_chat_output"), payload)
        assert receipt["delivered"] is True
        assert delivery.output is not None
        assert receipt["report_summary"]["section_count"] == 2
        assert "## Genes\n\n| Gene |" in delivery.output
        assert "## Evidence\n\n| Record | Paper name |" in delivery.output
        assert "name é 0" in delivery.output
        assert "name é 0" not in raw
        saved = delivery.output
        second, _ = await _call(_tool(tools, "finalize_chat_output"), payload)
        assert second["code"] == "already_finalized"
        assert delivery.output == saved


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["field", "source", "raw_rows", "mixed", "empty", "heading"])
async def test_invalid_report_never_delivers_partial_tables(damage):
    r = report()
    kwargs = {}
    if damage == "field":
        r["sections"][1]["plan"]["columns"][0]["field_ref"] = "object.missing"
    elif damage == "source":
        r["sections"][1]["plan"]["source_keys"] = ["another-users-source"]
    elif damage == "raw_rows":
        r["sections"][1]["plan"]["rows"] = [{"gene": "invented"}]
    elif damage == "mixed":
        kwargs["plan_json"] = json.dumps(r["sections"][0]["plan"])
    elif damage == "empty":
        r["sections"] = []
    else:
        r["sections"][1]["heading"] = "Evidence\n| fabricated |"
    bundle = _rows_bundle(2)
    before = deepcopy(bundle)
    tools = _chat_tools(bundle)
    with chat_output_delivery_scope() as delivery:
        result, _ = await _call(_tool(tools, "finalize_chat_output"), {"report_json": json.dumps(r), **kwargs})
        assert result["status"] == "invalid"
        assert delivery.output is None
    assert bundle == before


@pytest.mark.asyncio
async def test_section_and_aggregate_limits_fail_without_delivery(monkeypatch):
    r = report()
    monkeypatch.setattr(formatter, "get_flow_output_chat_max_sections", lambda: 1)
    with chat_output_delivery_scope() as delivery:
        result, _ = await _call(_tool(_chat_tools(_rows_bundle(2)), "finalize_chat_output"), {"report_json": json.dumps(r)})
        assert result["status"] == "invalid"
        assert delivery.output is None
    monkeypatch.setattr(formatter, "get_flow_output_chat_max_sections", lambda: 12)
    monkeypatch.setattr(formatter, "get_flow_projection_max_rows", lambda: 3)
    reports = []
    monkeypatch.setattr(formatter, "report_payload_contract_violation", lambda *a, **k: reports.append(k))
    with chat_output_delivery_scope() as delivery:
        result, _ = await _call(_tool(_chat_tools(_rows_bundle(2)), "finalize_chat_output"), {"report_json": json.dumps(r)})
        assert result["code"] == "operational_ceiling_exceeded"
        assert delivery.output is None
        assert len(reports) == 1


@pytest.mark.asyncio
async def test_valid_empty_section_keeps_its_heading_and_does_not_invent_rows():
    r = report()
    r["sections"][1]["plan"]["filters"] = [{"field_ref": "object.object_id", "op": "eq", "value": "absent"}]
    with chat_output_delivery_scope() as delivery:
        result, _ = await _call(_tool(_chat_tools(_rows_bundle(2)), "finalize_chat_output"), {"report_json": json.dumps(r)})
        assert result["delivered"] is True
        assert delivery.output is not None
        assert "## Evidence\n\nNo rows matched" in delivery.output


def test_invalid_selected_field_configuration_is_rejected_before_report_tools_exist():
    # Chat selected-fields is not a supported saved-layout format today. Do not
    # turn it into an unconstrained report as a fallback.
    plan = report()["sections"][0]["plan"]
    plan["selection_mode"] = "selected_fields"
    with pytest.raises(ValueError, match="selected field layout"):
        _chat_tools(_rows_bundle(2), configured_plan=plan)


@pytest.mark.asyncio
async def test_six_source_report_preserves_each_table_and_terminal_persistence():
    from src.lib.flows.output_projection import FlowOutputArtifact, FlowOutputArtifactBundle, FlowOutputField
    from src.lib.flows.outcome import FlowRunOutcome
    names = ["Genes", "Alleles", "Phenotypes", "Expression", "GO candidates", "GO lookup"]
    artifacts, fields, sections = [], [], []
    for index, name in enumerate(names):
        ref = f"object.attribute.field_{index}"
        artifacts.append(FlowOutputArtifact(source_key=name, rows_by_source={"object": [{
            "artifact.source_key": name, "object.object_id": f"record-{index}", ref: f"saved-value-{index}",
        }]}))
        fields.append(FlowOutputField(ref=ref, label=name, row_source="object", value_type="string"))
        sections.append({"heading": name, "plan": {"format": "chat", "row_source": "object",
            "source_keys": [name], "columns": [{"key": f"column-{index}", "header": name, "field_ref": ref}]}})
    bundle = FlowOutputArtifactBundle(flow_name="Synthetic six-source flow", artifacts=artifacts,
                                      field_catalog=fields, default_row_source="object")
    with chat_output_delivery_scope() as delivery:
        receipt, raw = await _call(_tool(_chat_tools(bundle), "finalize_chat_output"),
                                   {"report_json": json.dumps({"sections": sections})})
        assert receipt["delivered"] is True, receipt
        output = delivery.output
    assert output is not None
    for index, name in enumerate(names):
        assert f"## {name}\n\n| {name} |\n| --- |\n| saved-value-{index} |" in output
        assert output.count(f"saved-value-{index}") == 1
        assert f"saved-value-{index}" not in raw
    outcome = FlowRunOutcome()
    event = {"type": "CHAT_OUTPUT_READY", "details": {"formatter_node_id": "report", "output": output}}
    outcome.observe(event)
    outcome.observe(event)
    outcome.observe({"type": "RUN_FINISHED", "response": "Chat report delivered."})
    outcome.observe({"type": "FLOW_FINISHED", "status": "completed"})
    assert outcome.final_user_visible_text == output
    assert sum(e["type"] == "CHAT_OUTPUT_READY" for e in outcome.events_for_persistence()) == 1
    assert outcome.publishable_terminal_events() == []
    outcome.mark_persisted(transcript=True)
    assert outcome.publishable_terminal_events() == outcome.events_for_persistence()


@pytest.mark.asyncio
async def test_combined_report_character_ceiling_and_verbatim_scientific_headings(monkeypatch):
    r = report()
    r["sections"][0]["heading"] = "C. elegans gene-disease (GO) & phenotypes"
    with chat_output_delivery_scope() as delivery:
        receipt, _ = await _call(_tool(_chat_tools(_rows_bundle(2)), "finalize_chat_output"),
                                {"report_json": json.dumps(r)})
        assert receipt["delivered"]
        assert delivery.output is not None
        assert "## C. elegans gene-disease (GO) & phenotypes\n\n" in delivery.output
        rendered_length = len(delivery.output)
    monkeypatch.setattr(formatter, "get_flow_output_chat_max_chars", lambda: rendered_length - 1)
    monkeypatch.setattr(formatter, "report_payload_contract_violation", lambda *a, **k: None)
    with chat_output_delivery_scope() as delivery:
        result, _ = await _call(_tool(_chat_tools(_rows_bundle(2)), "finalize_chat_output"),
                                {"report_json": json.dumps(r)})
        assert result["code"] == "operational_ceiling_exceeded"
        assert delivery.output is None
