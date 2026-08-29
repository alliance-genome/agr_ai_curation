"""PDFX parser client for AGR PDF extraction service."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiohttp

from src.lib.openai_agents.config import get_pdf_extraction_receipt_token_max_chars

from ..pdf_limits import MAX_PDF_FILE_SIZE_BYTES, pdf_file_size_limit_message
from ..storage_permissions import ensure_writable_directory
from ..exceptions import ConfigurationError, PDFCancellationError, PDFParsingError
from ..observability.sentry import (
    pdf_processing_stage_span,
    set_redacted_ai_span_data,
    set_pdf_processing_span_outcome,
)
from ...schemas.pdfx_schema import (  # noqa: F401 - re-exported for fixture tooling
    PDFXResponse,
    build_pipeline_elements,
    normalize_section_path,
    normalize_text,
    normalize_elements,
)
from .pdfx_page_provenance import (
    MergedPageProvenance,
    parse_merged_page_provenance,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]
ProcessIdCallback = Callable[[str], Awaitable[None]]
CancelRequestedCallback = Callable[[], Awaitable[bool]]
ObservabilityCallback = Callable[[Dict[str, Any]], None]

_TRUE_VALUES = {"1", "true", "yes", "on"}
_TRANSIENT_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}
_SAFE_PROVIDER_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*\Z")

PDFX_FAILURE_DETAILS_KEY = "pdfx_failure"
PDFX_PUBLIC_MESSAGE_DETAILS_KEY = "public_message"
PDFX_PROVIDER_FAILURE_MESSAGE = (
    "The PDF extraction service could not complete this document. Please try again later."
)
PDFX_POLLING_TIMEOUT_MESSAGE = (
    "PDF extraction did not finish within the configured time. Please try again later."
)
PDFX_UNKNOWN_FAILURE_MESSAGE = (
    "PDF extraction could not be completed. Please try again later."
)


def _safe_provider_token(value: Any) -> str | None:
    """Return a bounded content-free provider token, never free-form prose."""
    if not isinstance(value, str):
        return None
    token = value.strip()
    if len(token) > get_pdf_extraction_receipt_token_max_chars():
        return None
    return token if _SAFE_PROVIDER_TOKEN.fullmatch(token) else None


def _pdfx_failure_error(
    message: str,
    *,
    category: str,
    boundary: str,
    public_message: str,
    process_id: str | None = None,
    provider_status: str | None = None,
    provider_error_code: str | None = None,
    http_status: int | None = None,
) -> PDFParsingError:
    """Build a technical exception with a separate stable public message."""
    failure: Dict[str, Any] = {
        "failure_category": category,
        "failure_boundary": boundary,
    }
    if process_id:
        failure["process_id"] = process_id
    if provider_status:
        failure["provider_status"] = provider_status
    if provider_error_code:
        failure["provider_error_code"] = provider_error_code
    if http_status is not None:
        failure["http_status"] = http_status
    return PDFParsingError(
        message,
        details={
            PDFX_PUBLIC_MESSAGE_DETAILS_KEY: public_message,
            PDFX_FAILURE_DETAILS_KEY: failure,
        },
    )


def _with_attempt_evidence(
    failure: Dict[str, Any],
    *,
    submit_attempt_count: int,
    poll_attempt_count: int,
    timeout_seconds: int,
) -> Dict[str, Any]:
    """Copy failure evidence and add bounded request counters."""
    evidence = dict(failure)
    evidence.update(
        {
            "submit_attempt_count": submit_attempt_count,
            "poll_attempt_count": poll_attempt_count,
            "timeout_seconds": timeout_seconds,
        }
    )
    return evidence


def _cache_hit_from_payloads(*payloads: Any) -> bool | None:
    """Normalize an explicit provider cache signal without guessing from status text."""
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in ("cache_hit", "cached"):
            value = payload.get(key)
            if isinstance(value, bool):
                return value
        cache = payload.get("cache")
        if isinstance(cache, dict) and isinstance(cache.get("hit"), bool):
            return cache["hit"]
    return None


class PDFXParser:
    """Parser for PDFs using AGR PDF extraction service."""

    _cognito_access_token: Optional[str] = None
    _cognito_token_expires_at: float = 0.0

    def __init__(self):
        """Initialize parser configuration."""
        self.service_url = os.getenv("PDF_EXTRACTION_SERVICE_URL", "").rstrip("/")
        if not self.service_url:
            raise ConfigurationError("PDF_EXTRACTION_SERVICE_URL is required")

        timeout_raw = os.getenv("PDF_EXTRACTION_TIMEOUT", "3600")
        try:
            self.timeout_seconds = int(timeout_raw)
        except ValueError as exc:
            raise ConfigurationError(f"PDF_EXTRACTION_TIMEOUT must be an integer, got: {timeout_raw}") from exc
        if self.timeout_seconds < 1:
            raise ConfigurationError("PDF_EXTRACTION_TIMEOUT must be greater than 0")

        poll_interval_raw = os.getenv("PDF_EXTRACTION_POLL_INTERVAL_SECONDS", "2")
        try:
            self.poll_interval_seconds = float(poll_interval_raw)
        except ValueError as exc:
            raise ConfigurationError(
                "PDF_EXTRACTION_POLL_INTERVAL_SECONDS must be numeric"
            ) from exc
        if self.poll_interval_seconds <= 0:
            raise ConfigurationError("PDF_EXTRACTION_POLL_INTERVAL_SECONDS must be greater than 0")

        download_retry_raw = os.getenv("PDF_EXTRACTION_DOWNLOAD_RETRY_SECONDS", "120")
        try:
            self.download_retry_seconds = int(download_retry_raw)
        except ValueError as exc:
            raise ConfigurationError(
                f"PDF_EXTRACTION_DOWNLOAD_RETRY_SECONDS must be an integer, got: {download_retry_raw}"
            ) from exc
        if self.download_retry_seconds < 0:
            raise ConfigurationError("PDF_EXTRACTION_DOWNLOAD_RETRY_SECONDS must be 0 or greater")

        methods_raw = os.getenv("PDF_EXTRACTION_METHODS", "grobid,marker")
        self._method_list = [part.strip() for part in methods_raw.split(",") if part.strip()]
        self.methods = ",".join(self._method_list)
        if not self.methods:
            raise ConfigurationError("PDF_EXTRACTION_METHODS must include at least one extraction method")

        self.merge_enabled = os.getenv("PDF_EXTRACTION_MERGE", "true").strip().lower() in _TRUE_VALUES
        self.download_variant = "merged"
        if not self.merge_enabled:
            explicit_variant = os.getenv("PDF_EXTRACTION_PRIMARY_DOWNLOAD_METHOD", "").strip().lower()
            if explicit_variant:
                if explicit_variant not in self._method_list:
                    raise ConfigurationError(
                        "PDF_EXTRACTION_PRIMARY_DOWNLOAD_METHOD must match one of "
                        f"PDF_EXTRACTION_METHODS ({self.methods})"
                    )
                self.download_variant = explicit_variant
            else:
                # Deterministic, no fallback: use the first configured extraction method.
                self.download_variant = self._method_list[0]

        self.auth_mode = os.getenv("PDF_EXTRACTION_AUTH_MODE", "none").strip().lower()
        valid_auth_modes = {"none", "static_bearer", "cognito_client_credentials"}
        if self.auth_mode not in valid_auth_modes:
            raise ConfigurationError(
                f"Invalid PDF_EXTRACTION_AUTH_MODE '{self.auth_mode}'. "
                f"Expected one of: {sorted(valid_auth_modes)}"
            )

        self.invocation_count = 0
        self.max_invocations_per_session = 50
        self._submit_attempt_count = 0
        self._poll_attempt_count = 0

        logger.info(
            "Initialized PDF extraction parser service=%s timeout=%ss poll_interval=%ss "
            "download_retry=%ss methods=%s merge=%s auth_mode=%s",
            self.service_url,
            self.timeout_seconds,
            self.poll_interval_seconds,
            self.download_retry_seconds,
            self.methods,
            self.merge_enabled,
            self.auth_mode,
        )

    async def parse_pdf_document(
        self,
        file_path: Path,
        document_id: str,
        user_id: str,
        extraction_strategy: Optional[str] = None,
        enable_table_extraction: Optional[bool] = None,
        progress_callback: Optional[ProgressCallback] = None,
        process_id_callback: Optional[ProcessIdCallback] = None,
        cancel_requested_callback: Optional[CancelRequestedCallback] = None,
        observability_callback: Optional[ObservabilityCallback] = None,
    ) -> Dict[str, Any]:
        """Parse PDF through PDF extraction service and return pipeline elements."""
        del extraction_strategy
        del enable_table_extraction

        if not file_path.exists():
            raise PDFParsingError(f"File not found: {file_path}")
        if file_path.suffix.lower() != ".pdf":
            raise PDFParsingError(f"File is not a PDF: {file_path}")

        if self.invocation_count >= self.max_invocations_per_session:
            raise PDFParsingError(
                f"Circuit breaker: Too many invocations ({self.invocation_count}). "
                "Create a new parser instance or restart service."
            )
        self.invocation_count += 1

        logger.info("Submitting %s for extraction as document %s", file_path.name, document_id)

        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        external_started_at = datetime.now(timezone.utc)
        external_started_monotonic = time.monotonic()
        external_status = "failed"
        external_boundary = "authentication"
        process_id = ""
        failure_evidence: Dict[str, Any] = {}
        self._submit_attempt_count = 0
        self._poll_attempt_count = 0
        submit_payload: Dict[str, Any] = {}
        status_payload: Dict[str, Any] = {}
        external_span_context = pdf_processing_stage_span(
            stage="external_request",
            document_id=document_id,
            selection={
                "extraction_methods": list(self._method_list),
                "merge_enabled": self.merge_enabled,
                "download_variant": self.download_variant,
            },
        )
        external_span = external_span_context.__enter__()
        try:
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    headers = await self._build_auth_headers(session)
                    external_boundary = "submit"
                    submit_payload = await self._submit_extraction(session, file_path, headers)
                    process_id = str(submit_payload.get("process_id", "")).strip()
                    if not process_id:
                        raise PDFParsingError("Extraction service returned no process_id")

                    if process_id_callback:
                        await process_id_callback(process_id)

                    if cancel_requested_callback and await cancel_requested_callback():
                        await self._request_cancel(session, process_id, headers)
                        raise PDFCancellationError(
                            "PDF extraction cancelled by user request before polling started"
                        )

                    external_boundary = "poll"
                    status_payload = await self._poll_until_complete(
                        session=session,
                        process_id=process_id,
                        headers=headers,
                        progress_callback=progress_callback,
                        cancel_requested_callback=cancel_requested_callback,
                    )
                    external_boundary = "download"
                    merged_markdown = await self._download_markdown(session, process_id, headers)
                    page_provenance = None
                    if self.merge_enabled:
                        page_provenance = await self._download_page_provenance(
                            session,
                            process_id,
                            headers,
                            merged_markdown=merged_markdown.encode("utf-8"),
                        )
                external_status = "completed"
            except PDFCancellationError:
                external_status = "cancelled"
                raise
            except PDFParsingError as exc:
                failure = exc.details.get(PDFX_FAILURE_DETAILS_KEY)
                if not isinstance(failure, dict):
                    safe_process_id = _safe_provider_token(process_id)
                    wrapped = _pdfx_failure_error(
                        "PDF extraction failed at "
                        f"boundary={external_boundary}"
                        + (
                            f" process_id={safe_process_id}"
                            if safe_process_id
                            else ""
                        ),
                        category="unknown_provider_failure",
                        boundary=external_boundary,
                        public_message=PDFX_UNKNOWN_FAILURE_MESSAGE,
                        process_id=_safe_provider_token(process_id),
                    )
                    failure_evidence = _with_attempt_evidence(
                        wrapped.details[PDFX_FAILURE_DETAILS_KEY],
                        submit_attempt_count=self._submit_attempt_count,
                        poll_attempt_count=self._poll_attempt_count,
                        timeout_seconds=self.timeout_seconds,
                    )
                    wrapped.details[PDFX_FAILURE_DETAILS_KEY] = failure_evidence
                    raise wrapped from None
                failure_evidence = _with_attempt_evidence(
                    failure,
                    submit_attempt_count=self._submit_attempt_count,
                    poll_attempt_count=self._poll_attempt_count,
                    timeout_seconds=self.timeout_seconds,
                )
                exc.details[PDFX_FAILURE_DETAILS_KEY] = failure_evidence
                raise
            except asyncio.TimeoutError:
                wrapped = _pdfx_failure_error(
                    f"PDF extraction request timeout after {self.timeout_seconds} seconds",
                    category="unknown_provider_failure",
                    boundary=external_boundary,
                    public_message=PDFX_UNKNOWN_FAILURE_MESSAGE,
                    process_id=_safe_provider_token(process_id),
                )
                failure_evidence = _with_attempt_evidence(
                    wrapped.details[PDFX_FAILURE_DETAILS_KEY],
                    submit_attempt_count=self._submit_attempt_count,
                    poll_attempt_count=self._poll_attempt_count,
                    timeout_seconds=self.timeout_seconds,
                )
                wrapped.details[PDFX_FAILURE_DETAILS_KEY] = failure_evidence
                raise wrapped from None
            except aiohttp.ClientError as exc:
                wrapped = _pdfx_failure_error(
                    "PDF extraction network error at "
                    f"boundary={external_boundary} type={type(exc).__name__}",
                    category="unknown_provider_failure",
                    boundary=external_boundary,
                    public_message=PDFX_UNKNOWN_FAILURE_MESSAGE,
                    process_id=_safe_provider_token(process_id),
                )
                failure_evidence = _with_attempt_evidence(
                    wrapped.details[PDFX_FAILURE_DETAILS_KEY],
                    submit_attempt_count=self._submit_attempt_count,
                    poll_attempt_count=self._poll_attempt_count,
                    timeout_seconds=self.timeout_seconds,
                )
                wrapped.details[PDFX_FAILURE_DETAILS_KEY] = failure_evidence
                raise wrapped from None
        finally:
            external_completed_at = datetime.now(timezone.utc)
            external_duration_ms = (time.monotonic() - external_started_monotonic) * 1000
            cache_hit = _cache_hit_from_payloads(status_payload, submit_payload)
            if cache_hit is not None:
                set_redacted_ai_span_data(
                    external_span,
                    "ai_curation.pdf.cache_hit",
                    cache_hit,
                )
            set_pdf_processing_span_outcome(
                external_span,
                outcome=external_status,
                duration_ms=external_duration_ms,
            )
            external_span_context.__exit__(None, None, None)
            if observability_callback is not None:
                try:
                    observability_callback(
                        {
                            "status": external_status,
                            "started_at": external_started_at,
                            "completed_at": external_completed_at,
                            "duration_ms": external_duration_ms,
                            "extraction_methods": list(self._method_list),
                            "merge_enabled": self.merge_enabled,
                            "download_variant": self.download_variant,
                            "cache_hit": cache_hit,
                            "process_id": _safe_provider_token(process_id),
                            "submit_attempt_count": self._submit_attempt_count,
                            "poll_attempt_count": self._poll_attempt_count,
                            "timeout_seconds": self.timeout_seconds,
                            **failure_evidence,
                        }
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to record PDF extraction boundary observability: %s",
                        type(exc).__name__,
                    )

        cleaned_elements = markdown_to_pipeline_elements(
            merged_markdown,
            page_provenance=page_provenance,
        )
        if not cleaned_elements:
            raise PDFParsingError("PDF extraction produced no usable elements")

        raw_payload = {
            "document_id": document_id,
            "process_id": process_id,
            "service_url": self.service_url,
            "submit_response": submit_payload,
            "status_response": status_payload,
            "methods": self.methods.split(","),
            "merge": self.merge_enabled,
            "content_format": f"{self.download_variant}_markdown",
            "page_provenance": (
                page_provenance.receipt() if page_provenance is not None else None
            ),
        }

        pdfx_json_path = await self._save_pdfx_json(raw_payload, document_id, user_id)
        processed_json_path = await self._save_processed_json(cleaned_elements, document_id, user_id)

        return {
            "elements": cleaned_elements,
            "pdfx_json_path": str(pdfx_json_path),
            "processed_json_path": str(processed_json_path),
        }

    async def _build_auth_headers(self, session: aiohttp.ClientSession) -> Dict[str, str]:
        """Build authorization headers for PDF extraction API calls."""
        if self.auth_mode == "none":
            return {}

        if self.auth_mode == "static_bearer":
            token = os.getenv("PDF_EXTRACTION_BEARER_TOKEN", "").strip()
            if not token:
                raise ConfigurationError(
                    "PDF_EXTRACTION_BEARER_TOKEN is required when PDF_EXTRACTION_AUTH_MODE=static_bearer"
                )
            return {"Authorization": f"Bearer {token}"}

        token = await self._get_cognito_client_credentials_token(session)
        return {"Authorization": f"Bearer {token}"}

    async def _get_cognito_client_credentials_token(self, session: aiohttp.ClientSession) -> str:
        """Fetch and cache Cognito access token for service-to-service auth."""
        now = time.monotonic()
        if self._cognito_access_token and now < self._cognito_token_expires_at - 30:
            return self._cognito_access_token

        token_url = os.getenv("PDF_EXTRACTION_COGNITO_TOKEN_URL", "").strip()
        if not token_url:
            domain = os.getenv("COGNITO_DOMAIN", "").strip().rstrip("/")
            if not domain:
                raise ConfigurationError(
                    "Set PDF_EXTRACTION_COGNITO_TOKEN_URL or COGNITO_DOMAIN for cognito_client_credentials auth mode"
                )
            token_url = f"{domain}/oauth2/token"

        client_id = os.getenv("PDF_EXTRACTION_COGNITO_CLIENT_ID", "").strip()
        client_secret = os.getenv("PDF_EXTRACTION_COGNITO_CLIENT_SECRET", "").strip()
        scope = os.getenv("PDF_EXTRACTION_COGNITO_SCOPE", "").strip()
        if not client_id or not client_secret or not scope:
            raise ConfigurationError(
                "PDF_EXTRACTION_COGNITO_CLIENT_ID, PDF_EXTRACTION_COGNITO_CLIENT_SECRET, "
                "and PDF_EXTRACTION_COGNITO_SCOPE are required for cognito_client_credentials auth mode"
            )

        form_data = {"grant_type": "client_credentials", "scope": scope}
        auth = aiohttp.BasicAuth(client_id, client_secret)
        async with session.post(token_url, data=form_data, auth=auth) as response:
            token_text = await response.text()
            if response.status != 200:
                raise PDFParsingError(
                    f"Failed to fetch Cognito token: {response.status} - {token_text}"
                )
            try:
                token_payload = json.loads(token_text)
            except json.JSONDecodeError as exc:
                raise PDFParsingError("Cognito token endpoint returned non-JSON response") from exc

        access_token = str(token_payload.get("access_token", "")).strip()
        if not access_token:
            raise PDFParsingError("Cognito token response missing access_token")

        expires_in = token_payload.get("expires_in", 3600)
        try:
            expires_seconds = int(expires_in)
        except (TypeError, ValueError):
            expires_seconds = 3600

        self._cognito_access_token = access_token
        self._cognito_token_expires_at = time.monotonic() + max(expires_seconds, 60)
        return access_token

    async def _submit_extraction(
        self,
        session: aiohttp.ClientSession,
        file_path: Path,
        headers: Dict[str, str],
    ) -> Dict[str, Any]:
        """Submit extraction request and return service response payload.

        The existing bounded POST retry is intentionally unchanged here. The
        current PDFX proxy allocates a new process ID per POST, so broadening
        retries requires a separately reviewed cross-service idempotency
        contract.
        """
        extract_endpoint = f"{self.service_url}/api/v1/extract"
        submit_deadline = time.monotonic() + self.timeout_seconds
        attempt = 0

        while True:
            attempt += 1
            self._submit_attempt_count = attempt
            try:
                with open(file_path, "rb") as file_handle:
                    data = aiohttp.FormData()
                    data.add_field(
                        "file",
                        file_handle,
                        filename=file_path.name,
                        content_type="application/pdf",
                    )
                    data.add_field("methods", self.methods)
                    data.add_field("merge", str(self.merge_enabled).lower())

                    async with session.post(extract_endpoint, data=data, headers=headers) as response:
                        body_text = await response.text()
                        if response.status == 202:
                            try:
                                return json.loads(body_text)
                            except json.JSONDecodeError as exc:
                                raise PDFParsingError("PDF extraction submit returned non-JSON response") from exc

                        error_message = f"PDF extraction submit failed with HTTP {response.status}"
                        if response.status in _TRANSIENT_HTTP_STATUS and time.monotonic() < submit_deadline:
                            logger.warning(
                                "Transient PDF extraction submit error (attempt %s): %s",
                                attempt,
                                error_message,
                            )
                            await asyncio.sleep(self.poll_interval_seconds)
                            continue
                        raise _pdfx_failure_error(
                            error_message,
                            category="unknown_provider_failure",
                            boundary="submit",
                            public_message=PDFX_UNKNOWN_FAILURE_MESSAGE,
                            http_status=response.status,
                        )
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                if time.monotonic() < submit_deadline:
                    logger.warning(
                        "Transient PDF extraction submit network error (attempt %s): %s",
                        attempt,
                        type(exc).__name__,
                    )
                    await asyncio.sleep(self.poll_interval_seconds)
                    continue
                raise _pdfx_failure_error(
                    "PDF extraction submit network error "
                    f"type={type(exc).__name__}",
                    category="unknown_provider_failure",
                    boundary="submit",
                    public_message=PDFX_UNKNOWN_FAILURE_MESSAGE,
                ) from None

    async def _poll_until_complete(
        self,
        session: aiohttp.ClientSession,
        process_id: str,
        headers: Dict[str, str],
        progress_callback: Optional[ProgressCallback],
        cancel_requested_callback: Optional[CancelRequestedCallback] = None,
    ) -> Dict[str, Any]:
        """Poll extraction job until completion or failure."""
        status_endpoint = f"{self.service_url}/api/v1/extract/{process_id}"
        deadline = time.monotonic() + self.timeout_seconds
        latest_status = "pending"
        last_logged_signature = ""

        while True:
            if cancel_requested_callback and await cancel_requested_callback():
                await self._request_cancel(session, process_id, headers)
                raise PDFCancellationError(
                    f"PDF extraction cancelled by user request for process_id={process_id}"
                )

            if time.monotonic() >= deadline:
                raise _pdfx_failure_error(
                    f"PDF extraction timed out before completion for process_id={process_id}",
                    category="polling_timeout",
                    boundary="poll",
                    public_message=PDFX_POLLING_TIMEOUT_MESSAGE,
                    process_id=_safe_provider_token(process_id),
                    provider_status=_safe_provider_token(latest_status),
                )

            self._poll_attempt_count += 1
            async with session.get(status_endpoint, headers=headers) as response:
                body_text = await response.text()
                payload: Dict[str, Any] = {}
                try:
                    payload = json.loads(body_text)
                except json.JSONDecodeError:
                    # Proxy/load balancer may emit HTML for transient gateway errors.
                    if response.status in _TRANSIENT_HTTP_STATUS:
                        logger.warning(
                            "Transient non-JSON PDF extraction status response: HTTP %s",
                            response.status,
                        )
                        await asyncio.sleep(self.poll_interval_seconds)
                        continue
                    raise _pdfx_failure_error(
                        "PDF extraction status endpoint returned non-JSON response "
                        f"with HTTP {response.status}",
                        category="unknown_provider_failure",
                        boundary="poll",
                        public_message=PDFX_UNKNOWN_FAILURE_MESSAGE,
                        process_id=_safe_provider_token(process_id),
                        http_status=response.status,
                    )

                status = str(payload.get("status", "")).strip().lower()

                if response.status in _TRANSIENT_HTTP_STATUS and status not in {"failed", "failure"}:
                    logger.warning(
                        "Transient PDF extraction status error for process_id=%s: HTTP %s",
                        _safe_provider_token(process_id) or "unavailable",
                        response.status,
                    )
                    await asyncio.sleep(self.poll_interval_seconds)
                    continue

                if not status:
                    raise PDFParsingError("PDF extraction status payload missing 'status'")
                latest_status = status

                if status in {"failed", "failure"}:
                    provider_error_code = _safe_provider_token(payload.get("error_code"))
                    safe_process_id = _safe_provider_token(process_id)
                    code_suffix = (
                        f" error_code={provider_error_code}"
                        if provider_error_code
                        else ""
                    )
                    raise _pdfx_failure_error(
                        "PDF extraction failed"
                        + (
                            f" for process_id={safe_process_id}"
                            if safe_process_id
                            else ""
                        )
                        + f" status={status}{code_suffix}",
                        category="provider_terminal_failure",
                        boundary="poll",
                        public_message=PDFX_PROVIDER_FAILURE_MESSAGE,
                        process_id=safe_process_id,
                        provider_status=status,
                        provider_error_code=provider_error_code,
                        http_status=response.status,
                    )

                progress: Dict[str, Any] = (
                    payload["progress"] if isinstance(payload.get("progress"), dict) else {}
                )
                progress_stage = str(progress.get("stage", "")).strip()
                progress_percent = progress.get("percent")
                safe_progress_percent = (
                    progress_percent
                    if isinstance(progress_percent, (int, float))
                    else "-"
                )
                state = str(payload.get("state", "")).strip()
                safe_status = _safe_provider_token(status) or "unrecognized"
                safe_state = _safe_provider_token(state) or "-"
                safe_progress_stage = _safe_provider_token(progress_stage) or "-"
                signature = (
                    f"{safe_status}|{safe_state}|{safe_progress_stage}|{safe_progress_percent}"
                )
                if signature != last_logged_signature:
                    logger.info(
                        "PDFX status process_id=%s status=%s state=%s progress_stage=%s "
                        "progress_percent=%s",
                        _safe_provider_token(process_id) or "unavailable",
                        safe_status,
                        safe_state,
                        safe_progress_stage,
                        safe_progress_percent,
                    )
                    last_logged_signature = signature

                if progress_callback:
                    message = _build_progress_message(payload)
                    try:
                        await progress_callback(message)
                    except PDFCancellationError:
                        raise
                    except Exception:
                        logger.debug("Progress callback failed", exc_info=True)

                if status in {"complete", "succeeded", "success"}:
                    return payload

            await asyncio.sleep(self.poll_interval_seconds)

        raise PDFParsingError(
            "PDF extraction ended in an unexpected status for "
            f"process_id={_safe_provider_token(process_id) or 'unavailable'}"
        )

    async def _request_cancel(
        self,
        session: aiohttp.ClientSession,
        process_id: str,
        headers: Dict[str, str],
    ) -> None:
        """Best-effort request to terminate remote extraction job."""
        cancel_endpoint = f"{self.service_url}/api/v1/extract/{process_id}/cancel"
        payload = {"reason": "Cancelled by user request"}

        try:
            async with session.post(cancel_endpoint, json=payload, headers=headers) as response:
                await response.read()
                if response.status in {200, 202}:
                    logger.info(
                        "Requested remote extraction cancellation for process_id=%s",
                        _safe_provider_token(process_id) or "unavailable",
                    )
                    return
                if response.status in {404, 409}:
                    logger.info(
                        "Remote extraction cancellation returned HTTP %s for process_id=%s",
                        response.status,
                        _safe_provider_token(process_id) or "unavailable",
                    )
                    return
                logger.warning(
                    "Remote extraction cancellation failed for process_id=%s: HTTP %s",
                    _safe_provider_token(process_id) or "unavailable",
                    response.status,
                )
        except Exception as exc:
            logger.warning(
                "Remote extraction cancellation request failed for process_id=%s: %s",
                _safe_provider_token(process_id) or "unavailable",
                type(exc).__name__,
            )

    async def _download_markdown(
        self,
        session: aiohttp.ClientSession,
        process_id: str,
        headers: Dict[str, str],
    ) -> str:
        """Download configured markdown output for completed extraction."""
        raw = await self._download_artifact_bytes(
            session,
            process_id,
            headers,
            variant=self.download_variant,
        )
        try:
            markdown = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PDFParsingError(
                f"PDF extraction returned non-UTF-8 markdown for process_id={process_id}"
            ) from exc
        if not markdown.strip():
            raise PDFParsingError(
                f"PDF extraction returned empty markdown for process_id={process_id}"
            )
        return markdown

    async def _download_page_provenance(
        self,
        session: aiohttp.ClientSession,
        process_id: str,
        headers: Dict[str, str],
        *,
        merged_markdown: bytes,
    ) -> MergedPageProvenance:
        """Download and validate the sidecar for exact merged Markdown bytes."""
        raw = await self._download_artifact_bytes(
            session,
            process_id,
            headers,
            variant="page_provenance",
        )
        try:
            return parse_merged_page_provenance(raw, merged_markdown=merged_markdown)
        except ValueError as exc:
            raise PDFParsingError(
                f"PDF extraction page provenance is invalid for process_id={process_id}: {exc}"
            ) from exc

    async def _download_artifact_bytes(
        self,
        session: aiohttp.ClientSession,
        process_id: str,
        headers: Dict[str, str],
        *,
        variant: str,
    ) -> bytes:
        """Download exact artifact bytes with the existing retry policy."""
        download_endpoint = (
            f"{self.service_url}/api/v1/extract/{process_id}/download/{variant}"
        )
        download_deadline = time.monotonic() + self.download_retry_seconds
        attempt = 0

        while True:
            attempt += 1
            try:
                async with session.get(download_endpoint, headers=headers) as response:
                    body = await response.read()
                    if response.status == 200:
                        return body

                    error_message = (
                        f"PDF extraction {variant} download failed for "
                        f"process_id={_safe_provider_token(process_id) or 'unavailable'} "
                        f"with HTTP {response.status}"
                    )
                    if response.status in _TRANSIENT_HTTP_STATUS and time.monotonic() < download_deadline:
                        logger.warning(
                            "Transient PDF extraction %s download error for process_id=%s (attempt %s): %s",
                            variant,
                            _safe_provider_token(process_id) or "unavailable",
                            attempt,
                            error_message,
                        )
                        await asyncio.sleep(self.poll_interval_seconds)
                        continue
                    raise PDFParsingError(error_message)
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                if time.monotonic() < download_deadline:
                    logger.warning(
                        "Transient PDF extraction %s download network error for process_id=%s (attempt %s): %s",
                        variant,
                        _safe_provider_token(process_id) or "unavailable",
                        attempt,
                        type(exc).__name__,
                    )
                    await asyncio.sleep(self.poll_interval_seconds)
                    continue
                raise PDFParsingError(
                    "PDF extraction download network error "
                    f"type={type(exc).__name__}"
                ) from None

    async def _save_pdfx_json(self, result: Dict[str, Any], document_id: str, user_id: str) -> Path:
        """Save raw extraction response to user-specific directory."""
        from ...config import get_pdf_storage_path

        pdf_storage = get_pdf_storage_path()
        user_pdfx_path = ensure_writable_directory(pdf_storage / user_id / "pdfx_json")
        file_path = user_pdfx_path / f"{document_id}.json"

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: file_path.write_text(json.dumps(result, indent=2)))

        logger.info("Saved raw extraction JSON to %s", file_path)
        return file_path.relative_to(pdf_storage)

    async def _save_processed_json(self, elements: List[Dict[str, Any]], document_id: str, user_id: str) -> Path:
        """Save processed element JSON to user-specific directory."""
        from ...config import get_pdf_storage_path

        pdf_storage = get_pdf_storage_path()
        user_processed_path = ensure_writable_directory(pdf_storage / user_id / "processed_json")
        file_path = user_processed_path / f"{document_id}.json"

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: file_path.write_text(json.dumps(elements, indent=2)))

        logger.info("Saved processed JSON to %s", file_path)
        return file_path.relative_to(pdf_storage)


def _build_progress_message(payload: Dict[str, Any]) -> str:
    """Build a stable curator-facing message without provider-authored prose."""
    status = str(payload.get("status", "")).strip().lower()
    state = str(payload.get("state", "")).strip().lower()
    progress = payload.get("progress")
    percent: int | None = None
    if isinstance(progress, dict):
        raw_percent = progress.get("percent")
        if (
            isinstance(raw_percent, (int, float))
            and not isinstance(raw_percent, bool)
            and 0 <= raw_percent <= 100
        ):
            percent = int(raw_percent)

    if status in {"queued", "pending"}:
        if state in {"ready", "busy"}:
            return "PDF extraction queued; waiting for PDFX worker..."
        return "PDF extraction queued..."
    if status in {"warming", "warming_up"}:
        return "PDF extraction service is starting..."
    if status in {"started", "progress", "running"}:
        if percent is not None:
            return f"Extracting PDF content... ({percent}%)"
        return "Extracting PDF content..."
    if status in {"complete", "succeeded", "success"}:
        return "PDF extraction complete. Finalizing..."
    if status in {"failed", "failure"}:
        return "PDF extraction failed."
    return "PDF extraction in progress..."


def _markdown_lines_with_byte_starts(markdown: str) -> tuple[List[str], List[int]]:
    """Split CR/LF Markdown lines while retaining exact UTF-8 byte starts."""

    lines: List[str] = []
    starts: List[int] = []
    line_start = 0
    line_start_byte = 0
    cursor = 0
    while cursor < len(markdown):
        if markdown[cursor] not in {"\r", "\n"}:
            cursor += 1
            continue

        lines.append(markdown[line_start:cursor])
        starts.append(line_start_byte)
        newline_end = cursor + 1
        if (
            markdown[cursor] == "\r"
            and newline_end < len(markdown)
            and markdown[newline_end] == "\n"
        ):
            newline_end += 1
        line_start_byte += len(markdown[line_start:newline_end].encode("utf-8"))
        line_start = newline_end
        cursor = newline_end

    lines.append(markdown[line_start:])
    starts.append(line_start_byte)
    return lines, starts


def markdown_to_pipeline_elements(
    markdown: str,
    *,
    page_provenance: Optional[MergedPageProvenance] = None,
) -> List[Dict[str, Any]]:
    """Convert merged markdown output into pipeline element dictionaries."""
    lines, line_byte_starts = _markdown_lines_with_byte_starts(markdown)
    elements: List[Dict[str, Any]] = []
    section_path: List[str] = []
    current_page = 1
    index = 0
    i = 0

    heading_re = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
    list_re = re.compile(r"^\s*([-*+]|\d+[.)])\s+(.+)$")
    page_markers = [
        re.compile(r"^<!--\s*page\s*[:=]?\s*(\d+)\s*-->$", re.IGNORECASE),
        re.compile(r"^\[\s*page\s+(\d+)\s*\]$", re.IGNORECASE),
    ]

    def add_element(
        element_type: str,
        text: str,
        content_type: str,
        original_type: str,
        line_index: int,
    ) -> None:
        nonlocal index
        clean_text = normalize_text(text.strip())
        if not clean_text:
            return
        normalized_section_path = normalize_section_path(section_path)
        active_section = normalized_section_path[-1] if normalized_section_path else None
        doc_item_label = {
            "Title": "section_header",
            "ListItem": "list_item",
            "Table": "table",
        }.get(element_type, "paragraph")
        page_number = current_page
        if page_provenance is not None:
            raw_line = lines[line_index]
            leading_characters = len(raw_line) - len(raw_line.lstrip())
            first_content_byte = line_byte_starts[line_index] + len(
                raw_line[:leading_characters].encode("utf-8")
            )
            page_number = page_provenance.page_for_byte_offset(first_content_byte)
        metadata = {
            "element_id": f"md_element_{index}",
            "doc_item_label": doc_item_label,
            "section_title": active_section,
            "section_path": normalized_section_path,
            "hierarchy_level": len(section_path) if section_path else 1,
            "page_number": page_number,
            "content_type": content_type,
            "original_type": original_type,
        }
        elements.append(
            {
                "index": index,
                "type": element_type,
                "text": clean_text,
                "metadata": metadata,
            }
        )
        index += 1

    while i < len(lines):
        raw_line = lines[i]
        stripped = raw_line.strip()
        if not stripped:
            i += 1
            continue

        matched_page_marker = False
        for pattern in page_markers:
            marker_match = pattern.match(stripped)
            if marker_match:
                current_page = max(1, int(marker_match.group(1)))
                matched_page_marker = True
                break
        if matched_page_marker:
            i += 1
            continue

        heading_match = heading_re.match(stripped)
        if heading_match:
            level = len(heading_match.group(1))
            title = normalize_text(heading_match.group(2).strip())
            section_path = section_path[: level - 1]
            if title:
                section_path.append(title)
                add_element("Title", title, "heading", "markdown_heading", i)
            i += 1
            continue

        if stripped.startswith("|"):
            element_start = i
            table_lines = [stripped]
            i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            add_element(
                "Table",
                "\n".join(table_lines),
                "table",
                "markdown_table",
                element_start,
            )
            continue

        if stripped.startswith("```"):
            element_start = i
            code_lines = [stripped]
            i += 1
            while i < len(lines):
                code_lines.append(lines[i])
                if lines[i].strip().startswith("```"):
                    i += 1
                    break
                i += 1
            add_element(
                "NarrativeText",
                "\n".join(code_lines),
                "code_block",
                "markdown_code_block",
                element_start,
            )
            continue

        list_match = list_re.match(raw_line)
        if list_match:
            add_element("ListItem", stripped, "list_item", "markdown_list_item", i)
            i += 1
            continue

        element_start = i
        paragraph_lines = [stripped]
        i += 1
        while i < len(lines):
            peek = lines[i].strip()
            if not peek:
                i += 1
                break
            if heading_re.match(peek) or peek.startswith("|") or peek.startswith("```") or list_re.match(lines[i]):
                break
            paragraph_lines.append(peek)
            i += 1
        add_element(
            "NarrativeText",
            " ".join(paragraph_lines),
            "paragraph",
            "markdown_paragraph",
            element_start,
        )

    return elements


async def parse_pdf_document(
    file_path: Path,
    document_id: str,
    user_id: str,
    extraction_strategy: Optional[str] = None,
    enable_table_extraction: Optional[bool] = None,
    progress_callback: Optional[ProgressCallback] = None,
    process_id_callback: Optional[ProcessIdCallback] = None,
    cancel_requested_callback: Optional[CancelRequestedCallback] = None,
    observability_callback: Optional[ObservabilityCallback] = None,
) -> Dict[str, Any]:
    """Parse PDF document using AGR PDF extraction service."""
    parser = PDFXParser()
    return await parser.parse_pdf_document(
        file_path=file_path,
        document_id=document_id,
        user_id=user_id,
        extraction_strategy=extraction_strategy,
        enable_table_extraction=enable_table_extraction,
        progress_callback=progress_callback,
        process_id_callback=process_id_callback,
        cancel_requested_callback=cancel_requested_callback,
        observability_callback=observability_callback,
    )


def validate_pdf_file(file_path: Path) -> Dict[str, Any]:
    """Validate PDF file before parsing."""
    validation = {
        "is_valid": True,
        "file_exists": False,
        "is_pdf": False,
        "file_size": 0,
        "errors": [],
    }

    if not file_path.exists():
        validation["is_valid"] = False
        validation["errors"].append(f"File not found: {file_path}")
        return validation

    validation["file_exists"] = True

    if file_path.suffix.lower() != ".pdf":
        validation["is_valid"] = False
        validation["errors"].append(f"Not a PDF file: {file_path.suffix}")
    else:
        validation["is_pdf"] = True

    file_size = file_path.stat().st_size
    validation["file_size"] = file_size

    if file_size == 0:
        validation["is_valid"] = False
        validation["errors"].append("File is empty")
    elif file_size > MAX_PDF_FILE_SIZE_BYTES:
        validation["is_valid"] = False
        validation["errors"].append(pdf_file_size_limit_message(file_size))

    try:
        with open(file_path, "rb") as file_handle:
            header = file_handle.read(5)
            if header != b"%PDF-":
                validation["is_valid"] = False
                validation["errors"].append("Invalid PDF header - file may be corrupted")
    except Exception as exc:
        validation["is_valid"] = False
        validation["errors"].append(f"Cannot read file: {exc}")

    return validation


def handle_parsing_errors(error: Exception) -> None:
    """Handle and log parsing errors."""
    error_message = str(error)

    if "timeout" in error_message.lower():
        logger.warning("PDF extraction service timed out. Check service health or increase timeout.")
    elif "network" in error_message.lower():
        logger.error("Network error accessing PDF extraction service. Check connectivity and service status.")
    elif "service error" in error_message.lower():
        logger.error("PDF extraction service returned an error. Check service logs.")
    else:
        logger.error("Unhandled parsing error: %s", error_message)


def get_extraction_strategy() -> str:
    """Get PDF extraction strategy from environment."""
    return os.getenv("PDF_EXTRACTION_STRATEGY", "auto")


def validate_extraction_strategy(strategy: str) -> None:
    """Validate extraction strategy."""
    valid_strategies = ["fast", "auto", "hi_res"]
    if strategy not in valid_strategies:
        raise ConfigurationError(f"Invalid extraction strategy: {strategy}. Must be one of {valid_strategies}")


def is_table_extraction_enabled() -> bool:
    """Check if table extraction is enabled."""
    value = os.getenv("ENABLE_TABLE_EXTRACTION", "false")
    return value.lower() in _TRUE_VALUES
