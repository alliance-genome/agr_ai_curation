#!/usr/bin/env python3
"""Run a durable AI Curation identifier-import smoke for ABC Literature."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from scripts.testing import abc_literature_ready_upload_smoke as ready_smoke  # noqa: E402
from scripts.testing import abc_literature_live_smoke as live_smoke  # noqa: E402
from scripts.testing import dev_release_smoke as dev_smoke  # noqa: E402


class IdentifierImportSmokeFailure(RuntimeError):
    """Raised when the identifier import smoke cannot complete safely."""


@dataclass(frozen=True)
class IdentifierImportSmokeConfig:
    ready_config: ready_smoke.ReadyUploadSmokeConfig
    identifier: str


@dataclass
class IdentifierImportSmokeRunResult:
    exit_code: int
    evidence_path: Path
    evidence: dict[str, Any]


def _extract_identifier_args(argv: Sequence[str] | None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--identifier",
        default="",
        help=(
            "Identifier to import through the backend endpoint. Defaults to "
            "ABC_LITERATURE_IDENTIFIER_IMPORT_SMOKE_IDENTIFIER or PMID:<fixture>."
        ),
    )
    return parser.parse_known_args(argv)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    identifier_args, remaining = _extract_identifier_args(argv)
    args = ready_smoke.parse_args(remaining)
    identifier_default = ready_smoke._env_first(  # noqa: SLF001 - script-level reuse
        "ABC_LITERATURE_IDENTIFIER_IMPORT_SMOKE_IDENTIFIER",
        default=f"PMID:{args.pmid}",
    )
    args.identifier = identifier_args.identifier.strip() or identifier_default
    return args


def config_from_args(args: argparse.Namespace) -> IdentifierImportSmokeConfig:
    return IdentifierImportSmokeConfig(
        ready_config=ready_smoke.config_from_args(args),
        identifier=args.identifier.strip(),
    )


def _append_check(
    checks: list[dict[str, Any]],
    *,
    step: str,
    ok: bool,
    status_code: int,
    payload: Any,
) -> None:
    ready_smoke._append_check(  # noqa: SLF001 - script-level helper reuse
        checks,
        step=step,
        ok=ok,
        status_code=status_code,
        payload=payload,
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IdentifierImportSmokeFailure(message)


def _validate_pdf_backed_source_provenance(
    *,
    provenance: dict[str, Any],
    ready_config: ready_smoke.ReadyUploadSmokeConfig,
    context: str,
) -> None:
    _require(
        provenance.get("provider") == "abc_literature",
        f"{context} provider did not match ABC Literature: {provenance}",
    )
    _require(
        str(provenance.get("viewer_mode") or "").lower() == "local_pdf",
        f"{context} was not PDF-backed: {provenance}",
    )
    _require(
        str(provenance.get("source_md5") or "") == ready_config.known_md5,
        f"{context} source md5 did not match fixture: {provenance}",
    )
    _require(
        str(provenance.get("pdf_artifact_id") or "") == ready_config.source_referencefile_id,
        f"{context} PDF artifact did not match fixture: {provenance}",
    )
    _require(
        str(provenance.get("converted_artifact_id") or "") == ready_config.converted_referencefile_id,
        f"{context} converted artifact did not match fixture: {provenance}",
    )


def _expected_normalized_identifier(raw_identifier: str) -> str:
    identifier = raw_identifier.strip()
    if identifier.isdigit():
        return f"PMID:{identifier}"
    prefix, separator, value = identifier.partition(":")
    if separator and prefix.upper() in {"PMID", "AGRKB", "ABC"}:
        return f"{prefix.upper()}:{value.strip()}"
    return identifier


def resolve_source_identifier(
    *,
    config: IdentifierImportSmokeConfig,
    token: str,
    requester: ready_smoke.HttpRequester,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    ready_config = config.ready_config
    response = requester(
        "POST",
        f"{ready_config.backend_base_url}/weaviate/documents/resolve/source-identifiers",
        headers=ready_smoke._backend_cookie_headers(token),  # noqa: SLF001
        json_body={"identifiers": config.identifier},
        timeout=ready_config.http_timeout_seconds,
    )
    _require(
        response.status_code == 200,
        f"Identifier resolve failed: {response.status_code} {response.text}",
    )
    payload = ready_smoke._json_object_response(response, "identifier resolve")  # noqa: SLF001
    results = payload.get("results")
    _require(isinstance(results, list) and len(results) == 1, f"Unexpected resolve results: {payload}")
    result = results[0]
    _require(isinstance(result, dict), f"Identifier resolve result was not an object: {payload}")
    _require(result.get("status") == "resolved", f"Identifier resolve did not report ready: {payload}")
    _require(
        result.get("identifier") == config.identifier,
        f"Identifier resolve echoed the wrong identifier: {payload}",
    )
    _require(
        result.get("normalized_identifier") == _expected_normalized_identifier(config.identifier),
        f"Identifier resolve normalized the wrong fixture identifier: {payload}",
    )
    _require(
        not result.get("document_id") and not result.get("job_id"),
        f"Identifier resolve unexpectedly created document/job resources: {payload}",
    )
    provenance = result.get("source_provenance")
    _require(isinstance(provenance, dict), f"Identifier resolve missing source provenance: {payload}")
    _validate_pdf_backed_source_provenance(
        provenance=dict(provenance),
        ready_config=ready_config,
        context="Identifier resolve",
    )
    _append_check(
        checks,
        step="backend_identifier_resolve_ready",
        ok=True,
        status_code=response.status_code,
        payload=payload,
    )
    return payload


def import_source_identifier(
    *,
    config: IdentifierImportSmokeConfig,
    token: str,
    requester: ready_smoke.HttpRequester,
    checks: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    ready_config = config.ready_config
    response = requester(
        "POST",
        f"{ready_config.backend_base_url}/weaviate/documents/import/source-identifiers",
        headers=ready_smoke._backend_cookie_headers(token),  # noqa: SLF001
        json_body={"identifiers": config.identifier},
        timeout=ready_config.upload_timeout_seconds,
    )
    _require(
        response.status_code == 200,
        f"Identifier import failed: {response.status_code} {response.text}",
    )
    payload = ready_smoke._json_object_response(response, "identifier import")  # noqa: SLF001
    results = payload.get("results")
    _require(isinstance(results, list) and len(results) == 1, f"Unexpected import results: {payload}")
    result = results[0]
    _require(isinstance(result, dict), f"Identifier import result was not an object: {payload}")
    _require(result.get("status") == "imported", f"Identifier import did not queue: {payload}")
    _require(
        result.get("identifier") == config.identifier,
        f"Identifier import echoed the wrong identifier: {payload}",
    )
    _require(
        result.get("normalized_identifier") == _expected_normalized_identifier(config.identifier),
        f"Identifier import normalized the wrong fixture identifier: {payload}",
    )
    document_id = str(result.get("document_id") or "").strip()
    _require(bool(document_id), f"Identifier import missing document_id: {payload}")
    provenance = result.get("source_provenance")
    _require(isinstance(provenance, dict), f"Identifier import missing source provenance: {payload}")
    _validate_pdf_backed_source_provenance(
        provenance=dict(provenance),
        ready_config=ready_config,
        context="Identifier import",
    )
    _append_check(
        checks,
        step="backend_identifier_import_queued",
        ok=True,
        status_code=response.status_code,
        payload=payload,
    )
    return document_id, payload


def run_smoke(
    config: IdentifierImportSmokeConfig,
    *,
    aws_client_factory: ready_smoke.AwsClientFactory = live_smoke.Boto3AwsSmokeClient,
    requester: ready_smoke.HttpRequester = dev_smoke.http_request,
    now: datetime | None = None,
) -> IdentifierImportSmokeRunResult:
    now = now or ready_smoke._utc_now()  # noqa: SLF001
    stamp = ready_smoke._timestamp_for_file(now)  # noqa: SLF001
    ready_config = config.ready_config
    ready_config.evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = ready_config.evidence_dir / f"abc_literature_identifier_import_smoke_{stamp}.json"
    live_config = ready_smoke._to_live_config(ready_config)  # noqa: SLF001
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
        "backend_base_url": ready_config.backend_base_url,
        "literature_base_url": ready_config.literature_base_url,
        "identifier": config.identifier,
        "aws": {
            "profile": ready_config.aws_profile,
            "region": ready_config.region,
            "user_pool_id": ready_config.user_pool_id,
            "client_id": ready_config.client_id,
            "client_secret_provided": bool(ready_config.client_secret),
            "api_timeout_seconds": ready_config.aws_api_timeout_seconds,
        },
        "fixture": {
            "known_md5": ready_config.known_md5,
            "pmid": ready_config.pmid,
            "reference": ready_config.reference,
            "source_referencefile_id": ready_config.source_referencefile_id,
            "converted_referencefile_id": ready_config.converted_referencefile_id,
        },
        "timeouts": {
            "http_timeout_seconds": ready_config.http_timeout_seconds,
            "upload_timeout_seconds": ready_config.upload_timeout_seconds,
            "processing_timeout_seconds": ready_config.processing_timeout_seconds,
            "poll_interval_seconds": ready_config.poll_interval_seconds,
        },
        "cleanup": {
            "keep_document": ready_config.keep_document,
            "failures": cleanup_failures,
        },
        "checks": checks,
    }

    try:
        if not ready_config.curator_id_token:
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

        curator_secret = ready_config.curator_password
        authorized_token, curator_payload, curator_secret = ready_smoke._existing_curator_token(  # noqa: SLF001
            config=ready_config,
            live_config=live_config,
            aws_client=aws_client,
        )
        evidence["smoke_user"] = curator_payload

        ready_smoke.preflight_backend_document_source(config=ready_config, requester=requester, checks=checks)
        ready_smoke.check_current_user(
            config=ready_config,
            token=authorized_token,
            requester=requester,
            checks=checks,
        )

        with tempfile.TemporaryDirectory(prefix="abc-identifier-import-smoke-") as temp_dir:
            source_pdf_path = Path(temp_dir) / ready_config.source_pdf_filename
            source_pdf = ready_smoke.download_source_pdf(
                config=ready_config,
                token=authorized_token,
                output_path=source_pdf_path,
                requester=requester,
                checks=checks,
            )
            source_pdf_bytes = source_pdf_path.read_bytes()
            evidence["source_pdf"] = source_pdf
            expected_markdown_bytes, expected_markdown = ready_smoke.download_expected_converted_markdown(
                config=ready_config,
                token=authorized_token,
                requester=requester,
                checks=checks,
            )
            evidence["expected_converted_markdown"] = expected_markdown
            resolve_payload = resolve_source_identifier(
                config=config,
                token=authorized_token,
                requester=requester,
                checks=checks,
            )
            evidence["identifier_resolve"] = resolve_payload
            document_id, import_payload = import_source_identifier(
                config=config,
                token=authorized_token,
                requester=requester,
                checks=checks,
            )
            evidence["document_id"] = document_id
            evidence["identifier_import"] = import_payload
            ready_smoke.wait_for_processing_complete(
                config=ready_config,
                token=authorized_token,
                document_id=document_id,
                requester=requester,
                checks=checks,
            )
            ready_smoke.fetch_document_detail(
                config=ready_config,
                token=authorized_token,
                document_id=document_id,
                requester=requester,
                checks=checks,
            )
            ready_smoke.fetch_download_info(
                config=ready_config,
                token=authorized_token,
                document_id=document_id,
                requester=requester,
                checks=checks,
            )
            ready_smoke.fetch_chunks(
                config=ready_config,
                token=authorized_token,
                document_id=document_id,
                requester=requester,
                checks=checks,
            )
            ready_smoke.download_source_markdown(
                config=ready_config,
                token=authorized_token,
                document_id=document_id,
                expected_markdown_bytes=expected_markdown_bytes,
                requester=requester,
                checks=checks,
            )
            ready_smoke.assert_pdf_download_available(
                config=ready_config,
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
            "message": ready_smoke._tail_text(  # noqa: SLF001
                ready_smoke._redact_text(str(exc), secrets_to_redact),  # noqa: SLF001
                ready_config.evidence_tail_limit,
            ),
        }
    finally:
        secrets_to_redact = (
            authorized_token or "",
            live_config.client_secret or "",
            curator_secret or "",
        )
        if document_id and authorized_token and ready_config.keep_document:
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
                delete_response = ready_smoke.delete_uploaded_document(
                    config=ready_config,
                    token=authorized_token,
                    document_id=document_id,
                    requester=requester,
                )
                deleted = delete_response.status_code == 200
                verified_deleted = False
                verify_response_payload: Any = None
                verify_status_code: int | None = None
                if deleted:
                    verify_response = ready_smoke.verify_uploaded_document_deleted(
                        config=ready_config,
                        token=authorized_token,
                        document_id=document_id,
                        requester=requester,
                    )
                    verify_status_code = verify_response.status_code
                    verify_response_payload = ready_smoke._response_json_or_text(verify_response)  # noqa: SLF001
                    verified_deleted = verify_response.status_code == 404
                evidence["cleanup"]["document"] = {
                    "document_id": document_id,
                    "deleted": deleted,
                    "verified_deleted": verified_deleted,
                    "status_code": delete_response.status_code,
                    "payload": ready_smoke._response_json_or_text(delete_response),  # noqa: SLF001
                    "verify_status_code": verify_status_code,
                    "verify_payload": verify_response_payload,
                }
                if not deleted:
                    raise IdentifierImportSmokeFailure(
                        (
                            "Document cleanup failed for "
                            f"{document_id}: {delete_response.status_code} {delete_response.text}"
                        )
                    )
                if not verified_deleted:
                    raise IdentifierImportSmokeFailure(
                        (
                            "Document cleanup verification failed for "
                            f"{document_id}: GET returned {verify_status_code} {verify_response_payload}"
                        )
                    )
            except Exception as exc:
                cleanup_failures.append(
                    ready_smoke._redact_text(  # noqa: SLF001
                        f"document {document_id}: {type(exc).__name__}: {exc}",
                        secrets_to_redact,
                    )
                )

        if cleanup_failures:
            status = "fail"
            exit_code = 1

        evidence["overall_status"] = status
        evidence = ready_smoke._redacted_evidence(evidence, secrets_to_redact)  # noqa: SLF001
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")

    return IdentifierImportSmokeRunResult(
        exit_code=exit_code,
        evidence_path=evidence_path,
        evidence=evidence,
    )


def main(argv: Sequence[str] | None = None) -> int:
    config = config_from_args(parse_args(argv))
    result = run_smoke(config)
    print("ABC Literature identifier import smoke complete.")
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
