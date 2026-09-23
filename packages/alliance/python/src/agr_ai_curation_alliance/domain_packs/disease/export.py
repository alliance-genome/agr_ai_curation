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
    malformed_payload_blocker,
    mapping_value,
    missing_field_blockers,
    string_value,
)
from .._resolvable_payloads import (
    VOCABULARY_TERM_IDENTITY_KEYS,
    export_condition_relations,
    export_identity,
    export_identity_list,
)
from ..schema_refs import ALLIANCE_LINKML_COMMIT
from .constants import (
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
    "disease_annotation_object",
    "disease_annotation_subject",
    "disease_relation",
    "single_reference.reference_id",
    # evidence_code_curies is a `multivalued: true` field; the bare path requires a non-empty list.
    "evidence_code_curies",
    "data_provider",
)
_UNRESOLVED_VALUE_CODE = "alliance.disease.export.unresolved_value"
_TERM_IDENTITY_KEYS = ("curie", "name")
_SUBJECT_IDENTITY_KEYS = ("subject_identifier", "subject_label")
_DATA_PROVIDER_IDENTITY_KEYS = ("abbreviation",)
_WITH_GENE_IDENTITY_KEYS = ("primary_external_id",)
_EVIDENCE_CODE_IDENTITY_KEYS = ("curie",)


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
            "semantic_source": "domain_envelope.extracted_objects",
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

    def identity(field_path: str, identity_keys: tuple[str, ...], label: str) -> dict[str, Any] | None:
        value, blocker = export_identity(
            candidate=candidate,
            payload=payload,
            field_path=field_path,
            identity_keys=identity_keys,
            code=_UNRESOLVED_VALUE_CODE,
            label=label,
        )
        if blocker is not None:
            blockers.append(blocker)
        return value

    def identities(field_path: str, identity_keys: tuple[str, ...], label: str) -> list[dict[str, Any]]:
        values, list_blockers = export_identity_list(
            candidate=candidate,
            payload=payload,
            field_path=field_path,
            identity_keys=identity_keys,
            code=_UNRESOLVED_VALUE_CODE,
            label=label,
        )
        blockers.extend(list_blockers)
        return values

    disease_object = identity("disease_annotation_object", _TERM_IDENTITY_KEYS, "Disease term")
    subject = identity("disease_annotation_subject", _SUBJECT_IDENTITY_KEYS, "Disease annotation subject")
    relation = identity("disease_relation", VOCABULARY_TERM_IDENTITY_KEYS, "Disease relation")
    data_provider = identity("data_provider", _DATA_PROVIDER_IDENTITY_KEYS, "Data provider")
    evidence_code_curies = identities("evidence_code_curies", _EVIDENCE_CODE_IDENTITY_KEYS, "Evidence code")
    # R4 optional slots. annotation_type is the curation-method constant (manually_curated) the
    # backend always stages; genetic_sex, disease_qualifier_names, and with_or_from are only projected
    # when the extractor staged them. Each is exported only as its validated identity.
    annotation_type = identity("annotation_type", VOCABULARY_TERM_IDENTITY_KEYS, "Annotation type")
    genetic_sex = identity("genetic_sex", VOCABULARY_TERM_IDENTITY_KEYS, "Genetic sex")
    disease_qualifier_names = identities("disease_qualifier_names", VOCABULARY_TERM_IDENTITY_KEYS, "Disease qualifier")
    with_gene_identifiers = identities("with_gene_identifiers", _WITH_GENE_IDENTITY_KEYS, "With/from gene")
    condition_relations, condition_blockers = export_condition_relations(
        candidate=candidate, payload=payload, code=_UNRESOLVED_VALUE_CODE
    )
    blockers.extend(condition_blockers)
    reference = mapping_value(payload, "single_reference")

    # The subject validator writes subject_type with the subject identity; it is read only once
    # the subject is resolved.
    subject_type = (
        string_value(payload, "disease_annotation_subject.subject_type")
        if subject is not None
        else None
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

    if (
        blockers
        or target is None
        or disease_object is None
        or subject is None
        or relation is None
        or data_provider is None
    ):
        return None, blockers

    linkml_payload = {
        "disease_annotation_subject": {
            "subject_type": subject_type,
            "primary_external_id": subject["subject_identifier"],
            "label": subject["subject_label"],
        },
        "disease_annotation_object": disease_object,
        "relation": relation,
        "negated": bool(payload.get("negated", False)),
        "single_reference": reference,
        "evidence_codes": evidence_code_curies,
        "data_provider": data_provider,
    }
    if annotation_type is not None:
        linkml_payload["annotation_type"] = annotation_type
    if genetic_sex is not None:
        linkml_payload["genetic_sex"] = genetic_sex
    if disease_qualifier_names:
        linkml_payload["disease_qualifiers"] = disease_qualifier_names
    if with_gene_identifiers:
        linkml_payload["with_or_from"] = [{"gene": gene} for gene in with_gene_identifiers]
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
                        "value": disease_object["curie"],
                    },
                    "relation_id": {
                        "table": "public.vocabularyterm",
                        "lookup_by": "name",
                        "value": relation["name"],
                    },
                    "evidenceitem_id": {
                        "table": "public.reference",
                        "lookup_by": "reference_id",
                        "value": reference.get("reference_id"),
                    },
                    "dataprovider_id": {
                        "table": "public.organization",
                        "lookup_by": "abbreviation",
                        "value": data_provider["abbreviation"],
                    },
                    target["subject_fk_column"]: {
                        "table": "public.biologicalentity",
                        "lookup_by": "primaryexternalid",
                        "value": subject["subject_identifier"],
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
