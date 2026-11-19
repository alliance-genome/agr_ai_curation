"""ALL-1561: records a finished flow review set aside, in files and chat tables.

"Review status" (accepted / set aside) is an optional review field like Review
reason. Selecting it is what puts set-aside records in an output (CSV, TSV,
JSON and chat alike); otherwise they, and the evidence and finding rows naming
them, are left out and counted. The real history read is in the persistence
test test_review_set_aside; here the reviewed read is a double.
"""

import csv
import io
import json

import pytest

from src.lib.flows import export_fields
from src.lib.flows.output_projection import (
    FlowOutputFilterSpec, FlowOutputSortSpec, _uncertainty_field_label, apply_projection_plan, build_flow_output_artifact_bundle,
    default_projection_plan, set_aside_record_views,
)
from src.lib.openai_agents.tools.file_output_tools import _projection_content_for_file_type
from src.lib.openai_agents.tools.output_formatter_tools import (
    _is_intentional_empty_projection, _set_aside_summary,
)
from src.schemas.domain_envelope import DomainEnvelope
from tests.unit.lib.flows.test_value_display import _envelope_step, _object_plan

STATUS = export_fields.REVIEW_STATUS_REF
REASON = export_fields.REVIEW_REASON_REF
STATEMENT = "object.pack.GeneExpressionAnnotation.where_expressed_statement"
PACK = "agr.alliance.gene_expression"


def _object(key, statement):
    return {"object_type": "GeneExpressionAnnotation", "pending_ref_id": key,
            "payload": {"where_expressed_statement": statement}, "evidence_record_ids": [f"ev-{key}"],
            "metadata": {"validation_history": {"target_id": f"target-{key}"}}}


def _reviewed_step(objects, pack=PACK, agent="gene_expression", node_id="node_1"):
    step = _envelope_step(agent, pack, objects, node_id=node_id)
    step["candidate"].payload_json["metadata"] = {
        "validation_history": {"artifact_id": f"artifact-{node_id}", "session_id": "history-session"},
        # The envelope's evidence inventory; records cite it by ID.
        "evidence_records": [{"evidence_record_id": f"ev-{item['pending_ref_id']}",
                              "quote": f"Quote for {item['pending_ref_id']}."} for item in objects]}
    return step


@pytest.fixture
def reviewed(monkeypatch):
    """The current publication: ``state['pending']`` records are pending, of which a
    finished run's set-aside fact covers ``state['set_aside']``; reasons are ``state['reasons']``."""

    state = {"set_aside": set(), "pending": set(), "reasons": {}, "reads": []}

    def read(scope, reference, *, reasons=True):
        state["reads"].append(reasons)
        payload = state["payloads"][reference["fact_id"]]
        return (DomainEnvelope.model_validate(payload), {"blockers": []},
                state["reasons"] if reasons else {}, set(state["pending"]), set(state["set_aside"]))

    monkeypatch.setattr("src.lib.validation_history.projection.latest_publication_reference",
                        lambda scope, artifact_id: {"fact_id": artifact_id})
    monkeypatch.setattr("src.lib.validation_history.projection.read_current_projection_with_reasons", read)
    return state


def _bundle(state, objects, *, aside, pending=(), reasons=None, review_reasons=True, pack=PACK,
            agent="gene_expression", extra_steps=()):
    steps = [_reviewed_step(objects, pack, agent), *extra_steps]
    state.update(payloads={f"artifact-{step['node_id']}": step["candidate"].payload_json for step in steps},
                 set_aside={f"target-{key}" for key in aside},
                 pending={f"target-{key}" for key in (*aside, *pending)}, reasons=reasons or {})
    return build_flow_output_artifact_bundle(
        user_id="curator", document_id="00000000-0000-0000-0000-000000000001",
        completed_steps=steps, flow_name="Set aside", output_format="tsv", review_reasons=review_reasons)


def _cells(content, output_format):
    return list(csv.reader(io.StringIO(content), delimiter="\t" if output_format == "tsv" else ","))


def test_review_status_is_a_review_field_outside_layouts_and_the_fingerprint():
    refs = [field["ref"] for field in export_fields.REVIEW_FIELDS]
    assert refs == [STATUS, export_fields.REVIEW_UNCERTAINTY_REF, REASON]
    labels = {field["ref"]: field["label"] for field in export_fields.REVIEW_FIELDS}
    assert labels[STATUS] == "Review status"
    fields = [{"ref": "object.attribute.count", "label": "Count", "value_type": "integer"}]
    with_review = export_fields.source_catalog(fields, {"receipt": 1})
    without = export_fields.catalog_fingerprint([*fields, *export_fields.COMMON_FIELDS], {"receipt": 1})
    assert with_review["schema_fingerprint"] == without


@pytest.mark.parametrize("output_format", ["csv", "tsv"])
def test_a_file_without_review_status_leaves_set_aside_records_out(reviewed, output_format):
    bundle = _bundle(reviewed, [_object("kept", "in the gut"), _object("aside", "near the gut")], aside={"aside"})
    [artifact] = bundle.artifacts
    statuses = {row["object.pending_ref_id"]: row[STATUS] for row in artifact.rows_by_source["object"]}
    assert statuses == {"kept": "accepted", "aside": "set aside"}
    assert not {STATUS} & set(artifact.default_object_refs)
    default = default_projection_plan(bundle, output_format=output_format)
    assert STATUS not in {column.field_ref for column in default.columns}

    plain = apply_projection_plan(bundle, _object_plan(output_format, [("statement", STATEMENT)]))
    assert _cells(_projection_content_for_file_type(output_format=output_format, projection=plain),
                  output_format) == [["statement"], ["in the gut"]]
    assert (plain.set_aside_left_out, plain.set_aside_included) == (1, 0)
    assert plain.export_accounting.candidate_row_count == 1
    assert _set_aside_summary([plain]) == {"included": 0, "left_out": 1}

    marked = apply_projection_plan(bundle, _object_plan(output_format, [("statement", STATEMENT),
                                                                         ("Review status", STATUS)]))
    assert _cells(_projection_content_for_file_type(output_format=output_format, projection=marked),
                  output_format) == [["statement", "Review status"], ["in the gut", "accepted"],
                                     ["near the gut", "set aside"]]
    assert (marked.set_aside_left_out, marked.set_aside_included) == (0, 1)


def test_json_follows_the_same_rule(reviewed):
    bundle = _bundle(reviewed, [_object("kept", "in the gut"), _object("aside", "near the gut")], aside={"aside"})
    plain = apply_projection_plan(bundle, _object_plan("json", [("statement", STATEMENT)]))
    assert json.loads(_projection_content_for_file_type(output_format="json", projection=plain)) == [
        {"statement": "in the gut"}]
    marked = apply_projection_plan(bundle, _object_plan("json", [("statement", STATEMENT), ("status", STATUS)]))
    assert json.loads(_projection_content_for_file_type(output_format="json", projection=marked)) == [
        {"statement": "in the gut", "status": "accepted"}, {"statement": "near the gut", "status": "set aside"}]


def test_a_chat_table_follows_the_same_rule(reviewed):
    """Chris, decision 4: tables in chat follow the same rule as files."""
    bundle = _bundle(reviewed, [_object("kept", "in the gut"), _object("aside", "near the gut")], aside={"aside"})
    plain = apply_projection_plan(bundle, _object_plan("chat", [("statement", STATEMENT)]))
    assert "in the gut" in plain.chat_output and "near the gut" not in plain.chat_output
    assert plain.set_aside_left_out == 1
    marked = apply_projection_plan(bundle, _object_plan("chat", [("statement", STATEMENT), ("status", STATUS)]))
    assert "near the gut" in marked.chat_output and "set aside" in marked.chat_output


def test_evidence_rows_of_a_set_aside_record_are_left_out_with_it(reviewed):
    bundle = _bundle(reviewed, [_object("kept", "in the gut"), _object("aside", "near the gut")], aside={"aside"})
    plan = _object_plan("csv", [("quote", "evidence.quote")]).model_copy(update={"row_source": "evidence"})
    rows = apply_projection_plan(bundle, plan).rows
    assert rows == [{"quote": "Quote for kept."}]



def test_a_filter_or_sort_on_review_status_alone_neither_includes_nor_counts_as_included(reviewed):
    """Only a Review status column includes set-aside records; the counts say what reached the output."""
    bundle = _bundle(reviewed, [_object("kept", "in the gut"), _object("aside", "near the gut")], aside={"aside"})
    only_aside = _object_plan("csv", [("statement", STATEMENT)]).model_copy(update={
        "filters": [FlowOutputFilterSpec(field_ref=STATUS, op="eq", value="set aside")]})
    result = apply_projection_plan(bundle, only_aside)
    assert result.rows == []
    assert (result.set_aside_included, result.set_aside_left_out) == (0, 1)
    sorted_plan = _object_plan("csv", [("statement", STATEMENT)]).model_copy(update={
        "sort": [FlowOutputSortSpec(field_ref=STATUS, direction="desc")]})
    result = apply_projection_plan(bundle, sorted_plan)
    assert result.rows == [{"statement": "in the gut"}]
    assert (result.set_aside_included, result.set_aside_left_out) == (0, 1)
    # With the column, a filter that keeps accepted records only leaves the set-aside one out.
    accepted_only = _object_plan("csv", [("statement", STATEMENT), ("status", STATUS)]).model_copy(update={
        "filters": [FlowOutputFilterSpec(field_ref=STATUS, op="eq", value="accepted")]})
    result = apply_projection_plan(bundle, accepted_only)
    assert result.rows == [{"statement": "in the gut", "status": "accepted"}]
    assert (result.set_aside_included, result.set_aside_left_out) == (0, 1)


def test_a_source_without_review_status_never_contributes_set_aside_records(reviewed):
    """M1: decided per source. The unprofiled source's set-aside record stays out even when a
    profiled source in the same output selects Review status, and counts as left out."""
    general = [{"object_type": "generic_object", "pending_ref_id": key,
                "payload": {"label": label, "attributes": {"source": label}},
                "metadata": {"validation_history": {"target_id": f"target-{key}"}}}
               for key, label in (("g-kept", "VT1"), ("g-aside", "VT2"))]
    bundle = _bundle(reviewed, [_object("kept", "in the gut"), _object("aside", "near the gut")],
                     aside={"aside", "g-aside"},
                     extra_steps=[_reviewed_step(general, "generic", "pdf", node_id="node_2")])
    plan = _object_plan("json", [("statement", STATEMENT), ("label", "object.label"), ("status", STATUS)])
    result = apply_projection_plan(bundle, plan.model_copy(update={"missing_value": None}))
    assert [row["status"] for row in result.rows if row["statement"]] == ["accepted", "set aside"]
    assert [row["label"] for row in result.rows if row["statement"] is None] == ["VT1"]
    assert (result.set_aside_included, result.set_aside_left_out) == (1, 1)
    assert _set_aside_summary([result]) == {"included": 1, "left_out": 1}


def test_review_status_is_blank_for_a_pending_record_no_finished_run_set_aside(reviewed):
    """Outside a finished run's set-aside fact nothing is set aside: previews keep pending
    records, and Review status says nothing about them."""
    bundle = _bundle(reviewed, [_object("kept", "in the gut"), _object("open", "near the gut")],
                     aside=set(), pending={"open"})
    rows = apply_projection_plan(bundle, _object_plan("json", [("statement", STATEMENT), ("status", STATUS)]).model_copy(
        update={"missing_value": None})).rows
    assert rows == [{"statement": "in the gut", "status": "accepted"}, {"statement": "near the gut", "status": None}]


def test_when_every_record_is_set_aside_the_empty_output_is_intentional(reviewed):
    bundle = _bundle(reviewed, [_object("aside", "near the gut")], aside={"aside"})
    result = apply_projection_plan(bundle, _object_plan("tsv", [("statement", STATEMENT)]))
    assert result.rows == [] and result.empty_reason == "all_records_set_aside"
    assert result.set_aside_left_out == 1
    assert _is_intentional_empty_projection(result)


def test_review_reason_carries_the_set_aside_reason(reviewed):
    """Chris, decision 7: Review reason also carries the set-aside reason for set-aside rows."""
    bundle = _bundle(reviewed, [_object("kept", "in the gut"), _object("aside", "near the gut")], aside={"aside"},
                     reasons={"target-kept": [(None, None, "The figure legend names the gut.")],
                              "target-aside": [(None, None, "The paper does not say which tissue.")]})
    rows = apply_projection_plan(bundle, _object_plan("tsv", [("status", STATUS), ("reason", REASON)])).rows
    assert rows == [{"status": "accepted", "reason": "The figure legend names the gut."},
                    {"status": "set aside", "reason": "The paper does not say which tissue."}]


def test_review_reasons_are_read_only_when_the_output_can_show_them(reviewed):
    _bundle(reviewed, [_object("kept", "in the gut")], aside=set(), review_reasons=False)
    _bundle(reviewed, [_object("kept", "in the gut")], aside=set())
    assert reviewed["reads"] == [False, True]


def test_a_saved_selected_fields_plan_decides_whether_reasons_are_read():
    from src.lib.flows.executor import _shows_review_reason

    def node(*refs):
        return {"projection_plan": {"format": "tsv", "row_source": "object", "selection_mode": "selected_fields",
                                    "selected_sources": [{"node_id": "extract", "schema_fingerprint": "sha256:x"}],
                                    "columns": [{"key": ref, "field_ref": ref, "source_node_id": "extract"}
                                                for ref in refs]}}

    assert _shows_review_reason({}) is True
    assert _shows_review_reason({"projection_plan": {"format": "tsv", "selection_mode": "ai"}}) is True
    assert _shows_review_reason(node(STATEMENT, STATUS)) is False
    assert _shows_review_reason(node(STATEMENT, REASON)) is True


def test_viewer_lists_each_set_aside_record_with_its_default_values_and_reason(reviewed):
    bundle = _bundle(reviewed, [_object("kept", "in the gut"), _object("aside", "near the gut")], aside={"aside"},
                     reasons={"target-aside": [(None, None, "The paper does not say which tissue.")]})
    [view] = set_aside_record_views(bundle)
    assert view["review_status"] == "set aside"
    assert view["review_reason"] == "The paper does not say which tissue."
    values = {field["field_ref"]: field["value"] for field in view["fields"]}
    assert values[STATEMENT] == "near the gut"
    assert not {STATUS, REASON, export_fields.REVIEW_UNCERTAINTY_REF} & set(values)
    assert STATUS in {field.ref for field in bundle.artifacts[0].declared_fields}


def test_unprofiled_general_results_offer_no_review_status_and_still_leave_set_aside_records_out(reviewed):
    """Chris, decision 5: no review fields offered; the records stay out and the viewer still lists them."""
    objects = [{"object_type": "generic_object", "pending_ref_id": key,
                "payload": {"label": label, "attributes": {"source": label}},
                "metadata": {"validation_history": {"target_id": f"target-{key}"}}}
               for key, label in (("kept", "VT1"), ("aside", "VT2"))]
    bundle = _bundle(reviewed, objects, aside={"aside"}, pack="generic", agent="pdf")
    [artifact] = bundle.artifacts
    assert artifact.declared_fields == []
    assert all(STATUS not in row for row in artifact.rows_by_source["object"])
    rows = apply_projection_plan(bundle, _object_plan("csv", [("source", "object.attribute.source")])).rows
    assert rows == [{"source": "VT1"}]
    [view] = set_aside_record_views(bundle)
    assert view["label"] == "VT2"


def test_a_list_items_doubt_reads_its_list_fields_declared_label_and_position():
    labels = {("Gene", "evidence_codes"): "Evidence code", ("Gene", "symbol"): "Symbol",
              ("Gene", "attributes.tags[]"): "Tag"}
    assert _uncertainty_field_label(labels, "Gene", "symbol") == "Symbol"
    assert _uncertainty_field_label(labels, "Gene", "evidence_codes[0]") == "Evidence code 1"
    assert _uncertainty_field_label(labels, "Gene", "evidence_codes[11]") == "Evidence code 12"
    assert _uncertainty_field_label(labels, "Gene", "attributes.tags[2]") == "Tag 3"
    # No declared label: the stored path, as before.
    assert _uncertainty_field_label(labels, "Gene", "notes[1]") == "notes[1]"
    assert _uncertainty_field_label(labels, "Gene", "evidence_codes[x]") == "evidence_codes[x]"
    assert _uncertainty_field_label(labels, "Other", "evidence_codes[0]") == "evidence_codes[0]"


def test_a_set_aside_record_with_a_standing_accepted_value_shows_both_reasons():
    """Chris (19 Nov): "Accepted earlier: ... | Set aside later: ...", per field after its name."""
    from src.lib.flows.output_projection import _attach_review_fields
    from src.lib.validation_history.projection import ACCEPTED_EARLIER, SET_ASIDE_LATER

    rows = [{"object.pending_ref_id": key} for key in ("own", "fields", "plain")]
    items = [{"object_type": "Annotation", "metadata": {"validation_history": {"target_id": key}}}
             for key in ("own", "fields", "plain")]
    reasons = {
        "own": [(None, ACCEPTED_EARLIER, "Table 1 lists it."), (None, SET_ASIDE_LATER, "A second reading did not hold up.")],
        "fields": [("statement", None, "The legend says so."),
                   ("evidence_code_curies[0]", ACCEPTED_EARLIER, "Table 2 gives this code."),
                   ("evidence_code_curies[0]", SET_ASIDE_LATER, "The new answer names another code.")],
        "plain": [(None, None, "The supplement is not available.")],
    }
    _attach_review_fields(rows, items, [], reasons, {("Annotation", "statement"): "Statement"})
    assert [row[REASON] for row in rows] == [
        "Accepted earlier: Table 1 lists it. | Set aside later: A second reading did not hold up.",
        "Statement: The legend says so. | evidence_code_curies[0]: Accepted earlier: Table 2 gives this code. | "
        "evidence_code_curies[0]: Set aside later: The new answer names another code.",
        "The supplement is not available.",
    ]
    assert export_fields.REVIEW_REASON_ACCEPTED_EARLIER_LABEL == "Accepted earlier"
    assert export_fields.REVIEW_REASON_SET_ASIDE_LATER_LABEL == "Set aside later"


@pytest.mark.parametrize("transform", [
    {"type": "count", "field_ref": STATUS},
    {"type": "boolean_label", "field_ref": STATUS},
    {"type": "map_value", "field_ref": STATUS, "mapping": {"accepted": "Yes"}},
    {"type": "format_elements", "field_refs": [STATEMENT, STATUS], "default": "{1}"},
    {"type": "literal", "value": "x", "field_ref": STATUS},
])
def test_a_transform_over_review_status_does_not_bring_set_aside_records_in(reviewed, transform):
    """R1: only a plain Review status column shows "set aside", so only it includes the records."""
    from src.lib.flows.output_projection import FlowOutputColumnSpec

    bundle = _bundle(reviewed, [_object("kept", "in the gut"), _object("aside", "near the gut")], aside={"aside"})
    plan = _object_plan("csv", [("statement", STATEMENT)])
    plan = plan.model_copy(update={"columns": [*plan.columns, FlowOutputColumnSpec(
        key="accepted", transform=transform)]})
    result = apply_projection_plan(bundle, plan)
    assert [row["statement"] for row in result.rows] == ["in the gut"]
    assert (result.set_aside_included, result.set_aside_left_out) == (0, 1)


def test_a_cell_override_cannot_rewrite_review_status(reviewed):
    """R1 note: overrides are validated, so an edit of the Review status column is refused."""
    from src.lib.flows.output_projection import FlowOutputOverrideSpec, bundle_row_refs

    bundle = _bundle(reviewed, [_object("kept", "in the gut"), _object("aside", "near the gut")], aside={"aside"})
    aside_ref = next(ref for row_id, ref in bundle_row_refs(bundle, "object").items()
                     for row in bundle.rows_for_source("object")
                     if id(row) == row_id and row["object.pending_ref_id"] == "aside")
    plan = _object_plan("csv", [("statement", STATEMENT), ("status", STATUS)]).model_copy(update={
        "overrides": [FlowOutputOverrideSpec(row_ref=aside_ref, column_key="status", value="accepted")]})
    with pytest.raises(ValueError, match="shows Review status"):
        apply_projection_plan(bundle, plan)
    # Other cells of the same row can still be edited.
    edited = plan.model_copy(update={
        "overrides": [FlowOutputOverrideSpec(row_ref=aside_ref, column_key="statement", value="edited")]})
    assert apply_projection_plan(bundle, edited).rows[1] == {"statement": "edited", "status": "set aside"}


def test_objects_a_set_aside_answer_contributed_are_set_aside_with_it():
    """R4: contributed objects hang under the envelope, not the blocked record; they follow their answer."""
    from src.lib.validation_history.projection import contributed_records

    def obj(key, completion=None):
        metadata = {"validation_history": {"target_id": f"target-{key}"}}
        if completion:
            metadata["selected_contribution"] = {"completion_id": completion, "selected_result_slots": []}
        return {"object_type": "GeneExpressionAnnotation", "pending_ref_id": key, "payload": {}, "metadata": metadata}

    projection = DomainEnvelope.model_validate({"envelope_id": "e", "domain_pack_id": PACK, "extracted_objects": [
        obj("record"), obj("from-blocked", "answer-a"), obj("from-accepted", "answer-b")]})
    manifest = {"units": [{"target_id": "unit-a", "selected": "answer-a"},
                          {"target_id": "unit-b", "selected": "answer-b"},
                          {"target_id": "unit-c", "selected": None}]}
    assert contributed_records(projection, manifest, {"unit-a", "unit-c"}) == {"target-from-blocked"}
    assert contributed_records(projection, manifest, set()) == set()
