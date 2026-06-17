"""Canonical DomainEnvelope normalization for persisted extraction results."""

from __future__ import annotations

from typing import Any, Mapping

from src.lib.curation_workspace.adapter_registry import load_curation_adapter_registry
from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
from src.schemas.curation_workspace import CurationExtractionResultRecord
from src.schemas.domain_envelope import (
    DomainEnvelope,
    DomainEnvelopeStatus,
    HistoryActorType,
    HistoryEvent,
    HistoryEventKind,
)
from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult


def domain_envelope_from_extraction_result(
    extraction_result: CurationExtractionResultRecord,
) -> DomainEnvelope:
    """Return the canonical downstream DomainEnvelope for an extraction result."""

    payload = extraction_result.payload_json
    if not isinstance(payload, Mapping):
        raise ValueError("extraction payload is not a JSON object")

    if _has_extractor_curatable_objects(payload) and _has_canonical_objects(payload):
        raise ValueError(
            "extraction payload mixes DomainEnvelope.objects[] with "
            "DomainEnvelopeExtractionResult.curatable_objects[]"
        )

    if is_canonical_domain_envelope_payload(payload):
        return DomainEnvelope.model_validate(payload)

    source = DomainEnvelopeExtractionResult.model_validate(payload)
    adapter_key = resolve_extraction_adapter_key(extraction_result)
    if adapter_key is None:
        raise ValueError("extraction result does not declare adapter ownership")

    registry = load_curation_adapter_registry()
    domain_pack = registry.get_domain_pack(adapter_key)
    if domain_pack is None:
        raise ValueError(
            f"adapter_key={adapter_key!r} does not declare a domain pack for envelope prep"
        )

    metadata = {
        "semantic_source": "domain_envelope.objects",
        "source_extraction_result_id": extraction_result.extraction_result_id,
        "source_agent_key": extraction_result.agent_key,
        "source_adapter_key": adapter_key,
        "source_kind": extraction_result.source_kind.value,
        "extraction_summary": source.summary,
        "extraction_metadata": source.metadata.model_dump(mode="json"),
        "run_summary": source.run_summary.model_dump(mode="json"),
    }

    return DomainEnvelope(
        envelope_id=extraction_envelope_id(extraction_result),
        domain_pack_id=domain_pack.pack_id,
        domain_pack_version=domain_pack.version,
        status=DomainEnvelopeStatus.EXTRACTED,
        schema_ref=source.schema_ref,
        objects=list(source.curatable_objects),
        history=[
            HistoryEvent(
                event_type=HistoryEventKind.CREATED,
                actor_type=HistoryActorType.SYSTEM,
                actor_id=CURATION_PREP_AGENT_ID,
                message=(
                    "Created persisted domain envelope from structured extraction result "
                    f"{extraction_result.extraction_result_id}."
                ),
            )
        ],
        metadata=metadata,
    )


def is_canonical_domain_envelope_payload(payload: Mapping[str, Any]) -> bool:
    """Return whether a payload is already the canonical downstream envelope shape."""

    return (
        bool(payload.get("envelope_id"))
        and bool(payload.get("domain_pack_id"))
        and _has_canonical_objects(payload)
        and not _has_extractor_curatable_objects(payload)
    )


def resolve_extraction_adapter_key(
    extraction_result: CurationExtractionResultRecord,
) -> str | None:
    """Return the non-empty adapter key for an extraction result, if present."""

    return normalized_optional_string(extraction_result.adapter_key)


def extraction_envelope_id(extraction_result: CurationExtractionResultRecord) -> str:
    """Return the stable DomainEnvelope id for an extraction result."""

    metadata = dict(extraction_result.metadata or {})
    envelope_id = normalized_optional_string(metadata.get("envelope_id"))
    if envelope_id is not None:
        return envelope_id
    return f"extraction-result:{extraction_result.extraction_result_id}"


def normalized_optional_string(value: Any) -> str | None:
    """Return a stripped non-empty string or None."""

    if value is None or not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _has_canonical_objects(payload: Mapping[str, Any]) -> bool:
    return isinstance(payload.get("objects"), list)


def _has_extractor_curatable_objects(payload: Mapping[str, Any]) -> bool:
    return isinstance(payload.get("curatable_objects"), list)


__all__ = [
    "domain_envelope_from_extraction_result",
    "extraction_envelope_id",
    "is_canonical_domain_envelope_payload",
    "normalized_optional_string",
    "resolve_extraction_adapter_key",
]
