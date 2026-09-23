"""ALL-1289: CSV, TSV and chat exports reach every phenotype term, not only the first."""

from __future__ import annotations

import csv
import io
import json
from types import SimpleNamespace

import pytest

from src.lib.flows.output_projection import (
    FlowOutputProjectionPlan,
    apply_projection_plan,
    build_flow_output_artifact_bundle,
    default_projection_plan,
    finalize_output_projection,
)
from src.lib.openai_agents.tools.file_output_tools import _projection_content_for_file_type

TERMS = "object.pack.PhenotypeAnnotation.phenotype_terms"
FIRST_TERM = "object.pack.PhenotypeAnnotation.phenotype_terms[0]"
FIRST_CURIE = "object.pack.PhenotypeAnnotation.phenotype_terms[0].curie"


def _term(label, curie, state):
    return {
        "curie": curie, "label": label, "resolution_state": state,
        "source_mentions": [label], "ontology_lookup_hint": {"data_provider": "WB"},
        "export_state": "blocked_pending_ontology_resolution",
        "write_blocked_reason": "phenotype term CURIE unresolved",
    }


def _phenotype_step():
    annotation = {
        "object_type": "PhenotypeAnnotation", "object_id": "p1",
        "payload": {
            "annotation_kind": "phenotype_annotation",
            "phenotype_annotation_object": "reduced brood size and slow growth",
            "phenotype_annotation_subject": {"subject_label": "mus-81", "subject_identifier": "WB:WBGene00003498",
                                             "resolution_state": "pending_entity_resolution"},
            "phenotype_terms": [
                _term("reduced brood size", "WBPhenotype:0000154", "resolved"),
                # Label-only proposal: no CURIE, so this term alone is unresolved.
                _term("slow growth", None, "pending_ontology_resolution"),
                # Carries a CURIE but an open finding names this element.
                _term("embryonic lethal", "WBPhenotype:0000050", "pending_ontology_resolution"),
            ],
            "negated": False,
            "evidence_record_ids": ["ev-1"],
        },
    }
    single = {
        "object_type": "PhenotypeAnnotation", "object_id": "p2",
        "payload": {
            "annotation_kind": "phenotype_annotation",
            "phenotype_annotation_object": "dumpy",
            "phenotype_terms": [_term("dumpy", "WBPhenotype:0000583", "resolved")],
            "negated": False,
        },
    }
    return {
        "step": 1, "node_id": "node_1", "agent_id": "phenotype_extractor", "agent_name": "Phenotype",
        "candidate": SimpleNamespace(
            agent_key="phenotype_extractor", adapter_key="phenotype", candidate_count=2,
            conversation_summary="two annotations",
            payload_json={
                "domain_pack_id": "agr.alliance.phenotype", "envelope_id": "env-ph",
                "extracted_objects": [annotation, single],
                "validation_findings": [{
                    "finding_id": "f1", "status": "open",
                    "field_path": "phenotype_terms[2].curie",
                    "field_ref": {"object_ref": {"object_id": "p1"}, "field_path": "phenotype_terms[2].curie"},
                }],
            },
        ),
    }


def _bundle(output_format):
    return build_flow_output_artifact_bundle(
        completed_steps=[_phenotype_step()], flow_name="Phenotype", output_format=output_format,
    )


def _plan(output_format, columns):
    return FlowOutputProjectionPlan.model_validate(
        {"format": output_format, "row_source": "object", "columns": columns}
    )


EXPECTED_TERMS = [
    "reduced brood size (WBPhenotype:0000154)",
    "slow growth (unresolved)",
    "embryonic lethal (WBPhenotype:0000050, unresolved)",
]


def test_phenotype_terms_is_a_declared_list_field_with_term_display():
    bundle = _bundle("csv")
    fields = {field.ref: field for field in bundle.field_catalog if field.row_source == "object"}
    assert fields[TERMS].value_type == "list"
    assert fields[TERMS].display == {"label": "label", "id": "curie"}


@pytest.mark.parametrize("output_format", ["csv", "tsv", "chat"])
def test_joined_cell_carries_every_term_marked_per_term(output_format):
    bundle = _bundle(output_format)
    result = apply_projection_plan(bundle, _plan(output_format, [
        {"key": "statement", "field_ref": "object.pack.PhenotypeAnnotation.phenotype_annotation_object"},
        {"key": "terms", "header": "Phenotype Terms", "field_ref": TERMS},
    ]))
    assert result.rows[0]["terms"] == "; ".join(EXPECTED_TERMS)
    assert result.rows[1]["terms"] == "dumpy (WBPhenotype:0000583)"


@pytest.mark.parametrize("output_format", ["csv", "tsv", "chat"])
def test_split_list_gives_one_column_per_term(output_format):
    bundle = _bundle(output_format)
    result = apply_projection_plan(bundle, _plan(output_format, [
        {"key": "terms", "field_ref": TERMS, "split_list": {"header_template": "Phenotype Term {n}"}},
    ]))
    assert [column.header for column in result.columns] == [
        "Phenotype Term 1", "Phenotype Term 2", "Phenotype Term 3",
    ]
    assert list(result.rows[0].values()) == EXPECTED_TERMS
    # A one-term annotation fills the first column; the rest are empty cells.
    assert result.rows[1]["terms_1"] == "dumpy (WBPhenotype:0000583)"
    assert result.rows[1]["terms_2"] == result.rows[1]["terms_3"] == ""
    if output_format == "csv":
        content = _projection_content_for_file_type(output_format="csv", projection=result)
        rows = list(csv.reader(io.StringIO(content)))
        assert rows[0] == ["Phenotype Term 1", "Phenotype Term 2", "Phenotype Term 3"]
        assert rows[1] == EXPECTED_TERMS


@pytest.mark.parametrize("output_format", ["csv", "chat"])
def test_default_layout_exports_every_phenotype_term(output_format):
    bundle = _bundle(output_format)
    plan = default_projection_plan(bundle, output_format=output_format, row_source="object")
    refs = [column.field_ref for column in plan.columns]
    assert TERMS in refs
    assert not any(ref and ref.startswith(TERMS + "[") for ref in refs)
    result = finalize_output_projection(bundle, plan)
    key = next(column.key for column in plan.columns if column.field_ref == TERMS)
    assert result.rows[0][key] == "; ".join(EXPECTED_TERMS)


def test_saved_first_term_layout_still_validates_and_renders():
    """Saved plans over phenotype_terms[0] keep their meaning: the first term only."""

    bundle = _bundle("csv")
    result = apply_projection_plan(bundle, _plan("csv", [
        {"key": "first", "field_ref": FIRST_TERM},
        {"key": "first_curie", "field_ref": FIRST_CURIE},
    ]))
    assert result.rows[0] == {"first": "reduced brood size (WBPhenotype:0000154)",
                              "first_curie": "WBPhenotype:0000154"}
    assert result.rows[1] == {"first": "dumpy (WBPhenotype:0000583)",
                              "first_curie": "WBPhenotype:0000583"}


def test_json_keeps_every_phenotype_term_lossless():
    bundle = _bundle("json")
    result = apply_projection_plan(bundle, _plan("json", [{"key": "terms", "field_ref": TERMS}]))
    terms = json.loads(json.dumps(result.json_data))[0]["terms"]
    assert [term["label"] for term in terms] == ["reduced brood size", "slow growth", "embryonic lethal"]
    assert terms[1]["curie"] is None
