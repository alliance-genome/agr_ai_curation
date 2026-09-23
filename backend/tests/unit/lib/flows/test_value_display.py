"""ALL-1282: structured values render as standard display text in non-JSON outputs."""

from __future__ import annotations

import csv
import io
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.lib.flows import export_fields
from src.lib.flows.output_projection import (
    FlowOutputArtifact,
    FlowOutputArtifactBundle,
    FlowOutputField,
    FlowOutputProjectionPlan,
    apply_projection_plan,
    build_flow_output_artifact_bundle,
    default_projection_plan,
    finalize_output_projection,
)
from src.lib.flows.value_display import display_text
from src.lib.openai_agents.tools.file_output_tools import _projection_content_for_file_type

TERM = {"label": "name", "id": "curie"}
GENE = {"label": "gene_symbol", "id": "primary_external_id"}
SUBJECT = {"label": "subject_label", "id": "subject_identifier", "state": "resolution_state",
           "resolved_states": ["resolved"]}
PHENOTYPE_TERM = {"label": "label", "id": "curie", "state": "resolution_state",
                  "resolved_states": ["resolved"]}


def _no_object_text(text: str) -> None:
    assert "{" not in text and "}" not in text, text
    assert "': " not in text and '": ' not in text, text


# Value shapes observed in production (key sets from the Sep 22 inventory).
PRODUCTION_SHAPES = [
    ({"curie": "WBbt:0005733", "name": "hypodermis"}, TERM, "hypodermis (WBbt:0005733)"),
    ({"curie": "MMO:0000672", "name": "knock-in in situ reporter assay"}, TERM,
     "knock-in in situ reporter assay (MMO:0000672)"),
    ({"gene_symbol": "Y71G12B.17", "primary_external_id": "WB:WBGene00022155",
      "source_phrase": "PPIT-2 (Y71G12B.17)"}, GENE, "Y71G12B.17 (WB:WBGene00022155)"),
    ({"allele_symbol": "e1370", "primary_external_id": "WB:WBVar00143949", "taxon": "NCBITaxon:6239"},
     {"label": "allele_symbol", "id": "primary_external_id"}, "e1370 (WB:WBVar00143949)"),
    ({"subject_label": "daf-2", "subject_identifier": "WB:WBGene00000898", "subject_type": "gene",
      "resolution_state": "resolved", "lookup_outcome": "matched"}, SUBJECT, "daf-2 (WB:WBGene00000898)"),
    # A pre-ALL-1283 "resolved" (no lookup_outcome) is legacy: unverified unless the
    # caller applied the read-time legacy rule with a covering validator event.
    ({"subject_label": "daf-2", "subject_identifier": "WB:WBGene00000898", "subject_type": "gene",
      "resolution_state": "resolved"}, SUBJECT, "UNRESOLVED"),
    ({"subject_label": "daf-2(e1370)", "resolution_state": "pending_lookup", "resolution_note": "n"},
     SUBJECT, "UNRESOLVED"),
    ({"curie": "WBPhenotype:0000154", "label": "reduced brood size", "resolution_state": "resolved",
      "lookup_outcome": "matched", "export_state": "ready", "write_blocked_reason": None}, PHENOTYPE_TERM,
     "reduced brood size (WBPhenotype:0000154)"),
    ({"name": "is_expressed_in", "vocabulary": "Expression Relation", "id": 200000200},
     {"label": "name"}, "is_expressed_in"),
    ({"abbreviation": "WB"}, {"label": "abbreviation"}, "WB"),
    ({"curie": "NCBITaxon:6239"}, TERM, "NCBITaxon:6239"),
    ({"name": "alzheimer disease"}, TERM, "alzheimer disease (unresolved)"),
    ({"reference_id": "DOI:10.17912/micropub.biology.002386", "doi": "10.17912/micropub.biology.002386",
      "title": "Endogenous Expression of PPIT-2", "source_phrase": "x"},
     {"label": "title", "id": "reference_id"},
     "Endogenous Expression of PPIT-2 (DOI:10.17912/micropub.biology.002386)"),
    # Generic reading without any declaration.
    ({"curie": "DOID:10652", "name": "Alzheimer's disease"}, None, "Alzheimer's disease (DOID:10652)"),
    ({"text": "PVD", "normalized_hint": "WBbt:0006831"}, None, "text: PVD; normalized_hint: WBbt:0006831"),
    ({"symbol": "unc-54"}, None, "symbol: unc-54"),
    ({"condition_relation_type": {"name": "has_condition"},
      "conditions": [{"condition_class": {"curie": "ZECO:0000111"}, "condition_summary": "heat"}]}, None,
     "condition_relation_type: has_condition; conditions: condition_class: ZECO:0000111; "
     "condition_summary: heat"),
    ([{"curie": "UBERON:0001008", "name": "renal system"}, {"curie": "UBERON:0002113", "name": "kidney"}],
     TERM, "renal system (UBERON:0001008) | kidney (UBERON:0002113)"),
    ({}, TERM, ""),
    ("free text", None, "free text"),
]


@pytest.mark.parametrize("value,spec,expected", PRODUCTION_SHAPES)
def test_display_text_renders_production_shapes(value, spec, expected):
    text = display_text(value, spec)
    assert text == expected
    _no_object_text(text.replace("Alzheimer's", "Alzheimers"))


def test_display_text_composite_and_unresolved_rules():
    where = {"anatomical_structure": {"curie": "WBbt:0005733", "name": "hypodermis"},
             "cellular_component": {"curie": "GO:0005634", "name": "nucleus"}}
    compose = {"compose": [{"path": "anatomical_structure", "display": TERM},
                           {"path": "cellular_component", "display": TERM}], "separator": "; "}
    assert display_text(where, compose) == "hypodermis (WBbt:0005733); nucleus (GO:0005634)"
    # Open finding: a paper CURIE alone is not proof of resolution.
    assert display_text({"curie": "WBbt:1", "name": "sperm"}, TERM, unresolved=True) == "sperm (WBbt:1, unresolved)"
    assert display_text("L4 larval stage", None, unresolved=True) == "L4 larval stage (unresolved)"
    terms = [{"curie": "A:1", "name": "a"}, {"name": "b"}, {"curie": "C:3", "name": "c"}]
    assert display_text(terms, TERM, unresolved=[(2,)]) == "a (A:1) | b (unresolved) | c (C:3, unresolved)"


def _bundle(rows, catalog, findings=()):
    return FlowOutputArtifactBundle(
        flow_name="Display",
        artifacts=[FlowOutputArtifact(source_key="s", rows_by_source={
            "object": rows, "validation_finding": list(findings)})],
        field_catalog=catalog,
        default_row_source="object",
    )


ANATOMY = "object.pack.GeneExpressionAnnotation.expression_pattern.where_expressed.anatomical_structure"
SUBJECT_REF = "object.pack.GeneExpressionAnnotation.expression_annotation_subject"
STAGES = "object.pack.GeneExpressionAnnotation.expression_pattern.when_expressed.stage_uberon_slim_terms"
STATEMENT = "object.pack.GeneExpressionAnnotation.where_expressed_statement"


def _structured_bundle():
    rows = [
        {"artifact.is_canonical_curation_data": True, "object.object_id": "o1", ANATOMY: {"curie": "WBbt:0005733", "name": "hypodermis"},
         SUBJECT_REF: {"gene_symbol": "Y71G12B.17", "primary_external_id": "WB:WBGene00022155"},
         STAGES: [{"curie": "UBERON:1", "name": "adult"}, {"curie": "UBERON:2", "name": "L4"}],
         STATEMENT: "detected in hypodermis"},
        {"artifact.is_canonical_curation_data": True, "object.object_id": "o2", ANATOMY: {"name": "residual body"},
         SUBJECT_REF: {"gene_symbol": "Y71G12B.17", "primary_external_id": "WB:WBGene00022155"},
         STAGES: [], STATEMENT: "near the residual body"},
    ]
    catalog = [
        FlowOutputField(ref=ANATOMY, label="Anatomical structure", value_type="object", row_source="object", display=TERM),
        FlowOutputField(ref=SUBJECT_REF, label="Gene", value_type="object", row_source="object", display=GENE),
        FlowOutputField(ref=STAGES, label="Stages", value_type="list", row_source="object", display=TERM),
        FlowOutputField(ref=STATEMENT, label="Statement", value_type="string", row_source="object"),
        FlowOutputField(ref="object.object_id", label="Object", value_type="string", row_source="object"),
    ]
    rows[1]["object.pending_ref_id"] = "pending-o2"
    findings = [{"object.object_id": "", "object.pending_ref_id": "pending-o2", "validation.status": "open",
                 "validation.field_path": "where_expressed_statement"},
                {"object.object_id": "o1", "validation.status": "open",
                 "validation.field_path": "expression_pattern.when_expressed.stage_uberon_slim_terms[1]"},
                {"object.object_id": "o2", "validation.status": "resolved",
                 "validation.field_path": "expression_pattern.where_expressed.anatomical_structure"}]
    return _bundle(rows, catalog, findings)


@pytest.mark.parametrize("output_format", ["csv", "tsv", "chat"])
def test_every_non_json_rendering_path_uses_display_text(output_format):
    bundle = _structured_bundle()
    plan = FlowOutputProjectionPlan.model_validate({
        "format": output_format, "row_source": "object", "missing_value": "—",
        "columns": [
            {"key": "gene", "header": "Gene", "field_ref": SUBJECT_REF},
            {"key": "anatomy", "header": "Anatomy", "field_ref": ANATOMY},
            {"key": "pair", "header": "Pair", "transform": {
                "type": "pair_join", "field_refs": [ANATOMY, STATEMENT], "pair_separator": " | "}},
            {"key": "concat", "header": "Concat", "transform": {
                "type": "concat", "values": [{"field_ref": SUBJECT_REF}, " in ", {"field_ref": ANATOMY}]}},
            {"key": "stages", "header": "Stages", "transform": {
                "type": "join_list", "field_ref": STAGES, "separator": " / "}},
            {"key": "elements", "header": "Elements", "transform": {
                "type": "format_elements", "field_refs": [STAGES], "default": "[{1}]", "separator": ", "}},
        ],
    })
    result = finalize_output_projection(bundle, plan)
    first, second = result.rows
    assert first["gene"] == "Y71G12B.17 (WB:WBGene00022155)"
    assert first["anatomy"] == "hypodermis (WBbt:0005733)"
    assert first["pair"] == "hypodermis (WBbt:0005733) | detected in hypodermis"
    assert first["concat"] == "Y71G12B.17 (WB:WBGene00022155) in hypodermis (WBbt:0005733)"
    assert first["stages"] == "adult (UBERON:1) / L4 (UBERON:2, unresolved)"
    assert first["elements"] == "[adult (UBERON:1)], [L4 (UBERON:2, unresolved)]"
    # Declared id role missing while a label is present marks the proposal.
    assert second["anatomy"] == "residual body (unresolved)"
    # An open finding referenced by pending_ref_id marks that object's field.
    assert second["pair"] == "residual body (unresolved) | near the residual body (unresolved)"
    assert second["stages"] == "—"
    for row in result.rows:
        for value in row.values():
            _no_object_text(str(value))
    if output_format == "chat":
        _no_object_text(result.chat_output or "")
        assert "| Y71G12B.17 (WB:WBGene00022155) | hypodermis (WBbt:0005733) |" in result.chat_output
    else:
        content = _projection_content_for_file_type(output_format=output_format, projection=result)
        _no_object_text(content)


def test_json_output_stays_lossless():
    bundle = _structured_bundle()
    plan = FlowOutputProjectionPlan.model_validate({
        "format": "json", "row_source": "object",
        "columns": [{"key": "anatomy", "field_ref": ANATOMY}, {"key": "stages", "field_ref": STAGES}],
    })
    result = finalize_output_projection(bundle, plan)
    assert result.rows[0]["anatomy"] == {"curie": "WBbt:0005733", "name": "hypodermis"}
    assert result.rows[0]["stages"][1] == {"curie": "UBERON:2", "name": "L4"}
    assert json.loads(_projection_content_for_file_type(output_format="json", projection=result))[0] == result.rows[0]


def test_packaged_field_value_fans_out_through_arrays():
    item = {"object_type": "T", "payload": {"condition_relations": [
        {"conditions": [{"condition_free_text": "heat"}, {"condition_free_text": "cold"}]},
        {"conditions": [{"condition_free_text": "dark"}]},
    ]}}
    field = {"object_type": "T", "payload_path": "condition_relations.conditions.condition_free_text"}
    assert export_fields.packaged_field_value(item, field) == [["heat", "cold"], ["dark"]]
    # One record per relation; items inside a record join with ", " (ALL-1290).
    assert display_text(export_fields.packaged_field_value(item, field)) == "heat, cold | dark"


def _validated_value(mention, **identity):
    """A stored value a validator resolved (ALL-1283 contract)."""

    return {**identity, "mention": mention, "resolution_state": "resolved",
            "lookup_outcome": "matched", "validator_explanation": None}


RESIDUAL_BODY = "structures associated with the residual body"


def _gene_expression_step():
    subject = _validated_value("PPIT-2 (Y71G12B.17)", gene_symbol="Y71G12B.17",
                               primary_external_id="WB:WBGene00022155")
    assay = _validated_value("knock-in", curie="MMO:0000672", name="knock-in in situ reporter assay")
    # Daniela's statement: the anatomy term matched nothing, so it stays UNRESOLVED
    # with the paper's wording instead of being dropped.
    residual_body = {"curie": None, "name": None, "mention": RESIDUAL_BODY,
                     "resolution_state": "unresolved", "lookup_outcome": "not_found",
                     "validator_explanation": "No anatomy term matches this wording."}

    def statement(object_id, anatomy, stage, statement_text):
        pattern = {"where_expressed": {"anatomical_structure": anatomy} if anatomy else {}}
        if stage:
            pattern["when_expressed"] = {"developmental_stage_start": stage}
        return {
            "object_type": "GeneExpressionAnnotation", "object_id": object_id,
            "payload": {
                "expression_annotation_subject": deepcopy(subject),
                "expression_experiment": {"expression_assay_used": deepcopy(assay),
                                          "entity_assayed": deepcopy(subject)},
                "relation": _validated_value("expressed in", name="is_expressed_in",
                                             vocabulary="Expression Relation", id=200000200),
                "expression_pattern": pattern,
                "where_expressed_statement": statement_text,
                "single_reference": _validated_value(
                    "DOI:10.17912/micropub.biology.002386",
                    reference_id="DOI:10.17912/micropub.biology.002386",
                ),
            },
        }

    objects = [
        statement("s1", _validated_value("hypodermis", curie="WBbt:0005733", name="hypodermis"),
                  _validated_value("L4 larval stage", curie="WBls:0000109", name="L4 larval stage"),
                  "GFP::PPIT-2 in hypodermis"),
        statement("s2", residual_body, {"curie": None, "name": None, "mention": "young adult",
                                        "resolution_state": "unresolved", "lookup_outcome": "not_validated",
                                        "validator_explanation": "Not validated yet."},
                  "signal near the residual body"),
    ]
    return {
        "step": 1, "node_id": "node_1", "agent_id": "gene_expression", "agent_name": "Gene Expression",
        "candidate": SimpleNamespace(
            agent_key="gene_expression", adapter_key="gene_expression", candidate_count=2,
            conversation_summary="two statements",
            payload_json={"domain_pack_id": "agr.alliance.gene_expression", "envelope_id": "env-ge",
                          "extracted_objects": objects,
                          "validation_findings": [{
                              "finding_id": "f1", "status": "open",
                              "field_path": "expression_pattern.when_expressed.developmental_stage_start",
                              "field_ref": {"object_ref": {"object_id": "s2"},
                                            "field_path": "expression_pattern.when_expressed.developmental_stage_start"},
                          }]},
        ),
    }


def _declared_gene_expression_pack(monkeypatch):
    """The real gene_expression pack plus in-test display declarations."""

    original = export_fields._packaged_domain_pack
    pack = original("gene_expression", {"curation": {"domain_pack_id": "agr.alliance.gene_expression"}})
    assert pack is not None
    declared = SimpleNamespace(metadata=pack.metadata.model_copy(deep=True))
    displays = {
        "OntologyTermSnapshotPayload": TERM,
        "GeneReferenceSnapshotPayload": GENE,
        "VocabularyTermSnapshotPayload": {"label": "name"},
        "OrganizationSnapshotPayload": {"label": "abbreviation"},
        "ReferenceSnapshotPayload": {"label": "title", "id": "reference_id"},
    }
    for model in declared.metadata.model_definitions:
        if model.model_id in displays:
            model.metadata["display"] = dict(displays[model.model_id])
    monkeypatch.setattr(export_fields, "_packaged_domain_pack", lambda *_args, **_kwargs: declared)
    return declared


@pytest.mark.parametrize("output_format", ["csv", "chat"])
def test_default_layout_for_gene_expression_uses_declared_parents(monkeypatch, output_format):
    _declared_gene_expression_pack(monkeypatch)
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_gene_expression_step()], flow_name="GE", output_format=output_format,
    )
    plan = default_projection_plan(bundle, output_format=output_format, row_source="object")
    headers = [column.header for column in plan.columns]
    refs = [column.field_ref for column in plan.columns]
    assert refs[0] == "object.pack.GeneExpressionAnnotation.expression_annotation_subject"
    assert "object.pack.GeneExpressionAnnotation.expression_experiment.expression_assay_used" in refs
    assert "object.pack.GeneExpressionAnnotation.expression_pattern.where_expressed.anatomical_structure" in refs
    assert not any(ref.endswith((".curie", ".name", ".gene_symbol")) for ref in refs)
    assert refs[-1] == "object.validation_status"
    assert "Adapter" not in headers
    result = finalize_output_projection(bundle, plan)
    first = result.rows[0]
    cells = [str(value) for row in result.rows for value in row.values()]
    for cell in cells:
        _no_object_text(cell)
    assert "Y71G12B.17 (WB:WBGene00022155)" in first.values()
    assert "knock-in in situ reporter assay (MMO:0000672)" in first.values()
    assert "hypodermis (WBbt:0005733)" in first.values()
    # s2's stage matched no term: its cell reads UNRESOLVED, never the paper wording.
    stage_ref = "object.pack.GeneExpressionAnnotation.expression_pattern.when_expressed.developmental_stage_start"
    stage_key = next(column.key for column in plan.columns if column.field_ref == stage_ref)
    assert first[stage_key] == "L4 larval stage (WBls:0000109)"
    assert result.rows[1][stage_key] == "UNRESOLVED"
    assert "young adult" not in " ".join(cells)


def test_selected_saved_plan_for_packaged_source_still_validates(monkeypatch):
    """A saved guided plan over leaf refs keeps validating and rendering."""
    _declared_gene_expression_pack(monkeypatch)
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_gene_expression_step()], flow_name="GE", output_format="csv",
    )
    plan = FlowOutputProjectionPlan.model_validate({
        "format": "csv", "row_source": "object", "columns": [
            {"key": "gene", "field_ref": "object.pack.GeneExpressionAnnotation.expression_annotation_subject.gene_symbol"},
            {"key": "assay", "field_ref": "object.pack.GeneExpressionAnnotation.expression_experiment.expression_assay_used"},
        ],
    })
    result = apply_projection_plan(bundle, plan)
    assert result.rows[0] == {"gene": "Y71G12B.17", "assay": "knock-in in situ reporter assay (MMO:0000672)"}
    rows = list(csv.reader(io.StringIO(_projection_content_for_file_type(output_format="csv", projection=result))))
    assert rows[1] == ["Y71G12B.17", "knock-in in situ reporter assay (MMO:0000672)"]


def test_packaged_gene_expression_declarations_render_daniela_values():
    """Integration with the packaged declarations (ALL-1282 packs)."""

    pack = export_fields._packaged_domain_pack("gene_expression", {"curation": {"domain_pack_id": "agr.alliance.gene_expression"}})
    models = {model.model_id: model for model in pack.metadata.model_definitions}
    if not models["OntologyTermSnapshotPayload"].metadata.get("display"):
        pytest.skip("gene_expression pack display declarations are not present in this checkout")
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_gene_expression_step()], flow_name="GE", output_format="csv",
    )
    for output_format in ("csv", "chat"):
        plan = default_projection_plan(bundle, output_format=output_format, row_source="object")
        result = finalize_output_projection(bundle, plan)
        values = [str(value) for row in result.rows for value in row.values()]
        for value in values:
            _no_object_text(value)
        assert "hypodermis (WBbt:0005733)" in " ".join(values)
        assert "Y71G12B.17 (WB:WBGene00022155)" in values
        assert "knock-in in situ reporter assay (MMO:0000672)" in values
        # The unmatched anatomy reads UNRESOLVED; its paper wording never fills the cell.
        assert RESIDUAL_BODY not in values
        assert "UNRESOLVED" in " ".join(values)
        if output_format == "csv":
            _no_object_text(_projection_content_for_file_type(output_format="csv", projection=result))


# --- split_list: list items in separate columns, never extra rows ---------------------

TERMS = "object.pack.GeneExpressionAnnotation.anatomy_terms"


def _split_bundle():
    rows = [
        {"artifact.is_canonical_curation_data": True, "object.object_id": f"o{index}",
         "object.label": f"statement {index}", TERMS: terms}
        for index, terms in enumerate([
            [{"curie": "WBbt:1", "name": "hypodermis"}, {"curie": "WBbt:2", "name": "vulva"},
             {"name": "residual body"}],
            [{"curie": "WBbt:3", "name": "sperm"}],
            [],
        ], start=1)
    ]
    catalog = [
        FlowOutputField(ref=TERMS, label="Anatomy", value_type="list", row_source="object", display=TERM),
        FlowOutputField(ref="object.label", label="Label", value_type="string", row_source="object"),
        FlowOutputField(ref="object.object_id", label="Object", value_type="string", row_source="object"),
    ]
    return _bundle(rows, catalog)


def _split_plan(output_format="csv", split=None, **extra):
    return FlowOutputProjectionPlan.model_validate({
        "format": output_format, "row_source": "object", "missing_value": "—",
        "columns": [
            {"key": "anatomy", "header": "Anatomy", "field_ref": TERMS, "split_list": split or {}},
            {"key": "label", "header": "Statement", "field_ref": "object.label"},
        ],
        **extra,
    })


@pytest.mark.parametrize("output_format", ["csv", "tsv", "chat"])
def test_split_list_expands_columns_with_display_items(output_format):
    bundle = _split_bundle()
    result = finalize_output_projection(bundle, _split_plan(output_format))
    assert [column.header for column in result.columns] == ["Anatomy 1", "Anatomy 2", "Anatomy 3", "Statement"]
    assert len(result.rows) == 3 and result.row_refs == ["object#1", "object#2", "object#3"]
    assert result.rows[0] == {"anatomy_1": "hypodermis (WBbt:1)", "anatomy_2": "vulva (WBbt:2)",
                              "anatomy_3": "residual body (unresolved)", "label": "statement 1"}
    assert result.rows[1] == {"anatomy_1": "sperm (WBbt:3)", "anatomy_2": "—",
                              "anatomy_3": "—", "label": "statement 2"}
    assert result.rows[2]["anatomy_1"] == "—"
    if output_format == "chat":
        assert "| Anatomy 1 | Anatomy 2 | Anatomy 3 | Statement |" in result.chat_output
    else:
        content = _projection_content_for_file_type(output_format=output_format, projection=result)
        rows = list(csv.reader(io.StringIO(content), delimiter="," if output_format == "csv" else "\t"))
        assert len(rows) == 4
        _no_object_text(content)


def test_split_list_header_template_and_explicit_headers():
    bundle = _split_bundle()
    templated = finalize_output_projection(bundle, _split_plan(split={"header_template": "Anatomy Term {n}"}))
    assert [c.header for c in templated.columns][:3] == ["Anatomy Term 1", "Anatomy Term 2", "Anatomy Term 3"]
    exact = finalize_output_projection(bundle, _split_plan(split={"headers": ["First", "Second", "Third"]}))
    assert [c.header for c in exact.columns] == ["First", "Second", "Third", "Statement"]
    wider = finalize_output_projection(bundle, _split_plan(split={"headers": ["A1", "A2", "A3", "A4"]}))
    assert [c.header for c in wider.columns][:4] == ["A1", "A2", "A3", "A4"]
    assert wider.rows[0]["anatomy_4"] == "—"
    with pytest.raises(ValueError, match="names 2 headers but the longest list has 3"):
        finalize_output_projection(bundle, _split_plan(split={"headers": ["First", "Second"]}))
    # Expanded headers take part in table-wide header uniqueness.
    with pytest.raises(ValueError, match="headers must be distinct after split_list expansion; duplicated: Statement"):
        finalize_output_projection(bundle, _split_plan(split={"headers": ["Statement", "B", "C"]}))


@pytest.mark.parametrize("split,message", [
    ({"header_template": "Anatomy Term"}, "must contain {n}"),
    ({"header_template": "A {n}", "headers": ["x"]}, "not both"),
    ({"headers": ["x", "x"]}, "headers must be distinct"),
    ({"max_columns": 0}, "max_columns must be between"),
])
def test_split_list_rejects_invalid_options(split, message):
    errors, _, _ = __import__("src.lib.flows.output_projection", fromlist=["x"]).validate_projection_plan(
        _split_bundle(), _split_plan(split=split))
    assert any(message in error for error in errors), errors


def test_split_list_accepts_single_values_and_rejects_transforms_and_json():
    from src.lib.flows.output_projection import validate_projection_plan

    bundle = _split_bundle()
    single = FlowOutputProjectionPlan.model_validate({
        "format": "csv", "row_source": "object", "missing_value": "\u2014",
        "columns": [{"key": "label", "header": "Statement", "field_ref": "object.label",
                     "split_list": {"header_template": "Statement {n}"}}],
    })
    # A single-valued field is a one-item list: one numbered column, rows unchanged.
    result = finalize_output_projection(bundle, single)
    assert [column.header for column in result.columns] == ["Statement 1"]
    assert [row["label_1"] for row in result.rows] == ["statement 1", "statement 2", "statement 3"]
    transform = FlowOutputProjectionPlan.model_validate({
        "format": "csv", "row_source": "object",
        "columns": [{"key": "t", "transform": {"type": "literal", "value": "x"}, "split_list": {}},
                    {"key": "label", "field_ref": "object.label"}],
    })
    errors, _, _ = validate_projection_plan(bundle, transform)
    assert any("needs a field_ref, not a transform" in error for error in errors)
    errors, _, _ = validate_projection_plan(bundle, _split_plan("json"))
    assert any("JSON keeps lists lossless" in error for error in errors)


def test_split_list_mixed_single_and_list_values_use_longest():
    bundle = _split_bundle()
    rows = bundle.rows_for_source("object")
    rows[1][TERMS] = {"curie": "WBbt:3", "name": "sperm"}  # single value, not a list
    rows[2][TERMS] = None
    result = finalize_output_projection(bundle, _split_plan(split={"header_template": "Anatomy Term {n}"}))
    assert [column.header for column in result.columns][:3] == ["Anatomy Term 1", "Anatomy Term 2", "Anatomy Term 3"]
    assert [result.rows[1][f"anatomy_{n}"] for n in (1, 2, 3)] == ["sperm (WBbt:3)", "\u2014", "\u2014"]
    assert [result.rows[2][f"anatomy_{n}"] for n in (1, 2, 3)] == ["\u2014", "\u2014", "\u2014"]
    assert len(result.rows) == 3


def test_split_list_limits_fail_explicitly(monkeypatch):
    from src.lib.flows.output_projection import FlowOutputOperationalCeilingError

    bundle = _split_bundle()
    with pytest.raises(ValueError, match="needs 3 split columns .* limit of 2"):
        finalize_output_projection(bundle, _split_plan(split={"max_columns": 2}))
    monkeypatch.setenv("FLOW_OUTPUT_SPLIT_LIST_MAX_COLUMNS", "2")
    with pytest.raises(FlowOutputOperationalCeilingError) as error:
        finalize_output_projection(bundle, _split_plan())
    assert error.value.setting == "FLOW_OUTPUT_SPLIT_LIST_MAX_COLUMNS"
    assert (error.value.measured, error.value.limit) == (3, 2)


def test_split_list_sized_after_filters_and_overrides_target_expanded_keys():
    bundle = _split_bundle()
    plan = _split_plan(filters=[{"field_ref": "object.object_id", "op": "ne", "value": "o1"}],
                       overrides=[{"row_ref": "object#2", "column_key": "anatomy_1", "value": "sperm cell"}])
    result = finalize_output_projection(bundle, plan)
    assert [column.key for column in result.columns] == ["anatomy_1", "label"]
    assert result.rows[0]["anatomy_1"] == "sperm cell"
    bad = _split_plan(overrides=[{"row_ref": "object#2", "column_key": "anatomy_9", "value": "x"}])
    with pytest.raises(ValueError, match="not an output column after split_list"):
        finalize_output_projection(bundle, bad)


@pytest.mark.asyncio
async def test_split_list_through_formatter_tools_with_lock_and_inventory():
    from src.lib.openai_agents.tools.output_formatter_tools import build_output_formatter_tools

    async def save(*_args):
        return {"file_id": "f", "filename": "f.csv", "download_url": "/f"}

    bundle = _split_bundle()
    plan = _split_plan(split={"header_template": "Anatomy Term {n}"})
    tools = {tool.name: tool for tool in build_output_formatter_tools(
        bundle=bundle, output_format="csv", formatter_agent_id="csv_formatter", save_projected_output=save)}

    async def call(name, payload):
        raw = await tools[name].on_invoke_tool(SimpleNamespace(tool_name=name), json.dumps(payload))
        return json.loads(raw)

    inventory = await call("inspect_output_artifacts", {"catalog_query": "anatomy_terms"})
    entry = inventory["inventory"]["field_catalog"]["entries"][0]
    assert entry["max_list_length"] == 3
    values = await call("inspect_field_values", {"row_source": "object", "field_ref": TERMS})
    assert values["max_list_length"] == 3
    preview = await call("preview_output_projection", {"plan_json": plan.model_dump_json()})
    assert preview["preview"]["preview_rows"][0]["anatomy_1"] == "hypodermis (WBbt:1)"
    assert [c["header"] for c in preview["preview"]["columns"]][:3] == [
        "Anatomy Term 1", "Anatomy Term 2", "Anatomy Term 3"]
    rejected = await call("validate_output_projection", {"plan_json": json.dumps({
        **json.loads(plan.model_dump_json()),
        "columns": [{**json.loads(plan.model_dump_json())["columns"][0], "split_list": {"bogus": 1}}]})})
    assert rejected["status"] == "invalid"

    # The selected-fields lock compares whole plans, so a changed split_list is a change.
    from src.lib.flows.output_projection import FlowOutputProjectionPlan as Plan

    locked = {**json.loads(plan.model_dump_json()), "selection_mode": "selected_fields"}
    changed = {**locked, "columns": [{**locked["columns"][0], "split_list": {"header_template": "Other {n}"}},
                                     locked["columns"][1]]}
    assert Plan.model_validate(locked) != Plan.model_validate(changed)


def test_declared_field_never_substitutes_another_field():
    """A resolved field renders only its own label/id (Chris, Sep 22)."""

    # Label and id empty: the mention is a separate column, not a substitute (ALL-1283:
    # a value with paper wording and no validated identity reads UNRESOLVED).
    assert display_text({"curie": None, "name": None, "mention": "PPIT-2"}, TERM) == "UNRESOLVED"
    assert display_text({"label": "", "mention": "gene X", "curie": ""},
                        {"label": "label", "id": "curie"}) == "UNRESOLVED"
    compose = {"compose": [{"path": "anatomical_structure", "display": TERM}], "separator": "; "}
    assert display_text({"anatomical_structure": {}, "statement": "free text"}, compose) == ""
    assert display_text({"mention": {"text": "unc-54(e190)"}}, {"label": "mention.text"}) == "unc-54(e190)"


def test_display_roles_must_be_single_leaf_paths():
    from pydantic import ValidationError

    from src.schemas.domain_pack_metadata import DomainPackFieldDefinition

    # Checked when the pack loads (ALL-1290), not when a bundle is built.
    with pytest.raises(ValidationError, match="single leaf path"):
        DomainPackFieldDefinition(field_path="gene_product",
                                  metadata={"display": {"label": ["label", "mention"]}})


_SHAPES_FIXTURE = (
    __import__("pathlib").Path(__file__).resolve().parents[3] / "fixtures" / "flows" / "display_value_shapes.json"
)


def _shape_cases(section):
    if not _SHAPES_FIXTURE.exists():
        return []
    return json.loads(_SHAPES_FIXTURE.read_text())[section]


@pytest.mark.skipif(not _SHAPES_FIXTURE.exists(), reason="display_value_shapes.json comes with the ALL-1282 packs")
@pytest.mark.parametrize("shape", _shape_cases("packaged"), ids=lambda shape: shape["shape_id"])
@pytest.mark.parametrize("output_format", ["csv", "tsv", "chat"])
def test_production_value_shapes_render_without_object_text(shape, output_format):
    step = {
        "step": 1, "node_id": "node_1", "agent_id": "shape_source", "agent_name": "Shape",
        "candidate": SimpleNamespace(
            agent_key="shape_source", adapter_key=shape["pack_id"], candidate_count=1,
            conversation_summary="shape",
            payload_json={"domain_pack_id": shape["pack_id"], "envelope_id": f"env-{shape['shape_id']}",
                          "extracted_objects": [{"object_type": shape["object_type"],
                                                 "object_id": shape["shape_id"], "payload": shape["payload"]}]},
        ),
    }
    bundle = build_flow_output_artifact_bundle(completed_steps=[step], flow_name="Shapes", output_format=output_format)
    refs = [field.ref for field in bundle.field_catalog
            if field.row_source == "object" and field.ref.startswith("object.pack.")]
    assert refs, "shape must map to declared pack fields"
    plan = FlowOutputProjectionPlan.model_validate({
        "format": output_format, "row_source": "object",
        "columns": [{"key": f"c{index}", "header": ref, "field_ref": ref} for index, ref in enumerate(refs)],
    })
    result = finalize_output_projection(bundle, plan)
    for value in result.rows[0].values():
        _no_object_text(str(value))
    default = finalize_output_projection(
        bundle, default_projection_plan(bundle, output_format=output_format, row_source="object"),
    )
    for value in default.rows[0].values():
        _no_object_text(str(value))


@pytest.mark.skipif(not _SHAPES_FIXTURE.exists(), reason="display_value_shapes.json comes with the ALL-1282 packs")
@pytest.mark.parametrize("shape", _shape_cases("custom_profiles"), ids=lambda shape: shape["shape_id"])
def test_custom_profile_value_shapes_render_without_object_text(shape):
    for value in (shape["payload"].get("attributes") or {}).values():
        _no_object_text(display_text(value))


# --- object.label is the declared label only (Chris, Sep 22) --------------------------

def test_gene_expression_object_label_is_the_declared_label():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_gene_expression_step()], flow_name="GE", output_format="csv",
    )
    labels = [row["object.label"] for row in bundle.rows_for_source("object")]
    assert labels == ["Y71G12B.17", "Y71G12B.17"]


def _gene_mention_step(payloads):
    return {
        "step": 1, "node_id": "node_1", "agent_id": "gene_extractor", "agent_name": "Gene",
        "candidate": SimpleNamespace(
            agent_key="gene_extractor", adapter_key="gene", candidate_count=len(payloads),
            conversation_summary="genes",
            payload_json={"domain_pack_id": "gene", "envelope_id": "env-gene", "extracted_objects": [
                {"object_type": "gene_mention_evidence", "object_id": f"m{index}", "payload": payload}
                for index, payload in enumerate(payloads)
            ]},
        ),
    }


def test_packaged_object_label_never_falls_back_to_mention(monkeypatch):
    original = export_fields._packaged_domain_pack
    pack = original("gene", {"curation": {"domain_pack_id": "gene"}})
    declared = SimpleNamespace(metadata=pack.metadata.model_copy(deep=True))
    for model in declared.metadata.model_definitions:
        if model.model_id == "GeneMentionEvidencePayload":
            model.metadata["display"] = {"label": "gene_symbol", "id": "primary_external_id"}
    monkeypatch.setattr(export_fields, "_packaged_domain_pack", lambda *_args, **_kwargs: declared)
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_gene_mention_step([
            {"gene_symbol": "unc-54", "primary_external_id": "WB:WBGene00006789", "mention": "UNC-54 myosin",
             "resolution_state": "resolved", "lookup_outcome": "matched"},
            {"gene_symbol": None, "primary_external_id": None, "mention": "PPIT-2", "symbol": "ppit-2",
             "name": "PPIT", "resolution_state": "unresolved", "lookup_outcome": "not_found"},
            # Stored before ALL-1283 and not covered by a validator event.
            {"gene_symbol": "egl-1", "primary_external_id": "WB:WBGene00001170", "mention": "EGL-1"},
        ])],
        flow_name="Genes", output_format="csv",
    )
    rows = bundle.rows_for_source("object")
    # The label never takes another field; an unresolved item's label is its
    # paper wording, labelled as such (ALL-1283).
    assert [row["object.label"] for row in rows] == [
        "unc-54", "PPIT-2 (paper wording)", "EGL-1 (legacy, unverified)"]
    plan = FlowOutputProjectionPlan.model_validate({
        "format": "csv", "row_source": "object", "missing_value": "—",
        "columns": [{"key": "label", "header": "Label", "field_ref": "object.label"}],
    })
    result = finalize_output_projection(bundle, plan)
    assert [row["label"] for row in result.rows] == [
        "unc-54", "PPIT-2 (paper wording)", "EGL-1 (legacy, unverified)"]
    json_result = finalize_output_projection(bundle, plan.model_copy(update={"format": "json"}))
    assert json_result.rows[1]["label"] == "PPIT-2 (paper wording)"


def test_custom_profile_object_label_is_its_payload_label():
    step = {
        "step": 1, "node_id": "node_1", "agent_id": "pdf_extraction", "agent_name": "PDF",
        "candidate": SimpleNamespace(
            agent_key="pdf_extraction", adapter_key="generic", candidate_count=2, conversation_summary="x",
            payload_json={"domain_pack_id": "generic", "envelope_id": "env-generic", "extracted_objects": [
                {"object_type": "generic_object", "object_id": "g1",
                 "payload": {"label": "B cell lymphoma", "symbol": "BCL", "attributes": {"a": "b"}}},
                {"object_type": "generic_object", "object_id": "g2",
                 "payload": {"symbol": "TCL", "name": "T cell lymphoma", "attributes": {"a": "c"}}},
            ]},
        ),
    }
    bundle = build_flow_output_artifact_bundle(completed_steps=[step], flow_name="Generic", output_format="csv")
    assert [row["object.label"] for row in bundle.rows_for_source("object")] == ["B cell lymphoma", None]


# --- Independent review of ALL-1282 (Sep 23) ------------------------------------------

def _envelope_step(agent_id, pack_id, objects, findings=(), *, step=1, node_id="node_1"):
    return {
        "step": step, "node_id": node_id, "agent_id": agent_id, "agent_name": agent_id,
        "candidate": SimpleNamespace(
            agent_key=agent_id, adapter_key=agent_id, candidate_count=len(objects),
            conversation_summary="x",
            payload_json={"domain_pack_id": pack_id, "envelope_id": f"env-{node_id}",
                          "extracted_objects": list(objects),
                          "validation_findings": list(findings)},
        ),
    }


def _open_finding(field_path, **object_ref):
    return {"finding_id": f"f-{field_path}", "status": "open", "severity": "warning",
            "field_path": field_path,
            "field_ref": {"object_ref": object_ref, "field_path": field_path}}


def _object_plan(output_format, columns):
    return FlowOutputProjectionPlan.model_validate({
        "format": output_format, "row_source": "object", "missing_value": "-",
        "columns": [{"key": key, "field_ref": ref} for key, ref in columns],
    })


def test_open_findings_only_mark_objects_in_their_own_envelope():
    """Pending ref ids restart per envelope; step 1's finding never marks step 2's row.

    A resolvable value's cell reads its own stored state, so the marker is shown on a
    plain field.
    """

    def annotation():
        return {"object_type": "GeneExpressionAnnotation", "pending_ref_id": "gene-expression-annotation-1",
                "payload": {"expression_annotation_subject": _validated_value(
                                "Y71", gene_symbol="Y71", primary_external_id="WB:1"),
                            "where_expressed_statement": "hyp"}}

    finding = _open_finding("where_expressed_statement", pending_ref_id="gene-expression-annotation-1",
                            object_type="GeneExpressionAnnotation")
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[
            _envelope_step("gene_expression", "agr.alliance.gene_expression", [annotation()], [finding]),
            _envelope_step("gene_expression", "agr.alliance.gene_expression", [annotation()],
                           step=2, node_id="node_2"),
        ],
        flow_name="P", output_format="csv",
    )
    result = apply_projection_plan(bundle, _object_plan("csv", [
        ("node", "artifact.node_id"),
        ("statement", "object.pack.GeneExpressionAnnotation.where_expressed_statement"),
    ]))
    marked = {row["node"]: "unresolved" in row["statement"] for row in result.rows}
    assert marked == {"node_1": True, "node_2": False}


def test_composite_marker_lands_on_the_unresolved_part():
    where = {"anatomical_structure": {"curie": "WBbt:1", "name": "hyp"},
             "cellular_component": {"curie": "GO:1", "name": "nucleus"}}
    compose = {"compose": [{"path": "anatomical_structure", "display": TERM},
                           {"path": "cellular_component", "display": TERM}], "separator": "; "}
    assert display_text(where, compose, unresolved=[("anatomical_structure",)]) == (
        "hyp (WBbt:1, unresolved); nucleus (GO:1)"
    )
    assert display_text(where, compose, unresolved=[("cellular_component", "curie")]) == (
        "hyp (WBbt:1); nucleus (GO:1, unresolved)"
    )
    # A finding on the composite itself, or on a part it does not display,
    # still marks the composite as a whole.
    assert display_text(where, compose, unresolved=True) == "hyp (WBbt:1); nucleus (GO:1) (unresolved)"
    assert display_text(where, compose, unresolved=[("subcellular_structure",)]) == (
        "hyp (WBbt:1); nucleus (GO:1) (unresolved)"
    )

    pattern_ref = "object.pack.GeneExpressionAnnotation.expression_pattern"
    pattern_spec = {"compose": [
        {"path": "where_expressed.anatomical_structure", "display": TERM},
        {"path": "when_expressed.developmental_stage_start", "display": TERM},
    ], "separator": "; "}
    row = {"object.object_id": "g1", pattern_ref: {
        "where_expressed": {"anatomical_structure": {"curie": "WBbt:1", "name": "hyp"}},
        "when_expressed": {"developmental_stage_start": {"curie": "WBls:1", "name": "L4"}},
    }}
    finding = {"object.object_id": "g1", "validation.status": "open",
               "validation.field_path": "expression_pattern.where_expressed.anatomical_structure"}
    bundle = _bundle([row], [FlowOutputField(ref=pattern_ref, label="Pattern", value_type="object",
                                             row_source="object", display=pattern_spec)], [finding])
    result = apply_projection_plan(bundle, _object_plan("csv", [("pattern", pattern_ref)]))
    assert result.rows[0]["pattern"] == "hyp (WBbt:1, unresolved); L4 (WBls:1)"


def test_composite_list_marker_lands_on_the_indexed_child():
    """A compose spec over a list places an indexed finding on the named child only."""

    relations = "object.pack.PhenotypeAnnotation.condition_relations"
    spec = {"compose": [
        {"path": "condition_relation_type.name", "display": None},
        {"path": "conditions", "display": {"label": "condition_summary", "id": "condition_class.curie"}},
    ], "separator": ": "}
    value = [
        {"condition_relation_type": {"name": "induced_by"}, "conditions": [
            {"condition_class": {"curie": "ZECO:1"}, "condition_summary": "heat"},
            {"condition_class": {"curie": "ZECO:2"}, "condition_summary": "diet"},
        ]},
        {"condition_relation_type": {"name": "has_condition"}, "conditions": [
            {"condition_class": {"curie": "ZECO:3"}, "condition_summary": "cold"},
        ]},
    ]
    catalog = [FlowOutputField(ref=relations, label="Relations", value_type="list",
                               row_source="object", display=spec)]

    def cell(field_path):
        finding = {"object.object_id": "p1", "validation.status": "open", "validation.field_path": field_path}
        bundle = _bundle([{"object.object_id": "p1", relations: deepcopy(value)}], catalog, [finding])
        return apply_projection_plan(bundle, _object_plan("csv", [("relations", relations)])).rows[0]["relations"]

    assert cell("condition_relations[0].conditions[1]") == (
        "induced_by: heat (ZECO:1), diet (ZECO:2, unresolved) | has_condition: cold (ZECO:3)"
    )
    assert cell("condition_relations[1].conditions[0].condition_class") == (
        "induced_by: heat (ZECO:1), diet (ZECO:2) | has_condition: cold (ZECO:3, unresolved)"
    )
    assert cell("condition_relations[0].condition_relation_type") == (
        "induced_by (unresolved): heat (ZECO:1), diet (ZECO:2) | has_condition: cold (ZECO:3)"
    )


def test_indexed_findings_mark_the_matching_fanned_out_position():
    relations = "object.pack.DiseaseAnnotation.condition_relations"
    summaries = f"{relations}.conditions.condition_summary"
    classes = f"{relations}.conditions.condition_class"
    relation_value = [{"condition_relation_type": {"name": "induced_by"}, "conditions": [
        {"condition_class": {"curie": "ZECO:1"}, "condition_summary": "heat"},
        {"condition_class": {"curie": "ZECO:2"}, "condition_summary": "diet"},
    ]}]
    row = {"object.object_id": "d1", relations: relation_value, summaries: [["heat", "diet"]],
           classes: [[{"curie": "ZECO:1"}, {"curie": "ZECO:2"}]]}
    finding = {"object.object_id": "d1", "validation.status": "open",
               "validation.field_path": "condition_relations[0].conditions[1]"}
    catalog = [FlowOutputField(ref=ref, label=ref, value_type="list", row_source="object")
               for ref in (relations, summaries, classes)]
    bundle = _bundle([row], catalog, [finding])
    result = apply_projection_plan(bundle, _object_plan("csv", [
        ("relations", relations), ("summaries", summaries), ("classes", classes),
    ]))
    cells = result.rows[0]
    assert cells["summaries"] == "heat, diet (unresolved)"
    assert cells["classes"] == "ZECO:1, ZECO:2 (unresolved)"
    assert cells["relations"] == (
        "condition_relation_type: induced_by; conditions: condition_class: ZECO:1; condition_summary: heat"
        ", condition_class: ZECO:2; condition_summary: diet (unresolved)"
    )
    # Split columns carry the marker only on the unresolved item.
    split = FlowOutputProjectionPlan.model_validate({
        "format": "csv", "row_source": "object", "missing_value": "-",
        "columns": [{"key": "cls", "field_ref": classes, "split_list": {"header_template": "Class {n}"}}],
    })
    # A split item that is itself a list joins like the whole cell (ALL-1290).
    assert apply_projection_plan(bundle, split).rows[0] == {"cls_1": "ZECO:1, ZECO:2 (unresolved)"}
    json_rows = apply_projection_plan(bundle, _object_plan("json", [("summaries", summaries)])).rows
    assert json_rows == [{"summaries": [["heat", "diet"]]}]


def test_default_layout_lists_only_curatable_units():
    for agent_id, pack_id, unit_types in (
        ("phenotype", "agr.alliance.phenotype", {"PhenotypeAnnotation"}),
        ("allele", "agr.alliance.allele", {"AllelePaperEvidenceAssociation"}),
        ("disease", "agr.alliance.disease", {"DiseaseAnnotation", "GeneDiseaseAnnotation",
                                             "AlleleDiseaseAnnotation", "AGMDiseaseAnnotation"}),
    ):
        entry = {"curation": {"domain_pack_id": pack_id}}
        source = export_fields.packaged_export_source(agent_id, entry, cache={})
        types = [obj.object_type for obj in source.domain_pack.metadata.object_definitions]
        refs = source.default_layout(types)
        assert refs, agent_id
        assert {ref.split(".")[2] for ref in refs} <= unit_types, (agent_id, refs)
    # A pack without curatable units lays out the objects it declares.
    gene_entry = {"curation": {"domain_pack_id": "gene"}}
    assert export_fields.packaged_export_source("gene", gene_entry, cache={}).default_layout(
        ["gene_mention_evidence"])


def test_lists_of_structured_records_keep_record_boundaries():
    candidates = {"candidates": [{"value": "A:1", "label": "a", "score": 1},
                                 {"value": "B:2", "label": "b", "score": 2}], "status": "ambiguous"}
    # A list inside a record joins its items with ", " (ALL-1290).
    assert display_text(candidates) == (
        "candidates: value: A:1; label: a; score: 1, value: B:2; label: b; score: 2; status: ambiguous"
    )
    assert display_text([{"curie": "A:1", "name": "a"}, {"curie": "B:2", "name": "b"}], TERM) == "a (A:1) | b (B:2)"
    assert display_text(["heat", "diet"]) == "heat; diet"
    # A second identifier is content too; the generic reading keeps it.
    assert display_text({"id": "X:1", "curie": "C:1", "name": "n"}) == "id: X:1; curie: C:1; name: n"


PHENOTYPE_PACK = "agr.alliance.phenotype"
PHENOTYPE_SUBJECT_REF = "object.pack.PhenotypeAnnotation.phenotype_annotation_subject"
PHENOTYPE_TERM_REF = "object.pack.PhenotypeAnnotation.phenotype_terms[0]"


def _phenotype_objects(term_count=1):
    # Terms without a stored state: open findings place the marker (ALL-1283 states decide otherwise).
    terms = [{"curie": f"WBPhenotype:{index}", "label": f"term {index}"}
             for index in range(term_count)]
    subject = {"resolution_state": "resolved", "lookup_outcome": "matched", "subject_label": "daf-2",
               "subject_identifier": "WB:WBGene00000898", "subject_type": "gene", "taxon": "NCBITaxon:6239"}
    annotation = {
        "object_type": "PhenotypeAnnotation", "pending_ref_id": "ann-1",
        "object_refs": [{"pending_ref_id": "subj-1", "object_type": "PhenotypeSubject"},
                        *({"pending_ref_id": f"term-{index}", "object_type": "PhenotypeTerm"}
                          for index in range(term_count))],
        "payload": {"phenotype_annotation_subject": dict(subject), "phenotype_terms": deepcopy(terms),
                    "phenotype_annotation_object": "reduced brood size"},
    }
    support = [{"object_type": "PhenotypeSubject", "pending_ref_id": "subj-1", "payload": dict(subject)}]
    support += [{"object_type": "PhenotypeTerm", "pending_ref_id": f"term-{index}", "payload": dict(term)}
                for index, term in enumerate(terms)]
    return [annotation, *support]


def test_object_ref_cells_carry_open_findings_on_the_referenced_object():
    findings = [
        {"finding_id": "f1", "status": "resolved", "severity": "info", "field_path": "subject_identifier",
         "field_ref": {"object_ref": {"pending_ref_id": "subj-1", "object_type": "PhenotypeSubject"},
                       "field_path": "subject_identifier"}},
        _open_finding("curie", pending_ref_id="term-0", object_type="PhenotypeTerm"),
    ]
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_envelope_step("phenotype", PHENOTYPE_PACK, _phenotype_objects(), findings)],
        flow_name="P", output_format="csv",
    )
    plan = FlowOutputProjectionPlan.model_validate({
        "format": "csv", "row_source": "object", "missing_value": "-",
        "filters": [{"field_ref": "object.object_type", "op": "eq", "value": "PhenotypeAnnotation"}],
        "columns": [{"key": "subject", "field_ref": PHENOTYPE_SUBJECT_REF},
                    {"key": "term", "field_ref": PHENOTYPE_TERM_REF}],
    })
    [row] = apply_projection_plan(bundle, plan).rows
    assert "unresolved" in row["term"]
    assert "unresolved" not in row["subject"]
    json_plan = plan.model_copy(update={"format": "json"})
    assert apply_projection_plan(bundle, json_plan).rows[0]["term"]["curie"] == "WBPhenotype:0"


def test_indexed_object_ref_field_maps_to_the_referenced_object_at_that_position():
    finding = _open_finding("curie", pending_ref_id="term-1", object_type="PhenotypeTerm")
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_envelope_step("phenotype", PHENOTYPE_PACK, _phenotype_objects(2), [finding])],
        flow_name="P", output_format="csv",
    )
    plan = FlowOutputProjectionPlan.model_validate({
        "format": "csv", "row_source": "object", "missing_value": "-",
        "filters": [{"field_ref": "object.object_type", "op": "eq", "value": "PhenotypeAnnotation"}],
        "columns": [{"key": "term", "field_ref": PHENOTYPE_TERM_REF}],
    })
    # phenotype_terms[0] references term-0; only term-1 has an open finding.
    assert "unresolved" not in apply_projection_plan(bundle, plan).rows[0]["term"]


def test_json_bundle_field_catalog_excludes_display_specs(monkeypatch):
    _declared_gene_expression_pack(monkeypatch)
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_gene_expression_step()], flow_name="GE", output_format="json",
    )
    assert any(field.display for field in bundle.field_catalog)
    plan = FlowOutputProjectionPlan.model_validate({
        "format": "json", "row_source": "object", "json_shape": "bundle",
        "columns": [{"key": "g", "field_ref": SUBJECT_REF}],
    })
    data = finalize_output_projection(bundle, plan).json_data
    assert all("display" not in field for field in data["field_catalog"])
    assert data["rows"][0]["g"]["gene_symbol"] == "Y71G12B.17"


def test_group_by_a_structured_field_uses_display_text(monkeypatch):
    _declared_gene_expression_pack(monkeypatch)
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_gene_expression_step()], flow_name="GE", output_format="chat",
    )
    plan = FlowOutputProjectionPlan.model_validate({
        "format": "chat", "row_source": "object", "group_by": [SUBJECT_REF],
        "columns": [{"key": "s", "header": "Statement", "field_ref": STATEMENT}],
    })
    chat = finalize_output_projection(bundle, plan).chat_output
    assert chat.count("## ") == 1
    assert "## Expression Annotation Subject: Y71G12B.17 (WB:WBGene00022155)" in chat
    _no_object_text(chat)
    grouped = finalize_output_projection(bundle, plan.model_copy(update={
        "format": "json", "json_shape": "grouped",
    })).json_data
    assert len(grouped) == 1
    assert grouped[0]["group"][SUBJECT_REF]["gene_symbol"] == "Y71G12B.17"


RESOLVABLE_TERM = {"label": "name", "id": "curie", "mention": "mention"}


def test_resolvable_values_read_label_id_or_the_literal_unresolved():
    """ALL-1283: resolved "label (ID)", unresolved UNRESOLVED, absent blank; never the mention."""

    resolved = {"curie": "WBbt:0005733", "name": "hypodermis", "mention": "hypodermal cells",
                "resolution_state": "resolved", "lookup_outcome": "matched"}
    unresolved = {"curie": None, "name": None, "mention": "structures associated with the residual body",
                  "resolution_state": "unresolved", "lookup_outcome": "not_found"}
    assert display_text(resolved, RESOLVABLE_TERM) == "hypodermis (WBbt:0005733)"
    assert display_text(unresolved, RESOLVABLE_TERM) == "UNRESOLVED"
    assert display_text(None, RESOLVABLE_TERM) == ""
    # The state decides; findings do not add markers to a resolvable value.
    assert display_text(resolved, RESOLVABLE_TERM, unresolved=True) == "hypodermis (WBbt:0005733)"
    assert display_text(unresolved, RESOLVABLE_TERM, marked=False) == "UNRESOLVED"
    # A legacy value the caller did not verify reads as unresolved.
    assert display_text({"curie": "WBbt:1", "name": "hyp"}, RESOLVABLE_TERM) == "UNRESOLVED"
    # List elements are separate values.
    assert display_text([resolved, unresolved], RESOLVABLE_TERM) == "hypodermis (WBbt:0005733) | UNRESOLVED"


def test_generic_reading_never_mixes_paper_wording_into_a_cell():
    """Custom profiles without a display spec (ALL-1283)."""

    assert display_text({"curie": "X:1", "name": "Y", "mention": "Z", "resolution_state": "resolved",
                         "lookup_outcome": "matched"}) == "Y (X:1)"
    assert display_text({"curie": None, "name": None, "mention": "Z", "resolution_state": "unresolved",
                         "lookup_outcome": "not_validated"}) == "UNRESOLVED"
    assert display_text({"curie": "X:1", "name": "Y", "mention": "Z"}) == "UNRESOLVED"
    assert display_text({"abbreviation": "WB", "mention": "WormBase", "resolution_state": "resolved",
                         "lookup_outcome": "matched"}) == "abbreviation: WB"


def test_display_mention_role_is_a_key_of_the_value():
    from pydantic import ValidationError

    from src.schemas.domain_pack_metadata import DomainPackFieldDefinition

    DomainPackFieldDefinition(field_path="term", metadata={"display": RESOLVABLE_TERM})
    with pytest.raises(ValidationError, match="keys of the value itself"):
        DomainPackFieldDefinition(field_path="term",
                                  metadata={"display": {"label": "term.name", "mention": "mention"}})
    with pytest.raises(ValidationError, match="takes no state"):
        DomainPackFieldDefinition(field_path="term", metadata={"display": {
            **RESOLVABLE_TERM, "state": "resolution_state", "resolved_states": ["resolved"]}})


def test_vocabulary_leaves_read_in_plain_words():
    from src.lib.domain_packs.resolvable_values import LOOKUP_OUTCOME_LABELS

    spec = {"value_labels": LOOKUP_OUTCOME_LABELS}
    assert display_text("not_found", spec) == "Not found"
    assert display_text("ambiguous", spec) == "Several matches"
    assert display_text(None, spec) == ""
    assert display_text("something else", spec) == "Invalid value (something else)"
