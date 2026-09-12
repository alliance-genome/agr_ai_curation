"""Request-local validation overlays for exact, closed generic profiles.

The package catalog is reauthorized on each resolution. Nothing is installed in
the global generic registry and unavailable mappings remain explicit outcomes.
This module compiles existing bindings; request construction and dispatch stay
in the ordinary domain-pack engine.
"""

from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any, Iterable, Mapping

from sqlalchemy.orm import Session

from src.lib.agent_studio.profile_conformance import ProfileIdentityError, ResolvedGenericProfile
from src.lib.agent_studio.profile_mapping_service import (
    ProfileMappingError, ReusableCapability, capability_catalog,
    declared_profile_path, validate_profile_mappings,
)
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry, ValidatorBinding, ValidationAttachmentOption, ValidationBindingState,
    _build_field_policies, _validation_attachment_id,
)
from src.schemas.agent_execution_revision import AgentExecutionReceipt
from src.schemas.domain_envelope import DomainEnvelope, ValidationFinding, ValidationFindingSeverity, ValidationFindingStatus
from src.schemas.domain_pack_metadata import (
    DomainPackEnumDefinition, DomainPackEnumValue, DomainPackFieldDefinition, DomainPackFieldType, DomainPackInputSelector,
    DomainPackObjectDefinition,
)
from src.schemas.generic_extraction_profile import ProfileField, canonical_json
from src.schemas.profile_validator_mapping import ProfileValidatorMapping


@dataclass(frozen=True)
class UnavailableProfileMapping:
    mapping: ProfileValidatorMapping
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ProfileValidationContext:
    receipt: AgentExecutionReceipt
    profile: ResolvedGenericProfile
    registry: DomainPackValidationRegistry
    capabilities: tuple[ReusableCapability, ...]
    unavailable: tuple[UnavailableProfileMapping, ...]

    @property
    def identity(self) -> str:
        """Immutable identity only; this is never an authorization cache key."""
        return canonical_json({
            "profile": self.profile.receipt,
            "capabilities": [{"ref": m.capability_ref.model_dump(mode="json"),
                              "fingerprint": m.capability_fingerprint}
                             for m in self.profile.contract.validator_mappings],
        })


def resolve_profile_validation(
    receipt: AgentExecutionReceipt,
    generic_pack: LoadedDomainPack,
    *,
    active_group_ids: Iterable[str] = (),
    db: Session | None = None,
    user_id: int | str | None = None,
) -> ProfileValidationContext | None:
    """Resolve persisted identity, never a mutable head or global generic fallback."""
    if receipt.output_contract.output_mode != "profile_bound_generic":
        return None
    from src.lib.curation_workspace.execution_contracts import load_receipt_profile, resolve_receipt_profile
    profile = resolve_receipt_profile(db, receipt) if db is not None else load_receipt_profile(receipt)
    if profile is None:
        raise ProfileIdentityError("The pinned validation profile is unavailable")
    return compile_profile_validation(receipt, profile, generic_pack, active_group_ids=active_group_ids, user_id=user_id)


def resolve_envelope_profile_validation(
    envelope: DomainEnvelope,
    generic_pack: LoadedDomainPack,
    *,
    active_group_ids: Iterable[str] = (),
    db: Session | None = None,
    user_id: int | str | None = None,
) -> ProfileValidationContext | None:
    """Use authoritative normalized identity; never infer a pin from model output.

    Profile-marked output without its durable execution receipt is invalid, not
    an invitation to dispatch the open generic registry's validators.
    """
    raw_receipt = envelope.metadata.get("execution_receipt")
    extraction_metadata = envelope.metadata.get("extraction_metadata")
    provenance = (extraction_metadata.get("provenance")
                  if isinstance(extraction_metadata, Mapping) else None)
    profile_marked = (
        isinstance(provenance, Mapping) and provenance.get("generic_profile_ref") is not None
    ) or any(obj.metadata.get("generic_profile_ref") is not None for obj in envelope.extracted_objects)
    if raw_receipt is None:
        if profile_marked:
            raise ProfileIdentityError("Profile validation requires the authoritative execution receipt")
        return None
    receipt = AgentExecutionReceipt.model_validate(raw_receipt)
    if receipt.output_contract.output_mode != "profile_bound_generic":
        if profile_marked:
            raise ProfileIdentityError("Profile output conflicts with its execution receipt")
        return None
    context = resolve_profile_validation(receipt, generic_pack, active_group_ids=active_group_ids, db=db, user_id=user_id)
    assert context is not None
    from src.lib.curation_workspace.execution_contracts import require_resolved_profile_conformance
    require_resolved_profile_conformance(context.profile, receipt, envelope.model_dump(mode="json"))
    return context


def compile_profile_validation(
    receipt: AgentExecutionReceipt,
    profile: ResolvedGenericProfile,
    generic_pack: LoadedDomainPack,
    *,
    active_group_ids: Iterable[str] = (),
    capabilities: Iterable[ReusableCapability] | None = None,
    user_id: int | str | None = None,
) -> ProfileValidationContext:
    """Compile approved paths and retain every unavailable pinned mapping.

    An injected catalog is useful for deterministic tests; production callers
    omit it so revocation/activation is checked anew, even for the same receipt.
    """
    pin = receipt.output_contract.generic_profile_ref
    if pin is None or receipt.output_contract.output_mode != "profile_bound_generic":
        raise ProfileIdentityError("Profile validation requires a profile-bound execution receipt")
    profile.require_receipt(pin.model_dump(mode="json"))
    if generic_pack.pack_id != "generic":
        raise ProfileIdentityError("Custom profiles require the generic domain pack")
    contract = profile.contract
    groups = tuple(active_group_ids)
    from src.lib.agent_studio.custom_profile_validators import runtime_validator_user_id
    catalog = list(capabilities) if capabilities is not None else capability_catalog(
        active_group_ids=groups, user_id=runtime_validator_user_id(user_id),
        references=[m.capability_ref for m in contract.validator_mappings])
    problems: list[dict[str, str]] = []
    try:
        validate_profile_mappings(contract, active_group_ids=groups, capabilities=catalog)
    except ProfileMappingError as exc:
        problems = exc.issues
    by_key = {cap.key(): cap for cap in catalog}
    bindings, selected, unavailable = [], [], []
    fanout_paths: set[str] = set()
    for index, mapping in enumerate(contract.validator_mappings):
        reasons = [issue["message"] for issue in problems
                   if issue["path"].startswith(f"validator_mappings[{index}]")
                   or issue["code"] == "write_conflict"]
        # The shared diagnostic list is bounded. Recheck each mapping so a long
        # list of earlier errors cannot hide a later revoked/invalid capability.
        try:
            validate_profile_mappings(contract.model_copy(update={"validator_mappings": [mapping]}),
                                      active_group_ids=groups, capabilities=catalog)
        except ProfileMappingError as exc:
            reasons.extend(issue["message"] for issue in exc.issues)
        capability = by_key.get(canonical_json(mapping.capability_ref.model_dump(mode="json")))
        if reasons or capability is None:
            unavailable.append(UnavailableProfileMapping(mapping, tuple(reasons or ["Exact capability is unavailable"])))
            continue
        binding, array_path = _compile_binding(profile, mapping, capability)
        if array_path is not None:
            fanout_paths.add(array_path)
        bindings.append(binding)
        selected.append(capability)

    fields, enums = _profile_fields(contract.fields, fanout_paths)
    # Only the closed profile's fields and bindings enter the overlay: no
    # generic-proxy aliases, source mirrors, inferred reference object classes,
    # packaged validators, or LinkML model claims leak into this context.
    metadata = generic_pack.metadata.model_copy(deep=True, update={
        "object_definitions": [DomainPackObjectDefinition(
            object_type="generic_object", display_name=contract.name,
            fields=fields, metadata={"generic_profile_ref": profile.receipt,
                "workspace_display": {"groups": [{"id": "profile", "label": contract.name,
                    "fields": ["attributes." + field.key for field in contract.fields]}]}},
        )],
        "enum_definitions": enums,
        "model_definitions": [], "schema_refs": [], "fixture_packs": [],
        "metadata": {"generic_profile_ref": profile.receipt},
    })
    pack = replace(generic_pack, metadata=metadata)
    registry = DomainPackValidationRegistry(
        domain_pack=pack, validator_metadata=(), bindings=tuple(bindings),
        field_policies=_build_field_policies(pack, tuple(bindings)),
    )
    return ProfileValidationContext(receipt, profile, registry, tuple(selected), tuple(unavailable))


def _compile_binding(profile, mapping, capability) -> tuple[ValidatorBinding, str | None]:
    source = capability.binding
    reuse = source.custom_profile_reuse
    assert reuse is not None  # validate_profile_mappings established opt-in.
    inputs = {}
    array_path = None
    for name, mapped in mapping.inputs.items():
        slot = reuse.inputs[name]
        if mapped.source == "context":
            assert slot.context_selector is not None
            inputs[name] = slot.context_selector.model_copy(deep=True)
        elif mapped.source == "constant":
            inputs[name] = DomainPackInputSelector(source="literal", value=deepcopy(mapped.value),
                                                  required=slot.required, allow_multiple=slot.value_schema.kind == "array")
        else:
            _, domains = declared_profile_path(profile.contract, mapped.field_path)
            if domains:
                array_path = domains[0].replace("[]", "")
            inputs[name] = DomainPackInputSelector(source="payload", path=mapped.field_path.replace("[]", ""),
                                                  required=slot.required, allow_multiple=slot.value_schema.kind == "array")
    for destination in mapping.outputs.values():
        _, domains = declared_profile_path(profile.contract, destination)
        if domains:
            array_path = domains[0].replace("[]", "")
    result_fields = {reuse.outputs[name].result_path: path.replace("[]", "")
                     for name, path in mapping.outputs.items()}
    provider_selectors = {path: inputs[name].model_dump(mode="json", exclude_none=True)
                          for path, name in reuse.provider_input_slots.items()}
    # Keep constants as selectors, not invented semantic payload attributes.
    # The profile-aware dispatch guard uses this full policy before executing.
    provider_paths = tuple(selector["path"] for selector in provider_selectors.values()
                           if selector["source"] == "payload")
    provenance = {"generic_profile_ref": profile.receipt,
                  "mapping": mapping.model_dump(mode="json"),
                  "provider_selectors": provider_selectors,
                  "source_group_scope": {
                      "required_any_active_group": list(source.required_any_active_group),
                      "provider_value_field_paths": list(source.provider_value_field_paths),
                      "allowed_provider_values": list(source.allowed_provider_values),
                      "allow_cross_provider": source.allow_cross_provider,
                  }}
    return replace(source,
        binding_id=profile_mapping_binding_id(profile, mapping),
        source_scope="field" if array_path else "object", source_object_type="generic_object",
        source_field_path=array_path, applies_to_domain_pack_id="generic",
        object_types=("generic_object",), object_roles=(), field_paths=(array_path,) if array_path else (),
        field_types=(), input_fields=inputs, expected_result_fields=result_fields,
        provider_value_field_paths=provider_paths,
        blocking=mapping.policy.blocks_readiness, required=source.required or mapping.policy.blocks_readiness,
        allow_opt_out=False, curator_override_allowed=False,
        custom_profile_reuse=None, raw={"profile_validation": provenance, **({"custom_validator": source.raw["custom_validator"]} if source.raw.get("custom_validator") else {})},
    ), array_path


def profile_mapping_binding_id(profile: ResolvedGenericProfile, mapping: ProfileValidatorMapping) -> str:
    return "profile-" + profile.receipt["fingerprint"].removeprefix("sha256:") + "-" + mapping.mapping_id


def profile_validation_attachment_options(context: ProfileValidationContext) -> tuple[ValidationAttachmentOption, ...]:
    """Keep selected mappings addressable even when their capability is revoked.

    The attachment represents the saved mapping, not a claim that its underlying
    validator is executable. Runtime resolution produces the unavailable finding.
    """
    options = list(context.registry.validation_attachment_options())
    for unavailable in context.unavailable:
        mapping = unavailable.mapping
        binding_id = profile_mapping_binding_id(context.profile, mapping)
        array_path = None
        paths = [item.field_path for item in mapping.inputs.values() if item.source == "field"]
        paths.extend(mapping.outputs.values())
        for path in paths:
            assert path is not None  # Field-source mappings require a path in the saved contract.
            _, domains = declared_profile_path(context.profile.contract, path)
            if domains:
                array_path = domains[0].replace("[]", "")
        scope = "field" if array_path else "object"
        options.append(ValidationAttachmentOption(
            attachment_id=_validation_attachment_id("generic", "binding", binding_id, scope,
                object_type="generic_object", field_path=array_path),
            domain_pack_id="generic", domain_pack_version=context.registry.domain_pack.version,
            validator_id=mapping.capability_ref.binding_id,
            validator_binding_id=binding_id, state=ValidationBindingState.ACTIVE,
            scope=scope, object_type="generic_object", field_path=array_path,
            label=mapping.mapping_id, description="Selected profile mapping; validator currently unavailable",
            reason="; ".join(unavailable.reasons),
            affected_fields=tuple(mapping.outputs.values()), default_enabled=True,
            export_blocking=mapping.policy.blocks_readiness, required=mapping.policy.blocks_readiness, allow_opt_out=False,
        ))
    return tuple(sorted(options, key=lambda option: option.attachment_id))


def profile_validation_attachment_metadata(context: ProfileValidationContext) -> list[dict[str, Any]]:
    """Expose live availability without changing the saved Flow selection state."""
    unavailable = {profile_mapping_binding_id(context.profile, item.mapping): list(item.reasons)
                   for item in context.unavailable}
    metadata = [option.to_dict() for option in profile_validation_attachment_options(context)]
    for item in metadata:
        reasons = unavailable.get(item["validator_binding_id"], [])
        item.update(available=not reasons, unavailable_reasons=reasons)
    return metadata


def _profile_fields(profile_fields: list[ProfileField], fanout_paths: set[str]):
    fields = [DomainPackFieldDefinition(field_path="semantic_class", field_type=DomainPackFieldType.STRING, required=True),
              DomainPackFieldDefinition(field_path="attributes", field_type=DomainPackFieldType.OBJECT, required=True)]
    enums = []

    def visit(field: ProfileField, prefix: str):
        path = prefix + "." + field.key
        schema = field.value_schema
        enum_id = None
        if schema.kind == "enum":
            enum_id = "profile_" + sha256(path.encode()).hexdigest()
            enums.append(DomainPackEnumDefinition(enum_id=enum_id, display_name=field.key,
                                                 values=[DomainPackEnumValue(value=value) for value in schema.values]))
        fields.append(DomainPackFieldDefinition(
            field_path=path, field_type=DomainPackFieldType(schema.kind), required=field.required, enum_ref=enum_id,
            display_name=field.display_name or field.key,
            metadata={"nullable": field.nullable, "multivalued": path in fanout_paths,
                      "editable": True, "description": field.description},
        ))
        if schema.kind == "array":
            schema = schema.items
        if schema.kind == "object":
            for child in schema.fields:
                visit(child, path)

    for field in profile_fields:
        visit(field, "attributes")
    return fields, enums


def profile_policy_finding(context, mapping, *, code, message, object_ref=None, details=None):
    """One explicit non-fatal semantic outcome under the immutable policy."""
    severity = (ValidationFindingSeverity.BLOCKER if mapping.policy.blocks_readiness else {
        "informational": ValidationFindingSeverity.INFO,
        "requires_curator_review": ValidationFindingSeverity.WARNING,
        "error": ValidationFindingSeverity.ERROR,
    }[mapping.policy.unresolved])
    return ValidationFinding(
        code=code, message=message, object_ref=object_ref, severity=severity, status=ValidationFindingStatus.OPEN,
        details={"execution_receipt": context.receipt.model_dump(mode="json"),
                 "generic_profile_ref": context.profile.receipt,
                 "profile_validator_mapping": mapping.model_dump(mode="json"),
                 "validation_metadata": {
                     "binding_state": "active", "blocking": mapping.policy.blocks_readiness,
                     "required": mapping.policy.blocks_readiness, "curator_override_allowed": False,
                     "validator_binding_id": profile_mapping_binding_id(context.profile, mapping),
                 },
                 "profile_conformance": "conforming", "linkml_alignment": "not_assessed",
                 "submission_readiness": "not_assessed", **(details or {})},
    )


def profile_dispatch_matches(envelope, context, *, authenticated_groups):
    """Enforce all pinned provider restrictions before any validator runs.

    The ordinary package path intentionally keeps its existing group matching
    semantics. Profiles cannot silently omit a denied mapping or dispatch after
    a provider mismatch, including when the provider is an approved constant.
    """
    from src.lib.curation_workspace.execution_contracts import require_resolved_profile_conformance
    require_resolved_profile_conformance(context.profile, context.receipt, envelope.model_dump(mode="json"))
    from src.lib.domain_packs.validator_dispatch import _payload_value
    groups = set(authenticated_groups or ())
    eligible, findings, audit = [], [], []
    for unavailable in context.unavailable:
        targets = [obj.to_object_ref() for obj in envelope.extracted_objects] or [None]
        for target in targets:
            findings.append(profile_policy_finding(context, unavailable.mapping,
                code="generic_profile.validator_unavailable", message="; ".join(unavailable.reasons), object_ref=target))
    for match in context.registry.match_bindings(envelope):
        info = match.binding.raw["profile_validation"]
        mapping = next(m for m in context.profile.contract.validator_mappings if m.mapping_id == info["mapping"]["mapping_id"])
        scope = info["source_group_scope"]
        reason = None
        if scope["required_any_active_group"] and not groups.intersection(scope["required_any_active_group"]):
            reason = "Required active-group membership is unavailable"
        values = []
        for selector in info["provider_selectors"].values():
            value = (selector.get("value") if selector["source"] == "literal" else
                     _payload_value(match.object_envelope.payload, match.resolve_input_path(selector["path"])))
            if not isinstance(value, str) or not value.strip():
                reason = "Provider value is missing or ambiguous"
                break
            values.append(value.casefold())
        if set(values) - {value.casefold() for value in scope["allowed_provider_values"]}:
            reason = "Provider value is outside the pinned allowed providers"
        if not scope["allow_cross_provider"] and len(set(values)) > 1:
            reason = "The pinned capability forbids cross-provider validation"
        audit.append({"generic_profile_ref": context.profile.receipt, "mapping_id": mapping.mapping_id,
                      "binding_id": match.binding.binding_id,
                      "capability_ref": mapping.capability_ref.model_dump(mode="json"),
                      "group_scope": deepcopy(scope), "authenticated_groups": sorted(groups),
                      "eligible": reason is None, "eligibility_reason": reason,
                      "target": match.target_details()})
        if reason:
            findings.append(profile_policy_finding(context, mapping,
                code="generic_profile.validator_scope_unavailable", message=reason,
                object_ref=match.object_envelope.to_object_ref(), details={"group_scope": deepcopy(scope)}))
        else:
            eligible.append(match)
    return eligible, findings, tuple(audit)
