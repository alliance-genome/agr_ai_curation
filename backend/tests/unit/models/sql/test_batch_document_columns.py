from src.models.sql.batch import BatchDocument


def test_batch_document_has_review_session_ids_column():
    assert "review_session_ids" in BatchDocument.__table__.columns
    column = BatchDocument.__table__.columns["review_session_ids"]
    assert column.nullable is True
