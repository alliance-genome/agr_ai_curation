"""Service layer for curation prep execution orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sqlalchemy.orm import Session

from src.lib.curation_workspace.curation_prep_constants import (
    CURATION_PREP_AGENT_ID,
    CURATION_PREP_UNAVAILABLE_MESSAGE,
)
from src.schemas.curation_prep import (
    CurationPrepAgentOutput,
    CurationPrepScopeConfirmation,
)
from src.schemas.curation_workspace import (
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
)


@dataclass(frozen=True)
class CurationPrepPersistenceContext:
    """Optional persistence metadata overrides for prep execution."""

    document_id: str | None = None
    source_kind: CurationExtractionSourceKind | None = None
    origin_session_id: str | None = None
    trace_id: str | None = None
    flow_run_id: str | None = None
    user_id: str | None = None
    conversation_summary: str | None = None


async def run_curation_prep(
    extraction_results: Sequence[CurationExtractionResultRecord],
    *,
    scope_confirmation: CurationPrepScopeConfirmation,
    db: Session | None = None,
    persistence_context: CurationPrepPersistenceContext | None = None,
) -> CurationPrepAgentOutput:
    """Fail fast until the deterministic prep mapper replaces the legacy LLM path."""

    _ = (
        extraction_results,
        scope_confirmation,
        db,
        persistence_context,
        CURATION_PREP_AGENT_ID,
    )
    raise RuntimeError(CURATION_PREP_UNAVAILABLE_MESSAGE)


__all__ = [
    "CurationPrepPersistenceContext",
    "run_curation_prep",
]
