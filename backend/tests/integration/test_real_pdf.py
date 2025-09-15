"""
Integration tests using real PDF document
Tests PDF extraction and chunking with actual scientific paper
"""

import pytest
import os
from pathlib import Path

from lib.pdf_processor import PDFProcessor, ExtractionResult
from lib.chunk_manager import ChunkManager, ChunkingStrategy


class TestRealPDFIntegration:
    """Integration tests with real scientific paper"""

    @pytest.fixture
    def real_pdf_path(self):
        """Path to real test PDF"""
        return "tests/fixtures/test_paper.pdf"

    @pytest.fixture
    def processor(self):
        """Create PDF processor instance"""
        return PDFProcessor()

    @pytest.fixture
    def manager(self):
        """Create chunk manager instance"""
        return ChunkManager()

    @pytest.fixture
    def extracted_pdf(self, processor, real_pdf_path):
        """Extract the real PDF once for multiple tests"""
        return processor.extract(
            pdf_path=real_pdf_path,
            extract_tables=True,
            extract_figures=True,
            preserve_layout=True,
        )

    # ==================== PDF EXTRACTION TESTS ====================

    def test_extract_real_pdf_basic(self, processor, real_pdf_path):
        """Test basic extraction of real PDF"""
        result = processor.extract(pdf_path=real_pdf_path)

        assert isinstance(result, ExtractionResult)
        assert result.page_count == 9  # Known page count
        assert len(result.pages) == 9
        assert len(result.full_text) > 50000  # Should have substantial text
        assert "Dnr1 mutations" in result.full_text  # Check title
        assert "neurodegeneration" in result.full_text
        assert result.extraction_time_ms > 0

    def test_extract_real_pdf_metadata(self, extracted_pdf):
        """Test metadata extraction from real PDF"""
        metadata = extracted_pdf.metadata

        assert metadata is not None
        assert "Dnr1 mutations" in metadata.get("title", "")
        assert "Proc. Natl. Acad. Sci" in metadata.get("subject", "")
        assert metadata.get("creator") is not None
        assert metadata.get("producer") is not None

    def test_extract_real_pdf_with_layout(self, processor, real_pdf_path):
        """Test layout extraction from real PDF"""
        result = processor.extract(pdf_path=real_pdf_path, preserve_layout=True)

        # Check that layout blocks were extracted
        has_layout = False
        for page in result.pages:
            if page.layout_blocks:
                has_layout = True
                # Verify block structure
                for block in page.layout_blocks:
                    assert "type" in block
                    assert "text" in block
                    assert "bbox" in block
                break

        assert has_layout, "Should have extracted layout blocks"

    def test_extract_real_pdf_figures(self, extracted_pdf):
        """Test figure extraction from real PDF"""
        # Scientific papers typically have figures
        assert extracted_pdf.figure_count > 0

        if extracted_pdf.figures:
            for figure in extracted_pdf.figures:
                assert figure.page_number > 0
                assert figure.page_number <= 9
                assert figure.bbox is not None

    def test_validate_real_pdf(self, processor, real_pdf_path):
        """Test validation of real PDF"""
        result = processor.validate(
            pdf_path=real_pdf_path, check_corruption=True, check_encryption=True
        )

        assert result.is_valid is True
        assert result.page_count == 9
        assert result.has_text is True
        assert result.file_size_bytes > 1000000  # > 1MB
        assert result.is_encrypted is False
        assert result.is_corrupted is False

    def test_hash_real_pdf(self, processor, real_pdf_path):
        """Test hashing of real PDF"""
        result = processor.hash(pdf_path=real_pdf_path, normalized=True, per_page=True)

        assert result.file_hash is not None
        assert len(result.file_hash) == 32  # MD5 length
        assert result.content_hash is not None
        assert result.content_hash_normalized is not None
        assert len(result.page_hashes) == 9
        assert result.page_count == 9

    # ==================== CHUNKING TESTS ====================

    def test_chunk_real_pdf_semantic(self, manager, extracted_pdf):
        """Test semantic chunking of real PDF"""
        result = manager.chunk(
            extraction_result=extracted_pdf,
            chunk_size=512,
            overlap=50,
            strategy=ChunkingStrategy.SEMANTIC,
            preserve_layout=True,
            mark_references=True,
            semantic_boundaries=True,
        )

        assert result.total_chunks > 20  # Should create multiple chunks
        assert result.total_chunks < 50  # But not too many

        # Verify chunk properties
        for chunk in result.chunks:
            assert chunk.token_count > 0
            assert chunk.token_count <= 600  # Some tolerance for semantic boundaries
            assert chunk.chunk_hash is not None
            assert chunk.page_start > 0
            assert chunk.page_end <= 9

    def test_chunk_real_pdf_references(self, manager, extracted_pdf):
        """Test that references section is properly marked"""
        result = manager.chunk(
            extraction_result=extracted_pdf,
            chunk_size=512,
            overlap=50,
            mark_references=True,
        )

        # Scientific papers should have references
        ref_chunks = [c for c in result.chunks if c.is_reference]
        assert len(ref_chunks) > 0, "Should identify reference chunks"

        # Check that reference chunks contain citation patterns
        for chunk in ref_chunks:
            text_lower = chunk.text.lower()
            # Should have numbers, years, or "references" keyword
            has_citations = (
                "references" in text_lower
                or "et al." in text_lower
                or bool(chunk.text.count("(19") + chunk.text.count("(20") > 2)
            )
            assert has_citations

    def test_chunk_real_pdf_captions(self, manager, extracted_pdf):
        """Test caption detection in real PDF"""
        result = manager.chunk(extraction_result=extracted_pdf, group_captions=True)

        # Check for figure/table captions
        caption_chunks = [
            c for c in result.chunks if c.is_caption or c.contains_caption
        ]

        # Scientific papers typically have figures/tables
        if caption_chunks:
            for chunk in caption_chunks[:3]:  # Check first few
                text_lower = chunk.text.lower()
                assert any(
                    pattern in text_lower
                    for pattern in ["fig.", "figure", "table", "tab."]
                )

    def test_chunk_real_pdf_fixed_size(self, manager, extracted_pdf):
        """Test fixed-size chunking of real PDF"""
        result = manager.chunk(
            extraction_result=extracted_pdf,
            chunk_size=200,
            overlap=20,
            strategy=ChunkingStrategy.FIXED_SIZE,
        )

        # Should create more chunks with smaller size
        assert result.total_chunks > 50

        # Check size consistency (except last chunk)
        for chunk in result.chunks[:-1]:
            assert 180 <= chunk.token_count <= 200

    def test_chunk_real_pdf_sentence_based(self, manager, extracted_pdf):
        """Test sentence-based chunking of real PDF"""
        result = manager.chunk(
            extraction_result=extracted_pdf,
            chunk_size=300,
            overlap=30,
            strategy=ChunkingStrategy.SENTENCE_BASED,
        )

        # Check that chunks end at sentence boundaries
        for chunk in result.chunks[:-1]:  # Except last
            text = chunk.text.strip()
            if text:
                # Should end with sentence-ending punctuation
                assert (
                    text[-1] in ".!?"
                ), f"Chunk should end with punctuation: ...{text[-50:]}"

    def test_chunk_real_pdf_overlap(self, manager, extracted_pdf):
        """Test that overlap works correctly with real PDF"""
        result = manager.chunk(
            extraction_result=extracted_pdf, chunk_size=400, overlap=100
        )

        # Check overlap between consecutive chunks
        for i in range(1, min(5, len(result.chunks))):
            chunk1 = result.chunks[i - 1]
            chunk2 = result.chunks[i]

            # Character positions should overlap
            assert chunk2.char_start < chunk1.char_end, "Chunks should overlap"

            # Calculate actual overlap
            overlap_start = chunk2.char_start
            overlap_end = min(chunk1.char_end, chunk2.char_end)
            overlap_chars = overlap_end - overlap_start

            # Should have some overlap (allowing for boundary adjustments)
            assert overlap_chars > 50, f"Insufficient overlap: {overlap_chars} chars"

    def test_analyze_real_pdf_chunks(self, manager, extracted_pdf):
        """Test chunk analysis on real PDF"""
        chunk_result = manager.chunk(extraction_result=extracted_pdf, chunk_size=512)

        analysis = manager.analyze(
            chunk_result=chunk_result, show_boundaries=True, token_counts=True
        )

        assert "total_chunks" in analysis
        assert "avg_chunk_size" in analysis
        assert "token_distribution" in analysis

        # Check distribution stats
        dist = analysis["token_distribution"]
        assert dist["mean"] > 400
        assert dist["mean"] < 600
        assert "percentiles" in dist

    # ==================== PERFORMANCE TESTS ====================

    def test_extraction_performance(self, processor, real_pdf_path):
        """Test that extraction completes in reasonable time"""
        result = processor.extract(
            pdf_path=real_pdf_path,
            extract_tables=True,
            extract_figures=True,
            preserve_layout=True,
        )

        # Should complete within 5 seconds for a ~2MB PDF
        assert result.extraction_time_ms < 5000

    def test_chunking_performance(self, manager, extracted_pdf):
        """Test that chunking completes in reasonable time"""
        result = manager.chunk(
            extraction_result=extracted_pdf,
            preserve_layout=True,
            mark_references=True,
            semantic_boundaries=True,
        )

        # Chunking should be fast (< 1 second)
        assert result.processing_time_ms < 1000

    # ==================== END-TO-END WORKFLOW TEST ====================

    def test_complete_workflow(self, processor, manager, real_pdf_path):
        """Test complete extraction and chunking workflow"""

        # Step 1: Validate PDF
        validation = processor.validate(pdf_path=real_pdf_path)
        assert validation.is_valid is True

        # Step 2: Extract with all features
        extraction = processor.extract(
            pdf_path=real_pdf_path,
            extract_tables=True,
            extract_figures=True,
            preserve_layout=True,
        )
        assert extraction.page_count == 9
        assert len(extraction.full_text) > 50000

        # Step 3: Chunk with semantic boundaries
        chunks = manager.chunk(
            extraction_result=extraction,
            chunk_size=512,
            overlap=50,
            preserve_layout=True,
            mark_references=True,
            semantic_boundaries=True,
        )
        assert chunks.total_chunks > 20

        # Step 4: Analyze chunks
        analysis = manager.analyze(chunk_result=chunks, token_counts=True)
        assert analysis["avg_chunk_size"] > 400

        # Step 5: Verify specific content
        # Should find abstract/introduction
        intro_found = any(
            "introduction" in chunk.text.lower() or "abstract" in chunk.text.lower()
            for chunk in chunks.chunks[:5]  # Check first few chunks
        )
        assert intro_found, "Should find introduction/abstract"

        # Should find references
        ref_found = any(chunk.is_reference for chunk in chunks.chunks)
        assert ref_found, "Should identify references section"
