"""Unit tests for Agent Studio domain-envelope inspection helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import src.lib.agent_studio.domain_envelope_tools as domain_tools
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    DomainEnvelopeStatus,
    HistoryActorType,
    HistoryEvent,
    HistoryEventKind,
)


def test_current_flow_domain_envelope_analysis_summarizes_validation_schedule(monkeypatch):
    monkeypatch.setattr(
        domain_tools,
        "get_domain_pack_validation_plan",
        lambda **_kwargs: {
            "success": True,
            "domain_pack_version": "0.7.0",
            "object_definitions": [
                {
                    "object_type": "allele",
                    "display_name": "Allele",
                    "field_paths": ["gene.symbol", "allele.symbol"],
                }
            ],
            "validation_attachment_summary": {
                "total": 3,
                "default_enabled": 1,
                "required": 1,
                "export_blocking": 1,
            },
        },
    )

    result = domain_tools.current_flow_domain_envelope_analysis(
        flow_context={
            "nodes": [
                {
                    "id": "extract_1",
                    "type": "agent",
                    "data": {
                        "agent_id": "allele_extractor",
                        "agent_display_name": "Allele Extraction",
                        "validation_attachments": [
                            {
                                "attachment_id": "active-binding",
                                "domain_pack_id": "alliance_allele",
                                "validator_id": "allele_lookup",
                                "validator_binding_id": "active-binding",
                                "state": "active",
                                "enabled": True,
                                "required": True,
                                "export_blocking": True,
                            },
                            {
                                "attachment_id": "opted-out-binding",
                                "domain_pack_id": "alliance_allele",
                                "validator_id": "manual_check",
                                "validator_binding_id": "opted-out-binding",
                                "state": "active",
                                "enabled": False,
                                "required": False,
                                "export_blocking": True,
                                "opt_out_reason": "Curator reviewed manually.",
                            },
                            {
                                "attachment_id": "planned-binding",
                                "domain_pack_id": "alliance_allele",
                                "validator_id": "future_validator",
                                "validator_binding_id": "planned-binding",
                                "state": "planned",
                                "enabled": False,
                            },
                        ],
                    },
                }
            ]
        },
        agent_registry={
            "allele_extractor": {
                "name": "Allele Extraction",
                "curation": {"domain_pack_id": "alliance_allele"},
            }
        },
    )

    node = result["nodes"][0]
    assert result["semantic_source"] == "domain_envelope.objects"
    assert result["envelope_node_count"] == 1
    assert node["domain_pack_id"] == "alliance_allele"
    assert node["domain_pack_version"] == "0.7.0"
    assert node["object_definitions"][0]["object_type"] == "allele"
    assert node["validation_schedule"]["scheduled_validators"][0][
        "validator_binding_id"
    ] == "active-binding"
    assert node["validation_schedule"]["opt_outs"][0]["validator_binding_id"] == (
        "opted-out-binding"
    )
    assert node["validation_schedule"]["inactive_metadata"][0][
        "validator_binding_id"
    ] == "planned-binding"


def test_resolved_object_id_accepts_pending_ref_id():
    object_id_by_ref = {
        ("object_id", "obj-1"): "obj-1",
        ("pending_ref_id", "pending-1"): "obj-1",
    }

    assert domain_tools._resolved_object_id("pending-1", object_id_by_ref) == "obj-1"
    assert domain_tools._resolved_object_id("obj-1", object_id_by_ref) == "obj-1"
    assert domain_tools._resolved_object_id("missing-ref", object_id_by_ref) == "missing-ref"


def test_lookup_attempt_summary_preserves_transient_attempts_separate_from_final_status():
    envelope = DomainEnvelope(
        envelope_id="env-lookup",
        domain_pack_id="alliance_gene",
        status=DomainEnvelopeStatus.VALIDATED,
        objects=[
            CuratableObjectEnvelope(
                object_type="gene",
                object_id="obj-1",
                payload={
                    "primary_external_id": "WB:WBGene00000001",
                    "lookup_status": "success",
                    "lookup_attempts": [
                        {
                            "lookup_status": "transient_error",
                            "attempted_query": {"symbol": "unc-54"},
                            "error": {"type": "TimeoutError"},
                        },
                        {
                            "lookup_status": "success",
                            "attempted_query": {"symbol": "unc-54"},
                            "resolved_id": "WB:WBGene00000001",
                            "resolved_label": "unc-54",
                        },
                    ],
                },
            )
        ],
    )
    projection_row = SimpleNamespace(
        object_id="obj-1",
        projection_type="review_row",
        projection_key="gene:unc-54",
        projection_json={
            "lookup_status": "success",
            "lookup_attempts": [
                {
                    "lookup_status": "success",
                    "target_projection": "gene:unc-54",
                    "resolved_id": "WB:WBGene00000001",
                }
            ],
        },
    )

    summary = domain_tools._lookup_attempt_summary(
        envelope=envelope,
        projection_rows=[projection_row],
    )

    assert summary["attempt_count"] == 3
    assert summary["by_status"] == {"success": 2, "transient_error": 1}
    assert summary["attempts"][0]["lookup_status"] == "transient_error"
    assert "audit trail" in summary["interpretation"]
    assert "final outcome" in summary["interpretation"]


def test_repair_attempt_summary_exposes_attempts_classifications_and_history_events():
    envelope = DomainEnvelope(
        envelope_id="env-repair",
        domain_pack_id="alliance_gene",
        objects=[
            CuratableObjectEnvelope(
                object_type="gene",
                object_id="obj-1",
                payload={"primary_external_id": "WB:WBGene00000001"},
            )
        ],
        metadata={
            "repair_context": {
                "latest_status": "no_repair_possible",
                "latest_chat_summary": "Supervisor classified the field as non-repairable.",
                "attempts": [
                    {
                        "repair_attempt_id": "repair-1",
                        "object_id": "obj-1",
                        "field_path": "primary_external_id",
                    }
                ],
                "classifications": [
                    {
                        "repair_attempt_id": "repair-1",
                        "status": "no_repair_possible",
                    }
                ],
            }
        },
        history=[
            HistoryEvent(
                event_id="event-repair-1",
                event_type=HistoryEventKind.REPAIR_FINAL_CLASSIFIED,
                timestamp=datetime(2026, 5, 11, tzinfo=timezone.utc),
                actor_type=HistoryActorType.AGENT,
                actor_id="validation_supervisor",
                message="Repair classified as not possible.",
                details={"repair_attempt_id": "repair-1"},
            )
        ],
    )

    summary = domain_tools._repair_attempt_summary(envelope)

    assert summary["latest_status"] == "no_repair_possible"
    assert summary["attempt_count"] == 1
    assert summary["classification_count"] == 1
    assert summary["attempts"][0]["field_path"] == "primary_external_id"
    assert summary["classifications"][0]["status"] == "no_repair_possible"
    assert summary["history_events"][0]["event_id"] == "event-repair-1"


def test_export_submission_readiness_returns_read_only_blockers(monkeypatch):
    class FakeDb:
        def close(self):
            pass

    class FakeReadiness:
        def model_dump(self, *, mode):
            assert mode == "json"
            return {
                "candidate_id": "candidate-1",
                "ready": False,
                "blockers": [
                    {
                        "code": "domain_validation_blocker",
                        "envelope_id": "env-1",
                        "object_id": "obj-1",
                        "field_path": "gene.symbol",
                        "message": "Resolve required validation finding.",
                    }
                ],
            }

    monkeypatch.setattr(
        domain_tools,
        "_session_visible_to_user",
        lambda _db, **_kwargs: True,
    )
    monkeypatch.setattr(
        domain_tools,
        "_load_session_for_validation",
        lambda _db, *, session_id: SimpleNamespace(
            candidates=[SimpleNamespace(id="candidate-1")]
        ),
    )
    monkeypatch.setattr(
        domain_tools,
        "_build_domain_envelope_submission_context",
        lambda **_kwargs: SimpleNamespace(envelope_snapshots={"env-1": object()}),
    )
    monkeypatch.setattr(
        domain_tools,
        "_latest_candidate_validation_snapshot",
        lambda _candidate: {"status": "failed"},
    )
    monkeypatch.setattr(
        domain_tools,
        "_candidate_submission_readiness",
        lambda *_args, **_kwargs: FakeReadiness(),
    )

    result = domain_tools.get_export_submission_readiness(
        session_factory=FakeDb,
        user_auth_sub="curator-1",
        session_id="session-1",
        candidate_ids=["candidate-1"],
        expected_envelope_revisions={"env-1": 3},
        mode="submission",
    )

    assert result["success"] is True
    assert result["mode"] == "submission"
    assert result["ready_count"] == 0
    assert result["blocker_count"] == 1
    assert result["domain_envelope_ids"] == ["env-1"]
    assert result["readiness"][0]["blockers"][0]["envelope_id"] == "env-1"
    assert "read-only readiness explanation" in result["instruction"]
