"""Package-driven registry for curation workspace adapters."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from src.lib.packages import (
    load_package_curation_adapter_exports,
    load_package_registry,
)
from src.lib.packages.paths import get_runtime_packages_dir


class CurationAdapterRegistry:
    """Simple in-memory registry keyed by adapter_key."""

    def __init__(self) -> None:
        self._candidate_normalizers: dict[str, Any] = {}
        self._export_adapters: dict[str, Any] = {}
        self._submission_transport_adapters: list[Any] = []
        self._domain_packs: dict[str, Any] = {}
        self._domain_packs_by_id: dict[str, Any] = {}
        self._domain_envelope_validators_by_pack_id: dict[str, Any] = {}
        self._review_row_materializers: dict[str, Any] = {}
        self._review_row_materializers_by_domain_pack: dict[str, Any] = {}

    def register_adapter(
        self,
        *,
        adapter_key: str,
        candidate_normalizer: Any,
        export_adapter: Any | None = None,
        submission_transport_adapters: Any | None = None,
        domain_pack: Any | None = None,
        domain_envelope_validator: Any | None = None,
        review_row_materializer: Any | None = None,
    ) -> None:
        normalized_key = str(adapter_key).strip()
        if not normalized_key:
            raise ValueError("adapter_key must not be blank")

        existing_normalizer = self._candidate_normalizers.get(normalized_key)
        if existing_normalizer is not None and existing_normalizer is not candidate_normalizer:
            raise ValueError(f"Curation adapter '{normalized_key}' is already registered")
        self._candidate_normalizers[normalized_key] = candidate_normalizer

        if export_adapter is not None:
            existing_export_adapter = self._export_adapters.get(normalized_key)
            if existing_export_adapter is not None and existing_export_adapter is not export_adapter:
                raise ValueError(f"Curation export adapter '{normalized_key}' is already registered")
            self._export_adapters[normalized_key] = export_adapter

        for submission_adapter in _normalize_submission_transport_adapters(
            submission_transport_adapters,
        ):
            if submission_adapter not in self._submission_transport_adapters:
                self._submission_transport_adapters.append(submission_adapter)

        if domain_pack is not None:
            existing_domain_pack = self._domain_packs.get(normalized_key)
            if existing_domain_pack is not None and existing_domain_pack is not domain_pack:
                raise ValueError(f"Curation domain pack for '{normalized_key}' is already registered")
            self._domain_packs[normalized_key] = domain_pack

            domain_pack_id = _domain_pack_id(domain_pack)
            if domain_pack_id is not None:
                existing_domain_pack_by_id = self._domain_packs_by_id.get(domain_pack_id)
                if (
                    existing_domain_pack_by_id is not None
                    and existing_domain_pack_by_id is not domain_pack
                ):
                    raise ValueError(
                        f"Curation domain pack id '{domain_pack_id}' is already registered"
                    )
                self._domain_packs_by_id[domain_pack_id] = domain_pack

        if domain_envelope_validator is not None:
            if not callable(domain_envelope_validator):
                raise ValueError("domain_envelope_validator must be callable")
            domain_pack_id = _domain_pack_id(domain_pack)
            if domain_pack_id is None:
                raise ValueError(
                    "domain_envelope_validator requires a registered domain_pack"
                )
            existing_validator = self._domain_envelope_validators_by_pack_id.get(
                domain_pack_id
            )
            if (
                existing_validator is not None
                and existing_validator is not domain_envelope_validator
            ):
                raise ValueError(
                    "Curation domain-envelope validator for domain pack "
                    f"'{domain_pack_id}' is already registered"
                )
            self._domain_envelope_validators_by_pack_id[domain_pack_id] = (
                domain_envelope_validator
            )

        if review_row_materializer is not None:
            existing_materializer = self._review_row_materializers.get(normalized_key)
            if (
                existing_materializer is not None
                and existing_materializer is not review_row_materializer
            ):
                raise ValueError(
                    f"Curation review-row materializer for '{normalized_key}' is already registered"
                )
            self._review_row_materializers[normalized_key] = review_row_materializer

            domain_pack_id = _domain_pack_id(domain_pack)
            if domain_pack_id is not None:
                existing_domain_materializer = (
                    self._review_row_materializers_by_domain_pack.get(domain_pack_id)
                )
                if (
                    existing_domain_materializer is not None
                    and existing_domain_materializer is not review_row_materializer
                ):
                    raise ValueError(
                        "Curation review-row materializer for domain pack "
                        f"'{domain_pack_id}' is already registered"
                    )
                self._review_row_materializers_by_domain_pack[domain_pack_id] = (
                    review_row_materializer
                )

    def get_candidate_normalizer(self, adapter_key: str) -> Any | None:
        return self._candidate_normalizers.get(str(adapter_key).strip())

    def require_candidate_normalizer(self, adapter_key: str) -> Any:
        normalizer = self.get_candidate_normalizer(adapter_key)
        if normalizer is None:
            known_keys = ", ".join(sorted(self._candidate_normalizers))
            raise KeyError(
                f"Unknown curation adapter '{adapter_key}'. Registered adapters: {known_keys}"
            )
        return normalizer

    def candidate_normalizers(self) -> dict[str, Any]:
        return dict(self._candidate_normalizers)

    def get_domain_pack(self, adapter_key: str) -> Any | None:
        return self._domain_packs.get(str(adapter_key).strip())

    def get_domain_pack_by_id(self, domain_pack_id: str) -> Any | None:
        return self._domain_packs_by_id.get(str(domain_pack_id).strip())

    def get_domain_envelope_validator_by_id(self, domain_pack_id: str) -> Any | None:
        return self._domain_envelope_validators_by_pack_id.get(
            str(domain_pack_id).strip()
        )

    def get_review_row_materializer(self, adapter_key: str) -> Any | None:
        return self._review_row_materializers.get(str(adapter_key).strip())

    def get_review_row_materializer_for_domain_pack(self, domain_pack_id: str) -> Any | None:
        return self._review_row_materializers_by_domain_pack.get(str(domain_pack_id).strip())

    def export_adapters(self) -> tuple[Any, ...]:
        return tuple(
            self._export_adapters[adapter_key]
            for adapter_key in sorted(self._export_adapters)
        )

    def submission_transport_adapters(self) -> tuple[Any, ...]:
        return tuple(self._submission_transport_adapters)

    def adapter_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._candidate_normalizers))


def _normalize_submission_transport_adapters(value: Any | None) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        normalized = tuple(value)
        if any(item is None for item in normalized):
            raise ValueError("submission_transport_adapters must not contain None")
        return normalized
    return (value,)


def _domain_pack_id(domain_pack: Any | None) -> str | None:
    if domain_pack is None:
        return None
    pack_id = getattr(domain_pack, "pack_id", None)
    if isinstance(pack_id, str) and pack_id.strip():
        return pack_id.strip()
    metadata = getattr(domain_pack, "metadata", None)
    metadata_pack_id = getattr(metadata, "pack_id", None)
    if isinstance(metadata_pack_id, str) and metadata_pack_id.strip():
        return metadata_pack_id.strip()
    return None


def build_curation_adapter_registry() -> CurationAdapterRegistry:
    """Build the adapter registry from package-owned exports."""

    package_registry = load_package_registry(packages_dir=_default_packages_dir())
    registry = CurationAdapterRegistry()

    for package in package_registry.loaded_packages:
        for export in load_package_curation_adapter_exports(package):
            export.register_hook(registry)

    return registry


@lru_cache(maxsize=1)
def load_curation_adapter_registry() -> CurationAdapterRegistry:
    """Return a cached package-driven curation adapter registry."""

    return build_curation_adapter_registry()


def resolve_curation_domain_pack_by_id(domain_pack_id: str) -> Any | None:
    """Resolve a domain pack from runtime packs or package-owned adapter exports."""

    normalized_id = str(domain_pack_id).strip()
    if not normalized_id:
        return None

    from src.lib.domain_packs.registry import load_domain_pack_registry

    domain_pack = load_domain_pack_registry().get_pack(normalized_id)
    if domain_pack is not None:
        return domain_pack

    return load_curation_adapter_registry().get_domain_pack_by_id(normalized_id)


def resolve_curation_domain_envelope_validator_by_id(domain_pack_id: str) -> Any | None:
    """Resolve a package-owned deterministic envelope validator by domain pack ID."""

    normalized_id = str(domain_pack_id).strip()
    if not normalized_id:
        return None
    return load_curation_adapter_registry().get_domain_envelope_validator_by_id(
        normalized_id
    )


def _default_packages_dir() -> Path:
    runtime_packages_dir = get_runtime_packages_dir()
    if runtime_packages_dir.exists():
        return runtime_packages_dir

    current = Path(__file__).resolve()
    for candidate in (current.parent, *current.parents):
        if (candidate / "packages").is_dir() and (candidate / "backend").is_dir():
            return candidate / "packages"
        if (candidate / "packages").is_dir() and (candidate / "config" / "agents").is_dir():
            return candidate / "packages"

    return runtime_packages_dir
