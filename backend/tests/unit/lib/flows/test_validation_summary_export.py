"""Reconstructed ALL-1209 topology, not a raw historical trace fixture."""
import csv
import io
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.lib.flows.export_fields import packaged_export_fields
from src.lib.flows.output_projection import (
    FlowOutputProjectionPlan, apply_projection_plan, build_flow_output_artifact_bundle,
    _bounded_projection_row,
)
from src.lib.flows.validation_summary_export import object_validation_summary
from src.lib.openai_agents.tools.output_formatter_tools import build_output_formatter_tools
from src.lib.openai_agents.tools.file_output_tools import _projection_content_for_file_type

PACK = "agr.alliance.allele"
PREFIX = "object.summary.AlleleMention."


def fixture():
    mention = {"object_type": "AlleleMention", "pending_ref_id": "mention-1",
               "payload": {"mention": {"text": 'original, "mention"\nline',
                                         "normalized_hint": "RRID:MGI:5487397"}},
               "evidence_record_ids": ["ev-1", "ev-1"]}
    unresolved = deepcopy(mention)
    unresolved["pending_ref_id"] = "mention-2"
    quote = {"object_type": "EvidenceQuote", "pending_ref_id": "quote-1",
             "payload": {"evidence_record_id": "ev-1", "verified_quote": 'quote, "text"\nline'}}
    finding = {"finding_id": "f-1", "status": "resolved", "message": "Saved lookup.",
               "field_ref": {"object_ref": {"object_type": "AlleleMention", "pending_ref_id": "mention-1"},
                             "field_path": "mention.text"},
               "details": {"validation_result": {
                   "status": "resolved", "request_id": "request-1",
                   "validator_binding_id": "allele_mention_reference_validation",
                   "target": {"domain_pack_id": PACK, "object_type": "AlleleMention", "object_id": "mention-1"},
                   "resolved_values": {"curie": "MGI:3716464", "symbol": "validated symbol", "taxon": "mouse"}}}}
    return {"domain_pack_id": PACK, "envelope_id": "env-1",
            "extracted_objects": [mention, unresolved, quote, deepcopy(quote)],
            "validation_findings": [finding]}


def bundle(*payloads):
    return build_flow_output_artifact_bundle(completed_steps=[{
        "step": index, "node_id": f"node-{index}", "source_key": f"source-{index}",
        "extraction_result_id": f"result-{index}", "agent_id": "allele_extractor",
        "candidate": SimpleNamespace(agent_key="allele_extractor", adapter_key="allele",
                                     payload_json=payload, candidate_count=0),
    } for index, payload in enumerate(payloads)], flow_name="Summary", output_format="csv")


def plan():
    return FlowOutputProjectionPlan.model_validate({
        "format": "csv", "row_source": "object", "row_strategy": "wide_union",
        "filters": [{"field_ref": "object.object_type", "op": "eq", "value": "AlleleMention"}],
        "columns": [{"key": key, "field_ref": ref} for key, ref in {
            "mention": "object.pack.AlleleMention.mention.text",
            "paper_hint": "object.pack.AlleleMention.mention.normalized_hint",
            "id": PREFIX + "resolved.curie", "symbol": PREFIX + "resolved.symbol",
            "status": PREFIX + "status", "evidence": PREFIX + "evidence",
            "source": "artifact.extraction_result_id",
        }.items()],
    })


def summarize(payload):
    fields = packaged_export_fields("allele_extractor", {"curation": {"domain_pack_id": PACK}})
    config = next(field["summary_config"] for field in fields if "summary_key" in field)
    return object_validation_summary(payload["extracted_objects"][0], payload["extracted_objects"],
                                     payload["validation_findings"], config, PACK)


def test_same_source_only_and_no_row_expansion():
    first, second = fixture(), fixture()
    second["validation_findings"] = []
    result = apply_projection_plan(bundle(first, second), plan())
    assert len(result.rows) == 4
    assert [row["id"] for row in result.rows] == ["MGI:3716464", "", "", ""]
    assert result.rows[0]["paper_hint"] == "RRID:MGI:5487397"
    assert len(result.rows[0]["evidence"]) == 1
    assert result.rows[2]["source"] == "result-1"


@pytest.mark.parametrize("change", ["duplicate_identity", "ambiguous_pending_ref", "null_values", "wrong_target", "wrong_pack", "mixed", "conflicting_values", "bare_ref"])
def test_unsafe_links_do_not_supply_identity(change):
    payload = fixture()
    finding = payload["validation_findings"][0]
    if change == "duplicate_identity":
        payload["extracted_objects"].append(deepcopy(payload["extracted_objects"][0]))
    elif change == "ambiguous_pending_ref":
        payload["extracted_objects"][0]["object_id"] = "durable-1"
        other = deepcopy(payload["extracted_objects"][0])
        other["object_id"] = "durable-2"
        payload["extracted_objects"].append(other)
    elif change == "null_values":
        finding["details"]["validation_result"]["resolved_values"] = None
    elif change == "wrong_target":
        finding["details"]["validation_result"]["target"]["object_id"] = "different"
    elif change == "wrong_pack":
        finding["details"]["validation_result"]["target"]["domain_pack_id"] = "other"
    elif change == "bare_ref":
        finding["field_ref"]["object_ref"].pop("object_type")
    else:
        other = deepcopy(finding)
        other["finding_id"] = "f-2"
        if change == "mixed":
            other["status"] = "open"
            other["details"]["validation_result"]["status"] = "unresolved"
        else:
            other["details"]["validation_result"]["resolved_values"]["curie"] = "MGI:other"
        payload["validation_findings"].append(other)
    summary = summarize(payload)
    assert summary["resolved.curie"] is None
    if change in {"mixed", "conflicting_values"}:
        assert summary["finding_ids"] == ["f-1", "f-2"]


def test_target_only_and_ambiguous_evidence():
    payload = fixture()
    payload["validation_findings"][0].pop("field_ref")
    assert summarize(payload)["resolved.curie"] == "MGI:3716464"
    payload["extracted_objects"][-1]["payload"]["verified_quote"] = "contradictory"
    summary = summarize(payload)
    assert summary["evidence"] == []
    assert any("ambiguous saved record" in note for note in summary["notes"])


@pytest.mark.asyncio
async def test_inventory_inspect_validate_preview_and_saved_csv(tmp_path):
    saved = []
    async def save(output_format, projection, filename_hint, formatter_agent_id):
        content = _projection_content_for_file_type(output_format=output_format, projection=projection)
        path = tmp_path / "summary.csv"
        path.write_bytes(content.encode())
        saved.append((projection, path))
        return {"file_id": "test-file", "filename": path.name, "format": "csv",
                "size_bytes": len(content.encode()), "download_url": "/download/test-file"}
    tools = build_output_formatter_tools(bundle=bundle(fixture()), output_format="csv",
                                         formatter_agent_id="csv_formatter", save_projected_output=save)
    async def invoke(name, **kwargs):
        tool = next(tool for tool in tools if tool.name == name)
        return json.loads(await tool.on_invoke_tool(SimpleNamespace(tool_name=name), json.dumps(kwargs)))
    inventory = await invoke("inspect_output_artifacts")
    assert PREFIX + "resolved.curie" in {field["ref"] for field in inventory["inventory"]["field_catalog"]}
    rejected = await invoke("inspect_output_rows", row_source="validation_finding", field_refs_json=json.dumps([
        "validation.target.input_values.mention", "validation.resolved_values.symbol",
        "validation.resolved_values.curie", "validation.status"]))
    assert rejected["status"] == "invalid"
    inspected = await invoke("inspect_output_rows", row_source="object", field_refs_json=json.dumps([
        "object.pack.AlleleMention.mention.text", PREFIX + "resolved.curie", PREFIX + "status"]),
        filters_json=json.dumps([{"field_ref": "object.object_type", "op": "eq", "value": "AlleleMention"}]))
    assert inspected["status"] == "ok"
    assert inspected["total_count"] == 2
    plan_json = plan().model_dump_json()
    assert (await invoke("validate_output_projection", plan_json=plan_json))["status"] == "ok"
    preview = await invoke("preview_output_projection", plan_json=plan_json)
    result = await invoke("finalize_and_save", plan_json=plan_json, filename_hint="summary")
    assert result["status"] == "ok"
    assert "/download/test-file" in json.dumps(result)
    projection, path = saved[0]
    # Existing tool previews normalize whitespace and bound text; serialization
    # must use the original values, never the presentation-only preview.
    assert preview["preview"]["preview_rows"] == [_bounded_projection_row(row) for row in projection.rows]
    parsed = list(csv.DictReader(io.StringIO(path.read_text())))
    assert len(parsed) == 2
    assert parsed[0]["mention"] == projection.rows[0]["mention"]
    assert json.loads(parsed[0]["evidence"]) == projection.rows[0]["evidence"]
    assert parsed[1]["id"] == ""
