"""Reranking helpers with provider-based dispatch."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Sequence
from urllib import error, request

try:  # pragma: no cover - exercised when package-runner env omits AWS deps
    import boto3
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore[assignment]

try:  # pragma: no cover - exercised when package-runner env omits AWS deps
    from botocore.exceptions import (
        BotoCoreError,
        ConnectTimeoutError,
        NoCredentialsError,
        PartialCredentialsError,
        ProfileNotFound,
        ReadTimeoutError,
    )
except ImportError:  # pragma: no cover
    class BotoCoreError(Exception):
        pass

    class NoCredentialsError(Exception):
        pass

    class PartialCredentialsError(Exception):
        pass

    class ProfileNotFound(Exception):
        pass

    class ConnectTimeoutError(Exception):
        pass

    class ReadTimeoutError(Exception):
        pass

from src.lib.aws_env import without_blank_aws_profile_env_vars
from src.lib.observability.runtime import report_runtime_exception

logger = logging.getLogger(__name__)

DEFAULT_RERANK_PROVIDER = "bedrock_cohere"
DEFAULT_BEDROCK_RERANK_MODEL_ARN = (
    "arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0"
)
# Env-configurable (defaults unchanged). This module can load inside the isolated
# package subprocess (which inherits the backend env), so it reads os.getenv
# directly using the same env var names documented in .env.example:
#   BEDROCK_RERANK_MAX_SOURCES (default 100),
#   LOCAL_TRANSFORMERS_RERANK_TIMEOUT_SECONDS (default 5).
MAX_BEDROCK_RERANK_SOURCES = int(os.getenv("BEDROCK_RERANK_MAX_SOURCES", "100"))
_DEFAULT_LOCAL_TRANSFORMERS_URL = "http://reranker-transformers:8080"
LOCAL_TRANSFORMERS_TIMEOUT_SECONDS = int(
    os.getenv("LOCAL_TRANSFORMERS_RERANK_TIMEOUT_SECONDS", "5")
)
RERANK_AWS_ENV_OVERRIDES = {
    "RERANK_AWS_PROFILE": "AWS_PROFILE",
    "RERANK_AWS_SHARED_CREDENTIALS_FILE": "AWS_SHARED_CREDENTIALS_FILE",
    "RERANK_AWS_CONFIG_FILE": "AWS_CONFIG_FILE",
}


class RerankProviderError(RuntimeError):
    """Sanitized configured-provider failure safe to surface and report."""

    def __init__(self, provider: str, category: str, message: str | None = None):
        self.provider = provider
        self.category = category
        super().__init__(
            message or f"Configured rerank provider {provider} failed ({category})"
        )


def get_rerank_provider() -> str:
    """Return the configured reranker provider."""
    return os.getenv("RERANK_PROVIDER", DEFAULT_RERANK_PROVIDER).strip().lower()


def get_effective_rerank_provider() -> str:
    """Return the provider that should be used for request-time reranking."""
    return get_rerank_provider()


def get_bedrock_reranker_status(*, check_credentials: bool = True) -> Dict[str, Any]:
    """Return sanitized readiness details for the Bedrock reranker provider."""
    provider = get_rerank_provider()
    rerank_aws_profile = os.getenv("RERANK_AWS_PROFILE")
    aws_profile = os.getenv("AWS_PROFILE")
    aws_default_profile = os.getenv("AWS_DEFAULT_PROFILE")
    model_arn = _get_bedrock_rerank_model_arn()

    status: Dict[str, Any] = {
        "provider": provider,
        "region": None,
        "model_arn_configured": bool(model_arn),
        "rerank_aws_profile_configured": bool(
            rerank_aws_profile and rerank_aws_profile.strip()
        ),
        "aws_profile_configured": bool(aws_profile and aws_profile.strip()),
        "aws_default_profile_configured": bool(
            aws_default_profile and aws_default_profile.strip()
        ),
        "is_healthy": None,
        "reason": None,
    }

    if provider == "none":
        status["reason"] = "RERANK_PROVIDER disables post-retrieval reranking"
        return status

    if provider != "bedrock_cohere":
        status["is_healthy"] = False
        status["reason"] = f"Unsupported RERANK_PROVIDER={provider}"
        return status

    try:
        region = _get_aws_region()
    except ValueError as exc:
        status["is_healthy"] = False
        status["reason"] = str(exc)
        return status
    status["region"] = region

    if not model_arn:
        status["is_healthy"] = False
        status["reason"] = "BEDROCK_RERANK_MODEL_ARN is required for bedrock_cohere"
        return status

    if boto3 is None:
        status["is_healthy"] = False
        status["reason"] = (
            "boto3/botocore are required for Bedrock reranking but are not installed"
        )
        return status

    try:
        with _with_rerank_aws_env():
            session = _bedrock_session(region)
            if check_credentials and session.get_credentials() is None:
                status["is_healthy"] = False
                status["reason"] = (
                    "AWS credentials were not found for Bedrock reranking; "
                    "configure an IAM role, environment credentials, or a valid "
                    "RERANK_AWS_PROFILE/AWS_PROFILE, or set RERANK_PROVIDER=none"
                )
                return status
    except ProfileNotFound as exc:
        status["is_healthy"] = False
        status["reason"] = (
            f"AWS profile configured for Bedrock reranking was not found: {exc}"
        )
        return status
    except (BotoCoreError, NoCredentialsError, PartialCredentialsError) as exc:
        status["is_healthy"] = False
        status["reason"] = f"AWS credential resolution failed for Bedrock reranking: {exc}"
        return status

    status["is_healthy"] = True
    status["reason"] = "Bedrock reranker configuration is usable"
    return status


def validate_bedrock_reranker_configuration(
    *,
    check_credentials: bool = True,
) -> tuple[bool, str | None]:
    """Validate Bedrock reranker config without leaking credential values."""
    status = get_bedrock_reranker_status(check_credentials=check_credentials)
    is_healthy = status.get("is_healthy")
    if is_healthy is True:
        return True, None
    if is_healthy is None:
        return True, None
    reason = status["reason"]
    assert isinstance(reason, str)
    return False, reason


def _get_aws_region() -> str:
    raw_region = os.getenv("AWS_REGION")
    if raw_region is None:
        return "us-east-1"

    region = raw_region.strip()
    if not region:
        raise ValueError("AWS_REGION must not be blank for Bedrock reranking")
    return region


def _get_bedrock_rerank_model_arn() -> str:
    return os.getenv(
        "BEDROCK_RERANK_MODEL_ARN",
        DEFAULT_BEDROCK_RERANK_MODEL_ARN,
    ).strip()


def _get_rerank_aws_profile() -> str:
    rerank_profile = os.getenv("RERANK_AWS_PROFILE", "").strip()
    if rerank_profile:
        return rerank_profile
    return os.getenv("AWS_PROFILE", "").strip()


@contextmanager
def _with_rerank_aws_env():
    """Apply rerank-only AWS env overrides while constructing Bedrock clients."""
    saved_values: Dict[str, str | None] = {}
    for source_key, target_key in RERANK_AWS_ENV_OVERRIDES.items():
        source_value = os.getenv(source_key)
        if source_value is None:
            continue
        saved_values[target_key] = os.environ.get(target_key)
        if source_value.strip():
            os.environ[target_key] = source_value
        else:
            os.environ.pop(target_key, None)

    try:
        with without_blank_aws_profile_env_vars():
            yield
    finally:
        for target_key, original_value in saved_values.items():
            if original_value is None:
                os.environ.pop(target_key, None)
            else:
                os.environ[target_key] = original_value


def _bedrock_session(region: str):
    if boto3 is None:
        raise RuntimeError(
            "boto3/botocore are required for Bedrock reranking but are not installed"
        )

    aws_profile = _get_rerank_aws_profile()
    if aws_profile:
        return boto3.Session(profile_name=aws_profile, region_name=region)
    return boto3.Session(region_name=region)


def _bedrock_agent_runtime_client():
    region = _get_aws_region()
    with _with_rerank_aws_env():
        session = _bedrock_session(region)
        return session.client("bedrock-agent-runtime", region_name=region)


def _get_local_transformers_url() -> str:
    return os.getenv("RERANKER_URL", _DEFAULT_LOCAL_TRANSFORMERS_URL)


def _log_rerank_request(
    provider: str,
    candidate_count: int,
    requested_results: int,
    query: str,
) -> None:
    query_fingerprint = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    logger.info(
        "rerank request provider=%s candidates=%s requested_results=%s query_fingerprint=%s",
        provider,
        candidate_count,
        requested_results,
        query_fingerprint,
    )


def _log_rerank_complete(
    provider: str,
    requested_results: int,
    results_count: int,
    top_rerank_score: float | None,
    duration_ms: float,
) -> None:
    logger.info(
        "rerank complete provider=%s requested_results=%s results=%s top_rerank_score=%s duration_ms=%.1f",
        provider,
        requested_results,
        results_count,
        top_rerank_score,
        duration_ms,
    )


def _report_rerank_failure(exc: RerankProviderError) -> None:
    logger.error(
        "Configured rerank provider failed provider=%s failure_category=%s",
        exc.provider,
        exc.category,
    )
    # Report a fresh sanitized exception without provider-response traceback
    # locals; response bodies and request content must never reach Sentry.
    incident = RerankProviderError(exc.provider, exc.category)
    report_runtime_exception(
        incident,
        component="rerank_provider",
        operation="rerank_chunks",
        context={
            "provider": exc.provider or "<blank>",
            "failure_category": exc.category,
        },
        level="error",
    )


def _parse_rerank_score(provider: str, value: Any) -> float:
    """Require a finite JSON number for a provider relevance score."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise RerankProviderError(provider, "malformed_response")
    try:
        score = float(value)
    except (OverflowError, TypeError, ValueError):
        raise RerankProviderError(provider, "malformed_response") from None
    if not math.isfinite(score):
        raise RerankProviderError(provider, "malformed_response")
    return score


def rerank_chunks(
    query: str,
    chunks: Sequence[Dict[str, Any]],
    *,
    top_n: int | None = None,
) -> List[Dict[str, Any]]:
    """Rerank chunk candidates with the configured provider."""
    provider = get_rerank_provider()
    if provider == "none":
        return list(chunks)
    dispatch = _RERANK_DISPATCH.get(provider)
    if dispatch is None:
        exc = RerankProviderError(
            provider,
            "configuration",
            f"Unsupported RERANK_PROVIDER={provider}",
        )
        # ALL-822: validate configured providers even when retrieval returned no
        # candidates; only RERANK_PROVIDER=none is the canonical opt-out.
        _report_rerank_failure(exc)
        raise exc
    if not chunks:
        return []

    try:
        ranked_chunks = dispatch(query, chunks, top_n=top_n)
        if not ranked_chunks:
            raise RerankProviderError(provider, "empty_response")
    except RerankProviderError as exc:
        # ALL-822: configured providers fail closed. RERANK_PROVIDER=none is the
        # canonical opt-out and the only mode that preserves retrieval order.
        _report_rerank_failure(exc)
        raise
    except (NoCredentialsError, PartialCredentialsError, ProfileNotFound):
        exc = RerankProviderError(
            provider,
            "credentials",
            "AWS credential/profile resolution failed for Bedrock reranking",
        )
        _report_rerank_failure(exc)
        raise exc from None
    except (TimeoutError, ConnectTimeoutError, ReadTimeoutError):
        exc = RerankProviderError(provider, "timeout")
        _report_rerank_failure(exc)
        raise exc from None
    except Exception:
        exc = RerankProviderError(provider, "provider_failure")
        _report_rerank_failure(exc)
        raise exc from None

    return ranked_chunks


def _rerank_chunks_with_local_transformers(
    query: str,
    chunks: Sequence[Dict[str, Any]],
    *,
    top_n: int | None = None,
) -> List[Dict[str, Any]]:
    candidate_chunks = list(chunks)
    requested_results = min(top_n or len(candidate_chunks), len(candidate_chunks))

    payload = {
        "query": query,
        "documents": [
            _text_for_rerank(candidate_chunk) for candidate_chunk in candidate_chunks
        ],
    }
    rerank_url = f"{_get_local_transformers_url().rstrip('/')}/rerank"
    rerank_request = request.Request(
        rerank_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    rerank_start = time.monotonic()
    _log_rerank_request(
        "local_transformers",
        len(candidate_chunks),
        requested_results,
        query,
    )
    try:
        with request.urlopen(
            rerank_request,
            timeout=LOCAL_TRANSFORMERS_TIMEOUT_SECONDS,
        ) as resp:
            raw_payload = resp.read().decode("utf-8")
    except TimeoutError:
        raise
    except error.URLError as exc:
        raise RerankProviderError(
            "local_transformers",
            (
                "timeout"
                if isinstance(exc.reason, TimeoutError)
                else "provider_unavailable"
            ),
        ) from None

    try:
        payload_dict = json.loads(raw_payload)
    except json.JSONDecodeError:
        raise RerankProviderError(
            "local_transformers", "malformed_response"
        ) from None

    scores = payload_dict.get("scores") if isinstance(payload_dict, dict) else None
    if not isinstance(scores, list):
        raise RerankProviderError("local_transformers", "malformed_response")

    if not scores:
        raise RerankProviderError("local_transformers", "empty_response")
    if len(scores) != len(candidate_chunks):
        raise RerankProviderError("local_transformers", "incomplete_response")

    ranked_chunks: List[Dict[str, Any]] = []
    seen_indexes: set[int] = set()
    rerank_scores: list[tuple[int, float]] = []
    for result_index, item in enumerate(scores):
        if not isinstance(item, dict):
            raise RerankProviderError("local_transformers", "malformed_response")
        score_value = item.get("score")
        if score_value is None:
            raise RerankProviderError("local_transformers", "malformed_response")

        # The local reranker sidecar returns scores in request order and omits an
        # explicit index, but we still reject malformed explicit indexes.
        source_index = item.get("index", result_index)
        if (
            not isinstance(source_index, int)
            or isinstance(source_index, bool)
            or source_index < 0
            or source_index >= len(candidate_chunks)
            or source_index in seen_indexes
        ):
            raise RerankProviderError("local_transformers", "malformed_response")
        rerank_score = _parse_rerank_score("local_transformers", score_value)
        seen_indexes.add(source_index)
        rerank_scores.append((source_index, rerank_score))

    rerank_scores.sort(key=lambda score_item: score_item[1], reverse=True)
    selected_indexes: set[int] = set()
    for source_index, rerank_score in rerank_scores[:requested_results]:
        selected_indexes.add(source_index)
        original_chunk = candidate_chunks[source_index]
        ranked_chunk = dict(original_chunk)
        ranked_chunk.pop("_rerank_text", None)
        metadata = dict(ranked_chunk.get("metadata", {}) or {})
        retrieval_score = ranked_chunk.get("score")
        if retrieval_score is not None:
            metadata["retrieval_score"] = retrieval_score
        metadata["rerank_score"] = rerank_score
        ranked_chunk["metadata"] = metadata
        ranked_chunk["score"] = rerank_score
        ranked_chunks.append(ranked_chunk)

    for source_index, original_chunk in enumerate(candidate_chunks):
        if source_index in selected_indexes:
            continue
        # The provider scored every candidate above. Preserve only the caller's
        # intentional top_n boundary, never a missing provider result.
        preserved_chunk = dict(original_chunk)
        preserved_chunk.pop("_rerank_text", None)
        ranked_chunks.append(preserved_chunk)

    top_rerank_score = None
    if ranked_chunks:
        top_rerank_score = (ranked_chunks[0].get("metadata") or {}).get("rerank_score")
    duration_ms = (time.monotonic() - rerank_start) * 1000

    _log_rerank_complete(
        "local_transformers",
        requested_results,
        len(rerank_scores),
        top_rerank_score,
        duration_ms,
    )
    return ranked_chunks


def _rerank_chunks_with_bedrock(
    query: str,
    chunks: Sequence[Dict[str, Any]],
    *,
    top_n: int | None = None,
) -> List[Dict[str, Any]]:
    is_ready, reason = validate_bedrock_reranker_configuration(check_credentials=False)
    if not is_ready:
        raise RerankProviderError(
            "bedrock_cohere",
            "configuration",
            f"Bedrock reranker configuration is not ready: {reason}",
        )

    candidate_chunks = list(chunks)[:MAX_BEDROCK_RERANK_SOURCES]
    if len(candidate_chunks) < len(chunks):
        logger.warning(
            "Truncating rerank candidates from %s to %s due to Bedrock source limit",
            len(chunks),
            MAX_BEDROCK_RERANK_SOURCES,
        )

    requested_results = min(top_n or len(candidate_chunks), len(candidate_chunks))
    model_arn = _get_bedrock_rerank_model_arn()
    rerank_start = time.monotonic()
    _log_rerank_request(
        "bedrock_cohere",
        len(candidate_chunks),
        requested_results,
        query,
    )
    logger.info(
        "Bedrock rerank request: provider=%s model_arn=%s candidates=%s requested_results=%s query_fingerprint=%s",
        get_rerank_provider(),
        model_arn,
        len(candidate_chunks),
        requested_results,
        hashlib.sha256(query.encode("utf-8")).hexdigest()[:16],
    )
    client = _bedrock_agent_runtime_client()

    request_payload: dict[str, Any] = {
        "queries": [{"type": "TEXT", "textQuery": {"text": query}}],
        "sources": [
            {
                "type": "INLINE",
                "inlineDocumentSource": {
                    "type": "TEXT",
                    "textDocument": {"text": _text_for_rerank(chunk)},
                },
            }
            for chunk in candidate_chunks
        ],
        "rerankingConfiguration": {
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                "modelConfiguration": {"modelArn": model_arn},
                "numberOfResults": requested_results,
            },
        },
    }
    ranked_results: list[Any] = []
    seen_next_tokens: set[str] = set()
    while True:
        response = client.rerank(**request_payload)
        if not isinstance(response, dict):
            raise RerankProviderError("bedrock_cohere", "malformed_response")
        page_results = response.get("results")
        if not isinstance(page_results, list):
            raise RerankProviderError("bedrock_cohere", "malformed_response")
        if not page_results:
            category = "empty_response" if not ranked_results else "incomplete_response"
            raise RerankProviderError("bedrock_cohere", category)
        ranked_results.extend(page_results)
        if len(ranked_results) > requested_results:
            raise RerankProviderError("bedrock_cohere", "malformed_response")
        if len(ranked_results) == requested_results:
            break

        next_token = response.get("nextToken")
        if next_token is None:
            break
        if (
            not isinstance(next_token, str)
            or not next_token.strip()
            or next_token in seen_next_tokens
        ):
            raise RerankProviderError("bedrock_cohere", "malformed_response")
        seen_next_tokens.add(next_token)
        request_payload["nextToken"] = next_token

    if len(ranked_results) != requested_results:
        raise RerankProviderError("bedrock_cohere", "incomplete_response")

    ranked_chunks: List[Dict[str, Any]] = []
    seen_indexes: set[int] = set()
    for result in ranked_results:
        if not isinstance(result, dict):
            raise RerankProviderError("bedrock_cohere", "malformed_response")
        source_index = result.get("index")
        if (
            not isinstance(source_index, int)
            or isinstance(source_index, bool)
            or source_index < 0
            or source_index >= len(candidate_chunks)
            or source_index in seen_indexes
            or "relevanceScore" not in result
        ):
            raise RerankProviderError("bedrock_cohere", "malformed_response")
        rerank_score = _parse_rerank_score(
            "bedrock_cohere", result["relevanceScore"]
        )
        seen_indexes.add(source_index)
        original_chunk = candidate_chunks[source_index]
        ranked_chunk = dict(original_chunk)
        ranked_chunk.pop("_rerank_text", None)
        metadata = dict(ranked_chunk.get("metadata", {}) or {})
        retrieval_score = ranked_chunk.get("score")
        if retrieval_score is not None:
            metadata["retrieval_score"] = retrieval_score
        metadata["rerank_score"] = rerank_score
        ranked_chunk["metadata"] = metadata
        ranked_chunk["score"] = rerank_score
        ranked_chunks.append(ranked_chunk)

    if len(ranked_chunks) < len(candidate_chunks):
        # Response completeness is validated above. Any remaining candidates
        # are outside an intentional top_n request, not an implicit fallback.
        for source_index, original_chunk in enumerate(candidate_chunks):
            if source_index in seen_indexes:
                continue
            preserved_chunk = dict(original_chunk)
            preserved_chunk.pop("_rerank_text", None)
            ranked_chunks.append(preserved_chunk)

    if len(candidate_chunks) < len(chunks):
        # Candidates beyond the configured Bedrock source limit were never sent
        # to the provider and therefore remain in retrieval order by design.
        for original_chunk in list(chunks)[len(candidate_chunks):]:
            preserved_chunk = dict(original_chunk)
            preserved_chunk.pop("_rerank_text", None)
            ranked_chunks.append(preserved_chunk)

    reordered_positions = _count_reordered_positions(
        candidate_chunks,
        ranked_chunks[: len(candidate_chunks)],
    )
    top_rerank_score = None
    if ranked_chunks:
        top_rerank_score = (ranked_chunks[0].get("metadata") or {}).get("rerank_score")
    duration_ms = (time.monotonic() - rerank_start) * 1000
    _log_rerank_complete(
        "bedrock_cohere",
        requested_results,
        len(ranked_results),
        top_rerank_score,
        duration_ms,
    )
    logger.info(
        "Bedrock rerank complete: model_arn=%s results=%s reordered_positions=%s top_rerank_score=%s duration_ms=%.1f",
        model_arn,
        len(ranked_results),
        reordered_positions,
        top_rerank_score,
        duration_ms,
    )

    return ranked_chunks


_RERANK_DISPATCH = {
    "bedrock_cohere": _rerank_chunks_with_bedrock,
    "local_transformers": _rerank_chunks_with_local_transformers,
}


def _text_for_rerank(chunk: Dict[str, Any]) -> str:
    # Rerank against full source chunk text only. content_preview is intentionally
    # excluded: previews are display metadata and may omit the evidence-bearing
    # sentence that the extractor must later select via read_chunk spans. Prefer
    # canonical full-text fields even if an older caller supplied _rerank_text.
    rerank_text = (
        chunk.get("text")
        or chunk.get("content")
        or chunk.get("_rerank_text")
        or ""
    )
    return str(rerank_text)


def _chunk_identity(chunk: Dict[str, Any]) -> str:
    metadata = chunk.get("metadata") or {}
    return str(
        chunk.get("id")
        or metadata.get("chunk_id")
        or metadata.get("uuid")
        or metadata.get("id")
        or ""
    ).strip()


def _count_reordered_positions(
    original_chunks: Sequence[Dict[str, Any]],
    ranked_chunks: Sequence[Dict[str, Any]],
) -> int:
    original_ids = [_chunk_identity(chunk) for chunk in original_chunks]
    ranked_ids = [_chunk_identity(chunk) for chunk in ranked_chunks]
    reordered = 0
    for original_id, ranked_id in zip(original_ids, ranked_ids):
        if original_id and ranked_id and original_id != ranked_id:
            reordered += 1
    return reordered
