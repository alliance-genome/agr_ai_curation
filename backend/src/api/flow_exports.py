"""A curator's own runnable flows, signed for the benchmark (design section 7).

Only ``Authorization: Bearer <ID token>`` is accepted; cookies are ignored, so a
cross-site browser request can't use these routes. The token is checked as strictly
as the sign-in cookie (the issuer and group claim of the provider AUTH_PROVIDER
selects, signature, expiry), against an explicit audience allowlist
(FLOW_EXPORT_BEARER_CLIENT_IDS). Nothing here writes: a curator with no account
simply has no flows.
"""

import logging
from datetime import datetime, timezone
from typing import Any, NoReturn
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from jwt.exceptions import InvalidTokenError, PyJWKClientConnectionError, PyJWKClientError
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth_runtime.oidc import OIDCAuthProvider
from src.api.auth import expected_token_failure_reason, is_unknown_signing_key_error
from src.config import get_app_version
from src.lib.benchmarks.observability import sanitized_benchmark_error
from src.lib.flow_transfer.config import (FlowExportConfig, FlowExportSignIn, flow_export_config,
                                          flow_export_sign_in)
from src.lib.flow_transfer.export import (EvaluatedFlow, ExportCurator, evaluate_flow,
                                          exportable_flows)
from src.lib.flow_transfer.reasons import ReasonCode
from src.lib.flow_transfer.signing import sign_bundle
from src.lib.flows.access import get_visible_flow
from src.lib.group_rules import get_groups_from_provider_groups
from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.openai_agents.config import (get_auth_jwks_cache_ttl_seconds,
                                          get_auth_jwks_timeout_seconds,
                                          get_auth_provider_timeout_seconds,
                                          get_benchmark_curator_auth_max_bytes,
                                          get_flow_transfer_export_page_size)
from src.lib.security.redaction import active_secret_redaction
from src.models.sql import get_db
from src.models.sql.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/flow-exports", tags=["Flow exports"])
NO_STORE = {"Cache-Control": "no-store"}
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub"]
# RFC 9068 marks JWT access tokens with this header type.
ACCESS_TOKEN_TYPES = {"at+jwt", "application/at+jwt"}


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
_provider_key: tuple | None = None


def _bearer_provider(config: FlowExportConfig, sign_in: FlowExportSignIn) -> OIDCAuthProvider:
    """Same issuer, keys and group claim as the sign-in cookie; only the audiences differ."""
    global _provider, _provider_key
    key = (sign_in, config.bearer_client_ids)
    if _provider is None or _provider_key != key:
        # Cognito ID tokens always carry token_use, so it is required there; generic OIDC
        # defines no such claim (see _require_id_token).
        required = REQUIRED_CLAIMS + (["token_use"] if sign_in.provider == "cognito" else [])
        _provider = OIDCAuthProvider({
            "issuer_url": sign_in.issuer_url,
            "client_id": config.bearer_client_ids[0],
            "audience": list(config.bearer_client_ids),
            "group_claim": sign_in.group_claim,
            "required_claims": required,
            "timeout_seconds": get_auth_provider_timeout_seconds(),
            "jwks_timeout_seconds": get_auth_jwks_timeout_seconds(),
            "jwks_cache_ttl_seconds": get_auth_jwks_cache_ttl_seconds(),
        })
        _provider_key = key
    return _provider


def _require_id_token(token: str, claims: dict[str, Any], sign_in: FlowExportSignIn) -> None:
    """Accept ID tokens only, never access tokens.

    Cognito: ``token_use`` must be ``id``, exactly as before. Generic OIDC has no
    standard ID-token marker, so every access-token marker the supported providers
    use is refused: a ``token_use`` other than ``id``, a ``typ`` claim other than
    ``ID`` (Keycloak), a ``scope``/``scp`` claim (ID tokens never carry scopes;
    Keycloak, Auth0, Okta and Entra access tokens do), or an RFC 9068 ``at+jwt``
    header. The audience must also be an allowlisted client id, which a provider's
    API access token does not carry.
    """
    if sign_in.provider == "cognito":
        if claims.get("token_use") != "id":
            raise InvalidTokenError("Only ID tokens are accepted")
        return
    header_type = str(jwt.get_unverified_header(token).get("typ", "")).lower()
    if (claims.get("token_use", "id") != "id"
            or str(claims.get("typ", "id")).lower() != "id"
            or "scope" in claims or "scp" in claims
            or header_type in ACCESS_TOKEN_TYPES):
        raise InvalidTokenError("Only ID tokens are accepted")


def _unavailable(exc: Exception) -> NoReturn:
    try:
        raise_sanitized_http_exception(
            logger, status_code=503, detail={"code": "authorization_unavailable"},
            log_message="Flow export sign-in check unavailable",
            exc=sanitized_benchmark_error("flow_export_sign_in", type(exc).__name__),
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
            sign_in = flow_export_sign_in()
            provider = _bearer_provider(config, sign_in)
            claims = await provider.validate_token(token)
            _require_id_token(token, claims, sign_in)
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
    limit: int | None = Query(default=None, ge=1),
    curator: ExportCurator | None = Depends(require_flow_export_curator),
    config: FlowExportConfig = Depends(require_flow_export_config),
    db: Session = Depends(get_db),
) -> FlowExportPage:
    """Every flow the curator can run in AI Curation (owned or project-shared)."""
    page_size = get_flow_transfer_export_page_size()
    if limit is None:
        limit = page_size
    elif limit > page_size:
        # The same 422 a static ``le`` would give; the bound is read from env per request.
        raise RequestValidationError([{
            "type": "less_than_equal", "loc": ("query", "limit"),
            "msg": f"Input should be less than or equal to {page_size}", "input": limit,
            "ctx": {"le": page_size}}])
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
    """One flow at exactly ``version``, signed for this curator for a short time."""
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
