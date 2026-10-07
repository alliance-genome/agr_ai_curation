"""Optional intent screening; never changes or blocks the selected chat agent."""
import asyncio
import json
import math
import os
from pathlib import Path

import httpx

from src.lib.openai_agents.config import (
    get_studio_reminder_enabled, get_studio_reminder_timeout_seconds,
    get_studio_reminder_threshold, get_studio_reminder_message_chars,
    get_studio_reminder_context_chars, get_studio_reminder_context_messages,
)

from src.lib.observability.runtime import report_runtime_exception, sanitized_runtime_error
QUESTIONS = json.loads(Path(__file__).with_name("studio_reminder_prompt.json").read_text())


async def should_suggest_studio(message: str, context: list[dict[str, str]]) -> bool | None:
    key = os.getenv("JEV_OPENROUTER_API_KEY", "").strip()
    if not get_studio_reminder_enabled() or not key or not message.strip():
        return False
    count = get_studio_reminder_context_messages()
    recent = context[-count:] if count else []
    state = {
        "current_message": message[:get_studio_reminder_message_chars()],
        "recent_context": [
            {"role": item["role"], "text": item["text"][:get_studio_reminder_context_chars()]}
            for item in recent
        ],
    }
    timeout = get_studio_reminder_timeout_seconds()
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.post(
                    "https://openrouter.ai/api/alpha/decisions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": os.getenv("STUDIO_REMINDER_MODEL", "typesafe/jev-1.13"),
                          "state": state, "questions": QUESTIONS},
                )
                response.raise_for_status()
                probability = response.json()["answers"]["focused"]["noul"]
                if isinstance(probability, bool) or not isinstance(probability, (int, float)):
                    raise ValueError("Invalid reminder probability")
                if not math.isfinite(probability) or not 0 <= probability <= 1:
                    raise ValueError("Invalid reminder probability")
                return probability >= get_studio_reminder_threshold()
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as exc:
        # No transcripts, provider bodies or credentials in logs/telemetry.
        report_runtime_exception(
            sanitized_runtime_error("Studio reminder screening failed"),
            component="studio_reminder", operation="screening_failed",
            context={"error_type": type(exc).__name__},
        )
        return None
