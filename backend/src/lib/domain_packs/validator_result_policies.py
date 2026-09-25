"""Shared policy checks for package validator results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.schemas.domain_validator import (
    DomainValidationRequest,
    DomainValidatorResultBase,
)

from .value_presence import missing_resolved_value


@dataclass(frozen=True)
class ValidatorResultPolicyViolation:
    """One expected-result field that violates request-scoped result policy."""

    field_name: str
    message: str


# Keys that carry a term identifier in a validator result (the generic id keys
# of value_display); only these are compared with an allowed-term list.
KNOWN_IDENTIFIER_KEYS = frozenset({"curie", "id", "identifier"})


def _is_identifier_field(
    field_name: str,
    field_path: Any,
) -> bool:
    """Whether an expected-result field carries an identifier (its own name or its write leaf)."""

    if field_name in KNOWN_IDENTIFIER_KEYS:
        return True
    if not isinstance(field_path, str):
        return False
    leaf = field_path.strip().rsplit(".", 1)[-1].split("[", 1)[0]
    return leaf in KNOWN_IDENTIFIER_KEYS


def allowed_term_policy_violations(
    result: DomainValidatorResultBase,
    *,
    request: DomainValidationRequest,
) -> list[ValidatorResultPolicyViolation]:
    """Return field violations for request-scoped ontology allowlists.

    Structured term values are checked by their own curie/id, and a list of
    scalars is a list of proposed identifiers. A single scalar is checked
    only when it carries an identifier: its result-field name or its write
    leaf (e.g. ``terms[2].curie``) is an identifier key. Labels (e.g. a
    term's ``name`` written per element) are never compared with the
    allowed list.
    """

    if result.status != "resolved":
        return []

    allowed_curies = _string_set(request.selected_inputs.get("allowed_term_curies"))
    unresolved_labels = _normalized_string_set(
        request.selected_inputs.get("unresolved_allowed_term_labels")
    )
    if not allowed_curies and not unresolved_labels:
        return []

    violations: list[ValidatorResultPolicyViolation] = []
    for field_name, field_path in request.expected_result_fields.items():
        resolved_value = result.resolved_values.get(field_name)
        if missing_resolved_value(resolved_value):
            continue

        if not isinstance(resolved_value, (list, dict)) and not _is_identifier_field(
            field_name, field_path
        ):
            # A single scalar that is not an identifier (e.g. a term's name).
            continue
        values = resolved_value if isinstance(resolved_value, list) else [resolved_value]
        invalid_terms: list[str] = []
        for item in values:
            curie = _term_curie(item)
            label = _term_label(item)
            normalized_label = _normalize_string(label)
            if normalized_label is not None and normalized_label in unresolved_labels:
                invalid_terms.append(
                    f"{label or '<unlabeled term>'} "
                    "(schema-allowed label lacks authoritative lookup path)"
                )
                continue
            if curie is None:
                if allowed_curies:
                    invalid_terms.append(label or "<missing curie>")
                continue
            if allowed_curies and curie not in allowed_curies:
                invalid_terms.append(curie)

        if invalid_terms:
            violations.append(
                ValidatorResultPolicyViolation(
                    field_name=field_name,
                    message=(
                        "Validator result included value(s) outside the "
                        f"field-specific allowed term list for {field_name}: "
                        + ", ".join(invalid_terms)
                    ),
                )
            )

    return violations


def _term_curie(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text if ":" in text else None
    if isinstance(value, dict):
        curie = value.get("curie") or value.get("id")
        if isinstance(curie, str) and curie.strip():
            return curie.strip()
    return None


def _term_label(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict):
        for key in ("name", "label"):
            label = value.get(key)
            if isinstance(label, str) and label.strip():
                return label.strip()
    return None


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item.strip() for item in value if isinstance(item, str) and item.strip()}


def _normalized_string_set(value: Any) -> set[str]:
    return {
        normalized
        for item in _string_set(value)
        if (normalized := _normalize_string(item)) is not None
    }


def _normalize_string(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.strip().casefold().split())
    return text or None


__all__ = [
    "KNOWN_IDENTIFIER_KEYS",
    "ValidatorResultPolicyViolation",
    "allowed_term_policy_violations",
]
