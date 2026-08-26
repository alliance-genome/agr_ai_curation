"""Contract tests for the Alliance typed existing-GO annotation adapter."""

from __future__ import annotations

import json
from pathlib import Path
import requests

from agr_ai_curation_alliance.tools.go_annotations import (
    lookup_existing_go_annotations,
)

REPO_ROOT = Path(__file__).resolve().parents[6]
RGD_FIXTURE = (
    REPO_ROOT / "backend/tests/fixtures/alliance/go_annotations/rgd_620474_go_api.json"
)
QUICKGO_RGD_FIXTURE = (
    REPO_ROOT
    / "backend/tests/fixtures/alliance/go_annotations/quickgo_rgd_620474_rejection.json"
)


class _Response:
    def __init__(self, status_code: int, payload: object):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_recorded_quickgo_contract_does_not_claim_direct_rgd_support():
    fixture = json.loads(QUICKGO_RGD_FIXTURE.read_text(encoding="utf-8"))

    assert fixture["fixture_metadata"]["http_status"] == 400
    assert fixture["response"]["messages"] == [
        "The 'Gene Product ID' parameter contains invalid values: RGD:620474"
    ]


def test_recorded_rgd_contract_preserves_with_from_and_provenance(monkeypatch):
    monkeypatch.delenv("GO_ANNOTATIONS_REQUEST_TIMEOUT_SECONDS", raising=False)
    payload = json.loads(RGD_FIXTURE.read_text(encoding="utf-8"))
    calls: list[tuple[str, dict[str, object]]] = []

    def requester(url: str, **kwargs):
        calls.append((url, kwargs))
        return _Response(200, payload)

    result = lookup_existing_go_annotations("RGD:620474", requester=requester)

    assert result.status == "ok"
    assert result.gene_id == "RGD:620474"
    assert result.gene_symbol == "Sox9"
    assert calls == [
        (
            "https://api.geneontology.org/api/bioentity/gene/RGD:620474/function",
            {"headers": {"Accept": "application/json"}, "timeout": 30.0},
        )
    ]
    first = result.annotations[0]
    assert first.gene_product_id == "RGD:620474"
    assert first.go_id == "GO:0000122"
    assert first.aspect == "BP"
    assert first.evidence_code == "ISO"
    assert first.eco_id == "ECO:0000266"
    assert first.references == ["GO_REF:0000121"]
    assert first.with_from == ["UniProtKB:P48436"]
    assert first.providers == ["RGD"]
    assert first.product_type == "protein"
    assert first.provenance.source == "Gene Ontology Consortium API"
    assert first.provenance.source_url == calls[0][0]
    assert first.provenance.source_record_id == payload["associations"][0]["id"]


def test_relation_qualifier_and_negation_are_retained_without_source_parsing():
    payload = json.loads(RGD_FIXTURE.read_text(encoding="utf-8"))
    association = payload["associations"][0]
    association["relation"] = {"id": "RO:0002327", "label": "enables"}
    association["qualifiers"] = ["contributes_to"]
    association["negated"] = True

    result = lookup_existing_go_annotations(
        "RGD:620474", requester=lambda *_args, **_kwargs: _Response(200, payload)
    )

    annotation = result.annotations[0]
    assert annotation.relation is not None
    assert annotation.relation.model_dump() == {
        "id": "RO:0002327",
        "label": "enables",
    }
    assert annotation.qualifiers == ["contributes_to"]
    assert annotation.negated is True


def test_invalid_and_unsupported_identifiers_never_dispatch_network_requests():
    calls = 0

    def requester(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("network dispatch must not occur")

    malformed = lookup_existing_go_annotations(
        "RGD:620474/../../annotations", requester=requester
    )
    wrong_local_id = lookup_existing_go_annotations(
        "RGD:not-a-number", requester=requester
    )
    unsupported = lookup_existing_go_annotations(
        "ENSEMBL:ENSRNOG00000000001", requester=requester
    )

    assert malformed.status == "invalid_input"
    assert wrong_local_id.status == "invalid_input"
    assert unsupported.status == "unsupported_identifier"
    assert calls == 0


def test_empty_404_and_upstream_failures_have_bounded_statuses():
    empty = lookup_existing_go_annotations(
        "RGD:620474",
        requester=lambda *_args, **_kwargs: _Response(200, {"associations": []}),
    )
    missing = lookup_existing_go_annotations(
        "RGD:620474",
        requester=lambda *_args, **_kwargs: _Response(404, {}),
    )
    failed = lookup_existing_go_annotations(
        "RGD:620474",
        requester=lambda *_args, **_kwargs: _Response(503, {}),
    )

    def connection_error(*_args, **_kwargs):
        raise requests.ConnectionError("offline")

    unreachable = lookup_existing_go_annotations(
        "RGD:620474", requester=connection_error
    )

    assert empty.status == "not_found"
    assert missing.status == "not_found"
    assert failed.status == "upstream_error"
    assert unreachable.status == "upstream_error"


def test_supported_non_rgd_mapped_gene_product_identifiers_remain_functional():
    payload = json.loads(RGD_FIXTURE.read_text(encoding="utf-8"))
    payload["associations"] = [payload["associations"][0]]
    cases = (
        ("WB:WBGene00000898", "WB:WBGene00000898", "daf-2"),
        ("FB:FBgn0000490", "FlyBase:FBgn0000490", "dpp"),
        ("HGNC:11998", "UniProtKB:P04637", "TP53"),
    )
    for gene_id, source_product_id, symbol in cases:
        payload["associations"][0]["subject"] = {
            "id": source_product_id,
            "label": symbol,
        }
        result = lookup_existing_go_annotations(
            gene_id,
            requester=lambda *_args, **_kwargs: _Response(200, payload),
        )

        assert result.status == "ok"
        assert result.gene_id == gene_id
        assert result.gene_symbol == symbol
        assert result.annotations[0].gene_product_id == source_product_id


def test_invalid_upstream_contract_is_not_silently_degraded():
    result = lookup_existing_go_annotations(
        "RGD:620474",
        requester=lambda *_args, **_kwargs: _Response(
            200, {"associations": [{"subject": {"id": "RGD:620474"}}]}
        ),
    )

    assert result.status == "upstream_error"
    assert result.annotations == []


def test_malformed_optional_subject_label_returns_upstream_error():
    payload = json.loads(RGD_FIXTURE.read_text(encoding="utf-8"))
    payload["associations"] = [payload["associations"][0]]
    payload["associations"][0]["subject"]["label"] = {"unexpected": "object"}

    result = lookup_existing_go_annotations(
        "RGD:620474",
        requester=lambda *_args, **_kwargs: _Response(200, payload),
    )

    assert result.status == "upstream_error"
    assert result.annotations == []
    assert "subject.label" in (result.message or "")
