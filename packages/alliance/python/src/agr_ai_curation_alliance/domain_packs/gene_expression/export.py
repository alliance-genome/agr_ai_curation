"""Alliance gene-expression export adapter for domain-envelope payloads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

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
from .constants import (
    GENE_EXPRESSION_DOMAIN_PACK_ID,
    GENE_EXPRESSION_LINKML_SCHEMA_NAME,
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
        "adapter_key": adapter_key,
        "mode": mode.value,
        "target_key": target_key,
        "session_id": export_context.session_id,
        "candidate_ids": list(export_context.candidate_ids),
        "candidate_count": export_context.candidate_count,
        "domain_pack_id": GENE_EXPRESSION_DOMAIN_PACK_ID,
        "linkml": {
            "provider": ALLIANCE_LINKML_PROVIDER_KEY,
            "commit": ALLIANCE_LINKML_COMMIT,
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
        _value_missing_or_blank(where_expressed.get("anatomical_structure"))
        and _value_missing_or_blank(where_expressed.get("cellular_component"))
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
        "columns": {},
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
                        "match": {"id": single_reference["reference_id"]},
                    },
                    "entityassayed_id": {
                        "table": "biologicalentity",
                        "match": {
                            "primaryexternalid": subject["primary_external_id"],
                        },
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
        }
    )


def _term_lookup(value: Any) -> dict[str, Any] | None:
    term = _term_payload(value)
    if not term:
        return None
    match = {"curie": term["curie"]} if term.get("curie") else {"name": term["name"]}
    return {"table": "ontologyterm", "match": match, "projection": term}


def _term_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    payload = _drop_empty(
        {
            "curie": value.get("curie"),
            "name": value.get("name"),
            "abbreviation": value.get("abbreviation"),
        }
    )
    return payload or None


def _term_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [
        term
        for item in value
        if (term := _term_payload(item)) is not None
    ]


def _payload_value(payload: Mapping[str, Any], field_path: str) -> Any:
    current: Any = payload
    for part in field_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _value_missing_or_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, Mapping):
        return len(value) == 0
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return len(value) == 0
    return False


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
