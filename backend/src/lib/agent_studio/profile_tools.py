"""Closed profile signatures over the existing evidence-backed generic builder."""

from __future__ import annotations

from copy import deepcopy
import json
from importlib import import_module
from typing import Any, Callable

from agents import function_tool

from src.lib.agent_studio.profile_conformance import ResolvedGenericProfile


def configure_profile_tools(tools: list[Any], profile: ResolvedGenericProfile) -> list[Any]:
    """Narrow authorized installed tools; never add capabilities to a saved agent."""
    from src.lib.agent_studio.catalog_service import _get_package_tool_binding

    names = {tool.name for tool in tools}
    if not {"stage_generic_object", "finalize_generic_extraction"}.issubset(names):
        raise ValueError("Profile-bound extraction requires saved generic stage and finalization tools")
    narrowed = []
    for tool in tools:
        if tool.name == "list_generic_object_classes":
            continue
        if tool.name in {"stage_generic_object", "patch_generic_object"}:
            binding = _get_package_tool_binding(tool.name)
            if binding is None or not binding.metadata.get("builder_run_state"):
                raise ValueError("Profile-bound builder tool has no installed run-state binding")
            module = import_module(binding.import_path.split(":", 1)[0])
            raw = getattr(module, f"_{tool.name}_impl")
            tool = profile_bound_tool(raw, tool, profile)
        narrowed.append(tool)
    return narrowed


def profile_runtime_instruction(profile: ResolvedGenericProfile) -> str:
    contract = profile.contract
    instructions = (
        "This run is bound to the saved output structure " + contract.name + ". "
        "Its semantic class is " + contract.semantic_class + ". "
        "Use only the canonical fields in stage_generic_object's closed attributes schema. "
        "Source labels describe paper terminology, not output keys. The runtime fixes "
        "generic:generic_object; do not choose another class, edit the profile, or put "
        "unknown fields in another bag. Do not coerce or invent missing values. Repair "
        "reported field errors using evidence, an explicitly allowed null/enum value, "
        "or discard the candidate. Finalize the retained candidate IDs with "
        "finalize_generic_extraction before responding, including an empty list when none qualify."
    )

    if contract.description:
        instructions += (
            "\n\nAdditional curator guidance for this item type "
            "(supplements the saved agent prompt and individual field instructions):\n"
            + contract.description
            + "\n\nApply this guidance when deciding what qualifies as an item and what "
            "belongs in a separate record. Keep the saved field contract and evidence "
            "requirements in force."
        )
    return instructions


def profile_bound_tool(raw: Callable[..., Any], existing: Any, profile: ResolvedGenericProfile) -> Any:
    """Keep the callable and exact schema together through run-state rebinding."""
    name = existing.name
    if name == "stage_generic_object":
        def stage(label: str, attributes: dict[str, Any], evidence_record_ids: list[str],
                  classification_notes: list[str]) -> Any:
            return raw(class_key="generic:generic_object", label=label, attributes=attributes,
                       semantic_class=profile.contract.semantic_class,
                       evidence_record_ids=evidence_record_ids, classification_notes=classification_notes)

        impl = stage
        description = "Stage an evidence-backed record using only the saved output structure's canonical fields."
        schema = {"type": "object", "additionalProperties": False,
                  "required": ["label", "attributes", "evidence_record_ids", "classification_notes"],
                  "properties": {
                      "label": {"type": "string"}, "attributes": profile.attributes_schema(),
                      "evidence_record_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                      "classification_notes": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                  }}
    elif name == "patch_generic_object":
        def patch(candidate_id: str, updates: list[dict[str, Any]]) -> Any:
            return raw(candidate_id=candidate_id, updates=updates)

        impl = patch
        description = "Replace a declared attributes subtree or existing array index; the complete record must still conform."
        schema = {"type": "object", "additionalProperties": False,
                  "required": ["candidate_id", "updates"], "properties": {
                      "candidate_id": {"type": "string"}, "updates": profile.patch_schema(),
                  }}
    else:
        raise ValueError(f"No profile-specific signature for tool {name}")
    tool = function_tool(impl, name_override=name, description_override=description, strict_mode=False)
    tool.params_json_schema = schema
    tool.profile_bound_raw_func = impl
    tool.profile_bound_schema = deepcopy(schema)
    tool.generic_profile_ref = profile.receipt
    _guard_arguments(tool)
    return tool


def preserve_profile_tool_contract(rebuilt: Any, original: Any) -> Any:
    """Used only when rebuilding an already profile-specific callable."""
    rebuilt.params_json_schema = deepcopy(original.profile_bound_schema)
    rebuilt.profile_bound_schema = deepcopy(original.profile_bound_schema)
    rebuilt.profile_bound_raw_func = original.profile_bound_raw_func
    rebuilt.generic_profile_ref = deepcopy(original.generic_profile_ref)
    _guard_arguments(rebuilt)
    return rebuilt


def assert_profile_tool_contract(tool: Any) -> None:
    if tool.params_json_schema != tool.profile_bound_schema:
        raise ValueError("Provider adapter changed the saved profile tool schema; execution is unsafe")


def _guard_arguments(tool: Any) -> None:
    invoke = tool.on_invoke_tool
    allowed = set(tool.params_json_schema["properties"])

    async def guarded(context: Any, arguments: str) -> Any:
        try:
            parsed = json.loads(arguments)
        except (ValueError, TypeError):
            return json.dumps({"status": "error", "message": "Provide a JSON object using the declared profile tool arguments."})
        if not isinstance(parsed, dict) or set(parsed) - allowed:
            return json.dumps({"status": "error", "message": "Use only the profile tool's declared arguments; identity overrides are forbidden."})
        return await invoke(context, arguments)

    tool.on_invoke_tool = guarded
