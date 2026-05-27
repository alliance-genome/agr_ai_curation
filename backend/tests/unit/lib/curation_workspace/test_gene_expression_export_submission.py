"""Gene-expression domain-envelope export/submission adapter tests."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

from src.lib.curation_workspace import adapter_registry as adapter_registry_module
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

from agr_ai_curation_alliance.domain_packs.gene_expression import (  # noqa: E402
    GENE_EXPRESSION_ADAPTER_KEY,
    GENE_EXPRESSION_DOMAIN_PACK_ID,
    GENE_EXPRESSION_TARGET_KEY,
    GeneExpressionExportAdapter,
    GeneExpressionSubmissionAdapter,
    gene_expression_export_blockers,
)


TMEM67_FIXTURE_PATH = (
    REPO_ROOT
    / "packages"
    / "alliance"
    / "domain_packs"
    / "gene_expression"
    / "fixtures"
    / "tmem67_pending.yaml"
)


def _payload_context(candidate: dict) -> dict:
    return {
        "session_id": "session-gene-expression",
        "candidate_ids": [candidate["candidate_id"]],
        "candidate_count": 1,
        "candidates": [],
        "domain_envelope_candidates": [candidate],
        "domain_envelopes": [],
        "readiness_blockers": [],
        "warnings": [],
    }


def _candidate_from_fixture() -> dict:
    fixture_pack = load_domain_fixture_pack(TMEM67_FIXTURE_PATH)
    envelope = fixture_pack.fixtures[0].envelope
    annotation = envelope.objects[0]
    object_id = annotation.pending_ref_id or annotation.object_id
    assert object_id is not None
    return {
        "candidate_id": "candidate-tmem67",
        "adapter_key": GENE_EXPRESSION_ADAPTER_KEY,
        "display_label": "Tmem67 expression",
        "secondary_label": "metanephros",
        "semantic_source": "domain_envelope.objects",
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
        "metadata": {"semantic_source": "domain_envelope.objects"},
    }


def _lta_candidate() -> dict:
    payload = {
        "unique_id": (
            "MMO:0000640|RGD:3020|AGRKB:101000000400377|N/A|"
            "extracellular space|GO:0005615"
        ),
        "date_created": "2014-03-24T17:36:40Z",
        "internal": False,
        "obsolete": False,
        "data_provider": {"abbreviation": "RGD"},
        "expression_annotation_subject": {
            "primary_external_id": "RGD:3020",
            "gene_symbol": "Lta",
        },
        "relation": {"name": "is_expressed_in"},
        "single_reference": {"reference_id": 419039},
        "expression_experiment": {
            "unique_id": "RGD:3020|AGRKB:101000000400377|MMO:0000640",
            "single_reference": {"reference_id": 419039},
            "entity_assayed": {
                "primary_external_id": "RGD:3020",
                "gene_symbol": "Lta",
            },
            "expression_assay_used": {
                "curie": "MMO:0000640",
                "name": "expression assay",
            },
        },
        "when_expressed_stage_name": "N/A",
        "where_expressed_statement": "extracellular space",
        "expression_pattern": {
            "where_expressed": {
                "cellular_component": {
                    "curie": "GO:0005615",
                    "name": "obsolete extracellular space",
                }
            }
        },
    }
    return {
        "candidate_id": "candidate-lta",
        "adapter_key": GENE_EXPRESSION_ADAPTER_KEY,
        "display_label": "Lta expression",
        "secondary_label": "extracellular space",
        "semantic_source": "domain_envelope.objects",
        "projection_ref": {
            "envelope_id": "gene-expression-lta-rgd-205864243",
            "object_id": "gene-expression-annotation-205864243",
            "envelope_revision": 1,
        },
        "envelope_id": "gene-expression-lta-rgd-205864243",
        "envelope_revision": 1,
        "domain_pack_id": GENE_EXPRESSION_DOMAIN_PACK_ID,
        "domain_pack_version": "0.1.0",
        "object_id": "gene-expression-annotation-205864243",
        "object_type": "GeneExpressionAnnotation",
        "object_role": "curatable_unit",
        "object_status": "pending",
        "definition_state": "stable",
        "payload": payload,
        "object": {"evidence_record_ids": ["evidence-lta-extracellular-space-1"]},
        "schema_ref": {},
        "object_model_ref": {},
        "model_field_ref": {},
        "projection_refs": [],
        "provider_refs": {},
        "metadata": {"semantic_source": "domain_envelope.objects"},
    }


def test_gene_expression_export_maps_tmem67_fixture_to_target_db_shape():
    payload = GeneExpressionExportAdapter().build_submission_payload(
        mode=SubmissionMode.EXPORT,
        target_key=GENE_EXPRESSION_TARGET_KEY,
        payload_context=_payload_context(_candidate_from_fixture()),
    )

    assert payload.adapter_key == GENE_EXPRESSION_ADAPTER_KEY
    assert payload.payload_json is not None
    annotation = payload.payload_json["gene_expression_annotations"][0]
    target_rows = annotation["target_rows"]

    assert payload.payload_json["linkml"]["commit"] == (
        "1b11d0888f19eba4ca72022200bb7d96b30d4a52"
    )
    assert target_rows["geneexpressionannotation"]["columns"]["uniqueid"] == (
        "MMO:0000655|MGI:1923928|AGRKB:101000000232912|TS26|metanephros|EMAPA:17373"
    )
    assert target_rows["geneexpressionannotation"]["lookups"][
        "expressionannotationsubject_id"
    ]["match"] == {"primaryexternalid": "MGI:1923928"}
    assert target_rows["geneexpressionannotation"]["lookups"]["relation_id"][
        "match"
    ] == {"name": "is_expressed_in"}
    assert target_rows["geneexpressionannotation"]["lookups"]["evidenceitem_id"][
        "match"
    ] == {"id": 203506}
    assert target_rows["geneexpressionannotation"]["lookups"][
        "expressionassayused_id"
    ]["match"] == {"curie": "MMO:0000655"}
    assert target_rows["anatomicalsite"]["lookups"]["anatomicalstructure_id"][
        "match"
    ] == {"curie": "EMAPA:17373"}
    assert target_rows["anatomicalsite"]["columns"] == {
        "anatomicalstructureuberontermother": False,
        "anatomicalsubstructureuberontermother": False,
        "cellularcomponentother": False,
    }
    assert target_rows["anatomicalsite"]["relationships"][
        "anatomicalsite_anatomicalstructureuberonterms"
    ] == [{"curie": "UBERON:0001008", "name": "renal system"}]


def test_gene_expression_export_maps_lta_cellular_component_projection():
    payload = GeneExpressionExportAdapter().build_submission_payload(
        mode=SubmissionMode.EXPORT,
        target_key=GENE_EXPRESSION_TARGET_KEY,
        payload_context=_payload_context(_lta_candidate()),
    )

    assert payload.payload_json is not None
    annotation = payload.payload_json["gene_expression_annotations"][0]
    target_rows = annotation["target_rows"]

    assert target_rows["geneexpressionannotation"]["lookups"][
        "expressionannotationsubject_id"
    ]["match"] == {"primaryexternalid": "RGD:3020"}
    assert target_rows["anatomicalsite"]["lookups"]["cellularcomponentterm_id"][
        "match"
    ] == {"curie": "GO:0005615"}
    assert target_rows["anatomicalsite"]["columns"] == {
        "anatomicalstructureuberontermother": False,
        "anatomicalsubstructureuberontermother": False,
        "cellularcomponentother": False,
    }
    assert "anatomicalstructure_id" not in target_rows["anatomicalsite"]["lookups"]
    assert annotation["term_projections"]["cellular_component"] == {
        "curie": "GO:0005615",
        "name": "obsolete extracellular space",
    }


def test_gene_expression_export_blockers_are_object_and_field_addressable():
    candidate = copy.deepcopy(_candidate_from_fixture())
    del candidate["payload"]["single_reference"]

    blockers = gene_expression_export_blockers(candidate)

    assert {
        (blocker.object_id, blocker.field_path, blocker.code)
        for blocker in blockers
    } == {
        (
            "gene-expression-annotation-206552169",
            "single_reference",
            "alliance.gene_expression.required_field_missing",
        ),
        (
            "gene-expression-annotation-206552169",
            "single_reference.reference_id",
            "alliance.gene_expression.required_field_missing",
        ),
    }


def test_gene_expression_adapter_readiness_blocks_missing_anatomical_site_terms():
    candidate = copy.deepcopy(_candidate_from_fixture())
    candidate["payload"]["expression_pattern"]["where_expressed"] = {
        "cellular_component_qualifiers": [
            {"curie": "RO:0002170", "name": "present in"}
        ],
    }

    blockers = GeneExpressionExportAdapter().domain_envelope_readiness_blockers(
        candidate=candidate,
    )

    assert {
        (blocker.object_id, blocker.field_path, blocker.code)
        for blocker in blockers
    } == {
        (
            "gene-expression-annotation-206552169",
            "expression_pattern.where_expressed",
            "alliance.gene_expression.anatomical_site_required",
        )
    }
    assert blockers[0].envelope_id == "gene-expression-tmem67-mgi-206552169"
    assert blockers[0].severity == "blocker"
    assert blockers[0].status == "open"
    assert blockers[0].projection_ref == candidate["projection_ref"]


def test_gene_expression_adapter_readiness_blocks_blank_nested_site_term():
    candidate = copy.deepcopy(_candidate_from_fixture())
    candidate["payload"]["where_expressed_statement"] = "nucleus"
    candidate["payload"]["expression_pattern"]["where_expressed"] = {
        "cellular_component": {
            "name": "   ",
        },
    }

    blockers = GeneExpressionExportAdapter().domain_envelope_readiness_blockers(
        candidate=candidate,
    )

    assert {
        (blocker.object_id, blocker.field_path, blocker.code)
        for blocker in blockers
    } == {
        (
            "gene-expression-annotation-206552169",
            "expression_pattern.where_expressed",
            "alliance.gene_expression.anatomical_site_required",
        )
    }


def test_gene_expression_submission_adapter_records_target_state():
    export_payload = GeneExpressionExportAdapter().build_submission_payload(
        mode=SubmissionMode.DIRECT_SUBMIT,
        target_key=GENE_EXPRESSION_TARGET_KEY,
        payload_context=_payload_context(_candidate_from_fixture()),
    )

    result = GeneExpressionSubmissionAdapter().submit(payload=export_payload)

    assert result.status == CurationSubmissionStatus.MANUAL_REVIEW_REQUIRED
    assert result.external_reference == (
        f"alliance:gene_expression:{GENE_EXPRESSION_TARGET_KEY}:1"
    )
    assert result.submission_state["target_status"] == "manual_review_required"
    assert result.submission_state["annotation_count"] == 1
    assert result.submission_state["envelope_revisions"] == [
        {
            "envelope_id": "gene-expression-tmem67-mgi-206552169",
            "envelope_revision": 1,
        }
    ]
    assert result.target_result_history == (
        {
            "status": "manual_review_required",
            "target_key": GENE_EXPRESSION_TARGET_KEY,
            "annotation_count": 1,
            "write_mode": "read_only_handoff",
        },
    )


def test_default_registries_expose_gene_expression_export_and_submission_adapters():
    adapter_registry_module.load_curation_adapter_registry.cache_clear()
    try:
        export_registry = build_default_export_adapter_registry()
        submission_registry = build_default_submission_adapter_registry()
    finally:
        adapter_registry_module.load_curation_adapter_registry.cache_clear()

    export_adapter = export_registry.require(GENE_EXPRESSION_ADAPTER_KEY)
    submission_adapter = submission_registry.require(GENE_EXPRESSION_TARGET_KEY)

    assert export_adapter.__class__.__name__ == "GeneExpressionExportAdapter"
    assert export_adapter.adapter_key == GENE_EXPRESSION_ADAPTER_KEY
    assert export_adapter.supported_target_keys == (GENE_EXPRESSION_TARGET_KEY,)
    assert submission_adapter.__class__.__name__ == "GeneExpressionSubmissionAdapter"
    assert submission_adapter.transport_key == "alliance_gene_expression_submission"
    assert submission_adapter.supported_target_keys == (GENE_EXPRESSION_TARGET_KEY,)
