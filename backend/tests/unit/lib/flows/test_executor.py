"""Tests for flow executor custom_instructions wiring."""
import asyncio
import importlib
import json
from types import SimpleNamespace
from uuid import UUID
import pytest
from unittest.mock import MagicMock, patch

from agents import Agent, ModelSettings, function_tool

def _executor_module():
    """Load flow executor lazily so monkeypatches target the active module instance."""
    return importlib.import_module("src.lib.flows.executor")


def _count_agent_ids(*args, **kwargs):
    return _executor_module()._count_agent_ids(*args, **kwargs)


def flow_requires_document(*args, **kwargs):
    return _executor_module().flow_requires_document(*args, **kwargs)


def get_all_agent_tools(*args, **kwargs):
    return _executor_module().get_all_agent_tools(*args, **kwargs)


def build_supervisor_instructions(*args, **kwargs):
    return _executor_module().build_supervisor_instructions(*args, **kwargs)


def create_flow_supervisor(*args, **kwargs):
    return _executor_module().create_flow_supervisor(*args, **kwargs)


async def execute_flow(*args, **kwargs):
    async for event in _executor_module().execute_flow(*args, **kwargs):
        yield event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_flow(nodes):
    """Create a mock CurationFlow with the given nodes list."""
    flow = MagicMock()
    flow.flow_definition = {"nodes": nodes}
    flow.name = "Test Flow"
    flow.id = "11111111-1111-1111-1111-111111111111"
    return flow


def _agent_node(
    node_id,
    agent_id,
    custom_instructions=None,
    step_goal=None,
    display_name=None,
    include_evidence=None,
    input_source="previous_output",
    custom_input=None,
    output_key=None,
    output_filename_template=None,
):
    """Build a minimal agent node dict."""
    data = {
        "agent_id": agent_id,
        "agent_display_name": display_name or agent_id.title(),
        "input_source": input_source,
        "output_key": output_key or f"{node_id}_out",
    }
    if custom_instructions is not None:
        data["custom_instructions"] = custom_instructions
    if step_goal is not None:
        data["step_goal"] = step_goal
    if include_evidence is not None:
        data["include_evidence"] = include_evidence
    if custom_input is not None:
        data["custom_input"] = custom_input
    if output_filename_template is not None:
        data["output_filename_template"] = output_filename_template
    return {
        "id": node_id,
        "type": "agent",
        "position": {"x": 0, "y": 0},
        "data": data,
    }


def _task_input_node(task_instructions="Do the thing", output_key="task_out"):
    """Build a task_input node dict."""
    return {
        "id": "node_task",
        "type": "task_input",
        "position": {"x": 0, "y": 0},
        "data": {
            "agent_id": "task_input",
            "agent_display_name": "Task Input",
            "output_key": output_key,
            "task_instructions": task_instructions,
        },
    }


def _make_evidence_record(
    entity: str,
    *,
    verified_quote: str,
    page: int = 1,
    section: str = "Results",
    chunk_id: str = "chunk-1",
):
    """Build a normalized evidence-record fixture."""

    return {
        "entity": entity,
        "verified_quote": verified_quote,
        "page": page,
        "section": section,
        "chunk_id": chunk_id,
    }


def _structured_step_output(
    label: str,
    *,
    actor: str = "gene_expression_specialist",
    destination: str = "gene_expression",
    evidence_records=None,
):
    """Build a minimal structured extraction payload with optional evidence."""

    return {
        "actor": actor,
        "destination": destination,
        "confidence": 0.9,
        "reasoning": "done",
        "items": [{"label": label}],
        "raw_mentions": [],
        "exclusions": [],
        "ambiguities": [],
        "evidence_records": list(evidence_records or []),
        "run_summary": {
            "candidate_count": 1,
            "kept_count": 1,
            "excluded_count": 0,
            "ambiguous_count": 0,
            "warnings": [],
        },
    }


def _make_completed_step(
    *,
    agent_id: str,
    agent_name: str,
    tool_name: str,
    step: int,
    adapter_key: str,
    payload: dict,
    conversation_summary: str = "Extract findings",
    evidence_records=None,
):
    """Build one completed-step entry matching flow executor state."""

    step_evidence_records = list(evidence_records or [])
    return {
        "step": step,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "tool_name": tool_name,
        "output": json.dumps(payload),
        "output_preview": json.dumps(payload),
        "candidate": _executor_module().ExtractionEnvelopeCandidate(
            agent_key=agent_id,
            payload_json=payload,
            candidate_count=payload.get("run_summary", {}).get("candidate_count", 0),
            adapter_key=adapter_key,
            conversation_summary=conversation_summary,
            metadata={
                "tool_name": tool_name,
                "flow_id": "11111111-1111-1111-1111-111111111111",
                "flow_name": "Test Flow",
                "step": step,
                "agent_name": agent_name,
            },
        ),
        "evidence_records": step_evidence_records,
        "evidence_count": len(step_evidence_records),
    }


def _make_flow_execution_state(*completed_steps):
    """Build executor flow state with evidence registry populated from steps."""

    registry = _executor_module()._EvidenceRegistry()
    for step in completed_steps:
        registry.add_many(step.get("evidence_records") or [])
    return {
        "completed_steps": list(completed_steps),
        "evidence_registry": registry,
    }


# ===========================================================================
# _count_agent_ids
# ===========================================================================


class TestCountAgentIds:
    """Tests for counting agent_id occurrences in flow nodes."""

    def test_single_agents(self):
        flow = _make_flow([
            _agent_node("n1", "gene"),
            _agent_node("n2", "disease"),
        ])
        assert _count_agent_ids(flow) == {"gene": 1, "disease": 1}

    def test_duplicate_agents(self):
        flow = _make_flow([
            _agent_node("n1", "gene"),
            _agent_node("n2", "disease"),
            _agent_node("n3", "gene"),
        ])
        assert _count_agent_ids(flow) == {"gene": 2, "disease": 1}

    def test_skips_task_input(self):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene"),
        ])
        result = _count_agent_ids(flow)
        assert "task_input" not in result
        assert result == {"gene": 1}

    def test_empty_flow(self):
        flow = _make_flow([])
        assert _count_agent_ids(flow) == {}


class TestFlowTemplateHelpers:
    """Tests the flow template rendering helpers used by the executor."""

    def test_build_initial_flow_template_variables_use_task_input_instructions(self):
        flow = _make_flow([
            _task_input_node(
                "Curator-authored task instructions",
                output_key="task_input_text",
            ),
        ])

        variables = _executor_module()._build_initial_flow_template_variables(flow)

        assert variables == {"task_input_text": "Curator-authored task instructions"}

    def test_build_flow_template_variables_uses_safe_built_in_defaults(self):
        variables = _executor_module()._build_flow_template_variables(
            stored_variables={},
            document_name=None,
            flow_run_id=None,
            timestamp="20260410T120000Z",
        )

        assert variables["input_filename"] == "input"
        assert variables["input_filename_stem"] == "input"
        assert variables["trace_id"] == "trace"
        assert variables["timestamp"] == "20260410T120000Z"

    def test_render_flow_template_replaces_missing_variables_with_empty_string(self):
        rendered = _executor_module()._render_flow_template(
            "Use {{known}} and {{missing}}.",
            {"known": "alpha"},
        )

        assert rendered == "Use alpha and ."

    def test_resolve_output_filename_descriptor_sanitizes_and_falls_back(self):
        resolved = _executor_module()._resolve_output_filename_descriptor(
            output_filename_template="{{input_filename_stem}}.tsv",
            template_variables={"input_filename_stem": "Smith et al. (2024)"},
        )
        fallback = _executor_module()._resolve_output_filename_descriptor(
            output_filename_template="{{missing_variable}}",
            template_variables={},
        )

        assert resolved == "Smith_et_al_2024"
        assert fallback == "output"


# ===========================================================================
# get_all_agent_tools – per-node custom_instructions wiring
# ===========================================================================


MOCK_REGISTRY = {
    "gene": {
        "name": "Gene Specialist",
        "description": "Curate genes",
        "category": "Extraction",
        "subcategory": "Entity Extraction",
        "factory": lambda: None,
        "requires_document": False,
        "curation": {
            "adapter_key": "gene",
            "launchable": True,
        },
    },
    "disease": {
        "name": "Disease Specialist",
        "description": "Curate diseases",
        "category": "Validation",
        "subcategory": "Data Validation",
        "factory": lambda: None,
        "requires_document": False,
        "curation": {
            "adapter_key": "disease",
            "launchable": True,
        },
    },
    "gene-expression": {
        "name": "Gene Expression Specialist",
        "description": "Curate gene expression findings",
        "category": "Extraction",
        "subcategory": "Entity Extraction",
        "factory": lambda: None,
        "requires_document": False,
        "curation": {
            "adapter_key": "gene_expression",
            "launchable": True,
        },
    },
    "chat_output_formatter": {
        "name": "Chat Output Formatter",
        "description": "Format the final response",
        "category": "Output",
        "subcategory": "Formatter",
        "factory": lambda: None,
        "requires_document": False,
        "curation": {
            "adapter_key": "gene",
            "launchable": True,
        },
    },
    "curation_prep": {
        "name": "Curation Prep Agent",
        "description": "Prepare curation candidates",
        "category": "Curation",
        "subcategory": "Prep",
        "factory": lambda: None,
        "requires_document": True,
    },
}


def _metadata_from_registry(
    agent_id: str,
    registry: dict[str, dict[str, object]] = MOCK_REGISTRY,
):
    """Build get_agent_metadata-like payload from simple registry fixtures."""
    entry = registry.get(agent_id)
    if entry is None:
        raise ValueError(f"Unknown agent_id: {agent_id}")
    requires_document = bool(entry.get("requires_document", False))
    return {
        "agent_id": agent_id,
        "display_name": entry.get("name", agent_id),
        "description": entry.get("description", ""),
        "category": entry.get("category", ""),
        "subcategory": entry.get("subcategory", ""),
        "requires_document": requires_document,
        "required_params": ["document_id", "user_id"] if requires_document else [],
        "curation": entry.get("curation"),
    }


@pytest.fixture(autouse=True)
def _mock_executor_agent_metadata(monkeypatch):
    """Default test metadata source for flow agents under test."""
    monkeypatch.setattr(
        "src.lib.flows.executor.get_agent_metadata",
        lambda agent_id: _metadata_from_registry(agent_id),
    )


class TestDbUserIdPropagation:
    """Tests that DB user identity is forwarded through flow runtime resolution."""

    def test_flow_requires_document_forwards_db_user_id_to_metadata(self, monkeypatch):
        observed = []

        def _metadata(agent_id, **kwargs):
            observed.append(kwargs.get("db_user_id"))
            return {
                "agent_id": agent_id,
                "display_name": "PDF Specialist",
                "description": "Reads documents",
                "requires_document": True,
                "required_params": ["document_id", "user_id"],
            }

        monkeypatch.setattr("src.lib.flows.executor.get_agent_metadata", _metadata)

        flow = _make_flow([_agent_node("n1", "pdf_extraction")])
        assert flow_requires_document(flow, db_user_id=42) is True
        assert observed == [42]

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_get_all_agent_tools_forwards_db_user_id(
        self, mock_get_agent, mock_streaming, monkeypatch
    ):
        observed = []

        def _metadata(agent_id, **kwargs):
            observed.append(kwargs.get("db_user_id"))
            return {
                "agent_id": agent_id,
                "display_name": "Gene Specialist",
                "description": "Curate genes",
                "requires_document": False,
                "required_params": [],
            }

        monkeypatch.setattr("src.lib.flows.executor.get_agent_metadata", _metadata)
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([_agent_node("n1", "gene")])
        get_all_agent_tools(flow, db_user_id=77)

        assert observed == [77]
        assert mock_get_agent.call_args.kwargs.get("db_user_id") == 77


class TestGetAllAgentToolsCustomInstructions:
    """Tests that get_all_agent_tools prepends per-node custom_instructions."""

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_custom_instructions_prepended(self, mock_get_agent, mock_streaming):
        """Agent instructions should have custom instructions prepended."""
        base_prompt = "You are the gene specialist."
        mock_agent = MagicMock(spec=Agent)
        mock_agent.instructions = base_prompt
        mock_get_agent.return_value = mock_agent
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "gene", custom_instructions="Only curate C. elegans genes"),
        ])

        get_all_agent_tools(flow)

        assert mock_agent.instructions.startswith("## CUSTOM INSTRUCTIONS")
        assert "Only curate C. elegans genes" in mock_agent.instructions
        assert "HIGHEST PRIORITY" in mock_agent.instructions
        assert base_prompt in mock_agent.instructions
        # Custom instructions come before the base prompt
        custom_pos = mock_agent.instructions.index("Only curate C. elegans genes")
        base_pos = mock_agent.instructions.index(base_prompt)
        assert custom_pos < base_pos

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_no_custom_instructions_unchanged(self, mock_get_agent, mock_streaming):
        """Agent instructions should be unchanged when no custom_instructions."""
        base_prompt = "You are the gene specialist."
        mock_agent = MagicMock(spec=Agent)
        mock_agent.instructions = base_prompt
        mock_get_agent.return_value = mock_agent
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([_agent_node("n1", "gene")])

        get_all_agent_tools(flow)

        assert mock_agent.instructions == base_prompt

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_custom_instructions_only_affects_target_agent(self, mock_get_agent, mock_streaming):
        """Custom instructions for gene should not affect disease agent."""
        gene_agent = MagicMock(spec=Agent)
        gene_agent.instructions = "Gene base"
        disease_agent = MagicMock(spec=Agent)
        disease_agent.instructions = "Disease base"
        mock_get_agent.side_effect = lambda aid, **kw: (
            gene_agent if aid == "gene" else disease_agent
        )
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "gene", custom_instructions="Custom gene stuff"),
            _agent_node("n2", "disease"),
        ])

        get_all_agent_tools(flow)

        assert "Custom gene stuff" in gene_agent.instructions
        assert disease_agent.instructions == "Disease base"

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_custom_instructions_with_none_base(self, mock_get_agent, mock_streaming):
        """Should handle agent.instructions being None gracefully."""
        mock_agent = MagicMock(spec=Agent)
        mock_agent.instructions = None
        mock_get_agent.return_value = mock_agent
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "gene", custom_instructions="Override everything"),
        ])

        get_all_agent_tools(flow)

        assert "Override everything" in mock_agent.instructions
        assert "HIGHEST PRIORITY" in mock_agent.instructions

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_empty_custom_instructions_unchanged(self, mock_get_agent, mock_streaming):
        """Empty/whitespace custom instructions should not modify agent."""
        base_prompt = "You are the gene specialist."
        mock_agent = MagicMock(spec=Agent)
        mock_agent.instructions = base_prompt
        mock_get_agent.return_value = mock_agent
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([_agent_node("n1", "gene", custom_instructions="   ")])

        get_all_agent_tools(flow)

        assert mock_agent.instructions == base_prompt

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_include_evidence_guidance_prepended(self, mock_get_agent, mock_streaming):
        """include_evidence should reuse the existing step-local instruction prefix."""
        base_prompt = "You are the output specialist."
        mock_agent = MagicMock(spec=Agent)
        mock_agent.instructions = base_prompt
        mock_get_agent.return_value = mock_agent
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "chat_output_formatter", include_evidence=True),
        ])

        get_all_agent_tools(flow)

        assert mock_agent.instructions.startswith("## OUTPUT EVIDENCE REQUIREMENT")
        assert "include supporting evidence from earlier steps" in mock_agent.instructions
        assert base_prompt in mock_agent.instructions

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_custom_instructions_and_include_evidence_share_prefix(self, mock_get_agent, mock_streaming):
        """Custom instructions and evidence guidance should both prepend before the base prompt."""
        base_prompt = "You are the output specialist."
        mock_agent = MagicMock(spec=Agent)
        mock_agent.instructions = base_prompt
        mock_get_agent.return_value = mock_agent
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node(
                "n1",
                "chat_output_formatter",
                custom_instructions="Group results by species.",
                include_evidence=True,
            ),
        ])

        get_all_agent_tools(flow)

        assert mock_agent.instructions.startswith("## CUSTOM INSTRUCTIONS")
        assert "Group results by species." in mock_agent.instructions
        assert "## OUTPUT EVIDENCE REQUIREMENT" in mock_agent.instructions
        assert mock_agent.instructions.index("## OUTPUT EVIDENCE REQUIREMENT") < mock_agent.instructions.index(base_prompt)

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_output_formatter_defaults_include_evidence_when_flag_missing(self, mock_get_agent, mock_streaming):
        """Output/formatter steps should include evidence by default when the flag is absent."""
        base_prompt = "You are the output specialist."
        mock_agent = MagicMock(spec=Agent)
        mock_agent.instructions = base_prompt
        mock_get_agent.return_value = mock_agent
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "chat_output_formatter"),
        ])

        get_all_agent_tools(flow)

        assert mock_agent.instructions.startswith("## OUTPUT EVIDENCE REQUIREMENT")
        assert base_prompt in mock_agent.instructions

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_output_formatter_false_flag_excludes_evidence(self, mock_get_agent, mock_streaming):
        """Explicit false should prepend exclusion guidance for output/formatter steps."""
        base_prompt = "You are the output specialist."
        mock_agent = MagicMock(spec=Agent)
        mock_agent.instructions = base_prompt
        mock_get_agent.return_value = mock_agent
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "chat_output_formatter", include_evidence=False),
        ])

        get_all_agent_tools(flow)

        assert mock_agent.instructions.startswith("## OUTPUT EVIDENCE EXCLUSION")
        assert "do NOT include supporting evidence" in mock_agent.instructions
        assert base_prompt in mock_agent.instructions


# ===========================================================================
# get_all_agent_tools – duplicate agent_id per-step isolation
# ===========================================================================


class TestGetAllAgentToolsDuplicateAgents:
    """Tests that duplicate agent_ids get separate tools with step-specific instructions."""

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_duplicate_agents_get_separate_tools(self, mock_get_agent, mock_streaming):
        """Same agent_id in two steps should create two separate tools."""
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "gene", step_goal="Extract genes"),
            _agent_node("n2", "gene", step_goal="Validate genes"),
        ])

        tools, created_names = get_all_agent_tools(flow)

        assert len(tools) == 2
        # Verify step-numbered tool names
        call_args = [call.kwargs for call in mock_streaming.call_args_list]
        tool_names = [args["tool_name"] for args in call_args]
        assert "ask_gene_step1_specialist" in tool_names
        assert "ask_gene_step2_specialist" in tool_names

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_duplicate_agents_different_custom_instructions(self, mock_get_agent, mock_streaming):
        """Each step gets its own custom instructions, not merged."""
        agents_created = []

        def create_fresh_agent(aid, **kw):
            agent = MagicMock(spec=Agent)
            agent.instructions = f"Base {aid}"
            agents_created.append(agent)
            return agent

        mock_get_agent.side_effect = create_fresh_agent
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "gene", custom_instructions="Focus on C. elegans"),
            _agent_node("n2", "gene", custom_instructions="Focus on zebrafish"),
        ])

        get_all_agent_tools(flow)

        assert len(agents_created) == 2
        # Step 1 agent has only C. elegans instructions
        assert "C. elegans" in agents_created[0].instructions
        assert "zebrafish" not in agents_created[0].instructions
        # Step 2 agent has only zebrafish instructions
        assert "zebrafish" in agents_created[1].instructions
        assert "C. elegans" not in agents_created[1].instructions

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_duplicate_agent_one_with_custom_one_without(self, mock_get_agent, mock_streaming):
        """Only the step with custom instructions should be modified."""
        agents_created = []

        def create_fresh_agent(aid, **kw):
            agent = MagicMock(spec=Agent)
            agent.instructions = "Base gene"
            agents_created.append(agent)
            return agent

        mock_get_agent.side_effect = create_fresh_agent
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "gene", custom_instructions="Special focus"),
            _agent_node("n2", "gene"),  # No custom instructions
        ])

        get_all_agent_tools(flow)

        assert len(agents_created) == 2
        assert "Special focus" in agents_created[0].instructions
        assert agents_created[1].instructions == "Base gene"

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_single_agent_keeps_simple_tool_name(self, mock_get_agent, mock_streaming):
        """Non-duplicate agents should keep the simple ask_{id}_specialist name."""
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "gene"),
            _agent_node("n2", "disease"),
        ])

        get_all_agent_tools(flow)

        call_args = [call.kwargs for call in mock_streaming.call_args_list]
        tool_names = [args["tool_name"] for args in call_args]
        assert "ask_gene_specialist" in tool_names
        assert "ask_disease_specialist" in tool_names

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_step_numbering_accounts_for_task_input(self, mock_get_agent, mock_streaming):
        """Step numbers should skip task_input nodes."""
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene"),
            _agent_node("n2", "gene"),
        ])

        get_all_agent_tools(flow)

        call_args = [call.kwargs for call in mock_streaming.call_args_list]
        tool_names = [args["tool_name"] for args in call_args]
        # Steps are 1, 2 (task_input is skipped)
        assert "ask_gene_step1_specialist" in tool_names
        assert "ask_gene_step2_specialist" in tool_names


# ===========================================================================
# get_all_agent_tools – strict step order runtime behavior
# ===========================================================================


class TestGetAllAgentToolsStepOrderRuntime:
    """Tests strict step order against real FunctionTool invocation shape."""

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_repeated_first_tool_is_blocked_after_success(self, mock_get_agent, mock_streaming):
        """After step 1 runs, calling it again should be blocked until step 2 runs."""
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        invocations = []

        def _make_streaming_tool(agent, tool_name, tool_description, specialist_name):
            @function_tool(name_override=tool_name, description_override=tool_description)
            async def _tool(query: str) -> str:
                invocations.append((tool_name, query))
                return f"ok:{tool_name}:{query}"

            return _tool

        mock_streaming.side_effect = _make_streaming_tool

        flow = _make_flow([
            _agent_node("n1", "gene"),
            _agent_node("n2", "disease"),
        ])

        tools, _ = get_all_agent_tools(flow)
        tool_ctx = SimpleNamespace(tool_name="flow_step_tool")

        # Step 1 executes normally.
        out1 = asyncio.run(tools[0].on_invoke_tool(tool_ctx, json.dumps({"query": "q1"})))
        # Repeating step 1 should now be blocked (step 2 is next).
        out2 = asyncio.run(tools[0].on_invoke_tool(tool_ctx, json.dumps({"query": "q2"})))
        # Step 2 executes normally.
        out3 = asyncio.run(tools[1].on_invoke_tool(tool_ctx, json.dumps({"query": "q3"})))

        assert out1.startswith("ok:ask_gene_specialist:q1")
        assert "Flow step order is strict" in out2
        assert "ask_disease_specialist" in out2
        assert out3.startswith("ok:ask_disease_specialist:q3")
        # Ensure blocked call did not invoke underlying step-1 specialist again.
        assert invocations == [
            ("ask_gene_specialist", "q1"),
            ("ask_disease_specialist", "q3"),
        ]

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_custom_input_templates_bind_task_input_and_prior_step_outputs(
        self, mock_get_agent, mock_streaming
    ):
        """Custom input templates should render built-ins plus stored output_key values."""
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        invocations = []

        def _make_streaming_tool(agent, tool_name, tool_description, specialist_name):
            @function_tool(name_override=tool_name, description_override=tool_description)
            async def _tool(query: str) -> str:
                invocations.append((tool_name, query))
                if tool_name == "ask_gene_specialist":
                    return "gene-result"
                return f"validated:{query}"

            return _tool

        mock_streaming.side_effect = _make_streaming_tool

        flow = _make_flow([
            _task_input_node(
                "Review the paper carefully.",
                output_key="task_input_text",
            ),
            _agent_node("n1", "gene", output_key="gene_output"),
            _agent_node(
                "n2",
                "disease",
                input_source="custom",
                custom_input=(
                    "Task={{task_input_text}} | "
                    "Gene={{gene_output}} | "
                    "File={{input_filename_stem}} | "
                    "Trace={{trace_id}} | "
                    "Timestamp={{timestamp}}"
                ),
            ),
        ])

        with patch("src.lib.flows.executor.get_current_trace_id", lambda: "trace-123"):
            tools, _ = get_all_agent_tools(
                flow,
                document_name="Smith et al. (2024).pdf",
                user_query="Focus on the validated findings.",
                flow_run_id="flow-run-123",
            )

            tool_ctx = SimpleNamespace(tool_name="flow_step_tool")
            asyncio.run(tools[0].on_invoke_tool(tool_ctx, json.dumps({"query": "ignored-q1"})))
            asyncio.run(tools[1].on_invoke_tool(tool_ctx, json.dumps({"query": "ignored-q2"})))

        assert invocations[0] == ("ask_gene_specialist", "ignored-q1")
        assert invocations[1][0] == "ask_disease_specialist"
        assert "Task=Review the paper carefully." in invocations[1][1]
        assert "Gene=gene-result" in invocations[1][1]
        assert "File=Smith et al. (2024)" in invocations[1][1]
        assert "Trace=trace-123" in invocations[1][1]
        assert "Timestamp=" in invocations[1][1]

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_non_custom_input_source_preserves_supervisor_query(
        self, mock_get_agent, mock_streaming
    ):
        """Only input_source='custom' should override the supervisor-provided query."""
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        invocations = []

        def _make_streaming_tool(agent, tool_name, tool_description, specialist_name):
            @function_tool(name_override=tool_name, description_override=tool_description)
            async def _tool(query: str) -> str:
                invocations.append(query)
                return "ok"

            return _tool

        mock_streaming.side_effect = _make_streaming_tool

        flow = _make_flow([
            _task_input_node(output_key="task_input_text"),
            _agent_node(
                "n1",
                "gene",
                input_source="previous_output",
                custom_input="Should not replace {{task_input_text}}",
            ),
        ])

        tools, _ = get_all_agent_tools(flow, user_query="Original flow input")
        tool_ctx = SimpleNamespace(tool_name="flow_step_tool")
        asyncio.run(tools[0].on_invoke_tool(tool_ctx, json.dumps({"query": "supervisor-query"})))

        assert invocations == ["supervisor-query"]

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_output_filename_template_sets_step_scoped_formatter_override(
        self, mock_get_agent, mock_streaming
    ):
        """Formatter steps should expose a resolved filename stem only during that tool call."""
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        observed = {}

        def _make_streaming_tool(agent, tool_name, tool_description, specialist_name):
            @function_tool(name_override=tool_name, description_override=tool_description)
            async def _tool(query: str) -> str:
                from src.lib.context import get_current_output_filename_stem

                observed["during_call"] = get_current_output_filename_stem()
                return "formatted"

            return _tool

        mock_streaming.side_effect = _make_streaming_tool

        flow = _make_flow([
            _task_input_node(),
            _agent_node(
                "n1",
                "chat_output_formatter",
                output_filename_template="{{input_filename_stem}}.tsv",
            ),
        ])

        tools, _ = get_all_agent_tools(flow, document_name="Smith et al. (2024).pdf")
        tool_ctx = SimpleNamespace(tool_name="flow_step_tool")
        asyncio.run(tools[0].on_invoke_tool(tool_ctx, json.dumps({"query": "format now"})))

        from src.lib.context import get_current_output_filename_stem

        assert observed["during_call"] == "Smith_et_al_2024"
        assert get_current_output_filename_stem() is None

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_curation_prep_step_runs_deterministic_prep(
        self, mock_get_agent, mock_streaming
    ):
        """Curation prep steps should hand upstream flow extractions to the deterministic mapper."""
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        captured = {}

        def _make_streaming_tool(agent, tool_name, tool_description, specialist_name):
            @function_tool(name_override=tool_name, description_override=tool_description)
            async def _tool(query: str) -> str:
                return json.dumps(
                    {
                        "adapter_key": "reference_adapter",
                        "actor": "gene_specialist",
                        "destination": "gene_expression",
                        "confidence": 0.92,
                        "reasoning": "matched",
                        "items": [{"label": "unc-54"}],
                        "raw_mentions": [{"mention": "unc-54"}],
                        "exclusions": [],
                        "ambiguities": [],
                        "run_summary": {"candidate_count": 1},
                    }
                )

            return _tool

        mock_streaming.side_effect = _make_streaming_tool

        async def _fake_run_curation_prep(
            extraction_results,
            *,
            scope_confirmation,
            persistence_context=None,
            db=None,
        ):
            captured["extraction_results"] = extraction_results
            captured["scope_confirmation"] = scope_confirmation
            captured["persistence_context"] = persistence_context
            captured["db"] = db
            return SimpleNamespace(
                model_dump_json=lambda: json.dumps(
                    {
                        "candidates": [
                            {
                                "adapter_key": "gene_expression",
                                "profile_key": None,
                                "payload": {"label": "unc-54"},
                                "evidence_records": [
                                    {
                                        "evidence_record_id": "extract-1",
                                        "field_paths": ["label"],
                                        "anchor": {
                                            "anchor_kind": "snippet",
                                            "locator_quality": "exact_quote",
                                            "supports_decision": "supports",
                                            "snippet_text": "unc-54 was observed.",
                                            "sentence_text": "unc-54 was observed.",
                                            "normalized_text": None,
                                            "viewer_search_text": "unc-54 was observed.",
                                            "viewer_highlightable": False,
                                            "page_number": 2,
                                            "page_label": None,
                                            "section_title": "Results",
                                            "subsection_title": None,
                                            "figure_reference": None,
                                            "table_reference": None,
                                            "chunk_ids": ["chunk-1"],
                                        },
                                        "notes": [],
                                    }
                                ],
                                "conversation_context_summary": "Prepared from flow context.",
                            }
                        ],
                        "run_metadata": {
                            "model_name": "deterministic_programmatic_mapper_v1",
                            "token_usage": {
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "total_tokens": 0,
                            },
                            "processing_notes": ["Prepared from flow extraction context."],
                            "warnings": [],
                        },
                    }
                )
            )

        mock_get_agent.side_effect = lambda agent_id, **_kwargs: MagicMock(spec=Agent, instructions="Base")

        with patch("src.lib.flows.executor.run_curation_prep", _fake_run_curation_prep), patch(
            "src.lib.flows.executor.get_current_trace_id",
            lambda: "trace-123",
        ):
            flow = _make_flow([
                _task_input_node("Prepare the extracted gene-expression findings for review."),
                _agent_node("n1", "gene", step_goal="Extract gene-expression findings"),
                _agent_node(
                    "n2",
                    "curation_prep",
                    step_goal="Prepare candidates for the workspace",
                    custom_instructions="Prioritize experimentally supported findings only.",
                ),
            ])

            tools, created_names = get_all_agent_tools(
                flow,
                document_id="doc-123",
                user_id="user-123",
                session_id="session-123",
                flow_run_id="flow-run-123",
                user_query="Focus on the confirmed findings.",
            )

            assert created_names == {"ask_gene_specialist", "ask_curation_prep_specialist"}

            tool_ctx = SimpleNamespace(tool_name="flow_step_tool")
            asyncio.run(tools[0].on_invoke_tool(tool_ctx, json.dumps({"query": "extract first"})))
            prep_output = asyncio.run(
                tools[1].on_invoke_tool(tool_ctx, json.dumps({"query": "prepare for review"}))
            )

        payload = json.loads(prep_output)
        assert payload["run_metadata"]["model_name"] == "deterministic_programmatic_mapper_v1"
        assert len(captured["extraction_results"]) == 1
        assert captured["extraction_results"][0].agent_key == "gene"
        assert captured["extraction_results"][0].source_kind is _executor_module().CurationExtractionSourceKind.FLOW
        assert captured["scope_confirmation"].confirmed is True
        assert captured["scope_confirmation"].adapter_keys == ["gene"]
        assert captured["persistence_context"].document_id == "doc-123"
        assert captured["persistence_context"].origin_session_id == "session-123"
        assert captured["persistence_context"].flow_run_id == "flow-run-123"
        assert captured["persistence_context"].trace_id == "trace-123"
        assert captured["persistence_context"].user_id == "user-123"
        assert mock_get_agent.call_count == 1

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_curation_prep_step_requires_upstream_extraction_envelope(
        self, mock_get_agent, mock_streaming
    ):
        """Curation prep should fail clearly when earlier flow steps did not produce extraction envelopes."""
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")

        def _make_streaming_tool(agent, tool_name, tool_description, specialist_name):
            @function_tool(name_override=tool_name, description_override=tool_description)
            async def _tool(query: str) -> str:
                return "not a structured extraction envelope"

            return _tool

        mock_streaming.side_effect = _make_streaming_tool

        flow = _make_flow([
            _task_input_node("Prepare the extracted findings for review."),
            _agent_node("n1", "gene", step_goal="Extract gene-expression findings"),
            _agent_node("n2", "curation_prep", step_goal="Prepare candidates for the workspace"),
        ])

        tools, _ = get_all_agent_tools(
            flow,
            document_id="doc-123",
            user_id="user-123",
            session_id="session-123",
        )

        tool_ctx = SimpleNamespace(tool_name="flow_step_tool")
        asyncio.run(tools[0].on_invoke_tool(tool_ctx, json.dumps({"query": "extract first"})))
        prep_output = asyncio.run(
            tools[1].on_invoke_tool(tool_ctx, json.dumps({"query": "prepare for review"}))
        )

        assert "require at least one upstream extraction envelope" in prep_output


# ===========================================================================
# build_supervisor_instructions – custom instruction annotation & tool refs
# ===========================================================================


class TestBuildSupervisorCustomInstructions:
    """Tests that build_supervisor_instructions annotates customized steps."""

    def test_step_with_custom_instructions_annotated(self):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene", step_goal="Extract genes", custom_instructions="WB only"),
        ])
        result = build_supervisor_instructions(flow)
        assert "[has custom instructions]" in result
        assert "Step 1: Gene" in result

    def test_step_without_custom_instructions_not_annotated(self):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene", step_goal="Extract genes"),
        ])
        result = build_supervisor_instructions(flow)
        assert "[has custom instructions]" not in result

    def test_empty_custom_instructions_not_annotated(self):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene", custom_instructions=""),
        ])
        result = build_supervisor_instructions(flow)
        assert "[has custom instructions]" not in result

    def test_whitespace_custom_instructions_not_annotated(self):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene", custom_instructions="   "),
        ])
        result = build_supervisor_instructions(flow)
        assert "[has custom instructions]" not in result

    def test_mixed_steps_only_customized_annotated(self):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene", step_goal="Extract genes", custom_instructions="WB only"),
            _agent_node("n2", "disease", step_goal="Extract diseases"),
        ])
        result = build_supervisor_instructions(flow)
        lines = result.split("\n")
        gene_line = next(line for line in lines if "Gene" in line)
        disease_line = next(line for line in lines if "Disease" in line)
        assert "[has custom instructions]" in gene_line
        assert "[has custom instructions]" not in disease_line

    def test_step_with_include_evidence_annotated(self):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "chat_output_formatter", step_goal="Format output", include_evidence=True),
        ])
        result = build_supervisor_instructions(flow)
        assert "[includes evidence in output]" in result

    def test_output_formatter_without_flag_defaults_to_include_evidence_annotation(self):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "chat_output_formatter", step_goal="Format output"),
        ])
        result = build_supervisor_instructions(flow)
        assert "[includes evidence in output]" in result

    def test_output_formatter_false_flag_annotated_as_excluding_evidence(self):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "chat_output_formatter", step_goal="Format output", include_evidence=False),
        ])
        result = build_supervisor_instructions(flow)
        assert "[excludes evidence from output]" in result


class TestBuildSupervisorDuplicateAgentRefs:
    """Tests that duplicate agents get tool name references in supervisor instructions."""

    def test_duplicate_agents_include_tool_refs(self):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene", step_goal="Extract genes"),
            _agent_node("n2", "disease", step_goal="Extract diseases"),
            _agent_node("n3", "gene", step_goal="Validate genes"),
        ])
        result = build_supervisor_instructions(flow)
        assert "ask_gene_step1_specialist" in result
        assert "ask_gene_step3_specialist" in result
        # Disease is not duplicated, should NOT have tool ref
        assert "ask_disease" not in result

    def test_single_agents_no_tool_refs(self):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene", step_goal="Extract genes"),
            _agent_node("n2", "disease", step_goal="Extract diseases"),
        ])
        result = build_supervisor_instructions(flow)
        assert "use tool:" not in result


# ===========================================================================
# get_all_agent_tools – created_tool_names return value
# ===========================================================================


class TestGetAllAgentToolsCreatedNames:
    """Tests that get_all_agent_tools returns accurate created_tool_names."""

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_returns_created_tool_names(self, mock_get_agent, mock_streaming):
        """Should return set of tool names that were actually created."""
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "gene"),
            _agent_node("n2", "disease"),
        ])

        tools, created_names = get_all_agent_tools(flow)

        assert len(tools) == 2
        assert created_names == {"ask_gene_specialist", "ask_disease_specialist"}

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_skipped_agent_not_in_created_names(self, mock_get_agent, mock_streaming):
        """Agent skipped due to missing metadata should not be in created_names."""
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "gene"),
            _agent_node("n2", "unknown_agent"),  # Not in MOCK_REGISTRY
        ])

        tools, created_names = get_all_agent_tools(flow)

        assert len(tools) == 1
        assert created_names == {"ask_gene_specialist"}

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_requires_document_skipped_without_doc(
        self, mock_get_agent, mock_streaming, monkeypatch
    ):
        """Agent requiring document should be skipped when no document_id provided."""
        monkeypatch.setattr(
            "src.lib.flows.executor.get_agent_metadata",
            lambda agent_id: _metadata_from_registry(
                agent_id,
                {
                    **MOCK_REGISTRY,
                    "pdf_extraction": {
                        "name": "PDF Specialist",
                        "description": "Read PDFs",
                        "requires_document": True,
                    },
                },
            ),
        )
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "gene"),
            _agent_node("n2", "pdf_extraction"),
        ])

        # No document_id provided
        tools, created_names = get_all_agent_tools(flow)

        assert len(tools) == 1
        assert "ask_gene_specialist" in created_names
        assert "ask_pdf_specialist" not in created_names

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_curation_prep_skipped_without_doc(
        self, mock_get_agent, mock_streaming
    ):
        """Curation prep should follow normal flow document gating when no doc is loaded."""
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "gene"),
            _agent_node("n2", "curation_prep"),
        ])

        tools, created_names = get_all_agent_tools(flow)

        assert len(tools) == 1
        assert "ask_gene_specialist" in created_names
        assert "ask_curation_prep_specialist" not in created_names

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_agent_factory_exception_skipped(self, mock_get_agent, mock_streaming):
        """Agent that throws during creation should be skipped."""
        def raise_for_disease(aid, **kw):
            if aid == "disease":
                raise RuntimeError("Factory failed")
            return MagicMock(spec=Agent, instructions="Base")

        mock_get_agent.side_effect = raise_for_disease
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "gene"),
            _agent_node("n2", "disease"),
        ])

        tools, created_names = get_all_agent_tools(flow)

        assert len(tools) == 1
        assert created_names == {"ask_gene_specialist"}

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_duplicate_with_one_skipped_names(
        self, mock_get_agent, mock_streaming, monkeypatch
    ):
        """Duplicate agent_id where one step is skipped should only include created tool."""
        monkeypatch.setattr(
            "src.lib.flows.executor.get_agent_metadata",
            lambda agent_id: _metadata_from_registry(
                agent_id,
                {
                    **MOCK_REGISTRY,
                    "pdf_extraction": {
                        "name": "PDF Specialist",
                        "description": "Read PDFs",
                        "requires_document": True,
                    },
                },
            ),
        )
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "pdf_extraction"),
            _agent_node("n2", "gene"),
            _agent_node("n3", "pdf_extraction"),
        ])

        # No document — both pdf steps skipped
        tools, created_names = get_all_agent_tools(flow)

        assert len(tools) == 1
        assert created_names == {"ask_gene_specialist"}
        assert "ask_pdf_step1_specialist" not in created_names
        assert "ask_pdf_step3_specialist" not in created_names

    @patch("src.lib.flows.executor.get_agent_metadata")
    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_custom_agent_tool_names_are_sanitized(
        self, mock_get_agent, mock_streaming, mock_get_agent_metadata
    ):
        """Custom agent IDs with hyphens should be normalized for tool naming."""
        custom_id = "ca_11111111-2222-3333-4444-555555555555"
        mock_get_agent_metadata.return_value = {
            "agent_id": custom_id,
            "display_name": "Doug's Gene Agent",
            "description": "Custom gene agent",
            "requires_document": False,
            "required_params": [],
        }
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([_agent_node("n1", custom_id)])
        tools, created_names = get_all_agent_tools(flow)

        assert len(tools) == 1
        assert "ask_ca_11111111_2222_3333_4444_555555555555_specialist" in created_names

    @patch("src.lib.flows.executor.get_agent_metadata")
    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_empty_metadata_description_uses_fallback_tool_description(
        self, mock_get_agent, mock_streaming, mock_get_agent_metadata
    ):
        """Empty metadata descriptions should fall back to 'Ask the <display_name>'."""
        custom_id = "ca_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        mock_get_agent_metadata.return_value = {
            "agent_id": custom_id,
            "display_name": "Gene Validation Agent (Custom)",
            "description": "",
            "requires_document": False,
            "required_params": [],
        }
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([_agent_node("n1", custom_id)])
        get_all_agent_tools(flow)

        assert mock_streaming.call_args.kwargs["tool_description"] == "Ask the Gene Validation Agent (Custom)"


# ===========================================================================
# build_supervisor_instructions – unavailable step filtering
# ===========================================================================


class TestBuildSupervisorUnavailableSteps:
    """Tests that supervisor instructions mark unavailable steps correctly."""

    def test_unavailable_step_marked_when_tool_missing(self):
        """Steps whose tools were not created should be marked [unavailable]."""
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene", step_goal="Extract genes"),
            _agent_node("n2", "pdf_extraction", step_goal="Read paper", display_name="PDF Specialist"),
        ])

        # Only gene tool was created (pdf was skipped)
        result = build_supervisor_instructions(
            flow, available_tools={"ask_gene_specialist"}
        )

        assert "Step 1: Gene - Extract genes" in result
        assert "[unavailable" in result
        assert "Step 2: PDF Specialist" in result
        # Should NOT have a tool reference for the unavailable step
        assert "ask_pdf_specialist" not in result

    def test_available_steps_not_marked_unavailable(self):
        """Steps with available tools should not be marked unavailable."""
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene", step_goal="Extract genes"),
            _agent_node("n2", "disease", step_goal="Extract diseases"),
        ])

        result = build_supervisor_instructions(
            flow,
            available_tools={"ask_gene_specialist", "ask_disease_specialist"},
        )

        assert "[unavailable" not in result
        assert "Step 1: Gene - Extract genes" in result
        assert "Step 2: Disease - Extract diseases" in result

    def test_none_available_tools_backward_compat(self):
        """When available_tools is None, all steps assumed available (backward compat)."""
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene", step_goal="Extract genes"),
        ])

        result = build_supervisor_instructions(flow, available_tools=None)

        assert "[unavailable" not in result
        assert "Step 1: Gene - Extract genes" in result

    def test_duplicate_agent_one_step_unavailable(self):
        """Duplicate agent where one step's tool was not created."""
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "pdf_extraction", step_goal="Read abstract", display_name="PDF Specialist"),
            _agent_node("n2", "gene", step_goal="Extract genes"),
            _agent_node("n3", "pdf_extraction", step_goal="Read methods", display_name="PDF Specialist"),
        ])

        # Only step 2 (gene) was created; both pdf steps skipped
        result = build_supervisor_instructions(
            flow, available_tools={"ask_gene_specialist"}
        )

        assert "Step 1: PDF Specialist [unavailable" in result
        assert "Step 2: Gene - Extract genes" in result
        assert "Step 3: PDF Specialist [unavailable" in result
        # No phantom tool references
        assert "ask_pdf_step1_specialist" not in result
        assert "ask_pdf_step3_specialist" not in result

    def test_unavailable_step_suppresses_custom_instruction_annotation(self):
        """Unavailable steps should not show [has custom instructions]."""
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "pdf_extraction", custom_instructions="Focus on methods",
                        display_name="PDF Specialist"),
        ])

        result = build_supervisor_instructions(
            flow, available_tools=set()  # No tools created
        )

        assert "[unavailable" in result
        assert "[has custom instructions]" not in result


# ===========================================================================
# Flow evidence accumulation
# ===========================================================================


class TestFlowEvidenceAccumulation:
    """Tests flow-step evidence normalization and accumulation state."""

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_completed_steps_preserve_raw_per_step_evidence_counts(
        self, mock_get_agent, mock_streaming
    ):
        mock_get_agent.side_effect = lambda *_args, **_kwargs: MagicMock(
            spec=Agent,
            instructions="Base",
        )
        evidence_a = _make_evidence_record(
            "TP53",
            verified_quote="TP53 was elevated.",
            chunk_id="chunk-a",
        )
        evidence_b = _make_evidence_record(
            "BRCA1",
            verified_quote="BRCA1 was elevated.",
            chunk_id="chunk-b",
        )
        outputs = iter(
            [
                json.dumps(
                    _structured_step_output(
                        "TP53",
                        evidence_records=[evidence_a, dict(evidence_a)],
                    )
                ),
                json.dumps(
                    _structured_step_output(
                        "BRCA1",
                        evidence_records=[dict(evidence_a), evidence_b],
                    )
                ),
            ]
        )

        def _make_streaming_tool(agent, tool_name, tool_description, specialist_name):
            @function_tool(name_override=tool_name, description_override=tool_description)
            async def _tool(query: str) -> str:
                return next(outputs)

            return _tool

        mock_streaming.side_effect = _make_streaming_tool

        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene", step_goal="Extract gene matches"),
            _agent_node("n2", "gene", step_goal="Extract confirmation matches"),
        ])

        tools, created_names, _, execution_state = get_all_agent_tools(
            flow,
            include_unavailable=True,
        )

        assert created_names == {
            "ask_gene_step1_specialist",
            "ask_gene_step2_specialist",
        }

        tool_ctx = SimpleNamespace(tool_name="flow_step_tool")
        asyncio.run(tools[0].on_invoke_tool(tool_ctx, json.dumps({"query": "step one"})))
        asyncio.run(tools[1].on_invoke_tool(tool_ctx, json.dumps({"query": "step two"})))

        completed_steps = execution_state["completed_steps"]
        assert len(completed_steps) == 2
        assert completed_steps[0]["evidence_count"] == 1
        assert completed_steps[0]["evidence_records"][0]["entity"] == "TP53"
        assert completed_steps[1]["evidence_count"] == 2
        assert [record["entity"] for record in completed_steps[1]["evidence_records"]] == [
            "TP53",
            "BRCA1",
        ]
        assert len(execution_state["evidence_registry"].records()) == 2


# ===========================================================================
# Backward compatibility
# ===========================================================================


class TestBackwardCompatibility:
    """Flows without custom_instructions should work identically to before."""

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_flow_without_custom_instructions_unchanged(self, mock_get_agent, mock_streaming):
        """A flow with no custom_instructions should produce identical agent tools."""
        base_prompt = "You are the gene specialist."
        mock_agent = MagicMock(spec=Agent)
        mock_agent.instructions = base_prompt
        mock_get_agent.return_value = mock_agent
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene", step_goal="Extract genes"),
        ])

        tools, created_names = get_all_agent_tools(flow)

        # Agent instructions untouched
        assert mock_agent.instructions == base_prompt
        # Tool was still created
        assert len(tools) == 1
        # Simple tool name (no step number)
        call_kwargs = mock_streaming.call_args.kwargs
        assert call_kwargs["tool_name"] == "ask_gene_specialist"

    def test_supervisor_instructions_without_custom_unchanged(self):
        """Supervisor instructions should have no custom annotation markers."""
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene", step_goal="Extract genes"),
            _agent_node("n2", "disease", step_goal="Extract diseases"),
        ])
        result = build_supervisor_instructions(flow)

        assert "[has custom instructions]" not in result
        assert "use tool:" not in result
        assert "Step 1: Gene - Extract genes" in result
        assert "Step 2: Disease - Extract diseases" in result


# ===========================================================================
# create_flow_supervisor – fail-fast when no tools created
# ===========================================================================


class TestCreateFlowSupervisorNoTools:
    """Tests that create_flow_supervisor raises when all tools are skipped."""

    @patch("src.lib.flows.executor.build_model_settings")
    @patch("src.lib.flows.executor.get_model_for_agent")
    @patch("src.lib.flows.executor.get_agent_config")
    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_raises_when_no_tools_created(
        self,
        mock_get_agent,
        mock_streaming,
        mock_config,
        mock_model,
        mock_settings,
        monkeypatch,
    ):
        """Should raise ValueError when all steps are skipped."""
        monkeypatch.setattr(
            "src.lib.flows.executor.get_agent_metadata",
            lambda agent_id: _metadata_from_registry(
                agent_id,
                {
                    "pdf_extraction": {
                        "name": "PDF Specialist",
                        "description": "Read PDFs",
                        "requires_document": True,
                    }
                },
            ),
        )
        mock_config.return_value = MagicMock(model="gpt-4o", temperature=0.0, reasoning=None)

        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "pdf_extraction", step_goal="Read paper"),
            _agent_node("n2", "pdf_extraction", step_goal="Extract data"),
        ])

        with pytest.raises(ValueError, match="no agent tools could be created"):
            create_flow_supervisor(flow, document_id=None)  # No doc — both steps skipped

    @patch("src.lib.flows.executor.build_model_settings")
    @patch("src.lib.flows.executor.get_model_for_agent", return_value="gpt-4o")
    @patch("src.lib.flows.executor.get_agent_config")
    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    def test_does_not_raise_when_tools_created(
        self, mock_get_agent, mock_streaming, mock_config, mock_model, mock_settings
    ):
        """Should NOT raise when at least one tool is created."""
        mock_config.return_value = MagicMock(model="gpt-4o", temperature=0.0, reasoning=None)
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        mock_streaming.return_value = MagicMock()
        mock_settings.return_value = ModelSettings()

        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene", step_goal="Extract genes"),
        ])

        # Should not raise
        supervisor = create_flow_supervisor(flow)
        assert supervisor is not None


# ===========================================================================
# execute_flow – fail-fast on specialist/runtime errors
# ===========================================================================


class TestExecuteFlowTermination:
    """Tests flow-level termination behavior for success and failure paths."""

    @pytest.mark.asyncio
    async def test_stops_immediately_on_specialist_error(self, monkeypatch):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene", step_goal="Extract genes"),
        ])

        monkeypatch.setattr(
            "src.lib.flows.executor.create_flow_supervisor",
            lambda **_kwargs: MagicMock(name="Flow Supervisor"),
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.build_flow_prompt",
            lambda *_args, **_kwargs: "run flow",
        )

        async def _fake_run_agent_streamed(**_kwargs):
            yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-1"}}
            yield {
                "type": "SPECIALIST_ERROR",
                "details": {"error": "Gene Validation did not call required AGR DB tools"},
            }
            # If execute_flow does not break, this would leak through.
            yield {"type": "CHAT_OUTPUT_READY", "data": {}}

        monkeypatch.setattr(
            "src.lib.openai_agents.runner.run_agent_streamed",
            _fake_run_agent_streamed,
        )

        events = [event async for event in execute_flow(flow, user_id="u1", session_id="s1")]
        event_types = [event.get("type") for event in events]

        assert "FLOW_STARTED" in event_types
        assert "SPECIALIST_ERROR" in event_types
        assert "FLOW_ERROR" in event_types
        assert "CHAT_OUTPUT_READY" not in event_types

        flow_finished = next(e for e in events if e.get("type") == "FLOW_FINISHED")
        assert flow_finished["data"]["status"] == "failed"
        assert flow_finished["data"]["failure_reason"] is not None

    @pytest.mark.asyncio
    async def test_converts_run_error_into_flow_error(self, monkeypatch):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "curation_prep", step_goal="Prepare candidates"),
        ])

        monkeypatch.setattr(
            "src.lib.flows.executor.create_flow_supervisor",
            lambda **_kwargs: MagicMock(name="Flow Supervisor"),
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.build_flow_prompt",
            lambda *_args, **_kwargs: "run flow",
        )

        async def _fake_run_agent_streamed(**_kwargs):
            yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-1"}}
            yield {
                "type": "RUN_ERROR",
                "data": {
                    "message": (
                        "Curation prep flow steps require at least one upstream extraction envelope."
                    ),
                    "trace_id": "trace-1",
                },
            }

        monkeypatch.setattr(
            "src.lib.openai_agents.runner.run_agent_streamed",
            _fake_run_agent_streamed,
        )

        events = [event async for event in execute_flow(flow, user_id="u1", session_id="s1")]
        event_types = [event.get("type") for event in events]

        assert "RUN_ERROR" in event_types
        assert "FLOW_ERROR" in event_types
        flow_error = next(event for event in events if event.get("type") == "FLOW_ERROR")
        assert flow_error["details"]["reason"] == "run_error"
        assert "failed during execution" in flow_error["details"]["message"]

        flow_finished = next(e for e in events if e.get("type") == "FLOW_FINISHED")
        assert flow_finished["data"]["status"] == "failed"
        assert flow_finished["data"]["failure_reason"] == (
            "Curation prep flow steps require at least one upstream extraction envelope."
        )

    @pytest.mark.asyncio
    async def test_marks_completed_on_chat_output_ready(self, monkeypatch):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene", step_goal="Extract genes"),
        ])

        monkeypatch.setattr(
            "src.lib.flows.executor.create_flow_supervisor",
            lambda **_kwargs: MagicMock(name="Flow Supervisor"),
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.build_flow_prompt",
            lambda *_args, **_kwargs: "run flow",
        )

        async def _fake_run_agent_streamed(**_kwargs):
            yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-1"}}
            yield {"type": "CHAT_OUTPUT_READY", "data": {}}

        monkeypatch.setattr(
            "src.lib.openai_agents.runner.run_agent_streamed",
            _fake_run_agent_streamed,
        )

        events = [event async for event in execute_flow(flow, user_id="u1", session_id="s1")]
        event_types = [event.get("type") for event in events]

        assert "FLOW_ERROR" not in event_types
        assert "CHAT_OUTPUT_READY" in event_types
        flow_finished = next(e for e in events if e.get("type") == "FLOW_FINISHED")
        assert flow_finished["data"]["status"] == "completed"
        assert flow_finished["data"]["failure_reason"] is None

    @pytest.mark.asyncio
    async def test_emits_flow_step_evidence_and_generates_flow_run_id(self, monkeypatch):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene-expression", step_goal="Extract genes"),
        ])
        persisted_requests = []
        evidence_record = _make_evidence_record(
            "TP53",
            verified_quote="TP53 expression increased.",
            chunk_id="chunk-tp53",
        )
        payload = _structured_step_output(
            "TP53",
            evidence_records=[evidence_record],
        )
        completed_step = _make_completed_step(
            agent_id="gene-expression",
            agent_name="Gene Expression",
            tool_name="ask_gene_expression_specialist",
            step=1,
            adapter_key="gene_expression",
            payload=payload,
            evidence_records=[evidence_record],
        )

        supervisor = MagicMock(name="Flow Supervisor")
        supervisor._flow_unavailable_steps = []
        supervisor._flow_execution_state = _make_flow_execution_state(completed_step)

        monkeypatch.setattr(
            "src.lib.flows.executor.create_flow_supervisor",
            lambda **_kwargs: supervisor,
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.build_flow_prompt",
            lambda *_args, **_kwargs: "run flow",
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.DocumentContext.fetch",
            lambda *_args, **_kwargs: SimpleNamespace(section_count=lambda: 0, abstract=None),
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.persist_extraction_results",
            lambda requests: persisted_requests.extend(requests),
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.uuid4",
            lambda: UUID("00000000-0000-0000-0000-000000000123"),
        )

        async def _fake_run_agent_streamed(**_kwargs):
            yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-1"}}
            yield {
                "type": "TOOL_COMPLETE",
                "details": {"toolName": "ask_gene_expression_specialist"},
            }
            yield {"type": "CHAT_OUTPUT_READY", "data": {}}

        monkeypatch.setattr(
            "src.lib.openai_agents.runner.run_agent_streamed",
            _fake_run_agent_streamed,
        )

        events = [
            event
            async for event in execute_flow(
                flow,
                user_id="u1",
                session_id="flow-session-1",
                document_id="doc-1",
            )
        ]

        flow_started = next(e for e in events if e.get("type") == "FLOW_STARTED")
        flow_step_evidence = next(e for e in events if e.get("type") == "FLOW_STEP_EVIDENCE")
        flow_finished = next(e for e in events if e.get("type") == "FLOW_FINISHED")

        assert flow_started["data"]["flow_run_id"] == "00000000-0000-0000-0000-000000000123"
        assert flow_step_evidence["data"]["flow_run_id"] == "00000000-0000-0000-0000-000000000123"
        assert flow_step_evidence["data"]["step"] == 1
        assert flow_step_evidence["data"]["evidence_count"] == 1
        assert flow_step_evidence["data"]["total_evidence_records"] == 1
        assert flow_step_evidence["data"]["evidence_records"][0]["entity"] == "TP53"
        assert flow_step_evidence["data"]["evidence_preview"][0]["entity"] == "TP53"
        assert flow_finished["data"]["flow_run_id"] == "00000000-0000-0000-0000-000000000123"
        assert flow_finished["data"]["document_id"] == "doc-1"
        assert flow_finished["data"]["origin_session_id"] == "flow-session-1"
        assert flow_finished["data"]["total_evidence_records"] == 1
        assert flow_finished["data"]["step_evidence_counts"] == {"1": 1}
        assert flow_finished["data"]["adapter_keys"] == ["gene_expression"]
        assert len(persisted_requests) == 1
        assert persisted_requests[0].flow_run_id == "00000000-0000-0000-0000-000000000123"

    @pytest.mark.asyncio
    async def test_flow_step_evidence_event_caps_preview_but_preserves_raw_count(self, monkeypatch):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene-expression", step_goal="Extract genes"),
        ])
        evidence_records = [
            _make_evidence_record(
                f"GENE-{index}",
                verified_quote=f"Quote {index}",
                chunk_id=f"chunk-{index}",
            )
            for index in range(12)
        ]
        payload = _structured_step_output(
            "GENE-0",
            evidence_records=evidence_records,
        )
        completed_step = _make_completed_step(
            agent_id="gene-expression",
            agent_name="Gene Expression",
            tool_name="ask_gene_expression_specialist",
            step=1,
            adapter_key="gene_expression",
            payload=payload,
            evidence_records=evidence_records,
        )

        supervisor = MagicMock(name="Flow Supervisor")
        supervisor._flow_unavailable_steps = []
        supervisor._flow_execution_state = _make_flow_execution_state(completed_step)

        monkeypatch.setattr(
            "src.lib.flows.executor.create_flow_supervisor",
            lambda **_kwargs: supervisor,
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.build_flow_prompt",
            lambda *_args, **_kwargs: "run flow",
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.DocumentContext.fetch",
            lambda *_args, **_kwargs: SimpleNamespace(section_count=lambda: 0, abstract=None),
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.persist_extraction_results",
            lambda requests: requests,
        )

        async def _fake_run_agent_streamed(**_kwargs):
            yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-1"}}
            yield {
                "type": "TOOL_COMPLETE",
                "details": {"toolName": "ask_gene_expression_specialist"},
            }
            yield {"type": "CHAT_OUTPUT_READY", "data": {}}

        monkeypatch.setattr(
            "src.lib.openai_agents.runner.run_agent_streamed",
            _fake_run_agent_streamed,
        )

        events = [
            event
            async for event in execute_flow(
                flow,
                user_id="u1",
                session_id="flow-session-1",
                document_id="doc-1",
            )
        ]

        flow_step_evidence = next(e for e in events if e.get("type") == "FLOW_STEP_EVIDENCE")

        assert flow_step_evidence["data"]["evidence_count"] == 12
        assert len(flow_step_evidence["data"]["evidence_preview"]) == 10
        assert len(flow_step_evidence["data"]["evidence_records"]) == 10

    @pytest.mark.asyncio
    async def test_persists_extraction_envelopes_after_success(self, monkeypatch):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene-expression", step_goal="Extract genes"),
        ])
        persisted_requests = []
        payload = _structured_step_output("notch")
        completed_step = _make_completed_step(
            agent_id="gene-expression",
            agent_name="Gene Expression",
            tool_name="ask_gene_expression_specialist",
            step=1,
            adapter_key="gene_expression",
            payload=payload,
        )

        supervisor = MagicMock(name="Flow Supervisor")
        supervisor._flow_unavailable_steps = []
        supervisor._flow_execution_state = _make_flow_execution_state(completed_step)

        monkeypatch.setattr(
            "src.lib.flows.executor.create_flow_supervisor",
            lambda **_kwargs: supervisor,
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.DocumentContext.fetch",
            lambda *_args, **_kwargs: SimpleNamespace(section_count=lambda: 0, abstract=None),
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.build_flow_prompt",
            lambda *_args, **_kwargs: "run flow",
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.persist_extraction_results",
            lambda requests: persisted_requests.extend(requests),
        )

        async def _fake_run_agent_streamed(**_kwargs):
            yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-1"}}
            yield {
                "type": "TOOL_COMPLETE",
                "details": {"toolName": "ask_gene_expression_specialist"},
            }
            yield {"type": "CHAT_OUTPUT_READY", "data": {}}

        monkeypatch.setattr(
            "src.lib.openai_agents.runner.run_agent_streamed",
            _fake_run_agent_streamed,
        )

        events = [
            event
            async for event in execute_flow(
                flow,
                user_id="u1",
                session_id="flow-session-1",
                document_id="doc-1",
                user_query="Extract findings",
            )
        ]

        event_types = [event.get("type") for event in events]
        assert "CHAT_OUTPUT_READY" in event_types
        assert "FLOW_FINISHED" in event_types
        assert len(persisted_requests) == 1
        persisted_request = persisted_requests[0]
        assert persisted_request.document_id == "doc-1"
        assert persisted_request.adapter_key == "gene_expression"
        assert persisted_request.agent_key == "gene-expression"
        assert persisted_request.source_kind is _executor_module().CurationExtractionSourceKind.FLOW
        assert persisted_request.origin_session_id == "flow-session-1"
        assert persisted_request.flow_run_id is not None
        assert persisted_request.trace_id == "trace-1"
        assert persisted_request.user_id == "u1"
        assert persisted_request.candidate_count == 1
        assert persisted_request.metadata["tool_name"] == "ask_gene_expression_specialist"
        assert persisted_request.metadata["flow_id"] == str(flow.id)

    @pytest.mark.asyncio
    async def test_persists_flow_run_id_on_flow_extraction_envelopes(self, monkeypatch):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene-expression", step_goal="Extract genes"),
        ])
        persisted_requests = []
        payload = _structured_step_output("notch")
        completed_step = _make_completed_step(
            agent_id="gene-expression",
            agent_name="Gene Expression",
            tool_name="ask_gene_expression_specialist",
            step=1,
            adapter_key="gene_expression",
            payload=payload,
        )

        supervisor = MagicMock(name="Flow Supervisor")
        supervisor._flow_unavailable_steps = []
        supervisor._flow_execution_state = _make_flow_execution_state(completed_step)

        monkeypatch.setattr(
            "src.lib.flows.executor.create_flow_supervisor",
            lambda **_kwargs: supervisor,
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.DocumentContext.fetch",
            lambda *_args, **_kwargs: SimpleNamespace(section_count=lambda: 0, abstract=None),
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.build_flow_prompt",
            lambda *_args, **_kwargs: "run flow",
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.persist_extraction_results",
            lambda requests: persisted_requests.extend(requests),
        )

        async def _fake_run_agent_streamed(**_kwargs):
            yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-1"}}
            yield {
                "type": "TOOL_COMPLETE",
                "details": {"toolName": "ask_gene_expression_specialist"},
            }
            yield {"type": "CHAT_OUTPUT_READY", "data": {}}

        monkeypatch.setattr(
            "src.lib.openai_agents.runner.run_agent_streamed",
            _fake_run_agent_streamed,
        )

        events = [
            event
            async for event in execute_flow(
                flow,
                user_id="u1",
                session_id="flow-session-1",
                document_id="doc-1",
                user_query="Extract findings",
                flow_run_id="batch-123",
            )
        ]

        assert "FLOW_FINISHED" in [event.get("type") for event in events]
        assert len(persisted_requests) == 1
        assert persisted_requests[0].flow_run_id == "batch-123"

    @pytest.mark.asyncio
    async def test_marks_flow_failed_when_extraction_persistence_fails(self, monkeypatch):
        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "gene-expression", step_goal="Extract genes"),
        ])
        payload = _structured_step_output("notch")
        completed_step = _make_completed_step(
            agent_id="gene-expression",
            agent_name="Gene Expression",
            tool_name="ask_gene_expression_specialist",
            step=1,
            adapter_key="gene_expression",
            payload=payload,
        )

        supervisor = MagicMock(name="Flow Supervisor")
        supervisor._flow_unavailable_steps = []
        supervisor._flow_execution_state = _make_flow_execution_state(completed_step)

        monkeypatch.setattr(
            "src.lib.flows.executor.create_flow_supervisor",
            lambda **_kwargs: supervisor,
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.DocumentContext.fetch",
            lambda *_args, **_kwargs: SimpleNamespace(section_count=lambda: 0, abstract=None),
        )
        monkeypatch.setattr(
            "src.lib.flows.executor.build_flow_prompt",
            lambda *_args, **_kwargs: "run flow",
        )

        def _raise_persistence(_request):
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(
            "src.lib.flows.executor.persist_extraction_results",
            _raise_persistence,
        )

        async def _fake_run_agent_streamed(**_kwargs):
            yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-1"}}
            yield {
                "type": "TOOL_COMPLETE",
                "details": {"toolName": "ask_gene_expression_specialist"},
            }
            yield {"type": "CHAT_OUTPUT_READY", "data": {}}

        monkeypatch.setattr(
            "src.lib.openai_agents.runner.run_agent_streamed",
            _fake_run_agent_streamed,
        )

        events = [
            event
            async for event in execute_flow(
                flow,
                user_id="u1",
                session_id="flow-session-1",
                document_id="doc-1",
                user_query="Extract findings",
            )
        ]

        event_types = [event.get("type") for event in events]
        assert "CHAT_OUTPUT_READY" not in event_types
        assert "FLOW_ERROR" in event_types
        flow_finished = next(e for e in events if e.get("type") == "FLOW_FINISHED")
        assert flow_finished["data"]["status"] == "failed"
        assert "db unavailable" in (flow_finished["data"]["failure_reason"] or "")
