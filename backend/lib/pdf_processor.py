"""
PDF Processor Library
Handles PDF extraction, validation, and hashing using PyMuPDF
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
import logging

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# Configuration
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "100"))


# ==================== Exceptions ====================


class PDFProcessorError(Exception):
    """Base exception for PDF processor errors"""

    pass


class UnsupportedFileError(PDFProcessorError):
    """Raised when file is not a valid PDF"""

    pass


class CorruptedPDFError(PDFProcessorError):
    """Raised when PDF is corrupted or malformed"""

    pass


# ==================== Data Models ====================


@dataclass
class PageContent:
    """Represents content from a single PDF page"""

    page_number: int
    text: str
    layout_blocks: Optional[List[Dict[str, Any]]] = None
    bbox: Optional[Dict[str, float]] = None


@dataclass
class ExtractedTable:
    """Represents an extracted table from PDF"""

    page_number: int
    table_index: int
    data: List[List[str]]
    headers: Optional[List[str]] = None
    bbox: Optional[Dict[str, float]] = None
    caption: Optional[str] = None


@dataclass
class ExtractedFigure:
    """Represents an extracted figure from PDF"""

    page_number: int
    figure_index: int
    figure_type: Optional[str] = None  # CHART, DIAGRAM, IMAGE, PLOT
    caption: Optional[str] = None
    bbox: Optional[Dict[str, float]] = None
    image_data: Optional[bytes] = None


@dataclass
class ExtractionResult:
    """Complete result from PDF extraction"""

    pdf_path: str
    page_count: int
    pages: List[PageContent]
    full_text: str
    extraction_time_ms: float
    file_size_bytes: int
    metadata: Dict[str, Any] = field(default_factory=dict)
    tables: List[ExtractedTable] = field(default_factory=list)
    figures: List[ExtractedFigure] = field(default_factory=list)

    @property
    def table_count(self) -> int:
        return len(self.tables)

    @property
    def figure_count(self) -> int:
        return len(self.figures)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict(), default=str)


@dataclass
class PDFValidationResult:
    """Result from PDF validation"""

    is_valid: bool
    page_count: int
    has_text: bool
    has_images: bool
    file_size_bytes: int
    is_encrypted: bool = False
    is_corrupted: bool = False
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class PDFHashResult:
    """Result from PDF hashing"""

    file_hash: str
    content_hash: str
    content_hash_normalized: Optional[str] = None
    page_hashes: Optional[List[str]] = None
    page_count: int = 0
    is_normalized: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ==================== Main Processor Class ====================


class PDFProcessor:
    """Main PDF processor using PyMuPDF"""

    def __init__(self):
        """Initialize PDF processor"""
        self.logger = logger

    def extract(
        self,
        pdf_path: str,
        extract_tables: bool = False,
        extract_figures: bool = False,
        preserve_layout: bool = False,
        start_page: Optional[int] = None,
        end_page: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> ExtractionResult:
        """
        Extract content from PDF

        Args:
            pdf_path: Path to PDF file
            extract_tables: Whether to extract tables
            extract_figures: Whether to extract figures
            preserve_layout: Whether to preserve layout information
            start_page: Starting page (1-indexed, inclusive)
            end_page: Ending page (1-indexed, inclusive)
            progress_callback: Callback for progress updates

        Returns:
            ExtractionResult with all extracted content

        Raises:
            FileNotFoundError: If PDF file doesn't exist
            UnsupportedFileError: If file is not a valid PDF
            CorruptedPDFError: If PDF is corrupted
            PDFProcessorError: For other extraction errors
        """
        start_time = time.time()

        # Validate file exists
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        # Check file size
        file_size = os.path.getsize(pdf_path)
        if file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
            raise PDFProcessorError(
                f"File too large: {file_size / 1024 / 1024:.1f}MB > {MAX_FILE_SIZE_MB}MB limit"
            )

        # Check if it's a PDF
        if not pdf_path.lower().endswith(".pdf"):
            with open(pdf_path, "rb") as f:
                header = f.read(5)
                if header != b"%PDF-":
                    raise UnsupportedFileError(f"File is not a valid PDF: {pdf_path}")

        try:
            # Open PDF with PyMuPDF
            doc = fitz.open(pdf_path)

            # Check if encrypted
            if doc.is_encrypted:
                doc.close()
                raise PDFProcessorError("PDF is encrypted/password-protected")

            # Extract metadata
            metadata = self._extract_metadata(doc)

            # Determine page range
            total_pages = len(doc)
            start_idx = (start_page - 1) if start_page else 0
            end_idx = end_page if end_page else total_pages
            end_idx = min(end_idx, total_pages)

            # Extract pages
            pages = []
            full_text_parts = []
            tables = []
            figures = []

            for idx in range(start_idx, end_idx):
                if progress_callback:
                    progress_callback(idx + 1, total_pages, "extracting")

                page = doc[idx]
                page_num = idx + 1

                # Extract text
                text = page.get_text()
                full_text_parts.append(text)

                # Extract layout blocks if requested
                layout_blocks = None
                if preserve_layout:
                    layout_blocks = self._extract_layout_blocks(page)

                # Create page content
                page_content = PageContent(
                    page_number=page_num, text=text, layout_blocks=layout_blocks
                )
                pages.append(page_content)

                # Extract tables if requested
                if extract_tables:
                    page_tables = self._extract_tables_from_page(page, page_num)
                    tables.extend(page_tables)

                # Extract figures if requested
                if extract_figures:
                    page_figures = self._extract_figures_from_page(page, page_num)
                    figures.extend(page_figures)

            doc.close()

            if progress_callback:
                progress_callback(total_pages, total_pages, "completed")

            # Calculate extraction time
            extraction_time_ms = (time.time() - start_time) * 1000

            return ExtractionResult(
                pdf_path=pdf_path,
                page_count=len(pages),
                pages=pages,
                full_text="".join(full_text_parts),
                extraction_time_ms=extraction_time_ms,
                file_size_bytes=file_size,
                metadata=metadata,
                tables=tables,
                figures=figures,
            )

        except fitz.FileDataError as e:
            # Check if it's an empty or minimal PDF
            if "no objects found" in str(e).lower():
                # Handle empty PDF gracefully
                return ExtractionResult(
                    pdf_path=pdf_path,
                    page_count=0,
                    pages=[],
                    full_text="",
                    extraction_time_ms=(time.time() - start_time) * 1000,
                    file_size_bytes=file_size,
                    metadata=self._get_empty_metadata(),
                    tables=[],
                    figures=[],
                )
            raise UnsupportedFileError(f"Not a valid PDF: {pdf_path}")
        except Exception as e:
            if isinstance(
                e, (PDFProcessorError, UnsupportedFileError, CorruptedPDFError)
            ):
                raise
            raise PDFProcessorError(f"Failed to extract PDF: {str(e)}")

    def validate(
        self,
        pdf_path: str,
        check_corruption: bool = False,
        check_encryption: bool = False,
        format: str = "dict",
    ) -> Any:
        """
        Validate PDF structure and properties

        Args:
            pdf_path: Path to PDF file
            check_corruption: Whether to check for corruption
            check_encryption: Whether to check for encryption
            format: Output format ("dict" or "json")

        Returns:
            PDFValidationResult or JSON string
        """
        if not os.path.exists(pdf_path):
            result = PDFValidationResult(
                is_valid=False,
                page_count=0,
                has_text=False,
                has_images=False,
                file_size_bytes=0,
                is_corrupted=True,
                issues=["File not found"],
            )
        else:
            file_size = os.path.getsize(pdf_path)
            issues = []

            try:
                doc = fitz.open(pdf_path)

                is_encrypted = doc.is_encrypted if check_encryption else False
                if is_encrypted:
                    issues.append("PDF is encrypted")

                page_count = len(doc)
                has_text = False
                has_images = False

                # Check first few pages for content
                for i in range(min(3, page_count)):
                    page = doc[i]
                    if page.get_text().strip():
                        has_text = True
                    if page.get_images():
                        has_images = True
                    if has_text and has_images:
                        break

                doc.close()

                result = PDFValidationResult(
                    is_valid=not is_encrypted and page_count > 0,
                    page_count=page_count,
                    has_text=has_text,
                    has_images=has_images,
                    file_size_bytes=file_size,
                    is_encrypted=is_encrypted,
                    is_corrupted=False,
                    issues=issues,
                )

            except Exception as e:
                result = PDFValidationResult(
                    is_valid=False,
                    page_count=0,
                    has_text=False,
                    has_images=False,
                    file_size_bytes=file_size,
                    is_corrupted=True,
                    issues=[f"Validation error: {str(e)}"],
                )

        if format == "json":
            return result.to_json()
        return result

    def hash(
        self, pdf_path: str, normalized: bool = False, per_page: bool = False
    ) -> PDFHashResult:
        """
        Generate hashes for PDF content

        Args:
            pdf_path: Path to PDF file
            normalized: Whether to generate normalized content hash
            per_page: Whether to generate per-page hashes

        Returns:
            PDFHashResult with various hashes
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        # File hash (MD5 of raw file)
        with open(pdf_path, "rb") as f:
            file_hash = hashlib.md5(f.read()).hexdigest()

        try:
            doc = fitz.open(pdf_path)

            # Extract all text for content hash
            full_text = ""
            page_texts = []

            for page in doc:
                text = page.get_text()
                page_texts.append(text)
                full_text += text

            doc.close()

            # Content hash (MD5 of extracted text)
            content_hash = hashlib.md5(full_text.encode("utf-8")).hexdigest()

            # Normalized content hash (remove whitespace variations)
            content_hash_normalized = None
            if normalized:
                normalized_text = " ".join(full_text.split())
                normalized_text = normalized_text.lower()
                content_hash_normalized = hashlib.md5(
                    normalized_text.encode("utf-8")
                ).hexdigest()

            # Per-page hashes
            page_hashes_list = None
            if per_page:
                page_hashes_list = []
                for text in page_texts:
                    page_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
                    page_hashes_list.append(page_hash)

            return PDFHashResult(
                file_hash=file_hash,
                content_hash=content_hash,
                content_hash_normalized=content_hash_normalized,
                page_hashes=page_hashes_list,
                page_count=len(page_texts),
                is_normalized=normalized,
            )

        except Exception as e:
            raise PDFProcessorError(f"Failed to hash PDF: {str(e)}")

    # ==================== Helper Methods ====================

    def _extract_metadata(self, doc: fitz.Document) -> Dict[str, Any]:
        """Extract metadata from PDF document"""
        metadata = doc.metadata or {}

        # Ensure all expected keys exist
        expected_keys = [
            "title",
            "author",
            "subject",
            "keywords",
            "creator",
            "producer",
            "creation_date",
            "modification_date",
        ]

        result = {}
        for key in expected_keys:
            # PyMuPDF uses different key names
            pymupdf_key = key.replace("_", "").replace("date", "Date")
            result[key] = metadata.get(pymupdf_key, None)

        return result

    def _get_empty_metadata(self) -> Dict[str, Any]:
        """Get empty metadata structure for empty PDFs."""
        return {
            "title": "",
            "author": "",
            "subject": "",
            "keywords": "",
            "creator": "",
            "producer": "",
            "creation_date": "",
            "modification_date": None,
        }

    def _extract_layout_blocks(self, page: fitz.Page) -> List[Dict[str, Any]]:
        """Extract layout blocks from a page"""
        blocks = []

        # Get text blocks with bbox
        text_blocks = page.get_text("dict")

        for block in text_blocks.get("blocks", []):
            if block.get("type") == 0:  # Text block
                bbox = block.get("bbox", [0, 0, 0, 0])

                # Determine block type based on content and position
                block_text = ""
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        block_text += span.get("text", "")

                block_type = self._classify_block_type(block_text, bbox)

                blocks.append(
                    {
                        "type": block_type,
                        "text": block_text.strip(),
                        "bbox": {
                            "x1": bbox[0],
                            "y1": bbox[1],
                            "x2": bbox[2],
                            "y2": bbox[3],
                        },
                    }
                )

        return blocks

    def _classify_block_type(self, text: str, bbox: List[float]) -> str:
        """Classify the type of a text block"""
        text_lower = text.lower().strip()

        # Check for common patterns
        if text_lower.startswith("table ") or text_lower.startswith("tab."):
            return "table_caption"
        elif text_lower.startswith("figure ") or text_lower.startswith("fig."):
            return "figure_caption"
        elif len(text) < 100 and text.isupper():
            return "header"
        elif len(text) < 50 and any(
            text_lower.startswith(w)
            for w in [
                "abstract",
                "introduction",
                "methods",
                "results",
                "discussion",
                "conclusion",
                "references",
            ]
        ):
            return "header"
        else:
            return "paragraph"

    def _extract_tables_from_page(
        self, page: fitz.Page, page_num: int
    ) -> List[ExtractedTable]:
        """Extract tables from a page"""
        tables = []

        # PyMuPDF doesn't have built-in table extraction
        # This is a simplified approach - in production, consider using
        # libraries like camelot-py or tabula-py for better table extraction

        # For now, we'll detect table-like structures in the text
        text = page.get_text()
        lines = text.split("\n")

        # Simple heuristic: consecutive lines with multiple tab/space separations
        # might be table rows
        potential_table_lines = []
        for line in lines:
            if "\t" in line or "  " in line:
                potential_table_lines.append(line)

        # If we found potential table content, create a table
        if potential_table_lines:
            # This is very simplified - real implementation would need
            # better table detection and parsing
            data = []
            for line in potential_table_lines[:10]:  # Limit to 10 rows for now
                cells = line.split("\t") if "\t" in line else line.split("  ")
                cells = [c.strip() for c in cells if c.strip()]
                if cells:
                    data.append(cells)

            if data:
                table = ExtractedTable(
                    page_number=page_num,
                    table_index=0,
                    data=data,
                    headers=data[0] if data else None,
                    bbox={"x1": 0, "y1": 0, "x2": 100, "y2": 100},  # Placeholder
                )
                tables.append(table)

        return tables

    def _extract_figures_from_page(
        self, page: fitz.Page, page_num: int
    ) -> List[ExtractedFigure]:
        """Extract figures from a page"""
        figures = []

        # Get images from page
        image_list = page.get_images()

        for idx, img in enumerate(image_list):
            # Get image bbox (if available)
            # PyMuPDF stores images differently, this is simplified
            figure = ExtractedFigure(
                page_number=page_num,
                figure_index=idx,
                figure_type="IMAGE",  # Default type
                bbox={"x1": 0, "y1": 0, "x2": 100, "y2": 100},  # Placeholder
            )
            figures.append(figure)

        return figures


# ==================== CLI Interface ====================


class cli:
    """CLI interface for pdf_processor"""

    @staticmethod
    def extract(*args, **kwargs):
        """CLI extract command"""
        processor = PDFProcessor()
        return processor.extract(*args, **kwargs)

    @staticmethod
    def validate(*args, **kwargs):
        """CLI validate command"""
        processor = PDFProcessor()
        return processor.validate(*args, **kwargs)

    @staticmethod
    def hash(*args, **kwargs):
        """CLI hash command"""
        processor = PDFProcessor()
        return processor.hash(*args, **kwargs)


# ==================== Module Entry Point ====================

if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="PDF Processor CLI")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Extract command
    extract_parser = subparsers.add_parser("extract", help="Extract content from PDF")
    extract_parser.add_argument("pdf_path", help="Path to PDF file")
    extract_parser.add_argument("--tables", action="store_true", help="Extract tables")
    extract_parser.add_argument(
        "--figures", action="store_true", help="Extract figures"
    )
    extract_parser.add_argument("--layout", action="store_true", help="Preserve layout")

    # Validate command
    validate_parser = subparsers.add_parser("validate", help="Validate PDF")
    validate_parser.add_argument("pdf_path", help="Path to PDF file")
    validate_parser.add_argument("--format", choices=["dict", "json"], default="json")

    # Hash command
    hash_parser = subparsers.add_parser("hash", help="Generate PDF hashes")
    hash_parser.add_argument("pdf_path", help="Path to PDF file")
    hash_parser.add_argument(
        "--normalized", action="store_true", help="Generate normalized hash"
    )
    hash_parser.add_argument(
        "--per-page", action="store_true", help="Generate per-page hashes"
    )

    args = parser.parse_args()

    if args.command == "extract":
        processor = PDFProcessor()
        result = processor.extract(
            args.pdf_path,
            extract_tables=args.tables,
            extract_figures=args.figures,
            preserve_layout=args.layout,
        )
        print(result.to_json())

    elif args.command == "validate":
        processor = PDFProcessor()
        result = processor.validate(args.pdf_path, format=args.format)
        print(result)

    elif args.command == "hash":
        processor = PDFProcessor()
        result = processor.hash(
            args.pdf_path, normalized=args.normalized, per_page=args.per_page
        )
        print(json.dumps(result.to_dict(), indent=2))

    else:
        parser.print_help()
