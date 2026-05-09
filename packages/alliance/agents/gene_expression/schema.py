"""Gene-expression extractor schema for Alliance domain-envelope output."""

from pydantic import model_validator

from src.schemas.domain_envelope import field_path_exists

from src.lib.openai_agents.models import (
    GeneExpressionEnvelope as RuntimeGeneExpressionEnvelope,
)


GENE_EXPRESSION_OBJECT_TYPE = "GeneExpressionAnnotation"
GENE_EXPRESSION_OBJECT_ROLE = "curatable_unit"
GENE_EXPRESSION_MODEL_REF = "GeneExpressionAnnotationPayload"
GENE_EXPRESSION_SCHEMA_ID = "alliance.linkml.GeneExpressionAnnotation"
FORBIDDEN_PAYLOAD_EVIDENCE_FIELDS = frozenset(
    {
        "evidence_text",
        "evidence_page_numbers",
        "evidence_figure_references",
        "evidence_internal_citations",
    }
)
REQUIRED_GENE_EXPRESSION_PAYLOAD_FIELDS = frozenset(
    {
        "date_created",
        "internal",
        "data_provider",
        "data_provider.abbreviation",
        "expression_annotation_subject",
        "expression_annotation_subject.primary_external_id",
        "expression_annotation_subject.gene_symbol",
        "relation",
        "relation.name",
        "single_reference",
        "single_reference.reference_id",
        "expression_experiment",
        "expression_experiment.unique_id",
        "expression_experiment.expression_assay_used",
        "expression_experiment.expression_assay_used.curie",
        "when_expressed_stage_name",
        "where_expressed_statement",
        "expression_pattern",
        "expression_pattern.where_expressed",
    }
)


class GeneExpressionEnvelope(RuntimeGeneExpressionEnvelope):
    """Config-discovered Alliance gene-expression extraction envelope."""

    __envelope_class__ = True

    @model_validator(mode="after")
    def _validate_gene_expression_domain_contract(self) -> "GeneExpressionEnvelope":
        errors: list[str] = []
        evidence_ids = {
            evidence.evidence_record_id
            for evidence in self.metadata.evidence_records
            if evidence.evidence_record_id
        }

        for index, obj in enumerate(self.curatable_objects):
            location = f"curatable_objects[{index}]"
            if obj.object_type != GENE_EXPRESSION_OBJECT_TYPE:
                errors.append(
                    f"{location}.object_type must be {GENE_EXPRESSION_OBJECT_TYPE}"
                )
            if obj.object_role != GENE_EXPRESSION_OBJECT_ROLE:
                errors.append(
                    f"{location}.object_role must be {GENE_EXPRESSION_OBJECT_ROLE}"
                )
            if obj.model_ref != GENE_EXPRESSION_MODEL_REF:
                errors.append(
                    f"{location}.model_ref must be {GENE_EXPRESSION_MODEL_REF}"
                )
            if obj.schema_ref is None:
                errors.append(f"{location}.schema_ref is required")
            elif obj.schema_ref.schema_id != GENE_EXPRESSION_SCHEMA_ID:
                errors.append(
                    f"{location}.schema_ref.schema_id must be "
                    f"{GENE_EXPRESSION_SCHEMA_ID}"
                )

            forbidden_payload_fields = sorted(
                FORBIDDEN_PAYLOAD_EVIDENCE_FIELDS.intersection(obj.payload)
            )
            if forbidden_payload_fields:
                errors.append(
                    f"{location}.payload must not store evidence fields "
                    f"{', '.join(forbidden_payload_fields)}; use "
                    "metadata.evidence_records[] plus evidence_record_ids[]"
                )

            missing_payload_fields = sorted(
                field_path
                for field_path in REQUIRED_GENE_EXPRESSION_PAYLOAD_FIELDS
                if not field_path_exists(obj.payload, field_path)
            )
            if missing_payload_fields:
                errors.append(
                    f"{location}.payload is missing required "
                    "GeneExpressionAnnotation fields: "
                    + ", ".join(missing_payload_fields)
                )

            if not obj.evidence_record_ids:
                errors.append(f"{location}.evidence_record_ids must not be empty")
            else:
                missing_evidence_ids = sorted(
                    evidence_id
                    for evidence_id in obj.evidence_record_ids
                    if evidence_id not in evidence_ids
                )
                if missing_evidence_ids:
                    errors.append(
                        f"{location}.evidence_record_ids references unknown "
                        "metadata.evidence_records IDs: "
                        + ", ".join(missing_evidence_ids)
                    )

            if self.repair_mode:
                if not obj.field_refs:
                    errors.append(
                        f"{location}.field_refs must identify repaired field paths "
                        "when repair_mode is true"
                    )
                object_ref_keys = set(obj.ref_keys())
                for field_ref_index, field_ref in enumerate(obj.field_refs):
                    if field_ref.object_ref.ref_key() not in object_ref_keys:
                        errors.append(
                            f"{location}.field_refs[{field_ref_index}].object_ref "
                            "must point at the repaired object"
                        )

        if self.repair_mode and not self.metadata.repair_notes:
            errors.append("metadata.repair_notes must describe repair-mode changes")

        if errors:
            raise ValueError("; ".join(errors))
        return self
