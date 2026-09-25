"""Extracted vs validated values for generic classes (ALL-1283).

A class's resolvable values are the ones its source domain pack declares with
a ``mention`` display role (``src.lib.domain_packs.resolvable_values``). The
extractor stages only their paper wording (``mention``); the identity keys and
resolution state belong to the builder and the validators. Fields a validator
binding writes back, and the evidence location fields copied from the verified
evidence record, are never written by the extractor either.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.lib.domain_packs.resolvable_values import (
    CONTRACT_KEYS,
    MENTION_KEY,
    ResolvableSpec,
    unresolved_value,
)
from src.schemas.domain_envelope import parse_field_path


# Payload fields copied from the candidate's first verified evidence record, and
# the evidence record field each one reads.
EVIDENCE_SOURCE_FIELDS = {
    "evidence_record_id": "evidence_record_id",
    "verified_quote": "verified_quote",
    "page": "page",
    "section": "section",
    "subsection": "subsection",
    "chunk_id": "chunk_id",
    "figure_reference": "figure_reference",
}

# Resolution keys only the builder and validators write.
_BUILDER_OWNED_CONTRACT_KEYS = tuple(key for key in CONTRACT_KEYS if key != MENTION_KEY)


def builder_owned_keys(spec: ResolvableSpec) -> tuple[str, ...]:
    """The keys of a resolvable value the extractor never writes."""

    return (*spec.identity_keys, *_BUILDER_OWNED_CONTRACT_KEYS)


def _values_at(payload: Any, tokens: tuple[str | int, ...]) -> list[Any]:
    """Every value stored at a declared path; list fields are read element by element."""

    if isinstance(payload, list):
        return [value for item in payload for value in _values_at(item, tokens)]
    if not tokens:
        return [payload]
    token, rest = tokens[0], tokens[1:]
    if isinstance(token, int):
        return []
    if not isinstance(payload, Mapping) or token not in payload:
        return []
    return _values_at(payload[token], rest)


def _declared_values(payload: Mapping[str, Any], field_path: str) -> list[Any]:
    return _values_at(payload, parse_field_path(field_path) if field_path else ())


def extractor_payload_issues(
    payload: Mapping[str, Any],
    *,
    resolvable_fields: Mapping[str, ResolvableSpec],
    validator_owned_fields: tuple[str, ...],
    payload_fields: tuple[str, ...],
) -> list[dict[str, str]]:
    """Problems with a payload the extractor staged for one class.

    The extractor may not write validator-owned fields, the evidence location
    fields, or a resolvable value's identity and resolution keys. Every
    resolvable value it stages carries its paper wording.
    """

    issues: list[dict[str, str]] = []
    for field_path in validator_owned_fields:
        if any(value is not None for value in _declared_values(payload, field_path)):
            issues.append({
                "field_path": f"payload.{field_path}",
                "reason": "validator_owned_field",
                "message": (
                    f"{field_path} is filled in by validation; stage the paper wording "
                    "and any proposed values in their own fields instead."
                ),
            })
    for field_path in EVIDENCE_SOURCE_FIELDS:
        if field_path in payload_fields and field_path in payload:
            issues.append({
                "field_path": f"payload.{field_path}",
                "reason": "evidence_owned_field",
                "message": (
                    f"{field_path} is copied from the verified evidence record; "
                    "do not type it."
                ),
            })
    for field_path, spec in resolvable_fields.items():
        for value in _declared_values(payload, field_path):
            path = f"payload.{field_path}" if field_path else "payload"
            if not isinstance(value, Mapping):
                issues.append({
                    "field_path": path,
                    "reason": "invalid_resolvable_value",
                    "message": f"Stage this value as an object with its paper wording in {spec.mention_key}.",
                })
                continue
            owned = sorted(key for key in builder_owned_keys(spec) if key in value)
            if owned:
                issues.append({
                    "field_path": path,
                    "reason": "builder_owned_field",
                    "message": (
                        f"{', '.join(owned)} are filled in by validation; stage only the "
                        f"paper wording in {spec.mention_key}."
                    ),
                })
            mention = value.get(spec.mention_key)
            if field_path and not (isinstance(mention, str) and mention.strip()):
                issues.append({
                    "field_path": f"{path}.{spec.mention_key}",
                    "reason": "missing_paper_wording",
                    "message": "A staged value needs the paper's wording for it.",
                })
    return issues


def _staged_value(value: Mapping[str, Any], spec: ResolvableSpec) -> dict[str, Any]:
    mention = value.get(spec.mention_key)
    extra = {key: item for key, item in value.items() if key != spec.mention_key}
    return unresolved_value(mention, identity_keys=spec.identity_keys, **extra)


def _expand_at(node: Any, tokens: tuple[str | int, ...], spec: ResolvableSpec) -> Any:
    if isinstance(node, list):
        return [_expand_at(item, tokens, spec) for item in node]
    if not isinstance(node, Mapping):
        return node
    if not tokens:
        mention = node.get(spec.mention_key)
        if not (isinstance(mention, str) and mention.strip()):
            return dict(node)
        return _staged_value(node, spec)
    token = tokens[0]
    if isinstance(token, int) or token not in node:
        return dict(node)
    return {**node, token: _expand_at(node[token], tokens[1:], spec)}


def unresolved_payload(
    payload: Mapping[str, Any],
    *,
    resolvable_fields: Mapping[str, ResolvableSpec],
) -> dict[str, Any]:
    """The payload with every staged resolvable value unresolved and not yet validated.

    Only values that carry paper wording are staged; their identity keys are
    written empty until a validator resolves them.
    """

    result: Any = dict(payload)
    for field_path, spec in sorted(resolvable_fields.items(), key=lambda item: len(item[0])):
        tokens = parse_field_path(field_path) if field_path else ()
        result = _expand_at(result, tokens, spec)
    return result


__all__ = [
    "EVIDENCE_SOURCE_FIELDS",
    "builder_owned_keys",
    "extractor_payload_issues",
    "unresolved_payload",
]
