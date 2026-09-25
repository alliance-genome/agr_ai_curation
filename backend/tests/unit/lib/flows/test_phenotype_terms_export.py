"""ALL-1289: CSV, TSV and chat exports reach every phenotype term, not only the first.

ALL-1283: each term is a resolvable value. Its cell is "label (ID)" once a validator
resolved it and the literal UNRESOLVED otherwise; the paper wording is its own column.
Terms stored before that contract follow the shared legacy rule.
"""

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
from src.lib.flows.value_display import RECORD_SEPARATOR
from src.lib.openai_agents.tools.file_output_tools import _projection_content_for_file_type

TERMS = "object.pack.PhenotypeAnnotation.phenotype_terms"
FIRST_TERM = "object.pack.PhenotypeAnnotation.phenotype_terms[0]"
FIRST_CURIE = "object.pack.PhenotypeAnnotation.phenotype_terms[0].curie"
FIRST_MENTION = "object.pack.PhenotypeAnnotation.phenotype_terms[0].mention"
FIRST_OUTCOME = "object.pack.PhenotypeAnnotation.phenotype_terms[0].lookup_outcome"


def _term(mention, label=None, curie=None, *, outcome="matched"):
    """A term value: resolved (label and CURIE from a validator) or unresolved with its outcome."""

    resolved = curie is not None
    return {
        "source_mentions": [mention],
        "ontology_lookup_hint": {"data_provider": "WB"},
        "curie": curie,
        "label": label,
        "mention": mention,
        "resolution_state": "resolved" if resolved else "unresolved",
        "lookup_outcome": "matched" if resolved else outcome,
        "validator_explanation": "Fixture decision.",
    }


def _phenotype_step():
    annotation = {
        "object_type": "PhenotypeAnnotation", "object_id": "p1",
        "payload": {
            "annotation_kind": "phenotype_annotation",
            "phenotype_annotation_object": "reduced brood size and slow growth",
            "phenotype_terms": [
                _term("fewer progeny", "reduced brood size", "WBPhenotype:0000154"),
                # Staged, never validated.
                _term("slow growth", outcome="not_validated"),
                # The validator looked it up and found nothing.
                _term("embryos died", outcome="not_found"),
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
            "phenotype_terms": [_term("short and fat", "dumpy", "WBPhenotype:0000583")],
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
                "validation_findings": [],
            },
        ),
    }


def _bundle(output_format, step=None):
    return build_flow_output_artifact_bundle(
        completed_steps=[step or _phenotype_step()], flow_name="Phenotype", output_format=output_format,
    )


def _plan(output_format, columns):
    return FlowOutputProjectionPlan.model_validate(
        {"format": output_format, "row_source": "object", "columns": columns}
    )


EXPECTED_TERMS = [
    "reduced brood size (WBPhenotype:0000154)",
    "UNRESOLVED",
    "UNRESOLVED",
]


def test_phenotype_terms_is_a_declared_list_field_with_term_display():
    bundle = _bundle("csv")
    fields = {field.ref: field for field in bundle.field_catalog if field.row_source == "object"}
    assert fields[TERMS].value_type == "list"
    assert fields[TERMS].display == {"label": "label", "id": "curie", "mention": "mention"}
    assert fields[FIRST_MENTION].label.endswith("(paper wording)")
    assert fields[FIRST_OUTCOME].label.endswith("(lookup result)")


@pytest.mark.parametrize("output_format", ["csv", "tsv", "chat"])
def test_joined_cell_carries_every_term_by_its_own_state(output_format):
    bundle = _bundle(output_format)
    result = apply_projection_plan(bundle, _plan(output_format, [
        {"key": "statement", "field_ref": "object.pack.PhenotypeAnnotation.phenotype_annotation_object"},
        {"key": "terms", "header": "Phenotype Terms", "field_ref": TERMS},
    ]))
    assert result.rows[0]["terms"] == RECORD_SEPARATOR.join(EXPECTED_TERMS)
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
    assert result.rows[0][key] == RECORD_SEPARATOR.join(EXPECTED_TERMS)


def test_saved_first_term_layout_still_validates_and_renders():
    """Saved plans over phenotype_terms[0] keep their meaning: the first term only."""

    bundle = _bundle("csv")
    result = apply_projection_plan(bundle, _plan("csv", [
        {"key": "first", "field_ref": FIRST_TERM},
        {"key": "first_curie", "field_ref": FIRST_CURIE},
        {"key": "first_wording", "field_ref": FIRST_MENTION},
    ]))
    assert result.rows[0] == {"first": "reduced brood size (WBPhenotype:0000154)",
                              "first_curie": "WBPhenotype:0000154",
                              "first_wording": "fewer progeny"}
    assert result.rows[1] == {"first": "dumpy (WBPhenotype:0000583)",
                              "first_curie": "WBPhenotype:0000583",
                              "first_wording": "short and fat"}


def test_paper_wording_and_lookup_result_are_separate_columns():
    step = _phenotype_step()
    step["candidate"].payload_json["extracted_objects"][0]["payload"]["phenotype_terms"] = [
        _term("embryos died", outcome="not_found"),
    ]
    bundle = _bundle("csv", step)
    result = apply_projection_plan(bundle, _plan("csv", [
        {"key": "first", "field_ref": FIRST_TERM},
        {"key": "wording", "field_ref": FIRST_MENTION},
        {"key": "outcome", "field_ref": FIRST_OUTCOME},
    ]))
    assert result.rows[0] == {"first": "UNRESOLVED", "wording": "embryos died", "outcome": "Not found"}


def test_json_keeps_every_phenotype_term_lossless():
    bundle = _bundle("json")
    result = apply_projection_plan(bundle, _plan("json", [{"key": "terms", "field_ref": TERMS}]))
    terms = json.loads(json.dumps(result.json_data))[0]["terms"]
    assert [term["mention"] for term in terms] == ["fewer progeny", "slow growth", "embryos died"]
    assert [term["lookup_outcome"] for term in terms] == ["matched", "not_validated", "not_found"]
    assert terms[1]["curie"] is None


def _legacy_step(*, covered: bool):
    """A term stored before the contract: an id and label, no resolution state."""

    metadata = {}
    if covered:
        metadata["validator_resolved_value_materialization"] = [
            {"materialized_field_paths": ["phenotype_terms[0].curie", "phenotype_terms[0].label"]}
        ]
    step = _phenotype_step()
    step["candidate"].payload_json["extracted_objects"] = [{
        "object_type": "PhenotypeAnnotation", "object_id": "p-legacy",
        "metadata": metadata,
        "payload": {
            "annotation_kind": "phenotype_annotation",
            "phenotype_annotation_object": "reduced brood size",
            "phenotype_terms": [{
                "curie": "WBPhenotype:0000154", "label": "reduced brood size",
                "source_mentions": ["reduced brood size"],
                "resolution_state": "pending_ontology_resolution",
            }],
            "negated": False,
        },
    }]
    return step


@pytest.mark.parametrize("output_format", ["csv", "chat"])
def test_legacy_term_reads_unresolved_unless_a_validator_event_covers_it(output_format):
    columns = [
        {"key": "first", "field_ref": FIRST_TERM},
        {"key": "wording", "field_ref": FIRST_MENTION},
        {"key": "outcome", "field_ref": FIRST_OUTCOME},
    ]
    unverified = apply_projection_plan(
        _bundle(output_format, _legacy_step(covered=False)), _plan(output_format, columns)
    )
    assert unverified.rows[0] == {
        "first": "UNRESOLVED",
        "wording": "reduced brood size (WBPhenotype:0000154) (legacy, unverified)",
        "outcome": "Legacy, unverified",
    }

    verified = apply_projection_plan(
        _bundle(output_format, _legacy_step(covered=True)), _plan(output_format, columns)
    )
    assert verified.rows[0]["first"] == "reduced brood size (WBPhenotype:0000154)"
    assert verified.rows[0]["outcome"] == "Matched"
