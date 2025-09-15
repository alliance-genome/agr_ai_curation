#!/usr/bin/env python
"""
CLI interface for Chunk Manager
"""

import argparse
import json
import sys
from pathlib import Path

from pdf_processor import PDFProcessor
from chunk_manager import ChunkManager, ChunkingStrategy, ChunkManagerError


def chunk_command(args):
    """Handle chunk command"""
    # First extract the PDF
    processor = PDFProcessor()
    manager = ChunkManager()

    try:
        print(f"📄 Extracting PDF: {args.pdf}")
        extraction_result = processor.extract(
            pdf_path=args.pdf, preserve_layout=args.preserve_layout
        )

        print(
            f"  ✓ Extracted {extraction_result.page_count} pages, {len(extraction_result.full_text):,} characters"
        )

        # Determine strategy
        strategy_map = {
            "semantic": ChunkingStrategy.SEMANTIC,
            "fixed": ChunkingStrategy.FIXED_SIZE,
            "sentence": ChunkingStrategy.SENTENCE_BASED,
            "paragraph": ChunkingStrategy.PARAGRAPH_BASED,
        }
        strategy = strategy_map[args.strategy]

        print(f"🔄 Chunking with {args.strategy} strategy...")
        chunk_result = manager.chunk(
            extraction_result=extraction_result,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
            strategy=strategy,
            preserve_layout=args.preserve_layout,
            mark_references=args.mark_references,
            group_captions=args.group_captions,
            semantic_boundaries=args.semantic_boundaries,
        )

        print(f"  ✓ Created {chunk_result.total_chunks} chunks")

        if args.output:
            # Save to file
            output_data = chunk_result.to_dict()

            with open(args.output, "w") as f:
                json.dump(output_data, f, indent=2)
            print(f"💾 Chunks saved to {args.output}")
        else:
            # Print summary
            if args.format == "json":
                print(json.dumps(chunk_result.to_dict(), indent=2))
            else:
                print(f"\nChunk Summary:")
                print(f"  Total chunks: {chunk_result.total_chunks}")
                print(f"  Strategy: {chunk_result.chunking_strategy.value}")
                print(f"  Chunk size: {chunk_result.chunk_size} tokens")
                print(f"  Overlap: {chunk_result.overlap} tokens")
                print(f"  Processing time: {chunk_result.processing_time_ms:.2f}ms")

                if args.verbose:
                    # Show section distribution
                    sections = {}
                    ref_count = 0
                    caption_count = 0

                    for chunk in chunk_result.chunks:
                        section = chunk.section_path or "Unknown"
                        sections[section] = sections.get(section, 0) + 1
                        if chunk.is_reference:
                            ref_count += 1
                        if chunk.is_caption or chunk.contains_caption:
                            caption_count += 1

                    print("\n  Section Distribution:")
                    for section, count in sorted(sections.items()):
                        print(f"    {section}: {count} chunks")

                    if args.mark_references:
                        print(f"\n  Reference chunks: {ref_count}")
                    if args.group_captions:
                        print(f"  Caption chunks: {caption_count}")

                    # Show sample chunks
                    print("\n  Sample Chunks:")
                    for i in range(min(3, len(chunk_result.chunks))):
                        chunk = chunk_result.chunks[i]
                        preview = chunk.text[:100].replace("\n", " ")
                        if len(chunk.text) > 100:
                            preview += "..."
                        print(f"    Chunk {i+1}: {preview}")

    except ChunkManagerError as e:
        print(f"✗ Chunking error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"✗ Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


def analyze_command(args):
    """Handle analyze command"""
    manager = ChunkManager()

    try:
        # Load chunks from file
        with open(args.chunks_file, "r") as f:
            chunk_data = json.load(f)

        # Reconstruct ChunkResult
        from chunk_manager import ChunkResult, Chunk, ChunkingStrategy

        chunks = []
        for chunk_dict in chunk_data["chunks"]:
            chunk = Chunk(
                chunk_index=chunk_dict["chunk_index"],
                text=chunk_dict["text"],
                page_start=chunk_dict["page_start"],
                page_end=chunk_dict["page_end"],
                char_start=chunk_dict["char_start"],
                char_end=chunk_dict["char_end"],
                token_count=chunk_dict["token_count"],
                chunk_hash=chunk_dict["chunk_hash"],
                pdf_id=chunk_dict.get("pdf_id"),
                section_path=chunk_dict.get("section_path"),
                layout_blocks=chunk_dict.get("layout_blocks"),
                is_reference=chunk_dict.get("is_reference", False),
                is_caption=chunk_dict.get("is_caption", False),
                contains_caption=chunk_dict.get("contains_caption", False),
                is_header=chunk_dict.get("is_header", False),
            )
            chunks.append(chunk)

        chunk_result = ChunkResult(
            chunks=chunks,
            total_chunks=chunk_data["total_chunks"],
            chunking_strategy=ChunkingStrategy[chunk_data["chunking_strategy"]],
            chunk_size=chunk_data["chunk_size"],
            overlap=chunk_data["overlap"],
            processing_time_ms=chunk_data["processing_time_ms"],
        )

        # Analyze
        analysis = manager.analyze(
            chunk_result=chunk_result,
            show_boundaries=args.show_boundaries,
            token_counts=args.token_counts,
        )

        if args.format == "json":
            print(json.dumps(analysis, indent=2))
        else:
            print("Chunk Analysis:")
            print(f"  Total chunks: {analysis['total_chunks']}")
            print(f"  Strategy: {analysis['chunking_strategy']}")
            print(f"  Average size: {analysis['avg_chunk_size']:.1f} tokens")
            print(f"  Min size: {analysis['min_chunk_size']} tokens")
            print(f"  Max size: {analysis['max_chunk_size']} tokens")

            if "token_distribution" in analysis:
                dist = analysis["token_distribution"]
                print("\n  Token Distribution:")
                print(f"    Mean: {dist['mean']:.1f}")
                print(f"    Median: {dist['median']:.1f}")
                print(f"    Std Dev: {dist['std_dev']:.1f}")
                print(f"    25th percentile: {dist['percentiles']['25']:.1f}")
                print(f"    75th percentile: {dist['percentiles']['75']:.1f}")

            if "chunk_boundaries" in analysis:
                print("\n  Chunk Boundaries (first 5):")
                for boundary in analysis["chunk_boundaries"][:5]:
                    print(
                        f"    Chunk {boundary['chunk_index']}: {boundary['start_text']}"
                    )

    except FileNotFoundError:
        print(f"✗ Error: Chunks file not found: {args.chunks_file}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"✗ Error analyzing chunks: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Chunk Manager CLI - Semantically chunk PDF documents"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Chunk command
    chunk_parser = subparsers.add_parser("chunk", help="Chunk a PDF document")
    chunk_parser.add_argument("pdf", help="Path to PDF file")
    chunk_parser.add_argument("-o", "--output", help="Output JSON file path")
    chunk_parser.add_argument(
        "-f",
        "--format",
        choices=["json", "summary"],
        default="summary",
        help="Output format",
    )
    chunk_parser.add_argument(
        "--chunk-size",
        type=int,
        default=512,
        help="Target chunk size in tokens (default: 512)",
    )
    chunk_parser.add_argument(
        "--overlap",
        type=int,
        default=50,
        help="Overlap between chunks in tokens (default: 50)",
    )
    chunk_parser.add_argument(
        "--strategy",
        choices=["semantic", "fixed", "sentence", "paragraph"],
        default="semantic",
        help="Chunking strategy (default: semantic)",
    )
    chunk_parser.add_argument(
        "--preserve-layout", action="store_true", help="Preserve layout information"
    )
    chunk_parser.add_argument(
        "--mark-references", action="store_true", help="Mark reference sections"
    )
    chunk_parser.add_argument(
        "--group-captions", action="store_true", help="Group captions with content"
    )
    chunk_parser.add_argument(
        "--semantic-boundaries", action="store_true", help="Respect semantic boundaries"
    )
    chunk_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose output"
    )

    # Analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze existing chunks")
    analyze_parser.add_argument("chunks_file", help="Path to chunks JSON file")
    analyze_parser.add_argument(
        "-f",
        "--format",
        choices=["json", "summary"],
        default="summary",
        help="Output format",
    )
    analyze_parser.add_argument(
        "--show-boundaries", action="store_true", help="Show chunk boundaries"
    )
    analyze_parser.add_argument(
        "--token-counts", action="store_true", help="Analyze token distribution"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Execute command
    if args.command == "chunk":
        if not Path(args.pdf).exists():
            print(f"✗ Error: PDF file not found: {args.pdf}", file=sys.stderr)
            sys.exit(1)
        chunk_command(args)
    elif args.command == "analyze":
        analyze_command(args)


if __name__ == "__main__":
    main()
