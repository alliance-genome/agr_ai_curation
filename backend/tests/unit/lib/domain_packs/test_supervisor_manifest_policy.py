"""Supervisor manifest policy validation tests."""

from __future__ import annotations

import pytest

from src.lib.domain_packs.supervisor_manifest import (
    SupervisorManifestPolicyError,
    supervisor_manifest_policy_for_object,
    validate_supervisor_manifest_policies,
)
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackMetadata,
    DomainPackObjectDefinition,
)


pytestmark = pytest.mark.provider_agnostic_domain_pack


def _metadata(
    *,
    object_metadata: dict,
    fields: list[DomainPackFieldDefinition] | None = None,
) -> DomainPackMetadata:
    return DomainPackMetadata(
        pack_id="fixture.supervisor",
        display_name="Fixture Supervisor Pack",
        version="0.1.0",
        metadata_api_version="1.0.0",
        object_definitions=[
            DomainPackObjectDefinition(
                object_type="Assertion",
                display_name="Assertion",
                metadata=object_metadata,
                fields=fields
                or [
                    DomainPackFieldDefinition(
                        field_path="label",
                        field_type=DomainPackFieldType.STRING,
                    ),
                    DomainPackFieldDefinition(
                        field_path="curie",
                        field_type=DomainPackFieldType.STRING,
                    ),
                ],
            )
        ],
    )


def test_supervisor_manifest_policy_required_for_curatable_object():
    metadata = _metadata(object_metadata={"object_role": "curatable_unit"})

    with pytest.raises(SupervisorManifestPolicyError, match="must declare"):
        validate_supervisor_manifest_policies(metadata)


def test_supervisor_manifest_rejects_unknown_keys():
    metadata = _metadata(
        object_metadata={
            "object_role": "curatable_unit",
            "supervisor_manifest": {
                "primary_label_field": "label",
                "retry_instruction": "do not put prose in YAML",
            },
        }
    )

    with pytest.raises(SupervisorManifestPolicyError, match="unknown key"):
        validate_supervisor_manifest_policies(metadata)


def test_supervisor_manifest_rejects_duplicate_field_paths():
    metadata = _metadata(
        object_metadata={
            "object_role": "curatable_unit",
            "supervisor_manifest": {
                "primary_label_field": "label",
                "summary_fields": ["curie", "curie"],
            },
        }
    )

    with pytest.raises(SupervisorManifestPolicyError, match="duplicate"):
        validate_supervisor_manifest_policies(metadata)


def test_supervisor_manifest_rejects_evidence_quote_paths():
    metadata = _metadata(
        object_metadata={
            "object_role": "curatable_unit",
            "supervisor_manifest": {
                "primary_label_field": "label",
                "summary_fields": ["evidence[0].quote"],
            },
        },
        fields=[
            DomainPackFieldDefinition(
                field_path="label",
                field_type=DomainPackFieldType.STRING,
            ),
            DomainPackFieldDefinition(
                field_path="evidence[0].quote",
                field_type=DomainPackFieldType.STRING,
            ),
        ],
    )

    with pytest.raises(SupervisorManifestPolicyError, match="evidence/quote/chunk"):
        validate_supervisor_manifest_policies(metadata)


def test_supervisor_manifest_rejects_non_scalar_fields():
    metadata = _metadata(
        object_metadata={
            "object_role": "curatable_unit",
            "supervisor_manifest": {
                "primary_label_field": "label",
                "summary_fields": ["attributes"],
            },
        },
        fields=[
            DomainPackFieldDefinition(
                field_path="label",
                field_type=DomainPackFieldType.STRING,
            ),
            DomainPackFieldDefinition(
                field_path="attributes",
                field_type=DomainPackFieldType.OBJECT,
            ),
        ],
    )

    with pytest.raises(SupervisorManifestPolicyError, match="non-scalar"):
        validate_supervisor_manifest_policies(metadata)


def test_supervisor_manifest_rejects_unknown_field_paths():
    metadata = _metadata(
        object_metadata={
            "object_role": "curatable_unit",
            "supervisor_manifest": {
                "primary_label_field": "label",
                "summary_fields": ["missing"],
            },
        }
    )

    with pytest.raises(SupervisorManifestPolicyError, match="undeclared"):
        validate_supervisor_manifest_policies(metadata)


def _labeled_fields() -> list[DomainPackFieldDefinition]:
    return [
        DomainPackFieldDefinition(
            field_path="label",
            field_type=DomainPackFieldType.STRING,
            display_name="Label",
        ),
        DomainPackFieldDefinition(
            field_path="symbol",
            field_type=DomainPackFieldType.STRING,
            display_name="Symbol",
        ),
        DomainPackFieldDefinition(
            field_path="curie",
            field_type=DomainPackFieldType.STRING,
            display_name="Validated CURIE",
        ),
    ]


def test_supervisor_manifest_falls_back_to_workspace_display_policy_source():
    """When no supervisor_manifest exists, workspace_display is the policy source."""

    metadata = _metadata(
        object_metadata={
            "object_role": "curatable_unit",
            "workspace_display": {
                "primary_label_field": "label",
                "secondary_label_field": "symbol",
                "summary_fields": ["curie"],
            },
        },
        fields=_labeled_fields(),
    )

    # Validation accepts the workspace_display-only pack.
    validate_supervisor_manifest_policies(metadata)

    policy = supervisor_manifest_policy_for_object(metadata, "Assertion")

    assert policy.source == "workspace_display"
    assert [field.path for field in policy.primary_label_fields] == ["label"]
    assert policy.primary_label_fields[0].label == "Label"
    assert policy.secondary_label_field is not None
    assert policy.secondary_label_field.path == "symbol"
    assert policy.secondary_label_field.label == "Symbol"
    assert [field.path for field in policy.summary_fields] == ["curie"]
    assert policy.summary_fields[0].label == "Validated CURIE"
    assert policy.field_paths == ("label", "symbol", "curie")


def test_supervisor_manifest_overrides_workspace_display_when_both_present():
    """supervisor_manifest takes precedence over workspace_display."""

    metadata = _metadata(
        object_metadata={
            "object_role": "curatable_unit",
            "supervisor_manifest": {
                "primary_label_field": "symbol",
                "summary_fields": ["curie"],
            },
            "workspace_display": {
                "primary_label_field": "label",
                "secondary_label_field": "symbol",
                "summary_fields": ["curie"],
            },
        },
        fields=_labeled_fields(),
    )

    policy = supervisor_manifest_policy_for_object(metadata, "Assertion")

    assert policy.source == "supervisor_manifest"
    assert [field.path for field in policy.primary_label_fields] == ["symbol"]
    # The broader workspace_display secondary label is not used when overridden.
    assert policy.secondary_label_field is None
    assert [field.path for field in policy.summary_fields] == ["curie"]
