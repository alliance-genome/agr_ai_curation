"""Tests for workspace field projection presentation hints."""

from __future__ import annotations

import pytest

from src.lib.curation_workspace.pipeline import _draft_fields_from_review_row
from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DomainEnvelope,
    DomainEnvelopeStatus,
)
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackMetadata,
    DomainPackObjectDefinition,
)


pytestmark = pytest.mark.provider_agnostic_domain_pack


def _pack() -> DomainPackMetadata:
    return DomainPackMetadata(
        pack_id="fixture.hints",
        display_name="Fixture Hints Pack",
        version="0.1.0",
        metadata_api_version="1.0.0",
        object_definitions=[
            DomainPackObjectDefinition(
                object_type="Thing",
                display_name="Thing",
                metadata={
                    "object_role": "curatable_unit",
                    "workspace_display": {
                        "primary_label_field": "name",
                        "groups": [
                            {
                                "id": "main",
                                "label": "Main",
                                "fields": ["name", "tags", "code"],
                            },
                        ],
                    },
                },
                fields=[
                    DomainPackFieldDefinition(
                        field_path="name",
                        field_type=DomainPackFieldType.STRING,
                        metadata={"editable": True},
                    ),
                    DomainPackFieldDefinition(
                        field_path="tags",
                        field_type=DomainPackFieldType.ARRAY,
                        metadata={"hide_when_empty": True, "render_as": "chip"},
                    ),
                    DomainPackFieldDefinition(
                        field_path="code",
                        field_type=DomainPackFieldType.STRING,
                        metadata={"render_as": "curie-chip"},
                    ),
                ],
            ),
        ],
    )


def _materialize(payload: dict):
    envelope = DomainEnvelope(
        envelope_id="env-1",
        domain_pack_id="fixture.hints",
        domain_pack_version="0.1.0",
        status=DomainEnvelopeStatus.EXTRACTED,
        objects=[
            CuratableObjectEnvelope(
                object_type="Thing",
                object_id="thing-1",
                status=CuratableObjectStatus.PENDING,
                payload=payload,
            )
        ],
    )
    rows = DomainPackMetadataReviewRowMaterializer(_pack()).materialize(
        envelope,
        envelope_revision=1,
    )
    return rows[0]


def _workspace_fields(payload: dict) -> list[dict]:
    return _materialize(payload).metadata["workspace_fields"]


def test_hide_when_empty_drops_empty_workspace_field():
    fields = _workspace_fields({"name": "n", "tags": [], "code": "X:1"})

    assert [field["field_path"] for field in fields] == ["name", "code"]


def test_hide_when_empty_keeps_populated_field():
    fields = _workspace_fields({"name": "n", "tags": ["a"], "code": "X:1"})

    assert [field["field_path"] for field in fields] == ["name", "tags", "code"]


def test_render_as_metadata_passes_through_to_projected_field():
    fields = _workspace_fields({"name": "n", "tags": ["a"], "code": "X:1"})
    by_path = {field["field_path"]: field for field in fields}

    assert by_path["tags"]["metadata"]["render_as"] == "chip"
    assert by_path["code"]["metadata"]["render_as"] == "curie-chip"


def test_render_as_metadata_passes_through_to_draft_field():
    row = _materialize({"name": "n", "tags": ["a"], "code": "X:1"})
    draft_fields = _draft_fields_from_review_row(row)
    by_key = {field.field_key: field for field in draft_fields}

    assert by_key["code"].metadata["field_metadata"]["render_as"] == "curie-chip"
    assert by_key["code"].read_only is True
    assert by_key["name"].read_only is False
