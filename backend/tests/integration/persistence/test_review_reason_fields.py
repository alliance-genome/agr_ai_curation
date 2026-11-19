"""ALL-1565: Review reason on records a validator checked lists each field's standing reason.

Real history, real decisions and real publication; fake validator answers. The
reason text is copied from the decision that stands on each unit, labelled by the
column the pack declares for exactly that path, in manifest order.
"""
from types import SimpleNamespace

import pytest

from src.lib.validation_review import decisions, reads
from src.lib.validation_review.decisions import ReviewContext
from tests.integration.persistence.test_chat_review_adapter import (
    checkpoint_provider, migrated as migrated, planned as planned, scope as scope,
)
from tests.integration.persistence.test_review_packet import _answers, _handles
from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload


def _reason_cells(item, monkeypatch, columns):
    from src.lib.flows.output_projection import (
        FlowOutputProjectionPlan, apply_projection_plan, build_flow_output_artifact_bundle,
    )
    monkeypatch.setattr("src.lib.flows.export_fields.packaged_domain_pack", lambda *args, **kwargs: item.pack)
    envelope = checkpoint_provider(item)().envelope
    bundle = build_flow_output_artifact_bundle(
        user_id=item.scope.owner_id, document_id=item.scope.document_id,
        completed_steps=[{"step": 1, "node_id": "source", "agent_id": "fixture", "agent_name": "Fixture",
            "candidate": SimpleNamespace(payload_json=envelope.model_dump(mode="json"), agent_key="fixture",
                adapter_key="fixture", candidate_count=len(envelope.extracted_objects), conversation_summary="x")}],
        flow_name="Field reasons", output_format="tsv")
    labels = {field.ref: field.label for artifact in bundle.artifacts for field in artifact.declared_fields}
    plan = FlowOutputProjectionPlan.model_validate({"format": "tsv", "row_source": "object",
        "columns": [{"key": key, "field_ref": ref} for key, ref in columns]})
    return apply_projection_plan(bundle, plan).rows, labels


async def _validated(item, monkeypatch):
    _answers(monkeypatch, lambda request: {**_result_payload(request, resolved_values={"curie": "ECO:X"}),
                                           "resolved_objects": []})
    await item.adapter.initial()
    packet = reads.review_packet(item.scope, item.ep)
    record, = packet["records"]
    handles = _handles(record)
    assert len(handles) == 2 and all(entry["review"] == "validator_answer" for entry in record["review"])
    return packet, handles


def _save(item, packet, decided, operation_id):
    return decisions.record_packet_decisions(
        ReviewContext(item.scope, item.ep, "supervisor.validation_review"),
        checkpoint_request=checkpoint_provider(item)(), operation_id=operation_id,
        packet_id=packet["packet_id"], packet_decisions=decided)


@pytest.mark.asyncio
async def test_a_validated_record_lists_each_fields_standing_reason(planned, monkeypatch):
    item = planned
    packet, (first, second) = await _validated(item, monkeypatch)
    _save(item, packet, [
        {"handle": first, "action": "accept", "reason": "Table 2 gives this evidence code."},
        {"handle": second, "action": "accept", "reason": "The methods name this assay."},
    ], "accept-both")
    rows, _ = _reason_cells(item, monkeypatch, [("reason", "object.review_reason")])
    # List elements have no declared column of their own, so they keep the stored path.
    assert rows == [{"reason": "evidence_code_curies[0]: Table 2 gives this evidence code. | "
                               "evidence_code_curies[1]: The methods name this assay."}]


@pytest.mark.asyncio
async def test_a_field_decided_only_by_defer_contributes_nothing(planned, monkeypatch):
    item = planned
    packet, (first, second) = await _validated(item, monkeypatch)
    _save(item, packet, [
        {"handle": first, "action": "accept", "reason": "Table 2 gives this evidence code."},
        {"handle": second, "action": "defer", "reason": "The supplement is not available yet."},
    ], "accept-and-defer")
    rows, _ = _reason_cells(item, monkeypatch, [("reason", "object.review_reason")])
    assert rows == [{"reason": "evidence_code_curies[0]: Table 2 gives this evidence code."}]


@pytest.mark.parametrize("planned", ["optional-composite-absence"], indirect=True)
def test_a_composite_decision_unit_reads_its_bindings_declared_column(planned, monkeypatch):
    """A declared decision unit is a child of its binding target; the binding names the record."""
    item = planned
    page = reads.review_packet(item.scope, item.ep)
    handle, = _handles(page["records"][0])
    reason = "The paper does not report a term for this field."
    _save(item, page, [{"handle": handle, "action": "accept", "reason": reason,
                        "absence_assessment": "omission_supported"}], "composite")
    rows, labels = _reason_cells(item, monkeypatch, [("reason", "object.review_reason")])
    label = labels["object.pack.Annotation.optional.term"]
    assert rows == [{"reason": f"{label}: {reason}"}]
