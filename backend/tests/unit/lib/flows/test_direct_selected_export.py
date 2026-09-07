"""Fixed output plans use the existing validated saver without a formatter run."""

import json
from types import SimpleNamespace

import pytest

from src.lib.context import get_current_flow_output_attachment
from src.lib.flows import executor
from src.lib.flows.output_projection import build_flow_output_artifact_bundle
from src.lib.openai_agents.tools.output_formatter_tools import build_output_formatter_tools
from .test_profile_projection import profile_step, _selected_plan  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["csv", "tsv", "json"])
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("save_failure", [False, True])
async def test_direct_export_reuses_validation_and_saver(monkeypatch, profile_step, format, empty, save_failure):
    monkeypatch.setenv("FLOW_SELECTED_FIELDS_DIRECT_EXPORT", "true")
    step, _, profile = profile_step
    step["node_id"] = "stocks"
    if empty:
        step["candidate"]["payload_json"]["curatable_objects"] = []
    bundle = build_flow_output_artifact_bundle(completed_steps=[step], flow_name="Stock", profile_resolver=lambda _: profile)
    plan = _selected_plan(bundle, format)
    saved = []

    async def save(output_format, projection, descriptor, agent_id):
        saved.append((output_format, projection, descriptor, agent_id, get_current_flow_output_attachment()))
        if save_failure:
            raise RuntimeError("Storage unavailable")
        return {"file_id": "saved", "download_url": "/api/files/saved/download"}

    def get_agent(agent_id, **kwargs):
        assert kwargs["authenticated_groups"] == ["WB"]
        assert kwargs["formatter_projection_plan"] == plan.model_dump(mode="json")
        return SimpleNamespace(tools=build_output_formatter_tools(
            bundle=bundle, output_format=format, formatter_agent_id=agent_id,
            configured_plan=kwargs["formatter_projection_plan"], save_projected_output=save,
        ))

    monkeypatch.setattr(executor, "get_agent_by_id", get_agent)
    monkeypatch.setattr(executor, "_build_terminal_flow_artifact_bundle", lambda **_: bundle)
    monkeypatch.setattr(executor, "_create_streaming_tool", lambda **_: pytest.fail("Formatter model must not run"))
    before = get_current_flow_output_attachment()
    tool = executor._make_flow_runtime_formatter_tool(
        agent_id=f"{format}_formatter", agent_name="Export", output_format=format,
        tool_name="export", tool_description="Export saved fields", specialist_name="Export",
        base_context={"authenticated_groups": ["WB"]}, step_instruction_prefix="",
        completed_steps=[step], flow_name="Stock", flow_run_id="run-1", document_id="doc-1",
        node_data={"projection_plan": plan.model_dump(mode="json")}, source_node_ids=["stocks"],
    )
    if save_failure:
        with pytest.raises(ValueError, match="Storage unavailable"):
            await tool.on_invoke_tool(SimpleNamespace(tool_name="export", run_config=None), json.dumps({"query": "Export"}))
    else:
        result = json.loads(await tool.on_invoke_tool(SimpleNamespace(tool_name="export", run_config=None), json.dumps({"query": "Export"})))
        assert result["status"] == "ok"
        assert result["download_url"] == "/api/files/saved/download"
    assert len(saved) == 1
    assert saved[0][0] == format
    assert saved[0][1].total_count == (0 if empty else 1)
    if not empty:
        assert saved[0][1].rows[0]["stocks sources"][1] == {"name": "B"}
    assert saved[0][4]["source_keys"] == [a.source_key for a in bundle.artifacts if a.source_key]
    assert get_current_flow_output_attachment() == before
