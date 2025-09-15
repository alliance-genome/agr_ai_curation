"""
Integration tests using real PDF document with Unstructured.io
Tests the new PDF extraction and chunking implementation
"""

import pytest
import os
from pathlib import Path

from lib.pdf_processor import PDFProcessor, ExtractionResult
from lib.chunk_manager import ChunkManager, ChunkingStrategy


class TestRealPDFUnstructuredIntegration:
    """Integration tests with real scientific paper using Unstructured"""

    @pytest.fixture
    def real_pdf_path(self):
        """Path to real test PDF"""
        return "tests/fixtures/test_paper.pdf"

    @pytest.fixture
    def processor(self):
        """Create PDF processor instance"""
        return PDFProcessor(default_strategy="fast")

    @pytest.fixture
    def manager(self):
        """Create chunk manager instance"""
        return ChunkManager()

    @pytest.fixture
    def extracted_pdf(self, processor, real_pdf_path):
        """Extract the real PDF once for multiple tests"""
        # Use fast strategy for testing speed
        return processor.extract(
            pdf_path=real_pdf_path,
            strategy="fast",
            extract_tables=True,
            extract_figures=True,
        )

    # ==================== PDF EXTRACTION TESTS ====================

    def test_extract_real_pdf_basic(self, processor, real_pdf_path):
        """Test basic extraction of real PDF with Unstructured"""
        result = processor.extract(pdf_path=real_pdf_path, strategy="fast")

        assert isinstance(result, ExtractionResult)
        assert result.page_count == 9  # Known page count
        assert len(result.full_text) > 50000  # Should have substantial text

        # Check for key content without hyphenation issues
        assert "Dnr1 mutations" in result.full_text or "Dnr1" in result.full_text
        assert "neurodegeneration" in result.full_text

        # Should NOT have hyphenation artifacts
        assert "im-\nportant" not in result.full_text
        assert "pro-\ntein" not in result.full_text

        assert result.extraction_time_ms > 0
        assert result.processing_strategy == "fast"

    def test_extract_real_pdf_element_types(self, extracted_pdf):
        """Test that Unstructured properly classifies elements"""
        element_types = {e.type for e in extracted_pdf.elements}

        # Should have various element types
        assert "Title" in element_types or "Header" in element_types
        assert "NarrativeText" in element_types

        # Count different types
        type_counts = {}
        for elem in extracted_pdf.elements:
            type_counts[elem.type] = type_counts.get(elem.type, 0) + 1

        # Should have substantial narrative text
        assert type_counts.get("NarrativeText", 0) > 10

    def test_extract_real_pdf_clean_text(self, extracted_pdf):
        """Test that text is properly cleaned"""
        # Check that common PDF artifacts are removed
        full_text = extracted_pdf.full_text

        # No broken hyphenation
        import re

        hyphen_pattern = r"\w+-\n\w+"
        hyphen_matches = re.findall(hyphen_pattern, full_text)
        assert (
            len(hyphen_matches) == 0
        ), f"Found hyphenation artifacts: {hyphen_matches[:5]}"

        # Proper spacing
        assert "  " not in full_text  # No double spaces in middle of text

    def test_extract_real_pdf_with_tables(self, processor, real_pdf_path):
        """Test table extraction with Unstructured"""
        result = processor.extract(
            pdf_path=real_pdf_path, strategy="fast", extract_tables=True
        )

        # Scientific papers typically have tables
        if result.tables:
            for table in result.tables:
                assert table["text"]
                assert table["page"] > 0
                assert table["page"] <= 9
                # Check if HTML representation is available
                if "html" in table:
                    assert "<table" in table["html"] or table["html"] is None

    def test_extract_real_pdf_with_figures(self, extracted_pdf):
        """Test figure detection with Unstructured"""
        # Check for figure captions
        figure_elements = [
            e for e in extracted_pdf.elements if e.type == "FigureCaption"
        ]

        if figure_elements:
            for fig_elem in figure_elements[:3]:  # Check first few
                assert "Fig" in fig_elem.text or "Figure" in fig_elem.text

    def test_extract_real_pdf_metadata(self, extracted_pdf):
        """Test metadata extraction with Unstructured"""
        metadata = extracted_pdf.metadata

        assert metadata is not None
        # Should extract title if available
        if "title" in metadata:
            assert len(metadata["title"]) > 0

    def test_validate_real_pdf(self, processor, real_pdf_path):
        """Test validation with Unstructured"""
        result = processor.validate(
            pdf_path=real_pdf_path, check_corruption=True, check_encryption=True
        )

        assert result.is_valid is True
        assert result.page_count >= 1  # May be approximate with fast validation
        assert result.has_text is True
        assert result.file_size_bytes > 1000000  # > 1MB
        assert result.is_encrypted is False
        assert result.is_corrupted is False
        assert result.is_scanned is False  # This is a text PDF

    def test_hash_real_pdf(self, processor, real_pdf_path):
        """Test hashing with Unstructured"""
        result = processor.hash(pdf_path=real_pdf_path, normalized=True, per_page=True)

        assert result.file_hash is not None
        assert len(result.file_hash) == 32  # MD5 length
        assert result.content_hash is not None
        assert result.content_hash_normalized is not None
        assert result.content_hash != result.content_hash_normalized
        assert len(result.page_hashes) > 0

    # ==================== CHUNKING TESTS ====================

    def test_chunk_real_pdf_by_title(self, manager, extracted_pdf):
        """Test title-based chunking (recommended for scientific papers)"""
        result = manager.chunk(
            extraction_result=extracted_pdf,
            strategy=ChunkingStrategy.BY_TITLE,
            max_characters=2000,
            overlap=200,
            combine_under_n_chars=100,
        )

        assert result.total_chunks > 5  # Should create multiple chunks
        assert result.total_chunks < 100  # But not too many

        # Verify chunk properties
        for chunk in result.chunks:
            assert chunk.token_count > 0
            assert chunk.chunk_hash is not None
            assert chunk.page_start > 0
            assert chunk.page_end <= 9

            # Check for clean text (no hyphenation)
            assert "im-\nportant" not in chunk.text

    def test_chunk_real_pdf_references(self, manager, extracted_pdf):
        """Test that references are properly identified"""
        result = manager.chunk(
            extraction_result=extracted_pdf,
            strategy=ChunkingStrategy.BY_TITLE,
            max_characters=2000,
        )

        # Scientific papers should have references
        ref_chunks = [c for c in result.chunks if c.is_reference]

        if ref_chunks:
            # Check that reference chunks contain citation patterns
            for chunk in ref_chunks[:2]:
                text_lower = chunk.text.lower()
                has_citations = (
                    "references" in text_lower
                    or "et al." in text_lower
                    or bool(chunk.text.count("(19") + chunk.text.count("(20") > 1)
                )
                assert has_citations

    def test_chunk_real_pdf_by_page(self, manager, extracted_pdf):
        """Test page-based chunking"""
        result = manager.chunk(
            extraction_result=extracted_pdf,
            strategy=ChunkingStrategy.BY_PAGE,
            max_characters=5000,  # Allow larger chunks for full pages
        )

        # Should have at least one chunk per page with content
        assert result.total_chunks >= 9

        # Check page assignments
        pages_covered = set()
        for chunk in result.chunks:
            pages_covered.add(chunk.page_start)

        # Should cover multiple pages
        assert len(pages_covered) >= 8

    def test_chunk_real_pdf_by_section(self, manager, extracted_pdf):
        """Test section-based chunking"""
        result = manager.chunk(
            extraction_result=extracted_pdf,
            strategy=ChunkingStrategy.BY_SECTION,
            max_characters=2000,
            overlap=100,
        )

        # Should create chunks based on document sections
        assert result.total_chunks > 3

        # Check that chunks have section information
        sections_found = set()
        for chunk in result.chunks:
            if chunk.section_path:
                sections_found.add(chunk.section_path)

        # Should identify multiple sections
        assert len(sections_found) >= 2

    def test_chunk_real_pdf_captions(self, manager, extracted_pdf):
        """Test caption detection in chunks"""
        result = manager.chunk(extraction_result=extracted_pdf)

        # Check for figure/table captions
        caption_chunks = [
            c for c in result.chunks if c.is_caption or c.contains_caption
        ]

        # Scientific papers typically have figures/tables
        if caption_chunks:
            for chunk in caption_chunks[:3]:
                text_lower = chunk.text.lower()
                assert any(
                    pattern in text_lower
                    for pattern in ["fig.", "figure", "table", "tab."]
                )

    def test_analyze_real_pdf_chunks(self, manager, extracted_pdf):
        """Test chunk analysis on real PDF"""
        chunk_result = manager.chunk(
            extraction_result=extracted_pdf,
            strategy=ChunkingStrategy.BY_TITLE,
            max_characters=2000,
        )

        analysis = manager.analyze(
            chunk_result=chunk_result,
            show_boundaries=True,
            token_counts=True,
            show_references=True,
        )

        assert "total_chunks" in analysis
        assert "avg_chunk_size" in analysis
        assert "token_distribution" in analysis

        # Check distribution stats
        dist = analysis["token_distribution"]
        assert dist["mean"] > 100  # Should have substantial chunks
        assert "percentiles" in dist

        # Check special content
        assert "special_content" in analysis
        special = analysis["special_content"]
        assert special["references"] >= 0

    # ==================== PERFORMANCE TESTS ====================

    def test_extraction_performance_fast(self, processor, real_pdf_path):
        """Test that fast extraction completes quickly"""
        result = processor.extract(
            pdf_path=real_pdf_path,
            strategy="fast",
            extract_tables=False,
            extract_figures=False,
        )

        # Fast strategy should be quick (allowing more time for Unstructured)
        assert result.extraction_time_ms < 30000  # 30 seconds max

    @pytest.mark.slow
    def test_extraction_performance_hi_res(self, processor, real_pdf_path):
        """Test hi-res extraction (slower but more accurate)"""
        result = processor.extract(
            pdf_path=real_pdf_path,
            strategy="hi_res",
            extract_tables=True,
            extract_figures=True,
        )

        # Hi-res will be slower but should complete
        assert result.extraction_time_ms < 60000  # 60 seconds max

        # Should have better element classification
        element_types = {e.type for e in result.elements}
        assert len(element_types) >= 3  # Multiple element types

    def test_chunking_performance(self, manager, extracted_pdf):
        """Test that chunking completes quickly"""
        result = manager.chunk(
            extraction_result=extracted_pdf,
            strategy=ChunkingStrategy.BY_TITLE,
            max_characters=2000,
        )

        # Chunking should be fast
        assert result.processing_time_ms < 1000  # Less than 1 second

    # ==================== DOCUMENT STRUCTURE TESTS ====================

    def test_document_structure(self, processor, real_pdf_path):
        """Test document structure extraction"""
        result = processor.extract(pdf_path=real_pdf_path, strategy="fast")

        structure = processor.build_document_structure(result.elements)

        assert len(structure) > 0
        # Should have hierarchical structure
        for section in structure:
            assert "title" in section
            assert "level" in section
            assert "content" in section

    def test_table_extraction_as_dataframes(self, processor, real_pdf_path):
        """Test table extraction as DataFrames"""
        result = processor.extract(
            pdf_path=real_pdf_path, strategy="fast", extract_tables=True
        )

        tables = processor.extract_tables_as_dataframes(result.elements)

        if tables:
            for table in tables:
                assert "text" in table
                assert "page" in table
                # May have DataFrame if pandas is available
                if "dataframe" in table:
                    assert table["dataframe"] is not None

    # ==================== END-TO-END WORKFLOW TEST ====================

    def test_complete_workflow(self, processor, manager, real_pdf_path):
        """Test complete extraction and chunking workflow with Unstructured"""

        # Step 1: Validate PDF
        validation = processor.validate(pdf_path=real_pdf_path)
        assert validation.is_valid is True
        assert validation.is_scanned is False

        # Step 2: Extract with all features
        extraction = processor.extract(
            pdf_path=real_pdf_path,
            strategy="fast",  # Use fast for testing
            extract_tables=True,
            extract_figures=True,
        )
        assert extraction.page_count == 9
        assert len(extraction.full_text) > 50000

        # Step 3: Verify clean text extraction
        assert "im-\nportant" not in extraction.full_text  # No hyphenation

        # Step 4: Chunk with title strategy (best for papers)
        chunks = manager.chunk(
            extraction_result=extraction,
            strategy=ChunkingStrategy.BY_TITLE,
            max_characters=2000,
            overlap=200,
            combine_under_n_chars=100,
        )
        assert chunks.total_chunks > 5

        # Step 5: Analyze chunks
        analysis = manager.analyze(
            chunk_result=chunks, token_counts=True, show_references=True
        )
        assert analysis["avg_chunk_size"] > 100

        # Step 6: Verify specific content improvements
        # Should find clean abstract/introduction
        intro_found = False
        for chunk in chunks.chunks[:5]:
            if "introduction" in chunk.text.lower() or "abstract" in chunk.text.lower():
                intro_found = True
                # Check text quality
                assert "  " not in chunk.text  # No double spaces
                break

        assert intro_found, "Should find introduction/abstract"

        # Should identify references properly
        if analysis["reference_chunks"]["count"] > 0:
            ref_indices = analysis["reference_chunks"]["indices"]
            ref_chunk = chunks.chunks[ref_indices[0]]
            assert ref_chunk.is_reference
