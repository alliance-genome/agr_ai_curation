# Docker Build Notes for Unstructured.io Migration

## ⚠️ Important: Build Requirements

The migration from PyMuPDF to Unstructured.io requires rebuilding the Docker container with significant new dependencies.

## Build Time Warning

**Expected build time: 5-10 minutes**

The build will download and install:

- **torch**: ~888MB (PyTorch for ML models)
- **transformers**: ~11.6MB (Hugging Face transformers)
- **onnxruntime**: ~16.5MB (ONNX runtime for models)
- **opencv-python**: ~63MB (Computer vision library)
- **nvidia-cublas**: ~594MB (CUDA libraries)
- **Total download**: ~1.5GB+

## Build Command

```bash
# Full rebuild (recommended)
docker compose build backend --no-cache

# Or regular build (uses cache)
docker compose build backend
```

## What Gets Installed

### System Packages (via apt-get)

- `tesseract-ocr` - OCR engine for scanned PDFs
- `tesseract-ocr-eng` - English language data for Tesseract
- `poppler-utils` - PDF rendering library
- `libmagic1` - File type detection

### Python Packages

```
unstructured[pdf,local-inference]==0.16.11
unstructured-inference==0.8.1
pytesseract
pdf2image
pillow
pandas
tabulate
```

### ML Dependencies (auto-installed)

- torch (PyTorch)
- transformers (Hugging Face)
- onnxruntime
- opencv-python
- timm (PyTorch Image Models)
- layoutparser
- effdet (EfficientDet)

## Alternative: Lighter Installation

If you don't need the full ML capabilities, you can use a lighter installation:

```bash
# In requirements.txt, change:
unstructured[pdf]==0.16.11  # Without local-inference

# This skips the heavy ML dependencies but still provides:
# - Basic PDF extraction
# - Table detection
# - Clean text extraction
```

## Testing Without Full Build

The structure verification script confirms all code is properly migrated:

```bash
cd backend
python3 test_structure.py
```

This verifies:

- ✅ No PyMuPDF references remain
- ✅ All imports use correct module names
- ✅ Test files import from correct locations
- ✅ 56 total test methods ready to run

## Running Tests After Build

Once the Docker container is built:

```bash
# Run all backend tests
docker compose exec backend pytest tests/ -v

# Run specific test suites
docker compose exec backend pytest tests/unit/test_pdf_processor.py -v
docker compose exec backend pytest tests/unit/test_chunk_manager.py -v
docker compose exec backend pytest tests/integration/test_real_pdf.py -v

# Run with coverage
docker compose exec backend pytest tests/ --cov=lib --cov-report=term-missing
```

## Troubleshooting

### Build Timeout

If the build times out, try:

1. Building in stages:
   ```bash
   docker compose build backend --progress=plain
   ```
2. Using a faster mirror for pip:
   ```bash
   docker compose build backend --build-arg PIP_INDEX_URL=https://pypi.org/simple
   ```

### Disk Space

Ensure you have at least 5GB free disk space for:

- Downloaded packages (~1.5GB)
- Extracted files (~2GB)
- Docker image layers (~1.5GB)

### Memory Issues

The build requires ~4GB RAM. If you encounter memory issues:

1. Close other applications
2. Increase Docker memory allocation
3. Build without cache: `docker compose build backend --no-cache`

## Verification Checklist

After successful build:

- [ ] Container starts without errors
- [ ] Can import `from unstructured.partition.pdf import partition_pdf`
- [ ] PDF extraction works with test file
- [ ] All tests pass
- [ ] No PyMuPDF imports remain

## Performance Notes

### First Run

- First extraction may be slower as models are initialized
- Subsequent extractions will be faster due to model caching

### Strategy Selection

- **fast**: Quick extraction, good for most PDFs (~5s for 100 pages)
- **hi_res**: Better accuracy, slower (~15s for 100 pages)
- **ocr_only**: For scanned PDFs (~30s+ for 100 pages)

## Success Indicators

When properly installed, you should see:

```python
>>> from unstructured.partition.pdf import partition_pdf
>>> print("Success!")
Success!
```

No errors about missing modules or dependencies.
