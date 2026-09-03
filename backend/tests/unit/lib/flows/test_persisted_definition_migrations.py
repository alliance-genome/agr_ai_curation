"""Regression coverage for versioned saved-flow definition migrations."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.lib.flows.persisted_definition_migrations import (
    PersistedFlowDefinitionMigrationError,
    RETIRED_ALLELE_VALIDATION_MIGRATION,
    migrate_persisted_flow_definition,
)


_FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "flows"
    / "pre_catalog_change_allele_flow.json"
)


def _fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text())


def test_retired_allele_selections_are_removed_without_other_changes():
    original = _fixture()
    expected = deepcopy(original)
    expected_data = expected["nodes"][1]["data"]
    expected_data["validation_attachments"] = [
        expected_data["validation_attachments"][0]
    ]
    expected_data["validation_groups"] = [expected_data["validation_groups"][0]]

    result = migrate_persisted_flow_definition(original)

    assert result.definition == expected
    assert original == _fixture()
    assert result.applied_versions == (RETIRED_ALLELE_VALIDATION_MIGRATION,)
    assert len(result.warnings) == 1
    assert "save the flow" in result.warnings[0]


def test_retired_allele_migration_is_idempotent():
    first = migrate_persisted_flow_definition(_fixture())
    second = migrate_persisted_flow_definition(first.definition)

    assert second.definition == first.definition
    assert second.applied_versions == ()
    assert second.warnings == ()


def test_current_and_arbitrary_unknown_selections_are_not_silently_removed():
    definition = _fixture()
    data = definition["nodes"][1]["data"]
    data["validation_attachments"] = [
        data["validation_attachments"][0],
        {
            "attachment_id": "agr.alliance.allele:binding:actually_unknown:pack:*:*",
            "domain_pack_id": "agr.alliance.allele",
            "validator_id": "actually_unknown",
            "validator_binding_id": "actually_unknown",
            "state": "active",
            "scope": "pack",
            "enabled": True,
        },
    ]
    data["validation_groups"] = [data["validation_groups"][0]]

    result = migrate_persisted_flow_definition(definition)

    assert result.definition == definition
    assert result.applied_versions == ()


@pytest.mark.parametrize("location", ["wrong_agent", "wrong_pack", "edge"])
def test_unreviewed_retired_reference_shapes_fail_closed(location: str):
    definition = _fixture()
    retired = definition["nodes"][1]["data"]["validation_attachments"][1]
    definition["nodes"][1]["data"]["validation_attachments"] = [retired]
    definition["nodes"][1]["data"]["validation_groups"] = []
    if location == "wrong_agent":
        definition["nodes"][1]["data"]["agent_id"] = "gene_extractor"
    elif location == "wrong_pack":
        retired["domain_pack_id"] = "agr.alliance.gene"
    else:
        definition["nodes"][1]["data"]["validation_attachments"] = []
        definition["edges"][0]["validator_binding_id"] = (
            "allele_pending_envelope_validator"
        )

    with pytest.raises(PersistedFlowDefinitionMigrationError, match="manual review"):
        migrate_persisted_flow_definition(definition)
