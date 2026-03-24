"""Curation workspace persistence models."""

import importlib
from typing import Any

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

# ---------------------------------------------------------------------------
# Lazy accessors for heavy submodules (curation_prep_service, pipeline)
# ---------------------------------------------------------------------------
# These names were previously imported eagerly, pulling in the OpenAI Agents
# SDK (agents.Runner, agents.RunConfig, agents.Agent) on every
# ``import curation_workspace``.  They are now resolved on first access so
# that lightweight consumers (models, extraction_results, etc.) are not
# penalised.
# ---------------------------------------------------------------------------

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # curation_prep_service
    "CurationPrepPersistenceContext": (
        ".curation_prep_service",
        "CurationPrepPersistenceContext",
    ),
    "run_curation_prep": (".curation_prep_service", "run_curation_prep"),
    # pipeline
    "DEFAULT_ASYNC_CANDIDATE_THRESHOLD": (
        ".pipeline",
        "DEFAULT_ASYNC_CANDIDATE_THRESHOLD",
    ),
    "AsyncioPipelineTaskScheduler": (".pipeline", "AsyncioPipelineTaskScheduler"),
    "DeterministicStructuralValidationService": (
        ".pipeline",
        "DeterministicStructuralValidationService",
    ),
    "PassthroughCandidateNormalizer": (
        ".pipeline",
        "PassthroughCandidateNormalizer",
    ),
    "PassthroughEvidenceAnchorResolver": (
        ".pipeline",
        "PassthroughEvidenceAnchorResolver",
    ),
    "PipelineExecutionMode": (".pipeline", "PipelineExecutionMode"),
    "PipelineRunStatus": (".pipeline", "PipelineRunStatus"),
    "PostCurationPipelineDependencies": (
        ".pipeline",
        "PostCurationPipelineDependencies",
    ),
    "PostCurationPipelineRequest": (".pipeline", "PostCurationPipelineRequest"),
    "PostCurationPipelineResult": (".pipeline", "PostCurationPipelineResult"),
    "execute_post_curation_pipeline": (
        ".pipeline",
        "execute_post_curation_pipeline",
    ),
    "run_post_curation_pipeline": (".pipeline", "run_post_curation_pipeline"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_IMPORTS:
        module_path, attr = _LAZY_IMPORTS[name]
        mod = importlib.import_module(module_path, __package__)
        val = getattr(mod, attr)
        globals()[name] = val  # cache for subsequent access
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
