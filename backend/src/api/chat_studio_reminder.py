"""Authenticated, advisory classifier endpoint for the main chat UI."""
import os
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .auth import get_auth_dependency
from .chat_common import router, _require_user_sub, _get_chat_history_repository
from src.models.sql import get_db
from src.lib.studio_reminder import should_suggest_studio
from src.lib.openai_agents.config import (
    get_studio_reminder_enabled, get_studio_reminder_message_chars, get_studio_reminder_context_chars,
    get_studio_reminder_context_messages,
)


class ReminderContext(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=get_studio_reminder_context_chars())


class StudioReminderRequest(BaseModel):
    message: str = Field(min_length=1, max_length=get_studio_reminder_message_chars())
    recent_context: list[ReminderContext] = Field(default_factory=list, max_length=get_studio_reminder_context_messages())


@router.post("/chat/session/{session_id}/studio-reminder")
async def check_studio_reminder(
    session_id: str,
    request: StudioReminderRequest,
    db: Session = Depends(get_db),
    user: dict = get_auth_dependency(),
):
    session = _get_chat_history_repository(db).get_session(
        session_id=session_id, user_auth_sub=_require_user_sub(user),
    )
    if session is None or session.chat_kind != "assistant_chat":
        raise HTTPException(status_code=404, detail="Chat session not found")
    decision = await should_suggest_studio(
        request.message, [item.model_dump() for item in request.recent_context],
    )
    if decision is None:
        raise HTTPException(status_code=503, detail="Agent Studio reminder temporarily unavailable")
    return {"show_reminder": decision}


@router.get("/chat/studio-reminder/config")
async def studio_reminder_config(user: dict = get_auth_dependency()):
    return {
        "enabled": get_studio_reminder_enabled() and bool(os.getenv("JEV_OPENROUTER_API_KEY", "").strip()),
        "message_chars": get_studio_reminder_message_chars(),
        "context_chars": get_studio_reminder_context_chars(),
        "context_messages": get_studio_reminder_context_messages(),
    }
