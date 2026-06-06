"""Prompt and model helpers for Agent Studio Opus interactions."""

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from src.lib.agent_studio.models import ChatContext
from src.lib.agent_studio.trace_agent_metadata import get_trace_agent_patterns


def list_anthropic_catalog_models(
    *,
    list_model_definitions: Callable[[], Iterable[Any]],
    logger: Any,
) -> List[Any]:
    """Return Anthropic models from catalog, sorted with defaults first."""

    try:
        models = list_model_definitions()
    except Exception as exc:
        logger.warning("Failed to load model catalog while resolving prompt explorer model: %s", exc)
        return []

    anthropic_models = [
        model
        for model in models
        if str(getattr(model, "provider", "") or "").strip().lower() == "anthropic"
    ]
    anthropic_models.sort(
        key=lambda model: (
            not bool(getattr(model, "default", False)),
            str(getattr(model, "name", "") or "").lower(),
        )
    )
    return anthropic_models


def resolve_prompt_explorer_model(
    *,
    configured_model_id: str,
    catalog_models: Sequence[Any],
) -> tuple[str, str]:
    """
    Resolve the model id/name for Agent Studio chat and suggestion submission.

    Resolution order is controlled by the caller:
    1. PROMPT_EXPLORER_MODEL_ID env override
    2. Legacy ANTHROPIC_OPUS_MODEL env override
    3. Anthropic model from config/models.yaml (default first)
    """

    catalog_name_by_id = {
        str(getattr(model, "model_id", "")).strip(): str(getattr(model, "name", "")).strip()
        for model in catalog_models
        if str(getattr(model, "model_id", "")).strip()
    }

    if configured_model_id:
        configured_name = catalog_name_by_id.get(configured_model_id) or configured_model_id
        return configured_model_id, configured_name

    if catalog_models:
        selected = catalog_models[0]
        selected_id = str(getattr(selected, "model_id", "")).strip()
        selected_name = str(getattr(selected, "name", "")).strip() or selected_id
        if selected_id:
            return selected_id, selected_name

    raise ValueError(
        "No Agent Studio Anthropic model configured. Set PROMPT_EXPLORER_MODEL_ID "
        "(or legacy ANTHROPIC_OPUS_MODEL), or add an anthropic model to config/models.yaml."
    )


def load_agent_studio_system_prompt_template(
    *,
    candidates: Sequence[Path],
    logger: Any,
) -> str:
    """Load the shared Agent Studio system prompt template from alliance_config."""

    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        except OSError:
            logger.debug("Failed to read Agent Studio system prompt template candidate: %s", candidate)

    candidate_list = ", ".join(str(path) for path in candidates)
    raise RuntimeError(
        "Failed to load Agent Studio system prompt template from any candidate path: "
        f"{candidate_list}"
    )


def build_package_diagnostic_tools_prompt() -> str:
    """Build Agent Studio tool guidance from package-owned tool metadata."""
    from src.lib.agent_studio.catalog_service import get_tool_registry

    tool_registry = get_tool_registry()
    lines: List[str] = []
    for tool_id, tool_info in sorted(tool_registry.items()):
        agent_studio_metadata = tool_info.get("agent_studio")
        if not isinstance(agent_studio_metadata, dict):
            continue
        diagnostic_metadata = agent_studio_metadata.get("diagnostic")
        if not isinstance(diagnostic_metadata, dict) or not bool(diagnostic_metadata.get("enabled", False)):
            continue

        description = str(agent_studio_metadata.get("prompt_description") or "").strip()
        if not description:
            raise ValueError(
                f"Package diagnostic tool '{tool_id}' must declare "
                "agent_studio.prompt_description for Agent Studio prompt guidance."
            )

        line = f"- **`{tool_id}`** - {description}"
        methods = tool_info.get("methods")
        if isinstance(methods, dict) and methods:
            method_names = ", ".join(str(name) for name in sorted(methods))
            line += f" Methods: {method_names}."
        lines.append(line)

        hint = str(diagnostic_metadata.get("hint") or "").strip()
        if hint:
            lines.append(f"- {hint}")

    if not lines:
        return "- No package diagnostic tools are currently installed."

    return "\n".join(lines)


def format_conversation_context(messages: Optional[List[dict]]) -> Optional[str]:
    """
    Format the entire conversation history as a readable string.

    Args:
        messages: List of message dicts with 'role' and 'content' keys

    Returns:
        Formatted conversation string, or None if no messages
    """

    if not messages:
        return None

    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        # Handle content that's a list (tool results)
        if isinstance(content, list):
            # Skip tool result messages - they're not part of the user conversation
            continue

        # Format role label
        role_label = {
            "user": "Curator",
            "assistant": "Opus",
        }.get(role, role.title())

        lines.append(f"{role_label}: {content}")

    return "\n\n".join(lines) if lines else None


def parse_markdown_heading(line: str) -> Optional[Dict[str, Any]]:
    """Parse a markdown heading line into level/text metadata."""

    match = re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", line)
    if not match:
        return None
    return {
        "level": len(match.group(1)),
        "text": match.group(2).strip(),
    }


def find_section_bounds(prompt: str, section_heading: str) -> Optional[Dict[str, Any]]:
    """Find byte-range bounds for a markdown section by heading text."""

    target = section_heading.strip().lower()
    if not target:
        return None

    lines = prompt.splitlines(keepends=True)
    if not lines:
        return None

    start_line_idx = None
    start_level = None
    heading_line = ""

    for idx, line in enumerate(lines):
        heading = parse_markdown_heading(line)
        if not heading:
            continue
        if heading["text"].strip().lower() == target:
            start_line_idx = idx
            start_level = heading["level"]
            heading_line = line if line.endswith("\n") else f"{line}\n"
            break

    if start_line_idx is None or start_level is None:
        return None

    end_line_idx = len(lines)
    for idx in range(start_line_idx + 1, len(lines)):
        heading = parse_markdown_heading(lines[idx])
        if heading and heading["level"] <= start_level:
            end_line_idx = idx
            break

    start_char = sum(len(line) for line in lines[:start_line_idx])
    end_char = sum(len(line) for line in lines[:end_line_idx])

    return {
        "start_char": start_char,
        "end_char": end_char,
        "heading_line": heading_line,
    }


def apply_targeted_workshop_edits(
    base_prompt: str,
    edits: List[Any],
) -> Dict[str, Any]:
    """Apply targeted edit operations against a workshop prompt draft."""

    working_prompt = base_prompt
    applied_edits: List[str] = []

    for idx, raw_edit in enumerate(edits, start=1):
        if not isinstance(raw_edit, dict):
            return {
                "success": False,
                "error": f"Edit #{idx} must be an object.",
            }

        operation = str(raw_edit.get("operation", "")).strip()
        if operation not in {"replace_text", "replace_section"}:
            return {
                "success": False,
                "error": f"Edit #{idx} has unsupported operation: {operation or 'missing operation'}",
            }

        replacement_text = raw_edit.get("replacement_text")
        if replacement_text is None:
            replacement_text = ""
        if not isinstance(replacement_text, str):
            return {
                "success": False,
                "error": f"Edit #{idx} replacement_text must be a string.",
            }

        if operation == "replace_text":
            find_text = raw_edit.get("find_text")
            if not isinstance(find_text, str) or not find_text:
                return {
                    "success": False,
                    "error": f"Edit #{idx} requires non-empty find_text for replace_text.",
                }

            occurrence = str(raw_edit.get("occurrence", "first")).strip().lower()
            if occurrence not in {"first", "last", "all"}:
                return {
                    "success": False,
                    "error": f"Edit #{idx} occurrence must be one of: first, last, all.",
                }

            if occurrence == "all":
                count = working_prompt.count(find_text)
                if count == 0:
                    return {
                        "success": False,
                        "error": f"Edit #{idx} could not find text to replace.",
                    }
                working_prompt = working_prompt.replace(find_text, replacement_text)
                applied_edits.append(
                    f"replace_text all occurrences ({count} replacements)"
                )
            else:
                pos = working_prompt.find(find_text) if occurrence == "first" else working_prompt.rfind(find_text)
                if pos < 0:
                    return {
                        "success": False,
                        "error": f"Edit #{idx} could not find text to replace.",
                    }
                working_prompt = (
                    working_prompt[:pos]
                    + replacement_text
                    + working_prompt[pos + len(find_text):]
                )
                applied_edits.append(f"replace_text {occurrence} occurrence")

        elif operation == "replace_section":
            section_heading = raw_edit.get("section_heading")
            if not isinstance(section_heading, str) or not section_heading.strip():
                return {
                    "success": False,
                    "error": f"Edit #{idx} requires section_heading for replace_section.",
                }

            bounds = find_section_bounds(working_prompt, section_heading)
            if not bounds:
                return {
                    "success": False,
                    "error": f"Edit #{idx} could not find section heading '{section_heading}'.",
                }

            replacement_block = replacement_text
            if not replacement_block.strip():
                return {
                    "success": False,
                    "error": f"Edit #{idx} replacement_text cannot be empty for replace_section.",
                }

            if not parse_markdown_heading(replacement_block.splitlines()[0] if replacement_block.splitlines() else ""):
                replacement_block = f"{bounds['heading_line']}{replacement_block.lstrip()}"

            if not replacement_block.endswith("\n"):
                replacement_block += "\n"

            start_char = bounds["start_char"]
            end_char = bounds["end_char"]
            working_prompt = (
                working_prompt[:start_char]
                + replacement_block
                + working_prompt[end_char:]
            )
            applied_edits.append(f"replace_section '{section_heading.strip()}'")

    summary = "; ".join(applied_edits) if applied_edits else "No edits applied."
    return {
        "success": True,
        "prompt": working_prompt,
        "applied_edits": applied_edits,
        "summary": summary,
    }


def fetch_trace_for_opus(trace_id: str, *, logger: Any) -> Optional[str]:
    """
    Fetch trace data from Langfuse and format it for Opus's context.

    Returns a formatted string with the trace summary, or None if fetch fails.
    """

    try:
        from langfuse import Langfuse

        client = Langfuse()

        # Fetch trace details
        trace = client.api.trace.get(trace_id)
        if not trace:
            logger.warning("Trace not found: %s", trace_id)
            return None

        # Fetch observations
        obs_response = client.api.observations.get_many(trace_id=trace_id)
        observations = list(obs_response.data) if hasattr(obs_response, "data") else []

        # Build the trace summary
        lines = []

        # Basic info
        lines.append(f"**Trace ID:** {trace_id}")
        if hasattr(trace, "input") and trace.input:
            user_input = trace.input
            if isinstance(user_input, dict):
                user_input = user_input.get("message", user_input.get("query", str(user_input)))
            lines.append(f"**User Query:** {user_input}")

        if hasattr(trace, "output") and trace.output:
            output = trace.output
            if isinstance(output, dict):
                output = output.get("response", output.get("content", str(output)))
            # Truncate very long outputs
            if len(str(output)) > 2000:
                output = str(output)[:2000] + "... [truncated]"
            lines.append(f"**Final Response:** {output}")

        # Extract agents used and tool calls
        agents_used = set()
        tool_calls = []
        trace_agent_patterns = get_trace_agent_patterns()

        for obs in observations:
            obs_type = getattr(obs, "type", None)
            obs_name = getattr(obs, "name", "")

            # Identify agents from generation observations
            if obs_type == "GENERATION":
                # Try to identify the agent
                for agent_pattern, agent_id in trace_agent_patterns.items():
                    if agent_pattern in obs_name.lower():
                        agents_used.add(agent_id)
                        break

            # Capture tool calls from spans
            if obs_type == "SPAN" and not obs_name.startswith("transfer_to_"):
                if obs_name not in ["supervisor", "agent_run", ""]:
                    tool_input = getattr(obs, "input", None)
                    tool_output = getattr(obs, "output", None)

                    # Format input
                    input_str = ""
                    if tool_input:
                        if isinstance(tool_input, dict):
                            input_str = json.dumps(tool_input, indent=2)[:500]
                        else:
                            input_str = str(tool_input)[:500]

                    # Format output (truncate)
                    output_str = ""
                    if tool_output:
                        if isinstance(tool_output, str):
                            output_str = tool_output[:300]
                        else:
                            output_str = str(tool_output)[:300]

                    tool_calls.append({
                        "name": obs_name,
                        "input": input_str,
                        "output": output_str + ("..." if len(str(tool_output or "")) > 300 else ""),
                    })

        if agents_used:
            lines.append(f"**Agents Involved:** {', '.join(sorted(agents_used))}")

        if tool_calls:
            lines.append("\n**Tool Calls:**")
            for i, tc in enumerate(tool_calls[:15], 1):
                lines.append(f"\n{i}. **{tc['name']}**")
                if tc["input"]:
                    lines.append(f"   Input: {tc['input']}")
                if tc["output"]:
                    lines.append(f"   Output: {tc['output']}")

            if len(tool_calls) > 15:
                lines.append(f"\n... and {len(tool_calls) - 15} more tool calls")

        return "\n".join(lines)

    except Exception as exc:
        logger.error("Failed to fetch trace for Opus: %s", exc, exc_info=True)
        return None


def build_opus_system_prompt(
    context: Optional[ChatContext],
    user_name: Optional[str] = None,
    user_email: Optional[str] = None,
    *,
    load_template: Callable[[], str],
    list_model_definitions: Callable[[], Iterable[Any]],
    get_prompt_catalog: Callable[[], Any],
    prepare_trace_context: Callable[[str], Optional[str]],
) -> str:
    """Build the system prompt for Opus based on UI context and user identity."""

    # Check if this user is a developer (configured in .env for security)
    developer_emails = os.getenv("PROMPT_EXPLORER_DEVELOPER_EMAILS", "").lower().split(",")
    developer_emails = [e.strip() for e in developer_emails if e.strip()]
    is_developer = user_email and user_email.lower() in developer_emails

    # User greeting - inject for everyone
    user_greeting = ""
    if user_name:
        user_greeting = f"\n\n**You are speaking with: {user_name}**\n"
        if is_developer:
            # Developer-specific prompt (content from .env for security)
            dev_prompt = os.getenv(
                "PROMPT_EXPLORER_DEVELOPER_PROMPT",
                "This user is a developer on the AI curation project. They may ask you to help with testing, debugging, or technical tasks beyond standard curator support. You can assist with these requests while maintaining your helpful assistant demeanor.",
            )
            user_greeting += f"\n{dev_prompt}\n"

    base_prompt = load_template().replace(
        "{{USER_GREETING}}",
        user_greeting,
    ).replace(
        "{{PACKAGE_DIAGNOSTIC_TOOLS}}",
        build_package_diagnostic_tools_prompt(),
    )

    if context:
        additions = []
        workshop_draft_tools: Optional[List[str]] = None

        if context.active_tab == "agent_workshop" and context.agent_workshop:
            workshop = context.agent_workshop
            workshop_draft_tools = workshop.draft_tool_ids or []
            draft_prompt = workshop.prompt_draft or ""
            selected_group_prompt = workshop.selected_group_prompt_draft or ""
            truncated = ""
            group_truncated = ""
            max_prompt_chars = 12000
            max_group_prompt_chars = 6000
            if len(draft_prompt) > max_prompt_chars:
                draft_prompt = draft_prompt[:max_prompt_chars]
                truncated = f"\n\n[Truncated to first {max_prompt_chars} chars for context.]"
            if len(selected_group_prompt) > max_group_prompt_chars:
                selected_group_prompt = selected_group_prompt[:max_group_prompt_chars]
                group_truncated = f"\n\n[Truncated to first {max_group_prompt_chars} chars for context.]"

            selected_group_prompt_block = ""
            if workshop.selected_group_id and selected_group_prompt:
                selected_group_prompt_block = f"""

<workshop_selected_group_prompt group="{workshop.selected_group_id}">
{selected_group_prompt}
</workshop_selected_group_prompt>{group_truncated}"""

            model_catalog_lines: List[str] = []
            try:
                for model in sorted(
                    [
                        model
                        for model in list_model_definitions()
                        if bool(getattr(model, "curator_visible", True))
                    ],
                    key=lambda model: (not bool(model.default), model.name.lower()),
                ):
                    reasoning_label = (
                        f"{', '.join(model.reasoning_options)} (default: {model.default_reasoning or 'none'})"
                        if model.reasoning_options
                        else "n/a"
                    )
                    model_catalog_lines.append(
                        f"- {model.name} [{model.model_id}]: "
                        f"{(model.guidance or model.description or '').strip() or 'No guidance configured.'} "
                        f"(reasoning: {reasoning_label})"
                    )
            except Exception:
                model_catalog_lines = []

            model_catalog_text = "\n".join(model_catalog_lines) if model_catalog_lines else "- Model catalog unavailable."

            additions.append(f"""
<agent_workshop_context>
## Current Context: Agent Workshop

The curator is actively iterating an agent draft in Agent Workshop.

- Template source: {workshop.template_name or workshop.template_source or 'Unknown'}
- Custom agent: {workshop.custom_agent_name or workshop.custom_agent_id or 'Unsaved draft'}
- Include group rules: {"Yes" if workshop.include_group_rules else "No"}
- Selected group: {workshop.selected_group_id or "None"}
- Has group prompt overrides: {"Yes" if workshop.has_group_prompt_overrides else "No"}
- Group override count: {workshop.group_prompt_override_count or 0}
- Template prompt stale: {"Yes" if workshop.template_prompt_stale else "No"}
- Template exists: {"Yes" if workshop.template_exists is not False else "No"}
- Draft attached tools: {", ".join(workshop_draft_tools) if workshop_draft_tools else "None"}
- Draft model: {workshop.draft_model_id or "Not set"}
- Draft reasoning: {workshop.draft_model_reasoning or "Not set"}

Agent Workshop model recommendation defaults:
- Use `gpt-5.5` with `medium` reasoning for difficult PDF extraction and deep reasoning.
- Use `gpt-5.4-mini` for fast iterative drafting, database lookup, and balanced quality/speed.

Configured model options:
{model_catalog_text}

Use this workshop context to give concrete prompt-engineering feedback, especially:
1. how to improve the editable curator overlay structure and specificity,
2. what to test next in flow execution (and when to compare with the template-source prompt),
3. how group rules may interact with the current draft.
4. proactively identify concrete prompt improvements during normal conversation and suggest them.
5. before giving authoritative advice about current prompt/tool behavior, inspect current surfaces:
   - use `refresh_workshop_prompt` before judging the current editable draft,
   - use `get_prompt` for the effective template/source prompt when it is not already in context,
   - use `get_tool_inventory` and `get_tool_details` for attached runtime tool schemas.
6. for PDF evidence extraction prompts, preserve the span workflow: `search_document` finds candidate chunks, `read_chunk` exposes deterministic `evidence_spans[].span_id`, and `record_evidence(span_ids=[...])` creates backend-copied evidence. Do not propose instructions that ask agents to generate quote strings, fuzzy-repair quotes, or confirm claims with a separate LLM.
7. before making any draft update call, ask for permission in plain language (e.g., "Want me to apply this as a targeted edit?").
8. after clear approval, call `update_workshop_prompt_draft`:
   - set `target_prompt="main"` for editable curator-overlay behavior changes,
   - set `target_prompt="group"` for editable group-specific override wording/rules and include `target_group_id`,
   - full rewrite: `apply_mode="replace"` and provide `updated_prompt`,
   - small scoped tweaks: `apply_mode="targeted_edit"` and provide `edits`.
   - never copy locked core/generated/base prompt contracts into `updated_prompt`.
9. when the curator is in Agent Workshop, do NOT call flow-only tools (`get_current_flow`, `get_available_agents`, `get_flow_templates`, `create_flow`, `validate_flow`) unless they explicitly switch to Flows.
10. after a curator applies a prompt update, verify the current `<workshop_prompt_draft>` contains the intended change and provide a quick quality review.
11. before reviewing or commenting on current prompt text, use `refresh_workshop_prompt`; after it returns, treat conversation history and older versions as historical only and never report text as present unless it appears in the refreshed `current_prompt`.
12. when proposing or applying prompt edits, use this distilled OpenAI-style prompt playbook:
   - put core instructions first, then separate context/examples with clear delimiters (`###` sections or triple quotes),
   - make directions specific and measurable (length, format, required fields, decision rules),
   - prefer explicit output schemas and short examples over vague prose,
   - replace vague wording ("brief", "not too much") with concrete bounds,
   - avoid "don't do X" alone; add the preferred behavior ("do Y instead"),
   - start with minimal/targeted edits first; escalate to larger rewrites only when needed,
   - for extraction/factual behavior, prioritize deterministic wording over creative language.
13. in reviews, explicitly check whether the updated prompt follows the playbook above and call out any misses.
14. choose the right target for edits:
   - use main prompt updates for overlay guidance that should apply across all groups,
   - use group prompt updates only for organism/group-specific exceptions or conventions.

<workshop_prompt_draft>
{draft_prompt}
</workshop_prompt_draft>{truncated}
{selected_group_prompt_block}

Prompt injection note:
- Structured output instructions are inserted near the first `## ` heading.
- If the draft lacks `## ` headings, insertion happens at the top.
</agent_workshop_context>""")

        if context.selected_agent_id:
            # Get the agent info to provide context
            service = get_prompt_catalog()
            agent = service.get_agent(context.selected_agent_id)
            if agent:
                tools_label = "Tools this agent can use"
                tools_for_context = agent.tools
                # In Agent Workshop, prefer the live draft tool attachments from UI context.
                if context.active_tab == "agent_workshop" and workshop_draft_tools is not None:
                    tools_label = "Tools attached to current workshop draft"
                    tools_for_context = workshop_draft_tools

                additions.append(f"""
## Current Context

The curator is viewing the **{agent.agent_name}** agent.

**Agent Description:** {agent.description}

**{tools_label}:** {', '.join(tools_for_context) if tools_for_context else 'None'}

**Has group-specific rules:** {'Yes' if agent.has_group_rules else 'No'}""")

                # Include the prompt content based on view mode
                if context.selected_group_id and context.selected_group_id in agent.group_rules:
                    group_rule = agent.group_rules[context.selected_group_id]
                    additions.append(f"""
### Currently Viewing: {context.selected_group_id}-Specific Rules

The curator is looking at the group-specific rules for {context.selected_group_id}. Here are those rules:

<group_rules group="{context.selected_group_id}">
{group_rule.content}
</group_rules>

And here is the base prompt that these rules extend:

<base_prompt agent="{agent.agent_id}">
{agent.base_prompt}
</base_prompt>""")
                else:
                    # Just viewing the base prompt
                    additions.append(f"""
### Currently Viewing: Base Prompt

<base_prompt agent="{agent.agent_id}">
{agent.base_prompt}
</base_prompt>""")

                    if agent.has_group_rules:
                        available_groups = list(agent.group_rules.keys())
                        additions.append(f"""
This agent has group-specific rules available for: {', '.join(available_groups)}. The curator can select a group to see how the base prompt is customized.""")

        if context.trace_id:
            # Provide lightweight trace context with tool usage instructions
            trace_context = prepare_trace_context(context.trace_id)
            if trace_context:
                additions.append(trace_context)

        # Add flow context when user is on the Flows tab
        if context.active_tab == "flows":
            flow_context = """
<flow_context>
## Current Context: Flow Builder

The curator is designing a curation flow - a guided supervisor run that executes selected agents in sequence against the flow task and loaded document.

<critical_instruction>
**MANDATORY: ALWAYS call `get_current_flow` tool FIRST before any flow discussion.**

This tool returns:
- Flow in **execution order** (following edges from entry node, not canvas placement order)
- Accurate step numbering based on actual execution sequence
- Disconnected nodes flagged as warnings
- Clean markdown representation
- `domain_envelope_analysis` for envelope-producing nodes, domain packs, object definitions, and validation schedules

**NEVER** reference flow structure, automatic validation choices, domain-envelope-producing nodes, or PDF evidence/tool contracts without calling the relevant inspection tools first. If the returned domain-pack or validator metadata is not enough for the curator's question, call `get_domain_pack_validation_plan`. Use `get_prompt`, `get_tool_inventory`, and `get_tool_details` before judging agent custom instructions, attached document tools, or PDF evidence schemas.
</critical_instruction>

<responsibilities>
**Your role:**
1. **Verify** - Check flow structure against validation checklist
2. **Suggest** - Recommend better ordering, missing steps, optimizations
3. **Explain** - Help curators understand what each agent does
4. **Debug** - Identify problems in flow structure or configuration
</responsibilities>

<validation_checklist>
**When asked to verify, check for:**
1. **Initial Instructions MUST Be First** - Every flow MUST start with the Initial Instructions node (task_input). This is the entry point that defines what the curator wants to accomplish.
2. **All Nodes Connected** - Disconnected nodes = steps that won't execute
3. **Logical Step Order** - Each agent appears in the right sequence for the curator's task
4. **Custom Instructions Redundancy** - For EACH node with custom instructions:
   - Call `get_prompt(agent_id)` to fetch the base prompt
   - Compare custom instructions to base prompt content
   - Flag any duplication (phrases, instructions, or concepts already in base)
5. **Missing Agents** - Any important processing steps absent?
6. **Redundant Steps** - Any agents called unnecessarily?
7. **Domain Envelope Production** - Which extraction nodes produce domain-envelope objects, which object types/field paths they create, and which schema/provider refs define them?
8. **Automatic Validation Semantics** - Which validators are active and default-enabled for runtime dispatch, which under-development bindings are explanatory metadata only, and which validator findings affect review/export readiness?
9. **Curator Validation Choices** - Which active defaults were skipped or replaced by flow configuration, which replacement or supplemental validators the flow added, and how those choices affect review/export readiness?

**CRITICAL for item 4:** You MUST actually call `get_prompt` for each agent with custom instructions to perform the comparison. Do NOT skip this step or guess based on agent name alone.
**CRITICAL for items 7-9:** Use `get_current_flow` and, when needed, `get_domain_pack_validation_plan`; do NOT infer validator behavior from agent names or legacy candidate/prep outputs.
**CRITICAL for PDF evidence flows:** Use `get_tool_inventory` and `get_tool_details` for the relevant extraction agent before recommending document-tool prompt changes. Preserve the `search_document` -> `read_chunk` -> `record_evidence(span_ids=[...])` workflow and the active-run evidence workspace tools; do not suggest quote-generation or fuzzy quote repair instructions.
</validation_checklist>

<flow_design_guidance>
## Flow Design Best Practices

**Every flow follows this pattern:**
1. **Initial Instructions** (REQUIRED FIRST STEP) - Define the curation task
2. **Extraction/Verification agents** - Process the document
3. **Automatic validation** - Domain-pack metadata and curator selections schedule active validators through runtime dispatch after extraction
4. **Output agent** (if exporting data) - Shape runtime-owned projections as CSV, TSV, JSON, or chat

Each step receives the flow task, loaded document context, selected agent, and
that node's custom instructions. Do not recommend custom input templates or
previous-step output prompts; earlier structured artifacts are preserved by the
runtime for review/export lookup instead of being pasted into later step
prompts.

**Initial Instructions should specify:**
- What to extract (e.g., "Extract all alleles mentioned in this paper")
- What data categories to capture (e.g., "For each allele, capture: parent gene symbol, allele identifier, phenotype description")
- Any validation steering or curator choices (e.g., "Run default validation and explain any flow opt-outs")

**When exporting to file (CSV/TSV/JSON):**
- The Initial Instructions should define WHAT data to collect
- Domain envelopes define the semantic objects; review rows and files are projections from those objects
- The formatter agent (chat_output, csv_formatter, tsv_formatter, json_formatter) should define HOW to present projected data
- Formatter custom instructions should specify column headers, row source, filters, sorting, grouping, and omitted fields when needed
- The runtime owns extraction, projection, serialization, file saving, and chat rendering; do not recommend model-authored file contents

**Example flow for allele extraction:**
1. **Initial Instructions**: "Extract alleles from this paper. For each allele, capture: parent gene symbol, allele identifier, and phenotype. Verify identifiers against the database."
2. **PDF Extraction**: Extract relevant sections
3. **Allele Extraction**: Produce domain-envelope allele objects with field paths and schema/provider refs
4. **Automatic Validation**: Scheduled validators write findings and lookup attempts back into the envelope
5. **CSV Formatter**: "Export with columns: parent_gene, allele_id, phenotype"
</flow_design_guidance>

<output_format>
**Structure your verification feedback as:**
- ✅ [What's correct] - Brief explanation
- ⚠️ [Warning] - Issue that may cause problems
- ❌ [Problem] - Must be fixed before flow will work correctly
- 💡 [Suggestion] - Optional improvement
</output_format>
</flow_context>"""

            additions.append(flow_context)

        if additions:
            base_prompt += "\n" + "\n".join(additions)

    return base_prompt
