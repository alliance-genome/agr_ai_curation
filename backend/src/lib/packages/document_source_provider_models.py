"""Neutral contracts for package-owned document-source providers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.lib.document_sources.models import DocumentSourceProvider


_PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_CAPABILITY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class DocumentSourceProviderPresentation:
    """Non-secret provider presentation metadata exposed to generic consumers."""

    display_label: str
    reference_label_priority: tuple[str, ...] = ()
    identifier_help_label: str = ""
    identifier_examples: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        display_label = self.display_label.strip()
        if not display_label:
            raise ValueError("document-source provider display_label must not be empty")
        priorities = tuple(item.strip() for item in self.reference_label_priority)
        if any(not item for item in priorities):
            raise ValueError(
                "document-source reference_label_priority entries must not be empty"
            )
        if len(set(priorities)) != len(priorities):
            raise ValueError(
                "document-source reference_label_priority entries must be unique"
            )
        identifier_help_label = self.identifier_help_label.strip()
        identifier_examples = tuple(item.strip() for item in self.identifier_examples)
        if any(not item for item in identifier_examples):
            raise ValueError(
                "document-source identifier_examples entries must not be empty"
            )
        if len(set(identifier_examples)) != len(identifier_examples):
            raise ValueError(
                "document-source identifier_examples entries must be unique"
            )
        if bool(identifier_help_label) != bool(identifier_examples):
            raise ValueError(
                "document-source identifier help label and examples must be configured together"
            )
        object.__setattr__(self, "display_label", display_label)
        object.__setattr__(self, "reference_label_priority", priorities)
        object.__setattr__(self, "identifier_help_label", identifier_help_label)
        object.__setattr__(self, "identifier_examples", identifier_examples)

    def as_public_dict(self) -> dict[str, Any]:
        """Return the stable provider metadata shape used by API provenance."""

        metadata: dict[str, Any] = {
            "display_label": self.display_label,
            "reference_label_priority": list(self.reference_label_priority),
        }
        if self.identifier_help_label:
            metadata["identifier_help_label"] = self.identifier_help_label
            metadata["identifier_examples"] = list(self.identifier_examples)
        return metadata


@dataclass(frozen=True, slots=True)
class DocumentSourceProviderRegistration:
    """One package-owned external document-source provider registration."""

    provider_id: str
    factory: Callable[[], "DocumentSourceProvider"]
    presentation: DocumentSourceProviderPresentation
    capabilities: Mapping[str, bool] = field(default_factory=dict)
    development_token_resolver: Callable[[], str | None] | None = None

    def __post_init__(self) -> None:
        provider_id = self.provider_id.strip()
        if not _PROVIDER_ID_PATTERN.fullmatch(provider_id):
            raise ValueError(
                f"document-source provider_id {provider_id!r} must start with a "
                "lowercase letter or digit and contain only lowercase letters, "
                "digits, dots, underscores, or hyphens"
            )
        if not callable(self.factory):
            raise ValueError(
                f"document-source provider '{provider_id}' factory must be callable"
            )
        if not isinstance(self.presentation, DocumentSourceProviderPresentation):
            raise ValueError(
                f"document-source provider '{provider_id}' presentation must use "
                "DocumentSourceProviderPresentation"
            )
        if self.development_token_resolver is not None and not callable(
            self.development_token_resolver
        ):
            raise ValueError(
                f"document-source provider '{provider_id}' "
                "development_token_resolver must be callable"
            )

        capabilities = dict(self.capabilities)
        for capability_id, enabled in capabilities.items():
            if not isinstance(capability_id, str) or not _CAPABILITY_ID_PATTERN.fullmatch(
                capability_id
            ):
                raise ValueError(
                    f"document-source provider '{provider_id}' capability IDs must "
                    "use lowercase snake_case"
                )
            if not isinstance(enabled, bool):
                raise ValueError(
                    f"document-source provider '{provider_id}' capability "
                    f"'{capability_id}' must be boolean"
                )

        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "capabilities", MappingProxyType(capabilities))
