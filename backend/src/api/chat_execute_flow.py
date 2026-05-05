# ruff: noqa: F403,F405
"""Execute-flow chat streaming endpoint."""

from .chat_common import *


def _extract_execute_flow_runtime_identifiers(
    payload_json: Dict[str, Any] | List[Any] | None,
) -> tuple[str | None, str | None]:
    """Read persisted execute-flow runtime identifiers from a durable user row."""

    if not isinstance(payload_json, dict):
        return None, None

    runtime_state = payload_json.get(_EXECUTE_FLOW_RUNTIME_STATE_KEY)
    if not isinstance(runtime_state, dict):
        return None, None

    flow_run_id = str(
        runtime_state.get(_EXECUTE_FLOW_RUNTIME_FLOW_RUN_ID_KEY) or ""
    ).strip() or None
    trace_id = str(
        runtime_state.get(_EXECUTE_FLOW_RUNTIME_TRACE_ID_KEY) or ""
    ).strip() or None
    return flow_run_id, trace_id


def _build_execute_flow_runtime_payload(
    payload_json: Dict[str, Any] | List[Any] | None,
    *,
    flow_run_id: str,
    trace_id: Optional[str],
) -> Dict[str, Any]:
    """Merge execute-flow runtime identifiers into a durable user-turn payload."""

    next_payload = dict(payload_json) if isinstance(payload_json, dict) else {}
    runtime_state: Dict[str, Any] = {
        _EXECUTE_FLOW_RUNTIME_FLOW_RUN_ID_KEY: flow_run_id,
    }
    normalized_trace_id = str(trace_id or "").strip() or None
    if normalized_trace_id is not None:
        runtime_state[_EXECUTE_FLOW_RUNTIME_TRACE_ID_KEY] = normalized_trace_id
    next_payload[_EXECUTE_FLOW_RUNTIME_STATE_KEY] = runtime_state
    return next_payload


def _truncate_text(value: Any, max_chars: int) -> str:
    """Convert to string and truncate with deterministic suffix when needed."""
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    overflow = len(text) - max_chars
    return f"{text[:max_chars]}... [truncated {overflow} chars]"


def _dedupe_preserve_order(values: List[str]) -> List[str]:
    """Return unique strings while preserving insertion order."""
    seen = set()
    ordered: List[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _serialize_hidden_flow_payload(payload: Dict[str, Any], max_chars: int) -> str:
    """Serialize hidden payload and compact it as needed while preserving valid JSON."""
    serialized = json.dumps(payload, default=str, ensure_ascii=True)
    if len(serialized) <= max_chars:
        return serialized

    compact_payload = dict(payload)
    compact_payload["truncated"] = True
    compact_payload["truncation_notice"] = "Hidden flow context compacted to fit memory budget."

    # Drop lower-priority collections first.
    for key in ("intermediate_specialist_summaries", "domain_warnings", "files"):
        if compact_payload.get(key):
            compact_payload[key] = []
            serialized = json.dumps(compact_payload, default=str, ensure_ascii=True)
            if len(serialized) <= max_chars:
                return serialized

    # Keep at most one specialist output and tighten output text.
    specialist_outputs = list(compact_payload.get("specialist_outputs") or [])
    if specialist_outputs:
        first_output = dict(specialist_outputs[0])
        first_output["output"] = _truncate_text(
            first_output.get("output"),
            _FLOW_MEMORY_COMPACT_SPECIALIST_OUTPUT_CHARS,
        )
        compact_payload["specialist_outputs"] = [first_output]
        serialized = json.dumps(compact_payload, default=str, ensure_ascii=True)
        if len(serialized) <= max_chars:
            return serialized

    flow_payload = compact_payload.get("flow") or {}
    minimal_payload = {
        "flow": {
            "flow_id": _truncate_text(flow_payload.get("flow_id"), 128),
            "flow_name": _truncate_text(flow_payload.get("flow_name"), 128),
            "session_id": _truncate_text(flow_payload.get("session_id"), 128),
            "status": _truncate_text(flow_payload.get("status"), 64),
            "trace_id": _truncate_text(flow_payload.get("trace_id"), 128),
            "failure_reason": _truncate_text(flow_payload.get("failure_reason"), 512),
        },
        "truncated": True,
        "truncation_notice": "Hidden flow context exceeded size limit and was reduced.",
    }
    serialized = json.dumps(minimal_payload, default=str, ensure_ascii=True)
    if len(serialized) <= max_chars:
        return serialized

    return json.dumps({"truncated": True}, ensure_ascii=True)


def _build_flow_memory_assistant_message(
    *,
    flow_name: str,
    flow_id: str,
    session_id: str,
    status: str,
    trace_id: Optional[str],
    final_user_output: Optional[str],
    agents_used: List[str],
    specialist_outputs: List[Dict[str, Any]],
    specialist_summaries: List[Dict[str, Any]],
    domain_warnings: List[Dict[str, Any]],
    file_outputs: List[Dict[str, Any]],
    failure_reason: Optional[str],
) -> str:
    """Build a flow execution context message for follow-up chat grounding."""
    agents = _dedupe_preserve_order([str(agent) for agent in agents_used if agent])
    visible_output = _truncate_text(final_user_output or "", _FLOW_MEMORY_MAX_VISIBLE_OUTPUT_CHARS)

    bounded_outputs: List[Dict[str, Any]] = []
    for output in specialist_outputs[:_FLOW_MEMORY_MAX_SPECIALIST_OUTPUTS]:
        bounded_outputs.append({
            "tool": output.get("tool"),
            "output_length": output.get("output_length"),
            "output": _truncate_text(output.get("output"), _FLOW_MEMORY_MAX_SPECIALIST_OUTPUT_CHARS),
        })

    hidden_payload = {
        "flow": {
            "flow_id": flow_id,
            "flow_name": flow_name,
            "session_id": session_id,
            "status": status,
            "trace_id": trace_id,
            "failure_reason": failure_reason,
        },
        "specialist_outputs": bounded_outputs,
        "intermediate_specialist_summaries": specialist_summaries[:_FLOW_MEMORY_MAX_SPECIALIST_SUMMARIES],
        "domain_warnings": domain_warnings,
        "files": file_outputs,
    }
    hidden_json = _serialize_hidden_flow_payload(hidden_payload, _FLOW_MEMORY_MAX_HIDDEN_JSON_CHARS)

    agents_line = ", ".join(agents) if agents else "Unknown"
    if visible_output:
        final_output_block = visible_output
    elif status == "failed":
        final_output_block = (
            "Flow failed before producing a final output. "
            f"Reason: {_format_flow_failure_reason(failure_reason)}"
        )
    else:
        final_output_block = "No final user-visible output was emitted."

    return (
        "Flow execution summary for follow-up questions:\n"
        f"- Flow: {flow_name} ({flow_id})\n"
        f"- Status: {status}\n"
        f"- Session: {session_id}\n"
        f"- Trace ID: {trace_id or 'n/a'}\n"
        f"- Agents involved: {agents_line}\n"
        "- Final user-visible output:\n"
        f"{final_output_block}\n\n"
        "Hidden flow context (internal grounding data; not user-visible output):\n"
        "<FLOW_INTERNAL_CONTEXT_JSON>\n"
        f"{hidden_json}\n"
        "</FLOW_INTERNAL_CONTEXT_JSON>"
    )


def _format_flow_failure_reason(failure_reason: Optional[str]) -> str:
    """Render a failed flow reason without masking missing or blank values."""

    normalized = failure_reason.strip() if isinstance(failure_reason, str) else None
    if normalized:
        return normalized
    return repr(failure_reason)


def _parse_event_created_at(value: Any) -> datetime | None:
    """Return a datetime when an optional SSE timestamp string parses cleanly."""

    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized:
        return None

    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_execute_flow_summary_content(
    *,
    status: str,
    final_user_output: Optional[str],
    failure_reason: Optional[str],
) -> str:
    """Build the user-visible durable transcript content for one completed flow turn."""

    visible_output = str(final_user_output or "").strip()
    if visible_output:
        return visible_output
    if status == "failed":
        return (
            "Flow failed before producing a final output. "
            f"Reason: {_format_flow_failure_reason(failure_reason)}"
        )
    return "No final user-visible output was emitted."


def _build_execute_flow_transcript_row_from_event(
    event_payload: Dict[str, Any],
) -> "ExecuteFlowTranscriptRow | None":
    """Convert one replayable SSE payload into a durable execute-flow transcript row."""

    event_type = str(event_payload.get("type") or "").strip()
    details = event_payload.get("details", {}) or {}
    trace_id = str(event_payload.get("trace_id") or "").strip() or None
    created_at = _parse_event_created_at(event_payload.get("timestamp"))

    if event_type == "DOMAIN_WARNING":
        warning_message = details.get("message") or event_payload.get("message")
        content = warning_message.strip() if isinstance(warning_message, str) else ""
        if not content:
            content = "Flow warning event missing message payload."
        return ExecuteFlowTranscriptRow(
            content=content,
            message_type="text",
            payload_json=dict(event_payload),
            trace_id=trace_id,
            created_at=created_at,
        )

    if event_type == "FLOW_STEP_EVIDENCE":
        step = event_payload.get("step")
        evidence_count = event_payload.get("evidence_count")
        if isinstance(step, int) and isinstance(evidence_count, int):
            quote_label = "quote" if evidence_count == 1 else "quotes"
            content = f"Flow step {step} captured {evidence_count} evidence {quote_label}."
        else:
            content = "Flow step evidence event missing integer step/evidence_count metadata."
        return ExecuteFlowTranscriptRow(
            content=content,
            message_type="flow_step_evidence",
            payload_json=dict(event_payload),
            trace_id=trace_id,
            created_at=created_at,
        )

    if event_type == "FILE_READY":
        filename_value = details.get("filename") or event_payload.get("filename")
        filename = filename_value.strip() if isinstance(filename_value, str) else ""
        content = f"Generated file: {filename}" if filename else "Generated file event missing filename metadata."
        return ExecuteFlowTranscriptRow(
            content=content,
            message_type="file_download",
            payload_json=dict(event_payload),
            trace_id=trace_id,
            created_at=created_at,
        )

    return None


def _build_execute_flow_summary_row(
    *,
    flow_id: str,
    flow_name: str,
    flow_run_id: Optional[str],
    session_id: str,
    document_id: Optional[str],
    status: str,
    trace_id: Optional[str],
    final_user_output: Optional[str],
    failure_reason: Optional[str],
    assistant_message: str,
    run_started_event: Optional[Dict[str, Any]],
    terminal_events: List[Dict[str, Any]],
) -> "ExecuteFlowTranscriptRow":
    """Build the final durable flow summary row used for replay and follow-up grounding."""

    payload_json: Dict[str, Any] = {
        "flow_id": flow_id,
        "flow_name": flow_name,
        "flow_run_id": flow_run_id,
        "session_id": session_id,
        "document_id": document_id,
        "status": status,
        "trace_id": trace_id,
        "failure_reason": failure_reason,
        "final_user_output": str(final_user_output or "").strip() or None,
        FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY: assistant_message,
        _FLOW_TRANSCRIPT_REPLAY_TERMINAL_EVENTS_KEY: [
            dict(event)
            for event in terminal_events
            if isinstance(event, dict) and isinstance(event.get("type"), str)
        ],
    }
    if isinstance(run_started_event, dict) and isinstance(run_started_event.get("type"), str):
        payload_json[_FLOW_TRANSCRIPT_REPLAY_RUN_STARTED_KEY] = dict(run_started_event)

    created_at = None
    for candidate in [*terminal_events[::-1], run_started_event]:
        if not isinstance(candidate, dict):
            continue
        created_at = _parse_event_created_at(candidate.get("timestamp"))
        if created_at is not None:
            break

    return ExecuteFlowTranscriptRow(
        content=_build_execute_flow_summary_content(
            status=status,
            final_user_output=final_user_output,
            failure_reason=failure_reason,
        ),
        message_type=FLOW_SUMMARY_MESSAGE_TYPE,
        payload_json=payload_json,
        trace_id=trace_id,
        created_at=created_at,
    )


def _build_execute_flow_turn_replay(
    messages: List[ChatMessageRecord],
) -> tuple[List[Dict[str, Any]], str] | None:
    """Return replayable SSE payloads plus assistant flow memory for a completed durable turn."""

    summary_message: ChatMessageRecord | None = None
    assistant_message: str | None = None
    for message in reversed(messages):
        assistant_candidate = extract_flow_assistant_message(message)
        if assistant_candidate is None:
            continue
        summary_message = message
        assistant_message = assistant_candidate
        break

    if summary_message is None or assistant_message is None:
        return None

    replay_events: List[Dict[str, Any]] = []
    summary_payload = summary_message.payload_json if isinstance(summary_message.payload_json, dict) else {}
    run_started_event = summary_payload.get(_FLOW_TRANSCRIPT_REPLAY_RUN_STARTED_KEY)
    if isinstance(run_started_event, dict) and isinstance(run_started_event.get("type"), str):
        replay_events.append(dict(run_started_event))

    for message in messages:
        if message.message_id == summary_message.message_id:
            continue
        if not isinstance(message.payload_json, dict):
            continue
        event_type = message.payload_json.get("type")
        if not isinstance(event_type, str) or not event_type.strip():
            continue
        replay_events.append(dict(message.payload_json))

    terminal_events = summary_payload.get(_FLOW_TRANSCRIPT_REPLAY_TERMINAL_EVENTS_KEY) or []
    if isinstance(terminal_events, list):
        replay_events.extend(
            dict(event)
            for event in terminal_events
            if isinstance(event, dict) and isinstance(event.get("type"), str)
        )

    return replay_events, assistant_message



def _prepare_execute_flow_turn(
    *,
    repository: ChatHistoryRepository,
    db: Session,
    flow: CurationFlow,
    session_id: str,
    user_id: str,
    user_message: str,
    requested_turn_id: Optional[str],
    active_document_id: UUID | None,
) -> PreparedExecuteFlowTurn:
    """Persist the durable execute-flow user turn and detect completed replays."""

    turn_id = requested_turn_id or uuid.uuid4().hex
    flow_run_id = str(uuid.uuid4())
    effective_user_message = user_message

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
        content=user_message,
        turn_id=turn_id,
        payload_json=_build_execute_flow_runtime_payload(
            None,
            flow_run_id=flow_run_id,
            trace_id=None,
        ),
    )

    if user_turn.created:
        flow.execution_count += 1
        flow.last_executed_at = datetime.now(timezone.utc)
        db.commit()
        return PreparedExecuteFlowTurn(
            turn_id=turn_id,
            flow_run_id=flow_run_id,
            effective_user_message=effective_user_message,
            replay_events=[],
        )

    effective_user_message = user_turn.message.content
    stored_flow_run_id, stored_trace_id = _extract_execute_flow_runtime_identifiers(
        user_turn.message.payload_json,
    )
    if stored_flow_run_id is None:
        stored_flow_run_id = flow_run_id
        repository.update_message_by_turn_id(
            session_id=session_id,
            user_auth_sub=user_id,
            turn_id=turn_id,
            role="user",
            payload_json=_build_execute_flow_runtime_payload(
                user_turn.message.payload_json,
                flow_run_id=stored_flow_run_id,
                trace_id=stored_trace_id,
            ),
            trace_id=stored_trace_id,
        )
        db.commit()

    replay = _build_execute_flow_turn_replay(
        repository.list_messages_for_turn(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
            turn_id=turn_id,
        )
    )
    if replay is not None:
        replay_events, replay_assistant_message = replay
        logger.info(
            "Returning durable replay for execute-flow turn %s",
            turn_id,
            extra={"session_id": session_id, "user_id": user_id, "turn_id": turn_id},
        )
        return PreparedExecuteFlowTurn(
            turn_id=turn_id,
            flow_run_id=stored_flow_run_id,
            effective_user_message=effective_user_message,
            replay_events=replay_events,
            replay_assistant_message=replay_assistant_message,
            resume_trace_id=stored_trace_id,
        )

    logger.info(
        "Retrying incomplete execute-flow turn %s after prior request ended",
        turn_id,
        extra={"session_id": session_id, "user_id": user_id, "turn_id": turn_id},
    )
    if effective_user_message != user_message:
        logger.info(
            "Reusing stored user content for retried execute-flow turn %s",
            turn_id,
            extra={"session_id": session_id, "user_id": user_id, "turn_id": turn_id},
        )
    if stored_trace_id:
        logger.info(
            "Reusing persisted trace context for retried execute-flow turn %s",
            turn_id,
            extra={
                "session_id": session_id,
                "user_id": user_id,
                "turn_id": turn_id,
                "trace_id": stored_trace_id,
                "flow_run_id": stored_flow_run_id,
            },
        )

    return PreparedExecuteFlowTurn(
        turn_id=turn_id,
        flow_run_id=stored_flow_run_id,
        effective_user_message=effective_user_message,
        replay_events=[],
        resume_trace_id=stored_trace_id,
    )


def _persist_execute_flow_runtime_state(
    *,
    session_id: str,
    user_id: str,
    turn_id: str,
    flow_run_id: str,
    trace_id: Optional[str],
) -> None:
    """Persist execute-flow runtime identifiers on the durable user row."""

    completion_db = SessionLocal()
    try:
        repository = _get_chat_history_repository(completion_db)
        user_turn = repository.get_message_by_turn_id(
            session_id=session_id,
            user_auth_sub=user_id,
            turn_id=turn_id,
            role="user",
        )
        if user_turn is None:
            raise LookupError("Chat user turn not found")

        existing_flow_run_id, existing_trace_id = _extract_execute_flow_runtime_identifiers(
            user_turn.payload_json,
        )
        effective_flow_run_id = existing_flow_run_id or flow_run_id
        effective_trace_id = str(trace_id or existing_trace_id or "").strip() or None
        if (
            existing_flow_run_id == effective_flow_run_id
            and existing_trace_id == effective_trace_id
            and user_turn.trace_id == effective_trace_id
        ):
            return

        repository.update_message_by_turn_id(
            session_id=session_id,
            user_auth_sub=user_id,
            turn_id=turn_id,
            role="user",
            payload_json=_build_execute_flow_runtime_payload(
                user_turn.payload_json,
                flow_run_id=effective_flow_run_id,
                trace_id=effective_trace_id,
            ),
            trace_id=effective_trace_id,
        )
        completion_db.commit()
    except Exception:
        completion_db.rollback()
        raise
    finally:
        completion_db.close()


def _persist_completed_execute_flow_turn(
    *,
    session_id: str,
    user_id: str,
    turn_id: str,
    user_message: str,
    transcript_rows: List[ExecuteFlowTranscriptRow],
) -> None:
    """Persist completed execute-flow transcript rows using a fresh SQL session."""

    completion_db = SessionLocal()
    try:
        repository = _get_chat_history_repository(completion_db)
        session = repository.get_session(
            session_id=session_id,
            user_auth_sub=user_id,
        )
        if session is None:
            raise ChatHistorySessionNotFoundError("Chat session not found")

        existing_replay = _build_execute_flow_turn_replay(
            repository.list_messages_for_turn(
                session_id=session_id,
                user_auth_sub=user_id,
                chat_kind=ASSISTANT_CHAT_KIND,
                turn_id=turn_id,
            )
        )
        if existing_replay is not None:
            return

        for row in transcript_rows:
            repository.append_message(
                session_id=session_id,
                user_auth_sub=user_id,
                chat_kind=ASSISTANT_CHAT_KIND,
                role="flow",
                content=row.content,
                message_type=row.message_type,
                turn_id=turn_id,
                payload_json=row.payload_json,
                trace_id=row.trace_id,
                created_at=row.created_at,
            )
        completion_db.commit()

    except Exception:
        completion_db.rollback()
        raise
    finally:
        completion_db.close()


# Document Management Endpoints



@router.post("/chat/execute-flow")
async def execute_flow_endpoint(
    request: ExecuteFlowRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
):
    """Execute a curation flow with SSE streaming response.

    Executes a user-defined curation flow, streaming events back via SSE.
    Flow ownership is verified before execution.

    Returns:
        StreamingResponse with Server-Sent Events

    HTTP Status Codes:
        200: Success (streaming response)
        400: Validation error (Pydantic)
        401: Unauthorized
        403: User doesn't own this flow
        404: Flow not found or soft-deleted
    """
    db_user = set_global_user_from_cognito(db, user)
    repository = _get_chat_history_repository(db)

    flow = db.query(CurationFlow).filter(
        CurationFlow.id == request.flow_id,
        CurationFlow.is_active == True,  # noqa: E712 - SQLAlchemy requires == for SQL
    ).first()

    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    if flow.user_id != db_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    user_id = _require_user_sub(user)
    history_user_message = (request.user_query or "").strip() or f"Run flow '{flow.name}'"

    cognito_groups = user.get("cognito:groups", [])
    active_groups = get_groups_from_cognito(cognito_groups)
    if active_groups:
        logger.info(
            "User has active groups: %s",
            active_groups,
            extra={"session_id": request.session_id, "user_id": user_id},
        )

    set_current_session_id(request.session_id)
    set_current_user_id(user_id)

    active_doc = document_state.get_document(user_id)
    document_name = active_doc.get("filename") if active_doc else None

    logger.info(
        "Starting flow execution: flow_id=%s flow_name=%s document_id=%s document_name=%s turn_id=%s",
        request.flow_id,
        flow.name,
        request.document_id,
        document_name,
        request.turn_id,
        extra={"session_id": request.session_id, "user_id": user_id, "turn_id": request.turn_id},
    )

    stream_lifecycle = await _claim_active_stream_lifecycle(
        session_id=request.session_id,
        user_id=user_id,
    )
    cancel_event = stream_lifecycle.cancel_event
    generated_title_candidate: str | None = None

    try:
        active_document_id, _ = _resolve_session_create_active_document(
            repository=repository,
            user_id=user_id,
        )
        prepared_turn = _prepare_execute_flow_turn(
            repository=repository,
            db=db,
            flow=flow,
            session_id=request.session_id,
            user_id=user_id,
            user_message=history_user_message,
            requested_turn_id=request.turn_id,
            active_document_id=active_document_id,
        )
        generated_title_candidate = _generate_title_from_turn(
            user_message=prepared_turn.effective_user_message,
        )
    except HTTPException:
        await stream_lifecycle.cleanup(request.session_id)
        raise
    except ValueError as exc:
        await stream_lifecycle.cleanup(request.session_id)
        _rollback_and_raise(
            db,
            status_code=400,
            detail="Invalid flow execution request",
            exc=exc,
            log_message=f"Failed to prepare execute-flow request for session {request.session_id}",
            level=logging.WARNING,
        )
    except Exception as exc:
        logger.error(
            "Failed to persist execute-flow request for session %s",
            request.session_id,
            extra={"session_id": request.session_id, "user_id": user_id, "turn_id": request.turn_id},
            exc_info=True,
        )
        db.rollback()
        await stream_lifecycle.cleanup(request.session_id)
        raise HTTPException(status_code=500, detail="Failed to start flow execution") from exc

    if prepared_turn.replay_events:
        if prepared_turn.replay_assistant_message is not None:
            generated_title_candidate = _generate_title_from_turn(
                user_message=prepared_turn.effective_user_message,
                assistant_message=prepared_turn.replay_assistant_message,
            )

        async def replay_stream():
            for event_payload in prepared_turn.replay_events:
                yield _stream_event_sse(event_payload)

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

    async def event_generator():
        """Generate SSE events from flow execution with cancellation support."""
        nonlocal generated_title_candidate
        current_session_id = request.session_id
        current_turn_id = prepared_turn.turn_id
        trace_id = None
        flow_status: Optional[str] = None
        flow_failure_reason: Optional[str] = None
        run_finished_response = ""
        chat_output_response = ""
        agents_used: List[str] = []
        specialist_outputs: List[Dict[str, Any]] = []
        specialist_summaries: List[Dict[str, Any]] = []
        domain_warnings: List[Dict[str, Any]] = []
        file_outputs: List[Dict[str, Any]] = []
        transcript_rows: List[ExecuteFlowTranscriptRow] = []
        run_started_event: Optional[Dict[str, Any]] = None
        chat_output_ready_event: Optional[Dict[str, Any]] = None
        run_error_event: Optional[Dict[str, Any]] = None
        buffered_flow_finished_event: Optional[Dict[str, Any]] = None

        try:
            async for event in execute_flow(
                flow=flow,
                user_id=user_id,
                session_id=current_session_id,
                db_user_id=db_user.id,
                document_id=str(request.document_id) if request.document_id else None,
                document_name=document_name,
                user_query=request.user_query,
                active_groups=active_groups,
                flow_run_id=prepared_turn.flow_run_id,
                trace_context=(
                    {"trace_id": prepared_turn.resume_trace_id}
                    if prepared_turn.resume_trace_id
                    else None
                ),
            ):
                if cancel_event.is_set() or await check_cancel_signal(current_session_id):
                    logger.info(
                        "Flow execution cancelled for session %s",
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
                            "RUN_ERROR",
                            session_id=current_session_id,
                            turn_id=current_turn_id,
                            trace_id=trace_id,
                            message="Flow execution cancelled by user",
                            error_type="FlowCancelled",
                        )
                    )
                    break

                event_type = event.get("type")
                event_data = event.get("data", {}) or {}
                event_details = event.get("details", {}) or {}

                if event_type == "RUN_STARTED" and "trace_id" in event_data:
                    trace_id = event_data.get("trace_id")
                    _persist_execute_flow_runtime_state(
                        session_id=current_session_id,
                        user_id=user_id,
                        turn_id=current_turn_id,
                        flow_run_id=prepared_turn.flow_run_id,
                        trace_id=trace_id,
                    )

                if event_type == "RUN_FINISHED":
                    run_finished_response = str(event_data.get("response") or "")
                    agents_used.extend([
                        str(agent_name) for agent_name in (event_data.get("agents_used") or [])
                        if agent_name
                    ])
                elif event_type == "CHAT_OUTPUT_READY":
                    chat_output_response = str(event_details.get("output") or event_data.get("output") or "")
                elif event_type == "CREW_START":
                    crew_name = event_details.get("crewDisplayName") or event_details.get("crewName")
                    if crew_name:
                        agents_used.append(str(crew_name))
                elif event_type == "SPECIALIST_SUMMARY":
                    specialist_summaries.append(dict(event_details))
                elif event_type == "DOMAIN_WARNING":
                    domain_warnings.append(dict(event_details))
                elif event_type == "FILE_READY":
                    file_outputs.append(dict(event_details))
                elif event_type == "FLOW_FINISHED":
                    flow_status = event_data.get("status")
                    flow_failure_reason = event_data.get("failure_reason")
                elif event_type == "TOOL_COMPLETE":
                    tool_name = event_details.get("toolName")
                    internal_payload = event.get("internal")
                    if (
                        isinstance(internal_payload, dict)
                        and isinstance(tool_name, str)
                        and tool_name.startswith("ask_")
                        and tool_name.endswith("_specialist")
                        and "tool_output" in internal_payload
                    ):
                        raw_output = internal_payload.get("tool_output")
                        output_text = str(raw_output) if raw_output is not None else ""
                        specialist_outputs.append({
                            "tool": tool_name,
                            "output": output_text,
                            "output_length": internal_payload.get("output_length", len(output_text)),
                        })

                flat_event = {
                    "type": event_type,
                    "session_id": current_session_id,
                    "turn_id": current_turn_id,
                }
                flat_event.update(event_data)

                if "timestamp" in event:
                    flat_event["timestamp"] = event["timestamp"]
                if "details" in event:
                    flat_event["details"] = event["details"]

                if event_type == "FLOW_STEP_EVIDENCE":
                    for source in (event, event_details):
                        for key in (
                            "flow_id",
                            "flow_name",
                            "flow_run_id",
                            "step",
                            "tool_name",
                            "agent_id",
                            "agent_name",
                            "evidence_preview",
                            "evidence_records",
                            "evidence_count",
                            "total_evidence_records",
                        ):
                            if key in source and key not in flat_event:
                                flat_event[key] = source[key]

                if event_type == "RUN_STARTED":
                    run_started_event = dict(flat_event)
                elif event_type == "CHAT_OUTPUT_READY":
                    chat_output_ready_event = dict(flat_event)
                elif event_type == "RUN_ERROR":
                    raw_message = str(flat_event.get("message") or "").strip()
                    if raw_message:
                        logger.error(
                            "Flow runner emitted RUN_ERROR: %s",
                            raw_message,
                            extra={
                                "session_id": current_session_id,
                                "user_id": user_id,
                                "trace_id": trace_id,
                                "turn_id": current_turn_id,
                            },
                        )
                    else:
                        logger.error(
                            "Flow runner emitted RUN_ERROR without message field",
                            extra={
                                "session_id": current_session_id,
                                "user_id": user_id,
                                "trace_id": trace_id,
                                "turn_id": current_turn_id,
                            },
                        )
                    flat_event["message"] = "Flow execution failed unexpectedly."
                    details = flat_event.get("details")
                    if isinstance(details, dict) and "error" in details:
                        flat_event["details"] = {**details, "error": "Flow execution failed unexpectedly."}
                    run_error_event = dict(flat_event)
                elif event_type == "FLOW_FINISHED":
                    buffered_flow_finished_event = dict(flat_event)

                transcript_row = _build_execute_flow_transcript_row_from_event(flat_event)
                if transcript_row is not None:
                    transcript_rows.append(transcript_row)

                if event_type == "FLOW_FINISHED":
                    continue

                yield _stream_event_sse(flat_event)

            if flow_status:
                history_assistant_message = _build_flow_memory_assistant_message(
                    flow_name=flow.name,
                    flow_id=str(flow.id),
                    session_id=current_session_id,
                    status=flow_status,
                    trace_id=trace_id,
                    final_user_output=chat_output_response or run_finished_response,
                    agents_used=agents_used,
                    specialist_outputs=specialist_outputs,
                    specialist_summaries=specialist_summaries,
                    domain_warnings=domain_warnings,
                    file_outputs=file_outputs,
                    failure_reason=flow_failure_reason,
                )
                summary_row = _build_execute_flow_summary_row(
                    flow_id=str(flow.id),
                    flow_name=flow.name,
                    flow_run_id=str(
                        (buffered_flow_finished_event or {}).get("flow_run_id") or ""
                    ).strip() or None,
                    session_id=current_session_id,
                    document_id=str(request.document_id) if request.document_id else None,
                    status=flow_status,
                    trace_id=trace_id,
                    final_user_output=chat_output_response or run_finished_response,
                    failure_reason=flow_failure_reason,
                    assistant_message=history_assistant_message,
                    run_started_event=run_started_event,
                    terminal_events=[
                        event_payload
                        for event_payload in [
                            chat_output_ready_event,
                            run_error_event,
                            buffered_flow_finished_event,
                        ]
                        if event_payload is not None
                    ],
                )
                _persist_completed_execute_flow_turn(
                    session_id=current_session_id,
                    user_id=user_id,
                    turn_id=current_turn_id,
                    user_message=prepared_turn.effective_user_message,
                    transcript_rows=[*transcript_rows, summary_row],
                )
                generated_title_candidate = _generate_title_from_turn(
                    user_message=prepared_turn.effective_user_message,
                    assistant_message=chat_output_response or run_finished_response or history_assistant_message,
                )

            if buffered_flow_finished_event is not None:
                yield _stream_event_sse(buffered_flow_finished_event)

        except asyncio.CancelledError:
            logger.warning(
                "Flow execution cancelled unexpectedly for session %s",
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
                        "error": "Flow cancelled unexpectedly",
                        "context": "asyncio.CancelledError",
                    },
                )
            )
            yield _stream_event_sse(
                _stream_event_payload(
                    "RUN_ERROR",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                    message="Flow execution was interrupted unexpectedly.",
                    error_type="StreamCancelled",
                )
            )
        except Exception as exc:
            run_error_message = (
                str(exc)
                if isinstance(exc, ValueError)
                else "Flow execution failed unexpectedly."
            )
            logger.error(
                "Flow execution error: %s",
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
                        error="Flow execution failed unexpectedly.",
                        exc=exc,
                    ),
                )
            )
            yield _stream_event_sse(
                _stream_event_payload(
                    "RUN_ERROR",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                    message=run_error_message,
                    error_type=type(exc).__name__,
                )
            )
        finally:
            await stream_lifecycle.cleanup(current_session_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        background=stream_lifecycle.background_task(lambda: generated_title_candidate),
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
