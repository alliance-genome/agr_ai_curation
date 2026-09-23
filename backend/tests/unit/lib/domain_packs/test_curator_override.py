"""A curator's validation override of a resolvable value (ALL-1283)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.lib.domain_envelopes.patches import (
    EnvelopeFieldPatch,
    EnvelopeFieldPatchOperation,
    EnvelopeFieldPatchStatus,
    apply_curator_field_patch,
)
from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.materialization import (
    ValidatorResultMaterializationInput,
    materialize_validator_results_into_envelope,
)
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.resolvable_values import (
    CURATOR_OVERRIDE_METADATA_KEY,
    LOOKUP_OUTCOME_LABELS,
    LOOKUP_OUTCOMES,
    OUTCOME_CURATOR_OVERRIDE,
    OUTCOME_MATCHED,
    OUTCOME_NOT_FOUND,
    OUTCOME_NOT_VALIDATED,
    RESOLVED,
    UNRESOLVED,
    ResolvableSpec,
    ResolvableValueError,
    apply_curator_identity,
    check_resolvable_value,
    effective_value,
    is_curator_override,
    mark_resolved,
    mark_unresolved,
    resolved_value,
    unresolved_value,
)
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.lib.flows.value_display import display_text
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope
from src.schemas.domain_pack_metadata import (
    DomainPackEnumDefinition,
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackMetadata,
    DomainPackObjectDefinition,
)
from src.schemas.domain_validator import DomainValidatorResultBase, ValidatorFieldResolution


KEYS = ("curie", "name")
DISPLAY = {"label": "name", "id": "curie", "mention": "mention"}
AT = "2026-09-23T20:00:00+00:00"


def _override(value, **edits):
    return apply_curator_identity(value, edits, identity_keys=KEYS, id_key="curie", label_key="name",
                                  actor_id="curator-7", at=AT)


# --- The vocabulary ----------------------------------------------------------------


def test_curator_override_is_the_last_lookup_outcome_and_a_resolved_one():
    assert LOOKUP_OUTCOMES[-1] == OUTCOME_CURATOR_OVERRIDE == "curator_override"
    assert LOOKUP_OUTCOME_LABELS[OUTCOME_CURATOR_OVERRIDE] == "Curator override"
    value = unresolved_value("skin", identity_keys=KEYS)
    _override(value, curie="ONT:1", name="epidermis")
    check_resolvable_value(value, identity_keys=KEYS)
    # Only a curator records an override: validators can neither claim it nor lose it.
    with pytest.raises(ResolvableValueError, match="records who"):
        check_resolvable_value({**value, "curator_override": None}, identity_keys=KEYS)
    with pytest.raises(ResolvableValueError):
        check_resolvable_value({**value, "lookup_outcome": OUTCOME_MATCHED}, identity_keys=KEYS)
    with pytest.raises(ValidationError):
        ValidatorFieldResolution.model_validate({"status": "resolved", "lookup_outcome": "curator_override"})
    with pytest.raises(ValidationError):
        ValidatorFieldResolution.model_validate({"status": "unresolved", "lookup_outcome": "curator_override"})


# --- Applying, clearing and restoring --------------------------------------------------


def test_an_unresolved_value_is_overridden():
    value = unresolved_value("skin", identity_keys=KEYS)
    audit = _override(value, curie="ONT:1", name="epidermis")

    assert (value["resolution_state"], value["lookup_outcome"]) == (RESOLVED, OUTCOME_CURATOR_OVERRIDE)
    assert (value["curie"], value["name"], value["mention"]) == ("ONT:1", "epidermis", "skin")
    assert value["validator_explanation"] == "Not validated yet."
    assert value["curator_override"]["actor_id"] == "curator-7"
    assert value["curator_override"]["at"] == AT
    assert audit["action"] == "override"
    assert display_text(value, DISPLAY) == "epidermis (ONT:1)"
    assert effective_value(value, ResolvableSpec(id_key="curie", label_key="name"),
                           covered_by_validator=False) is value


def test_a_resolved_value_is_overridden_and_its_validator_identity_set_aside():
    value = resolved_value("skin", {"curie": "ONT:1", "name": "epidermis"}, explanation="Matched.",
                           proposed_curie="ONT:9")
    _override(value, curie="ONT:2", name="dermis")

    assert (value["curie"], value["name"]) == ("ONT:2", "dermis")
    assert (value["overruled_curie"], value["overruled_name"]) == ("ONT:1", "epidermis")
    assert value["proposed_curie"] == "ONT:9"
    assert value["validator_explanation"] == "Matched."
    # A second edit refines the curator's own identity; nothing more is set aside.
    _override(value, name="skin cell")
    assert (value["curie"], value["name"], value["overruled_curie"]) == ("ONT:2", "skin cell", "ONT:1")


def test_clearing_the_identity_withdraws_the_override():
    value = unresolved_value("skin", identity_keys=KEYS, outcome=OUTCOME_NOT_FOUND, explanation="No match.")
    _override(value, curie="ONT:1", name="epidermis")
    audit = _override(value, curie=None, name="")

    assert (value["resolution_state"], value["lookup_outcome"]) == (UNRESOLVED, OUTCOME_NOT_FOUND)
    assert (value["curie"], value["name"]) == (None, None)
    assert "curator_override" not in value
    assert audit["action"] == "cleared"
    fresh = unresolved_value("skin", identity_keys=KEYS)
    _override(fresh, curie="ONT:1", name="epidermis")
    _override(fresh, curie=None, name=None)
    assert fresh["lookup_outcome"] == OUTCOME_NOT_VALIDATED


def test_an_override_fills_both_the_identifier_and_the_name():
    """L1: a partial override would leave a resolved value without its label or id."""

    validated = resolved_value("skin", {"curie": "ONT:1", "name": "epidermis"}, explanation="Matched.")
    staged = unresolved_value("skin", identity_keys=KEYS)
    for value, edits in ((validated, {"curie": "ONT:2"}), (staged, {"name": "epidermis"}),
                         (staged, {"curie": "ONT:2", "name": " "})):
        snapshot = dict(value)
        with pytest.raises(ResolvableValueError, match="^Enter both the identifier and the name for a curator override.$"):
            _override(value, **edits)
        assert value == snapshot
    # Once overridden, one key can be refined, but not emptied.
    _override(staged, curie="ONT:2", name="epidermis")
    with pytest.raises(ResolvableValueError, match="Enter both"):
        _override(staged, name=None)
    assert staged["name"] == "epidermis"


def test_validated_keys_stay_optional_in_an_override():
    value = unresolved_value("skin", identity_keys=(*KEYS, "taxon"))
    apply_curator_identity(value, {"curie": "ONT:1", "name": "epidermis"}, identity_keys=(*KEYS, "taxon"),
                           id_key="curie", label_key="name", actor_id="curator-7", at=AT)
    assert (value["resolution_state"], value["taxon"]) == (RESOLVED, None)
    # A validated key alone is no override: the declared label stays required.
    only_label = unresolved_value("skin", identity_keys=("name", "taxon"))
    with pytest.raises(ResolvableValueError, match="^Enter the name for a curator override.$"):
        apply_curator_identity(only_label, {"taxon": "T:1"}, identity_keys=("name", "taxon"), id_key=None,
                               label_key="name", actor_id="curator-7", at=AT)
    with pytest.raises(ResolvableValueError, match="declared id or label key"):
        apply_curator_identity(value, {"curie": "ONT:2"}, identity_keys=KEYS, id_key=None, label_key=None,
                               actor_id="curator-7", at=AT)


def test_entering_the_previous_identity_restores_the_previous_state():
    value = resolved_value("skin", {"curie": "ONT:1", "name": "epidermis"}, explanation="Matched.")
    original = dict(value)
    _override(value, curie="ONT:2", name="other")
    audit = _override(value, curie="ONT:1", name="epidermis")

    assert value == original
    assert audit["action"] == "restored"


def test_only_identity_keys_take_an_override():
    value = unresolved_value("skin", identity_keys=KEYS)
    with pytest.raises(ResolvableValueError, match="Only identity keys"):
        _override(value, mention="new words")


# --- Later validator runs ---------------------------------------------------------------


def test_validator_writes_never_change_an_overridden_value():
    value = unresolved_value("skin", identity_keys=KEYS)
    _override(value, curie="ONT:1", name="epidermis")
    snapshot = dict(value)
    mark_resolved(value, {"curie": "ONT:9", "name": "other"}, explanation="x", identity_keys=KEYS)
    mark_unresolved(value, OUTCOME_NOT_FOUND, explanation="x", identity_keys=KEYS)
    assert value == snapshot
    assert is_curator_override(value)


def _metadata(display=DISPLAY, name_editable=True) -> DomainPackMetadata:
    return DomainPackMetadata(
        pack_id="fixture.override",
        display_name="Fixture Override",
        version="0.1.0",
        metadata_api_version="1.0.0",
        enum_definitions=[DomainPackEnumDefinition(enum_id="LookupOutcome", display_name="Lookup outcome",
                                                   values=[{"value": value} for value in LOOKUP_OUTCOMES])],
        metadata={"validator_bindings": {"active": [{
            "binding_id": "fixture.site_lookup",
            "display_name": "Site lookup",
            "validator_agent": {"package_id": "fixture.validators", "agent_id": "term_validator"},
            "applies_to": {"domain_pack_id": "fixture.override", "object_types": ["Observation"]},
            "input_fields": {"mention": {"source": "payload", "path": "site.mention"}},
            "expected_result_fields": {"curie": "site.curie", "name": "site.name"},
        }], "under_development": []}},
        object_definitions=[DomainPackObjectDefinition(
            object_type="Observation", display_name="Observation", metadata={"object_role": "curatable_unit"},
            fields=[
                DomainPackFieldDefinition(field_path="site", field_type=DomainPackFieldType.OBJECT,
                                          metadata={"display": display}),
                DomainPackFieldDefinition(field_path="site.curie", field_type=DomainPackFieldType.STRING,
                                          metadata={"editable": True}),
                DomainPackFieldDefinition(field_path="site.name", field_type=DomainPackFieldType.STRING,
                                          metadata={"editable": name_editable}),
                DomainPackFieldDefinition(field_path="site.mention", field_type=DomainPackFieldType.STRING,
                                          metadata={"editable": True}),
                DomainPackFieldDefinition(field_path="site.taxon", field_type=DomainPackFieldType.STRING,
                                          metadata={"editable": True}),
                DomainPackFieldDefinition(field_path="site.lookup_outcome", field_type=DomainPackFieldType.ENUM,
                                          enum_ref="LookupOutcome", metadata={"editable": True}),
            ],
        )],
    )


def _pack(display=DISPLAY, name_editable=True) -> LoadedDomainPack:
    metadata = _metadata(display, name_editable)
    return LoadedDomainPack(
        pack_id=metadata.pack_id, display_name=metadata.display_name, version=metadata.version,
        pack_path=Path("."), metadata_path=Path("."), metadata=metadata,
    )


def _overridden_envelope() -> DomainEnvelope:
    site = unresolved_value("skin", identity_keys=KEYS)
    _override(site, curie="ONT:1", name="epidermis")
    return DomainEnvelope(
        envelope_id="override-env", domain_pack_id="fixture.override",
        extracted_objects=[CuratableObjectEnvelope(object_type="Observation", object_id="obs-1",
                                                   payload={"site": site})],
    )


def _validate(envelope, *, status, values=None, outcome="success"):
    metadata = _metadata()
    registry = DomainPackValidationRegistry.from_domain_pack(_pack())
    match = registry.match_bindings(envelope, states=[ValidationBindingState.ACTIVE])[0]
    request = build_domain_validation_request(match).request
    result = DomainValidatorResultBase.model_validate({
        "status": status, "request_id": request.request_id,
        "validator_binding_id": request.validator_binding_id, "validator_agent": request.validator_agent,
        "target": request.target, "resolved_values": values or {}, "resolved_objects": [],
        "missing_expected_fields": [], "candidates": [],
        "lookup_attempts": [{"provider": "f", "method": "m", "query": {}, "result_count": 1, "outcome": outcome}],
        "curator_message": None, "explanation": "Validator words.",
    })
    return materialize_validator_results_into_envelope(
        envelope, metadata, [ValidatorResultMaterializationInput(match=match, request=request, result=result)],
    )


def _disagreements(result):
    return [finding for finding in result.appended_findings
            if finding.code == "domain_pack.validator_disagrees_with_curator_override"]


def test_an_agreeing_validator_leaves_the_override_and_opens_nothing():
    envelope = _overridden_envelope()
    result = _validate(envelope, status="resolved", values={"curie": "ONT:1", "name": "epidermis"})

    assert result.envelope.extracted_objects[0].payload == envelope.extracted_objects[0].payload
    assert _disagreements(result) == []
    assert all(finding.status.value == "resolved" for finding in result.appended_findings)


@pytest.mark.parametrize(("status", "values", "outcome", "detail"), [
    ("resolved", {"curie": "ONT:9", "name": "other"}, "success", "it resolved curie 'ONT:9'"),
    ("unresolved", None, "not_found", "its lookup result is Not found"),
])
def test_a_disagreeing_validator_opens_a_finding_and_leaves_the_override(status, values, outcome, detail):
    envelope = _overridden_envelope()
    result = _validate(envelope, status=status, values=values, outcome=outcome)

    assert result.envelope.extracted_objects[0].payload == envelope.extracted_objects[0].payload
    [finding] = _disagreements(result)
    assert finding.status.value == "open"
    assert finding.message.startswith("Validator disagrees with the curator override:")
    assert detail in finding.message
    assert finding.field_ref.field_path == "site"
    # The validator's own outcome is not an open problem: the override wins.
    others = [f for f in result.appended_findings if f is not finding]
    assert all(f.status.value == "resolved" for f in others)


def test_a_non_decisive_validator_outcome_is_no_disagreement():
    envelope = _overridden_envelope()
    result = _validate(envelope, status="unresolved", outcome="error")

    assert result.envelope.extracted_objects[0].payload == envelope.extracted_objects[0].payload
    assert _disagreements(result) == []


# --- The curator edit path ----------------------------------------------------------------


def _patch(envelope, field_path, value, *, before, display=DISPLAY, name_editable=True,
           operation=EnvelopeFieldPatchOperation.REPLACE, pack=None):
    return apply_curator_field_patch(
        envelope, pack or _pack(display, name_editable),
        EnvelopeFieldPatch(envelope_id=envelope.envelope_id, expected_revision=1, object_id="obs-1",
                           field_path=field_path, before=before, value=value, operation=operation),
        current_revision=1, actor_id="curator-7",
    )


def _staged_envelope():
    return DomainEnvelope(
        envelope_id="override-env", domain_pack_id="fixture.override",
        extracted_objects=[CuratableObjectEnvelope(object_type="Observation", object_id="obs-1",
                                                   payload={"site": unresolved_value("skin", identity_keys=KEYS)})],
    )


def test_a_curator_edit_of_an_identity_key_is_an_override_with_an_audit_event():
    staged = _staged_envelope()
    before = staged.extracted_objects[0].payload["site"]
    result = _patch(staged, "site", {**before, "curie": "ONT:1", "name": "epidermis"}, before=before)

    assert result.status is EnvelopeFieldPatchStatus.ACCEPTED
    obj = result.envelope.extracted_objects[0]
    site = obj.payload["site"]
    assert (site["curie"], site["name"], site["lookup_outcome"], site["curator_override"]["actor_id"]) == (
        "ONT:1", "epidermis", OUTCOME_CURATOR_OVERRIDE, "curator-7")
    [event] = obj.metadata[CURATOR_OVERRIDE_METADATA_KEY]
    assert (event["action"], event["value_path"], event["field_path"]) == ("override", "site", "site")
    assert event["previous"]["lookup_outcome"] == OUTCOME_NOT_VALIDATED

    # One key of an override can be refined on its own.
    refined = _patch(result.envelope, "site.name", "skin epidermis", before="epidermis")
    assert refined.status is EnvelopeFieldPatchStatus.ACCEPTED
    site = refined.envelope.extracted_objects[0].payload["site"]
    assert (site["curie"], site["name"]) == ("ONT:1", "skin epidermis")

    cleared = _patch(refined.envelope, "site", {**site, "curie": None, "name": None}, before=site)
    site = cleared.envelope.extracted_objects[0].payload["site"]
    assert (site["resolution_state"], site["lookup_outcome"]) == (UNRESOLVED, OUTCOME_NOT_VALIDATED)


def test_a_curator_override_of_only_one_leaf_is_rejected_with_a_clear_message():
    """L1: an override never leaves a resolved value without its identifier or name."""

    staged = _staged_envelope()
    result = _patch(staged, "site.curie", "ONT:1", before=None)
    assert result.status is EnvelopeFieldPatchStatus.REJECTED
    assert result.errors == ("Enter both the identifier and the name for a curator override.",)
    assert result.envelope.extracted_objects[0].payload == staged.extracted_objects[0].payload

    overridden = _overridden_envelope()
    site = overridden.extracted_objects[0].payload["site"]
    for patch_args in (("site.name", None, "epidermis"), ("site", {**site, "curie": ""}, site)):
        rejected = _patch(overridden, patch_args[0], patch_args[1], before=patch_args[2])
        assert rejected.errors == ("Enter both the identifier and the name for a curator override.",)
        assert rejected.envelope.extracted_objects[0].payload["site"] == site


def test_a_whole_value_edit_cannot_change_the_extractor_proposal():
    """L2: proposed_* is the extractor's, even through a whole-value edit."""

    staged = _staged_envelope()
    staged.extracted_objects[0].payload["site"]["proposed_curie"] = "ONT:5"
    before = staged.extracted_objects[0].payload["site"]

    rejected = _patch(staged, "site", {**before, "curie": "ONT:1", "name": "epidermis", "proposed_curie": "ONT:1"},
                      before=before)
    assert rejected.status is EnvelopeFieldPatchStatus.REJECTED
    assert "cannot change proposed_curie" in rejected.errors[0]

    accepted = _patch(staged, "site", {**before, "curie": "ONT:1", "name": "epidermis"}, before=before)
    assert accepted.status is EnvelopeFieldPatchStatus.ACCEPTED
    assert accepted.envelope.extracted_objects[0].payload["site"]["proposed_curie"] == "ONT:5"


@pytest.mark.parametrize(("field_path", "value", "before"), [
    ("site.lookup_outcome", "matched", "not_validated"),
    ("site.mention", "other words", "skin"),
])
def test_curators_cannot_edit_the_paper_wording_or_the_validation_state(field_path, value, before):
    result = _patch(_staged_envelope(), field_path, value, before=before)

    assert result.status is EnvelopeFieldPatchStatus.REJECTED
    assert "set by extraction or validation" in result.errors[0]


def test_a_whole_value_override_changes_only_the_identity():
    """A container editable for whole-value overrides keeps its other keys closed."""

    staged = _staged_envelope()
    staged.extracted_objects[0].payload["site"]["entity_type"] = "tissue"
    before = staged.extracted_objects[0].payload["site"]
    identity = {"curie": "ONT:1", "name": "epidermis"}

    for other in ({"entity_type": "cell"}, {"mention": "other words"}, {"lookup_outcome": "matched"},
                  {"overruled_curie": "ONT:3"}, {"added_key": "x"}):
        rejected = _patch(staged, "site", {**before, **identity, **other}, before=before)
        assert rejected.status is EnvelopeFieldPatchStatus.REJECTED
        assert rejected.errors == (
            f"field_path 'site' cannot change {next(iter(other))}; "
            "only the identifier and name can be changed in a curator override",)
        assert rejected.envelope.extracted_objects[0].payload["site"] == before

    accepted = _patch(staged, "site", {**before, **identity}, before=before)
    site = accepted.envelope.extracted_objects[0].payload["site"]
    assert (site["curie"], site["name"], site["entity_type"]) == ("ONT:1", "epidermis", "tissue")
    # A declared validated key is part of the identity.
    with_taxon = _patch(staged, "site", {**before, **identity, "taxon": "T:1"}, before=before,
                        display={**DISPLAY, "validated": ["taxon"]})
    assert with_taxon.status is EnvelopeFieldPatchStatus.ACCEPTED
    assert with_taxon.envelope.extracted_objects[0].payload["site"]["taxon"] == "T:1"


# --- Whole-value overrides: identity fields editable, one atomic edit -------------------


IDENTITY = EnvelopeFieldPatchOperation.REPLACE_IDENTITY


def test_a_whole_value_override_needs_every_identity_field_editable():
    staged = _staged_envelope()
    before = staged.extracted_objects[0].payload["site"]

    closed = _patch(staged, "site", {**before, "curie": "ONT:1", "name": "epidermis"}, before=before,
                    name_editable=False)
    assert closed.status is EnvelopeFieldPatchStatus.REJECTED
    assert closed.errors == ("field_path 'site' takes no curator override: site.name not declared editable",)
    # The container itself needs no editable flag.
    opened = _patch(staged, "site", {**before, "curie": "ONT:1", "name": "epidermis"}, before=before)
    assert opened.status is EnvelopeFieldPatchStatus.ACCEPTED


def test_a_field_value_identity_patch_overrides_both_keys_atomically():
    staged = _staged_envelope()

    result = _patch(staged, "site.curie", {"curie": "ONT:1", "name": "epidermis"},
                    before={"curie": None, "name": None}, operation=IDENTITY)

    assert result.status is EnvelopeFieldPatchStatus.ACCEPTED
    obj = result.envelope.extracted_objects[0]
    assert (obj.payload["site"]["curie"], obj.payload["site"]["name"], obj.payload["site"]["lookup_outcome"]) == (
        "ONT:1", "epidermis", OUTCOME_CURATOR_OVERRIDE)
    [event] = obj.metadata[CURATOR_OVERRIDE_METADATA_KEY]
    assert (event["value_path"], event["field_path"]) == ("site", "site.curie")

    stale = _patch(staged, "site.curie", {"curie": "ONT:1", "name": "epidermis"},
                   before={"curie": "ONT:0", "name": None}, operation=IDENTITY)
    assert stale.status is EnvelopeFieldPatchStatus.REJECTED
    assert "before does not match" in stale.errors[0]
    for bad_path in ("site.mention", "site"):
        wrong = _patch(staged, bad_path, {"curie": "ONT:1", "name": "epidermis"},
                       before={"curie": None, "name": None}, operation=IDENTITY)
        assert wrong.errors == (
            f"field_path '{bad_path}' is not an identity field of a declared resolvable value",)


def _root_pack() -> LoadedDomainPack:
    from src.schemas.domain_pack_metadata import DomainPackModelDefinition

    metadata = DomainPackMetadata(
        pack_id="fixture.root_override", display_name="Root override", version="0.1.0",
        metadata_api_version="1.0.0",
        enum_definitions=[DomainPackEnumDefinition(enum_id="LookupOutcome", display_name="Lookup outcome",
                                                   values=[{"value": value} for value in LOOKUP_OUTCOMES])],
        model_definitions=[DomainPackModelDefinition(
            model_id="MentionPayload", display_name="Mention payload",
            metadata={"display": {"label": "symbol", "id": "curie", "mention": "mention"}},
        )],
        object_definitions=[DomainPackObjectDefinition(
            object_type="Mention", display_name="Mention", model_ref="MentionPayload",
            metadata={"object_role": "curatable_unit"},
            fields=[
                DomainPackFieldDefinition(field_path="symbol", field_type=DomainPackFieldType.STRING,
                                          metadata={"editable": True}),
                DomainPackFieldDefinition(field_path="curie", field_type=DomainPackFieldType.STRING,
                                          metadata={"editable": True}),
                DomainPackFieldDefinition(field_path="mention", field_type=DomainPackFieldType.STRING),
                DomainPackFieldDefinition(field_path="entity_type", field_type=DomainPackFieldType.STRING,
                                          metadata={"editable": True}),
                DomainPackFieldDefinition(field_path="lookup_outcome", field_type=DomainPackFieldType.ENUM,
                                          enum_ref="LookupOutcome"),
            ],
        )],
    )
    return LoadedDomainPack(pack_id=metadata.pack_id, display_name=metadata.display_name, version=metadata.version,
                            pack_path=Path("."), metadata_path=Path("."), metadata=metadata)


def _root_envelope() -> DomainEnvelope:
    payload = {**unresolved_value("gene-54", identity_keys=("curie", "symbol")), "entity_type": "gene"}
    return DomainEnvelope(
        envelope_id="root-env", domain_pack_id="fixture.root_override",
        extracted_objects=[CuratableObjectEnvelope(object_type="Mention", object_id="obs-1", payload=payload)],
    )


def test_an_object_root_value_is_overridden_in_one_identity_patch():
    envelope = _root_envelope()

    result = _patch(envelope, "curie", {"curie": "G:54", "symbol": "gene-54"},
                    before={"curie": None, "symbol": None}, operation=IDENTITY, pack=_root_pack())

    assert result.status is EnvelopeFieldPatchStatus.ACCEPTED
    obj = result.envelope.extracted_objects[0]
    assert (obj.payload["curie"], obj.payload["symbol"], obj.payload["lookup_outcome"]) == (
        "G:54", "gene-54", OUTCOME_CURATOR_OVERRIDE)
    assert (obj.payload["mention"], obj.payload["entity_type"]) == ("gene-54", "gene")
    [event] = obj.metadata[CURATOR_OVERRIDE_METADATA_KEY]
    assert (event["action"], event["value_path"], event["field_path"]) == ("override", "", "curie")


def test_an_object_root_override_rejects_other_keys_and_a_single_first_leaf():
    envelope = _root_envelope()
    pack = _root_pack()
    payload = envelope.extracted_objects[0].payload

    other = _patch(envelope, "curie", {"curie": "G:54", "symbol": "gene-54", "entity_type": "protein"},
                   before={"curie": None, "symbol": None, "entity_type": "gene"}, operation=IDENTITY, pack=pack)
    assert other.errors == (
        "field_path 'curie' cannot change entity_type; only the identifier and name can be changed "
        "in a curator override",)
    for patch_args in (("curie", "G:54", None, EnvelopeFieldPatchOperation.REPLACE),
                       ("symbol", {"symbol": "gene-54"}, {"symbol": None}, IDENTITY)):
        field_path, value, before, operation = patch_args
        single = _patch(envelope, field_path, value, before=before, operation=operation, pack=pack)
        assert single.status is EnvelopeFieldPatchStatus.REJECTED
        assert single.errors == ("Enter both the identifier and the name for a curator override.",)
        assert single.envelope.extracted_objects[0].payload == payload


def test_draft_edits_of_one_value_identity_become_one_patch_step():
    from types import SimpleNamespace

    from src.lib.curation_workspace.session_mutation_service import _draft_patch_steps

    def draft(key, path):
        return SimpleNamespace(field_key=key, metadata={"source_field_path": path})

    fields = {"a": draft("a", "curie"), "b": draft("b", "entity_type"), "c": draft("c", "symbol")}
    steps = _draft_patch_steps(["a", "b", "c"], fields, domain_pack=_root_pack(), object_type="Mention",
                               profile=None)
    assert steps == [[("a", "curie"), ("c", "symbol")], [("b", None)]]


def _list_pack() -> LoadedDomainPack:
    metadata = _metadata()
    fields = [
        *metadata.object_definitions[0].fields,
        DomainPackFieldDefinition(field_path="sites", field_type=DomainPackFieldType.ARRAY,
                                  metadata={"display": DISPLAY, "multivalued": True}),
        DomainPackFieldDefinition(field_path="sites.curie", field_type=DomainPackFieldType.STRING,
                                  metadata={"editable": True}),
        DomainPackFieldDefinition(field_path="sites.name", field_type=DomainPackFieldType.STRING,
                                  metadata={"editable": True}),
    ]
    definition = metadata.object_definitions[0].model_copy(update={"fields": fields})
    metadata = metadata.model_copy(update={"object_definitions": [definition]})
    return LoadedDomainPack(
        pack_id=metadata.pack_id, display_name=metadata.display_name, version=metadata.version,
        pack_path=Path("."), metadata_path=Path("."), metadata=metadata,
    )


def test_a_list_element_identity_takes_its_bare_declaration():
    """ALL-1283: ``sites[1].curie`` is edited under the declaration of ``sites.curie``; an index
    through a field that is not multivalued is not a declared path."""

    envelope = DomainEnvelope(
        envelope_id="override-env", domain_pack_id="fixture.override",
        extracted_objects=[CuratableObjectEnvelope(object_type="Observation", object_id="obs-1", payload={
            "site": unresolved_value("skin", identity_keys=KEYS),
            "sites": [unresolved_value("skin", identity_keys=KEYS), unresolved_value("gut", identity_keys=KEYS)],
        })],
    )

    def patch(field_path):
        return _patch(envelope, field_path, {"curie": "ONT:2", "name": "gut"},
                      before={"curie": None, "name": None}, operation=IDENTITY, pack=_list_pack())

    element = patch("sites[1].curie")
    assert element.status is EnvelopeFieldPatchStatus.ACCEPTED, element.errors
    edited = element.envelope.extracted_objects[0].payload["sites"][1]
    assert (edited["curie"], edited["name"], edited["lookup_outcome"]) == ("ONT:2", "gut", OUTCOME_CURATOR_OVERRIDE)

    not_a_list = patch("site[0].curie")
    assert not_a_list.status is EnvelopeFieldPatchStatus.REJECTED
    assert not_a_list.errors == (
        "field_path 'site[0].curie' takes no curator override: site[0].curie, site[0].name not declared editable",)
