"""ALL-1565: the reviewer's text is its own optional export field, never part of a value.

"Review uncertainty" copies each open review-uncertainty finding of a record as
"field: explanation"; "Review reason" copies the reason of the decision the record
stands on (read from history; see the persistence test in test_review_to_tsv).
Both are selectable for pack, generic and custom-profile records, and neither is
in any default layout.
"""

import csv
import hashlib
import io
import json
from copy import deepcopy

import pytest

from src.lib.flows import export_fields
from src.lib.flows.output_projection import FlowOutputProjectionPlan, apply_projection_plan, default_projection_plan
from src.lib.openai_agents.tools.file_output_tools import _projection_content_for_file_type
from src.lib.validation_history.assessments import FINDING_CODE
from tests.reviewed_output_unit_support import (
    build_reviewed_output_bundle as build_flow_output_artifact_bundle,
    reviewed_output_reader as reviewed_output_reader,
)
from tests.unit.lib.flows.test_profile_projection import profile_step as profile_step
from tests.unit.lib.flows.test_value_display import _envelope_step, _object_plan, _open_finding

pytestmark = pytest.mark.usefixtures("reviewed_output_reader")

REVIEW_REFS = {export_fields.REVIEW_UNCERTAINTY_REF, export_fields.REVIEW_REASON_REF}
STATEMENT = "object.pack.GeneExpressionAnnotation.where_expressed_statement"


def _doubt(field_path, message, **object_ref):
    """A review-uncertainty finding as project_assessments saves it (its path is in field_ref)."""
    finding = _open_finding(field_path, **object_ref)
    del finding["field_path"]
    finding.update(finding_id=f"doubt-{field_path}", code=FINDING_CODE, message=message)
    return finding


def test_review_fields_never_change_a_source_fingerprint():
    """Saved field layouts stay current: the fingerprint covers the source's own fields only."""

    fields = [{"ref": "object.attribute.count", "label": "Count", "value_type": "integer"}]
    catalog = export_fields.source_catalog(fields, {"receipt": 1})
    assert [field["ref"] for field in catalog["fields"]][-2:] == [
        export_fields.REVIEW_UNCERTAINTY_REF, export_fields.REVIEW_REASON_REF]
    before = {"fields": [*fields, *export_fields.COMMON_FIELDS], "execution_receipt": {"receipt": 1}}
    encoded = json.dumps(before, sort_keys=True, separators=(",", ":"), default=str)
    assert catalog["schema_fingerprint"] == "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()
    # A source with no declared fields still offers no selected-fields catalog.
    assert export_fields.source_catalog([])["fields"] == []


def _pack_bundle(findings):
    objects = [{"object_type": "GeneExpressionAnnotation", "pending_ref_id": key,
                "payload": {"where_expressed_statement": text}}
               for key, text in (("doubtful", "near the gut"), ("clean", "in the hypodermis"))]
    return build_flow_output_artifact_bundle(
        completed_steps=[_envelope_step("gene_expression", "agr.alliance.gene_expression", objects, findings)],
        flow_name="Pack", output_format="csv")


def test_pack_records_declare_the_review_fields_outside_the_default_layout():
    findings = [
        _doubt("where_expressed_statement", "The figure legend names a different tissue.",
               pending_ref_id="doubtful", object_type="GeneExpressionAnnotation"),
        _doubt("expression_pattern.where_expressed.cellular_component", "No compartment is named.",
               pending_ref_id="doubtful", object_type="GeneExpressionAnnotation"),
    ]
    resolved = _doubt("evidence_text", "Settled by the curator.", pending_ref_id="clean",
                      object_type="GeneExpressionAnnotation")
    resolved["status"] = "resolved"
    other = _open_finding("where_expressed_statement", pending_ref_id="clean", object_type="GeneExpressionAnnotation")
    other.update(code="domain_pack.required_field_missing", message="Not a review doubt.")
    bundle = _pack_bundle([*findings, resolved, other])
    [artifact] = bundle.artifacts
    declared = {field.ref: field.label for field in artifact.declared_fields}
    assert declared[export_fields.REVIEW_UNCERTAINTY_REF] == "Review uncertainty"
    assert declared[export_fields.REVIEW_REASON_REF] == "Review reason"
    assert not REVIEW_REFS & set(artifact.default_object_refs)
    for output_format in ("csv", "tsv"):
        default = default_projection_plan(bundle, output_format=output_format)
        assert not REVIEW_REFS & {column.field_ref for column in default.columns}

    rows = apply_projection_plan(bundle, _object_plan("tsv", [
        ("statement", STATEMENT), ("doubt", export_fields.REVIEW_UNCERTAINTY_REF),
        ("reason", export_fields.REVIEW_REASON_REF),
    ])).rows
    # Each open review doubt, in finding order; resolved and other findings are not doubts.
    assert rows == [
        {"statement": "near the gut",
         "doubt": "where_expressed_statement: The figure legend names a different tissue. | "
                  "expression_pattern.where_expressed.cellular_component: No compartment is named.",
         "reason": "-"},
        {"statement": "in the hypodermis", "doubt": "-", "reason": "-"},
    ]
    # JSON keeps raw values; an empty review field is null.
    json_plan = _object_plan("json", [("statement", STATEMENT), ("doubt", export_fields.REVIEW_UNCERTAINTY_REF)])
    json_rows = apply_projection_plan(bundle, json_plan.model_copy(update={"missing_value": None})).rows
    assert json_rows[1] == {"statement": "in the hypodermis", "doubt": None}


def test_generic_records_offer_the_review_fields_to_guided_layouts_only():
    """Generic records have no declared catalog; like Rationale, the fields are row fields."""

    objects = [{"object_type": "generic_object", "pending_ref_id": "g1",
                "payload": {"label": "VT1", "attributes": {"source": "BDSC"}, "rationale": "Named in methods."}}]
    finding = _doubt("attributes.source", "The stock centre is not named.", pending_ref_id="g1",
                     object_type="generic_object")
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_envelope_step("pdf", "generic", objects, [finding])], flow_name="Generic",
        output_format="csv")
    catalog = {field.ref: field.label for field in bundle.field_catalog if field.row_source == "object"}
    assert catalog[export_fields.REVIEW_UNCERTAINTY_REF] == "Review uncertainty"
    assert catalog[export_fields.REVIEW_REASON_REF] == "Review reason"
    assert bundle.artifacts[0].declared_fields == []
    default = default_projection_plan(bundle, output_format="csv")
    assert not REVIEW_REFS & {column.field_ref for column in default.columns}
    [row] = apply_projection_plan(bundle, _object_plan("csv", [
        ("doubt", export_fields.REVIEW_UNCERTAINTY_REF)])).rows
    assert row == {"doubt": "attributes.source: The stock centre is not named."}


@pytest.mark.parametrize("output_format", ["csv", "tsv"])
def test_profile_records_select_the_review_fields_and_a_layout_without_them_is_unchanged(
    profile_step, output_format,
):
    step, _, profile = profile_step
    step["node_id"] = "stocks"
    extraction = step["candidate"]["payload_json"]
    objects = extraction["curatable_objects"]
    objects.append(deepcopy(objects[0]))
    objects[1]["pending_ref_id"] = "record-2"
    # The saved (canonical) envelope carries the reviewer's findings.
    step["candidate"]["payload_json"] = {
        "envelope_id": "env-1", "domain_pack_id": "generic", "extracted_objects": objects,
        "metadata": {"extraction_metadata": extraction["metadata"]},
        "validation_findings": [_doubt("attributes.count", "The table total and the text disagree.",
                                       pending_ref_id="record-1", object_type="generic_object")],
    }
    bundle = build_flow_output_artifact_bundle(completed_steps=[step], flow_name="Stock",
                                               profile_resolver=lambda _: profile)
    [artifact] = bundle.artifacts
    assert REVIEW_REFS <= {field.ref for field in artifact.declared_fields}
    assert not REVIEW_REFS & set(artifact.default_object_refs)

    def export(columns):
        plan = FlowOutputProjectionPlan.model_validate({
            "format": output_format, "row_source": "object", "row_strategy": "wide_union",
            "selection_mode": "selected_fields", "missing_value": "",
            "selected_sources": [{"node_id": artifact.node_id,
                                  "schema_fingerprint": artifact.export_schema_fingerprint}],
            "columns": [{"key": key, "field_ref": ref, "source_node_id": artifact.node_id} for key, ref in columns],
        })
        content = _projection_content_for_file_type(output_format=output_format,
                                                     projection=apply_projection_plan(bundle, plan))
        return list(csv.reader(io.StringIO(content), delimiter="," if output_format == "csv" else "\t"))

    # Neither selected: the columns and values a flow gets today, the flagged value as stored.
    assert export([("count", "object.attribute.count")]) == [["count"], ["0"], ["0"]]
    assert export([("count", "object.attribute.count"),
                   ("Review uncertainty", export_fields.REVIEW_UNCERTAINTY_REF),
                   ("Review reason", export_fields.REVIEW_REASON_REF)]) == [
        ["count", "Review uncertainty", "Review reason"],
        ["0", "attributes.count: The table total and the text disagree.", ""],
        ["0", "", ""],
    ]
