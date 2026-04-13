"""Unit tests for the Amazon Bedrock reranker helper."""

import logging
from types import SimpleNamespace

import pytest

import src.lib.bedrock_reranker as bedrock_reranker


def test_rerank_chunks_returns_input_when_provider_disabled(monkeypatch):
    monkeypatch.setenv("RERANK_PROVIDER", "none")

    chunks = [{"id": "chunk-1", "score": 0.2}]

    assert bedrock_reranker.rerank_chunks("query", chunks) == chunks


def test_rerank_chunks_uses_profile_and_reorders_results(monkeypatch):
    captured = {}

    class _Client:
        def rerank(self, **kwargs):
            captured["kwargs"] = kwargs
            return {
                "results": [
                    {"index": 1, "relevanceScore": 0.91},
                    {"index": 0, "relevanceScore": 0.37},
                ]
            }

    class _Session:
        def __init__(self, profile_name=None, region_name=None):
            captured["profile_name"] = profile_name
            captured["region_name"] = region_name

        def client(self, service_name, region_name=None):
            captured["service_name"] = service_name
            captured["client_region_name"] = region_name
            return _Client()

    monkeypatch.setenv("RERANK_PROVIDER", "bedrock_cohere")
    monkeypatch.setenv("AWS_PROFILE", "ctabone")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(bedrock_reranker.boto3, "Session", _Session)

    ranked = bedrock_reranker.rerank_chunks(
        "gene expression in retina",
        [
            {
                "id": "chunk-1",
                "score": 0.12,
                "metadata": {"section_title": "Methods"},
                "_rerank_text": "Methods section text",
            },
            {
                "id": "chunk-2",
                "score": 0.55,
                "metadata": {"section_title": "Results"},
                "_rerank_text": "Results section text",
            },
        ],
        top_n=2,
    )

    assert [chunk["id"] for chunk in ranked] == ["chunk-2", "chunk-1"]
    assert ranked[0]["score"] == 0.91
    assert ranked[0]["metadata"]["retrieval_score"] == 0.55
    assert ranked[0]["metadata"]["rerank_score"] == 0.91
    assert "_rerank_text" not in ranked[0]
    assert captured["profile_name"] == "ctabone"
    assert captured["service_name"] == "bedrock-agent-runtime"
    assert (
        captured["kwargs"]["rerankingConfiguration"]["bedrockRerankingConfiguration"][
            "modelConfiguration"
        ]["modelArn"]
        == bedrock_reranker.DEFAULT_BEDROCK_RERANK_MODEL_ARN
    )
    assert (
        captured["kwargs"]["sources"][0]["inlineDocumentSource"]["textDocument"]["text"]
        == "Methods section text"
    )


def test_rerank_chunks_preserves_original_order_when_bedrock_errors(monkeypatch):
    class _Session:
        def __init__(self, profile_name=None, region_name=None):
            pass

        def client(self, service_name, region_name=None):
            raise RuntimeError("bedrock unavailable")

    monkeypatch.setenv("RERANK_PROVIDER", "bedrock_cohere")
    monkeypatch.setattr(bedrock_reranker.boto3, "Session", _Session)

    chunks = [
        {"id": "chunk-1", "score": 0.8},
        {"id": "chunk-2", "score": 0.3},
    ]

    assert bedrock_reranker.rerank_chunks("query", chunks, top_n=2) == chunks


def test_rerank_chunks_preserves_original_order_when_bedrock_returns_no_results(monkeypatch):
    class _Client:
        def rerank(self, **kwargs):
            return {"results": []}

    class _Session:
        def __init__(self, profile_name=None, region_name=None):
            pass

        def client(self, service_name, region_name=None):
            return _Client()

    monkeypatch.setenv("RERANK_PROVIDER", "bedrock_cohere")
    monkeypatch.setattr(bedrock_reranker.boto3, "Session", _Session)

    chunks = [
        {"id": "chunk-1", "score": 0.8},
        {"id": "chunk-2", "score": 0.3},
    ]

    assert bedrock_reranker.rerank_chunks("query", chunks, top_n=2) == chunks


def test_rerank_chunks_logs_request_and_completion_details(monkeypatch, caplog):
    class _Client:
        def rerank(self, **kwargs):
            return {
                "results": [
                    {"index": 1, "relevanceScore": 0.91},
                    {"index": 0, "relevanceScore": 0.37},
                ]
            }

    class _Session:
        def __init__(self, profile_name=None, region_name=None):
            pass

        def client(self, service_name, region_name=None):
            return _Client()

    monkeypatch.setenv("RERANK_PROVIDER", "bedrock_cohere")
    monkeypatch.setenv(
        "BEDROCK_RERANK_MODEL_ARN",
        "arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0",
    )
    monkeypatch.setattr(bedrock_reranker.boto3, "Session", _Session)

    with caplog.at_level(logging.INFO):
        bedrock_reranker.rerank_chunks(
            "gene expression in retina",
            [
                {"id": "chunk-1", "score": 0.12, "_rerank_text": "Methods section text"},
                {"id": "chunk-2", "score": 0.55, "_rerank_text": "Results section text"},
            ],
            top_n=2,
        )

    assert "Bedrock rerank request: provider=bedrock_cohere" in caplog.text
    assert "model_arn=arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0" in caplog.text
    assert "candidates=2" in caplog.text
    assert "requested_results=2" in caplog.text
    assert "Bedrock rerank complete:" in caplog.text
    assert "reordered_positions=2" in caplog.text
    assert "top_rerank_score=0.91" in caplog.text
