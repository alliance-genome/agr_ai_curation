"""Save-time capability inspection and mapping checks over the existing registry.

No dispatch or inferred mappings live here. Historical capability snapshots are
audit evidence, never permission to execute a removed or inaccessible package.
"""

from dataclasses import dataclass, replace
import hashlib
import re
from typing import Any, Iterable

from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry, ValidationBindingState, ValidatorBinding,
)
from src.lib.openai_agents.config import get_generic_profile_max_issues
from src.schemas.generic_extraction_profile import (
    ArrayValueSchema, EnumValueSchema, GenericProfileContract, ObjectValueSchema,
    ProfileField, ValueSchema, canonical_json, normalize_profile_contract,
)
from src.schemas.profile_validator_mapping import ValidatorCapabilityRef


class ProfileMappingError(ValueError):
    def __init__(self, issues: list[dict[str, str]]):
        self.issues = issues
        super().__init__("Profile validator mappings are incompatible")


@dataclass(frozen=True)
class ReusableCapability:
    ref: ValidatorCapabilityRef
    binding: ValidatorBinding
    available: bool = True
    unavailable_reason: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {"ref": self.ref.model_dump(mode="json"), "binding": self.binding.raw}

    def fingerprint(self) -> str:
        return "sha256:" + hashlib.sha256(canonical_json(self.snapshot()).encode()).hexdigest()

    def key(self) -> str:
        return canonical_json(self.ref.model_dump(mode="json"))


def capability_catalog(
    registries: Iterable[DomainPackValidationRegistry] | None = None,
    *, active_group_ids: Iterable[str] = (), user_id: int | None = None, references=(),
) -> list[ReusableCapability]:
    """Inspect opted-in declarations, reusing package identity and authorization."""
    from src.lib.config.agent_loader import get_agent_definition_for_package
    from src.lib.config.schema_discovery import resolve_output_schema
    from src.schemas.domain_validator import is_domain_validator_result_schema

    if registries is None:
        from src.lib.flows.validation_attachments import domain_pack_validation_registries
        registries = domain_pack_validation_registries().values()
    groups = set(active_group_ids)
    results = []
    for registry in registries:
        pack = registry.domain_pack
        if not pack.package_id or not pack.package_version:
            continue  # No package/version identity means no reusable capability.
        for binding in registry.bindings:
            # Explicit reuse opts in the validator, not the surrounding submission
            # envelope. An active resolver can serve custom fields while that
            # envelope is still in development; deprecated packs stay unavailable.
            if binding.custom_profile_reuse is None:
                continue
            reason = ""
            ref = binding.validator_agent
            agent = get_agent_definition_for_package(ref.package_id, ref.agent_id) if ref else None
            if binding.state is not ValidationBindingState.ACTIVE or pack.metadata.status.value == "deprecated":
                reason = "Binding is not active or domain pack is deprecated"
            elif agent is None:
                reason = "Package validator implementation is unavailable"
            elif not agent.output_schema or not is_domain_validator_result_schema(resolve_output_schema(agent.output_schema)):
                reason = "Validator output does not implement DomainValidatorResultBase"
            elif agent.access.allowed_group_ids and not groups.intersection(agent.access.allowed_group_ids):
                reason = "Validator is unavailable for your active groups"
            capability = ReusableCapability(
                ValidatorCapabilityRef(package_id=pack.package_id, package_version=pack.package_version,
                    domain_pack_id=pack.pack_id, domain_pack_version=pack.version, binding_id=binding.binding_id),
                binding, not reason, reason,
            )
            results.append(capability)
    from src.lib.agent_studio.custom_profile_validators import custom_validator_capabilities
    results.extend(custom_validator_capabilities(results, user_id=user_id,
                   active_group_ids=groups, references=references))
    counts: dict[str, int] = {}
    for capability in results:
        counts[capability.key()] = counts.get(capability.key(), 0) + 1
    return sorted([replace(capability, available=False, unavailable_reason="Composite binding identity is ambiguous")
                   if counts[capability.key()] > 1 else capability for capability in results],
                  key=lambda capability: capability.key())


def declared_profile_path(contract: GenericProfileContract, path: str) -> tuple[ProfileField, tuple[str, ...]]:
    """Resolve canonical paths with explicit [] fan-out; never use source labels."""
    if not re.fullmatch(r"attributes\.[a-z][a-z0-9_]*(?:\[\])?(?:\.[a-z][a-z0-9_]*(?:\[\])?)*", path):
        raise ValueError("Use a canonical attributes path, with [] only for explicit fan-out")
    fields = contract.fields
    traversed, arrays = "attributes", []
    effective_nullable = False
    effective_required = True
    for index, part in enumerate(path.split(".")[1:]):
        key = part.removesuffix("[]")
        field = next((field for field in fields if field.key == key), None)
        if field is None:
            raise ValueError("Path is not a declared canonical field; source labels are not paths")
        effective_nullable |= field.nullable
        effective_required &= field.required
        schema = field.value_schema
        traversed += "." + key
        if part.endswith("[]"):
            if not isinstance(schema, ArrayValueSchema):
                raise ValueError("[] requires a declared array field")
            traversed += "[]"
            arrays.append(traversed)
            schema = schema.items
        if index == len(path.split(".")) - 2:
            return field.model_copy(update={"value_schema": schema, "nullable": effective_nullable,
                                            "required": effective_required}), tuple(arrays)
        if not isinstance(schema, ObjectValueSchema):
            raise ValueError("Nested path requires a declared object")
        fields = schema.fields
    raise ValueError("A field path is required")


def schema_assignable(source: ValueSchema, target: ValueSchema) -> bool:
    """All source values must fit the destination without coercion or key dropping."""
    if isinstance(source, EnumValueSchema) and target.kind == "string":
        return True
    if source.kind == "integer" and target.kind == "number":
        return True
    if source.kind != target.kind:
        return False
    if isinstance(source, EnumValueSchema):
        assert isinstance(target, EnumValueSchema)
        return set(source.values) <= set(target.values)
    if isinstance(source, ArrayValueSchema):
        assert isinstance(target, ArrayValueSchema)
        return schema_assignable(source.items, target.items)
    if isinstance(source, ObjectValueSchema):
        assert isinstance(target, ObjectValueSchema)
        a, b = {f.key: f for f in source.fields}, {f.key: f for f in target.fields}
        if not a.keys() <= b.keys() or any(f.required and key not in a for key, f in b.items()):
            return False
        return all((not b[key].required or field.required)
                   and (not field.nullable or b[key].nullable)
                   and schema_assignable(field.value_schema, b[key].value_schema)
                   for key, field in a.items())
    return True


def capability_issues(capability: ReusableCapability, active_group_ids: Iterable[str]) -> list[str]:
    binding, reuse = capability.binding, capability.binding.custom_profile_reuse
    if not capability.available:
        return [capability.unavailable_reason or "Capability is unavailable"]
    if binding.state is not ValidationBindingState.ACTIVE or reuse is None or not reuse.enabled:
        return ["Capability is not opted-in and active"]
    problems = []
    if binding.required_any_active_group and not set(active_group_ids).intersection(binding.required_any_active_group):
        problems.append("Required active-group membership is absent")
    if set(reuse.provider_input_slots) != set(binding.provider_value_field_paths):
        problems.append("Package provider scope cannot be faithfully mapped")
    if binding.blocking and False in reuse.policy.readiness_allowed:
        problems.append("Reuse policy weakens the package-fixed blocking requirement")
    for name, selector in binding.input_fields.items():
        slot = reuse.inputs.get(name)
        if slot is None:
            problems.append(f"Package input {name} lacks typed reuse metadata")
        elif selector.required and not slot.required:
            problems.append(f"Reuse metadata weakens required input {name}")
        elif selector.source != "payload" and (
            slot.allow_field or slot.allow_constant or slot.context_selector is None
            or slot.context_selector.model_dump(mode="json", exclude_none=True)
            != selector.model_dump(mode="json", exclude_none=True)
        ):
            problems.append(f"Fixed/context input {name} must retain the exact package selector")
    if set(reuse.inputs) - binding.input_fields.keys():
        problems.append("Reuse metadata declares unknown implementation inputs")
    for slot in reuse.outputs.values():
        if slot.result_path not in binding.expected_result_fields:
            problems.append("Reusable output is not declared by the packaged binding")
    return problems


def validate_profile_mappings(
    contract: GenericProfileContract | dict[str, Any], *, active_group_ids: Iterable[str] = (),
    capabilities: Iterable[ReusableCapability] | None = None, user_id: int | None = None,
) -> list[ReusableCapability]:
    """Validate complete mappings without persisting or executing any validator."""
    profile = normalize_profile_contract(contract)
    if not profile.validator_mappings:
        return []
    groups = tuple(active_group_ids or ())
    catalog = list(capabilities) if capabilities is not None else capability_catalog(active_group_ids=groups, user_id=user_id, references=[m.capability_ref for m in profile.validator_mappings])
    by_key = {capability.key(): capability for capability in catalog}
    issues, selected, writes = [], [], []
    limit = get_generic_profile_max_issues()

    def issue(path: str, code: str, message: str):
        if len(issues) < limit:
            issues.append({"path": path, "code": code, "message": message})

    for index, mapping in enumerate(profile.validator_mappings):
        prefix = f"validator_mappings[{index}]"
        capability = by_key.get(canonical_json(mapping.capability_ref.model_dump(mode="json")))
        if capability is None:
            issue(prefix + ".capability_ref", "unavailable", "Exact package binding version is unavailable")
            continue
        selected.append(capability)
        for reason in capability_issues(capability, groups):
            issue(prefix + ".capability_ref", "not_selectable", reason)
        if capability.fingerprint() != mapping.capability_fingerprint:
            issue(prefix + ".capability_fingerprint", "identity_mismatch", "Reload the exact capability; its contract changed")
        reuse = capability.binding.custom_profile_reuse
        if reuse is None:
            continue
        if mapping.policy.unresolved not in reuse.policy.unresolved_allowed:
            issue(prefix + ".policy.unresolved", "policy", "Choose a package-allowed unresolved policy")
        if mapping.policy.blocks_readiness not in reuse.policy.readiness_allowed:
            issue(prefix + ".policy.blocks_readiness", "policy", "Cannot weaken or extend package readiness policy")
        if mapping.mode == "per_element" and not reuse.supports_element_fanout:
            issue(prefix + ".mode", "cardinality", "Capability does not support per-element fan-out")
        for alternatives in reuse.required_any_inputs:
            if not set(alternatives).intersection(mapping.inputs):
                issue(prefix + ".inputs", "missing_slot", "Map at least one input from: " + ", ".join(alternatives))
        array_domains = set()
        for name in mapping.inputs.keys() - reuse.inputs.keys():
            issue(prefix + ".inputs." + name, "unknown_slot", "Unknown capability input slot")
        for name, slot in reuse.inputs.items():
            path = prefix + ".inputs." + name
            source = mapping.inputs.get(name)
            if source is None:
                if slot.required:
                    issue(path, "missing_slot", "Map this required input slot")
                continue
            if source.source == "context":
                if slot.context_selector is None:
                    issue(path, "context", "This slot has no package-owned context selector")
                continue
            if source.source == "constant":
                if not slot.allow_constant:
                    issue(path, "constant", "Constants are not allowed for this input")
                else:
                    # Reuse the same bounded conformance service, not a second value validator.
                    from src.lib.agent_studio.profile_conformance import ResolvedGenericProfile
                    from src.schemas.agent_execution_revision import GenericProfilePin
                    from uuid import UUID
                    constant_contract = GenericProfileContract.model_validate({"name": "Constant", "semantic_class": "constant",
                        "fields": [ProfileField(key="constant_value", required=True, nullable=slot.nullable,
                                             value_schema=slot.value_schema).model_dump(mode="json")]})
                    pin = GenericProfilePin(profile_id=UUID(int=0), revision=1,
                        profile_revision_id=UUID(int=0), fingerprint=constant_contract.fingerprint())
                    if ResolvedGenericProfile(pin, constant_contract).validate_attributes({"constant_value": source.value}):
                        issue(path + ".value", "type", "Constant does not match the declared slot shape")
                continue
            if not slot.allow_field:
                issue(path, "context_only", "This input cannot read a profile field")
            try:
                field, domains = declared_profile_path(profile, source.field_path or "")
                array_domains.update(domains)
                if not schema_assignable(field.value_schema, slot.value_schema) or (field.nullable and not slot.nullable):
                    issue(path + ".field_path", "type", "Profile field is incompatible with the input shape/nullability")
                if slot.required and not field.required:
                    issue(path + ".field_path", "required", "Required input must map a required profile field")
                if field.value_schema.kind == "array" and not reuse.supports_whole_array:
                    issue(path, "cardinality", "Capability does not accept whole arrays")
            except ValueError as exc:
                issue(path + ".field_path", "path", str(exc))
        for name, destination in mapping.outputs.items():
            path = prefix + ".outputs." + name
            slot = reuse.outputs.get(name)
            if slot is None:
                issue(path, "unknown_slot", "Unknown capability output slot")
                continue
            try:
                field, domains = declared_profile_path(profile, destination)
                array_domains.update(domains)
                if mapping.mode == "per_element" and not domains:
                    issue(path, "cardinality", "Per-element output must stay inside the selected array")
                if not schema_assignable(slot.value_schema, field.value_schema) or (slot.nullable and not field.nullable):
                    issue(path, "type", "Output requires a compatible, separately declared destination")
                if any(destination == old or destination.startswith(old + ".") or old.startswith(destination + ".")
                       or destination.startswith(old + "[]") or old.startswith(destination + "[]") for old in writes):
                    issue(path, "write_conflict", "Mappings cannot write overlapping destinations")
                writes.append(destination)
            except ValueError as exc:
                issue(path, "path", str(exc))
        if (mapping.mode == "whole" and array_domains) or (mapping.mode == "per_element" and len(array_domains) != 1):
            issue(prefix + ".mode", "cardinality", "Use one shared explicit array domain for per-element mappings")
        # Provider identity must be bounded at save time; execution rechecks actual values.
        provider_values = set()
        for name in reuse.provider_input_slots.values():
            source = mapping.inputs.get(name)
            path = prefix + ".inputs." + name
            values = []
            if source and source.source == "constant" and isinstance(source.value, str):
                values = [source.value]
            elif source and source.source == "field":
                try:
                    field, _ = declared_profile_path(profile, source.field_path or "")
                    if isinstance(field.value_schema, EnumValueSchema) and field.required and not field.nullable:
                        values = field.value_schema.values
                except ValueError:
                    pass
            allowed = {value.casefold() for value in capability.binding.allowed_provider_values}
            if not values or not {value.casefold() for value in values} <= allowed:
                issue(path, "provider_scope", "Provider input must be a required bounded enum or permitted constant")
            provider_values.update(value.casefold() for value in values)
        if not capability.binding.allow_cross_provider and len(provider_values) > 1:
            issue(prefix + ".inputs", "cross_provider", "Package scope forbids ambiguous or cross-provider mapping")
    if issues:
        raise ProfileMappingError(issues)
    return selected


def persist_capability_references(db, revision, selected: list[ReusableCapability]) -> None:
    """Store an audit snapshot, rejecting reuse of a version for different bytes."""
    if not selected:
        return
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert
    from src.models.sql.profile_validator_capability import (
        ProfileValidatorCapability as CapabilityRow, ProfileValidatorCapabilityReference as Reference,
    )
    profile = normalize_profile_contract(revision.contract)
    for mapping, capability in zip(profile.validator_mappings, selected, strict=True):
        identity = capability.ref.model_dump()
        db.execute(insert(CapabilityRow).values(**identity, fingerprint=capability.fingerprint(),
                    snapshot=capability.snapshot()).on_conflict_do_nothing())
        stored = db.execute(select(CapabilityRow).filter_by(**identity)).scalar_one()
        if stored.fingerprint != capability.fingerprint() or stored.snapshot != capability.snapshot():
            raise ProfileMappingError([{"path": "validator_mappings." + mapping.mapping_id,
                "code": "capability_version_changed", "message": "Package capability changed without a new version"}])
        db.add(Reference(profile_revision_id=revision.id, mapping_id=mapping.mapping_id,
                         capability_fingerprint=stored.fingerprint))
    db.flush()
