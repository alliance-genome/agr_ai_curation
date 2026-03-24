"""Curation workspace persistence models.

Heavy runtime dependencies (``agents.Runner``, ``agents.RunConfig``) live in
``curation_prep_service`` and are **not** imported at package level.  Use an
explicit submodule import when you need ``run_curation_prep`` or
``CurationPrepPersistenceContext``.
"""

from .evidence_resolver import DeterministicEvidenceAnchorResolver
from .extraction_results import (
    ExtractionEnvelopeCandidate,
    build_extraction_envelope_candidate,
    build_safe_agent_key_map,
    list_extraction_results_for_origin_session,
    persist_extraction_result,
    persist_extraction_results,
    resolve_agent_key_from_tool_name,
)
from .models import (
    CurationActionLogEntry,
    CurationCandidate,
    CurationDraft,
    CurationEvidenceRecord,
    CurationExtractionResultRecord,
    CurationReviewSession,
    CurationSavedView,
    CurationSubmissionRecord,
    CurationValidationSnapshot,
)
from .pipeline import (
    DEFAULT_ASYNC_CANDIDATE_THRESHOLD,
    AsyncioPipelineTaskScheduler,
    DeterministicStructuralValidationService,
    PassthroughCandidateNormalizer,
    PassthroughEvidenceAnchorResolver,
    PipelineExecutionMode,
    PipelineRunStatus,
    PostCurationPipelineDependencies,
    PostCurationPipelineRequest,
    PostCurationPipelineResult,
    execute_post_curation_pipeline,
    run_post_curation_pipeline,
)

__all__ = [
    "CurationActionLogEntry",
    "CurationCandidate",
    "CurationPrepPersistenceContext",
    "CurationDraft",
    "CurationEvidenceRecord",
    "ExtractionEnvelopeCandidate",
    "DEFAULT_ASYNC_CANDIDATE_THRESHOLD",
    "AsyncioPipelineTaskScheduler",
    "build_extraction_envelope_candidate",
    "build_safe_agent_key_map",
    "DeterministicEvidenceAnchorResolver",
    "list_extraction_results_for_origin_session",
    "CurationExtractionResultRecord",
    "CurationReviewSession",
    "CurationSavedView",
    "CurationSubmissionRecord",
    "CurationValidationSnapshot",
    "DeterministicStructuralValidationService",
    "PassthroughCandidateNormalizer",
    "PassthroughEvidenceAnchorResolver",
    "PipelineExecutionMode",
    "PipelineRunStatus",
    "PostCurationPipelineDependencies",
    "PostCurationPipelineRequest",
    "PostCurationPipelineResult",
    "execute_post_curation_pipeline",
    "persist_extraction_result",
    "persist_extraction_results",
    "resolve_agent_key_from_tool_name",
    "run_curation_prep",
    "run_post_curation_pipeline",
]

# ---------------------------------------------------------------------------
# Lazy accessor – curation_prep_service depends on agents.Runner / RunConfig
# ---------------------------------------------------------------------------
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "CurationPrepPersistenceContext": (
        ".curation_prep_service",
        "CurationPrepPersistenceContext",
    ),
    "run_curation_prep": (".curation_prep_service", "run_curation_prep"),
}


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        module_path, attr = _LAZY_IMPORTS[name]
        import importlib

        mod = importlib.import_module(module_path, __package__)
        value = getattr(mod, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
