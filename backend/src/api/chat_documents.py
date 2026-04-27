# ruff: noqa: F403,F405
"""Document selection endpoints for chat."""

from .chat_common import *


@router.post("/chat/document/load", response_model=DocumentStatusResponse)
async def load_document_for_chat(
    payload: LoadDocumentRequest,
    user: Dict[str, Any] = get_auth_dependency()
) -> DocumentStatusResponse:
    """Select a document for chat interactions."""
    user_id = user.get("sub")
    logger.info(
        "Loading document for chat: %s",
        payload.document_id,
        extra={"user_id": user_id, "document_id": payload.document_id},
    )

    try:
        document_detail = await get_document(user["sub"], payload.document_id)
        logger.info(
            "Successfully retrieved document: %s",
            payload.document_id,
            extra={"user_id": user_id, "document_id": payload.document_id},
        )
    except ValueError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=404,
            detail="Document not found",
            log_message=f"Document {payload.document_id} is unavailable for chat load",
            exc=exc,
            level=logging.WARNING,
        )
    except Exception as exc:
        logger.error(
            "Error loading document %s: %s",
            payload.document_id,
            exc,
            extra={"user_id": user_id, "document_id": payload.document_id},
        )
        raise HTTPException(status_code=500, detail="Failed to load document for chat") from exc

    document_summary = document_detail.get("document")
    if not document_summary:
        logger.error(
            "Document payload missing summary for %s",
            payload.document_id,
            extra={"user_id": user_id, "document_id": payload.document_id},
        )
        raise HTTPException(status_code=500, detail="Document summary unavailable")

    document_state.set_document(user['sub'], document_summary)

    # Invalidate document metadata cache to ensure fresh data for new document
    from src.lib.document_cache import invalidate_cache
    invalidate_cache(user['sub'], payload.document_id)

    active_document = _build_active_document(document_summary)
    return DocumentStatusResponse(
        active=True,
        document=active_document,
        message=f"Document '{active_document.filename or active_document.id}' loaded for chat",
    )


@router.get("/chat/document", response_model=DocumentStatusResponse)
async def get_loaded_document(user: Dict[str, Any] = get_auth_dependency()) -> DocumentStatusResponse:
    """Return information about the currently loaded document."""
    document_summary = document_state.get_document(user['sub'])
    if not document_summary:
        return DocumentStatusResponse(active=False, message="No document selected")

    return DocumentStatusResponse(active=True, document=_build_active_document(document_summary))


@router.delete("/chat/document", response_model=DocumentStatusResponse)
async def clear_loaded_document(user: Dict[str, Any] = get_auth_dependency()) -> DocumentStatusResponse:
    """Clear the current document selection."""
    document_summary = document_state.get_document(user['sub'])
    if not document_summary:
        return DocumentStatusResponse(active=False, message="No document was loaded")

    active_document = _build_active_document(document_summary)
    document_state.clear_document(user['sub'])
    return DocumentStatusResponse(
        active=False,
        document=active_document,
        message="Document selection cleared",
    )


# Session Management Endpoints
