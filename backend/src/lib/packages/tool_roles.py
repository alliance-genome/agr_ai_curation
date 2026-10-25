"""Tool roles that package tool bindings declare in their metadata.

A package marks a tool's role with a metadata flag in its tool bindings, so the
platform derives these sets from the registry instead of hardcoded tool names:

- ``builder_finalization: true``: a builder-materializer finalize tool. An agent
  that carries one is an extraction agent.
- ``paper_source: true``: paper reading/search or evidence management; validators
  must use supplied context and cannot carry these tools.
- ``identity_lookup: true``: a tool that searches a database or service for an
  identity (terms, entities, references). Only validation and lookup agents may
  use these; extraction agents read the paper and never search for identities.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from typing import Any

BUILDER_FINALIZATION_METADATA_KEY = "builder_finalization"
IDENTITY_LOOKUP_METADATA_KEY = "identity_lookup"
# The saved output contract state of an agent that produces structured extraction.
STRUCTURED_EXTRACTION_OUTPUT_STATE = "structured_extraction"


@lru_cache(maxsize=16)
def tool_metadata_by_name() -> dict[str, dict[str, Any]]:
    """Return package-declared tool metadata keyed by tool ID."""

    from src.lib.packages.tool_registry import load_tool_registry

    registry = load_tool_registry()
    return {
        binding.tool_id: dict(binding.metadata)
        for binding in registry.bindings
        if isinstance(binding.metadata, dict)
    }


def _flagged_tool_names(metadata_key: str) -> frozenset[str]:
    return frozenset(
        tool_id
        for tool_id, metadata in tool_metadata_by_name().items()
        if metadata.get(metadata_key) is True
    )


@lru_cache(maxsize=1)
def builder_finalization_tool_names() -> frozenset[str]:
    """Tools whose binding metadata declares ``builder_finalization: true``."""

    return _flagged_tool_names(BUILDER_FINALIZATION_METADATA_KEY)


@lru_cache(maxsize=1)
def identity_lookup_tool_names() -> frozenset[str]:
    """Tools whose binding metadata declares ``identity_lookup: true``."""

    return _flagged_tool_names(IDENTITY_LOOKUP_METADATA_KEY)


def is_validator_output_schema(output_schema_key: str | None) -> bool:
    """Whether a saved output schema is a validator result (not an extraction envelope)."""

    if not output_schema_key:
        return False
    from src.lib.config.schema_discovery import resolve_output_schema
    from src.schemas.domain_validator import is_domain_validator_result_schema

    schema = resolve_output_schema(output_schema_key)
    return schema is not None and is_domain_validator_result_schema(schema)


def is_extraction_agent(
    tool_ids: Iterable[str],
    *,
    output_state: str | None = None,
    output_schema_key: str | None = None,
) -> bool:
    """An agent extracts when it carries a builder finalize tool, or its saved output contract
    is structured extraction that is not a validator result."""

    if set(tool_ids) & builder_finalization_tool_names():
        return True
    return output_state == STRUCTURED_EXTRACTION_OUTPUT_STATE and not is_validator_output_schema(
        output_schema_key
    )


def identity_lookup_tools_on_extraction_agent(
    tool_ids: Iterable[str],
    *,
    output_state: str | None = None,
    output_schema_key: str | None = None,
) -> tuple[str, ...]:
    """The identity-lookup tools an extraction agent carries (empty for any other agent)."""

    requested = list(dict.fromkeys(tool_ids))
    if not is_extraction_agent(requested, output_state=output_state, output_schema_key=output_schema_key):
        return ()
    lookups = identity_lookup_tool_names()
    return tuple(tool_id for tool_id in requested if tool_id in lookups)


def extraction_identity_lookup_message(agent_label: str, tool_ids: Iterable[str]) -> str:
    """Curator-facing reason an extraction agent cannot carry identity-lookup tools."""

    return (
        f"{agent_label} is an extraction agent, and extraction agents cannot use database "
        f"lookup tools ({', '.join(sorted(tool_ids))}). Extraction records the paper's "
        "wording; validators do the database search. Remove these tools to save or run it."
    )


def require_no_identity_lookup_on_extraction(
    tool_ids: Iterable[str],
    *,
    output_state: str | None = None,
    output_schema_key: str | None = None,
    agent_label: str,
) -> None:
    """Raise when an extraction agent carries identity-lookup tools."""

    forbidden = identity_lookup_tools_on_extraction_agent(
        tool_ids, output_state=output_state, output_schema_key=output_schema_key,
    )
    if forbidden:
        raise ValueError(extraction_identity_lookup_message(agent_label, forbidden))



def validator_forbidden_tool_names() -> frozenset[str]:
    """Package-declared source access and extraction tools, never validator tools."""
    return (_flagged_tool_names("paper_source")
            | builder_finalization_tool_names() | _flagged_tool_names("builder_run_state"))


def forbidden_tools_on_validator(tool_ids: Iterable[str], *, output_schema_key: str | None = None,
                                 validator_role: bool = False) -> tuple[str, ...]:
    if not validator_role and not is_validator_output_schema(output_schema_key):
        return ()
    forbidden = validator_forbidden_tool_names()
    return tuple(name for name in dict.fromkeys(tool_ids) if name in forbidden)


def validator_tool_message(agent_label: str, tool_ids: Iterable[str]) -> str:
    return (f"{agent_label} is a validator and cannot read papers or manage source evidence "
            f"({', '.join(sorted(tool_ids))}). Use supplied context and database/ontology "
            "lookups. Remove these tools to save or run it.")


class ValidatorToolPolicyError(ValueError):
    """Invalid validator configuration, not a provider/server failure."""


def require_validator_tool_boundary(tool_ids: Iterable[str], *, output_schema_key: str | None = None,
                                    validator_role: bool = False, agent_label: str) -> None:
    forbidden = forbidden_tools_on_validator(tool_ids, output_schema_key=output_schema_key,
                                            validator_role=validator_role)
    if forbidden:
        raise ValidatorToolPolicyError(validator_tool_message(agent_label, forbidden))


def require_supervisor_review_tool_boundary(tool_ids: Iterable[str]) -> None:
    """Reviewers inspect saved evidence; paper investigation belongs to extractors."""
    forbidden = set(tool_ids) & validator_forbidden_tool_names()
    if forbidden:
        raise ValueError(
            "Supervisor review cannot directly read papers, stage extraction or manage evidence: "
            + ", ".join(sorted(forbidden))
        )


def reset_cache() -> None:
    """Clear the registry-derived role caches (tests and package reloads)."""

    tool_metadata_by_name.cache_clear()
    builder_finalization_tool_names.cache_clear()
    identity_lookup_tool_names.cache_clear()


__all__ = [
    "BUILDER_FINALIZATION_METADATA_KEY",
    "IDENTITY_LOOKUP_METADATA_KEY",
    "STRUCTURED_EXTRACTION_OUTPUT_STATE",
    "builder_finalization_tool_names",
    "extraction_identity_lookup_message",
    "identity_lookup_tool_names",
    "identity_lookup_tools_on_extraction_agent",
    "is_extraction_agent",
    "is_validator_output_schema",
    "require_no_identity_lookup_on_extraction",
    "forbidden_tools_on_validator",
    "validator_forbidden_tool_names",
    "validator_tool_message",
    "ValidatorToolPolicyError",
    "require_validator_tool_boundary",
    "require_supervisor_review_tool_boundary",
    "reset_cache",
    "tool_metadata_by_name",
]
