"""Reference adapter scaffold for curation workspace candidates."""

from src.lib.curation_adapters.reference.field_layout import (
    REFERENCE_ADAPTER_KEY,
    REFERENCE_FIELD_DEFINITIONS,
    REFERENCE_FIELD_DEFINITIONS_BY_KEY,
    REFERENCE_TYPE_OPTIONS,
)
from src.lib.curation_adapters.reference.normalizer import (
    REFERENCE_LAYOUT_KEY,
    REFERENCE_PAYLOAD_BUILDER_KEY,
    REFERENCE_VALIDATION_PLAN_KEY,
    ReferenceCandidateNormalizer,
)

__all__ = [
    "REFERENCE_ADAPTER_KEY",
    "REFERENCE_FIELD_DEFINITIONS",
    "REFERENCE_FIELD_DEFINITIONS_BY_KEY",
    "REFERENCE_LAYOUT_KEY",
    "REFERENCE_PAYLOAD_BUILDER_KEY",
    "REFERENCE_TYPE_OPTIONS",
    "REFERENCE_VALIDATION_PLAN_KEY",
    "ReferenceCandidateNormalizer",
]
