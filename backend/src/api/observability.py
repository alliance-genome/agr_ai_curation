"""Dev-only observability smoke endpoints."""

import os

from fastapi import APIRouter, HTTPException

from src.lib.alerts.tool_failure_notifier import notify_tool_failure

router = APIRouter(prefix="/api/observability")


def _synthetic_endpoints_enabled() -> bool:
    return (
        os.getenv("DEV_MODE", "false").strip().lower() == "true"
        and os.getenv("SENTRY_SYNTHETIC_TEST_ENDPOINTS_ENABLED", "false").strip().lower()
        in {"1", "true", "yes", "on"}
    )


def _require_synthetic_endpoints_enabled() -> None:
    if not _synthetic_endpoints_enabled():
        raise HTTPException(status_code=404, detail="Not found")


@router.post("/sentry/synthetic-unhandled")
async def sentry_synthetic_unhandled() -> None:
    """Raise a sanitized dev-only exception for Sentry ingestion smoke tests."""

    _require_synthetic_endpoints_enabled()
    raise RuntimeError("synthetic_sentry_unhandled")


@router.post("/sentry/synthetic-caught-alert")
async def sentry_synthetic_caught_alert() -> dict[str, object]:
    """Report a sanitized dev-only caught alert through the runtime facade."""

    _require_synthetic_endpoints_enabled()
    sns_sent = await notify_tool_failure(
        error_type="SyntheticSentryCaughtAlert",
        error_message="sanitized synthetic caught alert",
        source="infrastructure",
        specialist_name="sentry_synthetic_caught_alert",
        trace_id="synthetic-sentry-trace",
        session_id="synthetic-sentry-session",
        curator_id="synthetic-sentry-curator",
        context="sanitized synthetic context",
    )
    return {
        "status": "reported",
        "sns_sent": sns_sent,
    }
