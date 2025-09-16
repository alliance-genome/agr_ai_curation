"""CLI utilities for embedding operations."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence
from uuid import UUID

from app.database import SessionLocal
from app.models import PDFDocument, PDFEmbedding
from lib.embedding_service import EmbeddingModelConfig, EmbeddingService


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Embedding service CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list-models", help="List configured embedding models"
    )

    embed_parser = subparsers.add_parser("embed", help="Generate embeddings for a PDF")
    embed_parser.add_argument("pdf_id", type=UUID)
    embed_parser.add_argument("--model")
    embed_parser.add_argument("--version")
    embed_parser.add_argument("--batch-size", type=int)
    embed_parser.add_argument("--force", action="store_true")

    status_parser = subparsers.add_parser(
        "status", help="Show embedding status for a PDF"
    )
    status_parser.add_argument("pdf_id", type=UUID)

    return parser.parse_args(argv)


def _load_service() -> EmbeddingService:
    session_factory = SessionLocal
    client = _load_client()
    config = EmbeddingModelConfig(
        name="text-embedding-3-small",
        dimensions=1536,
        default_version="1.0",
        max_batch_size=128,
    )
    return EmbeddingService(
        session_factory=session_factory,
        embedding_client=client,
        models={config.name: config},
    )


def _load_client():  # pragma: no cover - runtime dependency injection
    raise NotImplementedError("Embedding client factory not implemented")


def _list_models(service: EmbeddingService) -> None:
    payload = [
        {
            "name": config.name,
            "dimensions": config.dimensions,
            "default_version": config.default_version,
            "max_batch_size": config.max_batch_size,
        }
        for config in service.list_models()
    ]
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")


def _embed_pdf(service: EmbeddingService, args: argparse.Namespace) -> None:
    model_name = args.model or next((cfg.name for cfg in service.list_models()), None)
    if model_name is None:
        raise ValueError("No embedding models configured")

    result = service.embed_pdf(
        pdf_id=args.pdf_id,
        model_name=model_name,
        version=args.version,
        batch_size=args.batch_size,
        force=args.force,
    )
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")


def _status(service: EmbeddingService, args: argparse.Namespace) -> None:
    with SessionLocal() as session:
        document = session.get(PDFDocument, args.pdf_id)
        if document is None:
            sys.stderr.write(f"PDF document {args.pdf_id} not found\n")
            sys.exit(1)

        embeddings = session.query(PDFEmbedding).filter_by(pdf_id=args.pdf_id).all()
        payload = {
            "pdf_id": str(args.pdf_id),
            "embeddings_generated": document.embeddings_generated,
            "models": document.embedding_models,
            "count": len(embeddings),
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv or sys.argv[1:])
    service = _load_service()

    if args.command == "list-models":
        _list_models(service)
    elif args.command == "embed":
        _embed_pdf(service, args)
    elif args.command == "status":
        _status(service, args)
    else:  # pragma: no cover - defensive
        sys.stderr.write(f"Unknown command: {args.command}\n")
        sys.exit(2)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
