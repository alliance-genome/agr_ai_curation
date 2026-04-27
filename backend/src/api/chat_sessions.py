"""Durable chat session and conversation endpoints."""

from .chat_common import *


@router.post("/chat/session", response_model=SessionResponse)
async def create_session(
    request: CreateSessionRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
):
    """Create and persist one durable chat session for the authenticated user."""

    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)
    session_id = str(uuid.uuid4())
    active_document_id, active_document = _resolve_session_create_active_document(
        repository=repository,
        user_id=user_id,
    )

    try:
        session = repository.create_session(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=request.chat_kind,
            active_document_id=active_document_id,
        )
        db.commit()
    except Exception as exc:
        logger.error(
            "Failed to create durable chat session %s",
            session_id,
            extra={"session_id": session_id, "user_id": user_id},
            exc_info=True,
        )
        _rollback_and_raise(
            db,
            status_code=500,
            detail="Failed to create chat session",
            exc=exc,
        )

    logger.info(
        "Created durable chat session: %s",
        session_id,
        extra={"session_id": session_id, "user_id": user_id},
    )
    return SessionResponse(
        session_id=session.session_id,
        created_at=session.created_at,
        updated_at=session.updated_at,
        title=session.title,
        active_document_id=str(session.active_document_id) if session.active_document_id else None,
        active_document=active_document,
    )

@router.get("/chat/status")
async def chat_status(user: Dict[str, Any] = get_auth_dependency()):
    """Check the status of the chat service."""
    import os
    return {
        "service": "chat",
        "status": "ready",
        "engine": "openai-agents-sdk",
        "openai_key_configured": bool(os.getenv("OPENAI_API_KEY"))
    }


# Conversation History Endpoints

@router.get("/chat/conversation", response_model=ConversationStatusResponse)
async def get_conversation_status(
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
) -> ConversationStatusResponse:
    """Get the current conversation status and memory statistics for the authenticated user."""
    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)

    try:
        latest_session = _latest_visible_chat_session(repository, user_id=user_id)
        stats = _build_durable_conversation_stats(
            repository,
            user_id=user_id,
            current_session=latest_session,
        )
        return ConversationStatusResponse(
            is_active=latest_session is not None,
            conversation_id=latest_session.session_id if latest_session is not None else None,
            memory_stats=stats,
            message="Conversation status retrieved successfully",
        )
    except Exception as e:
        logger.error(
            "Failed to get conversation status: %s",
            e,
            extra={"user_id": user_id},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to retrieve conversation status") from e


@router.post("/chat/conversation/reset", response_model=ConversationResetResponse)
async def reset_conversation(
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
) -> ConversationResetResponse:
    """Reset the conversation memory for the authenticated user and start a new conversation."""
    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)

    try:
        active_document_id, _active_document = _resolve_session_create_active_document(
            repository=repository,
            user_id=user_id,
        )
        new_session_id = str(uuid.uuid4())
        new_session = repository.create_session(
            session_id=new_session_id,
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
            active_document_id=active_document_id,
        )
        db.commit()
        stats = _build_durable_conversation_stats(
            repository,
            user_id=user_id,
            current_session=new_session,
        )
        return ConversationResetResponse(
            success=True,
            message="Conversation reset successfully. Use the provided session_id for the next message.",
            memory_stats=stats,
            session_id=new_session_id,
        )
    except Exception as e:
        db.rollback()
        logger.error(
            "Failed to reset conversation: %s",
            e,
            extra={"user_id": user_id},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to reset conversation") from e


@router.get("/chat/history/{session_id}", response_model=ChatSessionDetailResponse)
async def get_session_history(
    session_id: str,
    message_limit: int = Query(100, ge=1, le=200),
    message_cursor: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
    background_tasks: BackgroundTasks = None,
):
    """Return one durable chat session plus one page of persisted transcript rows."""

    user_id = _require_user_sub(user)
    if not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id is required")
    repository = _get_chat_history_repository(db)

    try:
        detail = repository.get_session_detail(
            session_id=session_id,
            user_auth_sub=user_id,
            message_limit=message_limit,
            message_cursor=_decode_message_cursor(message_cursor),
        )
    except ValueError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=400,
            detail="Invalid chat history request",
            log_message=f"Failed to load chat history for session {session_id}",
            exc=exc,
            level=logging.WARNING,
        )
    if detail is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    active_document = await _load_session_active_document(
        user_id=user_id,
        active_document_id=detail.session.active_document_id,
    )
    generated_title = None
    if detail.session.effective_title is None:
        if message_cursor is None:
            generated_title = _generate_title_from_messages(detail.messages)
        _queue_chat_title_backfill(
            background_tasks,
            session_id=session_id,
            user_id=user_id,
            preferred_generated_title=generated_title,
        )
    return ChatSessionDetailResponse(
        session=_serialize_session(detail.session, title_override=generated_title),
        active_document=active_document,
        messages=[_serialize_message(message) for message in detail.messages],
        message_limit=message_limit,
        next_message_cursor=_encode_message_cursor(detail.next_message_cursor),
    )


@router.get("/chat/history", response_model=ChatSessionListResponse)
async def get_all_sessions_stats(
    chat_kind: Literal["assistant_chat", "agent_studio", "all"] = Query(...),
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    document_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
    background_tasks: BackgroundTasks = None,
):
    """Browse or search durable chat sessions visible to the authenticated user."""

    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)
    normalized_query = query.strip() if query is not None else None
    if query is not None and not normalized_query:
        raise HTTPException(status_code=400, detail="query cannot be blank")

    active_document_id = _parse_document_filter(document_id)
    decoded_cursor = _decode_session_cursor(cursor)

    try:
        if normalized_query:
            page = repository.search_sessions(
                user_auth_sub=user_id,
                chat_kind=chat_kind,
                query=normalized_query,
                limit=limit,
                cursor=decoded_cursor,
                active_document_id=active_document_id,
            )
            total_sessions = repository.count_sessions(
                user_auth_sub=user_id,
                chat_kind=chat_kind,
                query=normalized_query,
                active_document_id=active_document_id,
            )
        else:
            page = repository.list_sessions(
                user_auth_sub=user_id,
                chat_kind=chat_kind,
                limit=limit,
                cursor=decoded_cursor,
                active_document_id=active_document_id,
            )
            total_sessions = repository.count_sessions(
                user_auth_sub=user_id,
                chat_kind=chat_kind,
                active_document_id=active_document_id,
            )
    except ValueError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=400,
            detail="Invalid chat history query",
            log_message=f"Failed to list chat history for user {user_id}",
            exc=exc,
            level=logging.WARNING,
        )

    for session in page.items:
        if session.effective_title is None:
            _queue_chat_title_backfill(
                background_tasks,
                session_id=session.session_id,
                user_id=user_id,
            )

    return ChatSessionListResponse(
        chat_kind=chat_kind,
        total_sessions=total_sessions,
        limit=limit,
        query=normalized_query,
        document_id=str(active_document_id) if active_document_id else None,
        next_cursor=_encode_session_cursor(page.next_cursor),
        sessions=[_serialize_session(session) for session in page.items],
    )


@router.patch("/chat/session/{session_id}", response_model=RenameSessionResponse)
async def rename_session(
    session_id: str,
    request: RenameSessionRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
):
    """Rename one durable chat session visible to the authenticated user."""

    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)

    try:
        session = repository.rename_session(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
            title=request.title,
        )
        if session is None:
            db.rollback()
            raise HTTPException(status_code=404, detail="Chat session not found")
        db.commit()
    except HTTPException:
        raise
    except ValueError as exc:
        _rollback_and_raise(
            db,
            status_code=400,
            detail="Invalid chat session update",
            exc=exc,
            log_message=f"Failed to rename chat session {session_id}",
            level=logging.WARNING,
        )
    except Exception as exc:
        logger.error(
            "Failed to rename chat session %s",
            session_id,
            extra={"session_id": session_id, "user_id": user_id},
            exc_info=True,
        )
        _rollback_and_raise(
            db,
            status_code=500,
            detail="Failed to rename chat session",
            exc=exc,
        )

    return RenameSessionResponse(session=_serialize_session(session))


@router.delete("/chat/session/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
) -> Response:
    """Soft-delete one durable chat session visible to the authenticated user."""

    user_id = _require_user_sub(user)
    if not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id is required")
    repository = _get_chat_history_repository(db)

    try:
        deleted = repository.soft_delete_session(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
        )
        if not deleted:
            db.rollback()
            raise HTTPException(status_code=404, detail="Chat session not found")
        db.commit()
    except HTTPException:
        raise
    except ValueError as exc:
        _rollback_and_raise(
            db,
            status_code=400,
            detail="Invalid chat session request",
            exc=exc,
            log_message=f"Failed to delete chat session {session_id}",
            level=logging.WARNING,
        )
    except Exception as exc:
        logger.error(
            "Failed to delete chat session %s",
            session_id,
            extra={"session_id": session_id, "user_id": user_id},
            exc_info=True,
        )
        _rollback_and_raise(
            db,
            status_code=500,
            detail="Failed to delete chat session",
            exc=exc,
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/chat/session/bulk-delete", response_model=BulkDeleteSessionsResponse)
async def bulk_delete_sessions(
    request: BulkDeleteSessionsRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
):
    """Soft-delete multiple durable chat sessions visible to the authenticated user."""

    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)
    seen_session_ids: set[str] = set()
    normalized_session_ids: List[str] = []

    for raw_session_id in request.session_ids:
        normalized_session_id = raw_session_id.strip()
        if not normalized_session_id:
            raise HTTPException(status_code=400, detail="session_ids cannot include blank values")
        if normalized_session_id in seen_session_ids:
            continue
        seen_session_ids.add(normalized_session_id)
        normalized_session_ids.append(normalized_session_id)

    deleted_session_ids: List[str] = []
    try:
        for target_session_id in normalized_session_ids:
            if repository.soft_delete_session(
                session_id=target_session_id,
                user_auth_sub=user_id,
                chat_kind=ASSISTANT_CHAT_KIND,
            ):
                deleted_session_ids.append(target_session_id)
        db.commit()
    except Exception as exc:
        logger.error(
            "Failed to bulk delete chat sessions",
            extra={"user_id": user_id, "requested_count": len(normalized_session_ids)},
            exc_info=True,
        )
        _rollback_and_raise(
            db,
            status_code=500,
            detail="Failed to delete chat sessions",
            exc=exc,
        )

    return BulkDeleteSessionsResponse(
        requested_count=len(normalized_session_ids),
        deleted_count=len(deleted_session_ids),
        deleted_session_ids=deleted_session_ids,
    )
