"""Extracted vs validated values: the shared resolvable-value contract (ALL-1283)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.materialization import (
    ValidatorResultMaterializationInput,
    materialize_validator_results_into_envelope,
)
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.resolvable_values import (
    LEGACY_EXPLANATION,
    LEGACY_UNVERIFIED_SUFFIX,
    LOOKUP_OUTCOME_LABELS,
    LOOKUP_OUTCOMES,
    NOT_VALIDATED_EXPLANATION,
    OUTCOME_LEGACY_UNVERIFIED,
    OUTCOME_MATCHED,
    OUTCOME_NOT_FOUND,
    OUTCOME_NOT_VALIDATED,
    RESOLUTION_STATES,
    RESOLVED,
    UNRESOLVED,
    LookupOutcome,
    ResolutionState,
    ResolvableSpec,
    ResolvableValueError,
    check_resolvable_list,
    check_resolvable_value,
    effective_payload,
    effective_resolution,
    effective_value,
    lookup_outcome_for_failure,
    mark_resolved,
    mark_unresolved,
    resolvable_leaf_header,
    resolvable_spec_from_display,
    resolved_value,
    unresolved_list,
    unresolved_positions,
    unresolved_value,
    validator_event_covers,
)
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.lib.domain_packs.validator_result_classification import (
    VALIDATOR_FAILURE_CLASSIFICATIONS,
    validator_failure_classification,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DomainEnvelope,
)
from src.schemas.domain_pack_metadata import (
    DomainPackEnumDefinition,
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackMetadata,
    DomainPackObjectDefinition,
)
from src.schemas.domain_validator import DomainValidatorResultBase


TERM_KEYS = ("curie", "name")


def test_vocabularies_are_closed_enums():
    assert RESOLUTION_STATES == ("resolved", "unresolved")
    assert LOOKUP_OUTCOMES == (
        "matched", "not_found", "ambiguous", "conflict", "blocked", "transient", "invalid_schema",
        "missing_expected_result_field", "rejected_candidates", "not_validated", "legacy_unverified",
        "curator_override",
    )
    assert tuple(ResolutionState) == RESOLUTION_STATES
    assert tuple(LookupOutcome) == LOOKUP_OUTCOMES
    assert set(LOOKUP_OUTCOME_LABELS) == set(LOOKUP_OUTCOMES)


def test_every_validator_failure_classification_maps_to_a_lookup_outcome():
    """Guard: a new classification without a mapping fails here."""

    for classification in VALIDATOR_FAILURE_CLASSIFICATIONS:
        assert lookup_outcome_for_failure(classification) in LOOKUP_OUTCOMES
    with pytest.raises(ResolvableValueError):
        lookup_outcome_for_failure("something_new")


def test_the_classifier_only_returns_declared_classifications():
    """Guard: a new literal returned by the classifier must be declared (and so mapped)."""

    import ast
    import inspect

    source = inspect.getsource(validator_failure_classification)
    returned = {
        node.value.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)
    }
    assert returned and returned <= set(VALIDATOR_FAILURE_CLASSIFICATIONS)


def _classify(outcomes):
    return validator_failure_classification(DomainValidatorResultBase.model_validate({
        "status": "unresolved", "request_id": "r", "validator_binding_id": "b",
        "validator_agent": {"package_id": "p", "agent_id": "a"},
        "target": {"domain_pack_id": "fixture.vocab", "object_type": "T"}, "resolved_values": {}, "resolved_objects": [],
        "missing_expected_fields": [], "candidates": [],
        "lookup_attempts": [
            {"provider": "x", "method": "m", "query": {}, "result_count": 1, "outcome": outcome}
            for outcome in outcomes
        ],
        "curator_message": None, "explanation": "e",
    }))


def test_all_lookups_succeeding_without_a_fit_is_rejected_candidates():
    assert _classify(["success"]) == "rejected_candidates"
    assert _classify(["success", "success"]) == "rejected_candidates"
    # A lookup found something the validator rejected, even where another found nothing.
    assert _classify(["success", "not_found"]) == "rejected_candidates"
    assert lookup_outcome_for_failure("rejected_candidates") == "rejected_candidates"
    with pytest.raises(ValueError, match="Unable to classify"):
        _classify([])


def test_builders_write_mention_state_outcome_and_explanation():
    value = unresolved_value("residual body structures", identity_keys=TERM_KEYS)
    assert value == {
        "curie": None,
        "name": None,
        "mention": "residual body structures",
        "resolution_state": UNRESOLVED,
        "lookup_outcome": OUTCOME_NOT_VALIDATED,
        "validator_explanation": NOT_VALIDATED_EXPLANATION,
    }
    resolved = resolved_value("skin", {"curie": "ONT:1", "name": "epidermis"}, vocabulary="demo")
    assert resolved == {
        "vocabulary": "demo",
        "curie": "ONT:1",
        "name": "epidermis",
        "mention": "skin",
        "resolution_state": RESOLVED,
        "lookup_outcome": OUTCOME_MATCHED,
        "validator_explanation": None,
    }


@pytest.mark.parametrize(
    "value",
    [
        # Resolved without the identity a validator supplied.
        {"curie": None, "mention": "x", "resolution_state": "resolved", "lookup_outcome": "matched"},
        # Resolved with any outcome but matched.
        {"curie": "ONT:1", "mention": "x", "resolution_state": "resolved", "lookup_outcome": "not_found"},
        # Unresolved but carrying an identity.
        {"curie": "ONT:1", "mention": "x", "resolution_state": "unresolved", "lookup_outcome": "not_found"},
        # Unresolved as matched, without an outcome, or with the read-time-only outcome.
        {"curie": None, "mention": "x", "resolution_state": "unresolved", "lookup_outcome": "matched"},
        {"curie": None, "mention": "x", "resolution_state": "unresolved", "lookup_outcome": None},
        {"curie": None, "mention": "x", "resolution_state": "unresolved", "lookup_outcome": "legacy_unverified"},
        # Words outside the vocabularies.
        {"curie": "ONT:1", "mention": "x", "resolution_state": "validated", "lookup_outcome": "matched"},
        {"curie": None, "mention": "x", "resolution_state": "unresolved", "lookup_outcome": "no idea"},
        # An empty mention, or a non-text explanation.
        {"curie": None, "mention": " ", "resolution_state": "unresolved", "lookup_outcome": "not_validated"},
        {"curie": None, "mention": "x", "resolution_state": "unresolved", "lookup_outcome": "not_found",
         "validator_explanation": ["not", "text"]},
    ],
)
def test_invariant_violations_raise(value):
    with pytest.raises(ResolvableValueError):
        check_resolvable_value(value, identity_keys=TERM_KEYS)


def test_builders_require_mention_and_a_vocabulary_outcome():
    with pytest.raises(ResolvableValueError, match="paper wording"):
        unresolved_value("", identity_keys=TERM_KEYS)
    with pytest.raises(ResolvableValueError, match="paper wording"):
        resolved_value(None, {"curie": "ONT:1"})
    with pytest.raises(ResolvableValueError, match="lookup_outcome"):
        unresolved_value("skin", identity_keys=TERM_KEYS, outcome="free text reason")


def test_mark_writes_validator_words_and_never_touches_identity_or_mention():
    value = unresolved_value("skin", identity_keys=TERM_KEYS)
    mark_unresolved(value, OUTCOME_NOT_FOUND, explanation="No term matched skin.", curator_message="Check it.")
    assert value == {"mention": "skin", "curie": None, "name": None,
                     "resolution_state": UNRESOLVED, "lookup_outcome": OUTCOME_NOT_FOUND,
                     "validator_explanation": "No term matched skin.",
                     "validator_curator_message": "Check it."}
    mark_resolved(value, {"curie": "ONT:1", "name": "epidermis"}, explanation="Exact synonym.")
    assert value["mention"] == "skin"
    assert (value["resolution_state"], value["lookup_outcome"]) == (RESOLVED, OUTCOME_MATCHED)
    assert (value["validator_explanation"], value["validator_curator_message"]) == ("Exact synonym.", None)
    with pytest.raises(ResolvableValueError):
        mark_unresolved(value, "legacy_unverified", explanation=None, identity_keys=TERM_KEYS)
    # The validator is the authority: un-resolving keeps the identity only as hints.
    with pytest.raises(ResolvableValueError, match="identity keys"):
        mark_unresolved(value, OUTCOME_NOT_FOUND, explanation="x")
    mark_unresolved(value, OUTCOME_NOT_FOUND, explanation="No longer matches.", identity_keys=TERM_KEYS)
    assert value == {"mention": "skin", "curie": None, "name": None,
                     "overruled_curie": "ONT:1", "overruled_name": "epidermis",
                     "resolution_state": UNRESOLVED, "lookup_outcome": OUTCOME_NOT_FOUND,
                     "validator_explanation": "No longer matches.", "validator_curator_message": None}
    check_resolvable_value(value, identity_keys=TERM_KEYS)


def test_list_helpers_keep_each_element_separate():
    values = unresolved_list(["IMP", "IDA"], identity_keys=("curie",))
    mark_resolved(values[0], {"curie": "ECO:1"}, explanation=None)
    check_resolvable_list(values, identity_keys=("curie",))
    assert unresolved_positions(values) == [1]
    values[1]["curie"] = "ECO:2"
    with pytest.raises(ResolvableValueError, match="element 1"):
        check_resolvable_list(values, identity_keys=("curie",))


def test_display_spec_mention_role_declares_a_resolvable_value():
    assert resolvable_spec_from_display({"label": "name", "id": "curie"}) is None
    spec = resolvable_spec_from_display({"label": "name", "id": "curie", "mention": "mention"})
    assert spec == ResolvableSpec(id_key="curie", label_key="name")
    assert spec.identity_keys == ("curie", "name")


def test_leaf_headers_name_each_part_in_plain_words():
    assert resolvable_leaf_header("Anatomy", "mention") == "Anatomy (paper wording)"
    assert resolvable_leaf_header("Anatomy", "resolution_state") == "Anatomy (status)"
    assert resolvable_leaf_header("Anatomy", "lookup_outcome") == "Anatomy (lookup result)"
    assert resolvable_leaf_header("Anatomy", "validator_explanation") == "Anatomy (validator explanation)"
    assert resolvable_leaf_header("Anatomy", "curie") is None
    assert LOOKUP_OUTCOME_LABELS["ambiguous"] == "Several matches"
    assert LOOKUP_OUTCOME_LABELS["rejected_candidates"] == "Candidates rejected"


def test_legacy_values_are_resolved_only_with_an_identity_and_a_covering_event():
    metadata = {"validator_resolved_value_materialization": [
        {"original_values": {"terms[1].name": "skin"}},
        {"materialized_field_paths": ["site.curie"]},
    ]}
    assert validator_event_covers(metadata, "site")
    assert validator_event_covers(metadata, "terms[1]")
    assert not validator_event_covers(metadata, "terms[0]")
    assert not validator_event_covers(None, "site")

    assert effective_resolution({"curie": "ONT:1"}, identity_keys=TERM_KEYS, covered_by_validator=True) == (
        RESOLVED, OUTCOME_MATCHED)
    assert effective_resolution({"curie": "ONT:1"}, identity_keys=TERM_KEYS, covered_by_validator=False) == (
        UNRESOLVED, OUTCOME_LEGACY_UNVERIFIED)
    assert effective_resolution({"name": None}, identity_keys=TERM_KEYS, covered_by_validator=True) == (
        UNRESOLVED, OUTCOME_LEGACY_UNVERIFIED)
    # A contract value always reads as stored; a stored word outside the vocabulary raises.
    stored = {"curie": None, "mention": "x", "resolution_state": UNRESOLVED, "lookup_outcome": OUTCOME_NOT_FOUND}
    assert effective_resolution(stored, identity_keys=TERM_KEYS, covered_by_validator=True) == (
        UNRESOLVED, OUTCOME_NOT_FOUND)
    # A stored word outside the vocabulary reads as unresolved/invalid_schema; nothing raises.
    assert effective_resolution({**stored, "lookup_outcome": "whatever"}, identity_keys=TERM_KEYS,
                                covered_by_validator=True) == (UNRESOLVED, "invalid_schema")


@pytest.mark.parametrize("covered", [True, False])
@pytest.mark.parametrize("value", [
    {"curie": "ONT:1", "name": "skin"},
    {"curie": None, "name": "skin"},
    {"name": "skin", "resolution_state": "pending_ontology_resolution"},
    {"curie": "ONT:1", "resolution_state": "resolved"},
    {"mention": "skin"},
])
def test_the_legacy_rule_only_emits_vocabulary_values(value, covered):
    effective = effective_value(value, ResolvableSpec(id_key="curie", label_key="name"),
                                covered_by_validator=covered)
    assert effective["resolution_state"] in RESOLUTION_STATES
    assert effective["lookup_outcome"] in LOOKUP_OUTCOMES
    assert effective["lookup_outcome"] in (OUTCOME_MATCHED, OUTCOME_LEGACY_UNVERIFIED)
    assert effective["validator_explanation"] == LEGACY_EXPLANATION


def test_effective_payload_applies_the_legacy_rule_without_touching_storage():
    spec = ResolvableSpec(id_key="curie", label_key="name")
    payload = {
        "site": {"curie": "ONT:1", "name": "epidermis"},
        "terms": [{"curie": "ONT:2", "name": "gut"}, {"curie": None, "name": "unknown body part"}],
        "stage": {"curie": "ONT:3", "name": "adult"},
        "fresh": {"curie": None, "name": None, "mention": "tail",
                  "resolution_state": UNRESOLVED, "lookup_outcome": OUTCOME_NOT_FOUND},
    }
    metadata = {"validator_resolved_value_materialization": [
        {"original_values": {"site.name": "skin", "terms[0].name": "gut"}},
    ]}
    effective = effective_payload(
        payload,
        {"site": spec, "terms": spec, "stage": spec, "fresh": spec},
        object_metadata=metadata,
    )
    assert effective["site"]["resolution_state"] == RESOLVED
    assert effective["terms"][0]["resolution_state"] == RESOLVED
    assert effective["terms"][1] == {
        "curie": None, "name": None, "mention": f"unknown body part {LEGACY_UNVERIFIED_SUFFIX}",
        "resolution_state": UNRESOLVED, "lookup_outcome": OUTCOME_LEGACY_UNVERIFIED,
        "validator_explanation": LEGACY_EXPLANATION,
    }
    # No covering event: unverified, and its stored text is paper wording.
    assert effective["stage"]["mention"] == f"adult (ONT:3) {LEGACY_UNVERIFIED_SUFFIX}"
    assert effective["stage"]["curie"] is None
    assert effective["fresh"] == payload["fresh"]
    assert "resolution_state" not in payload["site"]


def _vocabulary_pack(*, outcome_values, state_values=None):
    state_values = state_values or list(RESOLUTION_STATES)
    return DomainPackMetadata(
        pack_id="fixture.vocab", display_name="Vocab", version="0.1.0", metadata_api_version="1.0.0",
        enum_definitions=[
            DomainPackEnumDefinition(enum_id="ResolutionState", display_name="State",
                                     values=[{"value": value} for value in state_values]),
            DomainPackEnumDefinition(enum_id="LookupOutcome", display_name="Outcome",
                                     values=[{"value": value} for value in outcome_values]),
        ],
        object_definitions=[DomainPackObjectDefinition(
            object_type="Observation", display_name="Observation", fields=[
                DomainPackFieldDefinition(field_path="site", field_type=DomainPackFieldType.OBJECT,
                                          metadata={"display": {"label": "name", "id": "curie",
                                                                "mention": "mention"}}),
                DomainPackFieldDefinition(field_path="site.resolution_state",
                                          field_type=DomainPackFieldType.ENUM, enum_ref="ResolutionState"),
                DomainPackFieldDefinition(field_path="site.lookup_outcome",
                                          field_type=DomainPackFieldType.ENUM, enum_ref="LookupOutcome"),
            ],
        )],
    )


def test_packs_declare_the_vocabularies_exactly():
    _vocabulary_pack(outcome_values=list(LOOKUP_OUTCOMES))
    with pytest.raises(ValueError, match="site.lookup_outcome must be an enum field"):
        _vocabulary_pack(outcome_values=[*LOOKUP_OUTCOMES, "other"])
    with pytest.raises(ValueError, match="site.resolution_state must be an enum field"):
        _vocabulary_pack(outcome_values=list(LOOKUP_OUTCOMES), state_values=["resolved", "pending"])


# --- Materializer write-back ---------------------------------------------------


_SITE_DISPLAY = {"label": "name", "id": "curie", "mention": "mention"}


def _metadata(*, expected=None, mirror=False, input_path="site.mention", declared=True) -> DomainPackMetadata:
    display = {"display": _SITE_DISPLAY} if declared else {}
    fields = [
        DomainPackFieldDefinition(field_path="site", field_type=DomainPackFieldType.OBJECT, metadata=display),
        DomainPackFieldDefinition(field_path="copy", field_type=DomainPackFieldType.OBJECT, metadata=display),
        DomainPackFieldDefinition(
            field_path="site.curie",
            field_type=DomainPackFieldType.STRING,
            metadata={"materializes_to_field_paths": ["copy.curie"]} if mirror else {},
        ),
        DomainPackFieldDefinition(
            field_path="site.name",
            field_type=DomainPackFieldType.STRING,
            metadata={"materializes_to_field_paths": ["copy.name"]} if mirror else {},
        ),
        DomainPackFieldDefinition(field_path="site.mention", field_type=DomainPackFieldType.STRING),
        DomainPackFieldDefinition(field_path="copy.curie", field_type=DomainPackFieldType.STRING),
        DomainPackFieldDefinition(field_path="copy.name", field_type=DomainPackFieldType.STRING),
    ]
    return DomainPackMetadata(
        pack_id="fixture.resolvable",
        display_name="Fixture Resolvable Pack",
        version="0.1.0",
        metadata_api_version="1.0.0",
        metadata={
            "validator_bindings": {
                "active": [
                    {
                        "binding_id": "fixture.site_lookup",
                        "display_name": "Site lookup",
                        "validator_agent": {"package_id": "fixture.validators", "agent_id": "term_validator"},
                        "applies_to": {
                            "domain_pack_id": "fixture.resolvable",
                            "object_types": ["Observation"],
                        },
                        "input_fields": {"mention": {"source": "payload", "path": input_path}},
                        "expected_result_fields": expected or {"curie": "site.curie", "name": "site.name"},
                    }
                ],
                "under_development": [],
            }
        },
        object_definitions=[
            DomainPackObjectDefinition(
                object_type="Observation",
                display_name="Observation",
                metadata={"object_role": "curatable_unit"},
                fields=fields,
            )
        ],
    )


def _envelope(payload) -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="resolvable-env",
        domain_pack_id="fixture.resolvable",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="Observation",
                pending_ref_id="observation-1",
                status=CuratableObjectStatus.PENDING,
                payload=payload,
            )
        ],
    )


def _item(metadata, envelope, *, status="resolved", values=None, outcome="success", missing=()):
    registry = DomainPackValidationRegistry.from_domain_pack(
        LoadedDomainPack(
            pack_id=metadata.pack_id,
            display_name=metadata.display_name,
            version=metadata.version,
            pack_path=Path("."),
            metadata_path=Path("."),
            metadata=metadata,
        )
    )
    match = registry.match_bindings(envelope, states=[ValidationBindingState.ACTIVE])[0]
    request = build_domain_validation_request(match).request
    assert request is not None
    result = DomainValidatorResultBase.model_validate(
        {
            "status": status,
            "request_id": request.request_id,
            "validator_binding_id": request.validator_binding_id,
            "validator_agent": request.validator_agent,
            "target": request.target,
            "resolved_values": values or {},
            "resolved_objects": [],
            "missing_expected_fields": list(missing),
            "candidates": [],
            "lookup_attempts": [
                {
                    "provider": "fixture_lookup",
                    "method": "exact_label",
                    "query": {"mention": "skin"},
                    "result_count": 1 if outcome == "success" else 0,
                    "outcome": outcome,
                }
            ],
            "curator_message": None,
            "explanation": "Fixture validator decision.",
        }
    )
    return ValidatorResultMaterializationInput(match=match, request=request, result=result)


def _staged_site():
    return unresolved_value("skin", identity_keys=TERM_KEYS)


def test_resolved_result_writes_identity_and_state_keeping_the_mention():
    metadata = _metadata()
    envelope = _envelope({"site": _staged_site()})
    item = _item(metadata, envelope, values={"curie": "ONT:1", "name": "epidermis"})

    result = materialize_validator_results_into_envelope(envelope, metadata, [item])

    patched = result.envelope.extracted_objects[0]
    assert patched.payload["site"] == {
        "curie": "ONT:1", "name": "epidermis", "mention": "skin",
        "resolution_state": RESOLVED, "lookup_outcome": OUTCOME_MATCHED,
        "validator_explanation": "Fixture validator decision.", "validator_curator_message": None,
    }
    assert patched.status is CuratableObjectStatus.VALIDATED
    event = patched.metadata["validator_resolved_value_materialization"][-1]
    assert event["materialized_field_paths"] == ["site.curie", "site.name"]


@pytest.mark.parametrize(
    ("outcome", "missing", "reason"),
    [("not_found", (), "not_found"), ("ambiguous", (), "ambiguous"), ("error", (), "transient"),
     ("success", (), "rejected_candidates"),
     # An unresolved result that filled nothing names every field as missing whatever the
     # reason: the lookups decide (see test_validator_result_classification for the matrix).
     ("not_found", ("curie", "name"), "not_found"),
     ("success", ("curie", "name"), "rejected_candidates")],
)
def test_unresolved_state_agrees_with_the_finding_classification(outcome, missing, reason):
    metadata = _metadata()
    envelope = _envelope({"site": _staged_site()})
    item = _item(metadata, envelope, status="unresolved", outcome=outcome, missing=missing)

    result = materialize_validator_results_into_envelope(envelope, metadata, [item])

    site = result.envelope.extracted_objects[0].payload["site"]
    assert site == {"curie": None, "name": None, "mention": "skin",
                    "resolution_state": UNRESOLVED, "lookup_outcome": reason,
                    "validator_explanation": "Fixture validator decision.", "validator_curator_message": None}
    parent = next(finding for finding in result.appended_findings if finding.field_ref is None
                  or finding.field_ref.field_path == "site")
    assert lookup_outcome_for_failure(parent.details["failure_classification"]) == site["lookup_outcome"]
    assert result.envelope.extracted_objects[0].status is CuratableObjectStatus.PENDING


def test_partial_resolved_result_leaves_the_value_unresolved():
    metadata = _metadata()
    envelope = _envelope({"site": _staged_site()})
    item = _item(metadata, envelope, values={"curie": "ONT:1"}, missing=("name",))

    result = materialize_validator_results_into_envelope(envelope, metadata, [item])

    site = result.envelope.extracted_objects[0].payload["site"]
    # A partial identity is never written into an unresolved value.
    assert site["curie"] is None
    assert (site["resolution_state"], site["lookup_outcome"]) == (
        UNRESOLVED, "missing_expected_result_field")
    missing = [finding for finding in result.appended_findings
               if finding.code == "domain_pack.validator_expected_field_missing"]
    assert [finding.details["failure_classification"] for finding in missing] == [
        "missing_expected_result_field"]


def test_mirror_copies_take_the_source_value_state():
    metadata = _metadata(mirror=True)
    envelope = _envelope({"site": _staged_site(), "copy": unresolved_value("skin", identity_keys=TERM_KEYS)})

    resolved = materialize_validator_results_into_envelope(
        envelope, metadata, [_item(metadata, envelope, values={"curie": "ONT:1", "name": "epidermis"})],
    ).envelope.extracted_objects[0].payload
    assert resolved["copy"] == {**resolved["site"]}

    unresolved = materialize_validator_results_into_envelope(
        envelope, metadata, [_item(metadata, envelope, status="unresolved", outcome="not_found")],
    ).envelope.extracted_objects[0].payload
    assert unresolved["copy"]["lookup_outcome"] == "not_found"
    assert unresolved["copy"]["validator_explanation"] == "Fixture validator decision."


def test_plain_values_without_a_resolvable_container_patch_as_before():
    metadata = _metadata(input_path="site.name", declared=False)
    envelope = _envelope({"site": {"curie": None, "name": "skin"}})
    item = _item(metadata, envelope, values={"curie": "ONT:1", "name": "epidermis"})

    payload = materialize_validator_results_into_envelope(
        envelope, metadata, [item]).envelope.extracted_objects[0].payload

    assert payload == {"site": {"curie": "ONT:1", "name": "epidermis"}}


def test_a_binding_skipped_for_missing_inputs_leaves_the_value_explicitly_not_validated():
    metadata = _metadata(input_path="site.name")
    binding = metadata.metadata["validator_bindings"]["active"][0]
    binding["input_fields"]["mention"]["required"] = False
    metadata = DomainPackMetadata.model_validate(metadata.model_dump(mode="json"))
    envelope = _envelope({"site": _staged_site()})
    registry = DomainPackValidationRegistry.from_domain_pack(
        LoadedDomainPack(
            pack_id=metadata.pack_id, display_name=metadata.display_name, version=metadata.version,
            pack_path=Path("."), metadata_path=Path("."), metadata=metadata,
        )
    )
    match = registry.match_bindings(envelope, states=[ValidationBindingState.ACTIVE])[0]

    assert build_domain_validation_request(match).request is None
    site = envelope.extracted_objects[0].payload["site"]
    assert (site["resolution_state"], site["lookup_outcome"]) == (UNRESOLVED, OUTCOME_NOT_VALIDATED)
    assert site["validator_explanation"] == NOT_VALIDATED_EXPLANATION


def test_workspace_candidate_matches_read_each_part_from_its_own_key():
    from src.lib.curation_workspace.validation_runtime import _candidate_match_from_mapping

    match = _candidate_match_from_mapping({"value": "ONT:1", "label": "epidermis", "score": 0.9})
    assert (match.identifier, match.label, match.score) == ("ONT:1", "epidermis", 0.9)
    # No label: the label stays missing, never the identifier or another field.
    unlabeled = _candidate_match_from_mapping({"value": "ONT:2", "name": "skin", "symbol": "sk"})
    assert (unlabeled.identifier, unlabeled.label) == ("ONT:2", None)


# --- Object-root resolvable values and declared legacy values -------------------


def _root_pack(*, state_values=None, outcome_values=None):
    from src.schemas.domain_pack_metadata import DomainPackModelDefinition

    return DomainPackMetadata(
        pack_id="fixture.root", display_name="Root", version="0.1.0", metadata_api_version="1.0.0",
        enum_definitions=[
            DomainPackEnumDefinition(enum_id="ResolutionState", display_name="State",
                                     values=[{"value": value} for value in (state_values or RESOLUTION_STATES)]),
            DomainPackEnumDefinition(enum_id="LookupOutcome", display_name="Outcome",
                                     values=[{"value": value} for value in (outcome_values or LOOKUP_OUTCOMES)]),
        ],
        model_definitions=[DomainPackModelDefinition(
            model_id="MentionPayload", display_name="Mention payload",
            metadata={"display": {"label": "symbol", "id": "curie", "mention": "mention"}},
        )],
        object_definitions=[
            DomainPackObjectDefinition(
                object_type="Mention", display_name="Gene mention", model_ref="MentionPayload",
                metadata={"object_role": "curatable_unit",
                          "workspace_display": {"primary_label_field": "symbol"}},
                fields=[
                    DomainPackFieldDefinition(field_path="symbol", field_type=DomainPackFieldType.STRING),
                    DomainPackFieldDefinition(field_path="curie", field_type=DomainPackFieldType.STRING),
                    DomainPackFieldDefinition(field_path="mention", field_type=DomainPackFieldType.STRING),
                    DomainPackFieldDefinition(field_path="resolution_state",
                                              field_type=DomainPackFieldType.ENUM, enum_ref="ResolutionState"),
                    DomainPackFieldDefinition(field_path="lookup_outcome",
                                              field_type=DomainPackFieldType.ENUM, enum_ref="LookupOutcome"),
                ],
            ),
            # Not resolvable: a lookup_outcome field here is someone else's word.
            DomainPackObjectDefinition(
                object_type="Note", display_name="Note",
                fields=[DomainPackFieldDefinition(field_path="lookup_outcome",
                                                  field_type=DomainPackFieldType.STRING)],
            ),
        ],
    )


def test_object_root_vocabulary_leaves_are_checked_and_other_objects_are_not():
    _root_pack()
    with pytest.raises(ValueError, match="Mention.fields.lookup_outcome must be an enum field"):
        _root_pack(outcome_values=["matched", "other"])
    with pytest.raises(ValueError, match="Mention.fields.resolution_state must be an enum field"):
        _root_pack(state_values=["resolved", "pending"])


def test_object_root_leaves_export_with_their_headers_and_plain_words():
    from types import SimpleNamespace

    from src.lib.flows.export_fields import PackagedExportSource, _pack_export_fields
    from src.lib.flows.value_display import display_text

    pack = SimpleNamespace(metadata=_root_pack())
    labels = {field["ref"]: field["label"] for field in _pack_export_fields(pack)}
    assert labels["object.pack.Mention.mention"] == "Gene mention (paper wording)"
    assert labels["object.pack.Mention.resolution_state"] == "Gene mention (status)"
    assert labels["object.pack.Mention.lookup_outcome"] == "Gene mention (lookup result)"
    enum_values = {field["ref"]: field.get("enum_values") for field in _pack_export_fields(pack)}
    assert enum_values["object.pack.Mention.lookup_outcome"] == list(LOOKUP_OUTCOMES)
    specs = PackagedExportSource(pack).display_specs
    assert display_text("not_validated", specs["object.pack.Mention.lookup_outcome"]) == "Not validated yet"
    assert display_text("resolved", specs["object.pack.Mention.resolution_state"]) == "Resolved"
    assert "object.pack.Note.lookup_outcome" not in specs


def test_declared_legacy_values_get_the_legacy_rule_in_labels():
    """A legacy root value with neither a state nor a mention is still declared resolvable."""

    from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
    from src.lib.domain_packs.resolvable_values import declared_resolvable_fields, unresolved_header_text

    metadata = _root_pack()
    specs = declared_resolvable_fields(metadata, "Mention")
    assert set(specs) == {""}
    legacy = {"symbol": "unc-54 myosin", "curie": None}
    # Without the declaration nothing marks it; with it, the legacy rule applies.
    assert unresolved_header_text(legacy, "symbol") is None
    assert unresolved_header_text(legacy, "symbol", resolvable_fields=specs) == (
        f"unc-54 myosin {LEGACY_UNVERIFIED_SUFFIX}")
    covered = {"validator_resolved_value_materialization": [{"materialized_field_paths": ["curie"]}]}
    assert unresolved_header_text({"symbol": "unc-54", "curie": "X:1"}, "symbol", object_metadata=covered,
                                  resolvable_fields=specs) is None

    envelope = DomainEnvelope(
        envelope_id="root-env", domain_pack_id="fixture.root",
        extracted_objects=[CuratableObjectEnvelope(object_type="Mention", pending_ref_id="m-1", payload=legacy)],
    )
    [row] = DomainPackMetadataReviewRowMaterializer(metadata).materialize(envelope, envelope_revision=1)
    assert row.display_label == f"unc-54 myosin {LEGACY_UNVERIFIED_SUFFIX}"


def test_effective_payload_annotates_explicitly_indexed_declared_paths():
    spec = ResolvableSpec(id_key="curie", label_key="label")
    payload = {"terms": [{"curie": "ONT:1", "label": "short"}, {"curie": "ONT:2", "label": "long"}]}
    covered = {"validator_resolved_value_materialization": [{"materialized_field_paths": ["terms[1].curie"]}]}

    effective = effective_payload(payload, {"terms[0]": spec, "terms[1]": spec}, object_metadata=covered)

    assert (effective["terms"][0]["resolution_state"], effective["terms"][0]["lookup_outcome"]) == (
        UNRESOLVED, OUTCOME_LEGACY_UNVERIFIED)
    assert effective["terms"][0]["mention"] == f"short (ONT:1) {LEGACY_UNVERIFIED_SUFFIX}"
    assert (effective["terms"][1]["resolution_state"], effective["terms"][1]["lookup_outcome"]) == (
        RESOLVED, OUTCOME_MATCHED)
    # Only the named element: an index past the end changes nothing.
    assert effective_payload(payload, {"terms[5]": spec}, object_metadata=None) == payload
    assert "resolution_state" not in payload["terms"][0]


def test_header_text_recognises_explicitly_indexed_declared_values():
    from src.lib.domain_packs.resolvable_values import unresolved_header_text

    specs = {"terms[0]": ResolvableSpec(id_key="curie", label_key="label")}
    payload = {"terms": [{"curie": None, "label": "slow growth"}]}
    assert unresolved_header_text(payload, "terms[0].label", resolvable_fields=specs) == (
        f"slow growth {LEGACY_UNVERIFIED_SUFFIX}")



@pytest.mark.parametrize("broken", [
    {"lookup_outcome": "whatever"},
    {"lookup_outcome": None},
    {"resolution_state": "validated"},
    {"lookup_outcome": "not_found"},  # resolved but not matched
    {"validator_explanation": ["not", "text"]},
    {"curie": None, "name": None},  # resolved without an identity
])
def test_invalid_stored_values_read_as_unresolved_with_a_marker_and_never_raise(broken, caplog):
    from src.lib.domain_packs.resolvable_values import (
        INVALID_RECORD_EXPLANATION,
        INVALID_RECORD_SUFFIX,
        stated_value,
    )
    from src.lib.flows.value_display import display_text

    stored = {**resolved_value("skin", {"curie": "ONT:1", "name": "epidermis"}), **broken}
    spec = ResolvableSpec(id_key="curie", label_key="name")

    with caplog.at_level("WARNING"):
        effective = effective_value(stored, spec, covered_by_validator=True)
    assert effective["resolution_state"] == UNRESOLVED
    assert effective["lookup_outcome"] == "invalid_schema"
    assert effective["validator_explanation"] == INVALID_RECORD_EXPLANATION
    assert (effective["curie"], effective["name"]) == (None, None)
    assert effective["mention"] == f"skin {INVALID_RECORD_SUFFIX}"
    assert "breaks the contract" in caplog.text
    # The effective copy satisfies the invariant; the stored value is untouched.
    check_resolvable_value(effective, identity_keys=TERM_KEYS)
    assert stored != effective

    payload = effective_payload({"site": stored}, {"site": spec}, object_metadata=None)
    assert payload["site"]["lookup_outcome"] == "invalid_schema"
    assert display_text(stored, {"label": "name", "id": "curie", "mention": "mention"}) == "UNRESOLVED"
    assert stated_value(stored)["lookup_outcome"] in ("invalid_schema", "matched")


def test_write_paths_enforce_the_vocabulary():
    value = unresolved_value("skin", identity_keys=TERM_KEYS)
    with pytest.raises(ResolvableValueError):
        mark_unresolved(value, "whatever", explanation=None)
    with pytest.raises(ResolvableValueError, match="validator_explanation"):
        mark_unresolved(value, OUTCOME_NOT_FOUND, explanation=["not", "text"])
    with pytest.raises(ResolvableValueError):
        mark_resolved(value, {"curie": "ONT:1"}, explanation={"not": "text"})


def test_a_stored_vocabulary_word_outside_the_vocabulary_is_marked_not_raised():
    from src.lib.flows.value_display import display_text

    assert display_text("whatever", {"value_labels": LOOKUP_OUTCOME_LABELS}) == "Invalid value (whatever)"


# --- Re-validating containers stored before the contract ------------------------

_OLD_PHENOTYPE_TERM = {"curie": "WBPhenotype:0000154", "label": "reduced brood size",
                       "resolution_state": "pending_ontology_resolution"}
_OLD_DISEASE_TERM = {"curie": "DOID:10652", "name": "Alzheimer's disease",
                     "resolution_state": "pending_ontology_resolution"}


def _legacy_metadata(label_key):
    return _metadata(expected={"curie": "site.curie", "label": f"site.{label_key}"}, input_path=f"site.{label_key}")


@pytest.mark.parametrize(("old", "label_key"), [(_OLD_PHENOTYPE_TERM, "label"), (_OLD_DISEASE_TERM, "name")])
def test_old_containers_revalidate_resolved_and_unresolved_without_raising(old, label_key):
    from src.lib.domain_packs.resolvable_values import unresolved_header_text

    metadata = _legacy_metadata(label_key)
    if label_key == "label":
        # The value declares the label key it actually holds.
        metadata = metadata.model_copy(update={"object_definitions": [
            metadata.object_definitions[0].model_copy(update={"fields": [
                *(
                    field.model_copy(update={"metadata": {**field.metadata,
                                                          "display": {**_SITE_DISPLAY, "label": "label"}}})
                    if field.field_path == "site" else field
                    for field in metadata.object_definitions[0].fields
                ),
                DomainPackFieldDefinition(field_path="site.label", field_type=DomainPackFieldType.STRING),
            ]})
        ]})
    spec = ResolvableSpec(id_key="curie", label_key=label_key)

    envelope = _envelope({"site": dict(old)})
    resolved_item = _item(metadata, envelope, values={"curie": old["curie"], "label": old[label_key]})
    resolved = materialize_validator_results_into_envelope(envelope, metadata, [resolved_item])
    site = resolved.envelope.extracted_objects[0].payload["site"]
    assert (site["resolution_state"], site["lookup_outcome"]) == (RESOLVED, OUTCOME_MATCHED)
    assert "mention" not in site
    assert effective_value(site, spec, covered_by_validator=False) is site

    # A non-decisive outcome (a transient lookup error), twice: nothing raises, the
    # old record stays exactly as stored and still reads as unverified paper
    # wording, and the outage is reported as a finding instead.
    transient = _item(metadata, envelope, status="unresolved", outcome="error")
    once = materialize_validator_results_into_envelope(envelope, metadata, [transient])
    twice = materialize_validator_results_into_envelope(once.envelope, metadata, [transient])
    site = twice.envelope.extracted_objects[0].payload["site"]
    assert site == old
    assert any(finding.code == "domain_pack.validator_error" for finding in once.appended_findings)
    effective = effective_value(site, spec, covered_by_validator=False)
    assert (effective["curie"], effective[label_key], effective["lookup_outcome"]) == (
        None, None, OUTCOME_LEGACY_UNVERIFIED)
    assert effective["mention"] == f"{old[label_key]} ({old['curie']}) {LEGACY_UNVERIFIED_SUFFIX}"
    assert unresolved_header_text({"site": site}, f"site.{label_key}", resolvable_fields={"site": spec}) == (
        f"{old[label_key]} ({old['curie']}) {LEGACY_UNVERIFIED_SUFFIX}")

    # A decisive outcome sets the old identity aside, like an overruled one.
    decided = _item(metadata, envelope, status="unresolved", outcome="not_found")
    site = materialize_validator_results_into_envelope(
        envelope, metadata, [decided]).envelope.extracted_objects[0].payload["site"]
    assert (site["resolution_state"], site["lookup_outcome"], site["curie"]) == (
        UNRESOLVED, OUTCOME_NOT_FOUND, None)
    assert site["overruled_curie"] == old["curie"]
    check_resolvable_value(site, identity_keys=spec.identity_keys)


def test_a_legacy_value_with_paper_wording_is_left_alone_by_an_outage():
    """M1/F1: an old value with a mention and an identity, re-validated unresolved."""

    spec = ResolvableSpec(id_key="curie", label_key="name")
    metadata = _metadata()
    old = {"mention": "skin", "curie": "ONT:9", "name": "old guess"}
    envelope = _envelope({"site": dict(old)})

    stale = materialize_validator_results_into_envelope(
        envelope, metadata, [_item(metadata, envelope, status="unresolved", outcome="error")],
    ).envelope.extracted_objects[0].payload["site"]
    assert stale == old
    effective = effective_value(stale, spec, covered_by_validator=False)
    assert (effective["lookup_outcome"], effective["curie"], effective["name"]) == (
        OUTCOME_LEGACY_UNVERIFIED, None, None)
    assert "invalid record" not in str(effective)

    decided = materialize_validator_results_into_envelope(
        envelope, metadata, [_item(metadata, envelope, status="unresolved", outcome="not_found")],
    ).envelope.extracted_objects[0].payload["site"]
    assert (decided["curie"], decided["overruled_curie"], decided["lookup_outcome"]) == (
        None, "ONT:9", OUTCOME_NOT_FOUND)
    assert effective_value(decided, spec, covered_by_validator=False) is decided


def test_an_outage_leaves_a_validated_legacy_value_validated():
    """F1: an old value the legacy rule reads as validated stays so after an outage."""

    spec = ResolvableSpec(id_key="curie", label_key="name")
    metadata = _metadata()
    old = {"mention": "gene-22", "curie": "G:22", "name": "gene-22"}
    envelope = _envelope({"site": dict(old)})
    covered = envelope.extracted_objects[0].model_copy(update={"metadata": {
        "validator_resolved_value_materialization": [{"materialized_field_paths": ["site.curie"]}]}})
    envelope = envelope.model_copy(update={"extracted_objects": [covered]})

    result = materialize_validator_results_into_envelope(
        envelope, metadata, [_item(metadata, envelope, status="unresolved", outcome="error")])

    stored = result.envelope.extracted_objects[0]
    assert stored.payload["site"] == old
    effective = effective_payload(stored.payload, {"site": spec}, object_metadata=stored.metadata)["site"]
    assert (effective["resolution_state"], effective["lookup_outcome"], effective["curie"]) == (
        RESOLVED, OUTCOME_MATCHED, "G:22")
    assert any(finding.code == "domain_pack.validator_error" for finding in result.appended_findings)


def test_an_undeclared_container_in_contract_shape_is_reported_not_written():
    """F2: a write into an undeclared container that holds a resolvable value."""

    metadata = _metadata(input_path="site.mention", declared=False)
    site = unresolved_value("skin", identity_keys=TERM_KEYS)
    envelope = _envelope({"site": dict(site)})
    item = _item(metadata, envelope, values={"curie": "ONT:1", "name": "epidermis"})

    result = materialize_validator_results_into_envelope(envelope, metadata, [item])

    assert result.envelope.extracted_objects[0].payload == {"site": site}
    problem = [finding for finding in result.appended_findings
               if finding.code == "domain_pack.validator_materialization_invalid"]
    assert len(problem) == 1
    assert "does not declare" in problem[0].details["materialization_error"]


def test_an_unresolved_contract_value_holding_an_identity_reads_as_invalid():
    """LOW: only a pre-contract value (no paper wording) can be a re-validation leftover."""

    spec = ResolvableSpec(id_key="curie", label_key="name")
    broken = {"mention": "skin", "curie": "ONT:9", "name": "old guess", "resolution_state": UNRESOLVED,
              "lookup_outcome": OUTCOME_NOT_FOUND, "validator_explanation": None}

    effective = effective_value(broken, spec, covered_by_validator=False)

    assert (effective["lookup_outcome"], effective["curie"], effective["name"]) == ("invalid_schema", None, None)
    assert "invalid record" in effective["mention"]


def test_a_contract_value_still_needs_non_empty_paper_wording():
    with pytest.raises(ResolvableValueError, match="paper wording"):
        check_resolvable_value({"curie": None, "mention": "  ", "resolution_state": "unresolved",
                                "lookup_outcome": "not_found"}, identity_keys=TERM_KEYS)


# --- Validator-filled keys beyond id/label (``validated``) ----------------------

GENE_DISPLAY = {"label": "gene_symbol", "id": "primary_external_id", "mention": "mention", "validated": ["taxon"]}


def test_validated_keys_are_part_of_the_identity():
    spec = resolvable_spec_from_display(GENE_DISPLAY)
    assert spec.validated_keys == ("taxon",)
    assert spec.identity_keys == ("primary_external_id", "gene_symbol", "taxon")

    staged = unresolved_value("unc-54", identity_keys=spec.identity_keys)
    assert staged["taxon"] is None
    # An unresolved value never carries a validator-filled taxon.
    with pytest.raises(ResolvableValueError, match="taxon"):
        check_resolvable_value({**staged, "taxon": "NCBITaxon:6239"}, identity_keys=spec.identity_keys)
    mark_resolved(staged, {"primary_external_id": "G:1", "gene_symbol": "unc-54", "taxon": "NCBITaxon:6239"},
                  explanation=None)
    check_resolvable_value(staged, identity_keys=spec.identity_keys)


def test_the_legacy_rule_empties_validated_keys_and_the_cell_stays_label_and_id():
    from src.lib.flows.value_display import display_text

    spec = resolvable_spec_from_display(GENE_DISPLAY)
    legacy = {"gene_symbol": "unc-54", "primary_external_id": "G:1", "taxon": "NCBITaxon:6239"}
    effective = effective_value(legacy, spec, covered_by_validator=False)
    assert (effective["gene_symbol"], effective["primary_external_id"], effective["taxon"]) == (None, None, None)
    covered = effective_value(legacy, spec, covered_by_validator=True)
    assert covered["taxon"] == "NCBITaxon:6239"
    assert display_text(covered, GENE_DISPLAY) == "unc-54 (G:1)"
    # A resolved value missing its taxon still reads resolved: the identity is present.
    resolved = resolved_value("unc-54", {"gene_symbol": "unc-54", "primary_external_id": "G:1", "taxon": None})
    assert display_text(resolved, GENE_DISPLAY) == "unc-54 (G:1)"


def test_validated_must_be_declared_leaves_of_a_resolvable_value():
    from src.schemas.domain_pack_metadata import DomainPackModelDefinition

    DomainPackFieldDefinition(field_path="gene", metadata={"display": GENE_DISPLAY})
    for bad in (["taxon", "taxon"], ["gene_symbol"], ["organism.taxon"], [], "taxon"):
        with pytest.raises(ValueError, match="validated"):
            DomainPackFieldDefinition(field_path="gene", metadata={"display": {**GENE_DISPLAY, "validated": bad}})
    with pytest.raises(ValueError, match="only for a resolvable value"):
        DomainPackFieldDefinition(field_path="gene", metadata={"display": {"label": "name", "validated": ["taxon"]}})

    def pack(root_fields, field_fields):
        return DomainPackMetadata(
            pack_id="fixture.validated", display_name="V", version="0.1.0", metadata_api_version="1.0.0",
            model_definitions=[DomainPackModelDefinition(model_id="Gene", display_name="Gene",
                                                         metadata={"display": GENE_DISPLAY})],
            object_definitions=[DomainPackObjectDefinition(
                object_type="GeneMention", display_name="Gene mention", model_ref="Gene",
                fields=[
                    *(DomainPackFieldDefinition(field_path=path, field_type=DomainPackFieldType.STRING)
                      for path in root_fields),
                    DomainPackFieldDefinition(field_path="allele", field_type=DomainPackFieldType.OBJECT,
                                              metadata={"display": GENE_DISPLAY}),
                    *(DomainPackFieldDefinition(field_path=path, field_type=DomainPackFieldType.STRING)
                      for path in field_fields),
                ],
            )],
        )

    pack(["taxon"], ["allele.taxon"])
    with pytest.raises(ValueError, match="validated key 'taxon' of '<object root>'"):
        pack([], ["allele.taxon"])
    with pytest.raises(ValueError, match="validated key 'taxon' of 'allele'"):
        pack(["taxon"], [])


def test_profile_validator_events_count_as_validator_coverage():
    metadata = {"profile_validator_materialization": [
        {"field_paths": ["attributes.gene.gene_id", "attributes.records[1].resolved_id",
                         "attributes.terms[].curie"]},
    ]}
    assert validator_event_covers(metadata, "attributes.gene")
    assert validator_event_covers(metadata, "attributes.records[1]")
    assert not validator_event_covers(metadata, "attributes.records[0]")
    # "[]" covers every element.
    assert validator_event_covers(metadata, "attributes.terms[3]")
    assert not validator_event_covers(metadata, "attributes.other")

    spec = ResolvableSpec(id_key="gene_id", label_key="symbol")
    payload = {"attributes": {"gene": {"gene_id": "G:1", "symbol": "unc-54"}}}
    effective = effective_payload(payload, {"attributes.gene": spec}, object_metadata=metadata)
    assert (effective["attributes"]["gene"]["resolution_state"],
            effective["attributes"]["gene"]["lookup_outcome"]) == (RESOLVED, OUTCOME_MATCHED)
    unverified = effective_payload(payload, {"attributes.gene": spec}, object_metadata={})
    assert unverified["attributes"]["gene"]["lookup_outcome"] == OUTCOME_LEGACY_UNVERIFIED


def test_a_list_and_its_indexed_element_both_declared_read_each_value_once():
    """A pack may declare ``terms`` and ``terms[0]``; element 0 must not be read twice."""

    from src.lib.flows.export_fields import PackagedExportSource
    from src.lib.flows.value_display import display_text
    from src.schemas.domain_pack_metadata import DomainPackModelDefinition
    from types import SimpleNamespace

    term_display = {"label": "label", "id": "curie", "mention": "mention"}
    metadata = DomainPackMetadata(
        pack_id="fixture.terms", display_name="Terms", version="0.1.0", metadata_api_version="1.0.0",
        model_definitions=[DomainPackModelDefinition(model_id="Term", display_name="Term",
                                                     metadata={"display": term_display})],
        object_definitions=[DomainPackObjectDefinition(
            object_type="Annotation", display_name="Annotation",
            fields=[
                DomainPackFieldDefinition(field_path="terms", field_type=DomainPackFieldType.ARRAY,
                                          model_ref="Term"),
                DomainPackFieldDefinition(field_path="terms[0]", field_type=DomainPackFieldType.OBJECT,
                                          model_ref="Term"),
            ],
        )],
    )
    specs = declared_resolvable_fields_for_test(metadata)
    assert set(specs) == {"terms", "terms[0]"}
    legacy = {"terms": [{"curie": "T:1", "label": "slow growth"}, {"curie": "T:2", "label": "small"}]}

    effective = effective_payload(legacy, specs, object_metadata=None)

    for index, (label, curie) in enumerate((("slow growth", "T:1"), ("small", "T:2"))):
        term = effective["terms"][index]
        assert (term["resolution_state"], term["lookup_outcome"]) == (UNRESOLVED, OUTCOME_LEGACY_UNVERIFIED)
        assert term["mention"] == f"{label} ({curie}) {LEGACY_UNVERIFIED_SUFFIX}"
        assert "invalid record" not in term["mention"]
    item = PackagedExportSource(SimpleNamespace(metadata=metadata)).effective_item(
        {"object_type": "Annotation", "payload": legacy}
    )
    assert item["payload"] == effective
    assert display_text(effective["terms"][0], term_display) == "UNRESOLVED"


def declared_resolvable_fields_for_test(metadata):
    from src.lib.domain_packs.resolvable_values import declared_resolvable_fields

    return declared_resolvable_fields(metadata, "Annotation")


# --- Legacy coverage: mirrors, list-level writes, re-validation upgrades --------

_TERM_DISPLAY = {"label": "name", "id": "curie", "mention": "mention"}
_GENE_DISPLAY = {"label": "gene_symbol", "id": "primary_external_id", "mention": "mention"}


def _expression_metadata():
    def field(path, **kwargs):
        return DomainPackFieldDefinition(field_path=path, field_type=kwargs.pop("field_type",
                                         DomainPackFieldType.STRING), **kwargs)

    return DomainPackMetadata(
        pack_id="fixture.expression",
        display_name="Fixture Expression",
        version="0.1.0",
        metadata_api_version="1.0.0",
        metadata={"validator_bindings": {"active": [
            {
                "binding_id": "fixture.subject_lookup",
                "display_name": "Subject lookup",
                "validator_agent": {"package_id": "fixture.validators", "agent_id": "gene_validator"},
                "applies_to": {"domain_pack_id": "fixture.expression", "object_types": ["Expression"]},
                "input_fields": {"symbol": {"source": "payload", "path": "subject.gene_symbol"}},
                "expected_result_fields": {"primary_external_id": "subject.primary_external_id",
                                           "gene_symbol": "subject.gene_symbol"},
            },
            {
                "binding_id": "fixture.slim_lookup",
                "display_name": "Slim lookup",
                "validator_agent": {"package_id": "fixture.validators", "agent_id": "term_validator"},
                "applies_to": {"domain_pack_id": "fixture.expression", "object_types": ["Expression"]},
                "input_fields": {"terms": {"source": "payload", "path": "slim_terms", "allow_multiple": True}},
                "expected_result_fields": {"terms": "slim_terms"},
            },
        ], "under_development": []}},
        object_definitions=[DomainPackObjectDefinition(
            object_type="Expression", display_name="Expression",
            metadata={"object_role": "curatable_unit"},
            fields=[
                field("subject", field_type=DomainPackFieldType.OBJECT, metadata={"display": _GENE_DISPLAY}),
                field("subject.primary_external_id",
                      metadata={"materializes_to_field_paths": ["experiment.entity_assayed.primary_external_id"]}),
                field("subject.gene_symbol",
                      metadata={"materializes_to_field_paths": ["experiment.entity_assayed.gene_symbol"]}),
                field("experiment", field_type=DomainPackFieldType.OBJECT),
                field("experiment.entity_assayed", field_type=DomainPackFieldType.OBJECT,
                      metadata={"display": _GENE_DISPLAY}),
                field("experiment.entity_assayed.primary_external_id"),
                field("experiment.entity_assayed.gene_symbol"),
                field("slim_terms", field_type=DomainPackFieldType.ARRAY, metadata={"display": _TERM_DISPLAY}),
            ],
        )],
    )


def _legacy_expression_payload():
    # Stored before the contract: no mention, no state, no lookup outcome.
    gene = {"primary_external_id": "G:1", "gene_symbol": "tmem-67"}
    return {"subject": dict(gene), "experiment": {"entity_assayed": dict(gene)},
            "slim_terms": [{"curie": "U:1", "name": "embryo"}, {"curie": "U:2", "name": "adult"}]}


def test_mirror_sources_and_list_level_writes_cover_legacy_values():
    from src.lib.domain_packs.resolvable_values import declared_resolvable_fields

    specs = declared_resolvable_fields(_expression_metadata(), "Expression")
    assert specs["experiment.entity_assayed"].covered_by == (
        "subject.primary_external_id", "subject.gene_symbol")
    # An old event recorded only the source subject paths and the whole slim list.
    metadata = {"validator_resolved_value_materialization": [
        {"original_values": {"subject.primary_external_id": "G:1", "slim_terms": []}},
    ]}
    effective = effective_payload(_legacy_expression_payload(), specs, object_metadata=metadata)
    assert effective["subject"]["lookup_outcome"] == OUTCOME_MATCHED
    assert effective["experiment"]["entity_assayed"]["lookup_outcome"] == OUTCOME_MATCHED
    assert [term["lookup_outcome"] for term in effective["slim_terms"]] == [OUTCOME_MATCHED] * 2
    # Without an event they stay unverified.
    unverified = effective_payload(_legacy_expression_payload(), specs, object_metadata={})
    assert unverified["experiment"]["entity_assayed"]["lookup_outcome"] == OUTCOME_LEGACY_UNVERIFIED


def test_revalidating_a_legacy_record_upgrades_declared_values_and_records_the_event():
    from src.lib.domain_packs.resolvable_values import declared_resolvable_fields

    metadata = _expression_metadata()
    envelope = DomainEnvelope(
        envelope_id="legacy-expression", domain_pack_id="fixture.expression",
        extracted_objects=[CuratableObjectEnvelope(
            object_type="Expression", pending_ref_id="expression-1",
            status=CuratableObjectStatus.VALIDATED, payload=_legacy_expression_payload(),
        )],
    )
    registry = DomainPackValidationRegistry.from_domain_pack(LoadedDomainPack(
        pack_id=metadata.pack_id, display_name=metadata.display_name, version=metadata.version,
        pack_path=Path("."), metadata_path=Path("."), metadata=metadata,
    ))
    items = []
    for match in registry.match_bindings(envelope, states=[ValidationBindingState.ACTIVE]):
        request = build_domain_validation_request(match).request
        values = (
            {"primary_external_id": "G:1", "gene_symbol": "tmem-67"}
            if request.validator_binding_id == "fixture.subject_lookup"
            else {"terms": [{"curie": "U:1", "name": "embryo"}, {"curie": "U:2", "name": "adult"}]}
        )
        result = DomainValidatorResultBase.model_validate({
            "status": "resolved", "request_id": request.request_id,
            "validator_binding_id": request.validator_binding_id, "validator_agent": request.validator_agent,
            "target": request.target, "resolved_values": values, "resolved_objects": [],
            "missing_expected_fields": [], "candidates": [],
            "lookup_attempts": [{"provider": "fixture", "method": "exact", "query": {}, "result_count": 1,
                                 "outcome": "success"}],
            "curator_message": None, "explanation": "Matched the stored identity.",
        })
        items.append(ValidatorResultMaterializationInput(match=match, request=request, result=result))

    result = materialize_validator_results_into_envelope(envelope, metadata, items)

    patched = result.envelope.extracted_objects[0]
    # The declared subject took the contract shape, and its mirror too.
    assert (patched.payload["subject"]["resolution_state"], patched.payload["subject"]["lookup_outcome"]) == (
        RESOLVED, OUTCOME_MATCHED)
    assert patched.payload["subject"]["validator_explanation"] == "Matched the stored identity."
    assert patched.payload["experiment"]["entity_assayed"]["lookup_outcome"] == OUTCOME_MATCHED
    # Unchanged but uncovered values still get an event, so the legacy rule sees them.
    events = patched.metadata["validator_resolved_value_materialization"]
    assert {path for event in events for path in event["materialized_field_paths"]} >= {
        "subject.primary_external_id", "slim_terms"}
    effective = effective_payload(
        patched.payload, declared_resolvable_fields(metadata, "Expression"), object_metadata=patched.metadata,
    )
    assert all(term["lookup_outcome"] == OUTCOME_MATCHED for term in effective["slim_terms"])

    # A second identical re-validation adds no further event.
    again = materialize_validator_results_into_envelope(result.envelope, metadata, items)
    assert len(again.envelope.extracted_objects[0].metadata["validator_resolved_value_materialization"]) == len(
        events)



def test_a_validator_overrules_a_builder_resolved_value():
    """The builder's deterministic lookup resolved it; the validator says no."""

    metadata = _metadata(mirror=True)
    builder_resolved = resolved_value("skin", {"curie": "ONT:1", "name": "epidermis"},
                                      explanation="Matched by the builder's lookup.",
                                      proposed_curie="ONT:9")  # the extractor's own proposal
    envelope = _envelope({"site": dict(builder_resolved), "copy": dict(builder_resolved)})
    item = _item(metadata, envelope, status="unresolved", outcome="not_found")

    result = materialize_validator_results_into_envelope(envelope, metadata, [item])

    payload = result.envelope.extracted_objects[0].payload
    for key in ("site", "copy"):
        value = payload[key]
        assert (value["resolution_state"], value["lookup_outcome"]) == (UNRESOLVED, OUTCOME_NOT_FOUND)
        assert (value["curie"], value["name"]) == (None, None)
        assert (value["overruled_curie"], value["overruled_name"]) == ("ONT:1", "epidermis")
        # The extractor's proposal (a validator input) is never touched.
        assert value["proposed_curie"] == "ONT:9"
        assert value["validator_explanation"] == "Fixture validator decision."
        assert value["mention"] == "skin"
        check_resolvable_value(value, identity_keys=TERM_KEYS)
    assert effective_value(payload["site"], ResolvableSpec(id_key="curie", label_key="name"),
                           covered_by_validator=True) is payload["site"]



def test_overruled_identities_are_informational_only():
    from types import SimpleNamespace

    from src.lib.domain_packs.resolvable_values import overruled_key, without_overruled
    from src.lib.flows.export_fields import PackagedExportSource
    from src.lib.flows.value_display import display_text

    assert overruled_key("curie") == "overruled_curie"
    value = resolved_value("skin", {"curie": "ONT:1", "name": "epidermis"})
    mark_unresolved(value, OUTCOME_NOT_FOUND, explanation="No.", identity_keys=TERM_KEYS)
    assert value["overruled_curie"] == "ONT:1"

    # Never the value, never exported.
    assert display_text(value, _TERM_DISPLAY) == "UNRESOLVED"
    assert "overruled_curie" not in without_overruled({"site": value})["site"]
    item = PackagedExportSource(SimpleNamespace(metadata=_expression_metadata())).effective_item(
        {"object_type": "Expression", "payload": {"subject": value, "overruled_note": "x"}}
    )
    assert "overruled_curie" not in item["payload"]["subject"]
    assert "overruled_note" not in item["payload"]

    # Never a validator input, even when a selector reads the whole value.
    metadata = _metadata(input_path="site")
    envelope = _envelope({"site": value})
    registry = DomainPackValidationRegistry.from_domain_pack(LoadedDomainPack(
        pack_id=metadata.pack_id, display_name=metadata.display_name, version=metadata.version,
        pack_path=Path("."), metadata_path=Path("."), metadata=metadata,
    ))
    match = registry.match_bindings(envelope, states=[ValidationBindingState.ACTIVE])[0]
    request = build_domain_validation_request(match).request
    assert "overruled_curie" not in request.selected_inputs["mention"]

    # A fresh resolution replaces the overruled identity.
    mark_resolved(value, {"curie": "ONT:2", "name": "skin"}, explanation="Matched.")
    assert not any(key.startswith("overruled_") for key in value)


# --- H1: resolvable values come only from declarations --------------------------


def _annotation_metadata():
    def binding(binding_id, input_path, expected):
        return {
            "binding_id": binding_id,
            "display_name": binding_id,
            "validator_agent": {"package_id": "fixture.validators", "agent_id": "cv_validator"},
            "applies_to": {"domain_pack_id": "fixture.annotation", "object_types": ["Annotation"]},
            "input_fields": {"text": {"source": "payload", "path": input_path}},
            "expected_result_fields": expected,
        }

    return DomainPackMetadata(
        pack_id="fixture.annotation", display_name="Annotation", version="0.1.0", metadata_api_version="1.0.0",
        metadata={"validator_bindings": {"active": [
            binding("fixture.annotation_type", "mention", {"term_name": "annotation_type_name"}),
            binding("fixture.relation", "mention", {"term_name": "relation_name"}),
        ], "under_development": []}},
        object_definitions=[DomainPackObjectDefinition(
            object_type="Annotation", display_name="Annotation", metadata={"object_role": "curatable_unit"},
            fields=[DomainPackFieldDefinition(field_path=path, field_type=DomainPackFieldType.STRING)
                    for path in ("mention", "annotation_type_name", "relation_name")],
        )],
    )


def test_an_undeclared_object_root_with_a_mention_is_not_a_resolvable_value():
    metadata = _annotation_metadata()
    envelope = DomainEnvelope(
        envelope_id="annotation-env", domain_pack_id="fixture.annotation",
        extracted_objects=[CuratableObjectEnvelope(
            object_type="Annotation", pending_ref_id="annotation-1",
            payload={"mention": "paper sentence", "annotation_type_name": None, "relation_name": None},
        )],
    )
    registry = DomainPackValidationRegistry.from_domain_pack(LoadedDomainPack(
        pack_id=metadata.pack_id, display_name=metadata.display_name, version=metadata.version,
        pack_path=Path("."), metadata_path=Path("."), metadata=metadata,
    ))
    items = []
    for match in registry.match_bindings(envelope, states=[ValidationBindingState.ACTIVE]):
        request = build_domain_validation_request(match).request
        resolved = request.validator_binding_id == "fixture.annotation_type"
        result = DomainValidatorResultBase.model_validate({
            "status": "resolved" if resolved else "unresolved", "request_id": request.request_id,
            "validator_binding_id": request.validator_binding_id, "validator_agent": request.validator_agent,
            "target": request.target, "resolved_values": {"term_name": "manually_curated"} if resolved else {},
            "resolved_objects": [], "missing_expected_fields": [], "candidates": [],
            "lookup_attempts": [{"provider": "f", "method": "m", "query": {}, "result_count": 1,
                                 "outcome": "success" if resolved else "not_found"}],
            "curator_message": None, "explanation": "e",
        })
        items.append(ValidatorResultMaterializationInput(match=match, request=request, result=result))

    for ordered in (items, list(reversed(items))):
        payload = materialize_validator_results_into_envelope(
            envelope, metadata, ordered).envelope.extracted_objects[0].payload
        # Plain top-level fields: the resolved one is written; nothing is wiped
        # or given a root "state", whatever order the bindings come back in.
        assert payload == {"mention": "paper sentence", "annotation_type_name": "manually_curated",
                           "relation_name": None}


def test_object_root_coverage_needs_an_event_on_its_identity_keys():
    metadata = {"validator_resolved_value_materialization": [{"materialized_field_paths": ["relation_name"]}]}
    assert not validator_event_covers(metadata, "")
    assert not validator_event_covers(metadata, "", ("gene_symbol", "primary_external_id"))
    covered = {"validator_resolved_value_materialization": [{"materialized_field_paths": ["gene_symbol"]}]}
    assert validator_event_covers(covered, "", ("gene_symbol", "primary_external_id"))
    spec = ResolvableSpec(id_key="primary_external_id", label_key="gene_symbol")
    legacy_root = {"gene_symbol": "unc-54", "primary_external_id": "G:1"}
    assert effective_payload(legacy_root, {"": spec}, object_metadata=metadata)["lookup_outcome"] == (
        OUTCOME_LEGACY_UNVERIFIED)
    assert effective_payload(legacy_root, {"": spec}, object_metadata=covered)["lookup_outcome"] == OUTCOME_MATCHED


# --- H2: only decisive outcomes overrule a resolved value -----------------------


def _validated_gene_site():
    return resolved_value("unc-54", {"curie": "G:1", "name": "unc-54"}, explanation="Validated earlier.")


def test_an_api_outage_never_touches_an_earlier_validated_value():
    metadata = _metadata(mirror=True)
    envelope = _envelope({"site": _validated_gene_site(), "copy": _validated_gene_site()})
    item = _item(metadata, envelope, status="unresolved", outcome="error")

    result = materialize_validator_results_into_envelope(envelope, metadata, [item])

    payload = result.envelope.extracted_objects[0].payload
    assert payload["site"] == _validated_gene_site()
    assert payload["copy"] == _validated_gene_site()
    # The outage is reported as a finding instead.
    assert any(finding.code == "domain_pack.validator_error" for finding in result.appended_findings)


@pytest.mark.parametrize("outcome", ["transient", "invalid_schema", "missing_expected_result_field", "blocked"])
def test_non_decisive_outcomes_leave_a_resolved_value_as_it_was(outcome):
    value = _validated_gene_site()
    mark_unresolved(value, outcome, explanation="x", identity_keys=TERM_KEYS)
    assert value == _validated_gene_site()


@pytest.mark.parametrize("outcome", ["not_found", "ambiguous", "conflict", "rejected_candidates"])
def test_decisive_outcomes_overrule_a_resolved_value(outcome):
    value = _validated_gene_site()
    mark_unresolved(value, outcome, explanation="Decided.", identity_keys=TERM_KEYS)
    assert (value["resolution_state"], value["lookup_outcome"], value["curie"]) == (UNRESOLVED, outcome, None)
    assert value["overruled_curie"] == "G:1"


def test_a_never_resolved_value_takes_any_outcome():
    value = unresolved_value("unc-54", identity_keys=TERM_KEYS)
    mark_unresolved(value, "transient", explanation="Lookup service unavailable.")
    assert (value["resolution_state"], value["lookup_outcome"]) == (UNRESOLVED, "transient")


def test_a_partial_or_policy_rejected_result_never_touches_a_resolved_value():
    metadata = _metadata()
    envelope = _envelope({"site": _validated_gene_site()})
    partial = _item(metadata, envelope, values={"curie": "G:2"}, missing=("name",))

    payload = materialize_validator_results_into_envelope(
        envelope, metadata, [partial]).envelope.extracted_objects[0].payload

    assert payload["site"] == _validated_gene_site()


# --- M2: one value that cannot be written never aborts the run ------------------


def test_a_value_that_cannot_be_written_becomes_a_finding_and_the_rest_are_written():
    metadata = _metadata()
    envelope = DomainEnvelope(
        envelope_id="two-objects", domain_pack_id="fixture.resolvable",
        extracted_objects=[
            CuratableObjectEnvelope(object_type="Observation", pending_ref_id="broken",
                                    # A blank paper wording breaks the contract on any write.
                                    payload={"site": {"mention": "  ", "curie": None, "name": None,
                                                      "resolution_state": "unresolved",
                                                      "lookup_outcome": "not_validated"}}),
            CuratableObjectEnvelope(object_type="Observation", pending_ref_id="fine",
                                    payload={"site": _staged_site()}),
        ],
    )
    registry = DomainPackValidationRegistry.from_domain_pack(LoadedDomainPack(
        pack_id=metadata.pack_id, display_name=metadata.display_name, version=metadata.version,
        pack_path=Path("."), metadata_path=Path("."), metadata=metadata,
    ))
    items = []
    for match in registry.match_bindings(envelope, states=[ValidationBindingState.ACTIVE]):
        request = build_domain_validation_request(match).request
        result = DomainValidatorResultBase.model_validate({
            "status": "unresolved", "request_id": request.request_id,
            "validator_binding_id": request.validator_binding_id, "validator_agent": request.validator_agent,
            "target": request.target, "resolved_values": {}, "resolved_objects": [],
            "missing_expected_fields": [], "candidates": [],
            "lookup_attempts": [{"provider": "f", "method": "m", "query": {}, "result_count": 0,
                                 "outcome": "not_found"}],
            "curator_message": None, "explanation": "e",
        })
        items.append(ValidatorResultMaterializationInput(match=match, request=request, result=result))

    result = materialize_validator_results_into_envelope(envelope, metadata, items)

    broken, fine = result.envelope.extracted_objects
    assert broken.payload == envelope.extracted_objects[0].payload
    assert fine.payload["site"]["lookup_outcome"] == OUTCOME_NOT_FOUND
    problem = [finding for finding in result.appended_findings
               if finding.code == "domain_pack.validator_materialization_invalid"]
    assert len(problem) == 1
    assert "could not be written" in problem[0].details["materialization_error"]


def test_a_resolved_mirror_of_a_never_resolved_source_is_overruled_with_its_own_keys():
    metadata = _metadata(mirror=True)
    source = {"mention": "skin", "resolution_state": UNRESOLVED, "lookup_outcome": OUTCOME_NOT_VALIDATED,
              "validator_explanation": NOT_VALIDATED_EXPLANATION}
    envelope = _envelope({"site": source, "copy": _validated_gene_site()})

    result = materialize_validator_results_into_envelope(
        envelope, metadata, [_item(metadata, envelope, status="unresolved", outcome="not_found")],
    )

    copy = result.envelope.extracted_objects[0].payload["copy"]
    assert (copy["lookup_outcome"], copy["curie"], copy["overruled_curie"]) == (OUTCOME_NOT_FOUND, None, "G:1")
    assert not any(finding.code == "domain_pack.validator_materialization_invalid"
                   for finding in result.appended_findings)


# --- M3: reading an export stays fast ------------------------------------------


def test_effective_payload_reads_a_large_export_quickly():
    """300 objects x 28 declared values x 30 validator events read well under a second or two."""

    import time

    spec = ResolvableSpec(id_key="curie", label_key="name")
    specs = {f"value_{index}": spec for index in range(28)}
    metadata = {"validator_resolved_value_materialization": [
        {"materialized_field_paths": [f"value_{event % 28}.curie", f"value_{event % 28}.name"],
         "original_values": {f"value_{event % 28}.name": "x"}}
        for event in range(30)
    ]}
    legacy = {f"value_{index}": {"curie": f"T:{index}", "name": f"term {index}"} for index in range(28)}
    contract = {
        f"value_{index}": resolved_value(f"term {index}", {"curie": f"T:{index}", "name": f"term {index}"})
        for index in range(28)
    }

    started = time.perf_counter()
    for index in range(300):
        effective = effective_payload(legacy if index % 2 else contract, specs, object_metadata=metadata)
    elapsed = time.perf_counter() - started

    assert effective["value_0"]["lookup_outcome"] == OUTCOME_MATCHED
    assert elapsed < 2.0, elapsed


# --- M4: a re-validation with the same decision appends no event ---------------


def test_revalidation_with_new_wording_updates_the_explanation_without_a_new_event():
    metadata = _metadata()
    envelope = _envelope({"site": _staged_site()})
    first = materialize_validator_results_into_envelope(
        envelope, metadata, [_item(metadata, envelope, values={"curie": "ONT:1", "name": "epidermis"})],
    ).envelope
    events = first.extracted_objects[0].metadata["validator_resolved_value_materialization"]

    item = _item(metadata, first, values={"curie": "ONT:1", "name": "epidermis"})
    reworded = item.result.model_copy(update={"explanation": "Same term, different words."})
    second = materialize_validator_results_into_envelope(
        first, metadata, [ValidatorResultMaterializationInput(match=item.match, request=item.request,
                                                              result=reworded)],
    ).envelope

    patched = second.extracted_objects[0]
    assert patched.metadata["validator_resolved_value_materialization"] == events
    assert patched.payload["site"]["validator_explanation"] == "Same term, different words."
    assert patched.payload["site"]["curie"] == "ONT:1"


# --- M6: plain-text legacy values at declared paths -----------------------------


def test_plain_text_stored_at_a_declared_path_reads_as_legacy_paper_wording():
    from src.lib.domain_packs.resolvable_values import unresolved_header_text
    from src.lib.flows.value_display import display_text

    spec = ResolvableSpec(id_key="curie", label_key="name")
    payload = {"site": "hypodermis", "terms": ["embryo", "adult"], "empty": None}
    effective = effective_payload(payload, {"site": spec, "terms": spec, "empty": spec}, object_metadata=None)

    assert effective["site"] == {
        "curie": None, "name": None, "mention": f"hypodermis {LEGACY_UNVERIFIED_SUFFIX}",
        "resolution_state": UNRESOLVED, "lookup_outcome": OUTCOME_LEGACY_UNVERIFIED,
        "validator_explanation": LEGACY_EXPLANATION,
    }
    assert [term["mention"] for term in effective["terms"]] == [
        f"embryo {LEGACY_UNVERIFIED_SUFFIX}", f"adult {LEGACY_UNVERIFIED_SUFFIX}"]
    assert effective["empty"] is None
    assert display_text(effective["site"], _TERM_DISPLAY) == "UNRESOLVED"
    assert unresolved_header_text(payload, "site", resolvable_fields={"site": spec}) == (
        f"hypodermis {LEGACY_UNVERIFIED_SUFFIX}")
    assert payload["site"] == "hypodermis"


# --- LOW: a fresh resolution leaves no stale identity or proposal ---------------


def test_mark_resolved_clears_identity_keys_the_validator_did_not_supply():
    value = {"mention": "hypodermal cells", "curie": None, "name": "hypodermis",
             "proposed_curie": "ONT:9", "overruled_curie": "ONT:8",
             "resolution_state": UNRESOLVED, "lookup_outcome": OUTCOME_NOT_VALIDATED}
    mark_resolved(value, {"curie": "ONT:1"}, explanation="Matched by CURIE.", identity_keys=TERM_KEYS)
    assert (value["curie"], value["name"]) == ("ONT:1", None)
    assert "overruled_curie" not in value
    # The extractor's own proposal is a validator input and survives resolution.
    assert value["proposed_curie"] == "ONT:9"
    assert value["mention"] == "hypodermal cells"



def test_a_resolved_cell_never_shows_proposals_or_overruled_identities():
    from src.lib.flows.value_display import display_text

    value = {"abbreviation": "XP", "proposed_abbreviation": "Example Provider", "overruled_abbreviation": "YP",
             "mention": "Example Provider", "resolution_state": RESOLVED, "lookup_outcome": OUTCOME_MATCHED}
    assert display_text(value) == "abbreviation: XP"


def test_a_header_keeps_the_label_of_a_value_already_read_through_effective_payload():
    """A pre-applied legacy or invalid-record reading is not labelled "(paper wording)" again."""

    from src.lib.domain_packs.resolvable_values import unresolved_header_text

    spec = ResolvableSpec(id_key="curie", label_key="name")
    declared = {"site": spec}
    legacy = effective_payload({"site": {"curie": "ONT:9", "name": "old protein"}}, declared, object_metadata=None)
    assert legacy["site"]["lookup_outcome"] == OUTCOME_LEGACY_UNVERIFIED
    assert unresolved_header_text(legacy, "site.name", resolvable_fields=declared) == (
        f"old protein (ONT:9) {LEGACY_UNVERIFIED_SUFFIX}")

    broken = {"site": {"mention": "skin", "curie": "ONT:9", "name": "x", "resolution_state": UNRESOLVED,
                       "lookup_outcome": OUTCOME_NOT_FOUND, "validator_explanation": None}}
    invalid = effective_payload(broken, declared, object_metadata=None)
    header = unresolved_header_text(invalid, "site.name", resolvable_fields=declared)
    assert header == invalid["site"]["mention"]
    assert header.endswith("(invalid record, unverified)")

    # A stored unresolved value's paper wording is still labelled as such.
    stored = {"site": unresolved_value("skin", identity_keys=TERM_KEYS, outcome=OUTCOME_NOT_FOUND)}
    assert unresolved_header_text(stored, "site.name", resolvable_fields=declared) == "skin (paper wording)"


def test_an_override_settles_a_binding_whose_other_written_value_is_absent():
    """B3: writes into a declared value the payload does not hold do not keep a blocker open."""

    from src.lib.domain_packs.resolvable_values import apply_curator_identity

    expected = {"curie": "site.curie", "name": "site.name", "copy_curie": "copy.curie"}
    metadata = _metadata(expected=expected)
    site = unresolved_value("skin", identity_keys=TERM_KEYS)
    apply_curator_identity(site, {"curie": "ONT:9", "name": "skin"}, identity_keys=TERM_KEYS, id_key="curie",
                           label_key="name", actor_id="curator-1", actor_display_name="curator-1", at="2026-09-24T00:00:00+00:00")

    def codes(payload):
        envelope = _envelope(payload)
        result = materialize_validator_results_into_envelope(
            envelope, metadata, [_item(metadata, envelope, status="unresolved", outcome="not_found")])
        return {finding.code for finding in result.appended_findings}

    assert "domain_pack.validator_unresolved" not in codes({"site": dict(site)})
    assert "domain_pack.curator_override" in codes({"site": dict(site)})
    assert "domain_pack.validator_unresolved" in codes(
        {"site": dict(site), "copy": unresolved_value("skin", identity_keys=TERM_KEYS)})


def test_an_events_original_values_count_only_for_events_before_written_paths_were_recorded():
    """Contract S7: an event's original_values names paths it did not necessarily write."""

    current = {"validator_resolved_value_materialization": [
        {"materialized_field_paths": ["site.curie"], "original_values": {"copy.curie": "ONT:9"}},
    ]}
    assert validator_event_covers(current, "site")
    assert not validator_event_covers(current, "copy")
    # An event recorded before materialized_field_paths existed was written only for
    # the resolved values it wrote; its original_values name those.
    previous = {"validator_resolved_value_materialization": [{"original_values": {"copy.curie": "ONT:9"}}]}
    assert validator_event_covers(previous, "copy")
    spec = ResolvableSpec(id_key="curie", label_key="name")
    unwritten = effective_payload({"copy": {"curie": "ONT:9", "name": "old"}}, {"copy": spec}, object_metadata=current)
    assert unwritten["copy"]["lookup_outcome"] == OUTCOME_LEGACY_UNVERIFIED


def test_a_plain_mirror_follows_its_value_through_resolution_and_demotion():
    """Fix wave S3: a name kept beside its term never keeps a rejected term's name."""

    metadata = _metadata()
    definition = metadata.object_definitions[0]
    fields = [
        field.model_copy(update={"metadata": {**field.metadata, "materializes_to_field_paths": ["site_name"]}})
        if field.field_path == "site.name" else field
        for field in definition.fields
    ] + [DomainPackFieldDefinition(field_path="site_name", field_type=DomainPackFieldType.STRING)]
    metadata = metadata.model_copy(update={"object_definitions": [definition.model_copy(update={"fields": fields})]})
    envelope = _envelope({"site": unresolved_value("skin", identity_keys=TERM_KEYS), "site_name": None})

    resolved = materialize_validator_results_into_envelope(
        envelope, metadata, [_item(metadata, envelope, values={"curie": "ONT:1", "name": "epidermis"})],
    ).envelope
    assert resolved.extracted_objects[0].payload["site_name"] == "epidermis"

    outage = materialize_validator_results_into_envelope(
        resolved, metadata, [_item(metadata, resolved, status="unresolved", outcome="error")],
    ).envelope
    assert outage.extracted_objects[0].payload["site_name"] == "epidermis"

    demoted = materialize_validator_results_into_envelope(
        resolved, metadata, [_item(metadata, resolved, status="unresolved", outcome="not_found")],
    ).envelope.extracted_objects[0].payload
    assert (demoted["site"]["name"], demoted["site"]["overruled_name"]) == (None, "epidermis")
    assert demoted["site_name"] is None


def test_only_a_reading_counts_as_already_read():
    """Fix wave nit: a stored value claiming legacy_unverified while resolved is a broken record."""

    spec = ResolvableSpec(id_key="curie", label_key="name")
    claimed = {"mention": "x", "curie": "DOID:1", "name": "d", "resolution_state": RESOLVED,
               "lookup_outcome": OUTCOME_LEGACY_UNVERIFIED, "validator_explanation": None}
    assert effective_value(claimed, spec, covered_by_validator=False)["lookup_outcome"] == "invalid_schema"
    reading = effective_payload({"site": {"curie": "DOID:1", "name": "d"}}, {"site": spec}, object_metadata=None)["site"]
    assert effective_value(reading, spec, covered_by_validator=False) == reading


def test_a_numeric_identity_entry_is_parsed_as_its_declared_type():
    from src.lib.domain_packs.resolvable_values import typed_identity_input

    assert typed_identity_input(" 12 ", value_type="integer", label="Relation internal ID") == 12
    assert typed_identity_input(12, value_type="integer", label="x") == 12
    assert typed_identity_input("2.5", value_type="number", label="x") == 2.5
    assert typed_identity_input("", value_type="integer", label="x") is None
    assert typed_identity_input("ONT:1", value_type="string", label="x") == "ONT:1"
    for value, value_type, message in (
        ("12a", "integer", "Enter a whole number for the relation internal ID."),
        ("2.5", "integer", "Enter a whole number for the relation internal ID."),
        (True, "integer", "Enter a whole number for the relation internal ID."),
        ("nan", "number", "Enter a number for the relation internal ID."),
    ):
        with pytest.raises(ResolvableValueError, match=f"^{message}$"):
            typed_identity_input(value, value_type=value_type, label="Relation internal ID")


# --- Extraction never searches (2026-09-24) -------------------------------------------


def test_extraction_stages_every_declared_value_unvalidated():
    """Extraction reads the paper: a declared value it stages is unresolved/not_validated,
    except one its pack declares filled from a fixed in-code mapping table."""

    from src.lib.domain_packs.resolvable_values import EXTRACTION_MAPPING_KEY, extraction_value_problems

    metadata = _metadata()
    staged = {"site": unresolved_value("skin", identity_keys=TERM_KEYS, proposed_curie="ONT:7")}
    assert extraction_value_problems(staged, metadata, "Observation", stored=False) == []

    searched = {"site": resolved_value("skin", {"curie": "ONT:1", "name": "epidermis"})}
    [problem] = extraction_value_problems(searched, metadata, "Observation", stored=False)
    assert problem.startswith("Observation.site was staged resolved/matched")
    claimed = {"site": unresolved_value("skin", identity_keys=TERM_KEYS, outcome=OUTCOME_NOT_FOUND)}
    assert extraction_value_problems(claimed, metadata, "Observation", stored=False)
    # A value stored before the contract is left to the legacy rule; fresh output
    # records the state on every value (core review S2).
    stateless = {"site": {"curie": "ONT:1", "name": "x"}}
    assert extraction_value_problems(stateless, metadata, "Observation", stored=True) == []
    [problem] = extraction_value_problems(stateless, metadata, "Observation", stored=False)
    assert problem.startswith("Observation.site records no resolution state")
    for wording_only in ({"site": {"mention": "skin"}}, {"site": "skin"}):
        assert extraction_value_problems(wording_only, metadata, "Observation", stored=True) == []
        assert extraction_value_problems(wording_only, metadata, "Observation", stored=False)
    # A value the paper never mentions is absent, not stateless.
    assert extraction_value_problems({"site": None}, metadata, "Observation", stored=False) == []

    definition = metadata.object_definitions[0]
    mapped = metadata.model_copy(update={"object_definitions": [definition.model_copy(update={"fields": [
        field.model_copy(update={"metadata": {**field.metadata, EXTRACTION_MAPPING_KEY: True}})
        if field.field_path == "site" else field
        for field in definition.fields
    ]})]})
    assert extraction_value_problems(searched, mapped, "Observation", stored=False) == []


@pytest.mark.parametrize(("outcome", "mapped_ok", "unmapped_ok"), [
    (OUTCOME_NOT_VALIDATED, True, True),
    # The fixed table's own outcomes: a miss, or an entry that does not apply here.
    (OUTCOME_NOT_FOUND, True, False),
    ("conflict", True, False),
    # What no table decides.
    ("ambiguous", False, False),
    ("transient", False, False),
    ("blocked", False, False),
    ("rejected_candidates", False, False),
])
def test_a_fixed_mapping_field_takes_the_tables_own_outcomes(outcome, mapped_ok, unmapped_ok):
    from src.lib.domain_packs.resolvable_values import EXTRACTION_MAPPING_KEY, extraction_value_problems

    metadata = _metadata()
    definition = metadata.object_definitions[0]
    mapped = metadata.model_copy(update={"object_definitions": [definition.model_copy(update={"fields": [
        field.model_copy(update={"metadata": {**field.metadata, EXTRACTION_MAPPING_KEY: True}})
        if field.field_path == "site" else field
        for field in definition.fields
    ]})]})
    value = {"site": unresolved_value("TAS", identity_keys=TERM_KEYS, outcome=outcome)}
    assert (extraction_value_problems(value, mapped, "Observation", stored=False) == []) is mapped_ok
    assert (extraction_value_problems(value, metadata, "Observation", stored=False) == []) is unmapped_ok
    # A resolved value is matched, and only on a mapping field.
    overridden = {"site": {**resolved_value("IMP", {"curie": "ECO:1", "name": "x"}),
                           "lookup_outcome": "curator_override",
                           "curator_override": {"actor_id": "a", "actor_display_name": "A", "at": "t", "previous": {}}}}
    assert extraction_value_problems(overridden, mapped, "Observation", stored=False)
