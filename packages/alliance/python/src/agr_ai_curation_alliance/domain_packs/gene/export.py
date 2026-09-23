"""Export gene mention evidence as non-mutating validated-reference data."""

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
from src.lib.domain_packs.resolvable_values import (
    LOOKUP_OUTCOME_KEY,
    MENTION_KEY,
    RESOLUTION_STATE_KEY,
    RESOLVED,
    VALIDATOR_CURATOR_MESSAGE_KEY,
    VALIDATOR_EXPLANATION_KEY,
    ResolvableSpec,
    effective_value,
    validator_event_covers,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
)

from .._export_utils import (
    canonical_json,
    domain_envelope_from_snapshot,
    selected_object_ids,
    stable_object_id,
)
from ..schema_refs import ALLIANCE_LINKML_COMMIT, ALLIANCE_LINKML_PROVIDER_KEY
from .constants import (
    GENE_DOMAIN_PACK_ID,
    GENE_MENTION_EVIDENCE_OBJECT_TYPE,
)


GENE_VALIDATED_REFERENCE_EXPORT_TARGET_KEY = "gene_validated_reference_evidence"
GENE_VALIDATED_REFERENCE_EXPORT_SCHEMA_VERSION = 1

# The gene identity only a validator writes; a gene_mention_evidence object is itself the
# resolvable value (mention = paper wording).
_GENE_IDENTITY_SPEC = ResolvableSpec(
    id_key="primary_external_id", label_key="gene_symbol", validated_keys=("taxon",)
)
_RESOLUTION_FIELDS = (
    RESOLUTION_STATE_KEY,
    LOOKUP_OUTCOME_KEY,
    VALIDATOR_EXPLANATION_KEY,
    VALIDATOR_CURATOR_MESSAGE_KEY,
)
_REQUIRED_EVIDENCE_FIELDS = (
    "evidence_record_id",
    "verified_quote",
    "page",
    "section",
    "chunk_id",
)
_OPTIONAL_REFERENCE_FIELDS = ("species",)
_OPTIONAL_EVIDENCE_FIELDS = ("subsection", "figure_reference")


def build_gene_mention_evidence_export(
    envelope: DomainEnvelope,
    *,
    selected_object_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build the Alliance gene validated-reference/evidence export payload."""

    if envelope.domain_pack_id != GENE_DOMAIN_PACK_ID:
        raise ValueError(
            f"Expected domain_pack_id {GENE_DOMAIN_PACK_ID}, found {envelope.domain_pack_id}"
        )

    selected = set(selected_object_ids or ())
    records = [
        _gene_evidence_record(domain_object)
        for domain_object in envelope.extracted_objects
        if domain_object.object_type == GENE_MENTION_EVIDENCE_OBJECT_TYPE
        and (not selected or stable_object_id(domain_object) in selected)
    ]

    return {
        "schema_version": GENE_VALIDATED_REFERENCE_EXPORT_SCHEMA_VERSION,
        "export_type": "alliance_gene_validated_reference_evidence",
        "domain_pack_id": envelope.domain_pack_id,
        "domain_pack_version": envelope.domain_pack_version,
        "linkml": {
            "provider": ALLIANCE_LINKML_PROVIDER_KEY,
            "commit": ALLIANCE_LINKML_COMMIT,
        },
        "records": records,
        "write_behavior": {
            "mode": "non_mutating_validated_reference_evidence",
            "mutates_base_gene": False,
            "creates_paper_gene_association": False,
            "write_targets": [],
        },
    }


class GeneMentionEvidenceExportAdapter(DeterministicExportAdapter):
    """Workspace export adapter for gene mention evidence envelopes."""

    def __init__(
        self,
        *,
        adapter_key: str = GENE_DOMAIN_PACK_ID,
        target_key: SubmissionTargetKey = GENE_VALIDATED_REFERENCE_EXPORT_TARGET_KEY,
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
        records: list[dict[str, Any]] = []
        for raw_snapshot in export_context.domain_envelopes:
            envelope = domain_envelope_from_snapshot(raw_snapshot)
            selected_object_ids_for_snapshot = selected_object_ids(raw_snapshot)
            export_payload = build_gene_mention_evidence_export(
                envelope,
                selected_object_ids=selected_object_ids_for_snapshot,
            )
            records.extend(export_payload["records"])

        payload_json = canonical_json(
            {
                "schema_version": GENE_VALIDATED_REFERENCE_EXPORT_SCHEMA_VERSION,
                "bundle_type": "alliance_gene_validated_reference_evidence",
                "adapter_key": self.adapter_key,
                "mode": mode.value,
                "target_key": target_key,
                "session_id": export_context.session_id,
                "candidate_ids": export_context.candidate_ids,
                "candidate_count": export_context.candidate_count,
                "record_count": len(records),
                "records": records,
                "readiness_blockers": [
                    blocker.model_dump(mode="json")
                    for blocker in export_context.readiness_blockers
                ],
                "write_behavior": {
                    "mode": "non_mutating_validated_reference_evidence",
                    "mutates_base_gene": False,
                    "creates_paper_gene_association": False,
                    "write_targets": [],
                },
            }
        )
        return ExportBundleArtifact(
            payload_json=payload_json,
            payload_text=json.dumps(payload_json, indent=2, sort_keys=True),
            content_type="application/json",
            filename=f"{self.adapter_key}-{export_context.session_id}-gene-evidence.json",
        )


def _gene_evidence_record(domain_object: CuratableObjectEnvelope) -> dict[str, Any]:
    """One export record; an unresolved gene is exported as explicitly unresolved.

    ``validated_reference`` carries the gene's paper wording, its resolution state and lookup
    outcome, and the validator-written identity. The identity keys are null unless the gene is
    resolved. Values stored before ALL-1283 read through the shared legacy rule.
    """

    object_id = stable_object_id(domain_object)
    gene = effective_value(
        domain_object.payload,
        _GENE_IDENTITY_SPEC,
        covered_by_validator=validator_event_covers(domain_object.metadata, ""),
    )
    resolved = gene[RESOLUTION_STATE_KEY] == RESOLVED
    reference: dict[str, Any] = {
        MENTION_KEY: _required_payload_value(gene, MENTION_KEY),
        "confidence": _required_payload_value(domain_object.payload, "confidence"),
    }
    for field in _GENE_IDENTITY_SPEC.identity_keys:
        reference[field] = _required_payload_value(gene, field) if resolved else None
    reference.update({field: gene.get(field) for field in _RESOLUTION_FIELDS})
    reference.update(
        _optional_payload_values(domain_object.payload, _OPTIONAL_REFERENCE_FIELDS)
    )

    evidence = {
        field: _required_payload_value(domain_object.payload, field)
        for field in _REQUIRED_EVIDENCE_FIELDS
    }
    evidence.update(
        _optional_payload_values(domain_object.payload, _OPTIONAL_EVIDENCE_FIELDS)
    )

    return {
        "object_id": object_id,
        "pending_ref_id": domain_object.pending_ref_id,
        "object_type": domain_object.object_type,
        "object_role": domain_object.object_role,
        "definition_state": domain_object.definition_state.value,
        "schema_ref": (
            domain_object.schema_ref.model_dump(mode="json")
            if domain_object.schema_ref is not None
            else None
        ),
        "provider_refs": _provider_refs(domain_object.metadata),
        "validated_reference": reference,
        "evidence": evidence,
        "write_behavior": {
            "mutates_base_gene": False,
            "creates_paper_gene_association": False,
            "write_target": None,
        },
    }


def _required_payload_value(payload: Mapping[str, Any], field_path: str) -> Any:
    value = payload.get(field_path)
    if value is None:
        raise ValueError(f"gene_mention_evidence payload is missing {field_path}")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"gene_mention_evidence payload has blank {field_path}")
    return value


def _optional_payload_values(
    payload: Mapping[str, Any],
    field_paths: Sequence[str],
) -> dict[str, Any]:
    return {
        field_path: payload[field_path]
        for field_path in field_paths
        if payload.get(field_path) not in (None, "")
    }


def _provider_refs(metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw_provider_refs = metadata.get("provider_refs")
    return dict(raw_provider_refs) if isinstance(raw_provider_refs, Mapping) else {}


__all__ = [
    "GENE_VALIDATED_REFERENCE_EXPORT_SCHEMA_VERSION",
    "GENE_VALIDATED_REFERENCE_EXPORT_TARGET_KEY",
    "GeneMentionEvidenceExportAdapter",
    "build_gene_mention_evidence_export",
]
