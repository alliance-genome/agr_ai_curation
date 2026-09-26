"""Administrative runtime accounting; projections are never cached or stored."""
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from decimal import Decimal
import logging
from sqlalchemy import text
from datetime import datetime
import csv
import io
import json
from typing import Literal
from fastapi import Query
from sqlalchemy.exc import SQLAlchemyError

from src.api.admin.auth import require_admin
from src.lib.cost_ledger.runtime_reads import read_runtime_accounting
from src.models.sql.database import SessionLocal
from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.observability.runtime import sanitized_runtime_error
from src.lib.cost_ledger.reports import runtime_report, ReportTooLarge
from src.lib.openai_agents.config import get_cost_report_max_window_days, get_cost_report_page_size

router = APIRouter(prefix="/api/admin/cost", tags=["Admin - Cost"])
logger = logging.getLogger(__name__)


@router.get("/access")
def cost_access(_admin: dict = Depends(require_admin)):
    """Same admin policy for the presentation gateway; no identity headers."""
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


def report_filters(start: datetime | None = None, end: datetime | None = None,
                   session_id: str | None = None, run_id: str | None = None,
                   provider: str | None = None, model: str | None = None,
                   activity: str | None = None, agent_id: str | None = None,
                   flow_run_id: str | None = None, snapshot_id: str | None = None,
                   document_id: str | None = None, job_id: str | None = None,
                   invocation_id: str | None = None):
    if (start is None) != (end is None):
        raise HTTPException(422, "Supply both start and end")
    if start is None and not session_id and not run_id:
        raise HTTPException(422, "Select a date window, conversation, or run")
    if start is not None:
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise HTTPException(422, "Use timezone-aware start < end")
        if (end - start).total_seconds() > get_cost_report_max_window_days() * 86400:
            raise HTTPException(422, "Date window exceeds COST_REPORT_MAX_WINDOW_DAYS")
    return dict(start=start, end=end, session_id=session_id, run_id=run_id, provider=provider,
                model=model, activity=activity, agent_id=agent_id, flow_run_id=flow_run_id, snapshot_id=snapshot_id,
                document_id=document_id, job_id=job_id, invocation_id=invocation_id)


def _report(filters):
    try:
        with SessionLocal() as db:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            return runtime_report(db, **filters)
    except ReportTooLarge as error:
        raise HTTPException(422, str(error), headers={"Cache-Control": "no-store"}) from None
    except LookupError:
        raise HTTPException(404, "Pricing snapshot not found", headers={"Cache-Control": "no-store"}) from None
    except (ValueError, SQLAlchemyError):
        try:
            raise_sanitized_http_exception(
                logger, status_code=503, detail="Cost report unavailable",
                log_message="Cost report unavailable",
                exc=sanitized_runtime_error("Cost report unavailable"),
            )
        except HTTPException as error:
            error.headers = {"Cache-Control": "no-store"}
            raise


@router.get("/reports")
def cost_report(response: Response, offset: int = Query(0, ge=0),
                filters: dict = Depends(report_filters), _admin: dict = Depends(require_admin)):
    report = _report(filters)
    page_size = get_cost_report_page_size()
    response.headers["Cache-Control"] = "no-store"
    # Summary is over the entire bounded selection, never the displayed page.
    return {**report, "requests": report["requests"][offset:offset + page_size],
            "runs": report["runs"][offset:offset + page_size],
            "pagination": {"offset": offset, "page_size": page_size,
                           "request_count": len(report["requests"]), "run_count": len(report["runs"])}}


def _csv_cell(value):
    text_value = "" if value is None else str(value)
    # Spreadsheet formulas may be hidden behind leading whitespace/control bytes.
    if text_value.lstrip().startswith(("=", "+", "-", "@")) or text_value.startswith(("\t", "\r", "\n")):
        return "'" + text_value
    return text_value


@router.get("/export")
def export_cost_report(format: Literal["json", "csv"] = "json", filters: dict = Depends(report_filters),
                       _admin: dict = Depends(require_admin)):
    report = _report(filters)
    headers = {"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="cost-report.{format}"'}
    if format == "json":
        return Response(json.dumps(report, ensure_ascii=False), media_type="application/json", headers=headers)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    columns = ["deployment_id", "pricing_snapshot_id", "valuation_algorithm", "scope", "filters", "generated_at",
               "attempt_id", "fact_revision", "created_at", "session_id", "run_id", "activity", "flow_run_id", "document_id", "job_id",
               "invocation_id", "parent_invocation_id",
               "operation_type", "candidate_count", "pagination_request",
               "provider", "model", "agent_id", "agent_name", "agent_role", "agent_revision", "node_id",
               "requested_service_tier", "effective_service_tier", "outcome", "usage_status", "input_tokens", "output_tokens",
               "total_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
               "recorded_amount", "recorded_unit", "recorded_source", "estimated_usd_lower", "estimated_usd_upper", "estimate_unavailable_reason"]
    writer.writerow(columns)
    for row in report["requests"]:
        charge = row["recorded_charge"] or {}
        flat = {**{key: report[key] for key in columns[:6]}, **row, **row["usage"],
                "filters": json.dumps(report["filters"], sort_keys=True),
                "recorded_amount": charge.get("amount"), "recorded_unit": charge.get("unit"), "recorded_source": charge.get("source"),
                "estimated_usd_lower": row["estimate"].get("cost"), "estimated_usd_upper": row["estimate"].get("estimated_cost_upper"),
                "estimate_unavailable_reason": row["estimate"].get("estimate_unavailable_reason")}
        writer.writerow([_csv_cell(flat.get(key)) for key in columns])
    return Response(stream.getvalue(), media_type="text/csv", headers=headers)


@router.get("/sessions/{session_id}")
def session_accounting(session_id: str, response: Response, run_id: str | None = None,
                       _admin: dict = Depends(require_admin)):
    response.headers["Cache-Control"] = "no-store"
    try:
        with SessionLocal() as db:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            result = read_runtime_accounting(db, session_id=session_id, run_id=run_id)
        return jsonable_encoder(result, custom_encoder={Decimal: str})
    except LookupError:
        raise HTTPException(404, "Runtime accounting not found", headers={"Cache-Control": "no-store"}) from None
    except ValueError:
        try:
            raise_sanitized_http_exception(
                logger, status_code=503, detail="Runtime accounting unavailable",
                log_message="Runtime accounting read unavailable",
                exc=sanitized_runtime_error("Runtime accounting read unavailable"),
            )
        except HTTPException as error:
            error.headers = {"Cache-Control": "no-store"}
            raise
