"""Disease domain-envelope export adapter."""

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
    first_string,
    list_value,
    malformed_payload_blocker,
    mapping_value,
    missing_field_blockers,
    string_value,
)
from ..schema_refs import ALLIANCE_LINKML_COMMIT
from .constants import (
    DISEASE_DOMAIN_PACK_ID,
    DISEASE_LINKML_SCHEMA_SOURCE_FILE,
    DISEASE_OBJECT_TYPE,
)


DISEASE_EXPORT_TARGET_ID = "alliance.disease_annotation.v1"
DISEASE_EXPORT_SCHEMA_VERSION = 1

_SUBJECT_TARGETS = {
    "gene": {
        "linkml_class": "GeneDiseaseAnnotation",
        "db_table": "public.genediseaseannotation",
        "subject_fk_column": "diseaseannotationsubject_id",
    },
    "allele": {
        "linkml_class": "AlleleDiseaseAnnotation",
        "db_table": "public.allelediseaseannotation",
        "subject_fk_column": "diseaseannotationsubject_id",
    },
    "agm": {
        "linkml_class": "AGMDiseaseAnnotation",
        "db_table": "public.agmdiseaseannotation",
        "subject_fk_column": "diseaseannotationsubject_id",
    },
}

_REQUIRED_DISEASE_FIELD_PATHS = (
    "disease_annotation_object.curie",
    "disease_annotation_subject.subject_type",
    "disease_annotation_subject.subject_identifier",
    "single_reference.reference_id",
    "evidence_code_curies[0]",
    "data_provider.abbreviation",
)


class DiseaseAnnotationExportAdapter(DeterministicExportAdapter):
    """Build target-shaped DiseaseAnnotation payloads from ready envelopes."""

    def __init__(
        self,
        *,
        adapter_key: str = "disease",
        target_key: SubmissionTargetKey = DISEASE_EXPORT_TARGET_ID,
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
        payload_json = build_disease_annotation_export_payload(
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
            filename=f"{self.adapter_key}-{export_context.session_id}-disease-annotations.json",
            warnings=_warnings_for(payload_json),
        )


def build_disease_annotation_export_payload(
    *,
    domain_envelope_candidates: Sequence[Mapping[str, Any]],
    readiness_blockers: Sequence[Any] = (),
) -> dict[str, Any]:
    """Project complete disease envelope objects into Alliance target payloads."""

    adapter_blockers: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []

    for candidate in domain_envelope_candidates:
        if candidate_object_type(candidate) != DISEASE_OBJECT_TYPE:
            adapter_blockers.append(
                adapter_blocker(
                    candidate=candidate,
                    code="alliance.disease.export.unsupported_object_type",
                    message="Disease export only supports DiseaseAnnotation objects.",
                    details={"expected_object_type": DISEASE_OBJECT_TYPE},
                )
            )
            continue

        projection, blockers = _project_disease_candidate(candidate)
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
            "schema_version": DISEASE_EXPORT_SCHEMA_VERSION,
            "payload_type": "alliance_disease_annotation_export",
            "payload_status": payload_status,
            "semantic_source": "domain_envelope.objects",
            "grounding": {
                "linkml": {
                    "commit": ALLIANCE_LINKML_COMMIT,
                    "source_file": DISEASE_LINKML_SCHEMA_SOURCE_FILE,
                    "abstract_class": "DiseaseAnnotation",
                    "concrete_classes": [
                        "GeneDiseaseAnnotation",
                        "AlleleDiseaseAnnotation",
                        "AGMDiseaseAnnotation",
                    ],
                    "required_slots": [
                        "disease_annotation_subject",
                        "disease_annotation_object",
                        "relation",
                        "single_reference",
                        "evidence_codes",
                        "data_provider",
                    ],
                },
                "curation_db": {
                    "base_table": "public.diseaseannotation",
                    "concrete_tables": [
                        "public.genediseaseannotation",
                        "public.allelediseaseannotation",
                        "public.agmdiseaseannotation",
                    ],
                    "condition_relation_join_table": (
                        "public.diseaseannotation_conditionrelation"
                    ),
                },
            },
            "disease_annotations": annotations,
            "adapter_blockers": adapter_blockers,
            "readiness_blockers": readiness_payloads,
        }
    )


def _project_disease_candidate(
    candidate: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    payload_blocker = malformed_payload_blocker(
        candidate=candidate,
        code="alliance.disease.export.payload_malformed",
        message="Disease annotation export requires a mapping payload.",
    )
    if payload_blocker is not None:
        return None, [payload_blocker]

    payload = candidate_payload(candidate)
    blockers = missing_field_blockers(
        candidate=candidate,
        payload=payload,
        required_field_paths=_REQUIRED_DISEASE_FIELD_PATHS,
        code="alliance.disease.export.required_context_missing",
        message_prefix="Disease annotation export is missing required context",
    )

    relation_name, relation_field = first_string(
        payload,
        ("relation.name", "disease_relation_name"),
    )
    if relation_name is None:
        blockers.append(
            adapter_blocker(
                candidate=candidate,
                code="alliance.disease.export.required_context_missing",
                field_path="relation.name",
                message="Disease annotation export is missing required context: relation.name.",
                details={
                    "accepted_field_paths": ["relation.name", "disease_relation_name"]
                },
            )
        )

    subject_type = string_value(
        payload,
        "disease_annotation_subject.subject_type",
    )
    target = _SUBJECT_TARGETS.get(subject_type or "")
    if subject_type and target is None:
        blockers.append(
            adapter_blocker(
                candidate=candidate,
                code="alliance.disease.export.unsupported_subject_type",
                field_path="disease_annotation_subject.subject_type",
                message=(
                    "Disease annotation subject must resolve to gene, allele, or agm "
                    "before export."
                ),
                details={
                    "observed_subject_type": subject_type,
                    "supported_subject_types": sorted(_SUBJECT_TARGETS),
                },
            )
        )

    if blockers or target is None or relation_name is None:
        return None, blockers

    disease_object = mapping_value(payload, "disease_annotation_object")
    subject = mapping_value(payload, "disease_annotation_subject")
    reference = mapping_value(payload, "single_reference")
    data_provider = mapping_value(payload, "data_provider")
    evidence_code_curies = [
        value.strip()
        for value in list_value(payload, "evidence_code_curies")
        if isinstance(value, str) and value.strip()
    ]
    condition_relations = list_value(payload, "condition_relations")

    # R4 optional slots. annotation_type is the curation-method constant (manually_curated) the
    # backend always materializes; it is NOT added to the required field paths. genetic_sex,
    # disease_qualifiers, and with_or_from are only projected when the extractor staged them.
    annotation_type_name = string_value(payload, "annotation_type_name")
    genetic_sex_name = string_value(payload, "genetic_sex_name")
    disease_qualifier_names = [
        value.strip()
        for value in list_value(payload, "disease_qualifier_names")
        if isinstance(value, str) and value.strip()
    ]
    with_gene_identifiers = [
        value.strip()
        for value in list_value(payload, "with_gene_identifiers")
        if isinstance(value, str) and value.strip()
    ]

    linkml_payload = {
        "disease_annotation_subject": {
            "subject_type": subject_type,
            "primary_external_id": subject.get("subject_identifier"),
            "label": subject.get("subject_label"),
        },
        "disease_annotation_object": disease_object,
        "relation": {"name": relation_name},
        "negated": bool(payload.get("negated", False)),
        "single_reference": reference,
        "evidence_codes": [{"curie": curie} for curie in evidence_code_curies],
        "data_provider": data_provider,
    }
    if annotation_type_name:
        linkml_payload["annotation_type"] = {"name": annotation_type_name}
    if genetic_sex_name:
        linkml_payload["genetic_sex"] = {"name": genetic_sex_name}
    if disease_qualifier_names:
        linkml_payload["disease_qualifiers"] = [
            {"name": name} for name in disease_qualifier_names
        ]
    if with_gene_identifiers:
        linkml_payload["with_or_from"] = [
            {"gene": {"primary_external_id": gid}} for gid in with_gene_identifiers
        ]
    if condition_relations:
        linkml_payload["condition_relations"] = condition_relations

    return (
        {
            "candidate_id": candidate.get("candidate_id"),
            "envelope_id": candidate.get("envelope_id"),
            "object_id": candidate.get("object_id"),
            "target_class": target["linkml_class"],
            "target_tables": [
                "public.diseaseannotation",
                target["db_table"],
            ],
            "linkml_payload": linkml_payload,
            "db_projection": {
                "base_table": "public.diseaseannotation",
                "concrete_table": target["db_table"],
                "lookup_columns": {
                    "diseaseannotationobject_id": {
                        "table": "public.ontologyterm",
                        "lookup_by": "curie",
                        "value": disease_object.get("curie"),
                    },
                    "relation_id": {
                        "table": "public.vocabularyterm",
                        "lookup_by": "name",
                        "value": relation_name,
                        "source_field": relation_field,
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
                "condition_relation_join_table": (
                    "public.diseaseannotation_conditionrelation"
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
            "Disease export contains readiness or adapter blockers; blocked objects "
            "were not projected to write rows.",
        )
    return ()


__all__ = [
    "DISEASE_EXPORT_SCHEMA_VERSION",
    "DISEASE_EXPORT_TARGET_ID",
    "DiseaseAnnotationExportAdapter",
    "build_disease_annotation_export_payload",
]
