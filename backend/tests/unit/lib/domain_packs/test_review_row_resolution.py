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


def _metadata() -> DomainPackMetadata:
    return DomainPackMetadata(
        pack_id="fixture.resolution",
        display_name="Resolution fixture",
        version="0.1.0",
        metadata_api_version="1.0.0",
        model_definitions=[
            DomainPackModelDefinition(
                model_id="SubjectMention",
                display_name="Subject mention",
                metadata={"display": {"label": "symbol", "id": "identifier", "mention": "mention"}},
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
                        "summary_fields": ["site", "note"],
                        "groups": [
                            {
                                "id": "site",
                                "label": "Site",
                                "fields": [
                                    "site.curie",
                                    "site.name",
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
                    _field("site", DomainPackFieldType.OBJECT, metadata={"display": TERM_DISPLAY}),
                    _field("site.curie"),
                    _field("site.name"),
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
                                "fields": ["symbol", "identifier", "proposed_symbol", "mention", "lookup_outcome"],
                            },
                        ],
                    },
                },
                fields=[_field("symbol"), _field("identifier"), _field("mention"), _field("proposed_symbol")],
            ),
        ],
    )


def _row(payload: dict, *, object_type: str = "Observation", metadata: dict | None = None):
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
    )
    rows = DomainPackMetadataReviewRowMaterializer(_metadata()).materialize(
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
            identity_keys=("symbol", "identifier"),
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
    # The stored payload is read, never rewritten.
    assert _workspace_field(row, "site.curie").value == "ONT:0000101"


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
            "outside the controlled vocabulary",
        ),
        (
            {"mention": "gut", "curie": None, "name": None, "resolution_state": "unresolved",
             "lookup_outcome": None},
            "outside the controlled vocabulary",
        ),
        (
            {"mention": "gut", "curie": "ONT:1", "name": "gut", "resolution_state": "resolved",
             "lookup_outcome": "not_found"},
            "lookup_outcome is matched",
        ),
        (
            {"mention": "gut", "curie": None, "name": None, "resolution_state": "unresolved",
             "lookup_outcome": "not_found", "validator_explanation": {"text": "no"}},
            "validator_explanation must be text",
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
        assert value.lookup_outcome is None
        assert value.lookup_result == "Stored value unreadable"
        assert value.mention == "gut"
        assert issue in value.issue
    assert _workspace_field(row, "site.lookup_outcome").resolution.display_text == "Stored value unreadable"
    assert _summary_field(row, "note").value == "plain"
    assert "unreadable resolvable value" in caplog.text
