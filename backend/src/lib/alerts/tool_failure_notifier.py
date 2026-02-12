"""SNS notifier for infrastructure and tool-call failures."""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import boto3

logger = logging.getLogger(__name__)


async def notify_tool_failure(
    error_type: str,
    error_message: str,
    source: str,
    specialist_name: Optional[str],
    trace_id: Optional[str],
    session_id: Optional[str],
    curator_id: Optional[str],
    context: Optional[str] = None,
) -> bool:
    """
    Send a tool failure alert to SNS.

    Alerts are gated by TOOL_FAILURE_ALERTS_ENABLED and are best-effort only:
    failures are logged but never raised to callers.
    """
    alerts_enabled = os.getenv("TOOL_FAILURE_ALERTS_ENABLED", "false").lower() == "true"
    sns_topic_arn = os.getenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN")

    if not alerts_enabled:
        return False

    if not sns_topic_arn:
        logger.warning(
            "TOOL_FAILURE_ALERTS_ENABLED is true but PROMPT_SUGGESTIONS_SNS_TOPIC_ARN is not set"
        )
        return False

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    tool_name = specialist_name or "N/A"
    source_description = (
        "infrastructure (backend exception handler)"
        if source == "infrastructure"
        else "opus_report (Claude detected and reported)"
    )

    lines = [
        f"[Tool Failure] {error_type}: {tool_name}",
        "=" * 58,
        f"Source:         {source_description}",
        f"Error Type:     {error_type or 'N/A'}",
        f"Error Message:  {error_message or 'N/A'}",
        f"Tool:           {tool_name}",
        f"Trace ID:       {trace_id or 'N/A'}",
        f"Session ID:     {session_id or 'N/A'}",
        f"Curator:        {curator_id or 'N/A'}",
        f"Timestamp:      {timestamp}",
    ]

    if context:
        lines.append(f"Context:        {context}")

    langfuse_url = os.getenv("LANGFUSE_PUBLIC_URL")
    if trace_id and langfuse_url:
        lines.extend(["", f"View trace: {langfuse_url.rstrip('/')}/trace/{trace_id}"])

    lines.append("=" * 58)

    subject = f"[Tool Failure] {error_type}: {tool_name}"[:100]
    message = "\n".join(lines)

    try:
        sns_region = os.getenv("SNS_REGION", "us-east-1")
        aws_profile = os.getenv("AWS_PROFILE")

        def _publish() -> dict:
            if aws_profile:
                session = boto3.Session(profile_name=aws_profile)
                sns_client = session.client("sns", region_name=sns_region)
            else:
                sns_client = boto3.client("sns", region_name=sns_region)

            return sns_client.publish(
                TopicArn=sns_topic_arn,
                Subject=subject,
                Message=message,
                MessageAttributes={
                    "type": {"DataType": "String", "StringValue": "tool_failure"},
                },
            )

        response = await asyncio.to_thread(_publish)
        logger.info(
            "Tool failure alert sent via SNS: %s",
            response.get("MessageId", "unknown"),
            extra={
                "error_type": error_type,
                "source": source,
                "tool_name": tool_name,
                "trace_id": trace_id,
                "session_id": session_id,
                "curator_id": curator_id,
            },
        )
        return True
    except Exception as exc:
        logger.error(
            "Failed to send tool failure notification via SNS: %s",
            exc,
            exc_info=True,
            extra={
                "error_type": error_type,
                "source": source,
                "tool_name": tool_name,
                "trace_id": trace_id,
                "session_id": session_id,
                "curator_id": curator_id,
            },
        )
        return False
