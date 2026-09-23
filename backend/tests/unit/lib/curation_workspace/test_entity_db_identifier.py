"""An entity tag's database identifier comes only from a validated lookup (ALL-1283)."""

from __future__ import annotations

import pytest

from src.lib.curation_workspace.session_serializers import _entity_db_identifier
from src.schemas.curation_workspace import CurationDraftField, FieldValidationStatus


def _field(status: FieldValidationStatus) -> CurationDraftField:
    return CurationDraftField.model_validate({
        "field_key": "entity_name",
        "label": "Entity",
        "value": "APOE",
        "validation_result": {
            "status": status.value,
            "candidate_matches": [{"label": "APOE", "identifier": "ID:613"}],
        },
    })


def test_a_validated_entity_carries_its_identifier():
    assert _entity_db_identifier(_field(FieldValidationStatus.VALIDATED)) == "ID:613"


@pytest.mark.parametrize("status", [
    FieldValidationStatus.AMBIGUOUS, FieldValidationStatus.NOT_FOUND, FieldValidationStatus.CONFLICT,
    FieldValidationStatus.SKIPPED,
])
def test_a_candidate_of_an_unresolved_lookup_is_not_an_identifier(status):
    assert _entity_db_identifier(_field(status)) is None
