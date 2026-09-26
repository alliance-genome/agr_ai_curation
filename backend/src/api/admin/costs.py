"""Administrative runtime accounting; projections are never cached or stored."""
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from decimal import Decimal
from sqlalchemy import text

from src.api.admin.auth import require_admin
from src.lib.cost_ledger.runtime_reads import read_runtime_accounting
from src.models.sql.database import SessionLocal

router = APIRouter(prefix="/api/admin/cost", tags=["Admin - Cost"])


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
        raise HTTPException(503, "Runtime accounting unavailable", headers={"Cache-Control": "no-store"}) from None
