"""CLI for reranking search candidates."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence
from uuid import UUID

from lib.reranker import Reranker, RerankerCandidate


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reranker CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    rerank_parser = subparsers.add_parser("rerank", help="Rerank candidate chunks")
    rerank_parser.add_argument(
        "--candidates", required=True, help="Path to JSON candidates file"
    )
    rerank_parser.add_argument("--query", help="Query text (overrides file)")
    rerank_parser.add_argument("--top-k", type=int, default=5)
    rerank_parser.add_argument(
        "--mmr", action="store_true", help="Enable MMR diversification"
    )
    rerank_parser.add_argument("--lambda", dest="lambda_param", type=float, default=0.7)

    subparsers.add_parser(
        "evaluate", help="Evaluate reranker performance (not implemented)"
    )

    return parser.parse_args(argv)


def _load_candidates(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, dict) and "candidates" in payload:
        candidates = payload["candidates"]
        query = payload.get("query")
    else:
        candidates = payload
        query = None

    return {"query": query, "candidates": candidates}


def _parse_candidate(item: dict) -> RerankerCandidate:
    try:
        chunk_id = UUID(item["chunk_id"])
        text = item["text"]
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Candidate missing required field: {exc}") from exc

    return RerankerCandidate(
        chunk_id=chunk_id,
        text=text,
        retriever_score=float(item.get("retriever_score", 0.0)),
        embedding=item.get("embedding"),
        metadata=item.get("metadata", {}),
    )


def _command_rerank(args: argparse.Namespace) -> None:
    payload = _load_candidates(args.candidates)
    query = args.query or payload.get("query")
    if not query:
        raise ValueError("Query text must be provided via --query or candidates file")

    candidates = [_parse_candidate(item) for item in payload["candidates"]]
    reranker = Reranker()
    results = reranker.rerank(
        query=query,
        candidates=candidates,
        top_k=args.top_k,
        apply_mmr=args.mmr,
        lambda_param=args.lambda_param,
    )

    json.dump(
        [
            {
                "chunk_id": str(result.chunk_id),
                "rerank_score": result.rerank_score,
                "combined_score": result.combined_score,
                "metadata": result.metadata,
                "rank": result.rank,
            }
            for result in results
        ],
        sys.stdout,
        indent=2,
    )
    sys.stdout.write("\n")


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv or sys.argv[1:])
    if args.command == "rerank":
        _command_rerank(args)
    else:  # pragma: no cover - placeholder for future extensions
        sys.stderr.write("The evaluate command is not yet implemented.\n")
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
