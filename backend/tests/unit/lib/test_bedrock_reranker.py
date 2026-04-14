"""Unit tests for the Amazon Bedrock reranker helper."""

import json
import logging
import urllib.error
from typing import Any

import pytest

import src.lib.bedrock_reranker as bedrock_reranker


@pytest.mark.parametrize("provider", ["", "none"])
def test_rerank_chunks_returns_input_when_provider_disabled(monkeypatch, provider):
    monkeypatch.setenv("RERANK_PROVIDER", provider)

    chunks = [{"id": "chunk-1", "score": 0.2}]

    assert bedrock_reranker.rerank_chunks("query", chunks) == chunks


def test_rerank_chunks_rejects_unsupported_provider(monkeypatch):
    chunks = [{"id": "chunk-1", "score": 0.8}]
    monkeypatch.setenv("RERANK_PROVIDER", "unsupported")

    with pytest.raises(
        RuntimeError, match="Unsupported RERANK_PROVIDER=unsupported"
    ):
        bedrock_reranker.rerank_chunks("query", chunks)


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


def test_rerank_chunks_preserves_original_order_when_bedrock_returns_no_results(
    monkeypatch,
    caplog,
):
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

    with caplog.at_level(logging.WARNING, logger=bedrock_reranker.logger.name):
        assert bedrock_reranker.rerank_chunks("query", chunks, top_n=2) == chunks

    assert "rerank no results provider=bedrock_cohere" not in caplog.text


def test_log_rerank_no_results_prefers_bedrock_message(caplog):
    with caplog.at_level(logging.WARNING):
        bedrock_reranker._log_rerank_no_results("bedrock_cohere", "query")

    assert (
        "Bedrock reranking returned no results; preserving original retrieval order for "
        "query='query'" in caplog.text
    )
    assert "rerank no results provider=bedrock_cohere" not in caplog.text


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

    assert "rerank request provider=bedrock_cohere" in caplog.text
    assert "rerank complete provider=bedrock_cohere" in caplog.text
    assert "Bedrock rerank request: provider=bedrock_cohere" in caplog.text
    assert (
        "model_arn=arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0"
        in caplog.text
    )
    assert "candidates=2" in caplog.text
    assert "requested_results=2" in caplog.text
    assert "Bedrock rerank complete:" in caplog.text
    assert "reordered_positions=2" in caplog.text
    assert "top_rerank_score=0.91" in caplog.text


def test_rerank_chunks_calls_local_transformers_and_preserves_top_n(monkeypatch):
    observed = {}

    class _FakeResponse:
        def __init__(self, body: str):
            self._body = body.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self._body

    class _URLLib:
        @staticmethod
        def urlopen(req: Any, timeout: int):
            observed["url"] = req.full_url
            observed["method"] = req.get_method()
            observed["payload"] = json.loads(req.data.decode("utf-8"))
            return _FakeResponse(
                json.dumps(
                    {
                        "query": "gene expression in retina",
                        "scores": [
                            {"document": "Methods section text", "score": 0.37},
                            {"document": "Results section text", "score": 0.91},
                            {"document": "Third section text", "score": 0.12},
                        ],
                    }
                )
            )

    chunks = [
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
        {
            "id": "chunk-3",
            "score": 0.03,
            "metadata": {"section_title": "Intro"},
            "_rerank_text": "Third section text",
        },
    ]

    monkeypatch.setenv("RERANK_PROVIDER", "local_transformers")
    monkeypatch.setenv("RERANKER_URL", "http://reranker-transformers:9000")
    monkeypatch.setattr(bedrock_reranker.request, "urlopen", _URLLib.urlopen)

    ranked = bedrock_reranker.rerank_chunks(
        "gene expression in retina",
        chunks,
        top_n=2,
    )

    assert [chunk["id"] for chunk in ranked] == ["chunk-2", "chunk-1", "chunk-3"]
    assert ranked[0]["metadata"]["rerank_score"] == 0.91
    assert ranked[0]["metadata"]["retrieval_score"] == 0.55
    assert "_rerank_text" not in ranked[0]
    assert observed["url"] == "http://reranker-transformers:9000/rerank"
    assert observed["method"] == "POST"
    assert observed["payload"]["query"] == "gene expression in retina"
    assert observed["payload"]["documents"][1] == "Results section text"


def test_rerank_chunks_returns_original_chunks_on_local_transformers_empty_result(
    monkeypatch,
    caplog,
):
    class _FakeResponse:
        def __init__(self, body: str):
            self._body = body.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self._body

    def _urlopen(request: Any, timeout: int):
        return _FakeResponse(json.dumps({"query": "query", "scores": []}))

    chunks = [{"id": "chunk-1", "score": 0.8}, {"id": "chunk-2", "score": 0.3}]

    monkeypatch.setenv("RERANK_PROVIDER", "local_transformers")
    monkeypatch.setattr(bedrock_reranker.request, "urlopen", _urlopen)

    with caplog.at_level(logging.WARNING, logger=bedrock_reranker.logger.name):
        assert bedrock_reranker.rerank_chunks("query", chunks, top_n=2) == chunks

    assert "rerank no results provider=local_transformers" in caplog.text


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: urllib.error.URLError("service unavailable"),
        lambda: TimeoutError("timed out"),
    ],
)
def test_rerank_chunks_returns_original_chunks_on_local_transformers_errors(
    monkeypatch,
    error_factory,
):
    def _urlopen(request: Any, timeout: int):
        raise error_factory()

    chunks = [{"id": "chunk-1", "score": 0.8}, {"id": "chunk-2", "score": 0.3}]

    monkeypatch.setenv("RERANK_PROVIDER", "local_transformers")
    monkeypatch.setattr(bedrock_reranker.request, "urlopen", _urlopen)

    assert bedrock_reranker.rerank_chunks("query", chunks, top_n=2) == chunks


def test_rerank_chunks_logs_provider_neutral_local_transformers_lines(
    monkeypatch,
    caplog,
):
    class _FakeResponse:
        def __init__(self, body: str):
            self._body = body.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self._body

    def _urlopen(req: Any, timeout: int):
        return _FakeResponse(
            json.dumps(
                {
                    "query": "query",
                    "scores": [
                        {"document": "Chunk B", "score": 0.91},
                        {"document": "Chunk A", "score": 0.37},
                    ],
                }
            )
        )

    monkeypatch.setenv("RERANK_PROVIDER", "local_transformers")
    monkeypatch.setattr(bedrock_reranker.request, "urlopen", _urlopen)

    with caplog.at_level(logging.INFO):
        bedrock_reranker.rerank_chunks(
            "query",
            [
                {"id": "chunk-1", "score": 0.12, "_rerank_text": "Chunk A"},
                {"id": "chunk-2", "score": 0.55, "_rerank_text": "Chunk B"},
            ],
            top_n=2,
        )

    assert "rerank request provider=local_transformers" in caplog.text
    assert "rerank complete provider=local_transformers" in caplog.text


def test_rerank_chunks_returns_original_chunks_on_local_transformers_missing_scores(monkeypatch):
    class _FakeResponse:
        def __init__(self, body: str):
            self._body = body.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self._body

    def _urlopen(req: Any, timeout: int):
        return _FakeResponse(json.dumps({"query": "query"}))

    chunks = [{"id": "chunk-1", "score": 0.8}, {"id": "chunk-2", "score": 0.3}]

    monkeypatch.setenv("RERANK_PROVIDER", "local_transformers")
    monkeypatch.setattr(bedrock_reranker.request, "urlopen", _urlopen)

    assert bedrock_reranker.rerank_chunks("query", chunks, top_n=2) == chunks
