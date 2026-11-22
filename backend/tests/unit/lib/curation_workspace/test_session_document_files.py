"""The session document carries the paper's files; submissions do not (ALL-1460, P9a).

``_document_ref`` also feeds the stored submission retry fingerprint and the export
payload. A file joining the paper must change neither, so only the inventory and
workspace document (``CurationSessionDocumentRef``) lists files. Made-up names.
"""

from datetime import datetime, timezone
from uuid import UUID

from src.lib.curation_workspace import session_serializers as serializer_module
from src.lib.curation_workspace import session_submission_service as submission_module
from src.lib.pdf_viewer.files import viewer_file_refs
from src.models.sql.pdf_document import PDFDocumentFile
from src.schemas.curation_workspace import (
    CurationDocumentRef,
    CurationExportPayloadContext,
    CurationSessionDocumentRef,
    CurationSessionSummary,
)
from tests.pdf_document_test_support import new_paper

DOCUMENT_ID = UUID("6f0c1f4e-3a0f-4c39-9a51-6f0b2d1c0a11")
SUPPLEMENT_ID = UUID("aaaaaaaa-0000-0000-0000-0000000000a2")
_TIMESTAMP = datetime(2026, 11, 22, tzinfo=timezone.utc)


def _paper(*, with_supplement: bool):
    paper = new_paper(
        id=DOCUMENT_ID,
        user_id=1,
        filename="made-up-paper.pdf",
        title="Made-up paper",
        status="completed",
        upload_timestamp=_TIMESTAMP,
        file_path="owner/pdf/main.pdf",
        file_hash="a" * 64,
        file_size=4096,
        page_count=9,
    )
    paper.files[0].id = UUID("aaaaaaaa-0000-0000-0000-0000000000a1")
    if with_supplement:
        paper.files.append(
            PDFDocumentFile(
                id=SUPPLEMENT_ID,
                role="supplement",
                display_name="Table_S1.xlsx",
                status="ready",
                tab_order=1,
                display_mode="text",
                page_count=None,
                file_path=f"owner/source_markdown/{SUPPLEMENT_ID}.md",
                file_hash="b" * 64,
                file_size=2048,
                added_at=_TIMESTAMP,
            )
        )
    return paper


def test_session_document_lists_the_files_the_viewer_route_lists():
    paper = _paper(with_supplement=True)

    reference = serializer_module._session_document_ref(paper)

    assert isinstance(reference, CurationSessionDocumentRef)
    assert reference.files == viewer_file_refs(paper)
    assert [file_ref.file_id for file_ref in reference.files] == [paper.files[0].id, SUPPLEMENT_ID]
    # The single-file fields stay as they were until P9a2.
    assert reference.viewer_url == f"/api/pdf-viewer/documents/{DOCUMENT_ID}/content"
    assert reference.page_count == 9


def test_session_summary_document_requires_files():
    assert CurationSessionSummary.model_fields["document"].annotation is CurationSessionDocumentRef
    assert CurationSessionDocumentRef.model_fields["files"].is_required()


def test_submission_document_has_no_files():
    reference = serializer_module._document_ref(_paper(with_supplement=True))

    assert type(reference) is CurationDocumentRef
    assert "files" not in reference.model_dump()


def test_a_joining_file_changes_neither_the_retry_fingerprint_nor_the_export_document():
    before = submission_module._document_intent_payload(_paper(with_supplement=False))
    after = submission_module._document_intent_payload(_paper(with_supplement=True))

    assert after == before
    assert "files" not in after

    export_document = serializer_module._document_ref(_paper(with_supplement=True)).model_dump(mode="json")
    context = CurationExportPayloadContext.model_validate(
        {
            "session_id": "session-1",
            "document": export_document,
            "candidates": [],
            "candidate_ids": [],
            "candidate_count": 0,
        }
    )
    assert type(context.document) is CurationDocumentRef
