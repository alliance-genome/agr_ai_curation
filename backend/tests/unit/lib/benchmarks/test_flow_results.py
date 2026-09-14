from datetime import datetime, timezone

import pytest

from src.lib.benchmarks import flow_results
from src.schemas.curation_workspace import CurationExtractionResultRecord, CurationExtractionSourceKind


def extraction(result_id="result-1", **updates):
    values = {
        "extraction_result_id": result_id,
        "document_id": "document-1",
        "agent_key": "extractor",
        "adapter_key": "test-adapter",
        "source_kind": CurationExtractionSourceKind.FLOW,
        "origin_session_id": "run-1",
        "flow_run_id": "run-1",
        "user_id": "owner-1",
        "payload_json": {
            "envelope_id": f"envelope:{result_id}",
            "domain_pack_id": "test-pack",
            "extracted_objects": [],
        },
        "created_at": datetime(2026, 9, 5, tzinfo=timezone.utc),
    }
    return CurationExtractionResultRecord(**(values | updates))


def receipt(*result_ids):
    return {
        "status": "completed",
        "document_id": "document-1",
        "flow_run_id": "run-1",
        "origin_session_id": "run-1",
        "output_status": "completed",
        "outputs": [{"type": "CURATION_HANDOFF_READY"}],
        "extraction_result_refs": [
            {"extraction_result_id": result_id} for result_id in result_ids
        ],
    }


def load(completion):
    return flow_results.load_flow_extractions(
        completion, document_id="document-1", user_id="owner-1", run_id="run-1"
    )


def test_loads_canonical_envelopes_in_receipt_order_with_authoritative_provenance(monkeypatch):
    def records(**kwargs):
        assert kwargs == {
            "document_id": "document-1",
            "flow_run_id": "run-1",
            "origin_session_id": "run-1",
            "user_id": "owner-1",
            "source_kind": CurationExtractionSourceKind.FLOW,
        }
        return [extraction("result-2"), extraction("result-1")]

    monkeypatch.setattr(flow_results, "list_extraction_results", records)
    output = load(receipt("result-1", "result-2"))
    assert output["schema_version"] == "benchmark-flow-extractions/v1"
    assert "outputs" not in output
    assert [envelope["envelope_id"] for envelope in output["envelopes"]] == [
        "envelope:result-1", "envelope:result-2"
    ]
    assert output["envelopes"][0]["metadata"]["source_extraction_result_id"] == "result-1"
    assert output["envelopes"][0]["extracted_objects"] == []


@pytest.mark.parametrize("changes", [
    {"status": "failed"},
    {"document_id": "other-document"},
    {"flow_run_id": "other-run"},
    {"origin_session_id": "other-session"},
    {"extraction_result_refs": []},
    {"extraction_result_refs": [{"extraction_result_id": "result-1"}] * 2},
])
def test_rejects_invalid_receipt_before_reading_results(monkeypatch, changes):
    def unexpected(**kwargs):
        pytest.fail("Invalid receipt must fail before querying extraction results")

    monkeypatch.setattr(flow_results, "list_extraction_results", unexpected)
    with pytest.raises(ValueError):
        load(receipt("result-1") | changes)


@pytest.mark.parametrize("result_ids", [[], ["other-result"]])
def test_rejects_missing_or_cross_scope_results(monkeypatch, result_ids):
    monkeypatch.setattr(
        flow_results, "list_extraction_results", lambda **kwargs: [extraction(i) for i in result_ids]
    )
    with pytest.raises(ValueError, match="does not match persisted"):
        load(receipt("result-1"))


def test_only_receipted_results_become_output(monkeypatch):
    monkeypatch.setattr(
        flow_results, "list_extraction_results",
        lambda **kwargs: [extraction("result-1"), extraction("unreported-result")],
    )
    assert len(load(receipt("result-1"))["envelopes"]) == 1


@pytest.mark.parametrize("payload", [{}, {"status": "completed", "outputs": []}])
def test_rejects_completion_metadata_persisted_as_an_extraction(monkeypatch, payload):
    monkeypatch.setattr(
        flow_results, "list_extraction_results",
        lambda **kwargs: [extraction(payload_json=payload)],
    )
    with pytest.raises(ValueError):
        load(receipt("result-1"))


def test_normalizes_extractor_shape_using_existing_domain_pack_contract(monkeypatch):
    monkeypatch.setattr(
        flow_results, "list_extraction_results",
        lambda **kwargs: [extraction(adapter_key="gene", payload_json={"curatable_objects": []})],
    )
    envelope = load(receipt("result-1"))["envelopes"][0]
    assert envelope["domain_pack_id"] == "gene"
    assert envelope["extracted_objects"] == []
    assert "curatable_objects" not in envelope
    assert envelope["metadata"]["source_adapter_key"] == "gene"


def execution_context(**updates):
    return {
        "captured_at": "2026-09-14T00:00:00Z",
        "source_kind": "flow", "flow_id": "flow-1", "step_id": "node-1",
        "agent_key": "extractor", "executed_query": "private step query",
        "document": None,
    } | updates


def test_node_identity_comes_from_persisted_context_not_model_metadata(monkeypatch):
    records = [
        extraction("result-1", metadata={"execution_context": execution_context()}),
        extraction("result-2", metadata={"execution_context": execution_context(step_id="node-2")}),
        extraction("historical"),
    ]
    for record in records:
        record.payload_json["metadata"] = {"benchmark_flow_source": {"node_id": "forged"}}
    monkeypatch.setattr(flow_results, "list_extraction_results", lambda **kwargs: records)
    output = load(receipt("result-2", "result-1", "historical"))
    sources = [item["metadata"]["benchmark_flow_source"] for item in output["envelopes"]]
    assert sources == [
        {"schema_version": "benchmark-flow-source/v1", "flow_id": "flow-1",
         "node_id": node, "run_id": "run-1", "document_id": "document-1"}
        for node in ("node-2", "node-1")
    ] + [None]
    assert "private step query" not in str(output)
    assert all(r.payload_json["metadata"]["benchmark_flow_source"]["node_id"] == "forged" for r in records)


@pytest.mark.parametrize("context", [
    execution_context(agent_key="another-agent"),
    execution_context(step_id=None),
    execution_context(source_kind="chat", flow_id=None),
    execution_context(document={"document_id": "11111111-1111-4111-8111-111111111111"}),
])
def test_rejects_inconsistent_or_invalid_persisted_node_context(monkeypatch, context):
    monkeypatch.setattr(flow_results, "list_extraction_results", lambda **kwargs: [
        extraction(metadata={"execution_context": context}),
    ])
    with pytest.raises(ValueError):
        load(receipt("result-1"))
