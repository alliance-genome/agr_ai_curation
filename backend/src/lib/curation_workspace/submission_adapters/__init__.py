"""Public submission-transport surface for curation workspace direct submit flows."""

from src.lib.curation_workspace.submission_adapters.base import (
    DIRECT_SUBMISSION_RESULT_STATUSES,
    SubmissionTransportAdapter,
    SubmissionTransportError,
    SubmissionTransportResult,
    coerce_submission_transport_result,
    normalize_submission_transport_result,
)
from src.lib.curation_workspace.submission_adapters.noop import (
    DEFAULT_NOOP_SUBMISSION_TARGET_KEY,
    NoOpSubmissionAdapter,
)
from src.lib.curation_workspace.submission_adapters.registry import (
    SubmissionAdapterRegistry,
    build_default_submission_adapter_registry,
)

__all__ = [
    "DEFAULT_NOOP_SUBMISSION_TARGET_KEY",
    "DIRECT_SUBMISSION_RESULT_STATUSES",
    "NoOpSubmissionAdapter",
    "SubmissionAdapterRegistry",
    "SubmissionTransportAdapter",
    "SubmissionTransportError",
    "SubmissionTransportResult",
    "build_default_submission_adapter_registry",
    "coerce_submission_transport_result",
    "normalize_submission_transport_result",
]
