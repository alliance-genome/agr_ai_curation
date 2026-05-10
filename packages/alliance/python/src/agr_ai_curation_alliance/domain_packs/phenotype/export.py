"""Phenotype domain-envelope export adapter."""

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

from .._export_utils import (
    adapter_blocker,
    candidate_object_type,
    candidate_payload,
    canonical_json,
    list_value,
    malformed_payload_blocker,
    mapping_value,
    missing_field_blockers,
    string_value,
)
from ..schema_refs import ALLIANCE_LINKML_COMMIT
from .constants import (
    PHENOTYPE_LINKML_SCHEMA_SOURCE_FILE,
    PHENOTYPE_OBJECT_TYPE,
)


PHENOTYPE_EXPORT_TARGET_ID = "alliance.phenotype_annotation.v1"
PHENOTYPE_EXPORT_SCHEMA_VERSION = 1

_SUBJECT_TARGETS = {
    "gene": {
        "linkml_class": "GenePhenotypeAnnotation",
        "db_table": "public.genephenotypeannotation",
        "subject_fk_column": "phenotypeannotationsubject_id",
    },
    "allele": {
        "linkml_class": "AllelePhenotypeAnnotation",
        "db_table": "public.allelephenotypeannotation",
        "subject_fk_column": "phenotypeannotationsubject_id",
    },
    "agm": {
        "linkml_class": "AGMPhenotypeAnnotation",
        "db_table": "public.agmphenotypeannotation",
        "subject_fk_column": "phenotypeannotationsubject_id",
    },
}

_REQUIRED_PHENOTYPE_FIELD_PATHS = (
    "phenotype_annotation_object",
    "phenotype_annotation_subject.subject_type",
    "phenotype_annotation_subject.subject_identifier",
    "phenotype_terms[0].curie",
    "single_reference.reference_id",
    "data_provider.abbreviation",
)


class PhenotypeAnnotationExportAdapter(DeterministicExportAdapter):
    """Build target-shaped PhenotypeAnnotation payloads from ready envelopes."""

    def __init__(
        self,
        *,
        adapter_key: str = "phenotype",
        target_key: SubmissionTargetKey = PHENOTYPE_EXPORT_TARGET_ID,
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
        payload_json = build_phenotype_annotation_export_payload(
            domain_envelope_candidates=export_context.domain_envelope_candidates,
            readiness_blockers=export_context.readiness_blockers,
        )
        payload_json["mode"] = mode.value
        payload_json["target_key"] = target_key
        payload_json["session_id"] = export_context.session_id
        payload_text = json.dumps(payload_json, indent=2, sort_keys=True)

        return ExportBundleArtifact(
            payload_json=payload_json,
            payload_text=payload_text,
            content_type="application/json",
            filename=f"{self.adapter_key}-{export_context.session_id}-phenotype-annotations.json",
            warnings=_warnings_for(payload_json),
        )


def build_phenotype_annotation_export_payload(
    *,
    domain_envelope_candidates: Sequence[Mapping[str, Any]],
    readiness_blockers: Sequence[Any] = (),
) -> dict[str, Any]:
    """Project complete phenotype envelope objects into Alliance target payloads."""

    adapter_blockers: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []

    for candidate in domain_envelope_candidates:
        if candidate_object_type(candidate) != PHENOTYPE_OBJECT_TYPE:
            adapter_blockers.append(
                adapter_blocker(
                    candidate=candidate,
                    code="alliance.phenotype.export.unsupported_object_type",
                    message="Phenotype export only supports PhenotypeAnnotation objects.",
                    details={"expected_object_type": PHENOTYPE_OBJECT_TYPE},
                )
            )
            continue

        projection, blockers = _project_phenotype_candidate(candidate)
        adapter_blockers.extend(blockers)
        if projection is not None:
            annotations.append(projection)

    readiness_payloads = [
        item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
        for item in readiness_blockers
    ]
    payload_status = (
        "blocked" if adapter_blockers or readiness_payloads else "ready"
    )
    return canonical_json(
        {
            "schema_version": PHENOTYPE_EXPORT_SCHEMA_VERSION,
            "payload_type": "alliance_phenotype_annotation_export",
            "payload_status": payload_status,
            "semantic_source": "domain_envelope.objects",
            "grounding": {
                "linkml": {
                    "commit": ALLIANCE_LINKML_COMMIT,
                    "source_file": PHENOTYPE_LINKML_SCHEMA_SOURCE_FILE,
                    "abstract_class": "PhenotypeAnnotation",
                    "concrete_classes": [
                        "GenePhenotypeAnnotation",
                        "AllelePhenotypeAnnotation",
                        "AGMPhenotypeAnnotation",
                    ],
                    "required_slots": [
                        "phenotype_annotation_subject",
                        "phenotype_annotation_object",
                        "phenotype_terms",
                        "single_reference",
                        "data_provider",
                    ],
                },
                "curation_db": {
                    "base_table": "public.phenotypeannotation",
                    "concrete_tables": [
                        "public.genephenotypeannotation",
                        "public.allelephenotypeannotation",
                        "public.agmphenotypeannotation",
                    ],
                    "term_join_table": "public.phenotypeannotation_ontologyterm",
                    "condition_relation_join_table": (
                        "public.phenotypeannotation_conditionrelation"
                    ),
                },
            },
            "phenotype_annotations": annotations,
            "adapter_blockers": adapter_blockers,
            "readiness_blockers": readiness_payloads,
        }
    )


def _project_phenotype_candidate(
    candidate: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    payload_blocker = malformed_payload_blocker(
        candidate=candidate,
        code="alliance.phenotype.export.payload_malformed",
        message="Phenotype annotation export requires a mapping payload.",
    )
    if payload_blocker is not None:
        return None, [payload_blocker]

    payload = candidate_payload(candidate)
    blockers = missing_field_blockers(
        candidate=candidate,
        payload=payload,
        required_field_paths=_REQUIRED_PHENOTYPE_FIELD_PATHS,
        code="alliance.phenotype.export.required_context_missing",
        message_prefix="Phenotype annotation export is missing required context",
    )

    subject_type = string_value(
        payload,
        "phenotype_annotation_subject.subject_type",
    )
    target = _SUBJECT_TARGETS.get(subject_type or "")
    if subject_type and target is None:
        blockers.append(
            adapter_blocker(
                candidate=candidate,
                code="alliance.phenotype.export.unsupported_subject_type",
                field_path="phenotype_annotation_subject.subject_type",
                message=(
                    "Phenotype annotation subject must resolve to gene, allele, "
                    "or agm before export."
                ),
                details={
                    "observed_subject_type": subject_type,
                    "supported_subject_types": sorted(_SUBJECT_TARGETS),
                },
            )
        )

    if blockers or target is None:
        return None, blockers

    subject = mapping_value(payload, "phenotype_annotation_subject")
    phenotype_terms = list_value(payload, "phenotype_terms")
    first_term = (
        dict(phenotype_terms[0])
        if phenotype_terms and isinstance(phenotype_terms[0], Mapping)
        else {}
    )
    reference = mapping_value(payload, "single_reference")
    data_provider = mapping_value(payload, "data_provider")
    condition_relations = list_value(payload, "condition_relations")

    linkml_payload = {
        "phenotype_annotation_subject": {
            "subject_type": subject_type,
            "primary_external_id": subject.get("subject_identifier"),
            "label": subject.get("subject_label"),
            "taxon": subject.get("taxon"),
        },
        "phenotype_annotation_object": payload["phenotype_annotation_object"],
        "phenotype_terms": [first_term],
        "single_reference": reference,
        "negated": bool(payload.get("negated", False)),
        "data_provider": data_provider,
    }
    if condition_relations:
        linkml_payload["condition_relations"] = condition_relations

    return (
        {
            "candidate_id": candidate.get("candidate_id"),
            "envelope_id": candidate.get("envelope_id"),
            "object_id": candidate.get("object_id"),
            "target_class": target["linkml_class"],
            "target_tables": [
                "public.phenotypeannotation",
                target["db_table"],
                "public.phenotypeannotation_ontologyterm",
            ],
            "linkml_payload": linkml_payload,
            "db_projection": {
                "base_table": "public.phenotypeannotation",
                "concrete_table": target["db_table"],
                "lookup_columns": {
                    "phenotypeannotationobject": payload[
                        "phenotype_annotation_object"
                    ],
                    "phenotypeterms_id": {
                        "table": "public.ontologyterm",
                        "lookup_by": "curie",
                        "value": first_term.get("curie"),
                    },
                    "evidenceitem_id": {
                        "table": "public.reference",
                        "lookup_by": "reference_id",
                        "value": reference.get("reference_id"),
                    },
                    "dataprovider_id": {
                        "table": "public.organization",
                        "lookup_by": "abbreviation",
                        "value": data_provider.get("abbreviation"),
                    },
                    target["subject_fk_column"]: {
                        "table": "public.biologicalentity",
                        "lookup_by": "primaryexternalid",
                        "value": subject.get("subject_identifier"),
                    },
                },
                "term_join_table": "public.phenotypeannotation_ontologyterm",
                "condition_relation_join_table": (
                    "public.phenotypeannotation_conditionrelation"
                    if condition_relations
                    else None
                ),
            },
        },
        [],
    )


def _warnings_for(payload_json: Mapping[str, Any]) -> tuple[str, ...]:
    if payload_json.get("payload_status") == "blocked":
        return (
            "Phenotype export contains readiness or adapter blockers; blocked "
            "objects were not projected to write rows.",
        )
    return ()


__all__ = [
    "PHENOTYPE_EXPORT_SCHEMA_VERSION",
    "PHENOTYPE_EXPORT_TARGET_ID",
    "PhenotypeAnnotationExportAdapter",
    "build_phenotype_annotation_export_payload",
]
