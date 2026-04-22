#!/usr/bin/env python3
"""Run a staged dev-release smoke against a deployed AI Curation backend.

This harness hits the deployed HTTP API surface instead of in-process fixtures.
By default it exercises the same high-risk paths we care about before a release.
It also supports partial slice runs via skip flags while the deeper stages are
still being implemented:
1. backend health
2. PDF extraction readiness and wake
3. upload + processing + download-info for a real PDF
4. loaded-document chat with a real OpenAI-backed answer
5. custom-agent creation + flow creation + real flow execution over SSE
6. batch flow validation + two-document batch execution + ZIP download
7. optional local rerank-provider smoke across bedrock/local/none modes
8. best-effort cleanup and evidence JSON output
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import mimetypes
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from uuid import uuid4


DEFAULT_CHAT_MESSAGE = (
    "What genes are the focus of the publication?"
)
DEFAULT_FLOW_QUERY = (
    "Extract the experimentally supported genes from the loaded paper, include the organism, and preserve verified evidence."
)
DEFAULT_FLOW_MODEL = "gpt-5.4-nano"
DEFAULT_CHAT_MODEL = "gpt-5.4"
DEFAULT_SPECIALIST_MODEL = "gpt-5.4-nano"
DEFAULT_WORKSPACE_ADAPTER_KEY = "gene"
DEFAULT_SHARED_SAMPLE_PDF = Path(
    "/home/ctabone/analysis/alliance/ai_curation_new/agr_ai_curation/sample_fly_publication.pdf"
)
KNOWN_CHAT_FAILURE_SNIPPETS = (
    "no document is currently loaded",
    "i don't have access to the document",
    "not authenticated",
    "invalid authentication token",
    "document retrieval failed",
    "document extraction step failed",
    "pdf extraction step failed",
    "document-extraction step failed",
    "required verified evidence records",
    "missing verified evidence records",
    "without the required verified evidence records",
    "lacked the required verified evidence records",
    "couldn't summarize the loaded paper",
    "couldn’t summarize the loaded paper",
)
REQUIRED_FOCUS_GENE_SNIPPETS = (
    "crb",
    "crumbs",
)


class SmokeFailure(RuntimeError):
    """Raised when the smoke run fails."""


@dataclass
class Response:
    status_code: int
    body: bytes
    text: str
    json_body: Optional[Any]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def load_dotenv_value(env_path: Path, key: str) -> Optional[str]:
    if not env_path.exists():
        return None

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return _strip_quotes(value)
    return None


def decode_json(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except Exception:
        return None


def build_headers(api_key: Optional[str]) -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "agr-ai-curation-dev-release-smoke/2.0",
    }
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def print_step(message: str) -> None:
    print(f"[dev-release-smoke] {message}", flush=True)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def require_chat_answer_ok(answer: str, *, context: str, raw_details: Any) -> None:
    cleaned = answer.strip()
    require(cleaned, f"{context} was empty: {raw_details}")
    require(len(cleaned) >= 20, f"{context} was unexpectedly short: {raw_details}")
    lowered = cleaned.lower()
    for snippet in KNOWN_CHAT_FAILURE_SNIPPETS:
        require(snippet not in lowered, f"{context} indicates failure: {raw_details}")


def require_model_looks_expected(model_blob: str, *, expected_model: str, context: str) -> None:
    cleaned_blob = model_blob.strip()
    require(cleaned_blob, f"{context} did not expose model information")
    lowered_blob = cleaned_blob.lower()
    lowered_expected = expected_model.strip().lower()
    expected_suffix = lowered_expected.split("/")[-1]
    require(
        lowered_expected in lowered_blob or expected_suffix in lowered_blob,
        f"{context} model did not match {expected_model!r}: {model_blob!r}",
    )


def require_text_contains_any_snippet(
    text: str,
    *,
    snippets: Iterable[str],
    context: str,
    raw_details: Any,
) -> None:
    lowered = text.strip().lower()
    require(lowered, f"{context} was empty: {raw_details}")
    require(
        any(snippet.lower() in lowered for snippet in snippets),
        f"{context} did not mention any of {list(snippets)!r}: {raw_details}",
    )


def http_request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    data: Optional[bytes] = None,
    timeout: float = 30.0,
) -> Response:
    request_headers = dict(headers or {})
    body = data
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(url, data=body, headers=request_headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body = response.read()
            text = raw_body.decode("utf-8", errors="replace")
            return Response(
                status_code=response.status,
                body=raw_body,
                text=text,
                json_body=decode_json(text),
            )
    except urllib.error.HTTPError as exc:
        raw_body = exc.read()
        text = raw_body.decode("utf-8", errors="replace")
        return Response(
            status_code=exc.code,
            body=raw_body,
            text=text,
            json_body=decode_json(text),
        )
    except urllib.error.URLError as exc:
        raise SmokeFailure(f"{method} {url} failed: {exc}") from exc


def encode_multipart_form(fields: Dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----agr-ai-curation-smoke-{uuid4().hex}"
    lines: list[bytes] = []

    for key, value in fields.items():
        lines.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    file_bytes = file_path.read_bytes()
    lines.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{file_path.name}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(lines), boundary


def append_check(
    checks: list[Dict[str, Any]],
    *,
    step: str,
    ok: bool,
    status_code: int,
    payload: Any,
) -> None:
    checks.append(
        {
            "step": step,
            "ok": ok,
            "status_code": status_code,
            "payload": payload,
        }
    )


def compute_scope_limitations(args: argparse.Namespace) -> list[str]:
    limitations: list[str] = []
    for name, enabled in (
        ("provider_health", args.skip_provider_health),
        ("user_info", args.skip_user_info),
        ("chat", args.skip_chat),
        ("flow", args.skip_flow),
        ("workspace", args.skip_workspace),
        ("batch", args.skip_batch),
        ("rerank_provider_smoke", not args.include_rerank_provider_smoke),
        ("dev_mode_fallback", args.allow_dev_mode_fallback),
        ("duplicate_reuse", args.allow_duplicate_reuse),
    ):
        if enabled:
            limitations.append(name)
    return limitations


def require_safe_fixture_deletion_principal(current_user: Dict[str, Any]) -> None:
    principal_fields = [
        str(current_user.get("auth_sub", "")).strip(),
        str(current_user.get("sub", "")).strip(),
        str(current_user.get("email", "")).strip(),
        str(current_user.get("user_id", "")).strip(),
    ]
    principal_blob = " ".join(value.lower() for value in principal_fields if value)
    require(
        any(token in principal_blob for token in ("test", "smoke")),
        "--delete-existing-sample-documents requires a dedicated test/smoke principal. "
        f"Current principal did not look safe for destructive fixture cleanup: {current_user}",
    )


def apply_cleanup_failures_to_evidence(evidence: Dict[str, Any]) -> None:
    if evidence.get("overall_status") != "pass":
        return

    checks = evidence.get("checks")
    if not isinstance(checks, list):
        return

    cleanup_failures = [
        check
        for check in checks
        if isinstance(check, dict)
        and str(check.get("step", "")).startswith("cleanup_")
        and check.get("ok") is not True
    ]
    if not cleanup_failures:
        return

    evidence["overall_status"] = "fail"
    evidence["cleanup_failures"] = cleanup_failures
    if not evidence.get("error"):
        failed_steps = ", ".join(str(check.get("step", "cleanup")) for check in cleanup_failures)
        evidence["error"] = f"Cleanup failed for one or more resources: {failed_steps}"


def resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_rerank_provider_smoke_script(path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = resolve_repo_root() / candidate
    return candidate.resolve()


def extract_evidence_path_from_output(output: str) -> Optional[Path]:
    match = re.search(r"Evidence file:\s*(?P<path>\S+)", output)
    if not match:
        return None
    candidate = Path(match.group("path")).expanduser()
    if not candidate.is_absolute():
        candidate = resolve_repo_root() / candidate
    return candidate.resolve()


def run_local_rerank_provider_smoke(
    *,
    script_path: Path,
    base_url: str,
) -> Dict[str, Any]:
    require(script_path.exists(), f"Rerank provider smoke script not found: {script_path}")

    result = subprocess.run(
        ["bash", str(script_path), base_url],
        cwd=resolve_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )
    output_blob = "\n".join(
        part for part in (result.stdout.strip(), result.stderr.strip()) if part
    )
    evidence_path = extract_evidence_path_from_output(output_blob)

    require(
        result.returncode == 0,
        (
            "Local rerank provider smoke failed "
            f"(exit={result.returncode}). Output: {output_blob[-1500:]}"
        ),
    )
    require(evidence_path is not None, f"Rerank provider smoke did not report an evidence file: {output_blob}")
    require(evidence_path.exists(), f"Rerank provider smoke evidence file was missing: {evidence_path}")

    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    require(
        isinstance(payload, dict),
        f"Rerank provider smoke evidence was not a JSON object: {evidence_path}",
    )
    require(
        payload.get("overall_status") == "pass",
        f"Rerank provider smoke reported failure: {payload}",
    )

    return {
        "script_path": str(script_path),
        "base_url": base_url,
        "evidence_file": str(evidence_path),
        "overall_status": payload.get("overall_status"),
        "pass_count": payload.get("pass_count"),
        "fail_count": payload.get("fail_count"),
        "derived_checks": payload.get("derived_checks"),
    }


def require_pdfx_release_health(payload: Dict[str, Any], *, context: str) -> None:
    status = str(payload.get("status", "")).strip().lower()
    error = payload.get("error")
    require(
        status == "healthy",
        (
            f"PDF extraction health is not release-ready during {context}: "
            f"status={status!r}, error={error!r}, status_error={payload.get('status_error')!r}, "
            f"worker_state={payload.get('worker_state')!r}"
        ),
    )
    require(
        not error,
        (
            f"PDF extraction health surfaced an error during {context}: "
            f"error={error!r}, status_error={payload.get('status_error')!r}"
        ),
    )


def extract_existing_document_id(payload: Any) -> Optional[str]:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict):
            existing_id = detail.get("existing_document_id")
            if isinstance(existing_id, str) and existing_id.strip():
                return existing_id.strip()
    return None


def collect_error_events(events: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    return [
        event
        for event in events
        if str(event.get("type", "")).strip().upper().endswith("_ERROR")
    ]


def parse_sse_events(text: str) -> list[Dict[str, Any]]:
    events: list[Dict[str, Any]] = []
    for raw_line in text.splitlines():
        if not raw_line.startswith("data: "):
            continue
        payload = raw_line[6:].strip()
        if not payload:
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SmokeFailure(f"Invalid SSE payload: {payload}") from exc
        if isinstance(event, dict):
            events.append(event)
    return events


def build_default_fixture_candidates() -> list[Path]:
    repo_root = Path.cwd()
    fixtures_root = repo_root / "backend" / "tests" / "fixtures"
    configured_sample_pdf = os.getenv("AGR_SMOKE_SAMPLE_PDF", "").strip()
    candidates: list[Path] = []
    if configured_sample_pdf:
        candidates.append(Path(configured_sample_pdf).expanduser())

    candidates.extend(
        [
            DEFAULT_SHARED_SAMPLE_PDF,
            repo_root / "sample_fly_publication.pdf",
            fixtures_root / "sample_fly_publication.pdf",
            fixtures_root / "micropub-biology-001725.pdf",
            fixtures_root / "live_tiny_chat.pdf",
        ]
    )

    deduped_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate.expanduser())
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped_candidates.append(candidate)
    return deduped_candidates


def compute_file_content_fingerprint(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return f"{path.stat().st_size}:{hasher.hexdigest()}"


def resolve_sample_pdf(explicit_path: Optional[str]) -> Path:
    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        require(path.exists(), f"Sample PDF not found: {path}")
        return path

    for candidate in build_default_fixture_candidates():
        if candidate.exists():
            return candidate.resolve()

    raise SmokeFailure("No sample PDF found under backend/tests/fixtures/")


def resolve_secondary_pdf(primary_pdf: Path, explicit_path: Optional[str]) -> Path:
    primary_fingerprint = compute_file_content_fingerprint(primary_pdf)

    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        require(path.exists(), f"Secondary sample PDF not found: {path}")
        require(path != primary_pdf, "Secondary sample PDF must differ from the primary sample PDF")
        require(
            compute_file_content_fingerprint(path) != primary_fingerprint,
            "Secondary sample PDF must differ in content from the primary sample PDF",
        )
        return path

    candidates = [candidate.resolve() for candidate in build_default_fixture_candidates() if candidate.exists()]
    for candidate in candidates:
        if candidate == primary_pdf:
            continue
        if compute_file_content_fingerprint(candidate) == primary_fingerprint:
            continue
        if candidate.name == primary_pdf.name:
            # Prefer clearly distinct fixtures when multiple samples exist.
            continue
        return candidate

    for candidate in candidates:
        if candidate == primary_pdf:
            continue
        if compute_file_content_fingerprint(candidate) == primary_fingerprint:
            continue
        return candidate

    raise SmokeFailure(
        "Could not find a second distinct sample PDF. Pass --secondary-pdf to supply one explicitly."
    )


def resolve_api_key(explicit_api_key: Optional[str], env_file: Path) -> Optional[str]:
    if explicit_api_key:
        return explicit_api_key
    env_api_key = os.getenv("TESTING_API_KEY", "").strip()
    if env_api_key:
        return env_api_key
    return load_dotenv_value(env_file, "TESTING_API_KEY")


def resolve_env_value(key: str, env_file: Path) -> Optional[str]:
    value = os.getenv(key, "").strip()
    if value:
        return value
    return load_dotenv_value(env_file, key)


def verify_api_key_mode(api_key: Optional[str], *, allow_dev_mode_fallback: bool) -> None:
    if api_key:
        return
    if allow_dev_mode_fallback:
        print_step(
            "No TESTING_API_KEY available; using DEV_MODE fallback because "
            "--allow-dev-mode-fallback was provided."
        )
        return
    raise SmokeFailure(
        "Release smoke requires API-key auth. Set TESTING_API_KEY or pass --api-key. "
        "Use --allow-dev-mode-fallback only for local debugging."
    )


def check_llm_provider_health(
    *,
    base_url: str,
    headers: Dict[str, str],
    checks: list[Dict[str, Any]],
) -> Dict[str, Any]:
    response = http_request("GET", f"{base_url}/api/admin/health/llm-providers", headers=headers, timeout=20.0)
    require(
        response.status_code == 200 and isinstance(response.json_body, dict),
        f"Unexpected LLM provider health response: {response.status_code} {response.text}",
    )
    require(
        str(response.json_body.get("status", "")).lower() != "unhealthy",
        f"LLM provider health is unhealthy: {response.text}",
    )
    require(
        not response.json_body.get("errors"),
        f"LLM provider health reported errors: {response.text}",
    )
    append_check(
        checks,
        step="llm_provider_health",
        ok=True,
        status_code=response.status_code,
        payload=response.json_body,
    )
    return response.json_body


def check_current_user(
    *,
    base_url: str,
    headers: Dict[str, str],
    checks: list[Dict[str, Any]],
    expected_auth_sub: Optional[str],
    expected_email: Optional[str],
) -> Dict[str, Any]:
    response = http_request("GET", f"{base_url}/api/users/me", headers=headers, timeout=20.0)
    require(
        response.status_code == 200 and isinstance(response.json_body, dict),
        f"Unexpected current-user response: {response.status_code} {response.text}",
    )
    actual_auth_sub = str(response.json_body.get("auth_sub", "")).strip()
    actual_email = str(response.json_body.get("email", "")).strip()
    require(
        actual_auth_sub.startswith("api-key-"),
        (
            "Current-user auth_sub does not look like an API-key principal. "
            f"Got {actual_auth_sub!r}. This can indicate a DEV_MODE fallback false-pass."
        ),
    )
    require(
        actual_email and actual_email != "dev@localhost",
        (
            "Current-user email does not look like an API-key principal. "
            f"Got {actual_email!r}. This can indicate a DEV_MODE fallback false-pass."
        ),
    )
    if expected_auth_sub:
        require(
            actual_auth_sub == expected_auth_sub,
            (
                "Current-user auth_sub did not match the API-key principal. "
                f"Expected {expected_auth_sub!r}, got {actual_auth_sub!r}. "
                "This can indicate a DEV_MODE fallback false-pass."
            ),
        )
    if expected_email:
        require(
            actual_email == expected_email,
            (
                "Current-user email did not match the API-key principal. "
                f"Expected {expected_email!r}, got {actual_email!r}. "
                "This can indicate a DEV_MODE fallback false-pass."
            ),
        )
    append_check(
        checks,
        step="current_user",
        ok=True,
        status_code=response.status_code,
        payload=response.json_body,
    )
    return response.json_body


def ensure_worker_ready(
    *,
    base_url: str,
    headers: Dict[str, str],
    wake_timeout_seconds: float,
    poll_interval_seconds: float,
    checks: list[Dict[str, Any]],
) -> Dict[str, Any]:
    health_url = f"{base_url}/weaviate/documents/pdf-extraction-health"
    wake_url = f"{base_url}/weaviate/documents/pdf-extraction-wake"

    health_response = http_request("GET", health_url, headers=headers, timeout=20.0)
    require(
        health_response.status_code == 200 and isinstance(health_response.json_body, dict),
        f"Unexpected PDF extraction health response: {health_response.status_code} {health_response.text}",
    )
    append_check(
        checks,
        step="pdf_extraction_health_initial",
        ok=(
            str(health_response.json_body.get("status", "")).strip().lower() == "healthy"
            and not health_response.json_body.get("error")
        ),
        status_code=health_response.status_code,
        payload=health_response.json_body,
    )

    payload = health_response.json_body
    require_pdfx_release_health(payload, context="initial PDF extraction preflight")
    if payload.get("worker_available"):
        return payload

    wake_response = http_request("POST", wake_url, headers=headers, timeout=20.0)
    require(
        wake_response.status_code == 200 and isinstance(wake_response.json_body, dict),
        f"Unexpected PDF extraction wake response: {wake_response.status_code} {wake_response.text}",
    )
    append_check(
        checks,
        step="pdf_extraction_wake",
        ok=True,
        status_code=wake_response.status_code,
        payload=wake_response.json_body,
    )

    deadline = time.monotonic() + wake_timeout_seconds
    last_payload = payload
    while time.monotonic() < deadline:
        time.sleep(poll_interval_seconds)
        poll_response = http_request("GET", health_url, headers=headers, timeout=20.0)
        require(
            poll_response.status_code == 200 and isinstance(poll_response.json_body, dict),
            f"Unexpected PDF extraction health poll response: {poll_response.status_code} {poll_response.text}",
        )
        last_payload = poll_response.json_body
        require_pdfx_release_health(last_payload, context="PDF extraction wake poll")
        if last_payload.get("worker_available"):
            append_check(
                checks,
                step="pdf_extraction_worker_ready",
                ok=True,
                status_code=poll_response.status_code,
                payload=last_payload,
            )
            return last_payload

    raise SmokeFailure(
        "PDF extraction worker did not become ready in time; "
        f"last health payload: {json.dumps(last_payload, sort_keys=True)}"
    )


def wait_for_processing_complete(
    *,
    base_url: str,
    document_id: str,
    headers: Dict[str, str],
    processing_timeout_seconds: float,
    poll_interval_seconds: float,
    checks: list[Dict[str, Any]],
    step_name: str,
) -> Dict[str, Any]:
    status_url = f"{base_url}/weaviate/documents/{document_id}/status"
    deadline = time.monotonic() + processing_timeout_seconds
    last_payload: Optional[Dict[str, Any]] = None

    while time.monotonic() < deadline:
        response = http_request("GET", status_url, headers=headers, timeout=20.0)
        require(
            response.status_code == 200 and isinstance(response.json_body, dict),
            f"Unexpected status response for document {document_id}: {response.status_code} {response.text}",
        )
        payload = response.json_body
        last_payload = payload
        processing_status = str(payload.get("processing_status", "")).strip().lower()
        if processing_status == "completed":
            append_check(
                checks,
                step=step_name,
                ok=True,
                status_code=response.status_code,
                payload=payload,
            )
            return payload
        if processing_status == "failed":
            raise SmokeFailure(
                f"Document processing failed for {document_id}: {json.dumps(payload, sort_keys=True)}"
            )
        time.sleep(poll_interval_seconds)

    raise SmokeFailure(
        "Timed out waiting for document processing to complete; "
        f"last payload: {json.dumps(last_payload or {}, sort_keys=True)}"
    )


def upload_pdf(
    *,
    base_url: str,
    sample_pdf: Path,
    headers: Dict[str, str],
    checks: list[Dict[str, Any]],
    can_reuse_duplicate: bool,
    step_name: str,
) -> tuple[str, bool]:
    upload_url = f"{base_url}/weaviate/documents/upload"
    body, boundary = encode_multipart_form({}, "file", sample_pdf)
    upload_headers = dict(headers)
    upload_headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    response = http_request("POST", upload_url, headers=upload_headers, data=body, timeout=180.0)

    if response.status_code == 201 and isinstance(response.json_body, dict):
        document_id = str(response.json_body.get("document_id", "")).strip()
        require(document_id, f"Upload response missing document_id: {response.text}")
        append_check(
            checks,
            step=step_name,
            ok=True,
            status_code=response.status_code,
            payload=response.json_body,
        )
        return document_id, True

    if response.status_code == 409 and can_reuse_duplicate:
        existing_document_id = extract_existing_document_id(response.json_body)
        if not existing_document_id:
            matching_documents = list_documents_by_filename(
                base_url=base_url,
                filename=sample_pdf.name,
                headers=headers,
            )
            if matching_documents:
                existing_document_id = str(
                    matching_documents[0].get("document_id") or matching_documents[0].get("id") or ""
                ).strip()
        require(existing_document_id, f"Duplicate upload did not include existing_document_id: {response.text}")
        append_check(
            checks,
            step=f"{step_name}_duplicate_reused",
            ok=True,
            status_code=response.status_code,
            payload=response.json_body,
        )
        return existing_document_id, False

    if response.status_code == 409:
        raise SmokeFailure(
            "Upload hit an existing duplicate document and duplicate reuse is disabled for release validation. "
            f"Response: {response.text}"
        )

    raise SmokeFailure(f"Upload failed for {sample_pdf.name}: {response.status_code} {response.text}")


def fetch_download_info(
    *,
    base_url: str,
    document_id: str,
    headers: Dict[str, str],
    checks: list[Dict[str, Any]],
    step_name: str,
) -> Dict[str, Any]:
    response = http_request(
        "GET",
        f"{base_url}/weaviate/documents/{document_id}/download-info",
        headers=headers,
        timeout=20.0,
    )
    require(
        response.status_code == 200 and isinstance(response.json_body, dict),
        f"Unexpected download-info response for {document_id}: {response.status_code} {response.text}",
    )
    append_check(
        checks,
        step=step_name,
        ok=True,
        status_code=response.status_code,
        payload=response.json_body,
    )
    return response.json_body


def list_documents_by_filename(
    *,
    base_url: str,
    filename: str,
    headers: Dict[str, str],
) -> list[Dict[str, Any]]:
    encoded_search = urllib.parse.quote(filename)
    response = http_request(
        "GET",
        f"{base_url}/weaviate/documents?page=1&page_size=100&search={encoded_search}",
        headers=headers,
        timeout=30.0,
    )
    require(
        response.status_code == 200 and isinstance(response.json_body, dict),
        f"Unexpected document list response while searching for {filename!r}: {response.status_code} {response.text}",
    )
    documents = response.json_body.get("documents") or []
    return [
        document
        for document in documents
        if str(document.get("filename", "")).strip() == filename
    ]


def delete_matching_documents(
    *,
    base_url: str,
    filename: str,
    headers: Dict[str, str],
    checks: list[Dict[str, Any]],
    step_prefix: str,
) -> list[str]:
    deleted_ids: list[str] = []
    matching_documents = list_documents_by_filename(
        base_url=base_url,
        filename=filename,
        headers=headers,
    )

    for document in matching_documents:
        document_id = str(document.get("document_id") or document.get("id") or "").strip()
        require(document_id, f"Document search payload missing document_id for filename {filename!r}: {document}")
        response = http_request(
            "DELETE",
            f"{base_url}/weaviate/documents/{document_id}",
            headers=headers,
            timeout=30.0,
        )
        append_check(
            checks,
            step=f"{step_prefix}:{document_id}",
            ok=response.status_code == 200,
            status_code=response.status_code,
            payload=response.json_body if response.json_body is not None else response.text,
        )
        require(
            response.status_code == 200,
            f"Failed deleting existing smoke fixture document {document_id}: {response.status_code} {response.text}",
        )
        deleted_ids.append(document_id)

    return deleted_ids


def fetch_chunks(
    *,
    base_url: str,
    document_id: str,
    headers: Dict[str, str],
    checks: list[Dict[str, Any]],
    step_name: str,
) -> Dict[str, Any]:
    response = http_request(
        "GET",
        f"{base_url}/weaviate/documents/{document_id}/chunks?page=1&page_size=100&include_metadata=false",
        headers=headers,
        timeout=30.0,
    )
    require(
        response.status_code == 200 and isinstance(response.json_body, dict),
        f"Unexpected chunks response for {document_id}: {response.status_code} {response.text}",
    )
    total_items = int((response.json_body.get("pagination") or {}).get("total_items") or 0)
    chunk_count = len(response.json_body.get("chunks") or [])
    require(total_items > 0, f"Expected chunk pagination total_items > 0: {response.text}")
    require(chunk_count > 0, f"Expected at least one chunk object in response: {response.text}")
    append_check(
        checks,
        step=step_name,
        ok=True,
        status_code=response.status_code,
        payload={
            "document_id": document_id,
            "total_items": total_items,
            "returned_chunks": chunk_count,
        },
    )
    return response.json_body


def download_document_artifact(
    *,
    base_url: str,
    document_id: str,
    file_type: str,
    headers: Dict[str, str],
    checks: list[Dict[str, Any]],
    step_name: str,
    require_json: bool = False,
) -> Dict[str, Any]:
    response = http_request(
        "GET",
        f"{base_url}/weaviate/documents/{document_id}/download/{file_type}",
        headers=headers,
        timeout=60.0,
    )
    require(
        response.status_code == 200,
        f"Unexpected document artifact response for {file_type}: {response.status_code} {response.text}",
    )
    require(response.body, f"Artifact download for {file_type} was empty")
    parsed_json = decode_json(response.text)
    if require_json:
        require(parsed_json is not None, f"Expected JSON artifact for {file_type}, got: {response.text[:500]}")
    append_check(
        checks,
        step=step_name,
        ok=True,
        status_code=response.status_code,
        payload={
            "document_id": document_id,
            "file_type": file_type,
            "byte_count": len(response.body),
            "json_detected": parsed_json is not None,
        },
    )
    return {
        "byte_count": len(response.body),
        "json_detected": parsed_json is not None,
        "json_body": parsed_json,
    }


def load_document_into_chat(
    *,
    base_url: str,
    document_id: str,
    headers: Dict[str, str],
    checks: list[Dict[str, Any]],
    step_name: str = "chat_document_load",
) -> Dict[str, Any]:
    response = http_request(
        "POST",
        f"{base_url}/api/chat/document/load",
        headers=headers,
        json_body={"document_id": document_id},
        timeout=20.0,
    )
    require(
        response.status_code == 200 and isinstance(response.json_body, dict),
        f"Unexpected chat document load response: {response.status_code} {response.text}",
    )
    require(response.json_body.get("active") is True, f"Document not active after load: {response.text}")
    append_check(
        checks,
        step=step_name,
        ok=True,
        status_code=response.status_code,
        payload=response.json_body,
    )
    return response.json_body


def create_chat_session(
    *,
    base_url: str,
    headers: Dict[str, str],
    checks: list[Dict[str, Any]],
) -> str:
    response = http_request(
        "POST",
        f"{base_url}/api/chat/session",
        headers=headers,
        json_body={"chat_kind": "assistant_chat"},
        timeout=20.0,
    )
    require(
        response.status_code == 200 and isinstance(response.json_body, dict),
        f"Unexpected session response: {response.status_code} {response.text}",
    )
    session_id = str(response.json_body.get("session_id", "")).strip()
    require(session_id, f"Session response missing session_id: {response.text}")
    append_check(
        checks,
        step="chat_session",
        ok=True,
        status_code=response.status_code,
        payload=response.json_body,
    )
    return session_id


def ask_chat_question(
    *,
    base_url: str,
    headers: Dict[str, str],
    session_id: str,
    message: str,
    chat_model: Optional[str],
    specialist_model: Optional[str],
    chat_timeout_seconds: float,
    checks: list[Dict[str, Any]],
) -> str:
    request_body: Dict[str, Any] = {
        "message": message,
        "session_id": session_id,
    }
    if chat_model:
        request_body["model"] = chat_model
        request_body["supervisor_temperature"] = 0.1
        request_body["supervisor_reasoning"] = "minimal"
    if specialist_model:
        request_body["specialist_model"] = specialist_model
        request_body["specialist_temperature"] = 0.1
        request_body["specialist_reasoning"] = "minimal"

    response = http_request(
        "POST",
        f"{base_url}/api/chat",
        headers=headers,
        json_body=request_body,
        timeout=chat_timeout_seconds,
    )
    require(
        response.status_code == 200 and isinstance(response.json_body, dict),
        f"Unexpected chat response: {response.status_code} {response.text}",
    )
    answer = str(response.json_body.get("response", "")).strip()
    require_chat_answer_ok(
        answer,
        context="Non-streaming chat response",
        raw_details=response.text,
    )

    append_check(
        checks,
        step="chat_question",
        ok=True,
        status_code=response.status_code,
        payload={
            "session_id": response.json_body.get("session_id"),
            "response_preview": answer[:500],
        },
    )
    return answer


def ask_streaming_chat_question(
    *,
    base_url: str,
    headers: Dict[str, str],
    session_id: str,
    message: str,
    chat_model: Optional[str],
    specialist_model: Optional[str],
    expected_model: Optional[str],
    chat_timeout_seconds: float,
    checks: list[Dict[str, Any]],
) -> Dict[str, Any]:
    request_body: Dict[str, Any] = {
        "message": message,
        "session_id": session_id,
    }
    if chat_model:
        request_body["model"] = chat_model
        request_body["supervisor_temperature"] = 0.1
        request_body["supervisor_reasoning"] = "minimal"
    if specialist_model:
        request_body["specialist_model"] = specialist_model
        request_body["specialist_temperature"] = 0.1
        request_body["specialist_reasoning"] = "minimal"

    request_headers = dict(headers)
    request_headers["Accept"] = "text/event-stream"
    response = http_request(
        "POST",
        f"{base_url}/api/chat/stream",
        headers=request_headers,
        json_body=request_body,
        timeout=chat_timeout_seconds,
    )
    require(response.status_code == 200, f"Unexpected chat stream response: {response.status_code} {response.text}")

    events = parse_sse_events(response.text)
    require(events, "Streaming chat returned no SSE events")
    event_types = [str(event.get("type", "")) for event in events]
    require("RUN_STARTED" in event_types, f"Missing RUN_STARTED in chat stream events: {event_types}")
    require("RUN_FINISHED" in event_types, f"Missing RUN_FINISHED in chat stream events: {event_types}")
    require(
        "CHUNK_PROVENANCE" in event_types,
        f"Streaming chat did not emit CHUNK_PROVENANCE, so document grounding was not proven: {event_types}",
    )

    error_events = collect_error_events(events)
    require(not error_events, f"Streaming chat emitted error events: {error_events}")

    run_started = next(event for event in events if event.get("type") == "RUN_STARTED")
    trace_id = str(run_started.get("trace_id", "")).strip()
    require(trace_id, f"Streaming chat RUN_STARTED did not expose a trace_id: {run_started}")

    model_blob = str(run_started.get("model", "")).strip()
    resolved_expected_model = expected_model or chat_model
    if resolved_expected_model:
        require_model_looks_expected(
            model_blob,
            expected_model=resolved_expected_model,
            context="Streaming chat RUN_STARTED",
        )

    run_finished = next(event for event in events if event.get("type") == "RUN_FINISHED")
    answer = str(run_finished.get("response", "")).strip()
    require_chat_answer_ok(
        answer,
        context="Streaming chat response",
        raw_details=run_finished,
    )

    summary = {
        "session_id": session_id,
        "trace_id": trace_id,
        "model": model_blob,
        "event_types": event_types,
        "response_preview": answer[:500],
    }
    append_check(
        checks,
        step="chat_stream",
        ok=True,
        status_code=response.status_code,
        payload=summary,
    )
    return summary


def create_custom_agent(
    *,
    base_url: str,
    headers: Dict[str, str],
    model_id: str,
    checks: list[Dict[str, Any]],
) -> Dict[str, str]:
    payload = {
        "template_source": "gene_extractor",
        "name": f"Dev Release Smoke Agent {uuid4().hex[:8]}",
        "description": "Temporary dev release smoke gene extraction agent",
        "include_group_rules": False,
        "model_id": model_id,
        "model_reasoning": "low",
    }
    response = http_request(
        "POST",
        f"{base_url}/api/agent-studio/custom-agents",
        headers=headers,
        json_body=payload,
        timeout=30.0,
    )
    require(
        response.status_code == 201 and isinstance(response.json_body, dict),
        f"Unexpected custom agent create response: {response.status_code} {response.text}",
    )
    custom_agent_id = str(response.json_body.get("id", "")).strip()
    custom_agent_key = str(response.json_body.get("agent_id", "")).strip()
    custom_agent_name = str(response.json_body.get("name", "")).strip()
    require(custom_agent_id and custom_agent_key and custom_agent_name, f"Malformed custom agent response: {response.text}")
    append_check(
        checks,
        step="create_custom_agent",
        ok=True,
        status_code=response.status_code,
        payload=response.json_body,
    )
    return {
        "id": custom_agent_id,
        "agent_id": custom_agent_key,
        "name": custom_agent_name,
    }


def build_flow_definition(agent_id: str, agent_name: str) -> Dict[str, Any]:
    return {
        "version": "1.0",
        "entry_node_id": "task_input_1",
        "nodes": [
            {
                "id": "task_input_1",
                "type": "task_input",
                "position": {"x": 0, "y": 0},
                "data": {
                    "agent_id": "task_input",
                    "agent_display_name": "Initial Instructions",
                    "task_instructions": (
                        "Read the loaded paper and extract the experimentally supported genes. "
                        "Include the organism when possible and preserve verified supporting evidence."
                    ),
                    "output_key": "task_input_text",
                    "input_source": "user_query",
                },
            },
            {
                "id": "agent_1",
                "type": "agent",
                "position": {"x": 280, "y": 0},
                "data": {
                    "agent_id": agent_id,
                    "agent_display_name": agent_name,
                    "output_key": "final_output",
                    "input_source": "previous_output",
                    "step_goal": (
                        "Extract experimentally supported genes from the loaded document and retain "
                        "verified evidence records."
                    ),
                },
            },
        ],
        "edges": [
            {"id": "edge_1", "source": "task_input_1", "target": "agent_1"},
        ],
    }


def build_batch_flow_definition() -> Dict[str, Any]:
    return {
        "version": "1.0",
        "entry_node_id": "task_input_1",
        "nodes": [
            {
                "id": "task_input_1",
                "type": "task_input",
                "position": {"x": 0, "y": 0},
                "data": {
                    "agent_id": "task_input",
                    "agent_display_name": "Initial Instructions",
                    "task_instructions": (
                        "Read the loaded PDF and extract up to 5 curator-relevant findings. "
                        "Include concise evidence snippets. Then format structured output."
                    ),
                    "output_key": "task_input_text",
                    "input_source": "user_query",
                },
            },
            {
                "id": "pdf_1",
                "type": "agent",
                "position": {"x": 280, "y": 0},
                "data": {
                    "agent_id": "pdf_extraction",
                    "agent_display_name": "PDF Specialist",
                    "output_key": "pdf_findings",
                    "input_source": "previous_output",
                    "step_goal": "Read the document and return concise evidence-based findings.",
                },
            },
            {
                "id": "json_1",
                "type": "agent",
                "position": {"x": 560, "y": 0},
                "data": {
                    "agent_id": "json_formatter",
                    "agent_display_name": "JSON Formatter",
                    "output_key": "final_output",
                    "input_source": "previous_output",
                    "step_goal": "Save the final findings as downloadable JSON output.",
                },
            },
        ],
        "edges": [
            {"id": "edge_1", "source": "task_input_1", "target": "pdf_1"},
            {"id": "edge_2", "source": "pdf_1", "target": "json_1"},
        ],
    }


def create_flow(
    *,
    base_url: str,
    headers: Dict[str, str],
    name: str,
    description: str,
    flow_definition: Dict[str, Any],
    checks: list[Dict[str, Any]],
    step_name: str,
) -> str:
    response = http_request(
        "POST",
        f"{base_url}/api/flows",
        headers=headers,
        json_body={
            "name": name,
            "description": description,
            "flow_definition": flow_definition,
        },
        timeout=30.0,
    )
    require(
        response.status_code == 201 and isinstance(response.json_body, dict),
        f"Unexpected flow create response: {response.status_code} {response.text}",
    )
    flow_id = str(response.json_body.get("id", "")).strip()
    require(flow_id, f"Flow create response missing id: {response.text}")
    append_check(
        checks,
        step=step_name,
        ok=True,
        status_code=response.status_code,
        payload=response.json_body,
    )
    return flow_id


def execute_flow(
    *,
    base_url: str,
    headers: Dict[str, str],
    flow_id: str,
    document_id: str,
    user_query: str,
    flow_timeout_seconds: float,
    checks: list[Dict[str, Any]],
) -> Dict[str, Any]:
    response = http_request(
        "POST",
        f"{base_url}/api/chat/execute-flow",
        headers=headers,
        json_body={
            "flow_id": flow_id,
            "session_id": f"dev-release-flow-{uuid4().hex[:8]}",
            "document_id": document_id,
            "user_query": user_query,
        },
        timeout=flow_timeout_seconds,
    )
    require(response.status_code == 200, f"Unexpected execute-flow response: {response.status_code} {response.text}")

    events = parse_sse_events(response.text)
    require(events, "Flow execution returned no SSE events")
    event_types = [str(event.get("type", "")) for event in events]
    require("FLOW_STARTED" in event_types, f"Missing FLOW_STARTED in flow events: {event_types}")
    require("RUN_STARTED" in event_types, f"Missing RUN_STARTED in flow events: {event_types}")
    require("RUN_FINISHED" in event_types, f"Missing RUN_FINISHED in flow events: {event_types}")
    require("FLOW_FINISHED" in event_types, f"Missing FLOW_FINISHED in flow events: {event_types}")
    require(
        "FLOW_STEP_EVIDENCE" in event_types,
        f"Flow execution did not emit FLOW_STEP_EVIDENCE, so persisted evidence was not proven: {event_types}",
    )

    error_events = collect_error_events(events)
    require(not error_events, f"Flow execution emitted error events: {error_events}")

    flow_finished = next(event for event in events if event.get("type") == "FLOW_FINISHED")
    flow_finished_data = flow_finished.get("data") if isinstance(flow_finished, dict) else None
    if not isinstance(flow_finished_data, dict) and isinstance(flow_finished, dict):
        flow_finished_data = flow_finished
    require(
        isinstance(flow_finished_data, dict) and flow_finished_data.get("status") == "completed",
        f"Flow did not complete successfully: {flow_finished}",
    )
    flow_run_id = str(flow_finished_data.get("flow_run_id", "")).strip()
    require(flow_run_id, f"Flow completion did not expose flow_run_id: {flow_finished}")
    total_evidence_records = int(flow_finished_data.get("total_evidence_records") or 0)
    require(total_evidence_records > 0, f"Flow finished without persisted evidence records: {flow_finished}")

    flow_step_evidence_events = [event for event in events if event.get("type") == "FLOW_STEP_EVIDENCE"]
    require(flow_step_evidence_events, f"Missing FLOW_STEP_EVIDENCE details: {event_types}")
    require(
        any(int(event.get("evidence_count") or 0) > 0 for event in flow_step_evidence_events),
        f"FLOW_STEP_EVIDENCE did not include positive evidence_count: {flow_step_evidence_events}",
    )

    run_started = next(event for event in events if event.get("type") == "RUN_STARTED")
    run_finished = next(event for event in events if event.get("type") == "RUN_FINISHED")
    run_output = str(run_finished.get("response", "")).strip()
    require(run_output, f"RUN_FINISHED did not include a response payload: {run_finished}")
    append_check(
        checks,
        step="execute_flow",
        ok=True,
        status_code=response.status_code,
        payload={
            "event_types": event_types,
            "run_started": run_started,
            "run_finished": run_finished,
            "flow_finished": flow_finished,
            "flow_step_evidence_events": flow_step_evidence_events,
        },
    )
    return {
        "event_types": event_types,
        "run_started": run_started,
        "run_finished": run_finished,
        "flow_finished": flow_finished,
        "flow_run_id": flow_run_id,
        "total_evidence_records": total_evidence_records,
    }


def export_flow_evidence_json(
    *,
    base_url: str,
    headers: Dict[str, str],
    flow_run_id: str,
    checks: list[Dict[str, Any]],
) -> Dict[str, Any]:
    response = http_request(
        "GET",
        f"{base_url}/api/flows/runs/{urllib.parse.quote(flow_run_id)}/evidence/export?format=json",
        headers=headers,
        timeout=30.0,
    )
    require(
        response.status_code == 200 and isinstance(response.json_body, dict),
        f"Unexpected flow evidence export response: {response.status_code} {response.text}",
    )

    payload = response.json_body
    require(payload.get("flow_run_id") == flow_run_id, f"Flow evidence export ID mismatch: {payload}")
    total_evidence_records = int(payload.get("total_evidence_records") or 0)
    require(total_evidence_records > 0, f"Flow evidence export was empty: {payload}")
    steps = payload.get("steps")
    require(isinstance(steps, list) and steps, f"Flow evidence export contained no steps: {payload}")
    require(
        any(int((step or {}).get("evidence_count") or 0) > 0 for step in steps if isinstance(step, dict)),
        f"Flow evidence export contained no positive evidence_count steps: {payload}",
    )

    append_check(
        checks,
        step="flow_evidence_export_json",
        ok=True,
        status_code=response.status_code,
        payload={
            "flow_run_id": flow_run_id,
            "total_evidence_records": total_evidence_records,
            "step_count": len(steps),
        },
    )
    return payload


def check_workspace_bootstrap_availability(
    *,
    base_url: str,
    headers: Dict[str, str],
    document_id: str,
    adapter_key: str,
    flow_run_id: Optional[str] = None,
    origin_session_id: Optional[str] = None,
    checks: list[Dict[str, Any]],
) -> Dict[str, Any]:
    query_payload = {"adapter_key": adapter_key}
    if flow_run_id:
        query_payload["flow_run_id"] = flow_run_id
    if origin_session_id:
        query_payload["origin_session_id"] = origin_session_id
    query = urllib.parse.urlencode(query_payload)
    response = http_request(
        "GET",
        f"{base_url}/api/curation-workspace/documents/{urllib.parse.quote(document_id)}/bootstrap-availability?{query}",
        headers=headers,
        timeout=30.0,
    )
    require(
        response.status_code == 200 and isinstance(response.json_body, dict),
        f"Unexpected curation-workspace bootstrap availability response: {response.status_code} {response.text}",
    )
    require(
        response.json_body.get("eligible") is True,
        f"Curation-workspace bootstrap was not eligible: {response.text}",
    )
    append_check(
        checks,
        step="workspace_bootstrap_availability",
        ok=True,
        status_code=response.status_code,
        payload=response.json_body,
    )
    return response.json_body


def bootstrap_workspace_session(
    *,
    base_url: str,
    headers: Dict[str, str],
    document_id: str,
    adapter_key: str,
    flow_run_id: Optional[str] = None,
    origin_session_id: Optional[str] = None,
    checks: list[Dict[str, Any]],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "adapter_key": adapter_key,
    }
    if flow_run_id:
        payload["flow_run_id"] = flow_run_id
    if origin_session_id:
        payload["origin_session_id"] = origin_session_id
    response = http_request(
        "POST",
        f"{base_url}/api/curation-workspace/documents/{urllib.parse.quote(document_id)}/bootstrap",
        headers=headers,
        json_body=payload,
        timeout=60.0,
    )
    require(
        response.status_code == 200 and isinstance(response.json_body, dict),
        f"Unexpected curation-workspace bootstrap response: {response.status_code} {response.text}",
    )
    session = response.json_body.get("session") or {}
    session_id = str(session.get("session_id", "")).strip()
    require(session_id, f"Bootstrap response missing session_id: {response.text}")
    require(
        str(((session.get("adapter") or {}).get("adapter_key") or "")).strip() == adapter_key,
        f"Bootstrap session adapter_key mismatch: {response.text}",
    )
    total_candidates = int(((session.get("progress") or {}).get("total_candidates") or 0))
    require(total_candidates > 0, f"Bootstrap session had no candidates: {response.text}")
    append_check(
        checks,
        step="workspace_bootstrap",
        ok=True,
        status_code=response.status_code,
        payload={
            "created": response.json_body.get("created"),
            "session_id": session_id,
            "adapter_key": (session.get("adapter") or {}).get("adapter_key"),
            "total_candidates": total_candidates,
        },
    )
    return response.json_body


def fetch_workspace_prep_preview(
    *,
    base_url: str,
    headers: Dict[str, str],
    chat_session_id: str,
    checks: list[Dict[str, Any]],
) -> Dict[str, Any]:
    query = urllib.parse.urlencode({"session_id": chat_session_id})
    response = http_request(
        "GET",
        f"{base_url}/api/curation-workspace/prep/preview?{query}",
        headers=headers,
        timeout=30.0,
    )
    require(
        response.status_code == 200 and isinstance(response.json_body, dict),
        f"Unexpected curation prep preview response: {response.status_code} {response.text}",
    )
    require(response.json_body.get("ready") is True, f"Curation prep preview not ready: {response.text}")
    require(
        int(response.json_body.get("candidate_count") or 0) > 0,
        f"Curation prep preview contained no candidates: {response.text}",
    )
    require(
        DEFAULT_WORKSPACE_ADAPTER_KEY in list(response.json_body.get("adapter_keys") or []),
        f"Curation prep preview did not expose adapter {DEFAULT_WORKSPACE_ADAPTER_KEY!r}: {response.text}",
    )
    append_check(
        checks,
        step="workspace_prep_preview",
        ok=True,
        status_code=response.status_code,
        payload=response.json_body,
    )
    return response.json_body


def run_workspace_chat_prep(
    *,
    base_url: str,
    headers: Dict[str, str],
    chat_session_id: str,
    adapter_key: str,
    checks: list[Dict[str, Any]],
) -> Dict[str, Any]:
    response = http_request(
        "POST",
        f"{base_url}/api/curation-workspace/prep",
        headers=headers,
        json_body={
            "session_id": chat_session_id,
            "adapter_keys": [adapter_key],
        },
        timeout=120.0,
    )
    require(
        response.status_code == 200 and isinstance(response.json_body, dict),
        f"Unexpected curation prep run response: {response.status_code} {response.text}",
    )
    require(
        int(response.json_body.get("candidate_count") or 0) > 0,
        f"Curation prep run produced no candidates: {response.text}",
    )
    prepared_sessions = response.json_body.get("prepared_sessions") or []
    require(
        isinstance(prepared_sessions, list) and prepared_sessions,
        f"Curation prep run created no prepared sessions: {response.text}",
    )
    require(
        any(
            str((session or {}).get("adapter_key") or "").strip() == adapter_key
            and str((session or {}).get("session_id") or "").strip()
            for session in prepared_sessions
            if isinstance(session, dict)
        ),
        f"Curation prep run did not create a prepared session for {adapter_key!r}: {response.text}",
    )
    append_check(
        checks,
        step="workspace_prep_run",
        ok=True,
        status_code=response.status_code,
        payload=response.json_body,
    )
    return response.json_body


def fetch_workspace_payload(
    *,
    base_url: str,
    headers: Dict[str, str],
    session_id: str,
    checks: list[Dict[str, Any]],
) -> Dict[str, Any]:
    response = http_request(
        "GET",
        f"{base_url}/api/curation-workspace/sessions/{urllib.parse.quote(session_id)}?include_workspace=true",
        headers=headers,
        timeout=60.0,
    )
    require(
        response.status_code == 200 and isinstance(response.json_body, dict),
        f"Unexpected curation-workspace session response: {response.status_code} {response.text}",
    )
    workspace = response.json_body.get("workspace") or {}
    session = workspace.get("session") or {}
    candidates = workspace.get("candidates") or []
    entity_tags = workspace.get("entity_tags") or []
    require(
        str(session.get("session_id", "")).strip() == session_id,
        f"Workspace payload session_id mismatch: {response.text}",
    )
    require(
        isinstance(candidates, list) and candidates,
        f"Workspace payload contained no candidates: {response.text}",
    )
    require(
        isinstance(entity_tags, list) and entity_tags,
        f"Workspace payload contained no entity tags: {response.text}",
    )
    first_candidate = candidates[0] if isinstance(candidates[0], dict) else {}
    require(
        str(first_candidate.get("adapter_key", "")).strip() == DEFAULT_WORKSPACE_ADAPTER_KEY,
        f"Workspace candidate adapter_key mismatch: {response.text}",
    )
    append_check(
        checks,
        step="workspace_fetch",
        ok=True,
        status_code=response.status_code,
        payload={
            "session_id": session_id,
            "candidate_count": len(candidates),
            "entity_tag_count": len(entity_tags),
            "active_candidate_id": workspace.get("active_candidate_id"),
        },
    )
    return response.json_body


def validate_batch_flow(
    *,
    base_url: str,
    headers: Dict[str, str],
    flow_id: str,
    checks: list[Dict[str, Any]],
) -> Dict[str, Any]:
    response = http_request("GET", f"{base_url}/api/flows/{flow_id}/validate-batch", headers=headers, timeout=20.0)
    require(
        response.status_code == 200 and isinstance(response.json_body, dict),
        f"Unexpected validate-batch response: {response.status_code} {response.text}",
    )
    require(response.json_body.get("valid") is True, f"Batch flow validation failed: {response.text}")
    append_check(
        checks,
        step="validate_batch_flow",
        ok=True,
        status_code=response.status_code,
        payload=response.json_body,
    )
    return response.json_body


def create_batch(
    *,
    base_url: str,
    headers: Dict[str, str],
    flow_id: str,
    document_ids: list[str],
    checks: list[Dict[str, Any]],
) -> str:
    response = http_request(
        "POST",
        f"{base_url}/api/batches",
        headers=headers,
        json_body={"flow_id": flow_id, "document_ids": document_ids},
        timeout=30.0,
    )
    require(
        response.status_code == 201 and isinstance(response.json_body, dict),
        f"Unexpected batch create response: {response.status_code} {response.text}",
    )
    batch_id = str(response.json_body.get("id", "")).strip()
    require(batch_id, f"Batch create response missing id: {response.text}")
    append_check(
        checks,
        step="create_batch",
        ok=True,
        status_code=response.status_code,
        payload=response.json_body,
    )
    return batch_id


def wait_for_batch_terminal(
    *,
    base_url: str,
    headers: Dict[str, str],
    batch_id: str,
    batch_timeout_seconds: float,
    poll_interval_seconds: float,
    checks: list[Dict[str, Any]],
) -> Dict[str, Any]:
    deadline = time.monotonic() + batch_timeout_seconds
    last_payload: Dict[str, Any] = {}

    while time.monotonic() < deadline:
        response = http_request("GET", f"{base_url}/api/batches/{batch_id}", headers=headers, timeout=20.0)
        require(
            response.status_code == 200 and isinstance(response.json_body, dict),
            f"Unexpected batch status response: {response.status_code} {response.text}",
        )
        last_payload = response.json_body
        status = str(last_payload.get("status", "")).lower()
        if status in {"completed", "cancelled", "failed"}:
            append_check(
                checks,
                step="batch_terminal",
                ok=status == "completed",
                status_code=response.status_code,
                payload=last_payload,
            )
            require(status == "completed", f"Batch did not complete successfully: {json.dumps(last_payload, sort_keys=True)}")
            return last_payload
        time.sleep(poll_interval_seconds)

    raise SmokeFailure(
        f"Batch {batch_id} did not reach terminal status in time; "
        f"last payload: {json.dumps(last_payload, sort_keys=True)}"
    )


def download_batch_zip(
    *,
    base_url: str,
    headers: Dict[str, str],
    batch_id: str,
    timeout_seconds: float,
    checks: list[Dict[str, Any]],
) -> list[str]:
    response = http_request(
        "GET",
        f"{base_url}/api/batches/{batch_id}/download-zip",
        headers=headers,
        timeout=timeout_seconds,
    )
    require(response.status_code == 200, f"Unexpected batch ZIP response: {response.status_code} {response.text}")
    require(response.body.startswith(b"PK"), "Batch ZIP response did not look like a ZIP archive")

    with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
    require(members, "Batch ZIP archive was empty")

    append_check(
        checks,
        step="batch_download_zip",
        ok=True,
        status_code=response.status_code,
        payload={"members": members},
    )
    return members


def cleanup_document(base_url: str, document_id: str, headers: Dict[str, str], checks: list[Dict[str, Any]]) -> None:
    response = http_request("DELETE", f"{base_url}/weaviate/documents/{document_id}", headers=headers, timeout=30.0)
    append_check(
        checks,
        step=f"cleanup_document:{document_id}",
        ok=response.status_code == 200,
        status_code=response.status_code,
        payload=response.json_body if response.json_body is not None else response.text,
    )


def cleanup_loaded_document(base_url: str, headers: Dict[str, str], checks: list[Dict[str, Any]]) -> None:
    response = http_request("DELETE", f"{base_url}/api/chat/document", headers=headers, timeout=30.0)
    append_check(
        checks,
        step="cleanup_loaded_document",
        ok=response.status_code == 200,
        status_code=response.status_code,
        payload=response.json_body if response.json_body is not None else response.text,
    )


def cleanup_flow(base_url: str, flow_id: str, headers: Dict[str, str], checks: list[Dict[str, Any]]) -> None:
    response = http_request("DELETE", f"{base_url}/api/flows/{flow_id}", headers=headers, timeout=30.0)
    append_check(
        checks,
        step=f"cleanup_flow:{flow_id}",
        ok=response.status_code == 200,
        status_code=response.status_code,
        payload=response.json_body if response.json_body is not None else response.text,
    )


def cleanup_custom_agent(base_url: str, custom_agent_id: str, headers: Dict[str, str], checks: list[Dict[str, Any]]) -> None:
    response = http_request(
        "DELETE",
        f"{base_url}/api/agent-studio/custom-agents/{custom_agent_id}",
        headers=headers,
        timeout=30.0,
    )
    append_check(
        checks,
        step=f"cleanup_custom_agent:{custom_agent_id}",
        ok=response.status_code == 200,
        status_code=response.status_code,
        payload=response.json_body if response.json_body is not None else response.text,
    )


def attempt_cleanup(
    *,
    step: str,
    checks: list[Dict[str, Any]],
    cleanup_fn,
    **kwargs: Any,
) -> None:
    try:
        cleanup_fn(**kwargs, checks=checks)
    except Exception as exc:
        append_check(
            checks,
            step=step,
            ok=False,
            status_code=0,
            payload={"error": str(exc)},
        )


def run(args: argparse.Namespace) -> Dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    env_file = Path(args.env_file).expanduser()
    api_key = resolve_api_key(args.api_key, env_file)
    expected_api_user = resolve_env_value("TESTING_API_KEY_USER", env_file)
    expected_api_email = resolve_env_value("TESTING_API_KEY_EMAIL", env_file)
    expected_auth_sub = f"api-key-{expected_api_user}" if expected_api_user else None

    scope_limitations = compute_scope_limitations(args)

    evidence: Dict[str, Any] = {
        "timestamp_utc": _now_iso(),
        "base_url": base_url,
        "env_file": str(env_file),
        "sample_pdf": args.sample_pdf,
        "secondary_pdf": args.secondary_pdf,
        "expected_runtime_chat_model": args.chat_model,
        "expected_runtime_specialist_model": args.specialist_model,
        "runtime_default_specialist_model_directly_validated": False,
        "flow_model": args.flow_model,
        "used_api_key_auth": bool(api_key),
        "checks": [],
        "resources": {
            "primary_document_id": None,
            "secondary_document_id": None,
            "chat_session_id": None,
            "stream_chat_session_id": None,
            "chat_trace_id": None,
            "custom_agent_id": None,
            "custom_agent_key": None,
            "flow_id": None,
            "workspace_session_id": None,
            "batch_flow_id": None,
            "batch_id": None,
        },
        "chat_response_preview": None,
        "chat_stream_summary": None,
        "flow_summary": None,
        "workspace_summary": None,
        "batch_zip_members": None,
        "rerank_provider_smoke": {
            "included": args.include_rerank_provider_smoke,
            "base_url": args.rerank_provider_smoke_base_url if args.include_rerank_provider_smoke else None,
            "script_path": args.rerank_provider_smoke_script if args.include_rerank_provider_smoke else None,
            "evidence_file": None,
            "overall_status": "not_requested",
        },
        "preflight": {},
        "document_artifacts": {},
        "expected_api_principal": {
            "auth_sub": expected_auth_sub,
            "email": expected_api_email,
        },
        "run_scope": "full" if not scope_limitations else "partial",
        "skipped_stages": scope_limitations,
    }
    checks: list[Dict[str, Any]] = evidence["checks"]
    headers = build_headers(api_key)
    created_documents: list[str] = []
    loaded_document = False
    custom_agent_id: Optional[str] = None
    flow_id: Optional[str] = None
    batch_flow_id: Optional[str] = None
    primary_document_id: Optional[str] = None
    secondary_document_id: Optional[str] = None
    sample_pdf: Optional[Path] = None
    secondary_pdf: Optional[Path] = None

    try:
        verify_api_key_mode(api_key, allow_dev_mode_fallback=args.allow_dev_mode_fallback)
        if args.skip_user_info and not args.allow_dev_mode_fallback:
            raise SmokeFailure(
                "--skip-user-info is only allowed when --allow-dev-mode-fallback is enabled. "
                "Release validation must verify the API-key principal."
            )
        if args.skip_chat and not args.skip_workspace:
            raise SmokeFailure(
                "--skip-workspace cannot be omitted when --skip-chat is enabled. "
                "The current workspace slice depends on persisted chat extraction results."
            )
        if args.delete_existing_sample_documents and args.skip_user_info:
            raise SmokeFailure(
                "--delete-existing-sample-documents requires authenticated user verification. "
                "Do not combine it with --skip-user-info."
            )

        sample_pdf = resolve_sample_pdf(args.sample_pdf)
        evidence["sample_pdf"] = str(sample_pdf)
        if not args.skip_batch:
            secondary_pdf = resolve_secondary_pdf(sample_pdf, args.secondary_pdf)
            evidence["secondary_pdf"] = str(secondary_pdf)

        print_step("Checking backend health")
        health_response = http_request("GET", f"{base_url}/health", headers=headers, timeout=15.0)
        require(
            health_response.status_code == 200 and isinstance(health_response.json_body, dict),
            f"Unexpected /health response: {health_response.status_code} {health_response.text}",
        )
        append_check(
            checks,
            step="health",
            ok=True,
            status_code=health_response.status_code,
            payload=health_response.json_body,
        )

        if not args.skip_provider_health:
            print_step("Checking LLM provider health report")
            evidence["preflight"]["llm_provider_health"] = check_llm_provider_health(
                base_url=base_url,
                headers=headers,
                checks=checks,
            )

        if not args.skip_user_info:
            print_step("Checking authenticated user resolution")
            current_user = check_current_user(
                base_url=base_url,
                headers=headers,
                checks=checks,
                expected_auth_sub=expected_auth_sub,
                expected_email=expected_api_email,
            )
            evidence["preflight"]["current_user"] = current_user
            if args.delete_existing_sample_documents:
                require_safe_fixture_deletion_principal(current_user)

        print_step("Ensuring PDF extraction worker is ready")
        ensure_worker_ready(
            base_url=base_url,
            headers=headers,
            wake_timeout_seconds=args.wake_timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            checks=checks,
        )

        if args.delete_existing_sample_documents:
            print_step(f"Deleting any existing primary fixture docs named {sample_pdf.name}")
            delete_matching_documents(
                base_url=base_url,
                filename=sample_pdf.name,
                headers=headers,
                checks=checks,
                step_prefix="delete_existing_primary_fixture",
            )

        print_step(f"Uploading primary sample PDF: {sample_pdf.name}")
        primary_document_id, created_primary = upload_pdf(
            base_url=base_url,
            sample_pdf=sample_pdf,
            headers=headers,
            checks=checks,
            can_reuse_duplicate=args.allow_duplicate_reuse,
            step_name="upload_primary_pdf",
        )
        evidence["resources"]["primary_document_id"] = primary_document_id
        if created_primary:
            created_documents.append(primary_document_id)

        print_step("Waiting for primary PDF processing to complete")
        final_status = wait_for_processing_complete(
            base_url=base_url,
            document_id=primary_document_id,
            headers=headers,
            processing_timeout_seconds=args.processing_timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            checks=checks,
            step_name="primary_document_processing_completed",
        )
        require(int(final_status.get("chunk_count") or 0) > 0, f"Expected chunk_count > 0: {final_status}")

        print_step("Checking primary document chunks")
        chunks_payload = fetch_chunks(
            base_url=base_url,
            document_id=primary_document_id,
            headers=headers,
            checks=checks,
            step_name="primary_chunks",
        )
        primary_chunk_total = int((chunks_payload.get("pagination") or {}).get("total_items") or 0)
        require(
            primary_chunk_total == int(final_status.get("chunk_count") or 0),
            f"Chunk pagination count did not match processing status: status={final_status} chunks={chunks_payload}",
        )
        evidence["document_artifacts"]["primary_chunks_total"] = primary_chunk_total

        print_step("Checking primary download-info")
        download_info = fetch_download_info(
            base_url=base_url,
            document_id=primary_document_id,
            headers=headers,
            checks=checks,
            step_name="primary_download_info",
        )
        require(download_info.get("pdf_available") is True, f"Expected pdf_available true: {download_info}")
        require(
            download_info.get("pdfx_json_available") is True,
            f"Expected pdfx_json_available true: {download_info}",
        )
        evidence["document_artifacts"]["primary_download_info"] = download_info

        print_step("Downloading primary PDFX JSON artifact")
        evidence["document_artifacts"]["primary_pdfx_json"] = download_document_artifact(
            base_url=base_url,
            document_id=primary_document_id,
            file_type="pdfx_json",
            headers=headers,
            checks=checks,
            step_name="primary_pdfx_json_download",
            require_json=True,
        )

        if not args.skip_chat:
            print_step("Loading primary document into chat context")
            load_document_into_chat(
                base_url=base_url,
                document_id=primary_document_id,
                headers=headers,
                checks=checks,
                step_name="chat_document_load",
            )
            loaded_document = True

            print_step("Creating chat session")
            chat_session_id = create_chat_session(base_url=base_url, headers=headers, checks=checks)
            evidence["resources"]["chat_session_id"] = chat_session_id

            print_step("Asking a real chat question on the default runtime model path")
            answer = ask_chat_question(
                base_url=base_url,
                headers=headers,
                session_id=chat_session_id,
                message=args.chat_message,
                chat_model=None,
                specialist_model=None,
                chat_timeout_seconds=args.chat_timeout_seconds,
                checks=checks,
            )
            if args.chat_message.strip() == DEFAULT_CHAT_MESSAGE:
                require_text_contains_any_snippet(
                    answer,
                    snippets=REQUIRED_FOCUS_GENE_SNIPPETS,
                    context="Default focus-gene chat response",
                    raw_details=answer,
                )
            evidence["chat_response_preview"] = answer[:500]

            print_step("Creating a dedicated streaming chat session")
            stream_chat_session_id = create_chat_session(base_url=base_url, headers=headers, checks=checks)
            evidence["resources"]["stream_chat_session_id"] = stream_chat_session_id

            print_step("Refreshing the loaded document before dedicated streaming chat")
            load_document_into_chat(
                base_url=base_url,
                document_id=primary_document_id,
                headers=headers,
                checks=checks,
                step_name="chat_document_load_streaming_refresh",
            )

            print_step("Running the real streaming chat path on runtime defaults")
            chat_stream_summary = ask_streaming_chat_question(
                base_url=base_url,
                headers=headers,
                session_id=stream_chat_session_id,
                message=args.chat_message,
                chat_model=None,
                specialist_model=None,
                expected_model=args.chat_model,
                chat_timeout_seconds=args.chat_timeout_seconds,
                checks=checks,
            )
            if args.chat_message.strip() == DEFAULT_CHAT_MESSAGE:
                require_text_contains_any_snippet(
                    str(chat_stream_summary.get("response_preview", "")),
                    snippets=REQUIRED_FOCUS_GENE_SNIPPETS,
                    context="Default focus-gene streaming chat response",
                    raw_details=chat_stream_summary,
            )
            evidence["chat_stream_summary"] = chat_stream_summary
            evidence["resources"]["chat_trace_id"] = chat_stream_summary["trace_id"]

            if not args.skip_workspace:
                print_step("Previewing curation prep from the chat session")
                workspace_prep_preview = fetch_workspace_prep_preview(
                    base_url=base_url,
                    headers=headers,
                    chat_session_id=chat_session_id,
                    checks=checks,
                )

                print_step("Checking curation-workspace bootstrap availability from chat prep context")
                workspace_availability = check_workspace_bootstrap_availability(
                    base_url=base_url,
                    headers=headers,
                    document_id=primary_document_id,
                    origin_session_id=chat_session_id,
                    adapter_key=DEFAULT_WORKSPACE_ADAPTER_KEY,
                    checks=checks,
                )

                print_step("Running curation prep and bootstrapping workspace sessions")
                workspace_prep_run = run_workspace_chat_prep(
                    base_url=base_url,
                    headers=headers,
                    chat_session_id=chat_session_id,
                    adapter_key=DEFAULT_WORKSPACE_ADAPTER_KEY,
                    checks=checks,
                )
                prepared_sessions = workspace_prep_run.get("prepared_sessions") or []
                prepared_session = next(
                    (
                        session for session in prepared_sessions
                        if isinstance(session, dict)
                        and str(session.get("adapter_key") or "").strip() == DEFAULT_WORKSPACE_ADAPTER_KEY
                    ),
                    None,
                )
                require(
                    isinstance(prepared_session, dict),
                    f"Workspace prep run did not return a {DEFAULT_WORKSPACE_ADAPTER_KEY!r} prepared session: {workspace_prep_run}",
                )
                workspace_session_id = str(prepared_session.get("session_id", "")).strip()
                require(
                    workspace_session_id,
                    f"Workspace prep session missing session_id: {workspace_prep_run}",
                )

                print_step("Replaying the bootstrap endpoint for the prepared chat scope")
                workspace_bootstrap = bootstrap_workspace_session(
                    base_url=base_url,
                    headers=headers,
                    document_id=primary_document_id,
                    origin_session_id=chat_session_id,
                    adapter_key=DEFAULT_WORKSPACE_ADAPTER_KEY,
                    checks=checks,
                )
                evidence["resources"]["workspace_session_id"] = workspace_session_id

                print_step("Fetching the hydrated curation workspace payload")
                workspace_payload = fetch_workspace_payload(
                    base_url=base_url,
                    headers=headers,
                    session_id=workspace_session_id,
                    checks=checks,
                )
                evidence["workspace_summary"] = {
                    "prep_preview": workspace_prep_preview,
                    "availability": workspace_availability,
                    "prep_candidate_count": workspace_prep_run.get("candidate_count"),
                    "prep_session_id": workspace_session_id,
                    "bootstrap_created": workspace_bootstrap.get("created"),
                    "bootstrap_session_id": str(
                        ((workspace_bootstrap.get("session") or {}).get("session_id") or "")
                    ).strip(),
                    "candidate_count": len(
                        ((workspace_payload.get("workspace") or {}).get("candidates") or [])
                    ),
                    "entity_tag_count": len(
                        ((workspace_payload.get("workspace") or {}).get("entity_tags") or [])
                    ),
                }

        if not args.skip_flow:
            print_step("Creating a temporary custom agent")
            custom_agent = create_custom_agent(
                base_url=base_url,
                headers=headers,
                model_id=args.flow_model,
                checks=checks,
            )
            custom_agent_id = custom_agent["id"]
            evidence["resources"]["custom_agent_id"] = custom_agent["id"]
            evidence["resources"]["custom_agent_key"] = custom_agent["agent_id"]

            print_step("Creating a smoke-test flow")
            flow_id = create_flow(
                base_url=base_url,
                headers=headers,
                name=f"Dev Release Smoke Flow {uuid4().hex[:8]}",
                description="Temporary dev release smoke flow",
                flow_definition=build_flow_definition(custom_agent["agent_id"], custom_agent["name"]),
                checks=checks,
                step_name="create_flow",
            )
            evidence["resources"]["flow_id"] = flow_id

            print_step("Executing the smoke-test flow over SSE")
            flow_summary = execute_flow(
                base_url=base_url,
                headers=headers,
                flow_id=flow_id,
                document_id=primary_document_id,
                user_query=args.flow_query,
                flow_timeout_seconds=args.flow_timeout_seconds,
                checks=checks,
            )
            evidence["flow_summary"] = flow_summary

            print_step("Exporting persisted flow evidence as JSON")
            flow_evidence_export = export_flow_evidence_json(
                base_url=base_url,
                headers=headers,
                flow_run_id=flow_summary["flow_run_id"],
                checks=checks,
            )
            evidence["flow_evidence_export"] = flow_evidence_export

        if not args.skip_batch:
            assert secondary_pdf is not None

            if args.delete_existing_sample_documents:
                print_step(f"Deleting any existing secondary fixture docs named {secondary_pdf.name}")
                delete_matching_documents(
                    base_url=base_url,
                    filename=secondary_pdf.name,
                    headers=headers,
                    checks=checks,
                    step_prefix="delete_existing_secondary_fixture",
                )

            print_step(f"Uploading secondary sample PDF for batch: {secondary_pdf.name}")
            secondary_document_id, created_secondary = upload_pdf(
                base_url=base_url,
                sample_pdf=secondary_pdf,
                headers=headers,
                checks=checks,
                can_reuse_duplicate=args.allow_duplicate_reuse,
                step_name="upload_secondary_pdf",
            )
            evidence["resources"]["secondary_document_id"] = secondary_document_id
            if created_secondary:
                created_documents.append(secondary_document_id)

            print_step("Waiting for secondary PDF processing to complete")
            secondary_status = wait_for_processing_complete(
                base_url=base_url,
                document_id=secondary_document_id,
                headers=headers,
                processing_timeout_seconds=args.processing_timeout_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
                checks=checks,
                step_name="secondary_document_processing_completed",
            )
            require(int(secondary_status.get("chunk_count") or 0) > 0, f"Expected chunk_count > 0: {secondary_status}")

            print_step("Creating a batch-compatible flow")
            batch_flow_id = create_flow(
                base_url=base_url,
                headers=headers,
                name=f"Dev Release Smoke Batch {uuid4().hex[:8]}",
                description="Temporary dev release smoke batch flow",
                flow_definition=build_batch_flow_definition(),
                checks=checks,
                step_name="create_batch_flow",
            )
            evidence["resources"]["batch_flow_id"] = batch_flow_id

            print_step("Validating the batch flow")
            validate_batch_flow(
                base_url=base_url,
                headers=headers,
                flow_id=batch_flow_id,
                checks=checks,
            )

            print_step("Creating the batch run")
            batch_id = create_batch(
                base_url=base_url,
                headers=headers,
                flow_id=batch_flow_id,
                document_ids=[primary_document_id, secondary_document_id],
                checks=checks,
            )
            evidence["resources"]["batch_id"] = batch_id

            print_step("Waiting for batch processing to complete")
            terminal_batch = wait_for_batch_terminal(
                base_url=base_url,
                headers=headers,
                batch_id=batch_id,
                batch_timeout_seconds=args.batch_timeout_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
                checks=checks,
            )
            documents = terminal_batch.get("documents") or []
            require(len(documents) >= 2, f"Expected at least two batch document results: {terminal_batch}")
            for document_result in documents:
                require(
                    str(document_result.get("status", "")).lower() == "completed",
                    f"Batch document did not complete successfully: {document_result}",
                )
                require(document_result.get("result_file_path"), f"Missing result_file_path: {document_result}")

            print_step("Downloading the batch ZIP")
            zip_members = download_batch_zip(
                base_url=base_url,
                headers=headers,
                batch_id=batch_id,
                timeout_seconds=args.zip_timeout_seconds,
                checks=checks,
            )
            evidence["batch_zip_members"] = zip_members

        if args.include_rerank_provider_smoke:
            print_step("Running optional local rerank provider smoke")
            rerank_provider_smoke = run_local_rerank_provider_smoke(
                script_path=resolve_rerank_provider_smoke_script(args.rerank_provider_smoke_script),
                base_url=args.rerank_provider_smoke_base_url.rstrip("/"),
            )
            evidence["rerank_provider_smoke"] = {
                "included": True,
                **rerank_provider_smoke,
            }
            append_check(
                checks,
                step="rerank_provider_smoke",
                ok=True,
                status_code=0,
                payload={
                    "base_url": rerank_provider_smoke["base_url"],
                    "evidence_file": rerank_provider_smoke["evidence_file"],
                    "pass_count": rerank_provider_smoke["pass_count"],
                    "fail_count": rerank_provider_smoke["fail_count"],
                },
            )

        evidence["overall_status"] = "pass"
    except Exception as exc:
        evidence["overall_status"] = "fail"
        evidence["error"] = str(exc)
    finally:
        if batch_flow_id:
            attempt_cleanup(
                step=f"cleanup_flow_exception:{batch_flow_id}",
                checks=checks,
                cleanup_fn=cleanup_flow,
                base_url=base_url,
                flow_id=batch_flow_id,
                headers=headers,
            )
        if flow_id:
            attempt_cleanup(
                step=f"cleanup_flow_exception:{flow_id}",
                checks=checks,
                cleanup_fn=cleanup_flow,
                base_url=base_url,
                flow_id=flow_id,
                headers=headers,
            )
        if custom_agent_id:
            attempt_cleanup(
                step=f"cleanup_custom_agent_exception:{custom_agent_id}",
                checks=checks,
                cleanup_fn=cleanup_custom_agent,
                base_url=base_url,
                custom_agent_id=custom_agent_id,
                headers=headers,
            )
        if loaded_document:
            attempt_cleanup(
                step="cleanup_loaded_document_exception",
                checks=checks,
                cleanup_fn=cleanup_loaded_document,
                base_url=base_url,
                headers=headers,
            )
        for document_id in reversed(created_documents):
            attempt_cleanup(
                step=f"cleanup_document_exception:{document_id}",
                checks=checks,
                cleanup_fn=cleanup_document,
                base_url=base_url,
                document_id=document_id,
                headers=headers,
            )

    apply_cleanup_failures_to_evidence(evidence)
    return evidence


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000", help="Backend base URL")
    parser.add_argument("--api-key", default=None, help="Override TESTING_API_KEY for API-key auth")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Optional env file used to auto-load TESTING_API_KEY when not already exported",
    )
    parser.add_argument("--sample-pdf", default=None, help="Path to the primary sample PDF to upload")
    parser.add_argument("--secondary-pdf", default=None, help="Path to a second sample PDF for batch coverage")
    parser.add_argument(
        "--chat-message",
        default=DEFAULT_CHAT_MESSAGE,
        help="Question to ask after loading the document into chat",
    )
    parser.add_argument(
        "--flow-query",
        default=DEFAULT_FLOW_QUERY,
        help="User query to send through the execute-flow smoke stage",
    )
    parser.add_argument(
        "--chat-model",
        default=DEFAULT_CHAT_MODEL,
        help="Expected supervisor model for the runtime-default smoke chat turn",
    )
    parser.add_argument(
        "--specialist-model",
        default=DEFAULT_SPECIALIST_MODEL,
        help="Specialist model for the smoke chat turn",
    )
    parser.add_argument(
        "--flow-model",
        default=DEFAULT_FLOW_MODEL,
        help="Model to use for the temporary custom agent in the flow smoke stage",
    )
    parser.add_argument("--wake-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--processing-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--chat-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--flow-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--batch-timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--zip-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    parser.add_argument(
        "--evidence-dir",
        default="/tmp/agr_ai_curation_dev_release_smoke",
        help="Directory for evidence JSON output",
    )
    parser.add_argument(
        "--allow-dev-mode-fallback",
        action="store_true",
        help="Allow DEV_MODE auth fallback when no TESTING_API_KEY is available",
    )
    parser.add_argument(
        "--allow-duplicate-reuse",
        action="store_true",
        help="Allow duplicate upload reuse instead of requiring a fresh upload",
    )
    parser.add_argument(
        "--delete-existing-sample-documents",
        action="store_true",
        help=(
            "Before upload, delete exact filename matches for the authenticated smoke user. "
            "Use this only with a dedicated test/smoke principal when rerunning shared fixture PDFs."
        ),
    )
    parser.add_argument("--skip-provider-health", action="store_true", help="Skip /api/admin/health/llm-providers")
    parser.add_argument("--skip-user-info", action="store_true", help="Skip /api/users/me preflight")
    parser.add_argument("--skip-chat", action="store_true", help="Skip the chat smoke stage")
    parser.add_argument("--skip-flow", action="store_true", help="Skip the flow smoke stage")
    parser.add_argument(
        "--skip-workspace",
        action="store_true",
        help="Skip the curation-workspace bootstrap stage (currently depends on flow evidence)",
    )
    parser.add_argument("--skip-batch", action="store_true", help="Skip the batch smoke stage")
    parser.add_argument(
        "--include-rerank-provider-smoke",
        action="store_true",
        help=(
            "Run scripts/testing/rerank_provider_smoke_local.sh after the HTTP API smoke. "
            "This is local-stack coverage and stays opt-in because it restarts the local compose backend."
        ),
    )
    parser.add_argument(
        "--rerank-provider-smoke-base-url",
        default="http://localhost:8000",
        help="Base URL for the local rerank provider smoke helper",
    )
    parser.add_argument(
        "--rerank-provider-smoke-script",
        default="scripts/testing/rerank_provider_smoke_local.sh",
        help="Path to the local rerank provider smoke helper (relative to repo root by default)",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def write_evidence(evidence_dir: Path, payload: Dict[str, Any]) -> Path:
    candidate_dirs = [evidence_dir.expanduser(), Path("/tmp/agr_ai_curation_dev_release_smoke")]
    errors: list[str] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for candidate_dir in candidate_dirs:
        try:
            candidate_dir.mkdir(parents=True, exist_ok=True)
            path = candidate_dir / f"dev_release_smoke_{stamp}.json"
            path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            return path
        except OSError as exc:
            errors.append(f"{candidate_dir}: {exc}")

    raise SmokeFailure("Could not write evidence JSON. Tried: " + " | ".join(errors))


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    evidence_dir = Path(args.evidence_dir)

    try:
        payload = run(args)
    except Exception as exc:
        payload = {
            "timestamp_utc": _now_iso(),
            "overall_status": "fail",
            "error": str(exc),
        }
        try:
            evidence_path = write_evidence(evidence_dir, payload)
            print_step(f"FAILED: {exc}")
            print_step(f"Evidence file: {evidence_path}")
        except Exception as write_exc:
            print_step(f"FAILED: {exc}")
            print_step(f"Could not write evidence JSON: {write_exc}")
        return 1

    evidence_path = write_evidence(evidence_dir, payload)
    if payload.get("overall_status") != "pass":
        print_step(f"FAILED: {payload.get('error', 'Unknown error')}")
        print_step(f"Evidence file: {evidence_path}")
        return 1

    if payload.get("run_scope") == "partial":
        skipped = ", ".join(payload.get("skipped_stages") or [])
        print_step(f"PASS (partial/debug run; omitted or relaxed: {skipped})")
    else:
        print_step("PASS")
    print_step(f"Evidence file: {evidence_path}")
    if payload.get("chat_response_preview"):
        print_step(f"Chat preview: {payload['chat_response_preview']}")
    if payload.get("batch_zip_members"):
        print_step(f"Batch ZIP members: {', '.join(payload['batch_zip_members'][:5])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
