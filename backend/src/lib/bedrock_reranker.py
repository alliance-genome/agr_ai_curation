"""Reranking helpers with provider-based dispatch."""

from __future__ import annotations

import json
import logging
import os
import time
from urllib import error, request
from typing import Any, Dict, List, Sequence

import boto3

logger = logging.getLogger(__name__)

DEFAULT_RERANK_PROVIDER = "bedrock_cohere"
DEFAULT_BEDROCK_RERANK_MODEL_ARN = (
    "arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0"
)
MAX_BEDROCK_RERANK_SOURCES = 100
_DEFAULT_LOCAL_TRANSFORMERS_URL = "http://reranker-transformers:8080"
LOCAL_TRANSFORMERS_TIMEOUT_SECONDS = 5


def get_rerank_provider() -> str:
    """Return the configured reranker provider."""
    return os.getenv("RERANK_PROVIDER", DEFAULT_RERANK_PROVIDER).strip().lower()


def _bedrock_agent_runtime_client():
    region = os.getenv("AWS_REGION", "us-east-1")
    aws_profile = os.getenv("AWS_PROFILE", "").strip()
    if aws_profile:
        session = boto3.Session(profile_name=aws_profile, region_name=region)
    else:
        session = boto3.Session(region_name=region)
    return session.client("bedrock-agent-runtime", region_name=region)


def _get_local_transformers_url() -> str:
    return os.getenv("RERANKER_URL", _DEFAULT_LOCAL_TRANSFORMERS_URL)


def _log_rerank_request(
    provider: str,
    candidate_count: int,
    requested_results: int,
    query: str,
) -> None:
    logger.info(
        "rerank request provider=%s candidates=%s requested_results=%s query_preview=%r",
        provider,
        candidate_count,
        requested_results,
        query[:120],
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


def _log_rerank_no_results(provider: str, query: str) -> None:
    if provider == "bedrock_cohere":
        logger.warning(
            "Bedrock reranking returned no results; preserving original retrieval order for query=%r",
            query[:200],
        )
        return
    logger.warning(
        "rerank no results provider=%s preserving original retrieval order for query=%r",
        provider,
        query[:200],
    )


def rerank_chunks(
    query: str,
    chunks: Sequence[Dict[str, Any]],
    *,
    top_n: int | None = None,
) -> List[Dict[str, Any]]:
    """Rerank chunk candidates with the configured provider."""
    provider = get_rerank_provider()
    if provider in {"", "none"}:
        return list(chunks)

    if provider == "bedrock_cohere":
        try:
            ranked_chunks = _rerank_chunks_with_bedrock(query, chunks, top_n=top_n)
        except Exception:
            logger.exception(
                "Bedrock reranking failed; preserving original retrieval order for query=%r",
                query[:200],
            )
            return list(chunks)
    elif provider == "local_transformers":
        try:
            ranked_chunks = _rerank_chunks_with_local_transformers(
                query, chunks, top_n=top_n
            )
        except Exception:
            logger.exception(
                "Local transformers reranking failed; preserving original retrieval order for query=%r",
                query[:200],
            )
            return list(chunks)
    else:
        raise RuntimeError(f"Unsupported RERANK_PROVIDER={provider}")

    if not ranked_chunks:
        _log_rerank_no_results(provider, query)
        return list(chunks)

    return ranked_chunks


def _rerank_chunks_with_local_transformers(
    query: str,
    chunks: Sequence[Dict[str, Any]],
    *,
    top_n: int | None = None,
) -> List[Dict[str, Any]]:
    if not chunks:
        return []

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
    except (error.URLError, TimeoutError) as exc:
        raise RuntimeError("Local transformers request failed") from exc

    try:
        payload_dict = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Local transformers response invalid JSON") from exc

    scores = payload_dict.get("scores") if isinstance(payload_dict, dict) else None
    if not isinstance(scores, list):
        raise RuntimeError("Local transformers response missing scores")

    if not scores:
        return []

    ranked_chunks: List[Dict[str, Any]] = []
    seen_indexes: set[int] = set()
    rerank_scores: list[tuple[int, float]] = []
    for result_index, item in enumerate(scores):
        if not isinstance(item, dict):
            continue
        score_value = item.get("score")
        if score_value is None:
            continue

        # The local reranker sidecar returns scores in request order and omits an
        # explicit index, but we still reject malformed explicit indexes.
        source_index = item.get("index", result_index)
        if not isinstance(source_index, int):
            raise RuntimeError(
                "Local transformers response index must be an integer when provided"
            )
        if source_index >= len(candidate_chunks):
            continue
        if source_index < 0:
            continue
        rerank_scores.append((source_index, float(score_value)))

    rerank_scores.sort(key=lambda score_item: score_item[1], reverse=True)
    for source_index, rerank_score in rerank_scores[:requested_results]:
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

    for source_index, original_chunk in enumerate(candidate_chunks):
        if source_index in seen_indexes:
            continue
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
    if not chunks:
        return []

    candidate_chunks = list(chunks)[:MAX_BEDROCK_RERANK_SOURCES]
    if len(candidate_chunks) < len(chunks):
        logger.warning(
            "Truncating rerank candidates from %s to %s due to Bedrock source limit",
            len(chunks),
            MAX_BEDROCK_RERANK_SOURCES,
        )

    requested_results = min(top_n or len(candidate_chunks), len(candidate_chunks))
    model_arn = os.getenv(
        "BEDROCK_RERANK_MODEL_ARN",
        DEFAULT_BEDROCK_RERANK_MODEL_ARN,
    )
    rerank_start = time.monotonic()
    _log_rerank_request(
        "bedrock_cohere",
        len(candidate_chunks),
        requested_results,
        query,
    )
    logger.info(
        "Bedrock rerank request: provider=%s model_arn=%s candidates=%s requested_results=%s query_preview=%r",
        get_rerank_provider(),
        model_arn,
        len(candidate_chunks),
        requested_results,
        query[:120],
    )
    client = _bedrock_agent_runtime_client()

    response = client.rerank(
        queries=[{"type": "TEXT", "textQuery": {"text": query}}],
        sources=[
            {
                "type": "INLINE",
                "inlineDocumentSource": {
                    "type": "TEXT",
                    "textDocument": {"text": _text_for_rerank(chunk)},
                },
            }
            for chunk in candidate_chunks
        ],
        rerankingConfiguration={
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                "modelConfiguration": {"modelArn": model_arn},
                "numberOfResults": requested_results,
            },
        },
    )

    ranked_chunks: List[Dict[str, Any]] = []
    seen_indexes: set[int] = set()
    for result in response.get("results", []):
        source_index = result.get("index")
        if source_index is None or source_index >= len(candidate_chunks):
            continue
        seen_indexes.add(source_index)
        rerank_score = float(result.get("relevanceScore", 0.0))
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
        for source_index, original_chunk in enumerate(candidate_chunks):
            if source_index in seen_indexes:
                continue
            preserved_chunk = dict(original_chunk)
            preserved_chunk.pop("_rerank_text", None)
            ranked_chunks.append(preserved_chunk)

    if len(candidate_chunks) < len(chunks):
        for original_chunk in list(chunks)[len(candidate_chunks):]:
            preserved_chunk = dict(original_chunk)
            preserved_chunk.pop("_rerank_text", None)
            ranked_chunks.append(preserved_chunk)

    ranked_results = response.get("results", [])
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


def _text_for_rerank(chunk: Dict[str, Any]) -> str:
    rerank_text = (
        chunk.get("_rerank_text")
        or chunk.get("content_preview")
        or chunk.get("text")
        or chunk.get("content")
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
