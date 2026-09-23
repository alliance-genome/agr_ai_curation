"""Component-level scientific decisions with program-owned condition assembly."""

from copy import deepcopy
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, StrictStr

from src.lib.domain_packs.compact_decisions import (
    CandidateAssessment, CompactValidatorDecision, DecisionContract, RecordValue,
)
from src.schemas.domain_validator import DomainValidatorBaseModel


class ComponentDecision(DomainValidatorBaseModel):
    component_type: Literal[
        "condition_class", "condition_id", "condition_chemical", "condition_taxon",
        "data_provider", "unit", "quantity", "relation", "free_text", "evidence_quotes",
    ] = Field(description="Use exactly the component types enumerated by this request's domain_contract.")
    status: Literal["resolved", "unresolved", "not_present", "not_checked"]
    candidates: list[CandidateAssessment] = Field(default_factory=list)
    slots: dict[str, RecordValue] = Field(default_factory=dict)
    lookup_refs: list[StrictStr] = Field(default_factory=list, description="validator_lookup_refs.lookup_ref values for this component's actual calls.")
    explanation: StrictStr
    curator_message: StrictStr | None = None


class ConditionDecision(CompactValidatorDecision):
    components: list[ComponentDecision] = Field(description="Exactly one decision for each component in this request's domain_contract, including supplemental context; no absent extra components.")


@dataclass(frozen=True)
class ConditionComponent:
    component_type: str
    source_inputs: dict[str, Any]
    field_path: str | None
    owner: str | None
    required: bool


_INPUTS = {
    "condition_class": ("condition_class_curie", "condition_class_name"),
    "condition_id": ("condition_id_curie", "condition_id_name"),
    "condition_chemical": ("condition_chemical_curie", "condition_chemical_name", "chemical_name"),
    "condition_taxon": ("condition_taxon_curie", "taxon", "taxon_id"),
    "data_provider": ("data_provider_abbreviation", "data_provider_name", "data_provider"),
    "unit": ("condition_unit",),
    "quantity": ("condition_quantity",),
    "relation": ("condition_relation_type",),
    "free_text": ("condition_free_text",),
    "evidence_quotes": ("evidence_quotes",),
}
_OWNERS = {
    "condition_class": "ontology_term_validation", "condition_id": "ontology_term_validation",
    "condition_chemical": "ontology_term_validation", "condition_taxon": "ontology_term_validation",
    "unit": "controlled_vocabulary_validation", "data_provider": "data_provider_validation",
    "quantity": "controlled_vocabulary_validation",
}
_METHODS = {
    "ontology_term_validation": {"get_ontology_term", "get_ontology_terms", "search_ontology_terms", "map_curies_to_names"},
    "controlled_vocabulary_validation": {"get_vocabulary_term", "search_vocabulary_terms"},
    "data_provider_validation": {"get_data_provider", "get_data_providers"},
}
_COMPONENT_FIELD_ALIASES = {"chebi_id": ("chebi_id", "curie"), "term_name": ("name", "term_name", "label")}


def condition_components(request) -> dict[str, ConditionComponent]:
    inputs = {**request.target.input_values, **request.selected_inputs}
    bundle = inputs.get("condition_components", {})
    components = {}
    for name, keys in _INPUTS.items():
        source = {key: deepcopy(inputs[key]) for key in keys
                  if inputs.get(key) is not None and inputs[key] != "" and inputs[key] != []}
        if not source and isinstance(bundle, dict):
            source = {key: deepcopy(bundle[key]) for key in (name, *keys)
                      if bundle.get(key) is not None and bundle[key] != "" and bundle[key] != []}
        required = bool(source) and name in _OWNERS
        required = required or any(key in request.expected_result_fields for key in keys)
        if not source and not required:
            continue
        paths = [request.input_selectors[key].get("path") for key in keys
                 if key in request.input_selectors and request.input_selectors[key].get("path")]
        field_path = paths[0] if paths else None
        components[name] = ConditionComponent(name, source, field_path, _OWNERS.get(name), required)
    return components


def condition_decision_contract(request, result_schema, *, profile_mapped=False):
    components = condition_components(request)

    def assemble_domain(payload, decision, workspace):
        names = [component.component_type for component in decision.components]
        if len(names) != len(set(names)) or set(names) != set(components):
            raise ValueError(
                "Condition decision must assess every present or required component exactly once: "
                f"expected={list(components)}; missing={sorted(set(components) - set(names))}; "
                f"unexpected={sorted(set(names) - set(components))}; "
                f"duplicates={sorted(name for name, count in Counter(names).items() if count > 1)}. "
                "Use the exact component names and statuses in this request's domain_contract."
            )
        validations, normalized, unresolved = [], [], []
        for judgment in decision.components:
            component = components[judgment.component_type]
            attempts = workspace.lookup_attempts_for(request.request_id, judgment.lookup_refs)
            if component.owner is None:
                if judgment.status != "not_checked" or judgment.candidates or judgment.slots or attempts:
                    raise ValueError("Supplemental condition context is not a separately resolved lookup component")
            else:
                if judgment.status not in {"resolved", "unresolved"}:
                    raise ValueError("Present or required lookup component must be resolved or unresolved")
                if any(attempt.method not in _METHODS[component.owner] for attempt in attempts):
                    raise ValueError("Component lookup uses a method outside its owning capability")
            candidates, objects, selected = [], [], {}
            seen = set()
            for assessment in judgment.candidates:
                if assessment.record_ref in seen:
                    raise ValueError("Duplicate condition candidate reference")
                seen.add(assessment.record_ref)
                record = workspace.record(request.request_id, assessment.record_ref)
                if workspace.source_call_id(request.request_id, assessment.record_ref) not in judgment.lookup_refs:
                    raise ValueError("Component candidate must come from one of its recorded lookups")
                workspace._check_evidence(request, assessment.evidence_record_ids)
                candidate = record.candidate.model_copy(deep=True)
                candidate.score = assessment.score
                candidate.matched_fields = deepcopy(assessment.matched_fields)
                candidate.details = {**candidate.details, "scientific_assessment": assessment.model_dump(exclude={"record_ref"}),
                                     "source_record_ref": assessment.record_ref,
                                     "source_call_id": workspace.source_call_id(request.request_id, assessment.record_ref)}
                candidates.append(candidate)
                if assessment.disposition == "selected":
                    selected[assessment.record_ref] = record
                    if record.resolved_object is not None:
                        objects.append(deepcopy(dict(record.resolved_object)))
            values = {}
            for slot, selection in judgment.slots.items():
                record = selected.get(selection.record_ref)
                if record is None or selection.field not in record.values:
                    raise ValueError("Component slot requires an available field on a selected record")
                if selection.field not in _COMPONENT_FIELD_ALIASES.get(slot, (slot,)):
                    raise ValueError(
                        f"Component {component.component_type} slot '{slot}' cannot copy field '{selection.field}'; "
                        f"use namesake component slot '{selection.field}' or a declared component alias. "
                        "Root slots are separate; see domain_contract.component_slots."
                    )
                values[slot] = deepcopy(record.values[selection.field])
            if judgment.status == "resolved" and (not attempts or len(selected) != 1 or not values):
                raise ValueError("Resolved component requires one authoritative selection and resolved fields")
            owner = {"package_id": "agr.alliance", "agent_id": component.owner} if component.owner else None
            validation = {
                "component_type": component.component_type, "field_path": component.field_path,
                "required": component.required, "validator_agent": owner, "status": judgment.status,
                "selected_inputs": deepcopy(component.source_inputs), "resolved_values": values,
                "missing_expected_fields": [], "candidates": candidates, "lookup_attempts": attempts,
                "curator_message": judgment.curator_message, "explanation": judgment.explanation,
            }
            validations.append(validation)
            if values:
                normalized.append({
                    "component_type": component.component_type, "field_path": component.field_path,
                    "resolved_values": deepcopy(values), "resolved_objects": objects,
                    "source_inputs": deepcopy(component.source_inputs), "validator_agent": owner,
                })
            if component.required and judgment.status != "resolved":
                unresolved.append(component.component_type)
        if decision.status == "resolved" and unresolved:
            raise ValueError("An unresolved required component keeps the condition unresolved")
        values = deepcopy(payload["resolved_values"])
        for snapshot in normalized:
            root_slot = snapshot["component_type"] + "_curie"
            if root_slot not in request.expected_result_fields:
                continue
            component_values = snapshot["resolved_values"]
            curie = component_values.get("curie", component_values.get("chebi_id"))
            if curie is not None:
                if root_slot in values and values[root_slot] != curie:
                    raise ValueError("Condition root value contradicts its component selection")
                values[root_slot] = curie
        if "normalized_components" in request.expected_result_fields and normalized:
            values["normalized_components"] = deepcopy(normalized)
        return {"resolved_values": values, "normalized_components": normalized,
                "component_validations": validations, "unresolved_components": unresolved,
                "condition_id": values.get("condition_id")}

    return DecisionContract(request=request, result_schema=result_schema,
                            profile_mapped=profile_mapped, decision_schema=ConditionDecision,
                            record_slot_fields={name + "_curie": ("curie",) for name in (
                                "condition_class", "condition_id", "condition_chemical", "condition_taxon")},
                            domain_contract={
                                "components": [{
                                    "component_type": component.component_type,
                                    "required": component.required,
                                    "allowed_statuses": ["resolved", "unresolved"] if component.owner else ["not_checked"],
                                    "lookup_methods": sorted(_METHODS.get(component.owner, ())),
                                } for component in components.values()],
                                "component_slots": {
                                    "namesake_fields": "validator_record_available_fields of the same tool response "
                                                       "(page), or the record ref's own available_fields where it "
                                                       "lists them",
                                    "aliases": _COMPONENT_FIELD_ALIASES,
                                    "root_slots_are_component_slots": False,
                                    "rule": "Copy a selected record's available field into the same-named component slot, "
                                            "or use a declared alias. For example, component curie copies field curie; "
                                            "root condition_class_curie separately copies field curie.",
                                },
                                "rules": "Assess every listed component exactly once, no extras. "
                                         "Lookup components use candidate record_refs and their actual lookup_refs. "
                                         "Resolved requires one selected record, lookup evidence and resolved fields. "
                                         "Supplemental not_checked components have no candidates, slots or lookup_refs. "
                                         "An unresolved required component keeps the condition unresolved.",
                            },
                            assemble_domain=assemble_domain)
