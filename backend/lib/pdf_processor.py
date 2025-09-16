"""
PDF Processor using Unstructured.io
Provides superior extraction with automatic de-hyphenation, element classification,
layout understanding, and built-in table/figure handling.
"""

import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from unstructured.cleaners.core import clean
from unstructured.documents.elements import Element
from unstructured.partition.pdf import partition_pdf


@dataclass
class UnstructuredElement:
    """Wrapper for Unstructured element with enhanced metadata"""

    type: str  # Title, NarrativeText, Table, FigureCaption, etc.
    text: str
    metadata: Dict[str, Any]
    element_id: str
    page_number: Optional[int] = None
    bbox: Optional[Dict[str, float]] = None
    parent_id: Optional[str] = None
    section_path: Optional[str] = None

    @classmethod
    def from_unstructured(cls, element: Element) -> "UnstructuredElement":
        """Create from Unstructured element"""
        metadata = (
            element.metadata.to_dict() if hasattr(element.metadata, "to_dict") else {}
        )

        return cls(
            type=element.category,
            text=clean(element.text),  # Removes artifacts
            metadata=metadata,
            element_id=str(element.id) if hasattr(element, "id") else None,
            page_number=metadata.get("page_number"),
            bbox=metadata.get("coordinates"),
            parent_id=metadata.get("parent_id"),
            section_path=metadata.get("section"),
        )


@dataclass
class ExtractionResult:
    """Updated extraction result with Unstructured elements"""

    pdf_path: str
    elements: List[UnstructuredElement]
    page_count: int
    full_text: str
    metadata: Dict[str, Any]
    tables: List[Dict[str, Any]]
    figures: List[Dict[str, Any]]
    extraction_time_ms: float
    file_size_bytes: int
    processing_strategy: str  # "hi_res", "fast", "ocr_only"
    content_hash: Optional[str] = None
    content_hash_normalized: Optional[str] = None
    page_hashes: List[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    """PDF validation result"""

    is_valid: bool
    page_count: int
    has_text: bool
    file_size_bytes: int
    is_encrypted: bool
    is_corrupted: bool
    is_scanned: bool  # New field for OCR detection
    error_message: Optional[str] = None


@dataclass
class HashResult:
    """PDF hashing result"""

    file_hash: str
    content_hash: str
    content_hash_normalized: str
    page_hashes: List[str]
    page_count: int


class PDFProcessor:
    """
    Enhanced PDF processor using Unstructured.io
    Handles extraction, validation, and hashing with superior quality
    """

    def __init__(self, default_strategy: str = "hi_res"):
        """
        Initialize PDF processor

        Args:
            default_strategy: Default extraction strategy ("hi_res", "fast", "ocr_only")
        """
        self.default_strategy = default_strategy

    def extract(
        self,
        pdf_path: str,
        strategy: Optional[str] = None,
        extract_tables: bool = True,
        extract_figures: bool = True,
        extract_images: bool = False,
        languages: List[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        **kwargs,
    ) -> ExtractionResult:
        """
        Extract content from PDF using Unstructured

        Args:
            pdf_path: Path to PDF file
            strategy: Extraction strategy ("hi_res", "fast", "ocr_only")
            extract_tables: Extract table structure
            extract_figures: Extract figure information
            extract_images: Extract actual images from PDF
            languages: Languages for OCR (default: ["eng"])
            progress_callback: Optional callback for progress updates
            **kwargs: Additional arguments for partition_pdf

        Returns:
            ExtractionResult with structured elements
        """
        start_time = time.time()
        pdf_path = Path(pdf_path)

        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        file_size = pdf_path.stat().st_size
        strategy = strategy or self.default_strategy
        languages = languages or ["eng"]

        # Progress callback
        if progress_callback:
            progress_callback(0, 100, "Starting PDF extraction...")

        try:
            # Partition PDF into elements
            elements = partition_pdf(
                filename=str(pdf_path),
                strategy=strategy,  # hi_res uses layout detection models
                infer_table_structure=extract_tables,
                include_page_breaks=True,
                extract_images_in_pdf=extract_images,
                extract_forms=False,
                languages=languages,
                **kwargs,
            )

            if progress_callback:
                progress_callback(50, 100, "Processing extracted elements...")

            # Convert to our format
            extracted_elements = []
            tables = []
            table_refs: List[Tuple[UnstructuredElement, Dict[str, Any]]] = []
            page_numbers = set()

            for i, element in enumerate(elements):
                elem = UnstructuredElement.from_unstructured(element)
                extracted_elements.append(elem)

                if elem.page_number:
                    page_numbers.add(elem.page_number)

                # Collect tables
                if extract_tables and elem.type == "Table":
                    table_data = {
                        "text": elem.text,
                        "page": elem.page_number,
                        "bbox": elem.bbox,
                        "element_id": elem.element_id,
                    }

                    # Check for HTML representation
                    if "text_as_html" in elem.metadata:
                        table_data["html"] = elem.metadata["text_as_html"]

                    tables.append(table_data)
                    table_refs.append((elem, table_data))

            # Generate full text in reading order
            full_text = "\n\n".join([e.text for e in extracted_elements if e.text])

            # Extract document metadata
            metadata = self._extract_document_metadata(elements)

            # Calculate page count
            page_count = max(page_numbers) if page_numbers else 0

            # Optionally enrich tables and figures
            if not extract_tables:
                tables = []
            else:
                for source_elem, table in table_refs:
                    caption = self._find_caption_for_element(
                        source_elem, extracted_elements, "Table"
                    )
                    table["caption"] = caption
                for table in tables:
                    table.setdefault("caption", None)

            figures = (
                self.extract_figures(extracted_elements)
                if extract_figures
                else []
            )

            # Generate hashes
            content_hash = hashlib.md5(full_text.encode()).hexdigest()
            normalized_text = self._normalize_text(full_text)
            content_hash_normalized = hashlib.md5(normalized_text.encode()).hexdigest()

            # Generate per-page hashes
            page_hashes = self._generate_page_hashes(extracted_elements)

            if progress_callback:
                progress_callback(100, 100, "Extraction complete")

            extraction_time = (time.time() - start_time) * 1000

            return ExtractionResult(
                pdf_path=str(pdf_path),
                elements=extracted_elements,
                page_count=page_count,
                full_text=full_text,
                metadata=metadata,
                tables=tables,
                figures=figures,
                extraction_time_ms=extraction_time,
                file_size_bytes=file_size,
                processing_strategy=strategy,
                content_hash=content_hash,
                content_hash_normalized=content_hash_normalized,
                page_hashes=page_hashes,
            )

        except Exception as e:
            raise RuntimeError(f"Failed to extract PDF: {str(e)}") from e

    def validate(
        self,
        pdf_path: str,
        check_corruption: bool = True,
        check_encryption: bool = True,
    ) -> ValidationResult:
        """
        Validate PDF file

        Args:
            pdf_path: Path to PDF file
            check_corruption: Check for file corruption
            check_encryption: Check for encryption

        Returns:
            ValidationResult with validation details
        """
        pdf_path = Path(pdf_path)

        if not pdf_path.exists():
            return ValidationResult(
                is_valid=False,
                page_count=0,
                has_text=False,
                file_size_bytes=0,
                is_encrypted=False,
                is_corrupted=False,
                is_scanned=False,
                error_message="File not found",
            )

        file_size = pdf_path.stat().st_size

        try:
            # Try fast extraction first to validate
            elements = partition_pdf(
                filename=str(pdf_path),
                strategy="fast",
                include_page_breaks=False,
                max_partition=1,  # Only process first page for validation
            )

            # Check if we got any text
            has_text = any(elem.text for elem in elements if hasattr(elem, "text"))

            # Check if it's likely a scanned PDF (no text elements)
            is_scanned = len(elements) == 0 or not has_text

            # Get page count (need to extract more for this)
            if check_corruption:
                full_elements = partition_pdf(
                    filename=str(pdf_path), strategy="fast", include_page_breaks=True
                )
                page_numbers = set()
                for elem in full_elements:
                    if hasattr(elem, "metadata") and hasattr(
                        elem.metadata, "page_number"
                    ):
                        page_numbers.add(elem.metadata.page_number)
                page_count = max(page_numbers) if page_numbers else 1
            else:
                page_count = 1

            return ValidationResult(
                is_valid=True,
                page_count=page_count,
                has_text=has_text,
                file_size_bytes=file_size,
                is_encrypted=False,
                is_corrupted=False,
                is_scanned=is_scanned,
            )

        except Exception as e:
            error_msg = str(e)
            is_encrypted = "encrypted" in error_msg.lower()

            return ValidationResult(
                is_valid=False,
                page_count=0,
                has_text=False,
                file_size_bytes=file_size,
                is_encrypted=is_encrypted,
                is_corrupted=not is_encrypted,
                is_scanned=False,
                error_message=error_msg,
            )

    def hash(
        self, pdf_path: str, normalized: bool = True, per_page: bool = False
    ) -> HashResult:
        """
        Generate hashes for PDF content

        Args:
            pdf_path: Path to PDF file
            normalized: Generate normalized content hash
            per_page: Generate per-page hashes

        Returns:
            HashResult with various hashes
        """
        pdf_path = Path(pdf_path)

        # File hash
        with open(pdf_path, "rb") as f:
            file_hash = hashlib.md5(f.read()).hexdigest()

        # Extract content for content hashing
        result = self.extract(
            pdf_path, strategy="fast", extract_tables=False, extract_figures=False
        )

        # Content hash
        content_hash = hashlib.md5(result.full_text.encode()).hexdigest()

        # Normalized content hash
        if normalized:
            normalized_text = self._normalize_text(result.full_text)
            content_hash_normalized = hashlib.md5(normalized_text.encode()).hexdigest()
        else:
            content_hash_normalized = content_hash

        # Per-page hashes
        page_hashes = []
        if per_page:
            page_hashes = self._generate_page_hashes(result.elements)

        return HashResult(
            file_hash=file_hash,
            content_hash=content_hash,
            content_hash_normalized=content_hash_normalized,
            page_hashes=page_hashes,
            page_count=result.page_count,
        )

    def _extract_document_metadata(self, elements: List[Element]) -> Dict[str, Any]:
        """Extract document-level metadata from elements"""
        metadata = {}

        # Look for title (usually first Title element)
        for elem in elements:
            if hasattr(elem, "category") and elem.category == "Title":
                metadata["title"] = elem.text
                break

        # Extract other metadata from first element if available
        if elements and hasattr(elements[0], "metadata"):
            first_meta = elements[0].metadata
            if hasattr(first_meta, "filename"):
                metadata["filename"] = first_meta.filename
            if hasattr(first_meta, "filetype"):
                metadata["filetype"] = first_meta.filetype

        return metadata

    def _normalize_text(self, text: str) -> str:
        """Normalize text for consistent hashing"""
        # Remove extra whitespace
        text = " ".join(text.split())
        # Convert to lowercase
        text = text.lower()
        # Remove punctuation (optional, depending on needs)
        import string

        text = text.translate(str.maketrans("", "", string.punctuation))
        return text

    def _generate_page_hashes(self, elements: List[UnstructuredElement]) -> List[str]:
        """Generate hashes for each page"""
        page_texts = {}

        for elem in elements:
            if elem.page_number and elem.text:
                if elem.page_number not in page_texts:
                    page_texts[elem.page_number] = []
                page_texts[elem.page_number].append(elem.text)

        # Sort by page number and generate hashes
        page_hashes = []
        for page_num in sorted(page_texts.keys()):
            page_text = "\n".join(page_texts[page_num])
            page_hash = hashlib.md5(page_text.encode()).hexdigest()
            page_hashes.append(page_hash)

        return page_hashes

    def extract_tables_as_dataframes(
        self, elements: List[UnstructuredElement]
    ) -> List[Dict[str, Any]]:
        """
        Extract tables as structured data (DataFrames)

        Args:
            elements: List of extracted elements

        Returns:
            List of table dictionaries with DataFrames
        """
        tables = []

        for element in elements:
            if element.type == "Table":
                table_dict = {
                    "text": element.text,
                    "page": element.page_number,
                    "element_id": element.element_id,
                }

                # Try to parse HTML if available
                if "text_as_html" in element.metadata:
                    try:
                        import pandas as pd

                        df = pd.read_html(element.metadata["text_as_html"])[0]
                        table_dict["dataframe"] = df
                        table_dict["html"] = element.metadata["text_as_html"]
                    except Exception:
                        pass  # Fall back to text representation

                # Look for associated caption
                table_dict["caption"] = self._find_caption_for_element(
                    element, elements, "Table"
                )
                tables.append(table_dict)

        return tables

    def extract_figures(
        self, elements: List[UnstructuredElement]
    ) -> List[Dict[str, Any]]:
        """
        Extract figures with captions

        Args:
            elements: List of extracted elements

        Returns:
            List of figure dictionaries
        """
        figures = []

        for i, element in enumerate(elements):
            if element.type == "FigureCaption":
                figure = {
                    "caption": element.text,
                    "page": element.page_number,
                    "bbox": element.bbox,
                    "element_id": element.element_id,
                    "has_image": False,
                }

                image_element = None

                # Prefer preceding image element, otherwise check following
                if i > 0 and elements[i - 1].type in ["Image", "Figure"]:
                    image_element = elements[i - 1]
                elif i + 1 < len(elements) and elements[i + 1].type in [
                    "Image",
                    "Figure",
                ]:
                    image_element = elements[i + 1]

                if image_element:
                    figure["has_image"] = True
                    figure["image_element_id"] = image_element.element_id
                    if image_element.bbox:
                        figure["bbox"] = image_element.bbox

                figures.append(figure)

        return figures

    def build_document_structure(
        self, elements: List[UnstructuredElement]
    ) -> List[Dict[str, Any]]:
        """
        Build hierarchical document structure

        Args:
            elements: List of extracted elements

        Returns:
            Hierarchical structure of document sections
        """
        structure = []
        current_section = None
        current_subsection = None

        for element in elements:
            if element.type == "Title":
                # Main title/section
                current_section = {
                    "title": element.text,
                    "level": 1,
                    "children": [],
                    "content": [],
                    "element_id": element.element_id,
                    "page": element.page_number,
                }
                structure.append(current_section)
                current_subsection = None

            elif element.type == "Header":
                # Subsection
                subsection = {
                    "title": element.text,
                    "level": 2,
                    "content": [],
                    "element_id": element.element_id,
                    "page": element.page_number,
                }

                if current_section:
                    current_section["children"].append(subsection)
                    current_subsection = subsection
                else:
                    # No parent section, make it a top-level section
                    structure.append(subsection)
                    current_section = subsection

            elif current_subsection:
                current_subsection["content"].append(
                    {
                        "type": element.type,
                        "text": element.text,
                        "element_id": element.element_id,
                    }
                )
            elif current_section:
                current_section["content"].append(
                    {
                        "type": element.type,
                        "text": element.text,
                        "element_id": element.element_id,
                    }
                )

        return structure

    def _find_caption_for_element(
        self,
        element: UnstructuredElement,
        all_elements: List[UnstructuredElement],
        element_type: str,
    ) -> Optional[str]:
        """Find caption for a table or figure element"""
        # Look for caption before or after the element
        element_index = all_elements.index(element)

        # Check previous element
        if element_index > 0:
            prev_elem = all_elements[element_index - 1]
            if prev_elem.type == f"{element_type}Caption":
                return prev_elem.text

        # Check next element
        if element_index < len(all_elements) - 1:
            next_elem = all_elements[element_index + 1]
            if next_elem.type == f"{element_type}Caption":
                return next_elem.text

        return None
