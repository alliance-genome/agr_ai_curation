"""Chat response, streaming, cancellation, and assistant rescue endpoints."""

from .chat_common import *


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    chat_message: ChatMessage,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = None,
):
    """Process a chat message and return a response (non-streaming)."""
    session_id = chat_message.session_id or str(uuid.uuid4())
    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)

    # Set context variables for file output tools
    set_current_session_id(session_id)
    set_current_user_id(user_id)

    # Get active document (optional)
    active_doc = document_state.get_document(user_id)
    document_id = active_doc.get("id") if active_doc else None
    document_name = active_doc.get("filename") if active_doc else None

    # Extract active groups from user's Cognito groups for prompt injection
    # Note: Cognito uses "cognito:groups" as the claim key
    cognito_groups = user.get("cognito:groups", [])
    active_groups = get_groups_from_cognito(cognito_groups)
    effective_user_message = chat_message.message
    turn_claim_key: Optional[str] = None
    turn_claim_token: Optional[str] = None
    turn_claim_acquired = False

    async def _release_non_stream_turn_claim() -> None:
        nonlocal turn_claim_acquired

        if not turn_claim_acquired or turn_claim_key is None or turn_claim_token is None:
            return

        turn_claim_acquired = False
        if _LOCAL_NON_STREAM_TURN_OWNERS.get(turn_claim_key) == turn_claim_token:
            _LOCAL_NON_STREAM_TURN_OWNERS.pop(turn_claim_key, None)
        await unregister_active_stream(turn_claim_key, user_id=turn_claim_token)

    if active_groups:
        logger.info(
            "User has active groups: %s (from Cognito groups: %s)",
            active_groups,
            cognito_groups,
            extra={"session_id": session_id, "user_id": user_id},
        )

    try:
        if chat_message.turn_id:
            turn_claim_key = f"non-stream-turn:{session_id}:{chat_message.turn_id}"
            # Use a per-request claim token so same-turn retries stay exclusive across workers.
            turn_claim_token = uuid.uuid4().hex

            if turn_claim_key in _LOCAL_NON_STREAM_TURN_OWNERS:
                raise HTTPException(status_code=409, detail="Chat turn is already in progress")

            _LOCAL_NON_STREAM_TURN_OWNERS[turn_claim_key] = turn_claim_token
            if not await register_active_stream(turn_claim_key, user_id=turn_claim_token):
                _LOCAL_NON_STREAM_TURN_OWNERS.pop(turn_claim_key, None)
                turn_claim_key = None
                turn_claim_token = None
                raise HTTPException(status_code=409, detail="Chat turn is already in progress")

            turn_claim_acquired = True

        active_document_id, _ = _resolve_session_create_active_document(
            repository=repository,
            user_id=user_id,
        )
        repository.get_or_create_session(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
            active_document_id=active_document_id,
        )
        user_turn = repository.append_message(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
            role="user",
            content=chat_message.message,
            turn_id=chat_message.turn_id,
        )
        db.commit()
    except HTTPException:
        await _release_non_stream_turn_claim()
        raise
    except ValueError as exc:
        await _release_non_stream_turn_claim()
        _rollback_and_raise(
            db,
            status_code=400,
            detail="Invalid chat request",
            exc=exc,
            log_message=f"Failed to persist durable non-stream user turn for session {session_id}",
            level=logging.WARNING,
        )
    except Exception as exc:
        await _release_non_stream_turn_claim()
        logger.error(
            "Failed to persist durable non-stream user turn for session %s",
            session_id,
            extra={"session_id": session_id, "user_id": user_id, "turn_id": chat_message.turn_id},
            exc_info=True,
        )
        _rollback_and_raise(
            db,
            status_code=500,
            detail="Failed to persist chat request",
            exc=exc,
        )

    if chat_message.turn_id and not user_turn.created:
        effective_user_message = user_turn.message.content
        try:
            assistant_turn = repository.get_message_by_turn_id(
                session_id=session_id,
                user_auth_sub=user_id,
                turn_id=chat_message.turn_id,
                role="assistant",
            )
        except ValueError as exc:
            await _release_non_stream_turn_claim()
            raise_sanitized_http_exception(
                logger,
                status_code=400,
                detail="Invalid chat replay request",
                log_message=f"Failed to load durable replay state for session {session_id}",
                exc=exc,
                level=logging.WARNING,
            )

        if assistant_turn is not None:
            _queue_chat_title_backfill(
                background_tasks,
                session_id=session_id,
                user_id=user_id,
                preferred_generated_title=_generate_title_from_turn(
                    user_message=effective_user_message,
                    assistant_message=assistant_turn.content,
                ),
            )
            logger.info(
                "Returning durable replay for non-stream chat turn %s",
                chat_message.turn_id,
                extra={"session_id": session_id, "user_id": user_id, "turn_id": chat_message.turn_id},
            )
            await _release_non_stream_turn_claim()
            return ChatResponse(response=assistant_turn.content, session_id=session_id)

        logger.info(
            "Retrying incomplete non-stream chat turn %s after prior request ended",
            chat_message.turn_id,
            extra={"session_id": session_id, "user_id": user_id, "turn_id": chat_message.turn_id},
        )
        if effective_user_message != chat_message.message:
            logger.info(
                "Reusing stored user content for retried non-stream turn %s",
                chat_message.turn_id,
                extra={"session_id": session_id, "user_id": user_id, "turn_id": chat_message.turn_id},
            )

    try:
        tool_agent_map = get_supervisor_tool_agent_map()
    except Exception as exc:
        await _release_non_stream_turn_claim()
        logger.error(
            "Supervisor tool-map resolution failed; aborting chat run to prevent silent extraction data loss",
            extra={"session_id": session_id, "user_id": user_id},
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Internal configuration error: unable to process chat request",
        ) from exc

    try:
        context_messages = _build_context_messages_from_durable_messages(
            repository,
            user_id=user_id,
            session_id=session_id,
            user_message=effective_user_message,
        )
        if context_messages:
            logger.info(
                "Including %s durable context messages for session %s",
                len(context_messages),
                session_id,
                extra={"session_id": session_id, "user_id": user_id},
            )

        # Collect full response from streaming generator
        full_response = ""
        error_message = None
        trace_id = None
        run_finished = False
        extraction_candidates: List[ExtractionEnvelopeCandidate] = []

        async for event in run_agent_streamed(
            context_messages=context_messages,
            user_id=user_id,
            session_id=session_id,
            document_id=document_id,
            document_name=document_name,
            active_groups=active_groups,
            supervisor_model=chat_message.model,
            specialist_model=chat_message.specialist_model,
            supervisor_temperature=chat_message.supervisor_temperature,
            specialist_temperature=chat_message.specialist_temperature,
            supervisor_reasoning=chat_message.supervisor_reasoning,
            specialist_reasoning=chat_message.specialist_reasoning,
        ):
            event_type = event.get("type")
            event_data = event.get("data", {}) or {}

            if event_type == "RUN_STARTED" and "trace_id" in event_data:
                trace_id = event_data.get("trace_id")

            candidate = _build_extraction_candidate_from_tool_event(
                event,
                tool_agent_map=tool_agent_map,
                conversation_summary=effective_user_message,
                metadata={"document_name": document_name} if document_name else None,
            )
            if candidate:
                extraction_candidates.append(candidate)

            if event_type == "RUN_FINISHED":
                full_response = event_data.get("response", "")
                run_finished = True
                continue
            elif event_type == "RUN_ERROR":
                # Capture error and stop processing
                error_message = event_data.get("message", "Unknown error")
                logger.error(
                    "Agent error during non-streaming chat: %s",
                    error_message,
                    extra={"session_id": session_id, "user_id": user_id},
                )
                break

        # If we got an error, raise it
        if error_message:
            raise HTTPException(status_code=500, detail="Failed to process chat request")

        if run_finished:
            try:
                _persist_extraction_candidates(
                    candidates=extraction_candidates,
                    document_id=document_id,
                    user_id=user_id,
                    session_id=session_id,
                    trace_id=trace_id,
                    source_kind=CurationExtractionSourceKind.CHAT,
                    db=db,
                )
                assistant_turn = repository.append_message(
                    session_id=session_id,
                    user_auth_sub=user_id,
                    chat_kind=ASSISTANT_CHAT_KIND,
                    role="assistant",
                    content=full_response,
                    turn_id=chat_message.turn_id,
                    trace_id=trace_id,
                )
                if chat_message.turn_id and not assistant_turn.created:
                    db.rollback()
                    logger.info(
                        "Discarding duplicate non-stream completion for replayed turn %s",
                        chat_message.turn_id,
                        extra={"session_id": session_id, "user_id": user_id, "turn_id": chat_message.turn_id},
                    )
                    _queue_chat_title_backfill(
                        background_tasks,
                        session_id=session_id,
                        user_id=user_id,
                        preferred_generated_title=_generate_title_from_turn(
                            user_message=effective_user_message,
                            assistant_message=assistant_turn.message.content,
                        ),
                    )
                    return ChatResponse(response=assistant_turn.message.content, session_id=session_id)
                db.commit()
                _queue_chat_title_backfill(
                    background_tasks,
                    session_id=session_id,
                    user_id=user_id,
                    preferred_generated_title=_generate_title_from_turn(
                        user_message=effective_user_message,
                        assistant_message=assistant_turn.message.content,
                    ),
                )
            except ValueError as exc:
                _rollback_and_raise(
                    db,
                    status_code=400,
                    detail="Invalid chat response state",
                    exc=exc,
                    log_message=f"Failed to persist durable non-stream assistant turn for session {session_id}",
                    level=logging.WARNING,
                )
            except Exception as exc:
                logger.error(
                    "Failed to persist durable non-stream assistant turn for session %s",
                    session_id,
                    extra={"session_id": session_id, "user_id": user_id, "turn_id": chat_message.turn_id},
                    exc_info=True,
                )
                _rollback_and_raise(
                    db,
                    status_code=500,
                    detail="Failed to persist chat response",
                    exc=exc,
                )
        else:
            raise HTTPException(status_code=500, detail="Chat run did not complete")

        return ChatResponse(response=full_response, session_id=session_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Chat error: %s",
            e,
            extra={"session_id": session_id, "user_id": user_id},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to process chat request") from e
    finally:
        await _release_non_stream_turn_claim()


@router.post("/chat/stream")
async def chat_stream_endpoint(
    chat_message: ChatMessage,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
):
    """Stream a chat response using Server-Sent Events."""
    session_id = chat_message.session_id or str(uuid.uuid4())
    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)

    # Set context variables for file output tools
    set_current_session_id(session_id)
    set_current_user_id(user_id)

    # Get active document (optional)
    active_doc = document_state.get_document(user_id)
    document_id = active_doc.get("id") if active_doc else None
    document_name = active_doc.get("filename") if active_doc else None

    doc_info = f"{document_id[:8]}..." if document_id else "none"
    logger.info(
        "Chat stream request received",
        extra={"session_id": session_id, "user_id": user_id, "document_id": doc_info},
    )

    # Extract active groups from user's Cognito groups for prompt injection
    # Note: Cognito uses "cognito:groups" as the claim key
    cognito_groups = user.get("cognito:groups", [])
    active_groups = get_groups_from_cognito(cognito_groups)
    if active_groups:
        logger.info(
            "User has active groups: %s (from Cognito groups: %s)",
            active_groups,
            cognito_groups,
            extra={"session_id": session_id, "user_id": user_id},
        )

    try:
        tool_agent_map = get_supervisor_tool_agent_map()
    except Exception as exc:
        logger.error(
            "Supervisor tool-map resolution failed; aborting chat stream to prevent silent extraction data loss",
            extra={"session_id": session_id, "user_id": user_id},
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Internal configuration error: unable to process chat request",
        ) from exc

    stream_lifecycle = await _claim_active_stream_lifecycle(session_id=session_id, user_id=user_id)
    cancel_event = stream_lifecycle.cancel_event
    generated_title_candidate: str | None = None

    try:
        active_document_id, _ = _resolve_session_create_active_document(
            repository=repository,
            user_id=user_id,
        )
        prepared_turn = _prepare_chat_stream_turn(
            repository=repository,
            db=db,
            session_id=session_id,
            user_id=user_id,
            user_message=chat_message.message,
            requested_turn_id=chat_message.turn_id,
            active_document_id=active_document_id,
        )
        generated_title_candidate = _generate_title_from_turn(
            user_message=prepared_turn.effective_user_message,
        )
    except HTTPException:
        await stream_lifecycle.cleanup(session_id)
        raise
    except ValueError as exc:
        await stream_lifecycle.cleanup(session_id)
        _rollback_and_raise(
            db,
            status_code=400,
            detail="Invalid chat request",
            exc=exc,
            log_message=f"Failed to prepare durable stream user turn for session {session_id}",
            level=logging.WARNING,
        )
    except Exception as exc:
        await stream_lifecycle.cleanup(session_id)
        logger.error(
            "Failed to persist durable stream user turn for session %s",
            session_id,
            extra={"session_id": session_id, "user_id": user_id, "turn_id": chat_message.turn_id},
            exc_info=True,
        )
        _rollback_and_raise(
            db,
            status_code=500,
            detail="Failed to persist chat request",
            exc=exc,
        )

    if prepared_turn.context_messages:
        logger.info(
            "Including %s durable context messages for session %s",
            len(prepared_turn.context_messages),
            session_id,
            extra={"session_id": session_id, "user_id": user_id, "turn_id": prepared_turn.turn_id},
        )

    if prepared_turn.replay_assistant_turn is not None:
        generated_title_candidate = _generate_title_from_turn(
            user_message=prepared_turn.effective_user_message,
            assistant_message=prepared_turn.replay_assistant_turn.content,
        )

        async def replay_stream():
            try:
                yield _stream_event_sse(
                    _stream_event_payload(
                        "TEXT_MESSAGE_CONTENT",
                        session_id=session_id,
                        turn_id=prepared_turn.turn_id,
                        trace_id=prepared_turn.replay_assistant_turn.trace_id,
                        content=prepared_turn.replay_assistant_turn.content,
                    )
                )
                yield _stream_event_sse(
                    _build_terminal_turn_event(
                        "turn_completed",
                        session_id=session_id,
                        turn_id=prepared_turn.turn_id,
                        trace_id=prepared_turn.replay_assistant_turn.trace_id,
                        message="Chat turn completed.",
                    )
                )
            finally:
                await stream_lifecycle.cleanup(session_id)

        return StreamingResponse(
            replay_stream(),
            media_type="text/event-stream",
            background=stream_lifecycle.background_task(lambda: generated_title_candidate),
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def generate_stream():
        """Generate SSE events from the agent runner."""
        nonlocal generated_title_candidate
        current_session_id = session_id
        current_turn_id = prepared_turn.turn_id
        full_response = ""
        trace_id = None
        run_finished = False
        runner_error_message: Optional[str] = None
        runner_error_type: Optional[str] = None
        interrupted_message: Optional[str] = None
        extraction_candidates: List[ExtractionEnvelopeCandidate] = []
        evidence_records: List[Dict[str, Any]] = []
        evidence_summary_event_received = False

        try:
            async for event in run_agent_streamed(
                context_messages=prepared_turn.context_messages,
                user_id=user_id,
                session_id=current_session_id,
                document_id=document_id,
                document_name=document_name,
                active_groups=active_groups,
                supervisor_model=chat_message.model,
                specialist_model=chat_message.specialist_model,
                supervisor_temperature=chat_message.supervisor_temperature,
                specialist_temperature=chat_message.specialist_temperature,
                supervisor_reasoning=chat_message.supervisor_reasoning,
                specialist_reasoning=chat_message.specialist_reasoning,
            ):
                if cancel_event.is_set() or await check_cancel_signal(current_session_id):
                    interrupted_message = "Run cancelled by user"
                    logger.info(
                        "Chat stream cancelled for session %s",
                        current_session_id,
                        extra={
                            "session_id": current_session_id,
                            "user_id": user_id,
                            "trace_id": trace_id,
                            "turn_id": current_turn_id,
                        },
                    )
                    break

                event_type = event.get("type")
                event_data = event.get("data", {}) or {}

                if "trace_id" in event_data:
                    trace_id = event_data.get("trace_id")

                flat_event = _stream_event_payload(
                    str(event_type),
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                )
                flat_event.update(event_data)
                flat_event["session_id"] = current_session_id
                flat_event["turn_id"] = current_turn_id

                if "timestamp" in event:
                    flat_event["timestamp"] = event["timestamp"]
                if "details" in event:
                    flat_event["details"] = event["details"]

                if event_type == "CHUNK_PROVENANCE":
                    for key in ["chunk_id", "doc_items", "message_id", "source_tool"]:
                        if key in event and key not in flat_event:
                            flat_event[key] = event[key]

                if event_type == "evidence_summary":
                    event_evidence_records = _extract_evidence_records(event.get("evidence_records"))
                    if not event_evidence_records:
                        event_evidence_records = _extract_evidence_records(
                            (event.get("details") or {}).get("evidence_records", [])
                        )
                    evidence_curation_metadata = _build_evidence_curation_metadata(
                        event=event,
                        tool_agent_map=tool_agent_map,
                    )
                    if event_evidence_records:
                        evidence_records = event_evidence_records
                        evidence_summary_event_received = True
                    if "evidence_records" not in flat_event:
                        if event_evidence_records:
                            flat_event["evidence_records"] = event_evidence_records
                        elif "evidence_records" in event:
                            flat_event["evidence_records"] = event["evidence_records"]
                        elif "evidence_records" in (event.get("details") or {}):
                            flat_event["evidence_records"] = event["details"]["evidence_records"]
                    for key, value in evidence_curation_metadata.items():
                        flat_event[key] = value
                    yield _stream_event_sse(flat_event)
                    continue

                candidate = _build_extraction_candidate_from_tool_event(
                    event,
                    tool_agent_map=tool_agent_map,
                    conversation_summary=prepared_turn.effective_user_message,
                    metadata={"document_name": document_name} if document_name else None,
                )
                if candidate:
                    extraction_candidates.append(candidate)

                if not evidence_summary_event_received:
                    evidence_record = _build_evidence_record_from_tool_event(event)
                    if evidence_record:
                        evidence_records.append(evidence_record)

                if event_type == "RUN_FINISHED":
                    full_response = event_data.get("response", "")
                    run_finished = True
                    continue

                if event_type == "RUN_ERROR":
                    runner_error_message = event_data.get("message")
                    runner_error_type = event_data.get("error_type")
                    if not runner_error_message:
                        logger.error(
                            "Agent sent RUN_ERROR without message field",
                            extra={"session_id": current_session_id, "turn_id": current_turn_id},
                        )
                        runner_error_message = "Agent error (no details provided)"
                    if not runner_error_type:
                        logger.error(
                            "Agent sent RUN_ERROR without error_type field",
                            extra={"session_id": current_session_id, "turn_id": current_turn_id},
                        )
                    logger.error(
                        "Agent error during streaming chat: %s",
                        runner_error_message,
                        extra={
                            "session_id": current_session_id,
                            "user_id": user_id,
                            "trace_id": trace_id,
                            "turn_id": current_turn_id,
                        },
                    )
                    runner_error_message = (
                        "An error occurred. Please provide feedback using the ⋮ menu on this message, "
                        "then try your query again."
                    )
                    break

                yield _stream_event_sse(flat_event)

            if interrupted_message:
                yield _stream_event_sse(
                    _build_terminal_turn_event(
                        "turn_interrupted",
                        session_id=current_session_id,
                        turn_id=current_turn_id,
                        trace_id=trace_id,
                        message=interrupted_message,
                    )
                )
                return

            if runner_error_message:
                yield _stream_event_sse(
                    _build_terminal_turn_event(
                        "turn_failed",
                        session_id=current_session_id,
                        turn_id=current_turn_id,
                        trace_id=trace_id,
                        message=runner_error_message,
                        error_type=runner_error_type,
                    )
                )
                return

            if run_finished:
                if evidence_records and not evidence_summary_event_received:
                    evidence_curation_metadata = _build_candidate_evidence_curation_metadata(
                        extraction_candidates,
                    )
                    yield _stream_event_sse(
                        _stream_event_payload(
                            "evidence_summary",
                            session_id=current_session_id,
                            turn_id=current_turn_id,
                            trace_id=trace_id,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            evidence_records=evidence_records,
                            **evidence_curation_metadata,
                        )
                    )

                try:
                    assistant_turn = _persist_completed_chat_stream_turn(
                        session_id=current_session_id,
                        user_id=user_id,
                        turn_id=current_turn_id,
                        user_message=prepared_turn.effective_user_message,
                        assistant_message=full_response,
                        trace_id=trace_id,
                        extraction_candidates=extraction_candidates,
                        document_id=document_id,
                    )
                except ChatHistorySessionNotFoundError:
                    yield _stream_event_sse(
                        _build_terminal_turn_event(
                            "session_gone",
                            session_id=current_session_id,
                            turn_id=current_turn_id,
                            trace_id=trace_id,
                            message="Chat session is no longer available.",
                        )
                    )
                    return
                except ChatStreamAssistantSaveFailedError as exc:
                    root_exc = exc.__cause__ or exc
                    logger.error(
                        "Failed to persist durable stream assistant turn for session %s",
                        current_session_id,
                        extra={
                            "session_id": current_session_id,
                            "user_id": user_id,
                            "trace_id": trace_id,
                            "turn_id": current_turn_id,
                        },
                        exc_info=True,
                    )
                    yield _stream_event_sse(
                        _stream_event_payload(
                            "SUPERVISOR_ERROR",
                            session_id=current_session_id,
                            turn_id=current_turn_id,
                            trace_id=trace_id,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            details=_stream_error_details(
                                error="Failed to save the assistant response.",
                                exc=root_exc,
                                message="The chat response completed, but saving the durable assistant turn failed.",
                            ),
                        )
                    )
                    yield _stream_event_sse(
                        _build_terminal_turn_event(
                            "turn_save_failed",
                            session_id=current_session_id,
                            turn_id=current_turn_id,
                            trace_id=trace_id,
                            message="Chat completed, but the assistant response could not be saved.",
                            error_type=type(root_exc).__name__,
                        )
                    )
                    return
                except Exception as exc:
                    logger.error(
                        "Failed to persist durable stream completion side effects for session %s",
                        current_session_id,
                        extra={
                            "session_id": current_session_id,
                            "user_id": user_id,
                            "trace_id": trace_id,
                            "turn_id": current_turn_id,
                        },
                        exc_info=True,
                    )
                    yield _stream_event_sse(
                        _stream_event_payload(
                            "SUPERVISOR_ERROR",
                            session_id=current_session_id,
                            turn_id=current_turn_id,
                            trace_id=trace_id,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            details=_stream_error_details(
                                error="Failed to save chat side effects.",
                                exc=exc,
                                message=(
                                    "The chat response completed, but saving durable stream side effects failed."
                                ),
                            ),
                        )
                    )
                    yield _stream_event_sse(
                        _build_terminal_turn_event(
                            "turn_failed",
                            session_id=current_session_id,
                            turn_id=current_turn_id,
                            trace_id=trace_id,
                            message="Chat completed, but durable side effects could not be saved.",
                            error_type=type(exc).__name__,
                        )
                    )
                    return

                generated_title_candidate = _generate_title_from_turn(
                    user_message=prepared_turn.effective_user_message,
                    assistant_message=assistant_turn.content,
                )
                yield _stream_event_sse(
                    _build_terminal_turn_event(
                        "turn_completed",
                        session_id=current_session_id,
                        turn_id=current_turn_id,
                        trace_id=assistant_turn.trace_id or trace_id,
                        message="Chat turn completed.",
                    )
                )
                return

            yield _stream_event_sse(
                _build_terminal_turn_event(
                    "turn_failed",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                    message="Chat run did not complete.",
                    error_type="IncompleteRun",
                )
            )
        except asyncio.CancelledError:
            logger.warning(
                "Chat stream cancelled unexpectedly for session %s",
                current_session_id,
                extra={
                    "session_id": current_session_id,
                    "user_id": user_id,
                    "trace_id": trace_id,
                    "turn_id": current_turn_id,
                },
            )
            yield _stream_event_sse(
                _stream_event_payload(
                    "SUPERVISOR_ERROR",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    details={
                        "error": "Stream cancelled unexpectedly",
                        "context": "asyncio.CancelledError",
                        "message": (
                            "The request was interrupted. Please provide feedback using the ⋮ menu, then try your query again."
                        ),
                    },
                )
            )
            yield _stream_event_sse(
                _build_terminal_turn_event(
                    "turn_interrupted",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                    message=(
                        "The request was interrupted unexpectedly. Please provide feedback using the ⋮ menu on this message, then try your query again."
                    ),
                    error_type="StreamCancelled",
                )
            )
        except Exception as exc:
            logger.error(
                "Stream error: %s",
                exc,
                extra={
                    "session_id": current_session_id,
                    "user_id": user_id,
                    "trace_id": trace_id,
                    "turn_id": current_turn_id,
                },
                exc_info=True,
            )
            yield _stream_event_sse(
                _stream_event_payload(
                    "SUPERVISOR_ERROR",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    details=_stream_error_details(
                        error="Chat stream failed unexpectedly.",
                        exc=exc,
                        message="An error occurred. Please provide feedback using the ⋮ menu, then try your query again.",
                    ),
                )
            )
            yield _stream_event_sse(
                _build_terminal_turn_event(
                    "turn_failed",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                    message=(
                        "An error occurred. Please provide feedback using the ⋮ menu on this message, then try your query again."
                    ),
                    error_type=type(exc).__name__,
                )
            )
        finally:
            await stream_lifecycle.cleanup(current_session_id)

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        background=stream_lifecycle.background_task(lambda: generated_title_candidate),
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.post("/chat/stop")
async def stop_chat(request: StopRequest, user: Dict[str, Any] = get_auth_dependency()):
    """Best-effort cancel of a running chat stream for the given session.

    Note: Stop is cooperative - it signals the stream to stop at the next event,
    but cannot interrupt long-running tool calls mid-execution.
    """
    session_id = request.session_id
    requester_id = user.get("sub")
    if not requester_id:
        raise HTTPException(status_code=401, detail="User identifier not found in token")

    owner_id = _LOCAL_SESSION_OWNERS.get(session_id)
    if owner_id is None:
        owner_id = await get_stream_owner(session_id)
    if owner_id and owner_id != requester_id:
        raise HTTPException(status_code=403, detail="You do not have permission to cancel this session")

    # Check if stream is active (either locally or in Redis)
    local_event = _LOCAL_CANCEL_EVENTS.get(session_id)
    stream_active = await is_stream_active(session_id)

    if stream_active and owner_id is None:
        raise HTTPException(status_code=403, detail="Unable to verify stream ownership for cancellation")

    if not local_event and not stream_active:
        return {"status": "ok", "message": "No running chat for this session."}

    # Signal cancellation via Redis (cross-worker) and local event (same-worker)
    await set_cancel_signal(session_id)
    if local_event:
        local_event.set()

    return {"status": "ok", "message": "Cancellation requested (cooperative - may take a moment)."}


@router.post(
    "/chat/{session_id}/assistant-rescue",
    response_model=AssistantRescueResponse,
    responses={
        409: {
            "description": (
                "The referenced user turn is missing, or the retry payload conflicts "
                "with an existing assistant turn for the same turn_id."
            )
        }
    },
)
async def assistant_rescue(
    session_id: str,
    request: AssistantRescueRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
    background_tasks: BackgroundTasks = None,
):
    """Backfill one durable assistant turn; retries must reuse the stored payload."""

    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)

    try:
        session = repository.get_session(
            session_id=session_id,
            user_auth_sub=user_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")

        user_turn = repository.get_message_by_turn_id(
            session_id=session_id,
            user_auth_sub=user_id,
            turn_id=request.turn_id,
            role="user",
        )
        if user_turn is None:
            raise HTTPException(status_code=409, detail="Chat user turn not found")

        assistant_turn = repository.append_message(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
            role="assistant",
            content=request.content,
            turn_id=request.turn_id,
            trace_id=request.trace_id,
        )
        if not assistant_turn.created:
            conflicting_fields = _assistant_rescue_conflicting_fields(
                existing_turn=assistant_turn.message,
                content=request.content,
                trace_id=request.trace_id,
            )
            if conflicting_fields:
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Assistant rescue payload conflicts with existing assistant turn "
                        f"for fields: {', '.join(conflicting_fields)}"
                    ),
                )
        db.commit()
        _queue_chat_title_backfill(
            background_tasks,
            session_id=session_id,
            user_id=user_id,
            preferred_generated_title=_generate_title_from_turn(
                user_message=user_turn.content,
                assistant_message=assistant_turn.message.content,
            ),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        _rollback_and_raise(
            db,
            status_code=400,
            detail="Invalid assistant rescue request",
            exc=exc,
            log_message=f"Failed to rescue assistant turn for session {session_id}",
            level=logging.WARNING,
        )
    except ChatHistorySessionNotFoundError as exc:
        _rollback_and_raise(db, status_code=404, detail="Chat session not found", exc=exc)
    except Exception as exc:
        logger.error(
            "Failed to rescue assistant turn for session %s",
            session_id,
            extra={"session_id": session_id, "user_id": user_id, "turn_id": request.turn_id},
            exc_info=True,
        )
        _rollback_and_raise(
            db,
            status_code=500,
            detail="Failed to rescue assistant turn",
            exc=exc,
        )

    return AssistantRescueResponse(
        session_id=session_id,
        turn_id=request.turn_id,
        created=assistant_turn.created,
        trace_id=assistant_turn.message.trace_id,
    )
