"""Component-level scientific decisions with program-owned condition assembly."""

from copy import deepcopy
from collections import Counter
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import Field, StrictStr

from src.lib.domain_packs.compact_decisions import (
    CandidateAssessment, CompactValidatorDecision, DecisionContract, RecordValue,
)
from src.lib.domain_packs.resolvable_values import (
    OUTCOME_MATCHED, OUTCOME_MISSING_EXPECTED_RESULT_FIELD, OUTCOME_NOT_VALIDATED,
    has_resolution_state, lookup_outcome_for_failure,
)
from src.lib.domain_packs.validator_result_classification import validator_failure_classification
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


_TERM_COMPONENTS = ("condition_class", "condition_id", "condition_chemical", "condition_taxon")
# The key an ontology lookup record (every term component's owner) carries the term name under.
_ONTOLOGY_RECORD_NAME_KEY = "name"


def _present(value: Any) -> bool:
    return value is not None and value != ""


def _component_resolution(request, component, judgment, attempts, values, selected):
    """One stored component's own decision for ``field_resolutions``, or None.

    Keyed by the component's ``<component>_curie`` result field. A resolved component
    takes its CURIE from its own selection and its name from the selected ontology
    record's ``name``; when either is empty that component alone stays unresolved
    (``missing_expected_result_field``). An unresolved one records the outcome of its
    own lookups; a component nobody looked up (supplemental ``not_checked``) stays not
    validated. Returns (key, decision, fields).
    """

    key = f"{component.component_type}_curie"
    if key not in request.expected_result_fields:
        return None
    fields = [field for field in (key, f"{component.component_type}_name")
              if field in request.expected_result_fields]
    base = {"explanation": judgment.explanation, "curator_message": judgment.curator_message}
    if judgment.status == "resolved":
        [record] = selected.values()
        name_field = f"{component.component_type}_name"
        resolved_values = {key: values.get("curie", values.get("chebi_id"))}
        if name_field in fields:
            resolved_values[name_field] = record.values.get(_ONTOLOGY_RECORD_NAME_KEY)
        if not all(_present(resolved_values[field]) for field in fields):
            return key, {**base, "status": "unresolved", "resolved_values": {},
                         "lookup_outcome": OUTCOME_MISSING_EXPECTED_RESULT_FIELD}, fields
        return key, {**base, "status": "resolved", "lookup_outcome": OUTCOME_MATCHED,
                     "resolved_values": resolved_values}, fields
    if judgment.status == "unresolved":
        return key, {**base, "status": "unresolved", "resolved_values": {},
                     "lookup_outcome": _component_outcome(request, attempts)}, fields
    if judgment.status == "not_checked":
        return key, {**base, "status": "unresolved", "resolved_values": {},
                     "lookup_outcome": OUTCOME_NOT_VALIDATED}, fields
    return None


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
        # Only a component the paper states and a lookup owns is required; naming its
        # result fields in the binding never makes an absent component required.
        required = bool(source) and name in _OWNERS
        if not source:
            continue
        paths = [request.input_selectors[key].get("path") for key in keys
                 if key in request.input_selectors and request.input_selectors[key].get("path")]
        field_path = paths[0] if paths else None
        components[name] = ConditionComponent(name, source, field_path, _OWNERS.get(name), required)
    return components


def _component_outcome(request, attempts) -> str:
    """The lookup outcome of an unresolved lookup component, derived from its own lookups."""

    if not attempts:
        # The component was judged without a lookup of its own: nothing validated it.
        return OUTCOME_NOT_VALIDATED
    return lookup_outcome_for_failure(validator_failure_classification(
        SimpleNamespace(lookup_attempts=attempts, missing_expected_fields=[], request_id=request.request_id)
    ))


def _stored_component_values(request) -> dict[str, Any]:
    """Components that the payload stores with the contract state (each decided on its own)."""

    inputs = {**request.target.input_values, **request.selected_inputs}
    bundle = inputs.get("condition_components")
    if not isinstance(bundle, dict):
        return {}
    return {name: value for name, value in bundle.items() if has_resolution_state(value)}


def condition_decision_contract(request, result_schema, *, profile_mapped=False):
    components = condition_components(request)
    stored_components = _stored_component_values(request)

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
        field_resolutions: dict[str, Any] = {}
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
            if component.component_type in stored_components:
                resolution = _component_resolution(request, component, judgment, attempts, values, selected)
                if resolution is not None:
                    key, decided, _fields = resolution
                    field_resolutions[key] = decided
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
        assembled = {"resolved_values": values, "normalized_components": normalized,
                     "component_validations": validations, "unresolved_components": unresolved,
                     "condition_id": values.get("condition_id")}
        if field_resolutions:
            # Each stored component carries its own complete decision (ALL-1283; an incomplete
            # resolved one is recorded as unresolved); values without one are not written, so
            # no decided field is missing.
            assembled["field_resolutions"] = field_resolutions
            assembled["missing_expected_fields"] = []
        return assembled

    return DecisionContract(request=request, result_schema=result_schema,
                            profile_mapped=profile_mapped, decision_schema=ConditionDecision,
                            record_slot_fields={
                                **{name + "_curie": ("curie",) for name in _TERM_COMPONENTS},
                                **{name + "_name": ("name",) for name in _TERM_COMPONENTS},
                            },
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
