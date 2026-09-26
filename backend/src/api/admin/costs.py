"""Administrative runtime accounting; projections are never cached or stored."""
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from decimal import Decimal
import logging
from sqlalchemy import text

from src.api.admin.auth import require_admin
from src.lib.cost_ledger.runtime_reads import read_runtime_accounting
from src.models.sql.database import SessionLocal
from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.observability.runtime import sanitized_runtime_error

router = APIRouter(prefix="/api/admin/cost", tags=["Admin - Cost"])
logger = logging.getLogger(__name__)


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
