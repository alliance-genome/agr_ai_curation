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
    FlowOutputFilterSpec, _uncertainty_field_label, apply_projection_plan, build_flow_output_artifact_bundle,
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


def _reviewed_step(objects, pack=PACK, agent="gene_expression"):
    step = _envelope_step(agent, pack, objects)
    step["candidate"].payload_json["metadata"] = {
        "validation_history": {"artifact_id": "artifact-1", "session_id": "history-session"},
        # The envelope's evidence inventory; records cite it by ID.
        "evidence_records": [{"evidence_record_id": f"ev-{item['pending_ref_id']}",
                              "quote": f"Quote for {item['pending_ref_id']}."} for item in objects]}
    return step


@pytest.fixture
def reviewed(monkeypatch):
    """The current publication sets ``state['set_aside']`` aside, with ``state['reasons']``."""

    state = {"set_aside": set(), "reasons": {}, "reads": []}

    def read(scope, reference, *, reasons=True):
        state["reads"].append(reasons)
        payload = state["payload"]
        return (DomainEnvelope.model_validate(payload), {"blockers": []},
                state["reasons"] if reasons else {}, set(state["set_aside"]))

    monkeypatch.setattr("src.lib.validation_history.projection.latest_publication_reference",
                        lambda scope, artifact_id: {"fact_id": "publication"})
    monkeypatch.setattr("src.lib.validation_history.projection.read_current_projection_with_reasons", read)
    return state


def _bundle(state, objects, *, aside, reasons=None, review_reasons=True, pack=PACK, agent="gene_expression"):
    step = _reviewed_step(objects, pack, agent)
    state.update(payload=step["candidate"].payload_json, set_aside={f"target-{key}" for key in aside},
                 reasons=reasons or {})
    return build_flow_output_artifact_bundle(
        user_id="curator", document_id="00000000-0000-0000-0000-000000000001",
        completed_steps=[step], flow_name="Set aside", output_format="tsv", review_reasons=review_reasons)


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
    # A filter on Review status counts as selecting it.
    filtered = _object_plan("csv", [("statement", STATEMENT)]).model_copy(update={
        "filters": [FlowOutputFilterSpec(field_ref=STATUS, op="eq", value="set aside")]})
    assert apply_projection_plan(bundle, filtered).rows == [{"statement": "near the gut"}]


def test_when_every_record_is_set_aside_the_empty_output_is_intentional(reviewed):
    bundle = _bundle(reviewed, [_object("aside", "near the gut")], aside={"aside"})
    result = apply_projection_plan(bundle, _object_plan("tsv", [("statement", STATEMENT)]))
    assert result.rows == [] and result.empty_reason == "all_records_set_aside"
    assert result.set_aside_left_out == 1
    assert _is_intentional_empty_projection(result)


def test_review_reason_carries_the_set_aside_reason(reviewed):
    """Chris, decision 7: Review reason also carries the set-aside reason for set-aside rows."""
    bundle = _bundle(reviewed, [_object("kept", "in the gut"), _object("aside", "near the gut")], aside={"aside"},
                     reasons={"target-kept": [(None, "The figure legend names the gut.")],
                              "target-aside": [(None, "The paper does not say which tissue.")]})
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
                     reasons={"target-aside": [(None, "The paper does not say which tissue.")]})
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
