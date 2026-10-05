"""Benchmark resolver: a curator imports their own AI Curation flow (design section 8).

Trust comes from main AI Curation's signature, which binds the bundle to the
authenticated curator's ``sub`` and issuer; the portal only relays it. Everything a
curator can act on answers 200 with an ``outcome``, because the portal never reads
the body of a refusal. Signature, closure and conflict failures are defects an honest
portal never triggers, so they are reported to Sentry through the runtime facade.
"""

import logging
from datetime import datetime
from typing import Any, Literal, NoReturn
from uuid import UUID

from anyio.to_thread import run_sync
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict

from src.api.benchmark_curator import require_benchmark_curator, require_benchmark_read_curator
from src.api.benchmark_gate import require_benchmark_api
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.observability import sanitized_benchmark_error
from src.lib.benchmarks.saved_flows import saved_flow_contracts
from src.lib.flow_transfer.bundle import BundleTooLarge, CheckedBundle, check_bundle
from src.lib.flow_transfer.config import FlowImportConfig, flow_import_config
from src.lib.flow_transfer.importer import (ImportConflict, ImportContext, ImportRefused,
                                            import_dependencies, import_flow, latest_import,
                                            latest_imports, unchanged_import)
from src.lib.flow_transfer.reasons import ReasonCode
from src.lib.flow_transfer.signing import UntrustedBundle, verify_bundle
from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.observability.runtime import report_runtime_exception
from src.lib.openai_agents.config import (get_flow_transfer_bundle_max_bytes,
                                          get_flow_transfer_import_list_max_flows)
from src.models.sql.database import SessionLocal

logger = logging.getLogger(__name__)
NO_STORE = {"Cache-Control": "no-store"}
# Room for the signature and the JSON wrapper around a bundle at its size bound.
ENVELOPE_BYTES = 65_536
UUID_CHARS = 36


def _error(status: int, code: str) -> HTTPException:
    return HTTPException(status, {"code": code}, headers=NO_STORE)


class FlowImportRoute(APIRoute):
    """Stable, data-free errors; unexpected failures are sanitized 503s; nothing is cached."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            try:
                response = await handler(request)
            except RequestValidationError:
                raise _error(422, "invalid_request") from None
            except HTTPException as exc:
                headers = {**(exc.headers or {}), **NO_STORE}
                if isinstance(exc.detail, dict):
                    exc.headers = headers
                    raise
                code = {401: "authorization_required", 403: "capability_required",
                        404: "not_found", 503: "authorization_unavailable"}.get(
                            exc.status_code, "request_failed")
                raise HTTPException(exc.status_code, {"code": code}, headers=headers) from None
            except Exception as exc:
                try:
                    raise_sanitized_http_exception(
                        logger, status_code=503, detail={"code": "flow_import_unavailable"},
                        log_message="Benchmark flow import unavailable",
                        exc=sanitized_benchmark_error("flow_import", type(exc).__name__),
                    )
                except HTTPException as sanitized:
                    sanitized.headers = NO_STORE
                    raise
            response.headers.update(NO_STORE)
            return response

        return wrapped


router = APIRouter(prefix="/api/v1/benchmarks", tags=["Benchmarks - Flow imports"],
                   dependencies=[Depends(require_benchmark_api)], route_class=FlowImportRoute)


class FlowImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bundle: dict[str, Any]
    signature: str


class FlowImportItem(BaseModel):
    source_flow_id: UUID
    flow_id: UUID
    version: int
    source_version: str
    imported_at: datetime


class FlowImportList(BaseModel):
    items: list[FlowImportItem]


class FlowImportOutcome(BaseModel):
    outcome: Literal["imported", "updated", "unchanged", "refused"]
    reason: ReasonCode | None
    flow_id: UUID | None
    version: int | None
    source_version: str
    runnable: bool | None
    run_problem: str | None


def _config() -> FlowImportConfig:
    config = flow_import_config()
    if config is None:
        raise _error(503, "flow_import_not_configured")
    return config


def _source_flow_ids(value: str) -> list[UUID]:
    """Comma-joined canonical UUIDs, no spaces, so the length bound is exact."""
    most = get_flow_transfer_import_list_max_flows()
    if len(value) > most * (UUID_CHARS + 1) - 1:
        raise _error(422, "invalid_request")
    try:
        ids = [UUID(part) for part in value.split(",")]
    except ValueError:
        raise _error(422, "invalid_request") from None
    if not ids or len(ids) > most:
        raise _error(422, "invalid_request")
    return ids


@router.get("/flow-imports", response_model=FlowImportList)
def list_flow_imports(
    source_flow_ids: str = Query(min_length=UUID_CHARS),
    curator: BenchmarkCuratorContext = Depends(require_benchmark_read_curator),
) -> FlowImportList:
    """The caller's latest import of each named AI Curation flow."""
    config = _config()
    ids = _source_flow_ids(source_flow_ids)
    with SessionLocal() as session:
        rows = latest_imports(session, user_id=curator.db_user_id, export_issuer=config.issuer,
                              source_flow_ids=ids)
        items = [FlowImportItem(source_flow_id=row.source_flow_id, flow_id=row.flow_id,
                                version=row.version, source_version=row.source_version,
                                imported_at=row.imported_at) for row in rows]
    return FlowImportList(items=items)


async def _body(request: Request) -> bytes:
    most = get_flow_transfer_bundle_max_bytes() + ENVELOPE_BYTES
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > most:
            raise _error(413, "bundle_too_large")
        body.extend(chunk)
    return bytes(body)


def _report(event: str, check: str, **context: Any) -> None:
    """One Sentry event through the runtime facade, carrying only a source-owned check
    code or exception class name in a chain-free wrapper (never bundle content)."""
    report_runtime_exception(
        sanitized_benchmark_error(event, check), component="benchmark_flow_import",
        operation=event, context={"check": check, **context},
        fingerprint=["benchmark_flow_import", event, check],
    )


def _defect(event: str, status: int, code: str, curator: BenchmarkCuratorContext,
            check: str) -> NoReturn:
    # ``check`` is a fixed literal (UntrustedBundle, ImportConflict) or a class name.
    _report(event, check, status_code=status)
    logger.error(event, extra={"event": event, "code": code, "check": check,
                               "curator_user_id": curator.db_user_id, "sentry_skip_event": True})
    raise _error(status, code)


@router.post("/flow-imports", response_model=FlowImportOutcome)
async def import_flow_route(
    request: Request,
    curator: BenchmarkCuratorContext = Depends(require_benchmark_curator),
) -> FlowImportOutcome:
    """Import or update the caller's private copy of one signed AI Curation flow."""
    config = _config()
    raw = await _body(request)
    try:
        payload = FlowImportRequest.model_validate_json(raw)
        checked = check_bundle(payload.bundle)
        verify_bundle(payload.signature, checked, config=config, subject=curator.subject,
                      issuer=curator.auth_issuer)
    except BundleTooLarge:
        raise _error(413, "bundle_too_large") from None
    except UntrustedBundle as exc:
        _defect("flow_import_untrusted", 400, "untrusted_bundle", curator, str(exc))
    except ValueError as exc:
        # InvalidBundle, a pydantic ValidationError, or the bare ValueError canonical
        # JSON raises for a non-finite number (NaN/Infinity) in the bundle.
        _defect("flow_import_invalid", 400, "invalid_bundle", curator, type(exc).__name__)
    return await run_sync(_import, curator, config, checked)


def _import(curator: BenchmarkCuratorContext, config: FlowImportConfig,
            checked: CheckedBundle) -> FlowImportOutcome:
    ctx = ImportContext(user_id=curator.db_user_id, subject=curator.subject,
                        groups=list(curator.active_groups), export_issuer=config.issuer)
    source = checked.bundle.flow
    try:
        with SessionLocal() as session:
            result = unchanged_import(session, ctx, checked)
        if result is None:
            with SessionLocal() as session:
                import_dependencies(session, ctx, checked)
                session.commit()
            with SessionLocal() as session:
                result = import_flow(session, ctx, checked)
                session.commit()
    except ImportRefused as refused:
        logger.info("flow_import_refused", extra={
            "event": "flow_import_refused", "reason": refused.reason,
            "source_flow_id": str(source.source_flow_id), "curator_user_id": curator.db_user_id})
        with SessionLocal() as session:
            previous = latest_import(session, ctx, source.source_flow_id)
            flow_id = previous.flow_id if previous is not None else None
            version = previous.version if previous is not None else None
        return FlowImportOutcome(outcome="refused", reason=refused.reason, flow_id=flow_id,
                                 version=version, source_version=source.source_version,
                                 runnable=None, run_problem=None)
    except ImportConflict as conflict:
        _defect("flow_import_conflict", 409, "import_conflict", curator, str(conflict))
    # The import is committed. A failure to report whether the copy can run must not
    # turn it into an "unavailable" answer, so the outcome stands without that report.
    runnable: bool | None = None
    run_problem: str | None = None
    try:
        with SessionLocal() as session:
            contracts = saved_flow_contracts(session, curator, result.flow_id)
        runnable, run_problem = contracts.runnable, contracts.run_problem
    except Exception as exc:
        _report("flow_import_report_failed", type(exc).__name__,
                flow_id=str(source.source_flow_id))
        logger.error("flow_import_report_failed", extra={
            "event": "flow_import_report_failed", "error_type": type(exc).__name__,
            "source_flow_id": str(source.source_flow_id), "curator_user_id": curator.db_user_id,
            "sentry_skip_event": True})
    logger.info("flow_import_done", extra={
        "event": "flow_import_done", "outcome": result.outcome, "version": result.version,
        "source_flow_id": str(source.source_flow_id), "curator_user_id": curator.db_user_id})
    return FlowImportOutcome(outcome=result.outcome, reason=None, flow_id=result.flow_id,
                             version=result.version, source_version=source.source_version,
                             runnable=runnable, run_problem=run_problem)
