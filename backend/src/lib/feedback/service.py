"""FeedbackService: Orchestrate user feedback processing workflow."""

import asyncio
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, List

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.lib.agent_studio.trace_context_service import get_trace_context_for_explorer
from src.lib.feedback.debug_links import (
    build_feedback_debug_url,
    build_trace_review_session_bundle_url,
)
from src.lib.feedback.email_notifier import EmailNotifier
from src.lib.chat_history_repository import ChatHistoryRepository, ChatHistorySessionNotFoundError
from src.lib.feedback.models import FeedbackReport, ProcessingStatus
from src.lib.feedback.sns_notifier import SNSNotifier
from src.lib.feedback.transcript import capture_feedback_conversation_transcript
from src.lib.agent_studio.models import TraceContext

logger = logging.getLogger(__name__)

MAX_TRACE_SNAPSHOT_TRACES = 5
MAX_TRACE_SNAPSHOT_ITEMS = 20
MAX_TRACE_PREVIEW_CHARS = 500
MAX_TRACE_ERROR_CHARS = 300

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_BEARER_TOKEN_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|password|secret|token)\b\s*[:=]\s*([^\s,;]+)"
)


class FeedbackService:
    """Orchestrates the complete user feedback processing workflow.

    This service coordinates:
    1. Lightweight payload creation (immediate, < 100ms)
    2. Background trace snapshot capture and notification via SNS or email

    The two-phase approach ensures curators get immediate feedback
    while notifications happen asynchronously.
    """

    def __init__(self, db: Session):
        """Initialize FeedbackService with database session.

        Args:
            db: SQLAlchemy database session for feedback operations
        """
        self.db = db

        # Check if SNS is enabled
        use_sns = os.getenv("FEEDBACK_USE_SNS", "false").lower() == "true"

        if use_sns:
            sns_topic_arn = os.getenv("SNS_TOPIC_ARN")
            sns_region = os.getenv("SNS_REGION", "us-east-1")

            if not sns_topic_arn:
                logger.warning("SNS enabled but SNS_TOPIC_ARN not configured, falling back to Email")
                self.notifier = EmailNotifier()
            else:
                logger.info('Using SNS for feedback notifications: %s', sns_topic_arn)
                self.notifier = SNSNotifier(topic_arn=sns_topic_arn, region=sns_region)
        else:
            # Use existing SMTP email notifier
            logger.info("Using SMTP for feedback notifications")
            self.notifier = EmailNotifier()

        logger.info("FeedbackService initialized")

    def create_feedback_payload(
        self,
        session_id: str,
        curator_id: str,
        feedback_text: str,
        trace_ids: List[str],
        user_auth_sub: str,
        authenticated_curator_email: str | None = None,
    ) -> str:
        """Create lightweight feedback payload and save to database.

        This method performs minimal processing to ensure fast response times
        (< 100ms). Heavy processing happens later in background.

        Args:
            session_id: Chat session identifier
            curator_id: Email/ID of curator submitting feedback
            feedback_text: Feedback comments from curator
            trace_ids: List of trace IDs to attach (for reference only)
            user_auth_sub: Authenticated token subject used for transcript lookup
            authenticated_curator_email: Authenticated email used to validate
                developer-facing curator_id values from the UI

        Returns:
            feedback_id: UUID string identifying this feedback report
        """
        feedback_id = str(uuid.uuid4())
        conversation_transcript = self._maybe_capture_conversation_transcript(
            feedback_id=feedback_id,
            session_id=session_id,
            curator_id=curator_id,
            user_auth_sub=user_auth_sub,
            authenticated_curator_email=authenticated_curator_email,
        )

        report = FeedbackReport(
            id=feedback_id,
            session_id=session_id,
            curator_id=curator_id,
            feedback_text=feedback_text,
            trace_ids=trace_ids,
            conversation_transcript=conversation_transcript,
            processing_status=ProcessingStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )

        self.db.add(report)
        self.db.commit()

        logger.info('Created feedback payload %s for session %s', feedback_id, session_id)
        return feedback_id

    def process_feedback_report(self, feedback_id: str) -> None:
        """Process feedback report in background.

        This method:
        1. Fetches the feedback report from database
        2. Captures compact trace review metadata when trace IDs are available
        3. Sends notification via SNS or email

        Args:
            feedback_id: UUID of feedback report to process
        """
        # Fetch report from database
        report = (
            self.db.query(FeedbackReport)
            .filter(FeedbackReport.id == feedback_id)
            .first()
        )

        if not report:
            logger.error('Feedback report %s not found in database', feedback_id)
            return

        try:
            # Mark as processing
            report.processing_status = ProcessingStatus.PROCESSING
            report.processing_started_at = datetime.now(timezone.utc)
            self.db.commit()

            logger.info('Starting background processing for feedback %s', feedback_id)

            try:
                trace_snapshot = self._capture_feedback_trace_snapshot(report)
                if trace_snapshot is not None:
                    report.trace_data = trace_snapshot
                    if trace_snapshot.get("capture_status") != "success":
                        report.error_details = (
                            "Trace capture completed with errors. "
                            "See trace_data.error_summary for details."
                        )
                    logger.info('Captured trace snapshot for feedback %s', feedback_id)
            except Exception as e:
                logger.warning(
                    'Trace snapshot capture failed for feedback %s: %s',
                    feedback_id,
                    str(e),
                    exc_info=True,
                )
                report.trace_data = self._trace_capture_failure_snapshot(report, e)
                capture_error = self._trace_capture_error(e)
                report.error_details = (
                    f"Trace capture failed: {capture_error['type']}: {capture_error['message']}"
                )

            # Send notification (SNS or email)
            try:
                self.notifier.send_feedback_notification(report)
                report.email_sent_at = datetime.now(timezone.utc)
                logger.info('Sent notification for feedback %s', feedback_id)
            except Exception as e:
                logger.error('Notification failed for %s: %s', feedback_id, str(e), exc_info=True)
                notification_error = f"Notification error: {str(e)}"
                if report.error_details:
                    report.error_details = f"{report.error_details}; {notification_error}"
                else:
                    report.error_details = notification_error

            # Mark as completed
            report.processing_status = ProcessingStatus.COMPLETED
            report.processing_completed_at = datetime.now(timezone.utc)
            self.db.commit()

            logger.info('Completed background processing for feedback %s', feedback_id)

        except Exception as e:
            # Catch-all for unexpected errors
            logger.error(
                'Unexpected error processing feedback %s: %s', feedback_id, str(e), exc_info=True
            )
            report.processing_status = ProcessingStatus.FAILED
            report.error_details = f"Unexpected error: {str(e)}"
            self.db.commit()

    def get_feedback_debug_detail(self, feedback_id: str) -> dict[str, Any] | None:
        """Return read-only feedback debug details without raw trace payloads."""

        report = (
            self.db.query(FeedbackReport)
            .filter(FeedbackReport.id == feedback_id)
            .first()
        )
        if report is None:
            return None

        trace_ids = self._normalize_trace_ids(report.trace_ids)
        return {
            "feedback_id": str(report.id),
            "session_id": report.session_id,
            "curator_id": report.curator_id,
            "feedback_text": report.feedback_text,
            "trace_ids": trace_ids,
            "processing_status": self._processing_status_value(report.processing_status),
            "created_at": self._json_timestamp(report.created_at),
            "processing_started_at": self._json_timestamp(report.processing_started_at),
            "processing_completed_at": self._json_timestamp(report.processing_completed_at),
            "email_sent_at": self._json_timestamp(report.email_sent_at),
            "processing_error": self._redacted_optional_text(report.error_details),
            "feedback_debug_url": build_feedback_debug_url(str(report.id)),
            "trace_review_session_url": build_trace_review_session_bundle_url(report.session_id),
            "transcript": self._feedback_transcript_debug(report),
            "trace_data": self._feedback_trace_data_debug(report, trace_ids),
        }

    def _maybe_capture_conversation_transcript(
        self,
        *,
        feedback_id: str,
        session_id: str,
        curator_id: str,
        user_auth_sub: str,
        authenticated_curator_email: str | None,
    ) -> dict | None:
        """Capture one durable transcript snapshot when the auth context matches."""

        if not self._curator_matches_authenticated_user(
            curator_id=curator_id,
            user_auth_sub=user_auth_sub,
            authenticated_curator_email=authenticated_curator_email,
        ):
            logger.info(
                "Skipping durable transcript lookup for feedback %s because "
                "curator_id %s does not match the authenticated user",
                feedback_id,
                curator_id,
            )
            return None

        repository = ChatHistoryRepository(self.db)
        try:
            transcript = capture_feedback_conversation_transcript(
                repository=repository,
                session_id=session_id,
                user_auth_sub=user_auth_sub,
            )
        except ChatHistorySessionNotFoundError as exc:
            logger.warning(
                "Failed to capture durable transcript for feedback %s "
                "(session_id=%s, user_auth_sub=%s): %s",
                feedback_id,
                session_id,
                user_auth_sub,
                exc,
                exc_info=True,
            )
            return None
        except SQLAlchemyError as exc:
            self.db.rollback()
            logger.warning(
                "Failed to capture durable transcript for feedback %s "
                "(session_id=%s, user_auth_sub=%s): %s",
                feedback_id,
                session_id,
                user_auth_sub,
                exc,
                exc_info=True,
            )
            return None

        if transcript is None:
            logger.warning(
                "Durable transcript lookup returned no session for feedback %s "
                "(session_id=%s, user_auth_sub=%s)",
                feedback_id,
                session_id,
                user_auth_sub,
            )
            return None

        session_payload = transcript.get("session") if isinstance(transcript, dict) else None
        chat_kind = None
        if isinstance(session_payload, dict):
            normalized_chat_kind = str(session_payload.get("chat_kind") or "").strip()
            chat_kind = normalized_chat_kind or None

        if chat_kind == "assistant_chat":
            return transcript

        if chat_kind == "agent_studio":
            logger.warning(
                "Feedback %s captured an unexpected agent_studio transcript for session %s",
                feedback_id,
                session_id,
            )
            return transcript

        logger.warning(
            "Feedback %s captured transcript for session %s with unexpected chat_kind=%r",
            feedback_id,
            session_id,
            chat_kind,
        )

        return transcript

    def _capture_feedback_trace_snapshot(self, report: FeedbackReport) -> dict | None:
        """Capture a compact, redacted trace snapshot for later feedback review."""

        trace_ids = self._normalize_trace_ids(report.trace_ids)
        if not trace_ids:
            return None

        captured_at = self._utc_timestamp()
        traces = []
        error_count = 0

        for trace_id in trace_ids[:MAX_TRACE_SNAPSHOT_TRACES]:
            try:
                trace_context = asyncio.run(get_trace_context_for_explorer(trace_id))
                traces.append(
                    self._trace_context_snapshot(
                        trace_context=trace_context,
                        feedback_session_id=report.session_id,
                    )
                )
            except Exception as exc:
                error_count += 1
                traces.append(
                    {
                        "trace_id": trace_id,
                        "capture_status": "error",
                        "error": self._trace_capture_error(exc),
                    }
                )

        if error_count == 0:
            capture_status = "success"
        elif error_count == len(traces):
            capture_status = "error"
        else:
            capture_status = "partial"

        snapshot = {
            "schema_version": 1,
            "capture_status": capture_status,
            "captured_at": captured_at,
            "source": {
                "kind": "langfuse",
                "extractor": (
                    "src.lib.agent_studio.trace_context_service."
                    "get_trace_context_for_explorer"
                ),
            },
            "feedback": {
                "session_id": report.session_id,
                "trace_ids": trace_ids,
            },
            "traces": traces,
        }

        omitted_count = len(trace_ids) - MAX_TRACE_SNAPSHOT_TRACES
        if omitted_count > 0:
            snapshot["omitted_trace_id_count"] = omitted_count

        if error_count > 0:
            snapshot["error_summary"] = {
                "trace_error_count": error_count,
                "message": "One or more trace snapshots could not be captured.",
            }

        return snapshot

    def _trace_context_snapshot(
        self,
        *,
        trace_context: TraceContext,
        feedback_session_id: str,
    ) -> dict:
        trace_session_id = trace_context.session_id
        prompts_executed = list(trace_context.prompts_executed)
        routing_decisions = list(trace_context.routing_decisions)
        tool_calls = list(trace_context.tool_calls)

        snapshot = {
            "trace_id": trace_context.trace_id,
            "capture_status": "success",
            "session_id": trace_session_id,
            "session_matches_feedback": trace_session_id == feedback_session_id,
            "timestamp": self._json_timestamp(trace_context.timestamp),
            "metrics": {
                "total_duration_ms": trace_context.total_duration_ms,
                "total_tokens": trace_context.total_tokens,
                "agent_count": trace_context.agent_count,
                "prompt_execution_count": len(prompts_executed),
                "routing_decision_count": len(routing_decisions),
                "tool_call_count": len(tool_calls),
            },
            "previews": {
                "user_query": self._compact_redacted_text(
                    trace_context.user_query,
                    max_chars=MAX_TRACE_PREVIEW_CHARS,
                ),
                "final_response": self._compact_redacted_text(
                    trace_context.final_response_preview,
                    max_chars=MAX_TRACE_PREVIEW_CHARS,
                ),
            },
            "prompts_executed": [
                {
                    "agent_id": prompt.agent_id,
                    "agent_name": prompt.agent_name,
                    "group_applied": prompt.group_applied,
                    "model": prompt.model,
                    "tokens_used": prompt.tokens_used,
                }
                for prompt in prompts_executed[:MAX_TRACE_SNAPSHOT_ITEMS]
            ],
            "routing_decisions": [
                {
                    "from_agent": decision.from_agent,
                    "to_agent": decision.to_agent,
                    "timestamp": self._json_timestamp(decision.timestamp),
                }
                for decision in routing_decisions[:MAX_TRACE_SNAPSHOT_ITEMS]
            ],
            "tool_calls": [
                {
                    "name": tool_call.name,
                    "duration_ms": tool_call.duration_ms,
                    "status": tool_call.status,
                }
                for tool_call in tool_calls[:MAX_TRACE_SNAPSHOT_ITEMS]
            ],
        }

        if len(prompts_executed) > MAX_TRACE_SNAPSHOT_ITEMS:
            snapshot["omitted_prompt_execution_count"] = (
                len(prompts_executed) - MAX_TRACE_SNAPSHOT_ITEMS
            )
        if len(routing_decisions) > MAX_TRACE_SNAPSHOT_ITEMS:
            snapshot["omitted_routing_decision_count"] = (
                len(routing_decisions) - MAX_TRACE_SNAPSHOT_ITEMS
            )
        if len(tool_calls) > MAX_TRACE_SNAPSHOT_ITEMS:
            snapshot["omitted_tool_call_count"] = len(tool_calls) - MAX_TRACE_SNAPSHOT_ITEMS

        return snapshot

    def _trace_capture_failure_snapshot(
        self,
        report: FeedbackReport,
        error: Exception,
    ) -> dict:
        trace_ids = self._normalize_trace_ids(report.trace_ids)
        return {
            "schema_version": 1,
            "capture_status": "error",
            "captured_at": self._utc_timestamp(),
            "source": {
                "kind": "langfuse",
                "extractor": (
                    "src.lib.agent_studio.trace_context_service."
                    "get_trace_context_for_explorer"
                ),
            },
            "feedback": {
                "session_id": report.session_id,
                "trace_ids": trace_ids,
            },
            "traces": [
                {
                    "trace_id": trace_id,
                    "capture_status": "error",
                    "error": self._trace_capture_error(error),
                }
                for trace_id in trace_ids[:MAX_TRACE_SNAPSHOT_TRACES]
            ],
            "error_summary": {
                "trace_error_count": min(len(trace_ids), MAX_TRACE_SNAPSHOT_TRACES),
                "message": "Trace snapshot capture failed before trace extraction completed.",
            },
        }

    @classmethod
    def _trace_capture_error(cls, error: Exception) -> dict:
        message = cls._compact_redacted_text(str(error), max_chars=MAX_TRACE_ERROR_CHARS)
        return {
            "type": error.__class__.__name__,
            "message": message,
        }

    @classmethod
    def _feedback_transcript_debug(cls, report: FeedbackReport) -> dict:
        transcript = report.conversation_transcript
        if transcript is None:
            return {
                "available": False,
                "message_count": None,
                "captured_at": None,
                "session_id": None,
                "chat_kind": None,
                "title": None,
                "effective_title": None,
                "session_matches_feedback": None,
            }
        if not isinstance(transcript, dict):
            raise TypeError("feedback conversation_transcript must be an object")

        session_payload = cls._optional_mapping(transcript, "session")

        transcript_session_id = cls._string_or_none(session_payload.get("session_id"))
        session_matches_feedback = (
            transcript_session_id == report.session_id
            if transcript_session_id is not None
            else None
        )

        return {
            "available": True,
            "message_count": cls._int_or_none(transcript.get("message_count")),
            "captured_at": cls._string_or_none(transcript.get("captured_at")),
            "session_id": transcript_session_id,
            "chat_kind": cls._string_or_none(session_payload.get("chat_kind")),
            "title": cls._string_or_none(session_payload.get("title")),
            "effective_title": cls._string_or_none(session_payload.get("effective_title")),
            "session_matches_feedback": session_matches_feedback,
        }

    @classmethod
    def _feedback_trace_data_debug(
        cls,
        report: FeedbackReport,
        trace_ids: list[str],
    ) -> dict:
        trace_data = report.trace_data
        if trace_data is None:
            return {
                "available": False,
                "status": "missing" if trace_ids else "not_requested",
                "stale": False,
                "capture_status": None,
                "captured_at": None,
                "schema_version": None,
                "source_kind": None,
                "source_extractor": None,
                "expected_trace_ids": trace_ids,
                "stored_trace_ids": [],
                "trace_count": 0,
                "omitted_trace_id_count": None,
                "error_summary": None,
                "errors": [],
            }
        if not isinstance(trace_data, dict):
            raise TypeError("feedback trace_data must be an object")

        feedback_payload = cls._optional_mapping(trace_data, "feedback")
        source_payload = cls._optional_mapping(trace_data, "source")
        trace_payloads = cls._optional_list(trace_data, "traces")

        stored_session_id = cls._string_or_none(feedback_payload.get("session_id"))
        stored_trace_ids = cls._normalize_trace_ids(feedback_payload.get("trace_ids"))
        stale = stored_session_id != report.session_id or stored_trace_ids != trace_ids
        capture_status = cls._string_or_none(trace_data.get("capture_status"))
        status = "stale" if stale else capture_status or "capture_status_missing"

        return {
            "available": True,
            "status": status,
            "stale": stale,
            "capture_status": capture_status,
            "captured_at": cls._string_or_none(trace_data.get("captured_at")),
            "schema_version": cls._int_or_none(trace_data.get("schema_version")),
            "source_kind": cls._string_or_none(source_payload.get("kind")),
            "source_extractor": cls._string_or_none(source_payload.get("extractor")),
            "expected_trace_ids": trace_ids,
            "stored_trace_ids": stored_trace_ids,
            "trace_count": len(trace_payloads),
            "omitted_trace_id_count": cls._int_or_none(
                trace_data.get("omitted_trace_id_count")
            ),
            "error_summary": cls._trace_data_error_summary(trace_data.get("error_summary")),
            "errors": cls._trace_data_errors(trace_payloads),
        }

    @classmethod
    def _trace_data_error_summary(cls, error_summary: Any) -> dict | None:
        if error_summary is None:
            return None
        if not isinstance(error_summary, dict):
            raise TypeError("error_summary must be an object")

        summary: dict[str, Any] = {}
        if "trace_error_count" in error_summary:
            summary["trace_error_count"] = cls._int_or_none(
                error_summary.get("trace_error_count")
            )
        if "message" in error_summary:
            summary["message"] = cls._redacted_optional_text(error_summary.get("message"))

        return {key: value for key, value in summary.items() if value is not None} or None

    @classmethod
    def _trace_data_errors(cls, trace_payloads: list[Any]) -> list[dict]:
        errors = []
        for trace_payload in trace_payloads:
            if not isinstance(trace_payload, dict):
                raise TypeError("trace payload entries must be objects")
            error_payload = trace_payload.get("error")
            if error_payload is None:
                continue
            if not isinstance(error_payload, dict):
                raise TypeError("trace payload error must be an object")
            errors.append(
                {
                    "trace_id": cls._string_or_none(trace_payload.get("trace_id")),
                    "type": cls._redacted_optional_text(error_payload.get("type")),
                    "message": cls._redacted_optional_text(error_payload.get("message")),
                }
            )
        return errors

    @staticmethod
    def _processing_status_value(status: Any) -> str:
        value = getattr(status, "value", status)
        return str(value)

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise TypeError("boolean values are not valid integers")
        return int(value)

    @staticmethod
    def _optional_mapping(payload: dict, key: str) -> dict:
        value = payload.get(key)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError(f"{key} must be an object")
        return value

    @staticmethod
    def _optional_list(payload: dict, key: str) -> list[Any]:
        value = payload.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError(f"{key} must be an array")
        return value

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _redacted_optional_text(cls, value: Any) -> str | None:
        return cls._compact_redacted_text(value, max_chars=MAX_TRACE_ERROR_CHARS)

    @staticmethod
    def _normalize_trace_ids(raw_trace_ids: Any) -> list[str]:
        if not isinstance(raw_trace_ids, list):
            return []

        normalized_trace_ids = []
        seen = set()
        for raw_trace_id in raw_trace_ids:
            if not isinstance(raw_trace_id, str):
                continue
            trace_id = raw_trace_id.strip()
            if not trace_id or trace_id in seen:
                continue
            normalized_trace_ids.append(trace_id)
            seen.add(trace_id)

        return normalized_trace_ids

    @staticmethod
    def _utc_timestamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _json_timestamp(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    @classmethod
    def _compact_redacted_text(cls, value: Any, *, max_chars: int) -> str | None:
        if value is None:
            return None

        text = str(value).replace("\r", " ").replace("\n", " ")
        text = re.sub(r"\s+", " ", text).strip()
        text = _EMAIL_RE.sub("[redacted-email]", text)
        text = _URL_RE.sub("[redacted-url]", text)
        text = _BEARER_TOKEN_RE.sub("Bearer [redacted-token]", text)
        text = _SECRET_ASSIGNMENT_RE.sub(
            lambda match: f"{match.group(1)}=[redacted]",
            text,
        )

        if len(text) <= max_chars:
            return text

        return f"{text[:max_chars].rstrip()}...[truncated]"

    @staticmethod
    def _curator_matches_authenticated_user(
        *,
        curator_id: str,
        user_auth_sub: str,
        authenticated_curator_email: str | None,
    ) -> bool:
        normalized_curator_id = curator_id.strip()
        if not normalized_curator_id:
            return False

        normalized_user_auth_sub = user_auth_sub.strip()
        if normalized_user_auth_sub and normalized_curator_id == normalized_user_auth_sub:
            return True

        normalized_email = (authenticated_curator_email or "").strip()
        if normalized_email and normalized_curator_id.casefold() == normalized_email.casefold():
            return True

        return False
