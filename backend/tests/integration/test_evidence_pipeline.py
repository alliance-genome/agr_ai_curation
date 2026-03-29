"""Integration coverage for tool-verified evidence mapping and workspace linkage."""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from tests.fixtures.evidence.harness import (
    build_expected_candidates,
    build_extraction_payload,
    build_extraction_scope,
)
from tests.integration.evidence_test_support import (
    collect_sse_events,
    configure_chat_stream_mocks,
    make_fixture_runner,
)

pytest_plugins = ["tests.integration.evidence_test_support"]


def _record_to_schema(record):
    from src.schemas.curation_workspace import CurationExtractionResultRecord

    return CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": str(record.id),
            "document_id": str(record.document_id),
            "adapter_key": record.adapter_key,
            "profile_key": record.profile_key,
            "domain_key": record.domain_key,
            "agent_key": record.agent_key,
            "source_kind": record.source_kind,
            "origin_session_id": record.origin_session_id,
            "trace_id": record.trace_id,
            "flow_run_id": record.flow_run_id,
            "user_id": record.user_id,
            "candidate_count": record.candidate_count,
            "conversation_summary": record.conversation_summary,
            "payload_json": record.payload_json,
            "created_at": record.created_at,
            "metadata": dict(record.extraction_metadata or {}),
        }
    )


def _fixture_extraction_result(
    evidence_fixture: dict[str, object],
    *,
    document_id: str,
    user_id: str,
    origin_session_id: str,
):
    from src.schemas.curation_workspace import (
        CurationExtractionResultRecord,
        CurationExtractionSourceKind,
    )

    extraction = evidence_fixture["extraction"]
    scope = build_extraction_scope(extraction)

    return CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": "fixture-extract-1",
            "document_id": document_id,
            "adapter_key": scope["adapter_key"],
            "profile_key": scope["profile_key"],
            "domain_key": scope["domain_key"],
            "agent_key": extraction["agent_key"],
            "source_kind": CurationExtractionSourceKind.CHAT,
            "origin_session_id": origin_session_id,
            "trace_id": "trace-evidence-fixture",
            "flow_run_id": None,
            "user_id": user_id,
            "candidate_count": extraction["run_summary"]["candidate_count"],
            "conversation_summary": evidence_fixture["paper"]["conversation_summary"],
            "payload_json": build_extraction_payload(evidence_fixture),
            "created_at": datetime.now(timezone.utc),
            "metadata": {"fixture_id": evidence_fixture["fixture_id"]},
        }
    )


def test_fixture_extraction_payload_and_result_preserve_fixture_scope_values(
    evidence_fixture,
):
    scoped_fixture = copy.deepcopy(evidence_fixture)
    scoped_fixture["extraction"]["profile_key"] = "pilot"
    scoped_fixture["extraction"]["scope_confirmation"]["profile_keys"] = ["pilot"]
    scoped_fixture["extraction"]["scope_confirmation"]["domain_keys"] = ["disease"]

    extraction_result = _fixture_extraction_result(
        scoped_fixture,
        document_id="document-fixture",
        user_id="user-fixture",
        origin_session_id="session-fixture",
    )
    payload = build_extraction_payload(scoped_fixture)

    assert payload["profile_key"] == "pilot"
    assert payload["scope_confirmation"]["profile_keys"] == ["pilot"]
    assert payload["scope_confirmation"]["domain_keys"] == ["disease"]
    assert extraction_result.profile_key == "pilot"
    assert extraction_result.domain_key == "disease"


@pytest.mark.asyncio
async def test_fixture_chat_extraction_maps_verified_evidence_into_prep_and_workspace(
    client,
    evidence_fixture,
    evidence_integration_context,
    monkeypatch,
    test_db,
):
    from src.lib.curation_workspace.curation_prep_service import (
        CurationPrepPersistenceContext,
        run_curation_prep,
    )
    from src.lib.curation_workspace.models import CurationExtractionResultRecord as ExtractionResultModel
    from src.schemas.curation_prep import CurationPrepScopeConfirmation

    extraction = evidence_fixture["extraction"]
    expected_candidate = build_expected_candidates(evidence_fixture)[0]
    session_id = "session-evidence-pipeline"

    configure_chat_stream_mocks(
        monkeypatch,
        document_id=evidence_integration_context["document_id"],
        filename=evidence_integration_context["paper"]["filename"],
        tool_agent_map={extraction["tool_name"]: extraction["agent_key"]},
        run_agent_streamed=make_fixture_runner(evidence_fixture),
        evidence_fixture=evidence_fixture,
    )

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={
            "message": evidence_integration_context["paper"]["conversation_summary"],
            "session_id": session_id,
        },
    ) as stream_response:
        events = collect_sse_events(stream_response)
        assert stream_response.status_code == 200

    assert [event["type"] for event in events][-2:] == ["evidence_summary", "RUN_FINISHED"]

    extraction_record = test_db.scalars(
        select(ExtractionResultModel).where(
            ExtractionResultModel.origin_session_id == session_id,
            ExtractionResultModel.agent_key == extraction["agent_key"],
        )
    ).one()
    persisted_extraction = _record_to_schema(extraction_record)
    assert persisted_extraction.payload_json == build_extraction_payload(evidence_fixture)
    assert persisted_extraction.origin_session_id == session_id

    prep_output = await run_curation_prep(
        [persisted_extraction],
        scope_confirmation=CurationPrepScopeConfirmation.model_validate(
            extraction["scope_confirmation"]
        ),
        db=test_db,
        persistence_context=CurationPrepPersistenceContext(
            origin_session_id=session_id,
            user_id=evidence_integration_context["current_user_auth_sub"],
        ),
    )

    assert len(prep_output.candidates) == 1
    assert prep_output.run_metadata.warnings == [evidence_fixture["expected_gating"]["warning"]]

    prep_candidate = prep_output.candidates[0]
    assert prep_candidate.adapter_key == expected_candidate["adapter_key"]
    assert prep_candidate.profile_key == expected_candidate["profile_key"]
    assert prep_candidate.payload == expected_candidate["payload"]
    assert [record.field_paths for record in prep_candidate.evidence_records] == [
        expected_candidate["field_paths"],
        expected_candidate["field_paths"],
    ]
    assert [record.extraction_result_id for record in prep_candidate.evidence_records] == [
        persisted_extraction.extraction_result_id,
        persisted_extraction.extraction_result_id,
    ]
    assert [record.anchor.snippet_text for record in prep_candidate.evidence_records] == [
        evidence["verified_quote"]
        for evidence in expected_candidate["evidence"]
    ]
    assert [record.anchor.chunk_ids for record in prep_candidate.evidence_records] == [
        [evidence["chunk_id"]]
        for evidence in expected_candidate["evidence"]
    ]

    bootstrap_response = client.post(
        (
            "/api/curation-workspace/documents/"
            f"{evidence_integration_context['document_id']}/bootstrap"
        ),
        json={"origin_session_id": session_id},
    )
    assert bootstrap_response.status_code == 200, bootstrap_response.text
    session_payload = bootstrap_response.json()["session"]

    workspace_response = client.get(
        f"/api/curation-workspace/sessions/{session_payload['session_id']}",
        params={"include_workspace": "true"},
    )
    assert workspace_response.status_code == 200, workspace_response.text
    workspace_candidate = workspace_response.json()["workspace"]["candidates"][0]

    assert workspace_candidate["adapter_key"] == expected_candidate["adapter_key"]
    assert [anchor["field_keys"] for anchor in workspace_candidate["evidence_anchors"]] == [
        expected_candidate["field_paths"],
        expected_candidate["field_paths"],
    ]
    assert [anchor["anchor"]["snippet_text"] for anchor in workspace_candidate["evidence_anchors"]] == [
        evidence["verified_quote"]
        for evidence in expected_candidate["evidence"]
    ]
    assert [anchor["anchor"]["chunk_ids"] for anchor in workspace_candidate["evidence_anchors"]] == [
        [evidence["chunk_id"]]
        for evidence in expected_candidate["evidence"]
    ]
    assert [anchor["anchor"]["section_title"] for anchor in workspace_candidate["evidence_anchors"]] == [
        evidence["section"]
        for evidence in expected_candidate["evidence"]
    ]


@pytest.mark.asyncio
async def test_fixture_chat_extraction_preserves_profile_scope_for_chat_persistence(
    client,
    evidence_fixture,
    evidence_integration_context,
    monkeypatch,
    test_db,
):
    from src.lib.curation_workspace.curation_prep_service import run_curation_prep
    from src.lib.curation_workspace.models import CurationExtractionResultRecord as ExtractionResultModel
    from src.schemas.curation_prep import CurationPrepScopeConfirmation

    scoped_fixture = copy.deepcopy(evidence_fixture)
    scoped_fixture["extraction"]["profile_key"] = "pilot"
    scoped_fixture["extraction"]["scope_confirmation"]["profile_keys"] = ["pilot"]
    extraction = scoped_fixture["extraction"]
    session_id = "session-evidence-profile-scope"

    configure_chat_stream_mocks(
        monkeypatch,
        document_id=evidence_integration_context["document_id"],
        filename=evidence_integration_context["paper"]["filename"],
        tool_agent_map={extraction["tool_name"]: extraction["agent_key"]},
        run_agent_streamed=make_fixture_runner(scoped_fixture),
        evidence_fixture=scoped_fixture,
    )

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={
            "message": evidence_integration_context["paper"]["conversation_summary"],
            "session_id": session_id,
        },
    ) as stream_response:
        collect_sse_events(stream_response)
        assert stream_response.status_code == 200

    extraction_record = test_db.scalars(
        select(ExtractionResultModel).where(
            ExtractionResultModel.origin_session_id == session_id,
            ExtractionResultModel.agent_key == extraction["agent_key"],
        )
    ).one()
    persisted_extraction = _record_to_schema(extraction_record)

    assert persisted_extraction.profile_key == "pilot"
    assert persisted_extraction.payload_json["profile_key"] == "pilot"
    assert persisted_extraction.payload_json["scope_confirmation"]["profile_keys"] == [
        "pilot"
    ]

    prep_output = await run_curation_prep(
        [persisted_extraction],
        scope_confirmation=CurationPrepScopeConfirmation.model_validate(
            extraction["scope_confirmation"]
        ),
    )

    assert len(prep_output.candidates) == 1
    assert prep_output.candidates[0].profile_key == "pilot"


@pytest.mark.asyncio
async def test_run_curation_prep_rejects_fixture_payload_when_all_candidates_have_zero_verified_evidence(
    evidence_fixture,
    evidence_integration_context,
):
    from src.lib.curation_workspace.curation_prep_service import run_curation_prep
    from src.schemas.curation_prep import CurationPrepScopeConfirmation

    extraction_result = _fixture_extraction_result(
        evidence_fixture,
        document_id=evidence_integration_context["document_id"],
        user_id=evidence_integration_context["current_user_auth_sub"],
        origin_session_id="session-evidence-all-zero",
    )
    payload = copy.deepcopy(extraction_result.payload_json)
    for item in payload["items"]:
        item["evidence"] = []
    payload["evidence_records"] = build_extraction_payload(evidence_fixture)["evidence_records"]
    extraction_result = extraction_result.model_copy(update={"payload_json": payload})

    with pytest.raises(
        ValueError,
        match=evidence_fixture["expected_gating"]["all_zero_error"],
    ):
        await run_curation_prep(
            [extraction_result],
            scope_confirmation=CurationPrepScopeConfirmation.model_validate(
                evidence_fixture["extraction"]["scope_confirmation"]
            ),
        )
