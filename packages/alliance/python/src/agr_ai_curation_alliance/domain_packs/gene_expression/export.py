"""Alliance gene-expression export adapter for domain-envelope payloads."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.lib.curation_workspace.export_adapters.base import (
    DeterministicExportAdapter,
    ExportBundleArtifact,
)
from src.schemas.curation_workspace import (
    CurationExportPayloadContext,
    CurationSubmissionReadinessBlocker,
    SubmissionMode,
    SubmissionTargetKey,
)

from ..schema_refs import ALLIANCE_LINKML_COMMIT, ALLIANCE_LINKML_PROVIDER_KEY
from ._payload_terms import (
    has_term_selector as _has_term_selector,
    term_list as _term_list,
    term_payload as _term_payload,
    value_missing_or_blank as _value_missing_or_blank,
)
from .constants import (
    GENE_EXPRESSION_DOMAIN_PACK_ID,
    GENE_EXPRESSION_DOMAIN_PACK_VERSION,
    GENE_EXPRESSION_LINKML_SCHEMA_ID,
    GENE_EXPRESSION_LINKML_SCHEMA_NAME,
    GENE_EXPRESSION_LINKML_SCHEMA_URI,
    GENE_EXPRESSION_MODEL_ID,
    GENE_EXPRESSION_OBJECT_TYPE,
)
from .conversion import REQUIRED_GENE_EXPRESSION_PAYLOAD_FIELDS


GENE_EXPRESSION_ADAPTER_KEY = "gene_expression"
GENE_EXPRESSION_TARGET_KEY = "alliance.gene_expression.curation_db"
GENE_EXPRESSION_EXPORT_SCHEMA_VERSION = 1
GENE_EXPRESSION_LINKML_CLASSES = (
    "GeneExpressionAnnotation",
    "GeneExpressionExperiment",
    "ExpressionPattern",
    "TemporalContext",
    "AnatomicalSite",
)
GENE_EXPRESSION_CURATION_DB_TABLES = (
    "geneexpressionannotation",
    "geneexpressionexperiment",
    "expressionpattern",
    "temporalcontext",
    "anatomicalsite",
    "temporalcontext_stageuberonslimterms",
    "anatomicalsite_anatomicalstructureuberonterms",
    "anatomicalsite_cellularcomponentqualifiers",
)
ANATOMICAL_SITE_REQUIRED_COLUMNS = {
    "anatomicalstructureuberontermother": False,
    "anatomicalsubstructureuberontermother": False,
    "cellularcomponentother": False,
}
GENE_EXPRESSION_LINKML_SCHEMA_SOURCE_FILE = "model/schema/expression.yaml"
_AUDIT_ONLY_CONTEXT_FIELDS = {
    "expression_experiment.detection_reagents": (
        "Detection reagent export mapping is not approved for the Gene Expression "
        "0.7.0 curation DB handoff."
    ),
    "expression_experiment.specimen_alleles": (
        "Specimen allele export mapping is not approved for the Gene Expression "
        "0.7.0 curation DB handoff."
    ),
    "expression_experiment.specimen_genomic_model": (
        "Specimen genomic model export mapping is not approved for the Gene "
        "Expression 0.7.0 curation DB handoff."
    ),
    "condition_relations": (
        "Condition relation export mapping is not approved for the Gene Expression "
        "0.7.0 curation DB handoff."
    ),
}
GENE_EXPRESSION_CONTEXT_NOT_EXPORTED_WARNING_CODE = (
    "alliance.gene_expression.context_not_exported"
)


@dataclass(frozen=True)
class GeneExpressionExportBlocker:
    """Object/field-addressable blocker emitted by the gene-expression mapper."""

    candidate_id: str | None
    envelope_id: str | None
    object_id: str | None
    field_path: str | None
    code: str
    message: str

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-serializable blocker payload."""

        return {
            "candidate_id": self.candidate_id,
            "envelope_id": self.envelope_id,
            "object_id": self.object_id,
            "field_path": self.field_path,
            "code": self.code,
            "message": self.message,
        }


class GeneExpressionExportValidationError(ValueError):
    """Raised when a ready candidate cannot be mapped to the target payload."""

    def __init__(self, blockers: Sequence[GeneExpressionExportBlocker]) -> None:
        self.blockers = tuple(blockers)
        super().__init__(
            "Gene-expression export payload is blocked: "
            + "; ".join(blocker.message for blocker in self.blockers)
        )


class GeneExpressionExportAdapter(DeterministicExportAdapter):
    """Build Alliance curation DB-shaped payloads from GeneExpressionAnnotation envelopes."""

    def __init__(self) -> None:
        super().__init__(
            adapter_key=GENE_EXPRESSION_ADAPTER_KEY,
            supported_target_keys=(GENE_EXPRESSION_TARGET_KEY,),
        )

    def build_export_bundle(
        self,
        *,
        mode: SubmissionMode,
        target_key: SubmissionTargetKey,
        export_context: CurationExportPayloadContext,
    ) -> ExportBundleArtifact:
        """Build the target-shaped JSON export bundle."""

        payload_json = build_gene_expression_export_payload(
            mode=mode,
            target_key=target_key,
            export_context=export_context,
            adapter_key=self.adapter_key,
        )
        payload_text = json.dumps(payload_json, indent=2, sort_keys=True)
        return ExportBundleArtifact(
            payload_json=payload_json,
            payload_text=payload_text,
            content_type="application/json",
            filename=(
                f"{self.adapter_key}-{export_context.session_id}-"
                "gene-expression-curation-db.json"
            ),
            warnings=tuple(_export_warning_messages(payload_json)),
        )

    def domain_envelope_readiness_blockers(
        self,
        *,
        candidate: Mapping[str, Any],
    ) -> tuple[CurationSubmissionReadinessBlocker, ...]:
        """Surface gene-expression export blockers through submission readiness."""

        return tuple(
            _readiness_blocker_from_export_blocker(blocker, candidate)
            for blocker in gene_expression_export_blockers(candidate)
        )


def build_gene_expression_export_payload(
    *,
    mode: SubmissionMode,
    target_key: str,
    export_context: CurationExportPayloadContext,
    adapter_key: str = GENE_EXPRESSION_ADAPTER_KEY,
) -> dict[str, Any]:
    """Map ready envelope candidates to the Alliance gene-expression target shape."""

    if export_context.candidates:
        raise ValueError(
            "Gene-expression export only accepts domain-envelope candidates; "
            "legacy candidate payloads are outside this adapter."
        )

    blockers = [
        blocker
        for candidate in export_context.domain_envelope_candidates
        for blocker in gene_expression_export_blockers(candidate)
    ]
    if blockers:
        raise GeneExpressionExportValidationError(blockers)

    annotations = [
        _gene_expression_annotation_payload(candidate)
        for candidate in export_context.domain_envelope_candidates
    ]
    readiness_blockers = [
        blocker.model_dump(mode="json")
        if isinstance(blocker, CurationSubmissionReadinessBlocker)
        else dict(blocker)
        for blocker in export_context.readiness_blockers
    ]

    payload: dict[str, Any] = {
        "schema_version": GENE_EXPRESSION_EXPORT_SCHEMA_VERSION,
        "bundle_type": "alliance_gene_expression_curation_db_export",
        "payload_status": "ready",
        "adapter_key": adapter_key,
        "mode": mode.value,
        "target_key": target_key,
        "session_id": export_context.session_id,
        "candidate_ids": list(export_context.candidate_ids),
        "candidate_count": export_context.candidate_count,
        "domain_pack_id": GENE_EXPRESSION_DOMAIN_PACK_ID,
        "domain_pack_version": GENE_EXPRESSION_DOMAIN_PACK_VERSION,
        "schema_ref": _linkml_schema_ref(),
        "linkml": {
            "provider": ALLIANCE_LINKML_PROVIDER_KEY,
            "commit": ALLIANCE_LINKML_COMMIT,
            "source_file": GENE_EXPRESSION_LINKML_SCHEMA_SOURCE_FILE,
            "schema_id": GENE_EXPRESSION_LINKML_SCHEMA_ID,
            "root_class": GENE_EXPRESSION_LINKML_SCHEMA_NAME,
            "classes": list(GENE_EXPRESSION_LINKML_CLASSES),
        },
        "curation_db": {
            "target": "read_only_grounded_write_payload",
            "tables": list(GENE_EXPRESSION_CURATION_DB_TABLES),
        },
        "gene_expression_annotations": annotations,
        "readiness_blockers": readiness_blockers,
        "warnings": list(export_context.warnings),
    }
    if export_context.document is not None:
        payload["document"] = export_context.document.model_dump(mode="json")
    if export_context.session_validation is not None:
        payload["session_validation"] = export_context.session_validation.model_dump(
            mode="json"
        )
    return _canonicalize(payload)


def gene_expression_export_blockers(
    candidate: Mapping[str, Any],
) -> tuple[GeneExpressionExportBlocker, ...]:
    """Return deterministic adapter blockers for one domain-envelope candidate."""

    projection_ref = _mapping(candidate.get("projection_ref"))
    payload = _mapping(candidate.get("payload"))
    candidate_id = _optional_string(candidate.get("candidate_id"))
    envelope_id = _optional_string(
        candidate.get("envelope_id") or projection_ref.get("envelope_id")
    )
    object_id = _optional_string(candidate.get("object_id") or projection_ref.get("object_id"))

    blockers: list[GeneExpressionExportBlocker] = []
    object_type = _optional_string(candidate.get("object_type"))
    if object_type != GENE_EXPRESSION_OBJECT_TYPE:
        blockers.append(
            GeneExpressionExportBlocker(
                candidate_id=candidate_id,
                envelope_id=envelope_id,
                object_id=object_id,
                field_path=None,
                code="alliance.gene_expression.unsupported_object_type",
                message=(
                    "Gene-expression export requires object_type "
                    f"{GENE_EXPRESSION_OBJECT_TYPE}."
                ),
            )
        )
    if not _mapping(candidate.get("schema_ref")):
        blockers.append(
            GeneExpressionExportBlocker(
                candidate_id=candidate_id,
                envelope_id=envelope_id,
                object_id=object_id,
                field_path="schema_ref",
                code="alliance.gene_expression.required_field_missing",
                message=(
                    "Gene-expression export requires schema_ref metadata for the "
                    "source envelope object."
                ),
            )
        )

    for field_path in sorted(REQUIRED_GENE_EXPRESSION_PAYLOAD_FIELDS):
        if _value_missing_or_blank(_payload_value(payload, field_path)):
            blockers.append(
                GeneExpressionExportBlocker(
                    candidate_id=candidate_id,
                    envelope_id=envelope_id,
                    object_id=object_id,
                    field_path=field_path,
                    code="alliance.gene_expression.required_field_missing",
                    message=f"Required gene-expression export field is missing: {field_path}.",
                )
            )

    where_expressed = _mapping(
        _payload_value(payload, "expression_pattern.where_expressed")
    )
    if (
        not _has_term_selector(where_expressed.get("anatomical_structure"))
        and not _has_term_selector(where_expressed.get("cellular_component"))
    ):
        blockers.append(
            GeneExpressionExportBlocker(
                candidate_id=candidate_id,
                envelope_id=envelope_id,
                object_id=object_id,
                field_path="expression_pattern.where_expressed",
                code="alliance.gene_expression.anatomical_site_required",
                message=(
                    "Gene-expression export requires anatomical_structure or "
                    "cellular_component on expression_pattern.where_expressed."
                ),
            )
        )

    return tuple(blockers)


def _gene_expression_annotation_payload(candidate: Mapping[str, Any]) -> dict[str, Any]:
    payload = _mapping(candidate["payload"])
    expression_experiment = _mapping(payload["expression_experiment"])
    expression_pattern = _mapping(payload["expression_pattern"])
    when_expressed = _mapping(expression_pattern.get("when_expressed"))
    where_expressed = _mapping(expression_pattern["where_expressed"])
    subject = _mapping(payload["expression_annotation_subject"])
    data_provider = _mapping(payload["data_provider"])
    relation = _mapping(payload["relation"])
    single_reference = _mapping(payload["single_reference"])
    assay = _mapping(expression_experiment["expression_assay_used"])
    experiment_reference = _mapping(expression_experiment["single_reference"])
    entity_assayed = _mapping(expression_experiment["entity_assayed"])
    export_warnings = _audit_only_context_warnings(payload)

    temporal_target = {
        "table": "temporalcontext",
        "columns": {},
        "lookups": _drop_empty(
            {
                "developmentalstagestart_id": _term_lookup(
                    when_expressed.get("developmental_stage_start")
                ),
            }
        ),
        "relationships": _drop_empty(
            {
                "temporalcontext_stageuberonslimterms": _term_list(
                    when_expressed.get("stage_uberon_slim_terms")
                ),
            }
        ),
    }
    anatomical_target = {
        "table": "anatomicalsite",
        "columns": dict(ANATOMICAL_SITE_REQUIRED_COLUMNS),
        "lookups": _drop_empty(
            {
                "anatomicalstructure_id": _term_lookup(
                    where_expressed.get("anatomical_structure")
                ),
                "cellularcomponentterm_id": _term_lookup(
                    where_expressed.get("cellular_component")
                ),
            }
        ),
        "relationships": _drop_empty(
            {
                "anatomicalsite_anatomicalstructureuberonterms": _term_list(
                    where_expressed.get("anatomical_structure_uberon_terms")
                ),
                "anatomicalsite_cellularcomponentqualifiers": _term_list(
                    where_expressed.get("cellular_component_qualifiers")
                ),
            }
        ),
    }

    target_rows = {
        "geneexpressionannotation": {
            "table": "geneexpressionannotation",
            "columns": _drop_empty(
                {
                    "uniqueid": payload.get("unique_id"),
                    "datecreated": payload["date_created"],
                    "internal": payload["internal"],
                    "obsolete": payload.get("obsolete"),
                    "whenexpressedstagename": payload["when_expressed_stage_name"],
                    "whereexpressedstatement": payload["where_expressed_statement"],
                    "negated": payload.get("negated"),
                    "uncertain": payload.get("uncertain"),
                }
            ),
            "lookups": _drop_empty(
                {
                    "dataprovider_id": {
                        "table": "organization",
                        "match": {"abbreviation": data_provider["abbreviation"]},
                    },
                    "expressionannotationsubject_id": {
                        "table": "biologicalentity",
                        "match": {
                            "primaryexternalid": subject["primary_external_id"],
                        },
                        "projection": _drop_empty(
                            {
                                "gene_symbol": subject.get("gene_symbol"),
                            }
                        ),
                    },
                    "relation_id": {
                        "table": "vocabularyterm",
                        "match": {"name": relation["name"]},
                    },
                    "evidenceitem_id": {
                        "table": "reference",
                        "match": {"id": single_reference["reference_id"]},
                    },
                    "expressionassayused_id": _term_lookup(assay),
                }
            ),
        },
        "geneexpressionexperiment": {
            "table": "geneexpressionexperiment",
            "columns": _drop_empty(
                {
                    "uniqueid": expression_experiment["unique_id"],
                    "curie": expression_experiment.get("curie"),
                    "primaryexternalid": expression_experiment.get("primary_external_id"),
                    "modinternalid": expression_experiment.get("mod_internal_id"),
                }
            ),
            "lookups": _drop_empty(
                {
                    "singlereference_id": {
                        "table": "reference",
                        "match": {"id": experiment_reference["reference_id"]},
                    },
                    "entityassayed_id": {
                        "table": "biologicalentity",
                        "match": {
                            "primaryexternalid": entity_assayed["primary_external_id"],
                        },
                        "projection": _drop_empty(
                            {
                                "gene_symbol": entity_assayed.get("gene_symbol"),
                            }
                        ),
                    },
                    "expressionassayused_id": _term_lookup(assay),
                    "dataprovider_id": {
                        "table": "organization",
                        "match": {"abbreviation": data_provider["abbreviation"]},
                    },
                }
            ),
        },
        "expressionpattern": {
            "table": "expressionpattern",
            "lookups": {
                "whenexpressed_id": {
                    "table": "temporalcontext",
                    "source": "target_rows.temporalcontext",
                },
                "whereexpressed_id": {
                    "table": "anatomicalsite",
                    "source": "target_rows.anatomicalsite",
                },
            },
        },
        "temporalcontext": temporal_target,
        "anatomicalsite": anatomical_target,
    }
    target_rows["geneexpressionannotation"]["lookups"]["expressionexperiment_id"] = {
        "table": "geneexpressionexperiment",
        "source": "target_rows.geneexpressionexperiment",
    }
    target_rows["geneexpressionannotation"]["lookups"]["expressionpattern_id"] = {
        "table": "expressionpattern",
        "source": "target_rows.expressionpattern",
    }

    return _canonicalize(
        {
            "candidate_id": candidate["candidate_id"],
            "projection_ref": dict(_mapping(candidate["projection_ref"])),
            "envelope": {
                "envelope_id": candidate["envelope_id"],
                "envelope_revision": candidate["envelope_revision"],
                "domain_pack_id": candidate["domain_pack_id"],
                "domain_pack_version": candidate.get("domain_pack_version"),
                "object_id": candidate["object_id"],
                "object_type": candidate["object_type"],
                "model_ref": GENE_EXPRESSION_MODEL_ID,
                "schema_ref": dict(_mapping(candidate["schema_ref"])),
            },
            "source_payload": payload,
            "target_rows": target_rows,
            "evidence": {
                "single_reference": {
                    "table": "reference",
                    "match": {"id": single_reference["reference_id"]},
                },
                "evidence_record_ids": list(
                    _mapping(candidate.get("object")).get("evidence_record_ids", ())
                ),
            },
            "term_projections": _drop_empty(
                {
                    "assay": _term_payload(assay),
                    "developmental_stage_start": _term_payload(
                        when_expressed.get("developmental_stage_start")
                    ),
                    "stage_uberon_slim_terms": _term_list(
                        when_expressed.get("stage_uberon_slim_terms")
                    ),
                    "anatomical_structure": _term_payload(
                        where_expressed.get("anatomical_structure")
                    ),
                    "anatomical_structure_uberon_terms": _term_list(
                        where_expressed.get("anatomical_structure_uberon_terms")
                    ),
                    "cellular_component": _term_payload(
                        where_expressed.get("cellular_component")
                    ),
                    "cellular_component_qualifiers": _term_list(
                        where_expressed.get("cellular_component_qualifiers")
                    ),
                }
            ),
            "export_diagnostics": {
                "warnings": export_warnings,
            },
        }
    )


def _linkml_schema_ref() -> dict[str, Any]:
    return {
        "schema_id": GENE_EXPRESSION_LINKML_SCHEMA_ID,
        "provider": ALLIANCE_LINKML_PROVIDER_KEY,
        "name": GENE_EXPRESSION_LINKML_SCHEMA_NAME,
        "version": ALLIANCE_LINKML_COMMIT,
        "uri": GENE_EXPRESSION_LINKML_SCHEMA_URI,
        "source_file": GENE_EXPRESSION_LINKML_SCHEMA_SOURCE_FILE,
        "class": GENE_EXPRESSION_OBJECT_TYPE,
    }


def _audit_only_context_warnings(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for field_path, message in _AUDIT_ONLY_CONTEXT_FIELDS.items():
        value = _payload_value(payload, field_path)
        if _value_missing_or_blank(value):
            continue
        warnings.append(
            _export_warning(
                field_path=field_path,
                message=message,
                source_context=value,
                reason_code="export_mapping_not_approved",
            )
        )
    return warnings


def _export_warning(
    *,
    field_path: str,
    message: str,
    source_context: Any,
    reason_code: str,
    code: str = GENE_EXPRESSION_CONTEXT_NOT_EXPORTED_WARNING_CODE,
) -> dict[str, Any]:
    return {
        "severity": "warning",
        "status": "audit_only",
        "code": code,
        "field_path": field_path,
        "message": message,
        "details": {
            "reason_code": reason_code,
            "source_context": source_context,
        },
    }


def _export_warning_messages(payload: Mapping[str, Any]) -> list[str]:
    messages: list[str] = []
    for annotation in payload["gene_expression_annotations"]:
        for warning in annotation["export_diagnostics"]["warnings"]:
            messages.append(f"{warning['field_path']}: {warning['message']}")
    return list(dict.fromkeys(messages))


def _term_lookup(value: Any) -> dict[str, Any] | None:
    term = _term_payload(value)
    if not term:
        return None
    match = {"curie": term["curie"]} if term.get("curie") else {"name": term["name"]}
    return {"table": "ontologyterm", "match": match, "projection": term}


def _readiness_blocker_from_export_blocker(
    blocker: GeneExpressionExportBlocker,
    candidate: Mapping[str, Any],
) -> CurationSubmissionReadinessBlocker:
    envelope_id = blocker.envelope_id
    if envelope_id is None:
        raise ValueError("Gene-expression export readiness blocker is missing envelope_id")

    return CurationSubmissionReadinessBlocker(
        envelope_id=envelope_id,
        object_id=blocker.object_id,
        field_path=blocker.field_path,
        severity="blocker",
        status="open",
        code=blocker.code,
        message=blocker.message,
        provider_refs=dict(_mapping(candidate.get("provider_refs"))),
        projection_ref=dict(_mapping(candidate.get("projection_ref"))),
        details=_drop_empty(
            {
                "candidate_id": blocker.candidate_id,
                "adapter_key": candidate.get("adapter_key"),
            }
        ),
    )


def _payload_value(payload: Mapping[str, Any], field_path: str) -> Any:
    current: Any = payload
    for part in field_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _drop_empty(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in values.items()
        if not _value_missing_or_blank(value)
    }


def _canonicalize(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, sort_keys=True))


__all__ = [
    "GENE_EXPRESSION_ADAPTER_KEY",
    "GENE_EXPRESSION_CURATION_DB_TABLES",
    "GENE_EXPRESSION_EXPORT_SCHEMA_VERSION",
    "GENE_EXPRESSION_LINKML_CLASSES",
    "GENE_EXPRESSION_TARGET_KEY",
    "GeneExpressionExportAdapter",
    "GeneExpressionExportBlocker",
    "GeneExpressionExportValidationError",
    "build_gene_expression_export_payload",
    "gene_expression_export_blockers",
]
