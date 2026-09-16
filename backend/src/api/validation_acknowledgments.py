"""Human acknowledgment endpoint; deliberately absent from AI tool catalogs."""
from typing import Literal
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from src.api.auth import get_auth_dependency
from src.lib.agent_studio.validation_coverage import ValidationCoverageScope
from src.models.sql import get_db
from src.models.sql.validation_acknowledgment import ValidationAcknowledgment
from src.services.user_service import set_global_user_from_cognito

router = APIRouter(prefix="/api/validation-acknowledgments")


class AcknowledgmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledge_extraction_only: Literal[True]
    scopes: list[ValidationCoverageScope] = Field(min_length=1)


@router.post("")
def acknowledge_validation(request: AcknowledgmentRequest, user: dict = get_auth_dependency(), db: Session = Depends(get_db)):
    actor = set_global_user_from_cognito(db, user)
    # This grants no access or validation status. Save/launch recompute the exact
    # scope and match its fingerprint for this authenticated actor only.
    for scope in request.scopes:
        db.execute(insert(ValidationAcknowledgment).values(
            user_id=actor.id, fingerprint=scope.fingerprint(), scope=scope.model_dump(mode="json"),
        ).on_conflict_do_nothing())
    db.commit()
    return {"acknowledged": [scope.fingerprint() for scope in request.scopes]}
