"""Unit tests for structured PDFX normalization metadata handling."""

import json
from pathlib import Path

from src.schemas.pdfx_schema import PDFXResponse, build_pipeline_elements, normalize_elements


def test_structured_pdfx_elements_preserve_bbox_provenance():
    response = PDFXResponse.model_validate(
        {
            "success": True,
            "elements": [
                {
                    "index": 0,
                    "type": "Text",
                    "original_type": "TextItem",
                    "level": 1,
                    "text": "Observed phenotype in curated sample.",
                    "section_title": "Results",
                    "section_path": ["Results"],
                    "content_type": "paragraph",
                    "metadata": {
                        "doc_item_label": "text",
                        "bbox": {
                            "left": 10.0,
                            "top": 20.0,
                            "right": 40.0,
                            "bottom": 5.0,
                            "coord_origin": "BOTTOMLEFT",
                        },
                        "provenance": [
                            {
                                "page_no": 4,
                                "bbox": {
                                    "left": 11.0,
                                    "top": 21.0,
                                    "right": 41.0,
                                    "bottom": 6.0,
                                    "coord_origin": "BOTTOMLEFT",
                                },
                            }
                        ],
                    },
                }
            ],
        }
    )

    normalized = normalize_elements(response)
    pipeline_elements = build_pipeline_elements(normalized)

    assert len(pipeline_elements) == 1
    metadata = pipeline_elements[0]["metadata"]
    assert metadata["page_number"] == 4
    assert metadata["bbox"]["coord_origin"] == "BOTTOMLEFT"
    assert metadata["provenance"][0]["page_no"] == 4
    assert metadata["provenance"][0]["bbox"]["left"] == 11.0


def test_curator_fixture_retains_bbox_for_every_element():
    fixture_path = Path(__file__).resolve().parents[2] / "fixtures" / "micropub-biology-001725_pdfx.json"
    elements = json.loads(fixture_path.read_text())

    assert elements

    missing_bbox = []
    for element in elements:
        metadata = element.get("metadata") or {}
        provenance = metadata.get("provenance") or []
        has_bbox = bool(metadata.get("bbox")) or any(item.get("bbox") for item in provenance)
        if not has_bbox:
            missing_bbox.append(element.get("index"))

    assert missing_bbox == []
