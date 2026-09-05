"""All-or-nothing write-back for one receipt-bearing closed-profile record.

Packaged and typed-proxy callers retain the ordinary sequential materializer.
Only explicit mapped result slots enter this transaction; no mirror, inferred
reference, or resolved_objects channel is forwarded to the ordinary writer.
"""

from collections import defaultdict
from copy import deepcopy
from typing import Iterable
from uuid import UUID

from src.lib.agent_studio.profile_conformance import ProfileConformanceError, ResolvedGenericProfile
from src.lib.curation_workspace.execution_contracts import require_resolved_profile_conformance
from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.materialization import (
    ValidatorResultMaterializationInput, ValidatorResultMaterializationResult,
    _finding_for_materialization_problem, _finding_for_validator_result,
)
from src.lib.domain_packs.profile_validation import ProfileValidationContext
from src.lib.domain_packs.validation_findings import append_validation_findings_to_envelope
from src.lib.domain_packs.validator_result_policies import allowed_term_policy_violations
from src.lib.domain_packs.validator_result_classification import validator_failure_classification
from src.schemas.agent_execution_revision import GenericProfilePin
from src.schemas.domain_envelope import DomainEnvelope, ValidationFinding, ValidationFindingSeverity
from src.schemas.generic_extraction_profile import GenericProfileContract, ProfileField


def materialize_profile_validator_results(
    envelope: DomainEnvelope,
    context: ProfileValidationContext,
    items: Iterable[ValidatorResultMaterializationInput],
    *,
    actor_id: str = "profile_validator_materialization",
    source_envelope_revision: int | None = None,
) -> ValidatorResultMaterializationResult:
    """Validate complete proposals in memory, then replace each record once."""
    require_resolved_profile_conformance(context.profile, context.receipt, envelope.model_dump(mode="json"))
    if source_envelope_revision is not None and source_envelope_revision < 1:
        raise ValueError("source_envelope_revision must be greater than zero")
    canonical_matches = {
        _match_key(match): match for match in context.registry.match_bindings(envelope)
    }
    grouped = defaultdict(list)
    for item in items:
        grouped[_object_key(item.match.object_envelope)].append(item)
    objects = {_object_key(obj): obj for obj in envelope.extracted_objects}
    replacements, findings = {}, []
    for key, record_items in grouped.items():
        target = objects.get(key)
        updates, paths, problems = [], [], []
        for item in record_items:
            canonical = canonical_matches.get(_match_key(item.match))
            problem, proposed = _approved_updates(item, canonical, context)
            if problem:
                problems.append(problem)
            for update in proposed:
                path = update["field_path"]
                if any(_overlap(path, previous) for previous in paths):
                    problems.append(f"Conflicting validator writes to {path}; no composition is approved")
                paths.append(path)
                updates.append(update)
        if target is None:
            problems.append("Validator target is not present in this envelope")
        elif "profile_validator_materialization" in target.metadata and not isinstance(
            target.metadata["profile_validator_materialization"], list
        ):
            problems.append("Profile validator materialization audit must be a list")
        proposed_attributes = None
        if not problems and target is not None and updates:
            try:
                # One shared closed-profile patch transaction for the COMPLETE
                # proposed record, including every validator and array element.
                proposed_attributes = context.profile.patch_attributes(
                    target.payload["attributes"], updates, candidate_id=target.object_id or target.pending_ref_id,
                )
            except ProfileConformanceError as exc:
                problems.extend(f"{issue['field_path']}: {issue['message']}" for issue in exc.issues)
        if not problems and target is not None and proposed_attributes is not None:
            metadata = deepcopy(target.metadata)
            audit = metadata.setdefault("profile_validator_materialization", [])
            audit.append({"execution_receipt": context.receipt.model_dump(mode="json"),
                          "request_ids": [item.request.request_id for item in record_items],
                          "field_paths": paths, "source_envelope_revision": source_envelope_revision})
            replacements[key] = target.model_copy(update={
                "payload": {**deepcopy(target.payload), "attributes": proposed_attributes}, "metadata": metadata,
            })
        for item in record_items:
            if problems:
                finding = _finding_for_materialization_problem(
                    item, "; ".join(dict.fromkeys(problems)), source_envelope_revision=source_envelope_revision,
                )
                # Do not let a successful validator's prose hide the reason the
                # whole record transaction was rejected.
                finding = finding.model_copy(update={"message": "Profile write-back rejected: " + "; ".join(dict.fromkeys(problems))})
            else:
                finding = _finding_for_validator_result(item, source_envelope_revision=source_envelope_revision)
            findings.append(profile_result_finding(finding, item, context, failed=bool(problems)))
    updated = envelope.model_copy(update={"extracted_objects": [
        replacements.get(_object_key(obj), obj) for obj in envelope.extracted_objects
    ]})
    updated, appended = append_validation_findings_to_envelope(updated, findings, actor_id=actor_id)
    return ValidatorResultMaterializationResult(updated, appended, ())


def _object_key(obj):
    return obj.to_object_ref().ref_key() if obj is not None else None


def _match_key(match):
    return match.binding.binding_id, _object_key(match.object_envelope), match.field_path


def _overlap(left: str, right: str) -> bool:
    return left == right or any(left.startswith(right + suffix) or right.startswith(left + suffix)
                                for suffix in (".", "["))


def _approved_updates(item, canonical, context):
    if canonical is None or item.match.binding != canonical.binding:
        return "Result does not belong to an approved profile binding/target", []
    expected = build_domain_validation_request(canonical).request
    if expected is None or item.request.request_id != expected.request_id or item.request.expected_result_fields != expected.expected_result_fields:
        return "Validator request is stale or changed its approved result destinations", []
    result = item.result
    if (result.request_id != expected.request_id or result.validator_binding_id != expected.validator_binding_id
            or result.validator_agent != expected.validator_agent or result.target != expected.target):
        return "Validator result identity does not match its exact request", []
    if result.resolved_objects:
        return "resolved_objects has no approved custom-profile destination; use explicitly mapped typed result slots", []
    if set(result.resolved_values) - expected.expected_result_fields.keys():
        return "Validator returned extra result slots outside the approved mapping", []
    violations = allowed_term_policy_violations(result, request=expected)
    if violations:
        return "; ".join(violation.message for violation in violations), []
    mapping_id = canonical.binding.raw["profile_validation"]["mapping"]["mapping_id"]
    mapping = next(m for m in context.profile.contract.validator_mappings if m.mapping_id == mapping_id)
    capability = next(cap for cap in context.capabilities if cap.ref == mapping.capability_ref)
    reuse = capability.binding.custom_profile_reuse
    # Slot types can be narrower than their declared profile destinations (e.g.
    # enum -> string). Validate those types using the SAME conformance service.
    fields, values = [], {}
    for index, slot_name in enumerate(mapping.outputs):
        slot = reuse.outputs[slot_name]
        key = f"slot_{index}"
        fields.append(ProfileField(key=key, nullable=slot.nullable, value_schema=slot.value_schema))
        if slot.result_path in result.resolved_values:
            values[key] = result.resolved_values[slot.result_path]
    if fields:
        slot_contract = GenericProfileContract.model_validate({
            "name": "Validator result slots", "semantic_class": "validator_result",
            "fields": [field.model_dump(mode="json") for field in fields],
        })
        slot_pin = GenericProfilePin(profile_id=UUID(int=0), profile_revision_id=UUID(int=0), revision=1,
                                     fingerprint=slot_contract.fingerprint())
        issues = ResolvedGenericProfile(slot_pin, slot_contract).validate_attributes(values)
        if issues:
            return "Validator result violates the mapped capability slot type: " + "; ".join(i["message"] for i in issues), []
    if result.status != "resolved":
        try:
            validator_failure_classification(result, error_type=ValueError)
        except ValueError as exc:
            return str(exc), []
        return None, []
    return None, [{"field_path": expected.expected_result_fields[slot], "value": deepcopy(value)}
                  for slot, value in result.resolved_values.items()]


def profile_result_finding(
    finding: ValidationFinding, item: ValidatorResultMaterializationInput,
    context: ProfileValidationContext, *, failed: bool = False,
) -> ValidationFinding:
    """Keep semantic outcome, conformance and readiness policy separate."""
    raw = item.match.binding.raw.get("profile_validation", {})
    mapping_id = raw.get("mapping", {}).get("mapping_id")
    mapping = next((m for m in context.profile.contract.validator_mappings if m.mapping_id == mapping_id), None)
    details = {**finding.details, "execution_receipt": context.receipt.model_dump(mode="json"),
               "generic_profile_ref": context.profile.receipt, "profile_conformance": "conforming",
               "materialization": "rejected" if failed else "accepted",
               "linkml_alignment": "not_assessed", "submission_readiness": "not_assessed"}
    if mapping is not None:
        details["profile_validator_mapping"] = mapping.model_dump(mode="json")
    severity = finding.severity
    if mapping is not None and (failed or item.result.status != "resolved"):
        severity = (ValidationFindingSeverity.BLOCKER if mapping.policy.blocks_readiness else {
            "informational": ValidationFindingSeverity.INFO,
            "requires_curator_review": ValidationFindingSeverity.WARNING,
            "error": ValidationFindingSeverity.ERROR,
        }[mapping.policy.unresolved])
    return finding.model_copy(update={"details": details, "severity": severity})
