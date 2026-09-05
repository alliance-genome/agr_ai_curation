"""Manual editor choices derived from the authoritative mapping type rules."""

from typing import Iterable

from src.lib.agent_studio.profile_mapping_service import (
    ReusableCapability, capability_catalog, capability_issues, declared_profile_path, schema_assignable,
)
from src.lib.openai_agents.config import get_generic_profile_list_page_size
from src.schemas.generic_extraction_profile import (
    ArrayValueSchema, EnumValueSchema, GenericProfileContract, ObjectValueSchema,
)


def profile_mapping_options(
    contract: GenericProfileContract, *, active_group_ids: Iterable[str], after: str | None = None,
    capabilities: Iterable[ReusableCapability] | None = None,
) -> dict:
    """Return shape-compatible slots, not guessed mappings or save approval.

    Each choice retains explicit fan-out domains. The existing complete mapping
    validator still checks shared domains, required alternatives, provider scope,
    policy and overlapping writes before save. No dispatch or persistence.
    """
    paths: list[str] = []

    def collect(fields, prefix):
        for field in fields:
            path = prefix + "." + field.key
            paths.append(path)
            schema = field.value_schema
            if isinstance(schema, ArrayValueSchema):
                path += "[]"
                paths.append(path)
                schema = schema.items
            if isinstance(schema, ObjectValueSchema):
                collect(schema.fields, path)

    collect(contract.fields, "attributes")
    fields = {path: declared_profile_path(contract, path) for path in paths}
    field_options = [{"path": path, "display_name": field.display_name or field.key,
                      "value_schema": field.value_schema.model_dump(mode="json"),
                      "required": field.required, "nullable": field.nullable,
                      "array_domains": list(domains)} for path, (field, domains) in fields.items()]
    groups = tuple(active_group_ids)
    catalog = list(capabilities) if capabilities is not None else capability_catalog(active_group_ids=groups)
    catalog.sort(key=lambda cap: cap.key())
    if after is not None:
        catalog = [cap for cap in catalog if cap.key() > after]
    size = get_generic_profile_list_page_size()
    result = []
    for cap in catalog[:size]:
        reuse = cap.binding.custom_profile_reuse
        if reuse is None:
            continue

        input_paths = {}
        for name, slot in reuse.inputs.items():
            candidates = []
            for path, (field, domains) in fields.items():
                if not (slot.allow_field
                        and (not domains or (len(domains) == 1 and reuse.supports_element_fanout))
                        and (field.value_schema.kind != "array" or reuse.supports_whole_array)
                        and schema_assignable(field.value_schema, slot.value_schema)
                        and (not field.nullable or slot.nullable)
                        and (not slot.required or field.required)):
                    continue
                if name in reuse.provider_input_slots.values():
                    allowed = {value.casefold() for value in cap.binding.allowed_provider_values}
                    if not (isinstance(field.value_schema, EnumValueSchema) and field.required
                            and not field.nullable and {v.casefold() for v in field.value_schema.values} <= allowed):
                        continue
                    if not cap.binding.allow_cross_provider and len({v.casefold() for v in field.value_schema.values}) > 1:
                        continue
                candidates.append(path)
            input_paths[name] = candidates
        output_paths = {name: [path for path, (field, domains) in fields.items()
                              if (not domains or (len(domains) == 1 and reuse.supports_element_fanout))
                              and schema_assignable(slot.value_schema, field.value_schema)
                              and (not slot.nullable or field.nullable)]
                        for name, slot in reuse.outputs.items()}
        diagnostics = capability_issues(cap, groups)
        result.append({"capability_ref": cap.ref.model_dump(mode="json"), "fingerprint": cap.fingerprint(),
                       "state": cap.binding.state.value, "selectable": not diagnostics,
                       "diagnostics": diagnostics, "metadata": cap.binding.identity_details(),
                       "input_paths": input_paths, "output_paths": output_paths})
    return {"fields": field_options, "capabilities": result,
            "next_cursor": catalog[size - 1].key() if len(catalog) > size else None}
