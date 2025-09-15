#!/usr/bin/env python
"""
CLI interface for PDF Processor
"""

import argparse
import json
import sys
from pathlib import Path

from pdf_processor import PDFProcessor, PDFProcessorError


def extract_command(args):
    """Handle extract command"""
    processor = PDFProcessor()

    try:
        result = processor.extract(
            pdf_path=args.pdf,
            extract_tables=args.tables,
            extract_figures=args.figures,
            preserve_layout=args.layout,
            start_page=args.start_page,
            end_page=args.end_page,
        )

        if args.output:
            # Save to file
            output_data = result.to_dict()

            if args.format == "json":
                with open(args.output, "w") as f:
                    json.dump(output_data, f, indent=2)
                print(f"✓ Extraction saved to {args.output}")
            elif args.format == "text":
                with open(args.output, "w") as f:
                    f.write(result.full_text)
                print(f"✓ Text saved to {args.output}")
        else:
            # Print to stdout
            if args.format == "json":
                print(json.dumps(result.to_dict(), indent=2))
            else:
                print(f"PDF: {result.pdf_path}")
                print(f"Pages: {result.page_count}")
                print(f"Characters: {len(result.full_text)}")
                print(f"Tables: {result.table_count}")
                print(f"Figures: {result.figure_count}")
                print(f"Extraction time: {result.extraction_time_ms:.2f}ms")

                if args.verbose:
                    print("\nMetadata:")
                    for key, value in result.metadata.items():
                        if value:
                            print(f"  {key}: {value}")

                    print(f"\nFirst 500 characters:")
                    print(result.full_text[:500])

    except PDFProcessorError as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"✗ Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


def validate_command(args):
    """Handle validate command"""
    processor = PDFProcessor()

    try:
        result = processor.validate(
            pdf_path=args.pdf,
            check_corruption=args.check_corruption,
            check_encryption=args.check_encryption,
            format="dict",
        )

        if args.format == "json":
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(f"Valid: {result.is_valid}")
            print(f"Pages: {result.page_count}")
            print(f"Size: {result.file_size_bytes:,} bytes")
            print(f"Has text: {result.has_text}")
            print(f"Has images: {result.has_images}")

            if args.check_encryption:
                print(f"Encrypted: {result.is_encrypted}")
            if args.check_corruption:
                print(f"Corrupted: {result.is_corrupted}")

            if result.issues:
                print("\nIssues:")
                for issue in result.issues:
                    print(f"  - {issue}")

    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        sys.exit(1)


def hash_command(args):
    """Handle hash command"""
    processor = PDFProcessor()

    try:
        result = processor.hash(
            pdf_path=args.pdf, normalized=args.normalized, per_page=args.per_page
        )

        if args.format == "json":
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(f"File hash: {result.file_hash}")
            print(f"Content hash: {result.content_hash}")

            if result.content_hash_normalized:
                print(f"Normalized hash: {result.content_hash_normalized}")

            if result.page_hashes:
                print(f"\nPage hashes ({len(result.page_hashes)} pages):")
                for i, hash_val in enumerate(result.page_hashes[:5], 1):
                    print(f"  Page {i}: {hash_val}")
                if len(result.page_hashes) > 5:
                    print(f"  ... and {len(result.page_hashes) - 5} more")

    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="PDF Processor CLI - Extract, validate, and hash PDF documents"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Extract command
    extract_parser = subparsers.add_parser("extract", help="Extract content from PDF")
    extract_parser.add_argument("pdf", help="Path to PDF file")
    extract_parser.add_argument("-o", "--output", help="Output file path")
    extract_parser.add_argument(
        "-f",
        "--format",
        choices=["json", "text", "summary"],
        default="summary",
        help="Output format",
    )
    extract_parser.add_argument("--tables", action="store_true", help="Extract tables")
    extract_parser.add_argument(
        "--figures", action="store_true", help="Extract figures"
    )
    extract_parser.add_argument("--layout", action="store_true", help="Preserve layout")
    extract_parser.add_argument("--start-page", type=int, help="Start page (1-based)")
    extract_parser.add_argument("--end-page", type=int, help="End page (inclusive)")
    extract_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose output"
    )

    # Validate command
    validate_parser = subparsers.add_parser("validate", help="Validate PDF structure")
    validate_parser.add_argument("pdf", help="Path to PDF file")
    validate_parser.add_argument(
        "-f",
        "--format",
        choices=["json", "summary"],
        default="summary",
        help="Output format",
    )
    validate_parser.add_argument(
        "--check-corruption", action="store_true", help="Check for corruption"
    )
    validate_parser.add_argument(
        "--check-encryption", action="store_true", help="Check for encryption"
    )

    # Hash command
    hash_parser = subparsers.add_parser("hash", help="Generate PDF hashes")
    hash_parser.add_argument("pdf", help="Path to PDF file")
    hash_parser.add_argument(
        "-f",
        "--format",
        choices=["json", "summary"],
        default="summary",
        help="Output format",
    )
    hash_parser.add_argument(
        "--normalized", action="store_true", help="Generate normalized content hash"
    )
    hash_parser.add_argument(
        "--per-page", action="store_true", help="Generate per-page hashes"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Check if PDF file exists
    if not Path(args.pdf).exists():
        print(f"✗ Error: PDF file not found: {args.pdf}", file=sys.stderr)
        sys.exit(1)

    # Execute command
    if args.command == "extract":
        extract_command(args)
    elif args.command == "validate":
        validate_command(args)
    elif args.command == "hash":
        hash_command(args)


if __name__ == "__main__":
    main()
