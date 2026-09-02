"""Stable v1 OpenAPI contract for curation benchmark snapshot handoff."""

from fastapi import FastAPI

from src.api.curation_workspace import router


def test_snapshot_handoff_openapi_exposes_only_the_reviewed_v1_shapes():
    app = FastAPI()
    app.include_router(router)
    document = app.openapi()

    create_path = (
        "/api/curation-workspace/sessions/{session_id}/envelopes/{envelope_id}"
        "/benchmark-snapshots"
    )
    download_path = "/api/curation-workspace/benchmark-snapshots/{snapshot_id}/download"
    handoff_path = "/api/curation-workspace/benchmark-snapshots/{snapshot_id}/handoffs"
    assert "post" in document["paths"][create_path]
    assert "get" in document["paths"][download_path]
    assert "post" in document["paths"][handoff_path]

    schemas = document["components"]["schemas"]
    assert set(schemas["CurationBenchmarkSnapshotCreateRequest"]["properties"]) == {
        "expected_revision"
    }
    assert set(schemas["CurationBenchmarkHandoffRequest"]["properties"]) == {
        "destination_id"
    }
    assert set(schemas["CurationBenchmarkSnapshotCreateResponse"]["properties"]) == {
        "snapshot_id",
        "schema_version",
        "envelope_revision",
        "envelope_digest",
        "download_path",
    }
    assert set(schemas["CurationBenchmarkHandoffResponse"]["properties"]) == {
        "handoff_id",
        "snapshot_id",
        "destination_id",
        "status",
        "receipt_id",
        "redirect_path",
    }
    assert "destination_url" not in str(schemas)
