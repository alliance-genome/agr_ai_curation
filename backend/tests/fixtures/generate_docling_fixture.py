#!/usr/bin/env python3
"""One-time script to generate the Docling fixture from raw API response.

Usage (from inside the backend Docker container):
    python tests/fixtures/generate_docling_fixture.py /path/to/docling_response.json

Or via docker compose:
    docker compose -f docker-compose.test.yml run --rm \
      -v /tmp/docling_response.json:/tmp/docling_response.json \
      backend-persistence-tests \
      python tests/fixtures/generate_docling_fixture.py /tmp/docling_response.json
"""

import json
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.lib.pipeline.docling_parser import (
    DoclingResponse,
    normalize_elements,
    build_pipeline_elements,
)


def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_docling_fixture.py <raw_docling_response.json>")
        sys.exit(1)

    raw_path = Path(sys.argv[1])
    if not raw_path.exists():
        print(f"File not found: {raw_path}")
        sys.exit(1)

    with open(raw_path) as f:
        raw_result = json.load(f)

    print(f"Raw response: {len(raw_result.get('elements', []))} elements")

    # Run the same normalization pipeline as DoclingParser
    response_model = DoclingResponse.model_validate(raw_result)
    normalized = normalize_elements(response_model)
    cleaned_elements = build_pipeline_elements(normalized)

    print(f"After normalization: {len(cleaned_elements)} elements")

    # Save to fixture location
    fixture_path = Path(__file__).parent / "micropub-biology-001725_docling.json"
    with open(fixture_path, "w") as f:
        json.dump(cleaned_elements, f, indent=2, default=str)

    print(f"Fixture saved to: {fixture_path}")
    print(f"File size: {fixture_path.stat().st_size:,} bytes")

    # Show a sample element
    if cleaned_elements:
        print(f"\nSample element keys: {list(cleaned_elements[0].keys())}")
        text_preview = cleaned_elements[0].get("text", "")[:100]
        print(f"First element text: {text_preview}...")


if __name__ == "__main__":
    main()
