"""Chemical-condition domain-envelope export adapter."""

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
    source_reference_id_from_context,
    string_value,
)
from ..schema_refs import ALLIANCE_LINKML_COMMIT
from .constants import (
    CHEMICAL_CONDITION_LINKML_SCHEMA_SOURCE_FILE,
    CHEMICAL_CONDITION_OBJECT_TYPE,
)


CHEMICAL_CONDITION_EXPORT_TARGET_ID = "alliance.chemical_condition.v1"
CHEMICAL_CONDITION_EXPORT_SCHEMA_VERSION = 1

_HOST_JOIN_TABLES = {
    "DiseaseAnnotation": "public.diseaseannotation_conditionrelation",
    "PhenotypeAnnotation": "public.phenotypeannotation_conditionrelation",
}

_REQUIRED_CHEMICAL_CONDITION_FIELD_PATHS = (
    "host_annotation_type",
    "host_annotation_id",
    "condition_relation_type.name",
    "condition_class.curie",
    "condition_class.name",
    "condition_chemical.curie",
    "condition_chemical.name",
)


class ChemicalConditionExportAdapter(DeterministicExportAdapter):
    """Build target-shaped condition-relation payloads from ready envelopes."""

    def __init__(
        self,
        *,
        adapter_key: str = "chemical",
        target_key: SubmissionTargetKey = CHEMICAL_CONDITION_EXPORT_TARGET_ID,
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
        payload_json = build_chemical_condition_export_payload(
            domain_envelope_candidates=export_context.domain_envelope_candidates,
            domain_envelopes=export_context.domain_envelopes,
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
            filename=f"{self.adapter_key}-{export_context.session_id}-chemical-conditions.json",
            warnings=_warnings_for(payload_json),
        )


def build_chemical_condition_export_payload(
    *,
    domain_envelope_candidates: Sequence[Mapping[str, Any]],
    domain_envelopes: Sequence[Mapping[str, Any]] = (),
    readiness_blockers: Sequence[Any] = (),
) -> dict[str, Any]:
    """Project complete chemical condition objects into Alliance target payloads."""

    adapter_blockers: list[dict[str, Any]] = []
    condition_relations: list[dict[str, Any]] = []

    for candidate in domain_envelope_candidates:
        if candidate_object_type(candidate) != CHEMICAL_CONDITION_OBJECT_TYPE:
            adapter_blockers.append(
                adapter_blocker(
                    candidate=candidate,
                    code="alliance.chemical_condition.export.unsupported_object_type",
                    message="Chemical export only supports ChemicalCondition objects.",
                    details={"expected_object_type": CHEMICAL_CONDITION_OBJECT_TYPE},
                )
            )
            continue

        projection, blockers = _project_chemical_condition_candidate(
            candidate,
            domain_envelopes=domain_envelopes,
        )
        adapter_blockers.extend(blockers)
        if projection is not None:
            condition_relations.append(projection)

    readiness_payloads = [
        item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
        for item in readiness_blockers
    ]
    payload_status = (
        "blocked" if adapter_blockers or readiness_payloads else "ready"
    )
    return canonical_json(
        {
            "schema_version": CHEMICAL_CONDITION_EXPORT_SCHEMA_VERSION,
            "payload_type": "alliance_chemical_condition_export",
            "payload_status": payload_status,
            "semantic_source": "domain_envelope.objects",
            "grounding": {
                "linkml": {
                    "commit": ALLIANCE_LINKML_COMMIT,
                    "source_file": CHEMICAL_CONDITION_LINKML_SCHEMA_SOURCE_FILE,
                    "classes": [
                        "ConditionRelation",
                        "ExperimentalCondition",
                        "ChemicalTerm",
                        "Reference",
                    ],
                    "required_slots": [
                        "condition_relation_type",
                        "conditions",
                        "condition_class",
                        "condition_chemical",
                    ],
                },
                "curation_db": {
                    "tables": [
                        "public.conditionrelation",
                        "public.experimentalcondition",
                        "public.conditionrelation_experimentalcondition",
                        "public.diseaseannotation_conditionrelation",
                        "public.phenotypeannotation_conditionrelation",
                    ],
                },
            },
            "condition_relations": condition_relations,
            "adapter_blockers": adapter_blockers,
            "readiness_blockers": readiness_payloads,
        }
    )


def _project_chemical_condition_candidate(
    candidate: Mapping[str, Any],
    *,
    domain_envelopes: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    payload_blocker = malformed_payload_blocker(
        candidate=candidate,
        code="alliance.chemical_condition.export.payload_malformed",
        message="Chemical condition export requires a mapping payload.",
    )
    if payload_blocker is not None:
        return None, [payload_blocker]

    payload = candidate_payload(candidate)
    blockers = missing_field_blockers(
        candidate=candidate,
        payload=payload,
        required_field_paths=_REQUIRED_CHEMICAL_CONDITION_FIELD_PATHS,
        code="alliance.chemical_condition.export.required_context_missing",
        message_prefix="Chemical condition export is missing required context",
    )

    reference_id = source_reference_id_from_context(
        candidate=candidate,
        domain_envelopes=domain_envelopes,
    )
    if reference_id is None:
        blockers.append(
            adapter_blocker(
                candidate=candidate,
                code="alliance.chemical_condition.export.required_context_missing",
                field_path="source_reference.reference_id",
                message=(
                    "Chemical condition export is missing required context: "
                    "source_reference.reference_id."
                ),
                details={
                    "accepted_sources": [
                        "payload.source_reference.reference_id",
                        "linked Reference payload.reference_id",
                    ]
                },
            )
        )

    host_type = string_value(payload, "host_annotation_type")
    host_join_table = _HOST_JOIN_TABLES.get(host_type or "")
    if host_type and host_join_table is None:
        blockers.append(
            adapter_blocker(
                candidate=candidate,
                code="alliance.chemical_condition.export.unsupported_host_annotation_type",
                field_path="host_annotation_type",
                message=(
                    "Chemical condition host annotation must be DiseaseAnnotation "
                    "or PhenotypeAnnotation before export."
                ),
                details={
                    "observed_host_annotation_type": host_type,
                    "supported_host_annotation_types": sorted(_HOST_JOIN_TABLES),
                },
            )
        )

    if blockers or reference_id is None or host_join_table is None:
        return None, blockers

    condition_relation_type = mapping_value(payload, "condition_relation_type")
    condition_class = mapping_value(payload, "condition_class")
    condition_chemical = mapping_value(payload, "condition_chemical")
    condition_id = mapping_value(payload, "condition_id")
    condition_taxon = mapping_value(payload, "condition_taxon")

    experimental_condition: dict[str, Any] = {
        "condition_class": condition_class,
        "condition_chemical": condition_chemical,
    }
    if condition_id:
        experimental_condition["condition_id"] = condition_id
    if condition_taxon:
        experimental_condition["condition_taxon"] = condition_taxon
    for source_key, target_key in (
        ("condition_quantity", "condition_quantity"),
        ("condition_free_text", "condition_free_text"),
        ("condition_summary", "condition_summary"),
    ):
        value = payload.get(source_key)
        if value is not None:
            experimental_condition[target_key] = value

    linkml_payload = {
        "host_annotation": {
            "type": host_type,
            "id": payload.get("host_annotation_id"),
        },
        "condition_relation": {
            "condition_relation_type": condition_relation_type,
            "single_reference": {"reference_id": reference_id},
            "conditions": [experimental_condition],
        },
    }

    return (
        {
            "candidate_id": candidate.get("candidate_id"),
            "envelope_id": candidate.get("envelope_id"),
            "object_id": candidate.get("object_id"),
            "target_class": "ConditionRelation",
            "target_tables": [
                "public.conditionrelation",
                "public.experimentalcondition",
                "public.conditionrelation_experimentalcondition",
                host_join_table,
            ],
            "linkml_payload": linkml_payload,
            "db_projection": {
                "conditionrelation": {
                    "table": "public.conditionrelation",
                    "lookup_columns": {
                        "conditionrelationtype_id": {
                            "table": "public.vocabularyterm",
                            "lookup_by": "name",
                            "value": condition_relation_type.get("name"),
                        },
                        "singlereference_id": {
                            "table": "public.reference",
                            "lookup_by": "reference_id",
                            "value": reference_id,
                        },
                    },
                },
                "experimentalcondition": {
                    "table": "public.experimentalcondition",
                    "lookup_columns": {
                        "conditionclass_id": {
                            "table": "public.ontologyterm",
                            "lookup_by": "curie",
                            "value": condition_class.get("curie"),
                        },
                        "conditionchemical_id": {
                            "table": "public.ontologyterm",
                            "lookup_by": "curie",
                            "value": condition_chemical.get("curie"),
                        },
                    },
                },
                "join_tables": [
                    "public.conditionrelation_experimentalcondition",
                    host_join_table,
                ],
            },
        },
        [],
    )


def _warnings_for(payload_json: Mapping[str, Any]) -> tuple[str, ...]:
    if payload_json.get("payload_status") == "blocked":
        return (
            "Chemical condition export contains readiness or adapter blockers; "
            "blocked objects were not projected to write rows.",
        )
    return ()


__all__ = [
    "CHEMICAL_CONDITION_EXPORT_SCHEMA_VERSION",
    "CHEMICAL_CONDITION_EXPORT_TARGET_ID",
    "ChemicalConditionExportAdapter",
    "build_chemical_condition_export_payload",
]
