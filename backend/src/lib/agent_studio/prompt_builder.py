"""Prompt helpers for provider-neutral Agent Studio AI Chat interactions."""

import os
import re
from typing import Any, Callable, Dict, Iterable, List, Optional

from src.lib.agent_studio.models import ChatContext
from src.lib.agent_studio.authoring_context import workshop_authoring_metadata_json
from src.lib.openai_agents.config import (
    get_agent_studio_workshop_context_group_prompt_max_chars,
    get_agent_studio_workshop_context_metadata_max_chars,
    get_agent_studio_workshop_context_prompt_max_chars,
)
from src.lib.prompts.assembly import PromptLayerBundle


def format_prompt_layers_for_opus(bundle: PromptLayerBundle, *, group_id: Optional[str]) -> str:
    """Render an effective prompt bundle with its inspection metadata and runtime order."""

    separator = "\n\n"
    combined_prompt = bundle.render(separator=separator)
    layer_blocks = []
    content_offset = 0
    for order, layer in enumerate(bundle.layers, 1):
        if layer.content:
            content_start = str(content_offset)
            content_offset += len(layer.content)
            content_end = str(content_offset)
            content_offset += len(separator)
        else:
            content_start = "omitted"
            content_end = "omitted"

        layer_blocks.append(f"""<prompt_layer order="{order}" kind="{layer.kind}" editable="{str(layer.editable).lower()}" locked="{str(layer.locked).lower()}" content_start="{content_start}" content_end="{content_end}">
<title>{layer.title}</title>
<provenance>{layer.provenance}</provenance>
<source_ref>{layer.source_ref}</source_ref>
</prompt_layer>""")

    selected_group = group_id or "none"
    return f"""### Effective Prompt Layers

The curator is inspecting the canonical prompt layers below in runtime order. Each
non-empty layer identifies its zero-based, end-exclusive character span in the
combined runtime prompt. Empty layers are marked omitted. Separator characters
between layers are not owned by either layer.

<prompt_layers agent="{bundle.agent_id}" selected_group="{selected_group}">
{chr(10).join(layer_blocks)}
</prompt_layers>

### Ordered Combined Runtime Prompt

<combined_prompt agent="{bundle.agent_id}" selected_group="{selected_group}">
{combined_prompt}
</combined_prompt>"""


def build_package_diagnostic_tools_prompt() -> str:
    """Build Agent Studio tool guidance from package-owned tool metadata."""
    from src.lib.agent_studio.catalog_service import get_tool_registry
    from src.lib.agent_studio.diagnostic_tools import get_diagnostic_tools_registry

    tool_registry = get_tool_registry()
    diagnostic_registry = get_diagnostic_tools_registry()
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
        if not diagnostic_registry.has_tool(tool_id):
            continue

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
            "assistant": "AI Chat",
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
    """Build the AI Chat system prompt from UI context and user identity."""

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
    base_prompt += """

## Live authoring capabilities

Before recommending or selecting an agent, model, runtime tool, output contract,
flow template, or Workshop group, call `search_studio_capabilities`. Treat that
authenticated live catalog—not remembered IDs or examples—as authoritative. Follow
`detail_call` / `next_call` for exact details, and search again when a fingerprint is
stale. A catalog result describes a currently visible resource; mutations and tool
invocations still perform their own authorization checks.

Explain envelope capabilities independently: pack/definition maturity, schema
references, extraction, validators, review, export, and write behavior.
In-development envelopes remain selectable when the requested operation is
available; respect explicit operation-level blockers, not a global readiness
label. Missing LinkML or validators does not prohibit extraction. General PDF
may select a fitting stageable class or exploratory generic object; it must not
escape a saved custom profile's closed contract. Never call a generic profile
LinkML-aligned or submission-ready.
"""

    if context:
        additions = []
        workshop_draft_tools: Optional[List[str]] = None

        if context.active_tab == "agent_workshop" and context.agent_workshop:
            workshop = context.agent_workshop
            workshop_draft_tools = workshop.draft_tool_ids or []
            draft_prompt = workshop.prompt_draft or ""
            selected_group_prompt = workshop.selected_group_prompt_draft or ""
            draft_prompt_total_chars = len(draft_prompt)
            selected_group_prompt_total_chars = len(selected_group_prompt)
            truncated = ""
            group_truncated = ""
            max_prompt_chars = get_agent_studio_workshop_context_prompt_max_chars()
            max_group_prompt_chars = (
                get_agent_studio_workshop_context_group_prompt_max_chars()
            )
            if draft_prompt_total_chars > max_prompt_chars:
                draft_prompt = draft_prompt[:max_prompt_chars]
                truncated = (
                    "\n\n[Incomplete preview: retained "
                    f"{len(draft_prompt)} of {draft_prompt_total_chars} characters. "
                    "Exact current main prompt content is available through callable "
                    "`refresh_workshop_prompt` with `target_prompt=\"main\"`: read its "
                    "content-free summary, then follow each `next_call` through ordered "
                    "chunks until `complete=true`.]"
                )
            if selected_group_prompt_total_chars > max_group_prompt_chars:
                selected_group_prompt = selected_group_prompt[:max_group_prompt_chars]
                group_truncated = (
                    "\n\n[Incomplete preview: retained "
                    f"{len(selected_group_prompt)} of "
                    f"{selected_group_prompt_total_chars} characters. Exact current "
                    "selected-group prompt content is available through callable "
                    "`refresh_workshop_prompt` with `target_prompt=\"group\"`: read its "
                    "content-free summary, then follow each `next_call` through ordered "
                    "chunks until `complete=true`.]"
                )

            metadata_document = workshop_authoring_metadata_json(workshop)
            metadata_total_chars = len(metadata_document)
            metadata_max_chars = get_agent_studio_workshop_context_metadata_max_chars()
            metadata_preview = metadata_document[:metadata_max_chars]
            metadata_truncated = ""
            if metadata_total_chars > metadata_max_chars:
                metadata_truncated = (
                    "\n[Incomplete metadata preview: retained "
                    f"{len(metadata_preview)} of {metadata_total_chars} characters. "
                    "Retrieve the exact metadata with `refresh_workshop_prompt` "
                    "using `target_prompt=\"metadata\"` and follow every `next_call` "
                    "until `complete=true`.]"
                )

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

<workshop_authoring_metadata_preview>
{metadata_preview}
</workshop_authoring_metadata_preview>{metadata_truncated}

Configured model options (authoritative recommendation source):
{model_catalog_text}

Recommend only from the configured options above. Use each entry's configured guidance,
recommended uses, and default reasoning rather than relying on historical model names.

Use this workshop context to give concrete prompt-engineering feedback, especially:
1. how to improve the editable main/base prompt structure and specificity,
2. what to test next in flow execution (and when to compare with the template-source prompt),
3. how group rules may interact with the current draft.
4. proactively identify concrete prompt improvements during normal conversation and suggest them.
5. before giving authoritative advice about current prompt/tool behavior, inspect current surfaces:
   - use `refresh_workshop_prompt` before judging the current editable draft; first
     read its content-free summary, then follow `next_call` until `complete=true`,
   - use `get_prompt` for the effective template/source prompt when it is not already in context,
   - use `get_tool_inventory` and `get_tool_details` for attached runtime tool schemas.
6. for PDF evidence extraction prompts, preserve the span workflow: `search_document` finds candidate chunks, `read_chunk` exposes deterministic `evidence_spans[].span_id`, and `record_evidence(span_ids=[...])` creates backend-copied evidence. Do not propose instructions that ask agents to generate quote strings, fuzzy-repair quotes, or confirm claims with a separate LLM.
7. For clear build/configure/edit requests, call `propose_workshop_draft_update` directly with
   the exact draft fingerprint and bounded semantic operations. Discover authorized capabilities
   through the live catalog. Include every required setting for new drafts, preserve unrelated
   fields in targeted edits, and state reversible assumptions in the change summary.
8. Proposal generation is read-only and requires no preliminary permission. The curator reviews
   the complete diff and chooses Apply or Cancel; Save remains a separate curator action.
   Never edit locked/generated prompt layers or inherited group restrictions. Clearing output
   explicitly means no structured output. Use typed edit_profile operations for profile
   basics, canonical fields, source labels and validator mappings; never put authoritative
   profile JSON in prompt text.
   For custom extraction, establish the extracted thing and one-record boundary first.
   Support ONE item type per custom agent for now. Build only the details the curator asks
   for; reagents, paper labels, source status and suppliers are examples, not default fields.
   Use the curator's language: Type of item, Additional guidance for this item type,
   details, parts, Always include, and Allow an empty answer if the paper doesn’t say.
   Walk through the item type, its details, and review. Do not ask curators to supply
   technical keys, semantic classes, source aliases or validator mappings to get started.
   Generate stable canonical identifiers yourself: new field keys use detail_ plus a
   lowercase snake_case name, unique among sibling keys AND source labels. Preserve existing
   keys, aliases and the semantic_class when renaming display names. Source labels are
   optional matching metadata, not collected answers; leave them empty unless needed.
   Ask only questions that materially affect the extraction, then propose a useful draft.
   Use one answer per detail: text, whole number, decimal number, yes/no, choices, or
   one object (an answer with several parts). Do not create arrays or repeating groups.
   A part uses only a scalar or enum answer; never put groups inside parts. Keep a supplier
   name and catalog number paired as sibling parts of one object. Add another part to
   that SAME parent field_path; never create a second item type or an extra wrapper group.
   The group itself pairs its parts. Always include maps to required (must exist), and
   applies to a part only when its parent answer is included. Checking Stock number and
   leaving Name unchecked requires the number but allows the name to be absent.
   Allow an empty answer maps to nullable (may explicitly be unknown). These controls
   are independent: required=true, nullable=true includes the detail but permits null;
   required=true, nullable=false requires an actual value. Never invent a missing value
   or turn on nullable just to make validation pass. Explain this only when relevant.
   Additional guidance is the profile description: a short description of what qualifies
   as an item, what to include/exclude, and what belongs in a separate record. The saved
   description is passed to the extraction LLM IN ADDITION TO the saved agent prompt and
   individual detail instructions. Encourage a brief complementary description; do not
   demand a duplicate full prompt or replace the earlier prompt when changing this field.
   Use update_basics with basics_update for targeted item name/description edits.
   Use update_field with field_update for detail/part display_name, description, required,
   nullable or value_schema changes. Omitted settings remain unchanged; use false or empty
   text to clear a setting, not null. Use add_field to append a detail or sibling part;
   use remove_field and reorder_fields for removal and order. Duplicate via add_field with
   a fresh unique key and cleared aliases. Only replace_field for a full deliberate replacement.
   Changing an answer format can discard choices or parts: retain compatible content and
   explicitly describe any removal in the proposal. Existing saved lists/deeper groups
   remain intact during unrelated edits; do not silently flatten them. If they need format
   changes, explain the one-answer design and propose the explicit conversion for review.
   After a proposal, describe what changed using display names and the parent group name.
   Adding a part keeps the curator at the parent parts table; Edit opens that part's settings.
   Always include is available in that table; the question-mark popup explains it. Done
   returns from a part to its parent and keeps local edits; it does not save the agent.
   inspect_workshop_profile reads current output, accessible saved profiles/exact revisions,
   compatible validator_options and neutral preview values. Inspect current data before
   proposing changes; discovery never selects or saves a resource. The current draft includes
   manual edits sent with this chat turn, every display_name and description, keys and parent
   groups. Resolve the curator's names to canonical field_path keys within the named group;
   if identical names occur in different groups and the target is unclear, ask which group.
   After Apply or manual edits, inspect current again and use the fresh fingerprint. A stale
   proposal must be regenerated, never overwrite the curator's intervening changes. Do not
   claim a proposal has changed the live draft until Apply reports success. Choose output through
   the existing select_output operation: profile_bound_generic for a closed custom structure,
   unprofiled_generic only for explicitly exploratory attributes, or an available packaged
   format (development maturity is advisory). A null schema never implies open extraction.
   Generic Objects retain system-owned identity, label, evidence and provenance. Profile
   attributes are closed: every permitted key is declared; optional fields may be absent.
   "Synonyms / source labels (not output fields)" recognize one canonical key, not extra keys.
   Structural conformance is always enforced. Semantic validators require explicit compatible
   opted-in capabilities, exact capability references/fingerprints and typed mappings.
   Never infer a validator solely from a field name, invent a capability or attach an arbitrary
   validator agent. Use "Validation", "Attach validator" and "Validator attached" with
   curators. An attachment configures validation; it does not mean an answer passed validation.
   To attach, change or remove a validator on a detail OR part, inspect current, then call
   inspect_workshop_profile(action="validator_options"). Follow next_cursor with after when
   needed. This authorized catalog includes built-in/installed-package validators and eligible
   saved Workshop custom validators. metadata.origin identifies package versus custom_agent;
   metadata.custom_validator records the exact saved custom revision. Use returned display
   names when guiding the curator, not binding IDs, fingerprints or internal field paths.
   Only offer selectable capabilities whose input_paths include the intended canonical field.
   Compatibility of the answer format is necessary but does not establish semantic fit.
   Explain what information the validator validates and which input the detail supplies,
   for example "Use Gene identifier as Gene id". Ask a focused question if the meaning or
   input association is ambiguous; a clear requested attachment needs no extra permission.
   Propose edit_profile with action=set_mapping, using the returned capability_ref and
   fingerprint, an explicit inputs slot/field_path association, and the supported policy.
   A part's input path identifies that part within its parent, not the whole parent answer.
   Other parts are not automatically validated. Use the same mapping_id to edit an existing
   attachment; remove_mapping requires that exact mapping_id. Preserve unrelated mappings,
   field definitions, parts and the earlier agent prompt. Never construct a custom pin from
   a name or select the mutable current head in place of a returned saved revision.
   Explain unresolved outcomes and any supported readiness/export blocking in plain language.
   Do not add output fields or enable blocking merely to attach a validator. When no validated
   values need writing back, outputs may be empty. If the curator wants resolved values saved,
   explicitly associate returned output slots with compatible existing or requested details.
   Apply updates the live draft and its Validator attached indicators; Workshop Save remains
   separate. A parent can show No and "1 part has a validator" because only the part is mapped.
   If no capability fits, explain that no compatible semantic validator is available and
   structural validation still applies. Do not suggest creating an arbitrary custom agent
   as a workaround: a Workshop validator must appear as eligible in validator_options.
   Verify custom fields against their pinned profile, not generic_reagent_candidate or an
   unrelated packaged envelope. A custom profile is not LinkML-aligned or submission-ready.
   Extraction-time agents cannot edit their saved contract. If asked to save, explain that
   the curator must activate Workshop Save; never invoke persistence or open its confirmation.
9. When in Workshop, use Workshop capabilities; flow editing resumes on the Flows tab.
11. before reviewing or commenting on current prompt text, use `refresh_workshop_prompt`; read the summary and follow every deterministic `next_call` until `complete=true`. Reconstruct the exact text from ordered chunk ranges, treat conversation history and older versions as historical, and never report text as present unless it appears in those refreshed chunks.
   - every ID listed in `group_prompt_override_ids` is callable with `target_prompt="group"` and `target_group_id`; inspect each relevant override rather than assuming only the selected group exists.
   - if the metadata preview is incomplete, reconstruct it with `target_prompt="metadata"` before making metadata-dependent claims.
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
                    tools_for_context = [
                        "See workshop_authoring_metadata_preview (or its exact metadata continuation)"
                    ]

                additions.append(f"""
## Current Context

The curator is viewing the **{agent.agent_name}** agent.

**Agent Description:** {agent.description}

**{tools_label}:** {', '.join(tools_for_context) if tools_for_context else 'None'}

**Has group-specific rules:** {'Yes' if agent.has_group_rules else 'No'}""")

                selected_group_id = (
                    context.selected_group_id
                    if context.selected_group_id in agent.group_rules
                    else None
                )
                bundle = service.get_effective_prompt_bundle(
                    context.selected_agent_id,
                    group_id=selected_group_id,
                )
                if bundle is not None:
                    additions.append(
                        format_prompt_layers_for_opus(bundle, group_id=selected_group_id)
                    )

                if agent.has_group_rules:
                    available_groups = list(agent.group_rules.keys())
                    additions.append(f"""
This agent has group-specific rules available for: {', '.join(available_groups)}. The selected group is {selected_group_id or 'None'}.""")

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

This tool returns the `current_flow_manifest_v1` contract:
- `ordered_control_node_ids` and `executable_agent_node_ids` for the control path and its ordinary agents
- `output_node_ids` and `validation_sidecar_node_ids` for attached Output and validation nodes
- `findings` and `has_critical_issues` for authoritative first-call verification status
- `detail_calls` with valid targeted tools for topology, node configuration, exact instructions, projection plans, validation warnings, and validation schedules omitted from the manifest

Use the targeted tools named in `detail_calls` to retrieve omitted details; do not infer or reconstruct the removed aggregate response.

For Flow Builder authoring:
- When the curator clearly asks to build, fix, or change the flow, inspect the
  relevant current-flow and live-catalog details and call
  `propose_flow_draft_update` without asking for preliminary permission.
- Ask one focused question only when a material product choice is genuinely
  ambiguous. Do not ask the curator for node IDs, edge IDs, output keys,
  positions, or other application-owned mechanics.
- Use only semantic operations. The returned candidate is a transient proposal,
  not an applied or saved flow. Tell the curator to review and Apply or Cancel it.
- If proposal validation returns blocking findings, repair them within the
  bounded turn using another proposal call. Do not claim success until the tool
  returns `valid=true` and `pending_user_approval=true`.
- Never represent Apply as Save. Only the editor's explicit Save action persists
  a flow.

For verification, follow this targeted evidence protocol:
1. Treat the first manifest as authoritative. FAIL if `has_critical_issues=true` or any `findings` entry has severity `CRITICAL`.
2. Reconstruct exact `task_instructions`, every present `custom_instructions`, and each judgment-relevant `step_goal` with `get_current_flow_instructions(node_id, field, cursor, limit)`. Execute the returned `next_call` until `complete=true` for every required field.
3. Inspect the `get_current_flow_topology` sections `issues`, `control_path`, `control_edges`, `output_bindings`, and `validation_sidecars`. Fetch relevant `get_current_flow_node` scalar details, `get_current_flow_projection_plan` field or JSON-Pointer sections, `get_current_flow_validation_warnings` pages, and `get_current_flow_validation_schedule` sections (`selections`, `scheduled_validators`, `opt_outs`, `replacement_validators`, `supplemental_validators`, `inactive_metadata`) only when the verification criteria require them. For every paged current-flow detail response, execute its returned `next_call` until `complete=true` and no `next_call` remains.
4. Call `get_available_agents(category="Output")` and execute each returned `next_call` through ordinary pages and exact record chunks until `complete=true` and no `next_call` remains; an unfiltered page cannot prove the Output boundary. Output agents are attachment branches with ordered `source_steps`, not terminal control nodes, so do not require the control path to end with an Output agent.
5. Before judging a prompt, call `get_prompt(agent_id, group_id, view="summary")`, then reconstruct every required `view="effective_prompt"` or selected `view="layer"` text through `next_cursor` until `complete=true`. A custom-instruction judgment requires both the exact node `custom_instructions` and the complete relevant base/effective prompt.
6. For document/PDF capability claims, use `get_tool_inventory(agent_id=<node agent>)` or another focused query and follow `next_cursor` until `truncated=false` and no `next_cursor` remains before judging capability or reporting PASS. Then use method/PDF-level `get_tool_details(tool_id, agent_id)`; never use the unsafe global inventory or oversized parent-tool metadata.
7. For domain or validator claims, call `get_domain_pack_validation_plan(agent_id=<node agent> or domain_pack_id=<id>)` for its compact summary, then retrieve only evidence-relevant section pages from `object_definitions`, `fields`, `validators`, `validator_bindings`, `field_policies`, or `validation_attachments` until complete.

**PASS gate:** NEVER report PASS when a required detail is incomplete, selected text or a section has another page, or any required response is `compacted_tool_result`. Duplicate `output_key` is HIGH unless authoritative validation classifies it CRITICAL. Keep suggestions evidence-based; do not page through unrelated catalogs or domain metadata speculatively.

Do not recommend standalone flow steps for validators that are absent from `get_available_agents`; those validators are attachment-only and run through validation attachments/default runtime dispatch.
</critical_instruction>

<responsibilities>
**Your role:**
1. **Verify** - Check flow structure against validation checklist
2. **Suggest** - Recommend better ordering, missing steps, optimizations
3. **Explain** - Help curators understand what each agent does
4. **Debug** - Identify problems in flow structure or configuration
5. **Author proposals** - Compile requested changes for explicit curator review
</responsibilities>

<validation_checklist>
**When asked to verify, check for:**
1. **Initial Instructions MUST Be First** - Every flow MUST start with the Initial Instructions node (task_input). This is the entry point that defines what the curator wants to accomplish.
2. **All Nodes Connected** - Disconnected nodes = steps that won't execute
3. **Logical Step Order** - Each agent appears in the right sequence for the curator's task
4. **Custom Instructions Redundancy** - For EACH node with custom instructions:
   - Retrieve its exact `custom_instructions` through completion
   - Retrieve the prompt summary and complete relevant base/effective text through completion
   - Compare the exact custom instructions to that exact prompt content
   - Flag any duplication (phrases, instructions, or concepts already in base)
5. **Missing Agents** - Any important processing steps absent?
6. **Redundant Steps** - Any agents called unnecessarily?
7. **Domain Envelope Production** - Which extraction nodes produce domain-envelope objects, which object types/field paths they create, and which schema/provider refs define them?
8. **Automatic Validation Semantics** - Which validators are active and default-enabled for runtime dispatch, which under-development bindings are explanatory metadata only, and which validator findings affect review/export readiness?
9. **Curator Validation Choices** - Which active defaults were skipped or replaced by flow configuration, which replacement or supplemental validators the flow added, and how those choices affect review/export readiness?

**CRITICAL for item 4:** You MUST actually complete the targeted instruction and prompt calls for each agent with custom instructions. Do NOT skip this step or guess based on agent name alone.
**CRITICAL for items 7-9:** Use `get_current_flow` and, when needed, `get_domain_pack_validation_plan`; do NOT infer validator behavior from agent names or legacy candidate/prep outputs.
**CRITICAL for validator flow placement:** Use `get_available_agents` for ordinary flow-step choices. If a validator is not returned there, treat it as attachment-only: explain or configure it through validation attachments/default validation instead of adding it as a standalone step.
**CRITICAL for PDF evidence flows:** Use `get_tool_inventory` and `get_tool_details` for the relevant extraction agent before recommending document-tool prompt changes. Preserve the `search_document` -> `read_chunk` -> `record_evidence(span_ids=[...])` workflow and the active-run evidence workspace tools; do not suggest quote-generation or fuzzy quote repair instructions.
</validation_checklist>

<flow_design_guidance>
## Flow Design Best Practices

**Every flow follows this pattern:**
1. **Initial Instructions** (REQUIRED FIRST STEP) - Define the curation task
2. **Extraction/Verification agents** - Process the document
3. **Automatic validation** - Domain-pack metadata and curator selections schedule active validators through runtime dispatch after extraction
4. **Output branches** (if exporting data) - Attach each CSV, TSV, JSON, or chat formatter to one or more earlier extraction or typed validation nodes through ordered `source_steps`

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
- Every formatter is a terminal output branch bound through ordered `source_steps` to one or more earlier extraction or typed validation results; grouped sources are projected together in that declared order
- Multiple formatters may attach to one extractor, and a flow may attach formatters to different extractors; each branch produces its own independent artifact
- The ordinary control-flow chain may continue after an output branch. Do not describe an output attachment as passing data into the next extraction step
- Filename metadata is runtime-owned. Use output_filename_template with built-ins such as {{input_filename_stem}} and {{timestamp}}; do not require document names or timestamps to exist as extraction fields
- The formatter agent (chat_output, csv_formatter, tsv_formatter, json_formatter) should define HOW to present projected data
- Formatter custom instructions should specify column headers, row source, filters, sorting, grouping, and omitted fields when needed
- When the curator requests fixed saved columns or a deterministic saved mapping, search output_contract capabilities for formatter_projection_plan and read its complete json_schema before proposing update_step.projection_plan. Read get_current_flow_projection_plan with the output node_id and view=source_fields, following all next_call pages. Use each returned field's ref verbatim, never its profile_path or an inferred attributes path. Omit source_keys/source_extraction_result_ids when using all attached sources; node output_key is not a runtime artifact selector. Do not guess enum values, column keys or transform properties, and do not substitute instructions alone for a requested saved plan. Repair each precise projection validation finding before presenting Apply.
- The runtime owns extraction, projection, serialization, file saving, and chat rendering; do not recommend model-authored file contents

**Example flow for allele extraction:**
1. **Initial Instructions**: "Extract alleles from this paper. For each allele, capture: parent gene symbol, allele identifier, and phenotype. Run the default validation attachments and report any unresolved identifiers."
2. **PDF Extraction**: Extract relevant sections
3. **Allele Extraction**: Produce domain-envelope allele objects with field paths and schema/provider refs
4. **Automatic Validation**: Scheduled validators write findings and lookup attempts back into the envelope
5. **CSV Formatter branch attached to Allele Extraction**: "Export with columns: parent_gene, allele_id, phenotype"
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
