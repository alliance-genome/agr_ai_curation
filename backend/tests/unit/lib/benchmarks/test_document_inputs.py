import json

import pytest

from src.lib.benchmarks.document_inputs import decode_frozen_document


def test_local_elements_preserve_page_section_and_figure_provenance():
    elements = [{
        "index": 0,
        "type": "NarrativeText",
        "text": "Synthetic result.",
        "metadata": {
            "page_number": 3,
            "section_title": "Results",
            "doc_items": [{"label": "paragraph", "self_ref": "#/texts/1"}],
        },
    }]
    assert decode_frozen_document(
        json.dumps(elements).encode(), content_type="application/json"
    ) == elements


@pytest.mark.parametrize("content_type", ["text/plain", "text/markdown"])
def test_text_uses_normal_pipeline_elements_without_fetching_image_assets(content_type):
    content = b"# Results\n\nSynthetic finding.\n\n![Figure 1](https://invalid.example/figure.png)"
    elements = decode_frozen_document(content, content_type=content_type)
    assert any("Synthetic finding." in element["text"] for element in elements)
    assert any(element["metadata"].get("section_title") == "Results" for element in elements)
    assert not any("invalid.example" in element["text"] for element in elements)


@pytest.mark.parametrize("xml", [
    b'''<article><front><article-meta><title-group><article-title>Synthetic paper</article-title>
    </title-group></article-meta></front><body><sec><title>Results</title>
    <p>Synthetic finding.</p></sec></body></article>''',
    b'''<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><titleStmt>
    <title>Synthetic paper</title></titleStmt><publicationStmt><p>Test</p></publicationStmt>
    <sourceDesc><p>Test</p></sourceDesc></fileDesc></teiHeader><text><body><div>
    <head>Results</head><p>Synthetic finding.</p></div></body></text></TEI>''',
])
def test_xml_uses_installed_jats_and_tei_converters(xml):
    elements = decode_frozen_document(xml, content_type="application/xml")
    assert any("Synthetic finding." in element["text"] for element in elements)
    assert any(element["metadata"].get("section_title") == "Results" for element in elements)


def test_xml_external_entities_are_not_resolved(tmp_path):
    sentinel = tmp_path / "not-a-benchmark-input.txt"
    sentinel.write_text("PRIVATE_SENTINEL_MUST_NOT_BE_READ")
    xml = f'''<!DOCTYPE article [<!ENTITY external SYSTEM "{sentinel.as_uri()}">]>
    <article><body><sec><title>Results</title><p>Safe text &external;</p></sec></body></article>'''
    elements = decode_frozen_document(xml.encode(), content_type="application/xml")
    assert "PRIVATE_SENTINEL_MUST_NOT_BE_READ" not in json.dumps(elements)


@pytest.mark.parametrize("payload", [
    {"messages": [], "user_id": "attacker", "document_id": "live-document"},
    [],
    [{"text": 3}],
    [{"text": "paper", "metadata": "invalid"}],
    [{"text": "  "}],
])
def test_invalid_document_json_cannot_become_runtime_arguments(payload):
    with pytest.raises(ValueError):
        decode_frozen_document(json.dumps(payload).encode(), content_type="application/json")


def test_unknown_content_type_is_not_guessed():
    with pytest.raises(ValueError, match="Unsupported"):
        decode_frozen_document(b"paper", content_type="text/html")
