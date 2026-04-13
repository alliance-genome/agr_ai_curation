"""Amazon Bedrock reranking helpers."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Sequence

import boto3

logger = logging.getLogger(__name__)

DEFAULT_RERANK_PROVIDER = "bedrock_cohere"
DEFAULT_BEDROCK_RERANK_MODEL_ARN = (
    "arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0"
)
MAX_BEDROCK_RERANK_SOURCES = 100


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
    if provider != "bedrock_cohere":
        raise RuntimeError(f"Unsupported RERANK_PROVIDER={provider}")
    try:
        ranked_chunks = _rerank_chunks_with_bedrock(query, chunks, top_n=top_n)
    except Exception:
        logger.exception(
            "Bedrock reranking failed; preserving original retrieval order for query=%r",
            query[:200],
        )
        return list(chunks)

    if not ranked_chunks:
        logger.warning(
            "Bedrock reranking returned no results; preserving original retrieval order for query=%r",
            query[:200],
        )
        return list(chunks)

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
    reordered_positions = _count_reordered_positions(candidate_chunks, ranked_chunks[:len(candidate_chunks)])
    top_rerank_score = None
    if ranked_chunks:
        top_rerank_score = (ranked_chunks[0].get("metadata") or {}).get("rerank_score")
    logger.info(
        "Bedrock rerank complete: model_arn=%s results=%s reordered_positions=%s top_rerank_score=%s duration_ms=%.1f",
        model_arn,
        len(ranked_results),
        reordered_positions,
        top_rerank_score,
        (time.monotonic() - rerank_start) * 1000,
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
