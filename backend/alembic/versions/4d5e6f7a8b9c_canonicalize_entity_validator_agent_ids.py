"""Canonicalize Alliance entity-validator agent identities.

Revision ID: 4d5e6f7a8b9c
Revises: 3c4d5e6f7a8b
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "4d5e6f7a8b9c"
down_revision: str | Sequence[str] | None = "3c4d5e6f7a8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CANONICAL_AGENT_IDS = {
    "gene": "gene_validation",
    "allele": "allele_validation",
    "disease": "disease_validation",
    "chemical": "chemical_validation",
}


def _canonical_agent_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return _CANONICAL_AGENT_IDS.get(value)


def _rewrite_flow_definition(definition: Any) -> tuple[Any, bool]:
    """Rewrite only known agent-identity fields in one saved flow."""

    if not isinstance(definition, Mapping):
        return definition, False

    nodes = definition.get("nodes")
    if not isinstance(nodes, list):
        return definition, False

    changed = False
    rewritten_nodes: list[Any] = []
    for raw_node in nodes:
        if not isinstance(raw_node, Mapping):
            rewritten_nodes.append(raw_node)
            continue

        raw_data = raw_node.get("data")
        if not isinstance(raw_data, Mapping):
            rewritten_nodes.append(raw_node)
            continue

        rewritten_data = dict(raw_data)
        legacy_agent_id = raw_data.get("agent_id")
        canonical_agent_id = _canonical_agent_id(legacy_agent_id)
        if canonical_agent_id is not None:
            rewritten_data["agent_id"] = canonical_agent_id
            changed = True

        raw_attachments = raw_data.get("validation_attachments")
        if isinstance(raw_attachments, list):
            rewritten_attachments: list[Any] = []
            attachments_changed = False
            for raw_attachment in raw_attachments:
                if not isinstance(raw_attachment, Mapping):
                    rewritten_attachments.append(raw_attachment)
                    continue
                legacy_validator_id = raw_attachment.get("validator_agent_id")
                canonical_validator_id = _canonical_agent_id(legacy_validator_id)
                if canonical_validator_id is None:
                    rewritten_attachments.append(raw_attachment)
                    continue
                rewritten_attachment = dict(raw_attachment)
                rewritten_attachment["validator_agent_id"] = canonical_validator_id
                rewritten_attachments.append(rewritten_attachment)
                attachments_changed = True

            if attachments_changed:
                rewritten_data["validation_attachments"] = rewritten_attachments
                changed = True

        if rewritten_data == raw_data:
            rewritten_nodes.append(raw_node)
            continue
        rewritten_node = dict(raw_node)
        rewritten_node["data"] = rewritten_data
        rewritten_nodes.append(rewritten_node)

    if not changed:
        return definition, False

    rewritten_definition = dict(definition)
    rewritten_definition["nodes"] = rewritten_nodes
    return rewritten_definition, True


def _migrate(connection: sa.Connection) -> None:
    """Migrate saved flows and custom/project parent-agent references."""

    connection.execute(
        sa.text(
            "SET LOCAL application_name = "
            "'alembic:4d5e6f7a8b9c:validator-agent-identities'"
        )
    )

    flow_update = sa.text(
        """
        UPDATE curation_flows
        SET flow_definition = :flow_definition
        WHERE id = :flow_id
        """
    ).bindparams(sa.bindparam("flow_definition", type_=JSONB))
    flow_rows = connection.execute(
        sa.text("SELECT id, flow_definition FROM curation_flows")
    ).mappings().all()
    for row in flow_rows:
        rewritten, changed = _rewrite_flow_definition(row["flow_definition"])
        if changed:
            connection.execute(
                flow_update,
                {"flow_id": row["id"], "flow_definition": rewritten},
            )

    for retired_alias, canonical_id in _CANONICAL_AGENT_IDS.items():
        conflicting_prompts = connection.execute(
            sa.text(
                """
                SELECT
                    legacy.id AS legacy_id,
                    canonical.id AS canonical_id,
                    legacy.content = canonical.content AS content_matches,
                    legacy.is_active AS legacy_is_active,
                    legacy.prompt_type,
                    legacy.group_id,
                    legacy.version
                FROM prompt_templates AS legacy
                JOIN prompt_templates AS canonical
                  ON canonical.agent_name = :canonical_id
                 AND canonical.prompt_type = legacy.prompt_type
                 AND canonical.group_id IS NOT DISTINCT FROM legacy.group_id
                 AND canonical.version = legacy.version
                WHERE legacy.agent_name = :retired_alias
                """
            ),
            {
                "retired_alias": retired_alias,
                "canonical_id": canonical_id,
            },
        ).mappings().all()
        unsafe_conflicts = [
            row for row in conflicting_prompts if not row["content_matches"]
        ]
        if unsafe_conflicts:
            conflict = unsafe_conflicts[0]
            raise RuntimeError(
                "Cannot safely canonicalize validator prompt identity "
                f"'{retired_alias}' to '{canonical_id}': conflicting "
                f"{conflict['prompt_type']} prompt version "
                f"{conflict['version']} for group {conflict['group_id']!r}."
            )

        for duplicate in conflicting_prompts:
            if duplicate["legacy_is_active"]:
                canonical_active_exists = connection.execute(
                    sa.text(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM prompt_templates
                            WHERE agent_name = :canonical_id
                              AND prompt_type = :prompt_type
                              AND group_id IS NOT DISTINCT FROM :group_id
                              AND is_active = true
                        )
                        """
                    ),
                    {
                        "canonical_id": canonical_id,
                        "prompt_type": duplicate["prompt_type"],
                        "group_id": duplicate["group_id"],
                    },
                ).scalar_one()
                if not canonical_active_exists:
                    connection.execute(
                        sa.text(
                            """
                            UPDATE prompt_templates
                            SET is_active = true
                            WHERE id = :canonical_prompt_id
                            """
                        ),
                        {"canonical_prompt_id": duplicate["canonical_id"]},
                    )
            connection.execute(
                sa.text(
                    """
                    UPDATE prompt_execution_log
                    SET prompt_template_id = :canonical_prompt_id
                    WHERE prompt_template_id = :legacy_prompt_id
                    """
                ),
                {
                    "canonical_prompt_id": duplicate["canonical_id"],
                    "legacy_prompt_id": duplicate["legacy_id"],
                },
            )
            connection.execute(
                sa.text("DELETE FROM prompt_templates WHERE id = :legacy_prompt_id"),
                {"legacy_prompt_id": duplicate["legacy_id"]},
            )

        connection.execute(
            sa.text(
                """
                UPDATE prompt_templates AS legacy
                SET is_active = false
                WHERE legacy.agent_name = :retired_alias
                  AND legacy.is_active = true
                  AND EXISTS (
                      SELECT 1
                      FROM prompt_templates AS canonical
                      WHERE canonical.agent_name = :canonical_id
                        AND canonical.prompt_type = legacy.prompt_type
                        AND canonical.group_id IS NOT DISTINCT FROM legacy.group_id
                        AND canonical.is_active = true
                  )
                """
            ),
            {
                "retired_alias": retired_alias,
                "canonical_id": canonical_id,
            },
        )
        connection.execute(
            sa.text(
                """
                UPDATE prompt_templates
                SET agent_name = :canonical_id
                WHERE agent_name = :retired_alias
                """
            ),
            {
                "retired_alias": retired_alias,
                "canonical_id": canonical_id,
            },
        )

    connection.execute(
        sa.text(
            """
            UPDATE prompt_execution_log
            SET agent_name = CASE agent_name
                WHEN 'gene' THEN 'gene_validation'
                WHEN 'allele' THEN 'allele_validation'
                WHEN 'disease' THEN 'disease_validation'
                WHEN 'chemical' THEN 'chemical_validation'
                ELSE agent_name
            END
            WHERE agent_name IN ('gene', 'allele', 'disease', 'chemical')
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE file_outputs
            SET agent_name = CASE agent_name
                WHEN 'gene' THEN 'gene_validation'
                WHEN 'allele' THEN 'allele_validation'
                WHEN 'disease' THEN 'disease_validation'
                WHEN 'chemical' THEN 'chemical_validation'
                ELSE agent_name
            END
            WHERE agent_name IN ('gene', 'allele', 'disease', 'chemical')
            """
        )
    )

    connection.execute(
        sa.text(
            """
            UPDATE agents
            SET template_source = CASE template_source
                    WHEN 'gene' THEN 'gene_validation'
                    WHEN 'allele' THEN 'allele_validation'
                    WHEN 'disease' THEN 'disease_validation'
                    WHEN 'chemical' THEN 'chemical_validation'
                    ELSE template_source
                END,
                group_rules_component = CASE group_rules_component
                    WHEN 'gene' THEN 'gene_validation'
                    WHEN 'allele' THEN 'allele_validation'
                    WHEN 'disease' THEN 'disease_validation'
                    WHEN 'chemical' THEN 'chemical_validation'
                    ELSE group_rules_component
                END
            WHERE visibility IN ('private', 'project')
              AND (
                  template_source IN ('gene', 'allele', 'disease', 'chemical')
                  OR group_rules_component IN (
                      'gene', 'allele', 'disease', 'chemical'
                  )
              )
            """
        )
    )


def upgrade() -> None:
    _migrate(op.get_bind())


def downgrade() -> None:
    """Keep canonical identities; retired aliases are not restored."""
