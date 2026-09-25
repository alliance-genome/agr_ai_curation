"""ALL-1292: always-sent instruction size guards for Agent Studio and the chat supervisor.

Production 2026-09-16..22 (Langfuse instr_chars): Agent Studio median 70,233
chars per call, chat Query Supervisor 24,232 (no group) / 27,801 (group rules).
Representative offline builds (real installed Alliance template, 3,000-char
Workshop draft, three configured models, 1,325-char diagnostic tool list):

Agent Studio, before -> after (chars)
  no tab context                49,064 -> 16,987
  Flows tab                     70,597 -> 25,125
  Agent Workshop tab            69,023 -> 28,232

Per-section, before -> after: role 680 -> 643 (user greeting moved to the
per-run tail); architecture 2,701 -> guide `system_architecture`; flow
verification (template copy) 3,601 -> removed as a duplicate of the Flows-tab
protocol; domain envelopes 6,547 -> guide `domain_envelopes` (+257 live-state
rule kept); trace analysis 4,660 + token budget 2,769 + workflow 2,191 ->
guides `trace_investigation` and `token_budget`; toolset 12,424 -> guides
`chat_history_and_feedback_tools`, `trace_and_log_tools`, `inspection_tools`
(+395 selected-agent prompt rule kept); model playbook 1,119 -> guide
`model_selection`; Studio Guide index +3,267; Flows critical instruction
11,220 -> 6,641 (verification protocol -> guide `flow_verification`);
validation checklist 2,722 -> guide `flow_verification`; flow design guidance
6,699 -> 604 (details -> guide `flow_design`); Workshop static guidance
~11,700 -> 6,288 (profile design, output/validator attachment and prompt
playbook -> guides `workshop_profile_design`, `workshop_output_and_validators`,
`prompt_playbook`). Behavioural rules, feedback/failure reporting, constraints
and live authoring guidance stay always-sent.

Chat supervisor (Alliance config base prompt 18,967 -> 16,878 chars before the
ALL-1287 merge; 19,852 -> 18,276 on the integration branch: runtime tool
authority 468 -> 256, extraction result completion 3,472 -> 1,876, output
contract 1,157 -> 624, routing map 1,653 -> 1,905 with the Display as column;
runtime note -330): duplicated extraction-result, export, curation-prep, runtime-tool
authority and display-label guidance is stated once.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.lib.agent_studio import prompt_builder
from src.lib.agent_studio import studio_guide
from src.lib.agent_studio.models import AgentWorkshopContext, ChatContext
from src.lib.packages.agent_studio_prompt_loader import load_installed_agent_studio_prompt


REPO_ROOT = Path(__file__).resolve().parents[5]
DIAGNOSTIC_TOOLS_TEXT = "- **`fixture_diagnostic_tool`** - " + "d" * 1_290

# Budgets sit a little above the post-ALL-1292 sizes; the pre-change sizes are
# 49,064 / 70,597 / 69,023, so these fail on the pre-change builder.
STUDIO_BUDGETS = {
    "none": 18_000,
    "flows": 26_500,
    "agent_workshop": 30_000,
}
# ALL-1287 added 885 chars of inspect_results guidance (18,967 -> 19,852 before
# this dedup); after ALL-1292 the merged base prompt is 18,276.
SUPERVISOR_BASE_PROMPT_BUDGET = 18_800


def _alliance_template() -> str:
    return load_installed_agent_studio_prompt(
        REPO_ROOT / "packages",
        overrides_path=REPO_ROOT / "config" / "overrides.yaml",
    ).content


def _models():
    return [
        SimpleNamespace(
            name=name,
            model_id=model_id,
            default=default,
            guidance="g" * 200,
            description="",
            reasoning_options=["low", "medium", "high"],
            default_reasoning="medium",
        )
        for name, model_id, default in (
            ("Sol", "gpt-6-sol", True),
            ("Astra", "gpt-6-astra", False),
        )
    ]


def _contexts() -> dict[str, ChatContext | None]:
    return {
        "none": None,
        "flows": ChatContext(active_tab="flows"),
        "agent_workshop": ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(
                prompt_draft="P" * 3_000,
                draft_name="Reagent extractor",
                draft_tool_ids=["search_document"],
            ),
        ),
    }


@pytest.fixture
def build_prompt(monkeypatch):
    monkeypatch.setattr(
        prompt_builder,
        "build_package_diagnostic_tools_prompt",
        lambda: DIAGNOSTIC_TOOLS_TEXT,
    )
    monkeypatch.setattr(prompt_builder, "is_model_selectable", lambda _model: True)
    template = _alliance_template()

    def _build(context: ChatContext | None) -> str:
        return prompt_builder.build_opus_system_prompt(
            context,
            user_name="Curator Example",
            load_template=lambda: template,
            list_model_definitions=_models,
            get_prompt_catalog=lambda: SimpleNamespace(get_agent=lambda _agent_id: None),
            prepare_trace_context=lambda _trace_id: None,
        )

    return _build


@pytest.mark.parametrize("tab", sorted(STUDIO_BUDGETS))
def test_studio_always_sent_instructions_stay_within_budget(build_prompt, tab):
    prompt = build_prompt(_contexts()[tab])

    assert len(prompt) <= STUDIO_BUDGETS[tab], (
        f"Agent Studio {tab} instructions grew to {len(prompt)} chars "
        f"(budget {STUDIO_BUDGETS[tab]}). Move sometimes-needed reference material "
        "into a studio_guide_topic instead of the always-sent prompt."
    )


def test_every_referenced_guide_topic_exists_and_is_indexed(build_prompt):
    topics = {topic.topic_id for topic in studio_guide.studio_guide_topics(_alliance_template())}
    prompts = [build_prompt(context) for context in _contexts().values()]

    referenced = set()
    for prompt in prompts:
        referenced.update(re.findall(r"studio guide topic `([a-z0-9_]+)`", prompt))
        for topic_id in topics:
            assert f"- `{topic_id}` (" in prompt
    assert referenced, "tab guidance should point to the guide topics it relies on"
    assert referenced <= topics


def test_moved_reference_material_is_reachable_through_the_guide():
    template = _alliance_template()
    always_sent, _topics = studio_guide.prepare_studio_prompt_template(template)
    moved_phrases = {
        "trace_investigation": "execute this workflow AUTOMATICALLY",
        "token_budget": "You have a 200K token context window.",
        "domain_envelopes": "PDF evidence is span-backed:",
        "inspection_tools": "{{PACKAGE_DIAGNOSTIC_TOOLS}}",
        "model_selection": "Agent Workshop Model Recommendation Playbook",
        "system_architecture": "Flow placement rule",
        "flow_verification": "**CRITICAL for item 4:**",
        "flow_design": 'selection_mode="selected_fields"',
        "workshop_profile_design": "Use update_field with field_update",
        "workshop_output_and_validators": 'inspect_workshop_profile(action="validator_options")',
        "prompt_playbook": "prioritize deterministic wording over creative language",
    }
    for topic_id, phrase in moved_phrases.items():
        if phrase != "{{PACKAGE_DIAGNOSTIC_TOOLS}}":
            assert phrase not in always_sent
        text = _read_topic(template, topic_id)
        if phrase == "{{PACKAGE_DIAGNOSTIC_TOOLS}}":
            assert DIAGNOSTIC_TOOLS_TEXT in text
        else:
            assert phrase in text


def test_static_instructions_precede_per_user_and_per_run_context(build_prompt):
    workshop = build_prompt(_contexts()["agent_workshop"])
    flows = build_prompt(_contexts()["flows"])

    assert workshop.index("## Studio Guide") < workshop.index("</agent_workshop_context>")
    assert workshop.index("</agent_workshop_context>") < workshop.index("<current_user>")
    assert workshop.index("<current_user>") < workshop.index("<agent_workshop_current_draft>")
    assert "Curator Example" not in workshop[: workshop.index("<current_user>")]
    assert flows.index("</flow_context>") < flows.index("<current_user>")
    assert "{{USER_GREETING}}" not in workshop


def test_template_with_user_greeting_placeholder_fails_explicitly(monkeypatch):
    monkeypatch.setattr(prompt_builder, "build_package_diagnostic_tools_prompt", lambda: "")
    with pytest.raises(ValueError, match="USER_GREETING"):
        prompt_builder.build_opus_system_prompt(
            None,
            user_name="Curator Example",
            load_template=lambda: "<role>{{USER_GREETING}}</role>",
            list_model_definitions=list,
            get_prompt_catalog=lambda: None,
            prepare_trace_context=lambda _trace_id: None,
        )


def _read_topic(template: str, topic_id: str) -> str:
    chunks = []
    call = {"topic": topic_id}
    while True:
        result = studio_guide.read_studio_guide(
            template=template,
            render_diagnostic_tools=lambda: DIAGNOSTIC_TOOLS_TEXT,
            **call,
        )
        assert result["success"] is True, result
        assert result["start"] == sum(len(chunk) for chunk in chunks)
        chunks.append(result["content"])
        if result["complete"]:
            assert result["next_call"] is None
            assert len("".join(chunks)) == result["total_chars"]
            return "".join(chunks)
        call = result["next_call"]["arguments"]


def test_guide_reads_are_bounded_and_hash_pinned(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_GUIDE_CHUNK_MAX_CHARS", "1000")
    template = _alliance_template()
    text = _read_topic(template, "trace_investigation")
    assert len(text) > 1000

    first = studio_guide.read_studio_guide(
        template=template, render_diagnostic_tools=str, topic="trace_investigation"
    )
    assert len(first["content"]) == 1000
    stale = studio_guide.read_studio_guide(
        template=template,
        render_diagnostic_tools=str,
        topic="trace_investigation",
        start=1000,
        content_hash="sha256:stale",
    )
    assert stale["success"] is False
    assert stale["code"] == "guide_content_changed"

    unknown = studio_guide.read_studio_guide(
        template=template, render_diagnostic_tools=str, topic="not_a_topic"
    )
    assert unknown["code"] == "guide_topic_not_found"
    assert "trace_investigation" in unknown["available_topics"]

    listing = studio_guide.read_studio_guide(
        template=template, render_diagnostic_tools=str, query="projection"
    )
    assert "flow_design" in {item["topic"] for item in listing["topics"]}
    assert all("content" not in item for item in listing["topics"])


def test_malformed_or_duplicate_guide_topics_fail_explicitly():
    topic = '<studio_guide_topic id="dup" title="T" read_when="W">\nbody\n</studio_guide_topic>\n'
    with pytest.raises(studio_guide.StudioGuideDefinitionError, match="Duplicate"):
        studio_guide.split_studio_guide_topics(topic + topic, source="fixture")
    with pytest.raises(studio_guide.StudioGuideDefinitionError, match="Malformed"):
        studio_guide.split_studio_guide_topics(
            '<studio_guide_topic id="x">\nbody\n</studio_guide_topic>\n', source="fixture"
        )
    with pytest.raises(studio_guide.StudioGuideDefinitionError, match="Duplicate"):
        studio_guide.studio_guide_topics(
            '<studio_guide_topic id="flow_design" title="T" read_when="W">\nbody\n</studio_guide_topic>\n'
        )


def test_supervisor_base_prompt_and_runtime_note_stay_deduplicated():
    from src.lib.openai_agents.agents.supervisor_agent import _build_runtime_tool_availability_note

    content = yaml.safe_load(
        (REPO_ROOT / "config" / "agents" / "supervisor" / "prompt.yaml").read_text(encoding="utf-8")
    )["content"]
    assert len(content) <= SUPERVISOR_BASE_PROMPT_BUDGET, (
        f"Supervisor base prompt grew to {len(content)} chars "
        f"(budget {SUPERVISOR_BASE_PROMPT_BUDGET})"
    )
    assert content.count('"Uploaded Document"') == 1
    assert "Use `inspect_results(target=\"latest\")`" not in content

    note = _build_runtime_tool_availability_note(
        tool_specs=[],
        available_specialist_tools=[SimpleNamespace(name="ask_pdf_extraction_specialist")],
        document_loaded=True,
    )
    assert "CURATION PREP HANDOFF" not in note


@pytest.mark.asyncio
async def test_read_studio_guide_tool_is_available_on_every_tab_and_dispatched(monkeypatch):
    import src.api.agent_studio as api
    from src.api import agent_studio_opus_tools as opus_tools

    for tab in ("agents", "flows", "agent_workshop"):
        context = ChatContext(active_tab=tab)
        assert opus_tools.is_tool_allowed_for_context("read_studio_guide", context)
        assert "read_studio_guide" in {tool["name"] for tool in api._get_all_opus_tools(context)}

    monkeypatch.setattr(api, "_load_agent_studio_system_prompt_template", _alliance_template)
    monkeypatch.setattr(
        prompt_builder, "build_package_diagnostic_tools_prompt", lambda: DIAGNOSTIC_TOOLS_TEXT
    )
    result = await api._execute_tool_call(
        "read_studio_guide",
        {"topic": "inspection_tools"},
        None,
        "fixture@example.org",
        "fixture-auth",
    )
    assert result["success"] is True
    assert result["complete"] is True
    assert DIAGNOSTIC_TOOLS_TEXT in result["content"]
    assert "{{PACKAGE_DIAGNOSTIC_TOOLS}}" not in result["content"]


def test_read_studio_guide_is_eager_so_the_index_can_name_it():
    from src.api.agent_studio_opus_tools import READ_STUDIO_GUIDE_TOOL_NAME
    from src.lib.config.tool_loading_loader import load_tool_loading_policies

    eager_tools = load_tool_loading_policies()["agent_studio"].eager_tools
    assert READ_STUDIO_GUIDE_TOOL_NAME == "read_studio_guide"
    assert "read_studio_guide" in eager_tools
    assert "search_studio_capabilities" in eager_tools


def test_guide_requests_with_invalid_arguments_fail_explicitly():
    template = _alliance_template()

    def _read(**arguments):
        return studio_guide.read_studio_guide(
            template=template, render_diagnostic_tools=str, **arguments
        )

    assert _read(start=10)["code"] == "guide_topic_required"
    assert _read(topic="flow_design", start="10")["code"] == "guide_start_invalid"
    assert _read(topic="flow_design", start=10**9)["code"] == "guide_start_out_of_range"
    assert _read(topic="flow_design", query="projection")["code"] == "guide_query_with_topic"


def test_guide_chunk_must_stay_below_provider_inline_boundary(monkeypatch):
    from src.lib.openai_agents.config import get_agent_studio_guide_chunk_max_chars

    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "12000")
    monkeypatch.setenv("AGENT_STUDIO_GUIDE_CHUNK_MAX_CHARS", "12000")
    with pytest.raises(ValueError, match="AGENT_STUDIO_GUIDE_CHUNK_MAX_CHARS"):
        get_agent_studio_guide_chunk_max_chars()
    monkeypatch.delenv("AGENT_STUDIO_GUIDE_CHUNK_MAX_CHARS")
    assert get_agent_studio_guide_chunk_max_chars() == 8_000


def test_verification_pass_gate_and_credential_rule_survive_off_the_flows_tab(build_prompt):
    template = _alliance_template()
    verification = _read_topic(template, "flow_verification")
    assert "NEVER report PASS when a required detail is incomplete" in verification
    assert "`output_key` is HIGH" in verification

    agents_tab = build_prompt(ChatContext(active_tab="agents"))
    assert "Ask for another person's credentials or bypass an access denial" in agents_tab


def test_core_template_references_resolve_to_core_guide_topics():
    core_template = (REPO_ROOT / "packages" / "core" / "config" / "agent_studio_system_prompt.md").read_text(
        encoding="utf-8"
    )
    always_sent, topics = studio_guide.prepare_studio_prompt_template(core_template)
    topic_ids = {topic.topic_id for topic in topics}
    assert "Read studio guide topic `flow_verification`" in always_sent
    assert "`flow_design` before designing a flow" in always_sent
    assert {"flow_verification", "flow_design"} <= topic_ids
    assert "- `flow_verification` (" in prompt_builder.build_opus_system_prompt(
        None,
        load_template=lambda: core_template.replace("{{PACKAGE_DIAGNOSTIC_TOOLS}}", ""),
        list_model_definitions=list,
        get_prompt_catalog=lambda: None,
        prepare_trace_context=lambda _trace_id: None,
    )
