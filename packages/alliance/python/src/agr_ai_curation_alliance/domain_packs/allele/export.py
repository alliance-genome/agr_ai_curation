"""Export allele association submission plans from domain envelopes."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from src.lib.curation_workspace.export_adapters.base import (
    DeterministicExportAdapter,
    ExportBundleArtifact,
)
from src.schemas.curation_workspace import (
    CurationExportPayloadContext,
    SubmissionMode,
    SubmissionTargetKey,
)
from src.schemas.domain_envelope import DomainEnvelope

from . import ALLELE_DOMAIN_PACK_ID
from .submit import (
    ALLELE_ASSOCIATION_SUBMISSION_TARGET_KEY,
    build_allele_association_submission_plan,
)


ALLELE_ASSOCIATION_EXPORT_SCHEMA_VERSION = 1
_DOMAIN_ENVELOPE_KEYS = (
    "envelope_id",
    "domain_pack_id",
    "domain_pack_version",
    "status",
    "schema_ref",
    "objects",
    "validation_findings",
    "history",
    "metadata",
)


def build_allele_association_export(
    envelope: DomainEnvelope,
    *,
    selected_object_ids: Sequence[str] | None = None,
    target_key: str = ALLELE_ASSOCIATION_SUBMISSION_TARGET_KEY,
) -> dict[str, Any]:
    """Build an allele association export payload with explicit write blockers."""

    if envelope.domain_pack_id != ALLELE_DOMAIN_PACK_ID:
        raise ValueError(
            f"Expected domain_pack_id {ALLELE_DOMAIN_PACK_ID}, found {envelope.domain_pack_id}"
        )
    submission_plan = build_allele_association_submission_plan(
        envelope,
        selected_object_ids=selected_object_ids,
        target_key=target_key,
    )
    return {
        "schema_version": ALLELE_ASSOCIATION_EXPORT_SCHEMA_VERSION,
        "export_type": "alliance_allele_paper_evidence_association",
        "domain_pack_id": envelope.domain_pack_id,
        "domain_pack_version": envelope.domain_pack_version,
        "submission_plan": submission_plan,
    }


class AllelePaperEvidenceExportAdapter(DeterministicExportAdapter):
    """Workspace export adapter for allele paper/evidence association plans."""

    def __init__(
        self,
        *,
        adapter_key: str = "allele",
        target_key: SubmissionTargetKey = ALLELE_ASSOCIATION_SUBMISSION_TARGET_KEY,
    ) -> None:
        super().__init__(
            adapter_key=adapter_key,
            supported_target_keys=(target_key,),
        )

    def build_export_bundle(
        self,
        *,
        mode: SubmissionMode,
        target_key: SubmissionTargetKey,
        export_context: CurationExportPayloadContext,
    ) -> ExportBundleArtifact:
        plans: list[dict[str, Any]] = []
        for raw_snapshot in export_context.domain_envelopes:
            envelope = _domain_envelope_from_snapshot(raw_snapshot)
            selected_object_ids = _selected_object_ids(raw_snapshot)
            plans.append(
                build_allele_association_export(
                    envelope,
                    selected_object_ids=selected_object_ids,
                    target_key=target_key,
                )
            )

        payload_json = _canonicalize_json_payload(
            {
                "schema_version": ALLELE_ASSOCIATION_EXPORT_SCHEMA_VERSION,
                "bundle_type": "alliance_allele_paper_evidence_association",
                "adapter_key": self.adapter_key,
                "mode": mode.value,
                "target_key": target_key,
                "session_id": export_context.session_id,
                "candidate_ids": export_context.candidate_ids,
                "candidate_count": export_context.candidate_count,
                "plans": plans,
                "readiness_blockers": [
                    blocker.model_dump(mode="json")
                    for blocker in export_context.readiness_blockers
                ],
            }
        )
        return ExportBundleArtifact(
            payload_json=payload_json,
            payload_text=json.dumps(payload_json, indent=2, sort_keys=True),
            content_type="application/json",
            filename=f"{self.adapter_key}-{export_context.session_id}-allele-plan.json",
        )


def _domain_envelope_from_snapshot(snapshot: Mapping[str, Any]) -> DomainEnvelope:
    return DomainEnvelope.model_validate(
        {key: snapshot[key] for key in _DOMAIN_ENVELOPE_KEYS if key in snapshot}
    )


def _selected_object_ids(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    raw_selected = snapshot.get("selected_object_ids") or ()
    if not isinstance(raw_selected, Sequence) or isinstance(raw_selected, str):
        return ()
    return tuple(str(value) for value in raw_selected)


def _canonicalize_json_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, sort_keys=True))


__all__ = [
    "ALLELE_ASSOCIATION_EXPORT_SCHEMA_VERSION",
    "AllelePaperEvidenceExportAdapter",
    "build_allele_association_export",
]
