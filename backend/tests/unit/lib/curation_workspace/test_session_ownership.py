"""A review session belongs to the curator who created it (ALL-1547)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import String, func, select

from src.lib.curation_workspace import session_ownership as ownership
from src.lib.curation_workspace import session_queries
from src.lib.curation_workspace.models import CurationExtractionResultRecord
from src.lib.curation_workspace.bootstrap_service import _select_bootstrap_extraction_result
from src.lib.curation_workspace.prepared_session_service import find_reusable_prepared_session
from src.schemas.curation_workspace import (
    CurationDocumentBootstrapRequest,
    CurationInventoryScope,
    CurationNextSessionRequest,
    CurationSessionFilters,
    CurationSessionListRequest,
    CurationSessionStatsRequest,
)
from tests.curation_ownership_support import (
    add_candidate,
    add_document,
    add_envelope,
    add_extraction_result,
    add_operation,
    add_session,
    add_snapshot,
    add_submission,
    add_user,
    ownership_db as ownership_db,
    ownership_session_factory as ownership_session_factory,
)

OWNER = "curator-owner"
OTHER = "curator-other"


@pytest.fixture
def world(ownership_db):
    owner = add_user(ownership_db, OWNER)
    other = add_user(ownership_db, OTHER)
    document = add_document(ownership_db, owner=owner)
    other_document = add_document(ownership_db, owner=other)
    # Assigned to the other curator: assignment no longer grants anything.
    mine = add_session(
        ownership_db, document_id=document.id, created_by_id=OWNER, assigned_curator_id=OTHER
    )
    theirs = add_session(
        ownership_db, document_id=document.id, created_by_id=OTHER, assigned_curator_id=OWNER
    )
    ownerless = add_session(ownership_db, document_id=document.id, created_by_id=None)
    ownership_db.commit()
    return {
        "db": ownership_db,
        "owner": owner,
        "other": other,
        "document": document,
        "other_document": other_document,
        "mine": mine,
        "theirs": theirs,
        "ownerless": ownerless,
    }


def _assert_not_found(exc_info, *, leaked: tuple[str, ...] = (OWNER, OTHER)):
    assert exc_info.value.status_code == 404
    detail = str(exc_info.value.detail)
    for value in leaked:
        assert value not in detail


def test_load_owned_session_returns_only_the_creators_session(world):
    db = world["db"]

    assert ownership.load_owned_session(db, world["mine"].id, OWNER).id == world["mine"].id
    for session_id in (world["theirs"].id, world["ownerless"].id, uuid4()):
        with pytest.raises(HTTPException) as exc_info:
            ownership.load_owned_session(db, session_id, OWNER)
        _assert_not_found(exc_info)


def test_not_owned_and_missing_sessions_are_indistinguishable(world):
    db = world["db"]
    missing_id = uuid4()

    with pytest.raises(HTTPException) as not_owned:
        ownership.load_owned_session(db, world["theirs"].id, OWNER)
    with pytest.raises(HTTPException) as missing:
        ownership.load_owned_session(db, missing_id, OWNER)

    assert not_owned.value.detail.replace(str(world["theirs"].id), "<id>") == (
        missing.value.detail.replace(str(missing_id), "<id>")
    )


def test_ownerless_session_is_visible_to_nobody(world):
    db = world["db"]
    for caller in (OWNER, OTHER, None, ""):
        with pytest.raises(HTTPException):
            ownership.load_owned_session(db, world["ownerless"].id, caller)
        assert ownership.session_is_owned(db, world["ownerless"].id, caller) is False


def test_session_is_owned_rejects_malformed_identifiers(world):
    assert ownership.session_is_owned(world["db"], "not-a-uuid", OWNER) is False


def test_system_loader_is_unscoped_for_background_work(world):
    db = world["db"]
    for key in ("mine", "theirs", "ownerless"):
        assert ownership.load_session_for_system(db, world[key].id).id == world[key].id


def test_candidate_resolver_uses_the_candidates_session(world):
    db = world["db"]
    mine = add_candidate(db, session_id=world["mine"].id)
    theirs = add_candidate(db, session_id=world["theirs"].id)
    db.commit()

    assert ownership.load_owned_candidate_session(db, mine.id, OWNER).id == world["mine"].id
    for candidate_id in (theirs.id, uuid4()):
        with pytest.raises(HTTPException) as exc_info:
            ownership.load_owned_candidate_session(db, candidate_id, OWNER)
        _assert_not_found(exc_info)


def test_submission_resolver_requires_owned_session_and_matching_submission(world):
    db = world["db"]
    mine = add_submission(db, session_id=world["mine"].id)
    theirs = add_submission(db, session_id=world["theirs"].id)
    db.commit()

    assert (
        ownership.load_owned_submission_session(db, world["mine"].id, mine.id, OWNER).id
        == world["mine"].id
    )
    for session_id, submission_id in (
        (world["theirs"].id, theirs.id),
        # An owned session cannot be used to reach another curator's submission.
        (world["mine"].id, theirs.id),
    ):
        with pytest.raises(HTTPException) as exc_info:
            ownership.load_owned_submission_session(db, session_id, submission_id, OWNER)
        _assert_not_found(exc_info)


def test_operation_resolver_uses_the_operations_session(world):
    db = world["db"]
    mine = add_operation(db, session_id=world["mine"].id, owner_id=OWNER)
    theirs = add_operation(db, session_id=world["theirs"].id, owner_id=OTHER)
    db.commit()

    assert ownership.load_owned_operation_session(db, mine.id, OWNER).id == world["mine"].id
    for operation_id in (theirs.id, str(uuid4())):
        with pytest.raises(HTTPException) as exc_info:
            ownership.load_owned_operation_session(db, operation_id, OWNER)
        _assert_not_found(exc_info)


def test_snapshot_resolver_uses_the_snapshots_session(world):
    db = world["db"]
    add_envelope(db, envelope_id="env-mine", document_id=world["document"].id, session_id=world["mine"].id)
    add_envelope(db, envelope_id="env-theirs", document_id=world["document"].id, session_id=world["theirs"].id)
    mine = add_snapshot(db, session_id=world["mine"].id, envelope_id="env-mine", created_by_id=OWNER)
    theirs = add_snapshot(db, session_id=world["theirs"].id, envelope_id="env-theirs", created_by_id=OTHER)
    db.commit()

    assert ownership.load_owned_snapshot_session(db, mine.id, OWNER).id == world["mine"].id
    for snapshot_id in (theirs.id, uuid4()):
        with pytest.raises(ownership.OwnedResourceNotFound):
            ownership.load_owned_snapshot_session(db, snapshot_id, OWNER)


def test_envelope_visible_through_any_linked_owned_session(world):
    db = world["db"]
    document_id = world["document"].id
    # Saved against another curator's session, but linked into mine by a candidate.
    add_envelope(db, envelope_id="env-linked", document_id=document_id, session_id=world["theirs"].id)
    add_candidate(db, session_id=world["mine"].id, envelope_id="env-linked")
    add_envelope(db, envelope_id="env-mine", document_id=document_id, session_id=world["mine"].id)
    add_envelope(db, envelope_id="env-theirs", document_id=document_id, session_id=world["theirs"].id)
    add_envelope(db, envelope_id="env-ownerless", document_id=document_id, session_id=world["ownerless"].id)
    db.commit()

    assert ownership.envelope_visible_to(db, "env-linked", OWNER) is True
    assert ownership.envelope_visible_to(db, "env-mine", OWNER) is True
    assert ownership.envelope_visible_to(db, "env-theirs", OWNER) is False
    assert ownership.envelope_visible_to(db, "env-ownerless", OWNER) is False
    assert ownership.envelope_visible_to(db, "env-ownerless", OTHER) is False
    assert ownership.envelope_visible_to(db, "env-missing", OWNER) is False
    with pytest.raises(HTTPException) as exc_info:
        ownership.require_visible_envelope(db, "env-theirs", OWNER)
    _assert_not_found(exc_info)


def _stored_text_id(db, extraction_result_id):
    # Envelopes name their source by the source row's id as text; read it the
    # way the database renders it (SQLite stores UUIDs without hyphens).
    return db.scalar(
        select(func.cast(CurationExtractionResultRecord.id, String)).where(
            CurationExtractionResultRecord.id == extraction_result_id
        )
    )


def test_sessionless_envelope_uses_the_extraction_results_user(world):
    db = world["db"]
    document_id = world["document"].id
    own_source = add_extraction_result(db, document_id=document_id, user_id=OWNER, agent_key="gene_extractor")
    other_source = add_extraction_result(db, document_id=document_id, user_id=OTHER, agent_key="gene_extractor")
    own_source_id = _stored_text_id(db, own_source.id)
    add_envelope(db, envelope_id="env-own-source", document_id=document_id,
                 source_extraction_result_id=own_source_id)
    add_envelope(db, envelope_id="env-other-source", document_id=document_id,
                 source_extraction_result_id=_stored_text_id(db, other_source.id))
    # Once a candidate links it, the linked session decides, not the source.
    add_envelope(db, envelope_id="env-own-source-linked-elsewhere", document_id=document_id,
                 source_extraction_result_id=own_source_id)
    add_candidate(db, session_id=world["theirs"].id, envelope_id="env-own-source-linked-elsewhere")
    db.commit()

    assert ownership.envelope_visible_to(db, "env-own-source", OWNER) is True
    assert ownership.envelope_visible_to(db, "env-other-source", OWNER) is False
    assert ownership.envelope_visible_to(db, "env-own-source-linked-elsewhere", OWNER) is False


def test_unlinked_manual_preparation_is_not_a_workspace_record(world):
    db = world["db"]
    add_envelope(
        db,
        envelope_id="env-prepared",
        document_id=world["document"].id,
        session_id=world["mine"].id,
        metadata={"manual_preparation": {"candidate_id": str(uuid4()), "source_sha256": "0" * 64}},
    )
    db.commit()
    assert ownership.envelope_visible_to(db, "env-prepared", OWNER) is False

    add_candidate(db, session_id=world["mine"].id, envelope_id="env-prepared")
    db.commit()
    assert ownership.envelope_visible_to(db, "env-prepared", OWNER) is True


def test_document_check_requires_the_callers_own_curator_document(world):
    db = world["db"]
    frozen = add_document(db, owner=world["owner"], viewer_mode="benchmark_frozen")
    db.commit()

    assert ownership.require_owned_document(db, world["document"].id, OWNER).id == world["document"].id
    for document_id in (world["other_document"].id, frozen.id, uuid4()):
        with pytest.raises(HTTPException) as exc_info:
            ownership.require_owned_document(db, document_id, OWNER)
        _assert_not_found(exc_info)
    with pytest.raises(HTTPException) as unknown_user:
        ownership.require_owned_document(db, world["document"].id, "curator-without-account")
    assert unknown_user.value.status_code == 404


@pytest.mark.parametrize(
    "scope",
    [
        CurationInventoryScope.MY_INVENTORY,
        CurationInventoryScope.SHOW_ALL,
        CurationInventoryScope.MY_ORGANIZATION,
    ],
)
def test_inventory_queries_filter_by_owner_for_every_scope(world, scope):
    db = world["db"]
    # The old filter reached sessions through assignment; it is ignored now.
    filters = CurationSessionFilters(inventory_scope=scope, curator_ids=[OWNER, OTHER])

    listed = session_queries.list_sessions(
        db, CurationSessionListRequest(filters=filters), current_user_id=OWNER
    )
    stats = session_queries.get_session_stats(
        db, CurationSessionStatsRequest(filters=filters), current_user_id=OWNER
    )
    first = session_queries.get_next_session(
        db, CurationNextSessionRequest(filters=filters), current_user_id=OWNER
    )

    assert [item.session_id for item in listed.sessions] == [str(world["mine"].id)]
    assert listed.page_info.total_items == 1
    assert stats.stats.total_sessions == 1
    assert first.session is not None and first.session.session_id == str(world["mine"].id)
    assert first.queue_context.total_sessions == 1
    with pytest.raises(HTTPException) as exc_info:
        session_queries.get_next_session(
            db,
            CurationNextSessionRequest(filters=filters, current_session_id=str(world["theirs"].id)),
            current_user_id=OWNER,
        )
    assert exc_info.value.status_code == 404


def test_each_curator_lists_only_their_own_sessions(world):
    db = world["db"]
    request = CurationSessionListRequest(filters=CurationSessionFilters())

    owner_ids = [item.session_id for item in session_queries.list_sessions(db, request, current_user_id=OWNER).sessions]
    other_ids = [item.session_id for item in session_queries.list_sessions(db, request, current_user_id=OTHER).sessions]

    assert owner_ids == [str(world["mine"].id)]
    assert other_ids == [str(world["theirs"].id)]


def test_reusable_prepared_session_belongs_to_the_caller(world):
    db = world["db"]
    document_id = world["document"].id
    source = add_extraction_result(db, document_id=document_id, user_id=OTHER, flow_run_id="flow-1")
    theirs = add_session(db, document_id=document_id, created_by_id=OTHER, flow_run_id="flow-1")
    add_candidate(db, session_id=theirs.id)
    db.commit()

    kwargs = {
        "document_id": str(document_id),
        "adapter_key": "fixture",
        "flow_run_id": "flow-1",
        "prep_extraction_result_id": str(source.id),
    }
    assert find_reusable_prepared_session(db, owner_id=OWNER, **kwargs) is None
    reusable = find_reusable_prepared_session(db, owner_id=OTHER, **kwargs)
    assert reusable is not None and reusable.session_id == str(theirs.id)


def test_bootstrap_selects_only_the_callers_prep_result(world):
    db = world["db"]
    document_id = world["document"].id
    add_extraction_result(db, document_id=document_id, user_id=OTHER)
    db.commit()
    request = CurationDocumentBootstrapRequest(adapter_key="fixture")

    with pytest.raises(HTTPException) as exc_info:
        _select_bootstrap_extraction_result(
            db, document_id=str(document_id), request=request, current_user_id=OWNER
        )
    assert exc_info.value.status_code == 404

    own = add_extraction_result(db, document_id=document_id, user_id=OWNER)
    db.commit()
    selected = _select_bootstrap_extraction_result(
        db, document_id=str(document_id), request=request, current_user_id=OWNER
    )
    assert selected.id == own.id


def test_session_update_cannot_reassign_the_session(world):
    from src.lib.curation_workspace.models import CurationActionLogEntry
    from src.lib.curation_workspace.session_mutation_service import update_session
    from src.schemas.curation_workspace import CurationSessionUpdateRequest

    db = world["db"]
    session_id = str(world["mine"].id)

    response = update_session(
        db,
        session_id,
        CurationSessionUpdateRequest(session_id=session_id, curator_id="curator-new", notes="kept"),
        {"sub": OWNER},
    )
    db.commit()

    refreshed = ownership.load_owned_session(db, session_id, OWNER)
    assert refreshed.created_by_id == OWNER
    assert refreshed.assigned_curator_id == OTHER
    assert refreshed.notes == "kept"
    assert response.action_log_entry is None
    assert db.query(CurationActionLogEntry).count() == 0
