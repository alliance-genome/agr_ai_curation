"""Review rows show each validated value as extracted and as validated (ALL-1283)."""

from __future__ import annotations

import pytest

from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
from src.lib.domain_packs.resolvable_values import (
    LEGACY_EXPLANATION,
    LEGACY_UNVERIFIED_SUFFIX,
    NOT_VALIDATED_EXPLANATION,
    UNRESOLVED_DISPLAY,
    VALIDATOR_MATERIALIZATION_METADATA_KEY,
    apply_curator_identity,
    mark_unresolved,
    resolved_value,
    unresolved_value,
)
from src.schemas.curation_workspace import (
    DomainEnvelopeReviewResolvedValue,
    DomainEnvelopeReviewRowSummaryField,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    DomainEnvelopeStatus,
    FieldRef,
    ObjectRef,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
)
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackMetadata,
    DomainPackModelDefinition,
    DomainPackObjectDefinition,
)


TERM_DISPLAY = {"label": "name", "id": "curie", "mention": "mention"}
TERM_KEYS = ("curie", "name")


def _field(path: str, field_type: DomainPackFieldType = DomainPackFieldType.STRING, **kwargs):
    return DomainPackFieldDefinition(field_path=path, field_type=field_type, **kwargs)


def _metadata(*, site_protected: bool = False) -> DomainPackMetadata:
    return DomainPackMetadata(
        pack_id="fixture.resolution",
        display_name="Resolution fixture",
        version="0.1.0",
        metadata_api_version="1.0.0",
        model_definitions=[
            DomainPackModelDefinition(
                model_id="SubjectMention",
                display_name="Subject mention",
                metadata={"display": {
                    "label": "symbol", "id": "identifier", "mention": "mention", "validated": ["taxon"],
                }},
            )
        ],
        object_definitions=[
            DomainPackObjectDefinition(
                object_type="Observation",
                display_name="Observation",
                metadata={
                    "object_role": "curatable_unit",
                    "workspace_display": {
                        "primary_label_field": "site.name",
                        "summary_fields": ["site", "note", "attributes.gene"],
                        "groups": [
                            {
                                "id": "site",
                                "label": "Site",
                                "fields": [
                                    "site.curie",
                                    "site.name",
                                    "site.proposed_curie",
                                    "site.mention",
                                    "site.resolution_state",
                                    "site.lookup_outcome",
                                    "site.validator_explanation",
                                    "codes",
                                ],
                            },
                            {"id": "context", "label": "Context", "fields": ["conditions", "note"]},
                        ],
                    },
                },
                fields=[
                    _field(
                        "site",
                        DomainPackFieldType.OBJECT,
                        metadata={"display": TERM_DISPLAY, **({"protected": True} if site_protected else {})},
                    ),
                    # A curator edits a value's identity (a validation override).
                    _field("site.curie", metadata={"editable": True}),
                    _field("site.name", metadata={"editable": True}),
                    _field("site.mention"),
                    _field(
                        "codes",
                        DomainPackFieldType.OBJECT,
                        metadata={"multivalued": True, "display": {"id": "curie", "mention": "mention"}},
                    ),
                    _field("conditions", DomainPackFieldType.OBJECT, metadata={"multivalued": True}),
                    _field(
                        "conditions.component",
                        DomainPackFieldType.OBJECT,
                        metadata={"display": TERM_DISPLAY},
                    ),
                    _field("note"),
                    _field("attributes.gene", DomainPackFieldType.OBJECT, metadata={"display": TERM_DISPLAY}),
                    _field("attributes.gene.curie", metadata={"editable": True}),
                    _field("attributes.gene.name", metadata={"editable": True}),
                ],
            ),
            DomainPackObjectDefinition(
                object_type="SubjectEvidence",
                display_name="Subject evidence",
                model_ref="SubjectMention",
                metadata={
                    "object_role": "curatable_unit",
                    "workspace_display": {
                        "summary_fields": ["symbol", "identifier"],
                        "groups": [
                            {
                                "id": "identity",
                                "label": "Identity",
                                "fields": [
                                    "symbol", "identifier", "taxon", "proposed_symbol", "mention", "lookup_outcome",
                                ],
                            },
                        ],
                    },
                },
                fields=[
                    _field("symbol"), _field("identifier"), _field("taxon"), _field("mention"),
                    _field("proposed_symbol"),
                ],
            ),
        ],
    )


def _row(
    payload: dict,
    *,
    object_type: str = "Observation",
    site_protected: bool = False,
    metadata: dict | None = None,
    findings: list[ValidationFinding] | None = None,
):
    envelope = DomainEnvelope(
        envelope_id="env-resolution",
        domain_pack_id="fixture.resolution",
        domain_pack_version="0.1.0",
        status=DomainEnvelopeStatus.EXTRACTED,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type=object_type,
                object_id="object-1",
                payload=payload,
                metadata=metadata or {},
            )
        ],
        validation_findings=findings or [],
    )
    rows = DomainPackMetadataReviewRowMaterializer(_metadata(site_protected=site_protected)).materialize(
        envelope, envelope_revision=1,
    )
    assert len(rows) == 1
    return rows[0]


def _workspace_field(row, field_path: str) -> DomainEnvelopeReviewRowSummaryField:
    for raw in row.metadata["workspace_fields"]:
        if raw["field_path"] == field_path:
            return DomainEnvelopeReviewRowSummaryField.model_validate(raw)
    raise AssertionError(f"no workspace field {field_path}")


def _summary_field(row, field_path: str) -> DomainEnvelopeReviewRowSummaryField:
    return next(field for field in row.summary_fields if field.field_path == field_path)


def test_resolved_value_shows_label_and_id_with_paper_wording_apart():
    site = resolved_value(
        "structures near the gut",
        {"curie": "ONT:0000101", "name": "gut"},
        explanation="Exact synonym match.",
    )
    row = _row({"site": site, "note": "plain"})

    whole = _summary_field(row, "site").resolution
    assert whole is not None
    assert whole.display_text == "gut (ONT:0000101)"
    assert whole.values == [
        DomainEnvelopeReviewResolvedValue(
            value_path="site",
            display_text="gut (ONT:0000101)",
            mention="structures near the gut",
            resolution_state="resolved",
            lookup_outcome="matched",
            lookup_result="Matched",
            validator_explanation="Exact synonym match.",
            validator_curator_message=None,
            identity_field_paths=["site.curie", "site.name"],
            id_key="curie",
            label_key="name",
            stored_identity={"curie": "ONT:0000101", "name": "gut"},
            overridable=True,
        )
    ]
    # An identity key of the value shows that key; the paper wording stays apart.
    assert _workspace_field(row, "site.curie").resolution.display_text == "ONT:0000101"
    assert _workspace_field(row, "site.name").resolution.display_text == "gut"
    # The value's own leaves read in plain words and name their leaf.
    leaves = {
        path: _workspace_field(row, path).resolution
        for path in (
            "site.mention",
            "site.resolution_state",
            "site.lookup_outcome",
            "site.validator_explanation",
        )
    }
    assert {path: (leaf.display_text, leaf.leaf_key) for path, leaf in leaves.items()} == {
        "site.mention": ("structures near the gut", "mention"),
        "site.resolution_state": ("Resolved", "resolution_state"),
        "site.lookup_outcome": ("Matched", "lookup_outcome"),
        "site.validator_explanation": ("Exact synonym match.", "validator_explanation"),
    }
    assert _workspace_field(row, "site.curie").resolution.leaf_key is None
    assert _summary_field(row, "note").resolution is None
    assert row.display_label == "gut"


def test_unresolved_value_reads_unresolved_never_its_paper_wording():
    site = unresolved_value(
        "structures near the residual body",
        identity_keys=TERM_KEYS,
        outcome="not_found",
        explanation="No term matched the wording.",
        validator_curator_message="Pick a term by hand.",
    )
    row = _row({"site": site})

    for field in (
        _summary_field(row, "site"),
        _workspace_field(row, "site.curie"),
        _workspace_field(row, "site.name"),
    ):
        assert field.resolution is not None
        assert field.resolution.display_text == UNRESOLVED_DISPLAY
        [value] = field.resolution.values
        assert value.display_text == UNRESOLVED_DISPLAY
        assert value.mention == "structures near the residual body"
        assert value.resolution_state == "unresolved"
        assert value.lookup_outcome == "not_found"
        assert value.lookup_result == "Not found"
        assert value.validator_explanation == "No term matched the wording."
        assert value.validator_curator_message == "Pick a term by hand."
    # The stored identity stays empty: nothing fills it from the wording.
    assert _workspace_field(row, "site.curie").value is None
    assert row.display_label == "structures near the residual body (paper wording)"


def test_staged_value_reads_not_validated_yet():
    row = _row({"site": unresolved_value("gut lining", identity_keys=TERM_KEYS)})

    [value] = _workspace_field(row, "site.curie").resolution.values
    assert value.lookup_result == "Not validated yet"
    assert value.validator_explanation == NOT_VALIDATED_EXPLANATION


def test_list_elements_each_carry_their_own_reading():
    codes = [
        resolved_value("IMP", {"curie": "ONT:0000315"}),
        unresolved_value("IGI", identity_keys=("curie",), outcome="ambiguous"),
    ]
    row = _row({"site": unresolved_value("gut", identity_keys=TERM_KEYS), "codes": codes})

    resolution = _workspace_field(row, "codes").resolution
    assert resolution.display_text == f"ONT:0000315 | {UNRESOLVED_DISPLAY}"
    assert [value.value_path for value in resolution.values] == ["codes[0]", "codes[1]"]
    assert [value.mention for value in resolution.values] == ["IMP", "IGI"]
    assert [value.lookup_result for value in resolution.values] == ["Matched", "Several matches"]


def test_container_field_lists_nested_values_and_never_renders_json():
    conditions = [
        {
            "relation": "has_condition",
            "component": unresolved_value("heat shock", identity_keys=TERM_KEYS, outcome="not_found"),
        },
        {
            "relation": "has_condition",
            "component": resolved_value("cold", {"curie": "ONT:0000200", "name": "cold exposure"}),
        },
    ]
    row = _row({"site": unresolved_value("gut", identity_keys=TERM_KEYS), "conditions": conditions})

    resolution = _workspace_field(row, "conditions").resolution
    assert resolution.display_text == (
        f"relation: has_condition; component: {UNRESOLVED_DISPLAY} | "
        "relation: has_condition; component: cold exposure (ONT:0000200)"
    )
    assert "{" not in resolution.display_text
    assert "heat shock" not in resolution.display_text
    assert [value.value_path for value in resolution.values] == [
        "conditions[0].component",
        "conditions[1].component",
    ]
    assert [value.mention for value in resolution.values] == ["heat shock", "cold"]


def test_object_root_value_reads_through_its_identity_fields():
    payload = {
        **unresolved_value(
            "abc-1",
            identity_keys=("symbol", "identifier", "taxon"),
            outcome="rejected_candidates",
            explanation="Every candidate was a different species.",
        ),
        "proposed_symbol": "ABC1",
    }
    row = _row(payload, object_type="SubjectEvidence")

    symbol = _workspace_field(row, "symbol").resolution
    assert symbol.display_text == UNRESOLVED_DISPLAY
    [value] = symbol.values
    assert value.value_path == ""
    assert value.mention == "abc-1"
    assert value.lookup_result == "Candidates rejected"
    # The extractor's proposal never becomes the validated value.
    assert _workspace_field(row, "proposed_symbol").resolution is None
    assert "ABC1" not in symbol.display_text
    # The object root's own leaves read in plain words.
    mention = _workspace_field(row, "mention").resolution
    outcome = _workspace_field(row, "lookup_outcome").resolution
    assert (mention.display_text, mention.leaf_key) == ("abc-1", "mention")
    assert (outcome.display_text, outcome.leaf_key) == ("Candidates rejected", "lookup_outcome")
    assert row.display_label == "object-1"


@pytest.mark.parametrize("covered", [False, True])
def test_legacy_values_follow_the_read_time_rule(covered):
    metadata = (
        {VALIDATOR_MATERIALIZATION_METADATA_KEY: [{"materialized_field_paths": ["site.curie"]}]}
        if covered
        else {}
    )
    row = _row({"site": {"curie": "ONT:0000101", "name": "gut"}}, metadata=metadata)

    resolution = _workspace_field(row, "site.curie").resolution
    [value] = resolution.values
    assert value.validator_explanation == LEGACY_EXPLANATION
    if covered:
        assert resolution.display_text == "ONT:0000101"
        assert value.resolution_state == "resolved"
        assert value.lookup_result == "Matched"
        assert value.mention is None
    else:
        assert resolution.display_text == UNRESOLVED_DISPLAY
        assert value.resolution_state == "unresolved"
        assert value.lookup_outcome == "legacy_unverified"
        assert value.lookup_result == "Legacy, unverified"
        assert value.mention == f"gut (ONT:0000101) {LEGACY_UNVERIFIED_SUFFIX}"
    # The field shows the read-time value (an unverified id is not shown as a value);
    # the stored identity, never rewritten, is what an override's `before` names.
    assert _workspace_field(row, "site.curie").value == ("ONT:0000101" if covered else None)
    assert value.stored_identity == {"curie": "ONT:0000101", "name": "gut"}


def test_resolved_value_vocabularies_are_closed():
    with pytest.raises(ValueError, match="lookup_outcome must be one of"):
        DomainEnvelopeReviewResolvedValue(
            value_path="site", display_text=UNRESOLVED_DISPLAY, resolution_state="unresolved",
            lookup_outcome="pending", lookup_result="Pending",
        )
    with pytest.raises(ValueError, match="plain words"):
        DomainEnvelopeReviewResolvedValue(
            value_path="site", display_text=UNRESOLVED_DISPLAY, resolution_state="unresolved",
            lookup_outcome="not_found", lookup_result="Missing",
        )


@pytest.mark.parametrize(
    ("site", "issue"),
    [
        (
            {"mention": "gut", "curie": "ONT:1", "name": "gut", "resolution_state": "resolved",
             "lookup_outcome": "pending"},
            "lookup_outcome must be one of",
        ),
        (
            {"mention": "gut", "curie": None, "name": None, "resolution_state": "unresolved",
             "lookup_outcome": None},
            "lookup_outcome must be one of",
        ),
        (
            {"mention": "gut", "curie": "ONT:1", "name": "gut", "resolution_state": "resolved",
             "lookup_outcome": "not_found"},
            "A resolved value's lookup_outcome is one of",
        ),
        (
            {"mention": "gut", "curie": None, "name": None, "resolution_state": "unresolved",
             "lookup_outcome": "not_found", "validator_explanation": {"text": "no"}},
            "validator_explanation must be text or null",
        ),
    ],
)
def test_unreadable_stored_value_reads_unresolved_without_breaking_the_row(site, issue, caplog):
    row = _row({"site": site, "note": "plain"})

    for field in (
        _summary_field(row, "site"),
        _workspace_field(row, "site.curie"),
        _workspace_field(row, "site.name"),
    ):
        assert field.resolution.display_text == UNRESOLVED_DISPLAY
        [value] = field.resolution.values
        assert value.resolution_state == "unresolved"
        assert value.lookup_outcome == "invalid_schema"
        assert value.lookup_result == "Invalid validator output"
        assert value.mention == "gut (invalid record, unverified)"
        assert value.validator_explanation is None
        assert value.issue == (
            "This stored value could not be read; please re-run validation or contact the "
            "AI Curation developers."
        )
    assert _workspace_field(row, "site.lookup_outcome").resolution.display_text == "Invalid validator output"
    assert _summary_field(row, "note").value == "plain"
    # The technical detail goes to the log, with where the value lives.
    assert "unreadable resolvable value" in caplog.text
    assert "envelope_id=env-resolution envelope_revision=1 object_id=object-1" in caplog.text
    assert issue in caplog.text


def test_validated_keys_read_like_the_identity():
    unresolved = _row(
        unresolved_value("abc-1", identity_keys=("symbol", "identifier", "taxon")),
        object_type="SubjectEvidence",
    )
    resolved = _row(
        resolved_value("abc-1", {"symbol": "abc-1", "identifier": "GENE:1", "taxon": "TAXON:9"}),
        object_type="SubjectEvidence",
    )

    assert _workspace_field(unresolved, "taxon").resolution.display_text == UNRESOLVED_DISPLAY
    assert _workspace_field(resolved, "taxon").resolution.display_text == "TAXON:9"
    assert _workspace_field(resolved, "symbol").resolution.values[0].display_text == "abc-1 (GENE:1)"


def test_an_overruled_value_never_shows_its_old_identity_or_proposal_as_the_value():
    # A validator overruled a builder-resolved value: the old identity is kept
    # only under overruled_* keys; proposed_* is the extractor's own proposal.
    site = resolved_value("gut lining", {"curie": "ONT:0000101", "name": "gut"}, proposed_curie="ONT:0000102")
    mark_unresolved(
        site,
        "rejected_candidates",
        explanation="The lookup matched a different tissue.",
        identity_keys=TERM_KEYS,
    )
    assert site["overruled_curie"] == "ONT:0000101"
    row = _row({"site": site})

    whole = _summary_field(row, "site").resolution
    assert whole.display_text == UNRESOLVED_DISPLAY
    [value] = whole.values
    assert value.display_text == UNRESOLVED_DISPLAY
    assert value.lookup_result == "Candidates rejected"
    for path in ("site.curie", "site.name"):
        assert _workspace_field(row, path).resolution.display_text == UNRESOLVED_DISPLAY
    # The extractor's proposal is its own labelled field, never a reading of the value.
    proposal = _workspace_field(row, "site.proposed_curie")
    assert proposal.value == "ONT:0000102"
    assert proposal.resolution is None
    readings = [whole.display_text, *(item.display_text for item in whole.values)]
    assert not any("ONT:0000101" in text or "ONT:0000102" in text for text in readings)
    assert "ONT:0000101" not in row.display_label


OVERRIDE_AT = "2026-09-23T20:00:00+00:00"
DISAGREEMENT = "domain_pack.validator_disagrees_with_curator_override"


def _overridden_site() -> dict:
    site = unresolved_value("gut lining", identity_keys=TERM_KEYS, outcome="not_found")
    apply_curator_identity(
        site,
        {"curie": "ONT:0000555", "name": "midgut"},
        identity_keys=TERM_KEYS,
        id_key="curie",
        label_key="name",
        actor_id="curator-1", actor_display_name="Curator One",
        at=OVERRIDE_AT,
    )
    return site


def _disagreement(*, field_path: str | None, message: str, status=ValidationFindingStatus.OPEN):
    object_ref = ObjectRef(object_id="object-1")
    return ValidationFinding(
        severity=ValidationFindingSeverity.WARNING,
        status=status,
        code=DISAGREEMENT,
        message=message,
        object_ref=None if field_path else object_ref,
        field_ref=FieldRef(object_ref=object_ref, field_path=field_path) if field_path else None,
    )


def test_a_curator_override_reads_resolved_with_who_and_when():
    row = _row({"site": _overridden_site()})

    curie = _workspace_field(row, "site.curie").resolution
    assert curie.display_text == "ONT:0000555"
    [value] = curie.values
    assert value.resolution_state == "resolved"
    assert value.lookup_outcome == "curator_override"
    assert value.lookup_result == "Curator override"
    assert value.mention == "gut lining"
    assert value.curator_override.actor_id == "curator-1"
    assert value.curator_override.actor_display_name == "Curator One"
    assert value.curator_override.at == OVERRIDE_AT
    assert value.override_disagreements == []
    assert value.identity_field_paths == ["site.curie", "site.name"]
    # A replace_identity override sends each identity key as stored as its `before`.
    assert value.stored_identity == {"curie": "ONT:0000555", "name": "midgut"}
    assert (value.id_key, value.label_key, value.validated_keys) == ("curie", "name", [])
    assert _summary_field(row, "site").resolution.display_text == "midgut (ONT:0000555)"
    assert _workspace_field(row, "site.lookup_outcome").resolution.display_text == "Curator override"


def test_an_open_validator_disagreement_is_carried_on_the_overridden_value():
    message = "Validator disagrees with the curator override: its lookup result is Not found."
    row = _row(
        {"site": _overridden_site()},
        findings=[
            _disagreement(field_path="site", message=message),
            _disagreement(
                field_path="site", message="An old, resolved disagreement.",
                status=ValidationFindingStatus.RESOLVED,
            ),
        ],
    )

    [value] = _workspace_field(row, "site.curie").resolution.values
    assert value.override_disagreements == [message]


def test_an_object_root_override_takes_object_level_disagreements():
    subject = unresolved_value("abc-1", identity_keys=("symbol", "identifier", "taxon"))
    apply_curator_identity(
        subject,
        {"symbol": "abc-1", "identifier": "GENE:7", "taxon": None},
        identity_keys=("symbol", "identifier", "taxon"),
        id_key="identifier",
        label_key="symbol",
        actor_id="curator-2", actor_display_name="curator-2",
        at=OVERRIDE_AT,
    )
    message = "Validator disagrees with the curator override: it resolved identifier 'GENE:8'."
    row = _row(subject, object_type="SubjectEvidence", findings=[_disagreement(field_path=None, message=message)])

    [value] = _workspace_field(row, "identifier").resolution.values
    assert value.value_path == ""
    assert value.curator_override.actor_id == "curator-2"
    assert value.override_disagreements == [message]
    assert value.identity_field_paths == ["identifier", "symbol", "taxon"]
    assert value.validated_keys == ["taxon"]
    assert value.stored_identity == {"symbol": "abc-1", "identifier": "GENE:7", "taxon": None}


def test_clearing_an_override_reads_unresolved_again():
    site = _overridden_site()
    apply_curator_identity(
        site, {"curie": None, "name": None}, identity_keys=TERM_KEYS, id_key="curie", label_key="name",
        actor_id="curator-1", actor_display_name="curator-1", at=OVERRIDE_AT,
    )
    row = _row({"site": site})

    [value] = _workspace_field(row, "site.curie").resolution.values
    assert _workspace_field(row, "site.curie").resolution.display_text == UNRESOLVED_DISPLAY
    assert value.curator_override is None
    assert value.lookup_outcome == "not_found"


def test_a_values_own_leaves_are_read_only_for_curators():
    row = _row({"site": _overridden_site()})

    for path in ("site.mention", "site.resolution_state", "site.lookup_outcome", "site.validator_explanation"):
        metadata = _workspace_field(row, path).metadata
        assert metadata["read_only"] is True
        assert metadata["editable"] is False
    assert _workspace_field(row, "site.curie").metadata["read_only"] is False


def test_a_protected_value_field_is_flagged_as_blocking_overrides():
    open_row = _row({"site": _overridden_site()})
    protected_row = _row({"site": _overridden_site()}, site_protected=True)

    [open_value] = _workspace_field(open_row, "site.curie").resolution.values
    [protected_value] = _workspace_field(protected_row, "site.curie").resolution.values
    assert (open_value.container_protected, open_value.overridable) == (False, True)
    assert (protected_value.container_protected, protected_value.overridable) == (True, False)


def test_profile_values_and_list_elements_carry_their_stored_value():
    gene = unresolved_value("abc one", identity_keys=TERM_KEYS, outcome="not_found")
    codes = [unresolved_value("IMP", identity_keys=("curie",)), unresolved_value("IDA", identity_keys=("curie",))]
    row = _row({"site": _overridden_site(), "attributes": {"gene": gene}, "codes": codes})

    [attribute_value] = _summary_field(row, "attributes.gene").resolution.values
    [site_value] = _workspace_field(row, "site.curie").resolution.values
    code_values = _workspace_field(row, "codes").resolution.values
    # A profile value takes a whole-value replace, and a list element a removal,
    # whose `before` is the value as stored.
    assert attribute_value.stored_value == gene
    assert [value.stored_value for value in code_values] == codes
    assert [value.value_path for value in code_values] == ["codes[0]", "codes[1]"]
    assert site_value.stored_value is None


def test_a_value_stored_as_plain_text_reads_as_legacy_text():
    """B5: a pre-contract text value (or list of texts) reads legacy, unverified, and never crashes."""

    row = _row({"site": "gut", "codes": ["ECO:0000314"]})

    [site] = _workspace_field(row, "site.curie").resolution.values
    assert (site.lookup_outcome, site.mention) == ("legacy_unverified", "gut (legacy, unverified)")
    assert site.stored_identity == {"curie": None, "name": None}
    assert site.overridable is True
    [code] = _summary_field(row, "codes").resolution.values if _summary_field_exists(row, "codes") else (
        _workspace_field(row, "codes").resolution.values)
    assert code.lookup_outcome == "legacy_unverified"


def _summary_field_exists(row, field_path):
    return any(field.field_path == field_path for field in row.summary_fields)


def test_a_display_copy_takes_its_stored_identity_from_storage():
    """B5: a pack reading through a display copy passes the stored envelope for override `before`s."""

    stored_site = {"curie": "ONT:7", "name": "old guess"}
    stored = DomainEnvelope(
        envelope_id="env-resolution", domain_pack_id="fixture.resolution", domain_pack_version="0.1.0",
        status=DomainEnvelopeStatus.EXTRACTED,
        extracted_objects=[CuratableObjectEnvelope(object_type="Observation", object_id="object-1",
                                                   payload={"site": stored_site})],
    )
    display = stored.model_copy(update={"extracted_objects": [stored.extracted_objects[0].model_copy(update={
        "payload": {"site": {"curie": None, "name": None, "mention": "old guess (ONT:7) (legacy, unverified)",
                             "resolution_state": "unresolved", "lookup_outcome": "legacy_unverified",
                             "validator_explanation": "Recorded before validation tracking; not verified."}},
    })]})

    [row] = DomainPackMetadataReviewRowMaterializer(_metadata()).materialize(
        display, envelope_revision=1, stored_envelope=stored,
    )

    [site] = _workspace_field(row, "site.curie").resolution.values
    assert site.lookup_outcome == "legacy_unverified"
    assert site.stored_identity == {"curie": "ONT:7", "name": "old guess"}
