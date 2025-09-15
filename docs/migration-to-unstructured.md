# Migration Plan: PyMuPDF → Unstructured.io

## Executive Summary

Migrating from PyMuPDF to Unstructured.io will solve our current extraction issues (hyphenation, layout, tables) and simplify our chunking implementation. Unstructured is specifically designed for RAG pipelines and provides structured element detection that PyMuPDF lacks.

## Current Issues with PyMuPDF

### Text Extraction Problems

1. **Hyphenated Words**: "im-\nportant" instead of "important"
2. **Mixed Headers/Footers**: Page headers/footers mixed with body text
3. **Column Layout Issues**: Multi-column papers read in wrong order
4. **No Semantic Understanding**: Cannot distinguish sections, captions, tables

### Chunking Challenges

1. **Sentence Boundaries**: Breaking mid-word due to PDF artifacts
2. **No Structure Awareness**: Cannot properly group related elements
3. **Manual Layout Detection**: Complex heuristics for identifying headers/captions
4. **Poor Table/Figure Handling**: Simplified extraction loses structure

## Benefits of Unstructured.io

### Superior Extraction

- **Automatic De-hyphenation**: Fixes broken words automatically
- **Element Classification**: Identifies Title, NarrativeText, Table, FigureCaption, etc.
- **Layout Understanding**: Maintains reading order in multi-column layouts
- **Table Structure**: Preserves table structure with rows/columns
- **Header/Footer Removal**: Automatically filters page artifacts

### Built-in Chunking

- **Semantic Chunking**: `chunk_by_title()` preserves document structure
- **Element Grouping**: Keeps related elements together (caption+figure)
- **Configurable Strategies**: Multiple chunking approaches available
- **Clean Output**: No PDF artifacts in chunks

## Implementation Plan

### Phase 1: Core Migration

#### 1.1 Update Requirements

```python
# Add to requirements.txt
unstructured[pdf,local-inference]==0.16.11
unstructured-inference==0.8.1
pytesseract  # For OCR fallback
pdf2image    # For image extraction
```

#### 1.2 New Data Models

```python
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from unstructured.documents.elements import Element

@dataclass
class UnstructuredElement:
    """Wrapper for Unstructured element"""
    type: str  # Title, NarrativeText, Table, etc.
    text: str
    metadata: Dict[str, Any]
    element_id: str
    page_number: Optional[int] = None
    bbox: Optional[Dict[str, float]] = None
    parent_id: Optional[str] = None

@dataclass
class ExtractionResult:
    """Updated extraction result"""
    pdf_path: str
    elements: List[UnstructuredElement]
    page_count: int
    full_text: str
    metadata: Dict[str, Any]
    tables: List[Dict[str, Any]]
    extraction_time_ms: float
    file_size_bytes: int
    processing_strategy: str  # "hi_res", "fast", "ocr_only"
```

#### 1.3 PDF Processor Refactor

```python
from unstructured.partition.pdf import partition_pdf
from unstructured.chunking.title import chunk_by_title
from unstructured.cleaners.core import clean

class PDFProcessor:
    def extract(
        self,
        pdf_path: str,
        strategy: str = "hi_res",  # "hi_res", "fast", "ocr_only"
        **kwargs
    ) -> ExtractionResult:
        """Extract using Unstructured"""

        # Partition PDF into elements
        elements = partition_pdf(
            filename=pdf_path,
            strategy=strategy,  # hi_res uses layout detection models
            infer_table_structure=True,  # Extract table structure
            include_page_breaks=True,
            extract_images_in_pdf=False,  # Can enable if needed
            extract_forms=False,
            languages=["eng"],  # Can add more languages
        )

        # Convert to our format
        extracted_elements = []
        tables = []

        for element in elements:
            elem = UnstructuredElement(
                type=element.category,
                text=clean(element.text),  # Removes artifacts
                metadata=element.metadata.to_dict(),
                element_id=element.id,
                page_number=element.metadata.page_number,
                bbox=element.metadata.coordinates.to_dict() if element.metadata.coordinates else None
            )
            extracted_elements.append(elem)

            # Collect tables separately
            if element.category == "Table":
                tables.append({
                    "text": element.text,
                    "html": element.metadata.text_as_html if hasattr(element.metadata, 'text_as_html') else None,
                    "page": element.metadata.page_number
                })

        # Generate full text (in reading order)
        full_text = "\n\n".join([e.text for e in extracted_elements])

        return ExtractionResult(
            pdf_path=pdf_path,
            elements=extracted_elements,
            page_count=max([e.page_number for e in extracted_elements if e.page_number]),
            full_text=full_text,
            metadata=self._extract_document_metadata(elements),
            tables=tables,
            extraction_time_ms=...,
            file_size_bytes=...,
            processing_strategy=strategy
        )
```

#### 1.4 Simplified Chunk Manager

```python
from unstructured.chunking.title import chunk_by_title
from unstructured.chunking.basic import chunk_elements

class ChunkManager:
    def chunk(
        self,
        extraction_result: ExtractionResult,
        strategy: str = "by_title",
        max_characters: int = 2000,
        overlap: int = 200,
        **kwargs
    ) -> ChunkResult:
        """Chunk using Unstructured's built-in strategies"""

        # Convert our elements back to Unstructured elements
        # (or keep original elements in ExtractionResult)

        if strategy == "by_title":
            # Preserves document structure
            chunks = chunk_by_title(
                elements=extraction_result.elements,
                max_characters=max_characters,
                overlap=overlap,
                combine_text_under_n_chars=100,  # Combine small sections
                include_orig_elements=True
            )
        else:
            # Basic chunking
            chunks = chunk_elements(
                elements=extraction_result.elements,
                max_characters=max_characters,
                overlap=overlap
            )

        # Convert to our chunk format
        result_chunks = []
        for i, chunk in enumerate(chunks):
            result_chunks.append(Chunk(
                chunk_index=i,
                text=chunk.text,
                page_start=chunk.metadata.page_number,
                page_end=chunk.metadata.page_number,
                section_path=self._build_section_path(chunk),
                is_reference="References" in chunk.metadata.section if hasattr(chunk.metadata, 'section') else False,
                is_caption=chunk.category in ["FigureCaption", "TableCaption"],
                contains_table="Table" in chunk.category,
                metadata=chunk.metadata.to_dict()
            ))

        return ChunkResult(chunks=result_chunks, ...)
```

### Phase 2: Enhanced Features

#### 2.1 Table Extraction

```python
def extract_tables_as_dataframes(elements):
    """Extract tables as structured data"""
    tables = []
    for element in elements:
        if element.category == "Table":
            # Unstructured provides HTML representation
            if hasattr(element.metadata, 'text_as_html'):
                import pandas as pd
                df = pd.read_html(element.metadata.text_as_html)[0]
                tables.append({
                    'dataframe': df,
                    'page': element.metadata.page_number,
                    'caption': _find_caption_for_table(element, elements)
                })
    return tables
```

#### 2.2 Figure Detection

```python
def extract_figures(elements):
    """Extract figures with captions"""
    figures = []
    for i, element in enumerate(elements):
        if element.category == "FigureCaption":
            # Look for associated image
            figure = {
                'caption': element.text,
                'page': element.metadata.page_number,
                'bbox': element.metadata.coordinates
            }
            # Check if next element is an image
            if i + 1 < len(elements) and elements[i+1].category == "Image":
                figure['has_image'] = True
            figures.append(figure)
    return figures
```

#### 2.3 Section Hierarchy

```python
def build_document_structure(elements):
    """Build hierarchical document structure"""
    structure = []
    current_section = None

    for element in elements:
        if element.category == "Title":
            current_section = {
                'title': element.text,
                'level': 1,
                'children': [],
                'content': []
            }
            structure.append(current_section)
        elif element.category == "Header":
            # Nested section
            subsection = {
                'title': element.text,
                'level': 2,
                'content': []
            }
            if current_section:
                current_section['children'].append(subsection)
        elif current_section:
            current_section['content'].append(element)

    return structure
```

### Phase 3: Testing Updates

#### 3.1 Unit Test Updates

```python
class TestPDFProcessor:
    def test_extract_with_unstructured(self, processor, real_pdf_path):
        """Test Unstructured extraction"""
        result = processor.extract(pdf_path=real_pdf_path, strategy="hi_res")

        # Check element types
        element_types = {e.type for e in result.elements}
        assert "Title" in element_types
        assert "NarrativeText" in element_types

        # No hyphenation issues
        assert "im-\nportant" not in result.full_text

        # Tables properly extracted
        tables = [e for e in result.elements if e.type == "Table"]
        assert len(tables) > 0

    def test_layout_preservation(self, processor, real_pdf_path):
        """Test that reading order is preserved"""
        result = processor.extract(pdf_path=real_pdf_path)

        # Check that elements are in reading order
        # (Unstructured handles multi-column layouts correctly)
        text = result.full_text
        assert text.index("Introduction") < text.index("Methods")
        assert text.index("Methods") < text.index("Results")
```

#### 3.2 Integration Test Updates

```python
def test_end_to_end_with_unstructured(processor, manager, real_pdf):
    """Test complete pipeline with Unstructured"""
    # Extract with layout detection
    extraction = processor.extract(real_pdf, strategy="hi_res")

    # Chunk by title (preserves structure)
    chunks = manager.chunk(extraction, strategy="by_title")

    # Verify quality
    for chunk in chunks.chunks:
        # No broken words
        assert not re.search(r'\w+-\n\w+', chunk.text)

        # Sections preserved
        if chunk.is_caption:
            assert chunk.text.startswith(("Figure", "Table", "Fig."))
```

### Phase 4: Configuration & Deployment

#### 4.1 Environment Variables

```python
# .env
UNSTRUCTURED_STRATEGY=hi_res  # or "fast" for speed
UNSTRUCTURED_API_KEY=...  # Optional for API mode
TESSERACT_PATH=/usr/bin/tesseract  # For OCR
```

#### 4.2 Docker Updates

```dockerfile
# Add to Dockerfile
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    libmagic1

# Install Python packages
RUN pip install unstructured[pdf,local-inference]
```

#### 4.3 Performance Tuning

```python
# Caching strategy for processed documents
class CachedProcessor:
    def extract(self, pdf_path):
        cache_key = hashlib.md5(open(pdf_path, 'rb').read()).hexdigest()

        # Check cache first
        if cached := self.get_from_cache(cache_key):
            return cached

        # Process with appropriate strategy
        if self.is_scanned_pdf(pdf_path):
            strategy = "ocr_only"
        elif self.needs_high_quality(pdf_path):
            strategy = "hi_res"
        else:
            strategy = "fast"

        result = self.processor.extract(pdf_path, strategy=strategy)
        self.save_to_cache(cache_key, result)
        return result
```

## Migration Timeline

### Week 1: Setup & Core Implementation

- [ ] Install Unstructured dependencies
- [ ] Create new data models
- [ ] Implement basic extraction
- [ ] Update PDF processor

### Week 2: Chunking & Features

- [ ] Implement Unstructured chunking
- [ ] Add table extraction
- [ ] Add figure detection
- [ ] Build section hierarchy

### Week 3: Testing & Validation

- [ ] Update unit tests
- [ ] Update integration tests
- [ ] Performance benchmarking
- [ ] Quality validation with real PDFs

### Week 4: Optimization & Deployment

- [ ] Add caching layer
- [ ] Configure for production
- [ ] Update documentation
- [ ] Deploy and monitor

## Risk Mitigation

### Potential Issues

1. **Slower Processing**: hi_res mode is slower than PyMuPDF
   - Mitigation: Use "fast" mode by default, "hi_res" for complex documents

2. **Larger Dependencies**: Unstructured has more dependencies
   - Mitigation: Use Docker multi-stage builds to minimize image size

3. **OCR Requirements**: Tesseract needed for scanned PDFs
   - Mitigation: Make OCR optional, warn users about scanned PDFs

### Rollback Plan

- Keep PyMuPDF code in separate module
- Feature flag for extraction method
- A/B test with subset of users

## Success Metrics

### Quality Improvements

- [ ] No hyphenation issues in extracted text
- [ ] Correct reading order for multi-column layouts
- [ ] > 90% accuracy in table extraction
- [ ] Proper section hierarchy preservation

### Performance Targets

- [ ] <15s extraction for 100-page PDF (hi_res mode)
- [ ] <5s extraction for 100-page PDF (fast mode)
- [ ] <2s chunking for extracted document

### User Experience

- [ ] Better answer quality due to cleaner text
- [ ] More accurate citations with proper sections
- [ ] Improved table/figure Q&A capabilities

## Conclusion

Migrating to Unstructured.io will:

1. **Eliminate** current text extraction issues
2. **Simplify** our chunking implementation
3. **Improve** answer quality for end users
4. **Reduce** maintenance burden

The migration is straightforward with clear benefits and minimal risks. The investment in better extraction will pay dividends in improved RAG performance.
