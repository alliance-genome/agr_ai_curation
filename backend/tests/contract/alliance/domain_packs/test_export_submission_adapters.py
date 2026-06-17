"""Contract tests for Alliance disease/phenotype export and submit adapters."""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.lib.curation_workspace.export_adapters import (
    build_default_export_adapter_registry,
)
from src.lib.curation_workspace.submission_adapters import (
    build_default_submission_adapter_registry,
)
from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.schemas.curation_workspace import CurationSubmissionStatus, SubmissionMode


REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs.disease import (  # noqa: E402
    DISEASE_EXPORT_TARGET_ID,
    DiseaseAnnotationExportAdapter,
    DiseaseAnnotationSubmissionBlockerAdapter,
    build_disease_annotation_export_payload,
)
from agr_ai_curation_alliance.domain_packs.gene_expression import (  # noqa: E402
    GENE_EXPRESSION_ADAPTER_KEY,
    GENE_EXPRESSION_DOMAIN_PACK_ID,
    GENE_EXPRESSION_TARGET_KEY,
    GeneExpressionExportAdapter,
    GeneExpressionSubmissionAdapter,
)
from agr_ai_curation_alliance.domain_packs.phenotype import (  # noqa: E402
    PHENOTYPE_EXPORT_TARGET_ID,
    PhenotypeAnnotationExportAdapter,
    PhenotypeAnnotationSubmissionBlockerAdapter,
    build_phenotype_annotation_export_payload,
)


FIXTURE_PATH = (
    REPO_ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "domain_packs"
    / "export_submission"
    / "projection_fixtures.yaml"
)
GENE_EXPRESSION_FIXTURE_PATH = (
    REPO_ROOT
    / "packages"
    / "alliance"
    / "domain_packs"
    / "gene_expression"
    / "fixtures"
    / "tmem67_pending.yaml"
)


def _fixtures() -> dict[str, Any]:
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))


def _payload_context(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": "session-1",
        "candidate_ids": [candidate["candidate_id"]],
        "candidate_count": 1,
        "candidates": [],
        "domain_envelope_candidates": [candidate],
        "domain_envelopes": [],
        "readiness_blockers": [],
        "warnings": [],
    }


def _gene_expression_fixture_candidate() -> dict[str, Any]:
    fixture_pack = load_domain_fixture_pack(GENE_EXPRESSION_FIXTURE_PATH)
    envelope = fixture_pack.fixtures[0].envelope
    annotation = envelope.extracted_objects[0]
    object_id = annotation.pending_ref_id or annotation.object_id
    assert object_id is not None
    return {
        "candidate_id": "candidate-tmem67-contract",
        "adapter_key": GENE_EXPRESSION_ADAPTER_KEY,
        "display_label": "Tmem67 expression",
        "secondary_label": "metanephros",
        "semantic_source": "domain_envelope.extracted_objects",
        "projection_ref": {
            "envelope_id": envelope.envelope_id,
            "object_id": object_id,
            "envelope_revision": 1,
        },
        "envelope_id": envelope.envelope_id,
        "envelope_revision": 1,
        "domain_pack_id": envelope.domain_pack_id,
        "domain_pack_version": envelope.domain_pack_version,
        "object_id": object_id,
        "object_type": annotation.object_type,
        "object_role": annotation.object_role,
        "object_status": annotation.status.value,
        "definition_state": annotation.definition_state.value,
        "payload": annotation.payload,
        "object": annotation.model_dump(mode="json"),
        "schema_ref": (
            annotation.schema_ref.model_dump(mode="json")
            if annotation.schema_ref is not None
            else {}
        ),
        "object_model_ref": {},
        "model_field_ref": {},
        "projection_refs": [],
        "provider_refs": {},
        "metadata": {"semantic_source": "domain_envelope.extracted_objects"},
    }


def test_alliance_default_registries_expose_domain_export_and_submission_adapters():
    export_registry = build_default_export_adapter_registry()
    submission_registry = build_default_submission_adapter_registry()

    assert isinstance(
        export_registry.require("disease"),
        DiseaseAnnotationExportAdapter,
    )
    assert isinstance(
        export_registry.require("phenotype"),
        PhenotypeAnnotationExportAdapter,
    )
    assert isinstance(
        export_registry.require(GENE_EXPRESSION_ADAPTER_KEY),
        GeneExpressionExportAdapter,
    )
    assert isinstance(
        submission_registry.require(DISEASE_EXPORT_TARGET_ID),
        DiseaseAnnotationSubmissionBlockerAdapter,
    )
    assert isinstance(
        submission_registry.require(PHENOTYPE_EXPORT_TARGET_ID),
        PhenotypeAnnotationSubmissionBlockerAdapter,
    )
    assert isinstance(
        submission_registry.require(GENE_EXPRESSION_TARGET_KEY),
        GeneExpressionSubmissionAdapter,
    )


def test_gene_expression_export_adapter_projects_fixture_to_schema_pinned_target_payload():
    candidate = _gene_expression_fixture_candidate()
    adapter = GeneExpressionExportAdapter()

    submission_payload = adapter.build_submission_payload(
        mode=SubmissionMode.EXPORT,
        target_key=GENE_EXPRESSION_TARGET_KEY,
        payload_context=_payload_context(candidate),
    )

    payload = submission_payload.payload_json
    assert payload is not None
    assert payload["payload_status"] == "ready"
    assert payload["domain_pack_id"] == GENE_EXPRESSION_DOMAIN_PACK_ID
    assert payload["schema_ref"] == {
        "class": "GeneExpressionAnnotation",
        "name": "GeneExpressionAnnotation",
        "provider": "alliance_linkml",
        "schema_id": "alliance.linkml.GeneExpressionAnnotation",
        "source_file": "model/schema/expression.yaml",
        "uri": (
            "https://github.com/alliance-genome/agr_curation_schema/blob/"
            "1b11d0888f19eba4ca72022200bb7d96b30d4a52/model/schema/expression.yaml"
        ),
        "version": "1b11d0888f19eba4ca72022200bb7d96b30d4a52",
    }
    annotation = payload["gene_expression_annotations"][0]
    assert annotation["source_payload"]["relation"] == {"name": "is_expressed_in"}
    assert annotation["target_rows"]["geneexpressionannotation"]["lookups"][
        "evidenceitem_id"
    ]["match"] == {"id": 203506}
    assert annotation["target_rows"]["geneexpressionexperiment"]["lookups"][
        "singlereference_id"
    ]["match"] == {"id": 203506}
    assert annotation["target_rows"]["geneexpressionexperiment"]["lookups"][
        "entityassayed_id"
    ]["match"] == {"primaryexternalid": "MGI:1923928"}
    assert annotation["term_projections"]["anatomical_structure"] == {
        "curie": "EMAPA:17373",
        "name": "metanephros",
    }


def test_disease_export_adapter_projects_complete_envelope_to_target_payload():
    candidate = _fixtures()["disease"]["candidate"]
    adapter = DiseaseAnnotationExportAdapter()

    submission_payload = adapter.build_submission_payload(
        mode=SubmissionMode.EXPORT,
        target_key=DISEASE_EXPORT_TARGET_ID,
        payload_context=_payload_context(candidate),
    )

    payload = submission_payload.payload_json
    assert payload is not None
    assert payload["payload_status"] == "ready"
    assert payload["semantic_source"] == "domain_envelope.extracted_objects"
    assert payload["adapter_blockers"] == []
    annotation = payload["disease_annotations"][0]
    assert annotation["target_class"] == "GeneDiseaseAnnotation"
    assert annotation["target_tables"] == [
        "public.diseaseannotation",
        "public.genediseaseannotation",
    ]
    assert annotation["linkml_payload"]["disease_annotation_object"]["curie"] == (
        "DOID:0050730"
    )
    assert annotation["linkml_payload"]["relation"] == {"name": "is_implicated_in"}
    assert annotation["db_projection"]["lookup_columns"]["dataprovider_id"] == {
        "lookup_by": "abbreviation",
        "table": "public.organization",
        "value": "SGD",
    }


def test_phenotype_export_adapter_projects_complete_envelope_to_target_payload():
    candidate = _fixtures()["phenotype"]["candidate"]

    payload = build_phenotype_annotation_export_payload(
        domain_envelope_candidates=[candidate],
    )

    assert payload["payload_status"] == "ready"
    annotation = payload["phenotype_annotations"][0]
    assert annotation["target_class"] == "AGMPhenotypeAnnotation"
    assert "public.phenotypeannotation_ontologyterm" in annotation["target_tables"]
    assert annotation["linkml_payload"]["phenotype_terms"] == [
        {
            "curie": "MP:0003733",
            "label": "abnormal retina inner nuclear layer morphology",
        }
    ]
    assert annotation["db_projection"]["lookup_columns"]["phenotypeterms_id"] == {
        "lookup_by": "curie",
        "table": "public.ontologyterm",
        "value": "MP:0003733",
    }


def test_disease_export_blocks_incomplete_subject_context_with_field_details():
    candidate = deepcopy(_fixtures()["disease"]["candidate"])
    del candidate["payload"]["disease_annotation_subject"]["subject_identifier"]

    payload = build_disease_annotation_export_payload(
        domain_envelope_candidates=[candidate],
    )

    assert payload["payload_status"] == "blocked"
    assert payload["disease_annotations"] == []
    assert payload["adapter_blockers"][0]["field_path"] == (
        "disease_annotation_subject.subject_identifier"
    )


@pytest.mark.parametrize(
    ("fixture_key", "builder", "expected_code"),
    (
        (
            "disease",
            build_disease_annotation_export_payload,
            "alliance.disease.export.payload_malformed",
        ),
        (
            "phenotype",
            build_phenotype_annotation_export_payload,
            "alliance.phenotype.export.payload_malformed",
        ),
    ),
)
def test_export_blocks_malformed_candidate_payload_with_object_details(
    fixture_key,
    builder,
    expected_code: str,
):
    candidate = deepcopy(_fixtures()[fixture_key]["candidate"])
    candidate["payload"] = "not-a-mapping"

    payload = builder(domain_envelope_candidates=[candidate])

    assert payload["payload_status"] == "blocked"
    assert payload["adapter_blockers"][0]["field_path"] == "payload"
    assert payload["adapter_blockers"][0]["code"] == expected_code
    assert payload["adapter_blockers"][0]["details"] == {
        "observed_payload_type": "str"
    }


@pytest.mark.parametrize(
    ("export_adapter", "target_key", "submit_adapter", "payload_key"),
    (
        (
            DiseaseAnnotationExportAdapter(),
            DISEASE_EXPORT_TARGET_ID,
            DiseaseAnnotationSubmissionBlockerAdapter(),
            "disease",
        ),
        (
            PhenotypeAnnotationExportAdapter(),
            PHENOTYPE_EXPORT_TARGET_ID,
            PhenotypeAnnotationSubmissionBlockerAdapter(),
            "phenotype",
        ),
    ),
)
def test_submission_adapters_return_explicit_non_writing_blockers(
    export_adapter,
    target_key: str,
    submit_adapter,
    payload_key: str,
):
    candidate = _fixtures()[payload_key]["candidate"]
    submission_payload = export_adapter.build_submission_payload(
        mode=SubmissionMode.DIRECT_SUBMIT,
        target_key=target_key,
        payload_context=_payload_context(candidate),
    )

    result = submit_adapter.submit(payload=submission_payload)

    assert result.status is CurationSubmissionStatus.MANUAL_REVIEW_REQUIRED
    assert result.external_reference is None
    assert result.submission_state["write_behavior"]["status"] == "blocked"
    assert result.submission_state["candidate_ids"] == [candidate["candidate_id"]]
    assert result.target_result_history[0]["status"] == "blocked"
