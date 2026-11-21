"""Read-only search index settings report: names and counts only."""

from __future__ import annotations

import copy
import json

from src.lib.weaviate_client import settings_report

MODEL = "text-embedding-3-small"


def _schema_from_code() -> dict:
    """A server schema that stores exactly what current code creates."""
    payloads = settings_report.expected_collection_payloads(MODEL)
    return {"classes": [copy.deepcopy(p) for p in payloads.values()]}


def test_server_matching_code_reports_no_differences():
    report = settings_report.build_report({"version": "1.31.18"}, _schema_from_code(), MODEL, "1.31.18")

    assert report["engine_version"] == "1.31.18"
    assert report["engine_version_matches"] is True
    assert report["collections_only_on_server"] == []
    for name in ("DocumentChunk", "PDFDocument"):
        c = report["collections"][name]
        assert c["on_server"] is True
        assert c["properties_in_code"] == c["properties_on_server"]
        for key in ("properties_missing_on_server", "properties_only_on_server",
                    "data_type_differences", "vectorizer_skip_differences",
                    "vectorize_property_name_differences"):
            assert c[key] == [], key
        assert c["vectorizer"]["code"] == c["vectorizer"]["server"]
        assert c["multi_tenancy"] == {"code": True, "server": True}
    assert report["collections"]["DocumentChunk"]["embedding_model"] == {"code": MODEL, "server": MODEL}


def test_older_server_settings_are_reported_by_name():
    schema = _schema_from_code()
    chunk = next(c for c in schema["classes"] if c["class"] == "DocumentChunk")
    for prop in chunk["properties"]:
        if prop["name"] == "documentId":
            prop["moduleConfig"]["text2vec-openai"]["skip"] = False
        if prop["name"] == "content":
            prop["moduleConfig"]["text2vec-openai"]["vectorizePropertyName"] = True
    chunk["properties"] = [p for p in chunk["properties"] if p["name"] != "docItemProvenance"]
    chunk["properties"].append({"name": "autoCreated", "dataType": ["text"]})
    chunk["vectorIndexConfig"] = {"filterStrategy": "sweeping"}
    chunk["replicationConfig"] = {"deletionStrategy": "NoAutomatedResolution"}
    schema["classes"].append({"class": "Leftover", "properties": []})

    report = settings_report.build_report({"version": "1.40.7"}, schema, MODEL, "1.31.18")

    assert report["engine_version_matches"] is False
    assert report["collections_only_on_server"] == ["Leftover"]
    c = report["collections"]["DocumentChunk"]
    assert c["properties_missing_on_server"] == ["docItemProvenance"]
    assert c["properties_only_on_server"] == ["autoCreated"]
    assert c["vectorizer_skip_differences"] == ["documentId"]
    assert c["vectorize_property_name_differences"] == ["content"]
    assert c["filter_strategy_stored"] == "sweeping"
    assert c["deletion_strategy_stored"] == "NoAutomatedResolution"


def test_missing_collection_and_unset_expected_version():
    schema = {"classes": [c for c in _schema_from_code()["classes"] if c["class"] != "PDFDocument"]}

    report = settings_report.build_report({"version": "1.31.18"}, schema, MODEL, None)

    assert report["engine_version_matches"] is None
    assert report["collections"]["PDFDocument"] == {"on_server": False, "properties_in_code": 10}


def test_report_carries_no_property_values():
    report = settings_report.build_report({"version": "1.31.18"}, _schema_from_code(), MODEL, "1.31.18")
    text = json.dumps(report)

    # Only setting names, counts, versions, the vectorizer and the model name appear.
    assert "tenant" not in text.lower()
    assert "objects" not in text.lower()
