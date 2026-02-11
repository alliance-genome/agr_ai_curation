"""Tests for flow executor custom_instructions wiring."""
import pytest
from unittest.mock import MagicMock, patch

from agents import Agent, ModelSettings

from src.lib.flows.executor import (
    _count_agent_ids,
    get_all_agent_tools,
    build_supervisor_instructions,
    create_flow_supervisor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_flow(nodes):
    """Create a mock CurationFlow with the given nodes list."""
    flow = MagicMock()
    flow.flow_definition = {"nodes": nodes}
    flow.name = "Test Flow"
    return flow


def _agent_node(node_id, agent_id, custom_instructions=None, step_goal=None, display_name=None):
    """Build a minimal agent node dict."""
    data = {
        "agent_id": agent_id,
        "agent_display_name": display_name or agent_id.title(),
        "output_key": f"{node_id}_out",
    }
    if custom_instructions is not None:
        data["custom_instructions"] = custom_instructions
    if step_goal is not None:
        data["step_goal"] = step_goal
    return {
        "id": node_id,
        "type": "agent",
        "position": {"x": 0, "y": 0},
        "data": data,
    }


def _task_input_node(task_instructions="Do the thing"):
    """Build a task_input node dict."""
    return {
        "id": "node_task",
        "type": "task_input",
        "position": {"x": 0, "y": 0},
        "data": {
            "agent_id": "task_input",
            "agent_display_name": "Task Input",
            "output_key": "task_out",
            "task_instructions": task_instructions,
        },
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


# ===========================================================================
# get_all_agent_tools – per-node custom_instructions wiring
# ===========================================================================


MOCK_REGISTRY = {
    "gene": {
        "name": "Gene Specialist",
        "description": "Curate genes",
        "factory": lambda: None,
        "requires_document": False,
    },
    "disease": {
        "name": "Disease Specialist",
        "description": "Curate diseases",
        "factory": lambda: None,
        "requires_document": False,
    },
}


class TestGetAllAgentToolsCustomInstructions:
    """Tests that get_all_agent_tools prepends per-node custom_instructions."""

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    @patch("src.lib.flows.executor.AGENT_REGISTRY", MOCK_REGISTRY)
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
    @patch("src.lib.flows.executor.AGENT_REGISTRY", MOCK_REGISTRY)
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
    @patch("src.lib.flows.executor.AGENT_REGISTRY", MOCK_REGISTRY)
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
    @patch("src.lib.flows.executor.AGENT_REGISTRY", MOCK_REGISTRY)
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
    @patch("src.lib.flows.executor.AGENT_REGISTRY", MOCK_REGISTRY)
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


# ===========================================================================
# get_all_agent_tools – duplicate agent_id per-step isolation
# ===========================================================================


class TestGetAllAgentToolsDuplicateAgents:
    """Tests that duplicate agent_ids get separate tools with step-specific instructions."""

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    @patch("src.lib.flows.executor.AGENT_REGISTRY", MOCK_REGISTRY)
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
    @patch("src.lib.flows.executor.AGENT_REGISTRY", MOCK_REGISTRY)
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
    @patch("src.lib.flows.executor.AGENT_REGISTRY", MOCK_REGISTRY)
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
    @patch("src.lib.flows.executor.AGENT_REGISTRY", MOCK_REGISTRY)
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
    @patch("src.lib.flows.executor.AGENT_REGISTRY", MOCK_REGISTRY)
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
        gene_line = next(l for l in lines if "Gene" in l)
        disease_line = next(l for l in lines if "Disease" in l)
        assert "[has custom instructions]" in gene_line
        assert "[has custom instructions]" not in disease_line


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
    @patch("src.lib.flows.executor.AGENT_REGISTRY", MOCK_REGISTRY)
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
    @patch("src.lib.flows.executor.AGENT_REGISTRY", MOCK_REGISTRY)
    def test_skipped_agent_not_in_created_names(self, mock_get_agent, mock_streaming):
        """Agent skipped due to missing registry entry should not be in created_names."""
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
    @patch("src.lib.flows.executor.AGENT_REGISTRY", {
        **MOCK_REGISTRY,
        "pdf": {"name": "PDF Specialist", "description": "Read PDFs", "requires_document": True},
    })
    def test_requires_document_skipped_without_doc(self, mock_get_agent, mock_streaming):
        """Agent requiring document should be skipped when no document_id provided."""
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "gene"),
            _agent_node("n2", "pdf"),
        ])

        # No document_id provided
        tools, created_names = get_all_agent_tools(flow)

        assert len(tools) == 1
        assert "ask_gene_specialist" in created_names
        assert "ask_pdf_specialist" not in created_names

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    @patch("src.lib.flows.executor.AGENT_REGISTRY", MOCK_REGISTRY)
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
    @patch("src.lib.flows.executor.AGENT_REGISTRY", {
        **MOCK_REGISTRY,
        "pdf": {"name": "PDF Specialist", "description": "Read PDFs", "requires_document": True},
    })
    def test_duplicate_with_one_skipped_names(self, mock_get_agent, mock_streaming):
        """Duplicate agent_id where one step is skipped should only include created tool."""
        mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
        mock_streaming.return_value = MagicMock()

        flow = _make_flow([
            _agent_node("n1", "pdf"),
            _agent_node("n2", "gene"),
            _agent_node("n3", "pdf"),
        ])

        # No document — both pdf steps skipped
        tools, created_names = get_all_agent_tools(flow)

        assert len(tools) == 1
        assert created_names == {"ask_gene_specialist"}
        assert "ask_pdf_step1_specialist" not in created_names
        assert "ask_pdf_step3_specialist" not in created_names


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
            _agent_node("n2", "pdf", step_goal="Read paper", display_name="PDF Specialist"),
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
            _agent_node("n1", "pdf", step_goal="Read abstract", display_name="PDF Specialist"),
            _agent_node("n2", "gene", step_goal="Extract genes"),
            _agent_node("n3", "pdf", step_goal="Read methods", display_name="PDF Specialist"),
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
            _agent_node("n1", "pdf", custom_instructions="Focus on methods",
                        display_name="PDF Specialist"),
        ])

        result = build_supervisor_instructions(
            flow, available_tools=set()  # No tools created
        )

        assert "[unavailable" in result
        assert "[has custom instructions]" not in result


# ===========================================================================
# Backward compatibility
# ===========================================================================


class TestBackwardCompatibility:
    """Flows without custom_instructions should work identically to before."""

    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    @patch("src.lib.flows.executor.AGENT_REGISTRY", MOCK_REGISTRY)
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
    @patch("src.lib.flows.executor.AGENT_REGISTRY", {
        "pdf": {"name": "PDF Specialist", "description": "Read PDFs", "requires_document": True},
    })
    def test_raises_when_no_tools_created(
        self, mock_get_agent, mock_streaming, mock_config, mock_model, mock_settings
    ):
        """Should raise ValueError when all steps are skipped."""
        mock_config.return_value = MagicMock(model="gpt-4o", temperature=0.0, reasoning=None)

        flow = _make_flow([
            _task_input_node(),
            _agent_node("n1", "pdf", step_goal="Read paper"),
            _agent_node("n2", "pdf", step_goal="Extract data"),
        ])

        with pytest.raises(ValueError, match="no agent tools could be created"):
            create_flow_supervisor(flow, document_id=None)  # No doc — both steps skipped

    @patch("src.lib.flows.executor.build_model_settings")
    @patch("src.lib.flows.executor.get_model_for_agent", return_value="gpt-4o")
    @patch("src.lib.flows.executor.get_agent_config")
    @patch("src.lib.flows.executor._create_streaming_tool")
    @patch("src.lib.flows.executor.get_agent_by_id")
    @patch("src.lib.flows.executor.AGENT_REGISTRY", MOCK_REGISTRY)
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
