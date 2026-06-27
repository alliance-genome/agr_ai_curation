#!/usr/bin/env python3
"""Run a durable AI Curation upload smoke for ABC Literature READY imports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence, cast

_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from scripts.testing import abc_literature_live_smoke as live_smoke  # noqa: E402
from scripts.testing import dev_release_smoke as dev_smoke  # noqa: E402


DEFAULT_BACKEND_BASE_URL = "http://localhost:8000"
DEFAULT_HTTP_TIMEOUT_SECONDS = 30.0
DEFAULT_UPLOAD_TIMEOUT_SECONDS = 180.0
DEFAULT_PROCESSING_TIMEOUT_SECONDS = 240.0
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_SOURCE_PDF_FILENAME = "abc-literature-ready-smoke-23970418.pdf"
DEFAULT_LOCAL_ENV_FILE = Path.home() / ".agr_ai_curation" / ".env"
_LOCAL_ENV_CACHE: dict[Path, dict[str, str]] = {}


class ReadyUploadSmokeFailure(RuntimeError):
    """Raised when the READY upload smoke cannot complete safely."""


@dataclass(frozen=True)
class ReadyUploadSmokeConfig:
    repo_root: Path
    backend_base_url: str
    literature_base_url: str
    aws_profile: str | None
    region: str
    user_pool_id: str
    client_id: str
    client_secret: str | None
    authorized_groups: tuple[str, ...]
    evidence_dir: Path
    http_timeout_seconds: float
    upload_timeout_seconds: float
    processing_timeout_seconds: float
    poll_interval_seconds: float
    aws_api_timeout_seconds: float
    evidence_tail_limit: int
    keep_document: bool
    curator_username: str | None
    curator_password: str | None
    curator_id_token: str | None
    known_md5: str
    pmid: str
    reference: str
    source_referencefile_id: str
    converted_referencefile_id: str
    source_pdf_filename: str


@dataclass
class ReadyUploadSmokeRunResult:
    exit_code: int
    evidence_path: Path
    evidence: dict[str, Any]


HttpRequester = Callable[..., dev_smoke.Response]
AwsClientFactory = Callable[[live_smoke.SmokeConfig], live_smoke.AwsSmokeClient]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp_for_file(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ")


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _local_env_values() -> dict[str, str]:
    raw_path = os.getenv("ABC_LITERATURE_READY_UPLOAD_SMOKE_ENV_FILE")
    path = Path(os.path.expandvars(raw_path)).expanduser() if raw_path else DEFAULT_LOCAL_ENV_FILE
    if not path.is_absolute():
        path = _repo_root_from_script() / path
    if path not in _LOCAL_ENV_CACHE:
        _LOCAL_ENV_CACHE[path] = _parse_env_file(path)
    return _LOCAL_ENV_CACHE[path]


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    local_env = _local_env_values()
    for name in names:
        value = local_env.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _parse_groups(value: str) -> tuple[str, ...]:
    return live_smoke._parse_groups(value)


def _repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def _redact_text(value: str, secret_values: Iterable[str]) -> str:
    return live_smoke.redact_text(value, secret_values)


def _tail_text(value: str, limit: int) -> str:
    return live_smoke.tail_text(value, limit)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReadyUploadSmokeFailure(message)


def _safe_json_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {str(key): _safe_json_payload(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_safe_json_payload(item) for item in payload]
    if isinstance(payload, (str, int, float, bool)) or payload is None:
        return payload
    return str(payload)


def _redacted_evidence(evidence: dict[str, Any], secret_values: Iterable[str]) -> dict[str, Any]:
    """Return a JSON-safe evidence copy with runtime secrets removed everywhere."""
    serialized = json.dumps(evidence, default=str, sort_keys=True)
    redacted = _redact_text(serialized, secret_values)
    payload = json.loads(redacted)
    if not isinstance(payload, dict):
        raise ReadyUploadSmokeFailure("Evidence redaction produced a non-object payload")
    return payload


def _append_check(
    checks: list[dict[str, Any]],
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
            "payload": _safe_json_payload(payload),
        }
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    env_file_default = os.getenv(
        "ABC_LITERATURE_READY_UPLOAD_SMOKE_ENV_FILE",
        str(DEFAULT_LOCAL_ENV_FILE),
    )
    env_parser = argparse.ArgumentParser(add_help=False)
    env_parser.add_argument("--env-file", default=env_file_default)
    env_args, _ = env_parser.parse_known_args(argv)
    if env_args.env_file:
        os.environ["ABC_LITERATURE_READY_UPLOAD_SMOKE_ENV_FILE"] = env_args.env_file
        _LOCAL_ENV_CACHE.clear()

    default_aws_profile = _env_first(
        "ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_PROFILE",
        "ABC_LITERATURE_SMOKE_AWS_PROFILE",
        "AWS_PROFILE",
        default="ctabone",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Authenticate as an existing test Cognito curator, download the known "
            "ABC Literature READY source PDF, upload it through AI Curation, wait "
            "for provider Markdown ingestion, delete the document, and write "
            "evidence JSON."
        )
    )
    parser.add_argument(
        "--backend-base-url",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_BACKEND_BASE_URL",
            "AI_CURATION_BACKEND_BASE_URL",
            default=DEFAULT_BACKEND_BASE_URL,
        ),
    )
    parser.add_argument(
        "--env-file",
        default=env_args.env_file,
        help=(
            "Local uncommitted .env file consulted for defaults before parsing. "
            "Defaults to ~/.agr_ai_curation/.env or "
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_ENV_FILE."
        ),
    )
    parser.add_argument(
        "--literature-base-url",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_LITERATURE_BASE_URL",
            "ABC_LITERATURE_LIVE_BASE_URL",
            "ABC_LITERATURE_SMOKE_BASE_URL",
            default=live_smoke.DEFAULT_BASE_URL,
        ),
    )
    parser.add_argument("--aws-profile", default=default_aws_profile)
    parser.add_argument(
        "--region",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_REGION",
            "ABC_LITERATURE_SMOKE_AWS_REGION",
            default=live_smoke.DEFAULT_AWS_REGION,
        ),
    )
    parser.add_argument(
        "--user-pool-id",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_USER_POOL_ID",
            "ABC_LITERATURE_SMOKE_USER_POOL_ID",
            default=live_smoke.DEFAULT_USER_POOL_ID,
        ),
    )
    parser.add_argument(
        "--client-id",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_CLIENT_ID",
            "ABC_LITERATURE_SMOKE_CLIENT_ID",
            default=live_smoke.DEFAULT_CLIENT_ID,
        ),
    )
    parser.add_argument(
        "--client-secret",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_CLIENT_SECRET",
            "ABC_LITERATURE_SMOKE_CLIENT_SECRET",
            default="",
        ),
        help="Optional Cognito app client secret. Never written to evidence.",
    )
    parser.add_argument(
        "--authorized-groups",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_AUTHORIZED_GROUPS",
            "ABC_LITERATURE_SMOKE_AUTHORIZED_GROUPS",
            default=",".join(live_smoke.DEFAULT_AUTHORIZED_GROUPS),
        ),
    )
    parser.add_argument(
        "--evidence-dir",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_EVIDENCE_DIR",
            "ABC_LITERATURE_SMOKE_EVIDENCE_DIR",
            default=str(live_smoke.DEFAULT_EVIDENCE_DIR),
        ),
    )
    parser.add_argument(
        "--http-timeout-seconds",
        type=float,
        default=float(
            _env_first(
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
                "ABC_LITERATURE_READY_UPLOAD_SMOKE_PROCESSING_TIMEOUT_SECONDS",
                default=str(DEFAULT_PROCESSING_TIMEOUT_SECONDS),
            )
        ),
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=float(
            _env_first(
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
                "ABC_LITERATURE_READY_UPLOAD_SMOKE_EVIDENCE_TAIL_LIMIT",
                "ABC_LITERATURE_SMOKE_EVIDENCE_TAIL_LIMIT",
                default=str(live_smoke.DEFAULT_EVIDENCE_TAIL_LIMIT),
            )
        ),
    )
    parser.add_argument(
        "--curator-username",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_USERNAME",
            default="",
        ),
        help="Existing Cognito curator username. Never written to evidence with secrets.",
    )
    parser.add_argument(
        "--curator-password",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_PASSWORD",
            default="",
        ),
        help="Existing Cognito curator password. Never written to evidence.",
    )
    parser.add_argument(
        "--curator-id-token",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_ID_TOKEN",
            default="",
        ),
        help="Optional existing curator IdToken. Prefer username/password in .env for repeatable runs.",
    )
    parser.add_argument(
        "--keep-document",
        action="store_true",
        help="Debug only: leave the uploaded AI Curation document in place.",
    )
    parser.add_argument(
        "--known-md5",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_KNOWN_MD5",
            "ABC_LITERATURE_LIVE_KNOWN_MD5",
            "ABC_LITERATURE_SMOKE_KNOWN_MD5",
            default=live_smoke.DEFAULT_KNOWN_MD5,
        ),
    )
    parser.add_argument(
        "--pmid",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_PMID",
            "ABC_LITERATURE_LIVE_PMID",
            "ABC_LITERATURE_SMOKE_PMID",
            default=live_smoke.DEFAULT_PMID,
        ),
    )
    parser.add_argument(
        "--reference",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_REFERENCE",
            "ABC_LITERATURE_LIVE_REFERENCE",
            "ABC_LITERATURE_SMOKE_REFERENCE",
            default=live_smoke.DEFAULT_REFERENCE,
        ),
    )
    parser.add_argument(
        "--source-referencefile-id",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_SOURCE_REFERENCEFILE_ID",
            "ABC_LITERATURE_SMOKE_SOURCE_REFERENCEFILE_ID",
            default=live_smoke.DEFAULT_SOURCE_REFERENCEFILE_ID,
        ),
    )
    parser.add_argument(
        "--converted-referencefile-id",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_CONVERTED_REFERENCEFILE_ID",
            "ABC_LITERATURE_LIVE_CONVERTED_REFERENCEFILE_ID",
            "ABC_LITERATURE_SMOKE_CONVERTED_REFERENCEFILE_ID",
            default=live_smoke.DEFAULT_CONVERTED_REFERENCEFILE_ID,
        ),
    )
    parser.add_argument(
        "--source-pdf-filename",
        default=_env_first(
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_SOURCE_PDF_FILENAME",
            default=DEFAULT_SOURCE_PDF_FILENAME,
        ),
    )
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> ReadyUploadSmokeConfig:
    repo_root = _repo_root_from_script()
    evidence_dir = Path(args.evidence_dir)
    if not evidence_dir.is_absolute():
        evidence_dir = repo_root / evidence_dir

    return ReadyUploadSmokeConfig(
        repo_root=repo_root,
        backend_base_url=args.backend_base_url.rstrip("/"),
        literature_base_url=args.literature_base_url.rstrip("/"),
        aws_profile=args.aws_profile.strip() or None,
        region=args.region,
        user_pool_id=args.user_pool_id,
        client_id=args.client_id,
        client_secret=args.client_secret or None,
        authorized_groups=_parse_groups(args.authorized_groups),
        evidence_dir=evidence_dir,
        http_timeout_seconds=args.http_timeout_seconds,
        upload_timeout_seconds=args.upload_timeout_seconds,
        processing_timeout_seconds=args.processing_timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        aws_api_timeout_seconds=args.aws_api_timeout_seconds,
        evidence_tail_limit=args.evidence_tail_limit,
        keep_document=args.keep_document,
        curator_username=args.curator_username.strip() or None,
        curator_password=args.curator_password or None,
        curator_id_token=args.curator_id_token.strip() or None,
        known_md5=args.known_md5.lower(),
        pmid=args.pmid,
        reference=args.reference,
        source_referencefile_id=args.source_referencefile_id,
        converted_referencefile_id=args.converted_referencefile_id,
        source_pdf_filename=args.source_pdf_filename,
    )


def _to_live_config(config: ReadyUploadSmokeConfig) -> live_smoke.SmokeConfig:
    return live_smoke.SmokeConfig(
        repo_root=config.repo_root,
        aws_profile=config.aws_profile,
        region=config.region,
        user_pool_id=config.user_pool_id,
        client_id=config.client_id,
        client_secret=config.client_secret,
        base_url=config.literature_base_url,
        authorized_groups=config.authorized_groups,
        evidence_dir=config.evidence_dir,
        pytest_timeout_seconds=live_smoke.DEFAULT_PYTEST_TIMEOUT_SECONDS,
        literature_timeout_seconds=config.http_timeout_seconds,
        aws_api_timeout_seconds=config.aws_api_timeout_seconds,
        evidence_tail_limit=config.evidence_tail_limit,
        keep_users=False,
        user_prefix="unused-existing-curator",
        unknown_md5=live_smoke.DEFAULT_UNKNOWN_MD5,
        known_md5=config.known_md5,
        restricted_md5=config.known_md5,
        pmid=config.pmid,
        reference=config.reference,
        source_referencefile_id=config.source_referencefile_id,
        converted_referencefile_id=config.converted_referencefile_id,
        python_executable=sys.executable,
    )


def _backend_cookie_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Cookie": f"auth_token={token}",
        "User-Agent": "agr-ai-curation-abc-ready-upload-smoke/1.0",
    }


def _literature_bearer_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/pdf,application/octet-stream,*/*",
        "Authorization": f"Bearer {token}",
        "User-Agent": "agr-ai-curation-abc-ready-upload-smoke/1.0",
    }


def _response_json_or_text(response: dev_smoke.Response) -> Any:
    return response.json_body if response.json_body is not None else response.text


def _json_object_response(response: dev_smoke.Response, context: str) -> dict[str, Any]:
    if not isinstance(response.json_body, dict):
        raise ReadyUploadSmokeFailure(
            f"{context} response was not a JSON object: {response.status_code} {response.text}"
        )
    return cast(dict[str, Any], response.json_body)


def _health_payload(response: dev_smoke.Response) -> dict[str, Any]:
    payload = response.json_body
    if isinstance(payload, dict) and isinstance(payload.get("detail"), dict):
        payload = payload["detail"]
    if not isinstance(payload, dict):
        raise ReadyUploadSmokeFailure(
            f"Backend health response was not JSON: {response.status_code} {response.text}"
        )
    return payload


def preflight_backend_document_source(
    *,
    config: ReadyUploadSmokeConfig,
    requester: HttpRequester,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    response = requester(
        "GET",
        f"{config.backend_base_url}/weaviate/health",
        headers={"Accept": "application/json"},
        timeout=config.http_timeout_seconds,
    )
    payload = _health_payload(response)
    _append_check(
        checks,
        step="backend_document_source_health",
        ok=response.status_code == 200,
        status_code=response.status_code,
        payload=payload,
    )
    _require(
        response.status_code == 200,
        f"Backend health is not ready: {response.status_code} {response.text}",
    )

    raw_details = payload.get("details")
    details = raw_details if isinstance(raw_details, dict) else {}
    raw_document_source = details.get("document_source")
    document_source = raw_document_source if isinstance(raw_document_source, dict) else {}
    provider = str(document_source.get("provider") or "").strip().lower()
    enabled = bool(document_source.get("enabled"))
    _require(
        bool(payload.get("cognito_configured")),
        "Backend health does not report Cognito configured; READY upload smoke needs real cookie auth.",
    )
    _require(
        bool(enabled and provider and provider != "local_pdf"),
        (
            "Backend document-source import is not configured for an external provider. "
            f"Health document_source={document_source!r}"
        ),
    )
    return payload


def check_current_user(
    *,
    config: ReadyUploadSmokeConfig,
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


def download_source_pdf(
    *,
    config: ReadyUploadSmokeConfig,
    token: str,
    output_path: Path,
    requester: HttpRequester,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    response = requester(
        "GET",
        (
            f"{config.literature_base_url}/reference/referencefile/download_file/"
            f"{config.source_referencefile_id}"
        ),
        headers=_literature_bearer_headers(token),
        timeout=config.http_timeout_seconds,
    )
    _require(
        response.status_code == 200,
        (
            "ABC Literature source PDF download failed: "
            f"{response.status_code} {response.text[:500]}"
        ),
    )
    _require(bool(response.body), "ABC Literature source PDF download was empty")
    actual_md5 = hashlib.md5(response.body).hexdigest()
    _require(
        actual_md5 == config.known_md5,
        (
            "Downloaded source PDF MD5 did not match fixture. "
            f"Expected {config.known_md5}, got {actual_md5}"
        ),
    )
    output_path.write_bytes(response.body)
    payload = {
        "referencefile_id": config.source_referencefile_id,
        "byte_count": len(response.body),
        "md5": actual_md5,
        "sha256": hashlib.sha256(response.body).hexdigest(),
    }
    _append_check(
        checks,
        step="literature_source_pdf_download",
        ok=True,
        status_code=response.status_code,
        payload=payload,
    )
    return payload


def download_expected_converted_markdown(
    *,
    config: ReadyUploadSmokeConfig,
    token: str,
    requester: HttpRequester,
    checks: list[dict[str, Any]],
) -> tuple[bytes, dict[str, Any]]:
    response = requester(
        "GET",
        (
            f"{config.literature_base_url}/reference/referencefile/download_file/"
            f"{config.converted_referencefile_id}"
        ),
        headers={
            "Accept": "text/markdown,text/plain,application/octet-stream,*/*",
            "Authorization": f"Bearer {token}",
            "User-Agent": "agr-ai-curation-abc-ready-upload-smoke/1.0",
        },
        timeout=config.http_timeout_seconds,
    )
    _require(
        response.status_code == 200,
        (
            "ABC Literature converted Markdown download failed: "
            f"{response.status_code} {response.text[:500]}"
        ),
    )
    _require(bool(response.body), "ABC Literature converted Markdown download was empty")
    text = response.body.decode("utf-8", errors="replace").strip()
    _require(any(char.isalpha() for char in text), "ABC converted Markdown did not look textual")
    payload = {
        "referencefile_id": config.converted_referencefile_id,
        "byte_count": len(response.body),
        "sha256": hashlib.sha256(response.body).hexdigest(),
    }
    _append_check(
        checks,
        step="literature_converted_markdown_download",
        ok=True,
        status_code=response.status_code,
        payload=payload,
    )
    return response.body, payload


def upload_ready_pdf(
    *,
    config: ReadyUploadSmokeConfig,
    token: str,
    source_pdf_path: Path,
    requester: HttpRequester,
    checks: list[dict[str, Any]],
) -> str:
    body, boundary = dev_smoke.encode_multipart_form({}, "file", source_pdf_path)
    headers = _backend_cookie_headers(token)
    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    response = requester(
        "POST",
        f"{config.backend_base_url}/weaviate/documents/upload",
        headers=headers,
        data=body,
        timeout=config.upload_timeout_seconds,
    )
    _require(response.status_code == 201, f"READY upload failed: {response.status_code} {response.text}")
    upload_payload = _json_object_response(response, "READY upload")
    document_id = str(upload_payload.get("document_id") or "").strip()
    _require(bool(document_id), f"READY upload response missing document_id: {response.text}")
    _append_check(
        checks,
        step="backend_ready_upload",
        ok=True,
        status_code=response.status_code,
        payload=upload_payload,
    )
    return document_id


def wait_for_processing_complete(
    *,
    config: ReadyUploadSmokeConfig,
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
        _require(response.status_code == 200, f"Unexpected status response for {document_id}: {response.status_code} {response.text}")
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
            raise ReadyUploadSmokeFailure(
                f"READY document processing failed: {json.dumps(payload, sort_keys=True)}"
            )
        time.sleep(config.poll_interval_seconds)

    raise ReadyUploadSmokeFailure(
        "Timed out waiting for READY document processing to complete; "
        f"last payload: {json.dumps(last_payload or {}, sort_keys=True)}"
    )


def fetch_document_detail(
    *,
    config: ReadyUploadSmokeConfig,
    token: str,
    document_id: str,
    requester: HttpRequester,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    response = requester(
        "GET",
        f"{config.backend_base_url}/weaviate/documents/{document_id}",
        headers=_backend_cookie_headers(token),
        timeout=config.http_timeout_seconds,
    )
    _require(response.status_code == 200, f"Unexpected document detail response: {response.status_code} {response.text}")
    payload = _json_object_response(response, "document detail")
    provenance = payload.get("source_provenance")
    _require(isinstance(provenance, dict), f"Document detail missing source_provenance: {payload}")
    provenance = cast(dict[str, Any], provenance)
    _require(
        str(provenance.get("source_md5") or "").lower() == config.known_md5,
        f"Document source_md5 did not match fixture: {provenance}",
    )
    _require(
        str(provenance.get("pdf_artifact_id") or "") == config.source_referencefile_id,
        f"Document PDF artifact id did not match fixture: {provenance}",
    )
    _require(
        str(provenance.get("converted_artifact_id") or "")
        == config.converted_referencefile_id,
        f"Document converted artifact id did not match fixture: {provenance}",
    )
    _require(
        str(provenance.get("viewer_mode") or "").lower() == "local_pdf",
        f"Document provenance viewer_mode is not local_pdf: {provenance}",
    )
    _append_check(
        checks,
        step="backend_document_source_provenance",
        ok=True,
        status_code=response.status_code,
        payload=payload,
    )
    return payload


def fetch_download_info(
    *,
    config: ReadyUploadSmokeConfig,
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
    _require(str(payload.get("viewer_mode") or "").lower() == "local_pdf", f"Expected local_pdf: {payload}")
    _require(payload.get("pdf_available") is True, f"PDF is not available for ABC READY import: {payload}")
    _require(
        int(payload.get("pdf_size") or 0) > 0,
        f"PDF size is not positive for ABC READY import: {payload}",
    )
    _require(
        payload.get("source_markdown_available") is True,
        f"Source Markdown is not available: {payload}",
    )
    _require(
        int(payload.get("source_markdown_size") or 0) > 0,
        f"Source Markdown size is not positive: {payload}",
    )
    _append_check(
        checks,
        step="backend_download_info_pdf_and_source_markdown",
        ok=True,
        status_code=response.status_code,
        payload=payload,
    )
    return payload


def fetch_chunks(
    *,
    config: ReadyUploadSmokeConfig,
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
    raw_pagination = payload.get("pagination")
    pagination = raw_pagination if isinstance(raw_pagination, dict) else {}
    total_items = int(pagination.get("total_items") or 0)
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


def download_source_markdown(
    *,
    config: ReadyUploadSmokeConfig,
    token: str,
    document_id: str,
    expected_markdown_bytes: bytes,
    requester: HttpRequester,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    response = requester(
        "GET",
        f"{config.backend_base_url}/weaviate/documents/{document_id}/download/source_markdown",
        headers=_backend_cookie_headers(token),
        timeout=config.http_timeout_seconds,
    )
    _require(
        response.status_code == 200,
        f"Unexpected source_markdown download response: {response.status_code} {response.text}",
    )
    _require(bool(response.body), "source_markdown download was empty")
    text = response.body.decode("utf-8", errors="replace").strip()
    _require(any(char.isalpha() for char in text), "source_markdown did not look textual")
    expected_sha256 = hashlib.sha256(expected_markdown_bytes).hexdigest()
    actual_sha256 = hashlib.sha256(response.body).hexdigest()
    _require(
        len(response.body) == len(expected_markdown_bytes),
        (
            "Backend source_markdown byte count did not match ABC converted artifact. "
            f"Expected {len(expected_markdown_bytes)}, got {len(response.body)}"
        ),
    )
    _require(
        actual_sha256 == expected_sha256,
        (
            "Backend source_markdown SHA256 did not match ABC converted artifact. "
            f"Expected {expected_sha256}, got {actual_sha256}"
        ),
    )
    payload = {
        "document_id": document_id,
        "byte_count": len(response.body),
        "sha256": actual_sha256,
        "matches_literature_converted_artifact": True,
    }
    _append_check(
        checks,
        step="backend_source_markdown_download",
        ok=True,
        status_code=response.status_code,
        payload=payload,
    )
    return payload


def verify_uploaded_document_deleted(
    *,
    config: ReadyUploadSmokeConfig,
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


def assert_pdf_download_available(
    *,
    config: ReadyUploadSmokeConfig,
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
        (
            "Backend PDF download MD5 did not match ABC source PDF. "
            f"Expected {expected_md5}, got {actual_md5}"
        ),
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
            "matches_literature_source_pdf": True,
        },
    )


def delete_uploaded_document(
    *,
    config: ReadyUploadSmokeConfig,
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


def _curator_evidence(
    *,
    username: str | None,
    auth_source: str,
    authorized_groups: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "mode": "existing",
        "username": username,
        "auth_source": auth_source,
        "expected_provider_groups": list(authorized_groups),
    }


def _existing_curator_token(
    *,
    config: ReadyUploadSmokeConfig,
    live_config: live_smoke.SmokeConfig,
    aws_client: live_smoke.AwsSmokeClient | None,
) -> tuple[str, dict[str, Any], str | None]:
    if config.curator_id_token:
        return (
            config.curator_id_token,
            _curator_evidence(
                username=config.curator_username,
                auth_source="curator_id_token",
                authorized_groups=config.authorized_groups,
            ),
            None,
        )

    if not config.curator_username or not config.curator_password:
        raise ReadyUploadSmokeFailure(
            "READY upload smoke requires an existing test curator. Set "
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_USERNAME and "
            "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_PASSWORD in the local .env, "
            "or provide ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_ID_TOKEN."
        )
    if aws_client is None:
        raise ReadyUploadSmokeFailure("AWS client unavailable for Cognito curator auth")

    token = live_smoke.token_for_user(
        live_config,
        username=config.curator_username,
        password=config.curator_password,
        aws_client=aws_client,
    )
    return (
        token,
        _curator_evidence(
            username=config.curator_username,
            auth_source="curator_username_password",
            authorized_groups=config.authorized_groups,
        ),
        config.curator_password,
    )


def run_smoke(
    config: ReadyUploadSmokeConfig,
    *,
    aws_client_factory: AwsClientFactory = live_smoke.Boto3AwsSmokeClient,
    requester: HttpRequester = dev_smoke.http_request,
    now: datetime | None = None,
) -> ReadyUploadSmokeRunResult:
    now = now or _utc_now()
    stamp = _timestamp_for_file(now)
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = config.evidence_dir / f"abc_literature_ready_upload_smoke_{stamp}.json"
    live_config = _to_live_config(config)
    cleanup_failures: list[str] = []
    checks: list[dict[str, Any]] = []
    aws_client: live_smoke.AwsSmokeClient | None = None
    authorized_token: str | None = None
    curator_secret: str | None = None
    document_id: str | None = None
    status = "fail"
    exit_code = 1

    evidence: dict[str, Any] = {
        "timestamp_utc": now.isoformat(),
        "overall_status": status,
        "backend_base_url": config.backend_base_url,
        "literature_base_url": config.literature_base_url,
        "aws": {
            "profile": config.aws_profile,
            "region": config.region,
            "user_pool_id": config.user_pool_id,
            "client_id": config.client_id,
            "client_secret_provided": bool(config.client_secret),
            "api_timeout_seconds": config.aws_api_timeout_seconds,
        },
        "fixture": {
            "known_md5": config.known_md5,
            "pmid": config.pmid,
            "reference": config.reference,
            "source_referencefile_id": config.source_referencefile_id,
            "converted_referencefile_id": config.converted_referencefile_id,
            "source_pdf_filename": config.source_pdf_filename,
        },
        "timeouts": {
            "http_timeout_seconds": config.http_timeout_seconds,
            "upload_timeout_seconds": config.upload_timeout_seconds,
            "processing_timeout_seconds": config.processing_timeout_seconds,
            "poll_interval_seconds": config.poll_interval_seconds,
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

        preflight_backend_document_source(config=config, requester=requester, checks=checks)
        check_current_user(
            config=config,
            token=authorized_token,
            requester=requester,
            checks=checks,
        )

        with tempfile.TemporaryDirectory(prefix="abc-ready-upload-smoke-") as temp_dir:
            source_pdf_path = Path(temp_dir) / config.source_pdf_filename
            source_pdf = download_source_pdf(
                config=config,
                token=authorized_token,
                output_path=source_pdf_path,
                requester=requester,
                checks=checks,
            )
            source_pdf_bytes = source_pdf_path.read_bytes()
            evidence["source_pdf"] = {
                key: value for key, value in source_pdf.items() if key != "path"
            }
            expected_markdown_bytes, expected_markdown = download_expected_converted_markdown(
                config=config,
                token=authorized_token,
                requester=requester,
                checks=checks,
            )
            evidence["expected_converted_markdown"] = expected_markdown
            document_id = upload_ready_pdf(
                config=config,
                token=authorized_token,
                source_pdf_path=source_pdf_path,
                requester=requester,
                checks=checks,
            )
            evidence["document_id"] = document_id
            wait_for_processing_complete(
                config=config,
                token=authorized_token,
                document_id=document_id,
                requester=requester,
                checks=checks,
            )
            fetch_document_detail(
                config=config,
                token=authorized_token,
                document_id=document_id,
                requester=requester,
                checks=checks,
            )
            fetch_download_info(
                config=config,
                token=authorized_token,
                document_id=document_id,
                requester=requester,
                checks=checks,
            )
            fetch_chunks(
                config=config,
                token=authorized_token,
                document_id=document_id,
                requester=requester,
                checks=checks,
            )
            download_source_markdown(
                config=config,
                token=authorized_token,
                document_id=document_id,
                expected_markdown_bytes=expected_markdown_bytes,
                requester=requester,
                checks=checks,
            )
            assert_pdf_download_available(
                config=config,
                token=authorized_token,
                document_id=document_id,
                expected_pdf_bytes=source_pdf_bytes,
                requester=requester,
                checks=checks,
            )
        status = "pass"
        exit_code = 0
    except Exception as exc:
        status = "fail"
        exit_code = 1
        secrets_to_redact = (
            authorized_token or "",
            live_config.client_secret or "",
            curator_secret or "",
        )
        evidence["error"] = {
            "type": type(exc).__name__,
            "message": _tail_text(
                _redact_text(str(exc), secrets_to_redact),
                config.evidence_tail_limit,
            ),
        }
    finally:
        secrets_to_redact = (
            authorized_token or "",
            live_config.client_secret or "",
            curator_secret or "",
        )
        if document_id and authorized_token and config.keep_document:
            evidence["cleanup"]["document"] = {
                "document_id": document_id,
                "deleted": False,
                "skipped_reason": "--keep-document",
            }
            if status == "pass":
                status = "debug_keep_document"
                exit_code = 1
        elif document_id and authorized_token:
            try:
                delete_response = delete_uploaded_document(
                    config=config,
                    token=authorized_token,
                    document_id=document_id,
                    requester=requester,
                )
                deleted = delete_response.status_code == 200
                verified_deleted = False
                verify_response_payload: Any = None
                verify_status_code: int | None = None
                if deleted:
                    verify_response = verify_uploaded_document_deleted(
                        config=config,
                        token=authorized_token,
                        document_id=document_id,
                        requester=requester,
                    )
                    verify_status_code = verify_response.status_code
                    verify_response_payload = _response_json_or_text(verify_response)
                    verified_deleted = verify_response.status_code == 404
                evidence["cleanup"]["document"] = {
                    "document_id": document_id,
                    "deleted": deleted,
                    "verified_deleted": verified_deleted,
                    "status_code": delete_response.status_code,
                    "payload": _response_json_or_text(delete_response),
                    "verify_status_code": verify_status_code,
                    "verify_payload": verify_response_payload,
                }
                if not deleted:
                    raise ReadyUploadSmokeFailure(
                        (
                            "Document cleanup failed for "
                            f"{document_id}: {delete_response.status_code} {delete_response.text}"
                        )
                    )
                if not verified_deleted:
                    raise ReadyUploadSmokeFailure(
                        (
                            "Document cleanup verification failed for "
                            f"{document_id}: GET returned {verify_status_code} {verify_response_payload}"
                        )
                    )
            except Exception as exc:
                cleanup_failures.append(
                    _redact_text(
                        f"document {document_id}: {type(exc).__name__}: {exc}",
                        secrets_to_redact,
                    )
                )

        if cleanup_failures:
            status = "fail"
            exit_code = 1

        evidence["overall_status"] = status
        evidence = _redacted_evidence(evidence, secrets_to_redact)
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")

    return ReadyUploadSmokeRunResult(
        exit_code=exit_code,
        evidence_path=evidence_path,
        evidence=evidence,
    )


def main(argv: Sequence[str] | None = None) -> int:
    config = config_from_args(parse_args(argv))
    result = run_smoke(config)
    print("ABC Literature READY upload smoke complete.")
    print(
        "Result: "
        f"{result.evidence['overall_status']} "
        f"(evidence={result.evidence_path})"
    )
    if result.evidence.get("error"):
        print(f"Error: {result.evidence['error']['message']}", file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
