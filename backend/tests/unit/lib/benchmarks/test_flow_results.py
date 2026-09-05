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
