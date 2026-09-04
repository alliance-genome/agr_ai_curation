"""Unit tests for Agent Studio domain-envelope inspection helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.lib.agent_studio.domain_envelope_tools as domain_tools
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
from src.lib.curation_workspace.models import (
    CurationCandidate,
    CurationExtractionResultRecord,
    CurationReviewSession,
    DomainEnvelopeHistory,
    DomainEnvelopeModel,
    DomainEnvelopeObject,
    DomainEnvelopeProjectionIndex,
    DomainValidationFinding,
)
from src.models.sql.database import Base
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import (
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationSessionStatus,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DomainEnvelope,
    DomainEnvelopeStatus,
    HistoryActorType,
    HistoryEventKind,
    ObjectRef,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
)


@compiles(PostgresUUID, "sqlite")
def _compile_pg_uuid_for_sqlite(_type, _compiler, **_kwargs):
    return "CHAR(36)"


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


TEST_TABLES = [
    PDFDocument.__table__,
    CurationReviewSession.__table__,
    CurationExtractionResultRecord.__table__,
    DomainEnvelopeModel.__table__,
    DomainEnvelopeObject.__table__,
    DomainValidationFinding.__table__,
    DomainEnvelopeHistory.__table__,
    DomainEnvelopeProjectionIndex.__table__,
    CurationCandidate.__table__,
]
LARGE_RUNTIME_ITEM_COUNT = 27


def test_domain_pack_validation_plan_exposes_validator_agent_owner(
    monkeypatch,
    tmp_path,
):
    pack_path = tmp_path / "fixture.validation"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(
        """
pack_id: fixture.validation
display_name: Fixture Validation Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
model_definitions:
  - model_id: AssertionPayload
    display_name: Assertion payload
object_definitions:
  - object_type: Assertion
    display_name: Assertion
    model_ref: AssertionPayload
    fields:
      - field_path: assertion.curie
        display_name: Assertion CURIE
        field_type: string
metadata:
  validator_bindings:
    active:
      - binding_id: fixture.agent_validator
        validator_agent:
          package_id: org.validators
          agent_id: shared_validator
        applies_to:
          domain_pack_id: fixture.validation
    under_development:
      - binding_id: fixture.assertion_curie_lookup
        display_name: Assertion CURIE lookup
        state_explanation: Lookup dispatch is still being configured.
        applies_to:
          domain_pack_id: fixture.validation
          object_types: [Assertion]
          field_paths: [assertion.curie]
""".strip(),
        encoding="utf-8",
    )
    metadata = load_domain_pack_metadata(metadata_path)
    loaded_pack = LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
        package_id="org.owner",
    )
    registry = DomainPackValidationRegistry.from_domain_pack(loaded_pack)
    monkeypatch.setattr(
        domain_tools,
        "domain_pack_validation_registries",
        lambda: {"fixture.validation": registry},
    )

    summary = domain_tools.get_domain_pack_validation_plan(
        domain_pack_id="fixture.validation",
    )
    bindings = domain_tools.get_domain_pack_validation_plan(
        domain_pack_id="fixture.validation",
        section="validator_bindings",
        limit=4,
    )
    attachments = domain_tools.get_domain_pack_validation_plan(
        domain_pack_id="fixture.validation",
        section="validation_attachments",
        limit=4,
    )

    assert summary["success"] is True
    assert summary["section"] == "summary"
    assert set(summary["section_counts"]) == set(domain_tools._DOMAIN_PLAN_SECTIONS)
    assert "validator_bindings" not in summary
    binding = bindings["items"][0]
    attachment = attachments["items"][0]
    assert binding["validator_agent"] == {
        "package_id": "org.validators",
        "agent_id": "shared_validator",
    }
    assert attachment["validator_package_id"] == "org.validators"
    assert attachment["validator_agent_id"] == "shared_validator"
    under_development_attachment = next(
        option
        for option in attachments["items"]
        if option["state"] == "under_development"
    )
    under_development_binding = next(
        item
        for item in bindings["items"]
        if item["binding_state"] == "under_development"
    )
    assert under_development_attachment["state_explanation"] == (
        "Lookup dispatch is still being configured."
    )
    assert under_development_attachment["affected_fields"] == ["assertion.curie"]
    assert under_development_binding["state_explanation"] == (
        "Lookup dispatch is still being configured."
    )
    assert summary["validation_dispatch_summary"]["active_automatic"] == 1
    assert summary["validation_dispatch_summary"]["under_development_metadata"] == 1
    assert "metadata_only" not in summary["validation_dispatch_summary"]
    assert (
        "get_prompt(agent_id=<validator agent id>)"
        in summary["validation_dispatch_summary"]["validator_prompt_inspection"]
    )
    assert (
        "Active default-enabled attachments are the only validators scheduled automatically"
        in summary["automatic_validation_semantics"]
    )
    assert "Under-development validator bindings are explanatory metadata" in summary[
        "automatic_validation_semantics"
    ]
    assert "Do not ask extractor prompts to call validators directly" in summary[
        "automatic_validation_semantics"
    ]
    assert "planned" not in summary["automatic_validation_semantics"].lower()
    assert "blocked" not in summary["automatic_validation_semantics"].lower()
    assert "opt-out " + "reason" not in json.dumps(summary).lower()
    assert "repair" not in json.dumps(summary).lower()


def test_gene_expression_validation_plan_accepts_flow_alias_and_package_agent_id():
    """Agent Studio can inspect the same domain pack through either public ID."""
    flow_alias_result = domain_tools.get_domain_pack_validation_plan(
        agent_id="gene_expression",
    )
    package_agent_result = domain_tools.get_domain_pack_validation_plan(
        agent_id="gene_expression_extraction",
    )

    assert flow_alias_result["success"] is True
    assert package_agent_result["success"] is True
    assert flow_alias_result["domain_pack_id"] == "agr.alliance.gene_expression"
    assert package_agent_result["domain_pack_id"] == "agr.alliance.gene_expression"
    assert flow_alias_result["agent_id"] == "gene_expression"
    assert package_agent_result["agent_id"] == "gene_expression_extraction"
    assert flow_alias_result["validation_dispatch_summary"] == package_agent_result[
        "validation_dispatch_summary"
    ]


@pytest.mark.parametrize("section", domain_tools._DOMAIN_PLAN_SECTIONS)
def test_disease_validation_plan_pages_every_section_deterministically(section):
    summary = domain_tools.get_domain_pack_validation_plan(
        domain_pack_id="agr.alliance.disease"
    )
    expected_count = summary["section_counts"][section]
    items = []
    cursor = None
    requests = []

    while True:
        page = domain_tools.get_domain_pack_validation_plan(
            domain_pack_id="agr.alliance.disease",
            section=section,
            limit=4,
            cursor=cursor,
        )
        assert page["success"] is True
        assert page["section"] == section
        assert page["section_total_count"] == expected_count
        assert page["returned_count"] == len(page["items"])
        assert page["truncated"] is (not page["complete"])
        items.extend(page["items"])
        requests.append(page)
        if page["complete"]:
            assert page["next_cursor"] is None
            assert page["next_request"] is None
            break
        assert page["next_request"]["cursor"] == page["next_cursor"]
        cursor = page["next_cursor"]

    repeated_first_page = domain_tools.get_domain_pack_validation_plan(
        domain_pack_id="agr.alliance.disease",
        section=section,
        limit=4,
    )
    assert repeated_first_page == requests[0]
    assert len(items) == expected_count


def test_domain_pack_validation_plan_filters_and_invalid_inputs():
    summary = domain_tools.get_domain_pack_validation_plan(
        domain_pack_id="agr.alliance.disease"
    )
    object_page = domain_tools.get_domain_pack_validation_plan(
        domain_pack_id="agr.alliance.disease",
        section="object_definitions",
        limit=1,
    )
    object_type = object_page["items"][0]["object_type"]
    assert object_page["items"][0]["capabilities"]["pack_state"] == "active"
    assert "write" in object_page["items"][0]["capabilities"]
    filtered = domain_tools.get_domain_pack_validation_plan(
        domain_pack_id="agr.alliance.disease",
        section="fields",
        object_type=object_type,
    )

    assert filtered["success"] is True
    assert filtered["total_count"] <= summary["section_counts"]["fields"]
    assert all(item["object_type"] == object_type for item in filtered["items"])
    assert domain_tools.get_domain_pack_validation_plan(
        domain_pack_id="agr.alliance.disease",
        section="not-a-section",
    )["error"].startswith("section must be one of")
    assert "does not support filter" in domain_tools.get_domain_pack_validation_plan(
        domain_pack_id="agr.alliance.disease",
        section="object_definitions",
        validator_id="validator",
    )["error"]
    assert "state must be one of" in domain_tools.get_domain_pack_validation_plan(
        domain_pack_id="agr.alliance.disease",
        section="validators",
        state="retired",
    )["error"]
    assert "cursor must be" in domain_tools.get_domain_pack_validation_plan(
        domain_pack_id="agr.alliance.disease",
        section="fields",
        cursor="next",
    )["error"]
    assert "section is required" in domain_tools.get_domain_pack_validation_plan(
        domain_pack_id="agr.alliance.disease",
        limit=1,
    )["error"]


def test_disease_field_pages_keep_verbose_policies_in_policy_section():
    policy_page = domain_tools.get_domain_pack_validation_plan(
        domain_pack_id="agr.alliance.disease",
        section="field_policies",
        limit=1,
    )
    policy = policy_page["items"][0]
    field_page = domain_tools.get_domain_pack_validation_plan(
        domain_pack_id="agr.alliance.disease",
        section="fields",
        object_type=policy["object_type"],
        field_path=policy["field_path"],
    )

    assert field_page["returned_count"] == 1
    assert "validation_policy" not in field_page["items"][0]
    assert policy["object_type"] == field_page["items"][0]["object_type"]
    assert policy["field_path"] == field_page["items"][0]["field_path"]


@pytest.fixture
def db_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    restored_defaults = []
    restored_indexes = []
    for table in TEST_TABLES:
        restored_indexes.append((table, set(table.indexes)))
        table.indexes.clear()
        for column in table.columns:
            restored_defaults.append((column, column.server_default))
            column.server_default = None

    Base.metadata.create_all(bind=engine, tables=TEST_TABLES)
    session_local = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    try:
        yield session_local
    finally:
        Base.metadata.drop_all(bind=engine, tables=TEST_TABLES)
        for table, indexes in restored_indexes:
            table.indexes.update(indexes)
        for column, server_default in restored_defaults:
            column.server_default = server_default


def _now() -> datetime:
    return datetime(2026, 5, 11, tzinfo=timezone.utc)


def _persist_document(db, *, suffix: str = "1"):
    now = _now()
    document = PDFDocument(
        id=uuid4(),
        user_id=1,
        filename=f"paper-{suffix}.pdf",
        title=f"Paper {suffix}",
        file_path=f"/tmp/paper-{suffix}.pdf",
        file_hash=suffix.rjust(64, "a")[-64:],
        file_size=1024,
        page_count=1,
        upload_timestamp=now,
        last_accessed=now,
        status="processed",
    )
    db.add(document)
    db.flush()
    return document


def _persist_review_session(db, *, document_id, curator_id: str):
    now = _now()
    session = CurationReviewSession(
        id=uuid4(),
        status=CurationSessionStatus.NEW,
        adapter_key="fixture",
        document_id=document_id,
        assigned_curator_id=curator_id,
        created_by_id=curator_id,
        session_version=1,
        total_candidates=1,
        reviewed_candidates=0,
        pending_candidates=1,
        accepted_candidates=0,
        rejected_candidates=0,
        manual_candidates=0,
        warnings=[],
        tags=[],
        prepared_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.flush()
    return session


def _persist_domain_envelope(db, *, envelope_id: str, document_id, session_id=None):
    now = _now()
    envelope = DomainEnvelope(
        envelope_id=envelope_id,
        domain_pack_id="fixture.pack",
        status=DomainEnvelopeStatus.EXTRACTED,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="gene",
                object_id=f"{envelope_id}-object",
                payload={"symbol": envelope_id},
            )
        ],
    )
    db.add(
        DomainEnvelopeModel(
            envelope_id=envelope_id,
            revision=1,
            project_key="fixture",
            domain_pack_key="fixture.pack",
            domain_pack_version=None,
            adapter_key="fixture_adapter",
            source_extraction_result_id=f"source:{envelope_id}",
            source_payload_hash="0" * 64,
            status=DomainEnvelopeStatus.EXTRACTED,
            document_id=document_id,
            session_id=session_id,
            schema_ref_json={},
            object_model_ref_json={},
            model_field_ref_json={},
            envelope_json=envelope.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
            checkpointed_at=now,
        )
    )
    db.flush()
    return envelope


def _persist_candidate_for_envelope(db, *, session_id, envelope: DomainEnvelope):
    now = _now()
    db.add(
        CurationCandidate(
            id=uuid4(),
            session_id=session_id,
            source=CurationCandidateSource.EXTRACTED,
            status=CurationCandidateStatus.PENDING,
            order=0,
            adapter_key="fixture",
            display_label=envelope.envelope_id,
            envelope_id=envelope.envelope_id,
            object_id=envelope.extracted_objects[0].object_id,
            envelope_revision=1,
            normalized_payload={},
            candidate_metadata={"semantic_source": "domain_envelope.extracted_objects"},
            created_at=now,
            updated_at=now,
        )
    )
    db.flush()


def _persist_large_runtime_state(db, *, envelope_id: str, document_id, session_id):
    now = _now()
    objects = [
        CuratableObjectEnvelope(
            object_type="gene",
            object_id=f"obj-{index}",
            payload={
                "symbol": f"GENE{index}",
                "lookup_attempts": [
                    {
                        "outcome": "success",
                        "query": {"symbol": f"GENE{index}"},
                        "result_count": 1,
                    }
                ],
            },
        )
        for index in range(LARGE_RUNTIME_ITEM_COUNT)
    ]
    findings = [
        ValidationFinding(
            finding_id=f"finding-{index}",
            severity=ValidationFindingSeverity.BLOCKER,
            status=ValidationFindingStatus.OPEN,
            code="fixture.required_lookup",
            message=f"Resolve GENE{index}.",
            object_ref=ObjectRef(object_id=f"obj-{index}", object_type="gene"),
            details={
                "validation_metadata": {
                    "validator_binding_id": "fixture.lookup",
                    "binding_state": "active",
                    "blocking": True,
                    "required": True,
                },
                "validation_request": {
                    "validator_binding_id": "fixture.lookup",
                    "target": {"object_id": f"obj-{index}"},
                    "selected_inputs": {"symbol": f"GENE{index}"},
                },
                "validation_result": {
                    "status": "unresolved",
                    "resolved_values": {},
                },
            },
        )
        for index in range(LARGE_RUNTIME_ITEM_COUNT)
    ]
    envelope = DomainEnvelope(
        envelope_id=envelope_id,
        domain_pack_id="fixture.pack",
        status=DomainEnvelopeStatus.VALIDATED,
        extracted_objects=objects,
        validation_findings=findings,
    )
    db.add(
        DomainEnvelopeModel(
            envelope_id=envelope_id,
            revision=2,
            project_key="fixture",
            domain_pack_key="fixture.pack",
            adapter_key="fixture_adapter",
            source_extraction_result_id=f"source:{envelope_id}",
            source_payload_hash="1" * 64,
            status=DomainEnvelopeStatus.VALIDATED,
            document_id=document_id,
            session_id=session_id,
            schema_ref_json={"provider": "fixture"},
            object_model_ref_json={},
            model_field_ref_json={},
            envelope_json=envelope.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
            checkpointed_at=now,
        )
    )
    for index in range(LARGE_RUNTIME_ITEM_COUNT):
        db.add(
            DomainEnvelopeObject(
                envelope_id=envelope_id,
                object_id=f"obj-{index}",
                envelope_revision=2,
                object_index=index,
                object_type="gene",
                status=CuratableObjectStatus.EXTRACTED,
                validation_state="blocked",
                schema_ref_json={"provider": "fixture"},
                object_model_ref_json={"object_type": "gene"},
                model_field_ref_json={"symbol": {"field_path": "symbol"}},
                payload_json=objects[index].payload,
                object_json=objects[index].model_dump(mode="json"),
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            DomainValidationFinding(
                envelope_id=envelope_id,
                finding_id=f"finding-{index}",
                envelope_revision=2,
                finding_index=index,
                object_id=f"obj-{index}",
                field_path="symbol",
                severity=ValidationFindingSeverity.BLOCKER,
                status=ValidationFindingStatus.OPEN,
                code="fixture.required_lookup",
                object_model_ref_json={"object_type": "gene"},
                model_field_ref_json={"field_path": "symbol"},
                finding_json=findings[index].model_dump(mode="json"),
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            DomainEnvelopeHistory(
                envelope_id=envelope_id,
                event_id=f"event-{index}",
                envelope_revision=2,
                event_index=LARGE_RUNTIME_ITEM_COUNT - index - 1,
                event_type=HistoryEventKind.VALIDATION_FINDING_ADDED,
                occurred_at=now,
                actor_type=HistoryActorType.AGENT,
                actor_id="fixture-validator",
                object_id=f"obj-{index}",
                field_path="symbol",
                model_field_ref_json={"field_path": "symbol"},
                event_json={"message": f"Added finding {index}.", "details": {}},
                created_at=now,
            )
        )
        db.add(
            DomainEnvelopeProjectionIndex(
                envelope_id=envelope_id,
                object_id=f"obj-{index}",
                envelope_revision=2,
                object_type="gene",
                projection_type="review_row",
                projection_key=f"gene:{index}",
                projection_status="blocked",
                schema_ref_json={"provider": "fixture"},
                object_model_ref_json={"object_type": "gene"},
                model_field_ref_json={"field_path": "symbol"},
                projection_json={"symbol": f"GENE{index}"},
                created_at=now,
                updated_at=now,
            )
        )
    db.flush()
    return envelope


def test_current_flow_domain_envelope_analysis_summarizes_validation_schedule(monkeypatch):
    monkeypatch.setattr(
        domain_tools,
        "get_domain_pack_validation_plan",
        lambda **_kwargs: {
            "success": True,
            "domain_pack_version": "0.7.0",
            "section": "summary",
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
                                "blocking": True,
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
                                "blocking": True,
                                "export_blocking": True,
                            },
                            {
                                "attachment_id": "under-development-binding",
                                "domain_pack_id": "alliance_allele",
                                "validator_id": "future_validator",
                                "validator_binding_id": "under-development-binding",
                                "state": "under_development",
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
    assert result["semantic_source"] == "domain_envelope.extracted_objects"
    assert result["envelope_node_count"] == 1
    assert node["domain_pack_id"] == "alliance_allele"
    assert node["domain_pack_version"] == "0.7.0"
    assert node["validation_plan_request"] == {
        "tool": "get_domain_pack_validation_plan",
        "input": {"agent_id": "allele_extractor"},
    }
    assert "object_definitions" not in node
    assert node["validation_schedule"]["scheduled_validators"][0][
        "validator_binding_id"
    ] == "active-binding"
    assert node["validation_schedule"]["opt_outs"][0]["validator_binding_id"] == (
        "opted-out-binding"
    )
    assert node["validation_schedule"]["inactive_metadata"][0][
        "validator_binding_id"
    ] == "under-development-binding"


def test_resolved_object_id_accepts_pending_ref_id():
    object_id_by_ref = {
        ("object_id", "obj-1"): "obj-1",
        ("pending_ref_id", "pending-1"): "obj-1",
    }

    assert domain_tools._resolved_object_id("pending-1", object_id_by_ref) == "obj-1"
    assert domain_tools._resolved_object_id("obj-1", object_id_by_ref) == "obj-1"
    assert domain_tools._resolved_object_id("missing-ref", object_id_by_ref) == "missing-ref"


def test_sessionless_domain_envelope_visibility_requires_visible_candidate_session(
    db_session_factory,
):
    seed_db = db_session_factory()
    try:
        document = _persist_document(seed_db)
        visible_session = _persist_review_session(
            seed_db,
            document_id=document.id,
            curator_id="curator-1",
        )
        hidden_session = _persist_review_session(
            seed_db,
            document_id=document.id,
            curator_id="curator-2",
        )
        visible_envelope = _persist_domain_envelope(
            seed_db,
            envelope_id="env-visible-sessionless",
            document_id=document.id,
        )
        hidden_envelope = _persist_domain_envelope(
            seed_db,
            envelope_id="env-hidden-sessionless",
            document_id=document.id,
        )
        _persist_domain_envelope(
            seed_db,
            envelope_id="env-orphan-sessionless",
            document_id=document.id,
        )
        _persist_candidate_for_envelope(
            seed_db,
            session_id=visible_session.id,
            envelope=visible_envelope,
        )
        _persist_candidate_for_envelope(
            seed_db,
            session_id=hidden_session.id,
            envelope=hidden_envelope,
        )
        seed_db.commit()
        visible_session_id = str(visible_session.id)
    finally:
        seed_db.close()

    select_statements = []
    engine = db_session_factory.kw["bind"]

    def record_select(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            select_statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_select)

    try:
        document_result = domain_tools.list_domain_envelopes(
            session_factory=db_session_factory,
            user_auth_sub="curator-1",
            document_id=str(document.id),
            limit=10,
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_select)
    session_result = domain_tools.list_domain_envelopes(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        session_id=visible_session_id,
        limit=10,
    )
    hidden_state = domain_tools.get_domain_envelope_state(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        envelope_id="env-hidden-sessionless",
    )
    orphan_state = domain_tools.get_domain_envelope_state(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        envelope_id="env-orphan-sessionless",
    )

    assert document_result["success"] is True
    assert len(select_statements) == 2
    assert "envelope_json" not in select_statements[-1]
    assert {row["envelope_id"] for row in document_result["envelopes"]} == {
        "env-visible-sessionless"
    }
    assert session_result["success"] is True
    assert [row["envelope_id"] for row in session_result["envelopes"]] == [
        "env-visible-sessionless"
    ]
    assert hidden_state == {
        "success": False,
        "error": "Domain envelope env-hidden-sessionless was not found.",
    }
    assert orphan_state == {
        "success": False,
        "error": "Domain envelope env-orphan-sessionless was not found.",
    }


def test_envelope_list_max_limit_pages_are_stable_complete_and_filtered(
    db_session_factory,
):
    seed_db = db_session_factory()
    try:
        document = _persist_document(seed_db, suffix="list-pages")
        review_session = _persist_review_session(
            seed_db,
            document_id=document.id,
            curator_id="curator-1",
        )
        expected_ids = [
            f"env-page-{index:03d}" for index in range(domain_tools._MAX_LIMIT + 3)
        ]
        stable_updated_at = _now()
        for envelope_id in expected_ids:
            _persist_domain_envelope(
                seed_db,
                envelope_id=envelope_id,
                document_id=document.id,
                session_id=review_session.id,
            )
            seed_db.get(DomainEnvelopeModel, envelope_id).updated_at = stable_updated_at
        seed_db.commit()
        session_id = str(review_session.id)
    finally:
        seed_db.close()

    observed_ids = []
    request = {
        "session_id": session_id,
        "domain_pack_id": "fixture.pack",
        "limit": domain_tools._MAX_LIMIT,
    }
    while True:
        page = domain_tools.list_domain_envelopes(
            session_factory=db_session_factory,
            user_auth_sub="curator-1",
            **request,
        )
        assert page["total_count"] == len(expected_ids)
        assert len(json.dumps(page)) <= domain_tools._PROVIDER_INLINE_MAX_CHARS
        observed_ids.extend(item["envelope_id"] for item in page["envelopes"])
        if page["complete"]:
            assert page["next_request"] is None
            break
        request = page["next_request"]
        assert request["session_id"] == session_id
        assert request["domain_pack_id"] == "fixture.pack"

    assert observed_ids == expected_ids
    assert len(observed_ids) == len(set(observed_ids))


def test_large_runtime_state_is_summary_first_and_completely_revision_paged(
    db_session_factory,
):
    seed_db = db_session_factory()
    try:
        document = _persist_document(seed_db, suffix="runtime")
        review_session = _persist_review_session(
            seed_db,
            document_id=document.id,
            curator_id="curator-1",
        )
        _persist_large_runtime_state(
            seed_db,
            envelope_id="env-runtime",
            document_id=document.id,
            session_id=review_session.id,
        )
        seed_db.commit()
    finally:
        seed_db.close()

    summary = domain_tools.get_domain_envelope_state(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        envelope_id="env-runtime",
    )

    assert summary["success"] is True
    assert summary["section"] == "summary"
    assert summary["envelope"]["envelope_revision"] == 2
    assert summary["blocker_count"] == LARGE_RUNTIME_ITEM_COUNT
    assert summary["readiness_status"] == "blocked"
    assert summary["section_counts"] == {
        "objects": LARGE_RUNTIME_ITEM_COUNT,
        "validation_findings": LARGE_RUNTIME_ITEM_COUNT,
        "projections": LARGE_RUNTIME_ITEM_COUNT,
        "history": LARGE_RUNTIME_ITEM_COUNT,
        "lookup_attempts": LARGE_RUNTIME_ITEM_COUNT,
        "validator_summaries": LARGE_RUNTIME_ITEM_COUNT,
        "object_ref_index": LARGE_RUNTIME_ITEM_COUNT,
    }
    assert "items" not in summary
    assert all(
        request["envelope_id"] == "env-runtime" and request["revision"] == 2
        for request in summary["detail_requests"]
    )

    for section, expected_count in summary["section_counts"].items():
        items = []
        request = {
            "envelope_id": "env-runtime",
            "revision": 2,
            "section": section,
            "limit": 2,
        }
        while True:
            page = domain_tools.get_domain_envelope_state(
                session_factory=db_session_factory,
                user_auth_sub="curator-1",
                **request,
            )
            assert page["section_total_count"] == expected_count
            assert page["returned_count"] == len(page["items"])
            assert page["blocker_count"] == LARGE_RUNTIME_ITEM_COUNT
            assert page["readiness_status"] == "blocked"
            items.extend(page["items"])
            if page["complete"]:
                assert page["next_request"] is None
                break
            request = page["next_request"]
            assert request["revision"] == 2
        assert len(items) == expected_count
        if section == "history":
            assert [item["event_index"] for item in items] == list(
                reversed(range(LARGE_RUNTIME_ITEM_COUNT))
            )

    filtered = domain_tools.get_domain_envelope_state(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        envelope_id="env-runtime",
        revision=2,
        section="validation_findings",
        object_id="obj-3",
    )
    assert filtered["section_total_count"] == LARGE_RUNTIME_ITEM_COUNT
    assert filtered["total_count"] == 1
    assert filtered["items"][0]["finding_id"] == "finding-3"

    filtered_history = domain_tools.get_domain_envelope_state(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        envelope_id="env-runtime",
        revision=2,
        section="history",
        object_id="obj-3",
        query="Added finding 3.",
    )
    assert filtered_history["section_total_count"] == LARGE_RUNTIME_ITEM_COUNT
    assert filtered_history["total_count"] == 1
    assert filtered_history["items"][0]["event_id"] == "event-3"

    identity_query = domain_tools.get_domain_envelope_state(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        envelope_id="env-runtime",
        revision=2,
        section="projections",
        query="gene:3",
    )
    assert identity_query["total_count"] == 1
    assert identity_query["items"][0]["projection_key"] == "gene:3"

    update_db = db_session_factory()
    try:
        update_db.get(DomainEnvelopeModel, "env-runtime").revision = 3
        update_db.commit()
    finally:
        update_db.close()
    stale_page = domain_tools.get_domain_envelope_state(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        envelope_id="env-runtime",
        revision=2,
        section="objects",
    )
    assert "at revision 3, not requested revision 2" in stale_page["error"]


def test_large_persisted_references_are_manifested_and_exactly_chunked(
    db_session_factory,
):
    large_reference = {
        "provider": "fixture",
        "definition_state": "in_development",
        "schema": "x" * 13_500,
        "nested": {"model": "y" * 13_500},
    }
    seed_db = db_session_factory()
    try:
        document = _persist_document(seed_db, suffix="large-reference")
        review_session = _persist_review_session(
            seed_db,
            document_id=document.id,
            curator_id="curator-1",
        )
        _persist_large_runtime_state(
            seed_db,
            envelope_id="env-large-reference",
            document_id=document.id,
            session_id=review_session.id,
        )
        envelope_row = seed_db.get(DomainEnvelopeModel, "env-large-reference")
        envelope_row.schema_ref_json = large_reference
        object_row = seed_db.scalar(
            select(DomainEnvelopeObject).where(
                DomainEnvelopeObject.envelope_id == "env-large-reference"
            )
        )
        object_row.schema_ref_json = large_reference
        object_row.object_model_ref_json = large_reference
        object_row.model_field_ref_json = large_reference
        object_row.payload_json = large_reference
        projection_row = seed_db.scalar(
            select(DomainEnvelopeProjectionIndex).where(
                DomainEnvelopeProjectionIndex.envelope_id == "env-large-reference"
            )
        )
        projection_row.schema_ref_json = large_reference
        projection_row.object_model_ref_json = large_reference
        projection_row.model_field_ref_json = large_reference
        projection_row.projection_json = large_reference
        seed_db.commit()
        document_id = str(document.id)
    finally:
        seed_db.close()

    listing = domain_tools.list_domain_envelopes(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        document_id=document_id,
        limit=1,
    )
    summary = domain_tools.get_domain_envelope_state(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        envelope_id="env-large-reference",
    )
    object_page = domain_tools.get_domain_envelope_state(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        envelope_id="env-large-reference",
        revision=2,
        section="objects",
        include_object_payload=True,
        limit=1,
    )
    projection_page = domain_tools.get_domain_envelope_state(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        envelope_id="env-large-reference",
        revision=2,
        section="projections",
        limit=1,
    )

    for response in (listing, summary, object_page, projection_page):
        assert len(json.dumps(response, default=str)) <= domain_tools._PROVIDER_INLINE_MAX_CHARS

    manifests = [
        listing["envelopes"][0]["schema_ref"],
        summary["envelope"]["schema_ref"],
        object_page["items"][0]["schema_ref"],
        object_page["items"][0]["object_model_ref"],
        object_page["items"][0]["model_field_ref"],
        object_page["items"][0]["payload"],
        projection_page["items"][0]["schema_ref"],
        projection_page["items"][0]["object_model_ref"],
        projection_page["items"][0]["model_field_ref"],
        projection_page["items"][0]["projection"],
    ]
    assert listing["envelopes"][0]["schema_ref"]["definition_state"] == "in_development"
    assert object_page["items"][0]["schema_ref"]["definition_state"] == "in_development"
    expected_json = domain_tools._canonical_json(large_reference)
    for manifest in manifests:
        assert manifest["json_chars"] == len(expected_json)
        request = dict(manifest["detail_request"])
        chunks = []
        while True:
            chunk = domain_tools.get_domain_envelope_state(
                session_factory=db_session_factory,
                user_auth_sub="curator-1",
                **request,
            )
            assert len(json.dumps(chunk)) <= domain_tools._PROVIDER_INLINE_MAX_CHARS
            chunks.append(chunk["content"])
            if chunk["complete"]:
                break
            request = chunk["next_request"]
        assert "".join(chunks) == expected_json
        assert hashlib.sha256(expected_json.encode()).hexdigest() == manifest["sha256"]


def test_single_oversized_object_lookup_and_validator_values_are_exactly_chunked(
    db_session_factory,
):
    payload: dict[str, Any] = {
        f"payload_key_{index:04d}": index for index in range(2_000)
    }
    lookup_attempt = {
        "outcome": "failure",
        "query": {"symbol": "oversized-lookup-filter"},
        "message": "l" * 13_500,
    }
    payload["lookup_attempts"] = [lookup_attempt]
    explanation = "v" * 21_000

    seed_db = db_session_factory()
    try:
        document = _persist_document(seed_db, suffix="oversized-record")
        review_session = _persist_review_session(
            seed_db,
            document_id=document.id,
            curator_id="curator-1",
        )
        _persist_large_runtime_state(
            seed_db,
            envelope_id="env-oversized-record",
            document_id=document.id,
            session_id=review_session.id,
        )
        envelope_row = seed_db.get(DomainEnvelopeModel, "env-oversized-record")
        envelope_json = dict(envelope_row.envelope_json)
        extracted_objects = list(envelope_json["extracted_objects"])
        extracted_objects[0] = {**extracted_objects[0], "payload": payload}
        envelope_row.envelope_json = {
            **envelope_json,
            "extracted_objects": extracted_objects,
        }
        object_row = seed_db.scalar(
            select(DomainEnvelopeObject)
            .where(DomainEnvelopeObject.envelope_id == "env-oversized-record")
            .where(DomainEnvelopeObject.object_index == 0)
        )
        object_row.payload_json = payload
        finding_row = seed_db.scalar(
            select(DomainValidationFinding)
            .where(DomainValidationFinding.envelope_id == "env-oversized-record")
            .where(DomainValidationFinding.finding_index == 0)
        )
        finding_json = dict(finding_row.finding_json)
        details = dict(finding_json["details"])
        details["validation_result"] = {
            **dict(details["validation_result"]),
            "explanation": explanation,
            "lookup_attempts": [lookup_attempt],
        }
        finding_row.finding_json = {**finding_json, "details": details}
        seed_db.commit()
    finally:
        seed_db.close()

    def reconstruct(manifest):
        request = dict(manifest["detail_request"])
        chunks = []
        while True:
            chunk = domain_tools.get_domain_envelope_state(
                session_factory=db_session_factory,
                user_auth_sub="curator-1",
                **request,
            )
            assert chunk["success"] is True
            assert len(json.dumps(chunk)) <= domain_tools._PROVIDER_INLINE_MAX_CHARS
            chunks.append(chunk["content"])
            if chunk["complete"]:
                return "".join(chunks)
            request = chunk["next_request"]

    object_page = domain_tools.get_domain_envelope_state(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        envelope_id="env-oversized-record",
        revision=2,
        section="objects",
        limit=1,
    )
    lookup_page = domain_tools.get_domain_envelope_state(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        envelope_id="env-oversized-record",
        revision=2,
        section="lookup_attempts",
        query="oversized-lookup-filter",
        limit=1,
    )
    validator_page = domain_tools.get_domain_envelope_state(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        envelope_id="env-oversized-record",
        revision=2,
        section="validator_summaries",
        object_id="obj-0",
        limit=1,
    )

    for response in (object_page, lookup_page, validator_page):
        assert response["success"] is True
        assert response["returned_count"] == 1
        assert len(json.dumps(response, default=str)) <= domain_tools._PROVIDER_INLINE_MAX_CHARS

    expected_keys = domain_tools._canonical_json(sorted(payload))
    assert reconstruct(object_page["items"][0]["payload_keys"]) == expected_keys
    assert reconstruct(lookup_page["items"][0]["evidence"]) == (
        domain_tools._canonical_json(lookup_attempt)
    )
    assert reconstruct(validator_page["items"][0]["explanation"]) == (
        domain_tools._canonical_json(explanation)
    )


def test_large_review_rows_are_summary_first_and_completely_paged(
    db_session_factory,
    monkeypatch,
):
    seed_db = db_session_factory()
    try:
        document = _persist_document(seed_db, suffix="rows")
        review_session = _persist_review_session(
            seed_db,
            document_id=document.id,
            curator_id="curator-1",
        )
        _persist_domain_envelope(
            seed_db,
            envelope_id="env-rows",
            document_id=document.id,
            session_id=review_session.id,
        )
        seed_db.commit()
    finally:
        seed_db.close()

    class FakeRow:
        def __init__(self, index):
            self.object_id = f"obj-{index % 2}"
            self.index = index

        def model_dump(self, *, mode):
            assert mode == "json"
            return {
                "envelope_id": "env-rows",
                "envelope_revision": 1,
                "object_id": self.object_id,
                "field_path": "symbol",
                "row_key": f"row-{self.index}",
            }

    monkeypatch.setattr(
        domain_tools,
        "materialize_persisted_envelope_review_rows",
        lambda *_args, **_kwargs: SimpleNamespace(
            envelope_id="env-rows",
            envelope_revision=1,
            row_count=7,
            rows=[FakeRow(index) for index in range(7)],
        ),
    )

    summary = domain_tools.get_domain_envelope_review_rows(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        envelope_id="env-rows",
    )
    assert summary["section"] == "summary"
    assert summary["row_count"] == 7
    assert summary["section_counts"] == {"rows": 7}
    assert "items" not in summary
    assert summary["detail_requests"][0]["envelope_id"] == "env-rows"
    assert summary["detail_requests"][0]["revision"] == 1

    items = []
    request = {
        "envelope_id": "env-rows",
        "revision": 1,
        "section": "rows",
        "limit": 2,
    }
    while True:
        page = domain_tools.get_domain_envelope_review_rows(
            session_factory=db_session_factory,
            user_auth_sub="curator-1",
            **request,
        )
        items.extend(page["items"])
        if page["complete"]:
            break
        request = page["next_request"]
        assert request["revision"] == 1
    assert len(items) == 7


def test_lookup_attempt_summary_preserves_transient_attempts_separate_from_final_status():
    envelope = DomainEnvelope(
        envelope_id="env-lookup",
        domain_pack_id="alliance_gene",
        status=DomainEnvelopeStatus.VALIDATED,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="gene",
                object_id="obj-1",
                payload={
                    "primary_external_id": "GENE:00000001",
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
                            "resolved_id": "GENE:00000001",
                            "resolved_label": "unc-54",
                        },
                    ],
                },
            )
        ],
    )
    projection_row = SimpleNamespace(
        envelope_id="env-lookup",
        envelope_revision=1,
        object_id="obj-1",
        projection_type="review_row",
        projection_key="gene:unc-54",
        projection_json={
            "lookup_status": "success",
            "lookup_attempts": [
                {
                    "lookup_status": "success",
                    "target_projection": "gene:unc-54",
                    "resolved_id": "GENE:00000001",
                }
            ],
        },
    )

    summary = domain_tools._lookup_attempt_summary(
        envelope_json=envelope.model_dump(mode="json"),
        envelope_id=envelope.envelope_id,
        envelope_revision=1,
        projection_rows=[cast(DomainEnvelopeProjectionIndex, projection_row)],
    )

    assert summary["attempt_count"] == 3
    assert summary["by_status"] == {"success": 2, "transient_error": 1}
    assert summary["attempts"][0]["status"] == "transient_error"
    assert summary["attempts"][0]["evidence"]["sha256"] == hashlib.sha256(
        domain_tools._canonical_json(
            {
                "lookup_status": "transient_error",
                "attempted_query": {"symbol": "unc-54"},
                "error": {"type": "TimeoutError"},
            }
        ).encode("utf-8")
    ).hexdigest()
    assert "audit trail" in summary["interpretation"]
    assert "final outcome" in summary["interpretation"]


def test_lookup_attempt_summary_accepts_validator_result_outcome_attempts():
    envelope = DomainEnvelope(
        envelope_id="env-validator-outcome",
        domain_pack_id="alliance_gene",
        status=DomainEnvelopeStatus.VALIDATED,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="gene",
                object_id="obj-1",
                payload={
                    "lookup_attempts": [
                        {
                            "provider": "agr_curation_query",
                            "method": "search_genes",
                            "query": {"gene_symbol": "unc-54"},
                            "result_count": 1,
                            "outcome": "success",
                        },
                    ],
                },
            )
        ],
    )

    summary = domain_tools._lookup_attempt_summary(
        envelope_json=envelope.model_dump(mode="json"),
        envelope_id=envelope.envelope_id,
        envelope_revision=1,
        projection_rows=[],
    )

    assert summary["attempt_count"] == 1
    assert summary["by_status"] == {"success": 1}
    assert summary["attempts"][0]["status"] == "success"
    assert summary["attempts"][0]["evidence"]["type"] == "dict"


def test_lookup_attempt_traversal_observes_lists_beyond_twenty_five_entries():
    envelope = DomainEnvelope(
        envelope_id="env-many-attempt-lists",
        domain_pack_id="fixture.pack",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="gene",
                object_id="obj-1",
                payload={
                    "attempt_groups": [
                        {
                            "lookup_attempts": [
                                {"outcome": "success", "query": {"index": index}}
                            ]
                        }
                        for index in range(31)
                    ]
                },
            )
        ],
    )

    summary = domain_tools._lookup_attempt_summary(
        envelope_json=envelope.model_dump(mode="json"),
        envelope_id=envelope.envelope_id,
        envelope_revision=1,
        projection_rows=[],
    )

    assert summary["attempt_count"] == 31
    assert summary["truncated"] is True
    assert len(summary["attempts"]) == domain_tools._MAX_LOOKUP_ATTEMPTS


def test_lookup_attempt_summary_rejects_attempts_without_status():
    envelope = DomainEnvelope(
        envelope_id="env-lookup-missing-status",
        domain_pack_id="alliance_gene",
        status=DomainEnvelopeStatus.VALIDATED,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="gene",
                object_id="obj-1",
                payload={
                    "lookup_attempts": [
                        {"attempted_query": {"symbol": "unc-54"}},
                    ],
                },
            )
        ],
    )

    with pytest.raises(
        ValueError,
        match=(
            "Lookup attempt at "
            r"envelope.extracted_objects\[0\].payload.lookup_attempts\[0\] "
            "is missing lookup_status/status/outcome"
        ),
    ):
        domain_tools._lookup_attempt_summary(
            envelope_json=envelope.model_dump(mode="json"),
            envelope_id=envelope.envelope_id,
            envelope_revision=1,
            projection_rows=[],
        )


def test_validator_summary_payload_exposes_request_result_and_materialization_paths():
    row = SimpleNamespace(
        envelope_id="env-validator-summary",
        envelope_revision=4,
        finding_id="finding-1",
        finding_index=0,
        object_id="obj-1",
        field_path="primary_external_id",
        status="resolved",
        severity="info",
        code="domain_pack.validator_resolved",
        finding_json={
            "details": {
                "validation_request": {
                    "request_id": "request-1",
                    "validator_binding_id": "alliance_gene_reference_lookup",
                    "validator_agent": {
                        "package_id": "agr.alliance",
                        "agent_id": "gene_validation",
                    },
                    "target": {
                        "domain_pack_id": "gene",
                        "object_type": "gene_mention_evidence",
                        "object_id": "obj-1",
                        "field_path": "primary_external_id",
                    },
                    "selected_inputs": {
                        "mention": "unc-54",
                        "taxon_hint": "NCBITaxon:6239",
                    },
                    "input_selectors": {
                        "mention": {"source": "payload", "path": "mention"},
                    },
                    "expected_result_fields": {
                        "curie": "primary_external_id",
                        "symbol": "gene_symbol",
                    },
                },
                "validation_result": {
                    "status": "resolved",
                    "validator_binding_id": "alliance_gene_reference_lookup",
                    "validator_agent": {
                        "package_id": "agr.alliance",
                        "agent_id": "gene_validation",
                    },
                    "resolved_values": {
                        "curie": "WB:WBGene00006763",
                        "symbol": "unc-54",
                    },
                    "missing_expected_fields": [],
                    "lookup_attempts": [
                        {
                            "provider": "agr_curation_query",
                            "method": "search_genes",
                            "query": {"gene_symbol": "unc-54"},
                            "result_count": 1,
                            "outcome": "success",
                            "message": "Resolved unc-54.",
                        },
                    ],
                    "curator_message": "Resolved unc-54.",
                    "explanation": "The gene lookup returned one match.",
                },
            },
        },
    )

    payload = domain_tools._validator_summary_payload(
        [cast(DomainValidationFinding, row)]
    )

    assert payload["summary_count"] == 1
    assert payload["by_result_status"] == {"resolved": 1}
    summary = payload["summaries"][0]
    assert summary["validator_binding_id"] == "alliance_gene_reference_lookup"
    assert summary["validator_agent"]["key_count"] == 2
    assert summary["selected_inputs"]["key_count"] == 2
    assert summary["expected_result_fields"]["key_count"] == 2
    assert summary["resolved_values"]["key_count"] == 2
    assert summary["lookup_attempts"]["length"] == 1
    assert summary["materialization_path_count"] == 2
    assert summary["verification_evidence"]["sha256"] == hashlib.sha256(
        domain_tools._canonical_json(row.finding_json).encode("utf-8")
    ).hexdigest()


def test_group_by_string_key_rejects_missing_grouping_key():
    with pytest.raises(
        ValueError,
        match="Item active-binding is missing required grouping key: state",
    ):
        domain_tools._group_by_string_key(
            [{"attachment_id": "active-binding", "validator_id": "validator-1"}],
            "state",
        )


def test_export_submission_readiness_replays_stale_revision_pin_for_blockers(monkeypatch):
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
    observed_revision_pins = []

    def build_context(**kwargs):
        observed_revision_pins.append(kwargs["expected_envelope_revisions"])
        return SimpleNamespace(envelope_snapshots={})

    monkeypatch.setattr(
        domain_tools,
        "_build_domain_envelope_submission_context",
        build_context,
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
        expected_envelope_revisions={"env-1": 2},
        mode="submission",
    )

    assert result["success"] is True
    assert result["mode"] == "submission"
    assert result["ready_count"] == 0
    assert result["blocker_count"] == 1
    assert result["domain_envelope_count"] == 1
    assert len(result["revision_set_sha256"]) == 64
    assert "candidate-1" not in json.dumps(result["detail_requests"])
    assert result["section"] == "summary"
    assert result["section_counts"] == {"candidates": 1, "blockers": 1}

    blocker_request = next(
        request for request in result["detail_requests"] if request["section"] == "blockers"
    )
    blocker_request.pop("supported_filters")
    assert blocker_request["expected_envelope_revisions"] == {"env-1": 2}
    blocker_page = domain_tools.get_export_submission_readiness(
        session_factory=FakeDb,
        user_auth_sub="curator-1",
        mode="submission",
        **blocker_request,
    )
    assert blocker_page["items"][0]["envelope_id"] == "env-1"
    assert blocker_page["items"][0]["candidate_id"] == "candidate-1"
    assert "read-only readiness explanation" in blocker_page["instruction"]
    assert observed_revision_pins == [{"env-1": 2}, {"env-1": 2}]


def test_export_submission_readiness_is_not_ready_without_candidates(monkeypatch):
    class FakeDb:
        def close(self):
            pass

    monkeypatch.setattr(
        domain_tools,
        "_session_visible_to_user",
        lambda _db, **_kwargs: True,
    )
    monkeypatch.setattr(
        domain_tools,
        "_load_session_for_validation",
        lambda _db, *, session_id: SimpleNamespace(candidates=[]),
    )
    monkeypatch.setattr(
        domain_tools,
        "_build_domain_envelope_submission_context",
        lambda **_kwargs: SimpleNamespace(envelope_snapshots={}),
    )

    result = domain_tools.get_export_submission_readiness(
        session_factory=FakeDb,
        user_auth_sub="curator-1",
        session_id="session-empty",
    )

    assert result["success"] is True
    assert result["candidate_count"] == 0
    assert result["ready_count"] == 0
    assert result["blocker_count"] == 0
    assert result["ready"] is False
    assert result["readiness_status"] == "no_candidates"


def test_runtime_limit_rejects_explicit_values_above_configured_maximum():
    with pytest.raises(
        ValueError,
        match=rf"limit must be between 1 and {domain_tools._RUNTIME_MAX_LIMIT}",
    ):
        domain_tools._runtime_limit(domain_tools._RUNTIME_MAX_LIMIT + 1)


def test_large_readiness_is_authoritative_then_pages_candidates_and_blockers(monkeypatch):
    class FakeDb:
        def close(self):
            pass

    class FakeReadiness:
        def __init__(self, candidate_id):
            self.candidate_id = str(candidate_id)

        def model_dump(self, *, mode):
            assert mode == "json"
            return {
                "candidate_id": self.candidate_id,
                "ready": False,
                "blockers": [{
                    "code": "fixture.blocker",
                    "envelope_id": "env-large",
                    "object_id": self.candidate_id,
                    "field_path": "field",
                    "message": "Resolve blocker.",
                }],
            }

    candidates = [SimpleNamespace(id=f"candidate-{index}") for index in range(1000)]
    monkeypatch.setattr(domain_tools, "_session_visible_to_user", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        domain_tools,
        "_load_session_for_validation",
        lambda *_args, **_kwargs: SimpleNamespace(candidates=candidates),
    )
    monkeypatch.setattr(
        domain_tools,
        "_build_domain_envelope_submission_context",
        lambda **_kwargs: SimpleNamespace(
            envelope_snapshots={
                "env-large": {
                    "envelope_id": "env-large",
                    "envelope_revision": 8,
                }
            }
        ),
    )
    monkeypatch.setattr(domain_tools, "_latest_candidate_validation_snapshot", lambda _item: {})
    monkeypatch.setattr(
        domain_tools,
        "_candidate_submission_readiness",
        lambda candidate, *_args, **_kwargs: FakeReadiness(candidate.id),
    )

    summary = domain_tools.get_export_submission_readiness(
        session_factory=FakeDb,
        user_auth_sub="curator-1",
        session_id="session-large",
    )
    assert summary["section"] == "summary"
    assert summary["candidate_count"] == 1000
    assert summary["ready_count"] == 0
    assert summary["blocker_count"] == 1000
    assert summary["readiness_status"] == "blocked"
    assert summary["section_counts"] == {"candidates": 1000, "blockers": 1000}
    assert summary["domain_envelope_count"] == 1
    assert len(json.dumps(summary)) < domain_tools._PROVIDER_INLINE_MAX_CHARS
    assert "items" not in summary
    assert all(
        request["readiness_token"] == summary["readiness_token"]
        and "candidate_ids" not in request
        and "expected_envelope_revisions" not in request
        for request in summary["detail_requests"]
    )

    for section, expected_count in summary["section_counts"].items():
        items = []
        request = next(
            dict(item) for item in summary["detail_requests"] if item["section"] == section
        )
        request.pop("supported_filters")
        request["limit"] = 4
        while True:
            page = domain_tools.get_export_submission_readiness(
                session_factory=FakeDb,
                user_auth_sub="curator-1",
                **request,
            )
            assert len(json.dumps(page)) < domain_tools._PROVIDER_INLINE_MAX_CHARS
            items.extend(page["items"])
            if page["complete"]:
                break
            request = page["next_request"]
            assert request["readiness_token"] == summary["readiness_token"]
        assert len(items) == expected_count
        if section == "candidates":
            assert all("blockers" not in item for item in items)
            assert all(item["blocker_count"] == 1 for item in items)


def test_readiness_partial_revision_pin_is_completed_for_every_selected_envelope(
    monkeypatch,
):
    class FakeDb:
        def close(self):
            pass

    class FakeReadiness:
        def __init__(self, candidate_id):
            self.candidate_id = str(candidate_id)

        def model_dump(self, *, mode):
            assert mode == "json"
            return {"candidate_id": self.candidate_id, "ready": True, "blockers": []}

    candidates = [SimpleNamespace(id="candidate-1"), SimpleNamespace(id="candidate-2")]
    monkeypatch.setattr(domain_tools, "_session_visible_to_user", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        domain_tools,
        "_load_session_for_validation",
        lambda *_args, **_kwargs: SimpleNamespace(candidates=candidates),
    )
    monkeypatch.setattr(
        domain_tools,
        "_build_domain_envelope_submission_context",
        lambda **_kwargs: SimpleNamespace(
            envelope_snapshots={
                "env-1": {"envelope_revision": 3},
                "env-2": {"envelope_revision": 7},
            }
        ),
    )
    monkeypatch.setattr(domain_tools, "_latest_candidate_validation_snapshot", lambda _item: {})
    monkeypatch.setattr(
        domain_tools,
        "_candidate_submission_readiness",
        lambda candidate, *_args, **_kwargs: FakeReadiness(candidate.id),
    )

    page = domain_tools.get_export_submission_readiness(
        session_factory=FakeDb,
        user_auth_sub="curator-1",
        session_id="session-1",
        candidate_ids=["candidate-1", "candidate-2"],
        expected_envelope_revisions={"env-1": 3},
        section="candidates",
        limit=1,
    )

    assert page["domain_envelope_count"] == 2
    assert len(page["revision_set_sha256"]) == 64
    assert "candidate_ids" not in page["next_request"]
    assert page["next_request"]["expected_envelope_revisions"] == {"env-1": 3}
    assert page["next_request"]["readiness_token"] == page["readiness_token"]
