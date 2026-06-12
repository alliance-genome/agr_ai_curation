import importlib
import json
from types import SimpleNamespace

import pytest


def _executor_module():
    return importlib.import_module("src.lib.flows.executor")


def _completed_artifact_step():
    executor = _executor_module()
    payload = {
        "domain_pack_id": "gene",
        "envelope_id": "env-gene-1",
        "objects": [
            {"object_type": "Gene", "symbol": "TP53"},
            {"object_type": "Gene", "symbol": "BRCA1"},
        ],
    }
    return {
        "step": 1,
        "agent_id": "gene",
        "agent_name": "Gene Specialist",
        "output_preview": "Saved gene candidates for TP53 and BRCA1.",
        "candidate": executor.ExtractionEnvelopeCandidate(
            agent_key="gene",
            payload_json=payload,
            candidate_count=2,
            adapter_key="gene",
            conversation_summary="Extracted two gene candidates.",
        ),
    }


def _completed_source_step(
    executor,
    *,
    step: int,
    agent_id: str,
    adapter_key: str,
    object_type: str,
    object_id: str,
    payload: dict,
    extraction_result_id: str | None = None,
    metadata: dict | None = None,
):
    source_id = extraction_result_id or f"{adapter_key}-step-{step}"
    result = {
        "step": step,
        "agent_id": agent_id,
        "agent_name": agent_id.replace("_", " ").title(),
        "output_preview": f"Extracted {object_type}.",
        "candidate": executor.ExtractionEnvelopeCandidate(
            agent_key=agent_id,
            payload_json={
                "domain_pack_id": adapter_key,
                "envelope_id": f"env-{source_id}",
                "objects": [
                    {
                        "object_type": object_type,
                        "object_id": object_id,
                        "status": "candidate",
                        "payload": payload,
                    }
                ],
            },
            candidate_count=1,
            adapter_key=adapter_key,
            conversation_summary=f"Extracted {object_type}.",
            metadata=metadata or {},
        ),
    }
    if extraction_result_id is not None:
        result["extraction_result_id"] = extraction_result_id
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent_id",
    ["csv_output_formatter", "json_output_formatter", "chat_output_formatter"],
)
async def test_terminal_formatter_flow_output_fails_without_model_fallback(agent_id):
    executor = _executor_module()

    with pytest.raises(
        executor.FlowTerminalOutputProjectionError,
        match="no completed structured artifacts",
    ):
        await executor._try_project_terminal_flow_output(
            agent_id=agent_id,
            completed_steps=[{"step": 1, "output": "plain text"}],
            flow_name="No Artifacts",
        )


@pytest.mark.asyncio
async def test_tsv_formatter_rejects_literal_only_projection_without_objects(monkeypatch):
    executor = _executor_module()
    save_calls = []

    async def _fake_save_tsv_impl(
        data_json: str,
        filename: str,
        columns: str | None = None,
    ) -> dict:
        save_calls.append({"data": json.loads(data_json), "columns": columns})
        return {"file_id": "unexpected", "filename": filename, "format": "tsv"}

    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools._save_tsv_impl",
        _fake_save_tsv_impl,
    )

    with pytest.raises(
        executor.FlowTerminalOutputProjectionError,
        match="literal-only TSV projections are not allowed",
    ):
        await executor._try_project_terminal_flow_output(
            agent_id="tsv_formatter",
            completed_steps=[],
            flow_name="No Artifacts",
            projection_plan={
                "format": "tsv",
                "row_source": "artifact",
                "columns": [
                    {
                        "key": "status",
                        "transform": {"type": "literal", "value": "completed"},
                    }
                ],
            },
        )

    assert save_calls == []


@pytest.mark.asyncio
async def test_tsv_formatter_rejects_pdf_answer_table_without_canonical_objects(
    monkeypatch,
):
    executor = _executor_module()
    save_calls = []
    payload = {
        "answer": (
            "Extracted genetic reagents:\n\n"
            "synonym\tsource\tsource_identifier\tcount\n"
            "Ck:GFP\tThis study\tNew in paper\t4\n"
            "Actn RNAi\tSource not found\tNot found\t2\n"
        ),
        "items": [
            {
                "label": "group-level audit item",
                "entity_type": "genetic reagent group",
                "evidence_record_ids": ["ev-1"],
            }
        ],
        "evidence_records": [
            {
                "evidence_record_id": "ev-1",
                "verified_quote": "Server verified quote.",
            }
        ],
    }

    async def _fake_save_tsv_impl(
        data_json: str,
        filename: str,
        columns: str | None = None,
    ) -> dict:
        save_calls.append(
            {
                "data": json.loads(data_json),
                "filename": filename,
                "columns": json.loads(columns or "[]"),
            }
        )
        return {
            "file_id": "file-pdf-tsv",
            "filename": "pdf.tsv",
            "format": "tsv",
            "size_bytes": 1234,
            "mime_type": "text/tab-separated-values",
            "download_url": "/api/files/file-pdf-tsv/download",
            "created_at": "2026-06-11T00:00:00Z",
        }

    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools._save_tsv_impl",
        _fake_save_tsv_impl,
    )

    with pytest.raises(
        executor.FlowTerminalOutputProjectionError,
        match="no completed structured artifacts",
    ):
        await executor._try_project_terminal_flow_output(
            agent_id="tsv_formatter",
            completed_steps=[
                {
                    "step": 1,
                    "agent_id": "pdf_extraction",
                    "agent_name": "General PDF Extraction Agent",
                    "output": json.dumps(payload),
                    "output_preview": "Extracted genetic reagents.",
                    "candidate": None,
                }
            ],
            flow_name="PDF TSV Flow",
        )

    assert save_calls == []


@pytest.mark.asyncio
async def test_tsv_formatter_projects_canonical_pdf_curatable_objects_without_model_fallback(
    monkeypatch,
):
    executor = _executor_module()
    save_calls = []
    payload = {
        "summary": "Two generic reagent candidates were retained.",
        "curatable_objects": [
            {
                "object_type": "generic_reagent_candidate",
                "pending_ref_id": "generic-obj-1",
                "payload": {
                    "class_key": "generic:generic_reagent_candidate",
                    "label": "Ck:GFP",
                    "source": "This study",
                    "source_identifier": "New in paper",
                    "count": 4,
                },
                "evidence_record_ids": ["ev-1"],
            },
            {
                "object_type": "generic_reagent_candidate",
                "pending_ref_id": "generic-obj-2",
                "payload": {
                    "class_key": "generic:generic_reagent_candidate",
                    "label": "Actn RNAi",
                    "source": "Source not found",
                    "source_identifier": "Not found",
                    "count": 2,
                },
                "evidence_record_ids": ["ev-2"],
            },
        ],
        "metadata": {
            "evidence_records": [
                {"evidence_record_id": "ev-1", "verified_quote": "Ck:GFP was reported."},
                {"evidence_record_id": "ev-2", "verified_quote": "Actn RNAi was reported."},
            ]
        },
        "run_summary": {"candidate_count": 2, "kept_count": 2},
    }

    async def _fake_save_tsv_impl(
        data_json: str,
        filename: str,
        columns: str | None = None,
    ) -> dict:
        save_calls.append(
            {
                "data": json.loads(data_json),
                "filename": filename,
                "columns": json.loads(columns or "[]"),
            }
        )
        return {
            "file_id": "file-pdf-tsv",
            "filename": "pdf.tsv",
            "format": "tsv",
            "size_bytes": 1234,
            "mime_type": "text/tab-separated-values",
            "download_url": "/api/files/file-pdf-tsv/download",
            "created_at": "2026-06-11T00:00:00Z",
        }

    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools._save_tsv_impl",
        _fake_save_tsv_impl,
    )

    await executor._try_project_terminal_flow_output(
        agent_id="tsv_formatter",
        completed_steps=[
            {
                "step": 1,
                "extraction_result_id": "extract-generic-1",
                "agent_id": "pdf_extraction",
                "agent_name": "General PDF Extraction Agent",
                "output_preview": "Extracted genetic reagents.",
                "candidate": executor.ExtractionEnvelopeCandidate(
                    agent_key="pdf_extraction",
                    payload_json=payload,
                    candidate_count=2,
                    adapter_key="generic",
                    conversation_summary="Extracted two generic reagents.",
                ),
            }
        ],
        flow_name="PDF TSV Flow",
    )

    assert "artifact_preview" not in save_calls[0]["columns"]
    assert "object_payload_label" in save_calls[0]["columns"]
    assert "object_payload_source" in save_calls[0]["columns"]
    assert "object_payload_source_identifier" in save_calls[0]["columns"]
    assert "object_payload_count" in save_calls[0]["columns"]
    assert [row["object_payload_label"] for row in save_calls[0]["data"]] == [
        "Ck:GFP",
        "Actn RNAi",
    ]
    assert save_calls[0]["data"][0]["object_payload_source"] == "This study"
    assert save_calls[0]["data"][0]["object_payload_source_identifier"] == "New in paper"
    assert save_calls[0]["data"][0]["object_payload_count"] == "4"


@pytest.mark.asyncio
async def test_tsv_formatter_applies_explicit_multi_source_export_plan(monkeypatch):
    executor = _executor_module()
    save_calls = []
    completed_steps = [
        _completed_source_step(
            executor,
            step=1,
            agent_id="gene_extractor",
            adapter_key="gene",
            object_type="Gene",
            object_id="gene-1",
            payload={"symbol": "BRCA1", "primary_external_id": "TEST:GENE001"},
            metadata={"flow_id": "flow-1", "step": 1, "tool_name": "ask_gene"},
        ),
        _completed_source_step(
            executor,
            step=2,
            agent_id="allele_extractor",
            adapter_key="allele",
            object_type="Allele",
            object_id="allele-1",
            payload={"allele_symbol": "brca1[tm1]", "primary_external_id": "TEST:ALLELE001"},
            metadata={"flow_id": "flow-1", "step": 2, "tool_name": "ask_allele"},
        ),
    ]

    async def _fake_save_tsv_impl(
        data_json: str,
        filename: str,
        columns: str | None = None,
    ) -> dict:
        save_calls.append(
            {
                "data": json.loads(data_json),
                "filename": filename,
                "columns": json.loads(columns or "[]"),
            }
        )
        return {
            "file_id": "file-multi-tsv",
            "filename": "multi.tsv",
            "format": "tsv",
            "size_bytes": 1234,
            "mime_type": "text/tab-separated-values",
            "download_url": "/api/files/file-multi-tsv/download",
            "created_at": "2026-06-11T00:00:00Z",
        }

    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools._save_tsv_impl",
        _fake_save_tsv_impl,
    )

    with pytest.raises(
        executor.FlowTerminalOutputProjectionError,
        match="Multiple canonical extraction sources",
    ):
        await executor._try_project_terminal_flow_output(
            agent_id="tsv_formatter",
            completed_steps=completed_steps,
            flow_name="Multi TSV Flow",
        )

    await executor._try_project_terminal_flow_output(
        agent_id="tsv_formatter",
        completed_steps=completed_steps,
        flow_name="Multi TSV Flow",
        projection_plan={
            "format": "tsv",
            "row_source": "object",
            "row_strategy": "object_ledger",
            "source_keys": [
                "flow-1:1:ask_gene:gene_extractor",
                "flow-1:2:ask_allele:allele_extractor",
            ],
        },
    )

    assert [row["source_key"] for row in save_calls[0]["data"]] == [
        "flow-1:1:ask_gene:gene_extractor",
        "flow-1:2:ask_allele:allele_extractor",
    ]


@pytest.mark.asyncio
async def test_csv_formatter_flow_output_saves_object_projection_without_model_round_trip(
    monkeypatch,
):
    executor = _executor_module()
    save_calls = []

    async def _fake_save_csv_impl(
        data_json: str,
        filename: str,
        columns: str | None = None,
    ) -> dict:
        save_calls.append(
            {
                "data": json.loads(data_json),
                "filename": filename,
                "columns": json.loads(columns or "[]"),
            }
        )
        return {
            "file_id": "file-artifact-csv",
            "filename": "flow_artifacts.csv",
            "format": "csv",
            "size_bytes": 1234,
            "mime_type": "text/csv",
            "download_url": "/api/files/file-artifact-csv/download",
            "created_at": "2026-04-26T00:00:00Z",
        }

    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools._save_csv_impl",
        _fake_save_csv_impl,
    )

    async def _unexpected_projection_planner(**kwargs):
        raise AssertionError("Default terminal exports should not invoke the planner")

    monkeypatch.setattr(
        executor,
        "_run_output_projection_planner",
        _unexpected_projection_planner,
    )

    result_text = await executor._try_project_terminal_flow_output(
        agent_id="csv_output_formatter",
        completed_steps=[_completed_artifact_step()],
        flow_name="CSV Flow",
    )

    result = json.loads(result_text or "{}")
    assert result["format"] == "csv"
    assert save_calls[0]["filename"] == "CSV_Flow_csv_export"
    assert save_calls[0]["columns"][:4] == [
        "adapter_key",
        "object_object_type",
        "object_status",
        "object_payload_symbol",
    ]
    assert save_calls[0]["data"][0]["object_payload_symbol"] == "TP53"


@pytest.mark.asyncio
async def test_csv_formatter_flow_output_applies_validated_projection_plan(monkeypatch):
    executor = _executor_module()
    save_calls = []

    async def _fake_save_csv_impl(
        data_json: str,
        filename: str,
        columns: str | None = None,
    ) -> dict:
        save_calls.append(
            {
                "data": json.loads(data_json),
                "columns": json.loads(columns or "[]"),
            }
        )
        return {
            "file_id": "file-custom-csv",
            "filename": "custom.csv",
            "format": "csv",
            "size_bytes": 1234,
            "mime_type": "text/csv",
            "download_url": "/api/files/file-custom-csv/download",
            "created_at": "2026-04-26T00:00:00Z",
        }

    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools._save_csv_impl",
        _fake_save_csv_impl,
    )

    await executor._try_project_terminal_flow_output(
        agent_id="csv_formatter",
        completed_steps=[_completed_artifact_step()],
        flow_name="CSV Flow",
        projection_plan={
            "format": "csv",
            "row_source": "object",
            "filters": [
                {
                    "field_ref": "object.payload.symbol",
                    "op": "eq",
                    "value": "BRCA1",
                }
            ],
            "columns": [
                {
                    "key": "gene_symbol",
                    "header": "Gene Symbol",
                    "field_ref": "object.payload.symbol",
                }
            ],
        },
    )

    assert save_calls[0]["columns"] == ["gene_symbol"]
    assert save_calls[0]["data"] == [{"gene_symbol": "BRCA1"}]


@pytest.mark.asyncio
async def test_json_formatter_flow_output_saves_runtime_generated_json(monkeypatch):
    executor = _executor_module()
    save_calls = []

    async def _fake_save_json_impl(
        data_json: str,
        filename: str,
        pretty: bool = True,
    ) -> dict:
        save_calls.append(
            {
                "data": json.loads(data_json),
                "filename": filename,
                "pretty": pretty,
            }
        )
        return {
            "file_id": "file-artifact-json",
            "filename": "flow_artifacts.json",
            "format": "json",
            "size_bytes": 1234,
            "mime_type": "application/json",
            "download_url": "/api/files/file-artifact-json/download",
            "created_at": "2026-04-26T00:00:00Z",
        }

    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools._save_json_impl",
        _fake_save_json_impl,
    )

    result_text = await executor._try_project_terminal_flow_output(
        agent_id="json_formatter",
        completed_steps=[_completed_artifact_step()],
        flow_name="JSON Flow",
    )

    result = json.loads(result_text or "{}")
    assert result["format"] == "json"
    assert save_calls[0]["filename"] == "JSON_Flow_json_export"
    assert save_calls[0]["data"][0]["object_payload_symbol"] == "TP53"


@pytest.mark.asyncio
async def test_chat_output_formatter_flow_output_renders_runtime_chat_table():
    executor = _executor_module()

    result_text = await executor._try_project_terminal_flow_output(
        agent_id="chat_output_formatter",
        completed_steps=[_completed_artifact_step()],
        flow_name="Chat Flow",
    )

    assert result_text is not None
    assert "| Adapter | Object Type | Status | Symbol |" in result_text
    assert "TP53" in result_text
    assert "BRCA1" in result_text


@pytest.mark.asyncio
async def test_csv_formatter_custom_instructions_use_projection_planner(monkeypatch):
    executor = _executor_module()
    save_calls = []
    planner_calls = []

    async def _fake_save_csv_impl(
        data_json: str,
        filename: str,
        columns: str | None = None,
    ) -> dict:
        save_calls.append(
            {
                "data": json.loads(data_json),
                "columns": json.loads(columns or "[]"),
            }
        )
        return {
            "file_id": "file-planned-csv",
            "filename": "planned.csv",
            "format": "csv",
            "size_bytes": 1234,
            "mime_type": "text/csv",
            "download_url": "/api/files/file-planned-csv/download",
            "created_at": "2026-04-26T00:00:00Z",
        }

    async def _fake_projection_planner(**kwargs):
        planner_calls.append(kwargs)
        plan = executor.FlowOutputProjectionPlan(
            format=kwargs["output_format"],
            row_source="object",
            filters=[
                {
                    "field_ref": "object.payload.symbol",
                    "op": "eq",
                    "value": "BRCA1",
                }
            ],
            columns=[
                {
                    "key": "gene_symbol",
                    "header": "Gene Symbol",
                    "field_ref": "object.payload.symbol",
                }
            ],
        )
        return executor.finalize_output_projection(kwargs["bundle"], plan)

    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools._save_csv_impl",
        _fake_save_csv_impl,
    )
    monkeypatch.setattr(
        executor,
        "_run_output_projection_planner",
        _fake_projection_planner,
    )

    await executor._try_project_terminal_flow_output(
        agent_id="csv_output_formatter",
        completed_steps=[_completed_artifact_step()],
        flow_name="CSV Flow",
        agent_name="CSV Formatter",
        node_data={
            "custom_instructions": "Only include BRCA1 and call the column Gene Symbol."
        },
        resolved_query="Create the final CSV export.",
    )

    assert len(planner_calls) == 1
    assert planner_calls[0]["agent_id"] == "csv_output_formatter"
    assert planner_calls[0]["agent_name"] == "CSV Formatter"
    assert save_calls[0]["columns"] == ["gene_symbol"]
    assert save_calls[0]["data"] == [{"gene_symbol": "BRCA1"}]


@pytest.mark.asyncio
async def test_curator_run_request_customization_uses_projection_planner(monkeypatch):
    executor = _executor_module()
    save_calls = []
    planner_calls = []

    async def _fake_save_csv_impl(
        data_json: str,
        filename: str,
        columns: str | None = None,
    ) -> dict:
        save_calls.append(
            {
                "data": json.loads(data_json),
                "columns": json.loads(columns or "[]"),
            }
        )
        return {
            "file_id": "file-user-query-csv",
            "filename": "user-query.csv",
            "format": "csv",
            "size_bytes": 1234,
            "mime_type": "text/csv",
            "download_url": "/api/files/file-user-query-csv/download",
            "created_at": "2026-04-26T00:00:00Z",
        }

    async def _fake_projection_planner(**kwargs):
        planner_calls.append(kwargs)
        plan = executor.FlowOutputProjectionPlan(
            format=kwargs["output_format"],
            row_source="object",
            columns=[
                {
                    "key": "gene_symbol",
                    "header": "Gene Symbol",
                    "field_ref": "object.payload.symbol",
                }
            ],
        )
        return executor.finalize_output_projection(kwargs["bundle"], plan)

    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools._save_csv_impl",
        _fake_save_csv_impl,
    )
    monkeypatch.setattr(
        executor,
        "_run_output_projection_planner",
        _fake_projection_planner,
    )

    await executor._try_project_terminal_flow_output(
        agent_id="csv_output_formatter",
        completed_steps=[_completed_artifact_step()],
        flow_name="CSV Flow",
        agent_name="CSV Formatter",
        node_data={},
        resolved_query=(
            "Flow task:\nExtract key curation-ready findings.\n\n"
            "Curator run request:\nDownload this as CSV, but call the first column Gene Symbol.\n\n"
            "Runtime artifact policy:\nThe runtime stores completed artifacts separately."
        ),
    )

    assert len(planner_calls) == 1
    assert save_calls[0]["columns"] == ["gene_symbol"]
    assert save_calls[0]["data"] == [
        {"gene_symbol": "TP53"},
        {"gene_symbol": "BRCA1"},
    ]


def test_curator_run_request_reorder_language_uses_projection_planner():
    executor = _executor_module()
    bundle = executor.build_flow_output_artifact_bundle(
        completed_steps=[_completed_artifact_step()],
        flow_name="Planner Trigger Flow",
        output_format="csv",
    )

    assert executor._flow_output_should_run_projection_planner(
        bundle=bundle,
        output_format="csv",
        node_data={},
        resolved_query=(
            "Flow task:\nExtract key curation-ready findings.\n\n"
            "Curator run request:\nDownload CSV with FlyBase IDs before symbols.\n\n"
            "Runtime artifact policy:\nThe runtime stores completed artifacts separately."
        ),
    )


def test_output_projection_planner_tool_surface_excludes_file_savers():
    executor = _executor_module()
    bundle = executor.build_flow_output_artifact_bundle(
        completed_steps=[_completed_artifact_step()],
        flow_name="Planner Tool Flow",
        output_format="csv",
    )
    state = executor._FlowOutputProjectionPlannerState()

    tools = executor._build_output_projection_planner_tools(
        bundle=bundle,
        output_format="csv",
        state=state,
    )

    tool_names = {getattr(tool, "name", "") for tool in tools}
    assert tool_names == executor._FLOW_OUTPUT_PROJECTION_PLANNER_TOOL_NAMES
    assert all(not name.startswith("save_") for name in tool_names)


def _patch_projection_planner_runtime(monkeypatch, executor):
    class FakeAgent:
        def __init__(self, **kwargs):
            self.name = kwargs["name"]
            self.instructions = kwargs["instructions"]
            self.model = kwargs["model"]
            self.model_settings = kwargs["model_settings"]
            self.tools = kwargs["tools"]

    monkeypatch.setattr(executor, "Agent", FakeAgent)
    monkeypatch.setattr(
        executor,
        "get_agent_config",
        lambda agent_id: SimpleNamespace(
            model="configured-test-model",
            temperature=0.0,
            reasoning=None,
            tool_choice="auto",
        ),
    )
    monkeypatch.setattr(
        executor,
        "resolve_model_provider",
        lambda model, provider_override=None: "test-provider",
    )
    monkeypatch.setattr(
        executor,
        "get_model_for_agent",
        lambda model, provider_override=None: f"resolved:{model}:{provider_override}",
    )
    monkeypatch.setattr(
        executor,
        "build_model_settings",
        lambda **kwargs: {"settings": kwargs},
    )
    monkeypatch.setattr(executor, "get_max_turns", lambda: 60)


async def _invoke_projection_tool(tool, payload: dict) -> dict:
    tool_ctx = SimpleNamespace(tool_name=getattr(tool, "name", "tool"))
    raw_result = await tool.on_invoke_tool(tool_ctx, json.dumps(payload))
    return json.loads(raw_result)


@pytest.mark.asyncio
async def test_projection_planner_retries_after_invalid_finalization(monkeypatch):
    executor = _executor_module()
    _patch_projection_planner_runtime(monkeypatch, executor)
    bundle = executor.build_flow_output_artifact_bundle(
        completed_steps=[_completed_artifact_step()],
        flow_name="Planner Retry Flow",
        output_format="csv",
    )
    default_plan = executor.default_projection_plan(bundle, output_format="csv")
    run_calls = []

    async def _fake_run(agent, run_input, **kwargs):
        run_calls.append(
            {
                "input": run_input,
                "max_turns": kwargs.get("max_turns"),
                "tool_names": [getattr(tool, "name", "") for tool in agent.tools],
                "model": agent.model,
            }
        )
        finalize_tool = next(
            tool
            for tool in agent.tools
            if getattr(tool, "name", "") == "finalize_output_projection"
        )
        if len(run_calls) == 1:
            response = await _invoke_projection_tool(
                finalize_tool,
                {
                    "plan_json": json.dumps(
                        {
                            "format": "csv",
                            "row_source": "object",
                            "columns": [
                                {
                                    "key": "missing",
                                    "field_ref": "object.payload.not_a_field",
                                }
                            ],
                        }
                    )
                },
            )
            assert response["status"] == "invalid"
        else:
            response = await _invoke_projection_tool(
                finalize_tool,
                {
                    "plan_json": json.dumps(
                        {
                            "format": "csv",
                            "row_source": "object",
                            "filters": [
                                {
                                    "field_ref": "object.payload.symbol",
                                    "op": "eq",
                                    "value": "BRCA1",
                                }
                            ],
                            "columns": [
                                {
                                    "key": "gene_symbol",
                                    "field_ref": "object.payload.symbol",
                                }
                            ],
                        }
                    )
                },
            )
            assert response["status"] == "ok"
        return SimpleNamespace(final_output="done")

    monkeypatch.setattr(executor.Runner, "run", _fake_run)

    result = await executor._run_output_projection_planner(
        bundle=bundle,
        output_format="csv",
        default_plan=default_plan,
        agent_id="csv_output_formatter",
        agent_name="CSV Formatter",
        node_data={"custom_instructions": "Only export BRCA1."},
        resolved_query="Create the final CSV.",
    )

    assert len(run_calls) == 2
    # Planner uses the standard agent turn budget (get_max_turns(), patched to 60
    # in this fixture), not a tight clamp — an 8-turn cap exhausted before
    # finalizing richer multi-column bundles. See hotfix 0.7.3.
    assert run_calls[0]["max_turns"] == 60
    assert run_calls[0]["model"] == "resolved:configured-test-model:test-provider"
    assert run_calls[0]["tool_names"] == [
        "inspect_output_artifacts",
        "preview_output_projection",
        "finalize_output_projection",
    ]
    assert "object.payload.not_a_field" in run_calls[1]["input"]
    assert result.rows == [{"gene_symbol": "BRCA1"}]


@pytest.mark.asyncio
async def test_projection_planner_fails_after_two_runs_without_finalize(monkeypatch):
    executor = _executor_module()
    _patch_projection_planner_runtime(monkeypatch, executor)
    bundle = executor.build_flow_output_artifact_bundle(
        completed_steps=[_completed_artifact_step()],
        flow_name="Planner Failure Flow",
        output_format="csv",
    )
    default_plan = executor.default_projection_plan(bundle, output_format="csv")
    run_calls = []

    async def _fake_run(agent, run_input, **kwargs):
        run_calls.append(run_input)
        return SimpleNamespace(final_output="I forgot to call the tool")

    monkeypatch.setattr(executor.Runner, "run", _fake_run)

    with pytest.raises(RuntimeError, match="did not finalize a valid plan"):
        await executor._run_output_projection_planner(
            bundle=bundle,
            output_format="csv",
            default_plan=default_plan,
            agent_id="csv_output_formatter",
            agent_name="CSV Formatter",
            node_data={"custom_instructions": "Only export BRCA1."},
            resolved_query="Create the final CSV.",
        )

    assert len(run_calls) == 2
