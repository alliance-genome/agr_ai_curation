"""Tests for forward-only migrations of saved flow definitions."""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.lib.flows.persisted_flow_migrations import (
    PersistedFlowMigrationError,
    RETIRED_ALLELE_PENDING_VALIDATOR_ATTACHMENT_IDS,
    RETIRED_ALLELE_PENDING_VALIDATOR_BINDING_ID,
    RETIRED_ALLELE_PENDING_VALIDATOR_MIGRATION,
    migrate_persisted_flow_definition,
)


def _selection(attachment_id: str) -> dict:
    metadata_only = ":metadata:" in attachment_id
    return {
        "attachment_id": attachment_id,
        "domain_pack_id": "agr.alliance.allele",
        "validator_id": "agr.alliance:allele_validation",
        "validator_binding_id": (
            None if metadata_only else RETIRED_ALLELE_PENDING_VALIDATOR_BINDING_ID
        ),
        "state": "under_development",
        "scope": "pack" if metadata_only else "object",
        "enabled": False,
    }


def _definition(*, agent_id: str = "allele_extractor") -> dict:
    return {
        "version": "1.1",
        "nodes": [
            {
                "id": "extract_1",
                "type": "agent",
                "data": {
                    "agent_id": agent_id,
                    "validation_attachments": [
                        *[
                            _selection(attachment_id)
                            for attachment_id in sorted(
                                RETIRED_ALLELE_PENDING_VALIDATOR_ATTACHMENT_IDS
                            )
                        ],
                        {
                            "attachment_id": "agr.alliance.allele:binding:current",
                            "validator_binding_id": "current",
                            "enabled": False,
                        },
                    ],
                    "validation_groups": [],
                },
            }
        ],
        "edges": [],
        "entry_node_id": "extract_1",
        "user_extension": {"preserve": True},
    }


def test_removes_only_exact_retired_allele_selections_without_mutating_input():
    original = _definition()
    before = deepcopy(original)

    result = migrate_persisted_flow_definition(original)

    assert original == before
    assert result.applied_migrations == (
        RETIRED_ALLELE_PENDING_VALIDATOR_MIGRATION,
    )
    assert set(result.removed_attachment_ids) == (
        RETIRED_ALLELE_PENDING_VALIDATOR_ATTACHMENT_IDS
    )
    assert result.definition["nodes"][0]["data"]["validation_attachments"] == [
        {
            "attachment_id": "agr.alliance.allele:binding:current",
            "validator_binding_id": "current",
            "enabled": False,
        }
    ]
    assert result.definition["user_extension"] == {"preserve": True}


def test_migration_is_idempotent_and_does_not_strip_other_agents():
    first = migrate_persisted_flow_definition(_definition())
    second = migrate_persisted_flow_definition(first.definition)
    unrelated = migrate_persisted_flow_definition(
        _definition(agent_id="custom_extractor")
    )

    assert first.changed is True
    assert second.changed is False
    assert second.definition == first.definition
    assert unrelated.changed is False
    assert len(
        unrelated.definition["nodes"][0]["data"]["validation_attachments"]
    ) == 7


def test_migration_rejects_unexpected_binding_and_dependent_graph_references():
    unexpected = _definition()
    unexpected["nodes"][0]["data"]["validation_attachments"][0][
        "validator_binding_id"
    ] = "different-binding"
    with pytest.raises(PersistedFlowMigrationError, match="unexpected binding"):
        migrate_persisted_flow_definition(unexpected)

    grouped = _definition()
    grouped["nodes"][0]["data"]["validation_groups"] = [
        {
            "group_id": "retired",
            "binding_id": RETIRED_ALLELE_PENDING_VALIDATOR_BINDING_ID,
        }
    ]
    with pytest.raises(PersistedFlowMigrationError, match="validation group or edge"):
        migrate_persisted_flow_definition(grouped)

    edged = _definition()
    edged["edges"] = [
        {
            "id": "retired-edge",
            "satisfies_binding_id": RETIRED_ALLELE_PENDING_VALIDATOR_BINDING_ID,
        }
    ]
    with pytest.raises(PersistedFlowMigrationError, match="validation group or edge"):
        migrate_persisted_flow_definition(edged)
