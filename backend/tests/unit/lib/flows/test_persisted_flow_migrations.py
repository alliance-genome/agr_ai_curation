"""Tests for forward-only migrations of saved flow definitions."""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.lib.flows.persisted_flow_migrations import (
    PersistedFlowMigrationError,
    migrate_persisted_flow_definition,
)
from src.lib.packages.persisted_flow_migration_loader import (
    PersistedFlowMigration,
    RetiredFlowAttachment,
)


MIGRATION_ID = "2026-09-03.remove-example-validator"
BINDING_ID = "retired_example_validator"
ATTACHMENT_IDS = frozenset(
    {
        "org.example.records:binding:retired_example_validator:object:Record:*",
        "org.example.records:metadata:retired_example_validator:pack:*:*",
    }
)
MIGRATION = PersistedFlowMigration(
    migration_id=MIGRATION_ID,
    agent_id="record_extractor",
    retired_binding_id=BINDING_ID,
    retired_attachments=tuple(
        RetiredFlowAttachment(
            attachment_id=attachment_id,
            validator_binding_id=None if ":metadata:" in attachment_id else BINDING_ID,
        )
        for attachment_id in sorted(ATTACHMENT_IDS)
    ),
)


def _selection(attachment_id: str) -> dict:
    metadata_only = ":metadata:" in attachment_id
    return {
        "attachment_id": attachment_id,
        "domain_pack_id": "org.example.records",
        "validator_id": "org.example:record_validation",
        "validator_binding_id": (None if metadata_only else BINDING_ID),
        "state": "under_development",
        "scope": "pack" if metadata_only else "object",
        "enabled": False,
    }


def _definition(*, agent_id: str = "record_extractor") -> dict:
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
                            for attachment_id in sorted(ATTACHMENT_IDS)
                        ],
                        {
                            "attachment_id": "org.example.records:binding:current",
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

    result = migrate_persisted_flow_definition(original, migrations=(MIGRATION,))

    assert original == before
    assert result.applied_migrations == (MIGRATION_ID,)
    assert set(result.removed_attachment_ids) == (ATTACHMENT_IDS)
    assert result.definition["nodes"][0]["data"]["validation_attachments"] == [
        {
            "attachment_id": "org.example.records:binding:current",
            "validator_binding_id": "current",
            "enabled": False,
        }
    ]
    assert result.definition["user_extension"] == {"preserve": True}


def test_migration_is_idempotent_and_does_not_strip_other_agents():
    first = migrate_persisted_flow_definition(_definition(), migrations=(MIGRATION,))
    second = migrate_persisted_flow_definition(
        first.definition,
        migrations=(MIGRATION,),
    )
    unrelated = migrate_persisted_flow_definition(
        _definition(agent_id="custom_extractor"),
        migrations=(MIGRATION,),
    )

    assert first.changed is True
    assert second.changed is False
    assert second.definition == first.definition
    assert unrelated.changed is False
    assert len(unrelated.definition["nodes"][0]["data"]["validation_attachments"]) == 3


def test_migration_rejects_unexpected_binding_and_dependent_graph_references():
    unexpected = _definition()
    unexpected["nodes"][0]["data"]["validation_attachments"][0][
        "validator_binding_id"
    ] = "different-binding"
    with pytest.raises(PersistedFlowMigrationError, match="unexpected binding"):
        migrate_persisted_flow_definition(unexpected, migrations=(MIGRATION,))

    grouped = _definition()
    grouped["nodes"][0]["data"]["validation_groups"] = [
        {
            "group_id": "retired",
            "binding_id": BINDING_ID,
        }
    ]
    with pytest.raises(PersistedFlowMigrationError, match="validation group or edge"):
        migrate_persisted_flow_definition(grouped, migrations=(MIGRATION,))

    edged = _definition()
    edged["edges"] = [
        {
            "id": "retired-edge",
            "satisfies_binding_id": BINDING_ID,
        }
    ]
    with pytest.raises(PersistedFlowMigrationError, match="validation group or edge"):
        migrate_persisted_flow_definition(edged, migrations=(MIGRATION,))
