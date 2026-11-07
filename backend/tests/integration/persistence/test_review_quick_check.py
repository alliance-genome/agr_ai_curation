"""The shadow quick check reads the exact pinned packet and leaves review history untouched."""
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from src.lib.validation_history import store
from src.lib.validation_review import quick_check, reads
from src.models.sql.validation_history import ValidationFact
from tests.cancellation_test_support import install_cancel_reader
from tests.integration.persistence.test_review_grouped_sources import QUOTE, _build, _evidence, _object, _targets
from tests.integration.persistence.test_validation_history import migrated as migrated, scope as scope


@pytest.fixture(autouse=True)
def cancel_reader(monkeypatch):
    install_cancel_reader(monkeypatch, AsyncMock(return_value=False))


def _fact_count(artifact):
    with store.SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(ValidationFact).where(ValidationFact.artifact_id == artifact))


def test_quick_check_reads_the_pinned_packet_without_writing_history(scope):
    objects = [_object(index, ["e1", "e2"] if index % 2 else ["e1"]) for index in range(3)]
    envelope, artifact, episode, _, _ = _build(scope, objects, [_evidence("e1"), _evidence("e2", QUOTE + " second")])
    page = reads.review_packet(scope, episode)
    before = _fact_count(artifact)

    body = quick_check._load_packet(scope, episode, page["packet_id"])

    assert _fact_count(artifact) == before, "reading for the shadow check pins or appends nothing"
    assert body["records"] == page["records"] and body["evidence"] == page["evidence"]
    records = quick_check.packet_records(body)
    assert [(record["record"], record["target_id"], record["top_level"]) for record in records] == [
        (f"R{index + 1}", target, True) for index, target in enumerate(_targets(envelope))]
    text = quick_check.build_input("List the reagents.", quick_check.asked_records(records))
    assert text.count(f'evidence E1 (page 1, Appendix 1): "{QUOTE}"') == 3
    assert text.count(f'evidence E2 (page 1, Appendix 1): "{QUOTE} second"') == 1
    assert '  label: "VT0"' in text and "class_key" not in text


def test_quick_check_cannot_read_a_packet_outside_its_review(scope):
    _, _, episode, _, _ = _build(scope, [_object(0, ["e1"])], [_evidence("e1")])
    page = reads.review_packet(scope, episode)
    other = store.HistoryScope(scope.owner_id, scope.document_id, "another-session")
    with pytest.raises(store.HistoryAccessError):
        quick_check._load_packet(other, episode, page["packet_id"])
    with pytest.raises(store.HistoryConflict):
        quick_check._load_packet(scope, episode, "not-a-packet")
