"""Contract tests for Alliance disease/phenotype/chemical export and submit adapters."""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.schemas.curation_workspace import CurationSubmissionStatus, SubmissionMode


REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs.chemical_condition import (  # noqa: E402
    CHEMICAL_CONDITION_EXPORT_TARGET_ID,
    ChemicalConditionExportAdapter,
    ChemicalConditionSubmissionBlockerAdapter,
    build_chemical_condition_export_payload,
)
from agr_ai_curation_alliance.domain_packs.disease import (  # noqa: E402
    DISEASE_EXPORT_TARGET_ID,
    DiseaseAnnotationExportAdapter,
    DiseaseAnnotationSubmissionBlockerAdapter,
    build_disease_annotation_export_payload,
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
    assert payload["semantic_source"] == "domain_envelope.objects"
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


def test_chemical_export_adapter_projects_complete_envelope_to_target_payload():
    candidate = _fixtures()["chemical_condition"]["candidate"]

    payload = build_chemical_condition_export_payload(
        domain_envelope_candidates=[candidate],
    )

    assert payload["payload_status"] == "ready"
    relation = payload["condition_relations"][0]
    assert relation["target_class"] == "ConditionRelation"
    assert relation["linkml_payload"]["host_annotation"] == {
        "id": "210270365",
        "type": "PhenotypeAnnotation",
    }
    assert relation["linkml_payload"]["condition_relation"]["single_reference"] == {
        "reference_id": 296935
    }
    assert relation["db_projection"]["join_tables"] == [
        "public.conditionrelation_experimentalcondition",
        "public.phenotypeannotation_conditionrelation",
    ]


def test_chemical_export_blocks_incomplete_host_context_with_field_details():
    candidate = deepcopy(_fixtures()["chemical_condition"]["candidate"])
    del candidate["payload"]["host_annotation_id"]

    payload = build_chemical_condition_export_payload(
        domain_envelope_candidates=[candidate],
    )

    assert payload["payload_status"] == "blocked"
    assert payload["condition_relations"] == []
    assert payload["adapter_blockers"][0]["field_path"] == "host_annotation_id"
    assert payload["adapter_blockers"][0]["code"] == (
        "alliance.chemical_condition.export.required_context_missing"
    )


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
        (
            ChemicalConditionExportAdapter(),
            CHEMICAL_CONDITION_EXPORT_TARGET_ID,
            ChemicalConditionSubmissionBlockerAdapter(),
            "chemical_condition",
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
