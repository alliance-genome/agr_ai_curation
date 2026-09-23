"""ALL-1290: remaining structured-display edge cases from the ALL-1282 review."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

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
from src.schemas.domain_pack_metadata import DomainPackFieldDefinition, DomainPackModelDefinition

TERM = {"label": "name", "id": "curie"}
CONDITION = {"label": "condition_summary", "id": "condition_class.curie"}
PHENOTYPE_TERM = {"label": "label", "id": "curie", "state": "resolution_state",
                  "resolved_states": ["resolved"]}


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


def _bundle(rows, catalog, findings=()):
    return FlowOutputArtifactBundle(
        flow_name="Display",
        artifacts=[FlowOutputArtifact(source_key="s", rows_by_source={
            "object": rows, "validation_finding": list(findings)})],
        field_catalog=catalog,
        default_row_source="object",
    )


def _plan(output_format, columns, **extra):
    return FlowOutputProjectionPlan.model_validate({
        "format": output_format, "row_source": "object", "missing_value": "-",
        "columns": columns, **extra,
    })


# --- ExperimentalCondition shows its chemical --------------------------------------------

CHEMICAL_RELATIONS = [{
    "condition_relation_type": {"name": "has_condition"},
    "conditions": [
        {"condition_class": {"curie": "ZECO:0000111"}, "condition_chemical": {"curie": "CHEBI:6909"},
         "condition_summary": "chemical treatment"},
        {"condition_chemical": {"curie": "CHEBI:16236", "name": "ethanol"}},
    ],
}]


@pytest.mark.parametrize("agent_id,pack_id,object_type", [
    ("gene_expression", "agr.alliance.gene_expression", "GeneExpressionAnnotation"),
])
@pytest.mark.parametrize("output_format", ["csv", "chat"])
def test_experimental_condition_cell_includes_its_chemical(agent_id, pack_id, object_type, output_format):
    ref = f"object.pack.{object_type}.condition_relations"
    item = {"object_type": object_type, "object_id": "a1",
            "payload": {"condition_relations": deepcopy(CHEMICAL_RELATIONS)}}
    finding = {"finding_id": "f1", "status": "open", "severity": "warning",
               "field_path": "condition_relations[0].conditions[0].condition_chemical",
               "field_ref": {"object_ref": {"object_id": "a1"},
                             "field_path": "condition_relations[0].conditions[0].condition_chemical"}}
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_envelope_step(agent_id, pack_id, [item])], flow_name="C", output_format=output_format,
    )
    [row] = apply_projection_plan(bundle, _plan(output_format, [{"key": "c", "field_ref": ref}])).rows
    assert row["c"] == (
        "has_condition: chemical treatment (ZECO:0000111) with CHEBI:6909, ethanol (CHEBI:16236)"
    )
    # An open finding on the chemical marks the chemical, not the condition class.
    marked = build_flow_output_artifact_bundle(
        completed_steps=[_envelope_step(agent_id, pack_id, [item], [finding])], flow_name="C",
        output_format=output_format,
    )
    [row] = apply_projection_plan(marked, _plan(output_format, [{"key": "c", "field_ref": ref}])).rows
    assert row["c"] == (
        "has_condition: chemical treatment (ZECO:0000111) with CHEBI:6909 (unresolved), ethanol (CHEBI:16236)"
    )
    # JSON keeps the stored value unchanged.
    [json_row] = apply_projection_plan(bundle, _plan("json", [{"key": "c", "field_ref": ref}])).rows
    assert json_row["c"] == CHEMICAL_RELATIONS


def _condition_value(mention, curie=None, **extra):
    resolved = curie is not None
    return {**extra, "curie": curie, "mention": mention,
            "resolution_state": "resolved" if resolved else "unresolved",
            "lookup_outcome": "matched" if resolved else "not_validated"}


@pytest.mark.parametrize("agent_id,pack_id,object_type", [
    ("phenotype", "agr.alliance.phenotype", "PhenotypeAnnotation"),
    ("disease", "agr.alliance.disease", "DiseaseAnnotation"),
])
@pytest.mark.parametrize("output_format", ["csv", "chat"])
def test_condition_cell_shows_each_part_by_its_own_state(agent_id, pack_id, object_type, output_format):
    """ALL-1283: every condition part is a resolvable value; paper wording stays out."""

    ref = f"object.pack.{object_type}.condition_relations"
    relations = [{
        "condition_relation_type": {"name": "has_condition", "mention": "has_condition",
                                    "resolution_state": "resolved", "lookup_outcome": "matched"},
        "conditions": [
            {"condition_class": _condition_value("chemical treatment", "ZECO:0000111"),
             "condition_chemical": _condition_value("rapamycin", proposed_curie="CHEBI:9168"),
             "condition_free_text": "3 pM",
             "condition_summary": "treated with 3 pM rapamycin"},
        ],
    }]
    item = {"object_type": object_type, "object_id": "a1",
            "payload": {"condition_relations": deepcopy(relations)}}
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_envelope_step(agent_id, pack_id, [item])],
        flow_name="C", output_format=output_format,
    )
    [row] = apply_projection_plan(bundle, _plan(output_format, [{"key": "c", "field_ref": ref}])).rows
    assert row["c"] == "has_condition: ZECO:0000111; UNRESOLVED; 3 pM"
    assert "rapamycin" not in row["c"]
    [json_row] = apply_projection_plan(bundle, _plan("json", [{"key": "c", "field_ref": ref}])).rows
    assert json_row["c"] == relations


# --- Generic reading keeps a second identifier ---------------------------------------------

def test_generic_display_keeps_both_identifiers():
    assert display_text({"id": "X:1", "curie": "C:1", "name": "n"}) == "id: X:1; curie: C:1; name: n"
    assert display_text({"identifier": "I:1", "curie": "C:1"}) == "identifier: I:1; curie: C:1"
    assert display_text([{"id": "X:1", "curie": "C:1"}, {"curie": "C:2", "name": "b"}]) == (
        "id: X:1; curie: C:1 | b (C:2)"
    )


# --- A template is never chosen by another field's value (ALL-1283) ------------------------

TERMS_REF = "object.pack.PhenotypeAnnotation.phenotype_terms"
STATE_REF = "object.pack.PhenotypeAnnotation.phenotype_terms.resolution_state"


def _status_template_bundle(findings=()):
    rows = [{"artifact.is_canonical_curation_data": True, "object.object_id": "p1", TERMS_REF: [
        {"curie": "WBPhenotype:1", "label": "slow"},
        {"curie": "WBPhenotype:2", "label": "small"},
    ], STATE_REF: ["resolved", "pending_lookup"]}]
    catalog = [FlowOutputField(ref=TERMS_REF, label="Terms", value_type="list", row_source="object",
                               display=PHENOTYPE_TERM),
               FlowOutputField(ref=STATE_REF, label="States", value_type="list", row_source="object")]
    return _bundle(rows, catalog, findings)


@pytest.mark.parametrize("selector", [
    {"field_ref": STATE_REF, "mapping": {"pending_lookup": "{1} (unresolved)"}},
    {"field_ref": STATE_REF},
    {"mapping": {"pending_lookup": "{1} (unresolved)"}},
])
def test_format_elements_rejects_a_per_element_template_selector(selector):
    plan = _plan("csv", [{"key": "terms", "transform": {
        "type": "format_elements", "field_refs": [TERMS_REF], "default": "{1}", "separator": "; ", **selector,
    }}])
    with pytest.raises(ValueError, match="template selector is not supported"):
        apply_projection_plan(_status_template_bundle(), plan)


def test_format_elements_renders_every_element_with_its_one_template():
    finding = {"object.object_id": "p1", "validation.status": "open", "validation.field_path": "phenotype_terms[1]"}
    plan = _plan("csv", [{"key": "terms", "transform": {
        "type": "format_elements", "field_refs": [TERMS_REF], "default": "[{1}]", "separator": " ",
    }}])
    [row] = apply_projection_plan(_status_template_bundle([finding]), plan).rows
    assert row["terms"] == "[slow (WBPhenotype:1)] [small (WBPhenotype:2, unresolved)]"


def test_list_elements_join_their_items_like_the_whole_cell():
    nested = "object.pack.PhenotypeAnnotation.condition_relations.conditions.condition_summary"
    rows = [{"artifact.is_canonical_curation_data": True, "object.object_id": "p1",
             nested: [["heat", "diet"], ["cold"]]}]
    bundle = _bundle(rows, [FlowOutputField(ref=nested, label="Conditions", value_type="list", row_source="object")])
    plan = _plan("csv", [
        {"key": "cell", "field_ref": nested},
        {"key": "split", "field_ref": nested, "split_list": {"header_template": "Relation {n}"}},
        {"key": "elements", "transform": {"type": "format_elements", "field_refs": [nested],
                                          "default": "[{1}]", "separator": " "}},
        {"key": "joined", "transform": {"type": "join_list", "field_ref": nested, "separator": " / "}},
    ])
    [row] = apply_projection_plan(bundle, plan).rows
    assert row == {"cell": "heat, diet | cold", "split_1": "heat, diet", "split_2": "cold",
                   "elements": "[heat, diet] [cold]", "joined": "heat, diet / cold"}


def test_self_part_marks_only_the_leaves_it_displays():
    spec = {"compose": [{"display": CONDITION}, {"path": "condition_chemical", "display": None}],
            "separator": " with "}
    condition = {"condition_class": {"curie": "ZECO:1"}, "condition_summary": "heat",
                 "condition_chemical": {"curie": "CHEBI:1"}, "condition_taxon": {"curie": "NCBITaxon:1"}}
    assert display_text(condition, spec, unresolved=[("condition_summary",)]) == (
        "heat (ZECO:1, unresolved) with CHEBI:1"
    )
    assert display_text(condition, spec, unresolved=[("condition_class",)]) == (
        "heat (ZECO:1, unresolved) with CHEBI:1"
    )
    # A finding on an undisplayed field marks the whole composite, not the summary part.
    assert display_text(condition, spec, unresolved=[("condition_taxon", "curie")]) == (
        "heat (ZECO:1) with CHEBI:1 (unresolved)"
    )


# --- map_value keys structured values by display text --------------------------------------

ANATOMY = "object.pack.GeneExpressionAnnotation.expression_pattern.where_expressed.anatomical_structure"


@pytest.mark.parametrize("output_format", ["csv", "json"])
def test_map_value_keys_structured_values_by_display_text(output_format):
    rows = [{"object.object_id": "g1", ANATOMY: {"curie": "WBbt:0005733", "name": "hypodermis"}},
            {"object.object_id": "g2", ANATOMY: {"name": "residual body"}},
            {"object.object_id": "g3", ANATOMY: ["a", "b"]}]
    finding = {"object.object_id": "g1", "validation.status": "open",
               "validation.field_path": "expression_pattern.where_expressed.anatomical_structure"}
    bundle = _bundle(rows, [FlowOutputField(ref=ANATOMY, label="Anatomy", value_type="object",
                                            row_source="object", display=TERM)], [finding])
    plan = _plan(output_format, [{"key": "tissue", "transform": {
        "type": "map_value", "field_ref": ANATOMY, "default": "other",
        "mapping": {"hypodermis (WBbt:0005733)": "epidermis", "residual body": "residual",
                    "a; b": "list"},
    }}])
    # Keys are the value's display text without any unresolved marker.
    assert [row["tissue"] for row in apply_projection_plan(bundle, plan).rows] == [
        "epidermis", "residual", "list",
    ]


# --- Pack display specs: resolved once per bundle, validated at load -----------------------

def _gene_expression_item(object_id):
    return {"object_type": "GeneExpressionAnnotation", "object_id": object_id,
            "payload": {"expression_annotation_subject": {"gene_symbol": "Y71", "primary_external_id": "WB:1"},
                        "where_expressed_statement": "hyp"}}


def test_pack_display_specs_resolve_once_per_bundle(monkeypatch):
    original = export_fields._packaged_domain_pack
    calls = []

    def counted(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(export_fields, "_packaged_domain_pack", counted)
    steps = [_envelope_step("gene_expression", "agr.alliance.gene_expression", [_gene_expression_item(f"g{n}")],
                            step=n, node_id=f"node_{n}") for n in (1, 2, 3)]
    bundle = build_flow_output_artifact_bundle(completed_steps=steps, flow_name="GE", output_format="csv")
    assert len(bundle.artifacts) == 3
    assert len(calls) == 1
    subject = "object.pack.GeneExpressionAnnotation.expression_annotation_subject"
    assert {field.ref: field.display for field in bundle.field_catalog}[subject] == {
        "label": "gene_symbol", "id": "primary_external_id",
    }


def test_packaged_source_without_pack_id_lists_agents_once(monkeypatch):
    from src.lib.config import agent_loader

    original = agent_loader.list_agents
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(agent_loader, "list_agents", counted)
    catalogs = {}
    first = export_fields.packaged_export_source("gene_expression", None, cache=catalogs)
    again = export_fields.packaged_export_source("gene_expression", None, cache=catalogs)
    assert first is again and first is not None
    assert len(calls) == 1
    assert first.display_specs and first.fields


@pytest.mark.parametrize("display,message", [
    ({"label": ["label", "mention"]}, "single leaf path"),
    ({"id": ""}, "single leaf path"),
    ({"label": "name", "colour": "red"}, "unknown display key"),
    ({"state": "resolution_state"}, "needs a label, id or compose"),
    ({"label": "name", "state": "resolution_state"}, "resolved_states"),
    ({"label": "name", "compose": ["a"]}, "compose cannot be combined"),
    ({"compose": []}, "compose must list"),
    ({"compose": [{"separator": ";"}]}, "compose entry"),
    ({"compose": ["a"], "separator": 3}, "separator must be a string"),
    ({"compose": [{"display": {"label": ["a", "b"]}}]}, "single leaf path"),
])
def test_display_declarations_are_validated_when_a_pack_loads(display, message):
    with pytest.raises(ValidationError, match=message):
        DomainPackModelDefinition(model_id="M", display_name="M", metadata={"display": display})
    with pytest.raises(ValidationError, match=message):
        DomainPackFieldDefinition(field_path="f", metadata={"display": display})


def test_packaged_domain_packs_load_with_valid_display_declarations():
    from src.lib.flows.validation_attachments import domain_pack_validation_registries

    registries = domain_pack_validation_registries()
    declared = [
        model.metadata["display"]
        for registry in registries.values()
        for model in registry.domain_pack.metadata.model_definitions
        if model.metadata.get("display")
    ]
    assert declared


# --- Default rows: curatable units only ---------------------------------------------------

def _phenotype_objects():
    subject = {"resolution_state": "resolved", "subject_label": "daf-2",
               "subject_identifier": "WB:WBGene00000898", "subject_type": "gene"}
    term = {"curie": "WBPhenotype:0", "label": "term 0", "resolution_state": "resolved"}
    annotation = {
        "object_type": "PhenotypeAnnotation", "pending_ref_id": "ann-1",
        "object_refs": [{"pending_ref_id": "subj-1", "object_type": "PhenotypeSubject"},
                        {"pending_ref_id": "term-0", "object_type": "PhenotypeTerm"}],
        "payload": {"phenotype_annotation_subject": dict(subject), "phenotype_terms": [dict(term)],
                    "phenotype_annotation_object": "reduced brood size"},
    }
    return [annotation,
            {"object_type": "PhenotypeSubject", "pending_ref_id": "subj-1", "payload": dict(subject)},
            {"object_type": "PhenotypeTerm", "pending_ref_id": "term-0", "payload": dict(term)}]


@pytest.mark.parametrize("output_format", ["csv", "tsv", "chat", "json"])
def test_default_plan_rows_are_the_curatable_units(output_format):
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_envelope_step("phenotype", "agr.alliance.phenotype", _phenotype_objects())],
        flow_name="P", output_format=output_format,
    )
    plan = default_projection_plan(bundle, output_format=output_format, row_source="object")
    assert [(f.field_ref, f.op, f.values) for f in plan.filters] == [
        ("object.object_type", "in", ["PhenotypeAnnotation"]),
    ]
    result = finalize_output_projection(bundle, plan)
    assert result.total_count == 1
    # Supporting objects stay available when a plan asks for them.
    everything = finalize_output_projection(bundle, plan.model_copy(update={"filters": []}))
    assert everything.total_count == 3


def test_default_plan_keeps_every_row_without_supporting_objects():
    steps = [
        _envelope_step("gene_expression", "agr.alliance.gene_expression", [_gene_expression_item("g1")]),
        _envelope_step("phenotype", "agr.alliance.phenotype", _phenotype_objects()[1:], step=2, node_id="node_2"),
    ]
    bundle = build_flow_output_artifact_bundle(completed_steps=steps[:1], flow_name="GE", output_format="csv")
    assert default_projection_plan(bundle, output_format="csv", row_source="object").filters == []
    # A source whose curatable units are absent keeps its own rows next to other sources' units.
    mixed = build_flow_output_artifact_bundle(completed_steps=steps, flow_name="M", output_format="csv")
    plan = default_projection_plan(mixed, output_format="csv", row_source="object")
    assert finalize_output_projection(mixed, plan).total_count == 3


@pytest.mark.asyncio
async def test_formatter_default_plan_for_a_selected_source_filters_to_units():
    import json

    from src.lib.openai_agents.tools.output_formatter_tools import build_output_formatter_tools

    async def save(*_args):
        return {"file_id": "f", "filename": "f.csv", "download_url": "/f"}

    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_envelope_step("phenotype", "agr.alliance.phenotype", _phenotype_objects())],
        flow_name="P", output_format="csv",
    )
    source_key = bundle.artifacts[0].source_key
    tools = {tool.name: tool for tool in build_output_formatter_tools(
        bundle=bundle, output_format="csv", formatter_agent_id="csv_formatter", save_projected_output=save)}
    raw = await tools["build_default_projection_plan"].on_invoke_tool(
        SimpleNamespace(tool_name="build_default_projection_plan"),
        json.dumps({"row_source": "object", "source_ref": source_key}),
    )
    response = json.loads(raw)
    assert response["status"] == "ok", response
    assert response["plan"]["source_keys"] == [source_key]
    assert response["plan"]["filters"] == [
        {"field_ref": "object.object_type", "op": "in", "values": ["PhenotypeAnnotation"]},
    ]


# --- Nested lists inside a record --------------------------------------------------------

def test_nested_lists_inside_a_record_use_commas_between_items():
    relations = "object.pack.PhenotypeAnnotation.condition_relations"
    spec = {"compose": [{"path": "condition_relation_type.name", "display": None},
                        {"path": "conditions", "display": CONDITION}], "separator": ": "}
    value = [
        {"condition_relation_type": {"name": "induced_by"}, "conditions": [
            {"condition_class": {"curie": "ZECO:1"}, "condition_summary": "heat"},
            {"condition_class": {"curie": "ZECO:2"}, "condition_summary": "diet"},
        ]},
        {"condition_relation_type": {"name": "has_condition"}, "conditions": [
            {"condition_class": {"curie": "ZECO:3"}, "condition_summary": "cold"},
        ]},
    ]
    assert display_text(value, spec) == (
        "induced_by: heat (ZECO:1), diet (ZECO:2) | has_condition: cold (ZECO:3)"
    )
    bundle = _bundle([{"object.object_id": "p1", relations: value}],
                     [FlowOutputField(ref=relations, label="Relations", value_type="list",
                                      row_source="object", display=spec)])
    [row] = apply_projection_plan(bundle, _plan("csv", [{"key": "r", "field_ref": relations}])).rows
    assert row["r"] == "induced_by: heat (ZECO:1), diet (ZECO:2) | has_condition: cold (ZECO:3)"
    # Generic records: inner lists use ", " so "; " and " | " keep their meaning.
    assert display_text({"synonyms": ["a", "b"], "curie": "X:1"}) == "synonyms: a, b; curie: X:1"
    assert display_text([["heat", "cold"], ["dark"]]) == "heat, cold | dark"
    assert display_text(["heat", "diet"]) == "heat; diet"
