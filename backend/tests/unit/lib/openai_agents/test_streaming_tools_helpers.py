"""Focused helper tests for streaming_tools core runtime behavior."""

import uuid
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src.lib.openai_agents import streaming_tools
from src.lib.prompts.context import (
    bind_prompt_run,
    clear_prompt_context,
    commit_pending_prompts,
    get_used_prompt_runs,
    set_pending_prompts,
)
from src.models.sql.prompts import PromptTemplate


class _Envelope(BaseModel):
    value: str


@pytest.fixture(autouse=True)
def _reset_streaming_state():
    clear_prompt_context()
    streaming_tools.reset_consecutive_call_tracker()
    streaming_tools.clear_collected_events()
    streaming_tools.set_live_event_list(None)
    yield
    clear_prompt_context()
    streaming_tools.reset_consecutive_call_tracker()
    streaming_tools.clear_collected_events()
    streaming_tools.set_live_event_list(None)


def test_extract_model_identifier_handles_string_and_object():
    assert streaming_tools._extract_model_identifier("gpt-4o") == "gpt-4o"
    assert streaming_tools._extract_model_identifier(SimpleNamespace(model=" groq/llama ")) == "groq/llama"
    assert streaming_tools._extract_model_identifier(SimpleNamespace()) == ""


def test_build_json_only_instruction_includes_schema_when_available():
    text = streaming_tools._build_json_only_instruction(_Envelope)
    assert "IMPORTANT OUTPUT FORMAT REQUIREMENT" in text
    assert "model_json_schema" not in text
    assert "value" in text


def test_build_json_only_instruction_without_schema():
    text = streaming_tools._build_json_only_instruction(None)
    assert "IMPORTANT OUTPUT FORMAT REQUIREMENT" in text
    assert "schema exactly" not in text.lower()


def test_runtime_instruction_append_updates_pending_prompt_assembly():
    clear_prompt_context()
    prompt = PromptTemplate(
        id=uuid.uuid4(),
        agent_name="gene",
        prompt_type="system",
        content="base",
        version=1,
        is_active=True,
    )
    source_agent = SimpleNamespace(name="Gene Specialist", instructions="base")
    prompt_run_id = set_pending_prompts(
        "Gene Specialist",
        [prompt],
        effective_prompt_hash="hash-1",
        layer_manifest={
            "agent_id": "gene",
            "layers": [
                {
                    "id": "gene:base_prompt",
                    "kind": "base_prompt",
                    "title": "Editable base prompt",
                    "content": "base",
                    "provenance": "prompt_template:system",
                    "editable": True,
                    "locked": False,
                    "source_ref": "prompt_templates:active:gene:system:base:v1",
                    "hash": "base-layer-hash",
                }
            ],
            "hash": "hash-1",
        },
    )
    bind_prompt_run(source_agent, prompt_run_id)

    runtime_agent = streaming_tools._append_agent_runtime_instruction(
        source_agent,
        source_agent,
        instruction="Runtime-only instruction.",
        layer_id_suffix="runtime_test",
        title="Runtime test instruction",
        source_ref="test:runtime_instruction",
    )
    commit_pending_prompts(runtime_agent)

    assert runtime_agent is not source_agent
    assert runtime_agent.instructions == "base\n\nRuntime-only instruction."
    used_run = get_used_prompt_runs()[0]
    assert used_run.assembly is not None
    assert used_run.assembly.effective_prompt_hash != "hash-1"
    assert used_run.assembly.layer_manifest["layers"][-1]["id"] == (
        "gene:runtime_context:runtime_test"
    )
    assert used_run.assembly.layer_manifest["layers"][-1]["content"] == (
        "Runtime-only instruction."
    )


def test_runtime_instruction_append_does_not_accumulate_on_reused_source_agent():
    clear_prompt_context()
    prompt = PromptTemplate(
        id=uuid.uuid4(),
        agent_name="gene",
        prompt_type="system",
        content="base",
        version=1,
        is_active=True,
    )
    source_agent = SimpleNamespace(name="Gene Specialist", instructions="base")
    prompt_run_id = set_pending_prompts(
        "Gene Specialist",
        [prompt],
        effective_prompt_hash="hash-1",
        layer_manifest={
            "agent_id": "gene",
            "layers": [
                {
                    "id": "gene:base_prompt",
                    "kind": "base_prompt",
                    "title": "Editable base prompt",
                    "content": "base",
                    "provenance": "prompt_template:system",
                    "editable": True,
                    "locked": False,
                    "source_ref": "prompt_templates:active:gene:system:base:v1",
                    "hash": "base-layer-hash",
                }
            ],
            "hash": "hash-1",
        },
    )
    bind_prompt_run(source_agent, prompt_run_id)

    first_runtime_agent = streaming_tools._append_agent_runtime_instruction(
        source_agent,
        source_agent,
        instruction="first runtime",
        layer_id_suffix="first",
        title="First runtime",
        source_ref="test:first_runtime",
    )
    commit_pending_prompts(first_runtime_agent)

    second_runtime_agent = streaming_tools._append_agent_runtime_instruction(
        source_agent,
        source_agent,
        instruction="second runtime",
        layer_id_suffix="second",
        title="Second runtime",
        source_ref="test:second_runtime",
    )
    commit_pending_prompts(second_runtime_agent)

    used_runs = get_used_prompt_runs()
    first_layers = used_runs[0].assembly.layer_manifest["layers"]
    second_layers = used_runs[1].assembly.layer_manifest["layers"]

    assert [layer["id"] for layer in first_layers] == [
        "gene:base_prompt",
        "gene:runtime_context:first",
    ]
    assert [layer["id"] for layer in second_layers] == [
        "gene:base_prompt",
        "gene:runtime_context:second",
    ]
    assert first_layers[-1]["content"] == "first runtime"
    assert second_layers[-1]["content"] == "second runtime"


def test_extract_tool_name_prefers_name_then_tool_name():
    assert streaming_tools._extract_tool_name(SimpleNamespace(name="search_document")) == "search_document"
    assert streaming_tools._extract_tool_name(SimpleNamespace(tool_name="agr_curation_query")) == "agr_curation_query"
    assert streaming_tools._extract_tool_name(SimpleNamespace()) == ""


def test_required_tool_names_for_agent_returns_agr_when_only_agr_tool_present():
    agent = SimpleNamespace(tools=[SimpleNamespace(name="agr_curation_query")])
    assert streaming_tools._required_tool_names_for_agent(agent) == {"agr_curation_query"}


def test_agent_tool_names_normalizes_known_tools():
    agent = SimpleNamespace(
        tools=[
            SimpleNamespace(name="search_document"),
            SimpleNamespace(tool_name="read_section"),
            SimpleNamespace(name="  "),
        ]
    )
    assert streaming_tools._agent_tool_names(agent) == {"search_document", "read_section"}


def test_estimate_bulk_entity_count_filters_noise_and_deduplicates():
    query = """
    Query: validate genes
    List:
    daf-16, lin-3, daf-16, , notes: ignore this, unc-54
    """
    assert streaming_tools._estimate_bulk_entity_count(query) == 3


def test_build_tool_efficiency_instruction_only_for_large_agr_lists():
    agr_agent = SimpleNamespace(tools=[SimpleNamespace(name="agr_curation_query")])
    non_agr_agent = SimpleNamespace(tools=[SimpleNamespace(name="search_document")])
    small_query = "List: a, b, c"
    large_query = "List: " + ", ".join(f"gene_{idx}" for idx in range(10))

    assert streaming_tools._build_tool_efficiency_instruction(non_agr_agent, large_query) == ""
    assert streaming_tools._build_tool_efficiency_instruction(agr_agent, small_query) == ""
    assert "TOOL EFFICIENCY REQUIREMENT" in streaming_tools._build_tool_efficiency_instruction(agr_agent, large_query)


def test_consecutive_tracker_and_batching_nudge_generation(monkeypatch):
    streaming_tools.reset_consecutive_call_tracker()
    monkeypatch.setattr(
        streaming_tools,
        "get_batching_config",
        lambda: {
            "ask_gene_specialist": {
                "entity": "genes",
                "example": 'ask_gene_specialist("Look up these genes: daf-16, lin-3")',
            }
        },
    )

    assert streaming_tools._track_specialist_call("ask_gene_specialist") == 1
    assert streaming_tools._generate_batching_nudge("ask_gene_specialist", 1) is None
    assert streaming_tools._track_specialist_call("ask_gene_specialist") == 2
    nudge = streaming_tools._generate_batching_nudge("ask_gene_specialist", 3)
    assert nudge is not None
    assert "individual genes" in nudge


def test_collected_events_and_live_list_modes():
    streaming_tools.clear_collected_events()
    streaming_tools.set_live_event_list(None)

    event_a = {"type": "TOOL_START"}
    streaming_tools.add_specialist_event(event_a)
    assert streaming_tools.get_collected_events() == [event_a]

    live = []
    streaming_tools.set_live_event_list(live)
    event_b = {"type": "TOOL_COMPLETE"}
    streaming_tools.add_specialist_event(event_b)
    assert live == [event_b]
    assert streaming_tools.get_collected_events() == [event_a]

    streaming_tools.set_live_event_list(None)


def test_emit_chunk_provenance_from_search_document_emits_events(monkeypatch):
    emitted = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", emitted.append)

    output = {
        "hits": [
            {"chunk_id": "chunk-1", "doc_items": [{"page": 1}]},
            {"chunk_id": "chunk-2", "page_number": 2},
        ]
    }
    streaming_tools._emit_chunk_provenance_from_output("search_document", output)

    assert len(emitted) == 2
    assert emitted[0]["type"] == "CHUNK_PROVENANCE"
    assert emitted[0]["chunk_id"] == "chunk-1"
    assert emitted[1]["doc_items"] == [{"page": 2}]


def test_emit_chunk_provenance_from_read_section_emits_when_doc_items_present(monkeypatch):
    emitted = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", emitted.append)

    output = {
        "section": {
            "section_title": "Methods",
            "doc_items": [{"page": 3, "bbox": [0, 0, 1, 1]}],
        }
    }
    streaming_tools._emit_chunk_provenance_from_output("read_section", output)

    assert len(emitted) == 1
    assert emitted[0]["chunk_id"] == "section:Methods"


def test_emit_chunk_provenance_handles_invalid_json_string_gracefully():
    # Should not raise
    streaming_tools._emit_chunk_provenance_from_output("search_document", "{bad json")


def test_required_tool_failure_message_for_document_tools():
    agent = SimpleNamespace(tools=[SimpleNamespace(name="search_document")])
    msg = streaming_tools._required_tool_failure_message(
        agent=agent,
        specialist_name="PDF Specialist",
        tool_calls=[SimpleNamespace(tool_name="read_metadata")],
    )
    assert msg is not None
    assert "required document tools" in msg
