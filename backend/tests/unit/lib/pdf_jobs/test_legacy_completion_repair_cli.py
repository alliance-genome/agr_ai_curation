"""The legacy completion repair command: modes, exit codes, content-free output, index reader."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.lib.pdf_jobs import legacy_completion_repair as repair

PAPER = UUID("11111111-1111-1111-1111-111111111111")
UPLOADED = datetime(2025, 12, 2, tzinfo=timezone.utc)


def _candidate(failed=()):
    return repair.Candidate(
        document_id=PAPER,
        upload_timestamp=UPLOADED,
        previous=repair.PreviousState("pending", None, None, None),
        failed_checks=tuple(failed),
    )


def _report(mode, candidates, written=0):
    return repair.RepairReport(
        mode=mode, run_at=UPLOADED, first_job_created_at=UPLOADED, candidates=tuple(candidates), written=written,
    )


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        (_report("dry_run", []), repair.EXIT_NOTHING_TO_DO),
        (_report("dry_run", [_candidate()]), repair.EXIT_WOULD_CHANGE),
        (_report("apply", [_candidate()], written=1), repair.EXIT_CHANGED),
        (_report("apply", [], written=0), repair.EXIT_NOTHING_TO_DO),
        (_report("dry_run", [_candidate(), _candidate(["S8_pieces_in_search_index"])]), repair.EXIT_PARTIAL_MATCHES),
    ],
)
def test_exit_codes_tell_nothing_would_change_changed_and_partial_apart(report, expected):
    assert report.exit_code() == expected


def test_the_report_holds_ids_times_statuses_and_checks_only():
    report = _report("dry_run", [_candidate(), _candidate(["S5_pdf_stored"])])

    payload = report.to_json()

    assert payload["qualifying"][0] == {
        "document_id": str(PAPER),
        "upload_timestamp": UPLOADED.isoformat(),
        "previous": {
            "status": "pending", "error_message": None,
            "processing_started_at": None, "processing_completed_at": None,
        },
        "repaired_to": {
            "status": "completed",
            "processing_started_at": UPLOADED.isoformat(),
            "processing_completed_at": UPLOADED.isoformat(),
        },
    }
    assert payload["partial"] == [{"document_id": str(PAPER), "failed_checks": ["S5_pdf_stored"]}]
    assert payload["selection_digest"] == repair.selection_digest([PAPER])
    assert set(payload) == {
        "mode", "run_at", "checks", "first_job_created_at", "candidate_count", "qualifying_count",
        "partial_count", "selection_digest", "written", "restored", "qualifying", "partial",
    }


def test_the_digest_does_not_depend_on_order():
    other = UUID("22222222-2222-2222-2222-222222222222")
    assert repair.selection_digest([PAPER, other]) == repair.selection_digest([other, PAPER])


def test_apply_without_the_dry_runs_count_and_digest_is_refused(capsys):
    assert repair.main(["--apply"], session_factory=object, storage_root=None, index=object()) == repair.EXIT_ERROR
    assert "--expect-count" in capsys.readouterr().err


def test_restore_without_a_digest_is_refused(capsys, tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text("{}")
    assert repair.main(["--restore-from-report", str(report_path)], session_factory=object) == repair.EXIT_ERROR


def test_the_dry_run_is_the_default_and_prints_the_report(monkeypatch, capsys):
    observed = {}

    def fake_dry_run(session_factory, *, storage_root, index, now):
        observed["called"] = True
        return _report("dry_run", [_candidate()])

    monkeypatch.setattr(repair, "dry_run", fake_dry_run)
    monkeypatch.setattr(repair, "apply", lambda *a, **k: pytest.fail("apply must not run"))

    code = repair.main([], session_factory=object, storage_root=object(), index=object())

    assert code == repair.EXIT_WOULD_CHANGE and observed == {"called": True}
    printed = json.loads(capsys.readouterr().out)
    assert printed["qualifying_count"] == 1 and printed["mode"] == "dry_run"


def test_an_error_is_reported_by_type_with_exit_code_one(monkeypatch, capsys):
    def failing(*_args, **_kwargs):
        raise repair.LegacyRepairError("selection_changed: made-up")

    monkeypatch.setattr(repair, "apply", failing)

    code = repair.main(
        ["--apply", "--expect-count", "1", "--expect-digest", "0" * 64],
        session_factory=object, storage_root=object(), index=object(),
    )

    assert code == repair.EXIT_ERROR
    assert json.loads(capsys.readouterr().err)["error"] == "LegacyRepairError"


class _Collection:
    def __init__(self, items=(), found=None):
        self._items = items
        self._found = found
        self.query = SimpleNamespace(fetch_object_by_id=lambda _id: self._found)

    def iterator(self, return_properties):
        assert return_properties == ["documentId", "chunkIndex"]
        return iter(self._items)


def test_the_index_reader_groups_piece_numbers_and_reads_document_statuses(monkeypatch):
    pieces = [SimpleNamespace(properties={"documentId": str(PAPER), "chunkIndex": index}) for index in (1, 0)]
    found = SimpleNamespace(properties={"processingStatus": "completed", "embeddingStatus": "completed"})
    monkeypatch.setattr(
        "src.lib.weaviate_helpers.get_user_collections",
        lambda client, owner: (_Collection(items=pieces), _Collection(found=found)),
    )
    reader = repair.WeaviateIndexReader(client=object())

    assert reader.piece_numbers("owner") == {str(PAPER): [1, 0]}
    assert reader.document_statuses("owner", str(PAPER)) == ("completed", "completed")


def test_the_index_reader_refuses_a_piece_without_a_number(monkeypatch):
    pieces = [SimpleNamespace(properties={"documentId": str(PAPER), "chunkIndex": None})]
    monkeypatch.setattr(
        "src.lib.weaviate_helpers.get_user_collections",
        lambda client, owner: (_Collection(items=pieces), _Collection()),
    )

    with pytest.raises(repair.LegacyRepairError):
        repair.WeaviateIndexReader(client=object()).piece_numbers("owner")
