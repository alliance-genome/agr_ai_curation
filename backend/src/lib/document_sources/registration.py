"""Provider-neutral document-source registration contracts."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .models import DocumentSourceProvider


_PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

DocumentSourceProviderFactory = Callable[[], DocumentSourceProvider]
DevelopmentTokenResolver = Callable[[], str | None]


@dataclass(frozen=True, slots=True)
class DocumentSourceProviderPresentation:
    """Non-secret labels used to present provider-backed provenance."""

    display_label: str
    reference_label_priority: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        display_label = self.display_label.strip()
        if not display_label:
            raise ValueError("display_label must not be empty")

        priorities = tuple(
            str(priority).strip() for priority in self.reference_label_priority
        )
        if any(not priority for priority in priorities):
            raise ValueError("reference_label_priority entries must not be empty")
        if len(set(priorities)) != len(priorities):
            raise ValueError("reference_label_priority entries must be unique")

        object.__setattr__(self, "display_label", display_label)
        object.__setattr__(self, "reference_label_priority", priorities)


@dataclass(frozen=True, slots=True)
class DocumentSourceProviderRegistration:
    """One package-owned external document-source provider definition."""

    provider_id: str
    factory: DocumentSourceProviderFactory
    development_token_resolver: DevelopmentTokenResolver | None = None
    presentation: DocumentSourceProviderPresentation | None = None
    capabilities: Mapping[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        provider_id = self.provider_id.strip()
        if not _PROVIDER_ID_PATTERN.fullmatch(provider_id):
            raise ValueError(
                "provider_id must start with a lowercase letter or digit and only "
                "use lowercase letters, digits, underscores, or hyphens"
            )
        if not callable(self.factory):
            raise ValueError("factory must be callable")
        if self.development_token_resolver is not None and not callable(
            self.development_token_resolver
        ):
            raise ValueError("development_token_resolver must be callable when set")
        if self.presentation is not None and not isinstance(
            self.presentation,
            DocumentSourceProviderPresentation,
        ):
            raise ValueError(
                "presentation must be a DocumentSourceProviderPresentation when set"
            )

        capabilities: dict[str, bool] = {}
        for raw_name, enabled in self.capabilities.items():
            name = str(raw_name).strip()
            if not name:
                raise ValueError("capability names must not be empty")
            if not isinstance(enabled, bool):
                raise ValueError(f"capability '{name}' must be boolean")
            capabilities[name] = enabled

        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(
            self,
            "capabilities",
            MappingProxyType(dict(sorted(capabilities.items()))),
        )
