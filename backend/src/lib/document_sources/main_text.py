"""Provider-owned canonical main-text readiness and selection."""

from __future__ import annotations

from collections.abc import Iterable

from src.lib.document_sources.models import (
    DocumentSourceProvider,
    SourceArtifact,
    SourceArtifactFormat,
    SourceArtifactRole,
    SourceArtifactStatus,
)


def select_preferred_main_text_artifact(
    provider: DocumentSourceProvider,
    artifacts: Iterable[SourceArtifact],
) -> tuple[SourceArtifact | None, int]:
    """Select the unique provider-preferred import-ready main Markdown artifact.

    Providers normalize producer-specific statusless rows at their boundary. By
    the time artifacts reach reusable import services, only ``AVAILABLE`` means
    that an artifact is consumable; explicit or unrecognized ``UNKNOWN`` values
    remain non-ready.
    """

    candidates = tuple(
        artifact
        for artifact in artifacts
        if artifact.role is SourceArtifactRole.CONVERTED_TEXT
        and artifact.artifact_format is SourceArtifactFormat.MARKDOWN
        and artifact.status is SourceArtifactStatus.AVAILABLE
        and provider.is_main_text_artifact(artifact)
    )
    if not candidates:
        return None, 0

    ranked = sorted(
        ((tuple(provider.main_text_artifact_sort_key(artifact)), artifact) for artifact in candidates),
        key=lambda item: (
            item[0],
            str(item[1].display_name or "").strip().lower(),
            item[1].artifact_id,
        ),
    )
    best_rank = ranked[0][0]
    best = [artifact for rank, artifact in ranked if rank == best_rank]
    if len(best) > 1:
        return None, len(best)
    return best[0], 1
