"""Domain-agnostic builder-extraction finalize orchestration.

This is the thin, reusable adapter layer that sits over the project-agnostic
``ExtractionBuilderWorkspace`` engine (``backend/src/lib/openai_agents/
extraction_builder_workspace.py``). It owns the staging/finalize control flow
that EVERY builder data type needs:

  1. normalize the requested ``candidate_ids`` (reject blanks/duplicates),
  2. short-circuit when the workspace is already finalized (idempotency),
  3. enforce per-candidate provenance (evidence + resolver selections),
  4. invoke the DOMAIN materializer (builder candidates -> extraction-output
     payload with RELATIVE ``metadata_refs``),
  5. upsert the single materialized envelope candidate, and
  6. finalize the workspace, translating engine errors into structured issues.

Per-type code supplies ONLY a materializer callback (a thin domain adapter) and
maps the returned :class:`BuilderFinalizationOutcome` into its own result shape.
No domain-specific field logic lives here, so adding a new builder data type is
a domain-adapter edit, not a clone of any one type's bespoke per-field code.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from agr_ai_curation_runtime.extraction_builder import (
    CANDIDATE_STATUS_VALID,
    ExtractionBuilderError,
    ExtractionBuilderFinalizationConflict,
    ExtractionBuilderValidationError,
)


class BuilderMaterializationResult(Protocol):
    """Structural contract a domain materializer result must satisfy.

    Mirrors ``GeneExpressionMaterializationResult`` so any domain's materializer
    plugs into this orchestration without bespoke per-type handling.
    """

    @property
    def ok(self) -> bool: ...

    @property
    def payload(self) -> Optional[Mapping[str, Any]]: ...

    @property
    def issues(self) -> Sequence[Mapping[str, Any]]: ...

    @property
    def evidence_record_ids(self) -> Sequence[str]: ...

    def summary(self) -> Mapping[str, Any]: ...


# (workspace, candidate_ids, evidence_records, resolver_entry_lookup) -> result
DomainMaterializer = Callable[..., BuilderMaterializationResult]


@dataclass(frozen=True)
class BuilderFinalizationOutcome:
    """Structured result of a builder finalize orchestration step.

    The domain adapter converts this into its own tool-result shape; the generic
    orchestrator stays free of any per-project result/event coupling.
    """

    ok: bool
    finalization: Any | None = None
    materialization_summary: Mapping[str, Any] | None = None
    issues: tuple[dict[str, Any], ...] = ()
    message: str | None = None
    finalized_candidate_count: int = 0
    materialized_candidate_id: str | None = None
    # Domain-pack/materializer-issue reasons that the adapter may want to surface
    # as their own trace events (e.g. placeholder_reference rejections).
    materialization_issues: tuple[dict[str, Any], ...] = ()


def _normalize_candidate_ids(
    candidate_ids: Sequence[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    normalized: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    blank = False
    for candidate_id in candidate_ids:
        normalized_id = str(candidate_id or "").strip()
        if not normalized_id:
            blank = True
            continue
        if normalized_id in seen:
            duplicates.append(normalized_id)
            continue
        seen.add(normalized_id)
        normalized.append(normalized_id)
    issues: list[dict[str, Any]] = []
    if blank:
        issues.append(
            {
                "field_path": "candidate_ids",
                "reason": "blank_candidate_id",
                "message": "candidate_ids must contain non-empty candidate IDs.",
            }
        )
    if duplicates:
        issues.append(
            {
                "field_path": "candidate_ids",
                "reason": "duplicate_candidate_id",
                "message": "candidate_ids must not contain duplicate candidate IDs.",
                "duplicate_candidate_ids": duplicates,
            }
        )
    return normalized, issues


def _materialized_candidate_id(candidate_ids: Sequence[str], *, prefix: str) -> str:
    if len(candidate_ids) == 1:
        return candidate_ids[0]
    digest = hashlib.sha256("|".join(candidate_ids).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def finalize_builder_extraction(
    *,
    workspace: Any,
    candidate_ids: Sequence[str],
    materialize: DomainMaterializer,
    evidence_records: Sequence[Mapping[str, Any]],
    resolver_entry_lookup: Optional[Callable[[str], Any]],
    materialized_candidate_prefix: str,
    require_evidence_record_ids: bool = True,
    require_resolver_selections: bool = True,
) -> BuilderFinalizationOutcome:
    """Run the shared builder finalize control flow for one domain.

    ``materialize`` is the only domain-specific dependency; it must accept the
    keyword arguments ``workspace``, ``candidate_ids``, ``evidence_records`` and
    ``resolver_entry_lookup`` and return a :class:`BuilderMaterializationResult`.

    All structural/provenance/idempotency/finalize handling is shared. The caller
    translates the returned :class:`BuilderFinalizationOutcome` into its own
    tool-result and trace events.
    """

    normalized_candidate_ids, normalization_issues = _normalize_candidate_ids(
        candidate_ids
    )
    if normalization_issues:
        return BuilderFinalizationOutcome(
            ok=False,
            issues=tuple(normalization_issues),
            message="failed input validation.",
        )

    # Idempotent re-finalization: the workspace already produced a finalization.
    existing_finalization = getattr(workspace, "finalization", None)
    if existing_finalization is not None:
        existing_source_candidate_ids = tuple(
            getattr(existing_finalization, "source_candidate_ids", ()) or ()
        ) or tuple(getattr(existing_finalization, "candidate_ids", ()) or ())
        if set(existing_source_candidate_ids) != set(normalized_candidate_ids):
            return BuilderFinalizationOutcome(
                ok=False,
                message=(
                    "failed because the builder run is already finalized with "
                    "different source candidate membership."
                ),
                issues=(
                    {
                        "field_path": "candidate_ids",
                        "reason": "finalization_conflict",
                        "message": (
                            "Builder run already finalized with different source "
                            "candidate membership."
                        ),
                        "existing_candidate_ids": list(existing_source_candidate_ids),
                        "requested_candidate_ids": list(normalized_candidate_ids),
                    },
                ),
            )
        return BuilderFinalizationOutcome(
            ok=True,
            finalization=existing_finalization,
            finalized_candidate_count=getattr(
                existing_finalization, "finalized_candidate_count", 0
            ),
        )

    # Per-candidate provenance gate (evidence + resolver selections).
    provenance_issues: list[dict[str, Any]] = []
    for candidate_id in normalized_candidate_ids:
        try:
            candidate = workspace.get_candidate(candidate_id)
        except KeyError as exc:
            provenance_issues.append(
                {
                    "field_path": "candidate_ids",
                    "reason": "unknown_candidate_id",
                    "message": str(exc),
                    "candidate_id": candidate_id,
                }
            )
            continue
        if require_evidence_record_ids and not candidate.evidence_record_ids:
            provenance_issues.append(
                {
                    "field_path": "evidence_record_ids",
                    "reason": "missing_evidence_record_ids",
                    "message": "Finalized builder candidates require evidence_record_ids.",
                    "candidate_id": candidate_id,
                }
            )
        if require_resolver_selections and not candidate.resolver_selection_refs:
            provenance_issues.append(
                {
                    "field_path": "controlled_fields",
                    "reason": "missing_resolver_selection",
                    "message": "Finalized builder candidates require validated resolver selections.",
                    "candidate_id": candidate_id,
                }
            )
    if provenance_issues:
        workspace.record_validation_failure(
            errors=provenance_issues, candidate_ids=normalized_candidate_ids
        )
        return BuilderFinalizationOutcome(
            ok=False,
            issues=tuple(provenance_issues),
            message="failed builder validation.",
        )

    # Domain materialization (the only domain-specific step).
    materialization = materialize(
        workspace=workspace,
        candidate_ids=normalized_candidate_ids,
        evidence_records=evidence_records,
        resolver_entry_lookup=resolver_entry_lookup,
    )
    if not materialization.ok or materialization.payload is None:
        issue_list = [dict(issue) for issue in materialization.issues]
        workspace.record_validation_failure(
            errors=issue_list, candidate_ids=normalized_candidate_ids
        )
        return BuilderFinalizationOutcome(
            ok=False,
            issues=tuple(issue_list),
            materialization_summary=materialization.summary(),
            materialization_issues=tuple(issue_list),
            message="failed materialization validation.",
        )

    materialized_candidate_id = _materialized_candidate_id(
        normalized_candidate_ids, prefix=materialized_candidate_prefix
    )
    workspace.upsert_candidate(
        candidate_id=materialized_candidate_id,
        staged_fields=materialization.payload,
        pending_ref_ids=[
            pending_ref
            for candidate_id in normalized_candidate_ids
            for pending_ref in workspace.get_candidate(candidate_id).pending_ref_ids
        ],
        evidence_record_ids=list(materialization.evidence_record_ids),
        resolver_selection_refs=[
            resolver_ref
            for candidate_id in normalized_candidate_ids
            for resolver_ref in workspace.get_candidate(candidate_id).resolver_selection_refs
        ],
        status=CANDIDATE_STATUS_VALID,
    )

    try:
        finalization = workspace.finalize(
            candidate_ids=[materialized_candidate_id],
            source_candidate_ids=normalized_candidate_ids,
        )
    except ExtractionBuilderValidationError as exc:
        return BuilderFinalizationOutcome(
            ok=False,
            message=str(exc),
            issues=tuple(dict(issue) for issue in getattr(workspace, "validation_errors", []) or []),
            materialization_summary=materialization.summary(),
        )
    except ExtractionBuilderFinalizationConflict as exc:
        return BuilderFinalizationOutcome(
            ok=False,
            message=str(exc),
            issues=(
                {
                    "field_path": "candidate_ids",
                    "reason": "finalization_conflict",
                    "message": str(exc),
                    "requested_candidate_ids": list(normalized_candidate_ids),
                },
            ),
            materialization_summary=materialization.summary(),
        )
    except (KeyError, ValueError, ExtractionBuilderError) as exc:
        return BuilderFinalizationOutcome(
            ok=False,
            message=str(exc),
            issues=(
                {
                    "field_path": "candidate_ids",
                    "reason": "finalization_failed",
                    "message": str(exc),
                },
            ),
            materialization_summary=materialization.summary(),
        )

    return BuilderFinalizationOutcome(
        ok=True,
        finalization=finalization,
        materialization_summary=materialization.summary(),
        finalized_candidate_count=getattr(finalization, "finalized_candidate_count", 0),
        materialized_candidate_id=materialized_candidate_id,
    )


__all__ = [
    "BuilderFinalizationOutcome",
    "BuilderMaterializationResult",
    "DomainMaterializer",
    "finalize_builder_extraction",
]
