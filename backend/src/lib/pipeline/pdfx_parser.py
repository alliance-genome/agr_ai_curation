"""PDFX parser client for AGR PDF extraction service."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiohttp

from ..exceptions import ConfigurationError, PDFCancellationError, PDFParsingError
from ...schemas.pdfx_schema import (  # noqa: F401 - re-exported for fixture tooling
    PDFXResponse,
    build_pipeline_elements,
    normalize_elements,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]
ProcessIdCallback = Callable[[str], Awaitable[None]]
CancelRequestedCallback = Callable[[], Awaitable[bool]]

_TRUE_VALUES = {"1", "true", "yes", "on"}
_TRANSIENT_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}


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

        logger.info(
            "Initialized PDF extraction parser service=%s timeout=%ss poll_interval=%ss methods=%s merge=%s auth_mode=%s",
            self.service_url,
            self.timeout_seconds,
            self.poll_interval_seconds,
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
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = await self._build_auth_headers(session)
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

                status_payload = await self._poll_until_complete(
                    session=session,
                    process_id=process_id,
                    headers=headers,
                    progress_callback=progress_callback,
                    cancel_requested_callback=cancel_requested_callback,
                )
                merged_markdown = await self._download_markdown(session, process_id, headers)
        except asyncio.TimeoutError as exc:
            raise PDFParsingError(f"PDF extraction timeout after {self.timeout_seconds} seconds") from exc
        except aiohttp.ClientError as exc:
            raise PDFParsingError(f"Network error calling PDF extraction service: {exc}") from exc

        # The live parser currently rebuilds elements from downloaded markdown.
        # Page markers survive this path, but bbox provenance is not synthesized
        # from markdown, so downstream localization is page-only unless another
        # structured pipeline injects provenance metadata.
        cleaned_elements = markdown_to_pipeline_elements(merged_markdown)
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
        """Submit extraction request and return service response payload."""
        extract_endpoint = f"{self.service_url}/api/v1/extract"
        submit_deadline = time.monotonic() + self.timeout_seconds
        attempt = 0

        while True:
            attempt += 1
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

                        error_message = f"PDF extraction submit failed: {response.status} - {body_text}"
                        if response.status in _TRANSIENT_HTTP_STATUS and time.monotonic() < submit_deadline:
                            logger.warning(
                                "Transient PDF extraction submit error (attempt %s): %s",
                                attempt,
                                error_message,
                            )
                            await asyncio.sleep(self.poll_interval_seconds)
                            continue
                        raise PDFParsingError(error_message)
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                if time.monotonic() < submit_deadline:
                    logger.warning(
                        "Transient PDF extraction submit network error (attempt %s): %s",
                        attempt,
                        exc,
                    )
                    await asyncio.sleep(self.poll_interval_seconds)
                    continue
                raise PDFParsingError(f"Network error calling PDF extraction service: {exc}") from exc

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

        while True:
            if cancel_requested_callback and await cancel_requested_callback():
                await self._request_cancel(session, process_id, headers)
                raise PDFCancellationError(
                    f"PDF extraction cancelled by user request for process_id={process_id}"
                )

            if time.monotonic() >= deadline:
                raise PDFParsingError(
                    f"PDF extraction timed out before completion for process_id={process_id}"
                )

            async with session.get(status_endpoint, headers=headers) as response:
                body_text = await response.text()
                payload: Dict[str, Any] = {}
                try:
                    payload = json.loads(body_text)
                except json.JSONDecodeError:
                    # Proxy/load balancer may emit HTML for transient gateway errors.
                    if response.status in _TRANSIENT_HTTP_STATUS:
                        logger.warning(
                            "Transient non-JSON PDF extraction status response: %s - %s",
                            response.status,
                            body_text[:200],
                        )
                        await asyncio.sleep(self.poll_interval_seconds)
                        continue
                    raise PDFParsingError(
                        f"PDF extraction status endpoint returned non-JSON response: {body_text[:200]}"
                    )

                status = str(payload.get("status", "")).strip().lower()

                if response.status in _TRANSIENT_HTTP_STATUS and status not in {"failed", "failure"}:
                    logger.warning(
                        "Transient PDF extraction status error for process_id=%s: %s - %s",
                        process_id,
                        response.status,
                        body_text[:200],
                    )
                    await asyncio.sleep(self.poll_interval_seconds)
                    continue

                if not status:
                    raise PDFParsingError("PDF extraction status payload missing 'status'")
                latest_status = status

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
                if status in {"failed", "failure"}:
                    error = payload.get("error") or payload.get("detail") or "Unknown extraction failure"
                    raise PDFParsingError(f"PDF extraction failed for process_id={process_id}: {error}")

            await asyncio.sleep(self.poll_interval_seconds)

        raise PDFParsingError(
            f"PDF extraction ended in unexpected status '{latest_status}' for process_id={process_id}"
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
                body_text = await response.text()
                if response.status in {200, 202}:
                    logger.info("Requested remote extraction cancellation for process_id=%s", process_id)
                    return
                if response.status in {404, 409}:
                    logger.info(
                        "Remote extraction cancellation returned %s for process_id=%s: %s",
                        response.status,
                        process_id,
                        body_text[:200],
                    )
                    return
                logger.warning(
                    "Remote extraction cancellation failed for process_id=%s: %s - %s",
                    process_id,
                    response.status,
                    body_text[:200],
                )
        except Exception as exc:
            logger.warning("Remote extraction cancellation request failed for process_id=%s: %s", process_id, exc)

    async def _download_markdown(
        self,
        session: aiohttp.ClientSession,
        process_id: str,
        headers: Dict[str, str],
    ) -> str:
        """Download configured markdown output for completed extraction."""
        download_endpoint = (
            f"{self.service_url}/api/v1/extract/{process_id}/download/{self.download_variant}"
        )
        async with session.get(download_endpoint, headers=headers) as response:
            body_text = await response.text()
            if response.status != 200:
                raise PDFParsingError(
                    f"PDF extraction {self.download_variant} download failed. "
                    f"Expected GET {download_endpoint} -> 200, got {response.status}: {body_text}"
                )

        markdown = body_text.strip()
        if not markdown:
            raise PDFParsingError(
                f"PDF extraction returned empty markdown for process_id={process_id}"
            )
        return markdown

    async def _save_pdfx_json(self, result: Dict[str, Any], document_id: str, user_id: str) -> Path:
        """Save raw extraction response to user-specific directory."""
        from ...config import get_pdf_storage_path

        pdf_storage = get_pdf_storage_path()
        user_pdfx_path = pdf_storage / user_id / "pdfx_json"
        user_pdfx_path.mkdir(parents=True, exist_ok=True)
        file_path = user_pdfx_path / f"{document_id}.json"

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: file_path.write_text(json.dumps(result, indent=2)))

        logger.info("Saved raw extraction JSON to %s", file_path)
        return file_path.relative_to(pdf_storage)

    async def _save_processed_json(self, elements: List[Dict[str, Any]], document_id: str, user_id: str) -> Path:
        """Save processed element JSON to user-specific directory."""
        from ...config import get_pdf_storage_path

        pdf_storage = get_pdf_storage_path()
        user_processed_path = pdf_storage / user_id / "processed_json"
        user_processed_path.mkdir(parents=True, exist_ok=True)
        file_path = user_processed_path / f"{document_id}.json"

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: file_path.write_text(json.dumps(elements, indent=2)))

        logger.info("Saved processed JSON to %s", file_path)
        return file_path.relative_to(pdf_storage)


def _build_progress_message(payload: Dict[str, Any]) -> str:
    """Build curator-facing progress message from extraction status payload."""
    status = str(payload.get("status", "")).strip().lower()
    progress = payload.get("progress")
    if isinstance(progress, dict):
        stage_display = str(progress.get("stage_display", "")).strip()
        stage_name = str(progress.get("stage", "")).strip()
        percent = progress.get("percent")
        if stage_display:
            if isinstance(percent, (int, float)):
                return f"PDF extraction: {stage_display} ({int(percent)}%)"
            return f"PDF extraction: {stage_display}"
        if stage_name:
            if isinstance(percent, (int, float)):
                return f"PDF extraction: {stage_name} ({int(percent)}%)"
            return f"PDF extraction: {stage_name}"

    if status in {"queued", "pending"}:
        return "PDF extraction queued..."
    if status in {"started", "progress", "running"}:
        return "Extracting PDF content..."
    if status in {"complete", "succeeded", "success"}:
        return "PDF extraction complete. Finalizing..."
    if status in {"failed", "failure"}:
        return "PDF extraction failed."
    return "PDF extraction in progress..."


def markdown_to_pipeline_elements(markdown: str) -> List[Dict[str, Any]]:
    """Convert merged markdown output into pipeline elements without bbox synthesis."""
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
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

    def add_element(element_type: str, text: str, content_type: str, original_type: str) -> None:
        nonlocal index
        clean_text = text.strip()
        if not clean_text:
            return
        active_section = section_path[-1] if section_path else None
        doc_item_label = {
            "Title": "section_header",
            "ListItem": "list_item",
            "Table": "table",
        }.get(element_type, "paragraph")
        metadata = {
            "element_id": f"md_element_{index}",
            "doc_item_label": doc_item_label,
            "section_title": active_section,
            "section_path": list(section_path),
            "hierarchy_level": len(section_path) if section_path else 1,
            "page_number": current_page,
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
            title = heading_match.group(2).strip()
            section_path = section_path[: level - 1]
            section_path.append(title)
            add_element("Title", title, "heading", "markdown_heading")
            i += 1
            continue

        if stripped.startswith("|"):
            table_lines = [stripped]
            i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            add_element("Table", "\n".join(table_lines), "table", "markdown_table")
            continue

        if stripped.startswith("```"):
            code_lines = [stripped]
            i += 1
            while i < len(lines):
                code_lines.append(lines[i])
                if lines[i].strip().startswith("```"):
                    i += 1
                    break
                i += 1
            add_element("NarrativeText", "\n".join(code_lines), "code_block", "markdown_code_block")
            continue

        list_match = list_re.match(raw_line)
        if list_match:
            add_element("ListItem", stripped, "list_item", "markdown_list_item")
            i += 1
            continue

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
        add_element("NarrativeText", " ".join(paragraph_lines), "paragraph", "markdown_paragraph")

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
    elif file_size > 100 * 1024 * 1024:
        validation["errors"].append("File exceeds 100MB limit - parsing may be slow")

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
