"""Unit tests for PDFX schema normalization."""

from src.schemas.pdfx_schema import (
    PDFXElement,
    PDFXResponse,
    build_pipeline_elements,
    normalize_elements,
    normalize_table_text,
    normalize_text,
)


def test_normalize_text_strips_inline_markdown_formatting():
    assert normalize_text("Signal from **B cells** depends on Ca<sup>2+</sup> and *kinase* activity.") == (
        "Signal from B cells depends on Ca2+ and kinase activity."
    )


def test_normalize_elements_and_pipeline_output_strip_inline_formatting():
    response = PDFXResponse(
        success=True,
        elements=[
            PDFXElement(
                index=0,
                type="Title",
                original_type="section_header",
                level=1,
                text="**Results** <sup>2+</sup>",
                section_path=["**Results** <sup>2+</sup>"],
                content_type="heading",
                is_heading=True,
                metadata={"doc_item_label": "section_header"},
            ),
            PDFXElement(
                index=1,
                type="NarrativeText",
                original_type="paragraph",
                level=1,
                text="Signal from **B cells** depends on Ca<sup>2+</sup> and *kinase* activity.",
                section_title="**Results** <sup>2+</sup>",
                section_path=["**Results** <sup>2+</sup>"],
                content_type="paragraph",
                metadata={"doc_item_label": "paragraph"},
            ),
        ],
    )

    normalized = normalize_elements(response)

    assert [element.text for element in normalized] == [
        "Results 2+",
        "Signal from B cells depends on Ca2+ and kinase activity.",
    ]
    assert normalized[0].section_path == ["Results 2+"]
    assert normalized[1].section_title == "Results 2+"
    assert normalized[1].section_path == ["Results 2+"]
    assert normalized[1].embedding_text == (
        "Results 2+\n\nSignal from B cells depends on Ca2+ and kinase activity."
    )

    pipeline_elements = build_pipeline_elements(normalized)

    assert [element["text"] for element in pipeline_elements] == [
        "Results 2+",
        "Results 2+\n\nSignal from B cells depends on Ca2+ and kinase activity.",
    ]
    assert pipeline_elements[1]["metadata"]["section_title"] == "Results 2+"
    assert pipeline_elements[1]["metadata"]["section_path"] == ["Results 2+"]


def test_normalize_text_keeps_literal_asterisks():
    assert normalize_text("Stop codon p.Tyr318*/c.954C>G in *drag-1* mutants.") == (
        "Stop codon p.Tyr318*/c.954C>G in drag-1 mutants."
    )


def test_normalize_table_text_cleans_cell_by_cell():
    assert normalize_table_text("| a* | **b** |\n| --- | --- |\n| c* | **** |") == (
        "| a* | b |\n| --- | --- |\n| c* | **** |"
    )


def test_normalize_elements_and_pipeline_output_clean_each_text_exactly_once():
    # An escaped pair of asterisks gives literal asterisks after one pass; a
    # second pass anywhere (section path, section prefix, embedding text)
    # would delete them.
    response = PDFXResponse(
        success=True,
        elements=[
            PDFXElement(
                index=0,
                type="Title",
                original_type="section_header",
                level=1,
                text="\\*Escaped\\* heading",
                section_path=["\\*Escaped\\* heading"],
                content_type="heading",
                is_heading=True,
                metadata={"doc_item_label": "section_header"},
            ),
            PDFXElement(
                index=1,
                type="NarrativeText",
                original_type="paragraph",
                level=1,
                text="Paragraph with \\*literal\\* stars.",
                section_title="\\*Escaped\\* heading",
                section_path=["\\*Escaped\\* heading"],
                content_type="paragraph",
                metadata={"doc_item_label": "paragraph"},
            ),
            PDFXElement(
                index=2,
                type="Table",
                original_type="table",
                level=1,
                text="| \\*p.Gln12\\* | **** |",
                section_title="\\*Escaped\\* heading",
                section_path=["\\*Escaped\\* heading"],
                content_type="table",
                metadata={"doc_item_label": "table"},
            ),
        ],
    )

    normalized = normalize_elements(response)

    assert [element.text for element in normalized] == [
        "*Escaped* heading",
        "Paragraph with *literal* stars.",
        "| *p.Gln12* | **** |",
    ]
    assert [element.section_path for element in normalized] == [["*Escaped* heading"]] * 3

    pipeline_elements = build_pipeline_elements(normalized)

    assert [element["text"] for element in pipeline_elements] == [
        "*Escaped* heading",
        "*Escaped* heading\n\nParagraph with *literal* stars.",
        "| *p.Gln12* | **** |",
    ]
    assert pipeline_elements[1]["metadata"]["section_title"] == "*Escaped* heading"
