"""A curator's own runnable flows, signed for the benchmark (design section 7).

Only ``Authorization: Bearer <ID token>`` is accepted; cookies are ignored, so a
cross-site browser request can't use these routes. The token is checked as strictly
as the sign-in cookie (pool issuer, signature, expiry), against an explicit audience
allowlist (FLOW_EXPORT_BEARER_CLIENT_IDS). Nothing here writes: a curator with no
account simply has no flows.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from jwt.exceptions import InvalidTokenError, PyJWKClientConnectionError, PyJWKClientError
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth_runtime.oidc import OIDCAuthProvider
from src.api.auth import expected_token_failure_reason, is_unknown_signing_key_error
from src.config import get_app_version
from src.lib.flow_transfer.config import FlowExportConfig, flow_export_config
from src.lib.flow_transfer.export import (EXPORT_PAGE_SIZE, EvaluatedFlow, ExportCurator,
                                          evaluate_flow, exportable_flows)
from src.lib.flow_transfer.reasons import ReasonCode
from src.lib.flow_transfer.signing import sign_bundle
from src.lib.flows.access import get_visible_flow
from src.lib.group_rules import get_groups_from_provider_groups
from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.openai_agents.config import (get_auth_jwks_cache_ttl_seconds,
                                          get_auth_jwks_timeout_seconds,
                                          get_auth_provider_timeout_seconds,
                                          get_benchmark_curator_auth_max_bytes)
from src.lib.security.redaction import active_secret_redaction
from src.models.sql import get_db
from src.models.sql.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/flow-exports", tags=["Flow exports"])
NO_STORE = {"Cache-Control": "no-store"}
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub", "token_use"]


class FlowExportItem(BaseModel):
    flow_id: UUID
    name: str
    description: str | None
    owned: bool
    version: str
    importable: bool
    reason: ReasonCode | None


class FlowExportPage(BaseModel):
    items: list[FlowExportItem]
    total_items: int
    next_offset: int | None


class SignedFlowBundle(BaseModel):
    bundle: dict[str, Any]
    signature: str


def _fail(status: int, detail: dict[str, Any]) -> HTTPException:
    return HTTPException(status, detail, headers=NO_STORE)


def require_flow_export_config() -> FlowExportConfig:
    config = flow_export_config()
    if config is None:
        raise _fail(503, {"code": "flow_export_not_configured"})
    return config


_provider: OIDCAuthProvider | None = None


def _bearer_provider(config: FlowExportConfig) -> OIDCAuthProvider:
    """Same pool issuer and keys as the sign-in cookie; only the audiences differ."""
    global _provider
    if _provider is None or tuple(_provider.audience) != config.bearer_client_ids:
        _provider = OIDCAuthProvider({
            "issuer_url": (f"https://cognito-idp.{os.environ['COGNITO_REGION']}.amazonaws.com/"
                           f"{os.environ['COGNITO_USER_POOL_ID']}"),
            "client_id": config.bearer_client_ids[0],
            "audience": list(config.bearer_client_ids),
            "group_claim": "cognito:groups",
            "required_claims": REQUIRED_CLAIMS,
            "timeout_seconds": get_auth_provider_timeout_seconds(),
            "jwks_timeout_seconds": get_auth_jwks_timeout_seconds(),
            "jwks_cache_ttl_seconds": get_auth_jwks_cache_ttl_seconds(),
        })
    return _provider


def _unavailable(exc: Exception) -> NoReturn:
    try:
        raise_sanitized_http_exception(
            logger, status_code=503, detail={"code": "authorization_unavailable"},
            log_message="Flow export sign-in check unavailable", exc=exc,
        )
    except HTTPException as failure:
        failure.headers = NO_STORE
        raise


def _rejected(reason: str) -> HTTPException:
    logger.info("flow_export_refused", extra={"event": "flow_export_refused",
                                              "code": "authorization_required", "reason": reason})
    return _fail(401, {"code": "authorization_required"})


def _refused(status: int, code: str, flow_id: UUID, **detail: Any) -> HTTPException:
    logger.info("flow_export_refused", extra={"event": "flow_export_refused", "code": code,
                                              "source_flow_id": str(flow_id)})
    return _fail(status, {"code": code, **detail})


async def require_flow_export_curator(
    request: Request,
    config: FlowExportConfig = Depends(require_flow_export_config),
    db: Session = Depends(get_db),
) -> ExportCurator | None:
    """The verified curator, or None when they have no AI Curation account yet."""
    scheme, separator, token = request.headers.get("authorization", "").partition(" ")
    if (scheme != "Bearer" or not separator or not token or any(c.isspace() for c in token)
            or len(token.encode("utf-8")) > get_benchmark_curator_auth_max_bytes()):
        raise _rejected("missing_or_malformed")
    try:
        with active_secret_redaction(token):
            provider = _bearer_provider(config)
            claims = await provider.validate_token(token)
        if claims.get("token_use") != "id":
            raise InvalidTokenError("Only ID tokens are accepted")
        principal = provider.extract_principal(claims)
        if not principal.subject:
            raise InvalidTokenError("Missing subject")
    except PyJWKClientConnectionError as exc:
        _unavailable(exc)
    except PyJWKClientError as exc:
        if not is_unknown_signing_key_error(exc):
            _unavailable(exc)
        raise _rejected("unknown_signer") from None
    except InvalidTokenError as exc:
        raise _rejected(expected_token_failure_reason(exc)) from None
    except Exception as exc:
        _unavailable(exc)
    finally:
        del token
    user = db.scalar(select(User).where(User.auth_sub == principal.subject,
                                        User.is_active.is_(True)))
    if user is None:
        return None
    return ExportCurator(subject=principal.subject, issuer=claims["iss"], user_id=user.id,
                         groups=get_groups_from_provider_groups(principal.groups))


def _evaluate(db: Session, flow: Any, curator: ExportCurator,
              config: FlowExportConfig) -> EvaluatedFlow:
    return evaluate_flow(db, flow, curator, issuer=config.issuer, app_version=get_app_version(),
                         exported_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _item(evaluated: EvaluatedFlow) -> FlowExportItem:
    return FlowExportItem(flow_id=evaluated.flow_id, name=evaluated.name,
                          description=evaluated.description, owned=evaluated.owned,
                          version=evaluated.version, importable=evaluated.reason is None,
                          reason=evaluated.reason)


@router.get("", response_model=FlowExportPage)
def list_flow_exports(
    response: Response,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=EXPORT_PAGE_SIZE, ge=1, le=EXPORT_PAGE_SIZE),
    curator: ExportCurator | None = Depends(require_flow_export_curator),
    config: FlowExportConfig = Depends(require_flow_export_config),
    db: Session = Depends(get_db),
) -> FlowExportPage:
    """Every flow the curator can run in AI Curation (owned or project-shared)."""
    response.headers.update(NO_STORE)
    if curator is None:
        return FlowExportPage(items=[], total_items=0, next_offset=None)
    flows, total = exportable_flows(db, curator.user_id, offset=offset, limit=limit)
    items = [_item(_evaluate(db, flow, curator, config)) for flow in flows]
    end = offset + len(items)
    return FlowExportPage(items=items, total_items=total,
                          next_offset=end if items and end < total else None)


@router.get("/{flow_id}", response_model=SignedFlowBundle)
def export_flow(
    flow_id: UUID,
    response: Response,
    version: str = Query(pattern=r"^sha256:[0-9a-f]{64}$"),
    curator: ExportCurator | None = Depends(require_flow_export_curator),
    config: FlowExportConfig = Depends(require_flow_export_config),
    db: Session = Depends(get_db),
) -> SignedFlowBundle:
    """One flow at exactly ``version``, signed for this curator for ten minutes."""
    response.headers.update(NO_STORE)
    if curator is None:
        raise _refused(404, "flow_not_found", flow_id)
    try:
        flow = get_visible_flow(db, flow_id, curator.user_id)
    except HTTPException:
        raise _refused(404, "flow_not_found", flow_id) from None
    evaluated = _evaluate(db, flow, curator, config)
    item = _item(evaluated).model_dump(mode="json")
    if evaluated.version != version:
        raise _refused(409, "flow_changed", flow_id, item=item)
    if evaluated.reason is not None or evaluated.bundle is None:
        raise _fail(422, {"code": evaluated.reason, "item": item})
    return SignedFlowBundle(
        bundle=evaluated.bundle,
        signature=sign_bundle(evaluated.bundle, config=config, now=datetime.now(timezone.utc)),
    )
