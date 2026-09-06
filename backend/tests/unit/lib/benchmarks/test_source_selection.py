"""Initial discovery/preparation preserves resolver authority and exact bytes."""

import asyncio
import hashlib
from datetime import datetime, timezone
from uuid import uuid4
from typing import Annotated, cast

import pytest
from pydantic import Field, RootModel, ValidationError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.lib.benchmarks.input_resolvers import (
    BenchmarkInputResolverCatalog, BenchmarkSourceError, BenchmarkSourceRequestContext,
    DelegatedAuthorizationCapability, DelegatedSourceAuthorization, MaterializedBenchmarkInput,
)
from src.schemas.benchmark_sources import (
    BenchmarkSourceArtifactChoice,
    BenchmarkSourcePaperChoice,
    BenchmarkSourceDiscoveryPage, BenchmarkSourceDiscoveryRequest,
    BenchmarkSourcePreparationRequest,
)


CONTENT = '[{"text":"Synthetic α paper\\r\\n"}]\r\n'


class Resolver:
    resolver_id = "test_source"
    reference_schema = RootModel[str]
    delegated_authorization = DelegatedAuthorizationCapability.REQUIRED

    def __init__(self):
        self.calls = []
        self.change = {}

    async def materialize(self, *args, **kwargs):
        pytest.fail("Initial preparation must not fake a pinned materialization")

    async def discover(self, selection, *, max_choices, request_context):
        self.calls.append((selection, request_context, max_choices))
        if selection.query:
            return BenchmarkSourceDiscoveryPage(choices=(
                BenchmarkSourcePaperChoice(label="Synthetic paper", locator="paper-1"),
            ))
        return BenchmarkSourceDiscoveryPage(choices=(
            BenchmarkSourceArtifactChoice(label="Text", reference="artifact-1"),
            BenchmarkSourceArtifactChoice(label="PDF", unavailable_reason="No converted text"),
        ))

    async def prepare(self, reference, *, max_bytes, request_context):
        self.calls.append((reference, request_context, max_bytes))
        digest = "sha256:" + hashlib.sha256(CONTENT.encode()).hexdigest()
        source = {
            "resolver": self.resolver_id, "reference": reference, "version": "v1",
            "digest": digest, "content": CONTENT,
            "metadata": {"content_type": "application/json", "content_bytes": len(CONTENT.encode())},
            "provenance": {"resolver": self.resolver_id, "reference": reference,
                           "version": "v1", "digest": digest},
        }
        source.update(self.change)
        return MaterializedBenchmarkInput.model_validate(source)


def setup(resolver=None, **kwargs):
    resolver = resolver or Resolver()
    catalog = BenchmarkInputResolverCatalog([resolver], timeout_seconds=1,
                                            max_input_bytes=kwargs.get("max_bytes", 4096))
    context = BenchmarkSourceRequestContext("human", DelegatedSourceAuthorization("synthetic"))
    return resolver, catalog, context


@pytest.mark.asyncio
async def test_discovery_navigates_paper_then_artifact_without_preparation(monkeypatch):
    monkeypatch.setenv("BENCHMARK_SOURCE_DISCOVERY_MAX_CHOICES", "2")
    resolver, catalog, context = setup()
    page = await catalog.discover(BenchmarkSourceDiscoveryRequest(resolver="test_source", query="paper"),
                                  request_context=context)
    assert page.choices[0].kind == "paper"
    page = await catalog.discover(BenchmarkSourceDiscoveryRequest(resolver="test_source", locator=page.choices[0].locator, cursor="page-2"),
                                  request_context=context)
    first, second = page.choices
    assert isinstance(first, BenchmarkSourceArtifactChoice) and first.reference == "artifact-1"
    assert isinstance(second, BenchmarkSourceArtifactChoice) and second.reference is None
    assert all(call[1] is context and call[2] == 2 for call in resolver.calls)
    assert resolver.calls[1][0].cursor == "page-2"


@pytest.mark.asyncio
async def test_prepare_derives_and_verifies_exact_returned_pins():
    resolver, catalog, context = setup()
    source = await catalog.prepare(BenchmarkSourcePreparationRequest(resolver="test_source", reference="artifact-1"),
                                   request_context=context)
    assert source.content == CONTENT
    assert source.digest == "sha256:" + hashlib.sha256(CONTENT.encode()).hexdigest()
    assert source.provenance.reference == "artifact-1"
    assert resolver.calls == [("artifact-1", context, 4096)]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"resolver": "other"}, {"reference": "other"}, {"version": "other"},
    {"digest": "sha256:" + "0" * 64}, {"content": "different"},
    {"metadata": {"content_type": "application/json", "content_bytes": 1}},
])
async def test_prepare_rejects_inconsistent_result(change):
    resolver, catalog, context = setup()
    resolver.change = change
    with pytest.raises(BenchmarkSourceError, match="inconsistent"):
        await catalog.prepare(BenchmarkSourcePreparationRequest(resolver="test_source", reference="artifact-1"),
                              request_context=context)


@pytest.mark.asyncio
async def test_prepare_bounds_bytes():
    _, catalog, context = setup(max_bytes=1)
    with pytest.raises(BenchmarkSourceError) as caught:
        await catalog.prepare(BenchmarkSourcePreparationRequest(resolver="test_source", reference="artifact-1"),
                              request_context=context)
    assert caught.value.code == "oversize_payload"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["discover", "prepare"])
async def test_missing_credential_denied_before_adapter(operation):
    resolver, catalog, _ = setup()
    selection = (BenchmarkSourceDiscoveryRequest(resolver="test_source", query="paper") if operation == "discover"
                 else BenchmarkSourcePreparationRequest(resolver="test_source", reference="artifact-1"))
    with pytest.raises(BenchmarkSourceError) as caught:
        await getattr(catalog, operation)(selection, request_context=BenchmarkSourceRequestContext("human"))
    assert caught.value.code == "missing_delegated_authorization"
    assert not resolver.calls


def test_discovery_request_and_response_limits(monkeypatch):
    monkeypatch.setenv("BENCHMARK_SOURCE_SELECTION_MAX_BYTES", "4")
    with pytest.raises(ValidationError):
        BenchmarkSourceDiscoveryRequest(resolver="test", query="ααα")
    monkeypatch.setenv("BENCHMARK_SOURCE_SELECTION_MAX_BYTES", "4096")
    with pytest.raises(ValidationError):
        BenchmarkSourceDiscoveryRequest(resolver="test", query="q", locator="p")
    monkeypatch.setenv("BENCHMARK_SOURCE_DISCOVERY_MAX_CHOICES", "1")
    with pytest.raises(ValidationError):
        BenchmarkSourceDiscoveryPage(choices=(BenchmarkSourcePaperChoice(label="p", locator="p"),) * 2)


@pytest.mark.asyncio
async def test_adapter_exception_is_sanitized_without_context():
    class Broken(Resolver):
        async def prepare(self, *args, **kwargs):
            raise BenchmarkSourceError("forbidden_source", "private response and synthetic credential")
    _, catalog, context = setup(Broken())
    with pytest.raises(BenchmarkSourceError) as caught:
        await catalog.prepare(BenchmarkSourcePreparationRequest(resolver="test_source", reference="artifact-1"),
                              request_context=context)
    assert caught.value.code == "forbidden_source"
    assert caught.value.__context__ is None
    assert "private response" not in str(caught.value)


def test_http_discovery_and_preparation_keep_scope_and_original_provenance(monkeypatch):
    from src.api import benchmark_sources as api
    from src.api.benchmark_auth import require_benchmark_source_read

    resolver, catalog, _ = setup()
    captured = []

    def freeze(source, owner, service):
        captured.append((source, owner, service))
        return {
            "snapshot_id": uuid4(), "digest": source.digest,
            "source_version": source.version, "content_type": source.metadata.content_type,
            "content_bytes": source.metadata.content_bytes, "resolver_id": source.resolver,
            "source_reference": source.reference,
            "sanitized_provenance": source.provenance.model_dump(), "owner_subject": owner,
            "service_principal": service, "blob_reference": "sha256/synthetic",
            "created_at": datetime.now(timezone.utc),
        }

    monkeypatch.setattr(api, "get_benchmark_enabled", lambda: True)
    monkeypatch.setattr(api, "_freeze_prepared_source", freeze)
    app = FastAPI()
    app.state.benchmark_input_resolvers = catalog
    app.dependency_overrides[require_benchmark_source_read] = lambda: {"sub": "human", "client_id": "portal"}
    app.include_router(api.router)
    with TestClient(app) as client:
        headers = {"X-Benchmark-Delegated-Source-Authorization": "Bearer synthetic"}
        response = client.post("/api/v1/benchmarks/sources/discover", headers=headers,
                               json={"resolver": "test_source", "query": "paper"})
        assert response.status_code == 200, response.text
        assert response.json()["choices"][0]["locator"] == "paper-1"
        assert not captured
        response = client.post("/api/v1/benchmarks/sources/prepare", headers=headers,
                               json={"resolver": "test_source", "reference": "artifact-1"})
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["resolver_id"] == "test_source"
        assert captured[0][1:] == ("human", "portal")
        assert captured[0][0].content == CONTENT
        assert len(resolver.calls) == 2
        for path, payload in [("discover", {"query": "paper"}), ("prepare", {"reference": "artifact-1"})]:
            denied = client.post("/api/v1/benchmarks/sources/" + path, json={"resolver": "test_source", **payload})
            assert denied.status_code == 401
        invalid = client.post("/api/v1/benchmarks/sources/prepare", headers=headers,
                              json={"resolver": "test_source", "reference": "artifact-1", "digest": "fake"})
        assert invalid.status_code == 422 and "fake" not in invalid.text
        assert len(resolver.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["discover", "prepare"])
async def test_registered_resolver_without_optional_capability_is_rejected(operation):
    class Legacy:
        resolver_id = "test_source"
        reference_schema = RootModel[str]
        delegated_authorization = DelegatedAuthorizationCapability.UNSUPPORTED

        async def materialize(self, *args, **kwargs):
            pytest.fail("No fallback materialization")

    _, catalog, _ = setup(Legacy())
    selection = (BenchmarkSourceDiscoveryRequest(resolver="test_source", query="paper") if operation == "discover"
                 else BenchmarkSourcePreparationRequest(resolver="test_source", reference="artifact-1"))
    with pytest.raises(BenchmarkSourceError) as caught:
        await getattr(catalog, operation)(selection, request_context=BenchmarkSourceRequestContext("human"))
    assert caught.value.code == "unsupported_operation"


@pytest.mark.parametrize("reference", ["https://untrusted.invalid/document", "x" * 1025])
def test_opaque_reference_constraints_are_shared_before_io(reference):
    from src.lib.benchmarks.models import BenchmarkInputReference

    with pytest.raises(ValidationError):
        BenchmarkSourcePreparationRequest(resolver="test_source", reference=reference)
    with pytest.raises(ValidationError):
        BenchmarkSourceArtifactChoice(label="Text", reference=reference)
    with pytest.raises(ValidationError):
        BenchmarkInputReference(resolver="test_source", reference=reference,
                                version="v1", digest="sha256:" + "0" * 64)


@pytest.mark.asyncio
async def test_reference_validation_error_does_not_keep_private_input_in_chain():
    class Restricted(Resolver):
        reference_schema = RootModel[Annotated[str, Field(pattern="^artifact-[0-9]+$")]]

    resolver, catalog, context = setup(Restricted())
    with pytest.raises(BenchmarkSourceError) as caught:
        await catalog.prepare(BenchmarkSourcePreparationRequest(resolver="test_source", reference="private-bad-value"),
                              request_context=context)
    assert caught.value.code == "invalid_reference"
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert not resolver.calls


@pytest.mark.asyncio
async def test_unknown_resolver_never_reaches_registered_adapter():
    resolver, catalog, context = setup()
    with pytest.raises(BenchmarkSourceError) as caught:
        await catalog.prepare(BenchmarkSourcePreparationRequest(resolver="missing", reference="artifact-1"),
                              request_context=context)
    assert caught.value.code == "unknown_resolver" and not resolver.calls


@pytest.mark.asyncio
async def test_optional_operation_timeout_cancels_adapter():
    canceled = []

    class Slow(Resolver):
        async def prepare(self, *args, **kwargs):
            try:
                await asyncio.sleep(10)
                return await super().prepare(*args, **kwargs)
            finally:
                canceled.append(True)

    resolver = Slow()
    catalog = BenchmarkInputResolverCatalog([resolver], timeout_seconds=0.001, max_input_bytes=4096)
    _, _, context = setup()
    with pytest.raises(BenchmarkSourceError) as caught:
        await catalog.prepare(BenchmarkSourcePreparationRequest(resolver="test_source", reference="artifact-1"),
                              request_context=context)
    assert caught.value.code == "source_unavailable" and canceled == [True]
    assert caught.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("content,media", [("not-json", "application/json"), ("<p>paper</p>", "text/html"), ("\ud800", "text/plain")])
async def test_prepared_document_must_be_supported_utf8(content, media):
    class Malformed(Resolver):
        async def prepare(self, *args, **kwargs):
            source = await super().prepare(*args, **kwargs)
            # Surrogate cannot encode; use a constructed trusted-adapter result
            # to verify the catalog, rather than only Pydantic construction.
            encoded = content.encode("utf-8", errors="replace")
            digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
            return source.model_copy(update={
                "content": content, "digest": digest,
                "metadata": source.metadata.model_copy(update={"content_type": media, "content_bytes": len(encoded)}),
                "provenance": source.provenance.model_copy(update={"digest": digest}),
            })

    _, catalog, context = setup(Malformed())
    with pytest.raises(BenchmarkSourceError) as caught:
        await catalog.prepare(BenchmarkSourcePreparationRequest(resolver="test_source", reference="artifact-1"),
                              request_context=context)
    assert caught.value.code == "source_unavailable"


def test_prepare_commit_failure_rolls_back_and_reports_only_safe_error(monkeypatch):
    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError
    from src.api import benchmark_sources as api
    from src.lib import http_errors

    events = []
    captured = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, kind, value, tb):
            events.append(("rollback_on_close", kind))

        def commit(self):
            raise IntegrityError("private SQL", {"credential": "private"}, Exception("private"))

    class Repository:
        def __init__(self, *args):
            pass

        def freeze_input(self, *args, **kwargs):
            return object()

        def receipt(self, snapshot):
            return object()

    monkeypatch.setattr(api, "SessionLocal", Session)
    monkeypatch.setattr(api, "BenchmarkSnapshotRepository", Repository)
    monkeypatch.setattr(api, "configured_benchmark_snapshot_store", lambda: object())
    monkeypatch.setattr(http_errors, "report_runtime_exception", lambda error, **kwargs: captured.append(error))
    with pytest.raises(HTTPException) as caught:
        api._freeze_prepared_source(cast(MaterializedBenchmarkInput, object()), "owner", "service")
    assert caught.value.status_code == 503
    assert events == [("rollback_on_close", IntegrityError)]
    assert "private" not in str(caught.value)
    assert captured and "private" not in str(captured[0])
    assert captured[0].__context__ is None
