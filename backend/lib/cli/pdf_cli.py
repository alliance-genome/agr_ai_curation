"""Command line interface for PDF processing and chunking."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Dict

from ..pdf_processor import PDFProcessor
from ..chunk_manager import ChunkManager, ChunkingStrategy


def _serialize(obj: Any) -> Any:
    """Convert dataclasses and complex objects into JSON-serialisable forms."""

    if is_dataclass(obj):
        return {k: _serialize(v) for k, v in asdict(obj).items()}

    if isinstance(obj, Enum):
        return obj.value

    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]

    return obj


def _print_output(payload: Dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        json.dump(_serialize(payload), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    for key, value in payload.items():
        sys.stdout.write(f"{key}: {value}\n")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PDF processing utilities")
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        help="Output format (default: json)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract", help="Extract PDF contents")
    extract_parser.add_argument("pdf_path", help="Path to PDF file")
    extract_parser.add_argument(
        "--strategy",
        choices=["hi_res", "fast", "ocr_only"],
        default=None,
        help="Extraction strategy override",
    )
    extract_parser.add_argument(
        "--no-extract-tables",
        dest="extract_tables",
        action="store_false",
        help="Disable table extraction",
    )
    extract_parser.add_argument(
        "--no-extract-figures",
        dest="extract_figures",
        action="store_false",
        help="Disable figure extraction",
    )
    extract_parser.add_argument(
        "--extract-images",
        dest="extract_images",
        action="store_true",
        help="Extract embedded images",
    )
    extract_parser.add_argument(
        "--languages",
        nargs="+",
        default=None,
        help="Languages for OCR (space separated)",
    )

    validate_parser = subparsers.add_parser("validate", help="Validate PDF file")
    validate_parser.add_argument("pdf_path")

    hash_parser = subparsers.add_parser("hash", help="Generate PDF hashes")
    hash_parser.add_argument("pdf_path")
    hash_parser.add_argument(
        "--no-normalized",
        dest="normalized",
        action="store_false",
        help="Disable normalized content hash",
    )
    hash_parser.add_argument(
        "--per-page",
        dest="per_page",
        action="store_true",
        help="Generate per-page hashes",
    )

    chunk_parser = subparsers.add_parser(
        "chunk", help="Create semantic chunks for a PDF"
    )
    chunk_parser.add_argument("pdf_path")
    chunk_parser.add_argument(
        "--strategy",
        choices=[s.value for s in ChunkingStrategy],
        default=ChunkingStrategy.BY_TITLE.value,
    )
    chunk_parser.add_argument(
        "--max-chars",
        dest="max_characters",
        type=int,
        default=2000,
        help="Maximum characters per chunk",
    )
    chunk_parser.add_argument(
        "--overlap",
        type=int,
        default=200,
        help="Character overlap between chunks",
    )
    chunk_parser.add_argument(
        "--combine-under",
        dest="combine_under_n_chars",
        type=int,
        default=100,
        help="Combine elements smaller than N characters",
    )
    chunk_parser.add_argument(
        "--analyze",
        action="store_true",
        help="Include chunk analysis summary",
    )

    return parser.parse_args(argv)


def _handle_extract(args: argparse.Namespace) -> Dict[str, Any]:
    processor = PDFProcessor()
    result = processor.extract(
        args.pdf_path,
        strategy=args.strategy,
        extract_tables=args.extract_tables,
        extract_figures=args.extract_figures,
        extract_images=args.extract_images,
        languages=args.languages,
    )
    return _serialize(result)


def _handle_validate(args: argparse.Namespace) -> Dict[str, Any]:
    processor = PDFProcessor()
    result = processor.validate(args.pdf_path)
    return _serialize(result)


def _handle_hash(args: argparse.Namespace) -> Dict[str, Any]:
    processor = PDFProcessor()
    result = processor.hash(
        args.pdf_path,
        normalized=args.normalized,
        per_page=args.per_page,
    )
    return _serialize(result)


def _handle_chunk(args: argparse.Namespace) -> Dict[str, Any]:
    processor = PDFProcessor()
    extraction = processor.extract(args.pdf_path, strategy=None)

    manager = ChunkManager()
    strategy = ChunkingStrategy(args.strategy)
    chunk_result = manager.chunk(
        extraction,
        strategy=strategy,
        max_characters=args.max_characters,
        overlap=args.overlap,
        combine_under_n_chars=args.combine_under_n_chars,
    )

    payload: Dict[str, Any] = {
        "chunk_result": _serialize(chunk_result),
    }

    if args.analyze:
        payload["analysis"] = _serialize(manager.analyze(chunk_result))

    return payload


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv or sys.argv[1:])

    if args.command == "extract":
        payload = _handle_extract(args)
    elif args.command == "validate":
        payload = _handle_validate(args)
    elif args.command == "hash":
        payload = _handle_hash(args)
    elif args.command == "chunk":
        payload = _handle_chunk(args)
    else:
        raise SystemExit(f"Unknown command: {args.command}")

    if not isinstance(payload, dict):
        payload = {"result": payload}

    _print_output(payload, args.format)


if __name__ == "__main__":
    main()
