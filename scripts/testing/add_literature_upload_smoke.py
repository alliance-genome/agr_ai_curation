#!/usr/bin/env python3
"""Run a durable Add Literature manual-PDF upload smoke with Cognito auth."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence, cast

_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from scripts.testing import abc_literature_live_smoke as live_smoke  # noqa: E402
from scripts.testing import abc_literature_ready_upload_smoke as ready_smoke  # noqa: E402
from scripts.testing import dev_release_smoke as dev_smoke  # noqa: E402


DEFAULT_BACKEND_BASE_URL = "http://localhost:8000"
DEFAULT_HTTP_TIMEOUT_SECONDS = 30.0
DEFAULT_UPLOAD_TIMEOUT_SECONDS = 180.0
DEFAULT_PROCESSING_TIMEOUT_SECONDS = 600.0
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_JOB_LIST_WINDOW_DAYS = 7
DEFAULT_JOB_LIST_LIMIT = 50


class AddLiteratureUploadSmokeFailure(RuntimeError):
    """Raised when the Add Literature upload smoke cannot complete safely."""


@dataclass(frozen=True)
class AddLiteratureUploadSmokeConfig:
    repo_root: Path
    backend_base_url: str
    aws_profile: str | None
    region: str
    user_pool_id: str
    client_id: str
    client_secret: str | None
    authorized_groups: tuple[str, ...]
    evidence_dir: Path
    sample_pdf: Path
    http_timeout_seconds: float
    upload_timeout_seconds: float
    processing_timeout_seconds: float
    poll_interval_seconds: float
    aws_api_timeout_seconds: float
    evidence_tail_limit: int
    job_list_window_days: int
    job_list_limit: int
    keep_document: bool
    allow_duplicate_reuse: bool
    curator_username: str | None
    curator_password: str | None
    curator_id_token: str | None


@dataclass
class AddLiteratureUploadSmokeRunResult:
    exit_code: int
    evidence_path: Path
    evidence: dict[str, Any]


HttpRequester = Callable[..., dev_smoke.Response]
AwsClientFactory = Callable[[live_smoke.SmokeConfig], live_smoke.AwsSmokeClient]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp_for_file(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ")


def _repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AddLiteratureUploadSmokeFailure(message)


def _json_object_response(response: dev_smoke.Response, context: str) -> dict[str, Any]:
    if not isinstance(response.json_body, dict):
        raise AddLiteratureUploadSmokeFailure(
            f"{context} response was not a JSON object: {response.status_code} {response.text}"
        )
    return cast(dict[str, Any], response.json_body)


def _safe_json_payload(payload: Any) -> Any:
    return ready_smoke._safe_json_payload(payload)  # noqa: SLF001 - shared smoke helper


def _append_check(
    checks: list[dict[str, Any]],
    *,
    step: str,
    ok: bool,
    status_code: int,
    payload: Any,
) -> None:
    ready_smoke._append_check(  # noqa: SLF001 - shared smoke helper
        checks,
        step=step,
        ok=ok,
        status_code=status_code,
        payload=payload,
    )


def _redacted_evidence(evidence: dict[str, Any], secret_values: Sequence[str | None]) -> dict[str, Any]:
    serialized = json.dumps(evidence, default=str, sort_keys=True)
    redacted = live_smoke.redact_text(serialized, [value for value in secret_values if value])
    payload = json.loads(redacted)
    if not isinstance(payload, dict):
        raise AddLiteratureUploadSmokeFailure("Evidence redaction produced a non-object payload")
    return cast(dict[str, Any], payload)


def _env_first(*names: str, default: str = "") -> str:
    return ready_smoke._env_first(*names, default=default)  # noqa: SLF001 - shared smoke helper


def _parse_groups(value: str) -> tuple[str, ...]:
    return live_smoke._parse_groups(value)  # noqa: SLF001 - shared smoke helper


def _backend_cookie_headers(token: str) -> dict[str, str]:
    headers = ready_smoke._backend_cookie_headers(token)  # noqa: SLF001 - shared smoke helper
    headers["User-Agent"] = "agr-ai-curation-add-literature-upload-smoke/1.0"
    return headers


def _default_sample_pdf(repo_root: Path) -> Path:
    configured = _env_first("ADD_LITERATURE_UPLOAD_SMOKE_SAMPLE_PDF", "AGR_SMOKE_SAMPLE_PDF")
    if configured:
        path = Path(os.path.expandvars(configured)).expanduser()
        if not path.is_absolute():
            path = repo_root / path
        return path.resolve()
    candidates = (
        repo_root / "sample_fly_publication.pdf",
        repo_root / "backend/tests/fixtures/sample_fly_publication.pdf",
        repo_root / "backend/tests/fixtures/micropub-biology-001725.pdf",
        repo_root / "backend/tests/fixtures/live_tiny_chat.pdf",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return candidates[1].resolve()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    env_file_default = os.getenv(
        "ADD_LITERATURE_UPLOAD_SMOKE_ENV_FILE",
        os.getenv(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_ENV_FILE",
            str(ready_smoke.DEFAULT_LOCAL_ENV_FILE),
        ),
    )
    env_parser = argparse.ArgumentParser(add_help=False)
    env_parser.add_argument("--env-file", default=env_file_default)
    env_args, _ = env_parser.parse_known_args(argv)
    if env_args.env_file:
        os.environ["ABC_LITERATURE_READY_UPLOAD_SMOKE_ENV_FILE"] = env_args.env_file
        ready_smoke._LOCAL_ENV_CACHE.clear()  # noqa: SLF001 - script-level cache

    repo_root = _repo_root_from_script()
    default_aws_profile = _env_first(
        "ADD_LITERATURE_UPLOAD_SMOKE_AWS_PROFILE",
        "ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_PROFILE",
        "ABC_LITERATURE_SMOKE_AWS_PROFILE",
        "AWS_PROFILE",
        default="ctabone",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Authenticate as an existing fake Cognito curator, upload a repo sample "
            "PDF through the Add Literature/manual upload path, wait for processing, "
            "verify PDF/chunks/job visibility, clean up, and write evidence JSON."
        )
    )
    parser.add_argument(
        "--backend-base-url",
        default=_env_first(
            "ADD_LITERATURE_UPLOAD_SMOKE_BACKEND_BASE_URL",
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_BACKEND_BASE_URL",
            "AI_CURATION_BACKEND_BASE_URL",
            default=DEFAULT_BACKEND_BASE_URL,
        ),
    )
    parser.add_argument("--env-file", default=env_args.env_file)
    parser.add_argument("--aws-profile", default=default_aws_profile)
    parser.add_argument(
        "--region",
        default=_env_first(
            "ADD_LITERATURE_UPLOAD_SMOKE_AWS_REGION",
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_REGION",
            "ABC_LITERATURE_SMOKE_AWS_REGION",
            default=live_smoke.DEFAULT_AWS_REGION,
        ),
    )
    parser.add_argument(
        "--user-pool-id",
        default=_env_first(
            "ADD_LITERATURE_UPLOAD_SMOKE_USER_POOL_ID",
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_USER_POOL_ID",
            "ABC_LITERATURE_SMOKE_USER_POOL_ID",
            default=live_smoke.DEFAULT_USER_POOL_ID,
        ),
    )
    parser.add_argument(
        "--client-id",
        default=_env_first(
            "ADD_LITERATURE_UPLOAD_SMOKE_CLIENT_ID",
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_CLIENT_ID",
            "ABC_LITERATURE_SMOKE_CLIENT_ID",
            default=live_smoke.DEFAULT_CLIENT_ID,
        ),
    )
    parser.add_argument(
        "--client-secret",
        default=_env_first(
            "ADD_LITERATURE_UPLOAD_SMOKE_CLIENT_SECRET",
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_CLIENT_SECRET",
            "ABC_LITERATURE_SMOKE_CLIENT_SECRET",
            default="",
        ),
        help="Optional Cognito app client secret. Never written to evidence.",
    )
    parser.add_argument(
        "--authorized-groups",
        default=_env_first(
            "ADD_LITERATURE_UPLOAD_SMOKE_AUTHORIZED_GROUPS",
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_AUTHORIZED_GROUPS",
            "ABC_LITERATURE_SMOKE_AUTHORIZED_GROUPS",
            default=",".join(live_smoke.DEFAULT_AUTHORIZED_GROUPS),
        ),
    )
    parser.add_argument(
        "--evidence-dir",
        default=_env_first(
            "ADD_LITERATURE_UPLOAD_SMOKE_EVIDENCE_DIR",
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_EVIDENCE_DIR",
            "ABC_LITERATURE_SMOKE_EVIDENCE_DIR",
            default=str(live_smoke.DEFAULT_EVIDENCE_DIR),
        ),
    )
    parser.add_argument("--sample-pdf", default=str(_default_sample_pdf(repo_root)))
    parser.add_argument(
        "--http-timeout-seconds",
        type=float,
        default=float(
            _env_first(
                "ADD_LITERATURE_UPLOAD_SMOKE_HTTP_TIMEOUT_SECONDS",
                "ABC_LITERATURE_READY_UPLOAD_SMOKE_HTTP_TIMEOUT_SECONDS",
                default=str(DEFAULT_HTTP_TIMEOUT_SECONDS),
            )
        ),
    )
    parser.add_argument(
        "--upload-timeout-seconds",
        type=float,
        default=float(
            _env_first(
                "ADD_LITERATURE_UPLOAD_SMOKE_UPLOAD_TIMEOUT_SECONDS",
                "ABC_LITERATURE_READY_UPLOAD_SMOKE_UPLOAD_TIMEOUT_SECONDS",
                default=str(DEFAULT_UPLOAD_TIMEOUT_SECONDS),
            )
        ),
    )
    parser.add_argument(
        "--processing-timeout-seconds",
        type=float,
        default=float(
            _env_first(
                "ADD_LITERATURE_UPLOAD_SMOKE_PROCESSING_TIMEOUT_SECONDS",
                default=str(DEFAULT_PROCESSING_TIMEOUT_SECONDS),
            )
        ),
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=float(
            _env_first(
                "ADD_LITERATURE_UPLOAD_SMOKE_POLL_INTERVAL_SECONDS",
                "ABC_LITERATURE_READY_UPLOAD_SMOKE_POLL_INTERVAL_SECONDS",
                default=str(DEFAULT_POLL_INTERVAL_SECONDS),
            )
        ),
    )
    parser.add_argument(
        "--aws-api-timeout-seconds",
        type=float,
        default=float(
            _env_first(
                "ADD_LITERATURE_UPLOAD_SMOKE_AWS_API_TIMEOUT_SECONDS",
                "ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_API_TIMEOUT_SECONDS",
                "ABC_LITERATURE_SMOKE_AWS_API_TIMEOUT_SECONDS",
                default=str(live_smoke.DEFAULT_AWS_API_TIMEOUT_SECONDS),
            )
        ),
    )
    parser.add_argument(
        "--evidence-tail-limit",
        type=int,
        default=int(
            _env_first(
                "ADD_LITERATURE_UPLOAD_SMOKE_EVIDENCE_TAIL_LIMIT",
                "ABC_LITERATURE_READY_UPLOAD_SMOKE_EVIDENCE_TAIL_LIMIT",
                "ABC_LITERATURE_SMOKE_EVIDENCE_TAIL_LIMIT",
                default=str(live_smoke.DEFAULT_EVIDENCE_TAIL_LIMIT),
            )
        ),
    )
    parser.add_argument(
        "--job-list-window-days",
        type=int,
        default=int(
            _env_first(
                "ADD_LITERATURE_UPLOAD_SMOKE_JOB_LIST_WINDOW_DAYS",
                default=str(DEFAULT_JOB_LIST_WINDOW_DAYS),
            )
        ),
    )
    parser.add_argument(
        "--job-list-limit",
        type=int,
        default=int(
            _env_first(
                "ADD_LITERATURE_UPLOAD_SMOKE_JOB_LIST_LIMIT",
                default=str(DEFAULT_JOB_LIST_LIMIT),
            )
        ),
    )
    parser.add_argument(
        "--curator-username",
        default=_env_first(
            "ADD_LITERATURE_UPLOAD_SMOKE_CURATOR_USERNAME",
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_USERNAME",
            default="",
        ),
        help="Existing Cognito fake curator username. Never written with secrets.",
    )
    parser.add_argument(
        "--curator-password",
        default=_env_first(
            "ADD_LITERATURE_UPLOAD_SMOKE_CURATOR_PASSWORD",
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_PASSWORD",
            default="",
        ),
        help="Existing Cognito fake curator password. Never written to evidence.",
    )
    parser.add_argument(
        "--curator-id-token",
        default=_env_first(
            "ADD_LITERATURE_UPLOAD_SMOKE_CURATOR_ID_TOKEN",
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_ID_TOKEN",
            default="",
        ),
        help="Optional existing curator IdToken.",
    )
    parser.add_argument("--keep-document", action="store_true")
    parser.add_argument(
        "--allow-duplicate-reuse",
        action="store_true",
        help=(
            "Debug only: reuse an existing matching document instead of requiring "
            "the smoke to create a fresh upload."
        ),
    )
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> AddLiteratureUploadSmokeConfig:
    repo_root = _repo_root_from_script()
    evidence_dir = Path(args.evidence_dir)
    if not evidence_dir.is_absolute():
        evidence_dir = repo_root / evidence_dir
    sample_pdf = Path(args.sample_pdf).expanduser()
    if not sample_pdf.is_absolute():
        sample_pdf = repo_root / sample_pdf
    sample_pdf = sample_pdf.resolve()
    _require(sample_pdf.is_file(), f"Sample PDF not found: {sample_pdf}")

    return AddLiteratureUploadSmokeConfig(
        repo_root=repo_root,
        backend_base_url=args.backend_base_url.rstrip("/"),
        aws_profile=args.aws_profile.strip() or None,
        region=args.region,
        user_pool_id=args.user_pool_id,
        client_id=args.client_id,
        client_secret=args.client_secret or None,
        authorized_groups=_parse_groups(args.authorized_groups),
        evidence_dir=evidence_dir,
        sample_pdf=sample_pdf,
        http_timeout_seconds=args.http_timeout_seconds,
        upload_timeout_seconds=args.upload_timeout_seconds,
        processing_timeout_seconds=args.processing_timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        aws_api_timeout_seconds=args.aws_api_timeout_seconds,
        evidence_tail_limit=args.evidence_tail_limit,
        job_list_window_days=args.job_list_window_days,
        job_list_limit=args.job_list_limit,
        keep_document=args.keep_document,
        allow_duplicate_reuse=args.allow_duplicate_reuse,
        curator_username=args.curator_username.strip() or None,
        curator_password=args.curator_password or None,
        curator_id_token=args.curator_id_token.strip() or None,
    )


def _to_live_config(config: AddLiteratureUploadSmokeConfig) -> live_smoke.SmokeConfig:
    return live_smoke.SmokeConfig(
        repo_root=config.repo_root,
        aws_profile=config.aws_profile,
        region=config.region,
        user_pool_id=config.user_pool_id,
        client_id=config.client_id,
        client_secret=config.client_secret,
        base_url=live_smoke.DEFAULT_BASE_URL,
        authorized_groups=config.authorized_groups,
        evidence_dir=config.evidence_dir,
        pytest_timeout_seconds=live_smoke.DEFAULT_PYTEST_TIMEOUT_SECONDS,
        literature_timeout_seconds=DEFAULT_HTTP_TIMEOUT_SECONDS,
        aws_api_timeout_seconds=config.aws_api_timeout_seconds,
        evidence_tail_limit=config.evidence_tail_limit,
        keep_users=False,
        user_prefix="unused-existing-curator",
        unknown_md5=live_smoke.DEFAULT_UNKNOWN_MD5,
        known_md5=live_smoke.DEFAULT_KNOWN_MD5,
        restricted_md5=live_smoke.DEFAULT_RESTRICTED_MD5,
        pmid=live_smoke.DEFAULT_PMID,
        reference=live_smoke.DEFAULT_REFERENCE,
        source_referencefile_id=live_smoke.DEFAULT_SOURCE_REFERENCEFILE_ID,
        converted_referencefile_id=live_smoke.DEFAULT_CONVERTED_REFERENCEFILE_ID,
        python_executable=sys.executable,
    )


def _existing_curator_token(
    *,
    config: AddLiteratureUploadSmokeConfig,
    live_config: live_smoke.SmokeConfig,
    aws_client: live_smoke.AwsSmokeClient | None,
) -> tuple[str, dict[str, Any], str | None]:
    if config.curator_id_token:
        return (
            config.curator_id_token,
            ready_smoke._curator_evidence(  # noqa: SLF001 - shared smoke helper
                username=config.curator_username,
                auth_source="curator_id_token",
                authorized_groups=config.authorized_groups,
            ),
            None,
        )

    if not config.curator_username or not config.curator_password:
        raise AddLiteratureUploadSmokeFailure(
            "Add Literature upload smoke requires an existing fake Cognito curator. "
            "Set ADD_LITERATURE_UPLOAD_SMOKE_CURATOR_USERNAME/"
            "ADD_LITERATURE_UPLOAD_SMOKE_CURATOR_PASSWORD or the corresponding "
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_* values in the local .env, or provide "
            "ADD_LITERATURE_UPLOAD_SMOKE_CURATOR_ID_TOKEN."
        )
    if aws_client is None:
        raise AddLiteratureUploadSmokeFailure("AWS client unavailable for Cognito curator auth")

    token = live_smoke.token_for_user(
        live_config,
        username=config.curator_username,
        password=config.curator_password,
        aws_client=aws_client,
    )
    return (
        token,
        ready_smoke._curator_evidence(  # noqa: SLF001 - shared smoke helper
            username=config.curator_username,
            auth_source="curator_username_password",
            authorized_groups=config.authorized_groups,
        ),
        config.curator_password,
    )


def check_backend_health(
    *,
    config: AddLiteratureUploadSmokeConfig,
    requester: HttpRequester,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    response = requester(
        "GET",
        f"{config.backend_base_url}/weaviate/health",
        headers={"Accept": "application/json"},
        timeout=config.http_timeout_seconds,
    )
    payload = ready_smoke._health_payload(response)  # noqa: SLF001 - shared smoke helper
    _append_check(
        checks,
        step="backend_weaviate_health",
        ok=response.status_code == 200,
        status_code=response.status_code,
        payload=payload,
    )
    _require(response.status_code == 200, f"Backend health is not ready: {response.status_code} {response.text}")
    _require(
        bool(payload.get("cognito_configured")),
        "Backend health does not report Cognito configured; smoke needs real cookie auth.",
    )
    return payload


def check_current_user(
    *,
    config: AddLiteratureUploadSmokeConfig,
    token: str,
    requester: HttpRequester,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    response = requester(
        "GET",
        f"{config.backend_base_url}/api/users/me",
        headers=_backend_cookie_headers(token),
        timeout=config.http_timeout_seconds,
    )
    _require(response.status_code == 200, f"Unexpected current-user response: {response.status_code} {response.text}")
    payload = _json_object_response(response, "current-user")
    auth_sub = str(payload.get("auth_sub") or payload.get("sub") or "").strip()
    _require(
        bool(auth_sub and not auth_sub.startswith("api-key-")),
        f"Current user did not come from real Cognito cookie auth: {payload}",
    )
    provider_groups = payload.get("provider_groups") or []
    if isinstance(provider_groups, str):
        provider_groups = [provider_groups]
    _require(
        any(group in set(provider_groups) for group in config.authorized_groups),
        (
            "Current user does not expose the expected authorized provider group. "
            f"Expected one of {config.authorized_groups!r}, got {provider_groups!r}"
        ),
    )
    _append_check(
        checks,
        step="backend_current_user_cookie_auth",
        ok=True,
        status_code=response.status_code,
        payload=payload,
    )
    return payload


def upload_sample_pdf(
    *,
    config: AddLiteratureUploadSmokeConfig,
    token: str,
    requester: HttpRequester,
    checks: list[dict[str, Any]],
) -> tuple[str, str | None, bool, dict[str, Any]]:
    body, boundary = dev_smoke.encode_multipart_form({}, "file", config.sample_pdf)
    headers = _backend_cookie_headers(token)
    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    response = requester(
        "POST",
        f"{config.backend_base_url}/weaviate/documents/upload",
        headers=headers,
        data=body,
        timeout=config.upload_timeout_seconds,
    )

    if response.status_code == 201:
        payload = _json_object_response(response, "sample PDF upload")
        document_id = str(payload.get("document_id") or "").strip()
        _require(bool(document_id), f"Upload response missing document_id: {response.text}")
        job_id = str(payload.get("job_id") or "").strip() or None
        _append_check(
            checks,
            step="backend_sample_pdf_upload",
            ok=True,
            status_code=response.status_code,
            payload=payload,
        )
        return document_id, job_id, True, payload

    if response.status_code == 409 and config.allow_duplicate_reuse:
        payload = response.json_body if isinstance(response.json_body, dict) else {}
        document_id = dev_smoke.extract_existing_document_id(payload)
        _require(bool(document_id), f"Duplicate upload missing existing document id: {response.text}")
        _append_check(
            checks,
            step="backend_sample_pdf_upload_duplicate_reused",
            ok=True,
            status_code=response.status_code,
            payload=payload,
        )
        return document_id, None, False, payload

    _require(False, f"Sample PDF upload failed: {response.status_code} {response.text}")
    raise AssertionError("unreachable")


def wait_for_processing_complete(
    *,
    config: AddLiteratureUploadSmokeConfig,
    token: str,
    document_id: str,
    requester: HttpRequester,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    status_url = f"{config.backend_base_url}/weaviate/documents/{document_id}/status"
    deadline = time.monotonic() + config.processing_timeout_seconds
    last_payload: dict[str, Any] | None = None

    while time.monotonic() < deadline:
        response = requester(
            "GET",
            status_url,
            headers=_backend_cookie_headers(token),
            timeout=config.http_timeout_seconds,
        )
        _require(
            response.status_code == 200,
            f"Unexpected status response for {document_id}: {response.status_code} {response.text}",
        )
        payload = _json_object_response(response, "document status")
        last_payload = payload
        processing_status = str(payload.get("processing_status") or "").strip().lower()
        if processing_status == "completed":
            _append_check(
                checks,
                step="backend_processing_completed",
                ok=True,
                status_code=response.status_code,
                payload=payload,
            )
            return payload
        if processing_status == "failed":
            raise AddLiteratureUploadSmokeFailure(
                f"Sample PDF processing failed: {json.dumps(payload, sort_keys=True)}"
            )
        time.sleep(config.poll_interval_seconds)

    raise AddLiteratureUploadSmokeFailure(
        "Timed out waiting for sample PDF processing to complete; "
        f"last payload: {json.dumps(last_payload or {}, sort_keys=True)}"
    )


def fetch_download_info(
    *,
    config: AddLiteratureUploadSmokeConfig,
    token: str,
    document_id: str,
    requester: HttpRequester,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    response = requester(
        "GET",
        f"{config.backend_base_url}/weaviate/documents/{document_id}/download-info",
        headers=_backend_cookie_headers(token),
        timeout=config.http_timeout_seconds,
    )
    _require(response.status_code == 200, f"Unexpected download-info response: {response.status_code} {response.text}")
    payload = _json_object_response(response, "download-info")
    _require(payload.get("pdf_available") is True, f"PDF unavailable after manual upload: {payload}")
    _require(int(payload.get("pdf_size") or 0) > 0, f"PDF size is not positive: {payload}")
    _append_check(
        checks,
        step="backend_download_info_pdf_available",
        ok=True,
        status_code=response.status_code,
        payload=payload,
    )
    return payload


def fetch_chunks(
    *,
    config: AddLiteratureUploadSmokeConfig,
    token: str,
    document_id: str,
    requester: HttpRequester,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    response = requester(
        "GET",
        (
            f"{config.backend_base_url}/weaviate/documents/{document_id}/chunks"
            "?page=1&page_size=100&include_metadata=false"
        ),
        headers=_backend_cookie_headers(token),
        timeout=config.http_timeout_seconds,
    )
    _require(response.status_code == 200, f"Unexpected chunks response: {response.status_code} {response.text}")
    payload = _json_object_response(response, "chunks")
    total_items = int((payload.get("pagination") or {}).get("total_items") or 0)
    returned_chunks = len(payload.get("chunks") or [])
    _require(total_items > 0, f"Expected chunk total_items > 0: {payload}")
    _require(returned_chunks > 0, f"Expected returned chunk rows: {payload}")
    _append_check(
        checks,
        step="backend_chunks_available",
        ok=True,
        status_code=response.status_code,
        payload={
            "document_id": document_id,
            "total_items": total_items,
            "returned_chunks": returned_chunks,
        },
    )
    return payload


def fetch_pdf_jobs(
    *,
    config: AddLiteratureUploadSmokeConfig,
    token: str,
    document_id: str,
    expected_job_id: str | None,
    requester: HttpRequester,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    response = requester(
        "GET",
        (
            f"{config.backend_base_url}/weaviate/pdf-jobs"
            f"?window_days={config.job_list_window_days}"
            f"&limit={config.job_list_limit}&offset=0"
        ),
        headers=_backend_cookie_headers(token),
        timeout=config.http_timeout_seconds,
    )
    _require(response.status_code == 200, f"Unexpected pdf-jobs response: {response.status_code} {response.text}")
    payload = _json_object_response(response, "pdf-jobs")
    jobs = payload.get("jobs")
    _require(isinstance(jobs, list), f"pdf-jobs payload missing jobs list: {payload}")
    matching_jobs = [
        job
        for job in jobs
        if isinstance(job, dict)
        and str(job.get("document_id") or "") == document_id
        and (not expected_job_id or str(job.get("job_id") or "") == expected_job_id)
    ]
    _require(
        bool(matching_jobs),
        f"Uploaded document did not appear in durable pdf-jobs list: document_id={document_id}, jobs={jobs}",
    )
    latest = matching_jobs[0]
    _append_check(
        checks,
        step="backend_pdf_jobs_visible",
        ok=True,
        status_code=response.status_code,
        payload={
            "document_id": document_id,
            "expected_job_id": expected_job_id,
            "matching_job": latest,
            "total": payload.get("total"),
        },
    )
    return latest


def assert_pdf_download_available(
    *,
    config: AddLiteratureUploadSmokeConfig,
    token: str,
    document_id: str,
    expected_pdf_bytes: bytes,
    requester: HttpRequester,
    checks: list[dict[str, Any]],
) -> None:
    response = requester(
        "GET",
        f"{config.backend_base_url}/weaviate/documents/{document_id}/download/pdf",
        headers=_backend_cookie_headers(token),
        timeout=config.http_timeout_seconds,
    )
    _require(response.status_code == 200, f"Unexpected PDF download response: {response.status_code} {response.text[:500]}")
    _require(bool(response.body), "PDF download was empty")
    expected_md5 = hashlib.md5(expected_pdf_bytes).hexdigest()
    actual_md5 = hashlib.md5(response.body).hexdigest()
    _require(
        actual_md5 == expected_md5,
        f"Backend PDF download MD5 did not match sample PDF. Expected {expected_md5}, got {actual_md5}",
    )
    _append_check(
        checks,
        step="backend_pdf_download_available",
        ok=True,
        status_code=response.status_code,
        payload={
            "document_id": document_id,
            "byte_count": len(response.body),
            "md5": actual_md5,
            "matches_sample_pdf": True,
        },
    )


def delete_uploaded_document(
    *,
    config: AddLiteratureUploadSmokeConfig,
    token: str,
    document_id: str,
    requester: HttpRequester,
) -> dev_smoke.Response:
    return requester(
        "DELETE",
        f"{config.backend_base_url}/weaviate/documents/{document_id}",
        headers=_backend_cookie_headers(token),
        timeout=config.http_timeout_seconds,
    )


def fetch_document_detail_response(
    *,
    config: AddLiteratureUploadSmokeConfig,
    token: str,
    document_id: str,
    requester: HttpRequester,
) -> dev_smoke.Response:
    return requester(
        "GET",
        f"{config.backend_base_url}/weaviate/documents/{document_id}",
        headers=_backend_cookie_headers(token),
        timeout=config.http_timeout_seconds,
    )


def run_smoke(
    config: AddLiteratureUploadSmokeConfig,
    *,
    aws_client_factory: AwsClientFactory = live_smoke.Boto3AwsSmokeClient,
    requester: HttpRequester = dev_smoke.http_request,
    now: datetime | None = None,
) -> AddLiteratureUploadSmokeRunResult:
    now = now or _utc_now()
    stamp = _timestamp_for_file(now)
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = config.evidence_dir / f"add_literature_upload_smoke_{stamp}.json"
    live_config = _to_live_config(config)
    checks: list[dict[str, Any]] = []
    cleanup_failures: list[str] = []
    aws_client: live_smoke.AwsSmokeClient | None = None
    authorized_token: str | None = None
    curator_secret: str | None = None
    document_id: str | None = None
    document_created = False
    status = "fail"
    exit_code = 1

    sample_pdf_bytes = config.sample_pdf.read_bytes()
    sample_pdf_md5 = hashlib.md5(sample_pdf_bytes).hexdigest()

    evidence: dict[str, Any] = {
        "timestamp_utc": now.isoformat(),
        "overall_status": status,
        "backend_base_url": config.backend_base_url,
        "aws": {
            "profile": config.aws_profile,
            "region": config.region,
            "user_pool_id": config.user_pool_id,
            "client_id": config.client_id,
            "client_secret_provided": bool(config.client_secret),
            "api_timeout_seconds": config.aws_api_timeout_seconds,
        },
        "sample_pdf": {
            "path": str(config.sample_pdf),
            "filename": config.sample_pdf.name,
            "byte_count": len(sample_pdf_bytes),
            "md5": sample_pdf_md5,
            "sha256": hashlib.sha256(sample_pdf_bytes).hexdigest(),
        },
        "timeouts": {
            "http_timeout_seconds": config.http_timeout_seconds,
            "upload_timeout_seconds": config.upload_timeout_seconds,
            "processing_timeout_seconds": config.processing_timeout_seconds,
            "poll_interval_seconds": config.poll_interval_seconds,
        },
        "job_list": {
            "window_days": config.job_list_window_days,
            "limit": config.job_list_limit,
        },
        "cleanup": {
            "keep_document": config.keep_document,
            "failures": cleanup_failures,
        },
        "checks": checks,
    }

    try:
        if not config.curator_id_token:
            aws_client = aws_client_factory(live_config)
            caller_identity = aws_client.caller_identity()
            if isinstance(caller_identity, dict):
                evidence["aws"]["caller_identity"] = {
                    "account": caller_identity.get("Account"),
                    "arn": caller_identity.get("Arn"),
                    "user_id": caller_identity.get("UserId"),
                }

            if not live_config.client_secret:
                discovered_secret = aws_client.discover_client_secret()
                if discovered_secret:
                    live_config = replace(live_config, client_secret=discovered_secret)
                    evidence["aws"]["client_secret_provided"] = True
                    evidence["aws"]["client_secret_source"] = "describe-user-pool-client"
                else:
                    evidence["aws"]["client_secret_source"] = "not_available"
            else:
                evidence["aws"]["client_secret_source"] = "provided"
        else:
            evidence["aws"]["client_secret_source"] = "not_needed_for_id_token"

        curator_secret = config.curator_password
        authorized_token, curator_payload, curator_secret = _existing_curator_token(
            config=config,
            live_config=live_config,
            aws_client=aws_client,
        )
        evidence["smoke_user"] = curator_payload

        check_backend_health(config=config, requester=requester, checks=checks)
        check_current_user(config=config, token=authorized_token, requester=requester, checks=checks)

        document_id, job_id, document_created, upload_payload = upload_sample_pdf(
            config=config,
            token=authorized_token,
            requester=requester,
            checks=checks,
        )
        evidence["document"] = {
            "document_id": document_id,
            "job_id": job_id,
            "created": document_created,
            "upload_payload": _safe_json_payload(upload_payload),
        }
        final_status = wait_for_processing_complete(
            config=config,
            token=authorized_token,
            document_id=document_id,
            requester=requester,
            checks=checks,
        )
        evidence["document"]["final_status"] = _safe_json_payload(final_status)
        fetch_pdf_jobs(
            config=config,
            token=authorized_token,
            document_id=document_id,
            expected_job_id=job_id,
            requester=requester,
            checks=checks,
        )
        download_info = fetch_download_info(
            config=config,
            token=authorized_token,
            document_id=document_id,
            requester=requester,
            checks=checks,
        )
        evidence["document"]["download_info"] = _safe_json_payload(download_info)
        chunks_payload = fetch_chunks(
            config=config,
            token=authorized_token,
            document_id=document_id,
            requester=requester,
            checks=checks,
        )
        evidence["document"]["chunk_total"] = int((chunks_payload.get("pagination") or {}).get("total_items") or 0)
        assert_pdf_download_available(
            config=config,
            token=authorized_token,
            document_id=document_id,
            expected_pdf_bytes=sample_pdf_bytes,
            requester=requester,
            checks=checks,
        )

        status = "pass"
        exit_code = 0
    except Exception as exc:  # noqa: BLE001 - smoke runner records all failures
        evidence["failure"] = {
            "type": type(exc).__name__,
            "message": ready_smoke._tail_text(str(exc), config.evidence_tail_limit),  # noqa: SLF001
        }
    finally:
        if document_id and authorized_token and document_created and not config.keep_document:
            delete_response = delete_uploaded_document(
                config=config,
                token=authorized_token,
                document_id=document_id,
                requester=requester,
            )
            cleanup_payload = delete_response.json_body if delete_response.json_body is not None else delete_response.text
            evidence["cleanup"]["document"] = {
                "document_id": document_id,
                "status_code": delete_response.status_code,
                "payload": _safe_json_payload(cleanup_payload),
                "deleted": delete_response.status_code == 200,
            }
            if delete_response.status_code != 200:
                cleanup_failures.append(
                    f"Deleting document {document_id} failed: {delete_response.status_code} {delete_response.text}"
                )
                status = "fail"
                exit_code = 1
            else:
                verify_response = fetch_document_detail_response(
                    config=config,
                    token=authorized_token,
                    document_id=document_id,
                    requester=requester,
                )
                verified_deleted = verify_response.status_code == 404
                evidence["cleanup"]["document"]["verified_deleted"] = verified_deleted
                if not verified_deleted:
                    cleanup_failures.append(
                        f"Deleted document {document_id} still returned {verify_response.status_code}"
                    )
                    status = "fail"
                    exit_code = 1
        elif document_id and not document_created:
            evidence["cleanup"]["document"] = {
                "document_id": document_id,
                "deleted": False,
                "reason": "document was reused, not created by this smoke",
            }
        elif document_id and config.keep_document:
            evidence["cleanup"]["document"] = {
                "document_id": document_id,
                "deleted": False,
                "reason": "--keep-document was set",
            }

        evidence["overall_status"] = status
        secret_values = [
            authorized_token,
            curator_secret,
            config.curator_id_token,
            config.client_secret,
            live_config.client_secret,
        ]
        redacted = _redacted_evidence(evidence, secret_values)
        evidence_path.write_text(json.dumps(redacted, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"[add-literature-upload-smoke] evidence: {evidence_path}", flush=True)
    print(f"[add-literature-upload-smoke] status: {status}", flush=True)
    return AddLiteratureUploadSmokeRunResult(exit_code=exit_code, evidence_path=evidence_path, evidence=redacted)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = config_from_args(args)
    result = run_smoke(config)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
