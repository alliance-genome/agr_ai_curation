"""Resolver catalog, integrity, bounds, and local-document policy tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, cast

import pytest
from pydantic import Field, RootModel, ValidationError

from src.lib.benchmarks.input_resolvers import (
    BenchmarkInputResolverCatalog,
    BenchmarkResolverRegistrationError,
    BenchmarkSourceError,
    BenchmarkSourceMetadata,
    BenchmarkSourceProvenance,
    CheckedInFixtureResolver,
    MaterializedBenchmarkInput,
    materialize_plan_inputs,
)
from src.lib.benchmarks.models import BenchmarkInputReference, ResolvedBenchmarkPlan
from src.lib.benchmarks.suites import load_checked_in_suites
from src.services.benchmark_document_source import (
    LocalDocumentResolver,
    LocalDocumentSourceRecord,
)


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


REPOSITORY_ROOT = Path(__file__).resolve().parents[5]


def _reference(
    *,
    resolver: str,
    reference: str,
    version: str,
    digest: str,
) -> BenchmarkInputReference:
    return BenchmarkInputReference(
        resolver=resolver,
        reference=reference,
        version=version,
        digest=digest,
    )


def test_checked_in_fixture_materializes_digest_verified_immutable_content(tmp_path):
    payload = b'{"messages": [{"role": "user", "content": "fixture"}]}\n'
    fixture = tmp_path / "cases" / "case-1" / "input.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(payload)
    catalog = BenchmarkInputResolverCatalog(
        [CheckedInFixtureResolver(tmp_path)],
        timeout_seconds=1,
        max_input_bytes=1024,
    )

    result = asyncio.run(
        catalog.materialize(
            _reference(
                resolver="checked_in_fixture",
                reference="cases/case-1/input.json",
                version="1",
                digest=_digest(payload),
            ),
            principal_subject="operator",
        )
    )

    assert result.content == payload.decode("utf-8")
    assert result.metadata.content_bytes == len(payload)
    assert result.provenance.digest == _digest(payload)
    with pytest.raises(ValidationError):
        result.content = "changed"


def test_shipped_suite_fixture_references_materialize_with_checked_in_receipts():
    benchmark_root = REPOSITORY_ROOT / "packages" / "alliance" / "benchmarks"
    catalog = BenchmarkInputResolverCatalog(
        [CheckedInFixtureResolver(benchmark_root)],
        timeout_seconds=1,
        max_input_bytes=1024 * 1024,
    )

    for suite in load_checked_in_suites(benchmark_root):
        for case in suite.cases:
            materialized = asyncio.run(
                catalog.materialize(case.input, principal_subject="operator")
            )
            assert materialized.reference == case.input.reference
            assert materialized.version == case.input.version
            assert materialized.digest == case.input.digest


@pytest.mark.parametrize(
    ("reference", "code"),
    [
        ("../secret.json", "invalid_reference"),
        ("python.module:resolver", "invalid_reference"),
    ],
)
def test_checked_in_fixture_rejects_non_allowlisted_reference_shapes(
    tmp_path, reference, code
):
    catalog = BenchmarkInputResolverCatalog(
        [CheckedInFixtureResolver(tmp_path)],
        timeout_seconds=1,
        max_input_bytes=1024,
    )
    with pytest.raises(BenchmarkSourceError, match="invalid") as exc_info:
        asyncio.run(
            catalog.materialize(
                _reference(
                    resolver="checked_in_fixture",
                    reference=reference,
                    version="1",
                    digest="sha256:" + "0" * 64,
                ),
                principal_subject="operator",
            )
        )
    assert exc_info.value.code == code


def test_common_input_schema_rejects_network_destinations():
    with pytest.raises(ValidationError, match="network URLs"):
        _reference(
            resolver="checked_in_fixture",
            reference="https://example.test/input.json",
            version="1",
            digest="sha256:" + "0" * 64,
        )


def test_catalog_rejects_unknown_and_duplicate_resolvers(tmp_path):
    resolver = CheckedInFixtureResolver(tmp_path)
    with pytest.raises(BenchmarkResolverRegistrationError, match="Duplicate") as exc_info:
        BenchmarkInputResolverCatalog(
            [resolver, resolver], timeout_seconds=1, max_input_bytes=1024
        )
    assert exc_info.value.code == "duplicate_resolver"

    catalog = BenchmarkInputResolverCatalog(
        [resolver], timeout_seconds=1, max_input_bytes=1024
    )
    with pytest.raises(BenchmarkSourceError) as exc_info:
        asyncio.run(
            catalog.materialize(
                _reference(
                    resolver="python_module",
                    reference="package.module:callable",
                    version="1",
                    digest="sha256:" + "0" * 64,
                ),
                principal_subject="operator",
            )
        )
    assert exc_info.value.code == "unknown_resolver"


def test_checked_in_fixture_rejects_stale_digest_and_oversize(tmp_path):
    payload = b'{"messages": []}\n'
    path = tmp_path / "input.json"
    path.write_bytes(payload)
    reference = _reference(
        resolver="checked_in_fixture",
        reference="input.json",
        version="1",
        digest="sha256:" + "0" * 64,
    )
    catalog = BenchmarkInputResolverCatalog(
        [CheckedInFixtureResolver(tmp_path)],
        timeout_seconds=1,
        max_input_bytes=1024,
    )
    with pytest.raises(BenchmarkSourceError) as exc_info:
        asyncio.run(catalog.materialize(reference, principal_subject="operator"))
    assert exc_info.value.code == "digest_conflict"

    bounded = BenchmarkInputResolverCatalog(
        [CheckedInFixtureResolver(tmp_path)],
        timeout_seconds=1,
        max_input_bytes=len(payload) - 1,
    )
    with pytest.raises(BenchmarkSourceError) as exc_info:
        asyncio.run(bounded.materialize(reference, principal_subject="operator"))
    assert exc_info.value.code == "oversize_payload"


class _StringReference(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class _VersionedResolver:
    resolver_id = "private_source"
    reference_schema = _StringReference

    async def materialize(
        self, reference, validated_reference, *, max_bytes, principal_subject
    ):
        del validated_reference, max_bytes, principal_subject
        payload = b"{}"
        digest = _digest(payload)
        provenance = BenchmarkSourceProvenance(
            resolver=self.resolver_id,
            reference=reference.reference,
            version="authoritative-v2",
            digest=digest,
        )
        return MaterializedBenchmarkInput(
            resolver=self.resolver_id,
            reference=reference.reference,
            version="authoritative-v2",
            digest=digest,
            content="{}",
            metadata=BenchmarkSourceMetadata(
                content_type="application/json", content_bytes=2
            ),
            provenance=provenance,
        )


def test_catalog_rejects_stale_authoritative_version():
    payload = b"{}"
    catalog = BenchmarkInputResolverCatalog(
        [_VersionedResolver()], timeout_seconds=1, max_input_bytes=10
    )
    with pytest.raises(BenchmarkSourceError) as exc_info:
        asyncio.run(
            catalog.materialize(
                _reference(
                    resolver="private_source",
                    reference="approved-id",
                    version="stale-v1",
                    digest=_digest(payload),
                ),
                principal_subject="operator",
            )
        )
    assert exc_info.value.code == "version_conflict"


def test_catalog_bounds_registered_resolver_timeout():
    class SlowResolver(_VersionedResolver):
        async def materialize(self, *args, **kwargs):
            await asyncio.sleep(0.05)
            return await super().materialize(*args, **kwargs)

    catalog = BenchmarkInputResolverCatalog(
        [SlowResolver()], timeout_seconds=0.001, max_input_bytes=100
    )
    with pytest.raises(BenchmarkSourceError) as exc_info:
        asyncio.run(
            catalog.materialize(
                _reference(
                    resolver="private_source",
                    reference="approved-id",
                    version="authoritative-v2",
                    digest=_digest(b"{}"),
                ),
                principal_subject="operator",
            )
        )
    assert exc_info.value.code == "source_unavailable"


def test_catalog_verifies_registered_resolver_content_receipt():
    class InconsistentResolver(_VersionedResolver):
        async def materialize(self, *args, **kwargs):
            result = await super().materialize(*args, **kwargs)
            return result.model_copy(update={"content": '{"changed":true}'})

    catalog = BenchmarkInputResolverCatalog(
        [InconsistentResolver()], timeout_seconds=1, max_input_bytes=100
    )
    with pytest.raises(BenchmarkSourceError) as exc_info:
        asyncio.run(
            catalog.materialize(
                _reference(
                    resolver="private_source",
                    reference="approved-id",
                    version="authoritative-v2",
                    digest=_digest(b"{}"),
                ),
                principal_subject="operator",
            )
        )
    assert exc_info.value.code == "source_unavailable"


def test_materialize_plan_inputs_resolves_every_case_before_handoff(tmp_path):
    first = b'{"messages": [{"content": "first"}]}\n'
    second = b'{"messages": [{"content": "second"}]}\n'
    (tmp_path / "first.json").write_bytes(first)
    (tmp_path / "second.json").write_bytes(second)
    catalog = BenchmarkInputResolverCatalog(
        [CheckedInFixtureResolver(tmp_path)],
        timeout_seconds=1,
        max_input_bytes=1024,
    )
    plan = SimpleNamespace(
        plan_digest="sha256:" + "a" * 64,
        cases=(
            SimpleNamespace(
                case_id="first",
                input=_reference(
                    resolver="checked_in_fixture",
                    reference="first.json",
                    version="1",
                    digest=_digest(first),
                ),
            ),
            SimpleNamespace(
                case_id="second",
                input=_reference(
                    resolver="checked_in_fixture",
                    reference="second.json",
                    version="1",
                    digest=_digest(second),
                ),
            ),
        ),
    )

    resolved = asyncio.run(
        materialize_plan_inputs(
            cast(ResolvedBenchmarkPlan, plan),
            catalog,
            principal_subject="operator",
        )
    )
    assert [case.case_id for case in resolved.cases] == ["first", "second"]
    assert [case.source.content for case in resolved.cases] == [
        first.decode(),
        second.decode(),
    ]


def test_local_document_resolver_enforces_owner_version_and_digest(tmp_path):
    document_id = "4b6ea638-9755-4d95-b523-e98b2493d8b1"
    owner_root = tmp_path / "owner-sub" / "processed_json"
    owner_root.mkdir(parents=True)
    content_path = owner_root / f"{document_id}.json"
    payload = json.dumps([{"text": "canonical extracted content"}], indent=2).encode()
    content_path.write_bytes(payload)

    def load(reference: str, subject: str) -> LocalDocumentSourceRecord:
        if subject != "owner-sub":
            raise BenchmarkSourceError(
                "forbidden_source", "Authenticated principal cannot access this document"
            )
        return LocalDocumentSourceRecord(
            reference=reference,
            version="2026-08-31T12:00:00+00:00",
            title="Example paper",
            content_path=f"owner-sub/processed_json/{document_id}.json",
        )

    catalog = BenchmarkInputResolverCatalog(
        [
            LocalDocumentResolver(
                storage_root_provider=lambda: Path(tmp_path), document_loader=load
            )
        ],
        timeout_seconds=1,
        max_input_bytes=1024,
    )
    reference = _reference(
        resolver="local_document",
        reference=document_id,
        version="2026-08-31T12:00:00+00:00",
        digest=_digest(payload),
    )

    result = asyncio.run(
        catalog.materialize(reference, principal_subject="owner-sub")
    )
    assert result.content == payload.decode()
    assert result.metadata.title == "Example paper"

    with pytest.raises(BenchmarkSourceError) as exc_info:
        asyncio.run(catalog.materialize(reference, principal_subject="other-sub"))
    assert exc_info.value.code == "forbidden_source"

    stale = reference.model_copy(update={"version": "stale"})
    with pytest.raises(BenchmarkSourceError) as exc_info:
        asyncio.run(catalog.materialize(stale, principal_subject="owner-sub"))
    assert exc_info.value.code == "version_conflict"


def test_local_document_resolver_requires_uuid_reference(tmp_path):
    def unexpected_loader(
        _reference: str, _subject: str
    ) -> LocalDocumentSourceRecord:
        raise AssertionError("invalid local reference must not reach document storage")

    catalog = BenchmarkInputResolverCatalog(
        [
            LocalDocumentResolver(
                storage_root_provider=lambda: Path(tmp_path),
                document_loader=unexpected_loader,
            )
        ],
        timeout_seconds=1,
        max_input_bytes=1024,
    )
    with pytest.raises(BenchmarkSourceError) as exc_info:
        asyncio.run(
            catalog.materialize(
                _reference(
                    resolver="local_document",
                    reference="not-a-document-uuid",
                    version="1",
                    digest="sha256:" + "0" * 64,
                ),
                principal_subject="owner-sub",
            )
        )
    assert exc_info.value.code == "invalid_reference"
